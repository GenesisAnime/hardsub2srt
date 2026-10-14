"""Yol karşılaştırması kapısı: çözülmüş/ham karışımını kaynak düzeyinde yakalar.

Ölçülen arıza sınıfı (windows-latest'te 44 kırmızı test) hep aynı **biçimde**
çıktı: karşılaştırmanın bir tarafı `resolve()`/`realpath()` ile normalize
edilmiş, diğer taraf ham. Kapı iki somut biçimi arar:

1. **Aynı ifadede asimetri** — bir operand `resolve()` çağırıyor, diğeri
   çağırmıyor::

       path.resolve(strict=True).is_relative_to(ROOT)   # ölçülen hata (phase5)
       out_path.relative_to(root.resolve())             # aynı biçim

2. **Çözümlemesi çağırana bağlı karşılaştırma** — iki operandı da fonksiyonun
   kendi parametresi olan çağrı; argümanların normalize olduğu yerel olarak
   garanti edilemez::

       def _contained(path, root):      # ölçülen hata (ocr_review)
           path.relative_to(root)

Karşılaştırmayı `path_scope.relative_to(...)` / `path_scope.is_within(...)`
ile yapmak her zaman serbesttir (iki tarafı da kendisi normalize eder).
Bilinçli istisna, aynı satıra ya da bir önceki satıra yazılan
`# path-scope-ok: <gerekçe>` işaretiyle verilir; böylece "iki taraf da
yukarıda/çağrı yerinde çözülmüş" iddiası **yazılı** olur.

Sınır (bilinçli): bu kapı *statik* ve *yereldir*. Bir parametrenin çağrı
yerinde çözülüp çözülmediğini izlemez (2. kural tam da bu yüzden "bilinemez"
der), ayrıca iki ham ad birbirinin farklı gösterimi ise bunu veri akışı analizi
olmadan göremez. Çalışma zamanındaki davranış kilidi bu yüzden ayrı durur:
`tests/test_path_scope.py` (kısa ad ve sembolik bağ gösterimleriyle).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "path-scope-ok"
SKIP_DIRS = {".git", ".github", ".claude", "__pycache__", ".venv", "venv",
             "site-packages", "tests", "Projeler", "docs"}
COMPARISONS = {"relative_to", "is_relative_to"}
NORMALIZERS = {"resolve", "realpath"}


def _call_name(node):
    func = getattr(node, "func", None)
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_normalised(node) -> bool:
    """Operand içinde `resolve()`/`realpath()` çağrısı var mı?"""
    return any(isinstance(inner, ast.Call) and _call_name(inner) in NORMALIZERS
               for inner in ast.walk(node))


def _plain_name(node):
    return node.id if isinstance(node, ast.Name) else None


def _marked(lines, lineno) -> bool:
    if 0 <= lineno - 1 < len(lines) and MARKER in lines[lineno - 1]:
        return True
    return 0 <= lineno - 2 < len(lines) and MARKER in lines[lineno - 2]


def _comparison(node):
    """(sol, sağ) ya da None: `relative_to`/`is_relative_to` çağrısı mı?"""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in COMPARISONS or not node.args:
        return None
    return node.func.value, node.args[0]


def _parameter_names(func) -> set[str]:
    args = func.args
    names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def scan_source(source: str):
    """(satır, ifade) çiftleri: işaretsiz riskli karşılaştırmalar."""
    lines = source.splitlines()
    found = []

    def visit(node, parameters):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parameters = _parameter_names(node)
        comparison = _comparison(node)
        if comparison is not None:
            left, right = comparison
            if _is_normalised(left) != _is_normalised(right):
                found.append((node.lineno, ast.unparse(node)))
            elif _plain_name(left) in parameters and _plain_name(right) in parameters:
                # Çözümlemesi tamamen çağrı yerine bağlı: yerelde kanıtlanamaz.
                found.append((node.lineno, ast.unparse(node)))
        for child in ast.iter_child_nodes(node):
            visit(child, parameters)

    visit(ast.parse(source), frozenset())
    return [(lineno, expression) for lineno, expression in found
            if not _marked(lines, lineno)]


def scan_repository():
    """Ürün modüllerindeki işaretsiz riskli karşılaştırmalar."""
    violations = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if SKIP_DIRS.intersection(part.lower() for part in relative.parts):
            continue
        if path.name == "path_scope.py":
            continue          # normalize etmenin tek meşru yeri
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for lineno, expression in scan_source(source):
            violations.append((relative.as_posix(), lineno, expression))
    return violations


def test_no_unmarked_risky_path_comparison():
    violations = scan_repository()
    detail = "\n".join(f"  {name}:{lineno}: {expression}"
                       for name, lineno, expression in violations)
    assert not violations, (
        "Riskli yol karşılaştırması bulundu (normalize/ham karışımı):\n"
        f"{detail}\n"
        "`path_scope.relative_to(...)`/`path_scope.is_within(...)` kullanın; iki taraf "
        f"da gerçekten çözülmüşse `# {MARKER}: <gerekçe>` işareti ekleyin."
    )


def test_gate_flags_the_measured_failure_shape():
    """Kapı kendi kendini doğrulasın: ölçülen iki arıza biçimini yakalamalı."""
    # phase5_execution.py:506 (windows-latest 44 kırmızı test)
    assert scan_source("path.resolve(strict=True).is_relative_to(ROOT)\n") == [
        (1, "path.resolve(strict=True).is_relative_to(ROOT)")]
    assert scan_source("out_path.relative_to(root.resolve())\n") == [
        (1, "out_path.relative_to(root.resolve())")]
    # ocr_review._contained — iki operand da parametre
    source = "def _contained(path, root):\n    path.relative_to(root)\n"
    assert scan_source(source) == [(2, "path.relative_to(root)")]


def test_gate_accepts_the_shared_helper_and_symmetric_forms():
    for source in ("path_scope.relative_to(resolved, root)\n",
                   "path_scope.is_within(path, root)\n",
                   "path.resolve(strict=True).relative_to(root.resolve())\n",
                   "raw.relative_to(raw_root)\n",
                   "def f(path, root):\n    return resolved_here.relative_to(root)\n",
                   "os.path.abspath(a).relative_to(os.path.abspath(b))\n"):
        assert scan_source(source) == [], source


def test_gate_honours_a_documented_exception():
    assert scan_source("# path-scope-ok: kök yukarıda resolve edildi\n"
                       "output.relative_to(dataset)\n") == []
    assert scan_source("def _inside(path, root):\n"
                       "    # path-scope-ok: çağrı yerleri iki tarafı da çözüyor\n"
                       "    path.relative_to(root)\n") == []


if __name__ == "__main__":
    for name, lineno, expression in scan_repository():
        print(f"{name}:{lineno}: {expression}")
