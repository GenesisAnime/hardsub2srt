import unittest

from srt2ass import parse_srt


class Srt2AssTests(unittest.TestCase):
    def test_rejects_zero_length_and_reversed_cues(self):
        text = (
            "1\n00:00:01,000 --> 00:00:01,000\nzero\n\n"
            "2\n00:00:02,000 --> 00:00:01,000\nreversed\n"
        )
        self.assertEqual(parse_srt(text), [])

    def test_rejects_out_of_range_minutes_and_seconds(self):
        text = (
            "1\n00:60:00,000 --> 00:61:00,000\ninvalid minutes\n\n"
            "2\n00:00:60,000 --> 00:01:60,000\ninvalid seconds\n"
        )
        self.assertEqual(parse_srt(text), [])

    def test_keeps_positive_duration_cue_and_hours_over_24(self):
        text = "1\n27:00:00,000 --> 27:00:01,000\nvalid\n"
        blocks = parse_srt(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], (27, 0, 0, 0))
        self.assertEqual(blocks[0][1], (27, 0, 1, 0))


if __name__ == "__main__":
    unittest.main()
