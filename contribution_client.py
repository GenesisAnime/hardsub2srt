"""Opt-in, minimized metrics contribution client for the local Flask UI.

This module deliberately accepts only an allowlisted subset of local stats.
It never reads or serializes media, subtitle text, paths, names, hashes, or IDs
that persist between submissions.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 16 * 1024


class ContributionError(ValueError):
    pass


def _api_endpoint(path: str) -> str:
    """Build a fixed API path; never place it in URL query/fragment slots."""
    base = urlsplit(os.environ["H2S_CONTRIB_URL"].strip())
    return urlunsplit((base.scheme, base.netloc, path, "", ""))


def _number(value, name, *, integer=False, minimum=0, maximum=10_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContributionError(f"{name} sayısal değil")
    if integer and not isinstance(value, int):
        raise ContributionError(f"{name} tam sayı değil")
    if not minimum <= value <= maximum:
        raise ContributionError(f"{name} izin verilen aralığın dışında")
    return value


def build_payload(stats: dict, duration_seconds: float, *,
                  include_cer: bool = False, trusted_reference_used: bool = False,
                  cer_consent: bool = False,
                  cer_value: float | None = None) -> dict:
    """Build the only payload this client is allowed to send.

    CER is absent unless the caller has both a trusted local reference and a
    separate explicit consent. The caller must provide the already-computed
    numeric CER; this function never reads VTT/SRT content.
    """
    if not isinstance(stats, dict):
        raise ContributionError("Koşum istatistiği okunamadı")
    metrics = {
        "duration_seconds": round(_number(duration_seconds, "duration_seconds", maximum=86400), 1),
        "frames_scanned": _number(stats.get("scanned_frames"), "frames_scanned", integer=True),
        "cue_count": _number(stats.get("blocks"), "cue_count", integer=True, maximum=1_000_000),
        "low_conf_count": _number(stats.get("low_conf"), "low_conf_count", integer=True, maximum=1_000_000),
        "device_class": _device_class(stats.get("device")),
        "engine_class": _engine_class(stats.get("ocr_mode")),
    }
    if metrics["low_conf_count"] > metrics["cue_count"]:
        raise ContributionError("low_conf_count cue_count değerini aşamaz")
    if metrics["cue_count"]:
        metrics["low_conf_ratio"] = round(metrics["low_conf_count"] / metrics["cue_count"], 4)
    speech = stats.get("speech_seconds")
    if speech is not None:
        metrics["speech_seconds"] = round(_number(speech, "speech_seconds", maximum=86400), 1)
    if include_cer:
        if not trusted_reference_used or not cer_consent:
            raise ContributionError("CER için güvenilir yerel referans ve ayrı onay gerekir")
        metrics["cer"] = {
            "value": round(_number(cer_value, "cer", maximum=10), 6),
            "reference_class": "trusted_local_vtt",
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "submission_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "metrics": metrics,
    }


def _device_class(value):
    s = str(value or "").strip().lower()
    if s == "cpu" or s.startswith("cpu "):
        return "cpu"
    if s.startswith("gpu") or "cuda" in s:
        return "cuda"
    return "unknown"


def _engine_class(value):
    s = str(value or "").strip().lower()
    if s.startswith("easyocr") or s == "easy":
        return "easyocr"
    if s.startswith("rapidocr") or s == "rapid":
        return "rapidocr"
    return "unknown"


def validate_payload(payload: dict) -> dict:
    """Strict local allowlist validation and redaction boundary."""
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "submission_id", "created_at", "metrics"}:
        raise ContributionError("Katkı paketi şemaya uymuyor")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise ContributionError("Desteklenmeyen katkı şeması")
    try:
        if str(uuid.UUID(payload["submission_id"])) != payload["submission_id"]:
            raise ValueError("non-canonical UUID")
    except (ValueError, TypeError, AttributeError):
        raise ContributionError("submission_id geçersiz")
    if not isinstance(payload["created_at"], str) or len(payload["created_at"]) > 40:
        raise ContributionError("created_at geçersiz")
    try:
        parsed_time = datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))
        if parsed_time.tzinfo is None:
            raise ValueError("timezone required")
    except ValueError:
        raise ContributionError("created_at geçersiz") from None
    metrics = payload["metrics"]
    allowed = {"duration_seconds", "frames_scanned", "cue_count", "low_conf_count", "device_class", "engine_class", "low_conf_ratio", "speech_seconds", "cer"}
    if not isinstance(metrics, dict) or set(metrics) - allowed:
        raise ContributionError("Bilinmeyen veya içerik taşıyan alan var")
    # Schema allowlisting is the privacy boundary: string-valued identifiers,
    # paths, filenames, and content have no accepted field in this contract.
    if any(isinstance(value, (dict, list)) for value in metrics.values() if value is not metrics.get("cer")):
        raise ContributionError("Beklenmeyen iç içe veri reddedildi")
    required = {"duration_seconds", "frames_scanned", "cue_count", "low_conf_count", "device_class", "engine_class"}
    if required - set(metrics):
        raise ContributionError("Zorunlu ölçüm alanı eksik")
    if metrics["device_class"] not in {"cuda", "cpu", "unknown"} or metrics["engine_class"] not in {"easyocr", "rapidocr", "unknown"}:
        raise ContributionError("Bilinmeyen çalışma sınıfı")
    for key in ("duration_seconds", "speech_seconds", "low_conf_ratio"):
        if key in metrics:
            _number(metrics[key], key, maximum=86400 if key != "low_conf_ratio" else 1)
    for key in ("frames_scanned", "cue_count", "low_conf_count"):
        _number(metrics[key], key, integer=True, maximum=10_000_000 if key == "frames_scanned" else 1_000_000)
    if metrics["low_conf_count"] > metrics["cue_count"]:
        raise ContributionError("low_conf_count cue_count değerini aşamaz")
    cer = metrics.get("cer")
    if cer is not None:
        if not isinstance(cer, dict) or set(cer) != {"value", "reference_class"} or cer["reference_class"] != "trusted_local_vtt":
            raise ContributionError("CER ayrıca onaylanmış güvenilir yerel referansa bağlı olmalı")
        _number(cer["value"], "cer", maximum=10)
    # Construct a fresh dict from exactly the approved keys: do not forward caller objects.
    return json.loads(json.dumps(payload, allow_nan=False))


def config_status() -> dict:
    base = os.environ.get("H2S_CONTRIB_URL", "").strip()
    token = os.environ.get("H2S_CONTRIB_TOKEN", "").strip()
    retention_raw = os.environ.get("H2S_CONTRIB_RETENTION_DAYS", "").strip()
    try:
        retention_days = int(retention_raw)
        retention_valid = str(retention_days) == retention_raw and 1 <= retention_days <= 3650
    except ValueError:
        retention_days, retention_valid = None, False
    valid_url = False
    destination_host = None
    if base:
        try:
            parsed = urlsplit(base)
            hostname = parsed.hostname
            port = parsed.port
            valid_url = (parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and
                         not parsed.password and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
            if valid_url:
                host_label = f"[{hostname}]" if ":" in hostname else hostname
                destination_host = host_label + (f":{port}" if port and port != 443 else "")
        except ValueError:
            valid_url = False
            destination_host = None
    return {"enabled": bool(valid_url and token and retention_valid), "url_present": bool(valid_url),
            "destination_host": destination_host,
            "token_present": bool(token), "retention_days": retention_days if retention_valid else None}


def send_payload(payload: dict, *, cer_consent: bool = False) -> dict:
    payload = validate_payload(payload)
    if "cer" in payload["metrics"] and not cer_consent:
        raise ContributionError("CER gönderimi için ayrı açık onay gerekli")
    status = config_status()
    if not status["enabled"]:
        raise ContributionError("Katkı URL'si veya token yapılandırılmamış; gönderim kapalı")
    endpoint = _api_endpoint("/v1/contributions")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise ContributionError("Katkı paketi boyut sınırını aşıyor")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + os.environ["H2S_CONTRIB_TOKEN"].strip(),
        "Idempotency-Key": payload["submission_id"], "X-H2S-Metrics-Consent": "v1",
        "X-H2S-Retention-Policy": str(status["retention_days"]),
        **({"X-H2S-CER-Consent": "v1"} if "cer" in payload["metrics"] and cer_consent else {}),
        "User-Agent": "hardsub2srt-contribution/1",
    })
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=12) as response:
            if response.status not in (200, 201, 202):
                raise ContributionError(f"API yanıtı beklenmiyor: HTTP {response.status}")
            data = json.loads(response.read(2048).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ContributionError(f"Katkı gönderilemedi: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ContributionError("Katkı gönderilemedi; bağlantı veya API yanıtı geçersiz") from None
    if not isinstance(data, dict) or data.get("submission_id") != payload["submission_id"]:
        raise ContributionError("API yanıtı gönderim kimliğiyle eşleşmiyor")
    return {"submission_id": payload["submission_id"], "status": str(data.get("status", "accepted"))[:24]}


def delete_submission(submission_id: str) -> dict:
    try:
        if str(uuid.UUID(submission_id)) != submission_id:
            raise ValueError("non-canonical UUID")
    except (ValueError, TypeError, AttributeError):
        raise ContributionError("submission_id geçersiz") from None
    status = config_status()
    if not status["enabled"]:
        raise ContributionError("Katkı URL'si veya token yapılandırılmamış")
    endpoint = _api_endpoint("/v1/contributions/" + submission_id)
    req = urllib.request.Request(endpoint, method="DELETE", headers={
        "Authorization": "Bearer " + os.environ["H2S_CONTRIB_TOKEN"].strip(),
        "User-Agent": "hardsub2srt-contribution/1",
    })

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req, timeout=12) as response:
            data = json.loads(response.read(2048).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"status": "not_found"}
        raise ContributionError(f"Silme isteği başarısız: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ContributionError("Katkı silinemedi; bağlantı veya API yanıtı geçersiz") from None
    if not isinstance(data, dict) or data.get("status") != "deleted":
        raise ContributionError("API silme işlemini doğrulayamadı")
    return {"status": "deleted"}
