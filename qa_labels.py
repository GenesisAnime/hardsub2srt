"""Unicode-capable text rendering for optional QA montage images.

Pillow stays optional: if no Unicode TrueType font is available, labels fall
back to OpenCV with Turkish letters transliterated to readable ASCII. This
module is used only by QA image generation, never by the OCR pipeline.
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

import numpy as np


_TURKISH_ASCII = str.maketrans({
    "Ç": "C", "ç": "c", "Ğ": "G", "ğ": "g", "İ": "I", "ı": "i",
    "Ö": "O", "ö": "o", "Ş": "S", "ş": "s", "Ü": "U", "ü": "u",
})


def _font_candidates() -> tuple[Path, ...]:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    return (
        windows / "arial.ttf",
        windows / "segoeui.ttf",
        windows / "tahoma.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    )


@lru_cache(maxsize=1)
def _unicode_font_path() -> str | None:
    """Return the first known system TrueType font, if one is installed."""
    try:
        from PIL import ImageFont
    except ImportError:
        return None

    for candidate in _font_candidates():
        if not candidate.is_file():
            continue
        try:
            # Loading here avoids selecting a present but unreadable font.
            ImageFont.truetype(str(candidate), 20)
        except (OSError, ValueError):
            continue
        return str(candidate)
    return None


def _ascii_fallback(text: str) -> str:
    """Transliterate Turkish letters; replace other non-ASCII glyphs safely."""
    text = text.translate(_TURKISH_ASCII)
    return "".join(ch if ord(ch) < 128 else "?" for ch in text)


def draw_qa_label(canvas, text: str, origin: tuple[int, int], color_bgr,
                  cv2_module) -> str:
    """Draw a label on a BGR NumPy canvas and return the renderer used.

    ``origin`` follows OpenCV's baseline convention. Pillow and font loading
    are attempted only when a QA image is actually being written.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        font_path = _unicode_font_path()
        if font_path:
            rgb = Image.fromarray(canvas[:, :, ::-1].copy())
            draw = ImageDraw.Draw(rgb)
            font = ImageFont.truetype(font_path, 20)
            draw.text(origin, text, font=font,
                      fill=tuple(reversed(tuple(color_bgr))), anchor="ls")
            canvas[:] = np.asarray(rgb)[:, :, ::-1]
            return "pillow"
    except (ImportError, OSError, ValueError, TypeError, AttributeError):
        # Optional renderer failure must never prevent OCR or QA output.
        pass

    cv2_module.putText(
        canvas, _ascii_fallback(text), origin,
        cv2_module.FONT_HERSHEY_SIMPLEX, 0.75, color_bgr, 1,
        cv2_module.LINE_AA,
    )
    return "opencv-ascii-fallback"
