# -*- coding: utf-8 -*-
"""Pathologically long SRT/VTT hour fields must fail as invalid input.

Python bounds int() string conversion at 4,300 digits by default, so an
unbounded hour field turns a malformed-cue path into a raw ValueError instead
of the parser's documented "invalid input" result. The SRT linter and the
Phase 5 video-evaluation adapter already bound this field; the review-pack
validator, the learning parser and the VTT comparison must agree, otherwise a
crafted SRT escapes as an unhandled 500 (review pack) or aborts the
"Kaydet = öğren" / VTT comparison steps with a traceback.

Practical long-form timelines (up to nine hour digits) keep working.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import ocr_review
import ogren

ROOT = Path(__file__).resolve().parents[1]

# vtt-qa.py is not an importable module name (hyphen), so load it by path.
# Register it before execution: @dataclass resolves annotations through
# sys.modules[cls.__module__].
_SPEC = importlib.util.spec_from_file_location("vtt_qa", ROOT / "vtt-qa.py")
vtt_qa = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = vtt_qa
_SPEC.loader.exec_module(vtt_qa)

OVERSIZED_HOUR = "9" * 4301
LONG_HOUR = "999999999"
HOUR_MS = 999999999 * 3600 * 1000


class ReviewPackTimestampTests(unittest.TestCase):
    def test_oversized_hour_reaches_the_documented_review_error(self):
        raw = (f"1\n{OVERSIZED_HOUR}:00:00,000 --> {OVERSIZED_HOUR}:00:01,000\n"
               "metin\n").encode("utf-8")
        with self.assertRaises(ocr_review.ReviewError) as caught:
            ocr_review._srt_cues_bytes(raw)
        self.assertEqual(caught.exception.status, 422)

    def test_long_but_reasonable_hour_still_parses(self):
        raw = (f"1\n{LONG_HOUR}:00:00,000 --> {LONG_HOUR}:00:01,000\n"
               "metin\n").encode("utf-8")
        cues = ocr_review._srt_cues_bytes(raw)
        self.assertEqual(cues[0]["start_ms"], HOUR_MS)
        self.assertEqual(cues[0]["end_ms"] - cues[0]["start_ms"], 1000)


class LearningParserTimestampTests(unittest.TestCase):
    def test_oversized_hour_is_skipped_instead_of_raising(self):
        self.assertIsNone(ogren._ts_ms(f"{OVERSIZED_HOUR}:00:00,000"))
        text = (f"1\n{OVERSIZED_HOUR}:00:00,000 --> {OVERSIZED_HOUR}:00:01,000\n"
                "metin\n")
        self.assertEqual(ogren.srt_parse(text), [])

    def test_long_but_reasonable_hour_still_parses(self):
        self.assertEqual(ogren._ts_ms(f"{LONG_HOUR}:00:01,000"), HOUR_MS + 1000)


class VttComparisonTimestampTests(unittest.TestCase):
    def test_oversized_hour_is_rejected_instead_of_raising(self):
        self.assertIsNone(vtt_qa.parse_ts(f"{OVERSIZED_HOUR}:00:00.000"))

    def test_long_but_reasonable_hour_still_parses(self):
        self.assertEqual(vtt_qa.parse_ts(f"{LONG_HOUR}:00:01.000"), HOUR_MS + 1000)


if __name__ == "__main__":
    unittest.main()
