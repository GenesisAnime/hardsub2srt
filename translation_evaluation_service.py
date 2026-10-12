"""Flask-facing adapter for the local translation evaluation backend.

Real held-out scoring remains blocked. This adapter only creates a fixed
synthetic rubric demo until server-validated source/episode split, trusted
reference, and reviewed rights artifacts are registered. The existing manual
AI response importer remains the only route for transferring provider output;
this adapter never calls a provider and never accepts browser-supplied source
state, split, candidate mapping, rights, or dataset-kind claims.

Persistent demo files live under the local non-synced app-data root selected by
``job_history.default_history_path``. The application caller relies on that
root's inherited OS ACL; this module does not set custom Windows ACLs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from pathlib import Path

import job_history
import ocr_experiment
import ocr_review
import ocr_video_eval
import translation_evaluation as evaluation
import translation_review


SYNTHETIC_FIXTURE_SCHEMA = 1
SYNTHETIC_SOURCE = "SYNTHETIC SOURCE TEXT · DEMO ONLY"
SYNTHETIC_CONTEXT = "SYNTHETIC CONTEXT · DEMO ONLY"
SYNTHETIC_CANDIDATES = (
    "SYNTHETIC TARGET CANDIDATE A · DEMO ONLY",
    "SYNTHETIC TARGET CANDIDATE B · DEMO ONLY",
)
_FIXTURE_FILE = "synthetic-fixture-v1.json"
_REAL_REGISTRY_FILE = "real-cohort-registry-v1.json"
_CONDITION_MAPPING_FILE = "condition-mapping-v1.json"
_REAL_LINKS_FILE = "real-source-links-v1.json"


class RealEvidenceBlocked(evaluation.EvaluationError):
    """Safe blocker raised when local held-out evidence is absent or invalid."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_text(text: str) -> str:
    return _sha(text.encode("utf-8"))


def _local_root() -> Path:
    """Return the safe local app-data child, creating only checked directories."""
    history_path = Path(job_history.default_history_path())
    app_root = history_path.parent
    if job_history._contains_reparse_component(app_root):
        raise evaluation.EvaluationError("Yerel çeviri değerlendirme depolaması junction/symlink içeremez")
    if os.name == "nt":
        is_local = job_history._windows_state_root_is_local(app_root)
    else:
        is_local = job_history._unix_state_root_is_local(app_root)
    if not is_local:
        raise evaluation.EvaluationError("Yerel olmayan veya eşitlenen depolama kullanılamaz")
    if not app_root.exists():
        if not app_root.parent.is_dir():
            raise evaluation.EvaluationError("Yerel uygulama verisi üst klasörü bulunamadı")
        app_root.mkdir(mode=0o700)
    if job_history._contains_reparse_component(app_root):
        raise evaluation.EvaluationError("Yerel uygulama verisi junction/symlink içeremez")
    app_root = app_root.resolve(strict=True)
    root = app_root / "translation-evaluation-v1"
    if not root.exists():
        root.mkdir(mode=0o700)
    root = evaluation._safe_root(root)
    try:
        os.chmod(root, 0o700)
    except OSError:
        # Windows inherits the local app-data ACL; no custom ACL is claimed.
        pass
    synthetic = root / "synthetic"
    if not synthetic.exists():
        synthetic.mkdir(mode=0o700)
    synthetic = evaluation._safe_root(synthetic)
    try:
        os.chmod(synthetic, 0o700)
    except OSError:
        pass
    return synthetic


def _evaluation_dir(evaluation_id: object) -> Path:
    if not evaluation._id(evaluation_id):
        raise evaluation.EvaluationError("Sentetik değerlendirme kimliği geçersiz")
    return evaluation._safe_root(_local_root() / evaluation_id)


def _fixture_path(root: Path) -> Path:
    return evaluation._child(root, _FIXTURE_FILE)


def _read_fixture(root: Path) -> dict:
    path = _fixture_path(root)
    if not path.exists():
        raise evaluation.EvaluationError("Sentetik demo örneği bulunamadı")
    try:
        if path.stat().st_size > 256 * 1024:
            raise evaluation.EvaluationError("Sentetik demo örneği boyut sınırını aşıyor")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise evaluation.EvaluationError("Sentetik demo örneği okunamıyor") from exc
    if not isinstance(data, dict) or set(data) != {
            "schema_version", "evaluation_id", "source_text", "context_text", "items"}:
        raise evaluation.EvaluationError("Sentetik demo örneği şeması geçersiz")
    if (type(data["schema_version"]) is not int or data["schema_version"] != SYNTHETIC_FIXTURE_SCHEMA or
            data["source_text"] != SYNTHETIC_SOURCE or data["context_text"] != SYNTHETIC_CONTEXT or
            data["evaluation_id"] != root.name or not isinstance(data["items"], list) or len(data["items"]) != 2):
        raise evaluation.EvaluationError("Sentetik demo örneği geçersiz")
    seen = set()
    for item in data["items"]:
        if (not isinstance(item, dict) or set(item) != {
                "blind_candidate_id", "condition_id", "text", "proposal_sha256"} or
                not evaluation._id(item["blind_candidate_id"]) or not evaluation._id(item["condition_id"]) or
                item["blind_candidate_id"] in seen or item["text"] not in SYNTHETIC_CANDIDATES or
                not evaluation._hash(item["proposal_sha256"])):
            raise evaluation.EvaluationError("Sentetik aday kaydı geçersiz")
        seen.add(item["blind_candidate_id"])
    return data


def create_synthetic_demo() -> dict:
    """Create a server-authored fake cohort; request bodies supply no claims."""
    evaluation_id = uuid.uuid4().hex
    source_id, episode_id = uuid.uuid4().hex, uuid.uuid4().hex
    group_id = evaluation.canonical_group_id(source_id, episode_id)
    case_id, source_event_id = uuid.uuid4().hex, uuid.uuid4().hex
    source_hash, context_hash = _hash_text(SYNTHETIC_SOURCE), _hash_text(SYNTHETIC_CONTEXT)
    crop_hash = _hash_text("SYNTHETIC-CROP-PLACEHOLDER-NO-IMAGE")
    srt_hash = _hash_text("SYNTHETIC-SRT-PLACEHOLDER-NO-FILE")
    candidates, fixture_items = [], []
    for text in SYNTHETIC_CANDIDATES:
        candidate_id, condition_id = uuid.uuid4().hex, uuid.uuid4().hex
        proposal_hash = _sha(_canonical({"kind": "synthetic_manual_candidate", "text": text}))
        candidates.append({"blind_candidate_id": candidate_id, "condition_id": condition_id,
                           "candidate_sha256": _hash_text(text), "proposal_sha256": proposal_hash})
        fixture_items.append({"blind_candidate_id": candidate_id, "condition_id": condition_id,
                              "text": text, "proposal_sha256": proposal_hash})
    manifest = {
        "schema_version": evaluation.SCHEMA_VERSION,
        "evaluation_id": evaluation_id,
        "dataset_kind": "synthetic",
        "source_language": "ja",
        "target_language": "tr",
        "style_profile_sha256": _hash_text("SYNTHETIC STYLE PROFILE · DEMO ONLY"),
        "glossary_sha256": _hash_text("SYNTHETIC EMPTY GLOSSARY · DEMO ONLY"),
        "rubric_version": evaluation.RUBRIC["version"],
        "rubric_sha256": evaluation.RUBRIC_SHA256,
        "primary_metric_version": evaluation.PRIMARY_METRIC_VERSION,
        "split_policy": "source_episode_group_v1",
        "group_identity_basis": "synthetic_fixture_ids_v1",
        "coverage_floor": {"minimum_rated_fraction_per_held_out_group": 1.0},
        "groups": [{"group_id": group_id, "source_id": source_id,
                    "episode_id": episode_id, "split": "held_out"}],
        "cases": [{
            "case_id": case_id, "group_id": group_id, "context_sha256": context_hash,
            "source_review_status": "accepted", "source_review_event_id": source_event_id,
            "source_text_sha256": source_hash, "crop_sha256": crop_hash, "srt_sha256": srt_hash,
            "candidates": candidates,
        }],
    }
    root = _local_root() / evaluation_id
    root.mkdir(mode=0o700)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    root = evaluation._safe_root(root)
    fixture = {"schema_version": SYNTHETIC_FIXTURE_SCHEMA, "evaluation_id": evaluation_id,
               "source_text": SYNTHETIC_SOURCE, "context_text": SYNTHETIC_CONTEXT,
               "items": fixture_items}
    fd = os.open(_fixture_path(root), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(_canonical(fixture) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    evaluation.create_evaluation(root, manifest)
    return status(evaluation_id)


def _load_synthetic(evaluation_id: object) -> tuple[Path, dict, dict]:
    root = _evaluation_dir(evaluation_id)
    _path, manifest, _digest, _events = evaluation._load(root)
    if manifest["dataset_kind"] != "synthetic":
        raise evaluation.EvaluationError("Gerçek değerlendirme bu yerel pilotta kapalı")
    fixture = _read_fixture(root)
    if fixture["evaluation_id"] != manifest["evaluation_id"]:
        raise evaluation.EvaluationError("Sentetik demo manifest ile eşleşmiyor")
    case = manifest["cases"][0]
    by_id = {item["blind_candidate_id"]: item for item in fixture["items"]}
    expected = {candidate["blind_candidate_id"]: candidate for candidate in case["candidates"]}
    if set(by_id) != set(expected):
        raise evaluation.EvaluationError("Sentetik aday kümesi manifest ile eşleşmiyor")
    for candidate_id, item in by_id.items():
        candidate = expected[candidate_id]
        if (item["condition_id"] != candidate["condition_id"] or
                _hash_text(item["text"]) != candidate["candidate_sha256"] or
                item["proposal_sha256"] != candidate["proposal_sha256"]):
            raise evaluation.EvaluationError("Sentetik aday metni/hash'i değişti")
    return root, manifest, fixture


def _synthetic_resolver(manifest: dict, fixture: dict):
    case = manifest["cases"][0]
    items = {item["blind_candidate_id"]: item for item in fixture["items"]}
    candidates = {row["blind_candidate_id"]: row for row in case["candidates"]}

    def resolve(case_id: str, candidate_id: str):
        if case_id != case["case_id"] or candidate_id not in items:
            return None
        item = items[candidate_id]
        candidate = candidates[candidate_id]
        return {
            "active": True,
            "context_sha256": case["context_sha256"],
            "source_review_status": case["source_review_status"],
            "source_review_event_id": case["source_review_event_id"],
            "source_text_sha256": _hash_text(fixture["source_text"]),
            "crop_sha256": case["crop_sha256"],
            "srt_sha256": case["srt_sha256"],
            "candidate_sha256": _hash_text(item["text"]),
            "proposal_sha256": item["proposal_sha256"],
        }
    return resolve


def status(evaluation_id: object) -> dict:
    root, manifest, fixture = _load_synthetic(evaluation_id)
    report = evaluation.aggregate_report(root, _synthetic_resolver(manifest, fixture))
    _path, _loaded, _digest, events = evaluation._load(root)
    active = evaluation._active_ratings(events)
    active_ratings = {
        candidate_id: {
            key: event[key] for key in (
                "event_id", "status", "dimensions", "issues", "rubric_outcome", "exclusion_reason")
        }
        for (candidate_id, reviewer_id), event in active.items() if reviewer_id == "local_user"
    }
    return {
        "evaluation_id": manifest["evaluation_id"],
        "mode": "synthetic_test_only",
        "source_language": manifest["source_language"],
        "target_language": manifest["target_language"],
        "rubric": evaluation.RUBRIC,
        "source_text": fixture["source_text"],
        "context_text": fixture["context_text"],
        "candidates": [{"blind_candidate_id": item["blind_candidate_id"], "text": item["text"]}
                       for item in fixture["items"]],
        "rated_candidate_ids": list(active_ratings),
        "active_ratings": active_ratings,
        "report": report,
    }


def record_synthetic_rating(evaluation_id: object, blind_candidate_id: object,
                            status_value: object, dimensions: object, issues: object,
                            rubric_outcome: object, exclusion_reason: object = None,
                            supersedes_event_id: object = None) -> dict:
    root, manifest, fixture = _load_synthetic(evaluation_id)
    event = evaluation.record_rating(
        root, blind_candidate_id, "local_user", status_value, dimensions, issues,
        rubric_outcome, source_resolver=_synthetic_resolver(manifest, fixture),
        exclusion_reason=exclusion_reason, supersedes_event_id=supersedes_event_id,
    )
    return {"ok": True, "mode": "synthetic_test_only", "event_id": event["event_id"],
            "message": "Sentetik puan günlüğe eklendi; kalite metriği üretilmez."}


def _local_evidence_path(value: object, label: str, *, directory: bool) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise RealEvidenceBlocked("INVALID_EVIDENCE_PATH", f"{label} için mutlak yerel yol gerekli.")
    path = Path(value)
    if job_history._contains_reparse_component(path):
        raise RealEvidenceBlocked("UNSAFE_EVIDENCE_PATH", f"{label} junction/symlink içermemeli.")
    local = (job_history._windows_state_root_is_local(path) if os.name == "nt"
             else job_history._unix_state_root_is_local(path))
    if not local:
        raise RealEvidenceBlocked("NONLOCAL_EVIDENCE_PATH", f"{label} yerel ve eşitlenmeyen depolamada olmalı.")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RealEvidenceBlocked("MISSING_EVIDENCE", f"{label} bulunamadı.") from exc
    if (directory and not resolved.is_dir()) or (not directory and not resolved.is_file()):
        raise RealEvidenceBlocked("INVALID_EVIDENCE_KIND", f"{label} dosya/klasör biçimi geçersiz.")
    return resolved


def _real_registry_path() -> Path:
    return _local_root().parent / _REAL_REGISTRY_FILE


def _read_registry() -> tuple[dict, dict]:
    root = _local_root().parent
    path = evaluation._child(root, _real_registry_path().name)
    if not path.exists():
        raise RealEvidenceBlocked(
            "SERVER_COHORT_REGISTRY_MISSING",
            "Sunucunun yerel değerlendirme klasöründe doğrulanmış Phase 5 kohort kaydı yok.",
        )
    try:
        if path.stat().st_size > 256 * 1024 or path.stat().st_nlink > 1:
            raise RealEvidenceBlocked("INVALID_COHORT_REGISTRY", "Kohort kaydı boyut/bağ sınırını aşıyor.")

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate key")
                value[key] = item
            return value

        raw = path.read_bytes()
        registry = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except RealEvidenceBlocked:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RealEvidenceBlocked("INVALID_COHORT_REGISTRY", "Kohort kaydı okunamadı veya şeması bozuk.") from exc
    required = {
        "schema_version", "dataset_path", "metadata_path", "experiment_path", "timed_reference_path",
        "review_pack_paths", "source_language", "target_language", "style_profile", "glossary",
        "minimum_rated_fraction_per_held_out_group",
    }
    floor = registry.get("minimum_rated_fraction_per_held_out_group") if isinstance(registry, dict) else None
    if (not isinstance(registry, dict) or set(registry) != required or
            type(registry.get("schema_version")) is not int or registry["schema_version"] != 1 or
            not isinstance(registry.get("source_language"), str) or not evaluation._LANG.fullmatch(registry["source_language"]) or
            not isinstance(registry.get("target_language"), str) or not evaluation._LANG.fullmatch(registry["target_language"]) or
            registry["source_language"].lower() == registry["target_language"].lower() or
            not isinstance(registry.get("style_profile"), str) or not registry["style_profile"].strip() or
            len(registry["style_profile"]) > 20_000 or not isinstance(registry.get("glossary"), str) or
            len(registry["glossary"]) > 20_000 or type(floor) not in (int, float) or not 0 < floor <= 1 or
            not isinstance(registry.get("review_pack_paths"), list) or
            not 3 <= len(registry["review_pack_paths"]) <= 100):
        raise RealEvidenceBlocked("INVALID_COHORT_REGISTRY", "Kohort kaydı gerekli alanları sağlamıyor.")
    paths = {
        "dataset": _local_evidence_path(registry["dataset_path"], "verified OCR dataset", directory=True),
        "metadata": _local_evidence_path(registry["metadata_path"], "Phase 5 metadata", directory=False),
        "experiment": _local_evidence_path(registry["experiment_path"], "Phase 5 experiment report", directory=False),
        "references": _local_evidence_path(registry["timed_reference_path"], "timed reference manifest", directory=False),
        "review_packs": [_local_evidence_path(value, "review-pack", directory=True)
                         for value in registry["review_pack_paths"]],
    }
    if len({str(path) for path in paths["review_packs"]}) != len(paths["review_packs"]):
        raise RealEvidenceBlocked("INVALID_COHORT_REGISTRY", "Review-pack listesi yinelenen klasör içeriyor.")
    return registry, {**paths, "registry_path": path, "registry_sha256": _sha(raw)}


def _read_condition_mapping() -> dict:
    """Read owner-local proposal-to-condition labels, never from a browser."""
    path = evaluation._child(_local_root().parent, _CONDITION_MAPPING_FILE)
    if not path.exists():
        raise RealEvidenceBlocked(
            "SERVER_CONDITION_MAP_MISSING",
            "Öneri/koşul eşlemesini içeren özel yerel dosya bulunamadı.",
        )
    try:
        if path.stat().st_size > 4 * 1024 * 1024 or path.stat().st_nlink > 1:
            raise RealEvidenceBlocked("INVALID_CONDITION_MAP", "Koşul eşleme dosyası boyut/bağ sınırını aşıyor.")

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate key")
                value[key] = item
            return value

        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except RealEvidenceBlocked:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RealEvidenceBlocked("INVALID_CONDITION_MAP", "Özel koşul eşleme dosyası okunamadı.") from exc
    if (not isinstance(document, dict) or set(document) != {"schema_version", "conditions", "proposal_assignments"} or
            type(document.get("schema_version")) is not int or document["schema_version"] != 1 or
            not isinstance(document.get("conditions"), list) or not 1 <= len(document["conditions"]) <= 32 or
            not isinstance(document.get("proposal_assignments"), dict) or
            len(document["proposal_assignments"]) > 100_000):
        raise RealEvidenceBlocked("INVALID_CONDITION_MAP", "Özel koşul eşleme dosyasının şeması geçersiz.")
    labels = {}
    for row in document["conditions"]:
        if (not isinstance(row, dict) or set(row) != {"condition_id", "private_label"} or
                not evaluation._id(row.get("condition_id")) or row["condition_id"] in labels or
                not isinstance(row.get("private_label"), str) or not row["private_label"].strip() or
                len(row["private_label"]) > 160):
            raise RealEvidenceBlocked("INVALID_CONDITION_MAP", "Koşul kimliği veya özel etiketi geçersiz.")
        labels[row["condition_id"]] = row["private_label"].strip()
    assignments = document["proposal_assignments"]
    if any(not evaluation._id(proposal_id) or condition_id not in labels
           for proposal_id, condition_id in assignments.items()):
        raise RealEvidenceBlocked("INVALID_CONDITION_MAP", "Proposal atamalarında bilinmeyen/bozuk kimlik var.")
    return {"labels": labels, "assignments": assignments, "path": path, "sha256": _sha(raw)}


def _single_condition_for_proposals(mapping: dict, proposal_ids: list[str]) -> str:
    assigned = [mapping["assignments"].get(proposal_id) for proposal_id in proposal_ids]
    if any(condition_id is None for condition_id in assigned):
        raise RealEvidenceBlocked(
            "CONDITION_ASSIGNMENT_MISSING", "Her held-out proposal için sunucu tarafı koşul ataması gerekli.")
    unique = set(assigned)
    if len(unique) != 1:
        raise RealEvidenceBlocked(
            "MIXED_CONDITIONS", "Bu pilot cohort tek koşul kabul eder; karışık atamalar reddedildi.")
    return next(iter(unique))


def _ratings_complete(candidate_ids: set[str], active_ratings: dict) -> bool:
    rated_ids = {candidate_id for (candidate_id, reviewer) in active_ratings
                 if reviewer == "local_user"}
    return bool(candidate_ids) and candidate_ids == rated_ids


def _phase5_evidence() -> dict:
    """Revalidate rights-reviewed Phase 2 lineage, frozen split and timed refs."""
    registry, paths = _read_registry()
    condition_mapping = _read_condition_mapping()
    split_path = paths["experiment"].parent / "split-manifest.json"
    try:
        groups, experiment, dataset_hash, metadata_hash, split_hash = ocr_video_eval._validated_split(
            paths["experiment"], split_path, paths["dataset"], paths["metadata"])
        references, reference_hash, source_language = ocr_video_eval._load_references(
            paths["references"], groups)
        dataset_rows = ocr_experiment.validate_dataset(paths["dataset"])
        metadata = ocr_experiment.validate_metadata(paths["metadata"], dataset_rows)
        provenance = ocr_experiment.verify_phase2_provenance(paths["dataset"], dataset_rows)
        ocr_experiment.validate_provenance_groups(provenance, metadata, dataset_rows)
        split_doc, split_raw = ocr_video_eval._json(split_path)
    except RealEvidenceBlocked:
        raise
    except Exception as exc:
        # Underlying validators may mention user paths; return a fixed blocker.
        raise RealEvidenceBlocked(
            "PHASE5_EVIDENCE_INVALID",
            "Dataset, hak incelemesi, frozen split veya zaman hizalı referans doğrulaması başarısız.",
        ) from exc
    if source_language.lower() != registry["source_language"].lower():
        raise RealEvidenceBlocked("LANGUAGE_PAIR_MISMATCH", "Kaynak dil referans manifestiyle eşleşmiyor.")
    test_groups = sorted(key for key, split in groups.items() if split == "test")
    if len(groups) < 3 or not test_groups:
        raise RealEvidenceBlocked("HELDOUT_GROUPS_INSUFFICIENT", "Üç kaynak/bölüm grubu ve test split gerekli.")
    split_rows = split_doc.get("rows") if isinstance(split_doc, dict) else None
    if not isinstance(split_rows, list):
        raise RealEvidenceBlocked("SPLIT_ROWS_INVALID", "Doğrulanmış split satırları eksik.")
    dataset_by_cue = {row["cue_id"]: row for row in dataset_rows}
    test_rows = []
    for row in split_rows:
        if row.get("split") != "test":
            continue
        cue_id = row.get("cue_id")
        source_id, episode_id = row.get("source_id"), row.get("episode_id")
        if cue_id not in dataset_by_cue or not isinstance(source_id, str) or not isinstance(episode_id, str):
            raise RealEvidenceBlocked("SPLIT_DATASET_LINK_INVALID", "Test split satırı verified export ile eşleşmiyor.")
        meta = metadata.get(cue_id)
        if not meta or ocr_experiment._group_key(meta) not in test_groups:
            raise RealEvidenceBlocked("SPLIT_GROUP_LINK_INVALID", "Test source/episode grubu metadata ile eşleşmiyor.")
        ref = references.get(ocr_experiment._group_key(meta))
        if not ref or ref.get("split") != "test":
            raise RealEvidenceBlocked("TRUSTED_REFERENCE_MISSING", "Test grubu için zaman hizalı insan referansı yok.")
        test_rows.append({"dataset_row": dataset_by_cue[cue_id], "metadata": meta,
                          "group_key": ocr_experiment._group_key(meta)})
    if not test_rows:
        raise RealEvidenceBlocked("TEST_CUES_MISSING", "Test split içinde OCR tarafından doğrulanmış altyazı yok.")
    return {
        "registry": registry, "paths": paths, "groups": groups, "test_groups": test_groups,
        "test_rows": test_rows, "dataset_hash": dataset_hash, "metadata_hash": metadata_hash,
        "split_hash": split_hash, "reference_hash": reference_hash,
        "condition_mapping": condition_mapping,
        "experiment": experiment, "source_language": source_language,
    }


def _opaque_source_id(value: str, label: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip().casefold()
    return hashlib.sha256(("translation-evaluation-" + label + "-v1:" + normalized).encode("utf-8")).hexdigest()[:32]


def _proposal_language_matches(proposal: dict, source_language: str, target_language: str) -> bool:
    """Reject manually imported candidates made for a different language pair."""
    source = proposal.get("source_language")
    target = proposal.get("target_language")
    return (isinstance(source, str) and isinstance(target, str) and
            source.strip().casefold() == source_language.strip().casefold() and
            target.strip().casefold() == target_language.strip().casefold())


def _verified_context(info: dict, sources: dict, cue_id: str) -> str:
    """Return current cue plus immediately adjacent human-verified source text."""
    cues = info["cues"]
    index = next((i for i, cue in enumerate(cues) if cue.get("cue_id") == cue_id), None)
    if index is None or cue_id not in sources:
        raise RealEvidenceBlocked("TEST_SOURCE_REVIEW_MISSING", "Test cue için doğrulanmış bağlam yok.")
    parts = []
    for offset, label in ((-1, "Önceki"), (0, "Mevcut"), (1, "Sonraki")):
        at = index + offset
        if at < 0 or at >= len(cues):
            continue
        neighbor_id = cues[at]["cue_id"]
        source = sources.get(neighbor_id)
        if source:
            parts.append(f"{label}: {source['event']['verified_source_text']}")
        elif offset == 0:
            raise RealEvidenceBlocked("TEST_SOURCE_REVIEW_MISSING", "Test cue için doğrulanmış kaynak metin yok.")
    return "\n".join(parts)


def _real_candidates(context: dict, current_review_id: object = None) -> dict:
    """Match all test cues to current human OCR events and manual AI proposals."""
    registered = {str(path) for path in context["paths"]["review_packs"]}
    pack_by_cue = {}
    current_pack_path = None
    if current_review_id:
        current_info = translation_review._info(current_review_id)
        current_pack_path = str(current_info["pack"].resolve(strict=True))
        if current_pack_path not in registered:
            raise RealEvidenceBlocked("CURRENT_PACK_NOT_IN_COHORT", "Açık review-pack server kayıtlı test cohort içinde değil.")
    for pack_path in context["paths"]["review_packs"]:
        try:
            opened_id, _public = ocr_review.open_pack(str(pack_path))
            info = translation_review._info(opened_id)
            sources = translation_review._verified_sources(info, verify_crops=True)
            proposals = translation_review.proposal_status(opened_id)["proposals"]
            _count, raw_events = translation_review._read_events(info["pack"] / "translation-review-events.jsonl")
            raw_proposals = {event["proposal_id"]: event for event in raw_events
                             if translation_review._valid_event(event) and event.get("event_type") == "proposal"}
        except Exception as exc:
            raise RealEvidenceBlocked("REVIEW_PACK_INVALID", "Bir registered review-pack OCR/AI event zinciri doğrulanamadı.") from exc
        cue_index = {cue["cue_id"]: cue for cue in info["cues"]}
        for cue_id, source in sources.items():
            cue = cue_index.get(cue_id)
            if not cue or cue_id in pack_by_cue:
                raise RealEvidenceBlocked("REVIEW_PACK_CUE_AMBIGUOUS", "Review-pack cue kimliği eksik veya yineleniyor.")
            pack_by_cue[cue_id] = {
                "pack_path": info["pack"], "review_id": opened_id, "info": info,
                "cue": cue, "source": source, "sources": sources, "proposals": proposals,
                "raw_proposals": raw_proposals,
            }

    selected, groups, missing = [], {}, []
    expected_test_ids = {item["dataset_row"]["cue_id"] for item in context["test_rows"]}
    if not expected_test_ids.issubset(pack_by_cue):
        raise RealEvidenceBlocked("TEST_SOURCE_REVIEW_MISSING", "Test cue'ların tümü etkin görsel OCR kararı ve crop ile doğrulanmalı.")
    for item in context["test_rows"]:
        row, meta, group_key = item["dataset_row"], item["metadata"], item["group_key"]
        cue_id = row["cue_id"]
        linked = pack_by_cue[cue_id]
        cue, source, info = linked["cue"], linked["source"], linked["info"]
        if (source["event"]["event_id"] != row["review_event_id"] or
                source["event"]["verified_source_text"] != row["source_text_verified"] or
                cue["crop_sha256_local"] != row["crop_sha256_local"] or
                cue["start_ms"] != row["start_ms"] or cue["end_ms"] != row["end_ms"]):
            raise RealEvidenceBlocked("OCR_SOURCE_LINEAGE_STALE", "Etkin OCR kararı/crop/SRT zamanları verified export ile farklı.")
        matching = [proposal for proposal in linked["proposals"]
                    if proposal.get("cue_id") == cue_id and
                    proposal.get("source_review_event_id") == source["event"]["event_id"] and
                    proposal.get("source_review", {}).get("status") == "confirmed" and
                    proposal.get("translation_review", {}).get("status") == "reviewed" and
                    isinstance(proposal.get("translation_review", {}).get("proposed_translation"), str)]
        if len(matching) != 1:
            missing.append(cue_id)
            continue
        proposal = matching[0]
        if not _proposal_language_matches(
                proposal, context["registry"]["source_language"], context["registry"]["target_language"]):
            raise RealEvidenceBlocked(
                "PROPOSAL_LANGUAGE_PAIR_MISMATCH",
                "Manuel AI önerisinin kaynak/hedef dili cohort dil çiftiyle eşleşmiyor.",
            )
        raw_proposal = linked["raw_proposals"].get(proposal["proposal_id"])
        if not raw_proposal:
            raise RealEvidenceBlocked("MANUAL_PROPOSAL_LINEAGE_INVALID", "Manuel AI yanıtı append-only proposal günlüğünde yok.")
        source_id = _opaque_source_id(meta["source_id"], "source")
        episode_id = _opaque_source_id(meta["episode_id"], "episode")
        group_id = evaluation.canonical_group_id(source_id, episode_id)
        old_split = groups.get(group_id)
        if old_split and old_split != "held_out":
            raise RealEvidenceBlocked("GROUP_SPLIT_LEAKAGE", "Aynı source/episode birden çok split'e ayrılıyor.")
        groups[group_id] = "held_out"
        target_text = proposal["translation_review"]["proposed_translation"]
        proposal_hash = _sha(_canonical(raw_proposal))
        context_text = _verified_context(info, linked["sources"], cue_id)
        selected.append({
            "cue_id": cue_id, "group_id": group_id, "source_id": source_id,
            "episode_id": episode_id, "source_status": source["event"]["status"],
            "source_review_event_id": source["event"]["event_id"],
            "source_text": source["event"]["verified_source_text"],
            "context_text": context_text,
            "context_sha256": _hash_text(context_text),
            "crop_sha256": cue["crop_sha256_local"], "srt_sha256": info["srt_sha256"],
            "candidate_text": target_text, "proposal_sha256": proposal_hash,
            "pack_path": str(linked["pack_path"]), "proposal_id": proposal["proposal_id"],
        })
    if missing:
        raise RealEvidenceBlocked("TEST_MANUAL_PROPOSALS_INCOMPLETE", "Her held-out cue için tek bir güncel manuel AI önerisi gerekli.")
    if len(groups) != len(context["test_groups"]):
        raise RealEvidenceBlocked("TEST_GROUP_COVERAGE_INCOMPLETE", "Her held-out test source/episode grubu için review-pack gerekli.")
    condition_id = _single_condition_for_proposals(
        context["condition_mapping"], [item["proposal_id"] for item in selected])
    evidence_payload = {
        "registry_sha256": context["paths"]["registry_sha256"],
        "dataset_sha256": context["dataset_hash"], "metadata_sha256": context["metadata_hash"],
        "split_sha256": context["split_hash"], "reference_sha256": context["reference_hash"],
        "experiment_sha256": context["experiment_hash"],
        "condition_mapping_sha256": context["condition_mapping"]["sha256"],
        "condition_id": condition_id,
        "items": [{key: item[key] for key in (
            "cue_id", "group_id", "source_review_event_id", "context_sha256", "crop_sha256", "srt_sha256",
            "proposal_id", "proposal_sha256", "pack_path")}
                   for item in sorted(selected, key=lambda row: row["cue_id"])],
    }
    evidence_fingerprint = _sha(_canonical(evidence_payload))
    evaluation_id = _sha(("translation-evaluation-real-v1:" + evidence_fingerprint).encode("ascii"))[:32]
    cases, links = [], {}
    for item in sorted(selected, key=lambda row: row["cue_id"]):
        candidate_id = _sha(("translation-evaluation-candidate-v1:" + evidence_fingerprint + ":" +
                             item["cue_id"] + ":" + item["proposal_id"]).encode("utf-8"))[:32]
        case_id = _sha(("translation-evaluation-case-v1:" + evidence_fingerprint + ":" +
                        item["cue_id"]).encode("utf-8"))[:32]
        cases.append({
            "case_id": case_id, "group_id": item["group_id"],
            "context_sha256": item["context_sha256"],
            "source_review_status": item["source_status"],
            "source_review_event_id": item["source_review_event_id"],
            "source_text_sha256": _hash_text(item["source_text"]),
            "crop_sha256": item["crop_sha256"], "srt_sha256": item["srt_sha256"],
            "candidates": [{"blind_candidate_id": candidate_id, "condition_id": condition_id,
                            "candidate_sha256": _hash_text(item["candidate_text"]),
                            "proposal_sha256": item["proposal_sha256"]}],
        })
        links[candidate_id] = {"pack_path": item["pack_path"], "cue_id": item["cue_id"],
                               "proposal_id": item["proposal_id"]}
    unique_groups = {}
    for item in selected:
        unique_groups[item["group_id"]] = {"group_id": item["group_id"], "source_id": item["source_id"],
                                            "episode_id": item["episode_id"], "split": "held_out"}
    return {"evaluation_id": evaluation_id, "cases": cases,
            "groups": [unique_groups[key] for key in sorted(unique_groups)], "links": links,
            "condition_id": condition_id, "evidence_fingerprint": evidence_fingerprint,
            "evidence_payload": evidence_payload}


def _real_registry_context(review_id: object = None) -> dict:
    context = _phase5_evidence()
    context["experiment_hash"] = _sha(context["paths"]["experiment"].read_bytes())
    candidate_data = _real_candidates(context, review_id)
    context["evidence_fingerprint"] = candidate_data["evidence_fingerprint"]
    context["candidate_data"] = candidate_data
    context["evidence_raw"] = candidate_data["evidence_payload"]
    return context


def _real_manifest(context: dict) -> dict:
    registry = context["registry"]
    data = context["candidate_data"]
    return {
        "schema_version": evaluation.SCHEMA_VERSION,
        "evaluation_id": data["evaluation_id"],
        "dataset_kind": "human_held_out",
        "source_language": registry["source_language"],
        "target_language": registry["target_language"],
        "style_profile_sha256": _hash_text(registry["style_profile"]),
        "glossary_sha256": _hash_text(registry["glossary"]),
        "rubric_version": evaluation.RUBRIC["version"],
        "rubric_sha256": evaluation.RUBRIC_SHA256,
        "primary_metric_version": evaluation.PRIMARY_METRIC_VERSION,
        "split_policy": "source_episode_group_v1",
        "group_identity_basis": "caller_verified_source_episode_ids_v1",
        "coverage_floor": {"minimum_rated_fraction_per_held_out_group":
                           registry["minimum_rated_fraction_per_held_out_group"]},
        "groups": data["groups"], "cases": data["cases"],
    }


def _real_base() -> Path:
    root = _local_root().parent / "real"
    if not root.exists():
        root.mkdir(mode=0o700)
    root = evaluation._safe_root(root)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def create_real_evaluation(review_id: object) -> dict:
    context = _real_registry_context(review_id)
    evaluation_id = context["candidate_data"]["evaluation_id"]
    root = _real_base() / evaluation_id
    if root.exists():
        return real_status(evaluation_id)
    root.mkdir(mode=0o700)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    root = evaluation._safe_root(root)
    manifest = _real_manifest(context)
    seal = evaluation.create_evaluation(root, manifest)
    sidecar = {
        "schema_version": 1, "evaluation_id": evaluation_id,
        "manifest_sha256": seal["manifest_sha256"],
        "evidence_fingerprint": context["evidence_fingerprint"],
        "evidence": context["evidence_raw"],
        "links": context["candidate_data"]["links"],
    }
    sidecar_path = evaluation._child(root, _REAL_LINKS_FILE)
    fd = os.open(sidecar_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(_canonical(sidecar) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return real_status(evaluation_id)


def _load_real_evaluation(evaluation_id: object) -> tuple[Path, dict, dict, dict]:
    if not evaluation._id(evaluation_id):
        raise evaluation.EvaluationError("Gerçek değerlendirme kimliği geçersiz")
    root = evaluation._safe_root(_real_base() / evaluation_id)
    _path, manifest, manifest_sha, _events = evaluation._load(root)
    if manifest["dataset_kind"] != "human_held_out":
        raise evaluation.EvaluationError("Sentetik veri gerçek cohort olarak kullanılamaz")
    sidecar_path = evaluation._child(root, _REAL_LINKS_FILE)
    if not sidecar_path.exists() or sidecar_path.stat().st_size > 16 * 1024 * 1024 or sidecar_path.stat().st_nlink > 1:
        raise evaluation.EvaluationError("Kohort source-link sidecar eksik/güvensiz")
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate key")
                value[key] = item
            return value
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise evaluation.EvaluationError("Kohort source-link sidecar okunamıyor") from exc
    if (not isinstance(sidecar, dict) or set(sidecar) != {
            "schema_version", "evaluation_id", "manifest_sha256", "evidence_fingerprint", "evidence", "links"} or
            type(sidecar["schema_version"]) is not int or sidecar["schema_version"] != 1 or
            sidecar["evaluation_id"] != evaluation_id or sidecar["manifest_sha256"] != manifest_sha or
            not evaluation._hash(sidecar["evidence_fingerprint"]) or not isinstance(sidecar["evidence"], dict) or
            not isinstance(sidecar["links"], dict)):
        raise evaluation.EvaluationError("Kohort source-link sidecar şeması/hash'i geçersiz")
    context = _real_registry_context()
    expected_manifest = _real_manifest(context)
    if (manifest["evaluation_id"] != context["candidate_data"]["evaluation_id"] or
            _sha(_canonical(expected_manifest)) != manifest_sha or
            sidecar["evidence_fingerprint"] != context["evidence_fingerprint"] or
            sidecar["evidence"] != context["evidence_raw"]):
        raise evaluation.EvaluationError("Registered Phase 5 kaynak/split/referans kanıtı cohort dondurulduktan sonra değişti")
    expected_links = context["candidate_data"]["links"]
    if sidecar["links"] != expected_links:
        raise evaluation.EvaluationError("Registered manual proposal/source link kümesi cohort ile eşleşmiyor")
    return root, manifest, sidecar, context


def _real_source_resolver(manifest: dict, sidecar: dict):
    cases = {case["case_id"]: case for case in manifest["cases"]}
    candidate_to_case = {candidate["blind_candidate_id"]: case
                         for case in manifest["cases"] for candidate in case["candidates"]}
    resolved_packs = {}

    def resolve(case_id: str, candidate_id: str):
        case, link = cases.get(case_id), sidecar["links"].get(candidate_id)
        if not case or not link:
            return None
        pack_path = link.get("pack_path")
        if not isinstance(pack_path, str):
            return None
        if pack_path not in resolved_packs:
            safe_pack = _local_evidence_path(pack_path, "review-pack", directory=True)
            review_id, _public = ocr_review.open_pack(str(safe_pack))
            info = translation_review._info(review_id)
            sources = translation_review._verified_sources(info, verify_crops=True)
            proposals = translation_review.proposal_status(review_id)["proposals"]
            count, raw_events = translation_review._read_events(info["pack"] / "translation-review-events.jsonl")
            raw_proposals = {event["proposal_id"]: event for event in raw_events
                             if translation_review._valid_event(event) and event.get("event_type") == "proposal"}
            resolved_packs[pack_path] = (review_id, info, sources, proposals, raw_proposals)
        review_id, info, sources, proposals, raw_proposals = resolved_packs[pack_path]
        cue_id, proposal_id = link.get("cue_id"), link.get("proposal_id")
        source = sources.get(cue_id)
        proposal = next((row for row in proposals if row.get("proposal_id") == proposal_id and row.get("cue_id") == cue_id), None)
        raw_proposal = raw_proposals.get(proposal_id)
        if not source or not proposal or not raw_proposal:
            return None
        target = proposal.get("translation_review", {}).get("proposed_translation")
        candidate = next((row for row in cases[candidate_to_case[candidate_id]["case_id"]]["candidates"]
                          if row["blind_candidate_id"] == candidate_id), None)
        if (not isinstance(target, str) or not candidate or
                source["event"]["event_id"] != case["source_review_event_id"] or
                _hash_text(source["event"]["verified_source_text"]) != case["source_text_sha256"] or
                _hash_text(_verified_context(info, sources, cue_id)) != case["context_sha256"] or
                source["cue"]["crop_sha256_local"] != case["crop_sha256"] or info["srt_sha256"] != case["srt_sha256"] or
                _hash_text(target) != candidate["candidate_sha256"] or
                _sha(_canonical(raw_proposal)) != candidate["proposal_sha256"]):
            return None
        return {
            "active": True, "context_sha256": case["context_sha256"],
            "source_review_status": case["source_review_status"],
            "source_review_event_id": source["event"]["event_id"],
            "source_text_sha256": _hash_text(source["event"]["verified_source_text"]),
            "crop_sha256": source["cue"]["crop_sha256_local"], "srt_sha256": info["srt_sha256"],
            "candidate_sha256": _hash_text(target), "proposal_sha256": _sha(_canonical(raw_proposal)),
        }
    return resolve


def real_status(evaluation_id: object) -> dict:
    root, manifest, sidecar, context = _load_real_evaluation(evaluation_id)
    resolver = _real_source_resolver(manifest, sidecar)
    report = evaluation.aggregate_report(root, resolver)
    report["condition_attribution"] = {
        "policy": "server_side_single_condition_assignment",
        "interpretation": "descriptive_only_provider_identity_user_maintained",
    }
    cases = {case["case_id"]: case for case in manifest["cases"]}
    candidates_by_id = {candidate["blind_candidate_id"]: (case, candidate)
                        for case in manifest["cases"] for candidate in case["candidates"]}
    items = []
    for candidate_id, (case, candidate) in candidates_by_id.items():
        link = sidecar["links"][candidate_id]
        pack_path = _local_evidence_path(link["pack_path"], "review-pack", directory=True)
        review_id, public = ocr_review.open_pack(str(pack_path))
        info = translation_review._info(review_id)
        sources = translation_review._verified_sources(info, verify_crops=True)
        source = sources.get(link["cue_id"])
        proposal = next(row for row in translation_review.proposal_status(review_id)["proposals"]
                        if row["proposal_id"] == link["proposal_id"])
        items.append({
            "blind_candidate_id": candidate_id,
            "source_text": source["event"]["verified_source_text"],
            "context_text": _verified_context(info, sources, link["cue_id"]),
            "target_text": proposal["translation_review"]["proposed_translation"],
            "start_ms": source["cue"]["start_ms"], "end_ms": source["cue"]["end_ms"],
            "crop_url": f"/api/ocr-inceleme/{review_id}/crop/{link['cue_id']}",
        })
    active = evaluation._active_ratings(evaluation._load(root)[3])
    active_ratings = {
        candidate_id: {
            key: event[key] for key in (
                "event_id", "status", "dimensions", "issues", "rubric_outcome", "exclusion_reason")
        }
        for (candidate_id, reviewer), event in active.items() if reviewer == "local_user"
    }
    return {
        "evaluation_id": manifest["evaluation_id"], "mode": "human_held_out",
        "source_language": manifest["source_language"], "target_language": manifest["target_language"],
        "style_profile": context["registry"]["style_profile"],
        "glossary": context["registry"]["glossary"],
        "rubric": evaluation.RUBRIC, "items": items,
        "rated_candidate_ids": list(active_ratings),
        "active_ratings": active_ratings,
        "mapping_reveal_available": _ratings_complete(set(candidates_by_id), active),
        "report": report,
    }


def reveal_condition_mapping(evaluation_id: object) -> dict:
    """Unblind the private condition label only after every candidate is rated."""
    root, manifest, _sidecar, context = _load_real_evaluation(evaluation_id)
    _path, _manifest, _digest, events = evaluation._load(root)
    active = evaluation._active_ratings(events)
    candidate_ids = {candidate["blind_candidate_id"]
                     for case in manifest["cases"] for candidate in case["candidates"]}
    if not _ratings_complete(candidate_ids, active):
        raise evaluation.EvaluationError("Koşul eşlemesi tüm kör adaylar puanlandıktan sonra açılabilir")
    conditions = {candidate["condition_id"]
                  for case in manifest["cases"] for candidate in case["candidates"]}
    if len(conditions) != 1:
        raise evaluation.EvaluationError("Koşul eşlemesi tek koşul pilotu için beklenen biçimde değil")
    condition_id = next(iter(conditions))
    private_label = context["condition_mapping"]["labels"].get(condition_id)
    if not private_label:
        raise evaluation.EvaluationError("Özel koşul eşlemesi cohort ile eşleşmiyor")
    return {"revealed": True, "condition_id": condition_id, "private_label": private_label}


def record_real_rating(evaluation_id: object, blind_candidate_id: object,
                       status_value: object, dimensions: object, issues: object,
                       rubric_outcome: object, exclusion_reason: object = None,
                       supersedes_event_id: object = None) -> dict:
    root, manifest, sidecar, _context = _load_real_evaluation(evaluation_id)
    event = evaluation.record_rating(
        root, blind_candidate_id, "local_user", status_value, dimensions, issues,
        rubric_outcome, source_resolver=_real_source_resolver(manifest, sidecar),
        exclusion_reason=exclusion_reason,
        supersedes_event_id=supersedes_event_id,
    )
    return {"ok": True, "mode": "human_held_out", "event_id": event["event_id"],
            "message": "İnsan puanı append-only kalite günlüğüne eklendi."}


_PUBLIC_BLOCKER_MESSAGES = {
    "SERVER_COHORT_REGISTRY_MISSING": "Sunucunun özel yerel depolamasında cohort kayıt dosyası yok.",
    "INVALID_COHORT_REGISTRY": "Sunucu tarafındaki cohort kayıt dosyasının şeması geçersiz.",
    "MISSING_EVIDENCE": "Kayıtlı Phase 5 kanıt dosyalarından biri eksik.",
    "UNSAFE_EVIDENCE_PATH": "Kayıtlı kanıt junction veya symlink içeriyor.",
    "NONLOCAL_EVIDENCE_PATH": "Kayıtlı kanıt yerel ve eşitlenmeyen depolamada değil.",
    "PHASE5_EVIDENCE_INVALID": "Dataset, split, rights review veya zaman hizalı referans doğrulanamadı.",
    "HELDOUT_GROUPS_INSUFFICIENT": "En az üç bağımsız source/episode grubu ve test split gerekli.",
    "TRUSTED_TEST_REFERENCE_MISSING": "Test grubu için doğrulanmış hizalı referans yok.",
    "TEST_SOURCE_REVIEW_MISSING": "Her test cue için güncel insan OCR kararı ve doğrulanmış crop gerekli.",
    "OCR_SOURCE_LINEAGE_STALE": "Etkin OCR kararı, crop veya SRT zamanları kayıtlı export'tan farklı.",
    "TEST_MANUAL_PROPOSALS_INCOMPLETE": "Her held-out cue için tek güncel manuel AI önerisi gerekli.",
    "TEST_GROUP_COVERAGE_INCOMPLETE": "Kayıtlı held-out grupların tamamında inceleme paketi yok.",
    "CURRENT_PACK_NOT_IN_COHORT": "Açık review-pack sunucu kayıtlı cohort içinde değil.",
    "PROPOSAL_LANGUAGE_PAIR_MISMATCH": "Manuel AI önerisinin kaynak/hedef dili cohort dil çiftiyle eşleşmiyor.",
    "SERVER_CONDITION_MAP_MISSING": "Sunucunun özel yerel koşul eşlemesi bulunamadı.",
    "INVALID_CONDITION_MAP": "Sunucu tarafındaki özel koşul eşlemesi doğrulanamadı.",
    "CONDITION_ASSIGNMENT_MISSING": "Her held-out AI önerisine özel koşul ataması gerekli.",
    "MIXED_CONDITIONS": "Bu pilot yalnız tek koşul destekler; karışık koşul atamaları reddedildi.",
}


def readiness(review_id: object) -> dict:
    """Revalidate all locally registered cohort evidence on every readiness check."""
    info = translation_review._info(review_id)
    source_rows = translation_review._verified_sources(info, verify_crops=True)
    proposals = translation_review.proposal_status(review_id)["proposals"]
    blockers = []
    cohort = None
    try:
        cohort = _real_registry_context(review_id)
    except RealEvidenceBlocked as exc:
        blockers.append({"code": exc.code,
                         "message": _PUBLIC_BLOCKER_MESSAGES.get(
                             exc.code, "Sunucu tarafındaki held-out cohort kanıtı doğrulanamadı.")})
    except Exception:
        # Do not return internal paths, source text, or validator exceptions.
        blockers.append({"code": "COHORT_VALIDATION_FAILED",
                         "message": "Sunucudaki held-out cohort kanıtı doğrulanamadı."})
    ready = cohort is not None
    result = {
        "real_evaluation_enabled": ready,
        "allowed_mode": "human_held_out" if ready else "synthetic_only",
        "blockers": blockers,
        "active_verified_source_cues": len(source_rows),
        "valid_manual_ai_proposals": len(proposals),
        "manual_ai_import_available": True,
        "quality_claims_available": False,
        "phase6_eligible": False,
    }
    if ready:
        result["held_out_group_count"] = len(cohort["candidate_data"]["groups"])
        result["held_out_candidate_count"] = len(cohort["candidate_data"]["cases"])
        result["coverage_floor_per_group"] = cohort["registry"][
            "minimum_rated_fraction_per_held_out_group"]
        result["message"] = (
            "Kanıtlar doğrulandı. Kör insan puanlaması açılabilir; kalite ölçüsü ancak "
            "önceden belirlenen grup kapsam eşiği karşılanınca raporlanır."
        )
    return result

