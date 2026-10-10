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
import subprocess
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
REPO_ROOT = Path(__file__).resolve().parent


def _settings():
    """Read mandatory configuration and fail closed on path/ACL/identity checks."""
    retention = os.environ.get("H2S_CONTRIB_RETENTION_DAYS", "").strip()
    if not retention:
        raise RuntimeError("H2S_CONTRIB_RETENTION_DAYS açıkça ayarlanmalı")
    try:
        retention_days = int(retention)
    except ValueError as exc:
        raise RuntimeError("H2S_CONTRIB_RETENTION_DAYS tam sayı olmalı") from exc
    if not 1 <= retention_days <= 3650:
        raise RuntimeError("H2S_CONTRIB_RETENTION_DAYS 1..3650 aralığında olmalı")
    raw_db = os.environ.get("H2S_CONTRIB_DB", "").strip()
    if not raw_db:
        raise RuntimeError("H2S_CONTRIB_DB açıkça ayarlanmalı")
    db = Path(raw_db).expanduser()
    if not db.is_absolute():
        raise RuntimeError("H2S_CONTRIB_DB mutlak yol olmalı")
    db = db.resolve(strict=False)
    try:
        db.relative_to(REPO_ROOT)
    except ValueError:
        pass
    else:
        raise RuntimeError("H2S_CONTRIB_DB depo/kaynak ağacının dışında olmalı")
    service_account = os.environ.get("H2S_CONTRIB_SERVICE_ACCOUNT", "").strip()
    purge_account = os.environ.get("H2S_CONTRIB_PURGE_ACCOUNT", "").strip()
    forbidden_accounts = {
        "localsystem", "local system", "nt authority\\system", "system",
        "nt authority\\localservice", "nt authority\\networkservice",
        "builtin\\administrators", "administrators", "builtin\\users", "users",
        "everyone", "authenticated users",
    }
    if not service_account or service_account.casefold() in forbidden_accounts:
        raise RuntimeError("Özel düşük yetkili H2S_CONTRIB_SERVICE_ACCOUNT zorunlu; LocalSystem kabul edilmez")
    if not purge_account or purge_account.casefold() in forbidden_accounts:
        raise RuntimeError("Ayrı düşük yetkili H2S_CONTRIB_PURGE_ACCOUNT zorunlu")
    if service_account.casefold() == purge_account.casefold():
        raise RuntimeError("API ve retention purge için ayrı hesaplar gerekli")
    config = (db, retention_days, service_account, purge_account)
    # Called only after the security helpers are defined at module startup.
    validator = globals().get("_validate_storage_security")
    if validator is not None:
        validator(config=config, require_service_identity=__name__ != "__main__")
    return config


_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$target = $env:H2S_ACL_TARGET
$serviceAccount = $env:H2S_CONTRIB_SERVICE_ACCOUNT
$purgeAccount = $env:H2S_CONTRIB_PURGE_ACCOUNT
$acl = Get-Acl -LiteralPath $target
$sidType = [System.Security.Principal.SecurityIdentifier]
$serviceSid = ([System.Security.Principal.NTAccount]$serviceAccount).Translate($sidType).Value
$purgeSid = ([System.Security.Principal.NTAccount]$purgeAccount).Translate($sidType).Value
$ownerSid = ([System.Security.Principal.NTAccount]$acl.Owner).Translate($sidType).Value
$rules = @()
foreach ($rule in $acl.Access) {
  $sid = $rule.IdentityReference.Translate($sidType).Value
  $rules += [pscustomobject]@{
    sid = $sid
    type = $rule.AccessControlType.ToString()
    rights = $rule.FileSystemRights.ToString()
    inherited = [bool]$rule.IsInherited
  }
}
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
[pscustomobject]@{
  owner = $ownerSid
  protected = [bool]$acl.AreAccessRulesProtected
  service = $serviceSid
  purge = $purgeSid
  current = $currentSid
  isDirectory = [bool](Get-Item -LiteralPath $target).PSIsContainer
  rules = @($rules)
} | ConvertTo-Json -Compress -Depth 5
"""


def _acl_snapshot(path: Path) -> dict:
    if os.name != "nt":
        raise RuntimeError("Windows ACL/owner doğrulaması yapılamıyor; fail-closed")
    env = os.environ.copy()
    env["H2S_ACL_TARGET"] = str(path)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _ACL_SCRIPT],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, env=env,
        )
        snapshot = json.loads(result.stdout)
    except Exception as exc:
        raise RuntimeError(f"DB ACL/owner güvenlik doğrulaması başarısız ({type(exc).__name__})") from None
    if isinstance(snapshot, dict) and isinstance(snapshot.get("rules"), dict):
        snapshot["rules"] = [snapshot["rules"]]
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("rules"), list):
        raise RuntimeError("DB ACL bilgisi okunamadı; fail-closed")
    return snapshot


def _check_acl(path: Path, *, is_directory: bool, required_owners: set[str],
               service_sid: str, purge_sid: str, require_current: str | None = None):
    snap = _acl_snapshot(path)
    system_sid, admin_sid = "S-1-5-18", "S-1-5-32-544"
    allowed = {service_sid, purge_sid, system_sid, admin_sid}
    if snap.get("owner") not in required_owners:
        raise RuntimeError("DB dizin/dosya sahibi izinli dedicated data hesabı değil")
    if is_directory and not snap.get("protected"):
        raise RuntimeError("DB dizininde ACL inheritance kapalı olmalı")
    rules = snap["rules"]
    access = {}
    for rule in rules:
        sid = rule.get("sid")
        if sid not in allowed or rule.get("type") != "Allow":
            raise RuntimeError("DB ACL içinde beklenmeyen principal veya deny ACE var")
        if is_directory and rule.get("inherited"):
            raise RuntimeError("DB dizini yalnız açık ACL girdileri kullanmalı")
        rights = rule.get("rights", "")
        access.setdefault(sid, []).append(rights)
    def has(sid, required):
        return any(required in rights or "FullControl" in rights for rights in access.get(sid, []))
    if not has(service_sid, "Modify") or not has(purge_sid, "Modify"):
        raise RuntimeError("Servis ve purge hesaplarına DB için Modify ACL gerekli")
    if not has(system_sid, "FullControl") or not has(admin_sid, "FullControl"):
        raise RuntimeError("SYSTEM ve Administrators için FullControl ACL gerekli")
    if require_current and snap.get("current") != require_current:
        raise RuntimeError("API WSGI süreci beklenen dedicated service account altında çalışmıyor")


def _validate_storage_security(*, config, require_service_identity: bool):
    db, _days, _service, _purge = config
    parent = db.parent
    if not parent.is_dir():
        raise RuntimeError("H2S_CONTRIB_DB üst dizini önceden oluşturulmalı ve ACL ile korunmalı")
    # Translate account names once using the parent ACL snapshot.
    snap = _acl_snapshot(parent)
    service_sid, purge_sid = snap.get("service"), snap.get("purge")
    if not service_sid or not purge_sid:
        raise RuntimeError("Dedicated account SID'leri çözümlenemedi; fail-closed")
    broad_or_privileged_sids = {
        "S-1-5-18", "S-1-5-19", "S-1-5-20", "S-1-5-32-544",
        "S-1-5-32-545", "S-1-5-11", "S-1-1-0",
    }
    if service_sid == purge_sid or service_sid in broad_or_privileged_sids or purge_sid in broad_or_privileged_sids:
        raise RuntimeError("API/purge kimlikleri ayrı ve düşük yetkili hesaplar olmalı")
    current = service_sid if require_service_identity else None
    _check_acl(parent, is_directory=True, required_owners={service_sid},
               service_sid=service_sid, purge_sid=purge_sid, require_current=current)
    if not require_service_identity and snap.get("current") not in {
            service_sid, purge_sid, "S-1-5-18", "S-1-5-32-544"}:
        raise RuntimeError("Yönetim komutu yalnız API/purge hesabı veya Administrators altında çalıştırılabilir")
    for child in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if child.exists():
            owners = ({service_sid, purge_sid} if child == db else
                      {service_sid, purge_sid, "S-1-5-18", "S-1-5-32-544"})
            _check_acl(child, is_directory=False, required_owners=owners,
                       service_sid=service_sid, purge_sid=purge_sid, require_current=current)


# Fail at WSGI import/startup if path, policy, owner, ACL, or process identity
# cannot be established. CLI admin commands receive ACL checks but may run as
# the dedicated purge account or an administrator.
_CONFIG = _settings()


def _db():
    path = _CONFIG[0]
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA secure_delete=ON")
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
    retention_days = _CONFIG[1]
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
    days = _CONFIG[1]
    cutoff = datetime.fromtimestamp(time.time() - days * 86400, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with _connection() as con:
        cur = con.execute("DELETE FROM contributions WHERE received_at < ?", (cutoff,))
        removed = cur.rowcount
    _compact_after_purge()
    return removed


def _compact_after_purge():
    """Checkpoint/truncate WAL and vacuum after retention deletes commit."""
    con = sqlite3.connect(_CONFIG[0], timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA secure_delete=ON")
        checkpoint = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and checkpoint[0] != 0:
            raise RuntimeError("SQLite WAL checkpoint busy; retry the scheduled purge")
        con.execute("VACUUM")
    finally:
        con.close()


def _cli(argv=None):
    parser = argparse.ArgumentParser(description="hardsub2srt contribution API administration")
    parser.add_argument("command", choices=("issue-token", "revoke-token", "purge"))
    args = parser.parse_args(argv)
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
