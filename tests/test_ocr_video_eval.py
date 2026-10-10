import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ocr_video_eval as video_eval
import ocr_experiment as prep


def cue(index, start, end, text):
    return {"index": index, "start_ms": start, "end_ms": end, "text": text}


class FullVideoAdapterTests(unittest.TestCase):
    def test_strict_srt_parser_reads_synthetic_cues_and_rejects_bad_time(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fixture.srt"
            path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nSynthetic line\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nAnother line\n",
                encoding="utf-8",
            )
            parsed = video_eval._read_srt(path, "fixture")
            self.assertEqual([(x["start_ms"], x["end_ms"]) for x in parsed],
                             [(1000, 2000), (3000, 4000)])
            path.write_text("1\nnot a timestamp --> 00:00:02,000\ntext\n", encoding="utf-8")
            with self.assertRaises(video_eval.VideoEvalError):
                video_eval._read_srt(path, "fixture")

    def test_synthetic_scoring_counts_missing_extra_text_and_timing(self):
        refs = [cue(0, 1000, 2000, "Café line"), cue(1, 3000, 4000, "Missing line")]
        preds = [cue(0, 1100, 2050, "Cafe line"), cue(1, 7000, 8000, "Extra line")]
        score = video_eval.score_cues(refs, preds, 150)
        self.assertEqual(score["matched_cues"], 1)
        self.assertEqual(score["missed_reference_cues"], 1)
        self.assertEqual(score["unmatched_prediction_cues"], 1)
        self.assertEqual(score["cue_precision"], 0.5)
        self.assertEqual(score["cue_recall"], 0.5)
        self.assertEqual(score["coverage"], 0.5)
        self.assertEqual(score["timing_start_abs_error_ms"], 100)
        self.assertEqual(score["timing_end_abs_error_ms"], 50)
        # Text scoring reuses the crop evaluator's NFC/whitespace CER/WER rules.
        self.assertGreater(score["cer"], 0)

    def test_tie_break_is_deterministic_one_to_one(self):
        refs = [cue(0, 0, 1000, "A"), cue(1, 1000, 2000, "B")]
        preds = [cue(0, 500, 1500, "x"), cue(1, 500, 1500, "y")]
        first = video_eval.match_cues(refs, preds)
        self.assertEqual(first, [(0, 0), (1, 1)])
        self.assertEqual(first, video_eval.match_cues(refs, preds))

    def test_matching_maximizes_cardinality_before_greedy_overlap_preference(self):
        refs = [cue(0, 0, 1000, "A"), cue(1, 1000, 2000, "B")]
        # The 0..1500 cue has the largest single overlap with reference 0,
        # but assigning it there would leave reference 1 unmatched.
        preds = [cue(0, 0, 1500, "merged"), cue(1, 0, 900, "short")]
        self.assertEqual(video_eval.match_cues(refs, preds), [(0, 1), (1, 0)])

    def test_dense_nonqualifying_pairs_hit_pair_operation_bound(self):
        refs = [cue(i, 0, 100, "reference") for i in range(3)]
        preds = [cue(i, 0, 40, "prediction") for i in range(3)]
        with patch.object(video_eval, "MAX_INTERVAL_PAIR_OPERATIONS", 2):
            with self.assertRaisesRegex(video_eval.VideoEvalError, "çift işlemi sınırı"):
                video_eval.match_cues(refs, preds)

    def test_active_list_pruning_scan_counts_toward_pair_operation_bound(self):
        refs = [cue(0, 0, 100, "first"), cue(1, 200, 300, "second")]
        preds = [cue(0, 0, 40, "expires before second")]
        with patch.object(video_eval, "MAX_INTERVAL_PAIR_OPERATIONS", 2):
            with self.assertRaisesRegex(video_eval.VideoEvalError, "çift işlemi sınırı"):
                video_eval.match_cues(refs, preds)

    def test_reference_manifest_requires_human_alignment_review_and_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            srt = root / "reference.srt"
            raw = b"1\n00:00:01,000 --> 00:00:02,000\nSynthetic source\n"
            srt.write_bytes(raw)
            record = {"source_id": "synthetic-source", "episode_id": "synthetic-episode",
                      "split": "test", "language": "en", "media_sha256": "a" * 64,
                      "reference_srt_path": "reference.srt",
                      "reference_srt_sha256": video_eval._sha256(raw),
                      "alignment_review": {"status": "pending", "reviewer": "",
                                           "reviewed_at": "", "media_sha256": "a" * 64}}
            manifest = root / "timed-reference-manifest-v1.json"
            manifest.write_text(json.dumps({"schema_version": 1, "records": [record]}),
                                encoding="utf-8")
            with self.assertRaises(video_eval.VideoEvalError):
                video_eval._load_references(manifest,
                    {prep._group_key({"source_id": "synthetic-source",
                                      "episode_id": "synthetic-episode"}): "test"})
            record["alignment_review"] = {"status": "reviewed", "reviewer": "synthetic-reviewer",
                                          "reviewed_at": "2030-01-01T00:00:00Z",
                                          "media_sha256": "a" * 64}
            second = dict(record, source_id="synthetic-source-2", episode_id="synthetic-episode-2",
                          language="ja", reference_srt_sha256=video_eval._sha256(raw))
            record["language"] = "en"
            manifest.write_text(json.dumps({"schema_version": 1, "records": [record, second]}),
                                encoding="utf-8")
            with self.assertRaises(video_eval.VideoEvalError):
                video_eval._load_references(manifest,
                    {prep._group_key({"source_id": "synthetic-source",
                                      "episode_id": "synthetic-episode"}): "test",
                     prep._group_key({"source_id": "synthetic-source-2",
                                      "episode_id": "synthetic-episode-2"}): "test"})

    def test_malformed_split_root_or_row_fails_with_controlled_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            experiment_dir = root / "experiment"
            experiment_dir.mkdir()
            experiment = experiment_dir / "experiment-report.json"
            split_path = experiment_dir / "split-manifest.json"
            valid_experiment = {
                "schema_version": 1, "status": "PREPARED_NOT_RUN",
                "dataset": {"manifest_sha256": "a" * 64, "metadata_sha256": "b" * 64},
                "experiment": {"baseline_id": "baseline", "candidate_id": "candidate",
                               "predeclared_gate": {"max_candidate_corpus_cer_increase": 0.01,
                                                     "timing_tolerance_ms": 100,
                                                     "require_no_test_group_regression": True}},
                "stop_gate": {"human_rights_review_complete": True,
                              "human_provenance_verified": True,
                              "must_have_train_validation_test": True,
                              "trusted_reference_required": True,
                              "engine_and_config_required": True,
                              "no_regression_gate_predeclared": True,
                              "experiment_run_blocked": True,
                              "metrics_claimable": False,
                              "rights_verified": False},
            }
            experiment.write_text(json.dumps(valid_experiment), encoding="utf-8")
            with patch.object(video_eval.crop_eval, "validate_run",
                              return_value=([], {}, {}, "a" * 64, "b" * 64, "c" * 64)) as validate:
                for malformed in ([], {"dataset_manifest_sha256": "a" * 64, "rows": [None]}):
                    split_path.write_text(json.dumps(malformed), encoding="utf-8")
                    validate.return_value = ([], {}, {}, "a" * 64, "b" * 64,
                                             video_eval._sha256(split_path.read_bytes()))
                    with self.assertRaises(video_eval.VideoEvalError):
                        video_eval._validated_split(experiment, split_path,
                                                    root / "dataset", root / "metadata.json")

    def test_synthetic_report_is_always_non_release_and_rights_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inputs = root / "inputs"
            dataset = inputs / "pack" / "verified-ocr-dataset-synthetic"
            metadata = inputs / "metadata"
            experiment_dir = inputs / "experiment"
            references_dir = inputs / "references"
            predictions_dir = inputs / "predictions"
            output_dir = root / "output"
            for directory in (dataset, metadata, experiment_dir, references_dir,
                              predictions_dir, output_dir):
                directory.mkdir(parents=True, exist_ok=True)
            experiment = experiment_dir / "experiment-report.json"
            experiment.write_text("{}", encoding="utf-8")
            metadata_path = metadata / "dataset-metadata.json"
            metadata_path.write_text("{}", encoding="utf-8")
            (experiment_dir / "split-manifest.json").write_text("{}", encoding="utf-8")
            reference_manifest = references_dir / "reference.json"
            reference_manifest.write_text("{}", encoding="utf-8")
            prediction_manifest = predictions_dir / "predictions.json"
            prediction_manifest.write_text("{}", encoding="utf-8")
            out = output_dir / "synthetic-report.json"
            hashes = {name: "a" * 64 for name in ("manifest", "metadata", "split", "ref", "pred")}
            groups = {prep._group_key({"source_id": "synthetic-source-1", "episode_id": "synthetic-episode-1"}): "train",
                      prep._group_key({"source_id": "synthetic-source-2", "episode_id": "synthetic-episode-2"}): "validation",
                      prep._group_key({"source_id": "synthetic-source-3", "episode_id": "synthetic-episode-3"}): "test"}
            exp_doc = {"experiment": {"baseline_id": "synthetic-baseline",
                                       "candidate_id": "synthetic-candidate",
                                       "predeclared_gate": {"max_candidate_corpus_cer_increase": 0.01,
                                                             "timing_tolerance_ms": 150,
                                                             "require_no_test_group_regression": True}}}
            refs = {key: {"split": split, "media_sha256": "b" * 64,
                          "cues": [cue(0, 1000, 2000, "Synthetic source text")]}
                    for key, split in groups.items()}
            preds = {}
            for engine in ("synthetic-baseline", "synthetic-candidate"):
                for key in groups:
                    preds[(engine, key)] = [cue(0, 1050, 2000,
                                                "Synthetic source text" if engine.endswith("baseline")
                                                else "Synthetic source text")]
            profiles = {"synthetic-baseline": ("e" * 64, "c" * 64, "cpu", "synthetic-cpu"),
                        "synthetic-candidate": ("f" * 64, "d" * 64, "cpu", "synthetic-cpu")}
            with patch.object(video_eval, "_validated_split",
                              return_value=(groups, exp_doc, hashes["manifest"],
                                            hashes["metadata"], hashes["split"])), \
                    patch.object(video_eval, "_load_references",
                                 return_value=(refs, hashes["ref"], "en")), \
                    patch.object(video_eval, "_load_predictions",
                                 return_value=(preds, profiles, hashes["pred"])):
                video_eval.run(dataset, metadata_path, experiment,
                               reference_manifest, prediction_manifest, out)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "MEASURED_ADAPTER_ONLY_NOT_RELEASE_PASS")
            self.assertFalse(report["phase6_eligible"])
            self.assertFalse(report["dataset"]["rights_verified"])
            self.assertFalse(report["references"]["authorized_use_verified"])
            self.assertEqual(report["release_gate"]["status"], "BLOCKED_ADAPTER_ONLY")
            self.assertEqual(len(report["engines"]), 6)
            self.assertEqual(report["references"]["language"], "en")
            self.assertEqual(report["scope"]["matching"]["max_interval_pair_operations"], 2_000_000)
            self.assertNotIn("Synthetic source text", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
