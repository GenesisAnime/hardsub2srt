"""Regression tests for duplicate application of user OCR correction rules.

Extract only the relevant functions via AST to avoid initializing OCR engines.
"""
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "hardsub2srt.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
            and node.name in {"_kullanici_uygula", "_duzelt_blok_metni"}]
namespace = {}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)


class CorrectionRuleTests(unittest.TestCase):
    def test_rule_only_once_across_two_passes(self):
        namespace["_satir_sfx_temizle"] = lambda text, *args: (text, [])
        namespace["_duzelt_metin"] = lambda text, *args: (text, [])
        result, changes = namespace["_duzelt_blok_metni"]("a", {}, [("a", "aa")])
        self.assertEqual(result, "aa")
        self.assertEqual(len(changes), 1)

    def test_rule_can_first_match_after_postfix(self):
        namespace["_satir_sfx_temizle"] = lambda text, *args: (text, [])
        namespace["_duzelt_metin"] = lambda text, *args: ("abc", [])
        result, changes = namespace["_duzelt_blok_metni"]("xyz", {}, [("abc", "ok")])
        self.assertEqual(result, "ok")
        self.assertEqual(len(changes), 1)

    def test_standalone_call_keeps_legacy_behavior(self):
        result, changes = namespace["_kullanici_uygula"]("can can", [("can", "Can")])
        self.assertEqual(result, "Can Can")
        self.assertEqual(len(changes), 1)


if __name__ == "__main__":
    unittest.main()
