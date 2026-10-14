import argparse
import unittest

import hardsub2srt


class OcrLanguageArgumentTests(unittest.TestCase):
    def test_language_codes_are_normalized_and_duplicates_removed(self):
        self.assertEqual(hardsub2srt._parse_ocr_languages("TR, en, tr"), "tr,en")

    def test_language_codes_reject_shell_text_and_empty_items(self):
        for value in ("tr;bad", "tr,,en", "", "tr en"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    hardsub2srt._parse_ocr_languages(value)


if __name__ == "__main__":
    unittest.main()
