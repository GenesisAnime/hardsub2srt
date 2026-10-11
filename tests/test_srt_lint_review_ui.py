import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import ocr_review
import ui_server
from review_bundle import build_review_pack


class SrtLintReviewUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.srt = root / "synthetic-quality.srt"
        rows = [
            (1000, 2500,
             "Synthetic caption with enough visible characters to exceed the heuristic CPS threshold"),
            (2000, 2200, "tiny"),
            (1800, 9500, "long"),
        ]
        blocks = [f"{index}\n00:00:{start // 1000:02d},{start % 1000:03d} --> "
                  f"00:00:{end // 1000:02d},{end % 1000:03d}\n{text}"
                  for index, (start, end, text) in enumerate(rows, 1)]
        self.srt.write_text("\n\n".join(blocks) + "\n\n", encoding="utf-8")
        cues = [{"start_ms": start, "end_ms": end, "text": text,
                 "confidence": 0.9, "frame_index": 30 + index,
                 "frame_ms": start, "band_y": 100, "band_height": 80}
                for index, (start, end, text) in enumerate(rows)]
        crop = np.full((24, 120, 3), 90, dtype=np.uint8)
        self.pack, _manifest = build_review_pack(
            self.srt, cues, {"synthetic": True}, lambda *_: crop)
        self.review_id, _ = ocr_review.open_pack(str(self.pack))
        self.client = ui_server.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_report_is_advisory_bounded_and_does_not_write_srt_or_review_events(self):
        srt_before = self.srt.read_bytes()
        response = self.client.get("/api/ocr-inceleme/srt-lint",
                                   query_string={"review_id": self.review_id})

        self.assertEqual(response.status_code, 200)
        report = response.get_json()
        self.assertEqual(report["cue_count"], 3)
        self.assertEqual(report["parse_error_count"], 0)
        self.assertTrue(report["read_only"])
        self.assertTrue(report["heuristic_only"])
        self.assertGreater(report["warning_count"], 0)
        rendered = "\n".join(report["warnings"])
        for category in ("CPS adayı", "Süre adayı", "Çakışma adayı", "Sıra uyarısı"):
            self.assertIn(category, rendered)
        self.assertNotIn("Synthetic caption", response.get_data(as_text=True))
        self.assertEqual(self.srt.read_bytes(), srt_before)
        self.assertFalse((self.pack / "review-events.jsonl").exists())
        self.assertFalse(list(self.pack.glob("verified-ocr-dataset-*/manifest.jsonl")))

    def test_report_fails_closed_when_srt_changes_after_pack_open(self):
        (self.pack / self.srt.name).write_text("changed after opening", encoding="utf-8")
        response = self.client.get("/api/ocr-inceleme/srt-lint",
                                   query_string={"review_id": self.review_id})
        self.assertEqual(response.status_code, 409)

    def test_report_hashes_exact_bytes_read_after_source_check(self):
        original_get_pack = ocr_review._get_pack

        def mutate_after_source_check(review_id, force_sources=False):
            info = original_get_pack(review_id, force_sources=force_sources)
            if force_sources:
                path = info["srt_path"]
                before = path.stat()
                raw = path.read_bytes()
                changed = raw.replace(b"tiny", b"xxxx", 1)
                self.assertEqual(len(changed), len(raw))
                self.assertNotEqual(changed, raw)
                path.write_bytes(changed)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                after = path.stat()
                self.assertEqual(after.st_size, before.st_size)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            return info

        with (patch("ocr_review._get_pack", side_effect=mutate_after_source_check),
              patch("srt_lint.parse_srt") as parse_srt,
              patch("srt_lint.lint_cues") as lint_cues):
            response = self.client.get("/api/ocr-inceleme/srt-lint",
                                       query_string={"review_id": self.review_id})

        self.assertEqual(response.status_code, 409)
        self.assertIn("SRT tarama sırasında değişti", response.get_json()["hata"])
        parse_srt.assert_not_called()
        lint_cues.assert_not_called()

    def test_report_caps_warning_details_but_preserves_total_count(self):
        with patch("srt_lint.lint_cues", return_value=[f"warning {i}" for i in range(501)]):
            response = self.client.get("/api/ocr-inceleme/srt-lint",
                                       query_string={"review_id": self.review_id})
        report = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(report["warning_count"], 501)
        self.assertEqual(len(report["warnings"]), 500)
        self.assertTrue(report["warnings_truncated"])

    def test_review_page_exposes_manual_read_only_report(self):
        page = self.client.get("/ocr-inceleme").get_data(as_text=True)
        self.assertIn("SRT taramasını çalıştır", page)
        self.assertIn("/api/ocr-inceleme/srt-lint", page)
        self.assertIn("SRT'yi, OCR kararlarını ve öğrenme verisini değiştirmez", page)


if __name__ == "__main__":
    unittest.main()
