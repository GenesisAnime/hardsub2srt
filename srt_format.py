"""Small, dependency-free SRT/VTT formatting and timestamp helpers.

Zaman damgası kuralları **tek yerde** burada tanımlıdır: alan genişlikleri,
ayırıcı, dakika/saniye sınırları ve saat alanının üst sınırı. Depodaki
ayrıştırıcılar (`srt_lint`, `ocr_review`, `ocr_video_eval`, `review_bundle`,
`vtt-qa`, `ogren`, `ui_server`, `srt2ass`) bu modülü çağırır; kural değişirse
tek yer değişir.

Sınır neden var (kapatılan sınıf hatası): sınırsız saat alanı `int()`'te
Python'un 4300 hanelik dönüşüm sınırına takılır ve ham `ValueError` olarak
dışarı sızar; `ogren.py` "Kaydet = öğren" turunu, `vtt-qa.py` VTT kıyasını
traceback ile düşürüyordu. `MAX_HOUR_DIGITS` bu yüzden 9'dur ve **bütün** giriş
noktaları aynı kapıdan geçer; `tests/test_srt_format_zaman.py` bunu özellik
tabanlı (deterministik rastgele + sınır taraması) testlerle kilitler.

İki lehçe bilinçli olarak ayrıdır, çünkü gerçek girdiler ayrıdır:

* `parse_srt_ms` — SRT dosyası damgası: `HH:MM:SS,mmm`, tam eşleşme, tam 3 hane
  milisaniye. SRT yazıcısı (`fmt_ts`) her zaman bu biçimi üretir.
* `parse_loose_ms` — VTT damgası ve kullanıcı girdisi: saat opsiyonel,
  ayırıcı `,` ya da `.`, milisaniye 1-3 hane, ön ek eşleşmesi (VTT cue ayarları
  zaman damgasından sonra gelebilir).

Dakika/saniye alanları 60'ı geçemez ve `MAX_TIMESTAMP_CHARS` ile ayrıştırmadan
önce uzunluk kapısı vardır; ikisi de "geçersiz süre" sınıfını burada kapatır.
"""

from __future__ import annotations

import re

MAX_HOUR_DIGITS = 9         # saat alanı en çok 9 hane (int() dönüşüm sınırı sınıfı)
MAX_TIMESTAMP_CHARS = 64    # ayrıştırmadan önceki uzunluk kapısı

# SRT token shape. parse_srt_ms separately enforces MAX_HOUR_DIGITS before
# converting fields; keeping this shape broad lets callers preserve useful
# syntax-versus-value diagnostics for oversized but otherwise SRT-shaped hours.
_SRT_BODY = r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})"
# Toleranslı gövde: saat opsiyonel, ayırıcı virgül ya da nokta, ms 1-3 hane.
_LOOSE_BODY = (rf"(?:(\d{{1,{MAX_HOUR_DIGITS}}}):)?(\d{{1,2}}):(\d{{2}})"
               rf"[.,](\d{{1,3}})")

#: Çağıranlar kendi tanılama mesajlarını üretirken bu desenleri yeniden kullanır
#: (kural yine tek yerde kalır: desen burada tanımlı).
SRT_TIMESTAMP_RE = re.compile(rf"^{_SRT_BODY}$")
LOOSE_TIMESTAMP_RE = re.compile(rf"^\s*{_LOOSE_BODY}\s*")


def _to_ms(hours: int, minutes: int, seconds: int, millis: int) -> int | None:
    if minutes > 59 or seconds > 59:
        return None
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_srt_ms(value) -> int | None:
    """`HH:MM:SS,mmm` damgasını milisaniyeye çevir; geçersizse `None`."""
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > MAX_TIMESTAMP_CHARS:
        return None
    match = SRT_TIMESTAMP_RE.fullmatch(text)
    if not match:
        return None
    if len(match.group(1)) > MAX_HOUR_DIGITS:
        return None
    return _to_ms(*(int(part) for part in match.groups()))


def parse_loose_ms(value, *, full_match: bool = False) -> int | None:
    """VTT/kullanıcı damgasını milisaniyeye çevir; geçersizse `None`.

    `full_match=False` (varsayılan) yalnız damgayı okur ve arkasındaki VTT cue
    ayarlarını yok sayar; `True` damganın satırın tamamı olmasını şart koşar.
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) > MAX_TIMESTAMP_CHARS:
        return None
    match = LOOSE_TIMESTAMP_RE.match(text)
    if not match or (full_match and match.end() != len(text)):
        return None
    hours = int(match.group(1) or 0)
    minutes, seconds = int(match.group(2)), int(match.group(3))
    millis = int((match.group(4) + "000")[:3])
    return _to_ms(hours, minutes, seconds, millis)


def normalise_srt_text(value) -> str | None:
    """Girdiyi kanonik SRT metnine çevir: `0:00:01,000` / `00:00:01.3`."""
    ms = parse_loose_ms(value, full_match=True)
    return None if ms is None else format_srt_ms(ms)


def format_srt_ms(ms) -> str:
    """Milisaniyeyi `HH:MM:SS,mmm` metnine çevir (saat alanı en az 2 hane)."""
    total = int(round(ms))
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def format_vtt_ms(ms) -> str:
    """Milisaniyeyi `HH:MM:SS.mmm` metnine çevir (`None` -> `?`)."""
    if ms is None:
        return "?"
    return format_srt_ms(ms).replace(",", ".")


def parse_timing_pair(line, *, token_edges: bool = False, loose: bool = False,
                      full_match: bool = True) -> tuple[int, int] | None:
    """`A --> B` zaman satırını `(start_ms, end_ms)` olarak çözer.

    `token_edges=True` ise kenar belirteçleri kullanılır (satır başındaki blok
    numarası ya da sondaki fazlalık yok sayılır) — `ogren`/`ui_server`/`srt2ass`
    böyle okur. `loose=True` toleranslı lehçeyi seçer, `full_match=False` ön ek
    eşleşmesine izin verir (`srt2ass`, VTT).
    """
    if not isinstance(line, str) or "-->" not in line:
        return None
    left, _, right = line.partition("-->")
    if token_edges:
        left_tokens, right_tokens = left.split(), right.split()
        if not left_tokens or not right_tokens:
            return None
        left_text, right_text = left_tokens[-1], right_tokens[0]
    else:
        left_text, right_text = left.strip(), right.strip()
    parse = parse_loose_ms if loose else parse_srt_ms
    if loose:
        start = parse(left_text, full_match=full_match)
        end = parse(right_text, full_match=full_match)
    else:
        start, end = parse(left_text), parse(right_text)
    if start is None or end is None:
        return None
    return start, end


def duration_ms(start_ms, end_ms) -> int | None:
    """Bitiş başlangıçtan sonraysa süre (ms), değilse `None` (geçersiz süre)."""
    if start_ms is None or end_ms is None or end_ms <= start_ms:
        return None
    return end_ms - start_ms


def fmt_ts(seconds):
    """Format seconds as an SRT timestamp (HH:MM:SS,mmm)."""
    return format_srt_ms(round(seconds * 1000))


def render_srt_cues_with_timing_diagnostics(cues, frame_time):
    """Render OCR cues exactly as the writer does and report end clamps.

    ``cues`` contains ``(start_frame, end_frame_inclusive, text, confidence)``
    rows. The returned SRT string intentionally preserves the existing
    next-cue-start clamp and timestamp formatting. Diagnostic rows omit cue
    text and confidence so callers can safely place them in stats metadata.
    """
    cue_list = list(cues)
    rendered = []
    clamps = []
    for index, (start_frame, end_frame, text, _confidence) in enumerate(cue_list):
        start = frame_time(start_frame)
        raw_end = frame_time(end_frame + 1)
        emitted_end = raw_end
        if index + 1 < len(cue_list):
            next_start = frame_time(cue_list[index + 1][0])
            emitted_end = min(raw_end, next_start)
        rendered.append(
            f"{index + 1}\n{fmt_ts(start)} --> {fmt_ts(emitted_end)}\n{text}\n\n"
        )
        if raw_end > emitted_end:
            clamps.append({
                "cue_index": index,
                "start_frame": int(start_frame),
                "end_frame_inclusive": int(end_frame),
                "raw_end": fmt_ts(raw_end),
                "emitted_end": fmt_ts(emitted_end),
                "clamp_delta_ms": round((raw_end - emitted_end) * 1000, 3),
            })
    return "".join(rendered), clamps
