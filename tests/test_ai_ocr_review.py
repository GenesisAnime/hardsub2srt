import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import cv2
import numpy as np

import ai_ocr_review
import ocr_review
import ui_server
from review_bundle import build_review_pack


class AiOcrReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.srt = root / "synthetic.srt"
        self.srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nsynthetic OCR text\n\n",
                            encoding="utf-8")
        crop = np.full((24, 120, 3), 90, dtype=np.uint8)
        self.pack, _ = build_review_pack(
            self.srt,
            [{"start_ms": 1000, "end_ms": 2000, "text": "synthetic OCR text",
              "confidence": 0.73, "frame_index": 30, "frame_ms": 1500,
              "band_y": 100, "band_height": 50}],
            {"synthetic": True}, lambda _frame, _y, _height: crop)
        self.review_id, self.public = ocr_review.open_pack(str(self.pack))
        self.cue = self.public["cues"][0]

    def tearDown(self):
        self.temp.cleanup()

    def make_response(self, template, status="correction_proposed"):
        row = template["cues"][0]
        row["status"] = status
        row["observed_text"] = "synthetic OCR text"
        row["proposed_text"] = "synthetic corrected text" if status == "correction_proposed" else None
        row["reason"] = "synthetic visual comparison"
        return template

    def test_manual_package_contains_only_selected_crop_and_privacy_safe_metadata(self):
        path, _filename, count = ai_ocr_review.create_package(self.review_id, [self.cue["cue_id"]])
        self.assertEqual(count, 1)
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            self.assertEqual(names, {"prompt.md", "request.json", "response-template.json",
                                     f"images/{self.cue['cue_id']}.jpg"})
            request = json.loads(archive.read("request.json"))
            template = json.loads(archive.read("response-template.json"))
            prompt = archive.read("prompt.md").decode()
        self.assertEqual(request["cues"][0]["source_text_ocr"], "synthetic OCR text")
        self.assertEqual(request["cues"][0]["ocr_confidence"], 0.73)
        self.assertNotIn(str(self.pack), prompt + json.dumps(request))
        self.assertNotIn("device_id", json.dumps(request))
        self.assertEqual(template["cues"][0]["status"], "uncertain")

    def test_ai_proposal_is_separate_append_only_record_and_duplicate_replay_is_idempotent(self):
        _path, _filename, _count = ai_ocr_review.create_package(self.review_id, [self.cue["cue_id"]])
        sidecar_files = list((self.pack / "ai-ocr-review").glob("*.local.json"))
        request_id = sidecar_files[0].stem.replace(".local", "")
        template = {"schema_version": 1, "request_id": request_id,
                    "cues": [{"cue_id": self.cue["cue_id"], "start_ms": 1000, "end_ms": 2000,
                              "status": "uncertain", "observed_text": "", "proposed_text": None,
                              "reason": "template"}]}
        response = self.make_response(template)
        self.assertEqual(ai_ocr_review.import_response(self.review_id, response)["imported"], 1)
        self.assertTrue(ai_ocr_review.import_response(self.review_id, response)["duplicate"])
        proposals = ai_ocr_review.status(self.review_id)["proposals"]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["status"], "correction_proposed")
        self.assertFalse(ai_ocr_review.status(self.review_id)["ai_output_is_human_label"])
        self.assertIsNone(ocr_review.pack_status(self.review_id)["cues"][0]["active_event"])
        self.assertFalse((self.pack / "review-events.jsonl").exists())

    def test_wrong_timing_duplicate_ids_and_invalid_status_fail_closed(self):
        _path, _filename, _count = ai_ocr_review.create_package(self.review_id, [self.cue["cue_id"]])
        sidecar = next((self.pack / "ai-ocr-review").glob("*.local.json"))
        request_id = sidecar.stem.removesuffix(".local")
        def response():
            return {"schema_version": 1, "request_id": request_id,
                    "cues": [{"cue_id": self.cue["cue_id"], "start_ms": 1001, "end_ms": 2000,
                              "status": "uncertain", "observed_text": "", "proposed_text": None,
                              "reason": "synthetic"}]}
        with self.assertRaises(ocr_review.ReviewError):
            ai_ocr_review.import_response(self.review_id, response())
        duplicate = response(); duplicate["cues"].append(duplicate["cues"][0])
        with self.assertRaises(ocr_review.ReviewError):
            ai_ocr_review.import_response(self.review_id, duplicate)
        invalid = response(); invalid["cues"][0]["start_ms"] = 1000; invalid["cues"][0]["status"] = "accepted"
        with self.assertRaises(ocr_review.ReviewError):
            ai_ocr_review.import_response(self.review_id, invalid)

    def test_all_proposal_states_are_available_but_never_promoted_and_changed_crop_hides_them(self):
        _path, _filename, _count = ai_ocr_review.create_package(self.review_id, [self.cue["cue_id"]])
        sidecar = next((self.pack / "ai-ocr-review").glob("*.local.json"))
        request_id = sidecar.stem.removesuffix(".local")
        for proposal_status in ("confirmed", "correction_proposed", "uncertain"):
            template = {"schema_version": 1, "request_id": request_id,
                        "cues": [{"cue_id": self.cue["cue_id"], "start_ms": 1000, "end_ms": 2000,
                                  "status": "uncertain", "observed_text": "", "proposed_text": None,
                                  "reason": "template"}]}
            response = self.make_response(template, proposal_status)
            self.assertEqual(ai_ocr_review.import_response(self.review_id, response)["imported"], 1)
        self.assertEqual({row["status"] for row in ai_ocr_review.status(self.review_id)["proposals"]},
                         {"confirmed", "correction_proposed", "uncertain"})
        crop_path = self.pack / "crops" / f"{self.cue['cue_id']}.jpg"
        crop_path.write_bytes(crop_path.read_bytes() + b"changed")
        self.assertEqual(ai_ocr_review.status(self.review_id)["proposals"], [])
        self.assertIsNone(ocr_review.pack_status(self.review_id)["cues"][0]["active_event"])

    def test_stale_pack_or_missing_human_decision_does_not_block_package_but_stale_srt_blocks_import(self):
        _path, _filename, count = ai_ocr_review.create_package(self.review_id, [self.cue["cue_id"]])
        self.assertEqual(count, 1)  # no prior review-events.jsonl needed
        (self.pack / "synthetic.srt").write_text("changed", encoding="utf-8")
        with self.assertRaises(ocr_review.ReviewError):
            ai_ocr_review.status(self.review_id)

    def test_empty_or_duplicate_selection_rejected(self):
        for selection in ([], [self.cue["cue_id"], self.cue["cue_id"]]):
            with self.assertRaises(ocr_review.ReviewError):
                ai_ocr_review.create_package(self.review_id, selection)

    def test_local_api_exposes_manual_package_and_proposal_status(self):
        client = ui_server.app.test_client()
        page = client.get("/ocr-inceleme").get_data(as_text=True)
        self.assertIn("AI görsel incelemesine dahil et", page)
        self.assertIn("AI OCR görsel incelemesi", page)
        response = client.post("/api/ai-ocr-gorsel/paket", json={
            "review_id": self.review_id, "cue_ids": [self.cue["cue_id"]]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")
        response.close()
        status = client.get("/api/ai-ocr-gorsel/durum", query_string={"review_id": self.review_id})
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json["ai_output_is_human_label"])


if __name__ == "__main__":
    unittest.main()
