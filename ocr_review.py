"""Local human verification for review packs; never calls a remote service."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
import cv2
import numpy as np
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_SRT_BYTES = 16 * 1024 * 1024
MAX_CUES = 5_000
MAX_CROP_BYTES = 8 * 1024 * 1024
MAX_TOTAL_CROP_BYTES = 128 * 1024 * 1024
MAX_CROP_DIMENSION = 1280
MAX_EVENTS_BYTES = 64 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
_LOCK = threading.RLock()
_PACKS: dict[str, Path] = {}


class ReviewError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _manifest_bytes(pack: Path) -> bytes:
    path = (pack / "manifest.json").resolve(strict=True)
    if not _contained(path, pack) or not path.is_file():
        raise ReviewError("manifest.json paketin içinde bulunamadı", 400)
    data = _read_bounded(path, MAX_MANIFEST_BYTES, "Manifest")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ReviewError("Manifest izin verilen boyutu aşıyor", 413)
    return data


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError:
        raise ReviewError(f"{label} okunamadı", 422)
    if len(data) > limit:
        raise ReviewError(f"{label} izin verilen boyutu aşıyor", 413)
    return data


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read SOF dimensions before decode so decompression is strictly bounded."""
    if len(data) < 4 or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        return None
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
           0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    pos = 2
    while pos < len(data) - 2:
        if data[pos] != 0xFF:
            return None
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            return None
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue
        if pos + 2 > len(data):
            return None
        length = int.from_bytes(data[pos:pos + 2], "big")
        if length < 2 or pos + length > len(data):
            return None
        if marker in sof:
            if length < 8:
                return None
            height = int.from_bytes(data[pos + 3:pos + 5], "big")
            width = int.from_bytes(data[pos + 5:pos + 7], "big")
            return width, height
        if marker == 0xDA:
            return None
        pos += length
    return None


def _validate_crop_bytes(data: bytes, cue: dict) -> bool:
    if not data or len(data) > MAX_CROP_BYTES:
        return False
    dimensions = _jpeg_dimensions(data)
    if not dimensions:
        return False
    width, height = dimensions
    if (width < 1 or height < 1 or width > MAX_CROP_DIMENSION or
            height > MAX_CROP_DIMENSION or width * height > MAX_CROP_DIMENSION ** 2):
        return False
    if (type(cue.get("crop_width")) is not int or type(cue.get("crop_height")) is not int or
            cue.get("crop_width") != width or cue.get("crop_height") != height):
        return False
    try:
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return False
    return (image is not None and image.ndim == 3 and image.shape[1] == width and
            image.shape[0] == height and image.shape[2] == 3)


def _srt_time_ms(value: str) -> int | None:
    m = re.fullmatch(r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})", value or "")
    if not m or int(m[2]) > 59 or int(m[3]) > 59:
        return None
    return ((int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])) * 1000 + int(m[4]))


def _srt_cues_bytes(data: bytes) -> list[dict]:
    raw = data.decode("utf-8-sig", errors="strict")
    blocks = []
    for block in re.split(r"\n[ \t]*\n+", raw.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [line for line in block.split("\n") if line.strip()]
        at = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if at is None:
            continue
        left, _, right = lines[at].partition("-->")
        left, right = left.strip(), right.strip().split()[0] if right.strip() else ""
        start, end = _srt_time_ms(left), _srt_time_ms(right)
        text = "\n".join(lines[at + 1:]).strip()
        if start is None or end is None or not text:
            raise ReviewError("SRT içinde biçimi bozuk bir altyazı bloğu var", 422)
        blocks.append({"start_ms": start, "end_ms": end, "text": text})
    return blocks


def _validated_pack(pack: Path) -> dict:
    try:
        pack = pack.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ReviewError("İnceleme paketi bulunamadı", 404)
    if not pack.is_dir() or not pack.name.endswith(".review-pack"):
        raise ReviewError("Seçilen klasör .review-pack değil", 400)
    mbytes = _manifest_bytes(pack)
    try:
        manifest = json.loads(mbytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ReviewError("Manifest geçerli UTF-8 JSON değil", 422)
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or
            manifest.get("schema_version") != 1):
        raise ReviewError("Desteklenmeyen manifest biçimi", 422)
    job_id = manifest.get("job_id")
    cues = manifest.get("cues")
    srt_name = manifest.get("srt_filename")
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ReviewError("Manifest job_id geçersiz", 422)
    if not isinstance(cues, list) or len(cues) > MAX_CUES:
        raise ReviewError("Manifest cues alanı geçersiz", 422)
    if not isinstance(srt_name, str) or Path(srt_name).name != srt_name or not srt_name.lower().endswith(".srt"):
        raise ReviewError("Manifest SRT adı geçersiz", 422)
    srt_path = (pack / srt_name).resolve(strict=True)
    if not _contained(srt_path, pack) or not srt_path.is_file():
        raise ReviewError("SRT paketin dışına çıkıyor veya bulunamıyor", 422)
    srt_bytes = _read_bounded(srt_path, MAX_SRT_BYTES, "SRT")
    srt_digest = _sha256(srt_bytes)
    if srt_digest != manifest.get("srt_sha256_local"):
        raise ReviewError("SRT özeti manifest ile eşleşmiyor; kayıt devre dışı", 409)
    try:
        parsed = _srt_cues_bytes(srt_bytes)
    except UnicodeError:
        raise ReviewError("SRT UTF-8 olarak okunamıyor", 422)
    if (len(parsed) != len(cues) or type(manifest.get("cue_count")) is not int or
            manifest.get("cue_count") != len(cues)):
        raise ReviewError("SRT ile manifest altyazı sayısı eşleşmiyor", 409)

    seen = set()
    normalized = []
    crop_path_cache = {}
    total_crop_bytes = 0
    for index, cue in enumerate(cues):
        if not isinstance(cue, dict):
            raise ReviewError("Manifestte bozuk altyazı kaydı var", 422)
        cue_id = cue.get("cue_id")
        if (not isinstance(cue_id, str) or cue_id in seen or
                not re.fullmatch(re.escape(job_id) + r"-cue-\d{5,}", cue_id)):
            raise ReviewError("Altyazı kimliği geçersiz veya yineleniyor", 422)
        seen.add(cue_id)
        if type(cue.get("index")) is not int or cue.get("index") != index:
            raise ReviewError("Altyazı sırası manifest ile eşleşmiyor", 409)
        text = cue.get("source_text_ocr")
        start, end = cue.get("start_ms"), cue.get("end_ms")
        if (not isinstance(text, str) or len(text) > MAX_TEXT_CHARS or
                type(start) is not int or type(end) is not int or start < 0 or end <= start):
            raise ReviewError("Altyazı metni veya zamanı geçersiz", 422)
        if (parsed[index]["start_ms"] != start or parsed[index]["end_ms"] != end or
                parsed[index]["text"] != text):
            raise ReviewError("SRT ile manifest altyazı içeriği eşleşmiyor", 409)
        if cue.get("mapping_status") != "exact_original_ocr_segment_needs_visual_confirmation":
            # Only a defensible, one-to-one original OCR interval can become
            # a source-text label. Merged/ambiguous cues remain view-only.
            cue_mapping_ok = False
        else:
            cue_mapping_ok = True
        crop_rel, crop_hash = cue.get("crop_path"), cue.get("crop_sha256_local")
        crop_ok = cue.get("crop_status") == "available_needs_visual_confirmation"
        crop_path = None
        if crop_ok:
            if (not isinstance(crop_rel, str) or not isinstance(crop_hash, str) or
                    not re.fullmatch(r"[a-f0-9]{64}", crop_hash)):
                crop_ok = False
            else:
                rel = PurePosixPath(crop_rel)
                if rel.is_absolute() or len(rel.parts) != 2 or rel.parts[0] != "crops" or ".." in rel.parts:
                    crop_ok = False
                else:
                    try:
                        crop_path = (pack / Path(*rel.parts)).resolve(strict=True)
                        if (not _contained(crop_path, pack) or not crop_path.is_file() or
                                crop_path.suffix.lower() != ".jpg"):
                            crop_ok = False
                        else:
                            cache_key = (str(crop_path), crop_hash)
                            if cache_key in crop_path_cache:
                                crop_ok = crop_path_cache[cache_key]
                            else:
                                size = crop_path.stat().st_size
                                if (size < 1 or size > MAX_CROP_BYTES or
                                        total_crop_bytes + size > MAX_TOTAL_CROP_BYTES):
                                    crop_ok = False
                                else:
                                    data = _read_bounded(crop_path, MAX_CROP_BYTES, "Altyazı kırpımı")
                                    crop_ok = (_sha256(data) == crop_hash and
                                               _validate_crop_bytes(data, cue))
                                    if crop_ok:
                                        total_crop_bytes += size
                                crop_path_cache[cache_key] = crop_ok
                    except (OSError, RuntimeError, ReviewError):
                        crop_ok = False
        normalized.append({**cue, "crop_valid": bool(crop_ok and cue_mapping_ok),
                           "crop_abs": crop_path if crop_ok else None})
    return {"pack": pack, "manifest": manifest, "manifest_sha256": _sha256(mbytes),
            "srt_sha256": srt_digest, "cues": normalized,
            "events_path": pack / "review-events.jsonl"}


def open_pack(path: str) -> tuple[str, dict]:
    if not isinstance(path, str) or len(path) > 4096:
        raise ReviewError("Paket yolu geçersiz", 400)
    info = _validated_pack(Path(path).expanduser())
    review_id = uuid.uuid4().hex
    _PACKS[review_id] = info["pack"]
    return review_id, public_pack(info)


def _get_pack(review_id: str) -> dict:
    pack = _PACKS.get(review_id)
    if not pack:
        raise ReviewError("İnceleme oturumu bulunamadı; paketi yeniden açın", 404)
    return _validated_pack(pack)


def _events(info: dict) -> tuple[list[dict], list[str]]:
    path = info["events_path"]
    if not path.exists():
        return [], []
    if path.is_symlink() or not _contained(path.resolve(), info["pack"]):
        raise ReviewError("İnceleme günlüğü paket dışına yönleniyor", 403)
    with _LOCK:
        data = _read_bounded(path, MAX_EVENTS_BYTES, "İnceleme günlüğü")
    events, warnings = [], []
    for number, line in enumerate(data.splitlines(), 1):
        try:
            event = json.loads(line.decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError
            events.append(event)
        except (UnicodeError, json.JSONDecodeError, ValueError):
            warnings.append(f"{number}. satır bozuk; dışa aktarmada yok sayıldı")
    return events, warnings


def _event_schema(event: dict) -> bool:
    common = {"schema_version", "event_type", "event_id", "job_id", "cue_id",
              "manifest_sha256", "srt_sha256", "crop_sha256_local", "created_at",
              "reviewer"}
    if (type(event.get("schema_version")) is not int or event["schema_version"] != 1 or
            not isinstance(event.get("event_id"), str) or
            not re.fullmatch(r"[a-f0-9]{32}", event["event_id"]) or
            not isinstance(event.get("job_id"), str) or
            not re.fullmatch(r"[a-f0-9]{32}", event["job_id"]) or
            not isinstance(event.get("cue_id"), str) or
            not isinstance(event.get("reviewer"), str) or event["reviewer"] != "local_user" or
            any(not isinstance(event.get(k), str) or not re.fullmatch(r"[a-f0-9]{64}", event[k])
                for k in ("manifest_sha256", "srt_sha256", "crop_sha256_local"))):
        return False
    created = event.get("created_at")
    if not isinstance(created, str):
        return False
    try:
        timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return False
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return False
    if event.get("event_type") == "review":
        if set(event) != common | {"status", "verified_source_text"}:
            return False
        status, text = event.get("status"), event.get("verified_source_text")
        return (status in ("accepted", "corrected", "uncertain") and
                isinstance(text, str) and len(text) <= MAX_TEXT_CHARS and
                (bool(text.strip()) if status == "corrected" else
                 text == "" if status == "uncertain" else True))
    if event.get("event_type") == "undo":
        return (set(event) == common | {"undo_event_id"} and
                isinstance(event.get("undo_event_id"), str) and
                bool(re.fullmatch(r"[a-f0-9]{32}", event["undo_event_id"])))
    return False


def _active(info: dict) -> tuple[dict[str, dict], list[str]]:
    events, warnings = _events(info)
    cue_by_id = {c["cue_id"]: c for c in info["cues"]}
    active: dict[str, dict] = {}
    ids = set()
    review_ids = set()
    for event in events:
        event_id = event.get("event_id")
        if not _event_schema(event):
            warnings.append("tam şema doğrulamasını geçemeyen olay dışlandı")
            continue
        if event_id in ids:
            warnings.append("yinelenen event_id dışlandı")
            continue
        cue = cue_by_id.get(event.get("cue_id"))
        if (not cue or event.get("job_id") != info["manifest"]["job_id"] or
                event.get("manifest_sha256") != info["manifest_sha256"] or
                event.get("srt_sha256") != info["srt_sha256"]):
            warnings.append("paketle eşleşmeyen olay dışlandı")
            continue
        if event.get("event_type") == "undo":
            target = event.get("undo_event_id")
            if (target in review_ids and
                    active.get(cue["cue_id"], {}).get("event_id") == target and
                    event.get("crop_sha256_local") == cue.get("crop_sha256_local")):
                active.pop(cue["cue_id"], None)
                ids.add(event_id)
            else:
                warnings.append("geçersiz geri alma olayı dışlandı")
            continue
        status = event.get("status")
        verified = event.get("verified_source_text")
        valid = (event.get("crop_sha256_local") == cue.get("crop_sha256_local") and
                 cue["crop_valid"])
        if status == "accepted":
            valid = valid and verified == cue["source_text_ocr"]
        elif status == "corrected":
            valid = valid and bool(verified.strip())
        elif status == "uncertain":
            valid = valid and verified == ""
        if valid:
            active[cue["cue_id"]] = event
            ids.add(event_id)
            review_ids.add(event_id)
        else:
            warnings.append("şeması veya görseli geçersiz olay dışlandı")
    return active, warnings


def public_pack(info: dict) -> dict:
    active, warnings = _active(info)
    cues = []
    for cue in info["cues"]:
        event = active.get(cue["cue_id"])
        cues.append({k: cue.get(k) for k in (
            "cue_id", "index", "start_ms", "end_ms", "source_text_ocr",
            "ocr_confidence", "representative_frame_index", "representative_frame_ms",
            "mapping_status", "crop_path", "crop_sha256_local", "crop_valid")}
            | {"active_event": event})
    return {"job_id": info["manifest"]["job_id"], "cues": cues,
            "warnings": warnings, "review_log": "review-events.jsonl"}


def pack_for(review_id: str) -> dict:
    return _get_pack(review_id)


def pack_status(review_id: str) -> dict:
    result = public_pack(_get_pack(review_id))
    result["review_id"] = review_id
    return result


def crop_for(review_id: str, cue_id: str) -> tuple[Path, str]:
    info = _get_pack(review_id)
    cue = next((c for c in info["cues"] if c["cue_id"] == cue_id), None)
    if not cue or not cue["crop_valid"] or cue["crop_abs"] is None:
        raise ReviewError("Görsel eksik, değiştirilmiş veya altyazıyla eşleşmiyor", 409)
    return cue["crop_abs"], "image/jpeg"


def append_review(review_id: str, cue_id: str, status: str, correction: str = "") -> dict:
    with _LOCK:
        info = _get_pack(review_id)
        cue = next((c for c in info["cues"] if c["cue_id"] == cue_id), None)
        if not cue or not cue["crop_valid"]:
            raise ReviewError("Görsel eksik, değiştirilmiş veya eşleşmiyor; kayıt yapılmadı", 409)
        if status not in ("accepted", "corrected", "uncertain"):
            raise ReviewError("Durum accepted/corrected/uncertain olmalı", 400)
        if status == "corrected":
            if not isinstance(correction, str) or not correction.strip() or len(correction) > MAX_TEXT_CHARS:
                raise ReviewError("Düzeltilmiş metin boş olamaz ve 20.000 karakteri aşamaz", 400)
            verified = correction.strip()
        elif status == "accepted":
            verified = cue["source_text_ocr"]
        else:
            verified = ""
        record = {"schema_version": 1, "event_type": "review", "event_id": uuid.uuid4().hex,
                  "job_id": info["manifest"]["job_id"], "cue_id": cue_id,
                  "manifest_sha256": info["manifest_sha256"], "srt_sha256": info["srt_sha256"],
                  "crop_sha256_local": cue["crop_sha256_local"], "status": status,
                  "verified_source_text": verified, "reviewer": "local_user",
                  "created_at": datetime.now(timezone.utc).isoformat()}
        _append_locked(info, record)
        return record


def undo_review(review_id: str, cue_id: str) -> dict:
    with _LOCK:
        info = _get_pack(review_id)
        cue = next((c for c in info["cues"] if c["cue_id"] == cue_id), None)
        if not cue:
            raise ReviewError("Altyazı kimliği bulunamadı", 404)
        active, _ = _active(info)
        current = active.get(cue_id)
        if not current:
            raise ReviewError("Geri alınacak etkin bir karar yok", 409)
        record = {"schema_version": 1, "event_type": "undo", "event_id": uuid.uuid4().hex,
                  "undo_event_id": current["event_id"], "job_id": info["manifest"]["job_id"],
                  "cue_id": cue_id, "manifest_sha256": info["manifest_sha256"],
                  "srt_sha256": info["srt_sha256"], "crop_sha256_local": cue["crop_sha256_local"],
                  "reviewer": "local_user", "created_at": datetime.now(timezone.utc).isoformat()}
        _append_locked(info, record)
        return record


def _append(info: dict, record: dict) -> None:
    with _LOCK:
        _append_locked(info, record)


def _append_locked(info: dict, record: dict) -> None:
    path = info["events_path"]
    if path.is_symlink():
        raise ReviewError("İnceleme günlüğü sembolik bağlantı olamaz", 403)
    line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if path.exists() and path.stat().st_size + len(line) > MAX_EVENTS_BYTES:
        raise ReviewError("İnceleme günlüğü boyut sınırına ulaştı", 413)
    if path.exists() and path.stat().st_size:
        with path.open("rb") as existing:
            existing.seek(-1, os.SEEK_END)
            if existing.read(1) != b"\n":
                raise ReviewError("İnceleme günlüğünün son kaydı yarım; geçmiş korunarak yeni yazım durduruldu", 409)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "ab", closefd=True) as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        raise


def export_dataset(review_id: str) -> tuple[Path, int]:
    info = _get_pack(review_id)
    active, _ = _active(info)
    eligible = []
    cues = {c["cue_id"]: c for c in info["cues"]}
    for cue_id, event in active.items():
        cue = cues.get(cue_id)
        if (not cue or not cue["crop_valid"] or event.get("status") not in ("accepted", "corrected") or
                event.get("crop_sha256_local") != cue.get("crop_sha256_local")):
            continue
        eligible.append((cue, event))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = info["pack"] / f"verified-ocr-dataset-{stamp}-{uuid.uuid4().hex[:8]}"
    staging = Path(tempfile.mkdtemp(prefix=".verified-ocr-", dir=info["pack"]))
    try:
        (staging / "crops").mkdir()
        rows, copied = [], {}
        for cue, event in eligible:
            digest = cue["crop_sha256_local"]
            if digest not in copied:
                name = f"{digest}.jpg"
                source = cue["crop_abs"]
                data = _read_bounded(source, MAX_CROP_BYTES, "Altyazı kırpımı")
                if (_sha256(data) != digest or not _validate_crop_bytes(data, cue)):
                    raise ReviewError("Görsel dışa aktarma sırasında değişti", 409)
                (staging / "crops" / name).write_bytes(data)
                copied[digest] = f"crops/{name}"
            rows.append({"schema_version": 1, "cue_id": cue["cue_id"],
                         "source_text_verified": event["verified_source_text"],
                         "crop_path": copied[digest], "crop_sha256_local": digest,
                         "start_ms": cue["start_ms"], "end_ms": cue["end_ms"],
                         "ocr_confidence": cue.get("ocr_confidence"),
                         "review_event_id": event["event_id"],
                         "reviewed_at": event["created_at"]})
        (staging / "manifest.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        (staging / "README.txt").write_text(
            "Human-verified OCR crop/text pairs. No SRT or translation data included.\n",
            encoding="utf-8")
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target, len(eligible)


def image_bytes_path(review_id: str, cue_id: str) -> tuple[Path, str]:
    return crop_for(review_id, cue_id)


REVIEW_HTML = r'''<!doctype html>
<html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Yerel OCR Doğrulama</title>
<style>
body{margin:0;background:#11151e;color:#e8edf8;font:15px system-ui,sans-serif}header{position:sticky;top:0;background:#191f2b;padding:16px 22px;border-bottom:1px solid #30394b;z-index:2}main{max-width:1120px;margin:22px auto;padding:0 16px}.row{display:flex;gap:8px;flex-wrap:wrap}input,textarea,button{background:#0f131b;color:#edf2ff;border:1px solid #39445a;border-radius:6px;padding:10px;font:inherit}input{flex:1;min-width:300px}button{cursor:pointer;background:#29364e;font-weight:650}button.primary{background:#3979ef;border-color:#3979ef}button.warn{background:#6b3841}.help,.meta{color:#a7b5ce;font-size:13px}.notice{padding:10px 12px;margin:12px 0;background:#202838;border-left:3px solid #e2ae59;border-radius:4px}.cue{display:grid;grid-template-columns:minmax(260px,42%) 1fr;gap:16px;border:1px solid #30394b;border-radius:9px;padding:14px;margin:12px 0;background:#1a202c}.cue img{display:block;max-width:100%;max-height:240px;object-fit:contain;background:#090c12;border-radius:4px}.cue textarea{box-sizing:border-box;width:100%;min-height:75px;resize:vertical}.controls{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}.status{color:#82d9a0}.bad{color:#ffb978}.hidden{display:none}@media(max-width:700px){.cue{grid-template-columns:1fr}}
</style><header><b>Yerel OCR Doğrulama</b><div class="help">Kırpımı okuyup OCR metnini kabul edin, düzeltin veya belirsiz bırakın. Kayıtlar paketin içine eklenir; SRT ve manifest değiştirilmez.</div></header>
<main><section><div class="row"><input id="path" placeholder=".review-pack klasörünün yolu"><button id="browse">Klasör seç</button><button class="primary" id="open">Paketi aç</button></div><div class="help">Bu sayfa yerel çalışır. GPT/DeepSeek çağrısı veya veri gönderimi yapmaz.</div></section>
<section id="workspace" class="hidden"><div id="summary" class="notice"></div><div class="row"><button class="primary" id="export">Doğrulanmış OCR veri kümesini dışa aktar</button><span id="export-result" class="help"></span></div><div id="warnings"></div><div id="cues"></div></section></main>
<script>
let reviewId=null, pack=null, pickerId=null;
const $=id=>document.getElementById(id);
async function api(url,opts={}){const r=await fetch(url,opts);let j;try{j=await r.json()}catch{throw Error("Sunucu yanıtı okunamadı")}if(!r.ok)throw Error(j.hata||"İstek başarısız");return j}
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function openPack(path){const j=await api('/api/ocr-inceleme/ac',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});reviewId=j.review_id;pack=j;$('workspace').classList.remove('hidden');draw()}
$('open').onclick=async()=>{try{await openPack($('path').value.trim())}catch(e){alert(e.message)}};
$('browse').onclick=async()=>{try{const j=await api('/api/secim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tur:'inceleme'})});pickerId=j.job_id;while(true){await new Promise(r=>setTimeout(r,400));const s=await api('/api/secim/'+encodeURIComponent(pickerId));if(s.status==='done'){const p=s.result?.dizin||'';if(p){$('path').value=p;await openPack(p)}break}if(['error','timeout','cancelled'].includes(s.status))throw Error(s.hata||'Klasör seçimi tamamlanamadı')}}catch(e){alert(e.message)}};
window.imageReady=(img,ok)=>{const card=img.closest('.cue');if(!card)return;card.querySelectorAll('.decision').forEach(b=>b.disabled=!ok);const textarea=card.querySelector('textarea');if(textarea)textarea.disabled=!ok;if(!ok){const n=document.createElement('div');n.className='notice bad';n.textContent='Görsel yüklenemedi; bu altyazı için karar verilemez.';img.replaceWith(n)}};
function draw(){
 const decisions=pack.cues.filter(c=>c.active_event).length;$('summary').textContent=`${pack.cues.length} altyazı · ${decisions} kayıtlı karar · günlük: ${pack.review_log}`;
 $('warnings').innerHTML=pack.warnings.length?'<div class="notice">'+pack.warnings.map(esc).join('<br>')+'</div>':'';
 $('cues').innerHTML=pack.cues.map((c,i)=>{const ev=c.active_event;const val=ev?.verified_source_text??c.source_text_ocr;const state=ev?ev.status:'İncelenmedi';const img=c.crop_valid?`<img loading="lazy" onload="imageReady(this,true)" onerror="imageReady(this,false)" alt="Altyazı kırpımı" src="/api/ocr-inceleme/${encodeURIComponent(reviewId)}/crop/${encodeURIComponent(c.cue_id)}">`:'<div class="notice">Bu blokta güvenilir ve doğrulanabilir kırpım yok; OCR veri kümesine alınamaz.</div>';return `<article class="cue"><div>${img}</div><div><b>#${i+1} · ${esc(c.start_ms)}–${esc(c.end_ms)} ms</b> <span class="meta">güven: ${esc(c.ocr_confidence??'bilinmiyor')} · ${esc(state)}</span><p class="meta">OCR metni</p><textarea id="txt-${i}" ${c.crop_valid?'':'disabled'}>${esc(val)}</textarea><div class="controls"><button class="primary decision" onclick="decide(${i},'accepted')" disabled>Görüntü doğru</button><button class="decision" onclick="decide(${i},'corrected')" disabled>Düzeltmeyi kaydet</button><button class="warn decision" onclick="decide(${i},'uncertain')" disabled>Belirsiz</button>${ev?`<button onclick="undo(${i})">Son kararı geri al</button>`:''}</div><div class="meta">${esc(c.mapping_status||'')} ${c.crop_valid?'':'· görsel doğrulaması kullanılamıyor'}</div></div></article>`}).join('');
}
window.decide=async(i,status)=>{const c=pack.cues[i];try{const correction=$('txt-'+i).value;await api('/api/ocr-inceleme/karar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({review_id:reviewId,cue_id:c.cue_id,status,correction})});await refresh()}catch(e){alert(e.message)}};
window.undo=async i=>{const c=pack.cues[i];try{await api('/api/ocr-inceleme/geri-al',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({review_id:reviewId,cue_id:c.cue_id})});await refresh()}catch(e){alert(e.message)}};
async function refresh(){pack=await api('/api/ocr-inceleme/durum?review_id='+encodeURIComponent(reviewId));draw()}
$('export').onclick=async()=>{try{const j=await api('/api/ocr-inceleme/disari-aktar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({review_id:reviewId})});$('export-result').textContent=`${j.count} görsel/metin çifti kaydedildi: ${j.path}`}catch(e){alert(e.message)}};
</script></html>'''

