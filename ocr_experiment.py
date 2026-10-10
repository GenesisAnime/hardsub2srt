#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepare and validate a local, human-verified OCR experiment dataset.

This tool does not train, run OCR, upload data, or modify an exported dataset.
It only validates a verified-ocr-dataset export plus a separate rights/group
metadata file, then writes a deterministic group split and an experiment plan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
LICENSE_STATUSES = {"public_domain", "permissive_license", "permission_granted", "user_owned"}
SPLITS = (("train", 0.80), ("validation", 0.10), ("test", 0.10))
MAX_ROWS = 100_000
MAX_CROP_BYTES = 8 * 1024 * 1024


class InputError(ValueError):
    pass


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"JSON okunamadı: {path.name}: {exc}") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_crop(root: Path, raw: object) -> Path:
    if not isinstance(raw, str) or not raw:
        raise InputError("crop_path boş veya metin değil")
    rel = PurePosixPath(raw)
    if rel.is_absolute() or ".." in rel.parts or "\\" in raw:
        raise InputError(f"Güvensiz crop_path: {raw!r}")
    path = root.joinpath(*rel.parts).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise InputError(f"crop_path veri kümesi dışına çıkıyor: {raw!r}") from exc
    return path


def _decode_image(path: Path, data: bytes) -> tuple[int, int]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise InputError("Kırpım çözümleme için OpenCV ve NumPy gerekli; fail-closed") from exc
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.ndim != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise InputError(f"Kırpım çözümlenemedi: {path.name}")
    return int(image.shape[1]), int(image.shape[0])


def validate_dataset(root: Path) -> list[dict]:
    manifest = root / "manifest.jsonl"
    if not root.is_dir() or not manifest.is_file():
        raise InputError("Girdi, manifest.jsonl içeren verified OCR veri kümesi klasörü olmalı")
    if manifest.stat().st_size > 64 * 1024 * 1024:
        raise InputError("manifest.jsonl 64 MiB sınırını aşıyor")
    rows = []
    seen = set()
    for line_no, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if len(rows) >= MAX_ROWS:
            raise InputError(f"Satır sayısı {MAX_ROWS} sınırını aşıyor")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputError(f"Manifest JSONL satır {line_no} bozuk") from exc
        required = {"schema_version", "cue_id", "source_text_verified", "crop_path",
                    "crop_sha256_local", "start_ms", "end_ms", "review_event_id", "reviewed_at"}
        if not isinstance(row, dict) or set(row) != required or row.get("schema_version") != 1:
            raise InputError(f"Satır {line_no}: Phase 2 verified export şemasıyla eşleşmiyor")
        cue = row.get("cue_id")
        if not isinstance(cue, str) or not cue or cue in seen:
            raise InputError(f"Satır {line_no}: cue_id eksik veya tekrar ediyor")
        seen.add(cue)
        if not isinstance(row.get("source_text_verified"), str) or not row["source_text_verified"].strip():
            raise InputError(f"Satır {line_no}: insan tarafından doğrulanmış metin eksik")
        if (not isinstance(row.get("start_ms"), int) or not isinstance(row.get("end_ms"), int)
                or row["start_ms"] < 0 or row["end_ms"] <= row["start_ms"]):
            raise InputError(f"Satır {line_no}: zaman aralığı geçersiz")
        digest = row.get("crop_sha256_local")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InputError(f"Satır {line_no}: crop SHA-256 geçersiz")
        crop = _safe_crop(root, row.get("crop_path"))
        try:
            data = crop.read_bytes()
        except OSError as exc:
            raise InputError(f"Satır {line_no}: crop okunamıyor") from exc
        if not data or len(data) > MAX_CROP_BYTES or _sha256(data) != digest:
            raise InputError(f"Satır {line_no}: crop boş, çok büyük veya SHA-256 eşleşmiyor")
        width, height = _decode_image(crop, data)
        rows.append({**row, "_width": width, "_height": height})
    if not rows:
        raise InputError("Veri kümesinde insan tarafından doğrulanmış örnek yok")
    return rows


def validate_metadata(path: Path, rows: list[dict]) -> dict[str, dict]:
    doc = _json(path)
    if not isinstance(doc, dict) or set(doc) != {"schema_version", "records"} or doc.get("schema_version") != 1:
        raise InputError("Metadata kök şeması schema_version=1 ve records olmalı")
    if not isinstance(doc["records"], list):
        raise InputError("Metadata records liste olmalı")
    by_id = {}
    for record in doc["records"]:
        required = {"cue_id", "source_id", "episode_id", "license"}
        if not isinstance(record, dict) or set(record) != required:
            raise InputError("Her metadata kaydı cue_id, source_id, episode_id, license içermeli")
        cue = record.get("cue_id")
        if not isinstance(cue, str) or not cue or cue in by_id:
            raise InputError("Metadata cue_id eksik veya tekrarlı")
        if not isinstance(record.get("source_id"), str) or not record["source_id"].strip():
            raise InputError(f"{cue}: source_id zorunlu")
        if not isinstance(record.get("episode_id"), str) or not record["episode_id"].strip():
            raise InputError(f"{cue}: episode_id zorunlu")
        lic = record.get("license")
        if (not isinstance(lic, dict) or set(lic) != {"status", "identifier", "evidence_ref"}
                or lic.get("status") not in LICENSE_STATUSES
                or not isinstance(lic.get("identifier"), str) or not lic["identifier"].strip()
                or not isinstance(lic.get("evidence_ref"), str) or not lic["evidence_ref"].strip()):
            raise InputError(f"{cue}: açık lisans durumu ve kanıt referansı gerekli; bu satır kabul edilmiyor")
        evidence_ref = lic["evidence_ref"]
        evidence_path = PurePosixPath(evidence_ref)
        if (evidence_path.is_absolute() or ".." in evidence_path.parts or "\\" in evidence_ref
                or re.match(r"^[A-Za-z]:", evidence_ref)):
            raise InputError(f"{cue}: evidence_ref mutlak/kaçışlı yol olamaz; yerel göreli belge kimliği kullanın")
        by_id[cue] = record
    wanted = {r["cue_id"] for r in rows}
    if wanted != set(by_id):
        missing = len(wanted - set(by_id))
        extra = len(set(by_id) - wanted)
        raise InputError(f"Metadata ile dataset cue_id eşleşmiyor (eksik={missing}, fazla={extra})")
    return by_id


def _group_key(record: dict) -> str:
    # Canonical combination prevents the same episode number from different sources colliding.
    return json.dumps([record["source_id"], record["episode_id"]], ensure_ascii=False, separators=(",", ":"))


def assign_splits(rows: list[dict], metadata: dict[str, dict], seed: str) -> dict[str, str]:
    groups = sorted({_group_key(metadata[r["cue_id"]]) for r in rows})
    if len(groups) < 3:
        raise InputError("Train/validation/test için en az 3 ayrı source_id + episode_id grubu gerekli")
    ranked = sorted(groups, key=lambda group: hashlib.sha256((seed + "\0" + group).encode("utf-8")).hexdigest())
    n = len(ranked)
    counts = [max(1, math.floor(n * frac)) for _, frac in SPLITS]
    while sum(counts) > n:
        eligible = [j for j in range(3) if counts[j] > 1]
        if not eligible:
            raise InputError("Deterministik split için grup sayısı yetersiz")
        i = max(eligible, key=lambda j: (counts[j] - n * SPLITS[j][1], counts[j], -j))
        counts[i] -= 1
    while sum(counts) < n:
        i = max(range(3), key=lambda j: (n * SPLITS[j][1] - counts[j], -j))
        counts[i] += 1
    assignment = {}
    offset = 0
    for (name, _), count in zip(SPLITS, counts):
        for group in ranked[offset:offset + count]:
            assignment[group] = name
        offset += count
    # Leakage assertion: group assignment is a function, and every row must have exactly one split.
    row_splits = {r["cue_id"]: assignment[_group_key(metadata[r["cue_id"]])] for r in rows}
    if len(row_splits) != len(rows) or set(assignment.values()) != {"train", "validation", "test"}:
        raise InputError("Split leakage/coverage invariant failed; refusing output")
    return row_splits


def make_artifacts(dataset: Path, metadata_path: Path, out_dir: Path, seed: str,
                   baseline_id: str, candidate_id: str, no_regression_cer_delta: float,
                   timing_tolerance_ms: int | None) -> None:
    rows = validate_dataset(dataset)
    metadata = validate_metadata(metadata_path, rows)
    split = assign_splits(rows, metadata, seed)
    try:
        out_dir.resolve().relative_to(dataset.resolve())
    except ValueError:
        pass
    else:
        raise InputError("Deney çıktısı verified dataset klasörünün içine yazılamaz")
    out_dir.mkdir(parents=True, exist_ok=False)
    crop_rows = []
    group_split = {}
    for row in rows:
        meta = metadata[row["cue_id"]]
        group = _group_key(meta)
        assigned = split[row["cue_id"]]
        group_split[group] = assigned
        crop_rows.append({"cue_id": row["cue_id"], "group_id": group, "split": assigned,
                          "source_id": meta["source_id"], "episode_id": meta["episode_id"],
                          "license": meta["license"], "crop_path": row["crop_path"],
                          "crop_sha256_local": row["crop_sha256_local"],
                          "width": row["_width"], "height": row["_height"],
                          "review_event_id": row["review_event_id"]})
    split_doc = {"schema_version": 1, "dataset_manifest_sha256": _sha256((dataset / "manifest.jsonl").read_bytes()),
                 "metadata_sha256": _sha256(metadata_path.read_bytes()), "seed": seed,
                 "algorithm": "sha256(seed + NUL + canonical-source-episode-group), rank then fixed 80/10/10 group counts",
                 "group_split": group_split, "rows": crop_rows,
                 "invariants": {"all_rows_assigned_once": True, "group_leakage": False,
                                "human_verified_only": True, "rights_metadata_complete": True}}
    (out_dir / "split-manifest.json").write_text(json.dumps(split_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"schema_version": 1, "experiment_id": out_dir.name, "status": "PREPARED_NOT_RUN",
              "dataset": {"manifest_sha256": split_doc["dataset_manifest_sha256"],
                          "metadata_sha256": split_doc["metadata_sha256"], "rows": len(rows),
                          "groups": len(group_split), "split_counts": {s: sum(1 for x in split.values() if x == s) for s, _ in SPLITS}},
              "data_policy": {"labels": "human_accepted_or_corrected_only", "license_gate": "passed_per_row",
                              "local_only": True, "training_performed": False},
              "experiment": {"baseline_id": baseline_id, "candidate_id": candidate_id,
                             "device_backend_config": None, "normalization": "NFC + whitespace collapse + trim; case and punctuation preserved",
                             "predeclared_gate": {"max_candidate_corpus_cer_increase": no_regression_cer_delta,
                                                  "timing_tolerance_ms": timing_tolerance_ms,
                                                  "require_no_test_group_regression": True}},
              "metrics": {"ocr": {"cer": None, "wer": None, "exact_match_cue_rate": None,
                                   "cue_precision": None, "cue_recall": None,
                                   "timing_start_abs_error_ms": None, "timing_end_abs_error_ms": None,
                                   "timing_within_tolerance_rate": None, "coverage": None,
                                   "per_group": [], "micro": None, "macro": None},
                          "translation": {"status": "NOT_EVALUATED_SEPARATE_HUMAN_BILINGUAL_SET_REQUIRED"}},
              "stop_gate": {"must_have_train_validation_test": True, "trusted_reference_required": True,
                            "engine_and_config_required": True, "no_regression_gate_predeclared": True,
                            "metrics_claimable": False, "reason": "Preparation only; no evaluation run or trusted aligned reference attached."}}
    (out_dir / "experiment-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Yerel, doğrulanmış OCR veri kümesi için deney hazırlık kapısı")
    parser.add_argument("dataset", type=Path, help="verified-ocr-dataset-* klasörü")
    parser.add_argument("--metadata", required=True, type=Path, help="source/episode/lisans metadata JSON")
    parser.add_argument("--out", required=True, type=Path, help="var olmayan yeni çıktı klasörü")
    parser.add_argument("--seed", required=True, help="sabit split tohumu")
    parser.add_argument("--baseline", required=True, help="baseline motor/sürüm kimliği")
    parser.add_argument("--candidate", required=True, help="aday motor/sürüm kimliği")
    parser.add_argument("--max-cer-regression", required=True, type=float, help="önceden belirlenmiş izinli CER artış üst sınırı")
    parser.add_argument("--timing-tolerance-ms", type=int, help="referans hizası zaman toleransı; bilinmiyorsa belirtilmez")
    args = parser.parse_args(argv)
    if not args.seed.strip() or not args.baseline.strip() or not args.candidate.strip():
        parser.error("seed, baseline ve candidate boş olamaz")
    if not math.isfinite(args.max_cer_regression) or args.max_cer_regression < 0:
        parser.error("--max-cer-regression sonlu ve >= 0 olmalı")
    if args.timing_tolerance_ms is not None and args.timing_tolerance_ms < 0:
        parser.error("--timing-tolerance-ms >= 0 olmalı")
    try:
        make_artifacts(args.dataset, args.metadata, args.out, args.seed, args.baseline,
                       args.candidate, args.max_cer_regression, args.timing_tolerance_ms)
    except (InputError, OSError, UnicodeError) as exc:
        print(f"DURDURULDU: {exc}", file=sys.stderr)
        return 2
    print(f"Hazırlandı: {args.out} (eğitim/OCR/değerlendirme çalıştırılmadı)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
