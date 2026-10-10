"""Build a local, crop-based review bundle from final OCR cues.

This module does not call an AI service or upload files. Cues without a
defensible representative crop remain in the manifest as unavailable.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2


MAX_CROP_DIMENSION = 1280
JPEG_QUALITY = 86
MAX_UNIQUE_CROPS = 1000
MAX_TOTAL_CROP_BYTES = 128 * 1024 * 1024
MAX_SINGLE_CROP_BYTES = 8 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _encode_crop(image):
    if image is None or getattr(image, "ndim", 0) != 3:
        raise ValueError("not_a_color_roi")
    original_height, original_width = image.shape[:2]
    if original_width < 1 or original_height < 1:
        raise ValueError("empty_roi")
    largest_dimension = max(original_width, original_height)
    if largest_dimension > MAX_CROP_DIMENSION:
        scale = MAX_CROP_DIMENSION / largest_dimension
        image = cv2.resize(
            image,
            (max(1, round(original_width * scale)),
             max(1, round(original_height * scale))),
            interpolation=cv2.INTER_AREA)
    height, width = image.shape[:2]
    ok, encoded = cv2.imencode(
        ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError("jpeg_encode_failed")
    return encoded.tobytes(), width, height, original_width, original_height


def build_review_pack(srt_path, cues, metadata, crop_provider):
    """Create `<srt-stem>.review-pack/` without overwriting an existing pack.

    `cues` are final main-SRT blocks in file order. Each cue carries its OCR
    segment frame and proven band y/height. `crop_provider` receives those
    values and must return only that subtitle ROI as BGR. A failed or unknown
    region is recorded as unavailable; it is never fabricated.
    """
    srt_path = Path(srt_path)
    target = srt_path.with_name(srt_path.stem + ".review-pack")
    if target.exists():
        raise FileExistsError(f"review pack already exists: {target.name}")
    if not srt_path.is_file():
        raise FileNotFoundError("final SRT is missing")

    srt_bytes = srt_path.read_bytes()
    srt_digest = _sha256(srt_bytes)
    job_id = uuid.uuid4().hex
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{target.name}.{job_id[:8]}-", dir=str(target.parent)))
    try:
        (temporary / "crops").mkdir()
        shutil.copy2(srt_path, temporary / srt_path.name)
        cues_out = []
        crop_by_digest = {}
        crop_count = 0
        crop_bytes = 0
        crop_by_source = {}
        failed_sources = set()

        for position, cue in enumerate(cues, 1):
            cue_id = f"{job_id}-cue-{position:05d}"
            record = {
                "cue_id": cue_id,
                "index": position - 1,
                "start_ms": int(cue["start_ms"]),
                "end_ms": int(cue["end_ms"]),
                "source_text_ocr": str(cue["text"]),
                "text_stage": "final_srt_after_postfix",
                "ocr_confidence": cue.get("confidence"),
                "representative_frame_index": (int(cue["frame_index"])
                                                if cue.get("frame_index") is not None
                                                else None),
                "representative_frame_ms": (int(cue["frame_ms"])
                                             if cue.get("frame_ms") is not None
                                             else None),
                "mapping_status": cue.get(
                    "mapping_status", "exact_original_ocr_segment_needs_visual_confirmation"),
                "source_region": cue.get("source_region", "main_band"),
                "source_band_y": cue.get("band_y"),
                "source_band_height": cue.get("band_height"),
                "crop_status": "unavailable",
                "crop_path": None,
                "crop_sha256_local": None,
                "duplicate_of_cue_id": None,
            }
            try:
                if record["representative_frame_index"] is None:
                    raise ValueError("source_frame_not_preserved")
                if record["source_region"] not in ("main_band", "upper_band"):
                    raise ValueError("unknown_source_region")
                if record["source_band_y"] is None or record["source_band_height"] is None:
                    raise ValueError("unknown_source_region")
                frame_index = record["representative_frame_index"]
                source_key = (frame_index, record["source_band_y"],
                              record["source_band_height"])
                if source_key in crop_by_source:
                    cached = crop_by_source[source_key]
                    record.update({key: value for key, value in cached.items()
                                   if key != "dedup_cue_id"})
                    record["duplicate_of_cue_id"] = cached["dedup_cue_id"]
                elif source_key in failed_sources:
                    raise ValueError("representative_roi_unavailable")
                else:
                    # Known same-frame/band duplicates can reuse their stored
                    # crop. For a new source image, stop before opening/reading
                    # the video once either persisted-output budget is spent.
                    if crop_count >= MAX_UNIQUE_CROPS:
                        raise ValueError("crop_count_limit_reached")
                    if crop_bytes + MAX_SINGLE_CROP_BYTES > MAX_TOTAL_CROP_BYTES:
                        raise ValueError("crop_byte_budget_reserve_exhausted")
                    try:
                        image = crop_provider(
                            frame_index, record["source_band_y"],
                            record["source_band_height"])
                        encoded, crop_width, crop_height, roi_width, roi_height = \
                            _encode_crop(image)
                    except Exception:
                        failed_sources.add(source_key)
                        raise
                    if len(encoded) > MAX_SINGLE_CROP_BYTES:
                        failed_sources.add(source_key)
                        raise ValueError("single_crop_byte_limit_reached")
                    digest = _sha256(encoded)
                    record["crop_width"] = crop_width
                    record["crop_height"] = crop_height
                    record["roi_width"] = roi_width
                    record["roi_height"] = roi_height
                    record["crop_sha256_local"] = digest
                    if digest in crop_by_digest:
                        record["crop_path"] = crop_by_digest[digest]["path"]
                        dedup_cue_id = crop_by_digest[digest]["cue_id"]
                        record["duplicate_of_cue_id"] = dedup_cue_id
                    else:
                        relative = f"crops/{cue_id}.jpg"
                        (temporary / relative).write_bytes(encoded)
                        crop_by_digest[digest] = {"path": relative, "cue_id": cue_id}
                        record["crop_path"] = relative
                        dedup_cue_id = cue_id
                        crop_count += 1
                        crop_bytes += len(encoded)
                    crop_by_source[source_key] = {
                        "crop_path": record["crop_path"],
                        "crop_sha256_local": record["crop_sha256_local"],
                        "crop_width": record["crop_width"],
                        "crop_height": record["crop_height"],
                        "roi_width": record["roi_width"],
                        "roi_height": record["roi_height"],
                        "dedup_cue_id": dedup_cue_id,
                        "crop_status": "available_needs_visual_confirmation",
                    }
                record["crop_status"] = "available_needs_visual_confirmation"
            except Exception as exc:
                # Keep user paths and FFmpeg diagnostics out of the review bundle.
                record["crop_path"] = None
                allowed_errors = {
                    "crop_count_limit_reached",
                    "crop_byte_budget_reserve_exhausted",
                    "single_crop_byte_limit_reached",
                }
                record["crop_error"] = (str(exc) if str(exc) in allowed_errors
                                         else "representative_roi_unavailable")
                if record["crop_error"] in allowed_errors:
                    record["crop_sha256_local"] = None
                    for key in ("crop_width", "crop_height", "roi_width", "roi_height"):
                        record.pop(key, None)
            cues_out.append(record)

        manifest = {
            "schema_version": 1,
            "bundle_kind": "local_ocr_translation_review",
            "job_id": job_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "local_only": True,
            "ai_api_called": False,
            "upload_performed": False,
            "srt_filename": srt_path.name,
            "srt_sha256_local": srt_digest,
            "cue_count": len(cues_out),
            "unique_crop_count": crop_count,
            "unique_crop_bytes": crop_bytes,
            "crop_limits": {
                "max_dimension_px": MAX_CROP_DIMENSION,
                "max_unique_crops": MAX_UNIQUE_CROPS,
                "max_total_encoded_bytes": MAX_TOTAL_CROP_BYTES,
                "max_single_encoded_bytes": MAX_SINGLE_CROP_BYTES,
            },
            "ocr_metadata": metadata,
            "cues": cues_out,
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        (temporary / "README.txt").write_text(
            "Local review bundle. No AI API call or upload was made.\n"
            "Contains the final SRT, one deduplicated subtitle-band crop per "
            "available visual cue, and manifest.json.\n"
            "Crop/text pairs need human visual confirmation before they are "
            "used as OCR labels. Translation memory is not included.\n",
            encoding="utf-8")
        temporary.rename(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target, manifest
