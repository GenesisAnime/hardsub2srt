# -*- coding: utf-8 -*-
"""Create local, deliberately incomplete Phase 5 measurement manifest drafts.

This helper performs no OCR, video access, network request, rights decision, or
release evaluation. The generated draft format is rejected by ocr_video_eval.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import ntpath
import os
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import ocr_experiment as prep
import ocr_video_eval as evaluator


class TemplateError(ValueError):
    pass


DRIVE_REMOTE = 4
LOCAL_DRIVE_TYPES = {2, 3, 5, 6}  # removable, fixed, CD-ROM, RAM disk


def _windows_drive_type(root: str) -> int:
    """Query drive type without opening/enumerating the drive contents."""
    if os.name != "nt":
        return 0
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = [wintypes.LPCWSTR]
    get_drive_type.restype = wintypes.UINT
    return int(get_drive_type(root))


def _reject_network_output_parent(raw_path: str) -> None:
    if raw_path.startswith(("\\\\", "//")):
        raise TemplateError("UNC/network output parents are not allowed")
    if os.name == "nt":
        drive, _tail = ntpath.splitdrive(raw_path)
        if not drive or not ntpath.isabs(raw_path):
            raise TemplateError("Output parent must be an absolute local-drive path")
        drive_root = drive + "\\"
        drive_type = _windows_drive_type(drive_root)
        if drive_type == DRIVE_REMOTE:
            raise TemplateError("Mapped/network output drives are not allowed")
        if drive_type not in LOCAL_DRIVE_TYPES:
            raise TemplateError("Output drive type is unknown or unavailable; refusing output")


def _reject_reparse_components(path: Path) -> None:
    """Use lstat on each existing lexical component before any path resolution."""
    candidate = path.expanduser()
    if not candidate.is_absolute():
        raise TemplateError("Output parent must be an absolute local path")
    components = candidate.parts
    current = Path(components[0])
    for component in components[1:]:
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            # No later component can exist without this component. The parent
            # is required to exist by the subsequent strict resolve.
            break
        except OSError as exc:
            raise TemplateError("Could not inspect output path components safely") from exc
        attributes = getattr(info, "st_file_attributes", 0)
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(info.st_mode) or attributes & reparse_attribute:
            raise TemplateError("Output path may not pass through a symlink/junction/reparse point")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_output_parent(out_parent: Path, protected_paths: list[Path]) -> Path:
    raw_parent = os.fspath(out_parent.expanduser())
    _reject_network_output_parent(raw_parent)
    _reject_reparse_components(Path(raw_parent))
    try:
        parent = Path(raw_parent).resolve(strict=True)
    except OSError as exc:
        raise TemplateError("Output parent must already exist and resolve locally") from exc
    # Resolve can follow a reparse point introduced between the lstat walk and
    # resolution. Revalidate the result's network/drive class before any later
    # filesystem operation such as is_dir().
    _reject_network_output_parent(os.fspath(parent))
    _reject_reparse_components(parent)
    if not parent.is_dir():
        raise TemplateError("Output parent must be a directory")
    for variable in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        configured_root = os.environ.get(variable, "").strip()
        if not configured_root or configured_root.startswith(("\\\\", "//")):
            continue
        try:
            configured_path = Path(configured_root).expanduser()
            _reject_network_output_parent(os.fspath(configured_path))
            _reject_reparse_components(configured_path)
            root = configured_path.resolve(strict=False)
            if _inside(parent, root):
                raise TemplateError(f"Output parent is under configured OneDrive root ({variable})")
        except OSError as exc:
            raise TemplateError(f"Could not safely compare configured OneDrive root ({variable})") from exc
    for protected in protected_paths:
        resolved = protected.expanduser().resolve(strict=False)
        if _inside(parent, resolved):
            raise TemplateError(f"Output parent is inside a protected input directory: {resolved.name}")
    return parent


def _frozen_group_rows(split_path: Path, expected_hash: str) -> list[dict]:
    try:
        raw = split_path.read_bytes()
        if evaluator._sha256(raw) != expected_hash:
            raise TemplateError("Frozen split changed after validation")
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TemplateError("Validated split could not be read") from exc
    if type(document) is not dict or type(document.get("rows")) is not list:
        raise TemplateError("Validated split rows are unavailable")
    by_group = {}
    for row in document["rows"]:
        if type(row) is not dict:
            raise TemplateError("Split row is malformed")
        key = row.get("group_id")
        if type(key) is not str or not key:
            raise TemplateError("Split group ID is malformed")
        existing = by_group.get(key)
        if existing is None:
            by_group[key] = {
                "source_id": row["source_id"],
                "episode_id": row["episode_id"],
                "split": row["split"],
            }
        elif existing["split"] != row["split"]:
            raise TemplateError("One canonical group maps to multiple splits")
    return [{"group_key": key, **by_group[key]} for key in sorted(by_group)]


def _reference_draft(groups: list[dict]) -> dict:
    records = []
    for group in groups:
        records.append({
            "source_id": group["source_id"],
            "episode_id": group["episode_id"],
            "split": group["split"],
            "language": "",
            "media_sha256": "",
            "reference_srt_path": "",
            "reference_srt_sha256": "",
            "alignment_review": {
                "status": "pending", "reviewer": "", "reviewed_at": "",
                "media_sha256": "",
            },
        })
    return {
        "draft_type": "hardsub2srt.timed-reference-manifest",
        "schema_version": "draft-v1",
        "template_only": True,
        "template_status": "INCOMPLETE_NOT_FOR_EVALUATION",
        "records": records,
    }


def _prediction_draft(groups: list[dict], baseline_id: str, candidate_id: str) -> dict:
    records = []
    for engine_id in (baseline_id, candidate_id):
        for group in groups:
            records.append({
                "engine_id": engine_id,
                "source_id": group["source_id"],
                "episode_id": group["episode_id"],
                "split": group["split"],
                "media_sha256": "",
                "prediction_srt_path": "",
                "prediction_srt_sha256": "",
                "model_sha256": "",
                "config_sha256": "",
                "device": "",
                "backend": "",
            })
    return {
        "draft_type": "hardsub2srt.prediction-manifest",
        "schema_version": "draft-v1",
        "template_only": True,
        "template_status": "INCOMPLETE_NOT_FOR_EVALUATION",
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "records": records,
    }


def _atomic_create(out_parent: Path, references: dict, predictions: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = out_parent / f"phase5-measurement-templates-{stamp}-{uuid.uuid4().hex[:12]}"
    staging = Path(tempfile.mkdtemp(prefix=".phase5-template-", dir=out_parent))
    try:
        files = {
            "timed-reference-manifest-v1.draft.json": references,
            "prediction-manifest-v1.draft.json": predictions,
        }
        for name, document in files.items():
            path = staging / name
            data = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        readme = (
            "# Phase 5 measurement manifest drafts\n\n"
            "These files are incomplete templates, not evaluation inputs. "
            "They use schema_version=draft-v1 and template_only=true; "
            "ocr_video_eval.py rejects this draft format.\n\n"
            "The template does not grant rights, attest source-language/reference alignment, "
            "verify model execution, run OCR, or establish a Phase 5/Phase 6 PASS. "
            "Complete each blank only from reviewed, authorized local evidence. "
            "Keep this directory local and do not treat a filled form as proof of legal rights.\n"
        )
        with (staging / "README.md").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(readme)
            stream.flush()
            os.fsync(stream.fileno())
        # Same-parent rename is atomic on the target filesystem and fails if a
        # name unexpectedly exists; no existing output is ever overwritten.
        os.rename(staging, target)
        return target
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def create_templates(dataset: Path, metadata: Path, experiment: Path,
                     out_parent: Path, protect_dirs: list[Path] | None = None) -> Path:
    dataset = dataset.expanduser().resolve(strict=True)
    metadata = metadata.expanduser().resolve(strict=True)
    experiment = experiment.expanduser().resolve(strict=True)
    split_path = experiment.parent / "split-manifest.json"
    protected = [dataset, dataset.parent, metadata, metadata.parent,
                 experiment, experiment.parent, split_path]
    protected.extend(protect_dirs or [])
    output_parent = _safe_output_parent(out_parent, protected)

    # Reuse the same strict provenance, rights-review, frozen split, and
    # preparation-report checks as the real evaluator. No alternate shortcut.
    groups_by_key, experiment_doc, _manifest_hash, _metadata_hash, split_hash = evaluator._validated_split(
        experiment, split_path, dataset, metadata)
    groups = _frozen_group_rows(split_path, split_hash)
    if {row["group_key"] for row in groups} != set(groups_by_key):
        raise TemplateError("Frozen group coverage changed after validation")
    frozen_experiment = experiment_doc["experiment"]
    baseline_id = frozen_experiment["baseline_id"]
    candidate_id = frozen_experiment["candidate_id"]
    if (type(baseline_id) is not str or type(candidate_id) is not str
            or not evaluator.ENGINE_ID_RE.fullmatch(baseline_id)
            or not evaluator.ENGINE_ID_RE.fullmatch(candidate_id)):
        raise TemplateError("Frozen baseline/candidate IDs are invalid")
    return _atomic_create(output_parent, _reference_draft(groups),
                          _prediction_draft(groups, baseline_id, candidate_id))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Create incomplete local Phase 5 timed-reference/prediction manifest drafts; no evaluation or approval is produced.")
    parser.add_argument("dataset", type=Path, help="verified-ocr-dataset-* directory")
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--experiment", required=True, type=Path,
                        help="PREPARED_NOT_RUN experiment-report.json")
    parser.add_argument("--out-parent", required=True, type=Path,
                        help="existing local non-synced directory outside protected inputs; a unique new child is created")
    parser.add_argument("--protect-dir", action="append", default=[], type=Path,
                        help="additional reference/prediction input or reserved directory that output must not be inside")
    args = parser.parse_args(argv)
    try:
        target = create_templates(args.dataset, args.metadata, args.experiment,
                                  args.out_parent, args.protect_dir)
    except (TemplateError, evaluator.VideoEvalError, evaluator.crop_eval.EvalError,
            prep.InputError, OSError, ValueError) as exc:
        print(f"DURDURULDU: {exc}", file=sys.stderr)
        return 2
    print(f"Incomplete local draft templates created: {target}; not valid for evaluation or release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
