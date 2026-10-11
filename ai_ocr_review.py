"""Manual, local AI review of OCR text against subtitle crops.

This is intentionally separate from translation_review: AI output is an
append-only proposal and can never create a human OCR event or dataset label.
No provider/network calls are made here.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import stat
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import ocr_review

MAX_PACKAGE_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CUES = 100
MAX_TEXT = 20_000
MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_LOG_RECORDS = 20_000
_ID = re.compile(r"^[a-f0-9]{32}$")
_HASH = re.compile(r"^[a-f0-9]{64}$")
_LOCK = threading.RLock()
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _info(review_id: str) -> dict:
    return ocr_review._get_pack(review_id, force_sources=True)


def _is_reparse(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)


def _identity(info) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _check_components(path: Path) -> None:
    """Reject symlinks, junctions, and other reparse points without following them."""
    absolute = Path(os.path.abspath(path))
    components = list(reversed(absolute.parents)) + [absolute]
    for component in components:
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ocr_review.ReviewError("AI OCR yolu bileşeni denetlenemedi", 403) from exc
        if _is_reparse(info):
            raise ocr_review.ReviewError("AI OCR yolu sembolik bağlantı/junction içeremez", 403)


def _entry_lstat(path: Path):
    _check_components(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ocr_review.ReviewError("AI OCR dosya yolu denetlenemedi", 403) from exc
    if _is_reparse(info):
        raise ocr_review.ReviewError("AI OCR dosyası sembolik bağlantı/junction olamaz", 403)
    return info


def _read_regular_nofollow(path: Path, limit: int, label: str,
                           require_single_link: bool = False) -> bytes:
    before = _entry_lstat(path)
    if before is None:
        raise ocr_review.ReviewError(f"{label} bulunamadı", 404)
    if (not stat.S_ISREG(before.st_mode) or
            (require_single_link and getattr(before, "st_nlink", 1) != 1)):
        raise ocr_review.ReviewError(f"{label} normal dosya değil", 403)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ocr_review.ReviewError(f"{label} açılamadı", 403) from exc
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before) or
                (require_single_link and getattr(opened, "st_nlink", 1) != 1) or
                opened.st_size > limit):
            raise ocr_review.ReviewError(f"{label} değişti veya boyut sınırını aşıyor", 409)
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk); total += len(chunk)
            if total > limit:
                raise ocr_review.ReviewError(f"{label} boyut sınırını aşıyor", 413)
        after = _entry_lstat(path)
        if (after is None or _identity(after) != _identity(opened) or
                (require_single_link and getattr(after, "st_nlink", 1) != 1)):
            raise ocr_review.ReviewError(f"{label} okunurken yol değişti", 409)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _open_append_nofollow(path: Path) -> int:
    for attempt in range(2):
        before = _entry_lstat(path)
        if before is not None and (not stat.S_ISREG(before.st_mode) or getattr(before, "st_nlink", 1) != 1):
            raise ocr_review.ReviewError("AI OCR öneri günlüğü normal dosya değil", 403)
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if before is None:
            flags |= os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            if before is None and attempt == 0:
                continue
            raise ocr_review.ReviewError("AI OCR öneri günlüğü oluşturulurken yol değişti", 409)
        except OSError as exc:
            raise ocr_review.ReviewError("AI OCR öneri günlüğü güvenle açılamadı", 403) from exc
        try:
            opened = os.fstat(fd)
            after = _entry_lstat(path)
            if (not stat.S_ISREG(opened.st_mode) or after is None or _is_reparse(after) or
                    getattr(opened, "st_nlink", 1) != 1 or getattr(after, "st_nlink", 1) != 1 or
                    _identity(opened) != _identity(after) or
                    (before is not None and _identity(before) != _identity(opened))):
                raise ocr_review.ReviewError("AI OCR öneri günlüğü açılırken değişti", 409)
            return fd
        except Exception:
            os.close(fd)
            raise
    raise ocr_review.ReviewError("AI OCR öneri günlüğü güvenle açılamadı", 409)


def _publish_file_noreplace(staging: Path, target: Path) -> None:
    """Publish a regular staged file atomically, refusing every existing target."""
    _check_components(staging)
    _check_components(target.parent)
    source = staging.lstat()
    if _is_reparse(source) or not stat.S_ISREG(source.st_mode):
        raise ocr_review.ReviewError("Yayınlama geçici dosyası normal dosya değil", 409)
    if _entry_lstat(target) is not None:
        raise ocr_review.ReviewError("Hedef zaten var; üzerine yazılmaz", 409)
    try:
        # Same-directory hard-link creation is atomic and fails if target exists.
        # There is deliberately no rename/replace fallback on unsupported filesystems.
        os.link(staging, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise ocr_review.ReviewError("Hedef zaten var; üzerine yazılmaz", 409) from exc
    except (OSError, NotImplementedError) as exc:
        raise ocr_review.ReviewError("Dosya sistemi atomik no-replace yayınlamayı desteklemiyor", 409) from exc
    try:
        _check_components(target.parent)
        published = _entry_lstat(target)
        if published is None or _identity(published) != _identity(source):
            raise ocr_review.ReviewError("Yayınlanan dosya kimliği beklenenden farklı", 409)
    except Exception:
        # Do not delete by path after the no-replace link: another writer may
        # have substituted that name between inspection and cleanup.
        raise


def _dir(info: dict) -> Path:
    pack = Path(os.path.abspath(info["pack"]))
    root = pack / "ai-ocr-review"
    _check_components(pack)
    _check_components(root)
    existing = _entry_lstat(root)
    if existing is None:
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ocr_review.ReviewError("AI OCR inceleme klasörü oluşturulamadı", 403) from exc
    _check_components(root)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or _is_reparse(root_info):
        raise ocr_review.ReviewError("AI OCR inceleme yolu normal klasör değil", 403)
    if sum(1 for _ in root.iterdir()) > 10_000:
        raise ocr_review.ReviewError("AI OCR inceleme dosya sınırı aşıldı", 413)
    return root


def _bounded(path: Path, limit: int, label: str) -> bytes:
    return _read_regular_nofollow(path, limit, label)


def create_package(review_id: str, selected: object) -> tuple[Path, str, int]:
    info = _info(review_id)
    if (not isinstance(selected, list) or not 1 <= len(selected) <= MAX_CUES or
            any(not isinstance(x, str) for x in selected) or len(set(selected)) != len(selected)):
        raise ocr_review.ReviewError(f"1–{MAX_CUES} farklı altyazı seçin", 400)
    by_id = {cue["cue_id"]: cue for cue in info["cues"]}
    request_id = uuid.uuid4().hex
    cues, side_cues, images = [], [], []
    total_image_bytes = 0
    for cue_id in selected:
        cue = by_id.get(cue_id)
        if not cue or not cue.get("crop_valid"):
            raise ocr_review.ReviewError("Seçilen altyazının doğrulanabilir kırpımı yok", 409)
        crop = ocr_review._verify_crop(info, cue)
        if crop is None:
            raise ocr_review.ReviewError("Kırpım değişmiş veya doğrulanamıyor", 409)
        confidence = cue.get("ocr_confidence")
        if confidence is not None and (type(confidence) not in (int, float) or not math.isfinite(confidence)):
            raise ocr_review.ReviewError("OCR güven değeri geçerli sayı değil", 409)
        image = ocr_review._read_bounded(crop[0], ocr_review.MAX_CROP_BYTES, "Altyazı kırpımı")
        crop_hash = _sha(image)
        image_size = len(image)
        if crop_hash != cue.get("crop_sha256_local"):
            raise ocr_review.ReviewError("Kırpım paketi hazırlanırken değişti", 409)
        total_image_bytes += len(image)
        del image
        if total_image_bytes > MAX_PACKAGE_BYTES:
            raise ocr_review.ReviewError("Seçilen kırpımlar 12 MiB sınırını aşıyor; daha az cue seçin", 413)
        image_name = f"images/{cue_id}.jpg"
        row = {"cue_id": cue_id, "start_ms": cue["start_ms"], "end_ms": cue["end_ms"],
               "source_text_ocr": cue["source_text_ocr"], "ocr_confidence": confidence,
               "image_file": image_name}
        cues.append(row)
        side_cues.append({"cue_id": cue_id, "start_ms": cue["start_ms"], "end_ms": cue["end_ms"],
                          "source_text_ocr": cue["source_text_ocr"],
                          "crop_sha256_local": crop_hash, "crop_bytes": image_size})
        images.append((image_name, cue))
    prompt = (
        "# OCR görsel doğrulama görevi\n\n"
        "Her görselde yazılı altyazıyı okuyup aynı cue'nun source_text_ocr alanıyla karşılaştır. "
        "Yalnız görüntüdeki altyazı metnini değerlendir; sahne yazısı, tabela, jenerik, logo veya "
        "altyazı dışı yazıyı OCR metnine katma. Görsel okunamıyorsa uncertain seç; tahmin etme. "
        "Zamanları ve cue_id değerlerini değiştirme. Çıktıda yalnız response-template.json ile aynı "
        "şemada JSON döndür. Bu bir öneridir; insan onayı değildir.\n"
    )
    template = {"schema_version": 1, "request_id": request_id,
                "cues": [{"cue_id": row["cue_id"], "start_ms": row["start_ms"], "end_ms": row["end_ms"],
                          "status": "uncertain", "observed_text": "", "proposed_text": None,
                          "reason": "Görselden emin olunamadı."} for row in cues]}
    request = {"schema_version": 1, "request_id": request_id, "task": "manual_ai_ocr_visual_review",
               "instructions": prompt, "cues": cues}
    data = _json(request)
    request_zip_data = (json.dumps(request, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(data) + total_image_bytes + 4096 > MAX_PACKAGE_BYTES:
        raise ocr_review.ReviewError("AI inceleme paketi 12 MiB sınırını aşıyor; daha az cue seçin", 413)
    directory = _dir(info)
    sidecar = {"schema_version": 1, "request_id": request_id,
               "job_id": info["manifest"]["job_id"], "manifest_sha256": info["manifest_sha256"],
               "srt_sha256": info["srt_sha256"], "request_sha256": _sha(request_zip_data), "cues": side_cues}
    side_data = _json(sidecar)
    base = directory / request_id
    zip_path, side_path = base.with_suffix(".zip"), base.with_suffix(".local.json")
    fd, zip_temp_name = tempfile.mkstemp(prefix=".ocr-review-", suffix=".zip", dir=directory)
    os.close(fd)
    zip_staging = Path(zip_temp_name)
    side_fd, side_temp_name = tempfile.mkstemp(prefix=".ocr-sidecar-", suffix=".tmp", dir=directory)
    side_staging = Path(side_temp_name)
    try:
        with zipfile.ZipFile(zip_staging, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr("prompt.md", prompt)
            archive.writestr("request.json", request_zip_data)
            archive.writestr("response-template.json", json.dumps(template, ensure_ascii=False, indent=2) + "\n")
            for name, cue in images:
                crop = ocr_review._verify_crop(info, cue)
                if crop is None:
                    raise ocr_review.ReviewError("Kırpım ZIP yazılırken değişti", 409)
                image = ocr_review._read_bounded(crop[0], ocr_review.MAX_CROP_BYTES, "Altyazı kırpımı")
                if _sha(image) != cue.get("crop_sha256_local"):
                    raise ocr_review.ReviewError("Kırpım ZIP yazılırken hash doğrulaması başarısız", 409)
                archive.writestr(name, image)
                del image
        _check_components(zip_staging)
        if zip_staging.stat().st_size > MAX_PACKAGE_BYTES:
            raise ocr_review.ReviewError("AI inceleme ZIP'i 12 MiB sınırını aşıyor; daha az cue seçin", 413)
        os.chmod(side_staging, 0o600)
        with os.fdopen(side_fd, "wb") as stream:
            side_fd = -1
            stream.write(side_data); stream.flush(); os.fsync(stream.fileno())
        _publish_file_noreplace(zip_staging, zip_path)
        _publish_file_noreplace(side_staging, side_path)
        zip_staging.unlink()
        side_staging.unlink()
    except Exception:
        if side_fd >= 0:
            os.close(side_fd)
        # A ZIP may remain orphaned if its companion sidecar could not publish.
        # Never unlink a published target by path during failure cleanup.
        zip_staging.unlink(missing_ok=True)
        side_staging.unlink(missing_ok=True)
        raise
    return zip_path, f"hardsub-ai-ocr-review-{request_id}.zip", len(cues)


def _load_sidecar(info: dict, request_id: str) -> dict:
    if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
        raise ocr_review.ReviewError("İstek kimliği geçersiz", 400)
    path = _dir(info) / f"{request_id}.local.json"
    raw = _bounded(path, 1024 * 1024, "Yerel AI OCR eşlemesi")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ocr_review.ReviewError("Yerel AI OCR eşlemesi bozuk", 409)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "request_id", "job_id",
            "manifest_sha256", "srt_sha256", "request_sha256", "cues"} or
            value.get("schema_version") != 1 or value.get("request_id") != request_id or
            value.get("job_id") != info["manifest"]["job_id"] or
            value.get("manifest_sha256") != info["manifest_sha256"] or
            value.get("srt_sha256") != info["srt_sha256"] or not isinstance(value.get("cues"), list) or
            not 1 <= len(value["cues"]) <= MAX_CUES):
        raise ocr_review.ReviewError("AI OCR isteği güncel paketle eşleşmiyor", 409)
    zip_path = _dir(info) / f"{request_id}.zip"
    zip_data = _bounded(zip_path, MAX_PACKAGE_BYTES, "AI OCR isteği ZIP'i")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or not {"prompt.md", "request.json", "response-template.json"}.issubset(names):
                raise ValueError("zip members")
            if (archive.getinfo("request.json").file_size > MAX_PACKAGE_BYTES or
                    archive.getinfo("response-template.json").file_size > MAX_PACKAGE_BYTES or
                    archive.getinfo("prompt.md").file_size > 16_384):
                raise ValueError("oversized metadata")
            public_request_bytes = archive.read("request.json")
            public_request = json.loads(public_request_bytes.decode("utf-8"))
            template = json.loads(archive.read("response-template.json").decode("utf-8"))
            archive_images = {}
            archive_image_bytes = 0
            for name in names:
                if name.startswith("images/"):
                    image_info = archive.getinfo(name)
                    archive_image_bytes += image_info.file_size
                    if (image_info.file_size > ocr_review.MAX_CROP_BYTES or
                            archive_image_bytes > MAX_PACKAGE_BYTES):
                        raise ValueError("oversized image")
                    archive_images[name] = archive.read(name)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, KeyError):
        raise ocr_review.ReviewError("AI OCR ZIP isteği bozuk veya eksik", 409)
    expected_names = {"prompt.md", "request.json", "response-template.json"} | {
        f"images/{row['cue_id']}.jpg" for row in value["cues"] if isinstance(row, dict) and isinstance(row.get("cue_id"), str)}
    if (set(names) != expected_names or _sha(public_request_bytes) != value.get("request_sha256") or
            not isinstance(public_request, dict) or public_request.get("schema_version") != 1 or
            public_request.get("request_id") != request_id or public_request.get("task") != "manual_ai_ocr_visual_review" or
            not isinstance(public_request.get("cues"), list) or not isinstance(template, dict) or
            template.get("schema_version") != 1 or template.get("request_id") != request_id):
        raise ocr_review.ReviewError("AI OCR ZIP isteği yerel eşlemeyle uyuşmuyor", 409)
    public_rows = {row.get("cue_id"): row for row in public_request["cues"] if isinstance(row, dict)}
    template_rows = {row.get("cue_id"): row for row in template.get("cues", []) if isinstance(row, dict)}
    if (len(public_rows) != len(value["cues"]) or len(template_rows) != len(value["cues"]) or
            set(public_rows) != {row.get("cue_id") for row in value["cues"]} or
            set(template_rows) != set(public_rows)):
        raise ocr_review.ReviewError("AI OCR ZIP cue sayısı/kimliği yerel eşlemeyle uyuşmuyor", 409)
    cue_map = {cue["cue_id"]: cue for cue in info["cues"]}
    seen = set()
    for row in value["cues"]:
        cue = cue_map.get(row.get("cue_id")) if isinstance(row, dict) else None
        if (not cue or row["cue_id"] in seen or not cue.get("crop_valid") or
                row.get("start_ms") != cue["start_ms"] or row.get("end_ms") != cue["end_ms"] or
                row.get("source_text_ocr") != cue["source_text_ocr"] or
                not isinstance(row.get("crop_sha256_local"), str) or not _HASH.fullmatch(row["crop_sha256_local"])):
            raise ocr_review.ReviewError("AI OCR isteği cue eşlemesi güncel paketle uyuşmuyor", 409)
        public = public_rows.get(row["cue_id"]); template_row = template_rows.get(row["cue_id"])
        if (not isinstance(public, dict) or set(public) != {"cue_id", "start_ms", "end_ms", "source_text_ocr", "ocr_confidence", "image_file"} or
                public.get("start_ms") != row["start_ms"] or public.get("end_ms") != row["end_ms"] or
                public.get("source_text_ocr") != row["source_text_ocr"] or
                public.get("image_file") != f"images/{row['cue_id']}.jpg" or
                not isinstance(template_row, dict) or template_row.get("start_ms") != row["start_ms"] or
                template_row.get("end_ms") != row["end_ms"]):
            raise ocr_review.ReviewError("AI OCR ZIP cue eşlemesi değiştirilmiş", 409)
        image = archive_images.get(public["image_file"])
        if not isinstance(image, bytes):
            raise ocr_review.ReviewError("AI OCR ZIP görseli eksik", 409)
        if (row["crop_bytes"] > ocr_review.MAX_CROP_BYTES or
                len(image) != row["crop_bytes"] or _sha(image) != row["crop_sha256_local"]):
            raise ocr_review.ReviewError("AI OCR ZIP görseli hash eşlemesiyle uyuşmuyor", 409)
        crop = ocr_review._verify_crop(info, cue)
        if crop is None or crop[1] != row.get("crop_bytes") or _sha(ocr_review._read_bounded(crop[0], ocr_review.MAX_CROP_BYTES, "Kırpım")) != row["crop_sha256_local"]:
            raise ocr_review.ReviewError("İstek kırpımı değişmiş veya doğrulanamıyor", 409)
        seen.add(row["cue_id"])
    return value


def _validate_response(value: object, sidecar: dict) -> tuple[list[dict], str]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "request_id", "cues"}:
        raise ocr_review.ReviewError("Yanıtın üst düzey alanları şemayla eşleşmiyor", 422)
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1 or value.get("request_id") != sidecar["request_id"]:
        raise ocr_review.ReviewError("Yanıt başka isteğe ait veya şema sürümü geçersiz", 409)
    sent = {row["cue_id"]: row for row in sidecar["cues"]}
    rows = value.get("cues")
    if not isinstance(rows, list) or len(rows) != len(sent):
        raise ocr_review.ReviewError("Yanıt cue sayısı istekle eşleşmiyor", 422)
    seen, normalized = set(), []
    for row in rows:
        fields = {"cue_id", "start_ms", "end_ms", "status", "observed_text", "proposed_text", "reason"}
        if not isinstance(row, dict) or set(row) != fields:
            raise ocr_review.ReviewError("Cue yanıtı şemayla eşleşmiyor", 422)
        cue_id = row.get("cue_id"); source = sent.get(cue_id)
        if (not source or cue_id in seen or type(row.get("start_ms")) is not int or
                type(row.get("end_ms")) is not int or row["start_ms"] != source["start_ms"] or
                row["end_ms"] != source["end_ms"]):
            raise ocr_review.ReviewError("Cue kimliği/zamanı eksik, yinelenmiş veya değişmiş", 409)
        status = row.get("status")
        observed, proposed, reason = row.get("observed_text"), row.get("proposed_text"), row.get("reason")
        if (status not in {"confirmed", "correction_proposed", "uncertain"} or
                not isinstance(observed, str) or len(observed) > MAX_TEXT or
                not isinstance(reason, str) or not reason.strip() or len(reason) > 4_000):
            raise ocr_review.ReviewError("OCR görsel değerlendirmesi geçersiz", 422)
        if status == "confirmed":
            if proposed is not None or observed != source["source_text_ocr"]:
                raise ocr_review.ReviewError("confirmed yanıtı mevcut OCR metniyle eşleşmeli", 422)
        elif status == "correction_proposed":
            if not isinstance(proposed, str) or not proposed.strip() or len(proposed) > MAX_TEXT or proposed == source["source_text_ocr"]:
                raise ocr_review.ReviewError("Düzeltme önerisi boş veya mevcut OCR metniyle aynı", 422)
        elif proposed is not None:
            raise ocr_review.ReviewError("Belirsiz yanıtta proposed_text null olmalı", 422)
        normalized.append({"cue_id": cue_id, "start_ms": row["start_ms"], "end_ms": row["end_ms"],
                           "status": status, "observed_text": observed, "proposed_text": proposed,
                           "reason": reason.strip()})
        seen.add(cue_id)
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return normalized, _sha(canonical)


def _events(info: dict) -> list[dict]:
    path = _dir(info) / "proposals.jsonl"
    entry = _entry_lstat(path)
    if entry is None:
        return []
    if getattr(entry, "st_nlink", 1) != 1:
        raise ocr_review.ReviewError("AI OCR öneri günlüğü hard link olamaz", 403)
    raw = _read_regular_nofollow(path, MAX_LOG_BYTES, "AI OCR öneri günlüğü", require_single_link=True)
    lines = raw.splitlines()
    if len(lines) > MAX_LOG_RECORDS:
        raise ocr_review.ReviewError("AI OCR öneri olayı sınırı aşıldı; kısmi geçmiş gösterilemez", 413)
    result = []
    event_ids = set()
    exact = {"schema_version", "event_type", "event_id", "request_id", "response_sha256", "job_id",
             "manifest_sha256", "srt_sha256", "crop_sha256_local", "created_at", "cue_id", "start_ms",
             "end_ms", "status", "observed_text", "proposed_text", "reason"}
    for line in lines:
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ocr_review.ReviewError("AI OCR öneri günlüğünde bozuk kayıt var; yeni yazım durduruldu", 409)
        if (not isinstance(event, dict) or set(event) != exact or event.get("schema_version") != 1 or
                event.get("event_type") != "proposal" or not isinstance(event.get("event_id"), str) or
                not _ID.fullmatch(event["event_id"]) or not isinstance(event.get("request_id"), str) or
                not _ID.fullmatch(event["request_id"]) or any(not isinstance(event.get(k), str) or not _HASH.fullmatch(event[k])
                for k in ("response_sha256", "manifest_sha256", "srt_sha256", "crop_sha256_local")) or
                not isinstance(event.get("job_id"), str) or not _ID.fullmatch(event["job_id"]) or
                not isinstance(event.get("cue_id"), str) or
                not re.fullmatch(re.escape(event.get("job_id", "")) + r"-cue-\d{5,}", event.get("cue_id", "")) or
                any(type(event.get(k)) is not int for k in ("start_ms", "end_ms")) or event["start_ms"] < 0 or
                event["end_ms"] <= event["start_ms"] or event.get("status") not in ("confirmed", "correction_proposed", "uncertain") or
                not isinstance(event.get("observed_text"), str) or len(event["observed_text"]) > MAX_TEXT or
                not isinstance(event.get("reason"), str) or not event["reason"].strip() or
                (event["status"] == "confirmed" and (event.get("proposed_text") is not None)) or
                (event["status"] == "correction_proposed" and
                 (not isinstance(event.get("proposed_text"), str) or not event["proposed_text"].strip() or
                  len(event["proposed_text"]) > MAX_TEXT)) or
                (event["status"] == "uncertain" and event.get("proposed_text") is not None)):
            raise ocr_review.ReviewError("AI OCR öneri günlüğünde şema dışı kayıt var; yeni yazım durduruldu", 409)
        if event["event_id"] in event_ids:
            raise ocr_review.ReviewError("AI OCR öneri günlüğünde yinelenen event ID var", 409)
        try:
            timestamp = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise ocr_review.ReviewError("AI OCR öneri günlüğünde zaman damgası bozuk", 409)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ocr_review.ReviewError("AI OCR öneri günlüğü zaman damgasında saat dilimi yok", 409)
        event_ids.add(event["event_id"])
        result.append(event)
    return result


def import_response(review_id: str, response: object) -> dict:
    info = _info(review_id)
    if not isinstance(response, dict):
        raise ocr_review.ReviewError("JSON nesnesi gerekli", 400)
    request_id = response.get("request_id")
    sidecar = _load_sidecar(info, request_id)
    rows, response_hash = _validate_response(response, sidecar)
    directory = _dir(info); path = directory / "proposals.jsonl"
    with _LOCK:
        events = _events(info)
        if any(e.get("request_id") == request_id and e.get("response_sha256") == response_hash for e in events):
            return {"ok": True, "duplicate": True, "imported": 0}
        if len(events) + len(rows) > MAX_LOG_RECORDS:
            raise ocr_review.ReviewError("AI OCR öneri olayı sınırına ulaştı", 413)
        now = _now()
        lines = []
        for row in rows:
            lines.append(_json({"schema_version": 1, "event_type": "proposal", "event_id": uuid.uuid4().hex,
                                "request_id": request_id, "response_sha256": response_hash,
                                "job_id": info["manifest"]["job_id"],
                                "manifest_sha256": info["manifest_sha256"], "srt_sha256": info["srt_sha256"],
                                "crop_sha256_local": next(x["crop_sha256_local"] for x in sidecar["cues"] if x["cue_id"] == row["cue_id"]),
                                "created_at": now, **row}))
        current = _entry_lstat(path)
        if current is not None and current.st_size + sum(map(len, lines)) > MAX_LOG_BYTES:
            raise ocr_review.ReviewError("AI OCR öneri günlüğü boyut sınırına ulaştı", 413)
        fd = _open_append_nofollow(path)
        if os.fstat(fd).st_size + sum(map(len, lines)) > MAX_LOG_BYTES:
            os.close(fd)
            raise ocr_review.ReviewError("AI OCR öneri günlüğü boyut sınırına ulaştı", 413)
        with os.fdopen(fd, "ab") as stream:
            for line in lines: stream.write(line)
            stream.flush(); os.fsync(stream.fileno())
    return {"ok": True, "duplicate": False, "imported": len(rows)}


def status(review_id: str) -> dict:
    info = _info(review_id)
    cue_map = {cue["cue_id"]: cue for cue in info["cues"]}
    valid = []
    verified_crops = {}
    for event in _events(info):
        cue = cue_map.get(event.get("cue_id"))
        if cue and cue["cue_id"] not in verified_crops:
            verified_crops[cue["cue_id"]] = ocr_review._verify_crop(info, cue) is not None
        relation_valid = bool(cue and event.get("start_ms") == cue["start_ms"] and
                              event.get("end_ms") == cue["end_ms"] and
                              (event["status"] != "confirmed" or event["observed_text"] == cue["source_text_ocr"]) and
                              (event["status"] != "correction_proposed" or
                               event["proposed_text"] != cue["source_text_ocr"]))
        if (cue and relation_valid and verified_crops[cue["cue_id"]] and cue.get("crop_valid") and
                event.get("job_id") == info["manifest"]["job_id"] and
                event.get("manifest_sha256") == info["manifest_sha256"] and event.get("srt_sha256") == info["srt_sha256"] and
                event.get("crop_sha256_local") == cue.get("crop_sha256_local")):
            valid.append(event)
    return {"proposals": valid, "review_log": "ai-ocr-review/proposals.jsonl",
            "ai_output_is_human_label": False}
