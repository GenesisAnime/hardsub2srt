#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed validation scaffold for a local OCR model candidate release.

This CLI validates metadata and hashes only. It never trains, loads, packages,
installs, activates, uploads, or publishes a model. Current Phase 5 v1 reports
are preparation-only and can never pass the Phase 6 release gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime
from itertools import product
from pathlib import Path, PurePosixPath

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SMALL_JSON_BYTES = 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
PYTHON_VERSION = re.compile(r"^3\.(?:[0-9]|[1-9][0-9])$")
BACKEND_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
OS_VALUES = {"windows", "linux", "macos"}
DEVICE_VALUES = {"gpu", "cpu"}
MODEL_FORMATS = {"onnx", "safetensors", "torch-state-dict"}


class GateError(ValueError):
    pass


def _load_json(path: Path, cap: int = MAX_JSON_BYTES) -> tuple[object, bytes]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(cap + 1)
    except OSError as exc:
        raise GateError(f"JSON okunamadı: {path}") from exc
    if len(raw) > cap:
        raise GateError(f"JSON {cap} byte sınırını aşıyor: {path.name}")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object), raw
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"Geçersiz UTF-8 JSON: {path.name}") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise GateError(f"JSON içinde yinelenen alan reddedildi: {key}")
        result[key] = value
    return result


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise GateError(f"Dosya okunamadı: {path}") from exc
    return digest.hexdigest(), size


def _safe_file(root: Path, value: object, label: str) -> Path:
    if type(value) is not str or not value or "\\" in value:
        raise GateError(f"{label}: ileri eğik çizgili göreli yol gerekli")
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts or any(part in {"", "."} or ":" in part for part in rel.parts):
        raise GateError(f"{label}: kök dışına çıkabilen veya mutlak yol reddedildi")
    candidate = root.joinpath(*rel.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise GateError(f"{label}: dosya yok veya aday klasörü dışına çıkıyor") from exc
    if not resolved.is_file():
        raise GateError(f"{label}: düzenli dosya gerekli")
    return resolved


def _obj(value: object, label: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if type(value) is not dict:
        raise GateError(f"{label}: JSON nesnesi gerekli")
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing or extra:
        raise GateError(f"{label}: eksik alanlar={sorted(missing)}; beklenmeyen alanlar={sorted(extra)}")
    return value


def _str(value: object, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value.strip() or (pattern is not None and not pattern.fullmatch(value)):
        raise GateError(f"{label}: geçersiz metin değeri")
    return value


def _bool(value: object, label: str, expected: bool | None = None) -> bool:
    if type(value) is not bool or (expected is not None and value is not expected):
        raise GateError(f"{label}: geçersiz boolean değeri")
    return value


def _int(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise GateError(f"{label}: geçersiz integer değeri")
    return value


def _digest(value: object, label: str) -> str:
    return _str(value, label, HEX64)


def _string_list(value: object, label: str, pattern: re.Pattern[str] | None = None) -> list[str]:
    if type(value) is not list or not value:
        raise GateError(f"{label}: boş olmayan liste gerekli")
    items = [_str(item, f"{label}[]", pattern) for item in value]
    if len(set(items)) != len(items):
        raise GateError(f"{label}: tekrar eden değer var")
    return items


def _versioned_object(value: object, label: str, version: int) -> dict:
    if type(value) is not dict or "schema_version" not in value:
        raise GateError(f"{label}: schema_version alanlı JSON nesnesi gerekli")
    obj = value
    # bool is a subclass of int in Python; exact type check is intentional.
    if type(obj["schema_version"]) is not int or obj["schema_version"] != version:
        raise GateError(f"{label}: schema_version {version} tam sayı olmalı (boolean kabul edilmez)")
    return obj


def _model_files(value: object, root: Path) -> tuple[list[dict], str]:
    if type(value) is not list or not value:
        raise GateError("model_files: en az bir model dosyası gerekli")
    normalized: list[dict] = []
    paths: set[str] = set()
    for index, raw in enumerate(value):
        entry = _obj(raw, f"model_files[{index}]", {"path", "size_bytes", "sha256"})
        rel = _str(entry["path"], f"model_files[{index}].path")
        if rel in paths:
            raise GateError(f"Yinelenen model dosyası: {rel}")
        paths.add(rel)
        path = _safe_file(root, rel, f"model_files[{index}].path")
        expected_size = _int(entry["size_bytes"], f"model_files[{index}].size_bytes", 1)
        expected_sha = _digest(entry["sha256"], f"model_files[{index}].sha256")
        actual_sha, actual_size = _sha256_file(path)
        if actual_size != expected_size or actual_sha != expected_sha:
            raise GateError(f"Model dosya hash'i/boyutu uyuşmuyor: {rel}")
        normalized.append({"path": rel, "size_bytes": expected_size, "sha256": expected_sha})
    normalized.sort(key=lambda item: item["path"])
    payload = "".join(f"{item['path']}\0{item['size_bytes']}\0{item['sha256']}\n" for item in normalized).encode("utf-8")
    return normalized, hashlib.sha256(payload).hexdigest()


def _bound_file_list(value: object, label: str) -> list[dict]:
    if type(value) is not list or not value:
        raise GateError(f"{label}: dosya hash listesi gerekli")
    rows: list[dict] = []
    for index, raw in enumerate(value):
        item = _obj(raw, f"{label}[{index}]", {"path", "size_bytes", "sha256"})
        rows.append({
            "path": _str(item["path"], f"{label}[{index}].path"),
            "size_bytes": _int(item["size_bytes"], f"{label}[{index}].size_bytes", 1),
            "sha256": _digest(item["sha256"], f"{label}[{index}].sha256"),
        })
    rows.sort(key=lambda item: item["path"])
    if len({item["path"] for item in rows}) != len(rows):
        raise GateError(f"{label}: tekrar eden model dosyası")
    return rows


def _check_phase5_report(report: dict, report_hash: str, manifest: dict,
                         trusted: dict, model_hashes: list[dict], bundle_hash: str) -> list[str]:
    blockers: list[str] = []
    # Phase 5 v1 is unconditionally preparation-only by its schema and writer.
    if type(report.get("schema_version")) is not int:
        raise GateError("Phase 5 schema_version boolean veya sayı olmayan değer olamaz")
    if report.get("schema_version") != 2:
        blockers.append("Phase 5 v1 raporu PREPARED_NOT_RUN/blocked formatındadır ve release için her koşulda reddedilir")
        return blockers
    try:
        report = _obj(report, "Phase 5 evaluated report v2", {
            "schema_version", "status", "experiment_id", "dataset", "provenance", "data_policy",
            "experiment", "metrics", "stop_gate", "candidate", "release_gate",
        })
        if report["status"] != "EVALUATED":
            raise GateError("Phase 5 status EVALUATED değil")
        dataset = _obj(report["dataset"], "Phase 5 dataset", {"manifest_sha256", "metadata_sha256"})
        provenance = _obj(report["provenance"], "Phase 5 provenance", {
            "source_commit", "training_recipe_id", "training_engine", "model_files", "model_bundle_sha256",
        })
        data_policy = _obj(report["data_policy"], "Phase 5 data policy", {
            "rights_verified", "human_rights_review_complete", "training_performed", "local_only",
        })
        candidate = _obj(report["candidate"], "Phase 5 candidate", {
            "id", "version", "source_commit", "training_recipe_id", "training_engine",
            "model_files", "model_bundle_sha256",
        })
        experiment = _obj(report["experiment"], "Phase 5 experiment", {"baseline_id", "candidate_id"})
        release = _obj(report["release_gate"], "Phase 5 release gate", {
            "status", "criteria_predeclared", "no_regressions", "baseline_candidate_same_conditions",
            "frozen_test_split_sha256", "trusted_reference_sha256", "coverage_passed",
        })
        stop = _obj(report["stop_gate"], "Phase 5 stop gate", {
            "experiment_run_blocked", "human_provenance_verified", "human_rights_review_complete",
            "rights_verified", "metrics_claimable", "trusted_reference_required",
        })
        _digest(dataset["manifest_sha256"], "Phase 5 dataset.manifest_sha256")
        _digest(dataset["metadata_sha256"], "Phase 5 dataset.metadata_sha256")
        if dataset["manifest_sha256"] != manifest["provenance"]["dataset_manifest_sha256"]:
            blockers.append("Phase 5 dataset hash aday manifestiyle eşleşmiyor")
        if dataset["metadata_sha256"] != manifest["provenance"]["dataset_metadata_sha256"]:
            blockers.append("Phase 5 metadata hash aday manifestiyle eşleşmiyor")
        for section, label in ((provenance, "provenance"), (candidate, "candidate")):
            _str(section["source_commit"], f"Phase 5 {label}.source_commit", COMMIT)
            _str(section["training_recipe_id"], f"Phase 5 {label}.training_recipe_id")
            _str(section["training_engine"], f"Phase 5 {label}.training_engine")
            _digest(section["model_bundle_sha256"], f"Phase 5 {label}.model_bundle_sha256")
        if _bound_file_list(candidate["model_files"], "Phase 5 candidate.model_files") != model_hashes or _bound_file_list(provenance["model_files"], "Phase 5 provenance.model_files") != model_hashes:
            blockers.append("Phase 5 raporu adayın birebir model dosyası hash listesini bağlamıyor")
        if candidate["model_bundle_sha256"] != bundle_hash or provenance["model_bundle_sha256"] != bundle_hash:
            blockers.append("Phase 5 raporundaki model bundle hash'i aday dosyalarıyla eşleşmiyor")
        for field in ("source_commit", "training_recipe_id", "training_engine"):
            if candidate[field] != provenance[field] or candidate[field] != manifest["provenance"][field]:
                blockers.append(f"Phase 5 {field} aday provenance'ıyla eşleşmiyor")
        if candidate["id"] != manifest["candidate"]["id"] or candidate["version"] != manifest["candidate"]["version"]:
            blockers.append("Phase 5 değerlendirmesi başka aday kimliği/sürümüne ait")
        if experiment["candidate_id"] != candidate["id"]:
            blockers.append("Phase 5 deneyi aday kimliğiyle eşleşmiyor")
        if release["status"] != "PASS":
            blockers.append("Phase 5 hesaplanmış release gate PASS değil")
        for key in ("criteria_predeclared", "no_regressions", "baseline_candidate_same_conditions", "coverage_passed"):
            _bool(release[key], f"Phase 5 release_gate.{key}", True)
        _digest(release["frozen_test_split_sha256"], "Phase 5 frozen_test_split_sha256")
        _digest(release["trusted_reference_sha256"], "Phase 5 trusted_reference_sha256")
        for key in ("experiment_run_blocked",):
            _bool(stop[key], f"Phase 5 stop_gate.{key}", False)
        for key in ("human_provenance_verified", "human_rights_review_complete", "rights_verified", "metrics_claimable", "trusted_reference_required"):
            _bool(stop[key], f"Phase 5 stop_gate.{key}", True)
        for key in ("rights_verified", "human_rights_review_complete", "training_performed", "local_only"):
            _bool(data_policy[key], f"Phase 5 data_policy.{key}", True)
        if data_policy["rights_verified"] is not True or data_policy["human_rights_review_complete"] is not True or data_policy["local_only"] is not True:
            blockers.append("Phase 5 rights/local data policy kapıları geçmiyor")
        metrics = _obj(report["metrics"], "Phase 5 metrics", {"ocr"})
        ocr = _obj(metrics["ocr"], "Phase 5 OCR metrics", {
            "cer", "wer", "exact_match_cue_rate", "cue_precision", "cue_recall",
            "timing_start_abs_error_ms", "timing_end_abs_error_ms", "coverage", "per_group",
        })
        for key in ("cer", "wer", "exact_match_cue_rate", "cue_precision", "cue_recall", "coverage"):
            value = ocr[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise GateError(f"Phase 5 metrics.ocr.{key}: ölçülmüş 0..1 sayısı gerekli")
        for key in ("timing_start_abs_error_ms", "timing_end_abs_error_ms"):
            value = ocr[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise GateError(f"Phase 5 metrics.ocr.{key}: ölçülmüş, negatif olmayan sayı gerekli")
        if type(ocr["per_group"]) is not list or not ocr["per_group"]:
            raise GateError("Phase 5 per_group ölçümü boş")
        if report_hash not in trusted.get("trusted_phase5_reports", []):
            blockers.append("Phase 5 rapor hash'i pinli trust listesinde yok; öz-beyan PASS kabul edilmez")
    except (KeyError, GateError, TypeError) as exc:
        blockers.append(f"Phase 5 PASS raporu geçersiz: {exc}")
    return blockers


def validate(manifest_path: Path, report_path: Path, trust_path: Path) -> list[str]:
    blockers: list[str] = []
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent
    raw_manifest, _ = _load_json(manifest_path, MAX_SMALL_JSON_BYTES)
    manifest = _versioned_object(raw_manifest, "candidate manifest", 1)
    manifest = _obj(manifest, "candidate manifest", {
        "schema_version", "candidate", "provenance", "evaluation", "rights_review", "model_files",
        "compatibility", "migration", "rollback", "activation_policy",
    })
    candidate = _obj(manifest["candidate"], "candidate", {"id", "version", "model_format"})
    _str(candidate["id"], "candidate.id")
    _str(candidate["version"], "candidate.version", SEMVER)
    _str(candidate["model_format"], "candidate.model_format")
    if candidate["model_format"] not in MODEL_FORMATS:
        raise GateError("candidate.model_format desteklenmiyor")
    files, bundle_hash = _model_files(manifest["model_files"], root)

    provenance = _obj(manifest["provenance"], "provenance", {
        "source_commit", "training_recipe_id", "dataset_manifest_sha256", "dataset_metadata_sha256", "training_engine",
    })
    _str(provenance["source_commit"], "provenance.source_commit", COMMIT)
    _str(provenance["training_recipe_id"], "provenance.training_recipe_id")
    _str(provenance["training_engine"], "provenance.training_engine")
    _digest(provenance["dataset_manifest_sha256"], "provenance.dataset_manifest_sha256")
    _digest(provenance["dataset_metadata_sha256"], "provenance.dataset_metadata_sha256")

    evaluation = _obj(manifest["evaluation"], "evaluation", {"report_path", "report_sha256", "phase5_status", "release_gate"})
    report_file = _safe_file(root, evaluation["report_path"], "evaluation.report_path")
    expected_report_hash = _digest(evaluation["report_sha256"], "evaluation.report_sha256")
    report_hash, _ = _sha256_file(report_file)
    if report_hash != expected_report_hash:
        raise GateError("Phase 5 raporunun SHA-256 değeri manifestle eşleşmiyor")
    if report_file != report_path.resolve(strict=True):
        raise GateError("CLI ile verilen Phase 5 raporu manifestteki raporla aynı değil")
    if evaluation["phase5_status"] != "EVALUATED" or evaluation["release_gate"] != "PASS":
        blockers.append("Aday manifesti Phase 5 EVALUATED/PASS sonucuna bağlanmıyor")
    raw_report, _ = _load_json(report_file)
    report = _obj(raw_report, "Phase 5 report")
    blockers.extend(_check_phase5_report(report, report_hash, manifest, _load_trust(trust_path), files, bundle_hash))

    rights = _obj(manifest["rights_review"], "rights_review", {"receipt_path", "receipt_sha256", "review_id"})
    receipt_path = _safe_file(root, rights["receipt_path"], "rights_review.receipt_path")
    receipt_sha = _digest(rights["receipt_sha256"], "rights_review.receipt_sha256")
    actual_receipt_sha, _ = _sha256_file(receipt_path)
    if receipt_sha != actual_receipt_sha:
        raise GateError("Hak inceleme makbuzu hash'i uyuşmuyor")
    receipt_raw, _ = _load_json(receipt_path, MAX_SMALL_JSON_BYTES)
    receipt = _check_rights_receipt(receipt_raw, rights["review_id"], manifest, report_hash, files, bundle_hash)
    trust = _load_trust(trust_path)
    if receipt_sha not in trust["trusted_rights_receipts"]:
        blockers.append("Hak receipt hash'i pinli trust listesinde yok; öz-beyan insan review sayılmaz")
    if report_hash not in trust["trusted_phase5_reports"]:
        blockers.append("Phase 5 rapor hash'i pinli trust listesinde yok")

    compatibility = _obj(manifest["compatibility"], "compatibility", {
        "operating_systems", "python_versions", "backends", "devices",
    })
    operating_systems = _string_list(compatibility["operating_systems"], "compatibility.operating_systems")
    python_versions = _string_list(compatibility["python_versions"], "compatibility.python_versions", PYTHON_VERSION)
    backends = _string_list(compatibility["backends"], "compatibility.backends", BACKEND_ID)
    devices = _string_list(compatibility["devices"], "compatibility.devices")
    if any(item not in OS_VALUES for item in operating_systems):
        raise GateError("compatibility.operating_systems içinde desteklenmeyen ad var")
    if any(item not in DEVICE_VALUES for item in devices):
        raise GateError("compatibility.devices yalnız gpu/cpu olabilir")
    # The product contract is GPU by default with CPU as an explicit opt-in.
    # A manifest cannot claim this policy while omitting validation for either mode.
    if set(devices) != DEVICE_VALUES:
        blockers.append("Ürün sözleşmesi gereği aday hem GPU varsayılanı hem CPU opt-in cihaz desteğini ve kanıtını taşımalı")
    evidence = trust["trusted_compatibility_evidence"]
    if not evidence:
        blockers.append("Desteklenen OS/Python/backend/device kombinasyonları için pinli doğrulama kanıtı yok")
    else:
        for os_name, py_version, backend, device in product(operating_systems, python_versions, backends, devices):
            match = any(
                item["os"] == os_name and item["python"] == py_version and item["backend"] == backend and
                item["device"] == device and item["model_bundle_sha256"] == bundle_hash and
                item["phase5_report_sha256"] == report_hash and item["validation_passed"] is True
                for item in evidence
            )
            if not match:
                blockers.append(f"Uyumluluk kombinasyonu için pinli PASS kanıtı yok: {os_name}/{py_version}/{backend}/{device}")

    activation = _obj(manifest["activation_policy"], "activation_policy", {
        "default_device", "cpu_opt_in", "automatic_replacement", "user_confirmation_required",
    })
    if _str(activation["default_device"], "activation_policy.default_device") != "gpu":
        blockers.append("Varsayılan OCR cihazı GPU olmalı")
    _bool_required(activation["cpu_opt_in"], "activation_policy.cpu_opt_in", True)
    _bool_required(activation["automatic_replacement"], "activation_policy.automatic_replacement", False)
    _bool_required(activation["user_confirmation_required"], "activation_policy.user_confirmation_required", True)
    migration = _obj(manifest["migration"], "migration", {"from_versions", "steps", "data_format_version"})
    _string_list(migration["from_versions"], "migration.from_versions", SEMVER)
    _string_list(migration["steps"], "migration.steps")
    _str(migration["data_format_version"], "migration.data_format_version")
    rollback = _obj(manifest["rollback"], "rollback", {"supported", "steps", "data_preserved"})
    _bool_required(rollback["supported"], "rollback.supported", True)
    _string_list(rollback["steps"], "rollback.steps")
    _bool_required(rollback["data_preserved"], "rollback.data_preserved", True)
    return blockers


def _bool_required(value: object, label: str, expected: bool) -> None:
    if type(value) is not bool or value is not expected:
        raise GateError(f"{label}: {expected} boolean gerekli")


def _check_rights_receipt(raw: object, expected_review_id: object, manifest: dict,
                          report_hash: str, model_files: list[dict], bundle_hash: str) -> dict:
    receipt = _versioned_object(raw, "rights review receipt", 1)
    receipt = _obj(receipt, "rights review receipt", {
        "schema_version", "review_id", "decision", "reviewer", "reviewed_at", "scope",
        "dataset_manifest_sha256", "dataset_metadata_sha256", "license_evidence_sha256s",
        "candidate_id", "candidate_version", "source_commit", "training_recipe_id",
        "training_engine", "model_files", "model_bundle_sha256", "phase5_report_sha256",
    })
    if type(receipt["review_id"]) is not str or receipt["review_id"] != expected_review_id:
        raise GateError("Rights receipt review_id manifestle eşleşmiyor")
    if receipt["decision"] != "APPROVED":
        raise GateError("Rights receipt kararı APPROVED değil")
    _str(receipt["reviewed_at"], "rights.reviewed_at")
    _str(receipt["scope"], "rights.scope")
    reviewer = _obj(receipt["reviewer"], "rights.reviewer", {"reviewer_id", "authority"})
    _str(reviewer["reviewer_id"], "rights.reviewer.reviewer_id")
    _str(reviewer["authority"], "rights.reviewer.authority")
    try:
        reviewed_at = datetime.fromisoformat(_str(receipt["reviewed_at"], "rights.reviewed_at").replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError("Rights receipt reviewed_at ISO-8601 olmalı") from exc
    if reviewed_at.tzinfo is None:
        raise GateError("Rights receipt reviewed_at timezone içermeli")
    expected = {
        "dataset_manifest_sha256": manifest["provenance"]["dataset_manifest_sha256"],
        "dataset_metadata_sha256": manifest["provenance"]["dataset_metadata_sha256"],
        "candidate_id": manifest["candidate"]["id"],
        "candidate_version": manifest["candidate"]["version"],
        "source_commit": manifest["provenance"]["source_commit"],
        "training_recipe_id": manifest["provenance"]["training_recipe_id"],
        "training_engine": manifest["provenance"]["training_engine"],
        "model_files": _bound_file_list(receipt["model_files"], "rights.model_files"),
        "model_bundle_sha256": bundle_hash,
        "phase5_report_sha256": report_hash,
    }
    for key, value in expected.items():
        if receipt[key] != value:
            raise GateError(f"Rights receipt kapsamı uyuşmuyor: {key}")
    license_hashes = _string_list(receipt["license_evidence_sha256s"], "rights.license_evidence_sha256s", HEX64)
    if not license_hashes:
        raise GateError("Hak kanıtı hash listesi boş")
    return receipt


def _load_trust(path: Path) -> dict:
    raw, _ = _load_json(path.resolve(strict=True), MAX_SMALL_JSON_BYTES)
    trust = _versioned_object(raw, "pinned trust registry", 1)
    trust = _obj(trust, "pinned trust registry", {
        "schema_version", "purpose", "trusted_rights_receipts", "trusted_phase5_reports", "trusted_compatibility_evidence",
    })
    _str(trust["purpose"], "trust.purpose")
    for key in ("trusted_rights_receipts", "trusted_phase5_reports"):
        if type(trust[key]) is not list:
            raise GateError(f"trust.{key}: liste gerekli")
        for digest in trust[key]:
            _digest(digest, f"trust.{key}[]")
    if type(trust["trusted_compatibility_evidence"]) is not list:
        raise GateError("trust.trusted_compatibility_evidence: liste gerekli")
    evidence: list[dict] = []
    for index, raw_entry in enumerate(trust["trusted_compatibility_evidence"]):
        item = _obj(raw_entry, f"trust.compatibility[{index}]", {
            "evidence_id", "evidence_path", "evidence_sha256", "os", "python", "backend", "device",
            "model_bundle_sha256", "phase5_report_sha256", "validation_passed",
        })
        _str(item["evidence_id"], f"trust.compatibility[{index}].evidence_id")
        evidence_path = _safe_file(path.resolve(strict=True).parent.parent, item["evidence_path"], f"trust.compatibility[{index}].evidence_path")
        evidence_sha = _digest(item["evidence_sha256"], f"trust.compatibility[{index}].evidence_sha256")
        actual_evidence_sha, _ = _sha256_file(evidence_path)
        if actual_evidence_sha != evidence_sha:
            raise GateError(f"trust.compatibility[{index}] evidence SHA-256 uyuşmuyor")
        _str(item["os"], f"trust.compatibility[{index}].os")
        _str(item["device"], f"trust.compatibility[{index}].device")
        if item["os"] not in OS_VALUES or item["device"] not in DEVICE_VALUES:
            raise GateError(f"trust.compatibility[{index}]: OS/device değeri geçersiz")
        _str(item["python"], f"trust.compatibility[{index}].python", PYTHON_VERSION)
        _str(item["backend"], f"trust.compatibility[{index}].backend", BACKEND_ID)
        _digest(item["model_bundle_sha256"], f"trust.compatibility[{index}].model_bundle_sha256")
        _digest(item["phase5_report_sha256"], f"trust.compatibility[{index}].phase5_report_sha256")
        _bool_required(item["validation_passed"], f"trust.compatibility[{index}].validation_passed", True)
        evidence.append(item)
    trust["trusted_compatibility_evidence"] = evidence
    return trust


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 6 release gate scaffold (validation only; no packaging or release)")
    parser.add_argument("manifest", type=Path, help="candidate klasöründeki manifest.json")
    parser.add_argument("--phase5-report", type=Path, required=True, help="manifestte hash'i bulunan Phase 5 raporu")
    parser.add_argument("--json", action="store_true", help="makinece okunur sonuç yaz")
    args = parser.parse_args(argv)
    blockers: list[str]
    try:
        trust_path = Path(__file__).resolve().parent / "schemas" / "trusted-rights-review-receipts-v1.json"
        blockers = validate(args.manifest, args.phase5_report, trust_path)
    except (GateError, OSError, ValueError) as exc:
        blockers = [str(exc)]
    result = {
        "status": "BLOCKED" if blockers else "STRUCTURE_VALID_NOT_RELEASED",
        "release_permitted": False,
        "packaging_performed": False,
        "publishing_performed": False,
        "blockers": blockers,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("Phase 6 release gate: " + result["status"])
        for item in blockers:
            print("- " + item)
        print("Bu CLI model yüklemez, paketlemez, etkinleştirmez veya yayımlamaz.")
    return 2 if blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
