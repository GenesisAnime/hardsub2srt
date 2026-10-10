"""Manual, local AI translation review round-trip.

This module only prepares/imports files and records user decisions. It never
connects to an AI provider or uploads content.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import ocr_review


MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CUES_PER_REQUEST = 100
MAX_EVENTS_BYTES = 32 * 1024 * 1024
MAX_EVENT_LINE_BYTES = 128 * 1024
MAX_EVENTS = 20_000
MAX_TEXT = 20_000
ISSUES = {"omission", "addition", "meaning", "term", "style", "grammar", "none"}
_REQUEST_ID = re.compile(r"^[a-f0-9]{32}$")
_LOCK = threading.RLock()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _append(path: Path, event: dict) -> None:
    line = _json_bytes(event)
    if len(line) > MAX_EVENT_LINE_BYTES:
        raise ocr_review.ReviewError("AI inceleme kaydı satır sınırını aşıyor", 413)
    if path.is_symlink():
        raise ocr_review.ReviewError("AI inceleme günlüğü sembolik bağlantı olamaz", 403)
    if path.exists():
        resolved = path.resolve(strict=True)
        if not ocr_review._contained(resolved, path.parent.resolve(strict=True)) or not path.is_file():
            raise ocr_review.ReviewError("AI inceleme günlüğü paket dışına yönleniyor", 403)
        size = path.stat().st_size
        if size > MAX_EVENTS_BYTES or size + len(line) > MAX_EVENTS_BYTES:
            raise ocr_review.ReviewError("AI inceleme günlüğü boyut sınırına ulaştı", 413)
        count = _read_events(path)[0]
    else:
        size, count = 0, 0
    if count >= MAX_EVENTS:
        raise ocr_review.ReviewError("AI inceleme günlüğü olay sınırına ulaştı", 413)
    if size:
        with path.open("rb") as stream:
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) != b"\n":
                raise ocr_review.ReviewError("AI inceleme günlüğünün son kaydı yarım; yeni yazım durduruldu", 409)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def _read_events(path: Path) -> tuple[int, list[dict]]:
    if not path.exists():
        return 0, []
    if path.is_symlink():
        raise ocr_review.ReviewError("AI inceleme günlüğü sembolik bağlantı olamaz", 403)
    if path.stat().st_size > MAX_EVENTS_BYTES:
        raise ocr_review.ReviewError("AI inceleme günlüğü boyut sınırını aşıyor", 413)
    events, count = [], 0
    with path.open("rb") as stream:
        while True:
            line = stream.readline(MAX_EVENT_LINE_BYTES + 1)
            if not line:
                break
            count += 1
            if count > MAX_EVENTS or len(line) > MAX_EVENT_LINE_BYTES:
                if count > MAX_EVENTS:
                    raise ocr_review.ReviewError("AI inceleme günlüğü olay sınırı aşıldı; geçmişten kısmi karar üretilemez", 413)
                while line and not line.endswith(b"\n"):
                    line = stream.readline(MAX_EVENT_LINE_BYTES + 1)
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict):
                continue
            events.append(value)
    return count, events


def _info(review_id: str) -> dict:
    return ocr_review._get_pack(review_id, force_sources=True)


def _verified_sources(info: dict, verify_crops: bool = False) -> dict[str, dict]:
    active, _warnings = ocr_review._active(info, force=True)
    result = {}
    cues = {cue["cue_id"]: cue for cue in info["cues"]}
    for cue_id, event in active.items():
        cue = cues.get(cue_id)
        if (cue and event.get("status") in ("accepted", "corrected") and
                cue.get("crop_valid") and
                event.get("crop_sha256_local") == cue.get("crop_sha256_local") and
                isinstance(event.get("verified_source_text"), str) and
                event["verified_source_text"].strip()):
            if not verify_crops or ocr_review._verify_crop(info, cue) is not None:
                result[cue_id] = {"cue": cue, "event": event}
    return result


def _request_dir(info: dict) -> Path:
    root = info["pack"] / "ai-review-requests"
    if root.is_symlink():
        raise ocr_review.ReviewError("AI istek klasörü sembolik bağlantı olamaz", 403)
    root.mkdir(mode=0o700, exist_ok=True)
    resolved = root.resolve(strict=True)
    if not ocr_review._contained(resolved, info["pack"].resolve(strict=True)):
        raise ocr_review.ReviewError("AI istek klasörü paket dışına yönleniyor", 403)
    if sum(1 for _ in resolved.iterdir()) > 10_000:
        raise ocr_review.ReviewError("AI istek klasörü dosya sınırını aştı", 413)
    return resolved


def create_request(review_id: str, drafts: object, source_language: object,
                   target_language: object) -> dict:
    info = _info(review_id)
    if not isinstance(source_language, str) or not source_language.strip() or len(source_language) > 40:
        raise ocr_review.ReviewError("Kaynak dil etiketi gerekli (ör. ja)", 400)
    if not isinstance(target_language, str) or not target_language.strip() or len(target_language) > 40:
        raise ocr_review.ReviewError("Hedef dil etiketi gerekli (ör. tr)", 400)
    if not isinstance(drafts, dict) or not drafts or len(drafts) > MAX_CUES_PER_REQUEST:
        raise ocr_review.ReviewError(f"1–{MAX_CUES_PER_REQUEST} altyazı seçin", 400)
    verified = _verified_sources(info)
    request = {
        "schema_version": 1, "request_id": uuid.uuid4().hex,
        "source_language": source_language.strip(), "target_language": target_language.strip(),
        "task": "source_first_translation_review",
        "instructions": (
            "Her cue için önce görüntüdeki kaynak metni doğrula. Kaynağı görüntüden "
            "kesin okuyamıyorsan source_review.status=uncertain yap ve translation_review.status=deferred "
            "döndür; bağlamdan tahmin etme. Kaynak görüntüde doğrulanırsa çeviri taslağını anlam, "
            "eksik/fazla içerik, terim ve üslup açısından değerlendir. Yalnız öneri ver; SRT zamanını, "
            "cue_id değerini değiştirme; cue ekleme/birleştirme/bölme. JSON dışında metin yazma."
        ),
        "cues": [],
        "response_schema": {
            "schema_version": 1, "request_id": "same request_id", "cues": [{
                "cue_id": "same cue_id", "start_ms": 0, "end_ms": 1,
                "source_review": {"status": "confirmed|correction_proposed|uncertain",
                                  "observed_text": "string", "proposed_source_text": None,
                                  "reason": "string"},
                "translation_review": {"status": "reviewed|deferred",
                                       "issues": ["meaning|omission|addition|term|style|grammar|none"],
                                       "proposed_translation": None, "reason": "string"},
            }],
        },
    }
    items, private_cues = request["cues"], []
    # Reserve envelope overhead first. Image bytes are checked against the
    # exact JSON budget before they are read into an accumulated base64 batch.
    estimated_size = len(_json_bytes(request)) - 2  # replace the empty [] with cue JSON
    for cue_id, draft in drafts.items():
        if not isinstance(cue_id, str) or not _valid_text(draft):
            raise ocr_review.ReviewError("Altyazı kimliği veya çeviri taslağı geçersiz", 400)
        link = verified.get(cue_id)
        if not link:
            raise ocr_review.ReviewError("Her seçili blok önce görselle doğrulanmış olmalı", 409)
        cue, source_event = link["cue"], link["event"]
        crop = ocr_review._verify_crop(info, cue)
        if crop is None:
            raise ocr_review.ReviewError("Kırpım değişmiş veya doğrulanamıyor", 409)
        item = {
            "cue_id": cue_id, "start_ms": cue["start_ms"], "end_ms": cue["end_ms"],
            "verified_source_text": source_event["verified_source_text"],
            "draft_translation": draft.strip(), "crop_mime_type": "image/jpeg",
            "crop_base64": "",
        }
        base64_length = 4 * ((crop[1] + 2) // 3)
        item_size = len(_json_bytes(item)) - 1 + base64_length
        proposed_size = estimated_size + (1 if items else 0) + item_size
        if proposed_size > MAX_REQUEST_BYTES:
            raise ocr_review.ReviewError(
                "İstek paketi 12 MiB sınırını aşacak; daha az veya küçük kırpım seçin", 413)
        image = ocr_review._read_bounded(crop[0], ocr_review.MAX_CROP_BYTES, "Altyazı kırpımı")
        if len(image) != crop[1] or _sha(image) != cue.get("crop_sha256_local"):
            raise ocr_review.ReviewError("Kırpım istek hazırlanırken değişti; istek oluşturulmadı", 409)
        item["crop_base64"] = base64.b64encode(image).decode("ascii")
        items.append(item)
        estimated_size = proposed_size
        private_cues.append({"cue_id": cue_id, "source_review_event_id": source_event["event_id"],
                             "crop_sha256_local": cue["crop_sha256_local"],
                             "verified_source_text": source_event["verified_source_text"],
                             "draft_translation": draft.strip(),
                             "start_ms": cue["start_ms"], "end_ms": cue["end_ms"]})
    data = _json_bytes(request)
    if len(data) > MAX_REQUEST_BYTES:
        raise ocr_review.ReviewError("İstek paketi 12 MiB sınırını aşıyor; daha küçük grup seçin", 413)
    directory = _request_dir(info)
    path = directory / f"{request['request_id']}.json"
    if path.exists():
        raise ocr_review.ReviewError("İstek kimliği çakıştı; yeniden deneyin", 409)
    sidecar = {"schema_version": 1, "request_id": request["request_id"],
               "job_id": info["manifest"]["job_id"],
               "manifest_sha256": info["manifest_sha256"], "srt_sha256": info["srt_sha256"],
               "request_sha256": _sha(data), "cues": private_cues}
    sidecar_path = directory / f".{request['request_id']}.local.json"
    if sidecar_path.exists() or sidecar_path.is_symlink():
        raise ocr_review.ReviewError("Yerel istek eşleme kimliği çakıştı; yeniden deneyin", 409)
    fd, tmp_name = tempfile.mkstemp(prefix=".request-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        Path(tmp_name).replace(path)
        side_data = _json_bytes(sidecar)
        side_fd, side_tmp = tempfile.mkstemp(prefix=".sidecar-", dir=directory)
        try:
            os.chmod(side_tmp, 0o600)
            with os.fdopen(side_fd, "wb") as stream:
                stream.write(side_data)
                stream.flush()
                os.fsync(stream.fileno())
            Path(side_tmp).replace(sidecar_path)
        except Exception:
            Path(side_tmp).unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return {"filename": path.name, "request": request}


def _valid_text(value: object, allow_empty: bool = False) -> bool:
    return isinstance(value, str) and len(value) <= MAX_TEXT and (allow_empty or bool(value.strip()))


def _load_request(info: dict, request_id: str) -> dict:
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ocr_review.ReviewError("İstek kimliği geçersiz", 400)
    directory = _request_dir(info)
    raw_path = directory / f"{request_id}.json"
    if raw_path.is_symlink():
        raise ocr_review.ReviewError("AI istek dosyası sembolik bağlantı olamaz", 403)
    try:
        path = raw_path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ocr_review.ReviewError("AI istek dosyası bulunamadı", 404)
    if not ocr_review._contained(path, directory) or not path.is_file():
        raise ocr_review.ReviewError("İstek dosyası bulunamadı", 404)
    data = ocr_review._read_bounded(path, MAX_REQUEST_BYTES, "AI isteği")
    try:
        request = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ocr_review.ReviewError("AI istek dosyası bozuk", 409)
    raw_sidecar_path = directory / f".{request_id}.local.json"
    if raw_sidecar_path.is_symlink():
        raise ocr_review.ReviewError("Yerel AI istek eşlemesi sembolik bağlantı olamaz", 403)
    try:
        sidecar_path = raw_sidecar_path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ocr_review.ReviewError("İsteğin yerel eşleştirme kaydı bulunamadı", 409)
    if not ocr_review._contained(sidecar_path, directory) or not sidecar_path.is_file():
        raise ocr_review.ReviewError("İsteğin yerel eşleştirme kaydı bulunamadı", 409)
    sidecar_data = ocr_review._read_bounded(sidecar_path, 1024 * 1024, "Yerel AI istek eşlemesi")
    try:
        sidecar = json.loads(sidecar_data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ocr_review.ReviewError("Yerel AI istek eşlemesi bozuk", 409)
    if (not isinstance(request, dict) or request.get("request_id") != request_id or
            not isinstance(sidecar, dict) or sidecar.get("request_id") != request_id or
            set(sidecar) != {"schema_version", "request_id", "job_id", "manifest_sha256", "srt_sha256",
                            "request_sha256", "cues"} or sidecar.get("schema_version") != 1 or
            sidecar.get("job_id") != info["manifest"]["job_id"] or
            sidecar.get("manifest_sha256") != info["manifest_sha256"] or
            sidecar.get("srt_sha256") != info["srt_sha256"] or
            sidecar.get("request_sha256") != _sha(data)):
        raise ocr_review.ReviewError("AI isteği bu güncel review-pack ile eşleşmiyor", 409)
    public_cues, private_cues = request.get("cues"), sidecar.get("cues")
    if (request.get("schema_version") != 1 or not isinstance(public_cues, list) or
            not isinstance(private_cues, list) or len(public_cues) != len(private_cues) or
            not 1 <= len(public_cues) <= MAX_CUES_PER_REQUEST or
            set(request) != {"schema_version", "request_id", "source_language", "target_language", "task",
                            "instructions", "cues", "response_schema"}):
        raise ocr_review.ReviewError("AI isteği içeriği geçersiz", 409)
    for public, local in zip(public_cues, private_cues):
        if (not isinstance(public, dict) or not isinstance(local, dict) or
                public.get("cue_id") != local.get("cue_id") or
                public.get("start_ms") != local.get("start_ms") or
                public.get("end_ms") != local.get("end_ms") or
                public.get("verified_source_text") != local.get("verified_source_text") or
                public.get("draft_translation") != local.get("draft_translation") or
                public.get("crop_mime_type") != "image/jpeg" or
                not isinstance(public.get("crop_base64"), str)):
            raise ocr_review.ReviewError("AI isteği yerel eşlemesi değişmiş veya bozuk", 409)
        try:
            crop_data = base64.b64decode(public["crop_base64"], validate=True)
        except (ValueError, base64.binascii.Error):
            raise ocr_review.ReviewError("AI isteğindeki görsel bozuk", 409)
        if (_sha(crop_data) != local.get("crop_sha256_local") or
                len(crop_data) > ocr_review.MAX_CROP_BYTES or
                not _valid_text(local.get("source_review_event_id"))):
            raise ocr_review.ReviewError("AI isteğindeki görsel/eşleme hash'i geçersiz", 409)
    request["_local"] = sidecar
    return request


def _validate_response(response: object, request: dict) -> list[dict]:
    if not isinstance(response, dict) or set(response) != {"schema_version", "request_id", "cues"}:
        raise ocr_review.ReviewError("Yanıtın üst düzey alanları şemayla eşleşmiyor", 422)
    for key in ("schema_version", "request_id"):
        expected = 1 if key == "schema_version" else request["request_id"]
        if type(response.get(key)) is not type(expected) or response.get(key) != expected:
            raise ocr_review.ReviewError("Yanıt başka istek/paket için veya sürümü geçersiz", 409)
    rows = response.get("cues")
    expected_rows = {row["cue_id"]: row for row in request["cues"]}
    if not isinstance(rows, list) or len(rows) != len(expected_rows):
        raise ocr_review.ReviewError("Yanıt cue sayısı istekle eşleşmiyor", 422)
    seen, normalized = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "cue_id", "start_ms", "end_ms", "source_review", "translation_review"}:
            raise ocr_review.ReviewError("Cue yanıtı şemayla eşleşmiyor", 422)
        cue_id = row.get("cue_id")
        sent = expected_rows.get(cue_id)
        if (not sent or cue_id in seen or type(row.get("start_ms")) is not int or
                type(row.get("end_ms")) is not int or row["start_ms"] != sent["start_ms"] or
                row["end_ms"] != sent["end_ms"]):
            raise ocr_review.ReviewError("Yanıttaki cue/timing istektekiyle eşleşmiyor", 409)
        seen.add(cue_id)
        source = row["source_review"]
        trans = row["translation_review"]
        if (not isinstance(source, dict) or set(source) != {
                "status", "observed_text", "proposed_source_text", "reason"} or
                source.get("status") not in ("confirmed", "correction_proposed", "uncertain") or
                not _valid_text(source.get("observed_text"), allow_empty=True) or
                not _valid_text(source.get("reason"))):
            raise ocr_review.ReviewError("Kaynak incelemesi geçersiz", 422)
        proposal = source.get("proposed_source_text")
        if source["status"] == "correction_proposed":
            if not _valid_text(proposal) or proposal == sent["verified_source_text"]:
                raise ocr_review.ReviewError("Kaynak düzeltme önerisi boş/geçersiz", 422)
        elif proposal is not None:
            raise ocr_review.ReviewError("Kaynak önerisi yalnız correction_proposed durumunda olabilir", 422)
        if source["status"] == "confirmed" and source["observed_text"] != sent["verified_source_text"]:
            raise ocr_review.ReviewError("Kaynak confirmed denmiş ancak görüntü metni doğrulanmış metinle aynı değil", 422)
        if (not isinstance(trans, dict) or set(trans) != {
                "status", "issues", "proposed_translation", "reason"} or
                trans.get("status") not in ("reviewed", "deferred") or
                not isinstance(trans.get("issues"), list) or len(trans["issues"]) > len(ISSUES) or
                any(not isinstance(issue, str) or issue not in ISSUES for issue in trans["issues"]) or
                len(set(trans["issues"])) != len(trans["issues"]) or
                ("none" in trans["issues"] and len(trans["issues"]) != 1) or
                not _valid_text(trans.get("reason"))):
            raise ocr_review.ReviewError("Çeviri incelemesi geçersiz", 422)
        translated = trans.get("proposed_translation")
        if trans["status"] == "reviewed":
            if source["status"] != "confirmed":
                raise ocr_review.ReviewError("Kaynak confirmed değil; çeviri mutlaka deferred olmalı", 422)
            if not _valid_text(translated):
                raise ocr_review.ReviewError("İncelenen çeviri önerisi boş/geçersiz", 422)
        elif translated is not None:
            raise ocr_review.ReviewError("deferred çeviride proposal null olmalı", 422)
        normalized.append({**row})
    return normalized


def import_response(review_id: str, value: object) -> dict:
    with _LOCK:
        return _import_response_locked(review_id, value)


def _import_response_locked(review_id: str, value: object) -> dict:
    info = _info(review_id)
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ocr_review.ReviewError("AI yanıtı 4 MiB sınırını aşıyor", 413)
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ocr_review.ReviewError("AI yanıtı geçerli JSON değil", 422)
    request_id = value.get("request_id") if isinstance(value, dict) else None
    request = _load_request(info, request_id)
    rows = _validate_response(value, request)
    # Re-check the human source links before importing any proposal.
    verified = _verified_sources(info)
    now = _now()
    log = info["pack"] / "translation-review-events.jsonl"
    _existing_count, existing = _read_events(log)
    imported = {(event.get("request_id"), event.get("cue_id")) for event in existing
                if _valid_event(event) and event.get("event_type") == "proposal"}
    added = []
    private_rows = {item["cue_id"]: item for item in request["_local"]["cues"]}
    for row in rows:
        sent = next(item for item in request["cues"] if item["cue_id"] == row["cue_id"])
        local = private_rows.get(row["cue_id"])
        link = verified.get(row["cue_id"])
        if (not local or not link or link["event"]["event_id"] != local["source_review_event_id"] or
                link["event"]["verified_source_text"] != local["verified_source_text"] or
                link["cue"]["crop_sha256_local"] != local["crop_sha256_local"] or
                sent["start_ms"] != local["start_ms"] or sent["end_ms"] != local["end_ms"]):
            raise ocr_review.ReviewError("Kaynak doğrulama isteğin hazırlanmasından sonra değişti", 409)
        if ocr_review._verify_crop(info, link["cue"]) is None:
            raise ocr_review.ReviewError("Review-pack kırpımı AI isteğinden sonra değişti", 409)
        if (request["request_id"], row["cue_id"]) in imported:
            continue  # Idempotent retry: never duplicate an imported proposal.
        proposal_id = uuid.uuid4().hex
        source, trans = row["source_review"], row["translation_review"]
        event = {
            "schema_version": 1, "event_type": "proposal", "proposal_id": proposal_id,
            "request_id": request["request_id"], "job_id": info["manifest"]["job_id"],
            "cue_id": row["cue_id"], "manifest_sha256": info["manifest_sha256"],
            "srt_sha256": info["srt_sha256"], "crop_sha256_local": local["crop_sha256_local"],
            "start_ms": row["start_ms"], "end_ms": row["end_ms"],
            "source_review_event_id": local["source_review_event_id"],
            "source_language": request["source_language"], "target_language": request["target_language"],
            "verified_source_text": local["verified_source_text"],
            "draft_translation": local["draft_translation"],
            "source_review": source, "translation_review": trans,
            "decision": "pending", "decision_translation": None,
            "reviewer": "local_user", "created_at": now,
        }
        _append(log, event)
        added.append(proposal_id)
        imported.add((request["request_id"], row["cue_id"]))
    return {"count": len(added), "proposal_ids": added, "already_imported": len(rows) - len(added)}


def proposal_status(review_id: str) -> dict:
    info = _info(review_id)
    _count, events = _read_events(info["pack"] / "translation-review-events.jsonl")
    cues = {cue["cue_id"]: cue for cue in info["cues"]}
    accepted_sources = _verified_sources(info)
    proposals, seen = {}, set()
    for event in events:
        if not _valid_event(event) or event["event_type"] != "proposal":
            continue
        pid, cue_id = event["proposal_id"], event["cue_id"]
        cue = cues.get(cue_id)
        link = accepted_sources.get(cue_id)
        if (pid in seen or not cue or not link or event["job_id"] != info["manifest"]["job_id"] or
                event["manifest_sha256"] != info["manifest_sha256"] or
                event["srt_sha256"] != info["srt_sha256"] or
                event["crop_sha256_local"] != cue.get("crop_sha256_local") or
                event["source_review_event_id"] != link["event"]["event_id"] or
                event["verified_source_text"] != link["event"]["verified_source_text"]):
            continue
        if ocr_review._verify_crop(info, cue) is None:
            continue
        seen.add(pid)
        proposals[pid] = event
    # Apply only valid append-only decision events, in order.
    decision_ids = set()
    for event in events:
        if not _valid_event(event) or event["event_type"] != "decision":
            continue
        proposal = proposals.get(event["proposal_id"])
        if (proposal and event.get("job_id") == proposal["job_id"] and
                event.get("cue_id") == proposal["cue_id"] and
                event.get("manifest_sha256") == proposal["manifest_sha256"] and
                event.get("srt_sha256") == proposal["srt_sha256"] and
                event.get("crop_sha256_local") == proposal["crop_sha256_local"] and
                event["event_id"] not in decision_ids and
                event["decision"] in ("accepted", "edited", "rejected") and
                "decision_event_id" not in proposal):
            decision_ids.add(event["event_id"])
            proposal["decision"] = event["decision"]
            proposal["decision_translation"] = event["approved_translation"]
            proposal["decision_event_id"] = event["event_id"]
            proposal["decision_created_at"] = event["created_at"]
    return {"proposals": list(proposals.values()), "warnings": []}


def sync_translation_memory(review_id: str) -> dict:
    """Atomically rebuild the TM snapshot from accepted decision journal events.

    The append-only translation review journal is authoritative. A failed file
    projection is safe to retry at any time and cannot lose the user decision.
    """
    with _LOCK:
        info = _info(review_id)
        proposals = proposal_status(review_id)["proposals"]
        rows = []
        for proposal in proposals:
            if proposal.get("decision") not in ("accepted", "edited"):
                continue
            decision_event_id = proposal.get("decision_event_id")
            if not isinstance(decision_event_id, str) or not re.fullmatch(r"[a-f0-9]{32}", decision_event_id):
                continue
            rows.append({
                "schema_version": 1,
                "memory_id": hashlib.sha256(
                    ("translation-memory-v1:" + decision_event_id).encode("ascii")).hexdigest()[:32],
                "source_review_event_id": proposal["source_review_event_id"],
                "verified_source_text": proposal["verified_source_text"],
                "approved_translation": proposal["decision_translation"],
                "source_language": proposal["source_language"],
                "target_language": proposal["target_language"],
                "cue_id": proposal["cue_id"], "job_id": proposal["job_id"],
                "proposal_id": proposal["proposal_id"],
                "decision_event_id": decision_event_id,
                "created_at": proposal["decision_created_at"],
            })
        payload = b"".join(_json_bytes(row) for row in rows)
        if len(payload) > MAX_EVENTS_BYTES or len(rows) > MAX_EVENTS:
            raise ocr_review.ReviewError("Onaylı çeviri belleği sınırları aşıyor", 413)
        target = info["pack"] / "translation-memory-v1.jsonl"
        if target.is_symlink():
            raise ocr_review.ReviewError("Çeviri belleği sembolik bağlantı olamaz", 403)
        if target.exists():
            current = ocr_review._read_bounded(target, MAX_EVENTS_BYTES, "Çeviri belleği")
            if current == payload:
                return {"status": "ready", "entry_count": len(rows)}
        fd, tmp_name = tempfile.mkstemp(prefix=".translation-memory-", dir=info["pack"])
        try:
            os.chmod(tmp_name, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            Path(tmp_name).replace(target)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return {"status": "ready", "entry_count": len(rows)}


def _valid_event(event: dict) -> bool:
    if (type(event.get("schema_version")) is not int or event["schema_version"] != 1 or
            not isinstance(event.get("event_type"), str) or
            not isinstance(event.get("created_at"), str) or
            event.get("reviewer") != "local_user"):
        return False
    try:
        timestamp = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return False
    except ValueError:
        return False
    if event["event_type"] == "proposal":
        required = {"schema_version", "event_type", "proposal_id", "request_id", "job_id", "cue_id",
                    "manifest_sha256", "srt_sha256", "crop_sha256_local", "source_review_event_id",
                    "start_ms", "end_ms",
                    "source_language", "target_language", "verified_source_text", "draft_translation",
                    "source_review", "translation_review", "decision", "decision_translation", "reviewer", "created_at"}
        return (set(event) == required and all(isinstance(event.get(k), str) and re.fullmatch(r"[a-f0-9]{32}", event[k])
                for k in ("proposal_id", "request_id", "job_id", "source_review_event_id")) and
                isinstance(event.get("cue_id"), str) and
                type(event.get("start_ms")) is int and type(event.get("end_ms")) is int and
                event["start_ms"] >= 0 and event["end_ms"] > event["start_ms"] and
                all(isinstance(event.get(k), str) and re.fullmatch(r"[a-f0-9]{64}", event[k])
                    for k in ("manifest_sha256", "srt_sha256", "crop_sha256_local")) and
                isinstance(event.get("source_language"), str) and isinstance(event.get("target_language"), str) and
                _valid_text(event.get("verified_source_text")) and _valid_text(event.get("draft_translation")) and
                event.get("decision") == "pending" and event.get("decision_translation") is None and
                _source_schema(event.get("source_review")) and _translation_schema(event.get("translation_review")) and
                (event["source_review"]["status"] != "confirmed" or
                 (event["translation_review"]["status"] == "deferred" or
                  event["source_review"]["observed_text"] == event["verified_source_text"])) and
                (event["source_review"]["status"] != "correction_proposed" or
                 event["source_review"]["proposed_source_text"] != event["verified_source_text"]) and
                (event["source_review"]["status"] == "confirmed" or
                 event["translation_review"]["status"] == "deferred"))
    if event["event_type"] == "decision":
        return (set(event) == {"schema_version", "event_type", "event_id", "proposal_id", "job_id", "cue_id",
                              "manifest_sha256", "srt_sha256", "crop_sha256_local", "decision",
                              "approved_translation", "reviewer", "created_at"} and
                isinstance(event.get("event_id"), str) and re.fullmatch(r"[a-f0-9]{32}", event["event_id"]) and
                isinstance(event.get("proposal_id"), str) and re.fullmatch(r"[a-f0-9]{32}", event["proposal_id"]) and
                isinstance(event.get("job_id"), str) and re.fullmatch(r"[a-f0-9]{32}", event["job_id"]) and
                isinstance(event.get("cue_id"), str) and
                all(isinstance(event.get(k), str) and re.fullmatch(r"[a-f0-9]{64}", event[k])
                    for k in ("manifest_sha256", "srt_sha256", "crop_sha256_local")) and
                event.get("decision") in ("accepted", "edited", "rejected") and
                ((event["decision"] == "rejected" and event.get("approved_translation") is None) or
                 (event["decision"] in ("accepted", "edited") and _valid_text(event.get("approved_translation")))))
    return False


def _source_schema(source: object) -> bool:
    return (isinstance(source, dict) and set(source) == {"status", "observed_text", "proposed_source_text", "reason"} and
            source.get("status") in ("confirmed", "correction_proposed", "uncertain") and
            _valid_text(source.get("observed_text"), allow_empty=True) and
            _valid_text(source.get("reason")) and
            ((source["status"] == "correction_proposed" and _valid_text(source.get("proposed_source_text"))) or
             (source["status"] != "correction_proposed" and source.get("proposed_source_text") is None)))


def _translation_schema(trans: object) -> bool:
    return (isinstance(trans, dict) and set(trans) == {"status", "issues", "proposed_translation", "reason"} and
            trans.get("status") in ("reviewed", "deferred") and isinstance(trans.get("issues"), list) and
            _valid_text(trans.get("reason")) and
            all(isinstance(issue, str) and issue in ISSUES for issue in trans["issues"]) and
            len(set(trans["issues"])) == len(trans["issues"]) and
            ("none" not in trans["issues"] or len(trans["issues"]) == 1) and
            ((trans["status"] == "reviewed" and _valid_text(trans.get("proposed_translation"))) or
             (trans["status"] == "deferred" and trans.get("proposed_translation") is None)))


def decide(review_id: str, proposal_id: str, decision: str, edited_translation: object = None) -> dict:
    with _LOCK:
        return _decide_locked(review_id, proposal_id, decision, edited_translation)


def _decide_locked(review_id: str, proposal_id: str, decision: str,
                    edited_translation: object = None) -> dict:
    info = _info(review_id)
    current = proposal_status(review_id)["proposals"]
    proposal = next((item for item in current if item["proposal_id"] == proposal_id), None)
    if not proposal:
        raise ocr_review.ReviewError("Öneri bu paketle eşleşmiyor, güncel değil veya kaynak doğrulaması kaldırılmış", 409)
    if proposal["decision"] != "pending":
        raise ocr_review.ReviewError("Bu öneri için zaten karar verilmiş", 409)
    if decision not in ("accepted", "edited", "rejected"):
        raise ocr_review.ReviewError("Karar accept/edit/reject olmalı", 400)
    if decision == "rejected":
        approved = None
    elif decision == "accepted":
        if proposal["translation_review"]["status"] != "reviewed":
            raise ocr_review.ReviewError("Kaynak belirsiz veya çeviri ertelenmiş; bu öneri kabul edilemez", 409)
        approved = proposal["translation_review"]["proposed_translation"]
    else:
        if proposal["translation_review"]["status"] != "reviewed" or not _valid_text(edited_translation):
            raise ocr_review.ReviewError("Düzenlenmiş çeviri boş/geçersiz veya kaynak doğrulanmamış", 400)
        approved = edited_translation.strip()
    record = {"schema_version": 1, "event_type": "decision", "event_id": uuid.uuid4().hex,
              "proposal_id": proposal_id, "job_id": info["manifest"]["job_id"],
              "cue_id": proposal["cue_id"], "manifest_sha256": info["manifest_sha256"],
              "srt_sha256": info["srt_sha256"], "crop_sha256_local": proposal["crop_sha256_local"],
              "decision": decision, "approved_translation": approved,
              "reviewer": "local_user", "created_at": _now()}
    # Commit the user's decision first. This append-only journal is the source
    # of truth; the translation-memory file is a recoverable derived snapshot.
    _append(info["pack"] / "translation-review-events.jsonl", record)
    try:
        record["translation_memory_sync"] = sync_translation_memory(review_id)
    except Exception as exc:
        record["translation_memory_sync"] = {
            "status": "pending", "message": "Karar kaydedildi; çeviri belleği görünümü daha sonra onarılabilir.",
            "error_type": type(exc).__name__,
        }
    return record
