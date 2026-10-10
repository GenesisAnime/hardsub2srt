"""Optional minimized-metrics API. Run behind a TLS reverse proxy.

The application binds only to 127.0.0.1. Raw content and unknown fields are
rejected. Tokens are random bearer secrets; only SHA-256 digests are stored.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

import contribution_client as contract

MAX_BODY = 16 * 1024
RATE_LIMIT = 60
RATE_WINDOW = 3600
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
_rates: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
logging.getLogger("werkzeug").disabled = True


def _settings():
    retention = os.environ.get("H2S_CONTRIB_RETENTION_DAYS", "").strip()
    if not retention:
        raise RuntimeError("H2S_CONTRIB_RETENTION_DAYS açıkça ayarlanmalı")
    try:
        retention_days = int(retention)
    except ValueError as exc:
        raise RuntimeError("H2S_CONTRIB_RETENTION_DAYS tam sayı olmalı") from exc
    if not 1 <= retention_days <= 3650:
        raise RuntimeError("H2S_CONTRIB_RETENTION_DAYS 1..3650 aralığında olmalı")
    db = os.environ.get("H2S_CONTRIB_DB", "").strip()
    if not db:
        raise RuntimeError("H2S_CONTRIB_DB açıkça ayarlanmalı")
    return Path(db), retention_days


# Fail at WSGI import/startup if operator has not set a real retention policy
# and an explicit out-of-repository database path.
_settings()


def _db():
    path, _ = _settings()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript("""
      CREATE TABLE IF NOT EXISTS tokens (
        token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, revoked_at TEXT
      );
      CREATE TABLE IF NOT EXISTS contributions (
        submission_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
        created_at TEXT NOT NULL, received_at TEXT NOT NULL, consent_version TEXT NOT NULL,
        cer_consent INTEGER NOT NULL, payload_json TEXT NOT NULL,
        FOREIGN KEY(token_hash) REFERENCES tokens(token_hash)
      );
      CREATE INDEX IF NOT EXISTS contribution_created_idx ON contributions(received_at);
    """)
    return con


@contextmanager
def _connection():
    con = _db()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _auth(con):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or len(header) > 512:
        return None
    raw = header[7:].strip()
    if len(raw) < 40 or len(raw) > 256:
        return None
    digest = _token_hash(raw)
    row = con.execute("SELECT token_hash FROM tokens WHERE token_hash=? AND revoked_at IS NULL", (digest,)).fetchone()
    return digest if row else None


def _rate_ok(token_hash):
    now = time.monotonic()
    with _rate_lock:
        calls = [t for t in _rates.get(token_hash, []) if now - t < RATE_WINDOW]
        if len(calls) >= RATE_LIMIT:
            _rates[token_hash] = calls
            return False
        calls.append(now)
        _rates[token_hash] = calls
        return True


def _error(message, status):
    return jsonify({"error": message}), status


@app.after_request
def _privacy_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/healthz")
def healthz():
    try:
        _settings()
        with _connection() as con:
            con.execute("SELECT 1").fetchone()
        return jsonify({"status": "ok"})
    except Exception:
        return _error("service unavailable", 503)


@app.post("/v1/contributions")
def create_contribution():
    if request.content_length is not None and request.content_length > MAX_BODY:
        return _error("payload too large", 413)
    if request.mimetype != "application/json":
        return _error("application/json required", 415)
    raw = request.get_data(cache=False, as_text=False)
    if len(raw) > MAX_BODY:
        return _error("payload too large", 413)
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        clean = contract.validate_payload(data)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return _error("payload rejected by schema", 400)
    if request.headers.get("Idempotency-Key") != clean["submission_id"]:
        return _error("idempotency key mismatch", 400)
    if request.headers.get("X-H2S-Metrics-Consent") != "v1":
        return _error("explicit metrics consent required", 400)
    _db_path, retention_days = _settings()
    if request.headers.get("X-H2S-Retention-Policy") != str(retention_days):
        return _error("client retention notice does not match server policy", 409)
    has_cer = "cer" in clean["metrics"]
    cer_consent = request.headers.get("X-H2S-CER-Consent") == "v1"
    if has_cer and not cer_consent:
        return _error("separate CER consent required", 400)
    if not has_cer and request.headers.get("X-H2S-CER-Consent"):
        return _error("unexpected CER consent header", 400)
    try:
        with _connection() as con:
            token_hash = _auth(con)
            if token_hash is None:
                return _error("unauthorized", 401)
            if not _rate_ok(token_hash):
                return _error("rate limit exceeded", 429)
            canonical_payload = json.dumps(clean, separators=(",", ":"), allow_nan=False)
            existing = con.execute("SELECT token_hash,payload_json FROM contributions WHERE submission_id=?", (clean["submission_id"],)).fetchone()
            if existing:
                if hmac.compare_digest(existing["token_hash"], token_hash) and hmac.compare_digest(existing["payload_json"], canonical_payload):
                    return jsonify({"submission_id": clean["submission_id"], "status": "already_received"}), 200
                return _error("submission id conflict", 409)
            received = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            con.execute("INSERT INTO contributions VALUES(?,?,?,?,?,?,?)", (
                clean["submission_id"], token_hash, clean["created_at"], received, "v1", int(cer_consent),
                canonical_payload,
            ))
        return jsonify({"submission_id": clean["submission_id"], "status": "accepted"}), 201
    except Exception:
        # Do not log request bodies, tokens, IP addresses, or database exception text.
        app.logger.error("contribution storage failed")
        return _error("storage unavailable", 503)


@app.delete("/v1/contributions/<submission_id>")
def retract_contribution(submission_id):
    try:
        normalized = str(uuid.UUID(submission_id))
    except (ValueError, AttributeError):
        return _error("invalid submission id", 400)
    with _connection() as con:
        token_hash = _auth(con)
        if token_hash is None:
            return _error("unauthorized", 401)
        if not _rate_ok(token_hash):
            return _error("rate limit exceeded", 429)
        cur = con.execute("DELETE FROM contributions WHERE submission_id=? AND token_hash=?", (normalized, token_hash))
        if cur.rowcount:
            return jsonify({"status": "deleted"}), 200
        return _error("not found", 404)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def purge_expired() -> int:
    _db_path, days = _settings()
    cutoff = datetime.fromtimestamp(time.time() - days * 86400, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with _connection() as con:
        cur = con.execute("DELETE FROM contributions WHERE received_at < ?", (cutoff,))
        return cur.rowcount


def _cli(argv=None):
    parser = argparse.ArgumentParser(description="hardsub2srt contribution API administration")
    parser.add_argument("command", choices=("issue-token", "revoke-token", "purge"))
    args = parser.parse_args(argv)
    _settings()
    if args.command == "issue-token":
        token = "h2s_" + secrets.token_urlsafe(36)
        with _connection() as con:
            con.execute("INSERT INTO tokens(token_hash,created_at) VALUES(?,?)", (_token_hash(token), datetime.now(timezone.utc).isoformat()))
        print("Token yalnızca şimdi gösteriliyor; güvenli parola yöneticisine kaydedin:\n" + token)
    elif args.command == "revoke-token":
        import getpass
        token = getpass.getpass("İptal edilecek token: ")
        with _connection() as con:
            cur = con.execute("UPDATE tokens SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (datetime.now(timezone.utc).isoformat(), _token_hash(token)))
        print("revoked" if cur.rowcount else "not found")
    elif args.command == "purge":
        print(f"purged={purge_expired()}")


if __name__ == "__main__":
    _cli()
