"""Windows Job Object ownership for OCR child processes.

Closing the owner handle kills every process assigned to the job, including if
the Flask parent exits abruptly. Assignment failure is fail-closed: the child
is terminated and reaped before an error is raised.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes

KILL_ON_JOB_CLOSE = 0x00002000
CREATE_SUSPENDED = 0x00000004


class JobAssignmentError(RuntimeError):
    pass


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Win32Api:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows Job Objects are unavailable on this platform")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self._kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self._kernel32.Thread32First.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        self._kernel32.Thread32First.restype = wintypes.BOOL
        self._kernel32.Thread32Next.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        self._kernel32.Thread32Next.restype = wintypes.BOOL
        self._kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenThread.restype = wintypes.HANDLE
        self._kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        self._kernel32.ResumeThread.restype = wintypes.DWORD

    def create(self):
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        return handle

    def enable_kill_on_close(self, handle):
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

    def assign(self, job_handle, process_handle):
        if not self._kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def resume_primary_thread(self, pid):
        # Popen created the process with CREATE_SUSPENDED, so exactly one
        # initial thread must exist and none of its OCR code has run yet.
        class ThreadEntry32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
            ]

        snapshot = self._kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
        invalid = ctypes.c_void_p(-1).value
        if not snapshot or ctypes.cast(snapshot, ctypes.c_void_p).value == invalid:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        thread_handle = None
        try:
            entry = ThreadEntry32()
            entry.dwSize = ctypes.sizeof(entry)
            found = []
            ok = self._kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while ok:
                if entry.th32OwnerProcessID == pid:
                    found.append(entry.th32ThreadID)
                ok = self._kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            if len(found) != 1:
                raise OSError("Expected one suspended primary thread")
            thread_handle = self._kernel32.OpenThread(0x0002, False, found[0])
            if not thread_handle:
                raise OSError(ctypes.get_last_error(), "OpenThread failed")
            previous = self._kernel32.ResumeThread(thread_handle)
            if previous != 1:
                raise OSError(ctypes.get_last_error(), "Primary thread was not suspended exactly once")
        finally:
            if thread_handle:
                self.close(thread_handle)
            self.close(snapshot)

    def close(self, handle):
        if not self._kernel32.CloseHandle(handle):
            raise OSError(ctypes.get_last_error(), "CloseHandle failed")


class WindowsJobObject:
    """Own a process tree and terminate it automatically when this is closed."""

    def __init__(self, process, *, api=None):
        self.process = process
        self.api = api or _Win32Api()
        self.handle = None
        handle = None
        try:
            handle = self.api.create()
            self.api.enable_kill_on_close(handle)
            self.api.assign(handle, process._handle)
            self.api.resume_primary_thread(process.pid)
            self.handle = handle
        except Exception:
            if handle is not None:
                try:
                    self.api.close(handle)
                except Exception:
                    pass
            self._terminate_and_reap(process)
            raise JobAssignmentError("OCR child process could not be safely assigned to a Windows Job Object") from None

    @staticmethod
    def _terminate_and_reap(process):
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.poll() is None:
                raise JobAssignmentError("OCR child process could not be reaped after ownership failure")
        except JobAssignmentError:
            raise
        except Exception:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                raise JobAssignmentError("OCR child process could not be reaped after ownership failure") from None

    def close(self):
        if self.handle is None:
            return
        handle = self.handle
        self.api.close(handle)
        # Keep ownership recorded until CloseHandle confirms success; callers
        # can safely retry without losing the only kill-on-close handle.
        self.handle = None
