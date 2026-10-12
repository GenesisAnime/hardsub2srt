"""Strict local model profile used only by the opt-in Phase 5 runner.

The ordinary product CLI keeps its existing engine resolution behavior. This
module accepts only explicit, hash-pinned local files and never resolves or
downloads weights.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import stat
from pathlib import Path


class OfflineProfileError(ValueError):
    pass


HEX64 = re.compile(r"^[0-9a-f]{64}$")
PROFILE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
ALLOWED_PROFILE_IDS = {
    "none": "hardsub2srt-easyocr-primary-v1",
    "ppocrv5": "hardsub2srt-easyocr-ppocrv5-v1",
}
MAX_PROFILE_BYTES = 256 * 1024
MAX_ASSETS = 64
MAX_ASSET_BYTES = 8 * 1024 * 1024 * 1024
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _no_reparse(path: Path) -> None:
    raw = path.expanduser()
    if not raw.is_absolute():
        raise OfflineProfileError("All profile and model paths must be absolute")
    raw_text = os.fspath(raw)
    if raw_text.startswith(("\\\\", "//")):
        raise OfflineProfileError("UNC/network profile and model paths are not allowed")
    if os.name == "nt":
        drive, _tail = ntpath.splitdrive(raw_text)
        if not drive:
            raise OfflineProfileError("Profile and model paths must use a local drive")
        try:
            import ctypes
            if int(ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")) == 4:
                raise OfflineProfileError("Mapped/network model paths are not allowed")
        except AttributeError:
            raise OfflineProfileError("Could not verify that model drive is local")
    current = Path(raw.anchor)
    for part in raw.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise OfflineProfileError("A profile/model path component is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE:
            raise OfflineProfileError("Symlink/junction/reparse paths are not allowed")


def _local_file(value: object, label: str) -> Path:
    if type(value) is not str or not value:
        raise OfflineProfileError(f"{label}: absolute path required")
    path = Path(value)
    _no_reparse(path)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise OfflineProfileError(f"{label}: local file is missing") from exc
    if not resolved.is_file():
        raise OfflineProfileError(f"{label}: regular file required")
    return resolved


def _load_unique(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    if len(raw) > MAX_PROFILE_BYTES:
        raise OfflineProfileError("Profile exceeds its size limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise OfflineProfileError("Duplicate JSON key in offline profile")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OfflineProfileError("Offline profile is not valid UTF-8 JSON") from exc
    if type(value) is not dict:
        raise OfflineProfileError("Offline profile must be a JSON object")
    return value, raw


def _asset_entries(items: object, label: str) -> list[tuple[str, Path, str]]:
    if type(items) is not list or not items or len(items) > MAX_ASSETS:
        raise OfflineProfileError(f"{label}: bounded non-empty asset list required")
    seen_paths, result, total = set(), [], 0
    for entry in items:
        if type(entry) is not dict or set(entry) != {"path", "sha256"}:
            raise OfflineProfileError(f"{label}: asset needs exactly path and sha256")
        if type(entry["sha256"]) is not str or not HEX64.fullmatch(entry["sha256"]):
            raise OfflineProfileError(f"{label}: lowercase SHA-256 required")
        path = _local_file(entry["path"], label)
        key = os.path.normcase(os.fspath(path))
        if key in seen_paths:
            raise OfflineProfileError(f"{label}: duplicate asset path")
        seen_paths.add(key)
        size = path.stat().st_size
        total += size
        if size <= 0 or total > MAX_ASSET_BYTES:
            raise OfflineProfileError(f"{label}: empty asset or total size limit exceeded")
        actual = _sha256(path)
        if actual != entry["sha256"]:
            raise OfflineProfileError(f"{label}: asset SHA-256 mismatch")
        result.append((key, path, actual))
    return result


def load_profile(path: Path) -> dict:
    """Load and validate exact local assets; return normalized runtime values."""
    path = path.expanduser()
    _no_reparse(path)
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise OfflineProfileError("Offline profile file is missing") from exc
    if not path.is_file():
        raise OfflineProfileError("Offline profile must be a regular file")
    doc, raw = _load_unique(path)
    required = {"schema_version", "profile_id", "device_preference", "languages",
                "easyocr_model_dir", "easyocr_assets", "secondary_engine",
                "secondary_assets"}
    if set(doc) != required or type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
        raise OfflineProfileError("schema_version 1 with the exact profile fields is required")
    if type(doc["profile_id"]) is not str or not PROFILE_ID.fullmatch(doc["profile_id"]):
        raise OfflineProfileError("profile_id is not a safe identifier")
    if doc["device_preference"] not in {"gpu_default", "cpu"}:
        raise OfflineProfileError("device_preference must be gpu_default or cpu")
    languages = doc["languages"]
    if (type(languages) is not list or not languages or len(languages) > 8
            or any(type(x) is not str or not re.fullmatch(r"[a-z]{2,3}(?:_[a-z]{2,3})?", x)
                   for x in languages)
            or len(set(languages)) != len(languages)):
        raise OfflineProfileError("languages must be a bounded unique list of EasyOCR codes")

    model_dir_raw = doc["easyocr_model_dir"]
    if type(model_dir_raw) is not str or not model_dir_raw:
        raise OfflineProfileError("easyocr_model_dir must be an absolute directory")
    model_dir = Path(model_dir_raw).expanduser()
    _no_reparse(model_dir)
    try:
        model_dir = model_dir.resolve(strict=True)
    except OSError as exc:
        raise OfflineProfileError("EasyOCR model directory is missing") from exc
    if not model_dir.is_dir():
        raise OfflineProfileError("EasyOCR model directory is not a directory")
    easy_assets = _asset_entries(doc["easyocr_assets"], "EasyOCR assets")
    if any(not Path(key).is_relative_to(model_dir) for key, _asset, _sha in easy_assets):
        raise OfflineProfileError("Every EasyOCR asset must be inside easyocr_model_dir")
    model_files = set()
    model_weights = set()
    inventory_entries = 0
    inventory_bytes = 0
    for child in model_dir.rglob("*"):
        inventory_entries += 1
        if inventory_entries > MAX_ASSETS * 4:
            raise OfflineProfileError("EasyOCR model directory has too many files/folders")
        try:
            info = child.lstat()
        except OSError as exc:
            raise OfflineProfileError("Could not inspect EasyOCR model directory") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE:
            raise OfflineProfileError("EasyOCR model directory may not contain reparse paths")
        if child.is_file():
            inventory_bytes += info.st_size
            if inventory_bytes > MAX_ASSET_BYTES:
                raise OfflineProfileError("EasyOCR model directory exceeds its total size limit")
            key = os.path.normcase(os.fspath(child.resolve(strict=True)))
            model_files.add(key)
            if child.suffix.lower() in {".pth", ".pt"}:
                model_weights.add(key)
    declared_weights = {key for key, item, _sha in easy_assets
                       if item.suffix.lower() in {".pth", ".pt"}}
    declared_files = {key for key, _item, _sha in easy_assets}
    if (not model_weights or model_weights != declared_weights
            or model_files != declared_files):
        raise OfflineProfileError("Every file in the EasyOCR model directory must be explicitly hash-pinned")

    secondary = doc["secondary_engine"]
    if secondary == "none":
        if doc["secondary_assets"] != []:
            raise OfflineProfileError("Disabled second engine cannot declare model assets")
        secondary_assets = {}
    elif secondary == "ppocrv5":
        entries = doc["secondary_assets"]
        if type(entries) is not dict or set(entries) != {"det", "rec", "dictionary"}:
            raise OfflineProfileError("PP-OCRv5 needs exact det/rec/dictionary file paths")
        secondary_assets = {}
        for role, value in entries.items():
            if type(value) is not dict or set(value) != {"path", "sha256"}:
                raise OfflineProfileError(f"PP-OCRv5 {role} asset needs path and sha256")
            actual = _asset_entries([value], f"PP-OCRv5 {role}")[0][2]
            asset_path = Path(value["path"]).expanduser().resolve(strict=True)
            suffixes = {"det": {".onnx"}, "rec": {".onnx"}, "dictionary": {".txt"}}
            if asset_path.suffix.lower() not in suffixes[role]:
                raise OfflineProfileError(f"PP-OCRv5 {role} has an unsupported file type")
            secondary_assets[role] = os.fspath(asset_path)
    else:
        raise OfflineProfileError("Only none or explicitly pinned ppocrv5 is allowlisted")
    if doc["profile_id"] != ALLOWED_PROFILE_IDS[secondary]:
        raise OfflineProfileError("profile_id does not match the allowlisted OCR engine composition")
    return {
        "profile_id": doc["profile_id"], "device_preference": doc["device_preference"],
        "languages": list(languages), "easyocr_model_dir": os.fspath(model_dir),
        "easyocr_assets": [{"path": os.fspath(p), "sha256": digest}
                           for _key, p, digest in easy_assets],
        "secondary_engine": secondary, "secondary_assets": secondary_assets,
        "secondary_asset_hashes": ({role: value["sha256"]
                                     for role, value in doc["secondary_assets"].items()}
                                    if secondary == "ppocrv5" else {}),
        "profile_path": os.fspath(path), "profile_sha256": hashlib.sha256(raw).hexdigest(),
    }
