import unittest

import ocr_review_signals


class OcrReviewSignalsTests(unittest.TestCase):
    def _codes(self, cue, languages=("tr",)):
        return {item["code"] for item in ocr_review_signals.review_signals(
            cue, languages=languages)}

    def test_low_confidence_is_flagged_for_human_review(self):
        codes = self._codes({"source_text_ocr": "Merhaba", "ocr_confidence": 0.5,
                             "start_ms": 0, "end_ms": 2000})
        self.assertIn("low_confidence", codes)

    def test_high_confidence_unexpected_script_still_gets_flagged(self):
        codes = self._codes({"source_text_ocr": "Merhaba привет",
                             "ocr_confidence": 0.99,
                             "start_ms": 0, "end_ms": 2000})
        self.assertIn("unexpected_script", codes)
        self.assertNotIn("low_confidence", codes)

    def test_suspicious_token_and_short_cue_are_flagged(self):
        codes = self._codes({"source_text_ocr": "k1ZS1n", "ocr_confidence": 0.96,
                             "start_ms": 0, "end_ms": 180})
        self.assertIn("suspicious_token", codes)
        self.assertIn("short_duration", codes)
        self.assertIn("high_cps", codes)

    def test_unusual_glyph_is_flagged_even_when_text_is_short(self):
        codes = self._codes({"source_text_ocr": "¿n", "ocr_confidence": 0.98,
                             "start_ms": 0, "end_ms": 1000})
        self.assertIn("unusual_glyph", codes)
        self.assertIn("very_short_cue", codes)

    def test_isolated_ocr_letters_and_zero_are_flagged(self):
        noisy = self._codes({"source_text_ocr": "Bize e gereken her şey 0 burada",
                             "ocr_confidence": 0.97,
                             "start_ms": 0, "end_ms": 4000})
        ordinary = self._codes({"source_text_ocr": "Ama o gün geldi.",
                                "ocr_confidence": 0.97,
                                "start_ms": 0, "end_ms": 2500})
        self.assertIn("suspicious_token", noisy)
        self.assertNotIn("suspicious_token", ordinary)

    def test_expected_japanese_text_and_normal_short_dialogue_are_not_flagged(self):
        japanese = self._codes({"source_text_ocr": "猫です", "ocr_confidence": 0.99,
                                "start_ms": 0, "end_ms": 1500}, ("ja",))
        ordinary = self._codes({"source_text_ocr": "Anne!", "ocr_confidence": 0.99,
                                 "start_ms": 0, "end_ms": 1500})
        self.assertEqual(japanese, set())
        self.assertEqual(ordinary, set())


if __name__ == "__main__":
    unittest.main()
