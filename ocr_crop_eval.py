#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local crop-text evaluation for a prepared, human-reviewed Phase 2 dataset.

This is deliberately separate from ``ocr_experiment.py``'s full-video/release
contract. It reads verified still crops, keeps recognized text in memory only,
and writes aggregate crop-level metrics. It never trains, uploads, or evaluates
cue detection/timing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import ocr_experiment as prep

MAX_SPLIT_BYTES = 32 * 1024 * 1024
MAX_TEXT_CHARS = 4096
ENGINES = {"easyocr-single", "easyocr-consensus3", "rapidocr-v6-small"}
NORMALIZATION = "NFC + whitespace collapse + trim; case and punctuation preserved"


class EvalError(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_unique(path: Path, cap: int) -> tuple[dict, bytes]:
    try:
        if path.stat().st_size > cap:
            raise EvalError(f"{path.name} boyut sınırını aşıyor")
        raw = path.read_bytes()
        def unique(pairs):
            out = {}
            for k, v in pairs:
                if k in out:
                    raise EvalError(f"JSON alanı yineleniyor: {k}")
                out[k] = v
            return out
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvalError(f"JSON okunamadı: {path.name}") from exc
    if type(doc) is not dict:
        raise EvalError(f"{path.name}: JSON nesnesi gerekli")
    return doc, raw


def validate_run(dataset: Path, metadata_path: Path, split_path: Path):
    """Revalidate every source and immutable split input immediately per engine."""
    dataset = dataset.resolve(strict=True)
    metadata_path = metadata_path.resolve(strict=True)
    split_path = split_path.resolve(strict=True)
    rows = prep.validate_dataset(dataset)
    metadata = prep.validate_metadata(metadata_path, rows)
    provenance = prep.verify_phase2_provenance(dataset, rows)
    if provenance.get("status") != "verified_against_parent_review_pack":
        raise EvalError("Phase 2 parent review-pack/active-event bağlantısı doğrulanmadı")
    if not all(m["license"]["human_review"]["status"] == "reviewed"
               for m in metadata.values()):
        raise EvalError("Her kayıt için insan rights review status=reviewed gerekli; rights_verified yine false kalır")

    split_doc, split_raw = _json_unique(split_path, MAX_SPLIT_BYTES)
    required = {"schema_version", "dataset_manifest_sha256", "metadata_sha256", "seed",
                "algorithm", "group_split", "rows", "invariants"}
    if set(split_doc) != required or type(split_doc.get("schema_version")) is not int or split_doc["schema_version"] != 1:
        raise EvalError("split-manifest.json Phase 5 split v1 şemasıyla eşleşmiyor")
    manifest_bytes = (dataset / "manifest.jsonl").read_bytes()
    metadata_bytes = metadata_path.read_bytes()
    if split_doc["dataset_manifest_sha256"] != sha256(manifest_bytes):
        raise EvalError("Veri kümesi manifest hash'i split manifest ile eşleşmiyor")
    if split_doc["metadata_sha256"] != sha256(metadata_bytes):
        raise EvalError("Metadata hash'i split manifest ile eşleşmiyor")
    seed = split_doc.get("seed")
    if type(seed) is not str or not seed.strip():
        raise EvalError("Split tohumu eksik")
    expected_split = prep.assign_splits(rows, metadata, seed)
    if type(split_doc.get("rows")) is not list or len(split_doc["rows"]) != len(rows):
        raise EvalError("Split satır sayısı doğrulanmış veri kümesiyle eşleşmiyor")
    expected_by_id = {r["cue_id"]: r for r in rows}
    seen = set()
    groups = {}
    for item in split_doc["rows"]:
        if type(item) is not dict:
            raise EvalError("Split satırı JSON nesnesi değil")
        cue_id = item.get("cue_id")
        if type(cue_id) is not str or cue_id in seen or cue_id not in expected_by_id:
            raise EvalError("Split içinde bilinmeyen veya yinelenen cue_id var")
        seen.add(cue_id)
        row = expected_by_id[cue_id]
        meta = metadata[cue_id]
        group = prep._group_key(meta)
        expected = {
            "cue_id": cue_id, "group_id": group, "split": expected_split[cue_id],
            "source_id": meta["source_id"], "episode_id": meta["episode_id"],
            "license": meta["license"], "crop_path": row["crop_path"],
            "crop_sha256_local": row["crop_sha256_local"],
            "ocr_confidence": row["ocr_confidence"], "width": row["_width"],
            "height": row["_height"], "review_event_id": row["review_event_id"],
        }
        if item != expected:
            raise EvalError(f"Split satırı değiştirilmiş veya dataset/metadata ile uyuşmuyor: {cue_id}")
        groups[group] = expected_split[cue_id]
    if seen != set(expected_by_id):
        raise EvalError("Split tüm doğrulanmış cue satırlarını tam bir kez içermiyor")
    if split_doc.get("group_split") != groups:
        raise EvalError("Grup-split eşlemesi satırlarla tutarsız")
    inv = split_doc.get("invariants")
    if (type(inv) is not dict or inv.get("all_rows_assigned_once") is not True
            or inv.get("group_leakage") is not False
            or inv.get("phase2_provenance_verified") is not True
            or inv.get("rights_evidence_present_self_attested") is not True
            or inv.get("rights_verified") is not False
            or inv.get("human_rights_review_complete") is not True):
        raise EvalError("Split invariant/Phase 2/rights beyanları geçerli değil")
    selected = [item for item in split_doc["rows"] if item["split"] == "test"]
    if not selected:
        raise EvalError("Dondurulmuş test split boş; değerlendirme yapılmadı")
    references = {r["cue_id"]: r["source_text_verified"] for r in rows}
    return selected, references, provenance, sha256(manifest_bytes), sha256(metadata_bytes), sha256(split_raw)


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def edit_distance(a, b):
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for i, cb in enumerate(b, 1):
        cur = [i]
        for j, ca in enumerate(a, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score_pairs(pairs):
    """Pairs contain only current in-memory strings; never returned or persisted."""
    chars_ref = chars_edits = words_ref = words_edits = exact = covered = 0
    for reference, prediction in pairs:
        ref, pred = normalize(reference), normalize(prediction)
        if max(len(ref), len(pred)) > MAX_TEXT_CHARS:
            raise EvalError("OCR metin alanı 4096 karakter sınırını aşıyor")
        if ref:
            chars_ref += len(ref)
            chars_edits += edit_distance(ref, pred)
            rt, pt = ref.split(), pred.split()
            if rt:
                words_ref += len(rt)
                words_edits += edit_distance(rt, pt)
        exact += int(ref == pred)
        covered += int(bool(pred))
    n = len(pairs)
    return {"rows": n, "coverage": covered / n if n else None,
            "cer": chars_edits / chars_ref if chars_ref else None,
            "wer": words_edits / words_ref if words_ref else None,
            "exact_match_rate": exact / n if n else None,
            "reference_chars": chars_ref, "reference_words": words_ref,
            "character_edits": chars_edits, "word_edits": words_edits,
            "nonempty_outputs": covered, "empty_reference_rows": 0}


def _device(cpu: bool, engine_name: str):
    """Resolve actual backend/device; GPU preferred unless --cpu is explicit."""
    device = {"requested": "cpu" if cpu else "gpu_if_available", "device": "cpu",
              "device_name": None, "backend": None}
    if engine_name.startswith("easyocr-"):
        import torch
        use_gpu = not cpu and bool(torch.cuda.is_available())
        if use_gpu:
            device.update(device="gpu", device_name=torch.cuda.get_device_name(0), backend="pytorch-cuda")
        else:
            device.update(backend="pytorch-cpu")
        device["runtime_version"] = str(torch.__version__)
        return device, use_gpu
    import onnxruntime as ort
    providers = list(ort.get_available_providers())
    use_gpu = not cpu and "CUDAExecutionProvider" in providers
    if use_gpu:
        device.update(device="gpu", device_name="CUDAExecutionProvider", backend="onnxruntime-cuda")
    else:
        device.update(backend="onnxruntime-cpu")
    device["runtime_version"] = str(ort.__version__)
    device["available_providers"] = providers
    return device, use_gpu


def _init_easyocr(languages: list[str], model_dir: Path, use_gpu: bool):
    try:
        import easyocr
    except ImportError as exc:
        raise EvalError("EasyOCR kurulu değil") from exc
    model_dir = model_dir.expanduser().resolve()
    if not model_dir.is_dir():
        raise EvalError("EasyOCR yerel model klasörü bulunamadı; otomatik model indirme kapalı")
    try:
        # Explicitly fail on missing local assets; this must never fetch weights.
        reader = easyocr.Reader(languages, gpu=use_gpu, model_storage_directory=str(model_dir),
                                download_enabled=False, verbose=False)
    except Exception as exc:
        raise EvalError(f"EasyOCR modeli yüklenemedi (indirme kapalı): {type(exc).__name__}") from exc
    weights = sorted(p for p in model_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".pth", ".pt"})
    if not weights:
        raise EvalError("EasyOCR model klasöründe hash'lenebilir yerel .pth/.pt ağırlığı yok")
    hashes = {p.relative_to(model_dir).as_posix(): _sha256_file(p) for p in weights}
    return reader, str(getattr(easyocr, "__version__", "unknown")), model_dir, hashes


def _init_rapid(det_path: Path, rec_path: Path, dict_path: Path, use_gpu: bool):
    files = [p.expanduser().resolve(strict=True) for p in (det_path, rec_path, dict_path)]
    if any(not p.is_file() or p.stat().st_size <= 0 for p in files):
        raise EvalError("RapidOCR için geçerli yerel det/rec/dictionary dosyaları zorunlu")
    try:
        import rapidocr
        from rapidocr import ModelType, OCRVersion, RapidOCR
    except ImportError as exc:
        raise EvalError("RapidOCR >= 3.9 kurulu değil") from exc
    params = {
        "Det.ocr_version": OCRVersion.PPOCRV6, "Det.lang_type": "ch",
        "Det.model_type": ModelType.SMALL, "Det.model_path": str(files[0]),
        "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.lang_type": "ch",
        "Rec.model_type": ModelType.SMALL, "Rec.model_path": str(files[1]),
        "Rec.rec_keys_path": str(files[2]), "Cls.use_cls": False,
        "EngineConfig.onnxruntime.use_cuda": bool(use_gpu),
    }
    try:
        # Explicit model paths prevent RapidOCR's default model resolver from downloading assets.
        engine = RapidOCR(params=params)
    except Exception as exc:
        raise EvalError(f"RapidOCR yerel modelleri yüklenemedi: {type(exc).__name__}") from exc
    return engine, str(getattr(rapidocr, "__version__", "unknown")), files, params


def _rapid_session_providers(engine) -> list[str]:
    """Read providers from created ONNX sessions; don't infer actual use from config."""
    queue, seen, providers = [engine], set(), set()
    while queue and len(seen) < 300:
        item = queue.pop()
        ident = id(item)
        if ident in seen:
            continue
        seen.add(ident)
        getter = getattr(item, "get_providers", None)
        if callable(getter):
            try:
                providers.update(str(x) for x in getter())
            except Exception:
                pass
        values = getattr(item, "__dict__", None)
        if isinstance(values, dict):
            for value in values.values():
                if isinstance(value, (list, tuple)):
                    queue.extend(x for x in value if hasattr(x, "__dict__") or callable(getattr(x, "get_providers", None)))
                elif hasattr(value, "__dict__") or callable(getattr(value, "get_providers", None)):
                    queue.append(value)
    if not providers:
        raise EvalError("RapidOCR çalışma oturumlarının ONNX provider bilgisi okunamadı; gerçek backend doğrulanamadı")
    return sorted(providers)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rapid_text(engine, image, hs):
    out = engine(image)
    if isinstance(out, tuple):
        out = out[0]
    texts = getattr(out, "txts", None) if out is not None else None
    if not texts:
        return ""
    boxes = getattr(out, "boxes", None)
    scores = getattr(out, "scores", None)
    if boxes is None or len(boxes) != len(texts):
        boxes = [None] * len(texts)
    if scores is None or len(scores) != len(texts):
        scores = [0.0] * len(texts)
    items = []
    for box, text, confidence in zip(boxes, texts, scores):
        if box is None:
            bbox = [[0.0, 0.0]]
        else:
            bbox = [[float(p[0]), float(p[1])] for p in box]
        items.append((bbox, str(text), float(confidence)))
    return hs._group_lines(items)[0]


def _engine_predictions(engine_name, selected, dataset: Path, references: dict,
                        reader, hs, easy_variants, rapid=False):
    import cv2
    import numpy as np
    predictions = []
    for item in selected:
        crop = prep._safe_crop(dataset, item["crop_path"])
        raw = prep._read_capped(crop, prep.MAX_CROP_BYTES, "OCR crop")
        if sha256(raw) != item["crop_sha256_local"]:
            raise EvalError(f"OCR başlamadan crop hash'i değişti: {item['cue_id']}")
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None or img.shape[:2] != (item["height"], item["width"]):
            raise EvalError(f"OCR başlamadan crop çözümlemesi başarısız: {item['cue_id']}")
        try:
            if rapid:
                recognized = _rapid_text(reader, img, hs)
            elif engine_name == "easyocr-consensus3":
                recognized = hs.ocr_consensus(reader, img)[0]
            else:
                recognized = hs.ocr_lines(reader, img)[0]
        except Exception as exc:
            # Do not echo engine exceptions that might include source contents.
            raise EvalError(f"OCR engine hatası ({engine_name}, cue={item['cue_id']}): {type(exc).__name__}") from None
        if type(recognized) is not str or len(recognized) > MAX_TEXT_CHARS:
            raise EvalError(f"OCR sonucu geçersiz/boyut sınırını aşıyor: {item['cue_id']}")
        predictions.append((references[item["cue_id"]], recognized))
    return predictions


def _aggregate(engine_name, selected, pairs):
    all_metrics = score_pairs(pairs)
    grouped = {}
    for row, pair in zip(selected, pairs):
        key = (row["source_id"], row["episode_id"])
        grouped.setdefault(key, []).append(pair)
    by_group = []
    for (source_id, episode_id), group_pairs in sorted(grouped.items()):
        metrics = score_pairs(group_pairs)
        by_group.append({"source_id": source_id, "episode_id": episode_id, "split": "test",
                         **{k: metrics[k] for k in ("rows", "coverage", "cer", "wer", "exact_match_rate")}})
    numeric = ("coverage", "cer", "wer", "exact_match_rate")
    macro = {key: (sum(g[key] for g in by_group if g[key] is not None) /
                   sum(g[key] is not None for g in by_group) if any(g[key] is not None for g in by_group) else None)
             for key in numeric}
    return {"engine_id": engine_name, "split": "test", "micro": all_metrics,
            "group_macro": macro, "per_source_episode": by_group}


def run(dataset: Path, metadata_path: Path, split_path: Path, out_path: Path,
        engine_names: list[str], cpu: bool, languages: list[str], easy_model_dir: Path,
        rapid_det: Path | None, rapid_rec: Path | None, rapid_dict: Path | None):
    if not engine_names or len(engine_names) != len(set(engine_names)) or set(engine_names) - ENGINES:
        raise EvalError("Desteklenmeyen veya yinelenen engine adı")
    if "rapidocr-v6-small" in engine_names and not all((rapid_det, rapid_rec, rapid_dict)):
        raise EvalError("rapidocr-v6-small için --rapid-det-model, --rapid-rec-model ve --rapid-dict gerekli")
    selected, references, provenance, manifest_hash, metadata_hash, split_hash = validate_run(
        dataset, metadata_path, split_path)
    out_path = out_path.resolve()
    if out_path.exists():
        raise EvalError("Çıktı raporu zaten var; üzerine yazılmayacak")
    dataset = dataset.resolve()
    for protected in (dataset, dataset.parent, metadata_path.resolve().parent):
        try:
            out_path.relative_to(protected.resolve())
        except ValueError:
            continue
        raise EvalError("Aggregate rapor dataset/review-pack/metadata klasörlerinin dışına yazılmalı")
    if out_path.suffix.lower() != ".json":
        raise EvalError("Çıktı yolu .json olmalı")
    import hardsub2srt as hs
    results = []
    for engine_name in engine_names:
        # Repeat all provenance, rights, dataset, and split checks for each engine run.
        now_rows, now_refs, now_provenance, now_mh, now_meta, now_sh = validate_run(
            dataset, metadata_path, split_path)
        if (now_mh, now_meta, now_sh) != (manifest_hash, metadata_hash, split_hash):
            raise EvalError("Bir engine koşusu sırasında dataset/metadata/split değişti")
        current = {r["cue_id"]: r for r in now_rows}
        this_selected = [current[x["cue_id"]] for x in selected]
        if now_refs != references or now_provenance != provenance:
            raise EvalError("Bir engine koşusu sırasında label/provenance değişti")
        device, use_gpu = _device(cpu, engine_name)
        model_hashes = {}
        if engine_name.startswith("easyocr-"):
            reader, version, model_dir, model_hashes = _init_easyocr(languages, easy_model_dir, use_gpu)
            actual_device = str(getattr(reader, "device", ""))
            actual_gpu = actual_device.lower().startswith("cuda")
            device["device"] = "gpu" if actual_gpu else "cpu"
            device["backend"] = "pytorch-cuda" if actual_gpu else "pytorch-cpu"
            device["runtime_device"] = actual_device or None
            if not actual_gpu:
                device["device_name"] = actual_device or "cpu"
            if use_gpu and not actual_gpu:
                device["gpu_fallback_reason"] = "EasyOCR reader reports a non-CUDA device"
            config = {"languages": languages, "variant": engine_name,
                      "local_model_directory_configured": True,
                      "ocr_variants": ["default", "mag_ratio=2", "low_text=0.3"]
                      if engine_name == "easyocr-consensus3" else ["default"]}
            device["engine_version"] = version
            device["model_sha256"] = model_hashes
            # No individual subtitle or model output is included in the report.
        else:
            reader, version, model_files, params = _init_rapid(rapid_det, rapid_rec,
                                                                rapid_dict, use_gpu)
            config = {"variant": engine_name, "ocr_version": "PP-OCRv6-small",
                      "classification": False, "explicit_local_model_paths": True}
            model_hashes = {label: _sha256_file(path) for label, path in
                            zip(("det", "rec", "dictionary"), model_files)}
            device["engine_version"] = version
            used = _rapid_session_providers(reader)
            device["actual_session_providers"] = used
            actual_gpu = "CUDAExecutionProvider" in used
            device["device"] = "gpu" if actual_gpu else "cpu"
            device["backend"] = "onnxruntime-cuda" if actual_gpu else "onnxruntime-cpu"
            device["device_name"] = "CUDAExecutionProvider" if actual_gpu else None
            if use_gpu and not actual_gpu:
                device["gpu_fallback_reason"] = "CUDA requested but no ONNX session uses CUDAExecutionProvider"
        pairs = _engine_predictions(engine_name, this_selected, dataset, references,
                                    reader, hs, None, rapid=engine_name.startswith("rapidocr-"))
        if model_hashes:
            device["model_sha256"] = model_hashes
        config_bytes = json.dumps(config, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")).encode("utf-8")
        device["config_sha256"] = sha256(config_bytes)
        results.append(_aggregate(engine_name, this_selected, pairs) | {
            "device_backend_config": device, "config": config})
        del reader, pairs

    # Verify the immutable inputs still match after all models have finished.
    _, _, _, end_mh, end_meta, end_sh = validate_run(dataset, metadata_path, split_path)
    if (end_mh, end_meta, end_sh) != (manifest_hash, metadata_hash, split_hash):
        raise EvalError("Değerlendirme sırasında dataset/metadata/split değişti; rapor yazılmadı")
    report = {
        "schema_version": 1, "evaluation_id": out_path.parent.name,
        "status": "CROP_LEVEL_MEASURED_NOT_VIDEO_ACCURACY",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "software": {"evaluator_sha256": sha256(Path(__file__).read_bytes()),
                     "ocr_adapter_sha256": sha256(Path(hs.__file__).read_bytes())},
        "dataset": {"manifest_sha256": manifest_hash, "metadata_sha256": metadata_hash,
                    "split_manifest_sha256": split_hash, "parent_provenance": provenance,
                    "rows": len(selected), "split": "test", "rights_verified": False,
                    "rights_review_status": "human_review_attested_not_legally_verified"},
        "scope": {"measurement": "human-verified still-crop source-text recognition only",
                  "video_accuracy_claimed": False, "cue_detection": None,
                  "cue_precision": None, "cue_recall": None,
                  "timing_start_abs_error_ms": None, "timing_end_abs_error_ms": None,
                  "timing_within_tolerance_rate": None,
                  "normalization": NORMALIZATION,
                  "empty_recognition_counts_as_uncovered_and_empty_prediction": True,
                  "metrics_persisted": "aggregate only; no per-cue recognized/reference text"},
        "engines": results,
        "release_gate": {"status": "NOT_EVALUATED_CROP_METRICS_ONLY",
                         "phase6_blocked": True,
                         "reason": "Still-crop text metrics do not provide trusted timed video references, cue detection/timing metrics, legal rights verification, or Phase 6 release evidence."},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Yerel OCR crop-text değerlendirmesi (video accuracy/release PASS değildir)")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path, help="ocr_experiment.py tarafından oluşturulmuş split-manifest.json")
    parser.add_argument("--out", required=True, type=Path, help="yerel aggregate rapor JSON yolu; var olmamalı")
    parser.add_argument("--engine", action="append", choices=sorted(ENGINES), required=True,
                        help="bir veya daha fazla: easyocr-single, easyocr-consensus3, rapidocr-v6-small")
    parser.add_argument("--lang", default="tr,en", help="EasyOCR dil listesi; varsayılan tr,en")
    parser.add_argument("--easyocr-model-dir", type=Path,
                        default=Path.home() / ".EasyOCR" / "model",
                        help="önceden mevcut EasyOCR model klasörü; indirme kapalı")
    parser.add_argument("--rapid-det-model", type=Path)
    parser.add_argument("--rapid-rec-model", type=Path)
    parser.add_argument("--rapid-dict", type=Path)
    parser.add_argument("--cpu", action="store_true", help="GPU yerine CPU'yu açıkça seç")
    args = parser.parse_args(argv)
    languages = [x.strip().lower() for x in args.lang.split(",") if x.strip()]
    if not languages:
        parser.error("--lang en az bir EasyOCR dili içermeli")
    try:
        run(args.dataset, args.metadata, args.split, args.out, args.engine, args.cpu,
            languages, args.easyocr_model_dir, args.rapid_det_model,
            args.rapid_rec_model, args.rapid_dict)
    except (EvalError, prep.InputError, OSError, ImportError) as exc:
        print(f"DURDURULDU: {exc}", file=sys.stderr)
        return 2
    print(f"Crop-level aggregate raporu yazıldı: {args.out}; video doğruluğu veya release PASS iddiası değildir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
