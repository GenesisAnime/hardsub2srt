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
import unicodedata
from datetime import datetime
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
LICENSE_STATUSES = {"public_domain", "permissive_license", "permission_granted", "user_owned"}
SPLITS = (("train", 0.80), ("validation", 0.10), ("test", 0.10))
MAX_ROWS = 100_000
MAX_CROP_BYTES = 8 * 1024 * 1024
MAX_TOTAL_CROP_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_RIGHTS_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_RIGHTS_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_SIDE = 1280
MAX_IMAGE_PIXELS = 1280 * 1280


class InputError(ValueError):
    pass


def _json(path: Path):
    try:
        return json.loads(_read_capped(path, MAX_METADATA_BYTES, "JSON metadata").decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"JSON okunamadı: {path.name}: {exc}") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_capped(path: Path, cap: int, label: str) -> bytes:
    try:
        if path.stat().st_size > cap:
            raise InputError(f"{label} {cap} byte sınırını aşıyor")
        with path.open("rb") as stream:
            data = stream.read(cap + 1)
    except OSError as exc:
        raise InputError(f"{label} okunamıyor") from exc
    if len(data) > cap:
        raise InputError(f"{label} {cap} byte sınırını aşıyor")
    return data


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
    width, height = _jpeg_dimensions(data, path.name)
    if (width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE or
            width * height > MAX_IMAGE_PIXELS):
        raise InputError(f"Kırpım boyutu sınırı aşıyor (en çok 1280×1280): {path.name}")
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise InputError("Kırpım çözümleme için OpenCV ve NumPy gerekli; fail-closed") from exc
    try:
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception as exc:
        raise InputError(f"Kırpım çözümlenemedi: {path.name}") from exc
    if image is None or image.ndim != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise InputError(f"Kırpım çözümlenemedi: {path.name}")
    decoded_width, decoded_height = int(image.shape[1]), int(image.shape[0])
    if (decoded_width, decoded_height) != (width, height):
        raise InputError(f"JPEG başlığı ile çözülen boyut eşleşmiyor: {path.name}")
    return width, height


def _jpeg_dimensions(data: bytes, label: str) -> tuple[int, int]:
    """Read dimensions from bounded JPEG markers before asking a decoder to allocate pixels."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise InputError(f"JPEG SOI imzası geçersiz: {label}")
    pos = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while pos < len(data):
        if data[pos] != 0xFF:
            raise InputError(f"JPEG marker dizisi bozuk: {label}")
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD9, 0xDA):  # EOI or SOS before a frame header.
            break
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if pos + 2 > len(data):
            break
        segment_length = int.from_bytes(data[pos:pos + 2], "big")
        if segment_length < 2 or pos + segment_length > len(data):
            raise InputError(f"JPEG segment sınırı geçersiz: {label}")
        if marker in sof:
            if segment_length < 8:
                raise InputError(f"JPEG frame başlığı kısa: {label}")
            height = int.from_bytes(data[pos + 3:pos + 5], "big")
            width = int.from_bytes(data[pos + 5:pos + 7], "big")
            if not width or not height:
                raise InputError(f"JPEG boyutu sıfır: {label}")
            return width, height
        pos += segment_length
    raise InputError(f"JPEG frame boyutu bulunamadı: {label}")


def validate_dataset(root: Path) -> list[dict]:
    manifest = root / "manifest.jsonl"
    if not root.is_dir() or not manifest.is_file():
        raise InputError("Girdi, manifest.jsonl içeren verified OCR veri kümesi klasörü olmalı")
    manifest_bytes = _read_capped(manifest, 64 * 1024 * 1024, "manifest.jsonl")
    rows = []
    seen = set()
    seen_crops = set()
    total_crop_bytes = 0
    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise InputError("manifest.jsonl UTF-8 değil") from exc
    for line_no, line in enumerate(manifest_text.splitlines(), 1):
        if not line.strip():
            continue
        if len(rows) >= MAX_ROWS:
            raise InputError(f"Satır sayısı {MAX_ROWS} sınırını aşıyor")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputError(f"Manifest JSONL satır {line_no} bozuk") from exc
        required = {"schema_version", "cue_id", "source_text_verified", "crop_path",
                    "crop_sha256_local", "start_ms", "end_ms", "ocr_confidence",
                    "review_event_id", "reviewed_at"}
        if not isinstance(row, dict) or set(row) != required or row.get("schema_version") != 1:
            raise InputError(f"Satır {line_no}: Phase 2 verified export şemasıyla eşleşmiyor")
        cue = row.get("cue_id")
        if not isinstance(cue, str) or not cue or cue in seen:
            raise InputError(f"Satır {line_no}: cue_id eksik veya tekrar ediyor")
        seen.add(cue)
        if not isinstance(row.get("source_text_verified"), str) or not row["source_text_verified"].strip():
            raise InputError(f"Satır {line_no}: insan tarafından doğrulanmış metin eksik")
        if (type(row.get("start_ms")) is not int or type(row.get("end_ms")) is not int
                or row["start_ms"] < 0 or row["end_ms"] <= row["start_ms"]):
            raise InputError(f"Satır {line_no}: zaman aralığı geçersiz")
        confidence = row.get("ocr_confidence")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                                       or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise InputError(f"Satır {line_no}: ocr_confidence 0..1 veya null olmalı")
        digest = row.get("crop_sha256_local")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InputError(f"Satır {line_no}: crop SHA-256 geçersiz")
        crop = _safe_crop(root, row.get("crop_path"))
        data = _read_capped(crop, MAX_CROP_BYTES, f"Satır {line_no} crop")
        if not data or len(data) > MAX_CROP_BYTES or _sha256(data) != digest:
            raise InputError(f"Satır {line_no}: crop boş, çok büyük veya SHA-256 eşleşmiyor")
        if digest not in seen_crops:
            total_crop_bytes += len(data)
            seen_crops.add(digest)
            if total_crop_bytes > MAX_TOTAL_CROP_BYTES:
                raise InputError("Benzersiz kırpımların toplamı 128 MiB sınırını aşıyor")
        width, height = _decode_image(crop, data)
        rows.append({**row, "_width": width, "_height": height})
    if not rows:
        raise InputError("Veri kümesinde insan tarafından doğrulanmış örnek yok")
    return rows


def validate_metadata(path: Path, rows: list[dict]) -> dict[str, dict]:
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise InputError("Metadata 16 MiB sınırını aşıyor")
    except OSError as exc:
        raise InputError("Metadata dosyası okunamıyor") from exc
    doc = _json(path)
    if not isinstance(doc, dict) or set(doc) != {"schema_version", "records"} or doc.get("schema_version") != 1:
        raise InputError("Metadata kök şeması schema_version=1 ve records olmalı")
    if not isinstance(doc["records"], list):
        raise InputError("Metadata records liste olmalı")
    by_id = {}
    evidence_cache = {}
    total_evidence_bytes = 0
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
        if (not isinstance(lic, dict) or set(lic) != {"status", "identifier", "evidence_path", "evidence_sha256", "human_review"}
                or lic.get("status") not in LICENSE_STATUSES
                or not isinstance(lic.get("identifier"), str) or not lic["identifier"].strip()
                or not isinstance(lic.get("evidence_path"), str) or not lic["evidence_path"].strip()
                or not isinstance(lic.get("evidence_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", lic["evidence_sha256"])):
            raise InputError(f"{cue}: lisans metadata'sı ve hash'li kanıt dosyası zorunlu")
        evidence_rel = PurePosixPath(lic["evidence_path"])
        if (evidence_rel.is_absolute() or ".." in evidence_rel.parts or "\\" in lic["evidence_path"]
                or re.match(r"^[A-Za-z]:", lic["evidence_path"])):
            raise InputError(f"{cue}: evidence_path mutlak/kaçışlı yol olamaz")
        evidence = (path.resolve().parent.joinpath(*evidence_rel.parts)).resolve()
        try:
            evidence.relative_to(path.resolve().parent)
            evidence_key = str(evidence)
            if evidence_key in evidence_cache:
                evidence_digest, evidence_size = evidence_cache[evidence_key]
            else:
                evidence_stat = evidence.stat()
                if (not evidence.is_file() or evidence_stat.st_size <= 0
                        or evidence_stat.st_size > MAX_RIGHTS_EVIDENCE_BYTES
                        or total_evidence_bytes + evidence_stat.st_size > MAX_TOTAL_RIGHTS_EVIDENCE_BYTES):
                    raise InputError(f"{cue}: lisans kanıtı boyut sınırını aşıyor veya dosya değil")
                evidence_bytes = _read_capped(evidence, MAX_RIGHTS_EVIDENCE_BYTES,
                                              f"{cue} lisans kanıtı")
                evidence_digest, evidence_size = _sha256(evidence_bytes), len(evidence_bytes)
                evidence_cache[evidence_key] = (evidence_digest, evidence_size)
                total_evidence_bytes += evidence_size
        except (ValueError, OSError) as exc:
            raise InputError(f"{cue}: lisans kanıtı eksik veya metadata dizini dışına çıkıyor") from exc
        if (not evidence_size or evidence_size > MAX_RIGHTS_EVIDENCE_BYTES
                or total_evidence_bytes > MAX_TOTAL_RIGHTS_EVIDENCE_BYTES
                or evidence_digest != lic["evidence_sha256"]):
            raise InputError(f"{cue}: lisans kanıtı boş, çok büyük veya hash'i eşleşmiyor")
        human_review = lic.get("human_review")
        if (not isinstance(human_review, dict) or set(human_review) != {"status", "reviewer", "reviewed_at"}
                or human_review.get("status") not in {"pending", "reviewed"}
                or (human_review["status"] == "reviewed" and
                    (not isinstance(human_review.get("reviewer"), str) or not human_review["reviewer"].strip()
                     or not isinstance(human_review.get("reviewed_at"), str) or not human_review["reviewed_at"].strip()))):
            raise InputError(f"{cue}: human_review alanı pending veya reviewed + reviewer/tarih olmalı")
        if human_review["status"] == "reviewed":
            try:
                reviewed_at = datetime.fromisoformat(human_review["reviewed_at"].replace("Z", "+00:00"))
            except (TypeError, ValueError) as exc:
                raise InputError(f"{cue}: rights review tarihi ISO-8601 olmalı") from exc
            if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
                raise InputError(f"{cue}: rights review tarihi timezone içermeli")
        by_id[cue] = record
    wanted = {r["cue_id"] for r in rows}
    if wanted != set(by_id):
        missing = len(wanted - set(by_id))
        extra = len(set(by_id) - wanted)
        raise InputError(f"Metadata ile dataset cue_id eşleşmiyor (eksik={missing}, fazla={extra})")
    return by_id


def _group_key(record: dict) -> str:
    # NFC, collapsed whitespace, and case-folding make equivalent group IDs co-locate.
    canonical = lambda value: " ".join(unicodedata.normalize("NFC", value).split()).casefold()
    return json.dumps([canonical(record["source_id"]), canonical(record["episode_id"])],
                      ensure_ascii=False, separators=(",", ":"))


def verify_phase2_provenance(dataset: Path, rows: list[dict]) -> dict:
    """Re-link export rows to the parent review pack and its active review journal."""
    try:
        import ocr_review
        if not dataset.name.startswith("verified-ocr-dataset-"):
            raise InputError("dataset klasör adı verified-ocr-dataset-* değil")
        parent = dataset.resolve().parent
        info = ocr_review._validated_pack(parent)
        active, warnings = ocr_review._active(info, force=True)
        if warnings:
            raise InputError("review-events.jsonl contains invalid, stale, or excluded records")
        cues = {cue["cue_id"]: cue for cue in info["cues"]}
        if not {row["cue_id"] for row in rows}.issubset(cues):
            raise InputError("export contains a cue absent from its parent manifest")
        for row in rows:
            cue = cues[row["cue_id"]]
            event = active.get(row["cue_id"])
            if (not event or event.get("status") not in ("accepted", "corrected")
                    or event.get("event_id") != row["review_event_id"]
                    or event.get("verified_source_text") != row["source_text_verified"]
                    or event.get("crop_sha256_local") != row["crop_sha256_local"]
                    or cue.get("crop_sha256_local") != row["crop_sha256_local"]
                    or not cue.get("crop_valid")
                    or cue.get("start_ms") != row["start_ms"]
                    or cue.get("end_ms") != row["end_ms"]
                    or cue.get("ocr_confidence") != row.get("ocr_confidence")):
                raise InputError("export row does not match its active parent review decision")
        event_bytes = info["events_path"].read_bytes()
        return {"status": "verified_against_parent_review_pack",
                "review_manifest_sha256": info["manifest_sha256"],
                "review_srt_sha256": info["srt_sha256"],
                "review_events_sha256": _sha256(event_bytes),
                "active_rows_verified": len(rows)}
    except Exception as exc:
        # A copied/export-only dataset is usable as a self-attested snapshot, but
        # must not pass the run gate without a verifiable source decision chain.
        return {"status": "self_attested", "reason": str(exc)[:240],
                "active_rows_verified": 0}


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
    provenance = verify_phase2_provenance(dataset, rows)
    human_rights_review_complete = all(
        meta["license"]["human_review"]["status"] == "reviewed" for meta in metadata.values())
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
                          "ocr_confidence": row["ocr_confidence"],
                          "width": row["_width"], "height": row["_height"],
                          "review_event_id": row["review_event_id"]})
    split_doc = {"schema_version": 1, "dataset_manifest_sha256": _sha256((dataset / "manifest.jsonl").read_bytes()),
                 "metadata_sha256": _sha256(metadata_path.read_bytes()), "seed": seed,
                 "algorithm": "sha256(seed + NUL + canonical-source-episode-group), rank then fixed 80/10/10 group counts",
                 "group_split": group_split, "rows": crop_rows,
                 "invariants": {"all_rows_assigned_once": True, "group_leakage": False,
                                "phase2_provenance_verified": provenance["status"] == "verified_against_parent_review_pack",
                                "rights_evidence_present_self_attested": True,
                                "rights_verified": False,
                                "human_rights_review_complete": human_rights_review_complete}}
    (out_dir / "split-manifest.json").write_text(json.dumps(split_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"schema_version": 1, "experiment_id": out_dir.name, "status": "PREPARED_NOT_RUN",
              "dataset": {"manifest_sha256": split_doc["dataset_manifest_sha256"],
                          "metadata_sha256": split_doc["metadata_sha256"], "rows": len(rows),
                          "groups": len(group_split),
                          "row_split_counts": {s: sum(1 for x in split.values() if x == s) for s, _ in SPLITS},
                          "group_split_counts": {s: sum(1 for x in group_split.values() if x == s) for s, _ in SPLITS}},
              "provenance": provenance,
              "data_policy": {"labels": "active_local_review_events_linked" if provenance["status"] == "verified_against_parent_review_pack" else "self_attested_unverified",
                              "license_gate": "evidence_present_self_attested",
                              "rights_verified": False,
                              "human_rights_review_complete": human_rights_review_complete,
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
                            "human_provenance_verified": provenance["status"] == "verified_against_parent_review_pack",
                            "human_rights_review_required": True,
                            "human_rights_review_complete": human_rights_review_complete,
                            "rights_verified": False,
                            "experiment_run_blocked": True,
                            "metrics_claimable": False,
                            "reason": "Preparation only; no evaluation run or trusted aligned reference. Human identity is unverified and no programmatic rights/legal verification is possible."}}
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
