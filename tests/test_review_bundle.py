import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from review_bundle import build_review_pack


class ReviewBundleTests(unittest.TestCase):
    def test_bundle_maps_cues_deduplicates_and_marks_missing_crop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            srt = root / "episode.srt"
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n",
                           encoding="utf-8")
            calls = []

            def crop(frame_index, band_y, band_height):
                calls.append(frame_index)
                self.assertEqual((band_y, band_height), (100, 80))
                if frame_index == 30:
                    raise RuntimeError("synthetic missing frame")
                return np.full((40, 120, 3), 60, dtype=np.uint8)

            cues = [
                {"start_ms": 1000, "end_ms": 2000, "text": "Hello",
                 "confidence": 0.91, "frame_index": 15, "frame_ms": 1500,
                 "band_y": 100, "band_height": 80},
                {"start_ms": 2000, "end_ms": 3000, "text": "Hello",
                 "confidence": 0.88, "frame_index": 16, "frame_ms": 2500,
                 "band_y": 100, "band_height": 80},
                {"start_ms": 3000, "end_ms": 4000, "text": "Again",
                 "confidence": 0.42, "frame_index": 30, "frame_ms": 3500,
                 "band_y": 100, "band_height": 80},
            ]

            target, manifest = build_review_pack(
                srt, cues, {"device": "cpu"}, crop)

            self.assertEqual(target.name, "episode.review-pack")
            self.assertEqual((target / "episode.srt").read_bytes(), srt.read_bytes())
            self.assertEqual(calls, [15, 16, 30])
            self.assertEqual(manifest["cue_count"], 3)
            self.assertEqual(manifest["unique_crop_count"], 1)
            self.assertFalse(manifest["ai_api_called"])
            self.assertFalse(manifest["upload_performed"])
            self.assertEqual(manifest["cues"][0]["crop_status"],
                             "available_needs_visual_confirmation")
            self.assertEqual(manifest["cues"][1]["duplicate_of_cue_id"],
                             manifest["cues"][0]["cue_id"])
            self.assertEqual(manifest["cues"][2]["crop_status"], "unavailable")
            self.assertEqual(manifest["cues"][2]["crop_path"], None)
            self.assertEqual(len(list((target / "crops").glob("*.jpg"))), 1)

            on_disk = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("video_path", on_disk)

    def test_existing_bundle_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            srt = root / "episode.srt"
            srt.write_text("SRT", encoding="utf-8")
            target = root / "episode.review-pack"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                build_review_pack(srt, [], {}, lambda _frame: None)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_missing_source_frame_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            srt = root / "episode.srt"
            srt.write_text("SRT", encoding="utf-8")
            target, manifest = build_review_pack(
                srt,
                [{"start_ms": 0, "end_ms": 1000, "text": "Merged",
                  "confidence": 0.8, "frame_index": None, "frame_ms": None,
                  "mapping_status": "merged_or_ambiguous_source_frame_unavailable",
                  "band_y": 0, "band_height": 100}],
                {},
                lambda *_args: self.fail("must not capture an ambiguous cue"))

            cue = manifest["cues"][0]
            self.assertEqual(cue["crop_status"], "unavailable")
            self.assertEqual(cue["mapping_status"],
                             "merged_or_ambiguous_source_frame_unavailable")
            self.assertIsNone(cue["crop_path"])
            self.assertTrue((target / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
