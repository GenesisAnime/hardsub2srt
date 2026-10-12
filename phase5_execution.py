"""Fail-closed, local full-video OCR execution for a frozen Phase 5 plan.

This opt-in runner calls only this repository's product CLI, with an explicit
hash-pinned offline OCR profile. It never accepts arbitrary commands. Its local
execution receipt is not legal-rights proof, trusted external evidence, or a
Phase 6 release result.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as package_metadata
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import ocr_experiment as prep
import ocr_video_eval as video_eval
import ocr_video_eval_templates as templates
from offline_ocr_profile import load_profile


class ExecutionError(ValueError):
    pass


ROOT = Path(__file__).resolve().parent
TRUST_REGISTRY = ROOT / "schemas" / "trusted-phase5-execution-receipts-v1.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
MAX_PLAN_BYTES = 1024 * 1024
MAX_RIGHTS_BYTES = 1024 * 1024
PROCESS_TIMEOUT_SECONDS = 24 * 60 * 60
RECEIPT_DECISION = "APPROVED_FOR_LOCAL_PHASE5_EXECUTION"
FIXED_PRODUCT_ARGS = ["--ocr", "paddle", "--qa", "0"]
SOURCE_SKIP_DIRS = {".git", "__pycache__", "tests", ".venv", "venv", "site-packages"}
CHILD_ENV_KEYS = (
    "PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOME",
    "APPDATA", "LOCALAPPDATA", "CUDA_PATH", "CUDA_HOME", "CUDA_VISIBLE_DEVICES",
    "CUDA_DEVICE_ORDER", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "LANG", "LC_ALL", "TZ",
)
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PYTHONNOUSERSITE": "1",
}
RUNTIME_DISTRIBUTIONS = (
    "opencv-python", "opencv-python-headless", "numpy", "easyocr", "torch",
    "rapidocr", "rapidocr-onnxruntime", "onnxruntime", "onnxruntime-gpu",
)


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_unique(path: Path, maximum: int) -> tuple[dict, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExecutionError("A required local JSON input is unavailable") from exc
    if len(raw) > maximum:
        raise ExecutionError("A required local JSON input exceeds its size limit")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ExecutionError("Duplicate JSON key in Phase 5 execution input")
            out[key] = value
        return out

    try:
        doc = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutionError("Invalid UTF-8 JSON in Phase 5 execution input") from exc
    if type(doc) is not dict:
        raise ExecutionError("Phase 5 execution JSON object required")
    return doc, raw


def _abs_path(value: object, label: str, *, directory: bool | None = None) -> Path:
    if type(value) is not str or not value:
        raise ExecutionError(f"{label}: absolute local path required")
    raw = Path(value).expanduser()
    if not raw.is_absolute() or os.fspath(raw).startswith(("\\\\", "//")):
        raise ExecutionError(f"{label}: absolute local path required; UNC/network paths are rejected")
    try:
        templates._reject_network_output_parent(os.fspath(raw))
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ExecutionError(f"{label}: path is missing") from exc
    except templates.TemplateError as exc:
        raise ExecutionError(f"{label}: local fixed-drive path required") from exc
    try:
        templates._reject_reparse_components(raw)
    except templates.TemplateError as exc:
        raise ExecutionError(f"{label}: symlink/junction/reparse paths are rejected") from exc
    if directory is True and not resolved.is_dir():
        raise ExecutionError(f"{label}: directory required")
    if directory is False and not resolved.is_file():
        raise ExecutionError(f"{label}: regular file required")
    return resolved


def _load_rights_receipt(path: Path, bindings: dict) -> tuple[dict, str]:
    doc, raw = _json_unique(path, MAX_RIGHTS_BYTES)
    expected = {
        "schema_version", "review_id", "decision", "reviewer", "reviewed_at", "scope",
        "dataset_manifest_sha256", "dataset_metadata_sha256", "split_manifest_sha256",
        "evaluation_plan_sha256", "experiment_report_sha256", "reference_manifest_sha256",
        "media_sha256s", "profile_sha256s",
    }
    if set(doc) != expected or type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
        raise ExecutionError("Local execution rights receipt v1 exact fields required")
    if doc["decision"] != RECEIPT_DECISION:
        raise ExecutionError("Rights receipt decision does not approve this local execution scope")
    for key in ("review_id", "scope"):
        if type(doc[key]) is not str or not doc[key].strip():
            raise ExecutionError(f"Rights receipt {key} must be non-empty")
    reviewer = doc["reviewer"]
    if (type(reviewer) is not dict or set(reviewer) != {"reviewer_id", "authority"}
            or any(type(reviewer[x]) is not str or not reviewer[x].strip()
                   for x in ("reviewer_id", "authority"))):
        raise ExecutionError("Rights receipt reviewer identity and authority required")
    try:
        reviewed_at = datetime.fromisoformat(doc["reviewed_at"].replace("Z", "+00:00"))
        if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
            raise ValueError
    except (TypeError, ValueError, AttributeError) as exc:
        raise ExecutionError("Rights receipt reviewed_at needs timezone-aware ISO-8601") from exc
    for key, value in bindings.items():
        if doc.get(key) != value:
            raise ExecutionError(f"Rights receipt is not bound to current input: {key}")
    media = doc["media_sha256s"]
    profiles = doc["profile_sha256s"]
    if media != bindings["media_sha256s"] or profiles != bindings["profile_sha256s"]:
        raise ExecutionError("Rights receipt media/profile hash maps do not match")
    digest = _sha_bytes(raw)
    return doc, digest


def _trusted_rights_hashes(path: Path | None = None) -> tuple[set[str], str]:
    path = TRUST_REGISTRY if path is None else path
    try:
        templates._reject_reparse_components(path)
    except templates.TemplateError as exc:
        raise ExecutionError("Pinned trust registry may not use symlink/junction paths") from exc
    doc, raw = _json_unique(path, 128 * 1024)
    if (set(doc) != {"schema_version", "purpose", "trusted_receipt_sha256s"}
            or type(doc.get("schema_version")) is not int or doc["schema_version"] != 1
            or type(doc.get("purpose")) is not str or not doc["purpose"].strip()
            or type(doc.get("trusted_receipt_sha256s")) is not list):
        raise ExecutionError("Pinned Phase 5 execution trust registry is invalid")
    values = doc["trusted_receipt_sha256s"]
    if any(type(value) is not str or not SHA256_RE.fullmatch(value) for value in values):
        raise ExecutionError("Pinned trust registry contains an invalid receipt digest")
    if len(values) != len(set(values)):
        raise ExecutionError("Pinned trust registry contains duplicate receipt digests")
    return set(values), _sha_bytes(raw)


def _profile_model_sha(profile: dict) -> str:
    value = {
        "easyocr_assets": profile["easyocr_assets"],
        "secondary_engine": profile["secondary_engine"],
        "secondary_assets": profile["secondary_assets"],
        "secondary_asset_hashes": profile["secondary_asset_hashes"],
    }
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return _sha_bytes(raw)


def _model_paths(profile: dict) -> list[Path]:
    result = [Path(item["path"]) for item in profile["easyocr_assets"]]
    result.extend(Path(path) for path in profile["secondary_assets"].values())
    return sorted(result, key=lambda p: os.path.normcase(os.fspath(p)))


def _input_bindings(plan_path: Path, doc: dict) -> dict:
    required = {"schema_version", "run_id", "dataset", "metadata", "split",
                "experiment", "references", "media", "profiles", "rights_receipt",
                "output_parent"}
    if set(doc) != required or type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
        raise ExecutionError("Phase 5 execution plan v1 exact fields required")
    if type(doc["run_id"]) is not str or not RUN_ID_RE.fullmatch(doc["run_id"]):
        raise ExecutionError("run_id is not a safe identifier")
    plan_sha = _sha_file(plan_path)
    dataset = _abs_path(doc["dataset"], "dataset", directory=True)
    metadata_path = _abs_path(doc["metadata"], "metadata", directory=False)
    split_path = _abs_path(doc["split"], "split", directory=False)
    experiment_path = _abs_path(doc["experiment"], "experiment report", directory=False)
    references_path = _abs_path(doc["references"], "timed references", directory=False)
    rights_path = _abs_path(doc["rights_receipt"], "rights receipt", directory=False)
    profiles_doc = doc["profiles"]
    if type(profiles_doc) is not list or len(profiles_doc) != 2:
        raise ExecutionError("Exactly baseline and candidate local profiles are required")
    groups, experiment_doc, manifest_hash, metadata_hash, split_hash = video_eval._validated_split(
        experiment_path, split_path, dataset, metadata_path)
    refs, refs_hash, _language = video_eval._load_references(references_path, groups)
    split_doc, split_raw = _json_unique(split_path, video_eval.MAX_JSON_BYTES)
    if _sha_bytes(split_raw) != split_hash:
        raise ExecutionError("Split changed while the frozen plan was validated")
    plan_doc, plan_doc_hash = prep.validate_evaluation_plan(
        experiment_path.parent / "evaluation-plan-v1.json",
        split_doc, manifest_hash, metadata_hash)
    experiment_report_hash = _sha_file(experiment_path)
    baseline_id = experiment_doc["experiment"]["baseline_id"]
    candidate_id = experiment_doc["experiment"]["candidate_id"]
    expected_engines = {baseline_id, candidate_id}
    if (not isinstance(baseline_id, str) or not video_eval.ENGINE_ID_RE.fullmatch(baseline_id)
            or not isinstance(candidate_id, str) or not video_eval.ENGINE_ID_RE.fullmatch(candidate_id)):
        raise ExecutionError("Frozen engine IDs are not safe allowlisted identifiers")
    media_doc = doc["media"]
    if type(media_doc) is not list or len(media_doc) != len(groups):
        raise ExecutionError("Exactly one local media path is required for every frozen source/episode group")
    media_by_group = {}
    media_paths = []
    media_hashes = {}
    for item in media_doc:
        if type(item) is not dict or set(item) != {"source_id", "episode_id", "path", "sha256"}:
            raise ExecutionError("Media plan rows need exactly source_id, episode_id, path and sha256")
        key = prep._group_key(item)
        if key not in groups or key in media_by_group:
            raise ExecutionError("Media group is unknown or duplicated")
        path = _abs_path(item["path"], "input media", directory=False)
        expected_sha = refs[key]["media_sha256"]
        if type(item["sha256"]) is not str or not SHA256_RE.fullmatch(item["sha256"]):
            raise ExecutionError("Input media SHA-256 required")
        actual_sha = _sha_file(path)
        if actual_sha != item["sha256"] or actual_sha != expected_sha:
            raise ExecutionError("Input media hash differs from plan/timed reference")
        media_by_group[key] = {"source_id": item["source_id"], "episode_id": item["episode_id"],
                               "path": path, "sha256": actual_sha, "split": groups[key]}
        media_paths.append(path)
        media_hashes[hashlib.sha256(key.encode("utf-8")).hexdigest()] = actual_sha
    if set(media_by_group) != set(groups):
        raise ExecutionError("Media plan does not cover the frozen split exactly")

    profiles = {}
    profile_hashes = {}
    model_roots = []
    all_model_paths = []
    for item in profiles_doc:
        if type(item) is not dict or set(item) != {"engine_id", "profile_path", "profile_sha256"}:
            raise ExecutionError("Profile rows need engine_id, profile_path and profile_sha256")
        engine_id = item["engine_id"]
        if type(engine_id) is not str or engine_id not in expected_engines or engine_id in profiles:
            raise ExecutionError("Profiles must map exactly to the frozen baseline and candidate IDs")
        profile_path = _abs_path(item["profile_path"], "offline OCR profile", directory=False)
        actual_profile_hash = _sha_file(profile_path)
        if item["profile_sha256"] != actual_profile_hash:
            raise ExecutionError("Offline OCR profile SHA-256 differs from the plan")
        profile = load_profile(profile_path)
        if profile["profile_sha256"] != actual_profile_hash:
            raise ExecutionError("Offline OCR profile changed while loading")
        profiles[engine_id] = {"path": profile_path, "sha256": actual_profile_hash,
                               "runtime": profile, "model_sha256": _profile_model_sha(profile),
                               "model_paths": _model_paths(profile)}
        profile_hashes[engine_id] = actual_profile_hash
        model_roots.append(Path(profile["easyocr_model_dir"]))
        all_model_paths.extend(profiles[engine_id]["model_paths"])
    if set(profiles) != expected_engines:
        raise ExecutionError("Both frozen engine profiles are required")

    bindings = {
        "dataset_manifest_sha256": manifest_hash,
        "dataset_metadata_sha256": metadata_hash,
        "split_manifest_sha256": split_hash,
        "evaluation_plan_sha256": plan_doc_hash,
        "experiment_report_sha256": experiment_report_hash,
        "reference_manifest_sha256": refs_hash,
        "media_sha256s": dict(sorted(media_hashes.items())),
        "profile_sha256s": dict(sorted(profile_hashes.items())),
    }
    rights_doc, rights_hash = _load_rights_receipt(rights_path, bindings)
    trusted_hashes, trust_registry_hash = _trusted_rights_hashes()
    if rights_hash not in trusted_hashes:
        raise ExecutionError("Rights receipt hash is not pinned in the project Phase 5 trust registry")

    out_parent_raw = doc["output_parent"]
    if type(out_parent_raw) is not str:
        raise ExecutionError("output_parent must be an absolute local directory")
    protected = [dataset, dataset.parent, metadata_path.parent, split_path.parent,
                 experiment_path.parent, references_path.parent, rights_path,
                 rights_path.parent, plan_path.resolve(strict=True), plan_path.parent,
                 *media_paths, *(path.parent for path in media_paths), *model_roots,
                 *all_model_paths, *(path.parent for path in all_model_paths),
                 *(profile["path"] for profile in profiles.values()),
                 *(profile["path"].parent for profile in profiles.values()), ROOT]
    try:
        out_parent = templates._safe_output_parent(Path(out_parent_raw), protected)
    except templates.TemplateError as exc:
        raise ExecutionError(f"Unsafe output parent: {exc}") from exc
    return {
        "run_id": doc["run_id"], "plan_path": plan_path, "plan_sha256": plan_sha,
        "dataset": dataset, "metadata": metadata_path, "split": split_path,
        "experiment": experiment_path, "references": references_path,
        "groups": groups, "reference_rows": refs, "profiles": profiles,
        "media": media_by_group, "rights_path": rights_path,
        "rights_receipt_sha256": rights_hash, "rights_review_id": rights_doc["review_id"],
        "bindings": bindings, "trust_registry_sha256": trust_registry_hash,
        "output_parent": out_parent, "baseline_id": baseline_id,
        "candidate_id": candidate_id, "evaluation_plan": plan_doc,
        "experiment_doc": experiment_doc,
    }


def _product_executor(media_path: Path, srt_path: Path, profile: dict,
                      child_env: dict[str, str], runtime: dict) -> None:
    """Invoke only the fixed product CLI command; no shell or plan-supplied argv."""
    args = [sys.executable, "-S", os.fspath(ROOT / "phase5_offline_bootstrap.py"),
            os.fspath(ROOT / "hardsub2srt.py"), os.fspath(media_path),
            "--out", os.fspath(srt_path), "--offline-profile", profile["path"],
            *FIXED_PRODUCT_ARGS, "--lang", ",".join(profile["runtime"]["languages"])]
    if profile["runtime"]["device_preference"] == "cpu":
        args.append("--cpu")
    env = dict(child_env)
    for name in ("ffmpeg", "ffprobe"):
        recorded = runtime["media_tools"].get(name)
        current = shutil.which(name, path=env.get("PATH", ""))
        if not recorded or current != recorded["path"]:
            raise ExecutionError(f"Effective child PATH no longer resolves to the recorded {name}")
    try:
        _run_product_command(args, env, timeout=PROCESS_TIMEOUT_SECONDS)
    except OSError as exc:
        raise ExecutionError(f"Product CLI could not start ({type(exc).__name__})") from None


class ProcessTreeUnstopped(ExecutionError):
    """A timed-out child tree may still be alive; staging must be preserved."""


def _terminate_process_tree(process) -> bool:
    """Terminate and wait for the complete product process tree on timeout."""
    if os.name == "nt":
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
            taskkill = Path(buffer.value) / "taskkill.exe" if length else None
        except (AttributeError, OSError):
            taskkill = None
        if taskkill is None or not taskkill.is_file():
            return False
        try:
            killed = subprocess.run([os.fspath(taskkill), "/PID", str(process.pid), "/T", "/F"],
                                    shell=False, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=20, check=False)
            if killed.returncode != 0:
                return False
            process.wait(timeout=30)
            return process.poll() is not None
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        import signal
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        return False
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_product_command(args: list[str], env: dict[str, str], *, timeout: int,
                         process_factory=None, tree_terminator=None) -> None:
    """Run fixed argv and confirm process-tree shutdown before timeout failure returns."""
    process_factory = subprocess.Popen if process_factory is None else process_factory
    tree_terminator = _terminate_process_tree if tree_terminator is None else tree_terminator
    kwargs = {"cwd": ROOT, "env": env, "shell": False,
              "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = process_factory(args, **kwargs)
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if not tree_terminator(process):
            raise ProcessTreeUnstopped(
                "Timed-out product process tree could not be confirmed stopped; staging is retained") from exc
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired as stop_exc:
            raise ProcessTreeUnstopped(
                "Timed-out product parent did not exit after tree termination; staging is retained") from stop_exc
        if process.poll() is None:
            raise ProcessTreeUnstopped(
                "Timed-out product parent still appears alive; staging is retained")
        raise ExecutionError("Product CLI exceeded the execution timeout; process tree stopped") from None
    if return_code != 0:
        # Do not echo subprocess logs; OCR output and local paths may be sensitive.
        raise ExecutionError(f"Product CLI failed with exit code {return_code}")


def _media_identity(path: Path) -> dict:
    stat = path.stat()
    with path.open("rb") as stream:
        prefix = stream.read(1024 * 1024)
    digest = hashlib.sha1(prefix).hexdigest()
    return {"ad": path.name, "boyut": stat.st_size,
            "sha1_ilk_1MB": digest}


def _configured_device(value: object) -> str:
    if type(value) is not str:
        raise ExecutionError("Stats do not report EasyOCR configured device")
    normalized = value.strip().lower()
    if normalized.startswith(("cuda", "gpu")):
        return "gpu"
    if normalized.startswith("cpu"):
        return "cpu"
    # MPS/Metal is an accelerator, not CPU. Unsupported devices fail closed.
    raise ExecutionError("EasyOCR configured device is unknown or unsupported; refusing to label it CPU/GPU")


def _check_outputs(media: dict, srt_path: Path, stats_path: Path,
                   profile: dict) -> tuple[list[dict], dict]:
    try:
        templates._reject_reparse_components(srt_path)
        templates._reject_reparse_components(stats_path)
    except templates.TemplateError as exc:
        raise ExecutionError("Product output passed through a symlink/junction") from exc
    if not srt_path.is_file() or not stats_path.is_file():
        raise ExecutionError("Product CLI did not produce both SRT and stats files")
    try:
        srt_cues = video_eval._read_srt(srt_path, "offline Phase 5 prediction")
    except video_eval.VideoEvalError as exc:
        raise ExecutionError("Product output SRT failed strict parsing") from exc
    stats, _raw = _json_unique(stats_path, 16 * 1024 * 1024)
    video = stats.get("video")
    identity = _media_identity(media["path"])
    if (type(video) is not dict or video.get("ad") != identity["ad"]
            or video.get("boyut") != identity["boyut"]
            or video.get("sha1_ilk_1MB") != identity["sha1_ilk_1MB"]):
        raise ExecutionError("Stats video identity does not match planned input media")
    offline = stats.get("offline_experiment")
    if (type(offline) is not dict
            or offline.get("profile_sha256") != profile["runtime"]["profile_sha256"]
            or offline.get("profile_id") != profile["runtime"]["profile_id"]
            or offline.get("phase6_eligible") is not False
            or offline.get("rights_verified") is not False):
        raise ExecutionError("Stats lack the pinned offline execution profile receipt")
    offline["_configured_device"] = _configured_device(
        offline.get("primary_reader_configured_device"))
    if (profile["runtime"]["secondary_engine"] == "ppocrv5"
            and offline.get("secondary_engine_used") == "pp-ocrv5-offline"
            and not offline.get("secondary_ocr_session_configured_providers")):
        raise ExecutionError("Used PP-OCRv5 session did not expose configured ONNX providers")
    srt_sha = _sha_file(srt_path)
    if stats.get("main_srt_sha256") not in (None, srt_sha):
        raise ExecutionError("Stats SRT hash does not match output bytes")
    if type(srt_cues) is not list:
        raise ExecutionError("SRT parser returned an invalid cue collection")
    return srt_cues, stats


def _source_hashes() -> dict[str, str]:
    """Hash the complete local Python source bundle plus behavior dictionaries.

    This works in a Desktop copy without .git and includes untracked Python
    files, so a commit ID alone is never treated as the executed source.
    """
    result = {}
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if SOURCE_SKIP_DIRS.intersection(part.lower() for part in relative.parts):
            continue
        try:
            templates._reject_reparse_components(path)
        except templates.TemplateError as exc:
            raise ExecutionError("Application source path uses a symlink/junction/reparse point") from exc
        if not path.resolve(strict=True).is_relative_to(ROOT):
            raise ExecutionError("Application source resolves outside the product root")
        result[relative.as_posix()] = _sha_file(path)
    for name in ("kullanici-sozlugu.txt", "turkce-sozluk.txt", "requirements.txt"):
        path = ROOT / name
        if path.is_file():
            try:
                templates._reject_reparse_components(path)
            except templates.TemplateError as exc:
                raise ExecutionError("Application/config source path uses a reparse point") from exc
            result[name] = _sha_file(path)
        elif name == "turkce-sozluk.txt":
            result[f"{name}:absent"] = _sha_bytes(b"optional-local-file-absent")
        elif name == "requirements.txt":
            raise ExecutionError("requirements.txt is missing from the source bundle")
    if not result:
        raise ExecutionError("No executable Python source was found for the source bundle")
    return dict(sorted(result.items()))


def _child_environment() -> tuple[dict[str, str], dict]:
    """Create an allowlisted child environment with proxy/Python overrides removed."""
    child = {key: os.environ[key] for key in CHILD_ENV_KEYS if key in os.environ}
    child.update(OFFLINE_ENV)
    removed_proxy_keys = sorted(
        key for key in os.environ
        if key.casefold() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    )
    return child, {
        "sha256s": {key: _sha_bytes(value.encode("utf-8", errors="surrogatepass"))
                    for key, value in sorted(child.items())},
        "removed_proxy_variables": removed_proxy_keys,
        "fixed_offline_flags": OFFLINE_ENV,
        "python_override_variables_removed": ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"],
    }


def _runtime_provenance(child_env: dict[str, str], environment: dict,
                        profiles: dict, *, require_available: bool) -> dict:
    python_path = _abs_path(sys.executable, "Python executable", directory=False)
    sys_paths = sysconfig.get_paths()
    package_roots = [Path(sys_paths[name]).resolve(strict=True)
                     for name in ("purelib", "platlib") if sys_paths.get(name)]
    distributions = {}
    package_locations = {}
    for name in RUNTIME_DISTRIBUTIONS:
        try:
            distribution = package_metadata.distribution(name)
            distributions[name] = distribution.version
            package_locations[name] = os.fspath(Path(distribution.locate_file("")).resolve(strict=True))
        except package_metadata.PackageNotFoundError:
            distributions[name] = None
            package_locations[name] = None
    required = {"numpy", "easyocr", "torch"}
    if any(value["runtime"]["secondary_engine"] == "ppocrv5"
           for value in profiles.values()):
        required.add("rapidocr")
        onnx_distribution = ("onnxruntime-gpu" if distributions["onnxruntime-gpu"]
                             else "onnxruntime" if distributions["onnxruntime"] else None)
        if onnx_distribution is None:
            raise ExecutionError("PP-OCRv5 needs an installed ONNX Runtime distribution")
        required.add(onnx_distribution)
    if require_available:
        opencv_names = ("opencv-python", "opencv-python-headless")
        if not any(distributions[name] for name in opencv_names):
            raise ExecutionError("An OpenCV runtime distribution is required")
        missing = sorted(name for name in required if not distributions.get(name))
        if missing:
            raise ExecutionError("Required OCR runtime package metadata is missing: " + ", ".join(missing))
        for name in required | {name for name in opencv_names if distributions[name]}:
            location = package_locations.get(name)
            if location is None or not any(Path(location).is_relative_to(root) for root in package_roots):
                raise ExecutionError(f"Required runtime package is outside isolated global site-packages: {name}")
    tools = {}
    if require_available and not child_env.get("PATH"):
        raise ExecutionError("An explicit child PATH is required to resolve local FFmpeg tools")
    for name in ("ffmpeg", "ffprobe"):
        found = shutil.which(name, path=child_env.get("PATH", ""))
        if not found:
            if require_available:
                raise ExecutionError(f"Required local media tool is unavailable: {name}")
            tools[name] = None
            continue
        path = _abs_path(found, f"{name} executable", directory=False)
        tools[name] = {"path": os.fspath(path), "sha256": _sha_file(path)}
    return {
        "python": {"version": sys.version.split()[0], "executable_sha256": _sha_file(python_path),
                   "startup_mode": "-S isolated bootstrap; .pth/sitecustomize and user site disabled",
                   "package_roots": [os.fspath(path) for path in package_roots]},
        "packages": distributions,
        "package_locations": package_locations,
        "media_tools": tools,
        "environment": environment,
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                shell=False, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=10, check=False,
                                text=True, encoding="ascii")
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", value) else None


def _atomic_json_new(path: Path, doc: dict) -> None:
    raw = (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(raw)
        os.link(temp, path)
    except FileExistsError as exc:
        raise ExecutionError("Output artifact already exists; refusing overwrite") from exc
    finally:
        temp.unlink(missing_ok=True)


def _revalidate(ctx: dict) -> None:
    if _sha_file(ctx["plan_path"]) != ctx["plan_sha256"]:
        raise ExecutionError("Execution plan changed during the run")
    for media in ctx["media"].values():
        if _sha_file(media["path"]) != media["sha256"]:
            raise ExecutionError("Input media changed during the run")
    groups, _experiment, manifest, metadata, split = video_eval._validated_split(
        ctx["experiment"], ctx["split"], ctx["dataset"], ctx["metadata"])
    refs, ref_hash, _language = video_eval._load_references(ctx["references"], groups)
    if (manifest != ctx["bindings"]["dataset_manifest_sha256"]
            or metadata != ctx["bindings"]["dataset_metadata_sha256"]
            or split != ctx["bindings"]["split_manifest_sha256"]
            or ref_hash != ctx["bindings"]["reference_manifest_sha256"]):
        raise ExecutionError("Dataset, experiment, split, metadata or reference drift detected")
    if refs != ctx["reference_rows"]:
        raise ExecutionError("Timed-reference data changed during the run")
    if _sha_file(ctx["experiment"]) != ctx["bindings"]["experiment_report_sha256"]:
        raise ExecutionError("Experiment report changed during the run")
    rights_raw = _sha_file(ctx["rights_path"])
    if rights_raw != ctx["rights_receipt_sha256"]:
        raise ExecutionError("Rights receipt changed during the run")
    trusted, trust_hash = _trusted_rights_hashes()
    if (trust_hash != ctx["trust_registry_sha256"]
            or ctx["rights_receipt_sha256"] not in trusted):
        raise ExecutionError("Pinned trust registry changed during the run")
    for profile in ctx["profiles"].values():
        try:
            loaded = load_profile(profile["path"])
        except ValueError as exc:
            raise ExecutionError("Offline profile/model assets changed or became invalid") from exc
        if loaded["profile_sha256"] != profile["sha256"]:
            raise ExecutionError("Offline profile changed during the run")
        for item in profile["runtime"]["easyocr_assets"]:
            if _sha_file(Path(item["path"])) != item["sha256"]:
                raise ExecutionError("EasyOCR model asset changed during the run")
        for role, asset_path in profile["runtime"]["secondary_assets"].items():
            # Profile reload already verifies each declared secondary hash;
            # this explicit second pass keeps the pre/post comparison visible.
            if _sha_file(Path(asset_path)) != next(
                    item["sha256"] for item in _secondary_profile_entries(profile["path"])
                    if item[0] == role):
                raise ExecutionError("RapidOCR model asset changed during the run")


def _secondary_profile_entries(profile_path: Path) -> list[tuple[str, str]]:
    raw, _ = _json_unique(profile_path, 256 * 1024)
    values = raw.get("secondary_assets")
    if type(values) is not dict:
        return []
    return [(role, entry["sha256"]) for role, entry in values.items()
            if type(entry) is dict and type(entry.get("sha256")) is str]


def _run(plan_path: Path, executor=None) -> Path:
    plan_path = _abs_path(os.fspath(plan_path), "execution plan", directory=False)
    doc, _ = _json_unique(plan_path, MAX_PLAN_BYTES)
    ctx = _input_bindings(plan_path, doc)
    # Re-hash the selected project trust registry immediately before creating
    # output, and ensure the exact approved receipt pin remains present.
    trusted, trust_hash = _trusted_rights_hashes()
    if ctx["rights_receipt_sha256"] not in trusted:
        raise ExecutionError("Pinned rights receipt disappeared before execution")
    ctx["trust_registry_sha256"] = trust_hash
    source_hashes = _source_hashes()
    source_bundle_hash = _sha_bytes(json.dumps(source_hashes, ensure_ascii=False,
                                                sort_keys=True, separators=(",", ":")).encode("utf-8"))
    child_env, environment = _child_environment()
    use_product_cli = executor is None
    runtime = _runtime_provenance(child_env, environment, ctx["profiles"],
                                  require_available=use_product_cli)
    commit = _git_commit()
    run_uuid = uuid.uuid4().hex
    staging = ctx["output_parent"] / f".phase5-{ctx['run_id']}-{run_uuid}.tmp"
    final = ctx["output_parent"] / f"phase5-{ctx['run_id']}-{run_uuid}"
    staging.mkdir(mode=0o700)
    start = time.monotonic()
    predictions = []
    if executor is None:
        executor = lambda media, output, profile: _product_executor(
            media, output, profile, child_env, runtime)
    try:
        for engine_id in (ctx["baseline_id"], ctx["candidate_id"]):
            profile = ctx["profiles"][engine_id]
            engine_dir = staging / ("baseline" if engine_id == ctx["baseline_id"] else "candidate")
            engine_dir.mkdir()
            for key, media in sorted(ctx["media"].items()):
                group_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
                srt_path = engine_dir / f"{group_digest}.srt"
                executor(media["path"], srt_path, profile)
                stats_path = srt_path.with_suffix(".stats.json")
                srt_cues, stats = _check_outputs(media, srt_path, stats_path, profile)
                configured_device = stats["offline_experiment"]["_configured_device"]
                if profile["runtime"]["device_preference"] == "cpu" and configured_device != "cpu":
                    raise ExecutionError("CPU-only profile reports a non-CPU configured device")
                offline = stats["offline_experiment"]
                configured_backend = (f"easyocr;secondary={offline['secondary_engine']};"
                                      f"onnx_probe_session_configured={','.join(offline.get('secondary_probe_session_configured_providers', [])) or 'not-observed'};"
                                      f"onnx_ocr_session_configured={','.join(offline.get('secondary_ocr_session_configured_providers', [])) or 'not-invoked'}")
                predictions.append({
                    "engine_id": engine_id, "source_id": media["source_id"],
                    "episode_id": media["episode_id"], "split": media["split"],
                    "media_sha256": media["sha256"],
                    "prediction_srt_path": f"{engine_dir.name}/{srt_path.name}",
                    "prediction_srt_sha256": _sha_file(srt_path),
                    "model_sha256": profile["model_sha256"],
                    "config_sha256": hashlib.sha256(json.dumps({
                        "profile_sha256": profile["sha256"], "source_hashes": source_hashes,
                        "source_bundle_sha256": source_bundle_hash,
                        "runtime": runtime, "fixed_args": FIXED_PRODUCT_ARGS,
                    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
                    "configured_device": configured_device,
                    "session_configured_backend": configured_backend,
                    "stats_sha256": _sha_file(stats_path),
                    "stats_path": f"{engine_dir.name}/{stats_path.name}",
                    "cue_count": len(srt_cues),
                })
        prediction_doc = {"schema_version": 1, "records": [
            {key: value for key, value in row.items() if key in {
                "engine_id", "source_id", "episode_id", "split", "media_sha256",
                "prediction_srt_path", "prediction_srt_sha256", "model_sha256",
                "config_sha256", "configured_device", "session_configured_backend"}}
            for row in predictions
        ]}
        _atomic_json_new(staging / "prediction-manifest-v1.json", prediction_doc)
        video_eval._load_predictions(
            staging / "prediction-manifest-v1.json", ctx["groups"],
            ctx["reference_rows"], ctx["experiment_doc"])
        prediction_sha = _sha_file(staging / "prediction-manifest-v1.json")
        receipt = {
            "schema_version": 1,
            "status": "COMPLETED_LOCAL_EXECUTION_NOT_PHASE6_PASS",
            "run_id": ctx["run_id"], "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.monotonic() - start, 3),
            "application": {"commit": commit,
                            "source_bundle_sha256": source_bundle_hash,
                            "source_sha256s": source_hashes},
            "runtime": runtime,
            "execution": {"mode": "product_cli_allowlisted_offline_profile",
                          "network_policy": "python_socket_apis_blocked; managed OCR loaders pinned/local/download-disabled; OS network isolation not certified",
                          "phase6_eligible": False, "rights_verified": False},
            "inputs": ctx["bindings"] | {"execution_plan_sha256": ctx["plan_sha256"],
                                         "rights_receipt_sha256": ctx["rights_receipt_sha256"],
                                         "trust_registry_sha256": ctx["trust_registry_sha256"]},
            "profiles": {engine: {"profile_sha256": profile["sha256"],
                                   "model_sha256": profile["model_sha256"],
                                   "device_preference": profile["runtime"]["device_preference"]}
                         for engine, profile in ctx["profiles"].items()},
            "predictions": {"manifest": "prediction-manifest-v1.json",
                            "manifest_sha256": prediction_sha,
                            "records": predictions},
            "limits": ["Human receipt and trust pin are attestations; code does not establish legal rights or reviewer identity.",
                       "This execution receipt is not a rights/trust receipt and cannot create Phase 6 PASS.",
                       "Video timestamps use the product's current FPS/frame timing and retain its VFR limitations."],
        }
        receipt_path = staging / "phase5-execution-receipt-v1.json"
        _atomic_json_new(receipt_path, receipt)
        _revalidate(ctx)
        if _source_hashes() != source_hashes or _git_commit() != commit:
            raise ExecutionError("Application source/commit changed during the run")
        current_env, current_environment = _child_environment()
        current_runtime = _runtime_provenance(current_env, current_environment,
                                              ctx["profiles"], require_available=use_product_cli)
        if current_runtime != runtime:
            raise ExecutionError("Runtime packages, executable/tool hashes, or effective environment drifted")
        if _sha_file(staging / "prediction-manifest-v1.json") != prediction_sha:
            raise ExecutionError("Prediction manifest changed before publication")
        for row in predictions:
            prediction_path = staging / row["prediction_srt_path"]
            stats_path = staging / row["stats_path"]
            if (_sha_file(prediction_path) != row["prediction_srt_sha256"]
                    or _sha_file(stats_path) != row["stats_sha256"]):
                raise ExecutionError("Generated SRT/stats changed before publication")
        # The just-created execution record is parsed again; it remains a
        # local execution note and is never fed into a release gate.
        _json_unique(receipt_path, 32 * 1024 * 1024)
        # Do not publish a run folder if any generated path is a reparse point
        # or has escaped its fresh staging root.
        templates._reject_reparse_components(staging)
        for path in staging.rglob("*"):
            info = path.lstat()
            if (path.is_symlink() or getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    or not path.resolve(strict=True).is_relative_to(staging.resolve(strict=True))):
                raise ExecutionError("Generated artifact escaped the private output directory")
        staging.rename(final)
        return final
    except ProcessTreeUnstopped:
        # The child tree may still be writing; never delete its staging directory.
        # It remains unpublished and needs manual cleanup after confirming the tree is gone.
        raise
    except Exception:
        # This path was created exclusively above and is never user-supplied.
        # Verify it still resolves as a normal child before removing our partial outputs.
        try:
            templates._reject_reparse_components(staging)
            if (staging.exists() and not staging.is_symlink()
                    and staging.resolve(strict=True).parent == ctx["output_parent"]):
                shutil.rmtree(staging)
        except (OSError, templates.TemplateError):
            pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hardsub2srt.py phase5-run",
        description="Run both frozen Phase 5 engines locally with exact offline model profiles")
    parser.add_argument("--plan", required=True, type=Path,
                        help="human-reviewed phase5-execution-plan-v1.json")
    args = parser.parse_args(argv)
    try:
        result = _run(args.plan)
    except (ExecutionError, prep.InputError, video_eval.VideoEvalError,
            templates.TemplateError, OSError, ValueError) as exc:
        print(f"DURDURULDU: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"DURDURULDU: beklenmeyen güvenli durdurma ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(f"Yerel Phase 5 execution artifacts: {result}")
    print("Bu receipt hak doğrulaması veya Phase 6 PASS değildir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
