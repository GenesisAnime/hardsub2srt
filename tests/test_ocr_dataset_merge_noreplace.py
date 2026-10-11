import ctypes
import errno
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ocr_dataset_merge import MergeError, _publish_noreplace


class _FakeNativeFunction:
    def __init__(self, result=0, error_number=0):
        self.result = result
        self.error_number = error_number
        self.args_seen = None

    def __call__(self, *args):
        self.args_seen = args
        ctypes.set_errno(self.error_number)
        return self.result


class AtomicNoReplacePublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hardsub-noreplace-")
        self.root = Path(self.tmp.name)
        self.staging = self.root / "verified-ocr-dataset-composite-staging-synthetic"
        self.target = self.root / "verified-ocr-dataset-composite-synthetic"
        self.staging.mkdir()
        (self.staging / "staging.txt").write_text("staging", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_windows_native_rename_preserves_existing_target(self):
        if os.name != "nt":
            self.skipTest("Windows native rename semantics require Windows")
        self.target.mkdir()
        (self.target / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(MergeError):
            _publish_noreplace(self.staging, self.target, platform_name="win32",
                               windows_rename=os.rename)
        self.assertEqual((self.target / "keep.txt").read_text(encoding="utf-8"), "keep")
        self.assertTrue((self.staging / "staging.txt").is_file())

    def test_linux_renameat2_uses_noreplace_flag_and_paths(self):
        function = _FakeNativeFunction()
        _publish_noreplace(self.staging, self.target, platform_name="linux",
                           libc_loader=lambda: SimpleNamespace(renameat2=function))
        self.assertEqual(function.args_seen,
                         (-100, os.fsencode(self.staging), -100, os.fsencode(self.target), 1))

    def test_linux_existing_target_errno_fails_without_replacement(self):
        self.target.mkdir()
        (self.target / "keep.txt").write_text("keep", encoding="utf-8")
        function = _FakeNativeFunction(result=-1, error_number=errno.EEXIST)
        with self.assertRaisesRegex(MergeError, "Hedef mevcut"):
            _publish_noreplace(self.staging, self.target, platform_name="linux",
                               libc_loader=lambda: SimpleNamespace(renameat2=function))
        self.assertEqual((self.target / "keep.txt").read_text(encoding="utf-8"), "keep")
        self.assertTrue((self.staging / "staging.txt").is_file())

    def test_linux_missing_primitive_fails_closed(self):
        with self.assertRaisesRegex(MergeError, "kullanılamıyor"):
            _publish_noreplace(self.staging, self.target, platform_name="linux",
                               libc_loader=lambda: SimpleNamespace())
        self.assertTrue(self.staging.is_dir())

    def test_unsupported_platform_fails_closed(self):
        with self.assertRaisesRegex(MergeError, "tanımlı değil"):
            _publish_noreplace(self.staging, self.target, platform_name="freebsd")
        self.assertTrue(self.staging.is_dir())

    def test_macos_renamex_np_uses_exclusive_flag(self):
        function = _FakeNativeFunction()
        _publish_noreplace(self.staging, self.target, platform_name="darwin",
                           libc_loader=lambda: SimpleNamespace(renamex_np=function))
        self.assertEqual(function.args_seen,
                         (os.fsencode(self.staging), os.fsencode(self.target), 0x00000004))


if __name__ == "__main__":
    unittest.main()
