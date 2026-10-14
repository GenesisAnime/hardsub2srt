"""Zaman damgası kuralları: sınıf hatalarını tek yerde kilitler.

İki sınıf hatası kapatıldı ve burada özellik (property) testleriyle korunuyor:

1. **Sınırsız alan** — sınırsız saat alanı `int()`'te Python'un 4300 hanelik
   dönüşüm sınırına takılıp ham `ValueError` olarak dışarı sızıyordu.
2. **Geçersiz süre/alan** — 60'ı geçen dakika/saniye ve bozuk ayırıcı, ayırıcı
   başına farklı kabul ediliyordu; artık iki lehçe için tek kural var.

Testler deterministiktir (sabit tohumlu `random`): `hypothesis` bu depoda bir
bağımlılık değil, bu yüzden rastgele üretim + sınır taraması elle yapılır.
"""

import random
import time
import unittest

import ogren
import review_bundle
import srt_lint
import ui_server
from srt_format import (MAX_HOUR_DIGITS, MAX_TIMESTAMP_CHARS, duration_ms,
                        fmt_ts, format_srt_ms, format_vtt_ms,
                        normalise_srt_text, parse_loose_ms, parse_srt_ms,
                        parse_timing_pair)

SEED = 20261013
SAMPLE = 400


def _random_ms(rng, count=SAMPLE):
    """Geçerli damgalar üret: 0..999 saat, 0..59 dakika/saniye, 0..999 ms."""
    values = []
    for _ in range(count):
        hours = rng.randint(0, 999)
        minutes, seconds = rng.randint(0, 59), rng.randint(0, 59)
        millis = rng.randint(0, 999)
        values.append(((hours * 60 + minutes) * 60 + seconds) * 1000 + millis)
    return values


def _corpus(rng, count=2000):
    """Bozuk girdi korpusu: ayırıcı, alan, uzunluk ve gürültü varyasyonları."""
    pieces = ["0", "1", "9", "00", "59", "60", "999", "1000", "9" * 11,
              "9" * 60, "9" * 5000, ":", ",", ".", "-->", " ", "\t", "\n",
              "x", "-", "+", "e", "\x00", "ö", "١٢٣", "00:00:01", "00:00:01,000",
              "00:00:01,0000", "999:59:59,999"]
    out = []
    for _ in range(count):
        length = rng.randint(0, 5)
        out.append("".join(rng.choice(pieces) for _ in range(length)))
    return out


class RoundTripTests(unittest.TestCase):
    def test_format_and_parse_are_inverse_on_canonical_text(self):
        rng = random.Random(SEED)
        for ms in _random_ms(rng):
            text = format_srt_ms(ms)
            self.assertEqual(parse_srt_ms(text), ms, text)
            self.assertEqual(parse_srt_ms(text).__class__, int)
            self.assertEqual(format_srt_ms(parse_srt_ms(text)), text)
            self.assertEqual(parse_loose_ms(text, full_match=True), ms, text)
            self.assertEqual(parse_loose_ms(format_vtt_ms(ms), full_match=True), ms)

    def test_hour_field_keeps_at_least_two_digits(self):
        self.assertEqual(format_srt_ms(0), "00:00:00,000")
        self.assertEqual(format_srt_ms(999), "00:00:00,999")
        self.assertEqual(format_srt_ms(3_600_000), "01:00:00,000")
        self.assertEqual(parse_srt_ms("1:00:00,000"), None)      # tek hane saat: SRT değil
        self.assertEqual(parse_loose_ms("1:00:00,000", full_match=True), 3_600_000)
        self.assertEqual(fmt_ts(3600), "01:00:00,000")
        self.assertEqual(parse_srt_ms(fmt_ts(3600)), 3_600_000)
        self.assertEqual(parse_srt_ms(fmt_ts(0.001)), 1)

    def test_milliseconds_round_trip_through_vtt_metin(self):
        rng = random.Random(SEED + 1)
        for ms in _random_ms(rng, 100):
            text = format_vtt_ms(ms)
            self.assertEqual(parse_loose_ms(text, full_match=True), ms, text)
        self.assertEqual(format_vtt_ms(None), "?")


class HourFieldBoundTests(unittest.TestCase):
    """1. sınıf: saat alanı hiçbir giriş noktasında sınırsız olamaz."""

    def test_over_long_hour_field_is_rejected_without_raising(self):
        for digits in range(MAX_HOUR_DIGITS + 1, MAX_HOUR_DIGITS + 4):
            text = f"{'9' * digits}:00:00,000"
            self.assertIsNone(parse_srt_ms(text), text)
            self.assertIsNone(parse_loose_ms(text, full_match=True), text)
            self.assertIsNone(normalise_srt_text(text), text)

    def test_python_int_digit_limit_class_is_closed(self):
        """4300+ hane `int()` sınırına takılan girdi sessizce reddedilmeli."""
        huge = f"{'9' * 5000}:00:00,000"
        self.assertIsNone(parse_srt_ms(huge))
        self.assertIsNone(parse_loose_ms(huge, full_match=True))
        self.assertIsNone(normalise_srt_text(huge))

    def test_length_gate_rejects_before_any_heavy_parsing(self):
        """Ayrıştırma öncesi uzunluk kapısı: 1 MB giriş hızlı reddedilir."""
        huge = "9" * 1_000_000
        self.assertGreater(len(huge), MAX_TIMESTAMP_CHARS)
        started = time.monotonic()
        self.assertIsNone(parse_srt_ms(huge))
        self.assertIsNone(parse_loose_ms(huge, full_match=True))
        self.assertIsNone(parse_timing_pair(f"{huge} --> {huge}"))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_hour_width_boundary_is_consistent_across_entry_points(self):
        accepted = f"{'9' * MAX_HOUR_DIGITS}:00:00,000"
        rejected = f"{'9' * (MAX_HOUR_DIGITS + 1)}:00:00,000"
        self.assertIsNotNone(parse_srt_ms(accepted))
        self.assertIsNotNone(parse_loose_ms(accepted, full_match=True))
        self.assertIsNotNone(normalise_srt_text(accepted))
        for value in (rejected,):
            self.assertIsNone(parse_srt_ms(value))
            self.assertIsNone(parse_loose_ms(value, full_match=True))
            self.assertIsNone(normalise_srt_text(value))


class FieldRangeTests(unittest.TestCase):
    """2. sınıf: dakika/saniye alanları ve ayırıcı kuralları tek yerde."""

    def test_out_of_range_minute_and_second_are_rejected_everywhere(self):
        for out in range(60, 100):
            values = [f"00:{out:02d}:00,000", f"00:00:{out:02d},000"]
            for value in values:
                self.assertIsNone(parse_srt_ms(value), value)
                self.assertIsNone(parse_loose_ms(value, full_match=True), value)
                self.assertIsNone(normalise_srt_text(value), value)
            self.assertIsNone(parse_timing_pair(f"00:{out:02d}:00,000 --> 00:01:00,000"))
            self.assertIsNone(parse_timing_pair(f"00:00:00.000 --> 00:00:{out:02d}.000",
                                                loose=True))

    def test_separator_and_millisecond_precision_per_dialect(self):
        self.assertIsNone(parse_srt_ms("00:00:01.000"))      # katı SRT virgül ister
        self.assertEqual(parse_loose_ms("00:00:01.000", full_match=True), 1000)
        self.assertEqual(parse_loose_ms("00:00:01,000", full_match=True), 1000)
        self.assertEqual(parse_loose_ms("00:00:01,5", full_match=True), 1500)
        self.assertEqual(parse_loose_ms("00:00:01,05", full_match=True), 1050)
        self.assertEqual(parse_loose_ms("00:00:01,005", full_match=True), 1005)
        self.assertIsNone(parse_loose_ms("00:00:01,0000", full_match=True))
        self.assertIsNone(parse_srt_ms("00:00:01,00"))
        self.assertEqual(normalise_srt_text("0:00:01.3"), "00:00:01,300")
        self.assertEqual(normalise_srt_text("00:00:01,000"), "00:00:01,000")

    def test_loose_prefix_match_keeps_vtt_cue_settings(self):
        self.assertEqual(parse_loose_ms("00:00:02.000 align:start position:0%"), 2000)
        self.assertIsNone(parse_loose_ms("00:00:02.000 align:start", full_match=True))

    def test_duration_gate_rejects_equal_or_reversed_ranges(self):
        rng = random.Random(SEED + 2)
        for _ in range(SAMPLE):
            start = rng.randint(0, 10_000_000)
            span = rng.randint(1, 1_000_000)
            self.assertEqual(duration_ms(start, start + span), span)
            self.assertIsNone(duration_ms(start, start))
            self.assertIsNone(duration_ms(start + span, start))
        self.assertIsNone(duration_ms(None, 5))
        self.assertIsNone(duration_ms(5, None))


class TimingPairTests(unittest.TestCase):
    def test_token_edges_policy_is_explicit(self):
        line = "1 00:00:01,000 --> 00:00:02,000 X1:100"
        self.assertEqual(parse_timing_pair(line, token_edges=True), (1000, 2000))
        self.assertIsNone(parse_timing_pair(line))
        self.assertEqual(parse_timing_pair("00:00:01,000 --> 00:00:02,000"),
                         (1000, 2000))
        self.assertIsNone(parse_timing_pair("no timing here"))
        self.assertIsNone(parse_timing_pair(None))

    def test_strict_pair_requires_canonical_srt_sides(self):
        self.assertEqual(parse_timing_pair("00:00:01,000 --> 00:00:02,000"),
                         (1000, 2000))
        self.assertIsNone(parse_timing_pair("00:00:01.000 --> 00:00:02.000"))
        self.assertIsNone(parse_timing_pair("1:00:01,000 --> 1:00:02,000"))
        self.assertEqual(parse_timing_pair("1:00:01,000 --> 1:00:02,000", loose=True),
                         (3_601_000, 3_602_000))


class GarbageCorpusTests(unittest.TestCase):
    def test_no_entry_point_raises_on_random_garbage(self):
        rng = random.Random(SEED + 3)
        for value in _corpus(rng):
            for call in (parse_srt_ms, parse_loose_ms, normalise_srt_text):
                result = call(value)
                self.assertTrue(result is None or isinstance(result, (int, str)), value)
            for loose in (False, True):
                result = parse_timing_pair(value, token_edges=rng.random() < 0.5,
                                           loose=loose, full_match=rng.random() < 0.5)
                self.assertTrue(result is None or isinstance(result, tuple), value)
            start = parse_srt_ms(value)
            if start is not None:
                self.assertIsInstance(start, int)
                self.assertIsNone(duration_ms(start, start))

    def test_garbage_never_produces_a_timestamp_out_of_the_accepted_range(self):
        rng = random.Random(SEED + 4)
        for value in _corpus(rng):
            parsed = parse_srt_ms(value)
            if parsed is not None:
                hours = parsed // 3_600_000
                self.assertLessEqual(len(str(hours)), MAX_HOUR_DIGITS)
                self.assertEqual(parse_srt_ms(format_srt_ms(parsed)), parsed)


class MigratedParserAgreementTests(unittest.TestCase):
    """Tek yer iddiası: taşınan ayrıştırıcılar kanonik modülle aynı kararı verir."""

    def test_strict_callers_agree_with_the_canonical_parser(self):
        rng = random.Random(SEED + 5)
        corpus = ([format_srt_ms(ms) for ms in _random_ms(rng, 100)] +
                  ["00:60:00,000", "1:00:00,000", "00:00:01.000", "00:00:01,00",
                   "9" * 11 + ":00:00,000", "", "bozuk", " 00:00:01,000 "])
        for value in corpus:
            expected = parse_srt_ms(value)
            self.assertEqual(srt_lint._timestamp_ms(value), expected, value)
            self.assertEqual(review_bundle._timestamp_ms(value), expected, value)

    def test_loose_callers_agree_with_the_canonical_parser(self):
        rng = random.Random(SEED + 6)
        corpus = (["00:00:01.250", "1:02:03,004", "0:0:1", "00:00:01", "01:02",
                   "00:99:00.000", "00:00:01,0000", "", "bozuk"] +
                  [format_srt_ms(ms) for ms in _random_ms(rng, 100)])
        for value in corpus:
            expected = parse_loose_ms(value, full_match=True)
            self.assertEqual(ogren._ts_ms(value), expected, value)
            self.assertEqual(ui_server.srt_zaman_norm(value),
                             None if expected is None else format_srt_ms(expected), value)


if __name__ == "__main__":
    unittest.main()
