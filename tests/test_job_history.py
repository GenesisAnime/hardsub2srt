import json
import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from pathlib import PurePosixPath
from unittest.mock import Mock, patch

import ui_server
from job_history import (JobHistory, MAX_FILE_BYTES, MAX_JOBS,
                         _macos_mount_path_is_local, default_history_path)
from windows_job import JobAssignmentError


class JobHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "job-history.json"
        self.history = JobHistory(self.path, max_jobs=3)

    def tearDown(self):
        self.temp.cleanup()

    def _job(self, number, output_dir=None):
        video = Path(self.temp.name) / f"secret-video-name-{number}.mp4"
        video.write_bytes(b"synthetic placeholder; no decoder is run")
        sec = {"cikti_klasor": str(output_dir or (Path(self.temp.name) / f"out-{number}")),
               "cpu": False}
        job = ui_server.is_olustur(video, sec, f"is-{number}", number)
        job["basladi"] = time.time()
        job["_diag_phase"] = "setup"
        return job

    def test_synthetic_setup_startup_child_success_and_cancel_are_recorded(self):
        history = JobHistory(self.path, max_jobs=10)
        regular_file = Path(self.temp.name) / "not-a-directory"
        regular_file.write_text("synthetic", encoding="utf-8")
        setup_job = self._job(1, regular_file)
        with patch.object(ui_server, "JOB_HISTORY", history), \
             patch.object(ui_server, "WindowsJobObject", lambda _process: None):
            with self.assertRaises(OSError):
                ui_server.is_yurut(setup_job)
            ui_server._worker_failed(setup_job, OSError("C:\\private\\output"))

            start_job = self._job(2)
            with patch.object(ui_server.subprocess, "Popen", side_effect=OSError("C:\\private\\video")):
                with self.assertRaises(OSError):
                    ui_server.is_yurut(start_job)
            ui_server._worker_failed(start_job, OSError("C:\\private\\video"))

            def fake_process(rc, *, emit_srt=False):
                class Process:
                    pid = 321
                    stdout = io.StringIO("")

                    def wait(self):
                        return rc
                def popen(command, **_kwargs):
                    if emit_srt:
                        output = Path(command[command.index("-o") + 1])
                        output.write_text("1\\n00:00:01,000 --> 00:00:02,000\\nSynthetic\\n", encoding="utf-8")
                    return Process()
                return popen

            child_job = self._job(3)
            with patch.object(ui_server.subprocess, "Popen", new=fake_process(17)):
                ui_server.is_yurut(child_job)

            success_job = self._job(4)
            with patch.object(ui_server.subprocess, "Popen", new=fake_process(0, emit_srt=True)):
                ui_server.is_yurut(success_job)

            cancel_job = self._job(5)
            ui_server.IPTAL_SETI.add(cancel_job["id"])
            try:
                with patch.object(ui_server.subprocess, "Popen", new=fake_process(-9)):
                    ui_server.is_yurut(cancel_job)
            finally:
                ui_server.IPTAL_SETI.discard(cancel_job["id"])

        rows = {row["id"].rsplit("-", 1)[-1]: row for row in history.recent(20)}
        self.assertEqual(rows["1"]["error_code"], "setup_failed")
        self.assertEqual(rows["2"]["error_code"], "process_start_failed")
        self.assertEqual(rows["3"]["return_code"], 17)
        self.assertEqual(rows["4"]["status"], "succeeded")
        self.assertEqual(rows["5"]["status"], "cancelled")

    def test_restart_persists_final_jobs_and_marks_running_job_interrupted(self):
        self.history.record("is-1", status="succeeded", phase="complete", return_code=0)
        self.history.record("is-2", status="running", phase="child_running")
        restarted = JobHistory(self.path)
        rows = {row["id"].rsplit("-", 1)[-1]: row for row in restarted.recent(20)}
        self.assertEqual(rows["1"]["status"], "succeeded")
        self.assertEqual(rows["2"]["status"], "interrupted")
        self.assertEqual(rows["2"]["error_code"], "server_restart")

    def test_restart_with_reused_queue_id_does_not_overwrite_previous_run(self):
        first_process = JobHistory(self.path)
        first_process.record("is-1", status="succeeded", phase="complete", return_code=0)
        second_process = JobHistory(self.path)
        second_process.record("is-1", status="failed", phase="complete",
                              error_code="process_start_failed")
        rows = second_process.recent(10)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        self.assertEqual({row["status"] for row in rows}, {"succeeded", "failed"})

    def test_retention_and_file_size_are_bounded(self):
        history = JobHistory(self.path, max_jobs=3)
        for number in range(1, MAX_JOBS + 5):
            history.record(f"is-{number}", status="succeeded", phase="complete")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertLessEqual(len(payload["jobs"]), 3)
        self.assertLessEqual(self.path.stat().st_size, MAX_FILE_BYTES)

    def test_raw_path_error_text_and_filename_are_never_serialized(self):
        history = JobHistory(self.path)
        history.record("is-12", status="failed", phase="complete",
                       error_code="setup_failed")
        serialized = self.path.read_text(encoding="utf-8")
        self.assertNotIn("C:\\\\Users", serialized)
        self.assertNotIn("secret-video-name", serialized)
        self.assertNotIn("traceback", serialized.lower())
        record = json.loads(serialized)["jobs"][0]
        self.assertEqual(set(record), {
            "id", "queue_id", "status", "phase", "mode", "started_at", "updated_at",
            "duration_seconds", "return_code", "error_code",
        })

    def test_malformed_history_fails_safe_and_can_be_replaced(self):
        self.path.write_text('{"schema_version":1,"jobs":[', encoding="utf-8")
        history = JobHistory(self.path)
        self.assertEqual(history.recent(), [])
        self.assertTrue(history.record("is-1", status="succeeded", phase="complete"))
        self.assertRegex(json.loads(self.path.read_text(encoding="utf-8"))["jobs"][0]["id"],
                         r"^run-[a-f0-9]{32}-1$")

    def test_symlink_state_path_is_not_read_or_written(self):
        target = Path(self.temp.name) / "external.json"
        target.write_text('{"outside":"must remain untouched"}', encoding="utf-8")
        link = Path(self.temp.name) / "linked-history.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable on this Windows account")
        history = JobHistory(link)
        self.assertEqual(history.recent(), [])
        self.assertFalse(history.record("is-1", status="succeeded", phase="complete"))
        self.assertEqual(target.read_text(encoding="utf-8"), '{"outside":"must remain untouched"}')

    @unittest.skipUnless(os.name == "nt", "tests Windows LocalAppData redirects")
    def test_redirected_unc_synced_and_mapped_localappdata_fall_back(self):
        fallback = Path.home() / "AppData" / "Local" / "hardsub2srt" / "job-history.json"
        with patch.dict(os.environ, {"LOCALAPPDATA": r"\\server\share\AppData"}, clear=False):
            self.assertEqual(default_history_path(), fallback)
        sync = Path.home() / "OneDrive" / "AppData" / "Local"
        with patch.dict(os.environ, {
            "LOCALAPPDATA": str(sync), "OneDrive": str(Path.home() / "OneDrive")
        }, clear=False):
            self.assertEqual(default_history_path(), fallback)

        def drive_type(root):
            return 4 if root.lower().startswith("z:") else 3

        with patch.dict(os.environ, {"LOCALAPPDATA": r"Z:\AppData"}, clear=False), \
             patch("job_history._get_windows_drive_type", side_effect=drive_type):
            self.assertEqual(default_history_path(), fallback)

    def test_macos_xdg_mount_detection_fails_closed_for_remote_and_sync_roots(self):
        mounts = (
            "/dev/disk3s1 on / (apfs, local, journaled)\n"
            "/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled)\n"
            "//nas/share on /Volumes/team (smbfs, nodev, nosuid)\n"
        )
        self.assertTrue(_macos_mount_path_is_local(
            PurePosixPath("/Users/example/.local/state"), mounts))
        self.assertFalse(_macos_mount_path_is_local(
            PurePosixPath("/Volumes/team/cache"), mounts))
        self.assertFalse(_macos_mount_path_is_local(
            PurePosixPath("/Users/example/Library/CloudStorage/OneDrive/state"), mounts,
            sync_roots=[PurePosixPath("/Users/example/Library/CloudStorage")]))
        self.assertFalse(_macos_mount_path_is_local(
            PurePosixPath("/Users/example/.local/state"), ""))

    def test_block_editor_rejects_nonpositive_cue_duration_without_writing(self):
        root = Path(self.temp.name)
        video = root / "synthetic-editor.mp4"
        video.write_bytes(b"synthetic; decoder is not run")
        run_dir = root / "synthetic-run"
        run_dir.mkdir()
        srt = run_dir / "synthetic-editor.srt"
        original = "1\n00:00:01,000 --> 00:00:02,000\nOriginal caption\n"
        srt.write_text(original, encoding="utf-8")
        job = ui_server.is_olustur(video, {"cikti_klasor": str(root), "cpu": False},
                                   "is-994", 994)
        job.update({"srt": str(srt), "run_dir": str(run_dir), "durum": "bitti"})
        client = ui_server.app.test_client()
        base = f"http://127.0.0.1:{ui_server.PORT}"
        local_headers = {"Origin": base}

        with patch.object(ui_server, "ISLER", [job]), patch.object(ui_server, "ogren_mod", None):
            for start, end in (("00:00:01,000", "00:00:01,000"),
                               ("00:00:02,000", "00:00:01,000"),
                               ("100:00:00,000", "99:59:59,999")):
                with self.subTest(start=start, end=end):
                    srt.write_text(original, encoding="utf-8")
                    backup = srt.with_name(srt.name + ".bak")
                    backup.unlink(missing_ok=True)
                    response = client.post("/api/blokkaydet", base_url=base,
                                           headers=local_headers, json={
                        "is_id": "is-994",
                        "bloklar": [{"index": 0, "start": start, "end": end,
                                     "metin": "Changed caption"}],
                    })
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("bitiş başlangıçtan sonra olmalı",
                                  response.get_json()["hata"])
                    self.assertEqual(srt.read_text(encoding="utf-8"), original)
                    self.assertFalse(backup.exists())

            valid = client.post("/api/blokkaydet", base_url=base,
                                headers=local_headers, json={
                "is_id": "is-994",
                "bloklar": [{"index": 0, "start": "00:00:01,000",
                             "end": "00:00:03,000", "metin": "Changed caption"}],
            })
            self.assertEqual(valid.status_code, 200)
            self.assertIn("00:00:01,000 --> 00:00:03,000",
                          srt.read_text(encoding="utf-8"))
            self.assertTrue(srt.with_name(srt.name + ".bak").exists())

            multi_day = client.post("/api/blokkaydet", base_url=base,
                                    headers=local_headers, json={
                "is_id": "is-994",
                "bloklar": [{"index": 0, "start": "99:59:59,000",
                             "end": "100:00:00,000", "metin": "Across 100 hours"}],
            })
            self.assertEqual(multi_day.status_code, 200)
            self.assertIn("99:59:59,000 --> 100:00:00,000",
                          srt.read_text(encoding="utf-8"))

    def test_all_ui_routes_reject_non_loopback_host_headers(self):
        client = ui_server.app.test_client()
        local_host = f"127.0.0.1:{ui_server.PORT}"
        for path in ("/", "/api/durum", "/api/ocr-inceleme/durum",
                     "/api/ocr-inceleme/srt-lint", "/api/qa/synthetic.json"):
            with self.subTest(path=path):
                response = client.get(path, base_url=f"http://{local_host}",
                                      headers={"Host": "attacker.synthetic.test:8765"})
                self.assertEqual(response.status_code, 403)

        for host in (f"localhost:{ui_server.PORT}", local_host):
            with self.subTest(host=host):
                response = client.get("/api/durum", base_url=f"http://{host}")
                self.assertEqual(response.status_code, 200)

    def test_cross_origin_form_cannot_write_user_dictionary(self):
        dictionary = Path(self.temp.name) / "synthetic-user-dictionary.txt"
        client = ui_server.app.test_client()
        host = f"http://127.0.0.1:{ui_server.PORT}"
        body = '{"eski":"CSRF-PROBE","yeni":"=POISONED"}'
        with patch.object(ui_server, "SOZLUK", dictionary):
            cross_origin = client.post(
                "/api/ogret", base_url=host,
                headers={"Origin": "https://attacker.synthetic.test",
                         "Content-Type": "text/plain"}, data=body)
            self.assertEqual(cross_origin.status_code, 403)
            self.assertFalse(dictionary.exists())

            same_origin = client.post(
                "/api/ogret", base_url=host,
                headers={"Origin": host, "Content-Type": "text/plain"}, data=body)
            self.assertEqual(same_origin.status_code, 200)
            self.assertEqual(
                dictionary.read_text(encoding="utf-8"),
                "CSRF-PROBE\t=POISONED\n",
            )

    def test_history_endpoint_returns_allowlisted_records(self):
        history = JobHistory(self.path)
        history.record("is-8", status="failed", phase="complete", error_code="missing_srt")
        with patch.object(ui_server, "JOB_HISTORY", history):
            response = ui_server.app.test_client().get(
                "/api/is-gecmisi?limit=10", base_url="http://127.0.0.1:8765")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["etkin"])
        self.assertEqual(body["isler"][0]["error_code"], "missing_srt")
        self.assertNotIn("path", body["isler"][0])

    def test_history_endpoint_rejects_host_and_origin_rebinding(self):
        history = JobHistory(self.path)
        with patch.object(ui_server, "JOB_HISTORY", history):
            client = ui_server.app.test_client()
            host_response = client.get("/api/is-gecmisi", headers={"Host": "attacker.example"})
            origin_response = client.get(
                "/api/is-gecmisi", base_url="http://127.0.0.1:8765",
                headers={"Origin": "http://attacker.example"})
        self.assertEqual(host_response.status_code, 403)
        self.assertEqual(origin_response.status_code, 403)

    def test_browser_mutations_reject_cross_origin_and_allow_same_origin(self):
        client = ui_server.app.test_client()
        external_origin = "https://attacker.synthetic.test"
        host = f"http://127.0.0.1:{ui_server.PORT}"
        headers = {"Content-Type": "text/plain", "Origin": external_origin}

        # A text/plain JSON body is form-compatible: HTML forms can submit it,
        # so requiring the Content-Type to say application/json is not enough.
        cross_origin = client.post(
            "/api/ogret", base_url=host, headers=headers,
            data='{"eski":"CSRF-PROBE","yeni":"=POISONED"}')
        self.assertEqual(cross_origin.status_code, 403)

        # The queue API also force-parses JSON and must reject the same browser
        # source before examining the caller-supplied local file paths.
        cross_origin_queue = client.post(
            "/api/ekle", base_url=host, headers=headers,
            data='{"yollar":["C:/synthetic/video.mp4"]}')
        self.assertEqual(cross_origin_queue.status_code, 403)

        same_origin = client.post(
            "/api/ekle", base_url=host,
            headers={"Origin": host, "Content-Type": "text/plain"},
            data='{"yollar":["C:/synthetic/video.mp4"]}')
        self.assertEqual(same_origin.status_code, 200)

        rejected_host = client.post(
            "/api/ogret", base_url="http://127.0.0.1.evil:8765",
            headers={"Origin": "http://127.0.0.1.evil:8765", "Content-Type": "text/plain"},
            data='{"eski":"x","yeni":"y"}')
        self.assertEqual(rejected_host.status_code, 403)

        hostile_origin = client.post(
            "/api/ogret", base_url=host,
            headers={"Origin": host + ".evil", "Content-Type": "text/plain"},
            data='{"eski":"x","yeni":"y"}')
        self.assertEqual(hostile_origin.status_code, 403)

        missing_origin = client.post(
            "/api/ogret", base_url=host, data='{"eski":"x","yeni":"y"}')
        self.assertEqual(missing_origin.status_code, 403)

        read_only = client.get("/api/durum", base_url=host,
                               headers={"Origin": external_origin})
        self.assertEqual(read_only.status_code, 200)
        self.assertNotIn("/api/durum", ui_server.LOCAL_MUTATION_PATHS)

        guarded = {
            "/api/ekle", "/api/iptal", "/api/klasortara", "/api/secim",
            "/api/ac", "/api/blokkaydet", "/api/ogret",
        }
        self.assertTrue(guarded.issubset(ui_server.LOCAL_MUTATION_PATHS))
        self.assertTrue(all(rule.rule in guarded for rule in ui_server.app.url_map.iter_rules()
                            if rule.rule in ui_server.LOCAL_MUTATION_PATHS))

    def test_worker_exception_reaps_child_before_persisting_terminal_failure(self):
        history = JobHistory(self.path)

        class SyntheticChild:
            pid = 456
            stdout = io.StringIO("synthetic child output")

            def __init__(self):
                self.exited = False
                self.terminated = False
                self.waited = False

            def poll(self):
                return 0 if self.exited else None

            def terminate(self):
                self.terminated = True
                self.exited = True

            def wait(self, timeout=None):
                self.waited = True
                self.exited = True
                return 0

            def kill(self):
                self.exited = True

        child = SyntheticChild()
        job = self._job(9)
        job["_diag_phase"] = "child_running"
        ui_server.CALISAN.update(pid=child.pid, is_id=job["id"], process=child)
        try:
            with patch.object(ui_server, "JOB_HISTORY", history), \
                 patch.object(ui_server.subprocess, "run", return_value=type("R", (), {"returncode": 0})()):
                self.assertTrue(ui_server._worker_failed(job, RuntimeError("private traceback")))
                self.assertEqual(ui_server.CALISAN, {})
        finally:
            ui_server.CALISAN.clear()
        self.assertTrue(child.terminated)
        self.assertTrue(child.waited)
        self.assertEqual(history.recent(1)[0]["status"], "failed")

    def test_worker_cleanup_failure_leaves_persisted_job_running(self):
        history = JobHistory(self.path)

        class StuckChild:
            pid = 789
            stdout = io.StringIO("")

            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                raise ui_server.subprocess.TimeoutExpired("synthetic", timeout)

            def kill(self):
                pass

        job = self._job(10)
        job["_diag_phase"] = "child_running"
        ui_server.CALISAN.update(pid=789, is_id=job["id"], process=StuckChild())
        try:
            with patch.object(ui_server, "JOB_HISTORY", history), \
                 patch.object(ui_server.subprocess, "run", return_value=type("R", (), {"returncode": 0})()):
                self.assertFalse(ui_server._worker_failed(job, RuntimeError("synthetic")))
                self.assertEqual(ui_server.CALISAN.get("is_id"), job["id"])
        finally:
            ui_server.CALISAN.clear()
        self.assertEqual(history.recent(1)[0]["status"], "running")

    def test_normal_exit_waits_for_persistent_owner_close_failure_before_serial_slot_release(self):
        history = JobHistory(self.path)
        first_close = threading.Event()
        third_failed_close = threading.Event()
        allow_close = threading.Event()
        worker_errors = []

        class Child:
            pid = 543
            stdout = io.StringIO("")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        class FlakyOwner:
            handle = "job-handle"
            close_calls = 0

            def close(self):
                self.close_calls += 1
                first_close.set()
                if self.close_calls <= 3 or not allow_close.is_set():
                    if self.close_calls >= 3:
                        third_failed_close.set()
                    raise OSError("synthetic persistent close failure")
                self.handle = None

        child = Child()
        owner = FlakyOwner()
        job = self._job(13)

        def fake_popen(command, **_kwargs):
            output = Path(command[command.index("-o") + 1])
            output.write_text("1\n00:00:01,000 --> 00:00:02,000\nSynthetic\n", encoding="utf-8")
            return child

        def run_job():
            try:
                ui_server.is_yurut(job)
            except Exception as exc:
                worker_errors.append(exc)

        try:
            with patch.object(ui_server, "JOB_HISTORY", history), \
                 patch.object(ui_server.subprocess, "Popen", side_effect=fake_popen), \
                 patch.object(ui_server, "WindowsJobObject", return_value=owner):
                worker = threading.Thread(target=run_job, daemon=True)
                worker.start()
                self.assertTrue(first_close.wait(timeout=5))
                self.assertTrue(third_failed_close.wait(timeout=5))
                self.assertTrue(worker.is_alive())
                self.assertEqual(ui_server.CALISAN.get("is_id"), job["id"])
                self.assertIs(ui_server.CALISAN.get("job_owner"), owner)
                self.assertEqual(history.recent(1)[0]["status"], "running")
                self.assertEqual(history.recent(1)[0]["phase"], "child_exit")
                self.assertEqual(owner.handle, "job-handle")
                allow_close.set()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
        finally:
            allow_close.set()
            ui_server.CALISAN.clear()
        self.assertEqual(worker_errors, [])
        self.assertGreaterEqual(owner.close_calls, 4)
        self.assertEqual(history.recent(1)[0]["status"], "succeeded")

    def test_job_owner_assignment_and_reap_failure_stays_tracked_until_reaped(self):
        history = JobHistory(self.path)

        class UnreapedSyntheticChild:
            pid = 987
            _handle = 987
            stdout = io.StringIO("")

            def __init__(self):
                self.exited = False
                self.waited = False

            def poll(self):
                return 0 if self.exited else None

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, timeout=None):
                if not self.exited:
                    raise ui_server.subprocess.TimeoutExpired("synthetic", timeout)
                self.waited = True
                return 0

        child = UnreapedSyntheticChild()
        job = self._job(11)
        popen = Mock(return_value=child)
        try:
            with patch.object(ui_server, "JOB_HISTORY", history), \
                 patch.object(ui_server.subprocess, "Popen", popen), \
                 patch.object(ui_server, "WindowsJobObject",
                              side_effect=JobAssignmentError("synthetic owner assignment/reap failure")):
                with self.assertRaises(JobAssignmentError):
                    ui_server.is_yurut(job)
                if os.name == "nt":
                    self.assertEqual(popen.call_args.kwargs.get("creationflags"),
                                     ui_server.CREATE_SUSPENDED)
                # The Popen child is in CALISAN before owner assignment is attempted.
                self.assertIs(ui_server.CALISAN.get("process"), child)
                self.assertEqual(ui_server.CALISAN.get("is_id"), job["id"])
                with patch.object(ui_server.subprocess, "run",
                                  return_value=type("R", (), {"returncode": 1})()):
                    self.assertFalse(ui_server._worker_failed(job, JobAssignmentError("synthetic")))
                self.assertEqual(history.recent(1)[0]["status"], "running")
                self.assertEqual(ui_server.CALISAN.get("is_id"), job["id"])

                # A later cleanup retry cannot finalize until the child actually exits.
                child.exited = True
                with patch.object(ui_server, "_stop_failed_child",
                                  wraps=ui_server._stop_failed_child), \
                     patch.object(ui_server.time, "sleep"):
                    ui_server._wait_for_failed_child_cleanup(job)
                self.assertEqual(ui_server.CALISAN, {})
                self.assertTrue(child.waited)
                self.assertEqual(history.recent(1)[0]["status"], "failed")
        finally:
            ui_server.CALISAN.clear()


if __name__ == "__main__":
    unittest.main()
