# -*- coding: utf-8 -*-
"""Fail-closed adapter for evaluating fixed, local full-episode OCR SRTs.

This module does not open videos or run OCR. It compares supplied SRT files to
hash-linked, human-reviewed timed references and always emits a measurement-only
report that cannot satisfy the Phase 6 release validator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import ocr_crop_eval as crop_eval
import ocr_experiment as prep

MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_SRT_BYTES = 64 * 1024 * 1024
MAX_CUES = 100_000
MAX_TEXT_CHARS = 4096
MAX_INTERVAL_CANDIDATES = 2_000_000
MAX_INTERVAL_PAIR_OPERATIONS = 2_000_000
MAX_EXECUTION_RECEIPT_BYTES = 32 * 1024 * 1024
FIXED_PRODUCT_ARGS = ["--ocr", "paddle", "--qa", "0"]
MIN_OVERLAP_REFERENCE_RATIO = 0.5  # frozen; overlap / reference cue duration
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENGINE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TIMESTAMP_RE = re.compile(r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3})$")


class VideoEvalError(ValueError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return _sha256(raw)


def _json(path: Path):
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise VideoEvalError(f"{path.name} boyut sınırını aşıyor")
        raw = path.read_bytes()

        def unique(pairs):
            doc = {}
            for key, value in pairs:
                if key in doc:
                    raise VideoEvalError(f"Yinelenen JSON alanı: {key}")
                doc[key] = value
            return doc

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VideoEvalError(f"JSON okunamadı: {path.name}") from exc
    return value, raw


def _relative_file(root: Path, raw: object, label: str) -> Path:
    if type(raw) is not str or not raw or "\\" in raw or any(ord(char) < 32 for char in raw):
        raise VideoEvalError(f"{label}: göreli POSIX dosya yolu gerekli")
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts or re.match(r"^[A-Za-z]:", raw):
        raise VideoEvalError(f"{label}: mutlak veya kök dışına çıkan yol kabul edilmez")
    path = (root / rel).resolve(strict=True)
    try:
        path.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise VideoEvalError(f"{label}: manifest kök dizini dışına çıkıyor") from exc
    if not path.is_file():
        raise VideoEvalError(f"{label}: normal dosya gerekli")
    return path


def _parse_srt_bytes(raw: bytes, label: str) -> list[dict]:
    try:
        if len(raw) > MAX_SRT_BYTES:
            raise VideoEvalError(f"{label}: SRT boyut sınırını aşıyor")
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise VideoEvalError(f"{label}: SRT okunamadı (UTF-8 gerekli)") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block for block in re.split(r"\n[ \t]*\n+", text.strip()) if block.strip()]
    if not blocks or len(blocks) > MAX_CUES:
        raise VideoEvalError(f"{label}: SRT boş veya cue sayısı sınırı aşıyor")
    cues = []
    for index, block in enumerate(blocks):
        lines = [line.strip() for line in block.split("\n")]
        if lines and lines[0].isdigit():
            lines = lines[1:]
        if len(lines) < 2 or "-->" not in lines[0]:
            raise VideoEvalError(f"{label}: {index + 1}. SRT bloğu geçersiz")
        left, sep, right = lines[0].partition("-->")
        match_a, match_b = TIMESTAMP_RE.fullmatch(left.strip()), TIMESTAMP_RE.fullmatch(right.strip())
        if not sep or not match_a or not match_b:
            raise VideoEvalError(f"{label}: {index + 1}. cue zaman biçimi geçersiz")

        def to_ms(match):
            hours, minutes, seconds, millis = map(int, match.groups())
            if minutes >= 60 or seconds >= 60:
                raise VideoEvalError(f"{label}: {index + 1}. cue zaman değeri geçersiz")
            return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis

        start, end = to_ms(match_a), to_ms(match_b)
        cue_text = " ".join(line for line in lines[1:] if line).strip()
        if start >= end or not cue_text or len(cue_text) > MAX_TEXT_CHARS:
            raise VideoEvalError(f"{label}: {index + 1}. cue süresi/metni geçersiz")
        cues.append({"index": index, "start_ms": start, "end_ms": end, "text": cue_text})
    return cues


def _read_srt(path: Path, label: str) -> list[dict]:
    try:
        if path.stat().st_size > MAX_SRT_BYTES:
            raise VideoEvalError(f"{label}: SRT boyut sınırını aşıyor")
        raw = path.read_bytes()
    except OSError as exc:
        raise VideoEvalError(f"{label}: SRT okunamadı") from exc
    return _parse_srt_bytes(raw, label)


def match_cues(reference: list[dict], prediction: list[dict]) -> list[tuple[int, int]]:
    """Maximum-cardinality 1:1 matching, with deterministic overlap preference.

    Edges require overlap/reference-duration >= 0.5. Hopcroft-Karp maximizes
    the number of matched cues. Its deterministic traversal orders reference
    nodes by degree then file index, and each adjacency list by descending
    overlap then prediction file index. This is a tie-break preference, not a
    claim that total overlap is globally maximized among all max-cardinality
    matchings.
    """
    ordered_pred = sorted(prediction, key=lambda cue: (cue["start_ms"], cue["index"]))
    adjacency = {}
    candidate_count = 0
    pair_operation_count = 0

    def count_pair_operation():
        nonlocal pair_operation_count
        if pair_operation_count >= MAX_INTERVAL_PAIR_OPERATIONS:
            raise VideoEvalError("Zaman aralığı çift işlemi sınırı aşıldı")
        pair_operation_count += 1

    active = []
    pred_pos = 0
    for ref in sorted(reference, key=lambda cue: (cue["start_ms"], cue["index"])):
        while pred_pos < len(ordered_pred) and ordered_pred[pred_pos]["start_ms"] < ref["end_ms"]:
            active.append(ordered_pred[pred_pos])
            pred_pos += 1
        still_active = []
        for cue in active:
            count_pair_operation()
            if cue["end_ms"] > ref["start_ms"]:
                still_active.append(cue)
        active = still_active
        ref_duration = ref["end_ms"] - ref["start_ms"]
        for pred in active:
            count_pair_operation()
            overlap = min(ref["end_ms"], pred["end_ms"]) - max(ref["start_ms"], pred["start_ms"])
            if overlap > 0 and overlap / ref_duration >= MIN_OVERLAP_REFERENCE_RATIO:
                if candidate_count >= MAX_INTERVAL_CANDIDATES:
                    raise VideoEvalError("Zaman aralığı eşleşme adayı sınırı aşıldı")
                adjacency.setdefault(ref["index"], []).append((overlap, pred["index"]))
                candidate_count += 1
    if not adjacency:
        return []
    for edges in adjacency.values():
        edges.sort(key=lambda item: (-item[0], item[1]))
    left_nodes = sorted(adjacency, key=lambda ref_index: (len(adjacency[ref_index]), ref_index))
    pair_left, pair_right = {}, {}
    infinity = len(left_nodes) + len(prediction) + 1

    def bfs():
        distance = {left: 0 for left in left_nodes if left not in pair_left}
        queue = list(distance)
        shortest = infinity
        head = 0
        while head < len(queue):
            left = queue[head]
            head += 1
            if distance[left] + 1 > shortest:
                continue
            for _overlap, right in adjacency[left]:
                mate = pair_right.get(right)
                if mate is None:
                    shortest = min(shortest, distance[left] + 1)
                elif mate not in distance:
                    distance[mate] = distance[left] + 1
                    queue.append(mate)
        return distance, shortest

    def augment(start, distance, shortest):
        stack = [start]
        path_rights = []
        next_edge = [0]
        while stack:
            left = stack[-1]
            edges = adjacency[left]
            if next_edge[-1] >= len(edges):
                distance[left] = infinity
                stack.pop()
                next_edge.pop()
                if path_rights:
                    path_rights.pop()
                continue
            _overlap, right = edges[next_edge[-1]]
            next_edge[-1] += 1
            mate = pair_right.get(right)
            if mate is None:
                if distance[left] + 1 != shortest:
                    continue
                rights = path_rights + [right]
                for matched_left, matched_right in zip(stack, rights):
                    pair_left[matched_left] = matched_right
                    pair_right[matched_right] = matched_left
                return True
            if distance.get(mate, infinity) == distance[left] + 1:
                path_rights.append(right)
                stack.append(mate)
                next_edge.append(0)
        return False

    while True:
        distance, shortest = bfs()
        if shortest == infinity:
            break
        for left in left_nodes:
            if left not in pair_left and distance.get(left) == 0:
                augment(left, distance, shortest)
    return sorted(pair_left.items())


def score_cues(reference: list[dict], prediction: list[dict], timing_tolerance_ms: int) -> dict:
    matches = match_cues(reference, prediction)
    pred_by_index = {cue["index"]: cue for cue in prediction}
    ref_by_index = {cue["index"]: cue for cue in reference}
    match_by_ref = dict(matches)
    pairs = [(cue["text"], pred_by_index[match_by_ref[cue["index"]]]["text"]
             if cue["index"] in match_by_ref else "") for cue in reference]
    text_metrics = crop_eval.score_pairs(pairs)
    start_errors, end_errors = [], []
    for ref_index, pred_index in matches:
        ref, pred = ref_by_index[ref_index], pred_by_index[pred_index]
        start_errors.append(abs(ref["start_ms"] - pred["start_ms"]))
        end_errors.append(abs(ref["end_ms"] - pred["end_ms"]))
    n_ref, n_pred, n_match = len(reference), len(prediction), len(matches)
    return {
        "reference_cues": n_ref, "prediction_cues": n_pred,
        "matched_cues": n_match, "missed_reference_cues": n_ref - n_match,
        "unmatched_prediction_cues": n_pred - n_match,
        "cue_precision": n_match / n_pred if n_pred else 0.0,
        "cue_recall": n_match / n_ref if n_ref else None,
        "coverage": n_match / n_ref if n_ref else None,
        "cer": text_metrics["cer"], "wer": text_metrics["wer"],
        "exact_match_cue_rate": text_metrics["exact_match_rate"],
        "reference_chars": text_metrics["reference_chars"],
        "reference_words": text_metrics["reference_words"],
        "character_edits": text_metrics["character_edits"],
        "word_edits": text_metrics["word_edits"],
        "timing_start_abs_error_ms": sum(start_errors) / len(start_errors) if start_errors else None,
        "timing_end_abs_error_ms": sum(end_errors) / len(end_errors) if end_errors else None,
        "timing_within_tolerance_rate": (
            sum(a <= timing_tolerance_ms and b <= timing_tolerance_ms
                for a, b in zip(start_errors, end_errors)) / len(matches) if matches else None),
        "timing_tolerance_ms": timing_tolerance_ms,
    }


def _validated_split(experiment_path: Path, split_path: Path, dataset: Path,
                     metadata_path: Path):
    split_path = split_path.resolve(strict=True)
    experiment_path = experiment_path.resolve(strict=True)
    if split_path != experiment_path.parent / "split-manifest.json":
        raise VideoEvalError("Split manifest must be the experiment report's sibling artifact")
    split, _ = _json(split_path)
    if type(split) is not dict:
        raise VideoEvalError("Split manifest kökü JSON nesnesi olmalı")
    split_rows = split.get("rows")
    if type(split_rows) is not list:
        raise VideoEvalError("Split manifest rows alanı liste olmalı")
    if any(type(record) is not dict for record in split_rows):
        raise VideoEvalError("Split satırı JSON nesnesi olmalı")
    # This rechecks the Phase 2 active event chain, rights evidence/review,
    # hashes, split assignment, and test presence using the existing contract.
    _, _, provenance, manifest_hash, metadata_hash, split_hash = crop_eval.validate_run(
        dataset, metadata_path, split_path)
    experiment, _ = _json(experiment_path)
    if (type(experiment) is not dict or type(experiment.get("schema_version")) is not int
            or experiment.get("schema_version") != 1
            or experiment.get("status") != "PREPARED_NOT_RUN"):
        raise VideoEvalError("Yalnız Phase 5 PREPARED_NOT_RUN manifesti referans alınabilir")
    ds = experiment.get("dataset")
    exp_provenance = experiment.get("provenance")
    exp = experiment.get("experiment")
    stop = experiment.get("stop_gate")
    if (type(ds) is not dict or type(exp) is not dict or type(stop) is not dict
            or ds.get("manifest_sha256") != manifest_hash
            or ds.get("metadata_sha256") != metadata_hash
            or type(exp_provenance) is not dict
            or exp_provenance.get("provenance_sha256") != provenance.get("provenance_sha256")
            or exp.get("baseline_id") in (None, "")
            or exp.get("candidate_id") in (None, "")
            or exp["baseline_id"] == exp["candidate_id"]
            or type(exp.get("predeclared_gate")) is not dict):
        raise VideoEvalError("Experiment report Phase 5 dataset/engine/gate alanları doğrulanamadı")
    gate = exp["predeclared_gate"]
    try:
        plan, plan_hash = prep.validate_evaluation_plan(
            experiment_path.parent / "evaluation-plan-v1.json",
            split, manifest_hash, metadata_hash)
    except prep.InputError as exc:
        raise VideoEvalError(str(exc)) from exc
    if (experiment.get("experiment_id") != plan["experiment_id"]
            or experiment.get("evaluation_plan_sha256") != plan_hash
            or experiment.get("split_manifest_sha256") != split_hash
            or exp.get("baseline_id") != plan["baseline_id"]
            or exp.get("candidate_id") != plan["candidate_id"]
            or gate != plan["predeclared_gate"]):
        raise VideoEvalError("Experiment report evaluation plan/hash bağları uyuşmuyor")
    if (type(gate.get("max_candidate_corpus_cer_increase")) not in (int, float)
            or not math.isfinite(gate["max_candidate_corpus_cer_increase"])
            or gate["max_candidate_corpus_cer_increase"] < 0
            or type(gate.get("timing_tolerance_ms")) is not int
            or gate["timing_tolerance_ms"] < 0
            or type(gate.get("min_test_group_coverage")) not in (int, float)
            or not math.isfinite(gate["min_test_group_coverage"])
            or not 0 < gate["min_test_group_coverage"] <= 1
            or gate.get("coverage_definition") != prep.COVERAGE_DEFINITION
            or gate.get("require_no_test_group_regression") is not True
            or stop.get("human_rights_review_complete") is not True
            or stop.get("human_provenance_verified") is not True
            or stop.get("must_have_train_validation_test") is not True
            or stop.get("trusted_reference_required") is not True
            or stop.get("engine_and_config_required") is not True
            or stop.get("no_regression_gate_predeclared") is not True
            or stop.get("experiment_run_blocked") is not True
            or stop.get("metrics_claimable") is not False
            or stop.get("rights_verified") is not False):
        raise VideoEvalError("Önceden sabitlenmiş Phase 5 gate/provenance/rights koşulu eksik")
    if split.get("dataset_manifest_sha256") != manifest_hash or split_hash != _sha256(split_path.read_bytes()):
        raise VideoEvalError("Split hash'i doğrulanamadı")
    groups = {}
    for record in split_rows:
        if (type(record.get("source_id")) is not str or not record["source_id"].strip()
                or type(record.get("episode_id")) is not str or not record["episode_id"].strip()):
            raise VideoEvalError("Split source_id ve episode_id metin olmalı")
        key = record.get("group_id")
        if type(key) is not str or not key:
            raise VideoEvalError("Split satırında kanonik group_id eksik")
        if record.get("split") not in {"train", "validation", "test"}:
            raise VideoEvalError("Split satırında geçersiz bölüm")
        old = groups.get(key)
        if old and old != record["split"]:
            raise VideoEvalError("Aynı source/episode birden fazla split'e ayrılmış")
        groups[key] = record["split"]
    if set(groups.values()) != {"train", "validation", "test"}:
        raise VideoEvalError("Train/validation/test gruplarının tümü gerekli")
    return groups, experiment, manifest_hash, metadata_hash, split_hash


def _load_references(path: Path, groups: dict):
    path = path.resolve(strict=True)
    doc, raw = _json(path)
    if (type(doc) is not dict or set(doc) != {"schema_version", "records"}
            or type(doc["schema_version"]) is not int or doc["schema_version"] != 1):
        raise VideoEvalError("timed-reference-manifest-v1 şeması gerekli")
    if type(doc["records"]) is not list or len(doc["records"]) != len(groups):
        raise VideoEvalError("Her source/episode split grubu için tek referans kaydı gerekli")
    found, languages = {}, set()
    for record in doc["records"]:
        required = {"source_id", "episode_id", "split", "language", "media_sha256",
                    "reference_srt_path", "reference_srt_sha256", "alignment_review"}
        if type(record) is not dict or set(record) != required:
            raise VideoEvalError("Timed reference kaydı alanları tam eşleşmeli")
        if (type(record["source_id"]) is not str or not record["source_id"].strip()
                or type(record["episode_id"]) is not str or not record["episode_id"].strip()
                or type(record["split"]) is not str):
            raise VideoEvalError("Reference source/episode/split alanları geçersiz")
        key = prep._group_key(record)
        if key not in groups or key in found or record["split"] != groups[key]:
            raise VideoEvalError("Reference kaydı bilinmiyor, yinelenmiş veya split ile farklı")
        if (type(record["language"]) is not str or not record["language"].strip()
                or not SHA256_RE.fullmatch(str(record["media_sha256"]))
                or not SHA256_RE.fullmatch(str(record["reference_srt_sha256"]))):
            raise VideoEvalError("Reference language/media/SRT SHA-256 alanı geçersiz")
        language = unicodedata.normalize("NFC", record["language"]).strip().casefold()
        languages.add(language)
        review = record["alignment_review"]
        if (type(review) is not dict or set(review) != {"status", "reviewer", "reviewed_at", "media_sha256"}
                or review.get("status") != "reviewed"
                or type(review.get("reviewer")) is not str or not review["reviewer"].strip()
                or review.get("media_sha256") != record["media_sha256"]):
            raise VideoEvalError("Her reference için medya hash'ine bağlı insan hizalama incelemesi gerekli")
        try:
            reviewed_at = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
            if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
                raise ValueError()
        except (TypeError, ValueError, AttributeError) as exc:
            raise VideoEvalError("Alignment review timestamp timezone içeren ISO-8601 olmalı") from exc
        ref_file = _relative_file(path.parent, record["reference_srt_path"], "Reference SRT")
        if ref_file.stat().st_size > MAX_SRT_BYTES:
            raise VideoEvalError("Reference SRT boyut sınırını aşıyor")
        ref_bytes = ref_file.read_bytes()
        if _sha256(ref_bytes) != record["reference_srt_sha256"]:
            raise VideoEvalError("Reference SRT hash'i eşleşmiyor")
        found[key] = {"split": record["split"], "media_sha256": record["media_sha256"],
                      "cues": _parse_srt_bytes(ref_bytes, f"{key} reference")}
    if set(found) != set(groups):
        raise VideoEvalError("Timed reference grupları frozen split'i tam karşılamıyor")
    if len(languages) != 1:
        raise VideoEvalError("Bir raporda tek source-language olmalı; dilleri karıştırmayın")
    return found, _sha256(raw), next(iter(languages))


def _receipt_object(value: object, keys: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise VideoEvalError(f"Execution receipt {label} schema fields are invalid")
    return value


def _receipt_hash(value: object, label: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        raise VideoEvalError(f"Execution receipt {label} must be a lowercase SHA-256")
    return value


def _validate_execution_receipt(manifest_root: Path, receipt: object,
                                receipt_raw: bytes, binding: dict,
                                records: list[dict], groups: dict,
                                references: dict,
                                expected_inputs: dict | None) -> dict:
    required = {"schema_version", "status", "run_id", "created_at", "duration_seconds",
                "application", "runtime", "execution", "inputs", "profiles", "predictions", "limits"}
    receipt = _receipt_object(receipt, required, "root")
    if (type(receipt["schema_version"]) is not int or receipt["schema_version"] != 2
            or receipt["status"] != "COMPLETED_LOCAL_EXECUTION_NOT_PHASE6_PASS"
            or type(receipt["run_id"]) is not str or not RUN_ID_RE.fullmatch(receipt["run_id"])
            or type(receipt["duration_seconds"]) not in (int, float)
            or (type(receipt["duration_seconds"]) is float
                and not math.isfinite(receipt["duration_seconds"]))
            or receipt["duration_seconds"] < 0
            or type(receipt["limits"]) is not list or not receipt["limits"]
            or any(type(item) is not str or not item for item in receipt["limits"])):
        raise VideoEvalError("Execution receipt version/status/run/limit fields are invalid")
    if receipt["run_id"] != binding["run_id"]:
        raise VideoEvalError("Execution receipt run ID differs from prediction manifest (possible replay)")
    try:
        timestamp = datetime.fromisoformat(receipt["created_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError()
    except (TypeError, ValueError, AttributeError) as exc:
        raise VideoEvalError("Execution receipt created_at must have a timezone") from exc

    application = _receipt_object(receipt["application"],
                                   {"commit", "source_bundle_sha256", "source_sha256s"}, "application")
    if (application["commit"] is not None and
            (type(application["commit"]) is not str or
             not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", application["commit"]))):
        raise VideoEvalError("Execution receipt application commit is invalid")
    _receipt_hash(application["source_bundle_sha256"], "source bundle hash")
    if (type(application["source_sha256s"]) is not dict or not application["source_sha256s"]
            or any(type(key) is not str or not key for key in application["source_sha256s"])):
        raise VideoEvalError("Execution receipt source hash inventory is invalid")
    for value in application["source_sha256s"].values():
        _receipt_hash(value, "source hash")
    source_bundle = _sha256(json.dumps(
        application["source_sha256s"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))
    if application["source_bundle_sha256"] != source_bundle:
        raise VideoEvalError("Execution receipt source bundle hash does not match its source inventory")

    runtime = _receipt_object(receipt["runtime"],
                              {"python", "distributions", "media_tools", "environment"}, "runtime")
    python = _receipt_object(runtime["python"],
                             {"version", "executable_sha256", "startup_mode", "package_roots"}, "runtime.python")
    if (type(python["version"]) is not str or not python["version"]
            or type(python["startup_mode"]) is not str or not python["startup_mode"]
            or type(python["package_roots"]) is not list
            or any(type(item) is not str for item in python["package_roots"])):
        raise VideoEvalError("Execution receipt Python runtime fields are invalid")
    _receipt_hash(python["executable_sha256"], "Python executable hash")
    distributions = runtime["distributions"]
    if type(distributions) is not list:
        raise VideoEvalError("Execution receipt distribution inventory must be an array")
    for row in distributions:
        row = _receipt_object(row, {"name", "normalized_name", "version", "location",
                                    "metadata_sha256", "record_sha256"}, "distribution")
        if any(type(row[key]) is not str or not row[key] for key in ("name", "normalized_name", "version", "location")):
            raise VideoEvalError("Execution receipt distribution metadata is invalid")
        for key in ("metadata_sha256", "record_sha256"):
            if row[key] is not None:
                _receipt_hash(row[key], f"distribution {key}")
    media_tools = runtime["media_tools"]
    if type(media_tools) is not dict:
        raise VideoEvalError("Execution receipt media_tools must be an object")
    for value in media_tools.values():
        if value is not None:
            value = _receipt_object(value, {"path", "sha256"}, "media tool")
            if type(value["path"]) is not str or not value["path"]:
                raise VideoEvalError("Execution receipt media tool path is invalid")
            _receipt_hash(value["sha256"], "media tool hash")
    environment = _receipt_object(runtime["environment"],
                                  {"sha256s", "removed_proxy_variables", "fixed_offline_flags",
                                   "python_override_variables_removed"}, "runtime.environment")
    if (type(environment["sha256s"]) is not dict
            or type(environment["removed_proxy_variables"]) is not list
            or any(type(item) is not str for item in environment["removed_proxy_variables"])
            or type(environment["fixed_offline_flags"]) is not dict
            or any(type(key) is not str or type(value) is not str
                   for key, value in environment["fixed_offline_flags"].items())
            or type(environment["python_override_variables_removed"]) is not list
            or any(type(item) is not str for item in environment["python_override_variables_removed"])):
        raise VideoEvalError("Execution receipt runtime environment is invalid")
    for value in environment["sha256s"].values():
        _receipt_hash(value, "environment value hash")

    execution = _receipt_object(receipt["execution"],
                                {"mode", "network_policy", "phase6_eligible", "rights_verified",
                                 "fixed_args"}, "execution")
    if (execution["mode"] != "product_cli_allowlisted_offline_profile"
            or execution["network_policy"] != (
                "python_socket_apis_blocked; managed OCR loaders pinned/local/download-disabled; "
                "OS network isolation not certified")
            or execution["phase6_eligible"] is not False
            or execution["rights_verified"] is not False
            or execution["fixed_args"] != FIXED_PRODUCT_ARGS):
        raise VideoEvalError("Execution receipt mode or non-release flags are invalid")

    inputs = _receipt_object(receipt["inputs"],
        {"dataset_manifest_sha256", "dataset_metadata_sha256", "split_manifest_sha256",
         "evaluation_plan_sha256", "experiment_report_sha256", "reference_manifest_sha256",
         "media_sha256s", "profile_sha256s", "execution_plan_sha256",
         "rights_receipt_sha256", "trust_registry_sha256"}, "inputs")
    for key in ("dataset_manifest_sha256", "dataset_metadata_sha256", "split_manifest_sha256",
                "evaluation_plan_sha256", "experiment_report_sha256", "reference_manifest_sha256",
                "execution_plan_sha256", "rights_receipt_sha256", "trust_registry_sha256"):
        _receipt_hash(inputs[key], f"inputs.{key}")
    if expected_inputs is None or any(inputs.get(key) != value for key, value in expected_inputs.items()):
        raise VideoEvalError("Execution receipt dataset/split/reference bindings differ from evaluated inputs")
    if type(inputs["media_sha256s"]) is not dict or type(inputs["profile_sha256s"]) is not dict:
        raise VideoEvalError("Execution receipt media/profile hash maps are invalid")
    for value in list(inputs["media_sha256s"].values()) + list(inputs["profile_sha256s"].values()):
        _receipt_hash(value, "media/profile input hash")

    profiles = receipt["profiles"]
    if type(profiles) is not dict or len(profiles) != 2:
        raise VideoEvalError("Execution receipt must bind exactly the two frozen profiles")
    expected_engines = {row["engine_id"] for row in records}
    if set(profiles) != expected_engines:
        raise VideoEvalError("Execution receipt profile engine IDs differ from predictions")
    profile_hashes = {}
    for engine, row in profiles.items():
        row = _receipt_object(row, {"profile_id", "profile_sha256", "model_sha256", "device_preference"},
                              "profile")
        if (type(row["profile_id"]) is not str or not row["profile_id"]
                or type(row["device_preference"]) is not str
                or row["device_preference"] not in {"gpu_default", "cpu"}):
            raise VideoEvalError("Execution receipt profile metadata is invalid")
        _receipt_hash(row["profile_sha256"], "profile hash")
        _receipt_hash(row["model_sha256"], "model hash")
        profile_hashes[engine] = row["profile_sha256"]
    if inputs["profile_sha256s"] != dict(sorted(profile_hashes.items())):
        raise VideoEvalError("Execution receipt profile hashes differ from its profile inventory")

    prediction_data = _receipt_object(receipt["predictions"],
        {"manifest", "manifest_records_sha256", "records"}, "predictions")
    if (prediction_data["manifest"] != "prediction-manifest-v1.json"
            or type(prediction_data["records"]) is not list
            or prediction_data["records"] != records
            or _canonical_sha256(prediction_data["records"]) !=
               _receipt_hash(prediction_data["manifest_records_sha256"], "manifest records hash")):
        raise VideoEvalError("Execution receipt prediction records do not match the bound manifest")
    if len(records) != 2 * len(groups):
        raise VideoEvalError("Execution receipt prediction record count differs from frozen groups")

    seen = set()
    expected_media = {}
    for group_key, reference in references.items():
        group_digest = _sha256(group_key.encode("utf-8"))
        expected_media[group_digest] = reference["media_sha256"]
    if inputs["media_sha256s"] != dict(sorted(expected_media.items())):
        raise VideoEvalError("Execution receipt media hashes differ from timed references")

    for row in records:
        row = _receipt_object(row, {"engine_id", "source_id", "episode_id", "split", "media_sha256",
                                    "prediction_srt_path", "prediction_srt_sha256", "profile_sha256",
                                    "model_sha256", "config_sha256", "configured_device",
                                    "session_configured_backend", "stats_sha256", "stats_path", "cue_count"},
                              "prediction record")
        group_key = prep._group_key(row)
        key = (row["engine_id"], group_key)
        if key in seen or row["engine_id"] not in expected_engines or group_key not in groups:
            raise VideoEvalError("Execution receipt prediction record is duplicate or outside frozen groups")
        seen.add(key)
        ref = references[group_key]
        profile = profiles[row["engine_id"]]
        expected_stats_path = Path(row["prediction_srt_path"]).with_suffix(".stats.json").as_posix()
        if (row["split"] != groups[group_key] or row["media_sha256"] != ref["media_sha256"]
                or row["profile_sha256"] != profile["profile_sha256"]
                or row["model_sha256"] != profile["model_sha256"]
                or row["stats_path"] != expected_stats_path
                or row["configured_device"] not in {"gpu", "cpu"}
                or (profile["device_preference"] == "cpu" and row["configured_device"] != "cpu")
                or type(row["config_sha256"]) is not str or not SHA256_RE.fullmatch(row["config_sha256"])
                or type(row["session_configured_backend"]) is not str
                or not row["session_configured_backend"].strip()
                or type(row["cue_count"]) is not int or row["cue_count"] < 0):
            raise VideoEvalError("Execution receipt media/profile/model/config/device fields do not match")
        expected_config_sha256 = _sha256(json.dumps({
            "profile_sha256": row["profile_sha256"],
            "source_hashes": application["source_sha256s"],
            "source_bundle_sha256": application["source_bundle_sha256"],
            "runtime": runtime,
            "fixed_args": execution["fixed_args"],
        }, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if row["config_sha256"] != expected_config_sha256:
            raise VideoEvalError("Execution receipt config hash does not match its profile/runtime/source identity")
        for field in ("media_sha256", "prediction_srt_sha256", "profile_sha256",
                      "model_sha256", "config_sha256", "stats_sha256"):
            _receipt_hash(row[field], f"prediction {field}")
        try:
            srt_path = _relative_file(manifest_root, row["prediction_srt_path"], "Receipt prediction SRT")
            stats_path = _relative_file(manifest_root, row["stats_path"], "Receipt stats")
        except OSError as exc:
            raise VideoEvalError("Execution receipt SRT/stats file is missing") from exc
        if (_sha256(srt_path.read_bytes()) != row["prediction_srt_sha256"]
                or _sha256(stats_path.read_bytes()) != row["stats_sha256"]):
            raise VideoEvalError("Execution receipt SRT/stats hashes do not match local outputs")
    expected_keys = {(engine, group) for engine in expected_engines for group in groups}
    if seen != expected_keys:
        raise VideoEvalError("Execution receipt lacks a frozen engine/group prediction")
    return {"receipt_sha256": _sha256(receipt_raw), "run_id": receipt["run_id"]}


def _stats_backend(offline: dict) -> str:
    secondary = offline.get("secondary_engine")
    probe = offline.get("secondary_probe_session_configured_providers", [])
    ocr = offline.get("secondary_ocr_session_configured_providers", [])
    if (type(secondary) is not str or not secondary
            or type(probe) is not list or any(type(x) is not str for x in probe)
            or type(ocr) is not list or any(type(x) is not str for x in ocr)):
        raise VideoEvalError("Execution stats provider configuration is invalid")
    return (f"easyocr;secondary={secondary};"
            f"onnx_probe_session_configured={','.join(probe) or 'not-observed'};"
            f"onnx_ocr_session_configured={','.join(ocr) or 'not-invoked'}")


def _load_predictions(path: Path, groups: dict, references: dict, experiment: dict,
                      expected_execution_inputs: dict | None = None):
    path = path.resolve(strict=True)
    doc, raw = _json(path)
    legacy_root = {"schema_version", "records"}
    runner_root = {"schema_version", "records", "execution_receipt"}
    if (type(doc) is not dict or set(doc) not in (legacy_root, runner_root)
            or type(doc["schema_version"]) is not int or doc["schema_version"] != 1):
        raise VideoEvalError("prediction-manifest-v1 şeması gerekli")
    runner_bound = set(doc) == runner_root
    records = doc["records"]
    baseline = experiment["experiment"]["baseline_id"]
    candidate = experiment["experiment"]["candidate_id"]
    if (type(baseline) is not str or not baseline.strip()
            or type(candidate) is not str or not candidate.strip()):
        raise VideoEvalError("Baseline/aday engine kimlikleri metin olmalı")
    if not ENGINE_ID_RE.fullmatch(baseline) or not ENGINE_ID_RE.fullmatch(candidate):
        raise VideoEvalError("Engine kimliği güvenli kısa tanımlayıcı biçiminde olmalı")
    engines = {baseline, candidate}
    if type(records) is not list or len(records) != len(groups) * 2:
        raise VideoEvalError("Baseline ve aday için her source/episode grubunda bir SRT gerekli")
    found, profiles, parsed_srt = {}, {}, {}
    required_common = {"engine_id", "source_id", "episode_id", "split", "media_sha256",
                       "prediction_srt_path", "prediction_srt_sha256", "model_sha256",
                       "config_sha256"}
    legacy_fields = required_common | {"device", "backend"}
    configured_fields = required_common | {"configured_device", "session_configured_backend"}
    runner_fields = configured_fields | {"profile_sha256", "stats_path", "stats_sha256", "cue_count"}
    for record in records:
        valid_fields = (runner_fields,) if runner_bound else (legacy_fields, configured_fields)
        if type(record) is not dict or set(record) not in valid_fields:
            raise VideoEvalError("Prediction kaydı alanları tam eşleşmeli")
        if (type(record["engine_id"]) is not str or type(record["source_id"]) is not str
                or type(record["episode_id"]) is not str or type(record["split"]) is not str):
            raise VideoEvalError("Prediction engine/source/episode/split alanları geçersiz")
        engine, key = record["engine_id"], prep._group_key(record)
        configured_device = record.get("configured_device", record.get("device"))
        configured_backend = record.get("session_configured_backend", record.get("backend"))
        if engine not in engines or key not in groups or (engine, key) in found:
            raise VideoEvalError("Prediction engine/grup bilinmiyor veya yinelenmiş")
        ref = references[key]
        if (record["split"] != groups[key] or record["media_sha256"] != ref["media_sha256"]
                or not SHA256_RE.fullmatch(str(record["model_sha256"]))
                or not SHA256_RE.fullmatch(str(record["config_sha256"]))
                or type(configured_device) is not str or configured_device not in {"gpu", "cpu"}
                or type(configured_backend) is not str or not configured_backend.strip()
                or not SHA256_RE.fullmatch(str(record["prediction_srt_sha256"]))):
            raise VideoEvalError("Prediction split/media/model config/configured-device provenance uyuşmuyor")
        profile = (record["model_sha256"], record["config_sha256"],
                   configured_device, configured_backend)
        if engine in profiles and profiles[engine] != profile:
            raise VideoEvalError("Aynı engine için config/device/backend gruplar arasında değişiyor")
        profiles[engine] = profile
        pred_file = _relative_file(path.parent, record["prediction_srt_path"], "Prediction SRT")
        if pred_file.stat().st_size > MAX_SRT_BYTES:
            raise VideoEvalError("Prediction SRT boyut sınırını aşıyor")
        pred_bytes = pred_file.read_bytes()
        if _sha256(pred_bytes) != record["prediction_srt_sha256"]:
            raise VideoEvalError("Prediction SRT hash'i eşleşmiyor")
        parsed = _parse_srt_bytes(pred_bytes, f"{engine} {key} prediction")
        found[(engine, key)] = parsed
        parsed_srt[(engine, key)] = parsed
    expected = {(engine, key) for engine in engines for key in groups}
    if set(found) != expected:
        raise VideoEvalError("Prediction grupları baseline/aday için frozen split'i tam karşılamıyor")

    execution_info = {"kind": "user_supplied_not_independently_verified",
                      "receipt_sha256": None, "run_id": None}
    if runner_bound:
        binding = doc["execution_receipt"]
        if (type(binding) is not dict or set(binding) != {"path", "sha256", "run_id"}
                or type(binding["run_id"]) is not str or not RUN_ID_RE.fullmatch(binding["run_id"])):
            raise VideoEvalError("Prediction execution_receipt binding schema is invalid")
        _receipt_hash(binding["sha256"], "binding hash")
        try:
            receipt_path = _relative_file(path.parent, binding["path"], "Execution receipt")
        except OSError as exc:
            raise VideoEvalError("Bound execution receipt is missing") from exc
        try:
            prep._reject_reparse_components(path.parent / binding["path"])
        except prep.InputError as exc:
            raise VideoEvalError("Execution receipt path contains a symlink/junction") from exc
        if receipt_path == path or receipt_path.stat().st_size > MAX_EXECUTION_RECEIPT_BYTES:
            raise VideoEvalError("Execution receipt path/size is invalid")
        receipt, receipt_raw = _json(receipt_path)
        if _sha256(receipt_raw) != binding["sha256"]:
            raise VideoEvalError("Execution receipt SHA-256 does not match prediction manifest")
        if (type(receipt) is not dict or type(receipt.get("predictions")) is not dict
                or receipt["predictions"].get("records") != records):
            raise VideoEvalError("Execution receipt record set does not match prediction manifest")
        expected_inputs = expected_execution_inputs
        execution_info.update(_validate_execution_receipt(
            path.parent, receipt, receipt_raw, binding, records, groups, references,
            expected_inputs))
        for row in records:
            engine, key = row["engine_id"], prep._group_key(row)
            receipt_profile = receipt["profiles"][engine]
            if row["profile_sha256"] != receipt_profile["profile_sha256"]:
                raise VideoEvalError("Prediction profile hash differs from execution receipt")
            stats_path = _relative_file(path.parent, row["stats_path"], "Execution stats")
            try:
                prep._reject_reparse_components(path.parent / row["stats_path"])
            except prep.InputError as exc:
                raise VideoEvalError("Execution stats path contains a symlink/junction") from exc
            stats, _stats_raw = _json(stats_path)
            offline = stats.get("offline_experiment") if type(stats) is dict else None
            if (type(offline) is not dict
                    or offline.get("profile_sha256") != row["profile_sha256"]
                    or offline.get("profile_id") != receipt_profile["profile_id"]
                    or offline.get("phase6_eligible") is not False
                    or offline.get("rights_verified") is not False
                    or stats.get("main_srt_sha256") != row["prediction_srt_sha256"]
                    or _stats_backend(offline) != row["session_configured_backend"]):
                raise VideoEvalError("Execution stats/profile/SRT/backend does not match bound prediction")
            configured = offline.get("primary_reader_configured_device")
            configured = configured.strip().lower() if type(configured) is str else ""
            configured_device = ("gpu" if configured.startswith(("cuda", "gpu"))
                                 else "cpu" if configured.startswith("cpu") else "")
            if configured_device != row["configured_device"]:
                raise VideoEvalError("Execution stats configured device differs from prediction")
            if len(parsed_srt[(engine, key)]) != row["cue_count"]:
                raise VideoEvalError("Execution stats cue count differs from parsed SRT")
        execution_info["kind"] = "runner_recorded_not_independently_verified"
    return found, profiles, _sha256(raw), execution_info


def _aggregate(engine_id: str, split: str, group_records: list[dict], tolerance: int):
    values = [score_cues(item["reference"], item["prediction"], tolerance)
              for item in group_records]
    sums = {key: sum(v[key] for v in values) for key in (
        "reference_cues", "prediction_cues", "matched_cues", "missed_reference_cues",
        "unmatched_prediction_cues", "reference_chars", "reference_words",
        "character_edits", "word_edits")}
    nref, npred, nmatch = sums["reference_cues"], sums["prediction_cues"], sums["matched_cues"]
    matched = [v for v in values if v["matched_cues"]]
    chars, words = sums["reference_chars"], sums["reference_words"]
    micro = {
        **sums,
        "cue_precision": nmatch / npred if npred else 0.0,
        "cue_recall": nmatch / nref if nref else None,
        "coverage": nmatch / nref if nref else None,
        "cer": sums["character_edits"] / chars if chars else None,
        "wer": sums["word_edits"] / words if words else None,
        "exact_match_cue_rate": sum(v["exact_match_cue_rate"] * v["reference_cues"] for v in values) / nref if nref else None,
        "timing_start_abs_error_ms": sum(v["timing_start_abs_error_ms"] * v["matched_cues"] for v in matched) / nmatch if nmatch else None,
        "timing_end_abs_error_ms": sum(v["timing_end_abs_error_ms"] * v["matched_cues"] for v in matched) / nmatch if nmatch else None,
        "timing_within_tolerance_rate": sum(v["timing_within_tolerance_rate"] * v["matched_cues"] for v in matched) / nmatch if nmatch else None,
        "timing_tolerance_ms": tolerance,
    }
    metrics = ["cue_precision", "cue_recall", "coverage", "cer", "wer",
               "exact_match_cue_rate", "timing_start_abs_error_ms",
               "timing_end_abs_error_ms", "timing_within_tolerance_rate"]
    macro = {key: (sum(v[key] for v in values if v[key] is not None) /
                   sum(v[key] is not None for v in values) if any(v[key] is not None for v in values) else None)
             for key in metrics}
    return {"engine_id": engine_id, "split": split, "micro": micro,
            "group_macro": macro,
            "per_source_episode": [
                {"group_key_sha256": _sha256(item["group_key"].encode("utf-8")),
                 "split": split, **value}
                for item, value in zip(group_records, values)]}


def _coverage_gate(test_results: list[dict], expected_group_hashes: set[str],
                   threshold: float) -> dict:
    """Require defined, unique coverage for every test group and engine."""
    if (type(threshold) not in (int, float) or not math.isfinite(threshold)
            or not 0 < threshold <= 1 or not expected_group_hashes):
        raise VideoEvalError("Coverage gate threshold/test group kümesi geçersiz")
    by_engine = {}
    group_results = []
    passed = True
    for engine_result in test_results:
        if type(engine_result) is not dict:
            raise VideoEvalError("Coverage gate engine sonucu nesne olmalı")
        engine = engine_result.get("engine_id")
        if type(engine) is not str or not engine or engine in by_engine:
            raise VideoEvalError("Coverage gate engine kapsamı eksik veya belirsiz")
        records = engine_result.get("per_source_episode")
        if type(records) is not list:
            raise VideoEvalError("Coverage gate per-group kayıtları eksik")
        found = {}
        for record in records:
            if type(record) is not dict:
                raise VideoEvalError("Coverage gate group kaydı nesne olmalı")
            key = record.get("group_key_sha256")
            if (type(key) is not str or not SHA256_RE.fullmatch(key)
                    or key in found or record.get("split") != "test"):
                raise VideoEvalError("Coverage gate test grubu eksik veya belirsiz")
            denominator = record.get("reference_cues")
            numerator = record.get("matched_cues")
            coverage = record.get("coverage")
            if (type(denominator) is not int or denominator <= 0
                    or type(numerator) is not int or not 0 <= numerator <= denominator
                    or type(coverage) not in (int, float) or not math.isfinite(coverage)
                    or not math.isclose(coverage, numerator / denominator,
                                        rel_tol=0.0, abs_tol=1e-12)):
                raise VideoEvalError("Coverage group pay/payda eksik veya tutarsız")
            found[key] = (numerator, denominator, coverage)
        if set(found) != expected_group_hashes:
            raise VideoEvalError("Coverage test grupları frozen split ile tam eşleşmiyor")
        by_engine[engine] = {key: values[2] for key, values in found.items()}
        for key in sorted(found):
            numerator, denominator, ratio = found[key]
            group_passed = ratio >= threshold
            passed = passed and group_passed
            group_results.append({"engine_id": engine, "group_key_sha256": key,
                                  "numerator_matched_cues": numerator,
                                  "denominator_reference_cues": denominator,
                                  "coverage": ratio, "passed": group_passed})
    if len(by_engine) != 2:
        raise VideoEvalError("Coverage gate baseline ve candidate sonuçları gerekli")
    return {"status": "PASS" if passed else "FAIL",
            "minimum_per_test_group": threshold,
            "definition": prep.COVERAGE_DEFINITION,
            "applies_to": "both baseline and candidate for every frozen test group",
            "per_engine_group": group_results}


def _comparison_gate(baseline_cer, candidate_cer, max_cer_increase: float,
                     test_group_comparison: list[dict],
                     require_no_test_group_regression: bool,
                     coverage_passed: bool) -> dict:
    """Combine all frozen comparison criteria without granting release status."""
    cer_available = (type(baseline_cer) in (int, float) and math.isfinite(baseline_cer)
                     and type(candidate_cer) in (int, float) and math.isfinite(candidate_cer))
    cer_delta = candidate_cer - baseline_cer if cer_available else None
    cer_passed = bool(cer_available and cer_delta <= max_cer_increase)
    group_deltas = [row.get("candidate_minus_baseline")
                    for row in test_group_comparison if type(row) is dict]
    group_data_complete = (len(group_deltas) == len(test_group_comparison) and bool(group_deltas)
                           and all(type(delta) in (int, float) and math.isfinite(delta)
                                   for delta in group_deltas))
    no_group_regression = (not require_no_test_group_regression or
                           (group_data_complete and all(delta <= 0 for delta in group_deltas)))
    all_passed = cer_passed and no_group_regression and coverage_passed
    return {"status": "PASS" if all_passed else "FAIL",
            "cer_limit_passed": cer_passed,
            "candidate_corpus_cer_increase": cer_delta,
            "maximum_candidate_corpus_cer_increase": max_cer_increase,
            "no_test_group_regression_required": require_no_test_group_regression,
            "no_test_group_regression_passed": no_group_regression,
            "test_group_cer_data_complete": group_data_complete,
            "coverage_passed": coverage_passed}


def run(dataset: Path, metadata: Path, experiment: Path, references_path: Path,
        predictions_path: Path, out_path: Path):
    dataset = dataset.resolve(strict=True)
    metadata = metadata.resolve(strict=True)
    split_path = experiment.resolve(strict=True).parent / "split-manifest.json"
    groups, experiment_doc, manifest_hash, metadata_hash, split_hash = _validated_split(
        experiment, split_path, dataset, metadata)
    references, ref_manifest_hash, source_language = _load_references(references_path, groups)
    expected_execution_inputs = {
        "dataset_manifest_sha256": manifest_hash,
        "dataset_metadata_sha256": metadata_hash,
        "split_manifest_sha256": split_hash,
        "evaluation_plan_sha256": experiment_doc["evaluation_plan_sha256"],
        "experiment_report_sha256": _sha256(experiment.resolve(strict=True).read_bytes()),
        "reference_manifest_sha256": ref_manifest_hash,
    }
    predictions, profiles, prediction_manifest_hash, execution_info = _load_predictions(
        predictions_path, groups, references, experiment_doc, expected_execution_inputs)
    out_path = out_path.resolve()
    if out_path.exists():
        raise VideoEvalError("Çıktı raporu zaten var; üzerine yazılmayacak")
    protected = (dataset, dataset.parent, metadata.parent, experiment.parent,
                 references_path.resolve().parent, predictions_path.resolve().parent)
    for root in protected:
        try:
            out_path.relative_to(root.resolve())
        except ValueError:
            continue
        raise VideoEvalError("Rapor veri/referans/prediction/deney klasörlerinin dışına yazılmalı")
    gate = experiment_doc["experiment"]["predeclared_gate"]
    tolerance = gate["timing_tolerance_ms"]
    engine_ids = [experiment_doc["experiment"]["baseline_id"],
                  experiment_doc["experiment"]["candidate_id"]]
    results = []
    for engine_id in engine_ids:
        for split in ("train", "validation", "test"):
            group_records = []
            for key, group_split in sorted(groups.items()):
                if group_split != split:
                    continue
                group_records.append({"group_key": key,
                                      "reference": references[key]["cues"],
                                      "prediction": predictions[(engine_id, key)]})
            results.append(_aggregate(engine_id, split, group_records, tolerance))
    baseline_test = next(r for r in results
                         if r["engine_id"] == engine_ids[0] and r["split"] == "test")
    candidate_test = next(r for r in results
                          if r["engine_id"] == engine_ids[1] and r["split"] == "test")
    expected_test_groups = {
        _sha256(key.encode("utf-8")) for key, split in groups.items() if split == "test"}
    coverage = _coverage_gate([baseline_test, candidate_test], expected_test_groups,
                              gate["min_test_group_coverage"])
    baseline_cer = baseline_test["micro"]["cer"]
    candidate_cer = candidate_test["micro"]["cer"]
    baseline_groups = {r["group_key_sha256"]: r
                       for r in baseline_test["per_source_episode"]}
    candidate_groups = {r["group_key_sha256"]: r
                        for r in candidate_test["per_source_episode"]}
    test_group_comparison = []
    for key in sorted(baseline_groups):
        before = baseline_groups[key]["cer"]
        after = candidate_groups[key]["cer"]
        test_group_comparison.append({"group_key_sha256": key,
                                      "baseline_cer": before, "candidate_cer": after,
                                      "candidate_minus_baseline": (after - before
                                                                   if before is not None and after is not None else None)})
    comparison = _comparison_gate(
        baseline_cer, candidate_cer, gate["max_candidate_corpus_cer_increase"],
        test_group_comparison, gate["require_no_test_group_regression"],
        coverage["status"] == "PASS")
    report = {
        "schema_version": 1,
        "status": "MEASURED_ADAPTER_ONLY_NOT_RELEASE_PASS",
        "phase6_eligible": False,
        "created_at": datetime.now().astimezone().isoformat(),
        "dataset": {"manifest_sha256": manifest_hash, "metadata_sha256": metadata_hash,
                    "split_manifest_sha256": split_hash, "groups": len(groups),
                    "rights_verified": False,
                    "rights_review_status": "human_attested_not_legally_verified"},
        "references": {"manifest_sha256": ref_manifest_hash,
                       "language": source_language,
                       "alignment_review_status": "human_attested_not_independently_verified",
                       "authorized_use_verified": False},
        "predictions": {"manifest_sha256": prediction_manifest_hash,
                        "execution_provenance": execution_info["kind"],
                        "execution_receipt_sha256": execution_info["receipt_sha256"],
                        "execution_run_id": execution_info["run_id"],
                        "engine_profiles": {key: {"model_sha256": value[0],
                                                   "config_sha256": value[1],
                                                   "configured_device": value[2],
                                                   "session_configured_backend": value[3]}
                                            for key, value in profiles.items()}},
        "scope": {"measurement": "fixed full-episode SRT vs timed source-language reference",
                  "ocr_executed": False, "training_performed": False,
                  "matching": {"algorithm": "maximum-cardinality one-to-one; Hopcroft-Karp traversal uses reference degree/index order and descending overlap/prediction index adjacency order",
                               "minimum_overlap_over_reference_duration": MIN_OVERLAP_REFERENCE_RATIO,
                               "max_interval_pair_operations": MAX_INTERVAL_PAIR_OPERATIONS,
                               "max_qualifying_edges": MAX_INTERVAL_CANDIDATES},
                  "normalization": crop_eval.NORMALIZATION,
                  "timing_tolerance_ms": tolerance,
                  "metrics_persisted": "aggregate only; no cue text or absolute paths"},
         "engines": results,
         "predeclared_comparison": {"baseline_id": engine_ids[0], "candidate_id": engine_ids[1],
                                    "coverage_plan_sha256": experiment_doc["evaluation_plan_sha256"],
                                    "comparison_gate": comparison,
                                    "max_candidate_corpus_cer_increase": gate["max_candidate_corpus_cer_increase"],
                                   "candidate_test_cer_minus_baseline": (candidate_cer - baseline_cer
                                                                          if baseline_cer is not None and candidate_cer is not None else None),
                                   "candidate_cer_exceeds_predeclared_limit": (
                                       candidate_cer - baseline_cer > gate["max_candidate_corpus_cer_increase"]
                                       if baseline_cer is not None and candidate_cer is not None else None),
                                   "test_group_cer": test_group_comparison,
                                   "test_groups_with_candidate_cer_regression": sum(
                                       row["candidate_minus_baseline"] is not None
                                       and row["candidate_minus_baseline"] > 0
                                       for row in test_group_comparison),
                                    "coverage_gate": coverage,
                                   "release_decision": "NOT_MADE_BY_THIS_ADAPTER"},
        "release_gate": {"status": "BLOCKED_ADAPTER_ONLY", "phase6_blocked": True,
                         "reason": "Fixed SRT evaluation does not independently verify rights, reference trust, model execution provenance, or candidate artifacts; use the Phase 6 validator only after the full authorized evidence chain is established."},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sabit yerel SRT çıktıları için Phase 5 ölçüm adaptörü; release PASS vermez")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--experiment", required=True, type=Path, help="Phase 5 PREPARED_NOT_RUN experiment-report.json")
    parser.add_argument("--references", required=True, type=Path, help="timed-reference-manifest-v1.json")
    parser.add_argument("--predictions", required=True, type=Path, help="prediction-manifest-v1.json")
    parser.add_argument("--out", required=True, type=Path, help="u yeni aggregate JSON raporu")
    args = parser.parse_args(argv)
    try:
        report = run(args.dataset, args.metadata, args.experiment, args.references,
                     args.predictions, args.out)
    except (VideoEvalError, crop_eval.EvalError, prep.InputError, OSError) as exc:
        print(f"DURDURULDU: {exc}", file=sys.stderr)
        return 2
    print(f"Sabit SRT ölçüm raporu yazıldı: {args.out}; Phase 6 için uygun değildir.")
    if report["predeclared_comparison"]["comparison_gate"]["status"] != "PASS":
        print("DURDURULDU: önceden ilan edilen CER, grup gerilemesi veya coverage gate başarısız.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
