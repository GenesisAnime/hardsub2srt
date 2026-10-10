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
MIN_OVERLAP_REFERENCE_RATIO = 0.5  # frozen; overlap / reference cue duration
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENGINE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
TIMESTAMP_RE = re.compile(r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3})$")


class VideoEvalError(ValueError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    if type(raw) is not str or not raw or "\\" in raw:
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
    _, _, _, manifest_hash, metadata_hash, split_hash = crop_eval.validate_run(
        dataset, metadata_path, split_path)
    experiment, _ = _json(experiment_path)
    if (type(experiment) is not dict or type(experiment.get("schema_version")) is not int
            or experiment.get("schema_version") != 1
            or experiment.get("status") != "PREPARED_NOT_RUN"):
        raise VideoEvalError("Yalnız Phase 5 PREPARED_NOT_RUN manifesti referans alınabilir")
    ds = experiment.get("dataset")
    exp = experiment.get("experiment")
    stop = experiment.get("stop_gate")
    if (type(ds) is not dict or type(exp) is not dict or type(stop) is not dict
            or ds.get("manifest_sha256") != manifest_hash
            or ds.get("metadata_sha256") != metadata_hash
            or exp.get("baseline_id") in (None, "")
            or exp.get("candidate_id") in (None, "")
            or exp["baseline_id"] == exp["candidate_id"]
            or type(exp.get("predeclared_gate")) is not dict):
        raise VideoEvalError("Experiment report Phase 5 dataset/engine/gate alanları doğrulanamadı")
    gate = exp["predeclared_gate"]
    if (type(gate.get("max_candidate_corpus_cer_increase")) not in (int, float)
            or not math.isfinite(gate["max_candidate_corpus_cer_increase"])
            or gate["max_candidate_corpus_cer_increase"] < 0
            or type(gate.get("timing_tolerance_ms")) is not int
            or gate["timing_tolerance_ms"] < 0
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


def _load_predictions(path: Path, groups: dict, references: dict, experiment: dict):
    path = path.resolve(strict=True)
    doc, raw = _json(path)
    if (type(doc) is not dict or set(doc) != {"schema_version", "records"}
            or type(doc["schema_version"]) is not int or doc["schema_version"] != 1):
        raise VideoEvalError("prediction-manifest-v1 şeması gerekli")
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
    found, profiles = {}, {}
    required = {"engine_id", "source_id", "episode_id", "split", "media_sha256",
                "prediction_srt_path", "prediction_srt_sha256", "model_sha256",
                "config_sha256", "device", "backend"}
    for record in records:
        if type(record) is not dict or set(record) != required:
            raise VideoEvalError("Prediction kaydı alanları tam eşleşmeli")
        if (type(record["engine_id"]) is not str or type(record["source_id"]) is not str
                or type(record["episode_id"]) is not str or type(record["split"]) is not str):
            raise VideoEvalError("Prediction engine/source/episode/split alanları geçersiz")
        engine, key = record["engine_id"], prep._group_key(record)
        if engine not in engines or key not in groups or (engine, key) in found:
            raise VideoEvalError("Prediction engine/grup bilinmiyor veya yinelenmiş")
        ref = references[key]
        if (record["split"] != groups[key] or record["media_sha256"] != ref["media_sha256"]
                or not SHA256_RE.fullmatch(str(record["model_sha256"]))
                or not SHA256_RE.fullmatch(str(record["config_sha256"]))
                or type(record["device"]) is not str or record["device"] not in {"gpu", "cpu"}
                or type(record["backend"]) is not str or not record["backend"].strip()
                or not SHA256_RE.fullmatch(str(record["prediction_srt_sha256"]))):
            raise VideoEvalError("Prediction split/media/model config/device provenance uyuşmuyor")
        profile = (record["model_sha256"], record["config_sha256"],
                   record["device"], record["backend"])
        if engine in profiles and profiles[engine] != profile:
            raise VideoEvalError("Aynı engine için config/device/backend gruplar arasında değişiyor")
        profiles[engine] = profile
        pred_file = _relative_file(path.parent, record["prediction_srt_path"], "Prediction SRT")
        if pred_file.stat().st_size > MAX_SRT_BYTES:
            raise VideoEvalError("Prediction SRT boyut sınırını aşıyor")
        pred_bytes = pred_file.read_bytes()
        if _sha256(pred_bytes) != record["prediction_srt_sha256"]:
            raise VideoEvalError("Prediction SRT hash'i eşleşmiyor")
        found[(engine, key)] = _parse_srt_bytes(pred_bytes, f"{engine} {key} prediction")
    expected = {(engine, key) for engine in engines for key in groups}
    if set(found) != expected:
        raise VideoEvalError("Prediction grupları baseline/aday için frozen split'i tam karşılamıyor")
    return found, profiles, _sha256(raw)


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


def run(dataset: Path, metadata: Path, experiment: Path, references_path: Path,
        predictions_path: Path, out_path: Path):
    dataset = dataset.resolve(strict=True)
    metadata = metadata.resolve(strict=True)
    split_path = experiment.resolve(strict=True).parent / "split-manifest.json"
    groups, experiment_doc, manifest_hash, metadata_hash, split_hash = _validated_split(
        experiment, split_path, dataset, metadata)
    references, ref_manifest_hash, source_language = _load_references(references_path, groups)
    predictions, profiles, prediction_manifest_hash = _load_predictions(
        predictions_path, groups, references, experiment_doc)
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
                        "execution_provenance": "user_supplied_not_independently_verified",
                        "engine_profiles": {key: {"model_sha256": value[0],
                                                   "config_sha256": value[1],
                                                   "device": value[2], "backend": value[3]}
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
                                   "coverage_gate": "NOT_PREDECLARED_IN_PHASE5_V1",
                                   "release_decision": "NOT_MADE_BY_THIS_ADAPTER"},
        "release_gate": {"status": "BLOCKED_ADAPTER_ONLY", "phase6_blocked": True,
                         "reason": "Fixed SRT evaluation does not independently verify rights, reference trust, model execution provenance, or candidate artifacts; use the Phase 6 validator only after the full authorized evidence chain is established."},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        run(args.dataset, args.metadata, args.experiment, args.references,
            args.predictions, args.out)
    except (VideoEvalError, crop_eval.EvalError, prep.InputError, OSError) as exc:
        print(f"DURDURULDU: {exc}", file=sys.stderr)
        return 2
    print(f"Sabit SRT ölçüm raporu yazıldı: {args.out}; Phase 6 için uygun değildir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
