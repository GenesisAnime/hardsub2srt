import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import ocr_experiment as prep
import ocr_dataset_metadata
import ocr_review
from ocr_dataset_merge import MergeError, merge_exports
from review_bundle import build_review_pack


class DatasetMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hardsub-merge-synthetic-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _export(self, number, duplicate_cue_id=None):
        run = self.root / f"synthetic-run-{number}"
        run.mkdir()
        srt = run / "synthetic-input.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nSYNTHETIC OCR LABEL\n\n",
            encoding="utf-8")
        cue = {"start_ms": 1000, "end_ms": 2000,
               "text": "SYNTHETIC OCR LABEL", "confidence": 0.91,
               "frame_index": 24, "frame_ms": 1000,
               "mapping_status": "exact_original_ocr_segment_needs_visual_confirmation",
               "source_region": "main_band", "band_y": 0, "band_height": 40}
        crop = np.zeros((40, 240, 3), dtype=np.uint8)
        crop[:, :, 0] = number * 20
        crop[10:30, 20:220] = 255
        pack, _manifest = build_review_pack(
            srt, [cue], {"tool_version": "synthetic-test"},
            lambda *_args: crop.copy())
        if duplicate_cue_id:
            manifest_path = pack / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["job_id"] = duplicate_cue_id.split("-cue-")[0]
            manifest["cues"][0]["cue_id"] = duplicate_cue_id
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        review_id, _ = ocr_review.open_pack(str(pack))
        cue_id = (duplicate_cue_id or _manifest["job_id"] + "-cue-00001")
        ocr_review.append_review(review_id, cue_id, "accepted")
        dataset, count = ocr_review.export_dataset(review_id)
        self.assertEqual(count, 1)
        return pack, dataset, cue_id

    def test_merge_preserves_active_lineage_and_unique_groups(self):
        exports = [self._export(i)[1] for i in (1, 2, 3)]
        source_files_before = {
            path: path.read_bytes()
            for export in exports for path in export.parent.rglob("*") if path.is_file()
        }
        relative = [path.relative_to(self.root) for path in exports]
        target, summary = merge_exports(
            self.root, relative, "verified-ocr-dataset-composite-synthetic")
        rows = prep.validate_dataset(target)
        provenance = prep.verify_phase2_provenance(target, rows)
        self.assertEqual(provenance["status"], "verified_against_parent_review_pack")
        self.assertEqual(provenance["mode"], "composite")
        self.assertEqual(summary["rows"], 3)
        self.assertEqual(provenance["active_rows_verified"], 3)
        metadata = {
            row["cue_id"]: {"source_id": f"synthetic-source-{i}",
                             "episode_id": f"synthetic-episode-{i}"}
            for i, row in enumerate(rows, 1)
        }
        prep.validate_provenance_groups(provenance, metadata, rows)
        self.assertTrue(all(path.read_bytes() == data
                            for path, data in source_files_before.items()))

    def test_metadata_template_accepts_composite_but_stays_blank(self):
        exports = [self._export(i)[1] for i in (1, 2, 3)]
        target, _ = merge_exports(
            self.root, [path.relative_to(self.root) for path in exports],
            "verified-ocr-dataset-composite-metadata-template")
        output = self.root / "SYNTHETIC-dataset-metadata.template.json"
        count, _ = ocr_dataset_metadata.make_template(target, output)
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(count, 3)
        self.assertEqual(len(document["records"]), 3)
        for record in document["records"]:
            self.assertEqual(record["source_id"], "")
            self.assertEqual(record["episode_id"], "")
            self.assertEqual(record["license"]["status"], "")
            self.assertEqual(record["license"]["human_review"]["status"], "pending")

    def test_phase5_preparation_still_requires_metadata(self):
        exports = [self._export(i)[1] for i in (1, 2, 3)]
        target, _ = merge_exports(
            self.root, [path.relative_to(self.root) for path in exports],
            "verified-ocr-dataset-composite-no-rights-metadata")
        blank_metadata = self.root / "SYNTHETIC-blank-metadata.template.json"
        ocr_dataset_metadata.make_template(target, blank_metadata)
        with self.assertRaises(prep.InputError):
            prep.make_artifacts(target, blank_metadata,
                                self.root / "synthetic-experiment", "SYNTHETIC-SEED",
                                "synthetic-baseline", "synthetic-candidate", 0.002, 500)

    def test_merge_refuses_same_pack_twice(self):
        _pack, dataset, _cue_id = self._export(1)
        _other_pack, other_dataset, _other_cue = self._export(2)
        with self.assertRaisesRegex(MergeError, "review-pack'ten birden fazla"):
            merge_exports(self.root,
                          [dataset.relative_to(self.root), dataset.relative_to(self.root),
                           other_dataset.relative_to(self.root)],
                          "verified-ocr-dataset-composite-duplicate-pack")

    def test_merge_refuses_cross_pack_cue_id_collision(self):
        _pack, first, first_cue = self._export(1)
        _other_pack, second, _ = self._export(2, duplicate_cue_id=first_cue)
        with self.assertRaisesRegex(MergeError, "cue_id"):
            merge_exports(self.root,
                          [first.relative_to(self.root), second.relative_to(self.root),
                           self._export(3)[1].relative_to(self.root)],
                          "verified-ocr-dataset-composite-cue-collision")

    def test_source_event_change_invalidates_composite(self):
        sources = [self._export(i) for i in (1, 2, 3)]
        exports = [item[1] for item in sources]
        target, _ = merge_exports(
            self.root, [path.relative_to(self.root) for path in exports],
            "verified-ocr-dataset-composite-stale-source")
        source_pack = sources[0][0]
        with (source_pack / "review-events.jsonl").open("ab") as stream:
            stream.write(b"\n")
        rows = prep.validate_dataset(target)
        provenance = prep.verify_phase2_provenance(target, rows)
        self.assertEqual(provenance["status"], "self_attested")

    def test_source_crop_change_invalidates_composite(self):
        sources = [self._export(i) for i in (1, 2, 3)]
        target, _ = merge_exports(
            self.root, [item[1].relative_to(self.root) for item in sources],
            "verified-ocr-dataset-composite-stale-crop")
        source_export = sources[0][1]
        crop = next((source_export / "crops").glob("*.jpg"))
        crop.write_bytes(crop.read_bytes() + b"altered")
        rows = prep.validate_dataset(target)
        provenance = prep.verify_phase2_provenance(target, rows)
        self.assertEqual(provenance["status"], "self_attested")

    def test_out_of_root_source_export_is_rejected(self):
        sources = [self._export(i)[1] for i in (1, 2, 3)]
        first_relative = sources[0].relative_to(self.root)
        escaping = Path("..") / first_relative
        with self.assertRaisesRegex(MergeError, "source-export"):
            merge_exports(self.root,
                          [escaping, sources[1].relative_to(self.root),
                           sources[2].relative_to(self.root)],
                          "verified-ocr-dataset-composite-path-escape")

    def test_merge_rejects_dangling_lineage_symlink_without_following_it(self):
        sources = [self._export(i)[1] for i in (1, 2, 3)]
        lineage = sources[0] / "source-lineage-v1.json"
        try:
            lineage.symlink_to(self.root / "missing-lineage-target.json")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Cannot create a synthetic local symlink: {exc}")
        with self.assertRaisesRegex(MergeError, "junction kabul edilmez"):
            merge_exports(self.root, [path.relative_to(self.root) for path in sources],
                          "verified-ocr-dataset-composite-lineage-link")

    def test_merge_rejects_dangling_target_symlink_without_following_it(self):
        sources = [self._export(i)[1] for i in (1, 2, 3)]
        target = self.root / "verified-ocr-dataset-composite-target-link"
        try:
            target.symlink_to(self.root / "missing-target", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Cannot create a synthetic local symlink: {exc}")
        with self.assertRaisesRegex(MergeError, "junction"):
            merge_exports(self.root, [path.relative_to(self.root) for path in sources],
                          target.name)

    def test_composite_verifier_rejects_lineage_symlink(self):
        sources = [self._export(i)[1] for i in (1, 2, 3)]
        target, _ = merge_exports(
            self.root, [path.relative_to(self.root) for path in sources],
            "verified-ocr-dataset-composite-lineage-verifier-link")
        lineage = target / "source-lineage-v1.json"
        lineage.unlink()
        outside = self.root / "external-lineage.json"
        outside.write_text("{}", encoding="utf-8")
        try:
            lineage.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Cannot create a synthetic local symlink: {exc}")
        rows = prep.validate_dataset(target)
        provenance = prep.verify_phase2_provenance(target, rows)
        self.assertEqual(provenance["status"], "self_attested")
        self.assertIn("symlink or reparse point", provenance["reason"])

    def test_single_pack_verifier_rejects_crop_link_before_resolving_it(self):
        _pack, export, _cue_id = self._export(1)
        source_pack = export.parent
        manifest = json.loads((source_pack / "manifest.json").read_text(encoding="utf-8"))
        crop_path = source_pack / manifest["cues"][0]["crop_path"]
        outside_crop = self.root / "external-crop.jpg"
        outside_crop.write_bytes(crop_path.read_bytes())
        crop_path.unlink()
        try:
            crop_path.symlink_to(outside_crop)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Cannot create a synthetic local symlink: {exc}")
        rows = prep.validate_dataset(export)
        with self.assertRaisesRegex(prep.InputError, "symlink or reparse point"):
            prep._verify_single_pack_export(export, rows)

    def test_pack_cannot_be_falsely_split_across_multiple_groups(self):
        sources = [self._export(i) for i in (1, 2, 3)]
        target, _ = merge_exports(
            self.root, [item[1].relative_to(self.root) for item in sources],
            "verified-ocr-dataset-composite-fake-group")
        rows = prep.validate_dataset(target)
        provenance = prep.verify_phase2_provenance(target, rows)
        metadata = {row["cue_id"]: {"source_id": "synthetic-same-source",
                                    "episode_id": "synthetic-same-episode"}
                    for row in rows}
        with self.assertRaisesRegex(prep.InputError, "cannot claim the same"):
            prep.validate_provenance_groups(provenance, metadata, rows)


if __name__ == "__main__":
    unittest.main()
