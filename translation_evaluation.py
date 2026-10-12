"""Local, append-only human evaluation for manually imported translations.

This module is deliberately independent of ``translation_review`` and the
translation-memory projection.  It stores rubric judgments, not user decisions
to accept/edit/reject an AI proposal.  Callers must supply current source-link
state through a trusted server-side resolver when recording or aggregating
judgments. Never pass browser fields as resolver state. Missing or changed
source links fail closed. The application caller, not this module, must bind the
store to private local app-data, enforce ACLs, and avoid cloud-synced/remote
directories; this module cannot establish those OS guarantees.

The manifest contains identifiers and hashes only.  It must not contain cue
text, candidate text, images, or filesystem paths.  The surrounding local UI is
responsible for resolving a blind candidate ID to its exact text for display.
No provider or network calls are made here.

In aggregate reports, ``coverage.excluded`` counts unique candidate units.
``exclusion_reasons`` uses reason incidence: a candidate is counted once for
each distinct applicable reason. Therefore reason counts can sum to more than
the number of excluded candidates (for example, two reviewers may mark one
candidate unevaluable for different reasons).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


SCHEMA_VERSION = 1
REPORT_VERSION = 1
MAX_EVENTS = 100_000
MAX_EVENT_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_CASES = 100_000
MAX_CANDIDATES = 1_000_000

DIMENSIONS = (
    "semantic_adequacy",
    "omissions",
    "additions",
    "terminology",
    "style_register",
)
ISSUES = frozenset({"meaning", "omission", "addition", "term", "style", "grammar", "none"})
OUTCOMES = frozenset({"accepted", "edited", "rejected"})
SPLITS = frozenset({"development", "held_out"})
DATASET_KINDS = frozenset({"synthetic", "human_held_out"})
PRIMARY_METRIC_VERSION = "translation-rubric-v1-no-material-defect-v1"
UNEVALUABLE_REASONS = frozenset({
    "candidate_missing", "source_unavailable", "stale_source", "stale_candidate", "context_missing",
    "language_uncertain", "other",
})

# The anchors are intentionally versioned and immutable for schema v1.
RUBRIC = {
    "version": "translation-rubric-v1",
    "dimensions": {
        "semantic_adequacy": {
            "0": "Materially wrong, contradictory, or changes the speaker's intent.",
            "1": "Core meaning is recoverable, but a non-core nuance is wrong or unclear.",
            "2": "Meaning and intent are preserved; no material semantic error.",
        },
        "omissions": {
            "0": "No meaningful source content is omitted.",
            "1": "A minor, non-essential detail is omitted.",
            "2": "A material proposition or detail is omitted.",
        },
        "additions": {
            "0": "No unsupported meaning is added.",
            "1": "A harmless clarification is added without changing intent.",
            "2": "Unsupported content materially changes or misleads about the source.",
        },
        "terminology": {
            "0": "Required terminology is wrong or inconsistent in a meaning-changing way.",
            "1": "A minor terminology inconsistency exists without material meaning change.",
            "2": "Terminology is accurate and follows the declared project glossary.",
        },
        "style_register": {
            "0": "Style/register materially conflicts with the declared target style.",
            "1": "There is a minor style/register drift.",
            "2": "Style/register is appropriate for the declared target style.",
        },
    },
    "unevaluable": "Use when source, candidate, context, or language cannot be judged reliably.",
}
RUBRIC_SHA256 = hashlib.sha256(
    json.dumps(RUBRIC, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()

_ID = re.compile(r"^[a-f0-9]{32}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_REVIEWER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LANG = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_LOCK = threading.RLock()


class EvaluationError(ValueError):
    """Invalid, stale, or tampered evaluation input."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_time(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.tzinfo is not None and dt.utcoffset() is not None
    except ValueError:
        return False


def _hash(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def canonical_group_id(source_id: str, episode_id: str) -> str:
    """Hash stable opaque IDs supplied from reviewed source/episode metadata."""
    if not _id(source_id) or not _id(episode_id):
        raise EvaluationError("source_id and episode_id must be stable opaque IDs")
    return _sha(("translation-evaluation-source-episode-v1:" + source_id + ":" + episode_id).encode("ascii"))


def _safe_root(root: str | os.PathLike[str], *, create: bool = False) -> Path:
    path = Path(root)
    if not path.is_absolute() or (os.name == "nt" and str(path).startswith("\\\\")):
        raise EvaluationError("evaluation storage must use an absolute local path")
    # Inspect every existing ancestor before mkdir/resolve so a junction or
    # symlink cannot redirect creation into a synced or remote location.
    for ancestor in (path, *path.parents):
        if _is_reparse(ancestor):
            raise EvaluationError("evaluation storage cannot pass through a symlink or junction")
    if create and not path.exists():
        if not path.parent.is_dir():
            raise EvaluationError("evaluation parent directory must already exist")
        path.mkdir()
    if not path.is_dir():
        raise EvaluationError("evaluation root must be a real local directory")
    return path.resolve(strict=True)


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(info.st_mode) or
            bool(getattr(info, "st_file_attributes", 0) & 0x400))


def _child(root: Path, name: str) -> Path:
    path = root / name
    if _is_reparse(path):
        raise EvaluationError("evaluation files cannot be symbolic links")
    if path.exists():
        resolved = path.resolve(strict=True)
        if resolved.parent != root or not resolved.is_file() or path.stat().st_nlink > 1:
            raise EvaluationError("evaluation file escapes its root")
    return path


def _write_new(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_new(path: Path, payload: bytes) -> None:
    # O_EXCL prevents replacing a pre-existing manifest. A partial file after a
    # crash is intentionally unrecoverable without explicit local cleanup.
    _write_new(path, payload)


def _validate_manifest(manifest: object) -> dict:
    required = {
        "schema_version", "evaluation_id", "dataset_kind", "source_language", "target_language",
        "style_profile_sha256", "glossary_sha256",
        "rubric_version", "rubric_sha256", "primary_metric_version", "split_policy",
        "group_identity_basis", "coverage_floor", "groups", "cases",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise EvaluationError("cohort manifest fields do not match schema v1")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION:
        raise EvaluationError("unsupported evaluation schema")
    if not _id(manifest["evaluation_id"]):
        raise EvaluationError("evaluation_id must be a random 32-character hex ID")
    if not isinstance(manifest["dataset_kind"], str) or manifest["dataset_kind"] not in DATASET_KINDS:
        raise EvaluationError("dataset_kind must be synthetic or human_held_out")
    for key in ("source_language", "target_language"):
        if (not isinstance(manifest[key], str) or not _LANG.fullmatch(manifest[key]) or
                len(manifest[key]) > 40):
            raise EvaluationError("source and target language tags are required")
    if not _hash(manifest["style_profile_sha256"]):
        raise EvaluationError("a frozen target style profile hash is required")
    if not _hash(manifest["glossary_sha256"]):
        raise EvaluationError("a frozen glossary hash is required; hash an explicit empty glossary if unused")
    if manifest["rubric_version"] != RUBRIC["version"] or manifest["rubric_sha256"] != RUBRIC_SHA256:
        raise EvaluationError("rubric version/hash differs from the frozen v1 rubric")
    if manifest["primary_metric_version"] != PRIMARY_METRIC_VERSION:
        raise EvaluationError("primary metric rule must be frozen before ratings")
    if manifest["split_policy"] != "source_episode_group_v1":
        raise EvaluationError("split_policy must freeze complete source+episode groups")
    expected_group_basis = ("synthetic_fixture_ids_v1" if manifest["dataset_kind"] == "synthetic"
                            else "caller_verified_source_episode_ids_v1")
    if manifest["group_identity_basis"] != expected_group_basis:
        raise EvaluationError("source/episode group identity basis is invalid for this dataset kind")
    floor = manifest["coverage_floor"]
    if (not isinstance(floor, dict) or set(floor) != {"minimum_rated_fraction_per_held_out_group"} or
            type(floor["minimum_rated_fraction_per_held_out_group"]) not in (int, float) or
            not 0 < floor["minimum_rated_fraction_per_held_out_group"] <= 1):
        raise EvaluationError("an explicit held-out per-group rating coverage floor in (0, 1] is required")
    groups = manifest["groups"]
    if not isinstance(groups, list) or not groups:
        raise EvaluationError("at least one opaque source+episode group is required")
    group_map = {}
    source_episode_pairs = set()
    for row in groups:
        if (not isinstance(row, dict) or set(row) != {"group_id", "source_id", "episode_id", "split"} or
                not _id(row["source_id"]) or not _id(row["episode_id"]) or
                row["group_id"] != canonical_group_id(row["source_id"], row["episode_id"]) or
                not isinstance(row["split"], str) or row["split"] not in SPLITS or
                row["group_id"] in group_map):
            raise EvaluationError("group rows must have unique opaque IDs and a fixed split")
        source_episode = (row["source_id"], row["episode_id"])
        if source_episode in source_episode_pairs:
            raise EvaluationError("a canonical source+episode pair cannot be assigned multiple group IDs/splits")
        source_episode_pairs.add(source_episode)
        group_map[row["group_id"]] = row["split"]
    if "held_out" not in group_map.values():
        raise EvaluationError("at least one complete held-out source+episode group is required")
    cases = manifest["cases"]
    if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
        raise EvaluationError("cohort cases are missing or exceed the cap")
    case_ids, candidate_ids = set(), set()
    candidate_count = 0
    frozen_conditions = None
    for case in cases:
        if (not isinstance(case, dict) or set(case) != {
                "case_id", "group_id", "context_sha256", "source_review_status",
                "source_review_event_id", "source_text_sha256",
                "crop_sha256", "srt_sha256", "candidates"}):
            raise EvaluationError("case fields do not match schema v1")
        if (not _id(case["case_id"]) or case["case_id"] in case_ids or
                not _hash(case["group_id"]) or case["group_id"] not in group_map or
                (case["context_sha256"] is not None and not _hash(case["context_sha256"])) or
                case["source_review_status"] not in ("accepted", "corrected") or
                not _id(case["source_review_event_id"]) or
                not all(_hash(case[key]) for key in ("source_text_sha256", "crop_sha256", "srt_sha256"))):
            raise EvaluationError("case identity/source evidence is missing or malformed")
        case_ids.add(case["case_id"])
        candidates = case["candidates"]
        if not isinstance(candidates, list) or not candidates:
            raise EvaluationError("every case must freeze its expected candidate systems")
        seen_conditions = set()
        for candidate in candidates:
            candidate_required = {"blind_candidate_id", "condition_id", "candidate_sha256", "proposal_sha256"}
            if not isinstance(candidate, dict) or set(candidate) != candidate_required:
                raise EvaluationError("candidate fields do not match schema v1")
            cid, condition = candidate["blind_candidate_id"], candidate["condition_id"]
            if not _id(cid) or cid in candidate_ids or not _id(condition):
                raise EvaluationError("candidate and condition IDs must be opaque random hex IDs")
            if condition in seen_conditions:
                raise EvaluationError("one condition may appear only once per case")
            missing_candidate = candidate["candidate_sha256"] is None and candidate["proposal_sha256"] is None
            present_candidate = _hash(candidate["candidate_sha256"]) and _hash(candidate["proposal_sha256"])
            if not (missing_candidate or present_candidate):
                raise EvaluationError("candidate and proposal hashes must both exist or both be null")
            candidate_ids.add(cid)
            seen_conditions.add(condition)
            candidate_count += 1
            if candidate_count > MAX_CANDIDATES:
                raise EvaluationError("candidate count exceeds the cap")
        if frozen_conditions is None:
            frozen_conditions = seen_conditions
        elif seen_conditions != frozen_conditions:
            raise EvaluationError("every frozen case must include every blind condition, using null hashes for missing outputs")
    return manifest


def _event_hash(event: dict) -> str:
    return _sha(_canonical({key: value for key, value in event.items() if key != "event_sha256"}))


def _event_path(root: Path) -> Path:
    return _child(root, "translation-evaluation-events.jsonl")


def _read_events(root: Path) -> list[dict]:
    path = _event_path(root)
    if not path.exists():
        return []
    if path.stat().st_size > MAX_EVENT_BYTES:
        raise EvaluationError("evaluation event log exceeds the byte limit")
    out, ids, previous = [], set(), None
    with path.open("rb") as stream:
        for index, line in enumerate(stream, 1):
            if index > MAX_EVENTS or len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                raise EvaluationError("evaluation event log is truncated or exceeds its limits")
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise EvaluationError("evaluation event log contains malformed JSON") from exc
            if not isinstance(event, dict) or not _valid_event(event):
                raise EvaluationError("evaluation event log contains an invalid event")
            if event["event_id"] in ids or event["previous_event_sha256"] != previous:
                raise EvaluationError("evaluation event chain has a duplicate or broken link")
            if event["event_sha256"] != _event_hash(event):
                raise EvaluationError("evaluation event hash mismatch; no partial report is available")
            ids.add(event["event_id"])
            previous = event["event_sha256"]
            out.append(event)
    if out and out[0]["event_type"] != "cohort_frozen":
        raise EvaluationError("evaluation log must begin with the frozen cohort event")
    if any(event["event_type"] == "cohort_frozen" for event in out[1:]):
        raise EvaluationError("cohort may only be frozen once")
    return out


def _append(root: Path, body: dict) -> dict:
    with _LOCK:
        events = _read_events(root)
        if len(events) >= MAX_EVENTS:
            raise EvaluationError("evaluation event count exceeds the cap")
        previous = events[-1]["event_sha256"] if events else None
        event = {**body, "previous_event_sha256": previous}
        event["event_sha256"] = _event_hash(event)
        line = _canonical(event) + b"\n"
        path = _event_path(root)
        current_size = path.stat().st_size if path.exists() else 0
        if len(line) > MAX_LINE_BYTES or current_size + len(line) > MAX_EVENT_BYTES:
            raise EvaluationError("evaluation event log size limit reached")
        if current_size:
            with path.open("rb") as stream:
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    raise EvaluationError("evaluation log ends in a partial event")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        return event


def create_evaluation(root: str | os.PathLike[str], manifest: object) -> dict:
    """Freeze an opaque cohort and rubric before any ratings are recorded.

    The directory is local/private storage chosen by the caller. The manifest
    rejects free-text/path fields so it can never embed cue or candidate text.
    """
    normalized = _validate_manifest(manifest)
    path = _safe_root(root, create=True)
    manifest_path = _child(path, "translation-evaluation-manifest-v1.json")
    log_path = _event_path(path)
    with _LOCK:
        if manifest_path.exists() or log_path.exists():
            raise EvaluationError("evaluation cohort is already frozen")
        payload = _canonical(normalized) + b"\n"
        _atomic_new(manifest_path, payload)
        digest = _sha(_canonical(normalized))
        _append(path, {
            "schema_version": SCHEMA_VERSION,
            "event_type": "cohort_frozen",
            "event_id": uuid.uuid4().hex,
            "evaluation_id": normalized["evaluation_id"],
            "manifest_sha256": digest,
            "rubric_version": normalized["rubric_version"],
            "rubric_sha256": normalized["rubric_sha256"],
            "created_at": _now(),
        })
    return {"evaluation_id": normalized["evaluation_id"], "manifest_sha256": digest,
            "rubric_version": normalized["rubric_version"], "candidate_count": sum(
                len(case["candidates"]) for case in normalized["cases"])}


def _load(root: str | os.PathLike[str]) -> tuple[Path, dict, str, list[dict]]:
    path = _safe_root(root)
    manifest_path = _child(path, "translation-evaluation-manifest-v1.json")
    if not manifest_path.exists():
        raise EvaluationError("frozen evaluation manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationError("frozen evaluation manifest is unreadable") from exc
    manifest = _validate_manifest(manifest)
    digest = _sha(_canonical(manifest))
    events = _read_events(path)
    if not events or events[0].get("manifest_sha256") != digest or events[0].get("evaluation_id") != manifest["evaluation_id"]:
        raise EvaluationError("frozen cohort does not match its append-only seal")
    if events[0].get("rubric_sha256") != RUBRIC_SHA256:
        raise EvaluationError("frozen rubric seal is invalid")
    _validate_event_bindings(manifest, digest, events)
    return path, manifest, digest, events


def _validate_event_bindings(manifest: dict, digest: str, events: list[dict]) -> None:
    """Fail closed if a well-formed event is attached to another cohort/item."""
    _cases, candidates = _case_index(manifest)
    prior_events = {}
    current = {}
    for event in events[1:]:
        if (event["evaluation_id"] != manifest["evaluation_id"] or
                event["manifest_sha256"] != digest or
                event["rubric_version"] != manifest["rubric_version"] or
                event["rubric_sha256"] != manifest["rubric_sha256"]):
            raise EvaluationError("rating event is not bound to the frozen cohort/rubric")
        if event["event_type"] != "rating":
            raise EvaluationError("unsupported event type in evaluation journal")
        candidate_link = candidates.get(event["blind_candidate_id"])
        if not candidate_link:
            raise EvaluationError("rating references an unknown blind candidate")
        case, candidate = candidate_link
        split = next(group["split"] for group in manifest["groups"] if group["group_id"] == case["group_id"])
        expected = {
            "case_id": case["case_id"], "group_id": case["group_id"],
            "context_sha256": case["context_sha256"],
            "source_review_status": case["source_review_status"], "split": split,
            "condition_id": candidate["condition_id"],
            "source_review_event_id": case["source_review_event_id"],
            "source_text_sha256": case["source_text_sha256"], "crop_sha256": case["crop_sha256"],
            "srt_sha256": case["srt_sha256"], "candidate_sha256": candidate["candidate_sha256"],
            "proposal_sha256": candidate["proposal_sha256"],
        }
        if any(event.get(key) != value for key, value in expected.items()):
            raise EvaluationError("rating hashes/IDs do not match the frozen source and candidate")
        key = (event["blind_candidate_id"], event["reviewer_id"])
        previous_id = event["supersedes_event_id"]
        active_id = current.get(key)
        if previous_id is None:
            if active_id is not None:
                raise EvaluationError("duplicate rating requires an append-only correction link")
        elif previous_id != active_id or previous_id not in prior_events:
            raise EvaluationError("rating correction must supersede the current prior event")
        prior_events[event["event_id"]] = event
        current[key] = event["event_id"]


def _case_index(manifest: dict) -> tuple[dict, dict]:
    cases, candidates = {}, {}
    for case in manifest["cases"]:
        cases[case["case_id"]] = case
        for candidate in case["candidates"]:
            candidates[candidate["blind_candidate_id"]] = (case, candidate)
    return cases, candidates


def _source_status(case: dict, candidate: dict, state: object) -> str:
    if not isinstance(state, Mapping) or state.get("active") is not True:
        return "source_unavailable"
    expected = {
        "context_sha256": case["context_sha256"],
        "source_review_status": case["source_review_status"],
        "source_review_event_id": case["source_review_event_id"],
        "source_text_sha256": case["source_text_sha256"],
        "crop_sha256": case["crop_sha256"],
        "srt_sha256": case["srt_sha256"],
    }
    if not all(state.get(key) == value for key, value in expected.items()):
        return "stale_source"
    if candidate["candidate_sha256"] is None:
        return "candidate_missing"
    if (state.get("candidate_sha256") != candidate["candidate_sha256"] or
            state.get("proposal_sha256") != candidate["proposal_sha256"]):
        return "stale_candidate"
    return "current"


def _active_ratings(events: list[dict]) -> dict[tuple[str, str], dict]:
    by_id = {event["event_id"]: event for event in events if event["event_type"] == "rating"}
    superseded = set()
    for event in by_id.values():
        previous_id = event.get("supersedes_event_id")
        if previous_id is not None:
            previous = by_id.get(previous_id)
            if not previous or (previous["blind_candidate_id"], previous["reviewer_id"]) != (
                    event["blind_candidate_id"], event["reviewer_id"]):
                raise EvaluationError("rating correction does not reference the same candidate/reviewer")
            superseded.add(previous_id)
    active = {}
    for event in events:
        if event["event_type"] != "rating" or event["event_id"] in superseded:
            continue
        key = (event["blind_candidate_id"], event["reviewer_id"])
        if key in active:
            raise EvaluationError("duplicate active rating; corrections must supersede prior events")
        active[key] = event
    return active


def _rating_body(manifest: dict, digest: str, candidate_id: str, reviewer_id: str,
                 status: str, dimensions: object, issues: object, rubric_outcome: object,
                 reason: object, supersedes_event_id: object, source_state: object) -> dict:
    _cases, candidate_index = _case_index(manifest)
    if candidate_id not in candidate_index:
        raise EvaluationError("blind candidate ID is not in the frozen cohort")
    if not isinstance(reviewer_id, str) or not _REVIEWER.fullmatch(reviewer_id):
        raise EvaluationError("reviewer_id must be a local pseudonymous alias")
    case, candidate = candidate_index[candidate_id]
    source_status = _source_status(case, candidate, source_state)
    if status not in ("rated", "unevaluable"):
        raise EvaluationError("rating status must be rated or unevaluable")
    if status == "rated":
        if source_status != "current":
            raise EvaluationError("only a current human-verified source can be rated")
        if candidate["candidate_sha256"] is None:
            raise EvaluationError("a missing AI candidate cannot be rated")
        if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
            raise EvaluationError("all rubric dimensions must be supplied")
        if any(type(dimensions[name]) is not int or dimensions[name] not in (0, 1, 2) for name in DIMENSIONS):
            raise EvaluationError("dimension scores must be integer anchors 0, 1, or 2")
        if not isinstance(issues, list) or any(issue not in ISSUES for issue in issues) or len(set(issues)) != len(issues):
            raise EvaluationError("issue categories are invalid or duplicated")
        if ("none" in issues and len(issues) != 1) or rubric_outcome not in OUTCOMES:
            raise EvaluationError("rated outcome or issue combination is invalid")
        if reason is not None:
            raise EvaluationError("rated records do not accept free-text rationale")
    else:
        if dimensions != {key: None for key in DIMENSIONS} or issues != [] or rubric_outcome is not None:
            raise EvaluationError("unevaluable records cannot contain quality scores")
        if not isinstance(reason, str) or reason not in UNEVALUABLE_REASONS:
            raise EvaluationError("unevaluable reason must be a fixed category")
        if source_status == "candidate_missing" and reason != "candidate_missing":
            raise EvaluationError("missing candidate reason must be explicit")
        if source_status == "source_unavailable" and reason != "source_unavailable":
            raise EvaluationError("missing active source reason must be explicit")
        if source_status == "stale_source" and reason != "stale_source":
            raise EvaluationError("stale source reason must be explicit")
        if source_status == "stale_candidate" and reason != "stale_candidate":
            raise EvaluationError("stale candidate reason must be explicit")
        if source_status == "current" and reason in (
                "source_unavailable", "stale_source", "stale_candidate", "candidate_missing"):
            raise EvaluationError("exclusion reason does not match current source/candidate state")
    if supersedes_event_id is not None and not _id(supersedes_event_id):
        raise EvaluationError("supersedes_event_id is malformed")
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": "rating",
        "event_id": uuid.uuid4().hex,
        "evaluation_id": manifest["evaluation_id"],
        "manifest_sha256": digest,
        "rubric_version": manifest["rubric_version"],
        "rubric_sha256": manifest["rubric_sha256"],
        "case_id": case["case_id"],
        "group_id": case["group_id"],
        "context_sha256": case["context_sha256"],
        "source_review_status": case["source_review_status"],
        "split": next(row["split"] for row in manifest["groups"] if row["group_id"] == case["group_id"]),
        "blind_candidate_id": candidate_id,
        "condition_id": candidate["condition_id"],
        "source_review_event_id": case["source_review_event_id"],
        "source_text_sha256": case["source_text_sha256"],
        "crop_sha256": case["crop_sha256"],
        "srt_sha256": case["srt_sha256"],
        "candidate_sha256": candidate["candidate_sha256"],
        "proposal_sha256": candidate["proposal_sha256"],
        "reviewer_id": reviewer_id,
        "status": status,
        "dimensions": dimensions,
        "issues": issues,
        "rubric_outcome": rubric_outcome,
        "exclusion_reason": reason,
        "supersedes_event_id": supersedes_event_id,
        "created_at": _now(),
    }


def record_rating(root: str | os.PathLike[str], blind_candidate_id: str, reviewer_id: str,
                  status: str, dimensions: object, issues: object, rubric_outcome: object,
                  *, source_resolver, exclusion_reason: object = None,
                  supersedes_event_id: object = None) -> dict:
    """Append one rubric rating after revalidating the current source link.

    ``source_resolver`` must be a trusted server-side callback that re-reads the
    current review pack, OCR decision journal, crop/SRT bytes, and proposal
    journal. Never pass a browser payload or client-created mapping here.
    """
    with _LOCK:
        path, manifest, digest, events = _load(root)
        _validate_resolver(source_resolver)
        _cases, candidate_index = _case_index(manifest)
        linked = candidate_index.get(blind_candidate_id)
        if not linked:
            raise EvaluationError("blind candidate ID is not in the frozen cohort")
        case, candidate = linked
        source_state = _resolve_source(source_resolver, case, candidate)
        body = _rating_body(manifest, digest, blind_candidate_id, reviewer_id, status,
                            dimensions, issues, rubric_outcome, exclusion_reason,
                            supersedes_event_id, source_state)
        active = _active_ratings(events)
        key = (blind_candidate_id, reviewer_id)
        prior = active.get(key)
        if prior is None and supersedes_event_id is not None:
            raise EvaluationError("cannot correct a rating that is not active")
        if prior is not None and supersedes_event_id != prior["event_id"]:
            raise EvaluationError("duplicate rating; correction must explicitly supersede the active event")
        return _append(path, body)


def _valid_event(event: dict) -> bool:
    if (type(event.get("schema_version")) is not int or event["schema_version"] != SCHEMA_VERSION or
            not _id(event.get("event_id")) or not _valid_time(event.get("created_at")) or
            not isinstance(event.get("event_type"), str)):
        return False
    base = {"schema_version", "event_type", "event_id", "created_at", "previous_event_sha256", "event_sha256"}
    if event["event_type"] == "cohort_frozen":
        required = base | {"evaluation_id", "manifest_sha256", "rubric_version", "rubric_sha256"}
        return (set(event) == required and _id(event["evaluation_id"]) and
                _hash(event["manifest_sha256"]) and event["rubric_version"] == RUBRIC["version"] and
                event["rubric_sha256"] == RUBRIC_SHA256 and event["previous_event_sha256"] is None and
                _hash(event["event_sha256"]))
    if event["event_type"] != "rating":
        return False
    required = base | {
        "evaluation_id", "manifest_sha256", "rubric_version", "rubric_sha256", "case_id", "group_id",
        "context_sha256", "source_review_status", "split", "blind_candidate_id", "condition_id",
        "source_review_event_id", "source_text_sha256",
        "crop_sha256", "srt_sha256", "candidate_sha256", "proposal_sha256", "reviewer_id", "status",
        "dimensions", "issues", "rubric_outcome", "exclusion_reason", "supersedes_event_id",
    }
    return (
        set(event) == required and _id(event["evaluation_id"]) and _hash(event["manifest_sha256"]) and
        event["rubric_version"] == RUBRIC["version"] and event["rubric_sha256"] == RUBRIC_SHA256 and
        _id(event["case_id"]) and _hash(event["group_id"]) and isinstance(event["split"], str) and event["split"] in SPLITS and
        (event["context_sha256"] is None or _hash(event["context_sha256"])) and
        event["source_review_status"] in ("accepted", "corrected") and
        _id(event["blind_candidate_id"]) and _id(event["condition_id"]) and _id(event["source_review_event_id"]) and
        all(_hash(event[k]) for k in ("source_text_sha256", "crop_sha256", "srt_sha256")) and
        ((event["candidate_sha256"] is None and event["proposal_sha256"] is None) or
         (_hash(event["candidate_sha256"]) and _hash(event["proposal_sha256"]))) and
        isinstance(event["reviewer_id"], str) and _REVIEWER.fullmatch(event["reviewer_id"]) is not None and
        isinstance(event["status"], str) and event["status"] in ("rated", "unevaluable") and isinstance(event["dimensions"], dict) and
        set(event["dimensions"]) == set(DIMENSIONS) and isinstance(event["issues"], list) and
        (event["supersedes_event_id"] is None or _id(event["supersedes_event_id"])) and
        ((event["status"] == "rated" and
          all(type(event["dimensions"][k]) is int and event["dimensions"][k] in (0, 1, 2) for k in DIMENSIONS) and
          all(isinstance(issue, str) and issue in ISSUES for issue in event["issues"]) and
          ("none" not in event["issues"] or len(event["issues"]) == 1) and
          isinstance(event["rubric_outcome"], str) and event["rubric_outcome"] in OUTCOMES and event["exclusion_reason"] is None) or
         (event["status"] == "unevaluable" and
          all(event["dimensions"][k] is None for k in DIMENSIONS) and event["issues"] == [] and
          event["rubric_outcome"] is None and isinstance(event["exclusion_reason"], str) and
          event["exclusion_reason"] in UNEVALUABLE_REASONS)) and
        (event["previous_event_sha256"] is None or _hash(event["previous_event_sha256"])) and
        _hash(event["event_sha256"])
    )


def _validate_resolver(source_resolver: object) -> None:
    if not callable(source_resolver):
        raise EvaluationError("trusted server-side source_resolver callback is required; browser data is not valid")


def _resolve_source(source_resolver, case: dict, candidate: dict) -> object:
    try:
        state = source_resolver(case["case_id"], candidate["blind_candidate_id"])
    except Exception:
        return None
    if state is not None and not isinstance(state, Mapping):
        raise EvaluationError("source resolver returned an invalid state")
    if isinstance(state, Mapping):
        required = {
            "active", "context_sha256", "source_review_status", "source_review_event_id",
            "source_text_sha256", "crop_sha256", "srt_sha256", "candidate_sha256", "proposal_sha256",
        }
        if set(state) != required:
            raise EvaluationError("source resolver state must be an exact server-verified allowlist")
    return state


def aggregate_report(root: str | os.PathLike[str], source_resolver) -> dict:
    """Return deterministic aggregate-only output; never returns text or paths.

    The caller must re-resolve each source event/crop/SRT and pass its current
    active state. Synthetic fixtures return counts only and no quality metric.
    """
    _path, manifest, digest, events = _load(root)
    _validate_resolver(source_resolver)
    active = _active_ratings(events)
    cases, candidates = _case_index(manifest)
    split_for_group = {group["group_id"]: group["split"] for group in manifest["groups"]}
    candidate_by_id = {cid: (case, candidate) for cid, (case, candidate) in candidates.items()}
    units_by_condition = defaultdict(list)
    for cid, (case, candidate) in candidates.items():
        units_by_condition[candidate["condition_id"]].append((cid, case, candidate))
    # Recheck that each source+episode group has a single immutable split.
    group_splits = defaultdict(set)
    for case in manifest["cases"]:
        group_splits[case["group_id"]].add(split_for_group[case["group_id"]])
    if any(len(splits) != 1 for splits in group_splits.values()):
        raise EvaluationError("source+episode leakage: one group appears in more than one split")

    condition_reports = {}
    for condition in sorted(units_by_condition):
        units = units_by_condition[condition]
        split_counts = {split: {"expected": 0, "active_ratings": 0, "rated": 0,
                                "unevaluable": 0, "missing_output": 0,
                                "source_unavailable": 0, "stale_source": 0, "stale_candidate": 0,
                                "disputed": 0, "unrated": 0, "excluded": 0}
                        for split in sorted(SPLITS)}
        exclusion_reasons = {split: defaultdict(int) for split in sorted(SPLITS)}
        dimension_counts = {
            split: {name: {"0": 0, "1": 0, "2": 0} for name in DIMENSIONS}
            for split in sorted(SPLITS)
        }
        issue_counts = {split: {issue: 0 for issue in sorted(ISSUES - {"none"})}
                        for split in sorted(SPLITS)}
        outcome_counts = {split: {outcome: 0 for outcome in sorted(OUTCOMES)}
                          for split in sorted(SPLITS)}
        group_coverage = defaultdict(lambda: {"expected": 0, "rated": 0, "passes": 0})
        for candidate_id, case, candidate in units:
            split = split_for_group[case["group_id"]]
            counts = split_counts[split]
            group = group_coverage[case["group_id"]]
            counts["expected"] += 1
            group["expected"] += 1
            if candidate["candidate_sha256"] is None:
                counts["missing_output"] += 1
                counts["excluded"] += 1
                exclusion_reasons[split]["missing_output"] += 1
                continue
            ratings = [event for (cid, _reviewer), event in active.items() if cid == candidate_id]
            counts["active_ratings"] += len(ratings)
            state = _resolve_source(source_resolver, case, candidate)
            source_status = _source_status(case, candidate, state)
            if source_status == "source_unavailable":
                counts["source_unavailable"] += 1
                counts["excluded"] += 1
                exclusion_reasons[split]["source_unavailable"] += 1
                continue
            if source_status == "stale_source":
                counts["stale_source"] += 1
                counts["excluded"] += 1
                exclusion_reasons[split]["stale_source"] += 1
                continue
            if source_status == "stale_candidate":
                counts["stale_candidate"] += 1
                counts["excluded"] += 1
                exclusion_reasons[split]["stale_candidate"] += 1
                continue
            if not ratings:
                counts["unrated"] += 1
                exclusion_reasons[split]["pending_rating"] += 1
                continue
            if any(event["manifest_sha256"] != digest or event["source_review_event_id"] != case["source_review_event_id"] or
                   event["source_text_sha256"] != case["source_text_sha256"] or
                   event["crop_sha256"] != case["crop_sha256"] or event["srt_sha256"] != case["srt_sha256"] or
                   event["candidate_sha256"] != candidate["candidate_sha256"] or
                   event["proposal_sha256"] != candidate["proposal_sha256"] for event in ratings):
                counts["stale_source"] += 1
                counts["excluded"] += 1
                exclusion_reasons[split]["stale_rating_binding"] += 1
                continue
            unevaluable = [event for event in ratings if event["status"] == "unevaluable"]
            if unevaluable:
                counts["unevaluable"] += 1
                counts["excluded"] += 1
                for reason in sorted({event["exclusion_reason"] for event in unevaluable}):
                    exclusion_reasons[split]["unevaluable:" + reason] += 1
                continue
            signatures = {
                (_canonical(event["dimensions"]), tuple(sorted(event["issues"])), event["rubric_outcome"])
                for event in ratings
            }
            if len(signatures) != 1:
                counts["disputed"] += 1
                counts["excluded"] += 1
                exclusion_reasons[split]["reviewer_disagreement"] += 1
                continue
            # Identical independent ratings count as one adjudicated item.
            event = sorted(ratings, key=lambda row: (row["reviewer_id"], row["event_id"]))[0]
            counts["rated"] += 1
            group["rated"] += 1
            outcome_counts[split][event["rubric_outcome"]] += 1
            for name in DIMENSIONS:
                dimension_counts[split][name][str(event["dimensions"][name])] += 1
            for issue in event["issues"]:
                if issue != "none":
                    issue_counts[split][issue] += 1
            no_material_defect = (
                event["dimensions"]["semantic_adequacy"] == 2 and
                event["dimensions"]["omissions"] < 2 and
                event["dimensions"]["additions"] < 2 and
                event["dimensions"]["terminology"] >= 1 and
                event["dimensions"]["style_register"] >= 1
            )
            if split == "held_out" and no_material_defect:
                group["passes"] += 1

        floor = manifest["coverage_floor"]["minimum_rated_fraction_per_held_out_group"]
        held_group_rates = [row["rated"] / row["expected"] for group_id, row in group_coverage.items()
                            if split_for_group[group_id] == "held_out" and row["expected"]]
        floor_passes = bool(held_group_rates) and all(rate >= floor for rate in held_group_rates)
        coverage_gate = {
            "required_rated_fraction_per_group": floor,
            "held_out_groups": len(held_group_rates),
            "groups_meeting_floor": sum(rate >= floor for rate in held_group_rates),
            "groups_below_floor": sum(rate < floor for rate in held_group_rates),
            "minimum_observed_rated_fraction": round(min(held_group_rates), 6) if held_group_rates else None,
            "passes": floor_passes,
        }
        quality_metrics = None
        metric_status = "SYNTHETIC_FIXTURE_ONLY" if manifest["dataset_kind"] == "synthetic" else "COVERAGE_FLOOR_FAILED"
        if manifest["dataset_kind"] == "human_held_out" and floor_passes:
            held = split_counts["held_out"]
            denom = held["expected"]
            pass_num = sum(row["passes"] for group_id, row in group_coverage.items()
                           if split_for_group[group_id] == "held_out")
            if denom:
                quality_metrics = {
                    "metric_name": "held_out_no_material_defect_rate",
                    "numerator": pass_num,
                    "denominator": denom,
                    "rate": round(pass_num / denom, 6),
                    "groups_with_ratings": len(held_group_rates),
                    "group_macro_rate": round(sum(row["passes"] / row["expected"] for group_id, row in group_coverage.items()
                                                   if split_for_group[group_id] == "held_out" and row["expected"]) /
                                               len(held_group_rates), 6),
                    "definition_version": PRIMARY_METRIC_VERSION,
                    "excluded_items_remain_failures_in_denominator": True,
                }
                metric_status = "DESCRIPTIVE_HELD_OUT_REVIEW"
        if manifest["dataset_kind"] == "synthetic":
            # Synthetic fixtures exercise mechanics only. Suppress all quality
            # distributions and rates, even when tests write sample ratings.
            dimension_counts = None
            issue_counts = None
            outcome_counts = None
        condition_reports[condition] = {
            "coverage": split_counts,
            "coverage_gate": coverage_gate,
            "exclusion_reason_semantics": "reason_incidence_unique_per_candidate",
            "exclusion_reasons": {split: dict(sorted(rows.items())) for split, rows in exclusion_reasons.items()},
            "rubric_dimensions": dimension_counts,
            "issues": issue_counts,
            "rubric_outcomes": outcome_counts,
            "held_out_metric": quality_metrics,
            "metric_status": metric_status,
        }
    return {
        "report_version": REPORT_VERSION,
        "evaluation_id": manifest["evaluation_id"],
        "manifest_sha256": digest,
        "dataset_kind": manifest["dataset_kind"],
        "source_language": manifest["source_language"],
        "target_language": manifest["target_language"],
        "style_profile_sha256": manifest["style_profile_sha256"],
        "glossary_sha256": manifest["glossary_sha256"],
        "rubric_version": manifest["rubric_version"],
        "rubric_sha256": manifest["rubric_sha256"],
        "primary_metric_version": manifest["primary_metric_version"],
        "split_policy": manifest["split_policy"],
        "group_identity_validation": (
            "SYNTHETIC_FIXTURE_ONLY" if manifest["dataset_kind"] == "synthetic"
            else "CALLER_ATTESTED_NOT_INDEPENDENTLY_RESOLVED"),
        "coverage_floor": manifest["coverage_floor"]["minimum_rated_fraction_per_held_out_group"],
        "group_count": len(manifest["groups"]),
        "held_out_group_count": sum(group["split"] == "held_out" for group in manifest["groups"]),
        "candidate_count": len(candidate_by_id),
        "conditions": condition_reports,
        "phase6_eligible": False,
    }


def manifest_digest(root: str | os.PathLike[str]) -> str:
    """Return the frozen cohort digest for local UI binding."""
    _path, _manifest, digest, _events = _load(root)
    return digest
