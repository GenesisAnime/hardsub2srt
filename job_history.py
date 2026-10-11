"""Small, privacy-preserving persistence for local UI job diagnostics.

Only enum-valued lifecycle data is written. Video/output paths, file names,
child output, exception text, and OCR/SRT contents are deliberately excluded.
"""
from __future__ import annotations

import ctypes
import json
import ntpath
import os
import platform
import posixpath
import re
import stat
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from ctypes import wintypes

SCHEMA_VERSION = 1
MAX_JOBS = 100
MAX_FILE_BYTES = 64 * 1024
RETENTION_DAYS = 30
STATUSES = {"running", "succeeded", "failed", "cancelled", "interrupted"}
PHASES = {"setup", "process_start", "child_running", "child_exit", "complete"}
ERROR_CODES = {
    "setup_failed", "process_start_failed", "worker_failed",
    "child_nonzero", "missing_srt", "cancelled", "server_restart",
}
MODES = {"gpu_default", "cpu"}
_QUEUE_ID = re.compile(r"^is-[0-9]{1,12}$")
_JOB_ID = re.compile(r"^run-[a-f0-9]{32}-[0-9]{1,12}$")


def default_history_path() -> Path:
    """Return an OS-local state path, never a project/OneDrive path."""
    if os.name == "nt":
        fallback = Path.home() / "AppData" / "Local"
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else fallback
        if not _windows_state_root_is_local(root) or _contains_reparse_component(root):
            root = fallback
        if not _windows_state_root_is_local(root) or _contains_reparse_component(root):
            raise RuntimeError("No local, non-synced Windows state directory is available")
        return root / "hardsub2srt" / "job-history.json"
    base = os.environ.get("XDG_STATE_HOME")
    fallback = Path.home() / ".local" / "state"
    root = Path(base) if base else fallback
    if not _unix_state_root_is_local(root) or _contains_reparse_component(root):
        root = fallback
    if not _unix_state_root_is_local(root) or _contains_reparse_component(root):
        raise RuntimeError("No local, non-synced state directory is available")
    return root / "hardsub2srt" / "job-history.json"


def _get_windows_drive_type(root):
    if os.name != "nt":
        return 3
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    return int(kernel32.GetDriveTypeW(root))


def _is_inside(path, root, *, windows=False):
    try:
        if windows:
            path_s = ntpath.normcase(ntpath.abspath(str(path)))
            root_s = ntpath.normcase(ntpath.abspath(str(root)))
            return ntpath.commonpath([path_s, root_s]) == root_s
        path_s = os.path.abspath(os.fspath(path))
        root_s = os.path.abspath(os.fspath(root))
        return os.path.commonpath([path_s, root_s]) == root_s
    except (OSError, ValueError):
        return False


def _one_drive_roots():
    return [value for key, value in os.environ.items()
            if key.upper() in {"ONEDRIVE", "ONEDRIVECONSUMER", "ONEDRIVECOMMERCIAL"}
            and value]


def _contains_reparse_component(path):
    try:
        for component in list(reversed(Path(path).parents)) + [Path(path)]:
            try:
                info = component.lstat()
            except FileNotFoundError:
                continue
            attributes = getattr(info, "st_file_attributes", 0)
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(info.st_mode) or attributes & reparse:
                return True
        return False
    except OSError:
        return True


def _windows_state_root_is_local(path):
    raw = str(path)
    drive, _tail = ntpath.splitdrive(raw)
    if not ntpath.isabs(raw) or not drive or raw.startswith(("\\\\", "//")):
        return False
    if _get_windows_drive_type(drive + "\\") not in {3, 6}:  # fixed disk or RAM disk
        return False
    parts = [part.casefold() for part in ntpath.normpath(raw).split(ntpath.sep) if part]
    if any(part == "onedrive" or part.startswith("onedrive ") for part in parts):
        return False
    return not any(_is_inside(path, root, windows=True) for root in _one_drive_roots())


def _unix_state_root_is_local(path):
    if not Path(path).is_absolute():
        return False
    try:
        resolved = Path(path).resolve(strict=False)
    except OSError:
        return False
    parts = [part.casefold() for part in resolved.parts]
    if any(part == "onedrive" or part.startswith("onedrive ") for part in parts):
        return False
    if any(_is_inside(resolved, root) for root in _one_drive_roots()):
        return False
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(["/sbin/mount"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=3,
                                    check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        home = Path.home()
        sync_roots = list(_one_drive_roots()) + [
            home / "Library" / "CloudStorage",
            home / "Library" / "Mobile Documents",
            home / "Library" / "Containers" / "com.apple.CloudDocs",
            home / "Dropbox", home / "Google Drive", home / "GoogleDrive", home / "Box",
        ]
        return _macos_mount_path_is_local(resolved, result.stdout, sync_roots=sync_roots)
    # Linux mountinfo gives filesystem type for nested mounts such as NFS/CIFS.
    mountinfo = Path("/proc/self/mountinfo")
    if mountinfo.is_file():
        remote_types = {"nfs", "nfs4", "cifs", "smb3", "sshfs", "fuse.sshfs",
                        "davfs", "afs", "ceph", "9p"}
        try:
            matches = []
            target = os.path.abspath(os.fspath(resolved))
            for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
                left, separator, right = line.partition(" - ")
                if not separator:
                    continue
                fields = left.split()
                fs_type = right.split()[0] if right.split() else ""
                if len(fields) < 5 or (fs_type not in remote_types and not fs_type.startswith("fuse.")):
                    continue
                mount_point = fields[4].replace("\\040", " ").replace("\\011", "\t")
                if _is_inside(target, mount_point):
                    matches.append(mount_point)
            if matches:
                return False
        except OSError:
            return False
    return True


def _macos_mount_path_is_local(path, mount_output, *, sync_roots=()):
    """Accept only paths covered by a positively identified local macOS mount."""
    target = posixpath.normpath(os.fspath(path))
    if not posixpath.isabs(target):
        return False
    for root in sync_roots:
        root_s = posixpath.normpath(os.fspath(root))
        try:
            if posixpath.commonpath([target, root_s]) == root_s:
                return False
        except ValueError:
            continue
    matches = []
    for line in str(mount_output).splitlines():
        match = re.match(r"^.+ on (.+) \(([^)]*)\)$", line.strip())
        if not match:
            continue
        mountpoint = posixpath.normpath(match.group(1).replace("\\040", " "))
        options = {part.strip().lower() for part in match.group(2).split(",")}
        try:
            if posixpath.commonpath([target, mountpoint]) == mountpoint:
                matches.append((len(mountpoint), "local" in options))
        except ValueError:
            continue
    if not matches:
        return False
    return max(matches, key=lambda item: item[0])[1]


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class JobHistory:
    """Bounded atomic JSON snapshot of recent job lifecycle outcomes."""

    def __init__(self, path: Path, *, max_jobs=MAX_JOBS, max_file_bytes=MAX_FILE_BYTES,
                 retention_days=RETENTION_DAYS):
        self.path = Path(path)
        self.max_jobs = max(1, min(int(max_jobs), MAX_JOBS))
        self.max_file_bytes = max(1024, min(int(max_file_bytes), MAX_FILE_BYTES))
        self.retention = timedelta(days=max(1, min(int(retention_days), RETENTION_DAYS)))
        self._session_id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._jobs = self._load()
        self._recover_running()

    def _clean_record(self, record):
        if not isinstance(record, dict):
            return None
        job_id = record.get("id")
        queue_id = record.get("queue_id")
        status = record.get("status")
        phase = record.get("phase")
        mode = record.get("mode")
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            return None
        if not isinstance(queue_id, str) or not _QUEUE_ID.fullmatch(queue_id):
            return None
        if status not in STATUSES or phase not in PHASES or mode not in MODES:
            return None
        started = record.get("started_at")
        updated = record.get("updated_at")
        if not isinstance(started, str) or not isinstance(updated, str):
            return None
        try:
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            return None
        if started_dt.tzinfo is None or updated_dt.tzinfo is None:
            return None
        if updated_dt < _now() - self.retention:
            return None
        rc = record.get("return_code")
        if rc is not None and (not isinstance(rc, int) or isinstance(rc, bool)
                               or not -(2 ** 31) <= rc <= (2 ** 32 - 1)):
            rc = None
        duration = record.get("duration_seconds")
        if duration is not None and (not isinstance(duration, (int, float)) or isinstance(duration, bool)
                                     or duration < 0 or duration > 7 * 24 * 3600):
            duration = None
        error = record.get("error_code")
        if error not in ERROR_CODES:
            error = None
        return {
            "id": job_id,
            "queue_id": queue_id,
            "status": status,
            "phase": phase,
            "mode": mode,
            "started_at": _iso(started_dt),
            "updated_at": _iso(updated_dt),
            "duration_seconds": round(float(duration), 1) if duration is not None else None,
            "return_code": rc,
            "error_code": error,
        }

    def _path_has_reparse_component(self):
        """Reject symlinks/junctions in the state path before read or write."""
        try:
            components = list(reversed(self.path.parents)) + [self.path]
            for component in components:
                try:
                    info = component.lstat()
                except FileNotFoundError:
                    continue
                attributes = getattr(info, "st_file_attributes", 0)
                reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                if stat.S_ISLNK(info.st_mode) or attributes & reparse:
                    return True
            return False
        except OSError:
            return True

    def _load(self):
        try:
            if self._path_has_reparse_component():
                return []
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    return []
                raw_bytes = stream.read(self.max_file_bytes + 1)
            if len(raw_bytes) > self.max_file_bytes:
                return []
            raw = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
                return []
            jobs = raw.get("jobs")
            if not isinstance(jobs, list):
                return []
            cleaned = [x for x in (self._clean_record(item) for item in jobs) if x]
            return cleaned[-self.max_jobs:]
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            # Corrupt, truncated, or inaccessible history never prevents UI startup.
            return []

    def _recover_running(self):
        changed = False
        with self._lock:
            for job in self._jobs:
                if job["status"] == "running":
                    job["status"] = "interrupted"
                    job["phase"] = "complete"
                    job["error_code"] = "server_restart"
                    job["updated_at"] = _iso(_now())
                    changed = True
            if changed:
                self._save_locked()

    def _save_locked(self):
        if self._path_has_reparse_component():
            raise OSError("job history path is a symlink or reparse point")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._path_has_reparse_component():
            raise OSError("job history path is a symlink or reparse point")
        payload = json.dumps({"schema_version": SCHEMA_VERSION, "jobs": self._jobs},
                             ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        # Bound output even if schema evolves: drop oldest records until it fits.
        while len(payload) > self.max_file_bytes and self._jobs:
            self._jobs.pop(0)
            payload = json.dumps({"schema_version": SCHEMA_VERSION, "jobs": self._jobs},
                                 ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix="job-history-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                if os.name != "nt":
                    os.chmod(temp_name, 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def record(self, job_id, *, status, phase, cpu=False, started_at=None,
               duration_seconds=None, return_code=None, error_code=None):
        """Create/update one record; arbitrary caller text is never accepted."""
        if not isinstance(job_id, str) or not _QUEUE_ID.fullmatch(job_id):
            return False
        if status not in STATUSES or phase not in PHASES:
            return False
        if error_code is not None and error_code not in ERROR_CODES:
            error_code = "worker_failed"
        now = _now()
        sequence = job_id.rsplit("-", 1)[1]
        persistent_id = f"run-{self._session_id}-{sequence}"
        with self._lock:
            existing = next((row for row in self._jobs if row["id"] == persistent_id), None)
            started = started_at or (existing["started_at"] if existing else _iso(now))
            candidate = self._clean_record({
                "id": persistent_id, "queue_id": job_id,
                "status": status, "phase": phase,
                "mode": "cpu" if cpu else "gpu_default",
                "started_at": started, "updated_at": _iso(now),
                "duration_seconds": duration_seconds,
                "return_code": return_code,
                "error_code": error_code,
            })
            if candidate is None:
                return False
            self._jobs = [row for row in self._jobs if row["id"] != persistent_id]
            self._jobs.append(candidate)
            cutoff = _now() - self.retention
            self._jobs = [row for row in self._jobs
                          if datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")) >= cutoff]
            self._jobs = self._jobs[-self.max_jobs:]
            try:
                self._save_locked()
                return True
            except OSError:
                # Diagnostics are best effort; OCR/UI jobs continue unaffected.
                return False

    def recent(self, limit=20):
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 20
        with self._lock:
            return [dict(row) for row in reversed(self._jobs[-limit:])]
