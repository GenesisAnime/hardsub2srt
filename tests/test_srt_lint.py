import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import srt_lint


class SrtLintTests(unittest.TestCase):
    def test_reports_chronology_overlap_cps_and_duration_as_advice(self):
        raw = (
            "1\n00:00:02,000 --> 00:00:04,000\n"
            "<i>one two three four five six</i>\n\n"
            "2\n00:00:01,000 --> 00:00:02,500\nshort\n"
        ).encode()
        cues, errors = srt_lint.parse_srt(raw, "synthetic.srt")
        warnings = srt_lint.lint_cues(cues, cps_warning=2, min_duration_ms=0,
                                       max_duration_ms=1000)
        self.assertEqual(errors, [])
        self.assertTrue(any("Sıra uyarısı" in warning for warning in warnings))
        self.assertTrue(any("Çakışma adayı" in warning for warning in warnings))
        self.assertTrue(any("CPS adayı" in warning for warning in warnings))
        self.assertTrue(any("Süre adayı" in warning for warning in warnings))
        self.assertEqual(cues[0]["end_ms"], 4000)  # lint never clamps/rewrites

    def test_parse_reports_bad_timestamp_empty_text_and_nonpositive_duration(self):
        raw = (
            "1\n00:00:xx,000 --> 00:00:02,000\ntext\n\n"
            "2\n00:00:03,000 --> 00:00:02,000\ntext\n\n"
            "3\n00:00:03,000 --> 00:00:04,000\n   \n"
        ).encode()
        cues, errors = srt_lint.parse_srt(raw)
        self.assertEqual(cues, [])
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("zaman damgası" in error for error in errors))
        self.assertTrue(any("bitiş başlangıçtan sonra değil" in error for error in errors))
        self.assertTrue(any("metin eksik" in error or "metni boş" in error
                            for error in errors))

    def test_thresholds_can_disable_heuristic_warnings(self):
        cues, errors = srt_lint.parse_srt(
            b"1\n00:00:00,000 --> 00:00:00,100\nLong subtitle text\n"
        )
        self.assertEqual(errors, [])
        self.assertEqual(srt_lint.lint_cues(cues, 0, 0, 0), [])

    def test_nonadjacent_overlap_is_reported(self):
        raw = (
            "1\n00:00:00,000 --> 00:00:10,000\nlong\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nshort\n\n"
            "3\n00:00:03,000 --> 00:00:04,000\nshort\n"
        ).encode()
        cues, errors = srt_lint.parse_srt(raw)
        warnings = srt_lint.lint_cues(cues, 0, 0, 0)
        self.assertEqual(errors, [])
        self.assertEqual(sum("Çakışma adayı" in warning for warning in warnings), 2)

    def test_empty_or_non_utf8_file_is_reported(self):
        self.assertTrue(srt_lint.parse_srt(b"")[1])
        self.assertIn("UTF-8", srt_lint.parse_srt(b"\xff")[1][0])

    def test_cli_returns_success_for_heuristics_but_reports_parse_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "synthetic.srt"
            path.write_text("1\n00:00:00,000 --> 00:00:01,000\ntext\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = srt_lint.main([str(path), "--cps-warning", "1"])
            self.assertEqual(result, 0)
            self.assertIn("UYARI (sezgisel)", output.getvalue())
            path.write_text("broken\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = srt_lint.main([str(path)])
            self.assertEqual(result, 1)
            self.assertIn("HATA:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
