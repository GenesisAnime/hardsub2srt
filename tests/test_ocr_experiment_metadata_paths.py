import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import ocr_experiment as prep


class MetadataEvidencePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hardsub-metadata-paths-")
        self.root = Path(self.tmp.name)
        self.metadata_dir = self.root / "metadata"
        self.metadata_dir.mkdir()
        self.metadata = self.metadata_dir / "dataset-metadata.json"
        self.rows = [{"cue_id": "synthetic-cue-1"}]

    def tearDown(self):
        self.tmp.cleanup()

    def _write_metadata(self, evidence_path: str, evidence_bytes: bytes):
        record = {
            "cue_id": "synthetic-cue-1",
            "source_id": "synthetic-source",
            "episode_id": "synthetic-episode",
            "license": {
                "status": "user_owned",
                "identifier": "synthetic-rights-record",
                "evidence_path": evidence_path,
                "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                "human_review": {"status": "pending", "reviewer": "", "reviewed_at": ""},
            },
        }
        self.metadata.write_text(json.dumps({"schema_version": 1, "records": [record]}),
                                 encoding="utf-8")

    def test_normal_nested_evidence_file_is_still_accepted(self):
        evidence = self.metadata_dir / "rights" / "permission.txt"
        evidence.parent.mkdir()
        evidence_bytes = b"synthetic rights evidence"
        evidence.write_bytes(evidence_bytes)
        self._write_metadata("rights/permission.txt", evidence_bytes)
        result = prep.validate_metadata(self.metadata, self.rows)
        self.assertEqual(result["synthetic-cue-1"]["license"]["evidence_path"],
                         "rights/permission.txt")

    def test_evidence_symlink_is_rejected_before_following_external_target(self):
        outside = self.root / "outside-rights.txt"
        outside.write_bytes(b"synthetic external evidence")
        evidence = self.metadata_dir / "rights.txt"
        try:
            evidence.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Cannot create a synthetic local symlink: {exc}")
        self._write_metadata("rights.txt", outside.read_bytes())
        with self.assertRaisesRegex(prep.InputError, "symlink or reparse point"):
            prep.validate_metadata(self.metadata, self.rows)

    def test_dangling_evidence_symlink_is_rejected(self):
        evidence = self.metadata_dir / "missing-rights.txt"
        try:
            evidence.symlink_to(self.root / "missing-target.txt")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Cannot create a synthetic local symlink: {exc}")
        self._write_metadata("missing-rights.txt", b"not present")
        with self.assertRaisesRegex(prep.InputError, "symlink or reparse point"):
            prep.validate_metadata(self.metadata, self.rows)

    def test_evidence_parent_junction_or_symlink_is_rejected(self):
        outside_dir = self.root / "outside-rights"
        outside_dir.mkdir()
        evidence_bytes = b"synthetic external evidence"
        (outside_dir / "permission.txt").write_bytes(evidence_bytes)
        linked_dir = self.metadata_dir / "rights"
        try:
            linked_dir.symlink_to(outside_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Cannot create a synthetic local symlink: {exc}")
        self._write_metadata("rights/permission.txt", evidence_bytes)
        with self.assertRaisesRegex(prep.InputError, "symlink or reparse point"):
            prep.validate_metadata(self.metadata, self.rows)

    def test_metadata_symlink_is_rejected_before_stat(self):
        outside = self.root / "outside-metadata.json"
        outside.write_text("{}", encoding="utf-8")
        self.metadata.unlink(missing_ok=True)
        try:
            self.metadata.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Cannot create a synthetic local symlink: {exc}")
        with self.assertRaisesRegex(prep.InputError, "junction kabul edilmez"):
            prep.validate_metadata(self.metadata, self.rows)


if __name__ == "__main__":
    unittest.main()
