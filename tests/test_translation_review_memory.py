"""Synthetic end-to-end checks for manual AI review and local translation memory.

No provider, network, real video, or user-produced OCR/SRT data is used here.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import ocr_review
import translation_memory
import translation_review
from review_bundle import build_review_pack


class TranslationReviewMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tm_root = self.root / "local-memory"
        self.tm_root.mkdir()
        self.srt = self.root / "synthetic.srt"
        self.texts = ["synthetic source phrase", "synthetic second phrase",
                      "synthetic third phrase", "猫", "黒猫です"]
        cues = []
        srt_lines = []
        for index, text in enumerate(self.texts):
            start = 1000 + index * 2000
            end = start + 1200
            srt_lines.extend((str(index + 1), f"00:00:{start // 1000:02d},{start % 1000:03d} --> "
                              f"00:00:{end // 1000:02d},{end % 1000:03d}", text, ""))
            cues.append({"start_ms": start, "end_ms": end, "text": text,
                         "confidence": 0.75, "frame_index": 30 + index,
                         "frame_ms": start + 500, "band_y": 100, "band_height": 40})
        self.srt.write_text("\n".join(srt_lines) + "\n", encoding="utf-8")

        def synthetic_crop(_frame, _y, _height):
            return np.full((32, 160, 3), 96, dtype=np.uint8)

        self.pack, _manifest = build_review_pack(
            self.srt, cues, {"synthetic": True}, synthetic_crop,
            generation_stats={"main_srt_sha256": hashlib.sha256(self.srt.read_bytes()).hexdigest()})
        self.review_id, self.public = ocr_review.open_pack(str(self.pack))
        self.cue_ids = [cue["cue_id"] for cue in self.public["cues"]]
        for cue_id in self.cue_ids:
            ocr_review.append_review(self.review_id, cue_id, "accepted")
        self.tm_root_patch = patch.object(translation_memory, "_root", return_value=self.tm_root)
        self.tm_root_patch.start()

    def tearDown(self):
        self.tm_root_patch.stop()
        self.temp.cleanup()

    def make_response(self, request, *, statuses=None, translations=None):
        statuses = statuses or ["confirmed"] * len(request["cues"])
        translations = translations or [f"synthetic Turkish {i + 1}" for i in range(len(request["cues"]))]
        rows = []
        for cue, source_status, translation in zip(request["cues"], statuses, translations):
            rows.append({
                "cue_id": cue["cue_id"], "start_ms": cue["start_ms"], "end_ms": cue["end_ms"],
                "source_review": {
                    "status": source_status,
                    "observed_text": cue["verified_source_text"] if source_status == "confirmed" else "",
                    "proposed_source_text": None,
                    "reason": "synthetic visual check",
                },
                "translation_review": {
                    "status": "reviewed" if source_status == "confirmed" else "deferred",
                    "issues": ["none"] if source_status == "confirmed" else [],
                    "proposed_translation": translation if source_status == "confirmed" else None,
                    "reason": "synthetic translation check",
                },
            })
        return {"schema_version": 1, "request_id": request["request_id"], "cues": rows}

    def make_proposal(self, cue_id, translation):
        request = translation_review.create_request(
            self.review_id, {cue_id: "synthetic draft"}, "ja", "tr")["request"]
        response = self.make_response(request, translations=[translation])
        result = translation_review.import_response(self.review_id, response)
        self.assertEqual(result["count"], 1)
        proposal = translation_review.proposal_status(self.review_id)["proposals"][-1]
        return proposal

    def test_source_confirmation_gate_and_response_identity_timing_and_staleness(self):
        ocr_review.undo_review(self.review_id, self.cue_ids[0])
        with self.assertRaises(ocr_review.ReviewError):
            translation_review.create_request(self.review_id, {self.cue_ids[0]: "draft"}, "ja", "tr")
        ocr_review.append_review(self.review_id, self.cue_ids[0], "accepted")

        request = translation_review.create_request(
            self.review_id, {self.cue_ids[0]: "synthetic draft"}, "ja", "tr")["request"]
        with self.assertRaises(ocr_review.ReviewError):
            translation_review.import_response(self.review_id, {
                **self.make_response(request), "request_id": "0" * 32})
        wrong_timing = self.make_response(request)
        wrong_timing["cues"][0]["start_ms"] += 1
        with self.assertRaises(ocr_review.ReviewError):
            translation_review.import_response(self.review_id, wrong_timing)
        mismatched_source = self.make_response(request)
        mismatched_source["cues"][0]["source_review"]["observed_text"] = "unverified text"
        with self.assertRaises(ocr_review.ReviewError):
            translation_review.import_response(self.review_id, mismatched_source)

        uncertain = self.make_response(request, statuses=["uncertain"])
        self.assertEqual(translation_review.import_response(self.review_id, uncertain)["count"], 1)
        pending = translation_review.proposal_status(self.review_id)["proposals"][-1]
        with self.assertRaises(ocr_review.ReviewError):
            translation_review.decide(self.review_id, pending["proposal_id"], "accepted")

        stale_request = translation_review.create_request(
            self.review_id, {self.cue_ids[1]: "synthetic draft"}, "ja", "tr")["request"]
        ocr_review.append_review(self.review_id, self.cue_ids[1], "accepted")
        with self.assertRaises(ocr_review.ReviewError):
            translation_review.import_response(
                self.review_id, self.make_response(stale_request, translations=["stale"]))

    def test_decisions_are_explicit_append_only_and_project_only_accepted_or_edited(self):
        proposals = [self.make_proposal(cue_id, f"synthetic suggestion {index}")
                     for index, cue_id in enumerate(self.cue_ids)]
        decisions = [
            translation_review.decide(self.review_id, proposals[0]["proposal_id"], "accepted"),
            translation_review.decide(self.review_id, proposals[1]["proposal_id"], "edited",
                                      "synthetic human edit"),
            translation_review.decide(self.review_id, proposals[2]["proposal_id"], "rejected"),
        ]
        self.assertEqual([d["decision"] for d in decisions], ["accepted", "edited", "rejected"])
        _count, journal = translation_review._read_events(self.pack / "translation-review-events.jsonl")
        self.assertEqual([row["event_type"] for row in journal],
                         ["proposal"] * 5 + ["decision"] * 3)
        self.assertTrue(all(row["decision"] == "pending" for row in journal[:5]))
        self.assertEqual(len({row["event_id"] for row in journal[5:]}), 3)
        self.assertEqual(translation_review.proposal_status(self.review_id)["proposals"][0]["decision"],
                         "accepted")
        with self.assertRaises(ocr_review.ReviewError):
            translation_review.decide(self.review_id, proposals[0]["proposal_id"], "accepted")
        snapshot = self.pack / "translation-memory-v1.jsonl"
        entries = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(entries), 2)
        self.assertEqual({entry["approved_translation"] for entry in entries},
                         {"synthetic suggestion 0", "synthetic human edit"})

    def test_translation_memory_scope_ambiguity_explicit_draft_apply_and_lexicon(self):
        first = self.make_proposal(self.cue_ids[0], "synthetic accepted translation")
        decision = translation_review.decide(self.review_id, first["proposal_id"], "accepted")
        decision_id = decision["event_id"]
        self.assertEqual(translation_memory.add_approved(
            self.review_id, "Synthetic-Series", "ja", "tr", [decision_id])["added"], 1)
        self.assertEqual(translation_memory.add_approved(
            self.review_id, "synthetic-series", "ja", "tr", [decision_id])["already_present"], 1)

        suggestion = translation_memory.suggest(
            self.review_id, self.cue_ids[0], "synthetic-series", "ja", "tr",
            self.texts[0], "keep this draft")
        self.assertEqual(suggestion["translation_memory"]["status"], "suggestion")
        self.assertEqual(suggestion["translation_memory"]["translation"], "synthetic accepted translation")
        self.assertEqual(suggestion["current_draft"], "keep this draft")
        self.assertEqual(self.srt.read_text(encoding="utf-8").count(self.texts[0]), 1)
        isolated = translation_memory.suggest(
            self.review_id, self.cue_ids[0], "other-series", "ja", "tr", self.texts[0])
        self.assertEqual(isolated["translation_memory"]["status"], "none")
        language_isolated = translation_memory.suggest(
            self.review_id, self.cue_ids[0], "synthetic-series", "ja", "en", self.texts[0])
        self.assertEqual(language_isolated["translation_memory"]["status"], "none")

        second = self.make_proposal(self.cue_ids[0], "another synthetic translation")
        second_decision = translation_review.decide(self.review_id, second["proposal_id"], "accepted")
        translation_memory.add_approved(
            self.review_id, "synthetic-series", "ja", "tr", [second_decision["event_id"]])
        ambiguous = translation_memory.suggest(
            self.review_id, self.cue_ids[0], "synthetic-series", "ja", "tr", self.texts[0])
        self.assertEqual(ambiguous["translation_memory"]["status"], "ambiguous")
        self.assertNotIn("translation", ambiguous["translation_memory"])

        translation_memory.add_lexicon(
            "synthetic-series", "ja", "tr", "source", "kaynak")
        lexicon = translation_memory.suggest(
            self.review_id, self.cue_ids[0], "synthetic-series", "ja", "tr",
            "synthetic source phrase", "original draft")
        self.assertEqual(lexicon["lexicon"]["status"], "suggestion")
        self.assertEqual(lexicon["lexicon"]["translation"], "synthetic kaynak phrase")
        self.assertEqual(lexicon["current_draft"], "original draft")
        translation_memory.add_lexicon("synthetic-series", "ja", "tr", "synthetic", "A")
        translation_memory.add_lexicon("synthetic-series", "ja", "tr", "synthetic", "B")
        collision = translation_memory.suggest(
            self.review_id, self.cue_ids[0], "synthetic-series", "ja", "tr", self.texts[0])
        self.assertEqual(collision["lexicon"]["status"], "ambiguous")
        self.assertEqual(collision["lexicon"]["matches"], [])

    def test_lexicon_cjk_matching_and_compound_ambiguity(self):
        translation_memory.add_lexicon("synthetic-series", "ja", "tr", "猫", "kedi")
        clear = translation_memory.suggest(
            self.review_id, self.cue_ids[3], "synthetic-series", "ja", "tr", "猫")
        self.assertEqual(clear["lexicon"]["status"], "suggestion")
        self.assertEqual(clear["lexicon"]["translation"], "kedi")
        compound = translation_memory.suggest(
            self.review_id, self.cue_ids[4], "synthetic-series", "ja", "tr", "黒猫です")
        self.assertEqual(compound["lexicon"]["status"], "ambiguous")

    def test_rollback_is_idempotent_projection_rebuilds_and_malformed_history_fails_closed(self):
        added = translation_memory.add_lexicon(
            "synthetic-series", "ja", "tr", "synthetic", "örnek")
        scope = added["scope"]
        paths = translation_memory._paths(scope)
        event_log, _tm_projection, lexicon_projection = paths
        event = json.loads(event_log.read_text(encoding="utf-8").splitlines()[0])
        first_undo = translation_memory.rollback(
            "synthetic-series", "ja", "tr", event["event_id"])
        self.assertFalse(first_undo.get("already_rolled_back", False))
        second_undo = translation_memory.rollback(
            "synthetic-series", "ja", "tr", event["event_id"])
        self.assertTrue(second_undo["already_rolled_back"])
        self.assertEqual(translation_memory.status("synthetic-series", "ja", "tr")["entry_count"], 0)

        translation_memory.add_lexicon("synthetic-series", "ja", "tr", "phrase", "söz")
        lexicon_projection.unlink()
        rebuilt = translation_memory.status("synthetic-series", "ja", "tr")
        self.assertEqual(rebuilt["entry_count"], 1)
        self.assertTrue(lexicon_projection.exists())
        self.assertEqual(json.loads(lexicon_projection.read_text(encoding="utf-8").splitlines()[0])[
            "target_term"], "söz")

        with event_log.open("ab") as stream:
            stream.write(b'{"malformed":true}\n')
        with self.assertRaises(ocr_review.ReviewError):
            translation_memory.status("synthetic-series", "ja", "tr")
        with self.assertRaises(ocr_review.ReviewError):
            translation_memory.add_lexicon("synthetic-series", "ja", "tr", "x", "y")


if __name__ == "__main__":
    unittest.main()
