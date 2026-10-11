import os
import subprocess
import sys
import unittest

from windows_job import CREATE_SUSPENDED, JobAssignmentError, KILL_ON_JOB_CLOSE, WindowsJobObject


class SyntheticProcess:
    _handle = 1234
    pid = 1234

    def __init__(self):
        self.exited = False
        self.terminated = False
        self.waited = False
        self.killed = False
        self.resumed = False

    def poll(self):
        return 0 if self.exited else None

    def terminate(self):
        self.terminated = True
        self.exited = True

    def kill(self):
        self.killed = True
        self.exited = True

    def wait(self, timeout=None):
        self.waited = True
        self.exited = True
        return 0


class FakeJobApi:
    def __init__(self, process, *, fail_at=None, fail_closes=0):
        self.process = process
        self.fail_at = fail_at
        self.closed = []
        self.flags = None
        self.assigned = False
        self.fail_closes = fail_closes
        self.close_calls = 0

    def create(self):
        if self.fail_at == "create":
            raise OSError("synthetic create failure")
        return "synthetic-job-handle"

    def enable_kill_on_close(self, handle):
        self.flags = KILL_ON_JOB_CLOSE
        if self.fail_at == "configure":
            raise OSError("synthetic configuration failure")

    def assign(self, handle, process_handle):
        if self.fail_at == "assign":
            raise OSError("synthetic assignment failure")
        self.assigned = True

    def close(self, handle):
        self.close_calls += 1
        if self.fail_closes:
            self.fail_closes -= 1
            raise OSError("synthetic CloseHandle failure")
        self.closed.append(handle)
        # Model Windows KILL_ON_JOB_CLOSE behavior for the owned process tree.
        if self.assigned and not self.process.exited:
            self.process.terminate()

    def resume_primary_thread(self, pid):
        if self.fail_at == "resume":
            raise OSError("synthetic resume failure")
        self.process.resumed = True


class WindowsJobObjectTests(unittest.TestCase):
    def test_owner_closing_terminates_owned_synthetic_process_tree(self):
        process = SyntheticProcess()
        api = FakeJobApi(process)
        owner = WindowsJobObject(process, api=api)
        self.assertEqual(api.flags, KILL_ON_JOB_CLOSE)
        self.assertTrue(process.resumed)
        self.assertFalse(process.exited)
        owner.close()
        self.assertTrue(process.exited)
        self.assertEqual(api.closed, ["synthetic-job-handle"])

    def test_assignment_failure_closes_job_and_terminates_reaps_child(self):
        process = SyntheticProcess()
        api = FakeJobApi(process, fail_at="assign")
        with self.assertRaises(JobAssignmentError):
            WindowsJobObject(process, api=api)
        self.assertEqual(api.closed, ["synthetic-job-handle"])
        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertTrue(process.exited)

    def test_resume_failure_closes_job_and_terminates_reaps_suspended_child(self):
        process = SyntheticProcess()
        api = FakeJobApi(process, fail_at="resume")
        with self.assertRaises(JobAssignmentError):
            WindowsJobObject(process, api=api)
        self.assertEqual(api.closed, ["synthetic-job-handle"])
        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)

    def test_one_shot_close_failure_retains_handle_and_succeeds_on_retry(self):
        process = SyntheticProcess()
        api = FakeJobApi(process, fail_closes=1)
        owner = WindowsJobObject(process, api=api)
        handle = owner.handle
        with self.assertRaises(OSError):
            owner.close()
        self.assertEqual(owner.handle, handle)
        self.assertFalse(process.exited)
        owner.close()
        self.assertIsNone(owner.handle)
        self.assertTrue(process.exited)
        self.assertEqual(api.close_calls, 2)

    @unittest.skipUnless(os.name == "nt", "requires Windows Job Objects")
    def test_real_job_handle_close_ends_a_synthetic_sleep_process(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            creationflags=CREATE_SUSPENDED,
        )
        owner = None
        try:
            try:
                owner = WindowsJobObject(process)
                owner.close()
            except JobAssignmentError:
                # Fail-closed assignment is also valid in hosts whose outer job
                # policy denies nesting; constructor must already reap the child.
                pass
            self.assertIsNotNone(process.wait(timeout=10))
        finally:
            if owner is not None:
                owner.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
