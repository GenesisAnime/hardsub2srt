import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ocr_experiment as prep
import ocr_video_eval as evaluator
import ocr_video_eval_templates as templates


class VideoEvaluationTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dataset_parent = self.root / "pack"
        self.dataset = self.dataset_parent / "verified-ocr-dataset-synthetic"
        self.dataset.mkdir(parents=True)
        self.metadata_parent = self.root / "metadata-input"
        self.metadata_parent.mkdir()
        self.metadata = self.metadata_parent / "metadata.json"
        self.metadata.write_text("{}", encoding="utf-8")
        self.experiment_dir = self.root / "experiment-input"
        self.experiment_dir.mkdir()
        self.experiment = self.experiment_dir / "experiment-report.json"
        self.experiment.write_text("{}", encoding="utf-8")
        self.split_path = self.experiment_dir / "split-manifest.json"
        ids = [("synthetic-source-1", "synthetic-episode-1", "train"),
               ("synthetic-source-2", "synthetic-episode-2", "validation"),
               ("synthetic-source-3", "synthetic-episode-3", "test")]
        self.split_rows = []
        for source_id, episode_id, split_name in ids:
            self.split_rows.append({
                "group_id": prep._group_key({"source_id": source_id, "episode_id": episode_id}),
                "source_id": source_id,
                "episode_id": episode_id,
                "split": split_name,
            })
        self.split_path.write_text(json.dumps({"rows": self.split_rows}), encoding="utf-8")
        self.split_hash = evaluator._sha256(self.split_path.read_bytes())
        self.groups = {row["group_id"]: row["split"] for row in self.split_rows}
        self.experiment_document = {"experiment": {
            "baseline_id": "baseline-synthetic", "candidate_id": "candidate-synthetic"}}
        self.validated_result = (self.groups, self.experiment_document,
                                 "a" * 64, "b" * 64, self.split_hash)
        self.out_parent = self.root / "safe-output"
        self.out_parent.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def _create(self, out_parent=None, protect_dirs=None, validator=None):
        validator = validator or patch.object(
            templates.evaluator, "_validated_split", return_value=self.validated_result)
        with validator:
            return templates.create_templates(
                self.dataset, self.metadata, self.experiment,
                out_parent or self.out_parent, protect_dirs=protect_dirs)

    def test_synthetic_validator_boundary_fills_only_frozen_groups_and_pending_blanks(self):
        with patch.object(templates.evaluator, "_validated_split",
                          return_value=self.validated_result) as validate:
            output = templates.create_templates(
                self.dataset, self.metadata, self.experiment, self.out_parent)
        validate.assert_called_once_with(
            self.experiment.resolve(), self.split_path.resolve(),
            self.dataset.resolve(), self.metadata.resolve())
        reference = json.loads((output / "timed-reference-manifest-v1.draft.json").read_text(encoding="utf-8"))
        prediction = json.loads((output / "prediction-manifest-v1.draft.json").read_text(encoding="utf-8"))
        expected = {(row["source_id"], row["episode_id"], row["split"]) for row in self.split_rows}
        actual = {(row["source_id"], row["episode_id"], row["split"]) for row in reference["records"]}
        self.assertEqual(actual, expected)
        self.assertEqual(len(reference["records"]), len(expected))
        self.assertEqual(len(prediction["records"]), len(expected) * 2)
        self.assertEqual((prediction["baseline_id"], prediction["candidate_id"]),
                         ("baseline-synthetic", "candidate-synthetic"))
        self.assertEqual({row["engine_id"] for row in prediction["records"]},
                         {"baseline-synthetic", "candidate-synthetic"})
        for document in (reference, prediction):
            self.assertEqual(document["schema_version"], "draft-v1")
            self.assertTrue(document["template_only"])
            self.assertEqual(document["template_status"], "INCOMPLETE_NOT_FOR_EVALUATION")
            self.assertNotIn("rights_verified", document)
        self.assertTrue(all(row["language"] == "" and row["media_sha256"] == "" and
                            row["reference_srt_path"] == "" and row["reference_srt_sha256"] == "" and
                            row["alignment_review"]["status"] == "pending" and
                            row["alignment_review"]["reviewer"] == "" and
                            row["alignment_review"]["reviewed_at"] == ""
                            for row in reference["records"]))
        self.assertTrue(all(row["media_sha256"] == "" and row["prediction_srt_path"] == "" and
                            row["prediction_srt_sha256"] == "" and row["model_sha256"] == "" and
                            row["config_sha256"] == "" and row["device"] == "" and row["backend"] == ""
                            for row in prediction["records"]))

    def test_draft_marker_is_rejected_by_actual_evaluator(self):
        output = self._create()
        reference_path = output / "timed-reference-manifest-v1.draft.json"
        prediction_path = output / "prediction-manifest-v1.draft.json"
        with self.assertRaises(evaluator.VideoEvalError):
            evaluator._load_references(reference_path, self.groups)
        with self.assertRaises(evaluator.VideoEvalError):
            evaluator._load_predictions(prediction_path, self.groups, {}, self.experiment_document)

    def test_synthetic_validator_rejection_stops_before_any_template_output(self):
        with patch.object(templates.evaluator, "_validated_split",
                          side_effect=evaluator.VideoEvalError("synthetic strict gate rejection")) as validate:
            with self.assertRaises(evaluator.VideoEvalError):
                templates.create_templates(
                    self.dataset, self.metadata, self.experiment, self.out_parent)
        validate.assert_called_once()
        self.assertEqual(list(self.out_parent.iterdir()), [])

    def test_real_validator_rejects_missing_dataset_and_no_rights_metadata_without_output(self):
        # This uses the real strict _validated_split/crop_eval path with a
        # synthetic empty dataset, no rights evidence, and no review events.
        with self.assertRaises((prep.InputError, evaluator.VideoEvalError,
                                evaluator.crop_eval.EvalError)):
            templates.create_templates(self.dataset, self.metadata, self.experiment,
                                       self.out_parent)
        self.assertEqual(list(self.out_parent.iterdir()), [])

    def test_refuses_output_inside_dataset_metadata_experiment_and_declared_ref_prediction_roots(self):
        refs_root = self.root / "future-references"
        preds_root = self.root / "future-predictions"
        refs_root.mkdir(); preds_root.mkdir()
        protected_cases = [self.dataset, self.dataset_parent, self.metadata_parent,
                           self.experiment_dir, refs_root, preds_root]
        with patch.object(templates.evaluator, "_validated_split",
                          return_value=self.validated_result) as validate:
            for target_parent in protected_cases:
                with self.subTest(target=target_parent.name):
                    with self.assertRaises(templates.TemplateError):
                        templates.create_templates(self.dataset, self.metadata, self.experiment,
                                                   target_parent, protect_dirs=[refs_root, preds_root])
            validate.assert_not_called()
        for root in (refs_root, preds_root):
            nested_output = root / "nested-output"
            nested_output.mkdir()
            with self.assertRaises(templates.TemplateError):
                templates.create_templates(self.dataset, self.metadata, self.experiment,
                                           nested_output, protect_dirs=[refs_root, preds_root])

    def test_unc_output_is_rejected_before_path_resolution(self):
        with patch.object(templates.Path, "resolve", side_effect=AssertionError("path resolution should not run")):
            with self.assertRaises(templates.TemplateError):
                templates._safe_output_parent(Path(r"\\server\share\phase5-drafts"), [])

    def test_local_junction_output_is_rejected_before_path_resolution(self):
        junction = self.root / "output-junction"
        try:
            junction.symlink_to(self.out_parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name == "nt":
                command = f'mklink /J "{junction}" "{self.out_parent}"'
                result = subprocess.run(["cmd.exe", "/d", "/c", command],
                                         capture_output=True, text=True, check=False)
                if result.returncode != 0 or not junction.exists():
                    self.skipTest("Cannot create a synthetic local junction on this Windows account")
            else:
                self.skipTest("Cannot create a synthetic local symlink")
        try:
            with patch.object(templates.Path, "resolve", side_effect=AssertionError("reparse path must be rejected before resolve")):
                with self.assertRaises(templates.TemplateError):
                    templates._safe_output_parent(junction / "nested", [])
        finally:
            if junction.exists() or junction.is_symlink():
                os.rmdir(junction)

    def test_resolved_unc_and_remote_drive_are_rechecked_before_directory_access(self):
        with patch.object(templates.Path, "resolve", return_value=Path(r"\\server\share\drafts")):
            with self.assertRaises(templates.TemplateError):
                templates._safe_output_parent(self.out_parent, [])
        with patch.object(templates.Path, "resolve", return_value=Path(r"Z:\remote\drafts")), \
                patch.object(templates, "_windows_drive_type", side_effect=[3, templates.DRIVE_REMOTE]) as drive_type:
            with self.assertRaises(templates.TemplateError):
                templates._safe_output_parent(self.out_parent, [])
        self.assertEqual(drive_type.call_count, 2)

    def test_remote_mapped_drive_is_rejected_without_opening_it(self):
        with patch.object(templates, "_windows_drive_type", return_value=templates.DRIVE_REMOTE) as drive_type:
            with self.assertRaises(templates.TemplateError):
                templates._safe_output_parent(self.out_parent, [])
        drive_type.assert_called_once()

    def test_configured_onedrive_output_is_rejected_but_other_local_output_is_allowed(self):
        onedrive = self.root / "cloud-sync-root"
        inside = onedrive / "drafts"
        inside.mkdir(parents=True)
        with patch.dict(os.environ, {"OneDrive": str(onedrive),
                                     "OneDriveCommercial": "", "OneDriveConsumer": ""},
                        clear=False):
            with self.assertRaises(templates.TemplateError):
                templates._safe_output_parent(inside, [])
            safe = templates._safe_output_parent(self.out_parent, [])
        self.assertEqual(safe, self.out_parent.resolve())

    def test_unique_atomic_output_does_not_overwrite_prior_template(self):
        first = self._create()
        saved = (first / "timed-reference-manifest-v1.draft.json").read_bytes()
        second = self._create()
        self.assertNotEqual(first, second)
        self.assertEqual((first / "timed-reference-manifest-v1.draft.json").read_bytes(), saved)
        self.assertTrue((second / "prediction-manifest-v1.draft.json").is_file())
        self.assertFalse(any(path.name.startswith(".phase5-template-")
                             for path in self.out_parent.iterdir()))


if __name__ == "__main__":
    unittest.main()
