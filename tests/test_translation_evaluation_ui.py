"""Synthetic Flask and optional Playwright checks for the Phase 3 evaluator."""

import hashlib
import json
import os
import tempfile
import threading
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from werkzeug.serving import make_server

import ocr_review
import translation_evaluation as evaluation_backend
import translation_evaluation_service as evaluation_service
from translation_evaluation_service import _local_root as _resolve_actual_local_root
import translation_review
import ui_server
from review_bundle import build_review_pack

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def _same_origin(candidate_url, expected_url):
    from urllib.parse import urlsplit
    try:
        candidate, expected = urlsplit(candidate_url), urlsplit(expected_url)
        return (candidate.scheme == expected.scheme == "http" and
                candidate.hostname == expected.hostname and candidate.port == expected.port and
                candidate.username is None and candidate.password is None)
    except (TypeError, ValueError):
        return False


def _synthetic_ai_response(request, translation="SYNTHETIC MANUAL AI OUTPUT"):
    cue = request["cues"][0]
    return {
        "schema_version": 1,
        "request_id": request["request_id"],
        "cues": [{
            "cue_id": cue["cue_id"],
            "start_ms": cue["start_ms"],
            "end_ms": cue["end_ms"],
            "source_review": {
                "status": "confirmed", "observed_text": cue["verified_source_text"],
                "proposed_source_text": None, "reason": "synthetic crop text was checked",
            },
            "translation_review": {
                "status": "reviewed", "issues": ["none"],
                "proposed_translation": translation, "reason": "synthetic manual response",
            },
        }],
    }


def _require_browser(available, reason):
    if available:
        return
    if os.environ.get("HARDSUB2SRT_REQUIRE_BROWSER_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError(reason)
    raise unittest.SkipTest(reason)


class TranslationEvaluationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = self.root / "private-local-app-data" / "translation-evaluation-v1" / "synthetic"
        self.storage.mkdir(parents=True)
        self.service_root_patch = patch.object(evaluation_service, "_local_root", return_value=self.storage)
        self.service_root_patch.start()
        self.port_patch = patch.object(ui_server, "PORT", 8765)
        self.port_patch.start()
        self.pack_registry_snapshot = ocr_review._PACKS.copy()
        self.srt = self.root / "synthetic-episode.srt"
        self.source_text = "SYNTHETIC VERIFIED SOURCE CUE"
        self.srt.write_text(
            f"1\n00:00:01,000 --> 00:00:02,000\n{self.source_text}\n\n", encoding="utf-8")
        cue = {"start_ms": 1000, "end_ms": 2000, "text": self.source_text,
               "confidence": 0.9, "frame_index": 30, "frame_ms": 1500,
               "band_y": 100, "band_height": 80}
        crop = np.full((24, 120, 3), 90, dtype=np.uint8)
        self.pack, _manifest = build_review_pack(
            self.srt, [cue], {"synthetic": True}, lambda *_: crop)
        self.review_id, public = ocr_review.open_pack(str(self.pack))
        self.cue = public["cues"][0]
        self.cue_id = self.cue["cue_id"]
        ocr_review.append_review(self.review_id, self.cue_id, "accepted")
        self.client = ui_server.app.test_client()
        self.base_url = "http://127.0.0.1:8765"

    def tearDown(self):
        with ocr_review._LOCK:
            ocr_review._PACKS.clear()
            ocr_review._PACKS.update(self.pack_registry_snapshot)
        self.port_patch.stop()
        self.service_root_patch.stop()
        self.temp.cleanup()

    def _get(self, path, **kwargs):
        return self.client.get(path, base_url=self.base_url, **kwargs)

    def _post(self, path, *, json_body, headers=None):
        return self.client.post(path, base_url=self.base_url,
                                headers=headers or {"Origin": self.base_url}, json=json_body)

    def test_real_quality_readiness_is_server_blocked_and_ignores_claim_parameters(self):
        response = self._get("/api/ceviri-kalitesi/hazirlik", query_string={
            "review_id": self.review_id, "split": "held_out", "human_held_out": "true",
            "rights_verified": "true", "timed_reference": "present",
        })
        self.assertEqual(response.status_code, 200)
        status = response.get_json()
        self.assertFalse(status["real_evaluation_enabled"])
        self.assertEqual(status["allowed_mode"], "synthetic_only")
        self.assertFalse(status["quality_claims_available"])
        self.assertFalse(status["phase6_eligible"])
        self.assertEqual(status["active_verified_source_cues"], 1)
        self.assertEqual(status["valid_manual_ai_proposals"], 0)
        self.assertEqual([item["code"] for item in status["blockers"]],
                         ["SERVER_COHORT_REGISTRY_MISSING"])
        self.assertNotIn("rights_verified", status)
        self.assertNotIn("split", status)

    def test_readiness_reports_server_evidence_failure_reason_without_echoing_paths(self):
        with patch.object(evaluation_service, "_real_registry_context",
                          side_effect=evaluation_service.RealEvidenceBlocked(
                              "OCR_SOURCE_LINEAGE_STALE", "verified crop hash changed")):
            status = evaluation_service.readiness(self.review_id)
        self.assertFalse(status["real_evaluation_enabled"])
        self.assertEqual(status["blockers"][0]["code"], "OCR_SOURCE_LINEAGE_STALE")
        encoded = json.dumps(status)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("verified crop hash changed", encoded)

    def test_real_cohort_create_requires_empty_body_and_server_evidence(self):
        forged = self._post("/api/ceviri-kalitesi/gercek?review_id=" + self.review_id,
                            json_body={"split": "held_out", "rights_verified": True})
        self.assertEqual(forged.status_code, 400)
        blocked = self._post("/api/ceviri-kalitesi/gercek?review_id=" + self.review_id,
                             json_body={})
        self.assertEqual(blocked.status_code, 409)
        body = blocked.get_json()
        self.assertNotIn(str(self.root), json.dumps(body))

    def test_real_rating_route_rejects_browser_source_and_provider_claims(self):
        payload = {"evaluation_id": "a" * 32, "blind_candidate_id": "b" * 32,
                   "status": "rated", "dimensions": {}, "issues": [],
                   "rubric_outcome": "accepted", "exclusion_reason": None,
                   "supersedes_event_id": None,
                   "source_state": {"active": True}, "condition_id": "provider-gpt"}
        response = self._post("/api/ceviri-kalitesi/gercek/puan", json_body=payload)
        self.assertEqual(response.status_code, 400)

    def test_new_mutations_require_same_origin_and_exact_host(self):
        payload = {}
        response = self.client.post(
            "/api/ceviri-kalitesi/sentetik", base_url=self.base_url,
            headers={"Origin": self.base_url + ".evil"}, json=payload)
        self.assertEqual(response.status_code, 403)
        hostile_host = self.client.post(
            "/api/ceviri-kalitesi/sentetik", base_url="http://127.0.0.1.evil:8765",
            headers={"Origin": "http://127.0.0.1.evil:8765"}, json=payload)
        self.assertEqual(hostile_host.status_code, 403)
        missing_origin = self.client.post(
            "/api/ceviri-kalitesi/sentetik", base_url=self.base_url, json={})
        self.assertEqual(missing_origin.status_code, 403)

    def test_manual_ai_candidate_import_is_separate_from_quality_evaluation(self):
        request = translation_review.create_request(
            self.review_id, {self.cue_id: "SYNTHETIC TARGET DRAFT"}, "ja", "tr")["request"]
        imported = self._post("/api/ai-ceviri/yanit", json_body={
            "review_id": self.review_id,
            "response": _synthetic_ai_response(request),
        })
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.get_json()["count"], 1)
        readiness = self._get("/api/ceviri-kalitesi/hazirlik",
                              query_string={"review_id": self.review_id}).get_json()
        self.assertEqual(readiness["valid_manual_ai_proposals"], 1)
        self.assertFalse(readiness["real_evaluation_enabled"])
        self.assertFalse((self.pack / "translation-evaluation-events.jsonl").exists())
        self.assertFalse((self.pack / "translation-memory-v1.jsonl").exists())

    def test_real_context_contains_only_active_verified_neighbor_text(self):
        info = translation_review._info(self.review_id)
        sources = translation_review._verified_sources(info, verify_crops=True)
        context = evaluation_service._verified_context(info, sources, self.cue_id)
        self.assertEqual(context, "Mevcut: SYNTHETIC VERIFIED SOURCE CUE")
        self.assertNotIn(str(self.pack), context)

    def test_manual_proposal_language_pair_must_match_cohort(self):
        proposal = {"source_language": "ja", "target_language": "tr"}
        self.assertTrue(evaluation_service._proposal_language_matches(proposal, "JA", "tr"))
        self.assertFalse(evaluation_service._proposal_language_matches(proposal, "ja", "en"))
        self.assertFalse(evaluation_service._proposal_language_matches(
            {"source_language": "ja"}, "ja", "tr"))

    def test_condition_mapping_requires_one_opaque_condition_per_proposal(self):
        first, second = "a" * 32, "b" * 32
        condition_a, condition_b = "c" * 32, "d" * 32
        mapping = {"assignments": {first: condition_a, second: condition_a}}
        self.assertEqual(evaluation_service._single_condition_for_proposals(
            mapping, [first, second]), condition_a)
        mapping["assignments"][second] = condition_b
        with self.assertRaises(evaluation_service.RealEvidenceBlocked) as mixed:
            evaluation_service._single_condition_for_proposals(mapping, [first, second])
        self.assertEqual(mixed.exception.code, "MIXED_CONDITIONS")
        with self.assertRaises(evaluation_service.RealEvidenceBlocked) as missing:
            evaluation_service._single_condition_for_proposals({"assignments": {first: condition_a}}, [first, second])
        self.assertEqual(missing.exception.code, "CONDITION_ASSIGNMENT_MISSING")

    def test_private_condition_label_reveals_only_after_every_candidate_has_rating(self):
        demo = self._post("/api/ceviri-kalitesi/sentetik", json_body={}).get_json()
        root = evaluation_service._evaluation_dir(demo["evaluation_id"])
        _path, manifest, _sha, _events = evaluation_backend._load(root)
        candidate_id = manifest["cases"][0]["candidates"][0]["blind_candidate_id"]
        manifest = {**manifest, "cases": [
            {**case, "candidates": [case["candidates"][0]]} for case in manifest["cases"]]}
        condition_id = manifest["cases"][0]["candidates"][0]["condition_id"]
        fake_context = {"condition_mapping": {
            "labels": {condition_id: "PRIVATE PROVIDER LABEL"}, "sha256": "e" * 64}}
        with patch.object(evaluation_service, "_load_real_evaluation",
                          return_value=(root, manifest, {}, fake_context)):
            before = self._get("/api/ceviri-kalitesi/gercek/kosul-eslemesi",
                               query_string={"evaluation_id": demo["evaluation_id"]})
            self.assertEqual(before.status_code, 409)
            self.assertNotIn("PRIVATE PROVIDER LABEL", before.get_data(as_text=True))
            for candidate in demo["candidates"]:
                payload = {
                    "evaluation_id": demo["evaluation_id"],
                    "blind_candidate_id": candidate["blind_candidate_id"], "status": "rated",
                    "dimensions": {key: 2 for key in (
                        "semantic_adequacy", "omissions", "additions", "terminology", "style_register")},
                    "issues": [], "rubric_outcome": "accepted", "exclusion_reason": None,
                    "supersedes_event_id": None,
                }
                self.assertEqual(self._post("/api/ceviri-kalitesi/sentetik/puan",
                                            json_body=payload).status_code, 200)
            after = self._get("/api/ceviri-kalitesi/gercek/kosul-eslemesi",
                              query_string={"evaluation_id": demo["evaluation_id"]})
            self.assertEqual(after.status_code, 200)
            reveal = after.get_json()
            self.assertTrue(reveal["revealed"])
            self.assertEqual(reveal["condition_id"], condition_id)
            self.assertEqual(reveal["private_label"], "PRIVATE PROVIDER LABEL")
            before_events = evaluation_backend._load(root)[3]
            self.assertEqual(before_events[-1]["event_type"], "condition_mapping_revealed")
            with (patch.object(evaluation_service, "_real_registry_context", return_value={
                      "candidate_data": {"evaluation_id": demo["evaluation_id"]}}),
                  patch.object(evaluation_service, "_real_base", return_value=root.parent)):
                readiness = evaluation_service.readiness(self.review_id)
            self.assertFalse(readiness["real_evaluation_enabled"])
            self.assertEqual(readiness["blockers"][0]["code"], "COHORT_ALREADY_UNBLINDED")
            self.assertNotIn("PRIVATE PROVIDER LABEL", json.dumps(
                self._get("/api/ceviri-kalitesi/sentetik/durum",
                          query_string={"evaluation_id": demo["evaluation_id"]}).get_json()))
            current = self._get("/api/ceviri-kalitesi/sentetik/durum",
                                query_string={"evaluation_id": demo["evaluation_id"]}).get_json()
            prior = current["active_ratings"][candidate_id]
            correction = {
                "evaluation_id": demo["evaluation_id"], "blind_candidate_id": candidate_id,
                "status": "rated", "dimensions": {key: 1 for key in (
                    "semantic_adequacy", "omissions", "additions", "terminology", "style_register")},
                "issues": [], "rubric_outcome": "edited", "exclusion_reason": None,
                "supersedes_event_id": prior["event_id"],
            }
            rejected = self._post("/api/ceviri-kalitesi/sentetik/puan", json_body=correction)
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(len(evaluation_backend._load(root)[3]), len(before_events))

    def test_claim_fields_cannot_be_used_to_create_synthetic_or_real_cohort(self):
        forged = {
            "dataset_kind": "human_held_out", "split": "held_out",
            "condition_id": "provider-name", "source_state": {"active": True},
            "rights_verified": True, "timed_reference": True,
        }
        response = self._post("/api/ceviri-kalitesi/sentetik", json_body=forged)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(self.storage.iterdir()), [])

    def test_synthetic_report_saves_only_fake_ratings_and_never_emits_metrics(self):
        created = self._post("/api/ceviri-kalitesi/sentetik", json_body={})
        self.assertEqual(created.status_code, 200)
        demo = created.get_json()
        self.assertEqual(demo["mode"], "synthetic_test_only")
        self.assertIn("SYNTHETIC SOURCE", demo["source_text"])
        self.assertEqual(len(demo["candidates"]), 2)
        self.assertTrue(all(len(row["blind_candidate_id"]) == 32 for row in demo["candidates"]))
        for row in demo["candidates"]:
            payload = {
                "evaluation_id": demo["evaluation_id"],
                "blind_candidate_id": row["blind_candidate_id"],
                "status": "rated",
                "dimensions": {key: 2 for key in (
                    "semantic_adequacy", "omissions", "additions", "terminology", "style_register")},
                "issues": ["none"],
                "rubric_outcome": "accepted",
                "exclusion_reason": None,
                "supersedes_event_id": None,
            }
            self.assertEqual(self._post("/api/ceviri-kalitesi/sentetik/puan", json_body=payload).status_code, 200)
        updated = self._get("/api/ceviri-kalitesi/sentetik/durum",
                            query_string={"evaluation_id": demo["evaluation_id"]}).get_json()
        self.assertEqual(updated["mode"], "synthetic_test_only")
        self.assertEqual(len(updated["rated_candidate_ids"]), 2)
        self.assertFalse(updated["report"]["phase6_eligible"])
        for condition in updated["report"]["conditions"].values():
            self.assertEqual(condition["metric_status"], "SYNTHETIC_FIXTURE_ONLY")
            self.assertIsNone(condition["held_out_metric"])
            self.assertIsNone(condition["rubric_dimensions"])
        text = json.dumps(updated, ensure_ascii=False)
        self.assertNotIn(str(self.pack), text)
        self.assertNotIn(self.source_text, text)

    def test_rating_correction_appends_and_supersedes_prior_event(self):
        demo = self._post("/api/ceviri-kalitesi/sentetik", json_body={}).get_json()
        candidate = demo["candidates"][0]["blind_candidate_id"]
        body = {
            "evaluation_id": demo["evaluation_id"], "blind_candidate_id": candidate,
            "status": "rated", "dimensions": {key: 1 for key in (
                "semantic_adequacy", "omissions", "additions", "terminology", "style_register")},
            "issues": [], "rubric_outcome": "edited", "exclusion_reason": None,
            "supersedes_event_id": None,
        }
        first = self._post("/api/ceviri-kalitesi/sentetik/puan", json_body=body)
        self.assertEqual(first.status_code, 200)
        body["dimensions"]["semantic_adequacy"] = 2
        body["supersedes_event_id"] = first.get_json()["event_id"]
        second = self._post("/api/ceviri-kalitesi/sentetik/puan", json_body=body)
        self.assertEqual(second.status_code, 200)
        root = evaluation_service._evaluation_dir(demo["evaluation_id"])
        events = evaluation_backend._load(root)[3]
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-2]["event_id"], first.get_json()["event_id"])
        self.assertEqual(events[-1]["supersedes_event_id"], first.get_json()["event_id"])
        status = self._get("/api/ceviri-kalitesi/sentetik/durum",
                           query_string={"evaluation_id": demo["evaluation_id"]}).get_json()
        self.assertEqual(status["active_ratings"][candidate]["event_id"], second.get_json()["event_id"])

    def test_forged_rating_links_or_gate_claims_are_rejected(self):
        demo = self._post("/api/ceviri-kalitesi/sentetik", json_body={}).get_json()
        candidate = demo["candidates"][0]
        body = {
            "evaluation_id": demo["evaluation_id"],
            "blind_candidate_id": candidate["blind_candidate_id"],
            "status": "rated",
            "dimensions": {key: 2 for key in (
                "semantic_adequacy", "omissions", "additions", "terminology", "style_register")},
            "issues": [], "rubric_outcome": "accepted", "exclusion_reason": None,
            "supersedes_event_id": None,
            "source_state": {"active": True}, "split": "held_out",
            "dataset_kind": "human_held_out", "condition_id": "provider-gpt",
            "rights_verified": True,
        }
        response = self._post("/api/ceviri-kalitesi/sentetik/puan", json_body=body)
        self.assertEqual(response.status_code, 400)

    def test_fixture_tampering_fails_closed(self):
        demo = self._post("/api/ceviri-kalitesi/sentetik", json_body={}).get_json()
        root = evaluation_service._evaluation_dir(demo["evaluation_id"])
        fixture_path = root / evaluation_service._FIXTURE_FILE
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture["items"][0]["text"] = "tampered real translation"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        response = self._get("/api/ceviri-kalitesi/sentetik/durum",
                             query_string={"evaluation_id": demo["evaluation_id"]})
        self.assertEqual(response.status_code, 409)

    def test_synthetic_storage_uses_checked_local_app_data_and_refuses_remote_root(self):
        app_data = self.root / "LocalAppData"
        history_parent = app_data / "hardsub2srt"
        history_parent.mkdir(parents=True)
        history = history_parent / "job-history.json"
        local_checker = ("_windows_state_root_is_local" if os.name == "nt"
                         else "_unix_state_root_is_local")
        with (patch.object(evaluation_service.job_history, "default_history_path", return_value=history),
              patch.object(evaluation_service.job_history, "_contains_reparse_component", return_value=False),
              patch.object(evaluation_service.job_history, local_checker, return_value=True)):
            resolved = _resolve_actual_local_root()
        self.assertEqual(resolved, history_parent / "translation-evaluation-v1" / "synthetic")
        with (patch.object(evaluation_service.job_history, "default_history_path", return_value=history),
              patch.object(evaluation_service.job_history, "_contains_reparse_component", return_value=False),
              patch.object(evaluation_service.job_history, local_checker, return_value=False)):
            with self.assertRaises(evaluation_backend.EvaluationError):
                _resolve_actual_local_root()

    def test_real_registry_path_is_inside_private_evaluation_root(self):
        expected = self.storage.parent / evaluation_service._REAL_REGISTRY_FILE
        self.assertEqual(evaluation_service._real_registry_path(), expected)

    def test_review_page_shows_real_blocker_and_synthetic_only_flow(self):
        response = self._get("/ocr-inceleme")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("İki dilli çeviri kalite değerlendirmesi", page)
        self.assertIn("Gerçek held-out kör değerlendirmeyi başlat", page)
        self.assertIn("Yalnız sentetik rubrik demosunu aç", page)
        self.assertIn("/api/ceviri-kalitesi/hazirlik", page)


class TranslationEvaluationBrowserSmoke(unittest.TestCase):
    """Optional local-only browser flow for synthetic scoring readiness."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = self.root / "private-local-app-data" / "translation-evaluation-v1" / "synthetic"
        self.storage.mkdir(parents=True)
        self.service_root_patch = patch.object(evaluation_service, "_local_root", return_value=self.storage)
        self.service_root_patch.start()
        self.port_patch_default = patch.object(ui_server, "PORT", 8765)
        self.port_patch_default.start()
        self.pack_registry_snapshot = ocr_review._PACKS.copy()
        self.srt = self.root / "synthetic-browser-review.srt"
        self.srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nSYNTHETIC VERIFIED SOURCE CUE\n\n", encoding="utf-8")
        self.pack, _manifest = build_review_pack(
            self.srt,
            [{"start_ms": 1000, "end_ms": 2000, "text": "SYNTHETIC VERIFIED SOURCE CUE",
              "confidence": 0.9, "frame_index": 30, "frame_ms": 1500,
              "band_y": 100, "band_height": 80}],
            {"synthetic": True}, lambda *_: np.full((24, 120, 3), 90, dtype=np.uint8))
        self.review_id, _public = ocr_review.open_pack(str(self.pack))
        ocr_review.append_review(self.review_id, _public["cues"][0]["cue_id"], "accepted")

    def tearDown(self):
        with ocr_review._LOCK:
            ocr_review._PACKS.clear()
            ocr_review._PACKS.update(self.pack_registry_snapshot)
        self.port_patch_default.stop()
        self.service_root_patch.stop()
        self.temp.cleanup()

    def test_page_blocks_real_claims_and_scores_the_fixed_demo(self):
        if sync_playwright is None:
            self.skipTest("Playwright is not installed")
        server = make_server("127.0.0.1", 0, ui_server.app, threaded=True)
        port_patch = patch.object(ui_server, "PORT", server.server_port)
        port_patch.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser_path = Path(playwright.chromium.executable_path)
                if not browser_path.is_file():
                    self.skipTest("Playwright Chromium is missing")
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context()
                    context.route("**/*", lambda route: route.continue_() if _same_origin(route.request.url, base)
                                  else route.abort())
                    page = context.new_page()
                    page.goto(base + "/ocr-inceleme", wait_until="domcontentloaded")
                    page.locator("#path").fill(str(self.pack))
                    page.locator("#open").click()
                    page.locator("#workspace:not(.hidden)").wait_for()
                    page.locator("#step-ai-translation summary").click()
                    page.get_by_text("Gerçek kalite puanlaması şu an kapalı.").wait_for()
                    page.get_by_role("button", name="Yalnız sentetik rubrik demosunu aç").click()
                    page.get_by_text("SYNTHETIC SOURCE TEXT", exact=False).first.wait_for()
                    card = page.locator(".eval-candidate").first
                    self.assertEqual(card.locator(".eval-dimension").count(), 5)
                    for select in card.locator(".eval-dimension").all():
                        select.select_option("2")
                    card.get_by_role("button", name="Sentetik kararı kaydet").click()
                    card.get_by_role("button", name="Puanı düzelt (yeni kayıt ekler)").wait_for()
                    card.locator(".eval-dimension").first.select_option("1")
                    card.get_by_role("button", name="Puanı düzelt (yeni kayıt ekler)").click()
                    card.get_by_role("button", name="Puanı düzelt (yeni kayıt ekler)").wait_for()
                    page.get_by_text("kalite metriği yok · Faz 6 uygun değil").wait_for()
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            port_patch.stop()

