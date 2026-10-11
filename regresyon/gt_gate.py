# -*- coding: utf-8 -*-
"""Run fixed OCR cases and gate their transcription against trusted VTT cues.

The gate uses hardsub2srt.py for OCR and vtt-qa.py for transcript alignment.
Input videos and references stay outside the repository. Missing/ambiguous
assets are reported as UNAVAILABLE and produce a non-zero exit status.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DEFAULT_CASES = HERE / "gt_cases.json"


def load_vtt_qa():
    path = PROJECT / "vtt-qa.py"
    spec = importlib.util.spec_from_file_location("hardsub_vtt_qa", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"vtt-qa.py yüklenemedi: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def resolve_case_assets(case: dict, video_root: Path, reference_root: Path):
    matches = sorted(glob.glob(str(video_root / case["video_glob"])))
    if not matches:
        return None, None, f"video bulunamadı: {case['video_glob']}"
    if len(matches) > 1:
        return None, None, f"video belirsiz ({len(matches)} eşleşme): {case['video_glob']}"
    video = Path(matches[0])
    reference = reference_root / case["reference_vtt"]
    if not reference.is_file():
        return video, None, f"referans VTT bulunamadı: {reference}"
    return video, reference, None


def analyze_case(case: dict, video: Path, reference: Path, cpu: bool,
                 qa) -> dict:
    with tempfile.TemporaryDirectory(prefix="hardsub-gt-regression-") as tmp:
        output = Path(tmp) / f"{case['id']}.srt"
        command = [sys.executable, str(PROJECT / "hardsub2srt.py"),
                   str(video), "-o", str(output),
                   "--limit-seconds", str(case["window_seconds"])]
        command.extend(case.get("ocr_args", []))
        if cpu:
            command.append("--cpu")

        print(f"\n=== {case['title']} ===", flush=True)
        print("OCR: " + " ".join(f'"{part}"' if " " in part else part
                                  for part in command), flush=True)
        run = subprocess.run(command, cwd=PROJECT, check=False)
        if run.returncode != 0:
            return {"id": case["id"], "status": "FAIL",
                    "reason": f"hardsub2srt çıkış kodu {run.returncode}"}
        if not output.is_file() or output.stat().st_size == 0:
            return {"id": case["id"], "status": "FAIL",
                    "reason": "OCR SRT üretmedi veya boş üretti"}
        stats_path = output.with_suffix(".stats.json")
        stats = (json.loads(stats_path.read_text(encoding="utf-8"))
                 if stats_path.is_file() else {})

        text = reference.read_text(encoding="utf-8-sig", errors="replace")
        cutoff_ms = int(float(case["window_seconds"]) * 1000)
        all_vtt = qa.parse_vtt(text)
        in_window_vtt = [cue for cue in all_vtt if cue.start < cutoff_ms]
        srt_text = output.read_text(encoding="utf-8-sig", errors="replace")
        srt_cues = qa.parse_srt(srt_text)
        report = qa.analyze(srt_cues, in_window_vtt,
                            lang=case.get("lang", "tr"))

    counts = report["counts"]
    cer = report["cer"]["corpus"]
    baseline = case["baseline"]
    gate = case["gate"]
    reasons = []
    if not in_window_vtt:
        reasons.append("pencere içinde referans cue yok")
    if len(in_window_vtt) < gate["min_reference_cues"]:
        reasons.append(
            f"referans kapsamı yetersiz: {len(in_window_vtt)} cue < "
            f"{gate['min_reference_cues']}"
        )
    if counts["aligned_pairs"] < gate["min_aligned_pairs"]:
        reasons.append(
            f"eşleşen çift yetersiz: {counts['aligned_pairs']} < "
            f"{gate['min_aligned_pairs']}"
        )
    total_gt = report["cer"]["total_gt_chars"]
    if total_gt < gate["min_gt_chars"]:
        reasons.append(f"GT karakter kapsamı yetersiz: {total_gt} < {gate['min_gt_chars']}")
    if cer is None or cer > gate["max_corpus_cer"]:
        reasons.append(
            f"korpus CER eşiği aşıldı: {cer!r} > {gate['max_corpus_cer']}"
        )
    if report["timing"]["compat_warning"]:
        reasons.append("vtt-qa zaman uyumu uyarısı verdi; referans güvenilir değil")

    return {
        "id": case["id"],
        "status": "FAIL" if reasons else "PASS",
        "video": video.name,
        "reference_vtt": str(reference),
        "window_seconds": case["window_seconds"],
        "baseline": baseline,
        "current": {
            "corpus_cer": cer,
            "delta_vs_baseline": (cer - baseline["corpus_cer"]
                                  if cer is not None else None),
            "aligned_pairs": counts["aligned_pairs"],
            "reference_cues_in_window": len(in_window_vtt),
            "gt_chars": total_gt,
            "recall": report["recall"]["rate"],
            "timing": report["timing"],
            "ocr_device": stats.get("device"),
            "worst_pairs": report["worst_pairs"],
        },
        "gate": gate,
        "reasons": reasons,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Sabit örneklerde OCR metnini güvenilir VTT referansıyla denetle")
    parser.add_argument("--video-root", required=True,
                        help="case manifest'indeki video globlarının aranacağı klasör")
    parser.add_argument("--reference-root", required=True,
                        help="case manifest'indeki VTT dosyalarının aranacağı klasör")
    parser.add_argument("--cases", default=str(DEFAULT_CASES),
                        help="case manifest JSON yolu")
    parser.add_argument("--case", action="append",
                        help="yalnız bu case id'lerini çalıştır (tekrarlanabilir)")
    parser.add_argument("--cpu", action="store_true",
                        help="OCR için CPU'yu açıkça seç; varsayılan ürün GPU seçimidir")
    parser.add_argument("--report", help="makinece okunur JSON raporu yaz")
    args = parser.parse_args(argv)

    cases_doc = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = cases_doc.get("cases", [])
    if args.case:
        wanted = set(args.case)
        cases = [case for case in cases if case["id"] in wanted]
        missing_ids = wanted - {case["id"] for case in cases}
        if missing_ids:
            print("[X] bilinmeyen case: " + ", ".join(sorted(missing_ids)))
            return 2
    if not cases:
        print("[UNAVAILABLE] çalıştırılacak case yok")
        return 2

    qa = load_vtt_qa()
    video_root = Path(args.video_root)
    reference_root = Path(args.reference_root)
    results = []
    for case in cases:
        video, reference, unavailable = resolve_case_assets(
            case, video_root, reference_root)
        if unavailable:
            print(f"[UNAVAILABLE] {case['id']}: {unavailable}")
            results.append({"id": case["id"], "status": "UNAVAILABLE",
                            "reason": unavailable})
            continue
        result = analyze_case(case, video, reference, args.cpu, qa)
        results.append(result)
        current = result.get("current", {})
        if current:
            print(f"[{result['status']}] {case['id']}: "
                  f"CER={current['corpus_cer']!r}, "
                  f"baseline={case['baseline']['corpus_cer']:.4f}, "
                  f"delta={current['delta_vs_baseline']!r}, "
                  f"cihaz={current['ocr_device'] or 'raporlanmadı'}, "
                  f"eşleşme={current['aligned_pairs']}/"
                  f"{current['reference_cues_in_window']} cue, "
                  f"GT={current['gt_chars']} karakter")
        else:
            print(f"[{result['status']}] {case['id']}: {result['reason']}")
        for reason in result.get("reasons", []):
            print(f"  - {reason}")

    report = {"schema_version": 1, "cases": results,
              "cpu_requested": args.cpu}
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")

    statuses = [result["status"] for result in results]
    if "FAIL" in statuses:
        print("\nSONUÇ: REGRESYON — en az bir case başarısız")
        return 1
    if "UNAVAILABLE" in statuses:
        print("\nSONUÇ: KULLANILAMAZ — eksik/belirsiz varlıklar var; geçiş sayılmaz")
        return 2
    print("\nSONUÇ: TÜM OCR-GT CASE'LERİ GEÇTİ")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
