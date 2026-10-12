"""Synthetic contribution API auth, token expiry, migration, and request-boundary tests."""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import contribution_client
import ui_server


@unittest.skipUnless(os.name == "nt", "contribution API startup requires Windows ACL checks")
class ContributionTokenExpiryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_root = tempfile.mkdtemp(prefix="h2s-contrib-token-ttl-")
        cls.db_path = os.path.join(cls.temp_root, "contributions.sqlite3")
        cls.service_account = r"NT SERVICE\synthetic-contrib-api"
        cls.purge_account = "synthetic-host\\hardsub-purge"
        cls.service_sid = "S-1-5-80-111"
        cls.purge_sid = "S-1-5-80-222"
        cls.admin_sid = "S-1-5-32-544"
        cls.system_sid = "S-1-5-18"
        now = datetime.now(timezone.utc)

        # Simulate the old on-disk schema before importing the WSGI module.
        legacy = sqlite3.connect(cls.db_path)
        legacy.execute("CREATE TABLE tokens (token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, revoked_at TEXT)")
        cls.legacy_recent = "h2s_legacy_recent_" + secrets.token_urlsafe(36)
        cls.legacy_old = "h2s_legacy_old_" + secrets.token_urlsafe(36)
        cls.legacy_bad_date = "h2s_legacy_bad_date_" + secrets.token_urlsafe(36)
        from hashlib import sha256
        legacy.executemany("INSERT INTO tokens(token_hash,created_at) VALUES(?,?)", [
            (sha256(cls.legacy_recent.encode()).hexdigest(), (now - timedelta(days=1)).isoformat()),
            (sha256(cls.legacy_old.encode()).hexdigest(), (now - timedelta(days=20)).isoformat()),
            (sha256(cls.legacy_bad_date.encode()).hexdigest(), "not-a-date"),
        ])
        legacy.commit()
        legacy.close()

        def fake_powershell_acl(_args, *, env, **_kwargs):
            rules = [
                {"sid": cls.service_sid, "type": "Allow", "rights": 0x301BF, "inherited": False},
                {"sid": cls.purge_sid, "type": "Allow", "rights": 0x301BF, "inherited": False},
                {"sid": cls.system_sid, "type": "Allow", "rights": 0x1F01FF, "inherited": False},
                {"sid": cls.admin_sid, "type": "Allow", "rights": 0x1F01FF, "inherited": False},
            ]
            snapshot = {
                "owner": cls.service_sid, "protected": True,
                "service": cls.service_sid, "purge": cls.purge_sid,
                "current": cls.service_sid, "isDirectory": True, "rules": rules,
            }
            return SimpleNamespace(stdout=json.dumps(snapshot), returncode=0)

        env = {
            "H2S_CONTRIB_DB": cls.db_path,
            "H2S_CONTRIB_RETENTION_DAYS": "30",
            "H2S_CONTRIB_TOKEN_TTL_DAYS": "7",
            "H2S_CONTRIB_SERVICE_ACCOUNT": cls.service_account,
            "H2S_CONTRIB_PURGE_ACCOUNT": cls.purge_account,
        }
        if "contribution_api" in __import__("sys").modules:
            del __import__("sys").modules["contribution_api"]
        with patch.dict(os.environ, env, clear=False), patch.object(subprocess, "run", side_effect=fake_powershell_acl):
            cls.api = importlib.import_module("contribution_api")
        cls.http = cls.api.app.test_client()
        # First DB access performs the additive, transactional migration.
        with cls.api._connection() as con:
            cls.recent_digest = cls.api._token_hash(cls.legacy_recent)
            cls.old_digest = cls.api._token_hash(cls.legacy_old)
            cls.bad_date_digest = cls.api._token_hash(cls.legacy_bad_date)
            cls.valid_token = "h2s_synthetic_valid_" + secrets.token_urlsafe(36)
            cls.revoked_token = "h2s_synthetic_revoked_" + secrets.token_urlsafe(36)
            for token in (cls.valid_token, cls.revoked_token):
                con.execute("INSERT INTO tokens(token_hash,created_at,expires_at) VALUES(?,?,?)", (
                    cls.api._token_hash(token), cls.api._utc_timestamp(now),
                    cls.api._utc_timestamp(now + timedelta(days=2))))
            con.execute("UPDATE tokens SET revoked_at=? WHERE token_hash=?", (
                cls.api._utc_timestamp(now), cls.api._token_hash(cls.revoked_token)))
        with cls.api._connection() as con:
            expiry_recent = cls.api._parse_timestamp(con.execute(
                "SELECT expires_at FROM tokens WHERE token_hash=?", (cls.recent_digest,)).fetchone()[0])
            expiry_old = cls.api._parse_timestamp(con.execute(
                "SELECT expires_at FROM tokens WHERE token_hash=?", (cls.old_digest,)).fetchone()[0])
            expiry_bad = con.execute("SELECT expires_at FROM tokens WHERE token_hash=?",
                                     (cls.bad_date_digest,)).fetchone()[0]
        assert expiry_recent is not None and expiry_recent > datetime.now(timezone.utc)
        assert expiry_old is not None and expiry_old < datetime.now(timezone.utc)
        assert expiry_bad == cls.api.EXPIRED_TIMESTAMP

    @classmethod
    def tearDownClass(cls):
        try:
            if "contribution_api" in __import__("sys").modules:
                del __import__("sys").modules["contribution_api"]
        finally:
            shutil.rmtree(cls.temp_root, ignore_errors=True)

    def setUp(self):
        with self.api._connection() as con:
            con.execute("DELETE FROM contributions")
            con.execute("UPDATE tokens SET revoked_at=NULL WHERE token_hash=?",
                        (self.api._token_hash(self.valid_token),))
            con.execute("UPDATE tokens SET expires_at=? WHERE token_hash=?", (
                self.api._utc_timestamp(datetime.now(timezone.utc) + timedelta(days=2)),
                self.api._token_hash(self.valid_token)))

    def _payload(self):
        return contribution_client.build_payload({
            "scanned_frames": 12, "blocks": 3, "low_conf": 1,
            "device": "cpu", "ocr_mode": "rapidocr",
        }, 2.5)

    def _post(self, payload, token=None, **extra_headers):
        headers = {
            "Authorization": "Bearer " + (token or self.valid_token),
            "Idempotency-Key": payload["submission_id"],
            "X-H2S-Metrics-Consent": "v1",
            "X-H2S-Retention-Policy": "30",
        }
        headers.update(extra_headers)
        return self.http.post("/v1/contributions", json=payload, headers=headers)

    def _insert_token(self, *, days=2, revoked=False):
        token = "h2s_synthetic_case_" + secrets.token_urlsafe(36)
        now = datetime.now(timezone.utc)
        with self.api._connection() as con:
            con.execute("INSERT INTO tokens(token_hash,created_at,expires_at,revoked_at) VALUES(?,?,?,?)", (
                self.api._token_hash(token), self.api._utc_timestamp(now),
                self.api._utc_timestamp(now + timedelta(days=days)),
                self.api._utc_timestamp(now) if revoked else None))
        return token

    def test_legacy_migration_expires_from_original_issue_time_and_bad_date_fails_closed(self):
        with self.api._connection() as con:
            columns = {row["name"] for row in con.execute("PRAGMA table_info(tokens)")}
            self.assertIn("expires_at", columns)
            self.assertIsNotNone(con.execute("SELECT expires_at FROM tokens WHERE token_hash=?",
                                             (self.recent_digest,)).fetchone()[0])
            self.assertEqual(con.execute("SELECT expires_at FROM tokens WHERE token_hash=?",
                                         (self.bad_date_digest,)).fetchone()[0], self.api.EXPIRED_TIMESTAMP)
        self.assertEqual(self._post(self._payload(), token=self.legacy_recent).status_code, 201)
        self.assertEqual(self._post(self._payload(), token=self.legacy_old).status_code, 401)

    def test_malformed_legacy_schema_migration_rolls_back_and_fails_closed(self):
        malformed = os.path.join(self.temp_root, "malformed.sqlite3")
        con = sqlite3.connect(malformed)
        try:
            con.execute("CREATE TABLE tokens (token_hash TEXT PRIMARY KEY)")
            con.commit()
        finally:
            con.close()
        config = self.api._CONFIG
        with patch.object(self.api, "_CONFIG", (Path(malformed), *config[1:])):
            with self.assertRaisesRegex(RuntimeError, "schema migration failed"):
                self.api._db()
        con = sqlite3.connect(malformed)
        try:
            columns = {row[1] for row in con.execute("PRAGMA table_info(tokens)")}
        finally:
            con.close()
        self.assertEqual(columns, {"token_hash"}, "failed migration must roll back its DDL")

    def test_fresh_valid_token_can_submit_and_delete(self):
        payload = self._payload()
        self.assertEqual(self._post(payload).status_code, 201)
        response = self.http.delete("/v1/contributions/" + payload["submission_id"], headers={
            "Authorization": "Bearer " + self.valid_token})
        self.assertEqual(response.status_code, 200)

    def test_expired_token_is_rejected_for_submit_and_delete(self):
        payload = self._payload()
        self.assertEqual(self._post(payload).status_code, 201)
        with self.api._connection() as con:
            con.execute("UPDATE tokens SET expires_at=? WHERE token_hash=?", (
                self.api.EXPIRED_TIMESTAMP, self.api._token_hash(self.valid_token)))
        self.assertEqual(self._post(self._payload()).status_code, 401)
        response = self.http.delete("/v1/contributions/" + payload["submission_id"], headers={
            "Authorization": "Bearer " + self.valid_token})
        self.assertEqual(response.status_code, 401)

    def test_revoked_token_is_rejected_for_submit_and_delete(self):
        payload = self._payload()
        self.assertEqual(self._post(payload).status_code, 201)
        with self.api._connection() as con:
            con.execute("UPDATE tokens SET revoked_at=? WHERE token_hash=?", (
                self.api._utc_timestamp(datetime.now(timezone.utc)), self.api._token_hash(self.valid_token)))
        self.assertEqual(self._post(self._payload()).status_code, 401)
        response = self.http.delete("/v1/contributions/" + payload["submission_id"], headers={
            "Authorization": "Bearer " + self.valid_token})
        self.assertEqual(response.status_code, 401)

    def test_issue_cli_persists_expiry_and_revoke_cli_disables_token(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.api._cli(["issue-token"])
        token = output.getvalue().strip().splitlines()[-1]
        with self.api._connection() as con:
            row = con.execute("SELECT created_at,expires_at FROM tokens WHERE token_hash=?",
                              (self.api._token_hash(token),)).fetchone()
        self.assertIsNotNone(row)
        delta = self.api._parse_timestamp(row["expires_at"]) - self.api._parse_timestamp(row["created_at"])
        self.assertAlmostEqual(delta.total_seconds(), 7 * 86400, delta=1)
        with patch("getpass.getpass", return_value=token), contextlib.redirect_stdout(io.StringIO()):
            self.api._cli(["revoke-token"])
        self.assertEqual(self._post(self._payload(), token=token).status_code, 401)

    def test_ttl_configuration_defaults_to_seven_and_enforces_bounds(self):
        base = {
            "H2S_CONTRIB_RETENTION_DAYS": "30",
            "H2S_CONTRIB_DB": os.path.join(self.temp_root, "settings.sqlite3"),
            "H2S_CONTRIB_SERVICE_ACCOUNT": self.service_account,
            "H2S_CONTRIB_PURGE_ACCOUNT": self.purge_account,
        }
        with patch.object(self.api, "_validate_storage_security"), patch.dict(os.environ, base, clear=False):
            os.environ.pop("H2S_CONTRIB_TOKEN_TTL_DAYS", None)
            self.assertEqual(self.api._settings()[4], 7)
            for value, expected in (("1", 1), ("30", 30)):
                with patch.dict(os.environ, {"H2S_CONTRIB_TOKEN_TTL_DAYS": value}):
                    self.assertEqual(self.api._settings()[4], expected)
            for value in ("0", "31", "-1", "forever"):
                with patch.dict(os.environ, {"H2S_CONTRIB_TOKEN_TTL_DAYS": value}):
                    with self.assertRaises(RuntimeError):
                        self.api._settings()

    def test_consent_unknown_fields_duplicate_json_and_body_size_are_rejected(self):
        payload = self._payload()
        self.assertEqual(self._post(payload, **{"X-H2S-Metrics-Consent": ""}).status_code, 400)
        unknown = dict(payload, unexpected="must-not-be-stored")
        self.assertEqual(self._post(unknown).status_code, 400)
        duplicate = json.dumps(payload, separators=(",", ":")).replace('"schema_version":1', '"schema_version":1,"schema_version":1')
        response = self.http.post("/v1/contributions", data=duplicate, content_type="application/json", headers={
            "Authorization": "Bearer " + self.valid_token,
            "Idempotency-Key": payload["submission_id"], "X-H2S-Metrics-Consent": "v1",
            "X-H2S-Retention-Policy": "30",
        })
        self.assertEqual(response.status_code, 400)
        oversized = b'{"pad":"' + (b"x" * self.api.MAX_BODY) + b'"}'
        self.assertEqual(self.http.post("/v1/contributions", data=oversized, content_type="application/json").status_code, 413)

    def test_idempotency_exact_replay_is_stable_and_payload_conflict_is_rejected(self):
        payload = self._payload()
        self.assertEqual(self._post(payload).status_code, 201)
        replay = self._post(payload)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.get_json()["status"], "already_received")
        changed = json.loads(json.dumps(payload))
        changed["metrics"]["frames_scanned"] += 1
        conflict = self._post(changed)
        self.assertEqual(conflict.status_code, 409)

    def test_rate_limit_rejects_submission(self):
        with patch.object(self.api, "_rate_ok", return_value=False):
            self.assertEqual(self._post(self._payload()).status_code, 429)

    def test_storage_error_log_does_not_include_token_or_payload(self):
        token = self.valid_token
        payload = self._payload()
        with patch.object(self.api, "_db", side_effect=sqlite3.OperationalError("synthetic failure")), \
                self.assertLogs(self.api.app.logger, level="ERROR") as captured:
            response = self._post(payload, token=token)
        self.assertEqual(response.status_code, 503)
        logged = "\n".join(captured.output)
        self.assertNotIn(token, logged)
        self.assertNotIn(payload["submission_id"], logged)
        self.assertNotIn("synthetic failure", logged)

    def test_local_contribution_mutation_requires_same_origin(self):
        client = ui_server.app.test_client()
        url = f"http://127.0.0.1:{ui_server.PORT}/api/katki/preview"
        body = {"job_id": "synthetic-job"}
        cross = client.post(url, json=body, headers={"Origin": "https://evil.synthetic.test"})
        self.assertEqual(cross.status_code, 403)
        same = client.post(url, json=body, headers={"Origin": url.split("/api/", 1)[0]})
        self.assertEqual(same.status_code, 404, "same-origin request should pass guard then miss synthetic job")

if __name__ == "__main__":
    unittest.main()
