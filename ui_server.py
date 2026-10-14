# -*- coding: utf-8 -*-
"""
ui_server.py — hardsub2srt için yerel web arayüzü (tek dosya, Flask).

Kullanım:
    arayuz.bat'a çift tık  (tarayıcıyı açar + sunucuyu başlatır)
    ya da: py -3 ui_server.py   →  http://127.0.0.1:8765

Önemli:
  - Yalnız 127.0.0.1'e bağlanır; port 8765 meşgulse ikinci örnek başlamaz
    ("sunucu zaten çalışıyor" deyip çıkar).
  - Kuyruk KESİNLİKLE SIRALI çalışır: tek GPU'da (RTX 4060) paralel OCR
    ölçüldüğü gibi yavaşlatır; bir iş bitmeden diğeri başlamaz.
  - Bu dosya hardsub2srt.py / srt2ass.py'yi ÇAĞIRIR; onları DEĞİŞTİRMEZ.
  - [auto] ve [!] satırları hardsub2srt.py'de stderr'e yazılır (note());
    stderr stdout'a birleştirilerek okunur.

Kuyruk işi başına çıktı:
    <çıktı_klasörü>/runs/<video-adı>/<tarih-saat>_<iş-id>/
    bu klasörün içinde SRT, stats, blok dökümü, öğrenme günlüğü, ASS ve VTT raporu
    qa/ altında ise QA montajları. Her kuyruk işi kendi yeni klasörünü alır.

Blok editörü (v1.1):
  - Sonuç kartındaki "İncele" → GET /api/bloklar?is=<id> (SRT blokları JSON)
  - "Kaydet" → POST /api/blokkaydet (yazmadan önce <ad>.srt.bak yedeği)
  - "Öğret" → POST /api/ogret → kullanici-sozlugu.txt (eski<TAB>yeni satırı;
    araca entegrasyonu — hardsub2srt'in bu sözlüğü kullanması — sonraki tur)
  - Güvenlik: yalnız kuyruğun kendi işlerinin SRT'leri; yol traversal kapalı

Otomatik öğrenme (v1.2 — Kaydet = öğren):
  - Her blok kaydında değişen bloklar .bak içeriğiyle karşılaştırılır;
    güvenli kelime çiftleri kullanici-sozlugu.txt'e OTOMATİK eklenir
    (sınıflandırıcı ogren.py'de: diakritik / kaynaşma / noktalama / harf;
    zehirlleme koruması — sözlükte tek egemen aday şartı). Post-fix
    (hardsub2srt.py _kullanici_uygula) bu sözlüğü zaten tüketiyor.
  - Serbest yeniden yazım öğrenilmez ("belirsiz"); aynı eski farklı yeniyle
    sözlükte varsa İLK kalır ve çatışma loglanır; aynı çift "zaten_var".
  - Tur günlüğü: <ad>.ogrenme-gunlugu.json + GET /api/ogrenmerapor?is=<id>;
    Kaydet sonrası editör "N kelime öğrenildi, M belirsiz (atlandı)" + rapor
    bağlantısı gösterir.
  - ogren.py import edilemezse KAYIT ÇALIŞMAYA DEVAM EDER, öğrenme atlanır.

Kontrol paneli + toplu pano (v1.3):
  - hardsub2srt.py artık her koşuda <ad>.bloklar.json döker (ana + _ekran
    blokları, OCR conf değerleriyle). Sonuç kartında low_conf > 0 ise
    "Kontrol Et (N)" düğmesi çıkar → GET /api/kontrol?is=<id> → düşük-güven
    bloklar (conf < stats.conf_thr) kırmızı vurgulu listelenir; düzenleme +
    Öğret + Kaydet MEVCUT blok editörü üzerinden yapılır (öğrenme döngüsü
    tek ekranda). "yalnız düşük güven" süzgeci yalnız GÖRÜNÜMÜ süzer;
    Kaydet her zaman tam SRT sırasını yazar.
  - Eski çıktıda (.bloklar.json yok) panel açılır ama bilgilendirme gösterir
    ("eski sürüm — kontrol için yeniden koşun") ve SRT'nin tamamı conf'suz
    listelenir.
  - "Pano" düğmesi (başlıkta) → GET /api/pano → çıktı klasöründeki TÜM
    *.stats.json tablosu: dosya | blok | low-conf% | konuşma sn |
    hardsub_shr | altyazısız? | tarih. low-conf% > %40 üstte + kırmızı;
    .bloklar.json varsa "İncele" (GET /api/pano/incele — salt-okunur
    görüntüleyici). Eski-format stats boş hücreyle zarifçe listelenir.
"""

import atexit
import base64
import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tempfile
import uuid
from urllib.parse import urlencode, urlsplit
from collections import deque
from datetime import datetime
from pathlib import Path

import path_scope

KLASOR = Path(__file__).resolve().parent
if os.name == "nt":
    _H2S_LOCAL_ROOT = Path(os.environ.get("LOCALAPPDATA") or
                           (Path.home() / "AppData" / "Local"))
else:
    _H2S_LOCAL_ROOT = Path(os.environ.get("XDG_STATE_HOME") or
                           (Path.home() / ".local" / "state"))
APP_LOCAL_DIR = _H2S_LOCAL_ROOT / "hardsub2srt"


def _seed_review_storage_roots():
    """Return a short, profile-scoped set of Local AppData roots.

    Packaged Windows launchers can virtualize LOCALAPPDATA for a child
    process. The per-user Shell Folders registry value gives the canonical
    Local AppData path; environment-derived paths remain a fallback. This
    deliberately checks only these known roots, never searches the disk.
    """
    roots = []
    raw_roots = []
    if os.name == "nt":
        try:
            import winreg
            for subkey in (
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                        value, _kind = winreg.QueryValueEx(key, "Local AppData")
                    if isinstance(value, str) and value.strip():
                        raw_roots.append(os.path.expandvars(value.strip()))
                except OSError:
                    continue
        except ImportError:
            pass
    raw_roots.extend((str(_H2S_LOCAL_ROOT), str(APP_LOCAL_DIR.parent)))
    seen = set()
    for raw in raw_roots:
        try:
            local_root = Path(raw).expanduser()
            text = os.fspath(local_root)
            if (not local_root.is_absolute() or text.startswith(("\\\\", "//")) or
                    any(part in (".", "..") for part in local_root.parts)):
                continue
            app_root = local_root / "hardsub2srt"
            key = os.path.normcase(os.path.abspath(os.fspath(app_root)))
            if key not in seen:
                seen.add(key)
                roots.append(app_root)
        except (OSError, RuntimeError, ValueError):
            continue
    if not roots:
        roots.append(APP_LOCAL_DIR)
    return roots


def _seed_review_storage_catalog():
    """Pick the first known Local AppData root containing validated seed packs."""
    import ocr_seed_import
    roots = _seed_review_storage_roots()
    for root in roots:
        packs = ocr_seed_import.list_seed_review_packs(root)
        if packs:
            return root, packs
    return roots[0], []


ARAC = KLASOR / "hardsub2srt.py"
SRT2ASS = KLASOR / "srt2ass.py"
VTT_QA = KLASOR / "vtt-qa.py"
SOZLUK = KLASOR / "kullanici-sozlugu.txt"     # /api/ogret hedefi (eski TAB yeni)
PORT = 8765
VIDEO_UZANTILARI = {".mp4", ".mkv", ".avi"}
LOG_LIMIT = 400            # bellekte tutulan toplam satır
LOG_YANIT = 200            # /api/durum yanıtında dönen satır
KUYRUK_YANIT = 100         # /api/durum yanıtında dönen iş sayısı
MAX_BATCH = 100            # tek ekle isteğinin üst sınırı
MAX_AKTIF_KUYRUK = 2000    # çalışan + bekleyen işlerin üst sınırı


def _child_process_env():
    """Preserve normal settings but never delegate contribution config/secrets."""
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith("H2S_CONTRIB_")}

try:
    from flask import Flask, Response, jsonify, request, send_file, send_from_directory
except ImportError:
    print("[HATA] Flask kurulu degil. Kurulum: py -3 -m pip install flask")
    sys.exit(2)

try:
    import ocr_review
except Exception as _ocr_review_hatasi:
    ocr_review = None
    print(f"[ui] UYARI: ocr_review.py yuklenemedi — OCR inceleme kapali: "
          f"{_ocr_review_hatasi!r}")

try:
    import translation_review
except Exception as _translation_review_hatasi:
    translation_review = None
    print(f"[ui] UYARI: translation_review.py yuklenemedi — AI çeviri round-trip kapali: "
          f"{_translation_review_hatasi!r}")

try:
    import ai_ocr_review
except Exception as _ai_ocr_review_error:
    ai_ocr_review = None
    print(f"[ui] UYARI: ai_ocr_review.py yüklenemedi — manuel AI OCR görsel incelemesi kapalı: "
          f"{_ai_ocr_review_error!r}")

try:
    import translation_memory
except Exception as _translation_memory_hatasi:
    translation_memory = None
    print(f"[ui] UYARI: translation_memory.py yuklenemedi — kapsamlı çeviri belleği kapalı: "
          f"{_translation_memory_hatasi!r}")

try:
    import translation_evaluation_service
    import translation_evaluation as translation_evaluation_backend
except Exception as _translation_evaluation_error:
    translation_evaluation_service = None
    translation_evaluation_backend = None
    print(f"[ui] UYARI: çeviri kalite pilotu yüklenemedi: {_translation_evaluation_error!r}")

try:
    import contribution_client
except Exception as _contrib_hatasi:
    contribution_client = None
    print(f"[ui] UYARI: contribution_client.py yüklenemedi — katkı gönderimi kapalı: "
          f"{_contrib_hatasi!r}")

try:
    from job_history import JobHistory, default_history_path
    JOB_HISTORY = JobHistory(default_history_path())
except Exception as _job_history_error:
    # Persistent diagnostics are best effort and must never block OCR startup.
    JOB_HISTORY = None
    print(f"[ui] UYARI: kalıcı iş tanılaması kapalı: {_job_history_error!r}")

try:
    from windows_job import CREATE_SUSPENDED, WindowsJobObject
except Exception as _windows_job_error:
    WindowsJobObject = None
    CREATE_SUSPENDED = 0x00000004
    if os.name == "nt":
        print(f"[ui] UYARI: Windows Job Object yönetimi yüklenemedi: {_windows_job_error!r}")

# otomatik öğrenme (Kaydet = öğren): sınıflandırıcı ogren.py'de tek kaynaktır;
# yüklenemezse kayıt çalışmaya devam eder, yalnız öğrenme atlanır
sys.path.insert(0, str(KLASOR))
try:
    import ogren as ogren_mod
except Exception as _ogren_hatasi:
    ogren_mod = None
    print(f"[ui] UYARI: ogren.py yuklenemedi — otomatik ogrenme kapali: "
          f"{_ogren_hatasi!r}")

app = Flask(__name__)


def _memory_request_error():
    """Reject DNS-rebinding Host headers and cross-origin browser mutations."""
    allowed_hosts = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
    raw_host = request.environ.get("HTTP_HOST", "")
    host = raw_host.lower()
    if raw_host != raw_host.strip() or any(ch.isspace() for ch in raw_host) or host not in allowed_hosts:
        return jsonify({"hata": "Yerel çeviri belleği yalnız localhost veya 127.0.0.1 üzerinden kullanılabilir"}), 403
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("Origin")
        if not origin:
            referer = request.headers.get("Referer", "")
            if referer:
                try:
                    parsed_referer = urlsplit(referer)
                    origin = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
                except ValueError:
                    origin = ""
        try:
            parsed = urlsplit(origin or "")
            valid_origin = (parsed.scheme.lower() == "http" and parsed.netloc.lower() == host and
                            parsed.hostname is not None and parsed.hostname.lower() in {"localhost", "127.0.0.1"} and
                            parsed.port == PORT and parsed.username is None and parsed.password is None and
                            parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
        except ValueError:
            valid_origin = False
        if not valid_origin:
            return jsonify({"hata": "Bellek değişikliği için aynı localhost kaynağından gelen istek gerekli"}), 403
    return None


@app.before_request
def _guard_local_translation_memory():
    if request.path == "/api/is-gecmisi":
        host = request.environ.get("HTTP_HOST", "")
        allowed = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
        if host != host.strip() or any(ch.isspace() for ch in host) or host.lower() not in allowed:
            return jsonify({"hata": "İş geçmişi yalnız localhost veya 127.0.0.1 üzerinden kullanılabilir"}), 403
        source = request.headers.get("Origin")
        if not source and request.headers.get("Referer"):
            try:
                ref = urlsplit(request.headers["Referer"])
                source = f"{ref.scheme}://{ref.netloc}"
            except ValueError:
                source = "invalid:"
        if source:
            try:
                parsed = urlsplit(source)
                valid = (parsed.scheme.lower() == "http" and parsed.netloc.lower() == host.lower()
                         and parsed.hostname is not None
                         and parsed.hostname.lower() in {"localhost", "127.0.0.1"}
                         and parsed.port == PORT and parsed.username is None
                         and parsed.password is None and parsed.path in ("", "/")
                         and not parsed.query and not parsed.fragment)
            except ValueError:
                valid = False
            if not valid:
                return jsonify({"hata": "İş geçmişi için aynı localhost kaynağı gerekli"}), 403
    if request.path.startswith("/api/ceviri-bellegi/"):
        return _memory_request_error()
    if request.path.startswith("/api/ceviri-kalitesi/"):
        error = _memory_request_error()
        if error:
            return error
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not (
                request.headers.get("Origin") or request.headers.get("Referer")):
            return jsonify({"hata": "Kalite kaydı için aynı localhost kaynağı gerekli"}), 403
    if request.path.startswith("/api/katki/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        host = request.environ.get("HTTP_HOST", "").lower()
        if host not in {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}:
            return jsonify({"hata": "Katkı işlemi yalnız yerel arayüzden kullanılabilir"}), 403
        try:
            origin = urlsplit(request.headers.get("Origin", ""))
            if (origin.scheme != "http" or origin.netloc.lower() != host or origin.hostname not in {"localhost", "127.0.0.1"} or
                    origin.port != PORT or origin.username or origin.password or origin.path not in ("", "/") or
                    origin.query or origin.fragment):
                return jsonify({"hata": "Katkı işlemi için aynı yerel arayüz kaynağı gerekli"}), 403
        except ValueError:
            return jsonify({"hata": "Geçersiz katkı isteği kaynağı"}), 403
    return None

# ---------------------------------------------------------------- durum ----
KILIT = threading.Lock()
PICKER_JOB_LOCK = threading.Lock()
PICKER_JOBS = {}
PICKER_ACTIVE_JOB = None
PICKER_SHUTDOWN = threading.Event()
PICKER_TIMEOUT_SECONDS = 300
ISLER = []                 # eklenme sırasıyla tüm işler (dict listesi)
LOG = deque(maxlen=LOG_LIMIT)
KATKI_ONIZLEMELERI = {}
LOG_SAYAC = 0
IS_SAYAC = 0
CALISAN = {}               # {"pid": int, "is_id": str, "process": Popen} — çalışan süreç
IPTAL_SETI = set()         # iptali istenen iş id'leri
SUNUCU_BASLANGIC = time.time()

# hardsub2srt.py stdout/stderr biçimleri (kaynaktan doğrulandı):
#   "    %17 (16/93 seg) ~kalan 88 sn"
#   "[auto] bant: y=..., h=... | ..."            (stderr, note())
#   "[!] ..."                                    (stderr, note())
#   "[3/3] SRT yazildi: <out>"
#   "    guveni dusuk blok (conf<0.6): 45/303"
#   "    istatistik: <out>.stats.json"
#   "    QA montajlari: qa/ (4 adet)"
RE_ILERLEME = re.compile(r"%(\d+) \((\d+)/(\d+) seg\) ~kalan (\d+)")
RE_DUSUK = re.compile(r"guveni dusuk(?: blok)? \(conf<[0-9.]+\): (\d+)/(\d+)")
RE_YAZILDI = re.compile(r"SRT yazildi:\s*(.+?)\s*$")
RE_ISTATISTIK = re.compile(r"istatistik:\s*(.+?)\s*$")
RE_QA = re.compile(r"QA montajlari:\s*(.+?)\s*\((\d+) adet\)")
RE_REVIEW_PACK = re.compile(r"^AI inceleme paketi yolu:\s*(.*)$")


def log_ekle(seviye, metin, is_id=None):
    global LOG_SAYAC
    with KILIT:
        LOG_SAYAC += 1
        LOG.append({
            "n": LOG_SAYAC,
            "t": datetime.now().strftime("%H:%M:%S"),
            "sev": seviye,
            "is": is_id,
            "m": metin,
        })


def satir_seviyesi(satir):
    t = satir.strip()
    if t.startswith("[auto]"):
        return "auto"
    if t.startswith("[!]"):
        return "uyari"
    if "SRT yazildi" in t or t.startswith("[ass]"):
        return "tamam"
    if t.startswith("Traceback") or "HATA" in t or t.startswith("[ui-hata]"):
        return "hata"
    return "bilgi"


def is_olustur(yol, sec, is_id=None, enqueue_seq=None):
    global IS_SAYAC
    if is_id is None:
        with KILIT:
            IS_SAYAC += 1
            is_id = f"is-{IS_SAYAC}"
            enqueue_seq = IS_SAYAC
    elif enqueue_seq is None:
        try:
            enqueue_seq = int(is_id.rsplit("-", 1)[1])
        except (ValueError, IndexError):
            enqueue_seq = None
    return {
        "id": is_id,
        "enqueue_seq": enqueue_seq,
        "yol": str(yol),
        "ad": Path(yol).name,
        "durum": "bekliyor",           # bekliyor|çalışıyor|bitti|hata|iptal
        "sec": dict(sec),
        "yuzde": 0,
        "seg_a": 0,
        "seg_b": 0,
        "kalan_sn": None,
        "auto": "",                    # "bant:" içeren [auto] satırı
        "auto_son": "",                # son [auto] satırı (bant yoksa)
        "uyarilar": [],
        "dusuk_a": None,
        "dusuk_b": None,
        "srt": None,
        "review_pack": None,
        "run_dir": None,
        "qa_root": None,
        "ass": None,
        "stats": None,
        "qa_dosyalari": [],
        "vtt": None,                   # VTT kıyas özeti (vtt-qa raporundan)
        "hata": "",
        "rc": None,
        "basladi": None,
        "bitti": None,
        "sure_sn": None,
    }


def is_snapshot(isim):
    """İşin kopyasını döndürür. ⚠️ Yalnız KILIT tutulurken çağrılır
    (kilit içinde kilit = threading.Lock'ta deadlock)."""
    s = dict(isim)
    s.pop("_diag_phase", None)
    s["uyarilar"] = list(isim["uyarilar"])
    s["sec"] = dict(isim["sec"])
    s["vtt"] = dict(isim["vtt"]) if isim.get("vtt") else None
    return s


# --------------------------------------------------------------- worker ----
def isci():
    """Kuyruğu SIRALI işleyen thread — bir iş bitmeden diğeri başlamaz."""
    while True:
        isim = None
        with KILIT:
            for k in ISLER:
                if k["durum"] == "bekliyor":
                    k["durum"] = "çalışıyor"
                    k["basladi"] = time.time()
                    k["_diag_phase"] = "setup"
                    isim = k
                    break
        if isim is None:
            time.sleep(0.4)
            continue
        _job_history_record(isim, status="running", phase="setup")
        try:
            is_yurut(isim)
        except Exception as e:                      # worker ölmesin
            cleaned = _worker_failed(isim, e)
            if not cleaned:
                _wait_for_failed_child_cleanup(isim)


def _worker_failed(isim, exc):
    with KILIT:
        isim["hata"] = f"worker istisnası: {exc!r}"
    cleaned = _stop_failed_child(isim["id"])
    if cleaned:
        _finalize_worker_failure(isim)
    else:
        with KILIT:
            isim["durum"] = "çalışıyor"
        _job_history_record(isim, status="running", phase="child_running")
    log_ekle("hata", f"worker istisnası: {exc!r}", isim["id"])
    return cleaned


def _wait_for_failed_child_cleanup(isim):
    """Block the serial worker until the failed child's process is reaped."""
    while not _stop_failed_child(isim["id"]):
        time.sleep(0.5)
    if isim.get("durum") != "hata":
        _finalize_worker_failure(isim)


def _close_job_owner_and_clear(is_id, owner):
    """Retry handle close and retain the active slot until OS confirmation."""
    warned = False
    while owner is not None and getattr(owner, "handle", None) is not None:
        try:
            owner.close()
        except Exception:
            if not warned:
                log_ekle("uyari", "Windows Job Object handle kapanışı bekleniyor", is_id)
                warned = True
            time.sleep(0.5)
    with KILIT:
        if CALISAN.get("is_id") == is_id:
            CALISAN.clear()


def _finalize_worker_failure(isim):
    with KILIT:
        isim["durum"] = "hata"
        phase = isim.get("_diag_phase", "setup")
    error_code = {
        "setup": "setup_failed",
        "process_start": "process_start_failed",
    }.get(phase, "worker_failed")
    _job_history_record(isim, status="failed", phase="complete",
                        error_code=error_code, finish=True)


def _stop_failed_child(is_id):
    """Stop and reap a child before making its failed job terminal."""
    with KILIT:
        child = CALISAN.get("process") if CALISAN.get("is_id") == is_id else None
        owner = CALISAN.get("job_owner") if CALISAN.get("is_id") == is_id else None
        if child is None:
            return CALISAN.get("is_id") != is_id
        pid = CALISAN.get("pid")
    try:
        if owner is not None:
            owner.close()
        alive = child.poll() is None
        if alive and os.name == "nt" and pid:
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10, env=_child_process_env())
            except Exception:
                pass
        if child.poll() is None:
            try:
                child.terminate()
            except Exception:
                pass
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        else:
            # wait() reaps an already-exited process too.
            child.wait(timeout=10)
        if child.poll() is None:
            return False
    except Exception:
        return False
    finally:
        try:
            if child.stdout:
                child.stdout.close()
        except Exception:
            pass
    with KILIT:
        if CALISAN.get("is_id") == is_id:
            CALISAN.clear()
    return True


def _job_history_record(isim, *, status, phase, error_code=None,
                        return_code=None, finish=False):
    """Best-effort persistence; only enum values and numeric metadata leave this scope."""
    if JOB_HISTORY is None:
        return
    started = isim.get("basladi")
    duration = None
    if finish and started is not None:
        duration = max(0, time.time() - started)
    try:
        JOB_HISTORY.record(
            isim.get("id"), status=status, phase=phase,
            cpu=bool(isim.get("sec", {}).get("cpu")),
            duration_seconds=duration, return_code=return_code,
            error_code=error_code,
        )
    except Exception as exc:
        # Keep even diagnostic failures out of durable logs and user data.
        print(f"[ui] kalıcı iş tanılaması yazılamadı ({type(exc).__name__})")


def is_yurut(isim):
    sec = isim["sec"]
    video = Path(isim["yol"])
    cikti_k = Path(sec["cikti_klasor"]).expanduser().resolve()
    cikti_k.mkdir(parents=True, exist_ok=True)
    run_dir = _run_klasoru(cikti_k, video, isim["id"])
    onek = sec.get("onek") or ""
    srt = run_dir / f"{onek}{video.stem}.srt"
    qa_dir = run_dir / "qa"
    with KILIT:
        isim["run_dir"] = str(run_dir)
        isim["qa_root"] = str(qa_dir)
        isim["srt"] = str(srt)

    cmd = [sys.executable, "-u", str(ARAC), str(video), "-o", str(srt)]
    if sec.get("cpu", False):
        cmd.append("--cpu")
    if sec.get("ust_bant"):
        cmd.append("--ust-bant")
    if sec.get("dil"):
        cmd += ["--lang", str(sec["dil"])]
    qa_sayi = int(sec.get("qa_sayi") or 0)
    if qa_sayi > 0:
        cmd += ["--qa", str(qa_sayi), "--qa-dir", str(qa_dir)]
    if sec.get("review_pack", False):
        cmd.append("--review-pack")
    limit = float(sec.get("limit_saniye") or 0)
    if limit > 0:
        cmd += ["--limit-seconds", str(limit)]

    ortam = _child_process_env()
    ortam["PYTHONUNBUFFERED"] = "1"
    ortam["PYTHONIOENCODING"] = "utf-8"

    log_ekle("bilgi", f"iş başladı: {video.name} -> {srt.name}", isim["id"])
    log_ekle("bilgi", "komut: " + " ".join(cmd), isim["id"])

    with KILIT:
        isim["_diag_phase"] = "process_start"
    _job_history_record(isim, status="running", phase="process_start")
    popen_options = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,        # note() stderr'e yazıyor → birleştir
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "cwd": str(KLASOR),
        "env": ortam,
    }
    if os.name == "nt":
        # Keep OCR dormant until the Job Object owns the process tree.
        popen_options["creationflags"] = CREATE_SUSPENDED
    surec = subprocess.Popen(cmd, **popen_options)
    # Track immediately, so even a failed Job Object assignment can fall back
    # to explicit termination/reap before the job is marked terminal.
    with KILIT:
        CALISAN["pid"] = surec.pid
        CALISAN["is_id"] = isim["id"]
        CALISAN["process"] = surec
        CALISAN["job_owner"] = None
    job_owner = None
    if os.name == "nt":
        if WindowsJobObject is None:
            try:
                surec.terminate()
                surec.wait(timeout=5)
            except Exception:
                try:
                    surec.kill()
                    surec.wait(timeout=5)
                except Exception:
                    pass
            raise RuntimeError("Windows Job Object yönetimi kullanılamıyor; iş güvenli biçimde başlatılamadı")
        job_owner = WindowsJobObject(surec)
    with KILIT:
        CALISAN["job_owner"] = job_owner
        isim["_diag_phase"] = "child_running"
    _job_history_record(isim, status="running", phase="child_running")
    log_ekle("bilgi", f"süreç başladı (pid {surec.pid})", isim["id"])

    stats_yol = None
    qa_adet = None
    while True:
        ham = surec.stdout.readline()
        if ham == "":
            break
        satir = ham.rstrip("\r\n")
        if not satir.strip():
            continue
        seviye = satir_seviyesi(satir)
        t = satir.strip()
        with KILIT:
            m = RE_ILERLEME.search(satir)
            if m:
                isim["yuzde"] = int(m.group(1))
                isim["seg_a"] = int(m.group(2))
                isim["seg_b"] = int(m.group(3))
                isim["kalan_sn"] = int(m.group(4))
            if t.startswith("[auto]"):
                isim["auto_son"] = t
                if "bant:" in t or "aday bant" in t or "donuldu" in t:
                    isim["auto"] = t
            if t.startswith("[!]"):
                if t not in isim["uyarilar"]:
                    isim["uyarilar"].append(t)
            m = RE_DUSUK.search(satir)
            if m:
                isim["dusuk_a"] = int(m.group(1))
                isim["dusuk_b"] = int(m.group(2))
            m = RE_YAZILDI.search(satir)
            if m and not t.startswith("[ust"):
                isim["srt"] = m.group(1).strip()
            m = RE_ISTATISTIK.search(satir)
            if m:
                stats_yol = m.group(1).strip()
            m = RE_QA.search(satir)
            if m:
                qa_adet = int(m.group(2))
            m = RE_REVIEW_PACK.search(satir)
            if m:
                isim["review_pack"] = m.group(1).strip()
        log_ekle(seviye, satir, isim["id"])

    rc = surec.wait()
    with KILIT:
        active_owner = CALISAN.get("job_owner") if CALISAN.get("is_id") == isim["id"] else None
    isim["rc"] = rc
    with KILIT:
        isim["_diag_phase"] = "child_exit"
    _job_history_record(isim, status="running", phase="child_exit", return_code=rc)
    _close_job_owner_and_clear(isim["id"], active_owner)
    isim["bitti"] = time.time()
    if isim["basladi"]:
        isim["sure_sn"] = round(isim["bitti"] - isim["basladi"], 1)

    iptal = isim["id"] in IPTAL_SETI
    if iptal:
        isim["durum"] = "iptal"
        isim["hata"] = "kullanıcı iptal etti"
        log_ekle("uyari", f"iş iptal edildi (rc={rc})", isim["id"])
        _job_history_record(isim, status="cancelled", phase="complete",
                            error_code="cancelled", return_code=rc, finish=True)
        return

    if rc != 0:
        isim["durum"] = "hata"
        isim["hata"] = f"süreç rc={rc} ile bitti (ayrıntı: log)"
        log_ekle("hata", f"iş hatalı bitti (rc={rc}): {video.name}", isim["id"])
        _job_history_record(isim, status="failed", phase="complete",
                            error_code="child_nonzero", return_code=rc, finish=True)
        return

    srt_yol = Path(isim["srt"]) if isim["srt"] else srt
    if not srt_yol.exists():
        isim["durum"] = "hata"
        isim["hata"] = f"SRT üretilmedi: {srt_yol}"
        log_ekle("hata", isim["hata"], isim["id"])
        _job_history_record(isim, status="failed", phase="complete",
                            error_code="missing_srt", return_code=rc, finish=True)
        return

    # stats.json (aracın kendi çıktısı: <out>.stats.json)
    sp = Path(stats_yol) if stats_yol else srt_yol.with_suffix(".stats.json")
    try:
        isim["stats"] = json.loads(sp.read_text(encoding="utf-8"))
    except Exception as e:
        log_ekle("uyari", f"stats.json okunamadı ({sp.name}): {e!r}", isim["id"])

    # QA montaj listesi (iş başlangıcından sonra değişenler)
    try:
        sinir = (isim["basladi"] or time.time()) - 5
        dosyalar = []
        if qa_dir.is_dir():
            for f in qa_dir.rglob("*.png"):
                if f.stat().st_mtime >= sinir:
                    dosyalar.append(f.relative_to(qa_dir).as_posix())
        isim["qa_dosyalari"] = sorted(dosyalar)
    except Exception as e:
        log_ekle("uyari", f"qa listesi okunamadı: {e!r}", isim["id"])

    # VTT referans kıyas (vtt-qa.py çağrısı — dosyalar değişmez, rapor yazar)
    vtt_kiyas(isim, srt_yol, video)

    isim["durum"] = "bitti"
    log_ekle("tamam", f"iş bitti: {srt_yol.name} "
                      f"(sure {isim['sure_sn']} sn, qa {qa_adet if qa_adet is not None else len(isim['qa_dosyalari'])})",
             isim["id"])
    _job_history_record(isim, status="succeeded", phase="complete",
                        return_code=rc, finish=True)

    # ASS üretimi (srt2ass.py çağrısı — dosya değiştirilmez)
    if sec.get("ass"):
        ass_yol = srt_yol.with_suffix(".ass")
        log_ekle("bilgi", f"ASS üretimi başlıyor: {ass_yol.name}", isim["id"])
        try:
            r = subprocess.run(
                [sys.executable, "-u", str(SRT2ASS), str(srt_yol),
                 "-o", str(ass_yol)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, cwd=str(KLASOR),
                env=_child_process_env(),
            )
            cikti = (r.stdout or "") + (r.stderr or "")
            for ln in cikti.splitlines():
                if ln.strip():
                    log_ekle("tamam" if ln.startswith("[ass]") else "bilgi",
                             ln.strip(), isim["id"])
            if r.returncode == 0 and ass_yol.exists():
                isim["ass"] = str(ass_yol)
            else:
                isim["uyarilar"].append(f"ASS üretilemedi (rc={r.returncode})")
        except Exception as e:
            isim["uyarilar"].append(f"ASS üretim istisnası: {e!r}")


def _run_klasoru(cikti_k, video, is_id):
    """Her UI işi için önceki çıktılara dokunmayan yeni bir klasör açar."""
    kok = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", video.stem).rstrip(" .")
    kok = (kok[:80] or "video")
    # "video-" öneki Windows'un CON/PRN/NUL gibi aygıt adlarını önler.
    grup = cikti_k / "runs" / ("video-" + kok)
    zaman = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for deneme in range(1000):
        ek = "" if deneme == 0 else f"_{deneme}"
        hedef = grup / f"{zaman}_{is_id}{ek}"
        try:
            hedef.mkdir(parents=True, exist_ok=False)
            return hedef
        except FileExistsError:
            continue
    raise FileExistsError(f"benzersiz koşu klasörü açılamadı: {grup}")


def vtt_bul(video, onek=""):
    """Videonun dizininde referans VTT ara: <ad>.tr.vtt → <ad>.en.vtt →
    <onek><ad>.tr.vtt → <onek><ad>.en.vtt (öneklisi işaretli test koşuları için)."""
    adaylar = [video.with_name(f"{video.stem}.{dil}.vtt") for dil in ("tr", "en")]
    if onek:
        adaylar += [video.with_name(f"{onek}{video.stem}.{dil}.vtt")
                    for dil in ("tr", "en")]
    for a in adaylar:
        if a.is_file():
            return a
    return None


def vtt_kiyas(isim, srt_yol, video):
    """İş bitiminde referans VTT varsa vtt-qa.py'yi koşturur (CPU işi, kısa).
    SRT/VTT yalnız OKUNUR; yazma yalnız <srt>.vtt-rapor.json'a. Sonuç işin
    'vtt' alanına özet olarak yazılır; VTT yoksa hiçbir şey yapmaz."""
    onek = isim["sec"].get("onek") or ""
    vtt = vtt_bul(video, onek)
    if vtt is None:
        return
    rapor = srt_yol.with_name(srt_yol.name + ".vtt-rapor.json")
    log_ekle("bilgi", f"VTT kıyası başlıyor: {vtt.name} → {rapor.name}", isim["id"])
    try:
        r = subprocess.run(
            [sys.executable, "-u", str(VTT_QA), str(srt_yol), str(vtt),
             "-o", str(rapor)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300, cwd=str(KLASOR),
            env=_child_process_env(),
        )
        if r.returncode == 0 and rapor.exists():
            j = json.loads(rapor.read_text(encoding="utf-8"))
            cer = j.get("cer", {}).get("corpus")
            isim["vtt"] = {
                "vtt": vtt.name,
                "rapor": str(rapor),
                "cer": cer,
                "recall": j.get("recall", {}).get("rate"),
                "precision": j.get("precision", {}).get("rate"),
                "eslesme": j.get("counts", {}).get("aligned_pairs"),
                "vtt_cue": j.get("counts", {}).get("vtt_cues"),
                "uyum_uyari": bool(j.get("timing", {}).get("compat_warning")),
            }
            cer_s = "?" if cer is None else f"%{cer * 100:.1f}"
            log_ekle("tamam", f"VTT kıyası bitti: CER {cer_s}, "
                              f"eşleşme {isim['vtt']['eslesme']}/"
                              f"{isim['vtt']['vtt_cue']} cue",
                     isim["id"])
        else:
            detay = ((r.stdout or "") + (r.stderr or "")).strip()[-200:]
            isim["uyarilar"].append(
                f"VTT kıyası başarısız (rc={r.returncode}): {detay}")
            log_ekle("hata", f"VTT kıyası başarısız (rc={r.returncode}): {detay}",
                     isim["id"])
    except Exception as e:
        isim["uyarilar"].append(f"VTT kıyas istisnası: {e!r}")
        log_ekle("hata", f"VTT kıyas istisnası: {e!r}", isim["id"])


# ------------------------------------------------------------------ HTML ---
# (tek sayfa, koyu tema, Türkçe; tarayıcı dosya yolu veremediği için
#  yol yapıştırma + klasör tarama kullanılır)
HTML = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hardsub Altyazı Arayüzü</title>
<style>
:root{
  --bg:#12141a; --panel:#1b1f29; --panel2:#22273500; --cizgi:#2c3345;
  --metin:#dde3f0; --soluk:#8b94ab; --mavi:#4f8cff; --yesil:#3fb96f;
  --sari:#e5b567; --kirmizi:#e5686a; --turuncu:#e58a4f;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--metin);
  font:14px/1.45 "Segoe UI",system-ui,sans-serif}
header{display:flex;align-items:center;gap:12px;padding:10px 18px;
  border-bottom:1px solid var(--cizgi);background:#151822;position:sticky;top:0;z-index:5}
header h1{font-size:17px;margin:0;font-weight:600}
header .klasor{color:var(--soluk);font-size:12px;max-width:44%;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#baglanti{margin-left:auto;font-size:12px;color:var(--soluk)}
#baglanti.ok{color:var(--yesil)} #baglanti.kopuk{color:var(--kirmizi)}
.app-nav{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.app-nav a{color:var(--soluk);text-decoration:none;padding:7px 10px;border-radius:6px;font-size:13px;font-weight:600}
.app-nav a:hover{background:#222a3a;color:var(--metin)}
.app-nav a[aria-current="page"]{background:#243758;color:#a9c9ff}
:focus-visible{outline:2px solid #78aaff;outline-offset:2px}
main{padding:14px 18px 40px;display:grid;gap:14px;
  grid-template-columns:minmax(0,min(420px,34vw)) minmax(0,1fr);align-items:start}
@media (max-width:960px){main{grid-template-columns:minmax(0,1fr)}}
@media (min-width:601px) and (max-width:1350px){
  header h1{white-space:nowrap}
  header .klasor{max-width:28%}
  header .app-nav{order:6;flex-basis:100%;overflow-x:auto;flex-wrap:nowrap}
  header .app-nav a{white-space:nowrap}
}
@media (max-width:600px){
  header{gap:8px;padding:10px 12px;flex-wrap:wrap}
  header h1{font-size:15px}
  header .klasor{order:4;flex-basis:100%;max-width:100%}
  #baglanti{margin-left:auto}
  .app-nav{order:5;flex-basis:100%;overflow-x:auto;flex-wrap:nowrap}
  .app-nav a{white-space:nowrap;padding:6px 8px;font-size:12px}
  main{padding:10px 10px 28px;gap:10px}
  .kart{padding:12px}
  .secenekler{grid-template-columns:minmax(0,1fr)}
  .device-choices{grid-template-columns:minmax(0,1fr)}
  .satir{align-items:stretch;flex-wrap:wrap}
  .satir>*{min-width:0}
  .satir button{flex:1 0 100%}
  #kuyruk-alan table{min-width:700px}
  #log{height:34vh;min-height:150px}
  .editor-panel{width:96vw;max-height:92vh;padding:10px}
}
.kart{background:var(--panel);border:1px solid var(--cizgi);
  border-radius:10px;padding:14px}
.queue-card{align-self:start;min-width:0}
.kart h2{font-size:16px;margin:0 0 10px;color:var(--metin);
  text-transform:none;letter-spacing:0;font-weight:650}
label{font-size:12px;color:var(--soluk);display:block;margin:8px 0 3px}
textarea,input[type=text],input[type=number]{
  width:100%;background:#0f1218;color:var(--metin);border:1px solid var(--cizgi);
  border-radius:6px;padding:7px 9px;font:13px/1.5 Consolas,monospace}
textarea{min-height:96px;resize:vertical}
input:focus,textarea:focus{outline:1px solid var(--mavi)}
.satir{display:flex;gap:8px;align-items:center}
.satir>*{flex:1}
button{background:var(--mavi);color:#fff;border:0;border-radius:6px;
  padding:8px 14px;font-size:13px;cursor:pointer;font-weight:600}
button.ikincil{background:#2a3145}
button.kirmizi{background:var(--kirmizi)}
header #iptal-btn{flex:0 0 auto;white-space:nowrap}
button:disabled{opacity:.45;cursor:not-allowed}
button:hover:not(:disabled){filter:brightness(1.12)}
.not{font-size:12px;color:var(--soluk);border-left:3px solid var(--sari);
  padding:6px 10px;background:#20242f;border-radius:0 6px 6px 0;margin:10px 0}
details>summary{cursor:pointer;color:var(--metin);font-size:13px;font-weight:600}
.katla{border-top:1px solid var(--cizgi);margin-top:12px;padding-top:10px}
.device-box{border:1px solid var(--cizgi);border-radius:8px;padding:10px 12px;margin:10px 0;background:#171b24}
.device-title{font-size:12px;color:var(--soluk);margin-bottom:7px}
.device-choices{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.device-choice{display:flex;align-items:flex-start;gap:8px;padding:9px 10px;border:1px solid var(--cizgi);border-radius:7px;background:#10131a;cursor:pointer}
.device-choice input{width:auto;margin:3px 0 0;accent-color:var(--mavi)}
.device-choice b{display:block;color:var(--metin);font-size:13px}
.device-choice small{display:block;color:var(--soluk);font-size:11px;font-weight:400}
.form-msg{min-height:18px;margin-top:8px;font-size:12px;color:var(--soluk)}
.form-msg.ok{color:var(--yesil)} .form-msg.warn{color:var(--sari)} .form-msg.err{color:var(--kirmizi)}
.bolum-baslik{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}
.bolum-baslik h2{margin:0}
#kuyruk-alan{overflow-x:auto}
#kuyruk-alan table{min-width:760px}
tr.kuyruk-aktif{background:#1a2840}
.mod-chip{display:inline-block;border-radius:12px;padding:2px 8px;font-size:11px;font-weight:600;margin-top:4px}
.mod-gpu{background:#1b355d;color:#a9c9ff}.mod-cpu{background:#423017;color:#ffd18a}
.empty-state{display:flex;align-items:center;gap:10px;flex-wrap:wrap;border:1px dashed var(--cizgi);border-radius:8px;padding:12px}
.empty-state strong{color:var(--metin)}
.empty-state span{color:var(--soluk);font-size:12px;flex:1}
.log-summary{padding:2px 0 8px;list-style:none}
.log-summary::-webkit-details-marker{display:none}
.log-summary:after{content:"▾";float:right;color:var(--soluk)}
#log-details:not([open]) .log-summary:after{content:"▸"}
.secenekler{display:grid;grid-template-columns:1fr 1fr;gap:4px 14px}
.chk{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--metin);
  margin:8px 0 0}
.chk input{width:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--cizgi);
  vertical-align:top}
th{color:var(--soluk);font-weight:600;font-size:11px;text-transform:uppercase}
.durum{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;
  font-weight:600;white-space:nowrap}
.d-bekliyor{background:#2a3145;color:#aab3c8}
.d-çalışıyor{background:#1d3a66;color:#7fb0ff}
.d-bitti{background:#173d2a;color:#5fd598}
.d-hata{background:#4a1f21;color:#ff9c9e}
.d-iptal{background:#4a3317;color:#ffc07a}
.bar{background:#0f1218;border:1px solid var(--cizgi);border-radius:5px;
  height:16px;overflow:hidden;position:relative;min-width:170px}
.bar>div{height:100%;background:linear-gradient(90deg,#2e5fb7,#4f8cff);
  transition:width .6s}
.bar>span{position:absolute;inset:0;font-size:11px;display:flex;
  align-items:center;justify-content:center;color:#eaf0ff;text-shadow:0 1px 2px #000}
.auto{color:#7fb0ff;font-family:Consolas,monospace;font-size:12px}
.uyari{color:var(--sari);font-family:Consolas,monospace;font-size:12px}
.kucuk{color:var(--soluk);font-size:12px}
.sonet{color:var(--yesil)}
#log{background:#0c0e13;border:1px solid var(--cizgi);border-radius:8px;
  height:260px;overflow-y:auto;padding:8px 10px;font:12px/1.55 Consolas,monospace}
.lg{white-space:pre-wrap;word-break:break-all}
.lg-t{color:#5b647a;margin-right:7px}
.lg-is{color:#5b647a;margin-right:7px}
.sev-bilgi{color:#b9c2d8} .sev-auto{color:#7fb0ff} .sev-uyari{color:var(--sari)}
.sev-hata{color:#ff9c9e} .sev-tamam{color:#5fd598}
.sonuc{border:1px solid var(--cizgi);border-radius:8px;padding:10px 12px;
  margin-bottom:10px;background:#181c26}
.sonuc.hatali{border-color:#5a2a2c}
.sonuc .baslik{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.sonuc .yol{font-family:Consolas,monospace;font-size:12px;color:#9fd0a5;
  word-break:break-all}
.cipler{display:flex;gap:8px;flex-wrap:wrap;margin:7px 0}
.cip{background:#232a3a;border-radius:14px;padding:2px 10px;font-size:12px}
.cip b{color:#fff}
.montajlar{display:flex;gap:8px;flex-wrap:wrap;margin-top:7px}
.montajlar img{height:74px;border-radius:5px;border:1px solid var(--cizgi);
  display:block}
.tarama{max-height:150px;overflow-y:auto;border:1px solid var(--cizgi);
  border-radius:6px;padding:6px 8px;margin-top:8px;display:none}
.tarama label{display:flex;gap:7px;align-items:center;margin:3px 0;
  color:var(--metin);font-size:12px;font-family:Consolas,monospace}
.bos{color:var(--soluk);font-size:13px;padding:10px 4px}
.summary-row{display:flex;align-items:center;justify-content:space-between;gap:10px;cursor:pointer;color:var(--metin);font-size:15px;font-weight:650;text-transform:none;letter-spacing:0}
.summary-row .kucuk{font-weight:400;text-transform:none;letter-spacing:0}
.summary-row:focus-visible{outline:2px solid #78aaff;outline-offset:3px}
.view-production-hidden{display:none!important}
/* blok editörü paneli */
.editor-kapla{position:fixed;inset:0;background:rgba(4,6,11,.72);z-index:60;
  display:none;align-items:center;justify-content:center}
.editor-kapla.acik{display:flex}
.editor-panel{width:min(920px,94vw);max-height:86vh;display:flex;flex-direction:column;
  gap:8px;background:var(--panel);border:1px solid var(--cizgi);
  border-radius:10px;padding:14px}
.editor-ust{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.editor-ust b{font-size:14px;max-width:42%;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
#editor-bloklar{overflow-y:auto;display:flex;flex-direction:column;gap:10px;padding:2px}
.e-blok{background:#181c26;border:1px solid var(--cizgi);border-radius:8px;padding:8px 10px}
.e-blok .e-zaman{display:flex;align-items:center;gap:8px;font-family:Consolas,monospace;
  font-size:12px;color:var(--soluk);margin-bottom:5px}
.e-blok textarea{min-height:50px;font-size:13px}
.e-blok button{padding:3px 10px;font-size:11px}
/* VTT kıyas rozetleri (CER eşiği: %5 iyi, %15 orta, üzeri zayıf) */
.vtt-iyi{background:#173d2a;color:#5fd598}
.vtt-orta{background:#4a3317;color:#ffc07a}
.vtt-kotu{background:#4a1f21;color:#ff9c9e}
/* kontrol paneli (v1.3): conf rozetleri + düşük güven vurgusu */
.conf-cip{font-size:11px;border-radius:10px;padding:1px 8px;font-weight:600;
  white-space:nowrap}
.conf-iyi{background:#173d2a;color:#5fd598}
.conf-orta{background:#4a3317;color:#ffc07a}
.conf-dusuk{background:#4a1f21;color:#ff9c9e}
.e-blok.e-dusuk{border-color:#7a3a3c;background:#241a1e}
#editor-banner,#incele-banner{display:none;font-size:12px;color:var(--sari);
  border-left:3px solid var(--sari);padding:6px 10px;background:#20242f;
  border-radius:0 6px 6px 0}
#editor-dusuk.aktif{background:var(--sari);color:#1b1f29}
.incele-metin{font:13px/1.5 Consolas,monospace;white-space:pre-wrap;
  word-break:break-word}
.pano-dusuk{background:#2a1618}
#pano-video-association{margin:8px 0;padding:10px;border:1px solid var(--cizgi);
  border-radius:8px;background:#171b24}
#pano-video-preview{display:block;width:100%;max-height:32vh;margin-top:8px;
  background:#05070b}
#pano-video-status{overflow-wrap:anywhere}
main.quality-view #kuyruk-kart{grid-column:1/-1}
.quality-view .empty-state{min-height:54px}
</style>
</head>
<body>
<header>
  <h1>Hardsub Altyazı Arayüzü</h1>
  <span class="klasor" id="sunucu-klasor" title="Sunucu klasörü (varsayılan çıktı klasörü)"></span>
  <button class="kirmizi" id="iptal-btn" onclick="iptalEt()" disabled>İşi durdur</button>
  <span id="baglanti">bağlanıyor…</span>
  <nav class="app-nav" aria-label="Ana gezinme">
    <a href="/" id="nav-uretim" aria-current="page">Üretim</a>
    <a href="/ocr-inceleme">İnceleme</a>
    <a href="/?view=kalite" id="nav-kalite">Kalite ve öğrenme</a>
    <a href="/katki">İsteğe bağlı katkı</a>
  </nav>
</header>

<main>
  <section class="kart view-production" id="video-kart">
    <h2>Video ekleme</h2>

    <details class="katla">
      <summary>Klasörden video seç</summary>
      <label for="tara-dizin">Video klasörü tara (mp4 / mkv / avi)</label>
      <div class="satir">
        <input type="text" id="tara-dizin" placeholder="ör. C:\Videos">
        <button class="ikincil" data-picker-button onclick="windowsSecim('klasor')">Windows’tan klasör seç</button>
        <button class="ikincil" onclick="klasorTara()">Klasör Tara</button>
      </div>
      <div id="tarama-sonuc" class="tarama"></div>
      <button class="ikincil" id="tarama-ekle" style="display:none;margin-top:8px"
              onclick="secilenleriEkle()">Seçilenleri yol listesine ekle</button>
    </details>

    <label for="yollar">Video yolları (her satıra bir tane yapıştır)</label>
    <button class="ikincil" data-picker-button onclick="windowsSecim('dosya')">Windows’tan videoları seç (çoklu)</button>
    <textarea id="yollar" placeholder="C:\Videos\episode-01.mp4&#10;C:\Videos\episode-02.mp4"></textarea>

    <div class="not">Video yollarını satır satır yapıştırın. Klasör seçici isteğe bağlıdır;
    klasik .bat başlatıcıları da kullanılabilir.</div>

    <fieldset class="device-box">
      <legend class="device-title">OCR modu</legend>
      <div class="device-choices">
        <label class="device-choice"><input type="radio" name="ocr-mod" id="opt-gpu" checked>
          <span><b>GPU · varsayılan</b><small>Uygun CUDA varsa GPU kullanılır.</small></span>
        </label>
        <label class="device-choice"><input type="radio" name="ocr-mod" id="opt-cpu">
          <span><b>CPU · açık seçim</b><small>Seçilirse araca --cpu gönderilir; daha yavaş olabilir.</small></span>
        </label>
      </div>
    </fieldset>

    <details class="katla">
      <summary>Gelişmiş ayarlar</summary>
      <div class="secenekler">
        <label class="chk"><input type="checkbox" id="opt-ust"> Üst yazıları da çıkar (--ust-bant)</label>
        <label class="chk"><input type="checkbox" id="opt-ass"> ASS üret (srt2ass)</label>
        <div>
          <label for="opt-dil">Dil (--lang)</label>
          <input type="text" id="opt-dil" value="tr,en">
        </div>
        <div>
          <label for="opt-qa">QA montaj sayısı</label>
          <input type="number" id="opt-qa" value="4" min="0" max="12">
        </div>
        <label class="chk" style="grid-column:1/-1"><input type="checkbox" id="opt-review-pack">
          Yerel AI inceleme paketi oluştur (final SRT + cue başına tek altyazı kırpımı; otomatik paylaşım yok)</label>
        <div>
          <label for="opt-limit">Test modu — ilk N sn (0 = tüm video)</label>
          <input type="number" id="opt-limit" value="0" min="0" step="30">
        </div>
        <div>
          <label for="opt-onek">Çıktı öneki (opsiyonel, ör. _ui-)</label>
          <input type="text" id="opt-onek" value="">
        </div>
        <div style="grid-column:1/-1">
          <label for="opt-cikti">Çıktı klasörü</label>
          <input type="text" id="opt-cikti" placeholder="sunucu klasörü">
        </div>
      </div>
    </details>

    <div id="ekle-mesaj" class="form-msg" role="status" aria-live="polite"></div>
    <button style="margin-top:4px;width:100%" onclick="kuyrugaEkle()"
            id="ekle-btn">Videoları kuyruğa ekle</button>
  </section>

  <section class="kart queue-card" id="kuyruk-kart">
    <div class="bolum-baslik">
      <h2>Kuyruk</h2>
      <span id="kuyruk-ozet" class="kucuk" aria-live="polite">0 iş</span>
    </div>
    <div id="kuyruk-alan"><div class="empty-state"><strong>Kuyruk boş</strong><span>Video yollarını ekleyince işler sırayla burada görünür.</span></div></div>
  </section>

  <section class="kart view-production" id="sonuc-kart" style="grid-column:1/-1">
    <details id="sonuc-details">
      <summary class="summary-row">Sonuçlar <span id="sonuc-ozet" class="kucuk">Henüz tamamlanan iş yok</span></summary>
      <div id="sonuc-alan"><div class="empty-state"><strong>Henüz sonuç yok</strong><span>Tamamlanan ve hata alan işler burada görünür.</span></div></div>
    </details>
  </section>

  <section class="kart view-production" id="log-kart" style="grid-column:1/-1">
    <details id="log-details">
      <summary class="log-summary">Canlı log <span class="kucuk">son 200 satır · daraltılabilir</span></summary>
      <div id="log"></div>
    </details>
  </section>

  <section class="kart" id="pano-kart" style="grid-column:1/-1;display:none">
    <div class="bolum-baslik"><h2>Kalite ve öğrenme</h2><span class="kucuk">Toplu kalite panosu</span></div>
    <div class="satir">
      <input type="text" id="pano-klasor"
             placeholder="stats.json klasörü (boş = sunucu klasörü)">
      <button class="ikincil" style="flex:0 0 auto" onclick="panoYukle()">Yenile</button>
    </div>
    <div id="pano-ozet" class="cipler" style="margin-top:8px"></div>
    <div id="pano-alan"></div>
  </section>
</main>

<div id="editor-kapla" class="editor-kapla" onclick="if(event.target===this)editorKapat()">
  <div class="editor-panel">
    <div class="editor-ust">
      <b id="editor-ad"></b>
      <input type="text" id="editor-ara" placeholder="metin ya da zaman ara…"
             oninput="editorFiltre()" style="max-width:230px">
      <span id="editor-durum" class="kucuk"></span>
      <button class="ikincil" id="editor-dusuk" style="display:none"
              onclick="dusukTogle()">yalnız düşük güven</button>
      <button style="margin-left:auto" onclick="editorKaydet()">Kaydet</button>
      <button class="ikincil" onclick="editorKapat()">Kapat</button>
    </div>
    <div id="editor-banner"></div>
    <div class="kucuk">Zamanlar salt-okunur. Metni düzenle → "Öğret" düzeltmeyi
      kullanici-sozlugu.txt'e yazar (eski TAB yeni); "Kaydet" SRT'yi
      &lt;ad&gt;.srt.bak yedeğiyle yeniden yazar ve güvenli düzeltmeleri
      OTOMATİK öğrenir (öğrenme raporu bağlantısı kaydetme sonrası görünür;
      serbest yeniden yazım öğrenilmez).</div>
    <div id="editor-bloklar"></div>
  </div>
</div>

<div id="incele-kapla" class="editor-kapla" onclick="if(event.target===this)inceleKapat()">
  <div class="editor-panel">
    <div class="editor-ust">
      <b id="incele-ad"></b>
      <span id="incele-durum" class="kucuk"></span>
      <button class="ikincil" style="margin-left:auto" onclick="inceleKapat()">Kapat</button>
    </div>
    <div id="incele-banner"></div>
    <div id="incele-yardim" class="kucuk">Pano incelemesi salt-okunurdur — düzenleme kuyruktaki
      işin "İncele" / "Kontrol Et" panelinden yapılır. Bu tarihsel görünümde
      kaynak video, koşu kimliğiyle yerel olarak eşleştirilirse açılır; seed görseli
      yalnız birebir eşleşme bulunursa açılır.</div>
    <div id="pano-video-association" style="display:none">
      <label for="pano-video-file">Bu koşunun kaynak videosunu seçin. Ad, boyut ve ilk 1 MiB özeti yalnız tarayıcıda doğrulanır; video sunucuya gönderilmez.</label>
      <input id="pano-video-file" type="file" accept="video/*,.mkv,.avi"
             onchange="panoYerelVideoDogrula(this.files && this.files[0])">
      <div id="pano-video-status" class="kucuk" role="status" aria-live="polite"></div>
      <video id="pano-video-preview" controls playsinline preload="metadata" style="display:none"
             aria-label="Kimliği doğrulanmış yerel kaynak video önizlemesi"></video>
    </div>
    <div id="incele-bloklar"></div>
  </div>
</div>

<script>
"use strict";
const $ = id => document.getElementById(id);
let SON_N = 0;
let IS_ADLARI = {};
let pickerInFlight = false;
let LAST_RESULT_SIGNATURE = "";

function escapeHtml(s){
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

async function api(yol, govde){
  const sec = govde !== undefined
    ? {method:"POST", headers:{"Content-Type":"application/json"},
       body: JSON.stringify(govde ?? {})}
    : undefined;
  const r = await fetch(yol, sec);
  const j = await r.json().catch(() => ({hata:"yanıt okunamadı"}));
  if (!r.ok) throw new Error(j.hata || ("HTTP " + r.status));
  return j;
}

function seceneklerOku(){
  return {
    ust_bant: $("opt-ust").checked,
    ass: $("opt-ass").checked,
    dil: $("opt-dil").value.trim() || "tr,en",
    qa_sayi: parseInt($("opt-qa").value || "4", 10) || 0,
    review_pack: $("opt-review-pack").checked,
    cikti_klasor: $("opt-cikti").value.trim(),
    limit_saniye: parseFloat($("opt-limit").value || "0") || 0,
    onek: $("opt-onek").value.trim(),
    cpu: $("opt-cpu").checked,
  };
}

function ekleMesaji(metin, tur=""){
  const el = $("ekle-mesaj");
  el.textContent = metin;
  el.className = "form-msg" + (tur ? " " + tur : "");
}

async function kuyrugaEkle(){
  const yollar = $("yollar").value.split(/\r?\n/)
    .map(s => s.trim().replace(/^"|"$/g, "")).filter(Boolean);
  if (!yollar.length){
    ekleMesaji("Önce video yolu ekleyin (satır başına bir tane).", "warn");
    $("yollar").focus();
    return;
  }
  const btn = $("ekle-btn"); btn.disabled = true; btn.textContent = "Ekleniyor…";
  try{
    const sec = seceneklerOku(), eklendi = [], hatali = [], yinelenen = [];
    let gonderilmeyen = [], hata = "";
    for (let bas = 0; bas < yollar.length; bas += 100){
      const parca = yollar.slice(bas, bas + 100);
      try{
        const j = await api("/api/ekle", Object.assign({yollar:parca}, sec));
        eklendi.push(...j.eklendi); hatali.push(...j.hatali); yinelenen.push(...j.yinelenen);
      }catch(e){ hata = e.message; gonderilmeyen = yollar.slice(bas); break; }
    }
    const kalan = hatali.map(h => h.yol).concat(gonderilmeyen);
    $("yollar").value = kalan.join("\n");
    let m = eklendi.length + " video kuyruğa eklendi.";
    if (yinelenen.length) m += " Zaten kuyrukta: " + yinelenen.length;
    if (hatali.length) m += " Geçersiz: " + hatali.slice(0, 5).map(h => h.yol + " (" + h.neden + ")").join(", ") +
      (hatali.length > 5 ? " … (" + hatali.length + " toplam; yollar kutuda bırakıldı)." : " (yollar kutuda bırakıldı)." );
    if (gonderilmeyen.length) m += " Kuyruğa girmeyen " + gonderilmeyen.length + " video kaldı: " + hata;
    ekleMesaji(m, hatali.length || gonderilmeyen.length ? "warn" : "ok");
    poll();
  }catch(e){ ekleMesaji("Ekleme hatası: " + e.message, "err"); }
  finally{ btn.disabled = false; btn.textContent = "Videoları kuyruğa ekle"; }
}

async function windowsSecim(tur){
  if (pickerInFlight) return;
  pickerInFlight = true;
  const pickerButtons = [...document.querySelectorAll("[data-picker-button]")];
  const oldLabels = pickerButtons.map(b => b.textContent);
  pickerButtons.forEach(b => { b.disabled = true; b.textContent = "Windows seçicisi açılıyor…"; });
  ekleMesaji("Windows seçicisi başlatılıyor…", "");
  let slowHint = setTimeout(() => {
    ekleMesaji("Seçici hâlâ hazırlanıyor. Windows penceresi başka bir pencerenin arkasında görünüyorsa görev çubuğundan öne alın.", "warn");
  }, 1800);
  try{
    const started = await api("/api/secim", {tur});
    if (!started.job_id) throw new Error("Seçici işi başlatılamadı.");
    let j;
    while (true){
      await new Promise(resolve => setTimeout(resolve, 300));
      const state = await api("/api/secim/" + encodeURIComponent(started.job_id));
      if (state.status === "done"){ j = state.result; break; }
      if (["error", "timeout", "cancelled"].includes(state.status))
        throw new Error(state.hata || "Windows seçicisi tamamlanamadı.");
      const statusText = state.status === "opening"
        ? "Windows seçicisi açılıyor; öne gelmezse görev çubuğundan öne alın."
        : "Windows seçicisi hazırlanıyor…";
      ekleMesaji(statusText, "");
    }
    if (tur === "klasor"){
      if (!j.dizin){ ekleMesaji("Klasör seçimi iptal edildi.", ""); return; }
      $("tara-dizin").value = j.dizin;
      await klasorTara();
    }else if (j.yollar && j.yollar.length){
      const mevcut = $("yollar").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
      const anahtar = new Set(mevcut.map(s => s.toLocaleLowerCase()));
      for (const yol of j.yollar){
        if (!anahtar.has(yol.toLocaleLowerCase())) { mevcut.push(yol); anahtar.add(yol.toLocaleLowerCase()); }
      }
      $("yollar").value = mevcut.join("\n");
      ekleMesaji(j.yollar.length + " dosya seçildi; eklemeden önce listeyi gözden geçirin.", "ok");
    }else ekleMesaji("Dosya seçimi iptal edildi.", "");
  }catch(e){
    const detail = e instanceof TypeError && /fetch/i.test(e.message)
      ? "UI sunucusuyla bağlantı kesildi. Başlatıcı penceresini yeniden açıp tekrar deneyin."
      : e.message;
    ekleMesaji("Dosya/klasör seçimi başarısız: " + detail, "err");
  }
  finally{
    clearTimeout(slowHint);
    pickerButtons.forEach((b, i) => { b.disabled = false; b.textContent = oldLabels[i]; });
    pickerInFlight = false;
  }
}

async function klasorTara(){
  const dizin = $("tara-dizin").value.trim();
  if (!dizin){ alert("Klasör yolu yazın."); return; }
  const kutu = $("tarama-sonuc");
  try{
    const j = await api("/api/klasortara", {dizin});
    kutu.style.display = "block";
    if (!j.videolar.length){
      kutu.innerHTML = '<div class="kucuk">Bu klasörde video bulunamadı.</div>';
      $("tarama-ekle").style.display = "none";
      return;
    }
    kutu.innerHTML = j.videolar.map(v =>
      '<label><input type="checkbox" value="' + escapeHtml(v.yol) + '" checked>' +
      escapeHtml(v.ad) + ' <span class="kucuk">(' + v.mb + ' MB)</span></label>').join("");
    $("tarama-ekle").style.display = "inline-block";
  }catch(e){
    kutu.style.display = "block";
    kutu.innerHTML = '<div class="uyari">Hata: ' + escapeHtml(e.message) + '</div>';
    $("tarama-ekle").style.display = "none";
  }
}

function secilenleriEkle(){
  const secilen = [...$("tarama-sonuc").querySelectorAll("input:checked")]
    .map(i => i.value);
  if (!secilen.length){ alert("Seçili video yok."); return; }
  const mevcut = $("yollar").value.split(/\r?\n/).filter(Boolean);
  $("yollar").value = mevcut.concat(secilen).join("\n");
}

async function iptalEt(){
  if (!confirm("Çalışan süreç öldürülsün mü? (taskkill /T /F)")) return;
  try{
    const j = await api("/api/iptal", {});
    alert("İptal istendi. taskkill rc=" + j.taskkill_rc + " (pid " + j.pid + ")");
    poll();
  }catch(e){ alert("İptal hatası: " + e.message); }
}

function klasorAc(yol){ api("/api/ac", {yol}).catch(e => alert(e.message)); }

function durumEtiketi(d){
  return {bekliyor:"bekliyor", "çalışıyor":"çalışıyor", bitti:"bitti",
          hata:"hata", iptal:"iptal"}[d] || d;
}

function kuyrukCiz(kuyruk, durum){
  IS_ADLARI = {};
  kuyruk.forEach(i => IS_ADLARI[i.id] = i.ad);
  const alan = $("kuyruk-alan");
  const aktif = durum.aktif ? 1 : 0;
  const bekleyen = durum.isler_bekleyen;
  $("kuyruk-ozet").textContent = (aktif ? "1 çalışıyor · " : "") + bekleyen + " bekliyor";
  $("kuyruk-ozet").textContent += " · gösterilen " + durum.kuyruk_gosterilen +
    " / toplam kayıt " + durum.is_kaydi_toplam;
  if (!kuyruk.length){
    alan.innerHTML = '<div class="empty-state"><strong>Kuyruk boş</strong>' +
      '<span>Video yollarını ekleyince işler sırayla burada görünür.</span>' +
      '<button class="ikincil" onclick="$(\'yollar\').focus()">Video ekle</button></div>';
    return;
  }
  let h = '<table><tr><th>#</th><th>Dosya / mod</th><th>Durum</th><th>İlerleme</th>' +
          '<th>Algılama / uyarılar</th></tr>';
  kuyruk.forEach((i, sira) => {
    const enqueueSeq = Number.isInteger(i.enqueue_seq) ? i.enqueue_seq : sira + 1;
    const barText = i.durum === "çalışıyor" || i.durum === "bitti"
      ? "%" + i.yuzde + " · " + i.seg_a + "/" + i.seg_b + " seg" +
        (i.kalan_sn !== null && i.durum === "çalışıyor" ? " · ~" + i.kalan_sn + " sn kaldı" : "")
      : "";
    const uyarilar = (i.uyarilar || []).map(u =>
      '<div class="uyari">' + escapeHtml(u) + '</div>').join("");
    h += '<tr class="' + (i.durum === "çalışıyor" ? "kuyruk-aktif" : "") + '"><td>' + enqueueSeq + "</td>" +
      '<td style="max-width:220px;word-break:break-all">' + escapeHtml(i.ad) +
      '<div class="mod-chip ' + (i.sec.cpu ? "mod-cpu" : "mod-gpu") + '">' +
      (i.sec.cpu ? "CPU · --cpu" : "GPU · varsayılan") + "</div>" +
      (i.sec.limit_saniye > 0 ? ' <span class="kucuk">(ilk ' + i.sec.limit_saniye + ' sn)</span>' : "") +
      (i.sec.onek ? ' <span class="kucuk">[' + escapeHtml(i.sec.onek) + ']</span>' : "") +
      "</td>" +
      '<td><span class="durum d-' + escapeHtml(i.durum) + '">' +
      durumEtiketi(i.durum) + "</span>" +
      (i.sure_sn !== null ? '<div class="kucuk">' + i.sure_sn + ' sn</div>' : "") +
      "</td>" +
      '<td><div class="bar"><div style="width:' + (i.durum === "bitti" ? 100 : i.yuzde) +
      '%"></div><span>' + escapeHtml(barText) + "</span></div>" +
      (i.durum === "hata" || i.durum === "iptal"
        ? '<div class="uyari">' + escapeHtml(i.hata || "") + "</div>" : "") +
      "</td>" +
      '<td style="max-width:340px">' +
      (i.auto ? '<div class="auto">' + escapeHtml(i.auto) + "</div>" : "") +
      uyarilar + "</td></tr>";
  });
  alan.innerHTML = h + "</table>";
}

function vttSatir(i){
  const v = i.vtt;
  if (!v || v.cer === undefined || v.cer === null) return "";
  const p = v.cer * 100;
  const cls = p <= 5 ? "vtt-iyi" : p <= 15 ? "vtt-orta" : "vtt-kotu";
  const yuzde = x => (x === undefined || x === null) ? "?" : (x * 100).toFixed(0) + "%";
  return '<div class="cipler">' +
    '<span class="durum ' + cls + '">VTT kıyas · CER %' + p.toFixed(1) + "</span>" +
    '<span class="cip">precision <b>' + yuzde(v.precision) + "</b></span>" +
    '<span class="cip">recall <b>' + yuzde(v.recall) + "</b></span>" +
    '<span class="cip">eşleşme <b>' + (v.eslesme ?? "?") + "/" + (v.vtt_cue ?? "?") +
    " cue</b></span>" +
    (v.uyum_uyari ? '<span class="cip" style="color:#ffc07a">zaman uyumu şüpheli</span>' : "") +
    '<a class="kucuk" href="/api/vttrapor?is=' + encodeURIComponent(i.id) +
    '" target="_blank">rapor JSON</a>' +
    "</div>";
}

function sonucCiz(kuyruk){
  const alan = $("sonuc-alan");
  const bittiler = kuyruk.filter(i => i.durum === "bitti").slice(-4).reverse();
  const hatalilar = kuyruk.filter(i => i.durum === "hata" || i.durum === "iptal")
                          .slice(-3).reverse();
  const toplam = bittiler.length + hatalilar.length;
  $("sonuc-ozet").textContent = toplam
    ? toplam + " son tamamlanan / hatalı iş"
    : "Henüz tamamlanan iş yok";
  const signature = bittiler.concat(hatalilar).map(i => i.id + ":" + i.durum).join("|");
  if (signature && signature !== LAST_RESULT_SIGNATURE) $("sonuc-details").open = true;
  LAST_RESULT_SIGNATURE = signature;
  if (!bittiler.length && !hatalilar.length){
    alan.innerHTML = '<div class="empty-state"><strong>Henüz sonuç yok</strong>' +
      '<span>Tamamlanan ve hata alan işler burada görünür.</span></div>';
    return;
  }
  let h = "";
  bittiler.forEach(i => {
    const st = i.stats || {};
    const cip = (etiket, deger) => deger === undefined || deger === null ? "" :
      '<span class="cip">' + etiket + ' <b>' + deger + "</b></span>";
    h += '<div class="sonuc"><div class="baslik"><span class="durum d-bitti">bitti</span>' +
      '<span class="mod-chip ' + (i.sec.cpu ? "mod-cpu" : "mod-gpu") + '">' +
      (i.sec.cpu ? "CPU · --cpu" : "GPU · varsayılan") + "</span>" +
      "<b>" + escapeHtml(i.ad) + "</b>" +
      (i.sure_sn !== null ? '<span class="kucuk">' + i.sure_sn + " sn</span>" : "") +
      (st.low_conf > 0 ? '<button style="margin-left:auto;padding:4px 10px" ' +
        'onclick="editorAc(' + JSON.stringify(i.id).replace(/"/g, "&quot;") +
        ', true)">Kontrol Et (' + st.low_conf + ')</button>'
       : '<span style="margin-left:auto"></span>') +
      '<button class="ikincil" style="padding:4px 10px" ' +
      'onclick="editorAc(' + JSON.stringify(i.id).replace(/"/g, "&quot;") +
      ')">İncele</button>' +
      '<button class="ikincil" style="padding:4px 10px" ' +
      'onclick="klasorAc(' + JSON.stringify(i.srt).replace(/"/g, "&quot;") +
      ')">Klasörü Aç</button></div>' +
      '<div class="yol">SRT: ' + escapeHtml(i.srt) + "</div>" +
      (i.review_pack ? '<div class="yol">Yerel AI inceleme paketi: ' +
        escapeHtml(i.review_pack) +
        '<button class="ikincil" style="margin-left:8px;padding:3px 8px" ' +
        'onclick="klasorAc(' + JSON.stringify(i.review_pack).replace(/"/g, "&quot;") +
        ')">Paketi aç</button></div>' : "") +
      (i.ass ? '<div class="yol">ASS: ' + escapeHtml(i.ass) + "</div>" : "") +
      '<div class="cipler">' +
      cip("blok", st.blocks) + cip("düşük güven", st.low_conf) +
      cip("konuşma sn", st.speech_seconds) + cip("fps", st.fps !== undefined ? Number(st.fps).toFixed(2) : undefined) +
      "</div>" +
      vttSatir(i) +
      (i.qa_dosyalari && i.qa_dosyalari.length
        ? '<div class="kucuk">QA montajları:</div><div class="montajlar">' +
          i.qa_dosyalari.slice(0, 6).map(f =>
            '<a href="/qa/' + i.id + "/" + f + '" target="_blank">' +
            '<img src="/qa/' + i.id + "/" + f + '" alt="qa"></a>').join("") +
          "</div>"
        : "") +
      "</div>";
  });
  hatalilar.forEach(i => {
    h += '<div class="sonuc hatali"><div class="baslik"><span class="durum d-' +
      escapeHtml(i.durum) + '">' + durumEtiketi(i.durum) + "</span><b>" +
      escapeHtml(i.ad) + '</b></div><div class="uyari">' + escapeHtml(i.hata || "") +
      "</div></div>";
  });
  alan.innerHTML = h;
}

function logCiz(log){
  const kutu = $("log");
  const sondayiz = kutu.scrollTop + kutu.clientHeight >= kutu.scrollHeight - 70;
  if (!log.length){
    kutu.innerHTML = '<div class="bos">Bir iş başlayınca canlı çıktı burada görünür.</div>';
    return;
  }
  kutu.innerHTML = log.map(l =>
    '<div class="lg"><span class="lg-t">' + l.t + '</span>' +
    (l.is && IS_ADLARI[l.is] ? '<span class="lg-is">[' + escapeHtml(IS_ADLARI[l.is]) + ']</span>' : "") +
    '<span class="sev-' + l.sev + '">' + escapeHtml(l.m) + "</span></div>").join("");
  if (sondayiz) kutu.scrollTop = kutu.scrollHeight;
}

// --- blok editörü + kontrol paneli ----------------------------------------
let ED = null;

function confCip(b, thr){
  if (b.conf === null || b.conf === undefined) return "";
  const t = (thr === null || thr === undefined) ? 0.75 : thr;
  const cls = b.dusuk ? "conf-dusuk" : (b.conf >= 0.85 ? "conf-iyi" : "conf-orta");
  return '<span class="conf-cip ' + cls + '">conf ' + b.conf.toFixed(2) + "</span>";
}

function panoGorselKontrol(block){
  let controls = "";
  const seconds = srtSaniye(block.start);
  if (PANO_VIDEO_IDENTITY && seconds !== null)
    controls += '<button class="ikincil pano-local-seek" type="button" disabled data-video-seconds="' +
      seconds.toFixed(3) + '" title="Önce bu koşunun yerel videosunu seçip doğrulayın; dosya sunucuya gönderilmez." onclick="panoYerelVideodaAc(this)">Videoyu açmak için video seçin</button>';
  else
    controls += '<button class="ikincil" type="button" disabled title="Bu koşuda doğrulanabilir video kimliği yok.">Video yok</button>';
  if (block.evidence_url)
    controls += '<a href="' + escapeHtml(block.evidence_url) + '" target="_blank" rel="noopener">Görsel kanıtı aç</a>';
  else
    controls += '<button class="ikincil" type="button" disabled title="Bu blok için tam eşleşen seed görseli yok.">Görsel yok</button>';
  return '<span class="e-actions">' + controls + '</span>';
}

function panoYerelVideoTemizle(){
  PANO_LOCAL_VIDEO_CHECK++;
  PANO_LOCAL_VIDEO_FILE = null;
  if (PANO_LOCAL_VIDEO_URL) URL.revokeObjectURL(PANO_LOCAL_VIDEO_URL);
  PANO_LOCAL_VIDEO_URL = null;
  const input = $("pano-video-file"), video = $("pano-video-preview");
  if (input) input.value = "";
  if (video){ video.pause(); video.onerror = null; video.removeAttribute("src"); video.load(); video.style.display = "none"; }
  document.querySelectorAll("#incele-bloklar .pano-local-seek").forEach(button => {
    button.disabled = true; button.textContent = "Videoyu açmak için video seçin";
  });
}

async function panoYerelVideoDogrula(file){
  panoYerelVideoTemizle();
  const check = PANO_LOCAL_VIDEO_CHECK;
  const status = $("pano-video-status"), identity = PANO_VIDEO_IDENTITY;
  const container = $("pano-video-association"), input = $("pano-video-file");
  if (container) container.style.display = "";
  if (input) input.disabled = !identity;
  if (!identity){
    if (status) status.textContent = "Bu tarihsel koşunun stats verisinde doğrulanabilir kaynak video kimliği yok; yerel video açma kullanılamıyor.";
    return;
  }
  if (!file){ if (status) status.textContent = "Videoyu açmak için bu koşuya ait yerel video dosyasını seçin."; return; }
  if (status) status.textContent = "Dosya adı, boyutu ve ilk 1 MiB özeti bu tarayıcıda doğrulanıyor…";
  try{
    if (!globalThis.crypto?.subtle || typeof file.slice !== "function")
      throw Error("Bu tarayıcı güvenli SHA-1 dosya özeti hesabını desteklemiyor.");
    if (String(file.name).toLocaleLowerCase("en-US") !== String(identity.ad).toLocaleLowerCase("en-US"))
      throw Error("Dosya adı bu koşunun video kimliğiyle eşleşmiyor.");
    if (file.size !== identity.boyut) throw Error("Dosya boyutu bu koşunun video kimliğiyle eşleşmiyor.");
    const firstMiB = await file.slice(0, 1024 * 1024).arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-1", firstMiB);
    const sha1 = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2,"0")).join("");
    if (check !== PANO_LOCAL_VIDEO_CHECK) return;
    if (sha1.toLowerCase() !== String(identity.sha1_ilk_1MB).toLowerCase())
      throw Error("Dosyanın ilk 1 MiB SHA-1 özeti eşleşmiyor.");
    const objectUrl = URL.createObjectURL(file);
    PANO_LOCAL_VIDEO_FILE = file; PANO_LOCAL_VIDEO_URL = objectUrl;
    const video = $("pano-video-preview");
    video.src = objectUrl; video.style.display = "block";
    video.onerror = () => { if (check === PANO_LOCAL_VIDEO_CHECK && status) status.textContent = "Kimlik eşleşti ancak bu tarayıcı video biçimini/kodeğini oynatamıyor."; };
    if (status) status.textContent = "Video eşleşti. Dosya adı, boyut ve ilk 1 MiB doğrulandı. Video verisi sunucuya gönderilmedi.";
    document.querySelectorAll("#incele-bloklar .pano-local-seek").forEach(button => { button.disabled = false; button.textContent = "Videoda aç"; });
  }catch(e){
    if (check !== PANO_LOCAL_VIDEO_CHECK) return;
    PANO_LOCAL_VIDEO_FILE = null;
    if (status) status.textContent = "Yerel video doğrulanamadı: " + e.message;
  }
}

function panoYerelVideodaAc(button){
  if (!PANO_LOCAL_VIDEO_FILE || !PANO_LOCAL_VIDEO_URL) return;
  const video = $("pano-video-preview"), status = $("pano-video-status");
  const seconds = Number(button.dataset.videoSeconds);
  if (!Number.isFinite(seconds) || seconds < 0) return;
  const seekAndPlay = () => {
    if (!PANO_LOCAL_VIDEO_FILE || !PANO_LOCAL_VIDEO_URL) return;
    video.currentTime = Math.min(seconds, Math.max(0, video.duration || seconds));
    video.scrollIntoView({behavior:"smooth",block:"nearest"}); video.focus({preventScroll:true});
    video.play().catch(() => { if (status) status.textContent = "Blok zamanı hazırlandı; oynatmak için Oynat'a basın."; });
  };
  if (video.readyState >= 1) seekAndPlay();
  else video.addEventListener("loadedmetadata", seekAndPlay, {once:true});
}

async function editorAc(isId, kontrol){
  try{
    const j = await api((kontrol ? "/api/kontrol" : "/api/bloklar") +
                        "?is=" + encodeURIComponent(isId));
    ED = {is_id: isId, srt: j.srt, kontrol: !!kontrol, sadeceDusuk: false,
          confThr: (j.conf_thr === undefined ? null : j.conf_thr),
          ekran: (j.ekran || []),
          bloklar: j.bloklar.map(b => ({start: b.start, end: b.end,
                                        metin: b.metin, orij: b.metin,
                                        conf: (b.conf === undefined ? null : b.conf),
                                        dusuk: !!b.dusuk}))};
    $("editor-ad").textContent = j.srt.split(/[\\/]/).pop() +
      " · " + j.bloklar.length + " blok" +
      (kontrol && j.bloklar_var ? " · düşük güven: " + j.low_conf +
        " (eşik conf<" + (ED.confThr ?? 0.75) + ")" : "");
    $("editor-ara").value = "";
    $("editor-durum").textContent = "";
    $("editor-banner").style.display = j.uyari ? "block" : "none";
    $("editor-banner").textContent = j.uyari || "";
    $("editor-dusuk").style.display =
      (ED.bloklar.some(b => b.dusuk)) ? "inline-block" : "none";
    $("editor-dusuk").classList.remove("aktif");
    editorRender();
    $("editor-kapla").classList.add("acik");
  }catch(e){ alert("İnceleme hatası: " + e.message); }
}

function editorRender(){
  let h = ED.bloklar.map((b, k) =>
    '<div class="e-blok' + (b.dusuk ? " e-dusuk" : "") + '" data-k="' + k + '">' +
    '<div class="e-zaman"><span>#' + (k + 1) + " · " + b.start + " → " + b.end + "</span>" +
    confCip(b, ED.confThr) +
    '<button class="ikincil" style="margin-left:auto" onclick="ogret(' + k + ')">Öğret</button></div>' +
    '<textarea oninput="editorDegisti(' + k + ', this)">' + escapeHtml(b.metin) + "</textarea></div>").join("");
  if (ED.kontrol && ED.ekran.length){
    h += '<div class="kucuk" style="margin-top:8px">Ayrılan (_ekran.srt) bloklar — ' +
      ED.ekran.length + " adet · salt-okunur (düzenleme ana SRT editörünü " +
      "etkiler, _ekran.srt'yi değil):</div>" +
      ED.ekran.map(b =>
        '<div class="e-blok" data-ekran="1">' +
        '<div class="e-zaman"><span>ekran#' + ((b.index ?? -1) + 1) + " · " +
        escapeHtml(b.start || "?") + " → " + escapeHtml(b.end || "?") + "</span>" +
        confCip(b, ED.confThr) + "</div>" +
        '<div class="incele-metin">' + escapeHtml(b.metin) + "</div></div>").join("");
  }
  $("editor-bloklar").innerHTML = h;
  editorFiltre();
}

function dusukTogle(){
  if (!ED) return;
  ED.sadeceDusuk = !ED.sadeceDusuk;
  $("editor-dusuk").classList.toggle("aktif", ED.sadeceDusuk);
  editorFiltre();
}

function editorDegisti(k, ta){
  if (ED) ED.bloklar[k].metin = ta.value;
}

function editorFiltre(){
  if (!ED) return;
  const q = $("editor-ara").value.trim().toLocaleLowerCase("tr");
  [...$("editor-bloklar").children].forEach(el => {
    const k = el.dataset.k;
    if (k === undefined){          // ekran bölümü: süzgeçten bağımsız
      el.style.display = ED.sadeceDusuk ? "none" : "";
      return;
    }
    const b = ED.bloklar[+k];
    let match = !q || b.metin.toLocaleLowerCase("tr").includes(q) ||
      (b.start + " → " + b.end).includes(q);
    if (match && ED.sadeceDusuk) match = b.dusuk;
    el.style.display = match ? "" : "none";
  });
}

function editorKapat(){ $("editor-kapla").classList.remove("acik"); }

async function editorKaydet(){
  if (!ED) return;
  const govde = {is_id: ED.is_id,
    bloklar: ED.bloklar.map((b, k) =>
      ({index: k, start: b.start, end: b.end, metin: b.metin}))};
  try{
    const j = await api("/api/blokkaydet", govde);
    let m = "kaydedildi · " + j.blok_sayisi + " blok · yedek: " +
      escapeHtml(j.yedek);
    if (j.ogrenme && j.ogrenme.hata){
      m += " — öğrenme atlandı: " + escapeHtml(j.ogrenme.hata);
    }else if (j.ogrenme){
      m += " — <b>" + j.ogrenme.ogrenildi + " kelime öğrenildi, " +
        (j.ogrenme.belirsiz + j.ogrenme.atlandi) + " belirsiz (atlandı)</b>" +
        (j.ogrenme.catisma ? " · " + j.ogrenme.catisma + " çatışma (ilk kaldı)" : "") +
        ' · <a href="/api/ogrenmerapor?is=' + encodeURIComponent(ED.is_id) +
        '" target="_blank">öğrenme raporu</a>';
    }
    $("editor-durum").innerHTML = m;
    ED.bloklar.forEach(b => { b.orij = b.metin; });
    poll();
  }catch(e){ $("editor-durum").textContent = "HATA: " + e.message; }
}

async function ogret(k){
  if (!ED) return;
  const b = ED.bloklar[k];
  if (b.metin === b.orij){
    $("editor-durum").textContent = "öğretilecek düzeltme yok (blok değişmedi)";
    return;
  }
  const duz = s => s.replace(/\s+/g, " ").trim();   // sözlük tek satır tutar
  try{
    const j = await api("/api/ogret", {eski: duz(b.orij), yeni: duz(b.metin)});
    $("editor-durum").textContent = "sözlük: " + j.durum;
  }catch(e){ $("editor-durum").textContent = "HATA: " + e.message; }
}

// --- toplu kalite panosu (v1.3) --------------------------------------------
let PANO_ACIK = false;
let PANO_KLASOR = "";
let PANO_VIDEO_IDENTITY = null;
let PANO_LOCAL_VIDEO_FILE = null;
let PANO_LOCAL_VIDEO_URL = null;
let PANO_LOCAL_VIDEO_CHECK = 0;

function panoGoster(acik){
  PANO_ACIK = Boolean(acik);
  const main = document.querySelector("main");
  if (main) main.classList.toggle("quality-view", PANO_ACIK);
  document.querySelectorAll(".view-production").forEach(s =>
    s.classList.toggle("view-production-hidden", PANO_ACIK));
  // Kuyruk ana iş akışının parçası olarak kalite görünümünde de görünür kalır.
  $("pano-kart").style.display = PANO_ACIK ? "" : "none";
  if (PANO_ACIK && !$("pano-klasor").value) panoYukle();
  const nav = $("nav-kalite");
  if (nav) {
    if (PANO_ACIK) nav.setAttribute("aria-current", "page");
    else nav.removeAttribute("aria-current");
  }
  const uretim = $("nav-uretim");
  if (uretim) {
    if (PANO_ACIK) uretim.removeAttribute("aria-current");
    else uretim.setAttribute("aria-current", "page");
  }
  if (PANO_ACIK) $("pano-kart").scrollIntoView({behavior:"smooth", block:"start"});
}

function panoAc(){
  panoGoster(!PANO_ACIK);
}

async function panoYukle(){
  const kl = $("pano-klasor").value.trim();
  const alan = $("pano-alan");
  try{
    const j = await api("/api/pano" +
                        (kl ? "?klasor=" + encodeURIComponent(kl) : ""));
    PANO_KLASOR = j.ozet.klasor || "";
    if (!kl) $("pano-klasor").placeholder = PANO_KLASOR;
    panoCiz(j);
  }catch(e){
    alan.innerHTML = '<div class="uyari">Hata: ' + escapeHtml(e.message) + '</div>';
  }
}

function panoCiz(j){
  const o = j.ozet;
  const cip = (etiket, deger) =>
    '<span class="cip">' + etiket + ' <b>' + deger + "</b></span>";
  $("pano-ozet").innerHTML =
    cip("bölüm", o.toplam_bolum) +
    cip("toplam blok", o.toplam_blok) +
    cip("ort. low-conf", o.ort_low_pct === null ? "?" : "%" + o.ort_low_pct) +
    cip("incelenmesi gereken", o.incelenecek) +
    cip("altyazısız-rip şüphesi", o.rip_supheli);
  if (!j.satirlar.length){
    $("pano-alan").innerHTML =
      '<div class="bos">Bu klasörde .stats.json yok.</div>';
    return;
  }
  let h = '<table><tr><th>dosya</th><th>blok</th><th>low-conf</th>' +
          '<th>konuşma sn</th><th>hardsub</th><th>altyazısız?</th>' +
          '<th>tarih</th><th></th></tr>';
  j.satirlar.forEach(s => {
    const pct = s.low_pct === null ? '<span class="kucuk">—</span>' :
      "%" + s.low_pct.toFixed(1) +
      ' <span class="kucuk">(' + s.low_conf + "/" + s.blok + ")</span>";
    const pctCell = s.low_yuksek ? '<span class="conf-cip conf-dusuk">' +
      "%" + s.low_pct.toFixed(1) + " — incele</span>" : pct;
    h += "<tr" + (s.low_yuksek ? ' class="pano-dusuk"' : "") + ">" +
      '<td style="max-width:280px;word-break:break-all">' +
      escapeHtml(s.ad) +
      (s.eski_format ? ' <span class="kucuk">(eski format)</span>' : "") +
      "</td>" +
      "<td>" + (s.blok === null ? "—" : s.blok) + "</td>" +
      "<td>" + pctCell + "</td>" +
      "<td>" + (s.konusma_sn === null ? "—" : s.konusma_sn) + "</td>" +
      "<td>" + escapeHtml(s.hardsub_shr || "—") + "</td>" +
      "<td>" + (s.altyazisiz ? '<span class="conf-cip conf-dusuk">şüpheli</span>'
                             : '<span class="kucuk">—</span>') + "</td>" +
      "<td>" + escapeHtml(s.tarih || "—") + "</td>" +
      "<td>" + (s.bloklar_var
        ? '<a href="#" class="pano-incele" data-stats="' +
          escapeHtml(s.stats_rel) + '" data-ad="' + escapeHtml(s.ad) +
          '">İncele</a>' : "") +
      "</td></tr>";
  });
  const pano = $("pano-alan");
  pano.innerHTML = h + "</table>" +
    '<div class="kucuk" style="margin-top:6px">low-conf% > %' +
    j.esik + " olanlar üstte ve kırmızı işaretlidir.</div>";
  pano.querySelectorAll("a.pano-incele").forEach(link => {
    link.addEventListener("click", ev => {
      ev.preventDefault();
      inceleAc(link.dataset.stats, link.dataset.ad);
    });
  });
}

async function inceleAc(statsRel, ad){
  panoYerelVideoTemizle();
  PANO_VIDEO_IDENTITY = null;
  try{
    const j = await api("/api/pano/incele?stats=" + encodeURIComponent(statsRel) +
                        "&klasor=" + encodeURIComponent(PANO_KLASOR));
    const identity = j.video_identity;
    PANO_VIDEO_IDENTITY = identity && typeof identity.ad === "string" &&
      Number.isSafeInteger(identity.boyut) && identity.boyut > 0 &&
      /^[a-f0-9]{40}$/i.test(String(identity.sha1_ilk_1MB || ""))
        ? identity : null;
    const videoContainer = $("pano-video-association");
    const videoInput = $("pano-video-file");
    const videoStatus = $("pano-video-status");
    videoContainer.style.display = "";
    videoInput.disabled = !PANO_VIDEO_IDENTITY;
    videoStatus.textContent = PANO_VIDEO_IDENTITY
      ? "Beklenen kaynak: " + PANO_VIDEO_IDENTITY.ad + " · " +
        PANO_VIDEO_IDENTITY.boyut.toLocaleString("tr-TR") + " bayt. Seçilen dosya tarayıcıda doğrulanır; içerik sunucuya yüklenmez."
      : (j.video_unavailable_reason || "Bu tarihsel koşuda doğrulanabilir kaynak video kimliği yok.");
    $("incele-ad").textContent = ad + " · " + j.bloklar.length + " blok";
    $("incele-durum").textContent = "eşik conf<" + (j.conf_thr ?? 0.75) +
      (j.bloklar_var ? " · düşük: " + j.bloklar.filter(b => b.dusuk).length
                     : " · conf yok");
    $("incele-banner").style.display = j.uyari ? "block" : "none";
    $("incele-banner").textContent = j.uyari || "";
    let h = j.bloklar.map(b =>
      '<div class="e-blok' + (b.dusuk ? " e-dusuk" : "") + '">' +
      '<div class="e-zaman"><span>#' + ((b.index ?? -1) + 1) + " · " +
      escapeHtml(b.start || "?") + " → " + escapeHtml(b.end || "?") + "</span>" +
      confCip(b, j.conf_thr) + panoGorselKontrol(b) + "</div>" +
      '<div class="incele-metin">' + escapeHtml(b.metin) + "</div></div>").join("");
    if (j.ekran && j.ekran.length){
      h += '<div class="kucuk" style="margin-top:8px">Ayrılan (_ekran.srt) bloklar — ' +
        j.ekran.length + " adet:</div>" +
        j.ekran.map(b =>
          '<div class="e-blok"><div class="e-zaman"><span>ekran#' +
          ((b.index ?? -1) + 1) + " · " + escapeHtml(b.start || "?") + " → " +
          escapeHtml(b.end || "?") + "</span>" + confCip(b, j.conf_thr) +
          '</div><div class="incele-metin">' + escapeHtml(b.metin) +
          "</div></div>").join("");
    }
    $("incele-bloklar").innerHTML = h;
    $("incele-kapla").classList.add("acik");
  }catch(e){
    PANO_VIDEO_IDENTITY = null;
    $("pano-video-association").style.display = "none";
    alert("İnceleme hatası: " + e.message);
  }
}

function inceleKapat(){
  $("incele-kapla").classList.remove("acik");
  PANO_VIDEO_IDENTITY = null;
  panoYerelVideoTemizle();
  $("pano-video-association").style.display = "none";
}

async function poll(){
  try{
    const j = await api("/api/durum");
    $("baglanti").textContent = "bağlı · pid " + (j.pid ?? "-") + " · " + j.isler_bekleyen + " bekliyor";
    $("baglanti").className = "ok";
    if (!$("opt-cikti").value && j.sunucu && j.sunucu.klasor)
      $("opt-cikti").value = j.sunucu.klasor;
    if (!$("tara-dizin").value && j.sunucu && j.sunucu.klasor)
      $("tara-dizin").value = j.sunucu.klasor;
    $("sunucu-klasor").textContent = j.sunucu ? j.sunucu.klasor : "";
    $("iptal-btn").disabled = !j.pid;
    kuyrukCiz(j.kuyruk, j);
    sonucCiz(j.kuyruk);
    logCiz(j.log);
  }catch(e){
    $("baglanti").textContent = "bağlantı yok — sunucu kapalı mı? (" + e.message + ")";
    $("baglanti").className = "kopuk";
    $("iptal-btn").disabled = true;
  }
}
poll();
setInterval(poll, 1000);
if (new URLSearchParams(location.search).get("view") === "kalite") panoGoster(true);
</script>
</body>
</html>
"""

# ------------------------------------------------------------------ API ----
@app.get("/")
def kok():
    return Response(HTML, mimetype="text/html")


@app.get("/ocr-inceleme")
def ocr_inceleme_sayfasi():
    if ocr_review is None:
        return Response("Yerel OCR inceleme modülü yüklenemedi.", status=503)
    return Response(ocr_review.REVIEW_HTML, mimetype="text/html")


@app.get("/katki")
def katki_sayfasi():
    if contribution_client is None:
        return Response("Katkı modülü kullanılamıyor.", status=503)
    status = contribution_client.config_status()
    enabled = "true" if status["enabled"] else "false"
    retention = (str(status["retention_days"]) + " gün") if status["retention_days"] else "yapılandırılmamış"
    destination = (json.dumps(status.get("destination_host"), ensure_ascii=False)
                   .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
                   .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    html = r'''<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>İsteğe bağlı ölçüm katkısı</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{max-width:100%;margin:0;padding:0 18px;background:#12141a;color:#dde3f0;font:14px/1.5 "Segoe UI",system-ui,sans-serif}header{position:sticky;top:0;z-index:5;margin:0 -18px;padding:10px 18px;background:#151822;border-bottom:1px solid #2c3345;display:flex;align-items:center;gap:12px;flex-wrap:wrap}.brand{font-size:17px;font-weight:650;color:#dde3f0;text-decoration:none;white-space:nowrap}.app-nav{display:flex;align-items:center;gap:4px;flex-wrap:wrap}.app-nav a{background:transparent;border:0;border-radius:6px;color:#8b94ab;text-decoration:none;padding:7px 10px;font-size:13px;font-weight:600}.app-nav a:hover{background:#222a3a;color:#dde3f0}.app-nav a[aria-current="page"]{background:#243758;color:#a9c9ff}#queue-status{margin-left:auto;color:#a7b5ce;font-size:12px}#queue-status a{background:none;color:#a9c9ff;padding:0;text-decoration:none}main{max-width:1200px;margin:20px auto 42px;padding:0;display:grid;gap:14px}main h1{margin:0 0 8px;font-size:24px;line-height:1.2}main h2{margin:0 0 10px;font-size:17px;color:#dde3f0}.page-intro p{max-width:920px;margin:6px 0;color:#b5bfd2}.contribution-card{min-width:0;padding:16px;border:1px solid #2c3345;border-radius:10px;background:#1b1f29}.contribution-card p{margin:8px 0}.job-picker{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:end;margin-bottom:14px}.job-picker label{display:block;margin:0 0 4px;color:#a7b5ce;font-size:12px}.job-picker select{width:100%}a,button{color:#fff;background:#315fae;border:0;border-radius:6px;padding:9px 13px;cursor:pointer;font:inherit;font-weight:600;text-decoration:none}a.action-link{display:inline-flex;align-items:center}input,select,pre{max-width:100%;background:#0e1016;color:#dde3f0;border:1px solid #394158;border-radius:6px;padding:10px;font:inherit}pre{max-height:300px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.muted{color:#9aa6bd}.warn{color:#efbd66}button:disabled{opacity:.45;cursor:not-allowed}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #85b1ff;outline-offset:2px}.consent-box{display:flex;align-items:flex-start;gap:9px;margin:14px 0;color:#c6cede;font-size:13px}.consent-box input{flex:0 0 auto;width:auto;margin:4px 0 0}.action-row{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.contribution-list{padding-left:22px;margin:10px 0 0}.contribution-list li{margin:10px 0;overflow-wrap:anywhere}.contribution-list button{margin:6px 6px 0 0}@media(max-width:700px){body{padding:0 12px}header{margin:0 -12px;padding:10px 12px;gap:8px}.app-nav{flex-basis:100%;overflow-x:auto;flex-wrap:nowrap}.app-nav a{white-space:nowrap;padding:6px 8px;font-size:12px}#queue-status{margin-left:auto}main{margin:14px auto 28px}.contribution-card{padding:12px}.job-picker{grid-template-columns:minmax(0,1fr)}.job-picker button{width:100%}}
</style><header><a class="brand" href="/">Hardsub2srt</a><nav class="app-nav" aria-label="Ana gezinme"><a href="/">Üretim</a><a href="/ocr-inceleme">İnceleme</a><a href="/?view=kalite">Kalite ve öğrenme</a><a href="/katki" aria-current="page">İsteğe bağlı katkı</a></nav><span id="queue-status" role="status" aria-live="polite"><a href="/#kuyruk-kart">Kuyruk durumu yükleniyor…</a></span></header><main class="contribution-page"><section class="page-intro"><h1>İsteğe bağlı ölçüm katkısı</h1><p>Bu özellik varsayılan olarak kapalıdır. Önizleme oluşturmak ağ isteği göndermez. Yalnızca onaydan sonra, gösterilen sayısal ölçümler yapılandırılmış HTTPS hedefine gönderilir. Video, kırpım, SRT/VTT metni, dosya adı/yolu ve kullanıcı adı gönderilmez. GPT/DeepSeek API’si kullanılmaz.</p><p id="config" class="warn"></p></section>
<section class="contribution-card" aria-labelledby="preview-heading"><div class="job-picker"><div><label for="job">Tamamlanan yerel OCR işi</label><select id="job"></select></div><button id="preview">Önizlemeyi oluştur</button></div>
<h2 id="preview-heading">Gönderilecek alanların önizlemesi</h2><pre id="payload">Henüz önizleme yok.</pre><p class="muted">Sunucu, katkı satırında bearer token'ın özetini tutar; aynı token ile yapılan gönderimler bu özet üzerinden ilişkilendirilebilir. Özet token başına hız sınırı ve aynı token sahibinin silme yetkisi için kullanılır. Katkı kayıtları token'a göre değil alım yaşına göre, yukarıda belirtilen süre sonunda silinir. Ham token saklanmaz. Son 100 gönderimin silme kimliği bu tarayıcıda yerel olarak tutulur; payload ve token localStorage'a yazılmaz.</p>
<label class="consent-box"><input type="checkbox" id="consent"> <span>Önizlemedeki sayısal ölçümlerin yukarıdaki HTTPS hedefine, belirtilen en fazla __RETENTION__ süreyle gönderilmesini ve aynı token ile yapılan gönderimlerin ilişkilendirilebilir olmasını bu gönderim için onaylıyorum.</span></label><div class="action-row"><button id="send" disabled>Onayla ve gönder</button><a class="action-link" href="/">Arayüze dön</a></div><p id="status" role="status" aria-live="polite"></p></section>
<section class="contribution-card" aria-labelledby="retractions-heading"><h2 id="retractions-heading">Gönderimlerim ve silme</h2><p class="muted">Silme için aynı HTTPS hedefi ve token yapılandırılmış olmalı. Sunucu 404 verirse kayıt silinmez: bu, saklama süresinin dolması veya token uyuşmazlığı anlamına gelebilir. Eski bir kimliği yalnız tarayıcı listesinden kaldırmak için ayrı düğmeyi kullanın. Saklama süresi dolunca sunucu kaydı otomatik temizlenir.</p><ul id="retractions" class="contribution-list"></ul></section></main>
<script>const configured=__ENABLED__,destinationHost=__DESTINATION__;const STORE_KEY='hardsub2srt.contribution.retractions.v1',MAX_LOCAL_RECORDS=100;const job=document.querySelector('#job'),pre=document.querySelector('#payload'),send=document.querySelector('#send'),consent=document.querySelector('#consent'),status=document.querySelector('#status'),retractions=document.querySelector('#retractions');let current=null;
document.querySelector('#config').textContent=configured?'HTTPS hedefi: https://'+destinationHost+'. Token hazır. Sunucunun saklama süresi: '+__RETENTION__+'. Gönderim yalnız bu sayfadaki açık onaydan sonra yapılır.':'Gönderim kapalı: HTTPS URL, token ve H2S_CONTRIB_RETENTION_DAYS birlikte ayarlanmalı.';
async function updateQueueStatus(){const el=document.querySelector('#queue-status');try{const r=await fetch('/api/durum'),j=await r.json();if(!r.ok)throw Error();el.innerHTML='<a href="/#kuyruk-kart">Kuyruk: '+(j.aktif?1:0)+' çalışıyor · '+j.isler_bekleyen+' bekliyor</a>'}catch(_){el.innerHTML='<a href="/#kuyruk-kart">Kuyruk durumu alınamadı · Üretime dön</a>'}}updateQueueStatus();setInterval(updateQueueStatus,2500);
async function api(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const j=await r.json();if(!r.ok)throw Error(j.hata||j.error||('HTTP '+r.status));return j}
function readRetractions(){try{const value=JSON.parse(localStorage.getItem(STORE_KEY)||'[]');if(!Array.isArray(value))return[];return value.filter(x=>x&&typeof x==='object'&&typeof x.submission_id==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(x.submission_id)&&typeof x.destination_host==='string'&&typeof x.created_at==='string'&&['pending','submitted'].includes(x.state)).slice(-MAX_LOCAL_RECORDS)}catch(_){return[]}}
function writeRetractions(records){try{localStorage.setItem(STORE_KEY,JSON.stringify(records.slice(-MAX_LOCAL_RECORDS)));return true}catch(_){return false}}
function rememberSubmission(submissionId,state){const records=readRetractions().filter(x=>x.submission_id!==submissionId);records.push({submission_id:submissionId,destination_host:destinationHost,created_at:new Date().toISOString(),state});if(!writeRetractions(records))return false;renderRetractions();return true}
function removeRetraction(submissionId){return writeRetractions(readRetractions().filter(x=>x.submission_id!==submissionId))}
function renderRetractions(){retractions.replaceChildren();for(const record of readRetractions().reverse()){const li=document.createElement('li'),label=document.createElement('span'),button=document.createElement('button'),localButton=document.createElement('button');label.textContent=record.created_at+' · '+record.destination_host+' · '+(record.state==='submitted'?'gönderildi':'gönderim sonucu belirsiz');button.textContent='Sunucudan sil';button.disabled=!configured||record.destination_host!==destinationHost;button.onclick=async()=>{button.disabled=true;status.textContent='Silme isteği gönderiliyor…';try{const result=await api('/api/katki/sil',{submission_id:record.submission_id});if(result.status==='deleted'){const localCleared=removeRetraction(record.submission_id);renderRetractions();status.textContent=localCleared?'Katkı sunucudan silindi.':'Sunucu kaydı silindi ancak yerel kimlik listeden kaldırılamadı; tarayıcı depolaması yazılamadı.'}else if(result.status==='not_found'){button.disabled=false;status.textContent='Sunucu kaydı bulunamadı. Saklama süresi dolmuş veya token farklı olabilir; yerel kimlik korundu. Doğru token/hedefle yeniden deneyin ya da ayrı yerel kaldırma düğmesini kullanın.'}else throw Error('Silme sonucu doğrulanamadı.')}catch(e){button.disabled=false;status.textContent='Silme başarısız; yerel kimlik korundu. '+e.message}};localButton.textContent='Yalnız listeden kaldır';localButton.onclick=()=>{if(removeRetraction(record.submission_id)){renderRetractions();status.textContent='Kimlik yalnız bu tarayıcının listesinden kaldırıldı; sunucuya istek gönderilmedi.'}else status.textContent='Yerel liste güncellenemedi; kimlik korundu.'};if(record.destination_host!==destinationHost){const note=document.createElement('small');note.textContent=' · Silmek için aynı HTTPS hedefini yapılandırın';li.append(label,note,button,localButton)}else li.append(label,button,localButton);retractions.append(li)}if(!retractions.children.length){const li=document.createElement('li');li.textContent='Henüz saklanan gönderim kimliği yok.';retractions.append(li)}}
fetch('/api/katki/isler').then(r=>r.json()).then(j=>{for(const x of j.jobs){const o=document.createElement('option');o.value=x.id;o.textContent=x.id;job.append(o)}if(!j.jobs.length)status.textContent='Bu oturumda tamamlanmış iş yok.'}).catch(()=>status.textContent='Yerel işler okunamadı.');
document.querySelector('#preview').onclick=async()=>{current=null;consent.checked=false;send.disabled=true;status.textContent='';try{const j=await api('/api/katki/preview',{job_id:job.value});current=j.submission_id;pre.textContent=JSON.stringify(j.payload,null,2);status.textContent='Önizleme hazır. Henüz ağa hiçbir veri gönderilmedi.'}catch(e){pre.textContent='Önizleme oluşturulamadı.';status.textContent=e.message}};
consent.onchange=()=>send.disabled=!(configured&&current&&consent.checked);
send.onclick=async()=>{if(!configured||!current||!consent.checked)return;send.disabled=true;if(!rememberSubmission(current,'pending')){status.textContent='Yerel silme kimliği kaydedilemedi; gönderim yapılmadı.';send.disabled=false;return}status.textContent='Gönderiliyor…';try{const result=await api('/api/katki/gonder',{submission_id:current,consent:true});if(!rememberSubmission(result.submission_id,'submitted'))throw Error('Yanıt alındı, yerel kimlik güncellenemedi.');status.textContent='Gönderildi: '+result.submission_id+' ('+result.status+'). Silme kimliği kaydedildi.';current=null;consent.checked=false}catch(e){status.textContent='Gönderim sonucu belirsiz olabilir. Aynı kimlikle yeniden deneyebilir veya listedeki silme işlemini kullanabilirsiniz. '+e.message;send.disabled=false}};
renderRetractions();
</script></html>'''.replace("__ENABLED__", enabled)
    html = html.replace("__RETENTION__", json.dumps(retention, ensure_ascii=False))
    html = html.replace("__DESTINATION__", destination)
    return Response(html, mimetype="text/html")


@app.get("/api/katki/isler")
def api_katki_isler():
    with KILIT:
        jobs = [{"id": x["id"]} for x in ISLER if x.get("durum") == "bitti" and isinstance(x.get("stats"), dict)]
    return jsonify({"jobs": jobs[-100:]})


@app.post("/api/katki/preview")
def api_katki_preview():
    if contribution_client is None:
        return jsonify({"hata": "Katkı modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    body = request.get_json(silent=True) or {}
    if set(body) != {"job_id"} or not isinstance(body.get("job_id"), str):
        return jsonify({"hata": "Önizleme için yalnız iş kimliği gerekli"}), 400
    job_id = body.get("job_id")
    with KILIT:
        job = next((is_snapshot(x) for x in ISLER if x["id"] == job_id and x.get("durum") == "bitti"), None)
    if not job or not isinstance(job.get("stats"), dict):
        return jsonify({"hata": "Tamamlanmış koşum istatistiği bulunamadı"}), 404
    try:
        payload = contribution_client.validate_payload(contribution_client.build_payload(job["stats"], job.get("sure_sn") or 0))
    except Exception as exc:
        return jsonify({"hata": str(exc)}), 422
    now = time.time()
    with KILIT:
        if len(KATKI_ONIZLEMELERI) > 100:
            KATKI_ONIZLEMELERI.clear()
        KATKI_ONIZLEMELERI[payload["submission_id"]] = {"payload": payload, "expires": now + 600, "used": False}
    return jsonify({"submission_id": payload["submission_id"], "payload": payload})


@app.post("/api/katki/gonder")
def api_katki_gonder():
    if contribution_client is None:
        return jsonify({"hata": "Katkı modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    body = request.get_json(silent=True) or {}
    if set(body) != {"submission_id", "consent"} or body.get("consent") is not True:
        return jsonify({"hata": "Bu gönderim için açık onay gerekli"}), 400
    sid = body.get("submission_id")
    with KILIT:
        pending = KATKI_ONIZLEMELERI.get(sid)
        if not pending or pending["expires"] < time.time() or pending["used"]:
            return jsonify({"hata": "Önizleme bulunamadı veya süresi doldu; yeniden oluşturun"}), 409
        pending["used"] = True
    try:
        result = contribution_client.send_payload(pending["payload"])
        return jsonify(result)
    except Exception as exc:
        with KILIT:
            pending["used"] = False
        return jsonify({"hata": str(exc)}), 503


@app.post("/api/katki/sil")
def api_katki_sil():
    if contribution_client is None:
        return jsonify({"hata": "Katkı modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    body = request.get_json(silent=True) or {}
    if set(body) != {"submission_id"} or not isinstance(body.get("submission_id"), str):
        return jsonify({"hata": "Silme için yalnız gönderim kimliği gerekli"}), 400
    try:
        return jsonify(contribution_client.delete_submission(body["submission_id"]))
    except Exception as exc:
        return jsonify({"hata": str(exc)}), 503


def _ocr_hata(exc):
    if ocr_review is not None and isinstance(exc, ocr_review.ReviewError):
        return jsonify({"hata": str(exc)}), exc.status
    app.logger.exception("OCR review request failed")
    return jsonify({"hata": "OCR inceleme işlemi tamamlanamadı; günlükte ayrıntı var"}), 500


@app.get("/api/ocr-inceleme/seed-paketleri")
def api_ocr_inceleme_seed_paketleri():
    """List only revalidated seed review packs below the fixed local app root."""
    error = _memory_request_error()
    if error:
        return error
    try:
        _root, packs = _seed_review_storage_catalog()
        return jsonify({"packs": [{
            "pack_id": row["pack_id"],
            "episode_key": row["episode_key"],
            "candidate_count": row["candidate_count"],
            "reviewed_count": row["reviewed_count"],
        } for row in packs]})
    except Exception:
        app.logger.exception("Seed review pack list failed")
        return jsonify({"hata": "Yerel seed paketleri güvenle listelenemedi"}), 500


@app.post("/api/ocr-inceleme/seed-paket-ac")
def api_ocr_inceleme_seed_paket_ac():
    """Open a validated local seed pack by opaque ID; do not accept a path."""
    error = _memory_request_error()
    if error:
        return error
    if ocr_review is None:
        return jsonify({"hata": "OCR inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    value = request.get_json(silent=True) or {}
    if type(value) is not dict or set(value) != {"pack_id"}:
        return jsonify({"hata": "Yalnız doğrulanmış seed pack kimliği kabul edilir"}), 400
    try:
        import ocr_seed_import
        seed_root, _packs = _seed_review_storage_catalog()
        pack_path = ocr_seed_import.open_seed_review_pack(seed_root, value["pack_id"])
        review_id, pack = ocr_review.open_pack(str(pack_path))
        pack["review_id"] = review_id
        return jsonify(pack)
    except Exception as exc:
        return _ocr_hata(exc)


@app.post("/api/ocr-inceleme/ac")
def api_ocr_inceleme_ac():
    if ocr_review is None:
        return jsonify({"hata": "OCR inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        review_id, pack = ocr_review.open_pack(v.get("path"))
        pack["review_id"] = review_id
        return jsonify(pack)
    except Exception as exc:
        return _ocr_hata(exc)


@app.get("/api/ocr-inceleme/durum")
def api_ocr_inceleme_durum():
    if ocr_review is None:
        return jsonify({"hata": "OCR inceleme modülü kullanılamıyor"}), 503
    try:
        return jsonify(ocr_review.pack_status(request.args.get("review_id", "")))
    except Exception as exc:
        return _ocr_hata(exc)


@app.get("/api/ocr-inceleme/srt-lint")
def api_ocr_inceleme_srt_lint():
    if ocr_review is None:
        return jsonify({"hata": "OCR inceleme modülü kullanılamıyor"}), 503
    try:
        return jsonify(ocr_review.srt_lint_report(request.args.get("review_id", "")))
    except Exception as exc:
        return _ocr_hata(exc)


@app.get("/api/ocr-inceleme/<review_id>/crop/<cue_id>")
def api_ocr_inceleme_crop(review_id, cue_id):
    if ocr_review is None:
        return jsonify({"hata": "OCR inceleme modülü kullanılamıyor"}), 503
    try:
        path, mimetype = ocr_review.image_bytes_path(review_id, cue_id)
        return send_file(path, mimetype=mimetype, conditional=True, max_age=0)
    except Exception as exc:
        return _ocr_hata(exc)


@app.post("/api/ocr-inceleme/karar")
def api_ocr_inceleme_karar():
    if ocr_review is None:
        return jsonify({"hata": "OCR inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        record = ocr_review.append_review(v.get("review_id", ""), v.get("cue_id", ""),
                                          v.get("status", ""), v.get("correction", ""))
        return jsonify({"ok": True, "event_id": record["event_id"]})
    except Exception as exc:
        return _ocr_hata(exc)


@app.post("/api/ocr-inceleme/geri-al")
def api_ocr_inceleme_geri_al():
    if ocr_review is None:
        return jsonify({"hata": "OCR inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        record = ocr_review.undo_review(v.get("review_id", ""), v.get("cue_id", ""))
        return jsonify({"ok": True, "event_id": record["event_id"]})
    except Exception as exc:
        return _ocr_hata(exc)


@app.post("/api/ocr-inceleme/disari-aktar")
def api_ocr_inceleme_disari_aktar():
    if ocr_review is None:
        return jsonify({"hata": "OCR inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        path, count = ocr_review.export_dataset(v.get("review_id", ""))
        return jsonify({"ok": True, "path": path.name, "count": count})
    except Exception as exc:
        return _ocr_hata(exc)


@app.post("/api/ocr-inceleme/metadata-sablonu")
def api_ocr_inceleme_metadata_sablonu():
    if ocr_review is None:
        return jsonify({"hata": "OCR inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    body = request.get_json(silent=True) or {}
    if (not isinstance(body, dict) or set(body) != {"review_id", "dataset_name"} or
            not isinstance(body.get("review_id"), str) or
            not isinstance(body.get("dataset_name"), str)):
        return jsonify({"hata": "review_id ve dataset_name gerekli"}), 400
    try:
        path = ocr_review.create_metadata_template(body["review_id"], body["dataset_name"])
        pack = ocr_review.pack_for(body["review_id"])
        return jsonify({"ok": True, "path": path_scope.relative_to(path, pack["pack"]).as_posix()})
    except Exception as exc:
        return _ocr_hata(exc)


def _translation_error(exc):
    if translation_review is not None and isinstance(exc, ocr_review.ReviewError):
        return jsonify({"hata": str(exc)}), exc.status
    app.logger.exception("AI translation round-trip request failed")
    return jsonify({"hata": "AI inceleme işlemi tamamlanamadı; günlükte ayrıntı var"}), 500


def _translation_evaluation_error(exc):
    if translation_evaluation_backend is not None and isinstance(exc, translation_evaluation_backend.EvaluationError):
        return jsonify({"hata": str(exc)}), 409
    if ocr_review is not None and isinstance(exc, ocr_review.ReviewError):
        return jsonify({"hata": str(exc)}), exc.status
    app.logger.exception("Local translation evaluation request failed")
    return jsonify({"hata": "Yerel çeviri kalite işlemi tamamlanamadı"}), 500


def _ai_ocr_error(exc):
    if ai_ocr_review is not None and isinstance(exc, ocr_review.ReviewError):
        return jsonify({"hata": str(exc)}), exc.status
    app.logger.exception("Manual AI OCR visual review request failed")
    return jsonify({"hata": "AI OCR inceleme işlemi tamamlanamadı"}), 500


@app.post("/api/ai-ocr-gorsel/paket")
def api_ai_ocr_gorsel_paket():
    if ai_ocr_review is None or ocr_review is None:
        return jsonify({"hata": "Manuel AI OCR inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    value = request.get_json(silent=True) or {}
    try:
        path, filename, _count = ai_ocr_review.create_package(value.get("review_id", ""), value.get("cue_ids"))
        return send_file(path, mimetype="application/zip", as_attachment=True,
                         download_name=filename, max_age=0)
    except Exception as exc:
        return _ai_ocr_error(exc)


@app.post("/api/ai-ocr-gorsel/yanit")
def api_ai_ocr_gorsel_yanit():
    if ai_ocr_review is None or ocr_review is None:
        return jsonify({"hata": "Manuel AI OCR inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    if request.content_length and request.content_length > ai_ocr_review.MAX_RESPONSE_BYTES:
        return jsonify({"hata": "AI OCR yanıtı 4 MiB sınırını aşıyor"}), 413
    value = request.get_json(silent=True) or {}
    try:
        return jsonify(ai_ocr_review.import_response(value.get("review_id", ""), value.get("response")))
    except Exception as exc:
        return _ai_ocr_error(exc)


@app.get("/api/ai-ocr-gorsel/durum")
def api_ai_ocr_gorsel_durum():
    if ai_ocr_review is None or ocr_review is None:
        return jsonify({"hata": "Manuel AI OCR inceleme modülü kullanılamıyor"}), 503
    try:
        return jsonify(ai_ocr_review.status(request.args.get("review_id", "")))
    except Exception as exc:
        return _ai_ocr_error(exc)


@app.post("/api/ai-ceviri/istek")
def api_ai_ceviri_istek():
    if translation_review is None or ocr_review is None:
        return jsonify({"hata": "AI çeviri inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        result = translation_review.create_request(
            v.get("review_id", ""), v.get("drafts"),
            v.get("source_language"), v.get("target_language"))
        return jsonify(result)
    except Exception as exc:
        return _translation_error(exc)


@app.get("/api/ceviri-kalitesi/hazirlik")
def api_ceviri_kalitesi_hazirlik():
    """Read only server-derived readiness; never accepts split/rights claims."""
    if translation_evaluation_service is None or translation_review is None or ocr_review is None:
        return jsonify({"hata": "Yerel çeviri kalite değerlendirmesi kullanılamıyor"}), 503
    try:
        return jsonify(translation_evaluation_service.readiness(request.args.get("review_id", "")))
    except Exception as exc:
        return _translation_evaluation_error(exc)


@app.post("/api/ceviri-kalitesi/sentetik")
def api_ceviri_kalitesi_sentetik():
    """Create a fixed fake rubric demo; the browser supplies no dataset fields."""
    if translation_evaluation_service is None or translation_evaluation_backend is None:
        return jsonify({"hata": "Yerel sentetik rubrik demosu kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "Boş JSON gövdesi gerekli"}), 415
    value = request.get_json(silent=True)
    if not isinstance(value, dict) or value:
        return jsonify({"hata": "Sentetik demo gövdesi boş olmalı; split/veri/hak alanı kabul edilmez"}), 400
    try:
        return jsonify(translation_evaluation_service.create_synthetic_demo())
    except Exception as exc:
        return _translation_evaluation_error(exc)


@app.get("/api/ceviri-kalitesi/sentetik/durum")
def api_ceviri_kalitesi_sentetik_durum():
    if translation_evaluation_service is None:
        return jsonify({"hata": "Yerel sentetik rubrik demosu kullanılamıyor"}), 503
    try:
        return jsonify(translation_evaluation_service.status(request.args.get("evaluation_id", "")))
    except Exception as exc:
        return _translation_evaluation_error(exc)


@app.post("/api/ceviri-kalitesi/sentetik/puan")
def api_ceviri_kalitesi_sentetik_puan():
    if translation_evaluation_service is None:
        return jsonify({"hata": "Yerel sentetik rubrik demosu kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    if request.content_length and request.content_length > 32 * 1024:
        return jsonify({"hata": "Sentetik puan isteği 32 KiB sınırını aşıyor"}), 413
    value = request.get_json(silent=True)
    required = {"evaluation_id", "blind_candidate_id", "status", "dimensions", "issues",
                "rubric_outcome", "exclusion_reason", "supersedes_event_id"}
    if not isinstance(value, dict) or set(value) != required:
        return jsonify({"hata": "Yalnız sentetik değerlendirme kimliği, kör aday kimliği ve rubrik alanları kabul edilir"}), 400
    try:
        return jsonify(translation_evaluation_service.record_synthetic_rating(
            value["evaluation_id"], value["blind_candidate_id"], value["status"],
            value["dimensions"], value["issues"], value["rubric_outcome"],
            value["exclusion_reason"], value["supersedes_event_id"],
        ))
    except Exception as exc:
        return _translation_evaluation_error(exc)


@app.post("/api/ceviri-kalitesi/gercek")
def api_ceviri_kalitesi_gercek():
    """Create a real cohort only from the private, server-registered evidence manifest."""
    if translation_evaluation_service is None or translation_evaluation_backend is None:
        return jsonify({"hata": "Yerel çeviri kalite değerlendirmesi kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "Boş JSON gövdesi gerekli"}), 415
    value = request.get_json(silent=True)
    if not isinstance(value, dict) or value:
        return jsonify({"hata": "Gövde boş olmalı; kaynak, split, hak veya aday eşlemesi tarayıcıdan alınmaz"}), 400
    try:
        return jsonify(translation_evaluation_service.create_real_evaluation(
            request.args.get("review_id", "")))
    except Exception as exc:
        return _translation_evaluation_error(exc)


@app.get("/api/ceviri-kalitesi/gercek/durum")
def api_ceviri_kalitesi_gercek_durum():
    if translation_evaluation_service is None:
        return jsonify({"hata": "Yerel çeviri kalite değerlendirmesi kullanılamıyor"}), 503
    try:
        return jsonify(translation_evaluation_service.real_status(
            request.args.get("evaluation_id", "")))
    except Exception as exc:
        return _translation_evaluation_error(exc)


@app.get("/api/ceviri-kalitesi/gercek/kosul-eslemesi")
def api_ceviri_kalitesi_gercek_kosul_eslemesi():
    if translation_evaluation_service is None:
        return jsonify({"hata": "Yerel çeviri kalite değerlendirmesi kullanılamıyor"}), 503
    try:
        return jsonify(translation_evaluation_service.reveal_condition_mapping(
            request.args.get("evaluation_id", "")))
    except Exception as exc:
        return _translation_evaluation_error(exc)


@app.post("/api/ceviri-kalitesi/gercek/puan")
def api_ceviri_kalitesi_gercek_puan():
    if translation_evaluation_service is None:
        return jsonify({"hata": "Yerel çeviri kalite değerlendirmesi kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    if request.content_length and request.content_length > 32 * 1024:
        return jsonify({"hata": "İnsan puanlama isteği 32 KiB sınırını aşıyor"}), 413
    value = request.get_json(silent=True)
    required = {"evaluation_id", "blind_candidate_id", "status", "dimensions", "issues",
                "rubric_outcome", "exclusion_reason", "supersedes_event_id"}
    if not isinstance(value, dict) or set(value) != required:
        return jsonify({"hata": "Yalnız cohort, kör aday ve rubrik alanları kabul edilir"}), 400
    try:
        return jsonify(translation_evaluation_service.record_real_rating(
            value["evaluation_id"], value["blind_candidate_id"], value["status"],
            value["dimensions"], value["issues"], value["rubric_outcome"],
            value["exclusion_reason"], value["supersedes_event_id"],
        ))
    except Exception as exc:
        return _translation_evaluation_error(exc)


@app.post("/api/ai-ceviri/sohbet-paketi")
def api_ai_ceviri_sohbet_paketi():
    if translation_review is None or ocr_review is None:
        return jsonify({"hata": "AI çeviri inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        path, filename, _count = translation_review.create_chat_export(
            v.get("review_id", ""), v.get("drafts"),
            v.get("source_language"), v.get("target_language"))
        return send_file(path, mimetype="application/zip", as_attachment=True,
                         download_name=filename, max_age=0)
    except Exception as exc:
        return _translation_error(exc)


@app.get("/api/ai-ceviri/durum")
def api_ai_ceviri_durum():
    if translation_review is None or ocr_review is None:
        return jsonify({"hata": "AI çeviri inceleme modülü kullanılamıyor"}), 503
    try:
        result = translation_review.proposal_status(request.args.get("review_id", ""))
        result["review_id"] = request.args.get("review_id", "")
        try:
            result["translation_memory_sync"] = translation_review.sync_translation_memory(
                request.args.get("review_id", ""))
        except Exception as exc:
            result["translation_memory_sync"] = {
                "status": "pending",
                "message": "Onay kararları kaydedildi; çeviri belleği görünümü henüz onarılamadı.",
                "error_type": type(exc).__name__,
            }
        return jsonify(result)
    except Exception as exc:
        return _translation_error(exc)


@app.post("/api/ai-ceviri/bellek-yenile")
def api_ai_ceviri_bellek_yenile():
    if translation_review is None or ocr_review is None:
        return jsonify({"hata": "AI çeviri inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        return jsonify(translation_review.sync_translation_memory(v.get("review_id", "")))
    except Exception as exc:
        return _translation_error(exc)


@app.post("/api/ai-ceviri/yanit")
def api_ai_ceviri_yanit():
    if translation_review is None or ocr_review is None:
        return jsonify({"hata": "AI çeviri inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    if request.content_length and request.content_length > translation_review.MAX_RESPONSE_BYTES + 4096:
        return jsonify({"hata": "AI yanıtı 4 MiB sınırını aşıyor"}), 413
    v = request.get_json(silent=True) or {}
    try:
        result = translation_review.import_response(v.get("review_id", ""), v.get("response"))
        return jsonify(result)
    except Exception as exc:
        return _translation_error(exc)


@app.post("/api/ai-ceviri/karar")
def api_ai_ceviri_karar():
    if translation_review is None or ocr_review is None:
        return jsonify({"hata": "AI çeviri inceleme modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        record = translation_review.decide(
            v.get("review_id", ""), v.get("proposal_id", ""),
            v.get("decision", ""), v.get("translation"))
        return jsonify({"ok": True, "event_id": record["event_id"],
                        "translation_memory_sync": record.get("translation_memory_sync")})
    except Exception as exc:
        return _translation_error(exc)


def _translation_memory_error(exc):
    if translation_memory is not None and isinstance(exc, ocr_review.ReviewError):
        return jsonify({"hata": str(exc)}), exc.status
    app.logger.exception("Scoped translation memory request failed")
    return jsonify({"hata": "Yerel çeviri belleği işlemi tamamlanamadı"}), 500


@app.get("/api/ceviri-bellegi/durum")
def api_ceviri_bellegi_durum():
    if translation_memory is None or ocr_review is None:
        return jsonify({"hata": "Yerel çeviri belleği modülü kullanılamıyor"}), 503
    try:
        result = translation_memory.status(request.args.get("project_key", ""),
                    request.args.get("source_language", ""), request.args.get("target_language", ""))
        return jsonify(result)
    except Exception as exc:
        return _translation_memory_error(exc)


@app.post("/api/ceviri-bellegi/ara")
def api_ceviri_bellegi_ara():
    if translation_memory is None or ocr_review is None:
        return jsonify({"hata": "Yerel çeviri belleği modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        result = translation_memory.suggest(v.get("review_id", ""), v.get("cue_id", ""),
                    v.get("project_key", ""), v.get("source_language", ""),
                    v.get("target_language", ""), v.get("source_text", ""), v.get("draft_translation", ""))
        return jsonify(result)
    except Exception as exc:
        return _translation_memory_error(exc)


@app.post("/api/ceviri-bellegi/onaylilari-ekle")
def api_ceviri_bellegi_onaylilari_ekle():
    if translation_memory is None or translation_review is None or ocr_review is None:
        return jsonify({"hata": "Yerel çeviri belleği modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        result = translation_memory.add_approved(v.get("review_id", ""), v.get("project_key", ""),
                    v.get("source_language", ""), v.get("target_language", ""), v.get("decision_event_ids"))
        return jsonify(result)
    except Exception as exc:
        return _translation_memory_error(exc)


@app.post("/api/ceviri-bellegi/terim")
def api_ceviri_bellegi_terim():
    if translation_memory is None or ocr_review is None:
        return jsonify({"hata": "Yerel çeviri belleği modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        result = translation_memory.add_lexicon(v.get("project_key", ""), v.get("source_language", ""),
                    v.get("target_language", ""), v.get("source_term", ""), v.get("target_term", ""))
        return jsonify(result)
    except Exception as exc:
        return _translation_memory_error(exc)


@app.post("/api/ceviri-bellegi/geri-al")
def api_ceviri_bellegi_geri_al():
    if translation_memory is None or ocr_review is None:
        return jsonify({"hata": "Yerel çeviri belleği modülü kullanılamıyor"}), 503
    if not request.is_json:
        return jsonify({"hata": "JSON isteği gerekli"}), 415
    v = request.get_json(silent=True) or {}
    try:
        result = translation_memory.rollback(v.get("project_key", ""), v.get("source_language", ""),
                    v.get("target_language", ""), v.get("event_id", ""))
        return jsonify(result)
    except Exception as exc:
        return _translation_memory_error(exc)


@app.get("/api/durum")
def api_durum():
    with KILIT:
        aktif_raw = next((i for i in ISLER if i["durum"] == "çalışıyor"), None)
        bekleyen = sum(1 for i in ISLER if i["durum"] == "bekliyor")
        isler_raw = list(ISLER[-KUYRUK_YANIT:])
        toplam = len(ISLER)
        if aktif_raw and all(i["id"] != aktif_raw["id"] for i in isler_raw):
            isler_raw.append(aktif_raw)
        enqueue_order = {i["id"]: i.get("enqueue_seq", index + 1)
                         for index, i in enumerate(ISLER)}
        isler_raw.sort(key=lambda i: enqueue_order[i["id"]])
        log = list(LOG)[-LOG_YANIT:]
        pid = CALISAN.get("pid")
        aktif = is_snapshot(aktif_raw) if aktif_raw else None
        son_isler = [is_snapshot(i) for i in isler_raw]
    return jsonify({
        "sunucu": {
            "klasor": str(KLASOR),
            "port": PORT,
            "basladi": datetime.fromtimestamp(SUNUCU_BASLANGIC)
                        .strftime("%Y-%m-%d %H:%M:%S"),
            "arac_var": ARAC.exists(),
            "surum": "1.3",
        },
        "pid": pid,
        "aktif": aktif,
        "isler_bekleyen": bekleyen,
        "is_kaydi_toplam": toplam,
        "kuyruk_gosterilen": len(son_isler),
        "kuyruk": son_isler,
        "log": log,
    })


@app.get("/api/is-gecmisi")
def api_is_gecmisi():
    """Return bounded lifecycle diagnostics; no media names or paths are stored."""
    if JOB_HISTORY is None:
        return jsonify({"etkin": False, "isler": [],
                        "uyari": "Kalıcı iş tanılaması bu oturumda kullanılamıyor."})
    try:
        limit = request.args.get("limit", "20")
        return jsonify({"etkin": True, "isler": JOB_HISTORY.recent(limit)})
    except Exception:
        # The endpoint is diagnostic only; malformed local state is fail-safe.
        return jsonify({"etkin": False, "isler": [],
                        "uyari": "İş geçmişi okunamadı; canlı kuyruk etkilenmedi."})


@app.post("/api/ekle")
def api_ekle():
    global IS_SAYAC
    v = request.get_json(force=True, silent=True) or {}
    yollar = v.get("yollar")
    if yollar is None and v.get("yol"):
        yollar = [v["yol"]]
    if not isinstance(yollar, list) or not yollar:
        return jsonify({"hata": "yollar listesi gerekli (satır başına bir video yolu)"}), 400
    if len(yollar) > MAX_BATCH:
        return jsonify({"hata": f"Tek istekte en fazla {MAX_BATCH} video eklenebilir; seçilen: {len(yollar)}. Listeyi parçalara bölün."}), 413

    sec = {
        "ust_bant": bool(v.get("ust_bant")),
        "ass": bool(v.get("ass")),
        "cpu": bool(v.get("cpu", False)),
        "review_pack": bool(v.get("review_pack", False)),
        "dil": str(v.get("dil") or "tr,en"),
        "qa_sayi": int(v.get("qa_sayi", 4) or 0),
        "cikti_klasor": str(v.get("cikti_klasor") or KLASOR),
        "limit_saniye": float(v.get("limit_saniye") or 0),
        "onek": str(v.get("onek") or ""),
    }
    if any(c in sec["onek"] for c in '<>:"/\\|?*\x00\r\n'):
        return jsonify({"hata": "çıktı öneki dosya yolu ayıracı veya geçersiz karakter içeremez"}), 400

    adaylar, hatali, yinelenen = [], [], []
    bu_istek = set()
    for yol in yollar:
        yol = str(yol).strip().strip('"')
        p = Path(yol).expanduser()
        if not p.exists():
            hatali.append({"yol": yol, "neden": "dosya bulunamadı"})
            continue
        if not p.is_file():
            hatali.append({"yol": yol, "neden": "dosya değil"})
            continue
        if p.suffix.lower() not in VIDEO_UZANTILARI:
            hatali.append({"yol": yol, "neden": f"uzantı desteklenmiyor ({p.suffix or 'yok'})"})
            continue
        p = p.resolve()
        anahtar = os.path.normcase(os.path.normpath(str(p)))
        if anahtar in bu_istek:
            yinelenen.append(p.name)
            continue
        bu_istek.add(anahtar)
        adaylar.append((p, anahtar))

    eklendi = []
    with KILIT:
        kuyruktaki = {
            os.path.normcase(os.path.normpath(str(Path(i["yol"]).resolve())))
            for i in ISLER if i["durum"] in ("bekliyor", "çalışıyor")
        }
        yeni = [(p, key) for p, key in adaylar if key not in kuyruktaki]
        yinelenen.extend(p.name for p, key in adaylar if key in kuyruktaki)
        kuyruk_aktif = sum(1 for i in ISLER if i["durum"] in ("bekliyor", "çalışıyor"))
        if kuyruk_aktif + len(yeni) > MAX_AKTIF_KUYRUK:
            bos = max(0, MAX_AKTIF_KUYRUK - kuyruk_aktif)
            return jsonify({"hata": f"Kuyruk sınırı {MAX_AKTIF_KUYRUK} iş. Şu an {kuyruk_aktif} aktif/bekleyen, bu istekte {len(yeni)} yeni ve {bos} boş yer var. Önce bekleyen işleri bitirin veya daha küçük grup ekleyin."}), 429
        for p, _key in yeni:
            IS_SAYAC += 1
            isim = is_olustur(p, sec, f"is-{IS_SAYAC}", IS_SAYAC)
            ISLER.append(isim)
            eklendi.append(isim["id"])
    for p, _key in yeni:
        ek_not = f" (ilk {sec['limit_saniye']:.0f} sn)" if sec["limit_saniye"] > 0 else ""
        log_ekle("bilgi", f"kuyruğa eklendi: {p.name}{ek_not}")
    return jsonify({"eklendi": eklendi, "hatali": hatali, "yinelenen": yinelenen,
                    "sec": sec})


@app.post("/api/iptal")
def api_iptal():
    with KILIT:
        pid = CALISAN.get("pid")
        is_id = CALISAN.get("is_id")
    if not pid:
        return jsonify({"hata": "çalışan iş yok"}), 404
    IPTAL_SETI.add(is_id)
    r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True, text=True, env=_child_process_env())
    log_ekle("uyari", f"İPTAL istendi: taskkill /T /F /PID {pid} → rc={r.returncode} "
                      f"{(r.stdout or r.stderr or '').strip()[:120]}")
    return jsonify({"ok": True, "pid": pid, "is_id": is_id,
                    "taskkill_rc": r.returncode,
                    "taskkill_cikti": (r.stdout or r.stderr or "").strip()})


@app.post("/api/klasortara")
def api_klasortara():
    v = request.get_json(force=True, silent=True) or {}
    dizin = str(v.get("dizin") or "").strip().strip('"')
    if not dizin:
        return jsonify({"hata": "dizin gerekli"}), 400
    p = Path(dizin)
    if not p.is_dir():
        return jsonify({"hata": f"klasör bulunamadı: {dizin}"}), 404
    videolar = []
    try:
        for f in sorted(p.iterdir(), key=lambda x: natural_sort_key(x.name)):
            if f.is_file() and f.suffix.lower() in VIDEO_UZANTILARI:
                try:
                    mb = round(f.stat().st_size / (1024 * 1024), 1)
                except OSError:
                    mb = None
                videolar.append({"yol": str(f.resolve()), "ad": f.name, "mb": mb})
    except OSError as e:
        return jsonify({"hata": f"klasör okunamadı: {e}"}), 400
    return jsonify({"dizin": str(p.resolve()), "videolar": videolar})


def _picker_terminate(process):
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            app.logger.exception("Windows picker helper could not be reaped")


def _foreground_owner_hwnd():
    """Return the current foreground window as a possible native-dialog owner."""
    if os.name != "nt":
        return 0
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetForegroundWindow.argtypes = []
        user32.IsWindow.argtypes = [ctypes.c_void_p]
        user32.IsWindow.restype = ctypes.c_bool
        handle = user32.GetForegroundWindow()
        return int(handle) if handle and user32.IsWindow(handle) else 0
    except (AttributeError, OSError, TypeError, ValueError):
        app.logger.exception("could not capture foreground window for picker owner")
        return 0


def _picker_finish(job_id, status, result=None, error=None):
    global PICKER_ACTIVE_JOB
    now = time.time()
    with PICKER_JOB_LOCK:
        job = PICKER_JOBS.get(job_id)
        if job is None:
            return
        job.update(status=status, result=result, hata=error, finished_at=now)
        if PICKER_ACTIVE_JOB == job_id:
            PICKER_ACTIVE_JOB = None


def _watch_picker_job(job_id):
    with PICKER_JOB_LOCK:
        job = PICKER_JOBS.get(job_id)
    if job is None:
        return
    process = job["process"]
    marker = Path(job["marker"])
    deadline = job["started_at"] + PICKER_TIMEOUT_SECONDS
    timeout = False
    dialog_marked = False
    try:
        while process.poll() is None:
            if PICKER_SHUTDOWN.is_set():
                _picker_terminate(process)
                break
            if not dialog_marked:
                try:
                    dialog_ms = int(marker.read_text(encoding="ascii").strip())
                except (OSError, ValueError):
                    dialog_ms = None
                if dialog_ms is not None:
                    dialog_marked = True
                    spawn_ms = dialog_ms - job["process_started_epoch_ms"]
                    request_ms = dialog_ms - job["request_started_epoch_ms"]
                    with PICKER_JOB_LOCK:
                        current = PICKER_JOBS.get(job_id)
                        if current:
                            current["status"] = "opening"
                            current["timing"].update(spawn_to_showdialog_ms=spawn_ms,
                                                     request_to_showdialog_ms=request_ms)
                    app.logger.info("picker timing mode=%s spawn_to_showdialog_ms=%d request_to_showdialog_ms=%d",
                                    job["mode"], spawn_ms, request_ms)
            if time.time() >= deadline:
                timeout = True
                _picker_terminate(process)
                break
            PICKER_SHUTDOWN.wait(0.1)
        stdout, stderr = process.communicate()
        total_ms = int((time.time() - job["request_started_epoch_ms"] / 1000) * 1000)
        if PICKER_SHUTDOWN.is_set():
            _picker_finish(job_id, "cancelled", error="Sunucu kapanırken Windows seçicisi kapatıldı.")
        elif timeout:
            app.logger.warning("picker timeout mode=%s total_ms=%d", job["mode"], total_ms)
            _picker_finish(job_id, "timeout", error="Windows seçicisi 5 dakika içinde tamamlanmadı; kapatıldı.")
        elif process.returncode != 0:
            detail = (stderr or stdout or "PowerShell seçicisi açılamadı").strip()
            app.logger.warning("picker helper failed mode=%s exit=%s error=%s",
                               job["mode"], process.returncode, detail[:400])
            _picker_finish(job_id, "error", error=detail[:500])
        else:
            try:
                result = json.loads(stdout.strip())
                if job["mode"] == "dosya":
                    result = {"yollar": [str(Path(p).resolve()) for p in result.get("yollar", [])]}
                else:
                    path = result.get("dizin") or ""
                    result = {"dizin": str(Path(path).resolve()) if path else ""}
                _picker_finish(job_id, "done", result=result)
            except (ValueError, TypeError, OSError) as exc:
                _picker_finish(job_id, "error", error=f"Windows seçicisi yanıtı okunamadı: {exc}")
        app.logger.info("picker timing mode=%s post_total_ms=%d exit=%s",
                        job["mode"], total_ms, process.returncode)
    except Exception as exc:
        app.logger.exception("Windows picker watcher failed")
        _picker_terminate(process)
        _picker_finish(job_id, "error", error=f"Windows seçicisi izlenemedi: {exc}")
    finally:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            app.logger.warning("picker marker cleanup failed: %s", marker)


def _stop_picker_helpers():
    PICKER_SHUTDOWN.set()
    with PICKER_JOB_LOCK:
        jobs = list(PICKER_JOBS.items())
    for job_id, job in jobs:
        _picker_terminate(job.get("process"))
        watcher = job.get("watcher")
        if watcher and watcher.ident is not None and watcher is not threading.current_thread():
            watcher.join(timeout=3)
        if job.get("status") in ("starting", "opening"):
            _picker_finish(job_id, "cancelled", error="Sunucu kapanırken Windows seçicisi kapatıldı.")
        try:
            Path(job["marker"]).unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_stop_picker_helpers)


@app.post("/api/secim")
def api_secim():
    """Başlatıcıyı hızlıca yanıtla; sonuç GET /api/secim/<job_id> ile izlenir."""
    global PICKER_ACTIVE_JOB
    if os.name != "nt":
        return jsonify({"hata": "Windows seçicisi yalnız Windows sunucusunda kullanılabilir; yolu elle girin."}), 501
    if PICKER_SHUTDOWN.is_set():
        return jsonify({"hata": "UI sunucusu kapanıyor; başlatıcıyı yeniden açın."}), 503
    v = request.get_json(force=True, silent=True) or {}
    tur = v.get("tur")
    if tur not in ("dosya", "klasor", "inceleme"):
        return jsonify({"hata": "tur 'dosya', 'klasor' veya 'inceleme' olmalı"}), 400

    powershell = shutil.which("powershell.exe")
    if not powershell:
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if candidate.is_file():
            powershell = str(candidate)
    if not powershell:
        return jsonify({"hata": "Windows PowerShell bulunamadı"}), 500

    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
$mode = $env:HARDSUB_PICKER_MODE
$owner = $null
$ownerValue = 0L
if ([long]::TryParse($env:HARDSUB_PICKER_OWNER_HWND, [ref]$ownerValue) -and $ownerValue -gt 0) {
  try {
    Add-Type -ReferencedAssemblies ([System.Windows.Forms.Form].Assembly.Location) -TypeDefinition @'
using System;
using System.Windows.Forms;
public sealed class HardsubPickerOwner : IWin32Window {
  public IntPtr Handle { get; private set; }
  public HardsubPickerOwner(long handle) { Handle = new IntPtr(handle); }
}
'@
    $owner = [HardsubPickerOwner]::new($ownerValue)
  } catch {
    [Console]::Error.WriteLine("Picker owner unavailable; using an unowned dialog: " + $_.Exception.Message)
  }
}
function Show-HardsubDialog($dialog, $owner) {
  if ($owner) {
    try { return $dialog.ShowDialog($owner) }
    catch { [Console]::Error.WriteLine("Owned picker failed; retrying without owner: " + $_.Exception.Message) }
  }
  return $dialog.ShowDialog()
}
$startPath = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyVideos)
if (-not $startPath -or -not (Test-Path -LiteralPath $startPath -PathType Container)) { $startPath = $env:USERPROFILE }
if ($mode -eq 'dosya') {
  $dialog = New-Object System.Windows.Forms.OpenFileDialog
  $dialog.Title = 'Videoları seçin (çoklu seçim açık)'
  $dialog.Filter = 'Video dosyaları (*.mp4;*.mkv;*.avi)|*.mp4;*.mkv;*.avi|Tüm dosyalar (*.*)|*.*'
  $dialog.Multiselect = $true
  $dialog.CheckFileExists = $true
  $dialog.InitialDirectory = $startPath
  $paths = @()
  [System.IO.File]::WriteAllText($env:HARDSUB_PICKER_STARTED, [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString())
  $dialogResult = Show-HardsubDialog $dialog $owner
  if ($dialogResult -eq [System.Windows.Forms.DialogResult]::OK) { $paths = @($dialog.FileNames) }
  @{ yollar = $paths } | ConvertTo-Json -Compress
} else {
  $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
  if ($mode -eq 'inceleme') { $dialog.Description = 'Yerel .review-pack klasörünü seçin' }
  else { $dialog.Description = 'Video klasörünü seçin' }
  $dialog.SelectedPath = $startPath
  $path = ''
  [System.IO.File]::WriteAllText($env:HARDSUB_PICKER_STARTED, [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString())
  $dialogResult = Show-HardsubDialog $dialog $owner
  if ($dialogResult -eq [System.Windows.Forms.DialogResult]::OK) { $path = $dialog.SelectedPath }
  @{ dizin = $path } | ConvertTo-Json -Compress
}
'''
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    env = _child_process_env()
    env["HARDSUB_PICKER_MODE"] = tur
    env["HARDSUB_PICKER_OWNER_HWND"] = str(_foreground_owner_hwnd())
    request_started_epoch_ms = int(time.time() * 1000)
    marker_fd, marker_path = tempfile.mkstemp(prefix="hardsub-picker-", suffix=".started")
    os.close(marker_fd)
    env["HARDSUB_PICKER_STARTED"] = marker_path
    job_id = uuid.uuid4().hex
    process = None
    try:
        with PICKER_JOB_LOCK:
            active = PICKER_JOBS.get(PICKER_ACTIVE_JOB) if PICKER_ACTIVE_JOB else None
            if active and active.get("status") in ("starting", "opening"):
                os.unlink(marker_path)
                return jsonify({"hata": "Windows seçicisi zaten açık; önce mevcut seçimi tamamlayın."}), 409
            now = time.time()
            for old_id, old in list(PICKER_JOBS.items()):
                if old.get("finished_at", now) < now - 600:
                    PICKER_JOBS.pop(old_id, None)
            process_started_epoch_ms = int(time.time() * 1000)
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            process = subprocess.Popen(
                [powershell, "-NoProfile", "-STA", "-EncodedCommand", encoded_script],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", env=env, cwd=str(KLASOR),
                creationflags=flags,
            )
            PICKER_JOBS[job_id] = {
                "process": process, "marker": marker_path, "mode": tur, "status": "starting",
                "result": None, "hata": None, "started_at": now,
                "request_started_epoch_ms": request_started_epoch_ms,
                "process_started_epoch_ms": process_started_epoch_ms,
                "timing": {"spawn_ms": process_started_epoch_ms - request_started_epoch_ms},
            }
            PICKER_ACTIVE_JOB = job_id
            watcher = threading.Thread(target=_watch_picker_job, args=(job_id,), daemon=True)
            PICKER_JOBS[job_id]["watcher"] = watcher
    except Exception as exc:
        _picker_terminate(process)
        try:
            os.unlink(marker_path)
        except OSError:
            pass
        app.logger.exception("Windows picker helper could not start")
        return jsonify({"hata": f"Windows seçicisi başlatılamadı: {exc}"}), 500

    try:
        watcher.start()
    except Exception as exc:
        _picker_terminate(process)
        try:
            process.communicate()
        except (OSError, ValueError):
            app.logger.exception("Windows picker helper output could not be closed")
        _picker_finish(job_id, "error", error=f"Windows seçicisi izleyicisi başlatılamadı: {exc}")
        try:
            os.unlink(marker_path)
        except OSError:
            pass
        app.logger.exception("Windows picker watcher could not start")
        return jsonify({"hata": f"Windows seçicisi izlenemedi: {exc}"}), 500
    app.logger.info("picker started mode=%s job_id=%s spawn_ms=%d",
                    tur, job_id, process_started_epoch_ms - request_started_epoch_ms)
    return jsonify({"job_id": job_id, "status": "starting"}), 202


@app.get("/api/secim/<job_id>")
def api_secim_durum(job_id):
    with PICKER_JOB_LOCK:
        job = PICKER_JOBS.get(job_id)
        if job is None:
            return jsonify({"hata": "Seçici işi bulunamadı veya süresi doldu."}), 404
        result = {"job_id": job_id, "status": job["status"], "timing": dict(job["timing"])}
        if job["status"] == "done":
            result["result"] = job["result"]
        elif job.get("hata"):
            result["hata"] = job["hata"]
    return jsonify(result)


@app.post("/api/ac")
def api_ac():
    v = request.get_json(force=True, silent=True) or {}
    yol = str(v.get("yol") or "").strip()
    p = Path(yol)
    if p.is_dir():
        subprocess.Popen(["explorer", str(p)], env=_child_process_env())
    elif p.is_file():
        subprocess.Popen(["explorer", "/select,", str(p)], env=_child_process_env())
    else:
        return jsonify({"hata": f"yol bulunamadı: {yol}"}), 404
    return jsonify({"ok": True})


@app.get("/qa/<path:dosya>")
def api_qa(dosya):
    """/qa/<is-id>/<rel-yol> → o işin QA klasörü; eski işler için kök fallback."""
    kok_qa = None
    rel = dosya
    parca = dosya.split("/", 1)
    with KILIT:
        if len(parca) == 2 and parca[0].startswith("is-"):
            isim = next((i for i in ISLER if i["id"] == parca[0]), None)
            if isim is not None:
                kok_qa = Path(isim.get("qa_root") or
                              (Path(isim["sec"]["cikti_klasor"]) / "qa"))
                rel = parca[1]
        if kok_qa is None:
            for i in reversed(ISLER):
                if i["qa_dosyalari"]:
                    kok_qa = Path(i.get("qa_root") or
                                  (Path(i["sec"]["cikti_klasor"]) / "qa"))
                    break
    if kok_qa is None:
        kok_qa = KLASOR / "qa"
    return send_from_directory(kok_qa, rel)


# -------------------------------------------- blok editörü + VTT kıyas -----
SRT_ZAMAN_RE = re.compile(r"^(\d{1,3}):(\d{1,2}):(\d{1,2})[.,](\d{1,3})$")


def srt_zaman_norm(s):
    """'0:00:01,000' / '00:00:01.3' → '00:00:01,300'; geçersizse None."""
    s = str(s or "").strip()
    m = SRT_ZAMAN_RE.match(s.replace(".", ","))
    if not m:
        return None
    h, mi, se, ms = m.groups()
    if int(mi) > 59 or int(se) > 59:
        return None
    return f"{int(h):02d}:{int(mi):02d}:{int(se):02d},{(ms + '000')[:3]}"


def srt_parse(metin):
    """SRT metni → [{'index','start','end','metin'}]; index dosya sırası (0 tabanlı)."""
    bloklar = []
    metin = metin.replace("\r\n", "\n").replace("\r", "\n")
    for ham_blok in re.split(r"\n[ \t]*\n+", metin):
        satirlar = [ln for ln in ham_blok.split("\n") if ln.strip()]
        if not satirlar:
            continue
        ti = next((k for k, ln in enumerate(satirlar) if "-->" in ln), None)
        if ti is None:
            continue
        sol, _, sag = satirlar[ti].partition("-->")
        sol_tokens, sag_tokens = sol.split(), sag.split()
        st = srt_zaman_norm(sol_tokens[-1]) if sol_tokens else None
        en = srt_zaman_norm(sag_tokens[0]) if sag_tokens else None
        mtn = "\n".join(satirlar[ti + 1:]).strip()
        if st is None or en is None or not mtn:
            continue
        bloklar.append({"index": len(bloklar), "start": st, "end": en, "metin": mtn})
    return bloklar


def is_srt_bul(is_id):
    """İşin SRT'sini bulur. Yol güvenliği: kayıt yalnız kuyruktaki işten gelir
    ve çözümlenen yol işin kendi koşu klasörü içinde olmak zorundadır.
    Dönüş: (snapshot, srt_path, (hata, http_kod) | None)."""
    with KILIT:
        isim = next((i for i in ISLER if i["id"] == is_id), None)
        snap = is_snapshot(isim) if isim else None
    if snap is None:
        return None, None, ("iş bulunamadı", 404)
    if not snap["srt"]:
        return snap, None, ("işin SRT kaydı yok", 400)
    srt = Path(snap["srt"])
    cikti = Path(snap.get("run_dir") or
                 snap["sec"]["cikti_klasor"]).expanduser().resolve()
    try:
        srt = srt.resolve()
    except OSError:
        return snap, None, ("SRT yolu çözülemedi", 400)
    if not _yol_altinda(srt, cikti) or srt.suffix.lower() != ".srt":
        return snap, None, ("SRT yolu işin koşu klasörü dışında — reddedildi", 403)
    if not srt.exists():
        return snap, None, (f"SRT bulunamadı: {srt}", 404)
    return snap, srt, None


def _yol_altinda(yol, kok):
    """Çözümlenmiş yolun kök klasör içinde kaldığını doğrular.

    İki taraf da normalize edilir: iş kaydındaki yol ile klasör aynı dizini
    farklı gösterimle (8.3 kısa ad/bağ) taşıyabilir.
    """
    return path_scope.is_within(yol, kok)


@app.get("/api/bloklar")
def api_bloklar():
    """İşin SRT'sini bloklara ayırır: /api/bloklar?is=<id> → {index,start,end,metin}."""
    snap, srt, hata = is_srt_bul(request.args.get("is", "").strip())
    if hata:
        return jsonify({"hata": hata[0]}), hata[1]
    try:
        bloklar = srt_parse(srt.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError as e:
        return jsonify({"hata": f"SRT okunamadı: {e}"}), 500
    return jsonify({"is_id": snap["id"], "srt": str(srt),
                    "bloklar": bloklar, "vtt": snap.get("vtt")})


def ogrenme_yurut(snap, srt, eski_ham, yeni_bloklar):
    """Kaydedilen düzeltmeden güvenli kelime çiftlerini öğrenir (ogren.py).
    eski_ham: yazmadan ÖNCEki SRT içeriği (.bak ile aynı). SRT yazıldıktan
    SONRA çağrılır; SRT'ye dokunmaz, yalnız sözlüğe + <ad>.ogrenme-gunlugu.json
    dosyasına yazar. Döner: özet dict (API yanıtının 'ogrenme' alanı)."""
    eski_bloklar = srt_parse(eski_ham)
    ciftler = []
    degisen_blok = 0
    for pos, yeni in enumerate(yeni_bloklar):
        idx = yeni.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(eski_bloklar):
            idx = pos if pos < len(eski_bloklar) else None
        if idx is None:
            continue                       # yeni eklenen blok — eski yok
        eski_m = eski_bloklar[idx]["metin"]
        if re.sub(r"\s+", " ", eski_m).strip() == \
                re.sub(r"\s+", " ", yeni["metin"]).strip():
            continue                       # blok değişmedi
        degisen_blok += 1
        for e, y, il in ogren_mod.kelime_ciftleri(eski_m, yeni["metin"]):
            ciftler.append((e, y, il))
    kayitlar, ozet = ogren_mod.ciftleri_ogren(ciftler, SOZLUK, yaz=True,
                                              kaynak="ui")
    log_yol = srt.with_name(srt.stem + ".ogrenme-gunlugu.json")
    rapor = {
        "arac": "ogren.py (ui_server Kaydet=öğren)",
        "kaynak": "ui", "is_id": snap["id"], "srt": str(srt),
        "zaman": datetime.now().isoformat(timespec="seconds"),
        "mod": "uygula", "sozluk": str(SOZLUK),
        "degisen_blok": degisen_blok,
        "ciftler": kayitlar, "ozet": ozet,
    }
    log_yol.write_text(json.dumps(rapor, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    log_ekle("tamam", f"öğrenme: {ozet['ogrenildi']} öğrenildi, "
                      f"{ozet['belirsiz']} belirsiz, {ozet['atlandi']} atlandı, "
                      f"{ozet['catisma']} çatışma, {ozet['zaten_var']} zaten var "
                      f"({log_yol.name})")
    return {"ogrenildi": ozet["ogrenildi"], "belirsiz": ozet["belirsiz"],
            "atlandi": ozet["atlandi"], "catisma": ozet["catisma"],
            "zaten_var": ozet["zaten_var"], "rapor": log_yol.name}


@app.post("/api/blokkaydet")
def api_blokkaydet():
    """Düzenlenen blokları SRT'ye yeniden yazar; yazmadan önce <ad>.srt.bak
    yedeği alır. Zamanlar doğrulanır (salt-okunur), boş bloklar düşer.
    v1.2: kayıt sonrası değişen bloklardan güvenli kelime çiftleri otomatik
    öğrenilir (ogren.py; sözlük + <ad>.ogrenme-gunlugu.json)."""
    v = request.get_json(force=True, silent=True) or {}
    ham = v.get("bloklar")
    snap, srt, hata = is_srt_bul(str(v.get("is_id") or "").strip())
    if hata:
        return jsonify({"hata": hata[0]}), hata[1]
    if not isinstance(ham, list) or not ham:
        return jsonify({"hata": "bloklar listesi gerekli"}), 400
    bloklar = []
    for b in ham:
        if not isinstance(b, dict):
            return jsonify({"hata": "bloklar listesi nesne içermeli"}), 400
        st = srt_zaman_norm(b.get("start"))
        en = srt_zaman_norm(b.get("end"))
        if st is None or en is None:
            return jsonify({"hata": f"blok {b.get('index')}: zaman biçimi geçersiz"}), 400
        mtn = str(b.get("metin") or "").replace("\r\n", "\n").replace("\r", "\n")
        mtn = "\n".join(ln.strip() for ln in mtn.split("\n")).strip()
        if mtn:
            idx = b.get("index")
            bloklar.append({"index": idx if isinstance(idx, int) else None,
                            "start": st, "end": en, "metin": mtn})
    if not bloklar:
        return jsonify({"hata": "tüm bloklar boş — kayıt iptal edildi"}), 400
    try:
        # newline="": \r\n'i \n'e çevirmez — satır sonu biçimi tespiti için şart
        with open(srt, "r", encoding="utf-8-sig", errors="replace",
                  newline="") as f:
            eski_ham = f.read()
    except OSError as e:
        return jsonify({"hata": f"SRT okunamadı: {e}"}), 500
    nl = "\r\n" if "\r\n" in eski_ham[:4000] else "\n"
    yedek = srt.with_name(srt.name + ".bak")
    try:
        shutil.copy2(srt, yedek)
        parcalar = []
        for n, b in enumerate(bloklar, 1):
            parcalar.append(f"{n}{nl}{b['start']} --> {b['end']}{nl}{b['metin']}")
        srt.write_text(nl.join(parcalar) + nl, encoding="utf-8", newline="")
    except OSError as e:
        return jsonify({"hata": f"SRT yazılamadı: {e}"}), 500
    log_ekle("tamam", f"blok kaydı: {srt.name} ({len(bloklar)} blok, yedek {yedek.name})")
    # Kaydet = öğren: SRT yazıldı; öğrenme başarısız olsa da kayıt geçerli
    ogrenme = None
    if ogren_mod is not None:
        try:
            ogrenme = ogrenme_yurut(snap, srt, eski_ham, bloklar)
        except Exception as e:
            log_ekle("uyari", f"otomatik öğrenme hatası: {e!r}")
            ogrenme = {"hata": repr(e)}
    return jsonify({"ok": True, "blok_sayisi": len(bloklar),
                    "yedek": yedek.name, "srt": str(srt),
                    "ogrenme": ogrenme})


@app.post("/api/ogret")
def api_ogret():
    """Kullanıcı düzeltmesini kullanici-sozlugu.txt'e yazar: eski<TAB>yeni.
    Tekrar: aynı satır varsa 'zaten-var'; eski farklı yeniyle varsa günceller."""
    v = request.get_json(force=True, silent=True) or {}
    eski = str(v.get("eski") or "").strip()
    yeni = str(v.get("yeni") or "").strip()
    if not eski or not yeni:
        return jsonify({"hata": "eski ve yeni boş olamaz"}), 400
    if eski == yeni:
        return jsonify({"hata": "eski ve yeni aynı — öğretilecek düzeltme yok"}), 400
    if "\t" in eski or "\t" in yeni or "\n" in eski or "\n" in yeni:
        return jsonify({"hata": "eski/yeni tek satır olmalı (sekme/satır sonu içeremez)"}), 400
    satirlar = []
    if SOZLUK.exists():
        try:
            satirlar = [ln for ln in SOZLUK.read_text(encoding="utf-8-sig",
                                                      errors="replace").splitlines()
                        if ln.strip()]
        except OSError as e:
            return jsonify({"hata": f"sözlük okunamadı: {e}"}), 500
    yeni_satir = f"{eski}\t{yeni}"
    for k, ln in enumerate(satirlar):
        parcalar = ln.split("\t")
        if parcalar and parcalar[0].strip() == eski:
            if len(parcalar) > 1 and parcalar[1].strip() == yeni:
                return jsonify({"ok": True, "durum": "zaten-var",
                                "satir": yeni_satir, "toplam": len(satirlar)})
            satirlar[k] = yeni_satir
            try:
                SOZLUK.write_text("\n".join(satirlar) + "\n", encoding="utf-8")
            except OSError as e:
                return jsonify({"hata": f"sözlük yazılamadı: {e}"}), 500
            log_ekle("tamam", f"sözlük güncellendi: {eski[:40]!r} → {yeni[:40]!r}")
            return jsonify({"ok": True, "durum": "guncellendi",
                            "satir": yeni_satir, "toplam": len(satirlar)})
    satirlar.append(yeni_satir)
    try:
        SOZLUK.write_text("\n".join(satirlar) + "\n", encoding="utf-8")
    except OSError as e:
        return jsonify({"hata": f"sözlük yazılamadı: {e}"}), 500
    log_ekle("tamam", f"sözlüğe eklendi: {eski[:40]!r} → {yeni[:40]!r}")
    return jsonify({"ok": True, "durum": "eklendi", "satir": yeni_satir,
                    "toplam": len(satirlar)})


@app.get("/api/ogrenmerapor")
def api_ogrenmerapor():
    """İşin SON öğrenme turunun günlüğü: /api/ogrenmerapor?is=<id>.
    Günlük dosyası blokkaydet tarafından <ad>.ogrenme-gunlugu.json olarak
    SRT'nin yanına yazılır; bu uç yalnız OKUR."""
    snap, srt, hata = is_srt_bul(request.args.get("is", "").strip())
    if hata:
        return jsonify({"hata": hata[0]}), hata[1]
    log_yol = srt.with_name(srt.stem + ".ogrenme-gunlugu.json")
    if not log_yol.exists():
        return jsonify({"hata": "bu iş için öğrenme günlüğü yok "
                                "(henüz Kaydet kullanılmadı)"}), 404
    try:
        veri = json.loads(log_yol.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return jsonify({"hata": f"günlük okunamadı: {e!r}"}), 500
    return jsonify(veri)


# ------------------------------------- kontrol paneli + pano (v1.3) --------
PANO_LOW_YUZDE = 40.0   # bu low-conf% oranının üstündekiler pano üstüne
                        # alınır ve kırmızı işaretlenir
BLOKLAR_JSON_ESKI_UYARI = ("Bu çıktıda blok-bazlı güven yok (eski sürüm) — "
                           "kontrol için yeniden koşun")


def _stats_conf_thr(srt_yol):
    """stats.json'daki conf_thr; okunamazsa EasyOCR varsayılanı 0.75."""
    try:
        st = json.loads(srt_yol.with_suffix(".stats.json")
                        .read_text(encoding="utf-8"))
        return float(st.get("conf_thr") or 0.75)
    except Exception:
        return 0.75


def _bloklar_json_oku(srt_yol):
    """<ad>.bloklar.json'ı okur → (ana | None, ekran[]).

    hardsub2srt'in dökümü tek listedir; kaynak alanına göre ayrılır.
    Dosya yoksa/bozuksa (None, []) döner — çağıran eski-çıktı yoluna düşer."""
    bj = srt_yol.with_name(srt_yol.stem + ".bloklar.json")
    try:
        ham = json.loads(bj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, []
    if not isinstance(ham, list):
        return None, []
    ana = [b for b in ham if isinstance(b, dict) and
           b.get("kaynak") == "ana"]
    ekran = [b for b in ham if isinstance(b, dict) and
             b.get("kaynak") == "ekran"]
    return ana, ekran


def _pano_zaman_ms(value):
    normalized = srt_zaman_norm(value)
    if not normalized:
        return None
    try:
        hours, minutes, tail = normalized.split(":")
        seconds, millis = tail.split(",")
        return ((int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000 +
                int(millis))
    except (ValueError, AttributeError):
        return None


def _pano_video_identity(stats_path):
    try:
        if stats_path.stat().st_size > 4 * 1024 * 1024:
            return None
        metadata = json.loads(stats_path.read_text(encoding="utf-8"))
        video = metadata.get("video") if isinstance(metadata, dict) else None
        if not isinstance(video, dict):
            return None
        name = video.get("ad")
        size = video.get("boyut")
        digest = str(video.get("sha1_ilk_1MB") or "").lower()
        if (not isinstance(name, str) or not name or Path(name).name != name or
                Path(name).suffix.lower() not in VIDEO_UZANTILARI or
                type(size) is not int or size <= 0 or
                not re.fullmatch(r"[a-f0-9]{40}", digest)):
            return None
        return {"ad": name, "boyut": size, "sha1_ilk_1MB": digest}
    except (OSError, ValueError, RuntimeError):
        return None


def _pano_seed_evidence_ref(packs, video_identity, block):
    """Match a crop only by video fingerprint, cue interval, and exact old text."""
    if not isinstance(video_identity, dict) or not isinstance(block, dict):
        return None
    start_ms, end_ms = _pano_zaman_ms(block.get("start")), _pano_zaman_ms(block.get("end"))
    if start_ms is None or end_ms is None or end_ms <= start_ms:
        return None
    if not isinstance(block.get("metin"), str) or not block["metin"]:
        return None
    matches = []
    for pack in packs:
        identity = pack.get("_video_identity") or {}
        if (identity.get("size") != video_identity.get("boyut") or
                identity.get("sha1_first_1mb") != video_identity.get("sha1_ilk_1MB")):
            continue
        for cue in pack.get("_cues", []):
            if (cue.get("text") == block["metin"] and
                    abs(cue.get("start_ms", -10**12) - start_ms) <= 800 and
                    abs(cue.get("end_ms", -10**12) - end_ms) <= 800 and
                    cue.get("crop_path")):
                matches.append({"pack_id": pack.get("pack_id"),
                                "cue_id": cue.get("cue_id")})
    return matches[0] if len(matches) == 1 else None


def _blok_conf(kayit):
    """bloklar.json kaydından float conf; yoksa/bozuksa None."""
    try:
        return float(kayit.get("conf"))
    except (TypeError, ValueError):
        return None


@app.get("/api/kontrol")
def api_kontrol():
    """Blok-bazlı güven listesi: /api/kontrol?is=<id>.

    Düzenleme kaynağı daima SRT'nin kendisidir (srt_parse); <ad>.bloklar.json
    YALNIZ conf/kaynak katkısı verir ve konumla (SRT blok sırası) eşleştirilir.
    bloklar.json yoksa (eski çıktı) ya da SRT ile blok sayısı uyuşmuyorsa
    (kayıt sonrası düzenleme) conf'suz liste + uyari döner — panel yine açılır.
    'ekran' alanı _ekran.srt bloklarıdır: bilgilendirme amaçlı, düzenlemez."""
    snap, srt, hata = is_srt_bul(request.args.get("is", "").strip())
    if hata:
        return jsonify({"hata": hata[0]}), hata[1]
    try:
        srt_blok = srt_parse(srt.read_text(encoding="utf-8-sig",
                                           errors="replace"))
    except OSError as e:
        return jsonify({"hata": f"SRT okunamadı: {e}"}), 500
    conf_thr = _stats_conf_thr(srt)
    ana_bj, ekran_bj = _bloklar_json_oku(srt)
    uyari = None
    if ana_bj is None:
        uyari = BLOKLAR_JSON_ESKI_UYARI
    elif len(ana_bj) != len(srt_blok):
        uyari = ("bloklar.json kaydı SRT ile uyuşmuyor (kayıt sonrası "
                 "düzenleme) — güven değerleri güncel değil")
        ana_bj = None
    bloklar = []
    for k, b in enumerate(srt_blok):
        conf = _blok_conf(ana_bj[k]) if ana_bj is not None else None
        bloklar.append({"index": b["index"], "start": b["start"],
                        "end": b["end"], "metin": b["metin"],
                        "conf": conf,
                        "dusuk": bool(conf is not None and conf < conf_thr)})
    ekran = [{"index": b.get("index"), "start": b.get("start"),
              "end": b.get("end"), "metin": str(b.get("metin") or ""),
              "conf": _blok_conf(b),
              "dusuk": bool((_blok_conf(b) is not None and
                             _blok_conf(b) < conf_thr))}
             for b in ekran_bj]
    return jsonify({
        "is_id": snap["id"], "srt": str(srt),
        "bloklar_var": ana_bj is not None,
        "uyari": uyari, "conf_thr": conf_thr,
        "low_conf": sum(1 for b in bloklar if b["dusuk"]),
        "bloklar": bloklar, "ekran": ekran, "vtt": snap.get("vtt"),
    })


@app.get("/api/pano")
def api_pano():
    """Toplu kalite panosu: klasörde ve alt klasörlerdeki *.stats.json taranır
    (varsayılan: sunucu klasörü; ?klasor= ile değiştirilir).

    Tablo: dosya | blok | low-conf% | konuşma sn | hardsub_shr | altyazısız? |
    tarih. low-conf% > PANO_LOW_YUZDE olanlar üstte + low_yuksek işareti.
    Eski-format stats (alan yok) None döner → arayüz boş hücre basar.
    .bloklar.json varsa bloklar_var=True (arayüz 'İncele' bağlantısı kurar)."""
    klasor = Path(request.args.get("klasor") or KLASOR)
    if not klasor.is_dir():
        return jsonify({"hata": f"klasör bulunamadı: {klasor}"}), 404
    try:
        klasor = klasor.resolve()
    except OSError:
        pass
    satirlar = []
    for sp in sorted(klasor.rglob("*.stats.json")):
        try:
            sp = sp.resolve()
            stats_rel = path_scope.relative_to(sp, klasor).as_posix()
        except (OSError, ValueError):
            continue
        kok_ad = sp.name[:-len(".stats.json")]
        if not kok_ad:
            continue
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        if not isinstance(st, dict):
            st = {}
        blok = st.get("blocks") if isinstance(st.get("blocks"), int) else None
        low = st.get("low_conf") if isinstance(st.get("low_conf"), int) \
            else None
        low_pct = None
        if blok and low is not None:
            low_pct = round(low * 100.0 / blok, 1)
        konusma = st.get("speech_seconds")
        if not isinstance(konusma, (int, float)) or \
                isinstance(konusma, bool):
            konusma = None
        shr = st.get("hardsub_shr")
        shr = shr if isinstance(shr, str) else None
        try:
            tarih = datetime.fromtimestamp(sp.stat().st_mtime)\
                             .strftime("%d.%m %H:%M")
        except OSError:
            tarih = ""
        satirlar.append({
            "ad": path_scope.relative_to(sp.parent / kok_ad, klasor).as_posix(),
            "stats": sp.name, "stats_rel": stats_rel,
            "blok": blok, "low_conf": low, "low_pct": low_pct,
            "low_yuksek": bool(low_pct is not None and
                               low_pct > PANO_LOW_YUZDE),
            "konusma_sn": konusma, "hardsub_shr": shr,
            "altyazisiz": st.get("altyazisiz_rip_muhtemel") is True,
            "eski_format": not ("blocks" in st and "low_conf" in st and
                                "conf_thr" in st),
            "bloklar_var": sp.with_name(kok_ad + ".bloklar.json").exists(),
            "tarih": tarih,
        })
    satirlar.sort(key=lambda r: (not r["low_yuksek"],
                                 -(r["low_pct"] if r["low_pct"] is not None
                                   else -1.0), r["ad"]))
    hesapli = [r["low_pct"] for r in satirlar if r["low_pct"] is not None]
    return jsonify({
        "esik": PANO_LOW_YUZDE,
        "ozet": {
            "klasor": str(klasor),
            "toplam_bolum": len(satirlar),
            "toplam_blok": sum(r["blok"] or 0 for r in satirlar),
            "ort_low_pct": round(sum(hesapli) / len(hesapli), 1)
                           if hesapli else None,
            "incelenecek": sum(1 for r in satirlar if r["low_yuksek"]),
            "rip_supheli": sum(1 for r in satirlar if r["altyazisiz"]),
        },
        "satirlar": satirlar,
    })


@app.get("/api/pano/incele")
def api_pano_incele():
    """Pano satırının blok dökümü: /api/pano/incele?stats=<rel-yol>&klasor=<dir>.

    SALT-OKUNUR görüntüleyicidir — düzenleme kuyruktaki iş üzerinden
    (/api/kontrol + /api/blokkaydet) yapılır. SRT metin/zamanları kaynağıdır;
    .bloklar.json yalnız birebir eşleştiğinde güven katkısı verir."""
    klasor = Path(request.args.get("klasor") or KLASOR)
    if not klasor.is_dir():
        return jsonify({"hata": f"klasör bulunamadı: {klasor}"}), 404
    try:
        klasor = klasor.resolve()
    except OSError:
        pass
    stats_rel = (request.args.get("stats") or "").strip()
    if stats_rel:
        stats_input = Path(stats_rel)
        if stats_input.is_absolute() or not stats_rel.endswith(".stats.json"):
            return jsonify({"hata": "geçersiz stats yolu"}), 400
        try:
            stats_path = (klasor / stats_input).resolve()
        except (OSError, RuntimeError):
            return jsonify({"hata": "stats yolu çözülemedi"}), 400
        if not _yol_altinda(stats_path, klasor):
            return jsonify({"hata": "yol klasör dışına çıkıyor"}), 403
        kok_ad = stats_path.name[:-len(".stats.json")]
        ad = path_scope.relative_to(stats_path.parent / kok_ad, klasor).as_posix()
        srt = stats_path.with_name(kok_ad + ".srt")
    else:
        # Önceki arayüz sürümünün API çağrıları için geriye dönük uyum.
        ad = (request.args.get("ad") or "").strip()
        if not ad or "/" in ad or "\\" in ad or ad in (".", ".."):
            return jsonify({"hata": "geçersiz ad"}), 400
        srt = (klasor / (ad + ".srt")).resolve()
        if not _yol_altinda(srt, klasor):
            return jsonify({"hata": "yol klasör dışına çıkıyor"}), 403
    try:
        srt = srt.resolve()
        stats_companion = srt.with_suffix(".stats.json").resolve()
        bloklar_companion = srt.with_name(srt.stem + ".bloklar.json").resolve()
    except (OSError, RuntimeError):
        return jsonify({"hata": "koşu dosyalarının yolu çözülemedi"}), 400
    if not _yol_altinda(srt, klasor):
        return jsonify({"hata": "SRT yolu klasör dışına çıkıyor"}), 403
    if not _yol_altinda(stats_companion, klasor):
        return jsonify({"hata": "stats yolu klasör dışına çıkıyor"}), 403
    if not _yol_altinda(bloklar_companion, klasor):
        return jsonify({"hata": "bloklar.json yolu klasör dışına çıkıyor"}), 403
    conf_thr = _stats_conf_thr(srt)
    ana_bj, ekran_bj = _bloklar_json_oku(srt)
    try:
        srt_blocks = srt_parse(srt.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError as exc:
        return jsonify({"hata": f"SRT okunamadı: {exc}"}), 500

    companion_matches = bool(ana_bj is not None and len(ana_bj) == len(srt_blocks))
    if companion_matches:
        for index, (companion, current) in enumerate(zip(ana_bj, srt_blocks)):
            if (companion.get("index") != index or
                    srt_zaman_norm(companion.get("start")) != current["start"] or
                    srt_zaman_norm(companion.get("end")) != current["end"] or
                    str(companion.get("metin") or "") != current["metin"]):
                companion_matches = False
                break

    if ana_bj is None:
        uyari = BLOKLAR_JSON_ESKI_UYARI
    elif not companion_matches:
        uyari = ("bloklar.json SRT metni veya zamanıyla uyuşmuyor; güncel metin "
                 "SRT'den açıldı, güven değerleri gösterilmiyor.")
    else:
        uyari = None

    video_identity = _pano_video_identity(stats_companion)
    blocks = []
    for index, current in enumerate(srt_blocks):
        confidence = _blok_conf(ana_bj[index]) if companion_matches else None
        blocks.append({"index": index, "start": current["start"],
                       "end": current["end"], "metin": current["metin"],
                       "conf": confidence,
                       "dusuk": bool(confidence is not None and confidence < conf_thr),
                       "evidence_url": None})

    seed_packs = None
    stats_rel_for_url = path_scope.relative_to(stats_companion, klasor).as_posix()
    for block in blocks:
        if not video_identity:
            break
        if seed_packs is None:
            try:
                _seed_root, seed_packs = _seed_review_storage_catalog()
            except Exception:
                seed_packs = []
        match = _pano_seed_evidence_ref(seed_packs, video_identity, block)
        if match:
            query = urlencode({"stats": stats_rel_for_url,
                               "klasor": str(klasor), "index": block["index"]})
            block["evidence_url"] = "/api/pano/seed-evidence?" + query

    ekran = []
    if companion_matches:
        ekran = [{"index": item.get("index"),
                  "start": str(item.get("start") or ""),
                  "end": str(item.get("end") or ""),
                  "metin": str(item.get("metin") or ""),
                  "conf": _blok_conf(item),
                  "dusuk": bool(_blok_conf(item) is not None and
                                _blok_conf(item) < conf_thr),
                  "evidence_url": None} for item in ekran_bj]
    return jsonify({"ad": ad, "srt": str(srt),
                    "bloklar_var": companion_matches,
                    "conf_thr": conf_thr, "uyari": uyari,
                    "video_identity": video_identity,
                    "video_unavailable_reason": (None if video_identity else
                        "Tarihsel stats dosyasında doğrulanabilir video fingerprint yok."),
                    "bloklar": blocks, "ekran": ekran})


@app.get("/api/pano/seed-evidence")
def api_pano_seed_evidence():
    """Serve a crop only after re-matching local stats/SRT to one seed cue."""
    error = _memory_request_error()
    if error:
        return error
    klasor = Path(request.args.get("klasor") or KLASOR)
    stats_rel = (request.args.get("stats") or "").strip()
    try:
        index = int(request.args.get("index", ""))
    except (TypeError, ValueError):
        return Response("Geçersiz blok indeksi.", status=400, mimetype="text/plain")
    if (not stats_rel.endswith(".stats.json") or "\\" in stats_rel or
            Path(stats_rel).is_absolute() or
            any(part in ("", ".", "..") for part in stats_rel.split("/"))):
        return Response("Geçersiz stats yolu.", status=400, mimetype="text/plain")
    try:
        klasor = klasor.resolve(strict=True)
        stats_path = (klasor / stats_rel).resolve(strict=True)
        if (not _yol_altinda(stats_path, klasor) or not stats_path.is_file() or
                stats_path.stat().st_size > 4 * 1024 * 1024):
            return Response("Stats kaynağı bulunamadı.", status=404, mimetype="text/plain")
        stem = stats_path.name[:-len(".stats.json")]
        srt = (stats_path.parent / f"{stem}.srt").resolve(strict=True)
        if (not _yol_altinda(srt, klasor) or not srt.is_file() or
                srt.stat().st_size > 16 * 1024 * 1024):
            return Response("SRT kaynağı bulunamadı.", status=404, mimetype="text/plain")
        video_identity = _pano_video_identity(stats_path)
        if not video_identity:
            return Response("Video fingerprint eşleşmedi.", status=404, mimetype="text/plain")
        blocks = srt_parse(srt.read_text(encoding="utf-8-sig", errors="replace"))
        if not 0 <= index < len(blocks):
            return Response("Blok bulunamadı.", status=404, mimetype="text/plain")
        import ocr_seed_import
        _seed_root, packs = _seed_review_storage_catalog()
        match = _pano_seed_evidence_ref(packs, video_identity, blocks[index])
        if not match:
            return Response("Birebir seed görsel eşleşmesi yok.", status=404,
                            mimetype="text/plain")
        pack_path = ocr_seed_import.open_seed_review_pack(_seed_root, match["pack_id"])
        info = ocr_review._validated_pack(pack_path)
        cue = next((item for item in info["cues"]
                    if item["cue_id"] == match["cue_id"]), None)
        imported = info["manifest"].get("seed_import")
        verified = ocr_review._verify_crop(info, cue) if cue else None
        start_ms = _pano_zaman_ms(blocks[index]["start"])
        end_ms = _pano_zaman_ms(blocks[index]["end"])
        if (not isinstance(imported, dict) or
                imported.get("schema") != "hardsub2srt.assistant-reviewed-seed-import" or
                imported.get("video_identity") != {
                    "size": video_identity["boyut"],
                    "sha1_first_1mb": video_identity["sha1_ilk_1MB"]} or
                cue is None or cue.get("source_text_ocr") != blocks[index]["metin"] or
                start_ms is None or end_ms is None or
                abs(cue.get("start_ms", -10**12) - start_ms) > 800 or
                abs(cue.get("end_ms", -10**12) - end_ms) > 800 or
                verified is None):
            return Response("Seed görsel kanıtı artık eşleşmiyor.", status=404,
                            mimetype="text/plain")
        return send_from_directory(pack_path, cue["crop_path"],
                                   mimetype="image/jpeg", max_age=0,
                                   conditional=True)
    except Exception:
        app.logger.info("Seed evidence request did not match a verified local pack")
        return Response("Seed görsel kanıtı bulunamadı veya eşleşmedi.", status=404,
                        mimetype="text/plain")


@app.get("/api/vttrapor")
def api_vttrapor():
    """İşin VTT kıyas raporunu (JSON) döndürür: /api/vttrapor?is=<id>."""
    with KILIT:
        isim = next((i for i in ISLER
                     if i["id"] == request.args.get("is", "").strip()), None)
        snap = is_snapshot(isim) if isim else None
    if snap is None:
        return jsonify({"hata": "iş bulunamadı"}), 404
    yol = (snap.get("vtt") or {}).get("rapor")
    if not yol or not Path(yol).exists():
        return jsonify({"hata": "bu iş için VTT raporu yok"}), 404
    p = Path(yol).resolve()
    run_dir = Path(snap.get("run_dir") or
                   snap["sec"]["cikti_klasor"]).expanduser().resolve()
    if not _yol_altinda(p, run_dir):
        return jsonify({"hata": "rapor yolu işin koşu klasörü dışında"}), 403
    return send_from_directory(p.parent, p.name, mimetype="application/json")


# ------------------------------------------------------------------ main ---
def main():
    if not ARAC.exists():
        print(f"[HATA] araç bulunamadı: {ARAC}")
        sys.exit(2)
    # tek örnek kilidi: port 8765 meşgulse ikinci sunucu başlamaz
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", PORT))
        s.close()
    except OSError:
        print(f"[HATA] {PORT} portu meşgul — sunucu zaten çalışıyor:")
        print(f"       http://127.0.0.1:{PORT}")
        sys.exit(1)

    t = threading.Thread(target=isci, daemon=True)
    t.start()
    log_ekle("bilgi", f"sunucu başladı: http://127.0.0.1:{PORT} — klasör: {KLASOR}")
    print(f"[ui] http://127.0.0.1:{PORT}  (durdurmak icin Ctrl+C)")
    print(f"[ui] arac: {ARAC}")
    try:
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False,
                threaded=True)
    except KeyboardInterrupt:
        print("[ui] kapanış istendi; Windows seçicisi varsa kapatılıyor")
    finally:
        _stop_picker_helpers()


if __name__ == "__main__":
    main()
