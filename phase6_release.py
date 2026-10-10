#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed validation scaffold for a local OCR model candidate release.

This CLI only validates references and file hashes. It never trains, loads,
packages, installs, activates, uploads, or publishes a model. Current Phase 5
v1 reports are preparation-only and cannot pass the Phase 6 release gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SMALL_JSON_BYTES = 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class GateError(ValueError):
    pass


def _load_json(path: Path, cap: int = MAX_JSON_BYTES) -> tuple[object, bytes]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(cap + 1)
    except OSError as exc:
        raise GateError(f"JSON okunamadı: {path}") from exc
    if len(raw) > cap:
        raise GateError(f"JSON {cap} byte sınırını aşıyor: {path.name}")
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"Geçersiz UTF-8 JSON: {path.name}") from exc


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise GateError(f"Dosya okunamadı: {path}") from exc
    return digest.hexdigest(), size


def _relative_file(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise GateError(f"{label}: ileri eğik çizgili göreli yol gerekli")
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts or any(part in {"", "."} for part in rel.parts):
        raise GateError(f"{label}: kök dışına çıkabilen veya mutlak yol reddedildi")
    candidate = root.joinpath(*rel.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise GateError(f"{label}: dosya yok veya kök klasör dışına çıkıyor") from exc
    if not resolved.is_file():
        raise GateError(f"{label}: düzenli dosya gerekli")
    return resolved


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise GateError(f"{label}: JSON nesnesi gerekli")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise GateError(f"{label}: küçük harfli SHA-256 gerekli")
    return value


def validate(manifest_path: Path, report_path: Path, trust_path: Path) -> list[str]:
    """Return blockers. Empty means structural checks passed, not release approval."""
    blockers: list[str] = []
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent
    manifest, _ = _load_json(manifest_path, MAX_SMALL_JSON_BYTES)
    data = _object(manifest, "candidate manifest")
    if data.get("schema_version") != 1:
        raise GateError("Manifest schema_version 1 değil")

    evaluation = _object(data.get("evaluation"), "evaluation")
    report_file = _relative_file(root, evaluation.get("report_path"), "evaluation.report_path")
    expected_report_hash = _digest(evaluation.get("report_sha256"), "evaluation.report_sha256")
    report_hash, _ = _sha256_file(report_file)
    if report_hash != expected_report_hash:
        blockers.append("Phase 5 raporunun SHA-256 değeri manifest ile eşleşmiyor")
    if report_file != report_path.resolve(strict=True):
        blockers.append("CLI ile verilen Phase 5 raporu manifestteki raporla aynı değil")
    report, _ = _load_json(report_file)
    report_obj = _object(report, "Phase 5 report")
    if report_obj.get("schema_version") != 2:
        blockers.append("Phase 5 v1 rapor şeması yalnız PREPARED_NOT_RUN/blocked üretir; gerçek release-pass rapor şeması yok")
    if report_obj.get("status") != "EVALUATED":
        blockers.append("Phase 5 raporu gerçek EVALUATED sonucu değil")
    release_gate = report_obj.get("release_gate")
    if not isinstance(release_gate, dict) or release_gate.get("status") != "PASS":
        blockers.append("Phase 5 raporunda açık ve hesaplanmış release_gate.status=PASS yok")
    elif (release_gate.get("criteria_predeclared") is not True or
          release_gate.get("no_regressions") is not True or
          release_gate.get("baseline_candidate_same_conditions") is not True or
          not HEX64.fullmatch(str(release_gate.get("frozen_test_split_sha256", ""))) or
          not HEX64.fullmatch(str(release_gate.get("trusted_reference_sha256", "")))):
        blockers.append("Phase 5 PASS kanıtında dondurulmuş test/referans veya önceden ilan edilmiş regresyon koşulları eksik")
    stop_gate = report_obj.get("stop_gate")
    if not isinstance(stop_gate, dict):
        blockers.append("Phase 5 stop_gate eksik")
    else:
        if stop_gate.get("experiment_run_blocked") is not False:
            blockers.append("Phase 5 stop_gate deneyi bloklu gösteriyor")
        if stop_gate.get("human_provenance_verified") is not True:
            blockers.append("Phase 5 insan doğrulamalı provenance geçmiyor")
        if stop_gate.get("human_rights_review_complete") is not True:
            blockers.append("Phase 5 insan hak incelemesini tamamlanmış göstermiyor")
        if stop_gate.get("rights_verified") is not True:
            blockers.append("Phase 5 stop_gate hakları doğrulanmamış gösteriyor")
        if stop_gate.get("metrics_claimable") is not True or stop_gate.get("trusted_reference_required") is not True:
            blockers.append("Phase 5 ölçüm/ trusted reference kapısı geçmiyor")
    policy = report_obj.get("data_policy")
    if not isinstance(policy, dict) or policy.get("rights_verified") is not True:
        blockers.append("Phase 5 raporunda haklar insan tarafından doğrulanmış değil")
    elif policy.get("human_rights_review_complete") is not True or policy.get("local_only") is not True:
        blockers.append("Phase 5 insan hak incelemesi veya yerel-only veri politikası geçmiyor")
    if isinstance(policy, dict) and policy.get("training_performed") is not True:
        blockers.append("Phase 5 raporu aday eğitiminin gerçekten yapıldığını göstermiyor")
    report_provenance = report_obj.get("provenance")
    if not isinstance(report_provenance, dict) or report_provenance.get("status") != "verified_against_parent_review_pack":
        blockers.append("Phase 5 veri provenance'ı bağımsız biçimde doğrulanmış değil")
    metrics = report_obj.get("metrics")
    ocr_metrics = metrics.get("ocr") if isinstance(metrics, dict) else None
    required_metrics = ("cer", "wer", "exact_match_cue_rate", "cue_precision", "cue_recall",
                        "timing_start_abs_error_ms", "timing_end_abs_error_ms", "coverage")
    if not isinstance(ocr_metrics, dict):
        blockers.append("Phase 5 OCR metrikleri eksik")
    else:
        for name in required_metrics:
            value = ocr_metrics.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                blockers.append(f"Phase 5 ölçülmemiş/geçersiz OCR metriği: {name}")
    experiment = report_obj.get("experiment")
    if not isinstance(experiment, dict) or not isinstance(data.get("candidate"), dict) or experiment.get("candidate_id") != data["candidate"].get("id"):
        blockers.append("Phase 5 ölçülen aday kimliği manifestteki sürümle eşleşmiyor")
    if evaluation.get("phase5_status") != "EVALUATED" or evaluation.get("release_gate") != "PASS":
        blockers.append("Aday manifesti Phase 5 EVALUATED/PASS sonucuna bağlanmıyor")
    files = data.get("model_files")
    if not isinstance(files, list) or not files:
        blockers.append("Aday model dosyası bildirilmemiş")
    else:
        seen: set[str] = set()
        for index, item in enumerate(files):
            entry = _object(item, f"model_files[{index}]")
            rel = entry.get("path")
            path = _relative_file(root, rel, f"model_files[{index}].path")
            if rel in seen:
                blockers.append(f"Yinelenen model dosyası: {rel}")
            seen.add(rel)
            expected = _digest(entry.get("sha256"), f"model_files[{index}].sha256")
            actual, size = _sha256_file(path)
            if size != entry.get("size_bytes") or actual != expected:
                blockers.append(f"Aday dosya boyutu/hash eşleşmiyor: {rel}")

    provenance = _object(data.get("provenance"), "provenance")
    if provenance.get("dataset_manifest_sha256") != report_obj.get("dataset", {}).get("manifest_sha256") if isinstance(report_obj.get("dataset"), dict) else True:
        blockers.append("Adayın veri kümesi hash'i Phase 5 raporuyla eşleşmiyor")

    compat = _object(data.get("compatibility"), "compatibility")
    if "gpu" not in compat.get("devices", []):
        blockers.append("Uyumluluk matrisi GPU cihazını kapsamıyor")
    if "cpu" not in compat.get("devices", []):
        blockers.append("Uyumluluk matrisi CPU seçeneğini kapsamıyor")
    activation = _object(data.get("activation_policy"), "activation_policy")
    if activation != {"default_device": "gpu", "cpu_opt_in": True, "automatic_replacement": False, "user_confirmation_required": True}:
        blockers.append("GPU varsayılanı, CPU opt-in veya sessiz değiştirmeme politikası korunmuyor")
    migration = _object(data.get("migration"), "migration")
    rollback = _object(data.get("rollback"), "rollback")
    if not migration.get("steps") or not migration.get("data_format_version"):
        blockers.append("Sürüm geçiş planı eksik")
    if rollback.get("supported") is not True or not rollback.get("steps") or rollback.get("data_preserved") is not True:
        blockers.append("Geri alma planı ve veri koruma garantisi eksik")

    rights = _object(data.get("rights_review"), "rights_review")
    receipt_path = _relative_file(root, rights.get("receipt_path"), "rights_review.receipt_path")
    expected_receipt_hash = _digest(rights.get("receipt_sha256"), "rights_review.receipt_sha256")
    actual_receipt_hash, _ = _sha256_file(receipt_path)
    if actual_receipt_hash != expected_receipt_hash:
        blockers.append("Hak inceleme makbuzu hash'i eşleşmiyor")
    trusted, _ = _load_json(trust_path.resolve(strict=True), MAX_SMALL_JSON_BYTES)
    trust_obj = _object(trusted, "trusted rights review receipts")
    if (trust_obj.get("schema_version") != 1 or
            not isinstance(trust_obj.get("trusted_receipts"), list) or
            not isinstance(trust_obj.get("trusted_phase5_reports"), list)):
        blockers.append("Güvenilen hak inceleme kökü geçersiz")
    elif expected_receipt_hash not in trust_obj.get("trusted_receipts", []):
        blockers.append("Makbuz boş/pinlenmemiş trust store içinde yok; dosya veya öz-beyan insan incelemesi sayılmaz")
    if report_hash not in trust_obj.get("trusted_phase5_reports", []):
        blockers.append("Phase 5 raporu bağımsız değerlendirme trust kökünde pinli değil; öz-beyan PASS kabul edilmez")

    return blockers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 6 yerel aday model sürüm kapısı (yalnız doğrulama; paketleme/yayın yok)")
    parser.add_argument("manifest", type=Path, help="aday paket kökündeki manifest.json")
    parser.add_argument("--phase5-report", type=Path, required=True, help="manifestte hash'i bulunan Phase 5 raporu")
    parser.add_argument("--json", action="store_true", help="makinece okunur sonuç yaz")
    args = parser.parse_args(argv)
    try:
        trust_path = Path(__file__).resolve().parent / "schemas" / "trusted-rights-review-receipts-v1.json"
        blockers = validate(args.manifest, args.phase5_report, trust_path)
    except (GateError, OSError, ValueError) as exc:
        blockers = [str(exc)]
    result = {"status": "BLOCKED" if blockers else "STRUCTURE_VALID_NOT_RELEASED", "release_permitted": False, "packaging_performed": False, "publishing_performed": False, "blockers": blockers}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("Phase 6 release gate: " + result["status"])
        for item in blockers:
            print("- " + item)
        print("Bu CLI model paketlemez, etkinleştirmez veya yayımlamaz.")
    return 2 if blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
