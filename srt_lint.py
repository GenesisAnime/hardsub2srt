#!/usr/bin/env python3
"""Read-only, heuristic SRT quality report; never edits subtitles or OCR output."""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

MAX_SRT_BYTES = 64 * 1024 * 1024
MAX_CUES = 100_000
MAX_TEXT_CHARS = 4096
MAX_OVERLAP_PAIRS = 200_000
TIMESTAMP_RE = re.compile(r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3})$")
MARKUP_RE = re.compile(r"<[^>]*>|\{\\[^}]*\}")


def _timestamp_ms(value: str) -> int | None:
    match = TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        return None
    hours, minutes, seconds, millis = map(int, match.groups())
    if minutes >= 60 or seconds >= 60:
        return None
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_srt(raw: bytes, label: str = "SRT") -> tuple[list[dict], list[str]]:
    """Parse SRT blocks, retaining valid cues and collecting syntax errors."""
    if len(raw) > MAX_SRT_BYTES:
        return [], [f"{label}: dosya {MAX_SRT_BYTES} bayt sınırını aşıyor"]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        return [], [f"{label}: UTF-8 olarak okunamadı"]
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return [], [f"{label}: dosya boş"]
    blocks = [b for b in re.split(r"\n[ \t]*\n+", text) if b.strip()]
    if len(blocks) > MAX_CUES:
        return [], [f"{label}: cue sayısı {MAX_CUES} sınırını aşıyor"]

    cues = []
    errors = []
    for block_index, block in enumerate(blocks, 1):
        lines = block.split("\n")
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if len(lines) < 2:
            errors.append(f"{label}: {block_index}. blokta zaman satırı veya metin eksik")
            continue
        timing = lines[0].strip()
        left, sep, right = timing.partition("-->")
        start = _timestamp_ms(left) if sep else None
        end = _timestamp_ms(right) if sep else None
        if start is None or end is None:
            errors.append(f"{label}: {block_index}. blokta zaman damgası biçimi geçersiz")
            continue
        cue_text = "\n".join(lines[1:]).strip()
        if start >= end:
            errors.append(f"{label}: {block_index}. blokta bitiş başlangıçtan sonra değil")
            continue
        if not cue_text:
            errors.append(f"{label}: {block_index}. blokta altyazı metni boş")
            continue
        if len(cue_text) > MAX_TEXT_CHARS:
            errors.append(f"{label}: {block_index}. blok metni {MAX_TEXT_CHARS} karakter sınırını aşıyor")
            continue
        cues.append({"index": block_index, "start_ms": start, "end_ms": end,
                     "text": cue_text})
    return cues, errors


def _visible_char_count(text: str) -> int:
    # This is a reproducible heuristic, not a language-specific reading model.
    visible = MARKUP_RE.sub("", text)
    return sum(not char.isspace() for char in visible)


def lint_cues(cues: list[dict], cps_warning: float = 20.0,
              min_duration_ms: int = 250, max_duration_ms: int = 7000) -> list[str]:
    """Return advisory messages; cues and their times are never modified."""
    warnings = []
    previous = None
    for cue in cues:
        if previous and cue["start_ms"] < previous["start_ms"]:
            warnings.append(
                f"Sıra uyarısı: cue {cue['index']} başlangıcı cue {previous['index']} "
                "başlangıcından önce geliyor (dosya sırası kronolojik değil)."
            )
        previous = cue

        duration = cue["end_ms"] - cue["start_ms"]
        if cps_warning > 0:
            chars = _visible_char_count(cue["text"])
            cps = chars * 1000 / duration
            if cps > cps_warning:
                warnings.append(
                    f"CPS adayı: cue {cue['index']} yaklaşık {cps:.1f} karakter/sn "
                    f"({chars} boşluk dışı karakter); ayarlı {cps_warning:g} eşiğini aşıyor."
                )
        if min_duration_ms > 0 and duration < min_duration_ms:
            warnings.append(
                f"Süre adayı: cue {cue['index']} {duration} ms; ayarlı alt "
                f"eşik {min_duration_ms} ms altında."
            )
        if max_duration_ms > 0 and duration > max_duration_ms:
            warnings.append(
                f"Süre adayı: cue {cue['index']} {duration} ms; ayarlı üst "
                f"eşik {max_duration_ms} ms üzerinde."
            )

    # Sweep by start time to detect non-adjacent overlaps while preserving the
    # original cue order for cue IDs. Bound dense synthetic/adversarial inputs.
    active = []
    comparisons = 0
    overlaps = 0
    overlap_detail_count = 0
    overlap_scan_capped = False
    ordered = sorted(cues, key=lambda c: (c["start_ms"], c["index"]))
    for cue in ordered:
        active = [old for old in active if old["end_ms"] > cue["start_ms"]]
        for old in active:
            comparisons += 1
            if comparisons > MAX_OVERLAP_PAIRS:
                overlap_scan_capped = True
                break
            overlaps += 1
            if overlap_detail_count < 500:
                warnings.append(
                    f"Çakışma adayı: cue {old['index']} ile cue {cue['index']} "
                    f"{min(old['end_ms'], cue['end_ms']) - cue['start_ms']} ms örtüşüyor."
                )
                overlap_detail_count += 1
        if overlap_scan_capped:
            break
        active.append(cue)
    if overlaps > overlap_detail_count:
        warnings.append(f"Ek {overlaps - overlap_detail_count} çakışma çifti bulundu; ayrıntılar kısaltıldı.")
    if overlap_scan_capped:
        warnings.append(
            f"Çakışma taraması {MAX_OVERLAP_PAIRS} karşılaştırma sınırına ulaştı; "
            "kalan cue çiftleri incelenmedi."
        )
    return warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SRT için salt-okunur yapısal ve sezgisel kalite raporu üretir."
    )
    parser.add_argument("srt", type=Path, help="Denetlenecek .srt dosyası")
    parser.add_argument("--cps-warning", type=float, default=20.0,
                        help="Karakter/sn uyarı eşiği; 0 kapatır (varsayılan: 20, sezgisel)")
    parser.add_argument("--min-duration-ms", type=int, default=250,
                        help="Alt süre uyarı eşiği; 0 kapatır (varsayılan: 250 ms)")
    parser.add_argument("--max-duration-ms", type=int, default=7000,
                        help="Üst süre uyarı eşiği; 0 kapatır (varsayılan: 7000 ms)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (not math.isfinite(args.cps_warning) or args.cps_warning < 0
            or args.min_duration_ms < 0 or args.max_duration_ms < 0):
        parser.error("eşikler negatif olamaz; devre dışı bırakmak için 0 kullanın")
    try:
        if args.srt.stat().st_size > MAX_SRT_BYTES:
            print(f"HATA: SRT dosyası {MAX_SRT_BYTES} bayt sınırını aşıyor")
            return 1
        raw = args.srt.read_bytes()
    except OSError as exc:
        print(f"HATA: SRT okunamadı: {exc}", file=sys.stderr)
        return 2
    cues, errors = parse_srt(raw, args.srt.name)
    for error in errors:
        print(f"HATA: {error}")
    warnings = lint_cues(cues, args.cps_warning,
                         args.min_duration_ms, args.max_duration_ms)
    for warning in warnings:
        print(f"UYARI (sezgisel): {warning}")
    print(f"Özet: {len(cues)} geçerli cue, {len(errors)} parse hatası, "
          f"{len(warnings)} sezgisel uyarı.")
    print("Sezgisel uyarılar kalite kusurunu veya yanlış zamanlamayı kanıtlamaz.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
