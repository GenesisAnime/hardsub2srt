import unittest

from srt2ass import parse_srt


class Srt2AssTimestampBoundsTests(unittest.TestCase):
    def test_keeps_cues_at_100_hours_and_beyond(self):
        text = (
            "1\n100:00:00,000 --> 100:00:01,000\nfirst\n\n"
            "2\n876543210:00:00,000 --> 876543210:00:01,000\nsecond\n"
        )

        blocks = parse_srt(text)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0][0], (100, 0, 0, 0))
        self.assertEqual(blocks[0][1], (100, 0, 1, 0))
        self.assertEqual(blocks[1][0], (876543210, 0, 0, 0))

    def test_skips_hour_fields_beyond_safe_digit_bound(self):
        hours = "9" * 10
        text = f"1\n{hours}:00:00,000 --> {hours}:00:01,000\nsynthetic\n"

        self.assertEqual(parse_srt(text), [])

    def test_keeps_timestamp_line_suffixes_while_rejecting_overlong_hours(self):
        valid = "1\n100:00:00,000 --> 100:00:01,000 X1:1 X2:2 Y1:3 Y2:4\nsynthetic\n"
        oversized_hours = "9" * 10
        invalid = (f"2\n{oversized_hours}:00:00,000 --> "
                   f"{oversized_hours}:00:01,000\nsynthetic\n")

        blocks = parse_srt(valid + "\n" + invalid)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], (100, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
