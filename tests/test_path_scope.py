"""Yol kapsama karşılaştırmaları: "tek biçim" varsayımı kırılırsa kırmızı yansın.

Windows'ta aynı dizin iki farklı metinle ifade edilebilir (8.3 kısa ad ya da
sembolik bağ/junction). GitHub koşucusu `TEMP`'i kısa adla verir
(`C:\\Users\\RUNNER~1\\...`), ürün ise yolu `resolve()` ile uzun ada açar; ham
metin karşılaştırması yapan kapılar bu yüzden **geçerli** dosyayı reddediyordu.
Bu dosya hem gerçek ürün kapılarını hem de "dışarı çıkan yol yine reddedilir"
tarafını kilitler.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ocr_dataset_merge
import ocr_experiment
import ocr_review
import ocr_video_eval
import path_scope
import phase5_execution
import translation_evaluation
import ui_server


class NormalizeUnitTests(unittest.TestCase):
    """İki gösterimli kökün kendi başına davranışı (ortamdan bağımsız)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hardsub-path-unit-")
        self.real = Path(self.tmp.name).resolve() / "veri"
        self.real.mkdir()
        self.second = self.real / ".." / "veri"      # aynı dizin, ikinci yazım

    def tearDown(self):
        self.tmp.cleanup()

    def test_normalize_collapses_the_second_spelling_and_is_idempotent(self):
        self.assertEqual(path_scope.normalize(self.second), self.real)
        self.assertEqual(path_scope.normalize(path_scope.normalize(self.second)), self.real)

    def test_relative_to_and_is_within_accept_the_second_spelling(self):
        child = self.real / "crop.png"
        child.write_bytes(b"synthetic")
        self.assertEqual(path_scope.relative_to(child, self.second), Path("crop.png"))
        self.assertTrue(path_scope.is_within(child, self.second))

    def test_outside_and_unresolvable_paths_stay_rejected(self):
        outside = Path(self.tmp.name).resolve() / "disarida"
        outside.mkdir()
        self.assertFalse(path_scope.is_within(outside / "x.txt", self.real))
        self.assertFalse(path_scope.is_within(self.real / "..", self.real))
        with self.assertRaises(ValueError):
            path_scope.relative_to(outside / "x.txt", self.real)


class ProductGuardTests(unittest.TestCase):
    """Ürün kapıları: kök bağ yoluyla yazıldığında da doğru karar vermeli."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hardsub-path-link-")
        self.real = Path(self.tmp.name).resolve() / "veri"
        self.real.mkdir()
        self.alias = Path(self.tmp.name) / "kisa"
        try:
            self.alias.symlink_to(self.real, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:   # ayrıcalık yoksa atla
            self.skipTest(f"sembolik bağ oluşturulamadı: {exc}")
        self.tree = self.real / "pack"
        self.tree.mkdir()
        self.inner = self.tree / "manifest.json"
        self.inner.write_text("{}", encoding="utf-8")
        self.sibling = Path(self.tmp.name).resolve() / "baska-kok"
        self.sibling.mkdir()
        self.outside = self.sibling / "disarida.json"
        self.outside.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_contained_accepts_in_tree_file_written_through_the_link(self):
        through_link = (self.alias / "pack" / "manifest.json").resolve(strict=True)
        self.assertTrue(ocr_review._contained(through_link, self.alias))
        self.assertTrue(ocr_review._contained(through_link, self.real))
        self.assertFalse(ocr_review._contained(self.outside, self.alias))

    def test_root_relative_and_ui_guard_accept_the_link_spelling(self):
        through_link = (self.alias / "pack" / "manifest.json").resolve(strict=True)
        self.assertEqual(ocr_dataset_merge._root_relative(self.alias, through_link),
                         "pack/manifest.json")
        self.assertTrue(ui_server._yol_altinda(self.tree.resolve(strict=True), self.alias))
        self.assertFalse(ui_server._yol_altinda(self.sibling, self.alias))

    def test_escape_is_still_rejected_through_the_link_spelling(self):
        self.assertFalse(ocr_review._contained(self.sibling / "x.json", self.real))
        with self.assertRaises(ocr_dataset_merge.MergeError):
            ocr_dataset_merge._root_relative(self.real, self.outside)

    def test_video_eval_relative_file_accepts_the_link_spelling(self):
        srt = self.real / "pack" / "kaynak.srt"
        srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nsynthetic\n\n", encoding="utf-8")
        self.assertEqual(ocr_video_eval._relative_file(self.alias, "pack/kaynak.srt", "kaynak"),
                         srt.resolve(strict=True))
        self.assertEqual(ocr_video_eval._relative_file(self.real, "pack/kaynak.srt", "kaynak"),
                         srt.resolve(strict=True))
        for escaping in ("../disarida.srt", "pack/../../disarida.srt"):
            with self.assertRaises(ocr_video_eval.VideoEvalError):
                ocr_video_eval._relative_file(self.alias, escaping, "kaynak")


class AmbientSpellingTests(unittest.TestCase):
    """Ortamın kendi ikinci yazımı (GitHub koşucusunda 8.3 kısa ad).

    Yerel makinede `TEMP` kanonikse atlanır; koşucuda çalışır. Ayrıca
    `TEMP`'i kısa adlı bir klasöre verip elle de koşturulabilir.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hardsub-path-ambient-")
        self.root = Path(self.tmp.name)
        if path_scope.normalize(self.root) == self.root:
            self.skipTest("ortam bu yolun ikinci bir yazımını sunmuyor (8.3/bağ)")

    def tearDown(self):
        self.tmp.cleanup()

    def test_contained_accepts_the_ambient_spelling(self):
        target = self.root / "veri" / "manifest.json"
        target.parent.mkdir()
        target.write_text("{}", encoding="utf-8")
        self.assertTrue(ocr_review._contained(target.resolve(strict=True), self.root))
        self.assertFalse(ocr_review._contained((self.root / "..").resolve(), self.root))

    def test_root_relative_helper_accepts_the_ambient_spelling(self):
        target = self.root / "veri" / "manifest.json"
        target.parent.mkdir()
        target.write_text("{}", encoding="utf-8")
        resolved = ocr_experiment._resolve_root_relative(self.root, "veri/manifest.json", "manifest")
        self.assertEqual(resolved, target.resolve(strict=True))

    def test_translation_evaluation_storage_accepts_the_ambient_spelling(self):
        storage = translation_evaluation._safe_root(self.root / "depo", create=True)
        self.assertEqual(storage, path_scope.normalize(self.root / "depo"))
        self.assertEqual(translation_evaluation._child(storage, "kayit.json"),
                         storage / "kayit.json")
        with self.assertRaises(translation_evaluation.EvaluationError):
            translation_evaluation._safe_root(Path("goreli/depo"))

    def test_video_eval_relative_file_accepts_the_ambient_spelling(self):
        srt = self.root / "veri" / "kaynak.srt"
        srt.parent.mkdir()
        srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nsynthetic\n\n", encoding="utf-8")
        self.assertEqual(ocr_video_eval._relative_file(self.root, "veri/kaynak.srt", "kaynak"),
                         srt.resolve(strict=True))
        with self.assertRaises(ocr_video_eval.VideoEvalError):
            ocr_video_eval._relative_file(self.root, "../disarida.srt", "kaynak")

    def test_source_bundle_hashes_the_copy_spelled_by_the_environment(self):
        copy = self.root / "desktop-copy"
        (copy / "tests").mkdir(parents=True)
        (copy / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        (copy / "tests" / "injected.py").write_text("TEST_ONLY = 1\n", encoding="utf-8")
        (copy / "requirements.txt").write_text("synthetic-requirements\n", encoding="utf-8")
        with patch.object(phase5_execution, "ROOT", copy):
            values = phase5_execution._source_hashes()
        self.assertIn("module.py", values)
        self.assertIn("requirements.txt", values)
        self.assertNotIn("tests/injected.py", values)


if __name__ == "__main__":
    unittest.main()
