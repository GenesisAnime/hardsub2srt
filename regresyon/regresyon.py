# -*- coding: utf-8 -*-
"""hardsub2srt regresyon seti — küçük kıyas mekanizması.

Amaç: hardsub2srt.py kod değişikliklerinden sonra, sabit kare seti üzerinde
OCR katmanının eski baseline'dan kötüleşip kötüleşmediğini 1-2 dakikada
söylemek. Araca DOKUNMAZ — yalnız import eder (import bozuksa temiz bir
alt süreçte kendini yeniden çalıştırır: mikro koşum).

Kullanım akışı (ayrıntı: README-kisa.md):
  py -3 regresyon.py --kare-cikar          # 1) ffmpeg ile sabit kareler (bir kez)
  py -3 regresyon.py --bant-yenile         # 2) detect_style bantları noktalar.json'a (bir kez)
  py -3 regresyon.py                       # 3) OCR koşumu -> baseline.json (bir kez)
  py -3 regresyon.py --cikti yeni.json     # 4) kod değişikliğinden sonra tekrar koşum
  py -3 regresyon.py --karsilastir yeni.json   # 5) kıyas: kötüleşme raporu

OCR zinciri aracın kendisiyle birebir aynıdır: grab (PNG'den kırpım) ->
binarize|ham (mask'e göre) -> isteğe bağlı 2x LANCZOS -> 3 varyant
(OCR_VARIANTS) konsensus -> conf < LOW_CONF_THR ise ikinci motor oyu
(get_second_engine) -> _consensus_pick. Fark: tek kare, video taraması yok
(scan_band yok — regresyon OCR katmanına odaklıdır; bant koordinatları
--bant-yenile ile aracın kendi detect_style+_dar_bant_genislet zincirinden
gelir ve noktalar.json'da dondurulur).

Girdi sabitliği: OCR girdisi VİDEO değil
kareler/*.png'dir — video sonradan değişse bile baseline kıyası geçerli kalır.
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KOK = Path(__file__).resolve().parent
VARSAYILAN_NOKTALAR = KOK / "noktalar.json"
VARSAYILAN_BASELINE = KOK / "baseline.json"
VARSAYILAN_KARELER = KOK / "kareler"

# ---------------------------------------------------------------------------
# Araç importu
# ---------------------------------------------------------------------------

def araci_yukle(arac_yol: Path):
    """hardsub2srt.py'yi import et; modül yolunu sys.path'e ekler."""
    if str(arac_yol.parent) not in sys.path:
        sys.path.insert(0, str(arac_yol.parent))
    import hardsub2srt as h2s  # noqa: PLC0415 — bilinçli geç import
    return h2s


# ---------------------------------------------------------------------------
# Ortak yardımcılar
# ---------------------------------------------------------------------------

def noktalari_oku(yol: Path) -> dict:
    with open(yol, encoding="utf-8") as f:
        return json.load(f)


def noktalari_yaz(yol: Path, veri: dict) -> None:
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)
        f.write("\n")


def video_bul(nokta: dict) -> str | None:
    eslesen = sorted(glob.glob(nokta["video_glob"]))
    return eslesen[0] if eslesen else None


def ts_str(t: float) -> str:
    return str(int(t)) if float(t) == int(t) else str(round(float(t), 1))


def png_yolu(kareler: Path, nokta: dict) -> Path:
    return kareler / f"{nokta['id']}.png"


# ---------------------------------------------------------------------------
# Mod: --kare-cikar
# ---------------------------------------------------------------------------

def kare_cikar(args, h2s) -> int:
    noktalar = noktalari_oku(Path(args.noktalar))["noktalar"]
    kareler = Path(args.kareler)
    kareler.mkdir(parents=True, exist_ok=True)
    hata = 0
    for n in noktalar:
        hedef = png_yolu(kareler, n)
        video = video_bul(n)
        if video is None:
            print(f"[X] {n['id']}: video bulunamadi: {n['video_glob']}")
            hata += 1
            continue
        cmd = [h2s.FFMPEG, "-y", "-v", "error",
               "-ss", f"{n['zaman_sn']:.6f}", "-i", video,
               "-frames:v", "1", str(hedef)]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0 or not hedef.exists():
            err = r.stderr.decode(errors="replace")[:200]
            print(f"[X] {n['id']}: ffmpeg hatasi: {err}")
            hata += 1
            continue
        kb = hedef.stat().st_size / 1024
        print(f"[OK] {hedef.name}  {kb:.0f} KB  <- {Path(video).name} "
              f"@ {n['zaman_sn']}s")
    print(f"\n{kareler}: {len(noktalar) - hata}/{len(noktalar)} kare")
    return 1 if hata else 0


# ---------------------------------------------------------------------------
# Mod: --bant-yenile
# ---------------------------------------------------------------------------

def _bant_tespit(h2s, nokta: dict, onbellek: dict) -> dict | None:
    """Tek nokta için aracın bant zinciri: detect_style + _dar_bant_genislet.
    Bolum bazında önbellekler; video yoksa None döner."""
    bolum = nokta["bolum"]
    if bolum in onbellek:
        return dict(onbellek[bolum])
    video = video_bul(nokta)
    if video is None:
        return None
    info = h2s.run_ffprobe(video)
    det = h2s.detect_style(video, info["width"], info["height"],
                           info["duration"])
    if det is None:
        det = {"band_y": h2s.DEFAULT_BAND_Y, "band_h": h2s.DEFAULT_BAND_H,
               "mask": h2s.DEFAULT_MASK,
               "white_thr": h2s.DEFAULT_WHITE_THR, "text_h": None}
        kaynak = "varsayilan (detect_style basarisiz)"
    else:
        genis = h2s._dar_bant_genislet(det, info["height"])
        if genis is not None:
            det = genis
            kaynak = "auto-genisletildi (dar bant -> hedef h)"
        else:
            kaynak = "auto"
    bant = {"band_y": int(det["band_y"]), "band_h": int(det["band_h"]),
            "mask": det["mask"], "white_thr": int(det["white_thr"]),
            "tophat_thr": int(h2s.DEFAULT_TOPHAT_THR),
            "text_h": det.get("text_h"),
            "upscale2x": bool(det.get("text_h") is not None
                              and det["text_h"] < h2s.UPSCALE_TEXT_H),
            "kaynak": kaynak, "video": Path(video).name,
            "cozunurluk": f"{info['width']}x{info['height']}"}
    if det.get("core"):
        bant["core"] = round(float(det["core"]), 1)
    if det.get("beyaz_fon"):
        bant["beyaz_fon"] = True
    onbellek[bolum] = bant
    return dict(bant)


def bant_yenile(args, h2s) -> int:
    dokuman = noktalari_oku(Path(args.noktalar))
    noktalar = dokuman["noktalar"]
    onbellek: dict[int, dict] = {}
    hata = 0
    for n in noktalar:
        t0 = time.time()
        bant = _bant_tespit(h2s, n, onbellek)
        if bant is None:
            print(f"[X] {n['id']}: video yok — bant yazilamadi")
            hata += 1
            continue
        n["bant"] = bant
        print(f"[OK] {n['id']}: y={bant['band_y']} h={bant['band_h']} "
              f"mask={bant['mask']} thr={bant['white_thr']} "
              f"({bant['kaynak']}, {time.time()-t0:.0f}s)")
    noktalari_yaz(Path(args.noktalar), dokuman)
    print(f"\nnoktalar.json guncellendi: {len(noktalar)-hata}/{len(noktalar)} "
          f"noktada bant donduruldu")
    return 1 if hata else 0


# ---------------------------------------------------------------------------
# OCR koşumu (baseline / yeni json)
# ---------------------------------------------------------------------------

def _nokta_ocr(h2s, reader, img, bant: dict, lang: list[str]):
    """Aracın OCR zincirinin tek-kare koşumu. Döner: (metin, conf, ikinci_oy)."""
    y = max(0, min(bant["band_y"], img.shape[0] - 1))
    h = max(1, min(bant["band_h"], img.shape[0] - y))
    bant_img = img[y:y + h]
    if bant["mask"] == "tophat":
        img_in = bant_img                      # ham bant (aracın kuralı)
    else:
        img_in = h2s.binarize_white(bant_img, bant["white_thr"])
    if bant.get("upscale2x"):
        import cv2  # noqa: PLC0415
        img_in = cv2.resize(img_in, None, fx=2, fy=2,
                            interpolation=cv2.INTER_LANCZOS4)

    var_out = [h2s.ocr_lines_batch(reader, [img_in], 1, **kw)
               for kw in h2s.OCR_VARIANTS]
    adaylar = [v[0] for v in var_out]
    metin, conf = h2s._consensus_pick(iter(adaylar))
    ikinci = None
    if metin and conf < h2s.LOW_CONF_THR:
        sec = h2s.get_second_engine(lang, False, "auto",
                                    conf_thr=h2s.LOW_CONF_THR)
        if sec["fn"] is not None:
            try:
                t2, c2 = sec["fn"](img_in)
            except Exception:
                t2 = ""
            if t2:
                ikinci = {"metin": t2, "conf": round(float(c2), 4),
                          "motor": sec["name"]}
                metin, conf = h2s._consensus_pick(adaylar + [(t2, c2)])
    return metin, conf, ikinci


def kosum(args, h2s) -> int:
    dokuman = noktalari_oku(Path(args.noktalar))
    noktalar = dokuman["noktalar"]
    kareler = Path(args.kareler)
    gpu_flag = bool(args.gpu)

    # eksik bant: yerinde detect_style (kalıcılık için --bant-yenile önerilir)
    onbellek: dict[int, dict] = {}
    for n in noktalar:
        if n.get("bant") is None:
            print(f"[!] {n['id']}: bant yok — yerinde detect_style "
                  f"(kalicilik icin --bant-yenile calistirin)")
            bant = _bant_tespit(h2s, n, onbellek)
            if bant is None:
                print(f"[X] {n['id']}: video yok — bant tespit edilemedi")
                return 2
            n["bant"] = bant

    try:
        import easyocr  # noqa: F401,PLC0415 — sürüm kaydı için
        import onnxruntime  # noqa: F401,PLC0415
    except Exception as e:
        print(f"[X] OCR bagimliligi yok: {type(e).__name__}: {e}")
        return 2

    h2s._ort_cuda_probe(gpu_flag)
    import easyocr
    reader = easyocr.Reader(["tr", "en"], gpu=gpu_flag, verbose=False)
    cihaz = "GPU" if gpu_flag else "CPU (regresyon)"

    sonuclar = {}
    hata = 0
    for n in noktalar:
        png = png_yolu(kareler, n)
        if not png.exists():
            print(f"[X] {n['id']}: kare yok: {png.name} — once --kare-cikar")
            sonuclar[n["id"]] = {"okundu": False, "hata": "kare yok",
                                 "png": png.name}
            hata += 1
            continue
        import cv2  # noqa: PLC0415
        img = cv2.imread(str(png), cv2.IMREAD_COLOR)
        if img is None:
            print(f"[X] {n['id']}: PNG okunamadi: {png.name}")
            sonuclar[n["id"]] = {"okundu": False, "hata": "PNG okunamadi",
                                 "png": png.name}
            hata += 1
            continue
        t0 = time.time()
        try:
            metin, conf, ikinci = _nokta_ocr(h2s, reader, img, n["bant"],
                                             ["tr", "en"])
        except Exception as e:
            print(f"[X] {n['id']}: OCR hatasi: {type(e).__name__}: {e}")
            sonuclar[n["id"]] = {"okundu": False,
                                 "hata": f"{type(e).__name__}: {e}",
                                 "png": png.name}
            hata += 1
            continue
        sure = time.time() - t0
        ozet = metin.replace("\n", " / ")[:60]
        print(f"[OK] {n['id']}  conf={conf:.3f}  {sure:.1f}s  {ozet!r}"
              + (f"  [2.motor: {ikinci['motor']}]" if ikinci else ""))
        sonuclar[n["id"]] = {
            "okundu": True, "metin": metin, "conf": round(float(conf), 4),
            "ikinci_oy": ikinci, "sure_sn": round(sure, 1),
            "bant": n["bant"], "png": png.name,
        }

    import easyocr as _eo
    try:
        import onnxruntime as _ort
        ort_surum = _ort.__version__
    except Exception:
        ort_surum = None
    cikti = Path(args.cikti)
    rapor = {
        "meta": {
            "arac": "hardsub2srt.py",
            "surum": h2s.ARAC_SURUM,
            "arac_yol": str(dokuman.get("arac_yol", "")),
            "tarih": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cihaz": cihaz, "dil": "tr,en",
            "easyocr": getattr(_eo, "__version__", "?"),
            "onnxruntime": ort_surum,
            "nokta_sayisi": len(noktalar),
            "hata_sayisi": hata,
            "not": "OCR girdisi kareler/*.png (sabit); bant koordinatlari "
                   "noktalar.json'da donduruldu (--bant-yenile). Zincir: "
                   "3 varyant konsensus + conf<0.75 ikinci motor oyu "
                   "(hardsub2srt.py ile ayni).",
        },
        "noktalar": sonuclar,
    }
    with open(cikti, "w", encoding="utf-8") as f:
        json.dump(rapor, f, ensure_ascii=False, indent=2)
        f.write("\n")
    okunan = len(noktalar) - hata
    print(f"\n{cikti}: {okunan}/{len(noktalar)} nokta OCR'landi "
          f"({cihaz}, {h2s.ARAC_SURUM})")
    return 1 if hata == len(noktalar) else 0


def mikro_kosum(args) -> int:
    """Import bozuksa: temiz alt süreçte kendini --ic-kosum ile çalıştır."""
    tmp = KOK / "_mikro-kosum-sonuc.json"
    cmd = [sys.executable, str(Path(__file__).resolve()), "--ic-kosum",
           "--cikti", str(tmp)]
    if args.noktalar:
        cmd += ["--noktalar", str(args.noktalar)]
    if args.kareler:
        cmd += ["--kareler", str(args.kareler)]
    if getattr(args, "gpu", False):
        cmd += ["--gpu"]
    print("[i] import bozuk — mikro kosum (temiz alt surec) deneniyor...")
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not tmp.exists():
        sys.stderr.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        print("[X] mikro kosum basarisiz")
        return 2
    with open(tmp, encoding="utf-8") as f:
        rapor = json.load(f)
    tmp.unlink(missing_ok=True)
    hedef = Path(args.cikti)
    with open(hedef, "w", encoding="utf-8") as f:
        json.dump(rapor, f, ensure_ascii=False, indent=2)
        f.write("\n")
    m = rapor["meta"]
    print(f"[OK] mikro kosum: {m['nokta_sayisi'] - m['hata_sayisi']}/"
          f"{m['nokta_sayisi']} nokta -> {hedef.name}")
    return 0


# ---------------------------------------------------------------------------
# Mod: --karsilastir
# ---------------------------------------------------------------------------

def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    onceki = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        simdi = [i]
        for j, cb in enumerate(b, 1):
            simdi.append(min(onceki[j] + 1, simdi[j - 1] + 1,
                             onceki[j - 1] + (ca != cb)))
        onceki = simdi
    return onceki[-1]


def _norm(metin: str) -> str:
    return " ".join((metin or "").split())


def karsilastir(args) -> int:
    with open(Path(args.baseline), encoding="utf-8") as f:
        eski = json.load(f)
    with open(Path(args.karsilastir), encoding="utf-8") as f:
        yeni = json.load(f)
    print(f"baseline : {eski['meta']['tarih']}  {eski['meta']['surum']}"
          f"  ({eski['meta']['cihaz']})")
    print(f"yeni     : {yeni['meta']['tarih']}  {yeni['meta']['surum']}"
          f"  ({yeni['meta']['cihaz']})")
    print()

    satirlar = []
    atlanan = []
    for nid, yeni_n in yeni["noktalar"].items():
        eski_n = eski["noktalar"].get(nid)
        if eski_n is None:
            atlanan.append((nid, "baseline'da yok"))
            continue
        if not eski_n.get("okundu") or not yeni_n.get("okundu"):
            atlanan.append((nid, "eski/yeni OCR hatasi"))
            continue
        a, b = _norm(eski_n["metin"]), _norm(yeni_n["metin"])
        if not a and not b:
            cer, benzer, durum = 0.0, 1.0, "ikisi de bos"
        elif not a or not b:
            cer, benzer, durum = 1.0, 0.0, "bos/dolu farki"
        else:
            cer = _lev(a, b) / max(len(a), len(b))
            benzer = 1.0 - cer
            durum = "ayni" if a == b else f"farkli ({_lev(a, b)} harf)"
        satirlar.append({"id": nid, "cer": cer, "benzer": benzer,
                         "durum": durum,
                         "conf_eski": eski_n.get("conf"),
                         "conf_yeni": yeni_n.get("conf"),
                         "uzunluk": max(len(a), len(b))})

    w = max((len(s["id"]) for s in satirlar), default=10)
    for s in satirlar:
        print(f"{s['id']:<{w}}  CER={s['cer']:.3f}  benzerlik="
              f"{s['benzer']:.3f}  conf {s['conf_eski']}->{s['conf_yeni']}  "
              f"{s['durum']}")
    for nid, sebep in atlanan:
        print(f"{nid:<{w}}  ATLANDI ({sebep})")

    if not satirlar:
        print("\nKiyaslanabilir nokta yok.")
        return 2
    ort = sum(s["cer"] for s in satirlar) / len(satirlar)
    en_kotu = sorted(satirlar, key=lambda s: -s["cer"])[:3]
    print(f"\nortalama CER : {ort:.4f}  (tolerans: ort<={args.tol_ort}, "
          f"tekil<={args.tol_tek})")
    print("en kotu 3    : " + "; ".join(
        f"{s['id']} ({s['cer']:.3f})" for s in en_kotu))

    kotu_ortalama = ort > args.tol_ort
    kotu_tekil = any(s["cer"] > args.tol_tek and s["uzunluk"] >= 10
                     for s in satirlar)
    hepsi_ayni = all(s["cer"] == 0.0 for s in satirlar)
    if hepsi_ayni:
        print("SONUC: DEGISIKLIK YOK — baseline ile birebir.")
        return 0
    if kotu_ortalama or kotu_tekil:
        print("SONUC: KOTULESME SUPHESI — degisen noktalari elle karsilastir "
              "(baseline.json vs yeni.json 'metin' alanlari).")
        return 1
    print("SONUC: kucuk sapma — esik altinda; yine de degisen satirlara goz at.")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="hardsub2srt regresyon seti (baseline + kiyas)")
    ap.add_argument("--noktalar", default=str(VARSAYILAN_NOKTALAR))
    ap.add_argument("--kareler", default=str(VARSAYILAN_KARELER))
    ap.add_argument("--cikti", default=str(VARSAYILAN_BASELINE),
                    help="kosum sonucunun yazilacagi JSON "
                         "(varsayilan: baseline.json)")
    ap.add_argument("--baseline", default=str(VARSAYILAN_BASELINE),
                    help="--karsilastir referansi")
    ap.add_argument("--karsilastir", metavar="YENI_JSON",
                    help="baseline ile yeni sonucu kiyasla")
    ap.add_argument("--kare-cikar", action="store_true",
                    help="ffmpeg ile sabit kareleri cikar (bir kez)")
    ap.add_argument("--bant-yenile", action="store_true",
                    help="detect_style + dar-bant genisletme ile bant "
                         "koordinatlarini noktalar.json'a yaz (bir kez)")
    ap.add_argument("--tol-ort", type=float, default=0.02,
                    help="ortalama CER kotulesme esigi (0.02)")
    ap.add_argument("--tol-tek", type=float, default=0.15,
                    help="tek nokta CER kotulesme esigi (0.15)")
    ap.add_argument("--gpu", action="store_true",
                    help="OCR'u GPU'da kos (varsayilan: CPU — GPU arka plan "
                         "toplu kosumunda mesgul olabilir)")
    ap.add_argument("--ic-kosum", action="store_true",
                    help=argparse.SUPPRESS)  # mikro kosum ic kullanimi
    args = ap.parse_args()

    if args.karsilastir:
        return karsilastir(args)

    # --ic-kosum: import zaten denendi ve bozuksa ust surec buraya dustu;
    # temiz yorumlayıcıda tekrar denenir.
    try:
        h2s = araci_yukle(Path(noktalari_oku(Path(args.noktalar))
                               .get("arac_yol",
                                    str(Path(__file__).resolve().parent.parent
                                        / "hardsub2srt.py"))))
    except Exception as e:
        if args.ic_kosum:
            print(f"[X] arac import edilemedi: {type(e).__name__}: {e}",
                  file=sys.stderr)
            return 2
        # mikro koşum: aynı betik temiz alt süreçte import'u tekrar dener
        return mikro_kosum(args)

    if args.ic_kosum:
        return kosum(args, h2s)
    if args.kare_cikar:
        return kare_cikar(args, h2s)
    if args.bant_yenile:
        return bant_yenile(args, h2s)
    return kosum(args, h2s)


if __name__ == "__main__":
    sys.exit(main())
