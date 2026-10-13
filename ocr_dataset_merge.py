#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Combine local Phase 2 exports without dropping their review-pack lineage.

The output is a new crop-only dataset under a common local root. Original
review packs and exports are never modified. Each output cue is revalidated
against its original export and active human review events whenever Phase 5
validates the composite dataset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import ocr_experiment as prep
import path_scope


MAX_MEMBERS = 1000
LINEAGE_SCHEMA_VERSION = 1
LINEAGE_NAME = "source-lineage-v1.json"


class MergeError(ValueError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_reparse(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _check_existing_components(path: Path, allowed_root: Path | None = None) -> None:
    """Fail closed on symlinks/junctions in an existing path."""
    absolute = Path(os.path.abspath(path))
    if allowed_root is not None:
        base = Path(os.path.abspath(allowed_root))
        if not path_scope.is_within(absolute, base):
            raise MergeError("Yol ortak yerel kökün dışında")
        try:
            relative, anchor = absolute.relative_to(base), base
        except ValueError:
            # Aynı ağacın iki gösterimi (8.3 kısa ad/bağ): denetimi normalize
            # et, yürüyüşü gerçek dosya adları üzerinde sürdür.
            relative = path_scope.relative_to(absolute, base)
            anchor = path_scope.normalize(base)
        components = [anchor]
        for part in relative.parts:
            anchor = anchor / part
            components.append(anchor)
    else:
        components = list(reversed(absolute.parents)) + [absolute]
    for component in components:
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise MergeError(f"Yol bileşeni denetlenemedi: {component.name}") from exc
        if _is_reparse(info):
            raise MergeError(f"Sembolik bağlantı/junction kabul edilmez: {component.name}")


def _read_capped(path: Path, cap: int, label: str) -> bytes:
    try:
        if path.stat().st_size > cap:
            raise MergeError(f"{label} {cap} byte sınırını aşıyor")
        with path.open("rb") as stream:
            data = stream.read(cap + 1)
    except OSError as exc:
        raise MergeError(f"{label} okunamadı") from exc
    if len(data) > cap:
        raise MergeError(f"{label} {cap} byte sınırını aşıyor")
    return data


def _lstat_if_present(path: Path):
    """Inspect a path entry without following a symlink or dangling junction."""
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MergeError(f"Yol bileşeni denetlenemedi: {path.name}") from exc


def _publish_noreplace(staging: Path, target: Path, *, platform_name: str | None = None,
                       windows_rename=None, libc_loader=None) -> None:
    """Atomically publish a directory only if the destination does not exist.

    Supported primitives are intentionally explicit. A platform or filesystem
    without a reliable exclusive rename fails closed instead of falling back to
    a check-then-rename sequence that can overwrite a concurrently-created dir.
    Optional callables let tests exercise each native branch on Windows.
    """
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == "win32":
        rename = windows_rename or os.rename
        try:
            rename(staging, target)
        except FileExistsError as exc:
            raise MergeError("Hedef mevcut; üzerine yazılmaz") from exc
        return

    if not platform_name.startswith("linux") and platform_name != "darwin":
        raise MergeError(f"{platform_name} için atomik no-replace yayınlama tanımlı değil")

    import ctypes
    import errno

    if libc_loader is None:
        libc_loader = lambda: ctypes.CDLL(None, use_errno=True)
    try:
        libc = libc_loader()
        if platform_name.startswith("linux"):
            function = getattr(libc, "renameat2", None)
            if function is None:
                raise MergeError("Linux renameat2(RENAME_NOREPLACE) kullanılamıyor; yayınlama durduruldu")
            function.argtypes = [ctypes.c_int, ctypes.c_char_p,
                                 ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            ctypes.set_errno(0)
            result = function(-100, os.fsencode(staging), -100, os.fsencode(target), 1)
        elif platform_name == "darwin":
            function = getattr(libc, "renamex_np", None)
            if function is None:
                raise MergeError("macOS renamex_np(RENAME_EXCL) kullanılamıyor; yayınlama durduruldu")
            function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            ctypes.set_errno(0)
            # <stdio.h> defines RENAME_EXCL as 0x00000004.
            result = function(os.fsencode(staging), os.fsencode(target), 0x00000004)
    except MergeError:
        raise
    except (AttributeError, OSError) as exc:
        raise MergeError("Atomik no-replace yayınlama ilkeli kullanılamadı") from exc

    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise MergeError("Hedef mevcut; üzerine yazılmaz")
    unsupported = {errno.ENOSYS, errno.EINVAL}
    for name in ("ENOTSUP", "EOPNOTSUPP"):
        value = getattr(errno, name, None)
        if value is not None:
            unsupported.add(value)
    if error_number in unsupported:
        raise MergeError("Dosya sistemi atomik no-replace yayınlamayı desteklemiyor; yayınlama durduruldu")
    raise OSError(error_number, os.strerror(error_number), os.fspath(target))


def _relative_source(root: Path, value: Path) -> Path:
    if value.is_absolute() or any(part in ("..", "") for part in value.parts):
        raise MergeError("--source-export yalnız ortak köke göreli, kaçışsız yol alır")
    candidate = root / value
    _check_existing_components(candidate, root)
    try:
        resolved = candidate.resolve(strict=True)
        path_scope.relative_to(resolved, root)
    except (OSError, ValueError) as exc:
        raise MergeError("Kaynak export bulunamadı veya ortak kökün dışına çıkıyor") from exc
    return resolved


def _root_relative(root: Path, path: Path) -> str:
    try:
        return path_scope.relative_to(path, root).as_posix()
    except ValueError as exc:
        raise MergeError("Kaynak yolu ortak yerel kökün dışında") from exc


def _read_source_export(root: Path, raw_path: Path):
    export = _relative_source(root, raw_path)
    if not export.is_dir() or not export.name.startswith("verified-ocr-dataset-"):
        raise MergeError("Her kaynak verified-ocr-dataset-* export klasörü olmalı")
    pack = export.parent
    if (path_scope.normalize(pack) == path_scope.normalize(root)
            or not path_scope.is_within(pack, root)
            or not pack.name.endswith(".review-pack")):
        raise MergeError("Her export doğrudan bir .review-pack klasöründe bulunmalı")
    lineage_path = export / LINEAGE_NAME
    # lstat every component before asking whether the lineage entry exists;
    # Path.exists() follows links and reports dangling links as absent.
    _check_existing_components(lineage_path, root)
    if _lstat_if_present(lineage_path) is not None:
        raise MergeError("Bileşik export'u tekrar kaynak olarak kullanmayın")
    _check_existing_components(pack, root)
    try:
        rows = prep.validate_dataset(export)
        provenance = prep._verify_single_pack_export(export, rows)
    except (prep.InputError, OSError) as exc:
        raise MergeError(f"Kaynak export/etkin review-pack zinciri doğrulanamadı: {exc}") from exc
    manifest = _read_capped(export / "manifest.jsonl", 64 * 1024 * 1024,
                            "Kaynak export manifesti")
    return export, pack, rows, manifest, provenance


def _write_file(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def merge_exports(root: Path, source_exports: list[Path], out_name: str) -> tuple[Path, dict]:
    raw_root = root.expanduser()
    root_text = os.fspath(raw_root)
    if root_text.startswith(("\\\\", "//")):
        raise MergeError("Ortak kök ağ/UNC yolu olamaz; yerel disk kullanın")
    _check_existing_components(raw_root)
    try:
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise MergeError("Ortak yerel kök önceden var olmalı") from exc
    if not root.is_dir():
        raise MergeError("Ortak kök klasör olmalı")
    if root.name.endswith(".review-pack"):
        raise MergeError("Ortak kök bir kaynak .review-pack olamaz")
    if os.name == "nt" and root.drive:
        try:
            import ctypes
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root.drive + "\\")
        except (AttributeError, OSError) as exc:
            raise MergeError("Windows sürücü türü güvenle denetlenemedi") from exc
        if drive_type == 4:  # DRIVE_REMOTE (mapped network drive)
            raise MergeError("Ortak kök eşlenmiş ağ sürücüsünde; yerel disk kullanın")
    _check_existing_components(root)
    for variable in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        configured = os.environ.get(variable, "").strip()
        if configured:
            try:
                # path-scope-ok: root yukarıda resolve(strict=True) edildi, sağ taraf da burada çözülüyor
                if root.is_relative_to(Path(configured).expanduser().resolve(strict=False)):
                    raise MergeError(f"Ortak kök senkronizasyon klasöründe ({variable}); yerel klasör kullanın")
            except OSError as exc:
                raise MergeError(f"Senkronizasyon kökü güvenle denetlenemedi ({variable})") from exc
    if not 3 <= len(source_exports) <= MAX_MEMBERS:
        raise MergeError(f"En az 3, en çok {MAX_MEMBERS} kaynak review-pack export'u gerekir")
    if (not isinstance(out_name, str) or not re.fullmatch(
            r"verified-ocr-dataset-composite-[A-Za-z0-9_.-]{1,100}", out_name)):
        raise MergeError("--out-name biçimi verified-ocr-dataset-composite-<kısa-ad> olmalı")
    target = root / out_name
    target_info = _lstat_if_present(target)
    if target_info is not None:
        if _is_reparse(target_info):
            raise MergeError("Hedef sembolik bağlantı/junction olamaz")
        raise MergeError("Hedef mevcut; üzerine yazılmaz")

    source_paths = []
    for value in source_exports:
        export, pack, rows, manifest, provenance = _read_source_export(root, value)
        source_paths.append((export, pack, rows, manifest, provenance))
    source_paths.sort(key=lambda item: path_scope.relative_to(item[0], root).as_posix())
    if len({str(pack) for _, pack, *_ in source_paths}) != len(source_paths):
        raise MergeError("Aynı review-pack'ten birden fazla export seçildi")

    all_cue_ids = set()
    total_rows = sum(len(rows) for _, _, rows, _, _ in source_paths)
    if not 1 <= total_rows <= prep.MAX_ROWS:
        raise MergeError(f"Birleşik cue sayısı 1..{prep.MAX_ROWS} sınırında olmalı")
    total_unique_bytes = 0
    crop_payloads = {}
    manifest_lines, members = [], []
    for export, pack, rows, source_manifest, provenance in source_paths:
        cue_ids = []
        for row in rows:
            cue_id = row["cue_id"]
            if cue_id in all_cue_ids:
                raise MergeError(f"cue_id çakışması: {cue_id}")
            all_cue_ids.add(cue_id)
            cue_ids.append(cue_id)
            src = prep._safe_crop(export, row["crop_path"])
            data = prep._read_capped(src, prep.MAX_CROP_BYTES, "Kaynak crop")
            digest = row["crop_sha256_local"]
            if _sha256(data) != digest:
                raise MergeError(f"Kaynak crop hash'i değişti: {cue_id}")
            old = crop_payloads.get(digest)
            if old is None:
                total_unique_bytes += len(data)
                if total_unique_bytes > prep.MAX_TOTAL_CROP_BYTES:
                    raise MergeError("Birleşik benzersiz crop toplamı 128 MiB sınırını aşıyor")
                crop_payloads[digest] = data
            elif old != data:
                raise MergeError("Aynı SHA-256 altında farklı crop baytları bulundu")
            merged_row = {key: value for key, value in row.items() if not key.startswith("_")}
            merged_row["crop_path"] = f"crops/{digest}.jpg"
            manifest_lines.append(json.dumps(merged_row, ensure_ascii=False, separators=(",", ":")) + "\n")
        members.append({
            "source_pack_path": _root_relative(root, pack),
            "source_export_path": _root_relative(root, export),
            "source_export_manifest_sha256": _sha256(source_manifest),
            "review_manifest_sha256": provenance["review_manifest_sha256"],
            "review_srt_sha256": provenance["review_srt_sha256"],
            "review_events_sha256": provenance["review_events_sha256"],
            "cue_ids": sorted(cue_ids),
        })
    manifest_bytes = "".join(manifest_lines).encode("utf-8")
    if len(manifest_bytes) > 64 * 1024 * 1024:
        raise MergeError("Birleşik manifest 64 MiB sınırını aşıyor")
    lineage = {"schema_version": LINEAGE_SCHEMA_VERSION,
               "kind": "phase2-multi-pack-verified-export", "members": members}
    lineage_bytes = (json.dumps(lineage, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(lineage_bytes) > 16 * 1024 * 1024:
        raise MergeError("Lineage manifesti 16 MiB sınırını aşıyor")

    # Keep the staging directory's name valid for the same provenance verifier
    # used by Phase 5, so validation can finish before publication.
    staging = Path(tempfile.mkdtemp(
        prefix="verified-ocr-dataset-composite-staging-", dir=root))
    try:
        (staging / "crops").mkdir()
        for digest, data in crop_payloads.items():
            _write_file(staging / "crops" / f"{digest}.jpg", data)
        _write_file(staging / "manifest.jsonl", manifest_bytes)
        _write_file(staging / LINEAGE_NAME, lineage_bytes)
        _write_file(staging / "README.txt", (
            "Composite Phase 2 dataset assembled from immutable per-review-pack exports.\n"
            "source-lineage-v1.json binds every cue to an original export and active review journal.\n"
            "This local snapshot contains no source SRT or video.\n"
        ).encode("utf-8"))
        rows = prep.validate_dataset(staging)
        verified = prep.verify_phase2_provenance(staging, rows)
        if verified.get("status") != "verified_against_parent_review_pack" or \
                verified.get("mode") != "composite":
            raise MergeError("Birleşik veri kümesinin lineage doğrulaması başarısız: " +
                             str(verified.get("reason") or "composite status missing"))
        # Recheck for a clear early error, then publish through a native atomic
        # no-replace primitive so a concurrent target creation cannot be lost.
        target_info = _lstat_if_present(target)
        if target_info is not None:
            if _is_reparse(target_info):
                raise MergeError("Hedef sembolik bağlantı/junction olamaz")
            raise MergeError("Hedef mevcut; üzerine yazılmaz")
        _publish_noreplace(staging, target)
    except Exception:
        # Only remove the uniquely-created staging path. The published target
        # is never recursively removed after a failed check, avoiding deletion
        # through a path substituted by another process.
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target, {"rows": total_rows, "groups_minimum": len(members),
                    "unique_crops": len(crop_payloads), "unique_crop_bytes": total_unique_bytes,
                    "lineage_sha256": _sha256(lineage_bytes), "provenance": verified}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Birden fazla doğrulanmış Phase 2 export'unu orijinal review-pack lineage'iyle birleştir")
    parser.add_argument("--root", required=True, type=Path,
                        help="Kaynak review-pack'leri ve yeni composite'i içeren mevcut yerel, senkronize olmayan kök")
    parser.add_argument("--source-export", action="append", required=True, type=Path,
                        help="--root'e göreli verified-ocr-dataset-* yolu; en az üç farklı .review-pack")
    parser.add_argument("--out-name", required=True,
                        help="--root altında var olmayan verified-ocr-dataset-composite-<kısa-ad> klasörü")
    args = parser.parse_args(argv)
    try:
        target, result = merge_exports(args.root, args.source_export, args.out_name)
    except (MergeError, prep.InputError, OSError, ValueError) as exc:
        print(f"DURDURULDU: {exc}", file=__import__("sys").stderr)
        return 2
    print(f"Birleşik yerel export oluşturuldu: {target.name}; {result['rows']} cue, "
          f"{result['groups_minimum']} kaynak export, {result['unique_crops']} benzersiz crop.")
    print("Rights/hukuki onay verilmedi; Phase 5 hazırlık ve değerlendirme ayrı adımlardır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
