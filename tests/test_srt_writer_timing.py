import unittest

from srt_format import render_srt_cues_with_timing_diagnostics


class SrtWriterTimingTests(unittest.TestCase):
    @staticmethod
    def frame_time(frame):
        return frame / 10.0

    def test_no_clamp_keeps_existing_srt_bytes_and_has_no_diagnostic(self):
        cues = [(0, 5, "first", 0.9), (8, 12, "second", 0.8)]
        rendered, clamps = render_srt_cues_with_timing_diagnostics(
            cues, self.frame_time)

        self.assertEqual(
            rendered,
            "1\n00:00:00,000 --> 00:00:00,600\nfirst\n\n"
            "2\n00:00:00,800 --> 00:00:01,300\nsecond\n\n")
        self.assertEqual(clamps, [])

    def test_positive_clamp_reports_raw_emitted_and_delta_without_text(self):
        cues = [(0, 10, "private cue text", 0.9), (8, 12, "next", 0.8)]
        rendered, clamps = render_srt_cues_with_timing_diagnostics(
            cues, self.frame_time)

        self.assertEqual(
            rendered.splitlines()[1],
            "00:00:00,000 --> 00:00:00,800")
        self.assertEqual(clamps, [{
            "cue_index": 0,
            "start_frame": 0,
            "end_frame_inclusive": 10,
            "raw_end": "00:00:01,100",
            "emitted_end": "00:00:00,800",
            "clamp_delta_ms": 300.0,
        }])
        self.assertNotIn("private cue text", repr(clamps))

    def test_touching_or_non_overlapping_cue_ends_are_not_clamps(self):
        touching = [(0, 9, "first", 1.0), (10, 12, "next", 1.0)]
        non_overlapping = [(0, 4, "first", 1.0), (10, 12, "next", 1.0)]

        _, touching_clamps = render_srt_cues_with_timing_diagnostics(
            touching, self.frame_time)
        _, non_overlapping_clamps = render_srt_cues_with_timing_diagnostics(
            non_overlapping, self.frame_time)

        self.assertEqual(touching_clamps, [])
        self.assertEqual(non_overlapping_clamps, [])


if __name__ == "__main__":
    unittest.main()
