"""Browser acceptance for scoped local translation memory.

All review packs, crops, translations, and memory entries are synthetic. The
test server is loopback-only, translation storage is redirected to a temporary
directory, and browser requests to non-local origins are blocked.
"""

import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

import numpy as np
from werkzeug.serving import make_server

import ocr_review
import translation_memory
import translation_review
import ui_server
from review_bundle import build_review_pack

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Keep Playwright an optional test dependency.
    sync_playwright = None


def _same_origin(candidate_url, expected_url):
    """Compare parsed scheme/host/effective port; never trust string prefixes."""
    try:
        candidate = urlsplit(candidate_url)
        expected = urlsplit(expected_url)
        if (candidate.scheme.lower() != expected.scheme.lower()
                or candidate.hostname is None or expected.hostname is None
                or candidate.username is not None or candidate.password is not None
                or expected.username is not None or expected.password is not None):
            return False
        candidate_port = candidate.port or (443 if candidate.scheme.lower() == "https" else 80)
        expected_port = expected.port or (443 if expected.scheme.lower() == "https" else 80)
        return (candidate.scheme.lower() in {"http", "https"}
                and candidate.hostname.lower() == expected.hostname.lower()
                and candidate_port == expected_port)
    except (AttributeError, TypeError, ValueError):
        return False


def _require_browser(available, reason):
    if available:
        return
    if os.environ.get("HARDSUB2SRT_REQUIRE_BROWSER_TESTS", "").strip().lower() in {
            "1", "true", "yes", "on"}:
        raise RuntimeError(reason)
    raise unittest.SkipTest(reason)


def _synthetic_response(request, translation, target_language):
    cue = request["cues"][0]
    return {
        "schema_version": 1,
        "request_id": request["request_id"],
        "cues": [{
            "cue_id": cue["cue_id"],
            "start_ms": cue["start_ms"],
            "end_ms": cue["end_ms"],
            "source_review": {
                "status": "confirmed",
                "observed_text": cue["verified_source_text"],
                "proposed_source_text": None,
                "reason": "synthetic visual verification",
            },
            "translation_review": {
                "status": "reviewed",
                "issues": ["none"],
                "proposed_translation": translation,
                "reason": f"synthetic {target_language} review",
            },
        }],
    }


class BrowserTestPolicyTests(unittest.TestCase):
    def test_hostile_prefix_origin_is_not_allowed(self):
        expected = "http://127.0.0.1:8765"
        self.assertTrue(_same_origin("http://127.0.0.1:8765/review", expected))
        self.assertFalse(_same_origin(expected + ".evil/review", expected))
        self.assertFalse(_same_origin("http://127.0.0.1.evil:8765/review", expected))
        self.assertFalse(_same_origin("https://127.0.0.1:8765/review", expected))

    def test_strict_mode_fails_instead_of_skipping_when_browser_is_missing(self):
        with patch.dict(os.environ, {"HARDSUB2SRT_REQUIRE_BROWSER_TESTS": "1"}):
            with self.assertRaisesRegex(RuntimeError, "Playwright/Chromium missing"):
                _require_browser(False, "Playwright/Chromium missing")


class TranslationMemoryBrowserAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.memory_root = self.root / "temporary-translation-memory"
        self.memory_root.mkdir()
        self.memory_patch = patch.object(translation_memory, "_root", return_value=self.memory_root)
        self.memory_patch.start()

        self.srt = self.root / "synthetic-review.srt"
        texts = ["synthetic source line", "synthetic source line"]
        srt_rows = []
        cues = []
        for index, text in enumerate(texts):
            start_ms = 1000 + index * 2000
            end_ms = start_ms + 1000
            srt_rows.extend((str(index + 1),
                             f"00:00:{start_ms // 1000:02d},{start_ms % 1000:03d} --> "
                             f"00:00:{end_ms // 1000:02d},{end_ms % 1000:03d}",
                             text, ""))
            cues.append({"start_ms": start_ms, "end_ms": end_ms, "text": text,
                         "confidence": 0.8, "frame_index": index + 1,
                         "frame_ms": start_ms + 500, "band_y": 20, "band_height": 20})
        self.srt.write_text("\n".join(srt_rows) + "\n", encoding="utf-8")

        def synthetic_crop(_frame, _y, _height):
            return np.full((32, 160, 3), 96, dtype=np.uint8)

        self.pack, _manifest = build_review_pack(
            self.srt, cues, {"synthetic": True}, synthetic_crop,
            generation_stats={"main_srt_sha256": hashlib.sha256(self.srt.read_bytes()).hexdigest()})
        self.review_id, public_pack = ocr_review.open_pack(str(self.pack))
        self.cue_ids = [cue["cue_id"] for cue in public_pack["cues"]]
        for cue_id in self.cue_ids:
            ocr_review.append_review(self.review_id, cue_id, "accepted")

        self.decision_ids = {}
        for cue_id, target_language, translation in (
                (self.cue_ids[0], "tr", "synthetic Turkish memory"),
                (self.cue_ids[1], "tr", "another synthetic Turkish memory"),
                (self.cue_ids[0], "en", "synthetic English memory")):
            request = translation_review.create_request(
                self.review_id, {cue_id: "synthetic draft"}, "ja", target_language)["request"]
            imported = translation_review.import_response(
                self.review_id, _synthetic_response(request, translation, target_language))
            self.assertEqual(imported["count"], 1)
            proposal = next(
                row for row in translation_review.proposal_status(self.review_id)["proposals"]
                if row["cue_id"] == cue_id and row["decision"] == "pending")
            decision = translation_review.decide(
                self.review_id, proposal["proposal_id"], "accepted")
            self.decision_ids[(cue_id, target_language)] = decision["event_id"]

        self.server = make_server("127.0.0.1", 0, ui_server.app, threaded=True)
        self.port_patch = patch.object(ui_server, "PORT", self.server.server_port)
        self.port_patch.start()
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(timeout=5)
        if hasattr(self, "port_patch"):
            self.port_patch.stop()
        self.memory_patch.stop()
        self.temp.cleanup()

    def _add_approved(self, project, language, decision_key):
        return translation_memory.add_approved(
            self.review_id, project, "ja", language, [self.decision_ids[decision_key]])

    def test_browser_scope_suggestions_ambiguity_and_idempotent_rollback(self):
        _require_browser(sync_playwright is not None,
                         "Playwright is missing; install it or use strict browser-test mode")
        self._add_approved("synthetic-series", "tr", (self.cue_ids[0], "tr"))
        self._add_approved("synthetic-series", "en", (self.cue_ids[0], "en"))

        with sync_playwright() as playwright:
            browser_path = Path(playwright.chromium.executable_path)
            _require_browser(browser_path.is_file(),
                             "Playwright Chromium missing; install it or use strict browser-test mode")
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                def restrict_to_exact_local_origin(route):
                    if _same_origin(route.request.url, self.base_url):
                        route.continue_()
                    else:
                        route.abort()

                context.route("**/*", restrict_to_exact_local_origin)
                page = context.new_page()
                # The hostile prefix must be rejected by the same origin predicate
                # used by the browser request gate, before any request can continue.
                self.assertFalse(_same_origin(self.base_url + ".evil/blocked", self.base_url))
                page.goto(f"{self.base_url}/ocr-inceleme", wait_until="domcontentloaded")
                page.locator("#path").fill(str(self.pack))
                page.locator("#open").click()
                page.locator("#workspace:not(.hidden)").wait_for()
                page.locator("#step-ai-translation summary").click()
                page.locator("#step-memory summary").click()
                page.locator("#tm-project").fill("synthetic-series")
                page.locator("#tm-load").click()

                first_draft = page.locator(".ai-draft").first
                first_draft.fill("keep this synthetic draft")
                page.locator("#ai-drafts button", has_text="Bellekten öneri ara").first.click()
                result = page.locator(f"#tm-cue-{self.cue_ids[0]}")
                result.get_by_text("synthetic Turkish memory", exact=False).wait_for()
                self.assertEqual(await_value(first_draft), "keep this synthetic draft",
                                 "looking up a memory must not silently replace a draft")
                result.get_by_role("button", name="Taslağa al").click()
                self.assertEqual(await_value(first_draft), "synthetic Turkish memory")

                # A different project key has an independent scope and yields no match.
                page.locator("#tm-project").fill("other-series")
                first_draft.fill("keep after scope miss")
                page.locator("#ai-drafts button", has_text="Bellekten öneri ara").first.click()
                result.get_by_text("tam eşleşme bulunamadı", exact=False).wait_for()
                self.assertEqual(await_value(first_draft), "keep after scope miss")

                # The same project with a different target language uses its own entry.
                page.locator("#tm-project").fill("synthetic-series")
                page.locator("#ai-target-lang").fill("en")
                page.locator("#tm-load").click()
                page.locator("#ai-drafts button", has_text="Bellekten öneri ara").first.click()
                result.get_by_text("synthetic English memory", exact=False).wait_for()
                self.assertEqual(await_value(first_draft), "keep after scope miss")

                # Two approved outputs for the exact same source are ambiguous; neither is applied.
                self._add_approved("synthetic-series", "tr", (self.cue_ids[1], "tr"))
                page.locator("#ai-target-lang").fill("tr")
                page.locator("#tm-load").click()
                first_draft.fill("keep through ambiguity")
                page.locator("#ai-drafts button", has_text="Bellekten öneri ara").first.click()
                result.get_by_text("farklı onaylı çeviriler var", exact=False).wait_for()
                self.assertEqual(await_value(first_draft), "keep through ambiguity")
                self.assertEqual(result.get_by_role("button").count(), 0)

                # Roll back one active item via the visible UI, then retry its event over
                # same-origin fetch and verify the API reports the idempotent outcome.
                entry = page.locator("#tm-entries article.notice").filter(
                    has_text="another synthetic Turkish memory")
                event_id = page.evaluate(
                    "el => el.querySelector('button[onclick^=\\\"rollbackTm\\\"]')?.getAttribute('onclick')",
                    entry.element_handle())
                self.assertTrue(event_id and event_id.startswith("rollbackTm('"))
                event_id = event_id[len("rollbackTm('"):-2]
                entry.get_by_role("button", name="Geri al").click()
                entry.wait_for(state="detached")
                retry = page.evaluate("""async ({eventId}) => {
                    const response = await fetch('/api/ceviri-bellegi/geri-al', {
                      method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({project_key:'synthetic-series', source_language:'ja',
                        target_language:'tr', event_id:eventId})
                    });
                    return {status: response.status, body: await response.json()};
                }""", {"eventId": event_id})
                self.assertEqual(retry["status"], 200)
                self.assertTrue(retry["body"]["already_rolled_back"])
                page.locator("#tm-entries article.notice").filter(
                    has_text="synthetic Turkish memory").get_by_role("button", name="Geri al").click()
                page.locator("#tm-entries").get_by_text("Bu kapsamda kayıt yok.").wait_for()
                self.assertEqual(page.locator("#tm-entries article.notice").count(), 0)
                context.close()
            finally:
                browser.close()


def await_value(locator):
    return locator.evaluate("element => element.value")


if __name__ == "__main__":
    unittest.main()
