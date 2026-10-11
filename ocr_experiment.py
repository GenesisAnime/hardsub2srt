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
    root = root.resolve(strict=True)
    candidate = root.joinpath(*rel.parts)
    _reject_reparse_path(candidate, root)
    path = candidate.resolve(strict=True)
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise InputError(f"crop_path veri kümesi dışına çıkıyor: {raw!r}") from exc
    return path


def _reject_reparse_components(path: Path) -> None:
    import os
    import stat
    absolute = Path(os.path.abspath(path))
    for component in list(reversed(absolute.parents)) + [absolute]:
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise InputError(f"Yol bileşeni denetlenemedi: {component.name}") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise InputError(f"Sembolik bağlantı/junction kabul edilmez: {component.name}")


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
    _reject_reparse_components(root)
    root = root.expanduser().resolve(strict=True)
    manifest = root / "manifest.jsonl"
    _reject_reparse_path(manifest, root)
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
    # Metadata is user-selected input too; reject a link/junction before the
    # initial stat or JSON read follows it.
    path = path.expanduser()
    _reject_reparse_components(path)
    try:
        path = path.resolve(strict=True)
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise InputError("Metadata 16 MiB sınırını aşıyor")
    except OSError as exc:
        raise InputError("Metadata dosyası okunamıyor") from exc
    metadata_root = path.parent.resolve(strict=True)
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
        evidence_candidate = metadata_root.joinpath(*evidence_rel.parts)
        _reject_reparse_existing_path(evidence_candidate, metadata_root)
        try:
            evidence = evidence_candidate.resolve(strict=True)
            evidence.relative_to(metadata_root)
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
    """Re-link an export to one or more parent packs and their active journals."""
    try:
        if not dataset.name.startswith("verified-ocr-dataset-"):
            raise InputError("dataset klasör adı verified-ocr-dataset-* değil")
        # Inspect every original path component before resolve/stat-style
        # probes can follow a symlink, junction, or other Windows reparse point.
        _reject_reparse_components(dataset)
        dataset = dataset.resolve(strict=True)
        lineage_path = dataset / "source-lineage-v1.json"
        _reject_reparse_existing_path(lineage_path, dataset)
        try:
            lineage_info = lineage_path.lstat()
        except FileNotFoundError:
            lineage_info = None
        if lineage_info is not None:
            return _verify_composite_provenance(dataset, rows, lineage_path)
        member = _verify_single_pack_export(dataset, rows)
        return {"status": "verified_against_parent_review_pack",
                "mode": "single_pack", "members": [{
                    "source_export_manifest_sha256": member["source_export_manifest_sha256"],
                    "review_manifest_sha256": member["review_manifest_sha256"],
                    "review_srt_sha256": member["review_srt_sha256"],
                    "review_events_sha256": member["review_events_sha256"],
                    "active_rows_verified": len(member["cue_ids"]),
                    "cue_ids": member["cue_ids"],
                }],
                "active_rows_verified": len(rows)}
    except Exception as exc:
        # A copied/export-only dataset is usable as a self-attested snapshot, but
        # must not pass the run gate without a verifiable source decision chain.
        return {"status": "self_attested", "reason": str(exc)[:240],
                "active_rows_verified": 0}


def _verify_single_pack_export(dataset: Path, rows: list[dict]) -> dict:
    """Validate one immutable Phase 2 export and its direct parent review pack."""
    import ocr_review
    _reject_reparse_components(dataset)
    dataset = dataset.resolve(strict=True)
    pack = dataset.parent.resolve(strict=True)
    if not dataset.is_dir() or not pack.is_dir() or not pack.name.endswith(".review-pack"):
        raise InputError("Phase 2 export must be directly inside its .review-pack")
    _reject_reparse_components(pack)
    # _validated_pack resolves its manifest, SRT and crops internally. Inspect
    # those lexical entries first so a link/junction is rejected before that
    # helper follows it. Manifest itself is safe to parse only after its entry
    # has passed the no-follow check.
    _reject_reparse_existing_path(pack / "manifest.json", pack)
    _reject_reparse_existing_path(pack / "review-events.jsonl", pack)
    import json
    try:
        manifest = json.loads(ocr_review._manifest_bytes(pack).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, ocr_review.ReviewError) as exc:
        raise InputError("Review-pack manifest could not be safely inspected") from exc
    if type(manifest) is not dict:
        raise InputError("Review-pack manifest is invalid")
    srt_name = manifest.get("srt_filename")
    if isinstance(srt_name, str):
        _reject_reparse_existing_path(pack / srt_name, pack)
    for cue in manifest.get("cues", []):
        crop_rel = cue.get("crop_path") if isinstance(cue, dict) else None
        if isinstance(crop_rel, str):
            rel = PurePosixPath(crop_rel)
            if not rel.is_absolute() and ".." not in rel.parts and "\\" not in crop_rel:
                _reject_reparse_existing_path(pack.joinpath(*rel.parts), pack)
    info = ocr_review._validated_pack(pack)
    protected_files = [pack / "manifest.json", info["events_path"]]
    srt_name = info["manifest"].get("srt_filename")
    if isinstance(srt_name, str):
        protected_files.append(pack / srt_name)
    for cue in info["manifest"].get("cues", []):
        crop_rel = cue.get("crop_path") if isinstance(cue, dict) else None
        if isinstance(crop_rel, str):
            rel = PurePosixPath(crop_rel)
            if not rel.is_absolute() and ".." not in rel.parts and "\\" not in crop_rel:
                protected_files.append(pack.joinpath(*rel.parts))
    for protected in protected_files:
        # This includes missing entries safely and detects dangling symlinks;
        # .exists() would follow a link before the rejection could run.
        _reject_reparse_existing_path(protected, pack)
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
    event_bytes = ocr_review._read_bounded(
        info["events_path"], ocr_review.MAX_EVENTS_BYTES, "review-events.jsonl")
    export_manifest = _read_capped(dataset / "manifest.jsonl", 64 * 1024 * 1024,
                                   "source export manifest")
    return {"source_pack_path": pack, "source_export_path": dataset,
            "source_export_manifest_sha256": _sha256(export_manifest),
            "review_manifest_sha256": info["manifest_sha256"],
            "review_srt_sha256": info["srt_sha256"],
            "review_events_sha256": _sha256(event_bytes),
            "cue_ids": sorted(row["cue_id"] for row in rows)}


def _resolve_root_relative(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise InputError(f"{label}: root-relative POSIX path required")
    rel = PurePosixPath(value)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise InputError(f"{label}: unsafe root-relative path")
    path = root.joinpath(*rel.parts)
    _reject_reparse_path(path, root)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise InputError(f"{label}: path escapes the composite root") from exc
    return resolved


def _reject_reparse_path(path: Path, root: Path) -> None:
    """Reject symlinks/junctions along a path beneath the composite root."""
    import os
    import stat
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise InputError("Path is outside the composite root") from exc
    current = root.absolute()
    candidates = [current]
    for part in relative.parts:
        current = current / part
        candidates.append(current)
    for candidate in candidates:
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise InputError(f"Path component unavailable: {candidate.name}") from exc
        attrs = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise InputError(f"Path contains a symlink or reparse point: {candidate.name}")


def _reject_reparse_existing_path(path: Path, root: Path) -> None:
    """Reject reparse points on an in-root path without following the final entry.

    Unlike _reject_reparse_path, absent components are allowed. This is useful
    for optional protected files while still detecting dangling links with
    lstat rather than Path.exists().
    """
    import os
    import stat
    try:
        relative = Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
    except ValueError as exc:
        raise InputError("Path is outside the protected root") from exc
    current = Path(os.path.abspath(root))
    for part in ("", *relative.parts):
        if part:
            current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise InputError(f"Path component unavailable: {current.name}") from exc
        attrs = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise InputError(f"Path contains a symlink or reparse point: {current.name}")


def _verify_composite_provenance(dataset: Path, rows: list[dict], lineage_path: Path) -> dict:
    import ocr_review
    root = dataset.parent.resolve(strict=True)
    if dataset.resolve().parent != root:
        raise InputError("Composite dataset must be a direct child of its local root")
    _reject_reparse_path(dataset, root)
    _reject_reparse_path(lineage_path, root)
    lineage_bytes = _read_capped(lineage_path, 16 * 1024 * 1024,
                                 "source-lineage-v1.json")
    try:
        lineage = json.loads(lineage_bytes.decode("utf-8"), object_pairs_hook=_unique_json_pairs)
    except (UnicodeError, json.JSONDecodeError, InputError) as exc:
        raise InputError("source-lineage-v1.json is invalid JSON") from exc
    if (type(lineage) is not dict or set(lineage) != {"schema_version", "kind", "members"}
            or type(lineage.get("schema_version")) is not int or lineage["schema_version"] != 1
            or lineage.get("kind") != "phase2-multi-pack-verified-export"
            or type(lineage.get("members")) is not list
            or not 3 <= len(lineage["members"]) <= 1000):
        raise InputError("source-lineage-v1.json schema/member count invalid")
    composite_by_id = {row["cue_id"]: row for row in rows}
    if len(composite_by_id) != len(rows):
        raise InputError("Composite dataset has cue_id collisions")
    seen_cues, seen_exports, seen_packs = set(), set(), set()
    verified_members = []
    source_crop_cache = {}
    for member in lineage["members"]:
        required = {"source_pack_path", "source_export_path", "source_export_manifest_sha256",
                    "review_manifest_sha256", "review_srt_sha256", "review_events_sha256", "cue_ids"}
        if (type(member) is not dict or set(member) != required
                or type(member.get("cue_ids")) is not list or not member["cue_ids"]
                or any(type(cue_id) is not str for cue_id in member["cue_ids"])):
            raise InputError("Malformed composite source member")
        pack = _resolve_root_relative(root, member["source_pack_path"], "source_pack_path")
        export = _resolve_root_relative(root, member["source_export_path"], "source_export_path")
        if (pack == root or not pack.is_relative_to(root)
                or pack.is_relative_to(dataset)
                or not pack.name.endswith(".review-pack") or export.parent != pack
                or not export.name.startswith("verified-ocr-dataset-")
                or not pack.is_dir() or not export.is_dir()):
            raise InputError("Source export must be directly inside its .review-pack")
        pack_key, export_key = str(pack), str(export)
        if pack_key in seen_packs or export_key in seen_exports:
            raise InputError("Duplicate source review pack/export in composite lineage")
        seen_packs.add(pack_key)
        seen_exports.add(export_key)
        _reject_reparse_path(export / "manifest.jsonl", pack)
        source_rows = validate_dataset(export)
        source_manifest = _read_capped(export / "manifest.jsonl", 64 * 1024 * 1024,
                                       "source export manifest")
        if _sha256(source_manifest) != member["source_export_manifest_sha256"]:
            raise InputError("Source export manifest hash changed")
        source_info = _verify_single_pack_export(export, source_rows)
        for hash_name in ("review_manifest_sha256", "review_srt_sha256", "review_events_sha256"):
            if source_info[hash_name] != member[hash_name]:
                raise InputError(f"Source review-pack {hash_name} changed")
        source_ids = {row["cue_id"] for row in source_rows}
        member_ids = set(member["cue_ids"])
        if len(member_ids) != len(member["cue_ids"]) or member_ids != source_ids:
            raise InputError("Lineage cue list does not exactly match source export")
        for source_row in source_rows:
            cue_id = source_row["cue_id"]
            if cue_id in seen_cues:
                raise InputError("Duplicate cue_id across source exports")
            seen_cues.add(cue_id)
            composite_row = composite_by_id.get(cue_id)
            if composite_row is None:
                raise InputError("Composite row missing its source export cue")
            expected = {key: value for key, value in source_row.items()
                        if not key.startswith("_")}
            source_crop = _safe_crop(export, source_row["crop_path"])
            digest = source_row["crop_sha256_local"]
            target_crop = f"crops/{digest}.jpg"
            expected["crop_path"] = target_crop
            composite_row_public = {key: value for key, value in composite_row.items()
                                    if not key.startswith("_")}
            if composite_row_public != expected:
                mismatches = [key for key in set(composite_row_public) | set(expected)
                              if composite_row_public.get(key) != expected.get(key)]
                raise InputError("Composite cue differs from its human-reviewed source export: " +
                                 ",".join(sorted(mismatches)))
            cached = source_crop_cache.get(digest)
            if cached is None:
                crop_bytes = _read_capped(source_crop, MAX_CROP_BYTES, "source crop")
                source_crop_cache[digest] = crop_bytes
                cached = crop_bytes
            composite_crop = _safe_crop(dataset, target_crop)
            _reject_reparse_path(composite_crop, dataset)
            data = _read_capped(composite_crop, MAX_CROP_BYTES, "composite crop")
            if _sha256(data) != digest or data != cached:
                raise InputError("Composite crop does not byte-match its source export")
        verified_members.append({
            "source_pack_sha256": _sha256(_read_capped(pack / "manifest.json", ocr_review.MAX_MANIFEST_BYTES,
                                                        "source review manifest")),
            "source_export_manifest_sha256": member["source_export_manifest_sha256"],
            "review_srt_sha256": member["review_srt_sha256"],
            "review_events_sha256": member["review_events_sha256"],
            "active_rows_verified": len(source_rows),
            "cue_ids": sorted(source_ids),
        })
    if seen_cues != set(composite_by_id):
        raise InputError("Composite rows are not exactly covered by source lineage")
    return {"status": "verified_against_parent_review_pack", "mode": "composite",
            "lineage_sha256": _sha256(lineage_bytes), "members": verified_members,
            "active_rows_verified": len(seen_cues)}


def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_provenance_groups(provenance: dict, metadata: dict[str, dict], rows: list[dict]) -> None:
    """Require one truthful source+episode group per independent review pack."""
    if provenance.get("status") != "verified_against_parent_review_pack":
        raise InputError("Phase 2 source lineage is not verified")
    row_ids = {row["cue_id"] for row in rows}
    groups_seen = set()
    member_cues_seen = set()
    members = provenance.get("members")
    if not isinstance(members, list) or len(members) < 3:
        raise InputError("At least three independent source review packs are required")
    for member in members:
        cue_ids = member.get("cue_ids") if isinstance(member, dict) else None
        if (not isinstance(cue_ids, list) or not cue_ids or
                any(not isinstance(cue_id, str) for cue_id in cue_ids)):
            raise InputError("Verified lineage member cue list is invalid")
        member_ids = set(cue_ids)
        if len(member_ids) != len(cue_ids) or not member_ids.issubset(row_ids) or member_cues_seen & member_ids:
            raise InputError("Source lineage cue coverage/collision invalid")
        member_cues_seen.update(member_ids)
        group_keys = {_group_key(metadata[cue_id]) for cue_id in member_ids if cue_id in metadata}
        if len(group_keys) != 1:
            raise InputError("Every source review pack must map to exactly one source+episode group")
        group = next(iter(group_keys))
        if group in groups_seen:
            raise InputError("Different source review packs cannot claim the same source+episode group")
        groups_seen.add(group)
    if member_cues_seen != row_ids or set(metadata) != row_ids or len(groups_seen) < 3:
        raise InputError("At least three distinct, fully covered source+episode groups are required")


def provenance_summary(provenance: dict) -> dict:
    """Return hash-bound lineage summary without private cue IDs or local paths."""
    summary = {key: value for key, value in provenance.items() if key != "members"}
    members = []
    for member in provenance.get("members", []):
        members.append({key: value for key, value in member.items()
                        if key not in {"cue_ids", "source_pack_path", "source_export_path"}})
    summary["members"] = members
    stable = json.dumps(summary, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False).encode("utf-8")
    summary["provenance_sha256"] = _sha256(stable)
    return summary


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
    provenance_full = verify_phase2_provenance(dataset, rows)
    validate_provenance_groups(provenance_full, metadata, rows)
    provenance = provenance_summary(provenance_full)
    split = assign_splits(rows, metadata, seed)
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
                 "metadata_sha256": _sha256(metadata_path.read_bytes()),
                 "provenance_sha256": provenance["provenance_sha256"], "seed": seed,
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
