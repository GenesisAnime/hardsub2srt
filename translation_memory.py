"""Local, project-scoped translation memory and terminology lexicon.

This module is deliberately separate from OCR post-fix and ``kullanici-sozlugu``.
It stores only explicit user actions, reads/writes no network service, and never
rewrites a translation unless the user explicitly applies a displayed draft.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

import ocr_review


SCHEMA_VERSION = 1
MAX_EVENTS = 50_000
MAX_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 128 * 1024
MAX_TEXT = 20_000
MAX_SCOPE = 80
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_text(value: object, *, max_len: int = MAX_TEXT) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= max_len and "\x00" not in value


def _norm_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _scope(project_key: object, source_language: object, target_language: object) -> dict:
    if not isinstance(project_key, str):
        raise ocr_review.ReviewError("Proje/seri anahtarı gerekli (ör. one-piece)", 400)
    key = unicodedata.normalize("NFKC", project_key).strip().casefold()
    if not re.fullmatch(r"[\w.-]{1,80}", key, flags=re.UNICODE) or key in {".", ".."}:
        raise ocr_review.ReviewError("Proje anahtarı 1–80 harf/rakam veya . _ - içermeli", 400)
    langs = []
    for value, label in ((source_language, "Kaynak"), (target_language, "Hedef")):
        if not isinstance(value, str):
            raise ocr_review.ReviewError(f"{label} dil etiketi gerekli", 400)
        tag = value.strip().replace("_", "-").lower()
        if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}", tag):
            raise ocr_review.ReviewError(f"{label} dil etiketi geçersiz (ör. ja, tr)", 400)
        langs.append(tag)
    if langs[0] == langs[1]:
        raise ocr_review.ReviewError("Kaynak ve hedef dil aynı olamaz", 400)
    body = {"project_key": key, "source_language": langs[0], "target_language": langs[1]}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**body, "scope_id": digest}


def _root() -> Path:
    if os.name == "nt":
        parent = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        parent = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    root = parent / "hardsub2srt" / "translation-memory-v1"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise ocr_review.ReviewError("Yerel çeviri belleği dizini güvenli değil", 403)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root.resolve(strict=True)


def _paths(scope: dict) -> tuple[Path, Path, Path]:
    root = _root()
    stem = "scope-" + scope["scope_id"]
    events = root / f"{stem}-events-v1.jsonl"
    translation_store = root / f"{stem}-translation-memory-v1.jsonl"
    lexicon_store = root / f"{stem}-lexicon-v1.jsonl"
    for path in (events, translation_store, lexicon_store):
        if path.is_symlink():
            raise ocr_review.ReviewError("Yerel çeviri belleği dosyası sembolik bağlantı olamaz", 403)
        if path.exists() and (not path.is_file() or not ocr_review._contained(path.resolve(strict=True), root)):
            raise ocr_review.ReviewError("Yerel çeviri belleği dosyası dizin dışına yönleniyor", 403)
    return events, translation_store, lexicon_store


def _json_line(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _valid_event(event: object, expected_scope: dict) -> bool:
    if not isinstance(event, dict) or event.get("schema_version") != SCHEMA_VERSION:
        return False
    common = {"schema_version", "event_type", "event_id", "scope_id", "project_key",
              "source_language", "target_language", "reviewer", "created_at"}
    if (not all(isinstance(event.get(k), str) for k in common - {"schema_version"}) or
            type(event.get("schema_version")) is not int or event["schema_version"] != SCHEMA_VERSION or
            event.get("scope_id") != expected_scope["scope_id"] or
            event.get("project_key") != expected_scope["project_key"] or
            event.get("source_language") != expected_scope["source_language"] or
            event.get("target_language") != expected_scope["target_language"] or
            event.get("reviewer") != "local_user" or
            not re.fullmatch(r"[a-f0-9]{32}", event.get("event_id", ""))):
        return False
    try:
        stamp = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return False
    except (ValueError, TypeError):
        return False
    kind = event.get("event_type")
    if kind == "translation_add":
        required = common | {"verified_source_text", "approved_translation", "source_review_event_id",
                             "decision_event_id", "proposal_id", "job_id", "cue_id", "memory_kind"}
        return (set(event) == required and all(re.fullmatch(r"[a-f0-9]{32}", event.get(k, ""))
                for k in ("source_review_event_id", "decision_event_id", "proposal_id", "job_id")) and
                _valid_text(event.get("cue_id"), max_len=256) and
                _valid_text(event.get("verified_source_text")) and
                _valid_text(event.get("approved_translation")) and event.get("memory_kind") == "translation")
    if kind == "lexicon_add":
        required = common | {"source_term", "target_term", "memory_kind"}
        return (set(event) == required and event.get("memory_kind") == "lexicon" and
                _valid_text(event.get("source_term"), max_len=500) and
                _valid_text(event.get("target_term"), max_len=500))
    if kind == "rollback":
        return (set(event) == common | {"undo_event_id"} and
                re.fullmatch(r"[a-f0-9]{32}", event.get("undo_event_id", "")) is not None)
    return False


def _read(scope: dict) -> tuple[list[dict], list[dict]]:
    event_path, _translation_store, _lexicon_store = _paths(scope)
    if not event_path.exists():
        return [], []
    if event_path.stat().st_size > MAX_BYTES:
        raise ocr_review.ReviewError("Bu kapsamın yerel bellek günlüğü boyut sınırını aşıyor", 413)
    events = []
    with event_path.open("rb") as stream:
        while True:
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                raise ocr_review.ReviewError("Bellek günlüğünde bozuk/aşırı uzun kayıt var; yazma durduruldu", 409)
            if len(events) >= MAX_EVENTS:
                raise ocr_review.ReviewError("Bellek günlüğü olay sınırını aştı", 413)
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ocr_review.ReviewError("Bellek günlüğü bozuk; kısmi sonuç kullanılmadı", 409) from exc
            if not _valid_event(event, scope):
                raise ocr_review.ReviewError("Bellek günlüğünde geçersiz olay var; kısmi sonuç kullanılmadı", 409)
            events.append(event)
    active = {}
    additions = set()
    for event in events:
        if event["event_type"] in ("translation_add", "lexicon_add"):
            if event["event_id"] in additions:
                continue
            additions.add(event["event_id"])
            active[event["event_id"]] = event
        elif event["event_type"] == "rollback":
            target = event["undo_event_id"]
            if target in active:
                del active[target]
    return events, list(active.values())


def _append(scope: dict, event: dict) -> None:
    if not _valid_event(event, scope):
        raise ocr_review.ReviewError("Bellek olayı şeması geçersiz", 500)
    path, _translation_store, _lexicon_store = _paths(scope)
    line = _json_line(event)
    if len(line) > MAX_LINE_BYTES:
        raise ocr_review.ReviewError("Bellek olayı satır sınırını aşıyor", 413)
    size = path.stat().st_size if path.exists() else 0
    if size + len(line) > MAX_BYTES:
        raise ocr_review.ReviewError("Bellek günlüğü boyut sınırına ulaştı", 413)
    events, _active = _read(scope)
    if len(events) >= MAX_EVENTS:
        raise ocr_review.ReviewError("Bellek günlüğü olay sınırına ulaştı", 413)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def _active_by_id(scope: dict) -> dict[str, dict]:
    _events, active = _read(scope)
    return {event["event_id"]: event for event in active}


def _invalidate_projection(scope: dict) -> None:
    _events, translation_store, lexicon_store = _paths(scope)
    for projection in (translation_store, lexicon_store):
        if projection.exists():
            projection.unlink()


def _atomic_projection(target: Path, payload: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".tm-snapshot-", dir=target.parent)
    try:
        os.chmod(name, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(target)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def _project(scope: dict) -> dict:
    events, active = _read(scope)
    scope_record = {k: scope[k] for k in ("project_key", "source_language", "target_language", "scope_id")}
    translation_rows = [e for e in active if e["event_type"] == "translation_add"]
    lexicon_rows = [e for e in active if e["event_type"] == "lexicon_add"]
    translation_payload = b"".join(_json_line(e) for e in translation_rows)
    lexicon_payload = b"".join(_json_line(e) for e in lexicon_rows)
    if len(translation_payload) > MAX_BYTES or len(lexicon_payload) > MAX_BYTES:
        raise ocr_review.ReviewError("Bellek görünümü boyut sınırını aşıyor", 413)
    _events_path, translation_store, lexicon_store = _paths(scope)
    _atomic_projection(translation_store, translation_payload)
    _atomic_projection(lexicon_store, lexicon_payload)
    return {"translation_count": len(translation_rows), "lexicon_count": len(lexicon_rows),
            "event_count": len(events), "scope": scope_record}


def status(project_key: object, source_language: object, target_language: object) -> dict:
    scope = _scope(project_key, source_language, target_language)
    with _LOCK:
        events, active = _read(scope)
        _project(scope)
    return {"scope": scope, "entry_count": len(active), "event_count": len(events),
            "entries": [{"event_id": e["event_id"], "kind": e["memory_kind"],
                         "source": e.get("verified_source_text", e.get("source_term")),
                         "target": e.get("approved_translation", e.get("target_term")),
                         "created_at": e["created_at"]} for e in active]}


def _approved_translation_events(review_id: str) -> list[dict]:
    import translation_review

    rows = translation_review.proposal_status(review_id)["proposals"]
    eligible = []
    for row in rows:
        if (row.get("decision") not in ("accepted", "edited") or
                row.get("source_review", {}).get("status") != "confirmed" or
                not _valid_text(row.get("verified_source_text")) or
                not _valid_text(row.get("decision_translation"))):
            continue
        if not all(re.fullmatch(r"[a-f0-9]{32}", row.get(key, ""))
                   for key in ("source_review_event_id", "decision_event_id", "proposal_id", "job_id")):
            continue
        eligible.append(row)
    return eligible


def add_approved(review_id: str, project_key: object, source_language: object,
                 target_language: object, decision_event_ids: object) -> dict:
    scope = _scope(project_key, source_language, target_language)
    if not isinstance(decision_event_ids, list) or not decision_event_ids or len(decision_event_ids) > 500:
        raise ocr_review.ReviewError("Belleğe eklenecek 1–500 kabul edilmiş kayıt seçin", 400)
    if any(not isinstance(i, str) or not re.fullmatch(r"[a-f0-9]{32}", i) for i in decision_event_ids):
        raise ocr_review.ReviewError("Çeviri karar kimliği geçersiz", 400)
    if len(set(decision_event_ids)) != len(decision_event_ids):
        raise ocr_review.ReviewError("Aynı çeviri kararı birden fazla seçilmiş", 400)
    rows = _approved_translation_events(review_id)
    by_decision = {row["decision_event_id"]: row for row in rows}
    missing = set(decision_event_ids) - by_decision.keys()
    if missing:
        raise ocr_review.ReviewError("Seçilen kayıt artık etkin, görselle doğrulanmış bir kaynak kararına bağlı değil", 409)
    selected_rows = [by_decision[decision_id] for decision_id in decision_event_ids]
    if any(row["source_language"].lower() != scope["source_language"] or
           row["target_language"].lower() != scope["target_language"] for row in selected_rows):
        raise ocr_review.ReviewError("Kabul edilmiş çeviri seçilen dil çiftine uymuyor", 409)
    added, skipped = 0, 0
    with _LOCK:
        active = _active_by_id(scope)
        for decision_id, row in zip(decision_event_ids, selected_rows):
            if any(e.get("decision_event_id") == decision_id for e in active.values()):
                skipped += 1
                continue
            event = {"schema_version": SCHEMA_VERSION, "event_type": "translation_add",
                     "event_id": uuid.uuid4().hex, **{k: scope[k] for k in
                     ("scope_id", "project_key", "source_language", "target_language")},
                     "verified_source_text": row["verified_source_text"],
                     "approved_translation": row["decision_translation"],
                     "source_review_event_id": row["source_review_event_id"],
                     "decision_event_id": decision_id, "proposal_id": row["proposal_id"],
                     "job_id": row["job_id"], "cue_id": row["cue_id"],
                     "memory_kind": "translation", "reviewer": "local_user", "created_at": _now()}
            _append(scope, event)
            added += 1
        _project(scope)
    return {"added": added, "already_present": skipped, "scope": scope}


def add_lexicon(project_key: object, source_language: object, target_language: object,
                source_term: object, target_term: object) -> dict:
    scope = _scope(project_key, source_language, target_language)
    if not _valid_text(source_term, max_len=500) or not _valid_text(target_term, max_len=500):
        raise ocr_review.ReviewError("Kaynak ve hedef terim 1–500 karakter olmalı", 400)
    event = {"schema_version": SCHEMA_VERSION, "event_type": "lexicon_add", "event_id": uuid.uuid4().hex,
             **{k: scope[k] for k in ("scope_id", "project_key", "source_language", "target_language")},
             "source_term": source_term.strip(), "target_term": target_term.strip(),
             "memory_kind": "lexicon", "reviewer": "local_user", "created_at": _now()}
    with _LOCK:
        _append(scope, event)
        _project(scope)
    return {"event_id": event["event_id"], "scope": scope}


def rollback(project_key: object, source_language: object, target_language: object,
             event_id: object) -> dict:
    scope = _scope(project_key, source_language, target_language)
    if not isinstance(event_id, str) or not re.fullmatch(r"[a-f0-9]{32}", event_id):
        raise ocr_review.ReviewError("Geri alınacak kayıt kimliği geçersiz", 400)
    with _LOCK:
        events, active_rows = _read(scope)
        active = {event["event_id"]: event for event in active_rows}
        if event_id not in active:
            if any(event.get("event_type") == "rollback" and event.get("undo_event_id") == event_id
                   for event in events):
                return {"rolled_back": event_id, "already_rolled_back": True}
            raise ocr_review.ReviewError("Kayıt bulunamadı veya zaten geri alınmış", 409)
        event = {"schema_version": SCHEMA_VERSION, "event_type": "rollback", "event_id": uuid.uuid4().hex,
                 **{k: scope[k] for k in ("scope_id", "project_key", "source_language", "target_language")},
                 "undo_event_id": event_id, "reviewer": "local_user", "created_at": _now()}
        _append(scope, event)
        _project(scope)
    return {"event_id": event["event_id"], "rolled_back": event_id}


def suggest(review_id: str, cue_id: object, project_key: object, source_language: object,
            target_language: object, source_text: object, draft_translation: object = "") -> dict:
    scope = _scope(project_key, source_language, target_language)
    if not _valid_text(source_text):
        raise ocr_review.ReviewError("Görselle doğrulanmış kaynak metin gerekli", 400)
    if not isinstance(draft_translation, str) or len(draft_translation) > MAX_TEXT:
        raise ocr_review.ReviewError("Taslak çeviri geçersiz", 400)
    import translation_review
    info = translation_review._info(review_id)
    verified = translation_review._verified_sources(info, verify_crops=True)
    linked = verified.get(cue_id) if isinstance(cue_id, str) else None
    if not linked or linked["event"].get("verified_source_text") != source_text:
        raise ocr_review.ReviewError("Bellek araması yalnız bu pakette görselle doğrulanmış kaynak metinde yapılabilir", 409)
    with _LOCK:
        _events, active = _read(scope)
    exact = [e for e in active if e["event_type"] == "translation_add" and
             _norm_text(e["verified_source_text"]) == _norm_text(source_text)]
    targets = {_norm_text(e["approved_translation"]): e for e in exact}
    exact_result = ({"status": "suggestion", "translation": next(iter(targets.values()))["approved_translation"],
                     "count": len(exact)} if len(targets) == 1 else
                    {"status": "ambiguous", "candidate_count": len(targets)} if len(targets) > 1 else
                    {"status": "none"})

    terms: dict[str, list[dict]] = {}
    for event in active:
        if event["event_type"] == "lexicon_add":
            terms.setdefault(_norm_text(event["source_term"]), []).append(event)
    matches, collision_matches = [], []
    for normalized, candidates in terms.items():
        targets_for_term = {_norm_text(e["target_term"]): e for e in candidates}
        source_term = candidates[0]["source_term"]
        pattern = re.compile(r"(?<!\w)" + re.escape(source_term) + r"(?!\w)", re.IGNORECASE)
        if len(targets_for_term) != 1:
            collision_matches.extend(pattern.finditer(source_text))
            continue
        matches.extend((m.start(), m.end(), candidates[0], next(iter(targets_for_term.values()))["target_term"])
                       for m in pattern.finditer(source_text))
    matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))
    chosen, ambiguous = [], bool(collision_matches)
    for match in matches:
        if chosen and match[0] < chosen[-1][1]:
            if match[0:2] != chosen[-1][0:2] or _norm_text(match[3]) != _norm_text(chosen[-1][3]):
                ambiguous = True
            continue
        chosen.append(match)
    if ambiguous:
        lexicon = {"status": "ambiguous", "matches": []}
    elif chosen:
        output = source_text
        for start, end, _event, target in reversed(chosen):
            output = output[:start] + target + output[end:]
        lexicon = {"status": "suggestion", "translation": output,
                   "matches": [{"source": m[2]["source_term"], "target": m[3]} for m in chosen]}
    else:
        lexicon = {"status": "none", "matches": []}
    return {"scope": scope, "cue_id": cue_id, "source_review_event_id": linked["event"]["event_id"],
            "translation_memory": exact_result, "lexicon": lexicon, "current_draft": draft_translation}
