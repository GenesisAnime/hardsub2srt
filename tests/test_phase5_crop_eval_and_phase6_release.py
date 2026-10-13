import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import ocr_crop_eval as crop_eval
import ocr_experiment as prep
import ocr_review
import phase6_release
from ocr_dataset_merge import merge_exports
from review_bundle import build_review_pack


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


class SyntheticPhase5Inputs:
    """Build a fully synthetic, locally reviewed three-group crop dataset."""

    def __init__(self, root: Path):
        self.root = root
        exports = []
        self.packs = []
        for number in range(1, 4):
            run = root / f"synthetic-run-{number}"
            run.mkdir(parents=True)
            srt = run / "synthetic.srt"
            label = f"SYNTHETIC LABEL {number}"
            srt.write_text(
                f"1\n00:00:01,000 --> 00:00:02,000\n{label}\n\n", encoding="utf-8")
            cue = {"start_ms": 1000, "end_ms": 2000, "text": label,
                   "confidence": 0.9, "frame_index": 1, "frame_ms": 1500,
                   "mapping_status": "exact_original_ocr_segment_needs_visual_confirmation",
                   "source_region": "main_band", "band_y": 0, "band_height": 40}
            image = np.full((40, 240, 3), number * 20, dtype=np.uint8)
            image[10:30, 20:220] = 255
            pack, manifest = build_review_pack(
                srt, [cue], {"tool_version": "synthetic-test"}, lambda *_args: image.copy())
            self.packs.append(pack)
            review_id, _ = ocr_review.open_pack(str(pack))
            ocr_review.append_review(review_id, manifest["job_id"] + "-cue-00001", "accepted")
            export, count = ocr_review.export_dataset(review_id)
            if count != 1:
                raise AssertionError("synthetic review export should contain one row")
            exports.append(export)

        self.dataset, _ = merge_exports(
            root, [path.relative_to(root) for path in exports],
            "verified-ocr-dataset-composite-synthetic-eval")
        self.rows = prep.validate_dataset(self.dataset)
        self.metadata_path = root / "synthetic-metadata.json"
        self._write_metadata()
        self.experiment = root / "synthetic-experiment"
        prep.make_artifacts(self.dataset, self.metadata_path, self.experiment,
                            "SYNTHETIC-SEED", "synthetic-baseline", "synthetic-candidate",
                            0.01, 500, 0.95)
        self.split_path = self.experiment / "split-manifest.json"

    def _write_metadata(self, *, pending: bool = False) -> None:
        records = []
        for index, row in enumerate(self.rows, 1):
            evidence_rel = f"synthetic-rights/evidence-{index}.txt"
            evidence_path = self.root / evidence_rel
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence = f"synthetic test evidence {index}".encode("utf-8")
            evidence_path.write_bytes(evidence)
            records.append({
                "cue_id": row["cue_id"], "source_id": f"synthetic-source-{index}",
                "episode_id": f"synthetic-episode-{index}",
                "license": {"status": "user_owned", "identifier": f"synthetic-license-{index}",
                            "evidence_path": evidence_rel, "evidence_sha256": _sha(evidence),
                            "human_review": {"status": "pending" if pending else "reviewed",
                                             "reviewer": None if pending else "synthetic-reviewer",
                                             "reviewed_at": None if pending else "2026-10-12T00:00:00Z"}},
            })
        _write_json(self.metadata_path, {"schema_version": 1, "records": records})


class Phase5CropEvalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hardsub-phase5-synthetic-")
        self.root = Path(self.tmp.name).resolve()   # kanonik kök (kısa ad/bağ karışmasın)
        self.inputs = SyntheticPhase5Inputs(self.root)

    def tearDown(self):
        for pack in self.inputs.packs:
            with ocr_review._LOCK:
                for review_id, info in list(ocr_review._PACKS.items()):
                    if info.get("path") == pack:
                        ocr_review._PACKS.pop(review_id, None)
        self.tmp.cleanup()

    def test_metric_mechanics_and_group_macro_are_known_for_synthetic_pairs(self):
        metrics = crop_eval.score_pairs([("CAT", "CAT"), ("DOG", "DOT")])
        self.assertEqual(metrics["rows"], 2)
        self.assertEqual(metrics["character_edits"], 1)
        self.assertAlmostEqual(metrics["cer"], 1 / 6)
        self.assertEqual(metrics["exact_match_rate"], 0.5)
        selected = [
            {"source_id": "synthetic-A", "episode_id": "episode-1"},
            {"source_id": "synthetic-B", "episode_id": "episode-2"},
        ]
        aggregate = crop_eval._aggregate("synthetic-engine", selected,
                                         [("CAT", "CAT"), ("DOG", "DOT")])
        self.assertEqual(aggregate["micro"]["exact_match_rate"], 0.5)
        self.assertEqual(len(aggregate["per_source_episode"]), 2)
        self.assertEqual(aggregate["group_macro"]["exact_match_rate"], 0.5)

    def test_validate_run_accepts_only_complete_reviewed_split_and_keeps_rights_false(self):
        selected, references, provenance, *_ = crop_eval.validate_run(
            self.inputs.dataset, self.inputs.metadata_path, self.inputs.split_path)
        self.assertTrue(selected)
        self.assertTrue(all(text.startswith("SYNTHETIC LABEL") for text in references.values()))
        self.assertEqual(provenance["status"], "verified_against_parent_review_pack")
        self.assertEqual(len(provenance["members"]), 3)

    def test_pre_result_plan_has_explicit_threshold_and_hash_bindings(self):
        plan = json.loads((self.inputs.experiment / "evaluation-plan-v1.json").read_text(encoding="utf-8"))
        split = json.loads(self.inputs.split_path.read_text(encoding="utf-8"))
        report = json.loads((self.inputs.experiment / "experiment-report.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["predeclared_gate"]["min_test_group_coverage"], 0.95)
        self.assertEqual(split["evaluation_plan_sha256"], _sha(
            (self.inputs.experiment / "evaluation-plan-v1.json").read_bytes()))
        self.assertEqual(report["evaluation_plan_sha256"], split["evaluation_plan_sha256"])
        self.assertEqual(report["split_manifest_sha256"], _sha(self.inputs.split_path.read_bytes()))

    def test_missing_threshold_and_mutated_or_replayed_plan_fail_closed(self):
        plan_path = self.inputs.experiment / "evaluation-plan-v1.json"
        original = json.loads(plan_path.read_text(encoding="utf-8"))
        missing = json.loads(json.dumps(original))
        del missing["predeclared_gate"]["min_test_group_coverage"]
        _write_json(plan_path, missing)
        with self.assertRaisesRegex(crop_eval.EvalError, "predeclared gate alanları"):
            crop_eval.validate_run(self.inputs.dataset, self.inputs.metadata_path,
                                   self.inputs.split_path)

        _write_json(plan_path, original)
        with plan_path.open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(crop_eval.EvalError, "hash'i veya split/dataset bağları"):
            crop_eval.validate_run(self.inputs.dataset, self.inputs.metadata_path,
                                   self.inputs.split_path)

        second = self.root / "synthetic-experiment-replay-source"
        prep.make_artifacts(self.inputs.dataset, self.inputs.metadata_path, second,
                            "SYNTHETIC-SEED", "synthetic-baseline", "synthetic-candidate",
                            0.01, 500, 0.95)
        plan_path.write_bytes((second / "evaluation-plan-v1.json").read_bytes())
        with self.assertRaisesRegex(crop_eval.EvalError, "hash'i veya split/dataset bağları"):
            crop_eval.validate_run(self.inputs.dataset, self.inputs.metadata_path,
                                   self.inputs.split_path)
        plan_path.unlink()
        with self.assertRaisesRegex(crop_eval.EvalError, "Evaluation plan okunamadı"):
            crop_eval.validate_run(self.inputs.dataset, self.inputs.metadata_path,
                                   self.inputs.split_path)

    def test_crop_hash_mismatch_blocks_before_ocr(self):
        selected, references, *_ = crop_eval.validate_run(
            self.inputs.dataset, self.inputs.metadata_path, self.inputs.split_path)
        item = selected[0]
        crop = prep._safe_crop(self.inputs.dataset, item["crop_path"])
        crop.write_bytes(crop.read_bytes() + b"tamper")
        fake_hs = SimpleNamespace(ocr_lines=lambda *_args: ("SHOULD NOT RUN", 1.0))
        with self.assertRaisesRegex(crop_eval.EvalError, "crop hash'i değişti"):
            crop_eval._engine_predictions("easyocr-single", [item], self.inputs.dataset,
                                          references, object(), fake_hs, None)

    def test_missing_crop_is_rejected(self):
        selected, *_ = crop_eval.validate_run(
            self.inputs.dataset, self.inputs.metadata_path, self.inputs.split_path)
        crop = prep._safe_crop(self.inputs.dataset, selected[0]["crop_path"])
        crop.unlink()
        with self.assertRaises(prep.InputError):
            crop_eval.validate_run(self.inputs.dataset, self.inputs.metadata_path, self.inputs.split_path)

    def test_unreviewed_rights_metadata_blocks_evaluation(self):
        self.inputs._write_metadata(pending=True)
        with self.assertRaisesRegex(crop_eval.EvalError, "status=reviewed"):
            crop_eval.validate_run(self.inputs.dataset, self.inputs.metadata_path, self.inputs.split_path)

    def test_provenance_requires_distinct_source_episode_groups(self):
        rows = prep.validate_dataset(self.inputs.dataset)
        provenance = prep.verify_phase2_provenance(self.inputs.dataset, rows)
        metadata = prep.validate_metadata(self.inputs.metadata_path, rows)
        first = next(iter(metadata.values()))
        for value in metadata.values():
            value["source_id"] = first["source_id"]
            value["episode_id"] = first["episode_id"]
        with self.assertRaisesRegex(prep.InputError, r"same source\+episode group"):
            prep.validate_provenance_groups(provenance, metadata, rows)

    def test_tampered_split_row_and_duplicate_manifest_key_are_rejected(self):
        split = json.loads(self.inputs.split_path.read_text(encoding="utf-8"))
        split["rows"][0]["split"] = "test" if split["rows"][0]["split"] != "test" else "train"
        _write_json(self.inputs.split_path, split)
        with self.assertRaisesRegex(crop_eval.EvalError, "Experiment report evaluation plan/hash"):
            crop_eval.validate_run(self.inputs.dataset, self.inputs.metadata_path, self.inputs.split_path)
        self.inputs.split_path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with self.assertRaisesRegex(crop_eval.EvalError, "yineleniyor"):
            crop_eval.validate_run(self.inputs.dataset, self.inputs.metadata_path, self.inputs.split_path)


class Phase6ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hardsub-phase6-synthetic-")
        self.root = Path(self.tmp.name).resolve()   # kanonik kök (kısa ad/bağ karışmasın)
        self.bundle = self.root / "model"
        self.bundle.mkdir()
        (self.bundle / "weights.bin").write_bytes(b"SYNTHETIC MODEL BYTES ONLY")
        self.report_path = self.root / "phase5-report.json"
        self.rights_path = self.root / "rights-review.json"
        self.evidence_dir = self.root / "evidence"
        self.evidence_dir.mkdir()
        self.trust_path = self.root / "schemas" / "synthetic-trust.json"
        self.trust_path.parent.mkdir()
        self._build_valid_candidate()

    def tearDown(self):
        self.tmp.cleanup()

    def _build_valid_candidate(self):
        model_path = self.bundle / "weights.bin"
        model_raw = model_path.read_bytes()
        files, bundle_hash = phase6_release._model_files([{
            "path": "model/weights.bin", "size_bytes": len(model_raw), "sha256": _sha(model_raw)}], self.root)
        provenance = {"source_commit": "a" * 40, "training_recipe_id": "synthetic-recipe-v1",
                      "dataset_manifest_sha256": "b" * 64, "dataset_metadata_sha256": "c" * 64,
                      "training_engine": "synthetic-engine-v1"}
        candidate = {"id": "synthetic-candidate", "version": "0.1.0", "model_format": "onnx"}
        p5_provenance = {"source_commit": provenance["source_commit"],
                         "training_recipe_id": provenance["training_recipe_id"],
                         "training_engine": provenance["training_engine"],
                         "model_files": files, "model_bundle_sha256": bundle_hash}
        p5_candidate = {"id": candidate["id"], "version": candidate["version"],
                        "source_commit": provenance["source_commit"],
                        "training_recipe_id": provenance["training_recipe_id"],
                        "training_engine": provenance["training_engine"],
                        "model_files": files, "model_bundle_sha256": bundle_hash}
        report = {"schema_version": 2, "status": "EVALUATED", "experiment_id": "synthetic-experiment",
                  "dataset": {"manifest_sha256": provenance["dataset_manifest_sha256"],
                              "metadata_sha256": provenance["dataset_metadata_sha256"]},
                  "provenance": p5_provenance,
                  "data_policy": {"rights_verified": True, "human_rights_review_complete": True,
                                  "training_performed": True, "local_only": True},
                  "experiment": {"baseline_id": "synthetic-baseline", "candidate_id": candidate["id"]},
                  "metrics": {"ocr": {"cer": 0.01, "wer": 0.02, "exact_match_cue_rate": 0.95,
                                       "cue_precision": 0.98, "cue_recall": 0.97,
                                       "timing_start_abs_error_ms": 10.0,
                                       "timing_end_abs_error_ms": 12.0, "coverage": 1.0,
                                       "per_group": [{"source_id": "synthetic-source",
                                                      "episode_id": "synthetic-episode"}]}},
                  "stop_gate": {"experiment_run_blocked": False, "human_provenance_verified": True,
                                "human_rights_review_complete": True, "rights_verified": True,
                                "metrics_claimable": True, "trusted_reference_required": True},
                  "candidate": p5_candidate,
                  "release_gate": {"status": "PASS", "criteria_predeclared": True,
                                   "no_regressions": True, "baseline_candidate_same_conditions": True,
                                   "frozen_test_split_sha256": "d" * 64,
                                   "trusted_reference_sha256": "e" * 64, "coverage_passed": True}}
        report_raw = _write_json(self.report_path, report)
        report_hash = _sha(report_raw)
        rights = {"schema_version": 1, "review_id": "synthetic-rights-review-1", "decision": "APPROVED",
                  "reviewer": {"reviewer_id": "synthetic-reviewer", "authority": "synthetic-test-only"},
                  "reviewed_at": "2026-10-12T00:00:00Z", "scope": "synthetic fixture only",
                  "dataset_manifest_sha256": provenance["dataset_manifest_sha256"],
                  "dataset_metadata_sha256": provenance["dataset_metadata_sha256"],
                  "license_evidence_sha256s": ["f" * 64], "candidate_id": candidate["id"],
                  "candidate_version": candidate["version"], "source_commit": provenance["source_commit"],
                  "training_recipe_id": provenance["training_recipe_id"],
                  "training_engine": provenance["training_engine"], "model_files": files,
                  "model_bundle_sha256": bundle_hash, "phase5_report_sha256": report_hash}
        rights_raw = _write_json(self.rights_path, rights)
        rights_hash = _sha(rights_raw)
        compatibility = []
        for device in ("gpu", "cpu"):
            evidence_path = self.evidence_dir / f"{device}.json"
            evidence_raw = _write_json(evidence_path, {"synthetic": True, "device": device})
            compatibility.append({"evidence_id": f"synthetic-{device}",
                                  "evidence_path": f"evidence/{device}.json",
                                  "evidence_sha256": _sha(evidence_raw), "os": "windows",
                                  "python": "3.11", "backend": "synthetic-backend",
                                  "device": device, "model_bundle_sha256": bundle_hash,
                                  "phase5_report_sha256": report_hash, "validation_passed": True})
        trust = {"schema_version": 1, "purpose": "synthetic test registry only",
                 "trusted_rights_receipts": [rights_hash], "trusted_phase5_reports": [report_hash],
                 "trusted_compatibility_evidence": compatibility}
        _write_json(self.trust_path, trust)
        self.manifest_path = self.root / "manifest.json"
        self.manifest = {"schema_version": 1, "candidate": candidate, "provenance": provenance,
                         "evaluation": {"report_path": "phase5-report.json", "report_sha256": report_hash,
                                        "phase5_status": "EVALUATED", "release_gate": "PASS"},
                         "rights_review": {"receipt_path": "rights-review.json",
                                           "receipt_sha256": rights_hash,
                                           "review_id": rights["review_id"]},
                         "model_files": files,
                         "compatibility": {"operating_systems": ["windows"],
                                            "python_versions": ["3.11"],
                                            "backends": ["synthetic-backend"],
                                            "devices": ["gpu", "cpu"]},
                         "migration": {"from_versions": ["0.0.1"], "steps": ["synthetic migration"],
                                       "data_format_version": "1"},
                         "rollback": {"supported": True, "steps": ["synthetic rollback"],
                                      "data_preserved": True},
                         "activation_policy": {"default_device": "gpu", "cpu_opt_in": True,
                                               "automatic_replacement": False,
                                               "user_confirmation_required": True}}
        _write_json(self.manifest_path, self.manifest)

    def _write_manifest(self):
        _write_json(self.manifest_path, self.manifest)

    def _write_trust(self, trust):
        _write_json(self.trust_path, trust)

    def test_synthetic_fully_pinned_fixture_is_structure_valid_but_never_released(self):
        self.assertEqual(phase6_release.validate(self.manifest_path, self.report_path, self.trust_path), [])

    def test_candidate_model_hash_tampering_is_rejected(self):
        (self.bundle / "weights.bin").write_bytes(b"TAMPERED SYNTHETIC MODEL")
        with self.assertRaisesRegex(phase6_release.GateError, "hash'i/boyutu uyuşmuyor"):
            phase6_release.validate(self.manifest_path, self.report_path, self.trust_path)

    def test_report_hash_binding_and_phase5_pass_binding_fail_closed(self):
        self.manifest["evaluation"]["report_sha256"] = "0" * 64
        self._write_manifest()
        with self.assertRaisesRegex(phase6_release.GateError, "raporunun SHA-256"):
            phase6_release.validate(self.manifest_path, self.report_path, self.trust_path)

    def test_phase5_v1_prepared_report_returns_explicit_blocker_without_release(self):
        import contextlib
        import io

        report_raw = _write_json(self.report_path, {
            "schema_version": 1, "status": "PREPARED_NOT_RUN",
            "experiment_id": "synthetic-prepared-only",
        })
        report_hash = _sha(report_raw)
        self.manifest["evaluation"]["report_sha256"] = report_hash
        self._write_manifest()

        rights = json.loads(self.rights_path.read_text(encoding="utf-8"))
        rights["phase5_report_sha256"] = report_hash
        rights_raw = _write_json(self.rights_path, rights)
        rights_hash = _sha(rights_raw)
        self.manifest["rights_review"]["receipt_sha256"] = rights_hash
        self._write_manifest()

        trust = json.loads(self.trust_path.read_text(encoding="utf-8"))
        trust["trusted_phase5_reports"] = [report_hash]
        trust["trusted_rights_receipts"] = [rights_hash]
        for item in trust["trusted_compatibility_evidence"]:
            item["phase5_report_sha256"] = report_hash
        self._write_trust(trust)

        blockers = phase6_release.validate(self.manifest_path, self.report_path, self.trust_path)
        self.assertTrue(any("Phase 5 v1" in blocker and "her koşulda reddedilir" in blocker
                            for blocker in blockers), blockers)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = phase6_release.main([str(self.manifest_path), "--phase5-report",
                                       str(self.report_path), "--json"])
        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("Phase 5 v1" in blocker and "her koşulda reddedilir" in blocker
                            for blocker in result["blockers"]), result)
        self.assertFalse(result["release_permitted"])
        self.assertFalse(result["packaging_performed"])
        self.assertFalse(result["publishing_performed"])
        self.assertFalse((self.root / "package").exists())

    def test_duplicate_candidate_manifest_key_is_rejected(self):
        self.manifest_path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with self.assertRaisesRegex(phase6_release.GateError, "yinelenen alan"):
            phase6_release.validate(self.manifest_path, self.report_path, self.trust_path)

    def test_phase5_rights_false_is_blocked_even_with_pinned_hash(self):
        report = json.loads(self.report_path.read_text(encoding="utf-8"))
        report["data_policy"]["rights_verified"] = False
        report["stop_gate"]["rights_verified"] = False
        report_raw = _write_json(self.report_path, report)
        report_hash = _sha(report_raw)
        self.manifest["evaluation"]["report_sha256"] = report_hash
        self.manifest["evaluation"]["release_gate"] = "PASS"
        self._write_manifest()
        rights = json.loads(self.rights_path.read_text(encoding="utf-8"))
        rights["phase5_report_sha256"] = report_hash
        rights_raw = _write_json(self.rights_path, rights)
        self.manifest["rights_review"]["receipt_sha256"] = _sha(rights_raw)
        self._write_manifest()
        trust = json.loads(self.trust_path.read_text(encoding="utf-8"))
        trust["trusted_phase5_reports"] = [report_hash]
        trust["trusted_rights_receipts"] = [_sha(rights_raw)]
        for item in trust["trusted_compatibility_evidence"]:
            item["phase5_report_sha256"] = report_hash
        self._write_trust(trust)
        blockers = phase6_release.validate(self.manifest_path, self.report_path, self.trust_path)
        self.assertTrue(any("rights_verified" in blocker or "rights/local data policy" in blocker
                            for blocker in blockers), blockers)

    def test_empty_pinned_registries_block_report_rights_and_compatibility(self):
        trust = json.loads(self.trust_path.read_text(encoding="utf-8"))
        trust.update(trusted_rights_receipts=[], trusted_phase5_reports=[],
                     trusted_compatibility_evidence=[])
        self._write_trust(trust)
        blockers = phase6_release.validate(self.manifest_path, self.report_path, self.trust_path)
        joined = "\n".join(blockers)
        self.assertIn("pinli trust listesinde yok", joined)
        self.assertIn("pinli doğrulama kanıtı yok", joined)

    def test_missing_pinned_cpu_or_gpu_compatibility_case_blocks(self):
        trust = json.loads(self.trust_path.read_text(encoding="utf-8"))
        trust["trusted_compatibility_evidence"] = [
            entry for entry in trust["trusted_compatibility_evidence"] if entry["device"] == "gpu"]
        self._write_trust(trust)
        blockers = phase6_release.validate(self.manifest_path, self.report_path, self.trust_path)
        self.assertTrue(any("pinli PASS kanıtı yok" in blocker for blocker in blockers), blockers)

    def test_rights_receipt_tamper_and_malformed_candidate_are_rejected(self):
        with self.rights_path.open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(phase6_release.GateError, "Hak inceleme makbuzu hash'i"):
            phase6_release.validate(self.manifest_path, self.report_path, self.trust_path)
        self.manifest["candidate"]["model_format"] = "unknown-format"
        self._write_manifest()
        with self.assertRaisesRegex(phase6_release.GateError, "model_format desteklenmiyor"):
            phase6_release.validate(self.manifest_path, self.report_path, self.trust_path)

    def test_cli_reports_no_packaging_or_publishing_when_gates_are_missing(self):
        import contextlib
        import io
        stdout = io.StringIO()
        # The repository trust registry is intentionally empty; also tamper the receipt
        # so the CLI must block before any hypothetical packaging or publishing action.
        with self.rights_path.open("ab") as stream:
            stream.write(b" tampered")
        with contextlib.redirect_stdout(stdout):
            code = phase6_release.main([str(self.manifest_path), "--phase5-report",
                                       str(self.report_path), "--json"])
        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["release_permitted"])
        self.assertFalse(result["packaging_performed"])
        self.assertFalse(result["publishing_performed"])
        self.assertFalse((self.root / "package").exists())


if __name__ == "__main__":
    unittest.main()
