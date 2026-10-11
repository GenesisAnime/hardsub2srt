import unittest
from unittest.mock import patch

import cv2
import numpy as np

import qa_labels


class QaLabelTests(unittest.TestCase):
    def test_turkish_diacritics_are_rendered_by_unicode_font_when_available(self):
        font_path = qa_labels._unicode_font_path()
        if font_path is None:
            self.skipTest("No supported system TrueType font is installed")
        from PIL import ImageFont

        font = ImageFont.truetype(font_path, 20)
        replacement = font.getmask("?")
        for character in "ÇçĞğİıÖöŞşÜü":
            with self.subTest(character=character):
                glyph = font.getmask(character)
                self.assertNotEqual((glyph.size, bytes(glyph)),
                                    (replacement.size, bytes(replacement)))

        canvas = np.zeros((70, 520, 3), dtype=np.uint8)
        rendered_by = qa_labels.draw_qa_label(
            canvas, "ŞĞİÖÜ şğıöü çÇ", (4, 48), (255, 255, 255), cv2)

        self.assertEqual(rendered_by, "pillow")
        self.assertGreater(np.count_nonzero(canvas), 0)

    def test_fallback_transliterates_turkish_instead_of_question_marks(self):
        canvas = np.zeros((70, 520, 3), dtype=np.uint8)
        with patch.object(qa_labels, "_unicode_font_path", return_value=None):
            rendered_by = qa_labels.draw_qa_label(
                canvas, "ŞĞİÖÜ şğıöü çÇ", (4, 48), (255, 255, 255), cv2)

        self.assertEqual(rendered_by, "opencv-ascii-fallback")
        self.assertEqual(qa_labels._ascii_fallback("ŞĞİÖÜ şğıöü çÇ"),
                         "SGIOU sgiou cC")
        self.assertGreater(np.count_nonzero(canvas), 0)

    def test_missing_or_unreadable_optional_font_keeps_fallback_working(self):
        canvas = np.zeros((70, 520, 3), dtype=np.uint8)
        with patch.object(qa_labels, "_unicode_font_path", return_value=None):
            rendered_by = qa_labels.draw_qa_label(
                canvas, "Kaynak: görsel doğru", (4, 48),
                (255, 255, 255), cv2)

        self.assertEqual(rendered_by, "opencv-ascii-fallback")
        self.assertGreater(np.count_nonzero(canvas), 0)


if __name__ == "__main__":
    unittest.main()
