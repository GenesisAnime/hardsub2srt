import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import ocr_review
import ui_server
from review_bundle import build_review_pack


class Phase5MetadataUITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.srt = self.root / "synthetic-episode.srt"
        self.srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nSynthetic subtitle\n\n",
            encoding="utf-8",
        )
        self.pack, _ = build_review_pack(
            self.srt,
            [{"start_ms": 1000, "end_ms": 2000, "text": "Synthetic subtitle",
              "confidence": 0.91, "frame_index": 15, "frame_ms": 1500,
              "band_y": 100, "band_height": 80}],
            {"device": "cpu"},
            lambda *_args: np.full((40, 120, 3), 60, dtype=np.uint8),
        )
        self.review_id, self.public = ocr_review.open_pack(str(self.pack))
        self.cue_id = self.public["cues"][0]["cue_id"]
        self.client = ui_server.app.test_client()

    def tearDown(self):
        with ocr_review._LOCK:
            ocr_review._PACKS.pop(self.review_id, None)
        self.temporary.cleanup()

    def _export(self):
        decision = self.client.post("/api/ocr-inceleme/karar", json={
            "review_id": self.review_id,
            "cue_id": self.cue_id,
            "status": "accepted",
            "correction": "Synthetic subtitle",
        })
        self.assertEqual(decision.status_code, 200, decision.get_json())
        response = self.client.post("/api/ocr-inceleme/disari-aktar", json={
            "review_id": self.review_id,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["path"]

    def test_export_then_blank_metadata_template_is_created_as_sibling(self):
        dataset_name = self._export()
        response = self.client.post("/api/ocr-inceleme/metadata-sablonu", json={
            "review_id": self.review_id,
            "dataset_name": dataset_name,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        output = self.pack / response.get_json()["path"]
        dataset = self.pack / dataset_name
        self.assertEqual(output.parent, dataset.parent)
        self.assertNotEqual(output.parent, dataset)
        metadata = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(len(metadata["records"]), 1)
        row = metadata["records"][0]
        self.assertEqual(row["cue_id"], self.cue_id)
        self.assertEqual(row["source_id"], "")
        self.assertEqual(row["episode_id"], "")
        self.assertEqual(row["license"]["status"], "")
        self.assertEqual(row["license"]["identifier"], "")
        self.assertEqual(row["license"]["evidence_path"], "")
        self.assertEqual(row["license"]["evidence_sha256"], "")
        self.assertEqual(row["license"]["human_review"]["status"], "pending")
        self.assertIsNone(row["license"]["human_review"]["reviewer"])
        self.assertIsNone(row["license"]["human_review"]["reviewed_at"])

    def test_repeated_request_uses_new_target_and_never_overwrites(self):
        dataset_name = self._export()
        payload = {"review_id": self.review_id, "dataset_name": dataset_name}
        first = self.client.post("/api/ocr-inceleme/metadata-sablonu", json=payload)
        second = self.client.post("/api/ocr-inceleme/metadata-sablonu", json=payload)
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(second.status_code, 200, second.get_json())
        first_path = self.pack / first.get_json()["path"]
        second_path = self.pack / second.get_json()["path"]
        self.assertNotEqual(first_path, second_path)
        first_path.write_text("preserve this sentinel", encoding="utf-8")
        third = self.client.post("/api/ocr-inceleme/metadata-sablonu", json=payload)
        self.assertEqual(third.status_code, 200, third.get_json())
        self.assertEqual(first_path.read_text(encoding="utf-8"), "preserve this sentinel")

    def test_rejects_path_traversal_and_unexported_or_foreign_dataset(self):
        self._export()
        before = set(self.pack.iterdir())
        for dataset_name in ("../outside", "verified-ocr-dataset-missing"):
            response = self.client.post("/api/ocr-inceleme/metadata-sablonu", json={
                "review_id": self.review_id,
                "dataset_name": dataset_name,
            })
            self.assertNotEqual(response.status_code, 200)
        self.assertEqual(set(self.pack.iterdir()), before)

    def test_review_page_explains_template_and_rights_limit(self):
        response = self.client.get("/ocr-inceleme")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Boş Phase 5 metadata şablonu oluştur", page)
        self.assertIn("Kullanım hakkı veya izin onayı değildir", page)
        self.assertIn("/api/ocr-inceleme/metadata-sablonu", page)


if __name__ == "__main__":
    unittest.main()
