import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import ai_ocr_review
import ocr_review
from review_bundle import build_review_pack


class AiOcrReviewPathSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()   # kanonik kök (kısa ad/bağ karışmasın)
        self.srt = self.root / "synthetic.srt"
        self.srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nsynthetic\n\n", encoding="utf-8")
        self.crop = np.full((24, 120, 3), 90, dtype=np.uint8)
        self.pack, _manifest = build_review_pack(
            self.srt,
            [{"start_ms": 1000, "end_ms": 2000, "text": "synthetic", "confidence": 0.8,
              "frame_index": 30, "frame_ms": 1500, "band_y": 10, "band_height": 20}],
            {"synthetic": True}, lambda *_args: self.crop)
        self.review_id, self.public = ocr_review.open_pack(str(self.pack))

    def tearDown(self):
        self.temp.cleanup()

    def test_reparse_point_directory_is_rejected_before_resolve_or_mkdir(self):
        target = self.pack / "ai-ocr-review"
        original = Path.lstat

        def fake_lstat(path):
            if path == target:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path)

        with patch.object(Path, "lstat", fake_lstat):
            with self.assertRaisesRegex(ocr_review.ReviewError, "junction"):
                ai_ocr_review._dir({"pack": self.pack})
        self.assertFalse(target.exists())

    def test_dangling_or_live_journal_symlink_is_rejected_without_touching_target(self):
        review_dir = ai_ocr_review._dir({"pack": self.pack})
        external = self.root / "external.jsonl"
        external.write_text("keep", encoding="utf-8")
        journal = review_dir / "proposals.jsonl"
        try:
            journal.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(ocr_review.ReviewError):
            ai_ocr_review._events({"pack": self.pack})
        with self.assertRaises(ocr_review.ReviewError):
            ai_ocr_review._open_append_nofollow(journal)
        self.assertEqual(external.read_text(encoding="utf-8"), "keep")
        journal.unlink()
        dangling = self.root / "missing-target.jsonl"
        try:
            journal.symlink_to(dangling)
        except (OSError, NotImplementedError):
            self.skipTest("dangling symlink creation is unavailable")
        with self.assertRaises(ocr_review.ReviewError):
            ai_ocr_review._events({"pack": self.pack})
        with self.assertRaises(ocr_review.ReviewError):
            ai_ocr_review._open_append_nofollow(journal)
        self.assertFalse(dangling.exists())

    def test_hard_linked_proposal_journal_is_rejected_before_read_and_append(self):
        review_dir = ai_ocr_review._dir({"pack": self.pack})
        journal = review_dir / "proposals.jsonl"
        alias = self.root / "journal-alias.jsonl"
        journal.write_text("must not be followed or changed\n", encoding="utf-8")
        try:
            os.link(journal, alias)
        except OSError:
            self.skipTest("hard-link creation is unavailable")
        with self.assertRaisesRegex(ocr_review.ReviewError, "hard link"):
            ai_ocr_review._events({"pack": self.pack})
        with self.assertRaises(ocr_review.ReviewError):
            ai_ocr_review._open_append_nofollow(journal)
        self.assertEqual(alias.read_text(encoding="utf-8"), "must not be followed or changed\n")

    def test_atomic_file_publication_never_overwrites_racing_target(self):
        staging = self.root / "staging"
        target = self.root / "target"
        staging.write_text("ours", encoding="utf-8")
        real_link = os.link

        def racing_link(src, dst, *, follow_symlinks=True):
            Path(dst).write_text("other writer", encoding="utf-8")
            raise FileExistsError("simulated publication race")

        with patch.object(ai_ocr_review.os, "link", racing_link):
            with self.assertRaises(ocr_review.ReviewError):
                ai_ocr_review._publish_file_noreplace(staging, target)
        self.assertEqual(target.read_text(encoding="utf-8"), "other writer")
        self.assertEqual(staging.read_text(encoding="utf-8"), "ours")

    def test_post_publication_substitution_is_not_unlinked_by_path(self):
        staging = self.root / "staging-postcheck"
        target = self.root / "target-postcheck"
        staging.write_text("ours", encoding="utf-8")
        real_lstat = ai_ocr_review._entry_lstat
        calls = 0

        def substitute(path):
            nonlocal calls
            if Path(path) == target:
                calls += 1
                if calls == 2:
                    target.unlink()
                    target.write_text("concurrent replacement", encoding="utf-8")
            return real_lstat(path)

        with patch.object(ai_ocr_review, "_entry_lstat", substitute):
            with self.assertRaises(ocr_review.ReviewError):
                ai_ocr_review._publish_file_noreplace(staging, target)
        self.assertEqual(target.read_text(encoding="utf-8"), "concurrent replacement")

    def test_failed_sidecar_publication_never_unlinks_a_substituted_zip(self):
        cue_id = self.public["cues"][0]["cue_id"]
        request_id = "a" * 32
        directory = self.pack / "ai-ocr-review"
        sidecar = directory / f"{request_id}.local.json"
        zip_path = directory / f"{request_id}.zip"
        real_link = os.link
        class FixedUuid:
            hex = request_id
        with patch.object(ai_ocr_review.uuid, "uuid4", return_value=FixedUuid()):
            def race_sidecar(src, dst, *, follow_symlinks=True):
                if Path(dst) == sidecar:
                    zip_path.unlink()
                    zip_path.write_text("concurrent replacement", encoding="utf-8")
                    sidecar.write_text("someone else's file", encoding="utf-8")
                    raise FileExistsError("simulated sidecar race")
                return real_link(src, dst, follow_symlinks=follow_symlinks)
            with patch.object(ai_ocr_review.os, "link", race_sidecar):
                with self.assertRaises(ocr_review.ReviewError):
                    ai_ocr_review.create_package(self.review_id, [cue_id])
        self.assertEqual(zip_path.read_text(encoding="utf-8"), "concurrent replacement")
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "someone else's file")


if __name__ == "__main__":
    unittest.main()
