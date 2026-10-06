#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vtt-qa.py — OCR SRT çıktısını VTT referans altyazıyla otomatik doğruluk denetimi.

Kullanım:
    py -3 vtt-qa.py "<uretilmis.srt>" "<referans.tr.vtt>" [-o rapor.json] [--lang tr]
    py -3 vtt-qa.py --selftest [-o _vqa-selftest-rapor.json]

Ne ölçer (hizalanan çiftlerde):
  - CER        : karakter hata oranı (Levenshtein / GT uzunluğu); korpus + satır ortalaması
  - recall     : VTT'de olup örtüşen SRT bloğu BULUNAMAYAN cue oranı (eksik yakalama)
  - precision  : VTT karşılığı olmayan SRT bloğu oranı (fazla/jenerik blok)
  - sistematik hata tablosu: GT→OCR karakter karışmaları (ş→g, ş→s, ç→g, ç→c,
                 ğ→g, ı→i, İ→I, o→0, 0→o, "..." kaybı, !→l/i) — sayım sıralı
  - zaman      : SRT start - VTT start farkı (ms); ilk 5 çiftin ortalama mutlak
                 farkı > 1000 ms ise "VTT sürümü videoyla uyumsuz olabilir" uyarısı

Hizalama: her SRT bloğu için en çok örtüşen VTT cue'u (örtüşme / SRT süresi >= %50);
bire bir eşleme (bir VTT cue en fazla bir SRT bloğuna eşleşir).

Bağımsız araçtır: hardsub2srt.py / srt2ass.py / mevcut SRT'lere DOKUNMAZ.
Girdi dosyaları yalnız okunur; yazma yalnız -o ile verilen rapora yapılır.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Levenshtein: rapidfuzz varsa onu kullan, yoksa saf Python yedeği
# ---------------------------------------------------------------------------
try:
    from rapidfuzz.distance import Levenshtein as _LEV

    def levenshtein(a: str, b: str) -> int:
        return _LEV.distance(a, b)

    def editops(a: str, b: str):
        """[(tag, src_pos, dest_pos)] — tag: replace/delete/insert."""
        return [(op.tag, op.src_pos, op.dest_pos) for op in _LEV.editops(a, b)]

    BACKEND = "rapidfuzz"
except ImportError:  # saf Python yedeği (rapidfuzz kurulmamışsa)
    BACKEND = "pure-python"

    def _dp_matrix(a: str, b: str):
        n, m = len(a), len(b)
        mat = [list(range(m + 1))]
        for i in range(1, n + 1):
            cur = [i] + [0] * m
            ai = a[i - 1]
            for j in range(1, m + 1):
                cost = 0 if ai == b[j - 1] else 1
                cur[j] = min(mat[i - 1][j] + 1, cur[j - 1] + 1, mat[i - 1][j - 1] + cost)
            mat.append(cur)
        return mat

    def levenshtein(a: str, b: str) -> int:
        if not a:
            return len(b)
        if not b:
            return len(a)
        return _dp_matrix(a, b)[-1][-1]

    def editops(a: str, b: str):
        mat = _dp_matrix(a, b)
        ops = []
        i, j = len(a), len(b)
        while i > 0 or j > 0:
            if i > 0 and j > 0 and mat[i][j] == mat[i - 1][j - 1] + (0 if a[i - 1] == b[j - 1] else 1):
                if a[i - 1] != b[j - 1]:
                    ops.append(("replace", i - 1, j - 1))
                i, j = i - 1, j - 1
            elif i > 0 and mat[i][j] == mat[i - 1][j] + 1:
                ops.append(("delete", i - 1, j))
                i -= 1
            else:
                ops.append(("insert", i, j - 1))
                j -= 1
        ops.reverse()
        return ops


MIN_OVERLAP_RATIO = 0.5      # örtüşme / SRT süresi eşiği
COMPAT_WARNING_MS = 1000.0   # ilk 5 çift ort. |start farkı| uyarı eşiği


# ---------------------------------------------------------------------------
# Cue modeli + zaman çözümleme
# ---------------------------------------------------------------------------
@dataclass
class Cue:
    start: int   # ms
    end: int     # ms
    text: str    # normalize edilmiş tek satır metin
    index: int   # dosyadaki 0 tabanlı sıra


_TS_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*")


def parse_ts(s: str):
    """'HH:MM:SS.mmm' | 'MM:SS.mmm' | 'HH:MM:SS,mmm' -> ms (int) ya da None."""
    m = _TS_RE.match(s)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2))
    se = int(m.group(3))
    ms = int((m.group(4) + "000")[:3])
    return ((h * 60 + mi) * 60 + se) * 1000 + ms


def ms_to_ts(ms) -> str:
    if ms is None:
        return "?"
    ms = int(round(ms))
    h, ms = divmod(ms, 3600000)
    mi, ms = divmod(ms, 60000)
    se, ms = divmod(ms, 1000)
    return f"{h:02d}:{mi:02d}:{se:02d}.{ms:03d}"


# ---------------------------------------------------------------------------
# Metin temizleme
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]*>")
_ASS_RE = re.compile(r"\{\\[^}]*\}")


def strip_tags(s: str) -> str:
    s = _ASS_RE.sub("", s)   # {\an8} gibi ASS kalıntıları
    s = _TAG_RE.sub("", s)   # <i>, <c>, <v Ad>, <00:00:01.000> vb.
    return html.unescape(s)


def normalize(s: str) -> str:
    s = strip_tags(s)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ---------------------------------------------------------------------------
# VTT parser
# ---------------------------------------------------------------------------
def parse_vtt(text: str):
    cues = []
    lines = text.splitlines()
    n = len(lines)
    i, idx = 0, 0
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue
        if s.startswith("WEBVTT"):
            i += 1
            continue
        if s.startswith(("NOTE", "STYLE", "REGION")) and "-->" not in s:
            i += 1
            while i < n and lines[i].strip():  # bloğu sonuna kadar atla
                i += 1
            continue
        if "-->" in raw:
            cue, i = _read_cue(lines, i, idx)
            if cue:
                cues.append(cue)
                idx += 1
            continue
        # muhtemel cue kimlik satırı: zaman sonraki satırda olmalı
        if i + 1 < n and "-->" in lines[i + 1]:
            cue, i = _read_cue(lines, i + 1, idx)
            if cue:
                cues.append(cue)
                idx += 1
            continue
        i += 1  # başıboş satır — yok say
    return cues


def _read_cue(lines, i, idx):
    """lines[i] '-->' içerir; cue metnini okuyup (Cue, sonraki_satır) döner."""
    left, _, right = lines[i].partition("-->")
    st = parse_ts(left)
    en = parse_ts(right)
    i += 1
    text_lines = []
    while i < len(lines) and lines[i].strip():
        text_lines.append(lines[i])
        i += 1
    t = normalize(" ".join(text_lines))
    if st is None or en is None or not t:
        return None, i
    return Cue(st, en, t, idx), i


# ---------------------------------------------------------------------------
# SRT parser
# ---------------------------------------------------------------------------
def parse_srt(text: str):
    cues = []
    idx = 0
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n[ \t]*\n+", text):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        ti = next((k for k, ln in enumerate(lines) if "-->" in ln), None)
        if ti is None:
            continue
        left, _, right = lines[ti].partition("-->")
        st = parse_ts(left)
        en = parse_ts(right)
        t = normalize(" ".join(lines[ti + 1:]))
        if st is None or en is None or not t:
            continue
        cues.append(Cue(st, en, t, idx))
        idx += 1
    return cues


# ---------------------------------------------------------------------------
# Hizalama + metrikler
# ---------------------------------------------------------------------------
# İzlenen karışmalar (GT, OCR) — "!→l" ve "!→i" tek kovalarda birleşir: "!→l/i"
WATCH = [("ş", "g"), ("ş", "s"), ("ç", "g"), ("ç", "c"), ("ğ", "g"),
         ("ı", "i"), ("İ", "I"), ("o", "0"), ("0", "o"), ("!", "l"), ("!", "i")]


def watch_key(g: str, o: str):
    for wg, wo in WATCH:
        if g == wg and o == wo:
            return "!→l/i" if g == "!" else f"{g}→{o}"
    return None


def align(srt_cues, vtt_cues):
    """Açgözlü bire bir eşleme: en büyük örtüşme önce kazanır."""
    smap = {c.index: c for c in srt_cues}
    vmap = {c.index: c for c in vtt_cues}
    cands = []
    for s in srt_cues:
        dur = s.end - s.start
        if dur <= 0:
            continue
        for v in vtt_cues:
            ov = min(s.end, v.end) - max(s.start, v.start)
            if ov > 0 and ov / dur >= MIN_OVERLAP_RATIO:
                cands.append((ov, s.index, v.index))
    cands.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_s, used_v = set(), set()
    pairs = []
    for ov, si, vi in cands:
        if si in used_s or vi in used_v:
            continue
        used_s.add(si)
        used_v.add(vi)
        pairs.append((smap[si], vmap[vi], ov))
    pairs.sort(key=lambda p: p[0].index)
    return pairs, used_s, used_v


def analyze(srt_cues, vtt_cues, lang="tr"):
    pairs, used_s, used_v = align(srt_cues, vtt_cues)

    conf = Counter()      # izlenen sistematik karışmalar
    other = Counter()     # izleme listesi dışı karakter karışmaları
    dists, line_cers, start_diffs, worst = [], [], [], []
    for s, v, _ov in pairs:
        gt, ocr = v.text, s.text
        if not gt:
            continue
        d = levenshtein(gt, ocr)
        cer = d / len(gt)
        dists.append(d)
        line_cers.append(cer)
        for tag, sp, dp in editops(gt, ocr):
            if tag == "replace":
                g, o = gt[sp], ocr[dp]
                wk = watch_key(g, o)
                if wk:
                    conf[wk] += 1
                else:
                    other[f"{g}→{o}"] += 1
        if gt.count("...") > ocr.count("..."):
            conf['"..." kaybı'] += gt.count("...") - ocr.count("...")
        diff = s.start - v.start
        start_diffs.append(diff)
        worst.append({"srt_index": s.index, "vtt_index": v.index, "gt": gt,
                      "ocr": ocr, "cer": round(cer, 6), "start_diff_ms": diff})

    worst.sort(key=lambda w: -w["cer"])
    worst10 = worst[:10]

    total_gt = sum(len(v.text) for s, v, _ in pairs if v.text)
    corpus_cer = (sum(dists) / total_gt) if total_gt else None
    mean_line_cer = (sum(line_cers) / len(line_cers)) if line_cers else None

    matched_v = len(used_v)
    matched_s = len(used_s)
    recall = {"matched": matched_v, "total": len(vtt_cues),
              "rate": (matched_v / len(vtt_cues)) if vtt_cues else None}
    precision = {"matched": matched_s, "total": len(srt_cues),
                 "rate": (matched_s / len(srt_cues)) if srt_cues else None}

    first5 = [abs(d) for d in start_diffs[:5]]
    first5_mean = (sum(first5) / len(first5)) if first5 else None
    compat = bool(first5_mean is not None and first5_mean > COMPAT_WARNING_MS)
    timing = {
        "mean_signed_start_diff_ms": (sum(start_diffs) / len(start_diffs)) if start_diffs else None,
        "mean_abs_start_diff_ms": (sum(abs(d) for d in start_diffs) / len(start_diffs)) if start_diffs else None,
        "first5_mean_abs_start_diff_ms": first5_mean,
        "compat_warning": compat,
        "note": ("VTT sürümü videoyla uyumsuz olabilir — kıyas yine de hesaplandı,"
                 " ama bu rapora güven notu DÜŞÜK." if compat else
                 "Zaman uyumu ön kontrolü geçti."),
    }

    unmatched_vtt = [c for c in vtt_cues if c.index not in used_v]
    unmatched_srt = [c for c in srt_cues if c.index not in used_s]

    def examples(cues, k=5):
        return [{"index": c.index, "start": ms_to_ts(c.start),
                 "end": ms_to_ts(c.end), "text": c.text} for c in cues[:k]]

    systematic = {k: conf[k] for k in sorted(conf, key=lambda k: (-conf[k], k))}
    others = {k: other[k] for k in sorted(other, key=lambda k: (-other[k], k))[:10]}

    return {
        "lang": lang,
        "counts": {"srt_blocks": len(srt_cues), "vtt_cues": len(vtt_cues),
                   "aligned_pairs": len(pairs),
                   "unmatched_srt": len(unmatched_srt),
                   "unmatched_vtt": len(unmatched_vtt)},
        "cer": {"corpus": corpus_cer, "mean_per_line": mean_line_cer,
                "total_edit_distance": sum(dists), "total_gt_chars": total_gt},
        "recall": recall,
        "precision": precision,
        "timing": timing,
        "systematic_errors": systematic,
        "other_confusions_top10": others,
        "worst_pairs": worst10,
        "unmatched_vtt_examples": examples(unmatched_vtt),
        "unmatched_srt_examples": examples(unmatched_srt),
    }


# ---------------------------------------------------------------------------
# Çıktı
# ---------------------------------------------------------------------------
def _pct(x) -> str:
    return "?" if x is None else f"%{x * 100:.1f}"


def _num(x, nd=4) -> str:
    return "?" if x is None else f"{x:.{nd}f}"


def print_summary(rep, srt_label, vtt_label, backend):
    c = rep["counts"]
    print("=== vtt-qa — VTT referanslı doğruluk denetimi ===")
    print(f"SRT (üretilen) : {srt_label}  ({c['srt_blocks']} blok)")
    print(f"VTT (referans) : {vtt_label}  ({c['vtt_cues']} cue, lang={rep['lang']})")
    print(f"Eşleşen çift   : {c['aligned_pairs']}   [Levenshtein: {backend}]")
    cer = rep["cer"]
    print()
    print(f"{'Metrik':<28}{'Değer'}")
    print(f"{'CER (korpus)':<28}{_num(cer['corpus'])}  ({_pct(cer['corpus'])})")
    print(f"{'CER (satır ortalaması)':<28}{_num(cer['mean_per_line'])}")
    print(f"{'Recall (yakalama)':<28}{_pct(rep['recall']['rate'])}  "
          f"({rep['recall']['matched']}/{rep['recall']['total']};"
          f" yakalanamayan: {c['unmatched_vtt']})")
    print(f"{'Precision (isabet)':<28}{_pct(rep['precision']['rate'])}  "
          f"({rep['precision']['matched']}/{rep['precision']['total']};"
          f" karşılıksız: {c['unmatched_srt']})")
    t = rep["timing"]
    print(f"{'Start farkı (ort. imzalı)':<28}{_num(t['mean_signed_start_diff_ms'], 1)} ms")
    print(f"{'Start farkı (ort. mutlak)':<28}{_num(t['mean_abs_start_diff_ms'], 1)} ms")
    print()
    print("Sistematik hata tablosu (GT→OCR, sayım sıralı):")
    if rep["systematic_errors"]:
        for k, n in rep["systematic_errors"].items():
            print(f"  {n:>4}  {k}")
    else:
        print("  (izlenen karışma görülmedi)")
    if rep["other_confusions_top10"]:
        print("Diğer karakter karışmaları (en sık 10):")
        for k, n in rep["other_confusions_top10"].items():
            print(f"  {n:>4}  {k}")
    if rep["worst_pairs"]:
        print("En kötü 3 çift (tam liste rapor JSON'da — en kötü 10):")
        for w in rep["worst_pairs"][:3]:
            print(f"  [CER {w['cer']:.4f}] VTT#{w['vtt_index']} ↔ SRT#{w['srt_index']}"
                  f" (start farkı {w['start_diff_ms']} ms)")
            print(f"    GT : {w['gt']}")
            print(f"    OCR: {w['ocr']}")
    print()
    print(f"Zaman uyumu: {t['note']} (ilk 5 çift ort. |fark|:"
          f" {_num(t['first5_mean_abs_start_diff_ms'], 1)} ms)")
    if t["compat_warning"]:
        print("[UYARI] VTT sürümü videoyla uyumsuz olabilir — rapora güven notu DÜŞÜK.")


# ---------------------------------------------------------------------------
# Kendi kendini test (sentetik SRT + VTT)
# ---------------------------------------------------------------------------
SYN_VTT = """WEBVTT

NOTE bu bir test notudur
bu satır da not bloğunun içindedir

STYLE
::cue { color: white }

cue-1
00:01.000 --> 00:04.000
Merhaba dünya

00:05.000 --> 00:08.000
Bugün hava
çok güzel

00:09.000 --> 00:12.000
Yarın görüşürüz...

cue-4
00:13.000 --> 00:16.000
<v Ana>Türkçe karakterler: ş ç ğ ı İ

00:17.000 --> 00:20.000
Bu blok SRT'de yok
"""

SYN_SRT = """1
00:00:01,000 --> 00:00:04,000
Merhaba dünya

2
00:00:05,000 --> 00:00:08,000
Bugün hava çok guzel

3
00:00:09,000 --> 00:00:12,000
Yarın görüşürüz

4
00:00:13,000 --> 00:00:16,000
<i>Türkçe karakterler: s ç ğ ı İ</i>

5
00:00:21,000 --> 00:00:24,000
Fazla blok
"""


def selftest():
    """Sentetik çifti üret, analiz et, beklenen metriklerle karşılaştır.

    Bilinen farklar: 1 harf hatası (ü→u), 1 harf hatası (ş→s), 1 eksik SRT bloğu
    (VTT'de var: recall eksiği), 1 fazla SRT bloğu (precision eksiği),
    satır sonu "..." kaybı.
    """
    vtt_cues = parse_vtt(SYN_VTT)
    srt_cues = parse_srt(SYN_SRT)
    m = analyze(srt_cues, vtt_cues, lang="tr")

    checks = []

    def chk(name, expected, measured):
        if isinstance(expected, float):
            ok = measured is not None and math.isclose(expected, measured,
                                                       rel_tol=1e-9, abs_tol=1e-12)
        else:
            ok = expected == measured
        checks.append({"metrik": name, "beklenen": expected,
                       "olculen": measured, "ok": ok})

    chk("VTT cue sayısı", 5, m["counts"]["vtt_cues"])
    chk("SRT blok sayısı", 5, m["counts"]["srt_blocks"])
    chk("eşleşen çift", 4, m["counts"]["aligned_pairs"])
    chk("yakalanamayan VTT (recall eksiği)", 1, m["counts"]["unmatched_vtt"])
    chk("karşılıksız SRT (precision eksiği)", 1, m["counts"]["unmatched_srt"])
    chk("korpus CER (5/80)", 5 / 80, m["cer"]["corpus"])
    chk("satır ort. CER ((0+1/20+3/18+1/29)/4)",
        (0 + 1 / 20 + 3 / 18 + 1 / 29) / 4, m["cer"]["mean_per_line"])
    chk("recall oranı (4/5)", 4 / 5, m["recall"]["rate"])
    chk("precision oranı (4/5)", 4 / 5, m["precision"]["rate"])
    chk('"..." kaybı', 1, m["systematic_errors"].get('"..." kaybı'))
    chk("ş→s", 1, m["systematic_errors"].get("ş→s"))
    chk("ü→u (diğer karışma)", 1, m["other_confusions_top10"].get("ü→u"))
    chk("ort. mutlak start farkı (ms)", 0.0, m["timing"]["mean_abs_start_diff_ms"])
    chk("uyumsuzluk uyarısı kapalı", False, m["timing"]["compat_warning"])

    all_ok = all(c["ok"] for c in checks)

    print("=== vtt-qa kendi kendini test (sentetik SRT+VTT) ===")
    print("Senaryo: 1 harf hatası (ü→u), 1 harf hatası (ş→s), 1 eksik SRT bloğu,")
    print("         1 fazla SRT bloğu, satır sonu \"...\" kaybı")
    print()
    print(f"{'metrik':<38}{'beklenen':<12}{'ölçülen':<12}{'sonuç'}")
    for c in checks:
        e, o = c["beklenen"], c["olculen"]
        es = f"{e:.6f}" if isinstance(e, float) else str(e)
        os_ = f"{o:.6f}" if isinstance(o, float) else str(o)
        print(f"{c['metrik']:<38}{es:<12}{os_:<12}{'OK' if c['ok'] else 'HATA'}")
    print()
    print("SONUÇ:", "TÜM TESTLER GEÇTİ" if all_ok else "BAŞARISIZ — yukarıya bak")

    report = {
        "selftest": {
            "backend": BACKEND,
            "pass": all_ok,
            "description": ("sentetik çift: 1 harf hatası (ü→u), 1 harf hatası (ş→s), "
                            "1 eksik SRT bloğu (VTT fazlası), 1 fazla SRT bloğu, "
                            "satır sonu '...' kaybı"),
            "checks": checks,
        },
        "synthetic_metrics": m,
    }
    return all_ok, report


# ---------------------------------------------------------------------------
# Ana
# ---------------------------------------------------------------------------
def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="OCR SRT'yi VTT referans altyazıyla otomatik doğruluk denetimi (CER, "
                    "recall, precision, sistematik hata tablosu).",
        epilog='Örnek: py -3 vtt-qa.py "out.srt" "bolum.tr.vtt" -o rapor.json')
    ap.add_argument("srt", nargs="?", help="üretilmiş SRT dosyası")
    ap.add_argument("vtt", nargs="?", help="referans VTT dosyası (ör. .tr.vtt)")
    ap.add_argument("-o", "--output", help="JSON rapor yolu")
    ap.add_argument("--lang", default="tr", help="referans dil (varsayılan: tr)")
    ap.add_argument("--selftest", action="store_true",
                    help="sentetik SRT+VTT ile kendi kendini test et")
    args = ap.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\nKanıt raporu yazıldı: {args.output}")
        return 0 if ok else 2

    if not args.srt or not args.vtt:
        ap.error("SRT ve VTT dosya yolları gerekli (ya da --selftest).")

    with open(args.srt, "r", encoding="utf-8-sig", errors="replace") as f:
        srt_text = f.read()
    with open(args.vtt, "r", encoding="utf-8-sig", errors="replace") as f:
        vtt_text = f.read()

    srt_cues = parse_srt(srt_text)
    vtt_cues = parse_vtt(vtt_text)
    rep = analyze(srt_cues, vtt_cues, lang=args.lang)

    report = {"tool": "vtt-qa.py", "version": "1.0", "backend": BACKEND,
              "srt": args.srt, "vtt": args.vtt,
              "min_overlap_ratio": MIN_OVERLAP_RATIO, **rep}

    print_summary(report, args.srt, args.vtt, BACKEND)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nRapor yazıldı: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
