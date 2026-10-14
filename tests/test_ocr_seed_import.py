"""Synthetic coverage for assistant-reviewed OCR seed imports."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import ocr_review
import ocr_seed_import as seed_import


class SeedImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hardsub-seed-import-synthetic-")
        self.root = Path(self.tmp.name).resolve()
        self.seed_root = self.root / "input"
        (self.seed_root / "evidence").mkdir(parents=True)
        self.image_bytes = self._synthetic_png()
        (self.seed_root / "evidence" / "sample.png").write_bytes(self.image_bytes)
        self.seed_path = self.seed_root / "training-seed.jsonl"
        self.app_root = self.root / "app-local"
        self.app_root.mkdir()

    def tearDown(self):
        ocr_review._PACKS.clear()
        self.tmp.cleanup()

    @staticmethod
    def _synthetic_png():
        image = np.zeros((48, 240, 3), dtype=np.uint8)
        image[10:38, 20:220] = (230, 230, 230)
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise AssertionError("synthetic PNG encoding failed")
        return encoded.tobytes()

    def _row(self, **overrides):
        row = {
            "schema": seed_import.SEED_SCHEMA,
            "version": seed_import.SEED_VERSION,
            "image": "evidence/sample.png",
            "image_sha256": hashlib.sha256(self.image_bytes).hexdigest(),
            "episode_key": "S01E01",
            "video_identity": {
                "size": 123456,
                "sha1_first_1mb": "a" * 40,
            },
            "cue_start_ms": 1000,
            "cue_end_ms": 2000,
            "observed_srt_text": "SYNTHETIC OBSERVED TEXT",
            "target_text": "SYNTHETIC CANDIDATE TEXT",
            "label_source": "assistant_visual_review",
            "action": "replace_text",
            "training_eligible": False,
            "user_confirmation_status": "pending",
            "rights_status": "not_verified",
            "dataset_status": "candidate_needs_human_review",
        }
        row.update(overrides)
        return row

    def _write_rows(self, rows):
        self.seed_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8")

    def _import(self, rows=None):
        self._write_rows(rows or [self._row()])
        # Match the documented LocalAppData destination. The UI discovers
        # imports by the ``seed-review-*`` directory prefix.
        output = self.app_root / "seed-review-example"
        target, report = seed_import.import_seed(self.seed_path, output)
        return target, report

    def test_successful_import_stays_unreviewed_and_exports_no_labels(self):
        target, report = self._import()

        self.assertEqual(report["human_decisions_created"], 0)
        self.assertEqual(report["exportable_labels_created_by_import"], 0)
        self.assertFalse(report["training_eligible"])
        self.assertEqual(report["review_packs_created"], 1)
        self.assertFalse((target / "review-packs" / "s01e01-aaaaaaaa-01.review-pack" /
                          "review-events.jsonl").exists())

        packs = seed_import.list_seed_review_packs(self.app_root)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0]["reviewed_count"], 0)
        self.assertEqual(packs[0]["candidate_count"], 1)
        pack_path = seed_import.open_seed_review_pack(self.app_root, packs[0]["pack_id"])
        review_id, public = ocr_review.open_pack(str(pack_path))
        self.assertIsNone(public["cues"][0]["active_event"])
        self.assertEqual(public["cues"][0]["seed_candidate_text"], "SYNTHETIC CANDIDATE TEXT")
        self.assertTrue(public["seed_import"])

        dataset, count = ocr_review.export_dataset(review_id)
        self.assertEqual(count, 0)
        self.assertEqual((dataset / "manifest.jsonl").read_text(encoding="utf-8"), "")

    def test_rejects_parent_traversal_in_evidence_path(self):
        self._write_rows([self._row(image="evidence/../outside.png")])
        with self.assertRaises(seed_import.ImportError):
            seed_import.import_seed(self.seed_path, None)

    def test_rejects_duplicate_json_keys(self):
        row_text = json.dumps(self._row(), ensure_ascii=False)
        # A duplicate key must not be silently resolved by json.loads.
        self.seed_path.write_text(row_text[:-1] + ',"version":1}\n', encoding="utf-8")
        with self.assertRaisesRegex(seed_import.ImportError, "geçersiz JSON"):
            seed_import.import_seed(self.seed_path, None)

    def test_rejects_duplicate_video_episode_cue_rows(self):
        row = self._row()
        self._write_rows([row, row.copy()])
        with self.assertRaisesRegex(seed_import.ImportError, "Yinelenen video/episode/cue"):
            seed_import.import_seed(self.seed_path, None)

    def test_rejects_bad_hash_and_invalid_row_schema(self):
        self._write_rows([self._row(image_sha256="0" * 64)])
        with self.assertRaisesRegex(seed_import.ImportError, "SHA-256"):
            seed_import.import_seed(self.seed_path, None)

        invalid = self._row()
        invalid["unexpected"] = "not accepted"
        self._write_rows([invalid])
        with self.assertRaisesRegex(seed_import.ImportError, "tam v1 şeması"):
            seed_import.import_seed(self.seed_path, None)

    def test_opaque_ids_reject_paths_unknown_ids_and_tampered_crop(self):
        target, _report = self._import()
        with self.assertRaises(seed_import.ImportError):
            seed_import.open_seed_review_pack(self.app_root, "../review-packs")
        with self.assertRaises(seed_import.ImportError):
            seed_import.open_seed_review_pack(self.app_root, "f" * 32)

        pack = target / "review-packs" / "s01e01-aaaaaaaa-01.review-pack"
        crop = next((pack / "crops").glob("*.jpg"))
        crop.write_bytes(b"tampered synthetic image")
        self.assertEqual(seed_import.list_seed_review_packs(self.app_root), [])

    def test_check_mode_validates_without_writing(self):
        self._write_rows([self._row()])
        target, report = seed_import.import_seed(self.seed_path, None)
        self.assertIsNone(target)
        self.assertEqual(report["assistant_candidates"], 1)
        self.assertEqual(report["human_decisions_created"], 0)
        self.assertFalse(any(self.app_root.iterdir()))


if __name__ == "__main__":
    unittest.main()
