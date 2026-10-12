"""Optional Ajv 2020 validation for synthetic Phase 5 receipt/report schemas.

Set HARDSUB2SRT_AJV_MODULE to an installed Ajv 8 `dist/2020` module path.
The Python suite remains usable where Node/Ajv is not installed.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILES = {
    "receipt": "ocr-phase5-execution-receipt-v2.schema.json",
    "manifest": "ocr-prediction-manifest-v1.schema.json",
    "report": "ocr-video-evaluation-report-v1.schema.json",
}
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64

AJV_BRIDGE = r"""
const fs = require('fs');
const mod = require(process.env.HARDSUB2SRT_AJV_MODULE);
const Ajv2020 = mod.default || mod.Ajv2020 || mod;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ajv = new Ajv2020({strict:false, validateFormats:false, allErrors:true});
const reportPredictions = {
  $schema: input.schemas.report.$schema,
  $id: 'https://example.invalid/hardsub2srt/test/report-predictions.schema.json',
  $defs: input.schemas.report.$defs,
  ...input.schemas.report.properties.predictions
};
const validators = {
  receipt: ajv.compile(input.schemas.receipt),
  manifest: ajv.compile(input.schemas.manifest),
  report_predictions: ajv.compile(reportPredictions),
  report_full: ajv.compile(input.schemas.report)
};
const results = input.cases.map(test => {
  const ok = validators[test.schema](test.value);
  return {name:test.name, valid:Boolean(ok), errors:ok ? [] : validators[test.schema].errors};
});
process.stdout.write(JSON.stringify(results));
"""


def _synthetic_records():
    rows = []
    for engine, folder in (("synthetic-baseline", "baseline"),
                           ("synthetic-candidate", "candidate")):
        for index, split in enumerate(("train", "validation", "test"), 1):
            name = f"{index}.srt"
            rows.append({
                "engine_id": engine,
                "source_id": f"synthetic-source-{index}",
                "episode_id": f"synthetic-episode-{index}",
                "split": split,
                "media_sha256": SHA_A,
                "prediction_srt_path": f"{folder}/{name}",
                "prediction_srt_sha256": SHA_B,
                "profile_sha256": SHA_C,
                "model_sha256": SHA_D,
                "config_sha256": SHA_A,
                "configured_device": "cpu",
                "session_configured_backend": "easyocr;secondary=synthetic",
                "stats_sha256": SHA_B,
                "stats_path": f"{folder}/{index}.stats.json",
                "cue_count": 0,
            })
    return rows


def _synthetic_receipt(records):
    return {
        "schema_version": 2,
        "status": "COMPLETED_LOCAL_EXECUTION_NOT_PHASE6_PASS",
        "run_id": "synthetic-run-01",
        "created_at": "2026-10-12T12:00:00+00:00",
        "duration_seconds": 0.0,
        "application": {"commit": None, "source_bundle_sha256": SHA_A,
                        "source_sha256s": {"synthetic.py": SHA_B}},
        "runtime": {
            "python": {"version": "3.x", "executable_sha256": SHA_A,
                       "startup_mode": "isolated", "package_roots": []},
            "distributions": [], "media_tools": {},
            "environment": {"sha256s": {}, "removed_proxy_variables": [],
                             "fixed_offline_flags": {},
                             "python_override_variables_removed": []},
        },
        "execution": {"mode": "product_cli_allowlisted_offline_profile",
                      "network_policy": "python_socket_apis_blocked; managed OCR loaders pinned/local/download-disabled; OS network isolation not certified",
                      "phase6_eligible": False, "rights_verified": False,
                      "fixed_args": ["--ocr", "paddle", "--qa", "0"]},
        "inputs": {
            "dataset_manifest_sha256": SHA_A, "dataset_metadata_sha256": SHA_A,
            "split_manifest_sha256": SHA_A, "evaluation_plan_sha256": SHA_A,
            "experiment_report_sha256": SHA_A, "reference_manifest_sha256": SHA_A,
            "media_sha256s": {SHA_B: SHA_A},
            "profile_sha256s": {"synthetic-baseline": SHA_C,
                                 "synthetic-candidate": SHA_C},
            "execution_plan_sha256": SHA_A, "rights_receipt_sha256": SHA_A,
            "trust_registry_sha256": SHA_A,
        },
        "profiles": {
            engine: {"profile_id": engine, "profile_sha256": SHA_C,
                     "model_sha256": SHA_D, "device_preference": "cpu"}
            for engine in ("synthetic-baseline", "synthetic-candidate")
        },
        "predictions": {"manifest": "prediction-manifest-v1.json",
                        "manifest_records_sha256": SHA_A, "records": records},
        "limits": ["synthetic limit one", "synthetic limit two", "synthetic limit three"],
    }


def _synthetic_full_report(predictions):
    counts = {
        "reference_cues": 1, "prediction_cues": 1, "matched_cues": 1,
        "missed_reference_cues": 0, "unmatched_prediction_cues": 0,
        "cue_precision": 1.0, "cue_recall": 1.0, "coverage": 1.0,
        "cer": 0.0, "wer": 0.0, "exact_match_cue_rate": 1.0,
        "reference_chars": 1, "reference_words": 1,
        "character_edits": 0, "word_edits": 0,
        "timing_start_abs_error_ms": 0.0, "timing_end_abs_error_ms": 0.0,
        "timing_within_tolerance_rate": 1.0, "timing_tolerance_ms": 150,
    }
    macro = {key: counts[key] for key in (
        "cue_precision", "cue_recall", "coverage", "cer", "wer",
        "exact_match_cue_rate", "timing_start_abs_error_ms",
        "timing_end_abs_error_ms", "timing_within_tolerance_rate")}
    engines = []
    for engine in ("synthetic-baseline", "synthetic-candidate"):
        for split in ("train", "validation", "test"):
            engines.append({
                "engine_id": engine,
                "split": split,
                "micro": dict(counts),
                "group_macro": dict(macro),
                "per_source_episode": [{"group_key_sha256": SHA_B, "split": split,
                                        **counts}],
            })
    group_rows = []
    for engine in ("synthetic-baseline", "synthetic-candidate"):
        group_rows.append({"engine_id": engine, "group_key_sha256": SHA_B,
                           "numerator_matched_cues": 1,
                           "denominator_reference_cues": 1,
                           "coverage": 1.0, "passed": True})
    return {
        "schema_version": 1,
        "status": "MEASURED_ADAPTER_ONLY_NOT_RELEASE_PASS",
        "phase6_eligible": False,
        "created_at": "2026-10-12T12:00:00+00:00",
        "dataset": {"manifest_sha256": SHA_A, "metadata_sha256": SHA_A,
                    "split_manifest_sha256": SHA_A, "groups": 3,
                    "rights_verified": False,
                    "rights_review_status": "human_attested_not_legally_verified"},
        "references": {"manifest_sha256": SHA_A, "language": "ja",
                       "alignment_review_status": "human_attested_not_independently_verified",
                       "authorized_use_verified": False},
        "predictions": predictions,
        "scope": {
            "measurement": "fixed full-episode SRT vs timed source-language reference",
            "ocr_executed": False, "training_performed": False,
            "matching": {
                "algorithm": "maximum-cardinality one-to-one; Hopcroft-Karp traversal uses reference degree/index order and descending overlap/prediction index adjacency order",
                "minimum_overlap_over_reference_duration": 0.5,
                "max_interval_pair_operations": 2000000,
                "max_qualifying_edges": 2000000,
            },
            "normalization": "synthetic normalization",
            "timing_tolerance_ms": 150,
            "metrics_persisted": "aggregate only; no cue text or absolute paths",
        },
        "engines": engines,
        "predeclared_comparison": {
            "baseline_id": "synthetic-baseline",
            "candidate_id": "synthetic-candidate",
            "coverage_plan_sha256": SHA_C,
            "comparison_gate": {
                "status": "PASS", "cer_limit_passed": True,
                "candidate_corpus_cer_increase": 0.0,
                "maximum_candidate_corpus_cer_increase": 0.01,
                "no_test_group_regression_required": True,
                "no_test_group_regression_passed": True,
                "test_group_cer_data_complete": True,
                "coverage_passed": True,
            },
            "max_candidate_corpus_cer_increase": 0.01,
            "candidate_test_cer_minus_baseline": 0.0,
            "candidate_cer_exceeds_predeclared_limit": False,
            "test_group_cer": [{"group_key_sha256": SHA_B,
                                "baseline_cer": 0.0, "candidate_cer": 0.0,
                                "candidate_minus_baseline": 0.0}],
            "test_groups_with_candidate_cer_regression": 0,
            "coverage_gate": {
                "status": "PASS", "minimum_per_test_group": 0.9,
                "definition": "synthetic coverage rule",
                "applies_to": "both baseline and candidate for every frozen test group",
                "per_engine_group": group_rows,
            },
            "release_decision": "NOT_MADE_BY_THIS_ADAPTER",
        },
        "release_gate": {"status": "BLOCKED_ADAPTER_ONLY", "phase6_blocked": True,
                         "reason": "Synthetic measurement only."},
    }


class Phase5SchemaAjvTests(unittest.TestCase):
    def test_receipt_manifest_and_report_prediction_shapes_with_ajv(self):
        node = shutil.which("node")
        ajv_module = os.environ.get("HARDSUB2SRT_AJV_MODULE", "").strip()
        if not node or not ajv_module:
            raise unittest.SkipTest("Set HARDSUB2SRT_AJV_MODULE to an installed Ajv 8 dist/2020 module")

        records = _synthetic_records()
        receipt = _synthetic_receipt(records)
        runner_manifest = {
            "schema_version": 1,
            "execution_receipt": {"path": "phase5-execution-receipt-v2.json",
                                  "sha256": SHA_A, "run_id": "synthetic-run-01"},
            "records": records,
        }
        legacy_fields = (
            "engine_id", "source_id", "episode_id", "split", "media_sha256",
            "prediction_srt_path", "prediction_srt_sha256", "model_sha256", "config_sha256",
        )
        legacy_manifest = {
            "schema_version": 1,
            "records": [{**{key: row[key] for key in legacy_fields},
                         "device": row["configured_device"],
                         "backend": row["session_configured_backend"]}
                        for row in records],
        }
        engine_profile = {"model_sha256": SHA_D, "config_sha256": SHA_A,
                          "configured_device": "cpu",
                          "session_configured_backend": "easyocr;secondary=synthetic"}
        report_predictions = {
            "manifest_sha256": SHA_A,
            "execution_provenance": "runner_recorded_not_independently_verified",
            "execution_receipt_sha256": SHA_B,
            "execution_run_id": "synthetic-run-01",
            "engine_profiles": {"synthetic-baseline": engine_profile,
                                 "synthetic-candidate": engine_profile},
        }
        full_report = _synthetic_full_report(report_predictions)
        legacy_predictions = json.loads(json.dumps(report_predictions))
        legacy_predictions.update({
            "execution_provenance": "user_supplied_not_independently_verified",
            "execution_receipt_sha256": None,
            "execution_run_id": None,
        })
        legacy_full_report = _synthetic_full_report(legacy_predictions)

        cases = [
            {"schema": "receipt", "name": "valid receipt", "value": receipt},
            {"schema": "manifest", "name": "valid runner manifest", "value": runner_manifest},
            {"schema": "manifest", "name": "valid legacy manual manifest", "value": legacy_manifest},
            {"schema": "report_predictions", "name": "valid report prediction object",
             "value": report_predictions},
            {"schema": "report_full", "name": "valid runner report", "value": full_report},
            {"schema": "report_full", "name": "valid legacy manual report",
             "value": legacy_full_report},
        ]
        for field, unsafe in (("prediction_srt_path", "/absolute/output.srt"),
                               ("prediction_srt_path", "../outside.srt"),
                               ("stats_path", "C:/private/output.stats.json"),
                               ("stats_path", "bad\x00path")):
            changed = json.loads(json.dumps(receipt))
            changed["predictions"]["records"][0][field] = unsafe
            cases.append({"schema": "receipt", "name": f"unsafe receipt {field} path",
                          "value": changed, "expected": False})

        changed_report = json.loads(json.dumps(report_predictions))
        changed_report["raw_text"] = "synthetic extra payload"
        cases.append({"schema": "report_predictions", "name": "reject extra prediction field",
                      "value": changed_report, "expected": False})
        changed_report = json.loads(json.dumps(report_predictions))
        changed_report["engine_profiles"]["unexpected"] = engine_profile
        cases.append({"schema": "report_predictions", "name": "reject unknown engine profile",
                      "value": changed_report, "expected": False})
        changed_report = json.loads(json.dumps(report_predictions))
        changed_report["engine_profiles"]["synthetic-baseline"]["raw_text"] = "not a profile field"
        cases.append({"schema": "report_predictions", "name": "reject extra profile payload",
                      "value": changed_report, "expected": False})

        nested_extra_paths = (
            ("dataset", ("dataset",)),
            ("references", ("references",)),
            ("scope", ("scope",)),
            ("matching", ("scope", "matching")),
            ("predeclared comparison", ("predeclared_comparison",)),
            ("comparison gate", ("predeclared_comparison", "comparison_gate")),
            ("test-group CER row", ("predeclared_comparison", "test_group_cer", 0)),
            ("coverage gate", ("predeclared_comparison", "coverage_gate")),
            ("coverage group row", ("predeclared_comparison", "coverage_gate", "per_engine_group", 0)),
            ("release gate", ("release_gate",)),
            ("engine split", ("engines", 0)),
            ("micro counts", ("engines", 0, "micro")),
            ("group macro", ("engines", 0, "group_macro")),
            ("per-source episode", ("engines", 0, "per_source_episode", 0)),
        )
        for label, path in nested_extra_paths:
            changed = json.loads(json.dumps(full_report))
            target = changed
            for part in path:
                target = target[part]
            target["synthetic_extra_payload"] = "not allowed by closed report schema"
            cases.append({"schema": "report_full", "name": f"reject extra field in {label}",
                          "value": changed, "expected": False})

        schemas = {name: json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
                   for name, filename in SCHEMA_FILES.items()}
        completed = subprocess.run(
            [node, "-e", AJV_BRIDGE],
            input=json.dumps({"schemas": schemas, "cases": cases}, ensure_ascii=False),
            text=True, capture_output=True, timeout=20,
            env={**os.environ, "HARDSUB2SRT_AJV_MODULE": ajv_module}, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        results = json.loads(completed.stdout)
        self.assertEqual(len(results), len(cases))
        for case, result in zip(cases, results):
            with self.subTest(case=result["name"]):
                expected = case.get("expected", True)
                self.assertEqual(result["valid"], expected, result["errors"])


if __name__ == "__main__":
    unittest.main()
