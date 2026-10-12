import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import hardsub2srt as hs
import ocr_experiment as prep
import ocr_review
import ocr_video_eval as video_eval
import phase5_execution as execution
from offline_ocr_profile import OfflineProfileError, load_profile
from test_phase5_crop_eval_and_phase6_release import SyntheticPhase5Inputs


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value: dict) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


class Phase5ExecutionHarness:
    """Synthetic Phase 2, split, references, media and model bytes only."""

    def __init__(self, root: Path, output_parent: Path):
        self.root = root
        (root / "inputs").mkdir()
        self.inputs = SyntheticPhase5Inputs(root / "inputs")
        self.refs_dir = root / "refs"
        self.refs_dir.mkdir()
        split = json.loads(self.inputs.split_path.read_text(encoding="utf-8"))
        group_map = {}
        for row in split["rows"]:
            group_map[row["group_id"]] = {
                "source_id": row["source_id"], "episode_id": row["episode_id"],
                "split": row["split"],
            }
        self.media = []
        ref_records = []
        for index, (group, values) in enumerate(sorted(group_map.items()), 1):
            media_path = root / f"synthetic-media-{index}.mkv"
            media_bytes = f"SYNTHETIC MEDIA {index}".encode()
            media_path.write_bytes(media_bytes)
            self.media.append({"source_id": values["source_id"],
                               "episode_id": values["episode_id"],
                               "path": str(media_path), "sha256": sha(media_bytes)})
            reference = self.refs_dir / f"reference-{index}.srt"
            reference_bytes = (
                f"1\n00:00:01,000 --> 00:00:02,000\nSYNTHETIC LABEL {index}\n\n"
            ).encode()
            reference.write_bytes(reference_bytes)
            review_date = datetime.now(timezone.utc).isoformat()
            ref_records.append({
                "source_id": values["source_id"], "episode_id": values["episode_id"],
                "split": values["split"], "language": "en",
                "media_sha256": sha(media_bytes),
                "reference_srt_path": reference.name,
                "reference_srt_sha256": sha(reference_bytes),
                "alignment_review": {"status": "reviewed", "reviewer": "synthetic-reviewer",
                                     "reviewed_at": review_date, "media_sha256": sha(media_bytes)},
            })
        self.references_path = self.refs_dir / "timed-reference-manifest-v1.json"
        write_json(self.references_path, {"schema_version": 1, "records": ref_records})
        self.profiles = []
        for engine_index, engine_id in enumerate(("synthetic-baseline", "synthetic-candidate"), 1):
            model_dir = root / f"model-{engine_index}"
            model_dir.mkdir()
            model_file = model_dir / "recognition.pth"
            model_file.write_bytes(f"SYNTHETIC MODEL WEIGHTS {engine_index}".encode())
            profile_path = root / f"profile-{engine_index}.json"
            profile_raw = write_json(profile_path, {
                "schema_version": 1, "profile_id": "hardsub2srt-easyocr-primary-v1",
                "device_preference": "cpu", "languages": ["tr", "en"],
                "easyocr_model_dir": str(model_dir),
                "easyocr_assets": [{"path": str(model_file), "sha256": sha(model_file.read_bytes())}],
                "secondary_engine": "none", "secondary_assets": [],
            })
            self.profiles.append({"engine_id": engine_id, "profile_path": str(profile_path),
                                  "profile_sha256": sha(profile_raw)})
        self.plan_path = root / "phase5-execution-plan.json"
        self.rights_path = root / "rights-receipt.json"
        self.output_parent = output_parent
        self.plan_doc = {
            "schema_version": 1, "run_id": "synthetic-execution-01",
            "dataset": str(self.inputs.dataset), "metadata": str(self.inputs.metadata_path),
            "split": str(self.inputs.split_path),
            "experiment": str(self.inputs.experiment / "experiment-report.json"),
            "references": str(self.references_path), "media": self.media,
            "profiles": self.profiles, "rights_receipt": str(self.rights_path),
            "output_parent": str(output_parent),
        }
        write_json(self.plan_path, self.plan_doc)
        groups, _experiment, manifest_hash, metadata_hash, split_hash = video_eval._validated_split(
            Path(self.plan_doc["experiment"]), self.inputs.split_path,
            self.inputs.dataset, self.inputs.metadata_path)
        _refs, refs_hash, _language = video_eval._load_references(self.references_path, groups)
        split_doc = json.loads(self.inputs.split_path.read_text(encoding="utf-8"))
        _evaluation_plan, evaluation_plan_hash = prep.validate_evaluation_plan(
            self.inputs.experiment / "evaluation-plan-v1.json", split_doc,
            manifest_hash, metadata_hash)
        media_hashes = {}
        for record in ref_records:
            key = prep._group_key(record)
            media_hashes[hashlib.sha256(key.encode()).hexdigest()] = record["media_sha256"]
        profile_hashes = {item["engine_id"]: item["profile_sha256"] for item in self.profiles}
        rights = {
            "schema_version": 1, "review_id": "synthetic-rights-review-01",
            "decision": execution.RECEIPT_DECISION,
            "reviewer": {"reviewer_id": "synthetic-reviewer", "authority": "synthetic-test-only"},
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "scope": "synthetic fixtures only; not legal or external rights evidence",
            "dataset_manifest_sha256": manifest_hash,
            "dataset_metadata_sha256": metadata_hash,
            "split_manifest_sha256": split_hash,
            "evaluation_plan_sha256": evaluation_plan_hash,
            "experiment_report_sha256": sha(Path(self.plan_doc["experiment"]).read_bytes()),
            "reference_manifest_sha256": refs_hash,
            "media_sha256s": dict(sorted(media_hashes.items())),
            "profile_sha256s": dict(sorted(profile_hashes.items())),
        }
        self.receipt_raw = write_json(self.rights_path, rights)
        self.trust_path = root / "synthetic-trust.json"
        write_json(self.trust_path, {
            "schema_version": 1, "purpose": "synthetic unit tests only",
            "trusted_receipt_sha256s": [sha(self.receipt_raw)],
        })

    def fake_executor(self, media_path: Path, srt_path: Path, profile: dict) -> None:
        media_raw = media_path.read_bytes()
        srt_raw = b"1\n00:00:01,000 --> 00:00:02,000\nSYNTHETIC PREDICTION\n\n"
        srt_path.write_bytes(srt_raw)
        video_identity = {
            "ad": media_path.name, "boyut": len(media_raw),
            "sha1_ilk_1MB": hashlib.sha1(media_raw[:1024 * 1024]).hexdigest(),
        }
        write_json(srt_path.with_suffix(".stats.json"), {
            "video": video_identity, "device": "CPU (--cpu ile zorlandi)",
            "main_srt_sha256": sha(srt_raw),
            "offline_experiment": {
                "profile_id": profile["runtime"]["profile_id"],
                "profile_sha256": profile["runtime"]["profile_sha256"],
                "primary_runtime_device": "cpu",
                "secondary_engine_configured": "none",
                "secondary_engine_used": None,
                "secondary_engine": "not_invoked",
                "secondary_probe_providers": [],
                "secondary_actual_session_providers": [],
                "onnxruntime_provider_status": "offline profile: second engine disabled",
                "phase6_eligible": False, "rights_verified": False,
            },
        })


class OfflineProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="phase5-offline-profile-synthetic-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _profile(self):
        model = self.root / "model"
        model.mkdir(exist_ok=True)
        weights = model / "recognition.pth"
        weights.write_bytes(b"SYNTHETIC WEIGHTS")
        path = self.root / "profile.json"
        raw = write_json(path, {
            "schema_version": 1, "profile_id": "hardsub2srt-easyocr-primary-v1",
            "device_preference": "gpu_default", "languages": ["tr", "en"],
            "easyocr_model_dir": str(model),
            "easyocr_assets": [{"path": str(weights), "sha256": sha(weights.read_bytes())}],
            "secondary_engine": "none", "secondary_assets": [],
        })
        return path, raw, weights

    def test_exact_local_asset_inventory_loads(self):
        path, raw, weights = self._profile()
        result = load_profile(path)
        self.assertEqual(result["profile_sha256"], sha(raw))
        self.assertEqual(result["easyocr_assets"][0]["path"], str(weights.resolve()))

    def test_hash_drift_or_unlisted_model_file_blocks_profile(self):
        path, _raw, weights = self._profile()
        weights.write_bytes(b"CHANGED WEIGHTS")
        with self.assertRaisesRegex(OfflineProfileError, "SHA-256 mismatch"):
            load_profile(path)
        path, _raw, _weights = self._profile()
        (self.root / "model" / "extra.pth").write_bytes(b"UNPINNED WEIGHTS")
        with self.assertRaisesRegex(OfflineProfileError, "Every file"):
            load_profile(path)

    def test_profile_identifier_cannot_claim_an_unknown_engine_composition(self):
        path, _raw, _weights = self._profile()
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["profile_id"] = "arbitrary-command-backed-engine"
        write_json(path, profile)
        with self.assertRaisesRegex(OfflineProfileError, "profile_id does not match"):
            load_profile(path)

    def test_disabled_second_engine_does_not_enter_any_fallback_chain(self):
        profile = {"secondary_engine": "none"}
        with patch.object(hs, "_OFFLINE_EXPERIMENT_PROFILE", profile), \
                patch.object(hs, "_SECOND", {"state": "untried", "name": None, "fn": None}), \
                patch.object(hs, "note") as note:
            result = hs.get_second_engine(["tr", "en"], False, "paddle")
        self.assertEqual(result["state"], "failed")
        self.assertIsNone(result["fn"])
        note.assert_called_once()

    def test_product_cli_opt_in_subcommand_dispatches_only_to_phase5_runner(self):
        from types import SimpleNamespace

        calls = []
        fake_runner = SimpleNamespace(main=lambda argv: calls.append(argv) or 0)
        with patch.dict("sys.modules", {"phase5_execution": fake_runner}), \
                self.assertRaises(SystemExit) as raised:
            hs.main(["phase5-run", "--plan", "C:\\synthetic\\plan.json"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(calls, [["--plan", "C:\\synthetic\\plan.json"]])

    def test_ffmpeg_protocol_allowlist_is_local_only_and_ordinary_mode_is_unchanged(self):
        with patch.object(hs, "_OFFLINE_EXPERIMENT_PROFILE", None):
            self.assertEqual(hs._offline_media_protocol_args(), [])
            self.assertEqual(hs._offline_media_protocol_args(pipe_output=True), [])
        with patch.object(hs, "_OFFLINE_EXPERIMENT_PROFILE", {"profile_id": "synthetic"}):
            self.assertEqual(hs._offline_media_protocol_args(),
                             ["-protocol_whitelist", "file"])
            self.assertEqual(hs._offline_media_protocol_args(pipe_output=True),
                             ["-protocol_whitelist", "file,pipe"])

    def test_offline_primary_loader_pins_model_dir_and_disables_downloads(self):
        from types import SimpleNamespace

        captured = {}

        class Reader:
            device = "cpu"

        def construct(languages, **kwargs):
            captured["languages"] = languages
            captured.update(kwargs)
            return Reader()

        profile = {"easyocr_model_dir": "C:\\synthetic\\models"}
        module = SimpleNamespace(Reader=construct)
        with patch.object(hs, "_OFFLINE_EXPERIMENT_PROFILE", profile), \
                patch.object(hs, "_OFFLINE_PROFILE_SHA256", "a" * 64), \
                patch.object(hs, "_OFFLINE_RUNTIME", {}), \
                patch.object(hs, "_verify_offline_profile_current"):
            result = hs._create_primary_reader(module, ["tr", "en"], False)
        self.assertIsInstance(result, Reader)
        self.assertEqual(captured["model_storage_directory"], profile["easyocr_model_dir"])
        self.assertIs(captured["download_enabled"], False)
        self.assertIs(captured["gpu"], False)

    def test_ppocrv5_uses_only_explicit_pinned_paths(self):
        from types import SimpleNamespace

        seen = {}

        class FakeEngine:
            def get_providers(self):
                return ["CPUExecutionProvider"]

            def __call__(self, _image):
                return SimpleNamespace(txts=["synthetic"])

        def construct(params):
            seen.update(params)
            return FakeEngine()

        fake_module = SimpleNamespace(
            RapidOCR=construct,
            OCRVersion=SimpleNamespace(PPOCRV5="v5"),
            ModelType=SimpleNamespace(MOBILE="mobile"),
        )
        with tempfile.TemporaryDirectory(prefix="phase5-ppocr-synthetic-") as td:
            root = Path(td)
            paths = {}
            for role, suffix in (("det", ".onnx"), ("rec", ".onnx"), ("dictionary", ".txt")):
                path = root / f"{role}{suffix}"
                path.write_bytes(f"SYNTHETIC {role}".encode())
                paths[role] = str(path)
            profile = {"secondary_engine": "ppocrv5", "secondary_assets": paths}
            with patch.dict("sys.modules", {"rapidocr": fake_module}), \
                    patch.object(hs, "_OFFLINE_EXPERIMENT_PROFILE", profile), \
                    patch.object(hs, "_PP5", {"state": "untried", "engine": None}), \
                    patch.object(hs, "_ORT", {"cuda": False}), \
                    patch.object(hs, "_OFFLINE_RUNTIME", {}), \
                    patch.object(hs, "_verify_offline_profile_current"):
                engine = hs._ppocrv5_init()
        self.assertIsNotNone(engine)
        self.assertEqual(seen["Det.model_path"], paths["det"])
        self.assertEqual(seen["Rec.model_path"], paths["rec"])
        self.assertEqual(seen["Rec.rec_keys_path"], paths["dictionary"])
        self.assertNotIn("Download", seen)


class Phase5ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="phase5-execution-synthetic-")
        self.root = Path(self.tmp.name)
        self.output_tmp = tempfile.TemporaryDirectory(prefix="phase5-output-synthetic-")
        self.output_parent = Path(self.output_tmp.name)
        self.harness = Phase5ExecutionHarness(self.root, self.output_parent)
        self.registry_patch = patch.object(execution, "TRUST_REGISTRY", self.harness.trust_path)
        self.registry_patch.start()

    def tearDown(self):
        self.registry_patch.stop()
        for pack in self.harness.inputs.packs:
            with ocr_review._LOCK:
                for review_id, info in list(ocr_review._PACKS.items()):
                    if info.get("path") == pack:
                        ocr_review._PACKS.pop(review_id, None)
        self.output_tmp.cleanup()
        self.tmp.cleanup()

    def test_fake_executor_produces_valid_measurement_manifest_and_nonrelease_receipt(self):
        result = execution._run(self.harness.plan_path, self.harness.fake_executor)
        receipt = json.loads((result / "phase5-execution-receipt-v1.json").read_text(encoding="utf-8"))
        manifest_path = result / "prediction-manifest-v1.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "COMPLETED_LOCAL_EXECUTION_NOT_PHASE6_PASS")
        self.assertFalse(receipt["execution"]["rights_verified"])
        self.assertFalse(receipt["execution"]["phase6_eligible"])
        self.assertEqual(receipt["predictions"]["manifest_sha256"], sha(manifest_path.read_bytes()))
        self.assertEqual(len(manifest["records"]), 6)
        groups, experiment_doc, *_ = video_eval._validated_split(
            Path(self.harness.plan_doc["experiment"]), self.harness.inputs.split_path,
            self.harness.inputs.dataset, self.harness.inputs.metadata_path)
        refs, *_ = video_eval._load_references(self.harness.references_path, groups)
        _predictions, profiles, _hash = video_eval._load_predictions(
            manifest_path, groups, refs, experiment_doc)
        self.assertEqual(set(profiles), {"synthetic-baseline", "synthetic-candidate"})
        self.assertFalse(list(self.output_parent.glob("*.tmp")))

    def test_receipt_must_be_pinned_before_any_output_directory_is_created(self):
        write_json(self.harness.trust_path, {
            "schema_version": 1, "purpose": "empty synthetic pin list",
            "trusted_receipt_sha256s": [],
        })
        with self.assertRaisesRegex(execution.ExecutionError, "not pinned"):
            execution._run(self.harness.plan_path, self.harness.fake_executor)
        self.assertEqual(list(self.output_parent.iterdir()), [])

    def test_partial_cli_output_is_removed(self):
        def partial(_media, srt_path, _profile):
            srt_path.write_text("1\n00:00:01,000 --> 00:00:02,000\npartial\n", encoding="utf-8")

        with self.assertRaisesRegex(execution.ExecutionError, "both SRT and stats"):
            execution._run(self.harness.plan_path, partial)
        self.assertEqual(list(self.output_parent.iterdir()), [])

    def test_media_drift_during_run_fails_and_cleans_staging(self):
        calls = 0

        def drift(media, out, profile):
            nonlocal calls
            calls += 1
            self.harness.fake_executor(media, out, profile)
            if calls == 1:
                media.write_bytes(media.read_bytes() + b"changed")

        with self.assertRaisesRegex(execution.ExecutionError, "identity does not match|changed during the run"):
            execution._run(self.harness.plan_path, drift)
        self.assertEqual(list(self.output_parent.iterdir()), [])

    def test_model_asset_drift_during_run_fails_and_cleans_staging(self):
        def drift(_media, out, profile):
            self.harness.fake_executor(_media, out, profile)
            model_path = Path(profile["runtime"]["easyocr_assets"][0]["path"])
            model_path.write_bytes(model_path.read_bytes() + b" changed")

        with self.assertRaisesRegex(execution.ExecutionError,
                                    "profile/model assets changed|asset changed"):
            execution._run(self.harness.plan_path, drift)
        self.assertEqual(list(self.output_parent.iterdir()), [])

    def test_output_parent_inside_media_dataset_or_reference_roots_is_rejected(self):
        protected_output = self.root / "unsafe-output"
        protected_output.mkdir()
        plan = dict(self.harness.plan_doc)
        plan["output_parent"] = str(protected_output)
        write_json(self.harness.plan_path, plan)
        with self.assertRaisesRegex(execution.ExecutionError, "Unsafe output parent"):
            execution._run(self.harness.plan_path, self.harness.fake_executor)
        self.assertEqual(list(protected_output.iterdir()), [])

    def test_unknown_plan_command_and_profile_engine_are_rejected(self):
        changed = dict(self.harness.plan_doc)
        changed["command"] = "format C:"
        write_json(self.harness.plan_path, changed)
        with self.assertRaisesRegex(execution.ExecutionError, "exact fields"):
            execution._run(self.harness.plan_path, self.harness.fake_executor)


if __name__ == "__main__":
    unittest.main()
