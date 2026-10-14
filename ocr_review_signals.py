"""Heuristic OCR review flags. These signals never edit or reject a cue."""
from __future__ import annotations

import re
import unicodedata

DEFAULT_CONFIDENCE_THRESHOLD = 0.75
DEFAULT_CPS_THRESHOLD = 20.0
_MARKUP_RE = re.compile(r"<[^>]*>|\{\\[^}]*\}")
_TOKEN_RE = re.compile(r"[^\s]+")
_ODD_GLYPHS = frozenset("¿��□■")
_EDGE_PUNCTUATION = ".,!?;:'\"()[]{}<>…—-"

_LANGUAGE_SCRIPTS = {
    "latin": {"tr", "en", "fr", "de", "es", "it", "pt", "nl", "sv",
              "no", "da", "fi", "is", "pl", "cs", "sk", "hu", "ro",
              "hr", "sl", "sq", "et", "lv", "lt", "id", "ms", "vi",
              "ja", "ko", "ch_sim", "ch_tra"},
    "cyrillic": {"ru", "uk", "bg", "mk", "be", "sr"},
    "arabic": {"ar", "fa", "ur"},
    "hebrew": {"he", "yi"},
    "greek": {"el"},
    "hiragana": {"ja"},
    "katakana": {"ja"},
    "cjk": {"ja", "ch_sim", "ch_tra"},
    "hangul": {"ko"},
}


def _languages(value: object) -> set[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return set()
    return {item.strip().lower() for item in values
            if isinstance(item, str) and item.strip()}


def _script(char: str) -> str | None:
    if not char.isalpha():
        return None
    name = unicodedata.name(char, "")
    for marker, result in (("LATIN", "latin"), ("CYRILLIC", "cyrillic"),
                           ("ARABIC", "arabic"), ("HEBREW", "hebrew"),
                           ("GREEK", "greek"), ("HIRAGANA", "hiragana"),
                           ("KATAKANA", "katakana"), ("HANGUL", "hangul"),
                           ("CJK", "cjk"), ("IDEOGRAPH", "cjk")):
        if marker in name:
            return result
    return "other"


def review_signals(cue: dict, languages: object = None,
                   confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
                   cps_threshold: float = DEFAULT_CPS_THRESHOLD) -> list[dict[str, str]]:
    """Return actionable review labels for one OCR cue, without changing it.

    Confidence is only one signal: a high-confidence cue may still be flagged
    for unexpected scripts, OCR-like token shapes, implausible brevity, or CPS.
    """
    text = cue.get("source_text_ocr", cue.get("text", ""))
    if not isinstance(text, str):
        text = ""
    visible = _MARKUP_RE.sub("", text).strip()
    compact = "".join(char for char in visible if not char.isspace())
    confidence = cue.get("ocr_confidence", cue.get("confidence"))
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    try:
        start_ms = int(cue.get("start_ms"))
        end_ms = int(cue.get("end_ms"))
        duration_ms = end_ms - start_ms
    except (TypeError, ValueError):
        duration_ms = 0

    flags: list[dict[str, str]] = []

    def add(code: str, label: str) -> None:
        flags.append({"code": code, "label": label})

    if confidence is not None and confidence < confidence_threshold:
        add("low_confidence", "Düşük OCR güveni; crop'u insan incelemesiyle doğrulayın")

    langs = _languages(languages)
    expected = {script for script, codes in _LANGUAGE_SCRIPTS.items()
                if langs & codes}
    observed = {_script(char) for char in visible} - {None}
    if expected and observed - expected:
        add("unexpected_script", "Altyazı dilinde beklenmeyen alfabe")

    odd_glyphs = _ODD_GLYPHS - ({"¿"} if "es" in langs else set())
    if any(char in odd_glyphs for char in visible):
        add("unusual_glyph", "Olağandışı OCR karakteri")

    tokens = _TOKEN_RE.findall(visible)
    token_shape_suspect = False
    allowed_singletons: set[str] = set()
    if langs & _LANGUAGE_SCRIPTS["latin"]:
        if "tr" in langs:
            allowed_singletons.update(("a", "o"))
        if langs & ({"en", "fr", "de", "es", "it", "pt", "nl", "sv",
                     "no", "da", "fi", "is", "pl", "cs", "sk", "hu",
                     "ro", "hr", "sl", "sq", "et", "lv", "lt", "id",
                     "ms", "vi"}):
            allowed_singletons.update(("a", "i"))
    previous_core = ""
    for token_index, token in enumerate(tokens):
        letters = [char for char in token if char.isalpha()]
        digits = [char for char in token if char.isdigit()]
        if letters and digits:
            token_shape_suspect = True
            break
        core = token.strip(_EDGE_PUNCTUATION)
        if core.isdigit() and (len(core) <= 2 or len(set(core)) == 1):
            token_shape_suspect = True
            break
        if (len(core) == 1 and core.isalpha() and allowed_singletons and
                0 < token_index < len(tokens) - 1):
            single = core.lower()
            if single not in allowed_singletons:
                token_shape_suspect = True
                break
            if previous_core.lower().endswith(single):
                token_shape_suspect = True
                break
        if len(letters) >= 4 and any(char.islower() for char in letters) and \
                sum(char.isupper() for char in letters[1:]) >= 2:
            token_shape_suspect = True
            break
        previous_core = core
    if token_shape_suspect:
        add("suspicious_token", "Karışık harf/rakam veya olağandışı büyük harf dizilimi")

    short_needs_review = len(compact) <= 2 and (
        confidence is None or confidence < max(confidence_threshold, 0.85) or
        any(char in odd_glyphs for char in visible) or duration_ms <= 250)
    if short_needs_review:
        add("very_short_cue", "Çok kısa cue; görüntüyle doğrulayın")

    if 0 < duration_ms < 250:
        add("short_duration", f"Çok kısa cue süresi ({duration_ms} ms); görüntüyle doğrulayın")

    if duration_ms > 0 and cps_threshold > 0:
        cps = len(compact) * 1000.0 / duration_ms
        if cps > cps_threshold:
            add("high_cps", f"Yüksek karakter/sn ({cps:.1f}); zamanlama ve metni inceleyin")

    return flags
