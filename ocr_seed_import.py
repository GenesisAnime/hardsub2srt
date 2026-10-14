#!/usr/bin/env python3
"""Convert assistant-reviewed screenshot candidates into local review packs.

This importer never accepts labels. Every transcription candidate remains an
unreviewed suggestion until a person records an explicit OCR decision in the
existing /ocr-inceleme workflow.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import sys
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import cv2
import numpy as np


SEED_SCHEMA = "hardsub2srt.screenshot-text-example"
SEED_VERSION = 1
PACK_SCHEMA = "hardsub2srt.assistant-reviewed-seed-import"
MAX_SEED_BYTES = 16 * 1024 * 1024
MAX_SEED_ROWS = 500
MAX_EVIDENCE_FILE_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGE_PIXELS = 64_000_000
MAX_CROP_DIMENSION = 1280
MAX_CROP_BYTES = 8 * 1024 * 1024
MAX_PACK_CROP_BYTES = 128 * 1024 * 1024
MAX_IMPORT_CROP_BYTES = 256 * 1024 * 1024
MAX_IMPORT_REPORT_BYTES = 1024 * 1024
MAX_TEXT_CHARS = 20_000
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
EPISODE = re.compile(r"^s[0-9]{2,4}e[0-9]{2,5}$", re.IGNORECASE)
REVIEW_MAPPING = "exact_original_ocr_segment_needs_visual_confirmation"


class ImportError(ValueError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ImportError(f"Yinelenen JSON alanı: {key}")
        result[key] = value
    return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_no_reparse(path: Path, stop: Path | None = None) -> None:
    """Reject symlink/junction components before opening local seed assets."""
    absolute = path.absolute()
    boundary = stop.absolute() if stop else Path(absolute.anchor)
    try:
        relative = absolute.relative_to(boundary)
    except ValueError as exc:
        raise ImportError("Yol izin verilen dizin ağacının dışında") from exc
    current = boundary
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ImportError("Sembolik bağlantı/junction yolu kabul edilmiyor")


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _local_output_root(raw: Path, seed_root: Path) -> tuple[Path, Path]:
    if raw.is_absolute() is False:
        raise ImportError("Çıktı yolu mutlak yerel yol olmalı")
    if any(part == ".." for part in raw.parts):
        raise ImportError("Çıktı yolunda .. bileşeni kabul edilmiyor")
    text = os.fspath(raw)
    if text.startswith(("\\\\", "//")):
        raise ImportError("UNC/ağ yolu çıktı klasörü kabul edilmiyor")
    try:
        _check_no_reparse(raw.parent)
        parent = raw.parent.resolve(strict=True)
        if not parent.is_dir():
            raise ImportError("Çıktının üst klasörü mevcut bir klasör olmalı")
        _check_no_reparse(parent)
        target = parent / raw.name
        if target.exists() or target.is_symlink():
            raise ImportError("Çıktı klasörü zaten var; üzerine yazılmayacak")
        if _under(target, seed_root) or _under(seed_root, target):
            raise ImportError("Çıktı, seed/evidence ağacıyla çakışamaz")
        if os.name == "nt" and parent.drive:
            import ctypes
            if int(ctypes.windll.kernel32.GetDriveTypeW(parent.drive + "\\")) == 4:
                raise ImportError("Eşlenmiş ağ sürücüsü yerel çıktı için kabul edilmiyor")
        for variable in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
            configured = os.environ.get(variable, "").strip()
            if configured:
                sync_root = Path(configured).expanduser().resolve(strict=False)
                if _under(target, sync_root):
                    raise ImportError("Çıktı eşitlenen klasörde; eşitlenmeyen yerel klasör seçin")
        return parent, target
    except OSError as exc:
        raise ImportError("Çıktı klasörü güvenle çözülemedi") from exc


def _publish_noreplace(staging: Path, target: Path) -> None:
    """Atomically publish a directory only if target does not already exist."""
    if os.name == "nt":
        try:
            os.rename(staging, target)
        except FileExistsError as exc:
            raise ImportError("Çıktı import sırasında oluşturuldu; üzerine yazılmadı") from exc
        return
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, "renameat2", None)
        if function is None:
            raise ImportError("renameat2 no-replace yok; atomik yayınlama durduruldu")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p,
                             ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = function(-100, os.fsencode(staging), -100, os.fsencode(target), 1)
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, "renamex_np", None)
        if function is None:
            raise ImportError("renamex_np no-replace yok; atomik yayınlama durduruldu")
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = function(os.fsencode(staging), os.fsencode(target), 0x00000004)
    else:
        raise ImportError(f"{sys.platform} için atomik no-replace yayınlama tanımlı değil")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise ImportError("Çıktı import sırasında oluşturuldu; üzerine yazılmadı")
    raise OSError(error_number, os.strerror(error_number), os.fspath(target))


def _strict_relative_image(value: object, seed_root: Path) -> Path:
    if type(value) is not str or not value or "\\" in value:
        raise ImportError("image alanı göreli POSIX yolu olmalı")
    rel = PurePosixPath(value)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise ImportError("image yolu mutlak veya kaçışlı olamaz")
    if len(rel.parts) < 2 or rel.parts[0] != "evidence":
        raise ImportError("image yolu evidence/ altında olmalı")
    path = seed_root.joinpath(*rel.parts)
    _check_no_reparse(path, seed_root)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(seed_root)
    except (OSError, ValueError) as exc:
        raise ImportError("Kanıt görseli bulunamadı veya seed kökünün dışına çıkıyor") from exc
    if not resolved.is_file() or resolved.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ImportError("Kanıt görseli normal JPG/PNG dosyası olmalı")
    return resolved


def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24 or data[12:16] != b"IHDR":
            return None
        return struct.unpack(">II", data[16:24])
    if not data.startswith(b"\xff\xd8"):
        return None
    pos = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
           0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while pos < len(data):
        if data[pos] != 0xFF:
            return None
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            return None
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if pos + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[pos:pos + 2], "big")
        if segment_length < 2 or pos + segment_length > len(data):
            return None
        if marker in sof:
            if segment_length < 7:
                return None
            height = int.from_bytes(data[pos + 3:pos + 5], "big")
            width = int.from_bytes(data[pos + 5:pos + 7], "big")
            return width, height
        if marker == 0xDA:
            return None
        pos += segment_length
    return None


def _normalize_crop(data: bytes) -> tuple[bytes, int, int]:
    dimensions = _image_dimensions(data)
    if not dimensions:
        raise ImportError("Kanıt görseli geçerli JPEG/PNG değil")
    width, height = dimensions
    if (width < 1 or height < 1 or width > MAX_IMAGE_DIMENSION or
            height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS):
        raise ImportError("Kanıt görseli çözünürlük sınırını aşıyor")
    try:
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise ImportError("Kanıt görseli güvenle çözülemedi") from exc
    if image is None or image.ndim != 3 or image.shape[1] != width or image.shape[0] != height:
        raise ImportError("Kanıt görseli güvenle çözülemedi")
    if max(width, height) > MAX_CROP_DIMENSION:
        scale = MAX_CROP_DIMENSION / max(width, height)
        try:
            image = cv2.resize(
                image, (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA)
        except cv2.error as exc:
            raise ImportError("Kanıt görseli güvenle küçültülemedi") from exc
    height, width = image.shape[:2]
    try:
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 86])
    except cv2.error as exc:
        raise ImportError("Kanıt görseli JPEG olarak hazırlanamadı") from exc
    if not ok:
        raise ImportError("Kanıt görseli JPEG olarak hazırlanamadı")
    crop = encoded.tobytes()
    if not crop or len(crop) > MAX_CROP_BYTES:
        raise ImportError("İçe aktarılan JPEG crop 8 MiB sınırını aşıyor")
    return crop, width, height


def _validate_row(row: object, seed_root: Path,
                  images: dict[str, tuple[Path, bytes]],
                  crop_cache: dict[str, tuple[bytes, int, int]]) -> dict:
    expected = {"schema", "version", "image", "image_sha256", "episode_key",
                "video_identity", "cue_start_ms", "cue_end_ms", "observed_srt_text",
                "target_text", "label_source", "action", "training_eligible",
                "user_confirmation_status", "rights_status", "dataset_status"}
    if type(row) is not dict or set(row) != expected:
        raise ImportError("Seed satırı tam v1 şemasıyla eşleşmiyor")
    if row.get("schema") != SEED_SCHEMA or type(row.get("version")) is not int or row["version"] != SEED_VERSION:
        raise ImportError("Desteklenmeyen seed schema/version")
    if (row.get("label_source") != "assistant_visual_review" or
            row.get("user_confirmation_status") != "pending" or
            row.get("rights_status") != "not_verified" or
            row.get("dataset_status") != "candidate_needs_human_review" or
            row.get("training_eligible") is not False):
        raise ImportError("Beklenmeyen seed label_source")
    episode = row.get("episode_key")
    if type(episode) is not str or not EPISODE.fullmatch(episode):
        raise ImportError("episode_key biçimi geçersiz")
    identity = row.get("video_identity")
    if type(identity) is not dict or set(identity) != {"size", "sha1_first_1mb"}:
        raise ImportError("video_identity alanı geçersiz")
    if (type(identity.get("size")) is not int or identity["size"] <= 0 or
            type(identity.get("sha1_first_1mb")) is not str or
            not SHA1.fullmatch(identity["sha1_first_1mb"])):
        raise ImportError("video_identity boyutu/hash'i geçersiz")
    start, end = row.get("cue_start_ms"), row.get("cue_end_ms")
    if (type(start) is not int or type(end) is not int or start < 0 or end <= start):
        raise ImportError("Cue zamanları geçersiz")
    old = row.get("observed_srt_text")
    if type(old) is not str or not old.strip() or len(old) > MAX_TEXT_CHARS:
        raise ImportError("observed_srt_text boş veya sınır dışı")
    action, target = row.get("action"), row.get("target_text")
    if action == "replace_text":
        if type(target) is not str or not target.strip() or len(target) > MAX_TEXT_CHARS:
            raise ImportError("replace_text assistant adayı geçersiz")
    elif action == "remove_cue":
        if target is not None:
            raise ImportError("remove_cue örneğinde target_text null olmalı")
    else:
        raise ImportError("Bilinmeyen seed action")
    image_path = _strict_relative_image(row.get("image"), seed_root)
    image_name = PurePosixPath(row["image"]).as_posix()
    data = images.get(image_name, (None, None))[1]
    if data is None:
        if image_path.stat().st_size > MAX_EVIDENCE_FILE_BYTES:
            raise ImportError("Kanıt görseli 16 MiB sınırını aşıyor")
        data = image_path.read_bytes()
        images[image_name] = (image_path, data)
    digest = row.get("image_sha256")
    if type(digest) is not str or not HEX64.fullmatch(digest) or _sha256(data) != digest:
        raise ImportError("Orijinal kanıt görselinin SHA-256 değeri eşleşmiyor")
    normalized_crop, width, height = crop_cache.get(digest, (None, None, None))
    if normalized_crop is None:
        normalized_crop, width, height = _normalize_crop(data)
        crop_cache[digest] = (normalized_crop, width, height)
    if action == "replace_text":
        crop = normalized_crop
    else:
        # Validate negative evidence too, but never export it as OCR text.
        crop = None
    return {"row": row, "episode_key": episode.lower(),
            "identity": {"size": identity["size"],
                         "sha1_first_1mb": identity["sha1_first_1mb"]},
            "start_ms": start, "end_ms": end, "old_text": old.strip(),
            "candidate_text": target.strip() if isinstance(target, str) else None,
            "action": action, "image_name": image_name, "image_path": image_path,
            "image_bytes": data, "image_sha256": digest,
            "crop_bytes": crop, "crop_width": width, "crop_height": height}


def _srt_timestamp(ms: int) -> str:
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _build_pack(pack: Path, group: list[dict], seed_sha256: str) -> dict:
    job_id = uuid.uuid4().hex
    srt_name = "seed-observed.srt"
    rows = sorted(group, key=lambda item: (item["start_ms"], item["end_ms"]))
    srt_blocks, cue_records = [], []
    crop_sizes = {}
    crop_paths = {}
    (pack / "crops").mkdir(parents=True)
    for index, item in enumerate(rows, 1):
        cue_id = f"{job_id}-cue-{index:05d}"
        srt_blocks.append(
            f"{index}\n{_srt_timestamp(item['start_ms'])} --> "
            f"{_srt_timestamp(item['end_ms'])}\n{item['old_text']}"
        )
        crop_rel = None
        crop_sha = None
        crop_status = "unavailable"
        if item["action"] == "replace_text":
            crop_sha = _sha256(item["crop_bytes"])
            crop_rel = crop_paths.get(crop_sha)
            if crop_rel is None:
                crop_rel = f"crops/{crop_sha}.jpg"
                _write_new(pack / crop_rel, item["crop_bytes"])
                crop_paths[crop_sha] = crop_rel
                crop_sizes[crop_sha] = len(item["crop_bytes"])
            crop_status = "available_needs_visual_confirmation"
            if sum(crop_sizes.values()) > MAX_PACK_CROP_BYTES:
                raise ImportError("Paket crop toplamı 128 MiB sınırını aşıyor")
        cue_records.append({
            "cue_id": cue_id, "index": index - 1,
            "start_ms": item["start_ms"], "end_ms": item["end_ms"],
            "source_text_ocr": item["old_text"],
            "text_stage": "assistant_reviewed_seed_candidate_unverified_by_user",
            "ocr_confidence": None,
            "representative_frame_index": None,
            "representative_frame_ms": item["start_ms"],
            "mapping_status": REVIEW_MAPPING,
            "source_region": "main_band",
            "source_band_y": None, "source_band_height": None,
            "crop_status": crop_status, "crop_path": crop_rel,
            "crop_sha256_local": crop_sha,
            "crop_width": item["crop_width"], "crop_height": item["crop_height"],
            "roi_width": item["crop_width"], "roi_height": item["crop_height"],
            "duplicate_of_cue_id": None,
            "seed_candidate_text": item["candidate_text"],
            "seed_row_sha256": _sha256(
                (json.dumps(item["row"], ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")) + "\n").encode("utf-8")),
            "seed_image_sha256": item["image_sha256"],
            "seed_image_path": f"../../seed-source/{item['image_name']}",
            "seed_label_source": "assistant_visual_review",
            "seed_user_confirmation_status": "pending",
            "seed_rights_status": "not_verified",
            "seed_dataset_status": "candidate_needs_human_review",
            "seed_training_eligible": False,
            "episode_key": item["episode_key"],
            "video_identity": item["identity"],
        })
    srt_bytes = ("\n\n".join(srt_blocks) + "\n").encode("utf-8")
    _write_new(pack / srt_name, srt_bytes)
    manifest = {
        "schema_version": 1,
        "bundle_kind": "local_ocr_translation_review",
        "job_id": job_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "local_only": True, "ai_api_called": False, "upload_performed": False,
        "srt_filename": srt_name, "srt_sha256_local": _sha256(srt_bytes),
        "raw_timing_metadata": {"status": "unavailable",
                                 "reason": "assistant_reviewed_seed_has_no_frame_pts"},
        "cue_count": len(cue_records),
        "unique_crop_count": len(crop_sizes),
        "unique_crop_bytes": sum(crop_sizes.values()),
        "crop_limits": {"max_dimension_px": MAX_CROP_DIMENSION,
                         "max_unique_crops": 1000,
                         "max_total_encoded_bytes": MAX_PACK_CROP_BYTES,
                         "max_single_encoded_bytes": MAX_CROP_BYTES},
        "ocr_metadata": {"origin": "assistant_reviewed_training_seed",
                          "source_seed_sha256": seed_sha256,
                          "human_decisions_created": False},
        "seed_import": {"schema": PACK_SCHEMA, "version": 1,
                        "seed_path": "../../training-seed.jsonl",
                        "seed_sha256": seed_sha256,
                        "episode_key": rows[0]["episode_key"],
                        "video_identity": rows[0]["identity"],
                        "srt_basis": "synthetic_subset_from_seed_observed_text_and_times",
                        "original_full_run_srt_included": False,
                        "label_source": "assistant_visual_review",
                        "user_confirmation_status": "pending",
                        "rights_status": "not_verified",
                        "dataset_status": "candidate_needs_human_review",
                        "training_eligible": False,
                        "human_review_events_created": 0,
                        "note": "Suggestions only; no review event is created by import."},
        "cues": cue_records,
    }
    _write_new(pack / "manifest.json",
               (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    _write_new(pack / "README.txt", (
        "Imported assistant-reviewed OCR candidates. These are not accepted labels.\n"
        "Open in /ocr-inceleme and inspect each image before recording a decision.\n"
        "No decision event is created during import.\n"
    ).encode("utf-8"))
    return {"episode_key": rows[0]["episode_key"],
            "video_identity": rows[0]["identity"],
            "pack_path": pack.name, "candidate_count": len(rows),
            "crop_count": len(crop_sizes), "source_seed_sha256": seed_sha256}


def import_seed(seed_path: Path, output_path: Path | None = None) -> tuple[Path | None, dict]:
    seed_path = seed_path.expanduser()
    if any(part == ".." for part in seed_path.parts):
        raise ImportError("Seed yolunda .. bileşeni kabul edilmiyor")
    _check_no_reparse(seed_path)
    seed_path = seed_path.resolve(strict=True)
    if not seed_path.is_file() or seed_path.stat().st_size > MAX_SEED_BYTES:
        raise ImportError("Seed dosyası normal dosya olmalı ve 16 MiB'ı aşmamalı")
    seed_root = seed_path.parent
    _check_no_reparse(seed_path, seed_root)
    raw_seed = seed_path.read_bytes()
    seed_sha = _sha256(raw_seed)
    try:
        lines = raw_seed.decode("utf-8-sig", errors="strict").splitlines()
    except UnicodeError as exc:
        raise ImportError("Seed UTF-8 değil") from exc
    if not lines or len(lines) > MAX_SEED_ROWS:
        raise ImportError("Seed satır sayısı 1..500 olmalı")
    images: dict[str, tuple[Path, bytes]] = {}
    crop_cache: dict[str, tuple[bytes, int, int]] = {}
    rows, ids = [], set()
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise ImportError(f"{line_number}. satır boş; kısmi seed kabul edilmiyor")
        try:
            raw_row = json.loads(line, object_pairs_hook=_unique_pairs)
        except (json.JSONDecodeError, ImportError) as exc:
            raise ImportError(f"Seed satırı {line_number}: geçersiz JSON") from exc
        row = _validate_row(raw_row, seed_root, images, crop_cache)
        row_key = (row["episode_key"], row["identity"]["size"],
                   row["identity"]["sha1_first_1mb"], row["start_ms"], row["end_ms"])
        if row_key in ids:
            raise ImportError(f"Yinelenen video/episode/cue kimliği: satır {line_number}")
        ids.add(row_key)
        rows.append(row)
    evidence_total = sum(len(data) for _path, data in images.values())
    if evidence_total > MAX_EVIDENCE_TOTAL_BYTES:
        raise ImportError("Özgün evidence toplamı 256 MiB sınırını aşıyor")
    if sum(len(crop) for crop, _width, _height in crop_cache.values()) > MAX_IMPORT_CROP_BYTES:
        raise ImportError("Normalize edilmiş crop toplamı 256 MiB sınırını aşıyor")

    groups = defaultdict(list)
    identity_to_episode = {}
    for item in rows:
        identity_key = (item["identity"]["size"], item["identity"]["sha1_first_1mb"])
        previous_episode = identity_to_episode.setdefault(identity_key, item["episode_key"])
        if previous_episode != item["episode_key"]:
            raise ImportError("Aynı video kimliği birden fazla episode_key ile etiketlenmiş")
        key = (item["episode_key"], item["identity"]["size"],
               item["identity"]["sha1_first_1mb"])
        groups[key].append(item)
    skipped = [{"episode_key": row["episode_key"],
                "cue_start_ms": row["start_ms"],
                "image_sha256": row["image_sha256"],
                "reason": "no_subtitle_example_not_a_text_recognition_label"}
               for row in rows if row["action"] == "remove_cue"]
    eligible = [row for row in rows if row["action"] == "replace_text"]
    if not eligible or len(groups) > 100:
        raise ImportError("İnceleme adayı yok veya grup sayısı sınırı aşıldı")

    pack_groups = [(key, group) for key, group in sorted(groups.items())
                   if any(item["action"] == "replace_text" for item in group)]
    report = {
        "schema": "hardsub2srt.seed-import-report", "version": 1,
        "source_seed_sha256": seed_sha,
        "source_seed_rows": len(rows),
        "assistant_candidates": len(eligible),
        "human_decisions_created": 0,
        "exportable_labels_created_by_import": 0,
        "label_source": "assistant_visual_review",
        "user_confirmation_status": "pending",
        "rights_status": "not_verified",
        "dataset_status": "candidate_needs_human_review",
        "training_eligible": False,
        "review_packs_planned": len(pack_groups),
        "skipped_examples": skipped,
        "source_identity_metadata_limitation": (
            "Video fingerprint and episode key are preserved in each review-pack manifest, "
            "but Phase 2 export rows and metadata templates do not carry/infer them. "
            "Human source_id, episode_id, rights evidence, and rights review remain required."),
    }
    if output_path is None:
        return None, report
    parent, target = _local_output_root(output_path.expanduser(), seed_root)

    staging = Path(tempfile.mkdtemp(prefix=".ocr-seed-import-", dir=parent))
    try:
        source_copy = staging / "seed-source"
        source_copy.mkdir()
        _write_new(staging / "training-seed.jsonl", raw_seed)
        for rel, (source, data) in images.items():
            dest = source_copy.joinpath(*PurePosixPath(rel).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            _write_new(dest, data)
        pack_dir = staging / "review-packs"
        pack_dir.mkdir()
        pack_summaries = []
        pack_summaries = []
        for index, (key, group) in enumerate(pack_groups, 1):
            group_rows = [item for item in group if item["action"] == "replace_text"]
            episode, _size, video_sha = key
            name = f"{episode}-{video_sha[:8]}-{index:02d}.review-pack"
            pack_summaries.append(_build_pack(pack_dir / name, group_rows, seed_sha))
        report.update({"created_at": datetime.now(timezone.utc).isoformat(),
                       "review_packs_created": len(pack_summaries),
                       "packs": pack_summaries})
        _write_new(staging / "import-report.json",
                   (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
        _publish_noreplace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target, report


def list_seed_review_packs(app_local_root: Path) -> list[dict]:
    """List only hash-bound seed review packs inside the fixed local app root."""
    try:
        # Keep the caller's lexical AppData spelling for containment. On
        # Windows, LocalAppData package virtualization can make resolve()
        # return a different physical-looking prefix for child directories.
        root = Path(os.path.abspath(app_local_root))
        _check_no_reparse(root)
        if not root.is_dir():
            return []
    except (OSError, RuntimeError):
        return []
    results = []
    for import_root in sorted(root.glob("seed-review-*"), key=lambda path: path.name.lower()):
        try:
            _check_no_reparse(import_root, root)
            if (not _under(import_root, root) or not import_root.is_dir() or
                    not import_root.name.startswith("seed-review-")):
                continue
            report_path = import_root / "import-report.json"
            seed_copy = import_root / "training-seed.jsonl"
            _check_no_reparse(report_path, import_root)
            _check_no_reparse(seed_copy, import_root)
            if (report_path.stat().st_size > MAX_IMPORT_REPORT_BYTES or
                    seed_copy.stat().st_size > MAX_SEED_BYTES):
                continue
            report_raw = report_path.read_bytes()
            report = json.loads(report_raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
            if (type(report) is not dict or
                    report.get("schema") != "hardsub2srt.seed-import-report" or
                    type(report.get("version")) is not int or report["version"] != 1 or
                    report.get("human_decisions_created") != 0 or
                    report.get("exportable_labels_created_by_import") != 0 or
                    report.get("label_source") != "assistant_visual_review" or
                    report.get("user_confirmation_status") != "pending" or
                    report.get("rights_status") != "not_verified" or
                    report.get("dataset_status") != "candidate_needs_human_review" or
                    report.get("training_eligible") is not False or
                    not isinstance(report.get("source_seed_sha256"), str) or
                    not HEX64.fullmatch(report["source_seed_sha256"])):
                continue
            seed_bytes = seed_copy.read_bytes()
            seed_sha = _sha256(seed_bytes)
            if seed_sha != report["source_seed_sha256"]:
                continue
            pack_rows = report.get("packs")
            if (type(pack_rows) is not list or not pack_rows or len(pack_rows) > 100 or
                    report.get("review_packs_created") != len(pack_rows) or
                    report.get("review_packs_planned") != len(pack_rows)):
                continue
            report_sha = _sha256(report_raw)
            pack_results = []
            seen = set()
            valid_import = True
            import_packs_root = import_root / "review-packs"
            _check_no_reparse(import_packs_root, import_root)
            if not _under(import_packs_root, import_root) or not import_packs_root.is_dir():
                continue
            for row in pack_rows:
                if (type(row) is not dict or
                        set(row) != {"episode_key", "video_identity", "pack_path",
                                     "candidate_count", "crop_count", "source_seed_sha256"}):
                    valid_import = False
                    break
                relative = row.get("pack_path")
                if (type(relative) is not str or "\\" in relative or
                        PurePosixPath(relative).is_absolute() or
                        PurePosixPath(relative).name != relative or
                        not relative.endswith(".review-pack") or relative in seen):
                    valid_import = False
                    break
                seen.add(relative)
                identity = row.get("video_identity")
                if (type(identity) is not dict or set(identity) != {"size", "sha1_first_1mb"} or
                        type(identity.get("size")) is not int or identity["size"] <= 0 or
                        type(identity.get("sha1_first_1mb")) is not str or
                        not SHA1.fullmatch(identity["sha1_first_1mb"]) or
                        row.get("source_seed_sha256") != seed_sha or
                        type(row.get("candidate_count")) is not int or row["candidate_count"] < 1):
                    valid_import = False
                    break
                pack = import_packs_root / relative
                _check_no_reparse(pack, import_packs_root)
                if not _under(pack, import_packs_root) or not pack.is_dir():
                    valid_import = False
                    break
                import ocr_review
                info = ocr_review._validated_pack(pack)
                manifest = info["manifest"]
                imported = manifest.get("seed_import")
                if (not isinstance(imported, dict) or
                        imported.get("schema") != PACK_SCHEMA or
                        imported.get("version") != 1 or
                        imported.get("seed_sha256") != seed_sha or
                        imported.get("episode_key") != row["episode_key"] or
                        imported.get("video_identity") != identity or
                        imported.get("user_confirmation_status") != "pending" or
                        imported.get("rights_status") != "not_verified" or
                        imported.get("dataset_status") != "candidate_needs_human_review" or
                        imported.get("training_eligible") is not False or
                        imported.get("human_review_events_created") != 0 or
                        len(info["cues"]) != row["candidate_count"] or
                        manifest.get("cue_count") != row["candidate_count"]):
                    valid_import = False
                    break
                for cue in info["cues"]:
                    if (cue.get("episode_key") != row["episode_key"] or
                            cue.get("video_identity") != identity or
                            cue.get("seed_label_source") != "assistant_visual_review" or
                            cue.get("seed_user_confirmation_status") != "pending" or
                            cue.get("seed_rights_status") != "not_verified" or
                            cue.get("seed_dataset_status") != "candidate_needs_human_review" or
                            cue.get("seed_training_eligible") is not False or
                            not isinstance(cue.get("seed_candidate_text"), str) or
                            not cue["seed_candidate_text"].strip() or
                            not cue.get("crop_valid") or
                            ocr_review._verify_crop(info, cue) is None):
                        valid_import = False
                        break
                if not valid_import:
                    break
                active, warnings = ocr_review._active(info, force=True)
                if warnings:
                    valid_import = False
                    break
                pack_id = _sha256(
                    (import_root.name + "\0" + relative + "\0" + report_sha +
                     "\0" + info["manifest_sha256"]).encode("utf-8"))[:32]
                pack_results.append({
                    "pack_id": pack_id,
                    "episode_key": row["episode_key"],
                    "candidate_count": row["candidate_count"],
                    "reviewed_count": sum(1 for cue in info["cues"]
                                           if cue["cue_id"] in active),
                    "_pack_path": pack,
                    "_video_identity": identity,
                    "_cues": [{"cue_id": cue["cue_id"],
                               "start_ms": cue["start_ms"],
                               "end_ms": cue["end_ms"],
                               "text": cue["source_text_ocr"],
                               "crop_path": cue["crop_path"]}
                              for cue in info["cues"]],
                })
            if valid_import and pack_results:
                results.extend(pack_results)
        except (OSError, UnicodeError, json.JSONDecodeError, ImportError,
                RuntimeError, ValueError):
            # Corrupt, incomplete, or changed imports are not offered to UI.
            continue
    return results


def open_seed_review_pack(app_local_root: Path, pack_id: object) -> Path:
    """Resolve a UI token to a revalidated pack; never accept a caller path."""
    if type(pack_id) is not str or not re.fullmatch(r"[a-f0-9]{32}", pack_id):
        raise ImportError("Seed review pack kimliği geçersiz")
    match = next((row for row in list_seed_review_packs(app_local_root)
                  if row["pack_id"] == pack_id), None)
    if match is None:
        raise ImportError("Doğrulanmış seed review-pack bulunamadı veya değişti")
    return match["_pack_path"]


def find_seed_crop(app_local_root: Path, video_identity: object,
                   start_ms: object, end_ms: object, old_text: object,
                   tolerance_ms: int = 800) -> dict | None:
    """Find one exact seed crop by fingerprint, cue interval, and old text.

    Ambiguous or weak matches are deliberately unavailable. The returned path
    always belongs to a pack accepted by ``list_seed_review_packs``.
    """
    if (type(video_identity) is not dict or
            type(video_identity.get("size")) is not int or
            type(video_identity.get("sha1_first_1mb")) is not str or
            not SHA1.fullmatch(video_identity["sha1_first_1mb"]) or
            type(start_ms) is not int or type(end_ms) is not int or
            start_ms < 0 or end_ms <= start_ms or
            type(old_text) is not str or not old_text or
            type(tolerance_ms) is not int or tolerance_ms < 0):
        return None
    matches = []
    for pack in list_seed_review_packs(app_local_root):
        identity = pack["_video_identity"]
        if (identity.get("size") != video_identity["size"] or
                identity.get("sha1_first_1mb", "").lower() !=
                video_identity["sha1_first_1mb"].lower()):
            continue
        for cue in pack["_cues"]:
            if (cue["text"] == old_text and
                    abs(cue["start_ms"] - start_ms) <= tolerance_ms and
                    abs(cue["end_ms"] - end_ms) <= tolerance_ms):
                matches.append({"pack_path": pack["_pack_path"],
                                "crop_path": cue["crop_path"],
                                "cue_id": cue["cue_id"]})
    return matches[0] if len(matches) == 1 else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Assistant-reviewed OCR seed'i insan incelemesi bekleyen .review-pack'lere dönüştür")
    parser.add_argument("seed", type=Path, help="training-seed.jsonl")
    parser.add_argument("--out", type=Path,
                        help="Var olmayan, yerel ve senkronize edilmeyen çıktı klasörü")
    parser.add_argument("--check", action="store_true",
                        help="Seed, kanıt hash'leri ve review gruplarını doğrula; hiçbir çıktı yazma")
    args = parser.parse_args(argv)
    if args.check and args.out is not None:
        parser.error("--check ile --out birlikte kullanılamaz")
    if not args.check and args.out is None:
        parser.error("Dosya üretmek için --out gerekli; salt doğrulama için --check kullanın")
    try:
        target, report = import_seed(args.seed, None if args.check else args.out)
    except (ImportError, OSError, RuntimeError) as exc:
        print(f"DURDURULDU: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(f"Doğrulama başarılı: {report['source_seed_rows']} satır · "
              f"{report['assistant_candidates']} assistant adayı · "
              f"{report['review_packs_planned']} pack grubu · "
              f"{len(report['skipped_examples'])} skip")
        print(f"Seed SHA-256: {report['source_seed_sha256']}")
        print("İnsan kararı: 0 · export etiketi: 0 · veri dosyası yazılmadı.")
        return 0
    print(f"Import tamamlandı: {target}")
    print(f"{report['review_packs_created']} review-pack · {report['assistant_candidates']} aday · "
          f"{len(report['skipped_examples'])} no-subtitle örneği dışlandı")
    print("Etiket kabul edilmedi; her paketi /ocr-inceleme içinde insan olarak inceleyin.")
    print("Source/episode/lisans metadata'sını Phase 2 export sonrasında tamamlayın.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
