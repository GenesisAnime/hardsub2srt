#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create an incomplete, local Phase 5 metadata template from a verified export.

This helper does not infer source/episode IDs, licensing, or permissions. It
validates the Phase 2 export and its active local review decisions, then writes
one blank metadata row per verified cue. The output deliberately fails the
Phase 5 experiment gate until a human completes and reviews it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import ocr_experiment


WARNING = (
    "INCOMPLETE TEMPLATE: fill source_id, episode_id, license identifier, "
    "evidence path/hash, and complete a human rights review. This file is not "
    "permission proof and must not pass the Phase 5 experiment gate as-is."
)


def make_template(dataset: Path, output: Path) -> tuple[int, Path]:
    """Validate a linked Phase 2 export and atomically create a blank template."""
    dataset = dataset.resolve(strict=True)
    if not dataset.is_dir() or not dataset.name.startswith("verified-ocr-dataset-"):
        raise ocr_experiment.InputError(
            "Girdi verified-ocr-dataset-* adlı Phase 2 dışa aktarım klasörü olmalı"
        )

    rows = ocr_experiment.validate_dataset(dataset)
    provenance = ocr_experiment.verify_phase2_provenance(dataset, rows)
    if provenance.get("status") != "verified_against_parent_review_pack":
        raise ocr_experiment.InputError(
            "Phase 2 dışa aktarımı üst review-pack ve etkin insan kararlarına bağlanamadı; "
            "metadata şablonu oluşturulmadı"
        )

    output = output.resolve(strict=False)
    try:
        output.relative_to(dataset)
    except ValueError:
        pass
    else:
        raise ocr_experiment.InputError(
            "Metadata şablonu kaynak veri kümesinin içine yazılamaz"
        )
    if output == dataset:
        raise ocr_experiment.InputError(
            "Metadata şablonu kaynak veri kümesinin üzerine yazılamaz"
        )
    if output.suffix.lower() != ".json":
        raise ocr_experiment.InputError("Çıktı yolu .json uzantılı olmalı")
    if not output.parent.is_dir():
        raise ocr_experiment.InputError("Çıktı klasörü önceden var olmalı")
    if output.exists():
        raise ocr_experiment.InputError("Çıktı zaten var; üzerine yazılmayacak")

    records = []
    for row in rows:
        records.append({
            "cue_id": row["cue_id"],
            "source_id": "",
            "episode_id": "",
            "license": {
                "status": "",
                "identifier": "",
                "evidence_path": "",
                "evidence_sha256": "",
                "human_review": {
                    "status": "pending",
                    "reviewer": None,
                    "reviewed_at": None,
                },
            },
        })
    payload = (json.dumps(
        {"schema_version": 1, "records": records},
        ensure_ascii=False,
        indent=2,
    ) + "\n").encode("utf-8")
    if len(payload) > ocr_experiment.MAX_METADATA_BYTES:
        raise ocr_experiment.InputError("Üretilen metadata 16 MiB sınırını aşıyor")

    # A same-directory temporary file plus hard-link gives an atomic,
    # no-overwrite publication on supported local filesystems.
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".ocr-metadata-", suffix=".tmp",
            dir=output.parent, delete=False,
        ) as stream:
            temp_name = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_name, output)
        except FileExistsError as exc:
            raise ocr_experiment.InputError(
                "Çıktı zaten var; üzerine yazılmayacak"
            ) from exc
    except OSError as exc:
        raise ocr_experiment.InputError(
            "Şablon atomik ve üzerine yazmadan oluşturulamadı; hedef dosya değiştirilmedi"
        ) from exc
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    return len(records), output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2 doğrulanmış export'tan boş, yerel Phase 5 metadata şablonu üret"
    )
    parser.add_argument("dataset", type=Path, help="verified-ocr-dataset-* klasörü")
    parser.add_argument("--out", required=True, type=Path, help="var olmayan, dataset dışındaki .json dosyası")
    args = parser.parse_args(argv)
    try:
        count, output = make_template(args.dataset, args.out)
    except (ocr_experiment.InputError, OSError, UnicodeError) as exc:
        print(f"DURDURULDU: {exc}", file=sys.stderr)
        return 2
    print(f"Şablon oluşturuldu: {output.name} ({count} cue)")
    print(f"UYARI: {WARNING}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
