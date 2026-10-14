import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ocr_seed_import as seed_import


def _row(image_name: str, data: bytes) -> dict:
    return {
        "schema": seed_import.SEED_SCHEMA,
        "version": seed_import.SEED_VERSION,
        "image": f"evidence/{image_name}",
        "image_sha256": hashlib.sha256(data).hexdigest(),
        "episode_key": "s01e01",
        "video_identity": {"size": 123, "sha1_first_1mb": "a" * 40},
        "cue_start_ms": 1000,
        "cue_end_ms": 2000,
        "observed_srt_text": "observed text",
        "target_text": "candidate text",
        "label_source": "assistant_visual_review",
        "action": "replace_text",
        "training_eligible": False,
        "user_confirmation_status": "pending",
        "rights_status": "not_verified",
        "dataset_status": "candidate_needs_human_review",
    }


class SeedImportMemoryLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "evidence").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_aggregate_evidence_limit_rejects_before_reading_next_image(self):
        first = b"first-image"
        second = b"second-image"
        first_path = self.root / "evidence" / "first.jpg"
        second_path = self.root / "evidence" / "second.jpg"
        first_path.write_bytes(first)
        second_path.write_bytes(second)
        images = {}
        crop_cache = {}

        with patch.object(seed_import, "MAX_EVIDENCE_FILE_BYTES", 64), \
                patch.object(seed_import, "MAX_EVIDENCE_TOTAL_BYTES", len(first) + 2), \
                patch.object(seed_import, "_normalize_crop", return_value=(b"crop", 1, 1)):
            seed_import._validate_row(_row("first.jpg", first), self.root, images, crop_cache)
            self.assertEqual(sum(len(data) for _path, data in images.values()), len(first))
            # The second file is individually small, but exceeds the remaining
            # aggregate budget. Its stat size must reject it before open/read.
            with patch.object(Path, "open", autospec=True,
                              side_effect=AssertionError("over-budget evidence was opened")):
                with self.assertRaisesRegex(seed_import.ImportError, "birikimli bayt sınırını"):
                    seed_import._validate_row(
                        _row("second.jpg", second), self.root, images, crop_cache)
        self.assertEqual(set(images), {"evidence/first.jpg"})

    def test_bounded_read_rejects_growth_after_size_check(self):
        class GrowingFile:
            def __init__(self):
                self.requested = []

            def stat(self):
                return SimpleNamespace(st_size=4)

            def open(self, _mode):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                self.requested.append(size)
                return b"x" * size

        growing = GrowingFile()
        with self.assertRaisesRegex(seed_import.ImportError, "birikimli bayt sınırını"):
            seed_import._read_bounded(growing, 4, "Özgün evidence")
        self.assertEqual(growing.requested, [5])

    def test_crop_cache_limit_is_checked_before_adding_each_normalized_crop(self):
        first = b"first-image"
        second = b"second-image"
        (self.root / "evidence" / "first.jpg").write_bytes(first)
        (self.root / "evidence" / "second.jpg").write_bytes(second)
        images = {}
        crop_cache = {}
        normalize = lambda data: ((b"crop1" if data == first else b"crop2"), 1, 1)

        with patch.object(seed_import, "MAX_EVIDENCE_FILE_BYTES", 64), \
                patch.object(seed_import, "MAX_EVIDENCE_TOTAL_BYTES", 64), \
                patch.object(seed_import, "MAX_IMPORT_CROP_BYTES", 7), \
                patch.object(seed_import, "_normalize_crop", side_effect=normalize):
            seed_import._validate_row(_row("first.jpg", first), self.root, images, crop_cache)
            with self.assertRaisesRegex(seed_import.ImportError, "Normalize edilmiş crop toplamı"):
                seed_import._validate_row(
                    _row("second.jpg", second), self.root, images, crop_cache)
        self.assertEqual(sum(len(crop) for crop, _w, _h in crop_cache.values()), 5)
        self.assertEqual(len(crop_cache), 1)


if __name__ == "__main__":
    unittest.main()
