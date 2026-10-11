import importlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import contribution_client
import ui_server


class ContributionClientAndUiTests(unittest.TestCase):
    def test_payload_is_numeric_allowlist_and_cer_requires_separate_consent(self):
        stats = {
            "scanned_frames": 120, "blocks": 8, "low_conf": 2,
            "device": "CUDA device name/private data", "ocr_mode": "EasyOCR",
            "speech_seconds": 7.5, "subtitle": "secret line",
            "path": r"C:\private\video.mkv", "filename": "secret.mkv",
        }
        payload = contribution_client.build_payload(stats, 12.345)
        self.assertEqual(set(payload), {"schema_version", "submission_id", "created_at", "metrics"})
        self.assertEqual(set(payload["metrics"]), {
            "duration_seconds", "frames_scanned", "cue_count", "low_conf_count",
            "device_class", "engine_class", "low_conf_ratio", "speech_seconds"})
        serialized = json.dumps(payload)
        for secret in ("secret line", "private", "secret.mkv", "video.mkv"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(payload["metrics"]["device_class"], "cuda")
        self.assertEqual(payload["metrics"]["engine_class"], "easyocr")
        with self.assertRaises(contribution_client.ContributionError):
            contribution_client.build_payload(stats, 1, include_cer=True, cer_value=0.1)
        cer_payload = contribution_client.build_payload(
            stats, 1, include_cer=True, trusted_reference_used=True,
            cer_consent=True, cer_value=0.1)
        self.assertIn("cer", cer_payload["metrics"])

    def test_config_and_katki_page_disclose_destination_and_local_retraction_policy(self):
        token = "synthetic-not-a-real-token"
        with patch.dict(os.environ, {
            "H2S_CONTRIB_URL": "https://collector.synthetic.test:9443",
            "H2S_CONTRIB_TOKEN": token,
            "H2S_CONTRIB_RETENTION_DAYS": "30",
        }, clear=False):
            config = contribution_client.config_status()
            self.assertTrue(config["enabled"])
            self.assertEqual(config["destination_host"], "collector.synthetic.test:9443")
            page = ui_server.app.test_client().get("/katki").get_data(as_text=True)
        self.assertIn('destinationHost="collector.synthetic.test:9443"', page)
        self.assertIn("HTTPS hedefi: https://'+destinationHost", page)
        self.assertNotIn(token, page)
        self.assertIn("bearer token'ın özetini", page)
        self.assertIn("hız sınırı", page)
        self.assertIn("30 gün", page)
        self.assertIn("hardsub2srt.contribution.retractions.v1", page)
        self.assertIn("MAX_LOCAL_RECORDS=100", page)
        self.assertIn("Sunucudan sil", page)
        self.assertIn("Yalnız listeden kaldır", page)
        self.assertIn("yerel kimlik korundu", page)
        send_flow = page[page.index("send.onclick="):page.index("renderRetractions();", page.index("send.onclick="))]
        self.assertLess(send_flow.index("rememberSubmission(current,'pending')"),
                        send_flow.index("await api('/api/katki/gonder'"))
        self.assertIn("catch(e){status.textContent='Gönderim sonucu belirsiz", send_flow)
        self.assertIn("localStorage.setItem(STORE_KEY,JSON.stringify(records.slice(-MAX_LOCAL_RECORDS)))", page)
        self.assertIn("removeRetraction(record.submission_id)", page)
        render_flow = page[page.index("function renderRetractions()"):page.index("\nfetch('/api/katki/isler')")]
        not_found_flow = render_flow[render_flow.index("result.status==='not_found'"):render_flow.index("else throw Error('Silme sonucu doğrulanamadı.')")]
        self.assertNotIn("removeRetraction", not_found_flow)
        local_only_flow = render_flow[render_flow.index("localButton.onclick="):render_flow.index("if(record.destination_host!==destinationHost)")]
        self.assertIn("removeRetraction(record.submission_id)", local_only_flow)
        self.assertNotIn("/api/katki/sil", local_only_flow)
        self.assertNotIn("localStorage", page[page.index("<label><input type=\"checkbox\" id=\"consent\""):page.index("</label>")])

    def test_destination_hostname_is_script_escaped_before_inline_javascript(self):
        with patch.dict(os.environ, {
            "H2S_CONTRIB_URL": "https://<script>",
            "H2S_CONTRIB_TOKEN": "synthetic-not-a-real-token",
            "H2S_CONTRIB_RETENTION_DAYS": "30",
        }, clear=False):
            page = ui_server.app.test_client().get("/katki").get_data(as_text=True)
        self.assertIn('destinationHost="\\u003cscript\\u003e"', page)


@unittest.skipUnless(os.name == "nt", "API storage startup requires Windows ACL checks; synthetic ACL snapshot only on Windows")
class ContributionApiDeleteAndRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_root = tempfile.mkdtemp(prefix="h2s-contrib-api-test-")
        cls.db_path = os.path.join(cls.temp_root, "contributions.sqlite3")
        cls.service_account = r"NT SERVICE\synthetic-contrib-api"
        cls.purge_account = "synthetic-host\\hardsub-purge"
        cls.service_sid = "S-1-5-80-111"
        cls.purge_sid = "S-1-5-80-222"
        cls.admin_sid = "S-1-5-32-544"
        cls.system_sid = "S-1-5-18"

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
            "H2S_CONTRIB_RETENTION_DAYS": "1",
            "H2S_CONTRIB_SERVICE_ACCOUNT": cls.service_account,
            "H2S_CONTRIB_PURGE_ACCOUNT": cls.purge_account,
        }
        if "contribution_api" in __import__("sys").modules:
            del __import__("sys").modules["contribution_api"]
        with patch.dict(os.environ, env, clear=False), patch.object(subprocess, "run", side_effect=fake_powershell_acl):
            cls.api = importlib.import_module("contribution_api")
        cls.token = "h2s_synthetic_" + secrets.token_urlsafe(36)
        with cls.api._connection() as con:
            con.execute("INSERT INTO tokens(token_hash,created_at) VALUES(?,?)", (
                cls.api._token_hash(cls.token), datetime.now(timezone.utc).isoformat()))
            cls.other_token = "h2s_other_" + secrets.token_urlsafe(36)
            con.execute("INSERT INTO tokens(token_hash,created_at) VALUES(?,?)", (
                cls.api._token_hash(cls.other_token), datetime.now(timezone.utc).isoformat()))
        cls.http = cls.api.app.test_client()

    @classmethod
    def tearDownClass(cls):
        try:
            if "contribution_api" in __import__("sys").modules:
                del __import__("sys").modules["contribution_api"]
        finally:
            shutil.rmtree(cls.temp_root, ignore_errors=True)

    def _payload(self):
        return contribution_client.build_payload(
            {"scanned_frames": 10, "blocks": 2, "low_conf": 1,
             "device": "cpu", "ocr_mode": "rapidocr"}, 2.5)

    def _post(self, payload, token=None):
        auth = token or self.token
        return self.http.post("/v1/contributions", json=payload, headers={
            "Authorization": "Bearer " + auth,
            "Idempotency-Key": payload["submission_id"],
            "X-H2S-Metrics-Consent": "v1",
            "X-H2S-Retention-Policy": "1",
        })

    def test_delete_is_scoped_to_token_and_removes_record(self):
        payload = self._payload()
        created = self._post(payload)
        self.assertEqual(created.status_code, 201, created.get_json())
        foreign = self.http.delete("/v1/contributions/" + payload["submission_id"], headers={
            "Authorization": "Bearer " + self.other_token})
        self.assertEqual(foreign.status_code, 404)
        deleted = self.http.delete("/v1/contributions/" + payload["submission_id"], headers={
            "Authorization": "Bearer " + self.token})
        self.assertEqual(deleted.status_code, 200)
        with self.api._connection() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM contributions WHERE submission_id=?",
                                          (payload["submission_id"],)).fetchone())

    def test_purge_removes_expired_submission_but_keeps_recent_one(self):
        old_payload, recent_payload = self._payload(), self._payload()
        self.assertEqual(self._post(old_payload).status_code, 201)
        self.assertEqual(self._post(recent_payload).status_code, 201)
        old_received = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.api._connection() as con:
            con.execute("UPDATE contributions SET received_at=? WHERE submission_id=?",
                        (old_received, old_payload["submission_id"]))
        removed = self.api.purge_expired()
        self.assertEqual(removed, 1)
        with self.api._connection() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM contributions WHERE submission_id=?",
                                          (old_payload["submission_id"],)).fetchone())
            self.assertIsNotNone(con.execute("SELECT 1 FROM contributions WHERE submission_id=?",
                                             (recent_payload["submission_id"],)).fetchone())


if __name__ == "__main__":
    unittest.main()
