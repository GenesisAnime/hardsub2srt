#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ogren.py — auto SRT ile kullanıcı düzeltmesi arasındaki farktan GÜVENLİ
kelime çifti öğrenir ve kullanici-sozlugu.txt'e yazar (post-fix bu sözlüğü
zaten otomatik tüketiyor: hardsub2srt.py `_kullanici_uygula`).

Kullanım:
    py -3 ogren.py "<auto.srt>" "<duzeltilmis.srt>"            # KURU: yalnız rapor
    py -3 ogren.py "<auto.srt>" "<duzeltilmis.srt>" --uygula   # sözlüğe yaz
    py -3 ogren.py ... -o rapor.json

Hizalama: vtt-qa.py'nin örtüşme mantığı — en büyük örtüşme önce kazanır,
bire bir eşleme, örtüşme/süre >= %50 (MIN_OVERLAP_RATIO).

ZEHİRLLEME KORUMASI (en önemli kalite şartı) — hardsub2srt.py post-fix
ailesinin BAĞIMSIZ kopyası; dört güvenli sınıftan biri olmayan hiçbir çift
öğrenilmez:
  (a) diakritik  : s<->ş, c<->ç, g<->ğ, ı<->i; <=2 nokta değişikliği; hedef
                   sözlükte OLMALI ve tek egemen aday olmalı (DOMINANS 10x;
                   'son'->'sön' gibi iki güçlü kelime öğrenilmez)
  (b) kaynaşma   : birleşik eski -> TAM iki bilinen kelime; nokta-normalize
                   birleştirme eşitliği; parça >= 4 harf; ek-veto
  (c) noktalama  : çekirdek kelime aynı, yalnız noktalama farkı; eski/yeni
                   token'ı birbirinin alt-dizisi OLAMAZ (".."->"..." her
                   uygulamada uzayan tuzağı; post-fix ellipsis'i zaten onarır)
  (d) harf       : 1 ağır işlem (ekle/sil/değiştir; ± diakritik) ya da aynı
                   uzunlukta <=2 salt harf-değiştirme ailesi içinde; hedef tek
                   egemen aday (FREQ_MIN 50 / DOMINANS 10)
Serbest yeniden yazım (uzun fark) HİÇ öğrenilmez -> "belirsiz".
Aynı eski farklı yeniyle sözlükte varsa İLK kalır, çatışma loglanır.
Aynı eski+yeni tekrar gelirse eklenmez ("zaten_var").

Aynı sınıflandırıcı ui_server.py tarafından da kullanılır (Kaydet = öğren);
tek kaynak burasıdır — kopyalamak yerine import edin.
Bu araç hardsub2srt.py / srt2ass.py / mevcut SRT'lere DOKUNMAZ; yazma yalnız
kullanici-sozlugu.txt (--uygula ile) ve -o rapor dosyasına yapılır.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

SURUM = "1.0"

# --- post-fix ailesiyle AYNI eşikler (hardsub2srt.py) ----------------------
DUZELT_MIN_LEN = 3            # bu uzunluğun altındaki kelimeye dokunulmaz
DUZELT_SPLIT_MIN = 7          # kaynaşma adayı en az bu kadar harf
DUZELT_SPLIT_MIN_PARCA = 4    # split parçasının en küçük uzunluğu
DUZELT_GENIS_MAX = 14         # bu uzunluğun üzerinde harf-ekle/değiştir yok
FREQ_MIN = 50                 # onarım adayının en küçük sözlük frekansı
DOMINANS = 10                 # kazanan adayın ikinciye karşı frekans egemenliği
MIN_OVERLAP_RATIO = 0.5       # vtt-qa ile aynı blok hizalama eşiği

_TR_ALFABE = "abcçdefgğhıijklmnoöprsştuüvyz"
_DIAC_ALTS = {"s": "ş", "ş": "s", "c": "ç", "ç": "c", "g": "ğ", "ğ": "g",
              "ı": "i", "i": "ı"}
_TR_LOW = str.maketrans("Iİ", "ıi")
_KELIME_RE = re.compile(r"^([^A-Za-zçğıöşüÇĞİÖŞÜ0-9]*)([A-Za-zçğıöşüÇĞİÖŞÜ]+)"
                        r"([^A-Za-zçğıöşüÇĞİÖŞÜ0-9]*)$")
_HARF_RE = re.compile(r"[A-Za-zçğıöşüÇĞİÖŞÜ]")
_TS_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*$")

_FREK = {"sozluk": None, "kaynak": None}


# ------------------------------------------------------- yardımcılar -------
def _tr_lower(s):
    return s.translate(_TR_LOW).lower()


def _cekirdek(tok):
    """Kenar noktalaması soyulmuş kelime çekirdeği; içinde harf-dışı karakter
    kalan (çok kelimeli / sayılı) token için None ('burada çalışmaz.' -> None,
    'buradaçalışmaz.' -> 'buradaçalışmaz')."""
    m = _KELIME_RE.match(tok)
    if not m:
        return None
    on, core, son = m.groups()
    if any(_HARF_RE.match(c) for c in on + son):
        return None
    return core


def _nokta_norm(kucuk):
    """Nokta-yoksunu karşılaştırma biçimi (ş->s, ç->c, ğ->g, i->ı).
    Yalnız EŞİTLİK testinde kullanılır; sözlük anahtarı değildir."""
    return (kucuk.replace("ş", "s").replace("ç", "c").replace("ğ", "g")
            .replace("i", "ı"))


def _nokta_farki(a, b):
    """a→b saf nokta değişikliği mi? -> değişen nokta sayısı | None."""
    if len(a) != len(b):
        return None
    n = 0
    for ca, cb in zip(a, b):
        if ca == cb:
            continue
        if _DIAC_ALTS.get(ca) != cb:
            return None
        n += 1
    return n


def _diakritik_varyantlar(kucuk, max_edits=2):
    """<=max_edits nokta değişikliği varyantları (sözlük kontrolü YOK)."""
    pozlar = [i for i, c in enumerate(kucuk) if c in _DIAC_ALTS]
    if not pozlar or len(pozlar) > 12:
        return
    for k in range(1, min(max_edits, len(pozlar)) + 1):
        for konumlar in combinations(pozlar, k):
            yeni = list(kucuk)
            for p in konumlar:
                yeni[p] = _DIAC_ALTS[yeni[p]]
            yield "".join(yeni)


def _genis_varyantlar(kucuk):
    """1 ağır işlem (sil/ekle/değiştir) varyantları; küçük-harf.
    sil: her uzunluk; ekle/değiştir: <=DUZELT_GENIS_MAX (maliyet sınırı)."""
    n = len(kucuk)
    for i in range(n):
        yield kucuk[:i] + kucuk[i + 1:]
    if n > DUZELT_GENIS_MAX:
        return
    for i in range(n + 1):
        for h in _TR_ALFABE:
            yield kucuk[:i] + h + kucuk[i:]
    for i in range(n):
        for h in _TR_ALFABE:
            if h != kucuk[i]:
                yield kucuk[:i] + h + kucuk[i + 1:]


def _egemen(havuz):
    """{aday: freq} havuzundan kazanan: freq>=FREQ_MIN ve ikinciye karşı
    >=DOMINANS egemen. Yoksa None."""
    if not havuz:
        return None
    sirali = sorted(havuz.items(), key=lambda kv: -kv[1])
    if sirali[0][1] < FREQ_MIN:
        return None
    if len(sirali) > 1 and sirali[0][1] < DOMINANS * sirali[1][1]:
        return None
    return sirali[0][0]


def _ek_veto(kucuk, sozluk):
    """Son 3/4 harfi atılınca sözlükte çıkıyorsa kelime ekli bir biçimdir
    -> bölme VETO ('etmemiştik'->'etme mistik' hatası)."""
    for n in (3, 4):
        if len(kucuk) > n + 3 and kucuk[:-n] in sozluk:
            return True
    return False


def _agir_aile_havuzu(kucuk, sozluk):
    """(d) aday havuzu: kelime + <=2 diakritik + 1 ağır işlem (± diakritik)
    + aynı uzunlukta <=2 salt harf-değiştirme. {aday: freq}."""
    havuz = {}

    def ekle(aday):
        f = sozluk.get(aday)
        if f and f > havuz.get(aday, 0):
            havuz[aday] = f

    ekle(kucuk)
    for v in _diakritik_varyantlar(kucuk, 2):
        ekle(v)
    for agir in _genis_varyantlar(kucuk):
        ekle(agir)
        for v in _diakritik_varyantlar(agir, 2):
            ekle(v)
    if len(kucuk) <= DUZELT_GENIS_MAX:
        n = len(kucuk)
        for i in range(n):
            for j in range(i + 1, n):
                for h1 in _TR_ALFABE:
                    if h1 == kucuk[i]:
                        continue
                    ara = kucuk[:i] + h1 + kucuk[i + 1:]
                    for h2 in _TR_ALFABE:
                        if h2 == ara[j]:
                            continue
                        ekle(ara[:j] + h2 + ara[j + 1:])
    return havuz


# ------------------------------------------------------- sınıflandırıcı ----
def siniflandir(eski_tok, yeni_tok, sozluk):
    """Tek kelime çifti -> {eski, yeni, sinif, durum, neden, ogrenilecek}.
    sinif: diakritik / kaynakma / noktalama / harf | None (belirsiz).
    durum: ogrenildi / atlandi / belirsiz. Sadece 'ogrenilecek' çifti True
    olanların eski/yeni'si sözlüğe yazılır."""
    r = {"eski": eski_tok, "yeni": yeni_tok, "sinif": None, "durum": None,
         "neden": "", "ogrenilecek": False}

    if " " in eski_tok:
        r["durum"] = "atlandi"
        r["neden"] = "birleşme (ters kaynakma) öğrenilmez"
        return r

    # (b) kaynaşma ayırma: yeni TAM iki kelime
    y_kelimeler = yeni_tok.split()
    if len(y_kelimeler) >= 2:
        r["sinif"] = "kaynakma"
        if len(y_kelimeler) != 2:
            r["durum"] = "atlandi"
            r["neden"] = f"{len(y_kelimeler)} parça — yalnız 2'li ayırma öğrenilir"
            return r
        e_core = _cekirdek(eski_tok)
        p1, p2 = _cekirdek(y_kelimeler[0]), _cekirdek(y_kelimeler[1])
        if e_core is None or p1 is None or p2 is None:
            r["durum"] = "atlandi"
            r["neden"] = "parça çekirdeği çözülemedi"
            return r
        e_lo, p1_lo, p2_lo = _tr_lower(e_core), _tr_lower(p1), _tr_lower(p2)
        if len(e_lo) < DUZELT_SPLIT_MIN:
            r["durum"] = "atlandi"
            r["neden"] = f"birleşik kelime < {DUZELT_SPLIT_MIN} harf"
        elif len(p1_lo) < DUZELT_SPLIT_MIN_PARCA or \
                len(p2_lo) < DUZELT_SPLIT_MIN_PARCA:
            r["durum"] = "atlandi"
            r["neden"] = (f"parça < {DUZELT_SPLIT_MIN_PARCA} harf")
        elif _ek_veto(e_lo, sozluk):
            r["durum"] = "atlandi"
            r["neden"] = "ek-veto: kelime ekli bir biçim, bölünmez"
        elif _nokta_norm(p1_lo) + _nokta_norm(p2_lo) != _nokta_norm(e_lo):
            r["durum"] = "atlandi"
            r["neden"] = "parçalar birleşimi eski kelimeye eşit değil"
        elif sozluk.get(p1_lo, 0) < FREQ_MIN or sozluk.get(p2_lo, 0) < FREQ_MIN:
            r["durum"] = "atlandi"
            r["neden"] = (f"parça frekansı < {FREQ_MIN} "
                          f"({sozluk.get(p1_lo, 0)}, {sozluk.get(p2_lo, 0)})")
        else:
            r["eski"] = e_core
            r["yeni"] = f"{p1} {p2}"
            r["durum"] = "ogrenildi"
            r["ogrenilecek"] = True
            r["neden"] = (f"parçalar sözlükte ve tek çözüm "
                          f"({sozluk.get(p1_lo)}, {sozluk.get(p2_lo)})")
        return r

    e_core, y_core = _cekirdek(eski_tok), _cekirdek(yeni_tok)
    if e_core is None or y_core is None:
        r["durum"] = "atlandi"
        r["neden"] = "kelime çekirdeği yok (sayı/işaret/çok kelimeli)"
        return r
    if len(e_core) < DUZELT_MIN_LEN or len(y_core) < DUZELT_MIN_LEN:
        r["durum"] = "atlandi"
        r["neden"] = f"çekirdek < {DUZELT_MIN_LEN} harf"
        return r

    e_lo, y_lo = _tr_lower(e_core), _tr_lower(y_core)
    if e_lo == y_lo:
        if e_core != y_core:
            r["durum"] = "atlandi"
            r["neden"] = "yalnız büyük/küçük harf farkı"
            return r
        # (c) noktalama: çekirdek aynı, yalnız kenar noktalaması farklı
        r["sinif"] = "noktalama"
        if eski_tok in yeni_tok or yeni_tok in eski_tok:
            r["durum"] = "atlandi"
            r["neden"] = ("alt-dizi tuzağı ('..'->'...' gibi her uygulamada "
                          "büyür; post-fix ellipsis'i zaten onarır)")
        else:
            r["eski"], r["yeni"] = eski_tok, yeni_tok
            r["durum"] = "ogrenildi"
            r["ogrenilecek"] = True
            r["neden"] = "çekirdek aynı, yalnız noktalama farkı"
        return r

    # (a) diakritik: saf nokta farkı
    nd = _nokta_farki(e_lo, y_lo)
    if nd is not None:
        r["sinif"] = "diakritik"
        if nd > 2:
            r["durum"] = "atlandi"
            r["neden"] = f"{nd} nokta değişikliği (sınır 2)"
            return r
        havuz = {}
        if e_lo in sozluk:
            havuz[e_lo] = sozluk[e_lo]
        for v in _diakritik_varyantlar(e_lo, 2):
            f = sozluk.get(v)
            if f and f > havuz.get(v, 0):
                havuz[v] = f
        if y_lo not in havuz:
            r["durum"] = "atlandi"
            r["neden"] = "hedef sözlükte yok"
        elif _egemen(havuz) == y_lo:
            r["eski"], r["yeni"] = e_core, y_core
            r["durum"] = "ogrenildi"
            r["ogrenilecek"] = True
            r["neden"] = f"tek egemen aday (freq {sozluk.get(y_lo)})"
        else:
            r["durum"] = "atlandi"
            r["neden"] = "çok aday / hedef egemen değil (iki güçlü kelime)"
        return r

    # (d) harf düzeltmesi: 1 ağır (± diakritik) ya da <=2 salt değiştirme
    if len(e_lo) <= DUZELT_GENIS_MAX:
        havuz = _agir_aile_havuzu(e_lo, sozluk)
        if y_lo in havuz:
            r["sinif"] = "harf"
            if _egemen(havuz) == y_lo:
                r["eski"], r["yeni"] = e_core, y_core
                r["durum"] = "ogrenildi"
                r["ogrenilecek"] = True
                r["neden"] = f"tek egemen aday (freq {sozluk.get(y_lo)})"
            else:
                r["durum"] = "atlandi"
                r["neden"] = "çok aday / hedef egemen değil (iki güçlü kelime)"
            return r

    r["durum"] = "belirsiz"
    r["neden"] = "serbest yeniden yazım (güvenli sınıf değil)"
    return r


# --------------------------------------------------- kelime çifti çıkarımı -
def _norm_tok(tok):
    c = _cekirdek(tok)
    return _nokta_norm(_tr_lower(c)) if c else _nokta_norm(_tr_lower(tok))


def _eslestir(ea, yb):
    """replace-koşusundaki token'ları eşleştir: 1-1 norm-eşitliği önce,
    sonra ayırma (1 eski -> ardışık yeni pencerisi), sonra birleşme; kalan
    1-1 fallback. Artanlar çiftsiz bırakılır."""
    out = []
    i = j = 0
    while i < len(ea) and j < len(yb):
        e, y = ea[i], yb[j]
        ne, ny = _norm_tok(e), _norm_tok(y)
        if ne == ny:
            out.append((e, y, "1-1"))
            i += 1
            j += 1
            continue
        bulundu = None                       # ayırma: eski[i] == yeni[j:k]
        for k in range(j + 1, min(j + 4, len(yb) + 1)):
            if "".join(_norm_tok(t) for t in yb[j:k]) == ne:
                bulundu = k
                break
        if bulundu:
            out.append((e, " ".join(yb[j:bulundu]), "ayrisma"))
            i += 1
            j = bulundu
            continue
        bulundu = None                       # birleşme: eski[i:m] == yeni[j]
        for m in range(i + 1, min(i + 4, len(ea) + 1)):
            if "".join(_norm_tok(t) for t in ea[i:m]) == ny:
                bulundu = m
                break
        if bulundu:
            out.append((" ".join(ea[i:bulundu]), y, "birlesme"))
            i = bulundu
            j += 1
            continue
        out.append((e, y, "1-1"))
        i += 1
        j += 1
    return out


def kelime_ciftleri(eski_metin, yeni_metin):
    """Blok metin farkından kelime çiftleri: [(eski_tok, yeni_tok, iliski)].
    iliski: 1-1 / ayrisma / birlesme. Özdeş token çiftleri elenir."""
    a = eski_metin.split()
    b = yeni_metin.split()
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ciftler = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            for e, y, il in _eslestir(a[i1:i2], b[j1:j2]):
                if e != y:
                    ciftler.append((e, y, il))
    return ciftler


# ------------------------------------------------------------- sözlük ------
def frekans_sozluk_yukle():
    """turkce-sozluk.txt ('kelime frekans' satırları) tembel tekil yükler.
    Yoksa boş sözlük: (a)/(b)/(d) doğrulanamaz -> hiçbiri öğrenilmez,
    yalnız (c) noktalama çalışır. Güvenli düşüş."""
    if _FREK["sozluk"] is not None:
        return _FREK["sozluk"], _FREK["kaynak"]
    yol = Path(__file__).resolve().parent / "turkce-sozluk.txt"
    soz = {}
    try:
        for satir in yol.read_text(encoding="utf-8").splitlines():
            p = satir.split()
            if len(p) == 2 and p[0] not in soz:
                try:
                    soz[p[0]] = int(p[1])
                except ValueError:
                    soz[p[0]] = 1
        _FREK["kaynak"] = f"{yol.name} ({len(soz)} kelime)"
    except OSError as e:
        _FREK["kaynak"] = f"YOK ({yol.name} okunamadı: {e}) — yalnız (c) çalışır"
    _FREK["sozluk"] = soz
    return soz, _FREK["kaynak"]


def sozluge_ekle(yol, ogrenilecek, yaz=True):
    """ogrenilecek kayıtları kullanici-sozlugu.txt'e ekler (eski<TAB>yeni).
    İLK KALIR kuralı: eski farklı yeniyle varsa eklenmez, çatışma loglanır;
    aynı eski+yeni varsa 'zaten_var'. Döner: (eklenen, zaten, catisma,
    satir_sayisi). yaz=False -> kuru simülasyon, dosyaya dokunulmaz."""
    yol = Path(yol)
    satirlar = []
    if yol.exists():
        satirlar = yol.read_text(encoding="utf-8-sig",
                                 errors="replace").splitlines()
    etkili = {}
    for ln in satirlar:
        if "\t" in ln:
            e, _, y = ln.partition("\t")
            e, y = e.strip(), y.strip()
            if e and y and e != y:
                etkili[e] = y                # hardsub2srt yükleyicisi gibi: son kazanır
    eklenen, zaten, catisma = [], [], []
    gorulen = dict(etkili)
    for r in ogrenilecek:
        e, y = r["eski"], r["yeni"]
        if "\t" in e or "\t" in y or "\n" in e or "\n" in y or not e or not y:
            r["sozluk"] = "atlandi"
            r["neden"] = (r["neden"] +
                          " — sözlük satırı sekme/satır sonu içeremez").strip(" —")
            continue
        if e in gorulen:
            if gorulen[e] == y:
                r["sozluk"] = "zaten_var"
                zaten.append(r)
            else:
                r["sozluk"] = "catisma"
                r["mevcut"] = gorulen[e]
                catisma.append(r)            # İLK kalır
        else:
            gorulen[e] = y
            satirlar.append(f"{e}\t{y}")
            r["sozluk"] = "eklendi"
            eklenen.append(r)
    temiz = [ln for ln in satirlar if ln.strip()]
    if yaz and eklenen:
        yol.write_text("\n".join(temiz) + "\n", encoding="utf-8")
    return eklenen, zaten, catisma, len(temiz)


def ciftleri_ogren(ciftler, sozluk_yol, yaz=True, kaynak="cli"):
    """[(eski, yeni, iliski)] -> sınıflandır + sözlüğe yaz (yaz=True).
    Döner: (kayitlar, ozet). Kayıtlar JSON-uyumludur (rapor + UI günlüğü)."""
    soz, frek_kaynak = frekans_sozluk_yukle()
    kayitlar = []
    for e, y, il in ciftler:
        r = siniflandir(e, y, soz)
        r["iliski"] = il
        kayitlar.append(r)
    ogrenilecek = [r for r in kayitlar if r["ogrenilecek"]]
    sozluge_ekle(sozluk_yol, ogrenilecek, yaz=yaz)
    ozet = {
        "ogrenildi": sum(1 for r in kayitlar if r.get("sozluk") == "eklendi"),
        "belirsiz": sum(1 for r in kayitlar if r["durum"] == "belirsiz"),
        "atlandi": sum(1 for r in kayitlar if r["durum"] == "atlandi"),
        "catisma": sum(1 for r in kayitlar if r.get("sozluk") == "catisma"),
        "zaten_var": sum(1 for r in kayitlar if r.get("sozluk") == "zaten_var"),
        "toplam_cift": len(kayitlar),
        "frekans_sozluk": frek_kaynak,
    }
    return kayitlar, ozet


# --------------------------------------------------------- SRT + hizalama --
def _ts_ms(s):
    m = _TS_RE.match(str(s or "").strip())
    if not m:
        return None
    h, mi, se, ms = m.groups()
    if int(mi) > 59 or int(se) > 59:
        return None
    return ((int(h or 0) * 60 + int(mi)) * 60 + int(se)) * 1000 + \
        int((ms + "000")[:3])


def srt_parse(metin):
    """SRT metni -> [{'start': ms, 'end': ms, 'metin': ...}] (dosya sırası)."""
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
        st = _ts_ms(sol.split()[-1] if sol.split() else "")
        en = _ts_ms(sag.split()[0] if sag.split() else "")
        mtn = "\n".join(satirlar[ti + 1:]).strip()
        if st is None or en is None or not mtn:
            continue
        bloklar.append({"start": st, "end": en, "metin": mtn})
    return bloklar


def hizala(auto_blok, duz_blok):
    """vtt-qa.py mantığı: en büyük örtüşme önce kazanır, bire bir eşleme,
    örtüşme/süre >= MIN_OVERLAP_RATIO. Döner: (çiftler, kullanılan_auto,
    kullanılan_duz) — çift (auto_blok, duz_blok, örtüşme_ms)."""
    cands = []
    for si, s in enumerate(auto_blok):
        dur = s["end"] - s["start"]
        if dur <= 0:
            continue
        for vi, v in enumerate(duz_blok):
            ov = min(s["end"], v["end"]) - max(s["start"], v["start"])
            if ov > 0 and ov / dur >= MIN_OVERLAP_RATIO:
                cands.append((ov, si, vi))
    cands.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_s, used_v, pairs = set(), set(), []
    for ov, si, vi in cands:
        if si in used_s or vi in used_v:
            continue
        used_s.add(si)
        used_v.add(vi)
        pairs.append((si, auto_blok[si], duz_blok[vi], ov))
    pairs.sort(key=lambda p: p[0])
    return [(sb, db, ov) for _si, sb, db, ov in pairs], used_s, used_v


def _norm_beyaz(s):
    return re.sub(r"\s+", " ", s or "").strip()


def fark_ciftleri(auto_yol, duz_yol):
    """İki SRT'yi hizala, değişen bloklardan kelime çiftlerini topla.
    Döner: (ciftler, bilgi) — bilgi hizalama sayaçları."""
    auto = srt_parse(Path(auto_yol).read_text(encoding="utf-8-sig",
                                              errors="replace"))
    duz = srt_parse(Path(duz_yol).read_text(encoding="utf-8-sig",
                                            errors="replace"))
    ciftler = []
    eslesen = degisen = 0
    for sb, db, _ov in hizala(auto, duz)[0]:
        eslesen += 1
        if _norm_beyaz(sb["metin"]) == _norm_beyaz(db["metin"]):
            continue
        degisen += 1
        ciftler.extend(kelime_ciftleri(sb["metin"], db["metin"]))
    bilgi = {"auto_blok": len(auto), "duz_blok": len(duz),
             "eslesen_blok": eslesen,
             "eslesmeyen_auto": len(auto) - eslesen,
             "eslesmeyen_duz": len(duz) - eslesen,
             "degisen_blok": degisen}
    return ciftler, bilgi


# ---------------------------------------------------------------- ana ------
def kos(auto_yol, duz_yol, sozluk_yol, uygula=False, cikti=None,
        kaynak="cli", ekstra=None):
    """Tam öğrenme turu: hizala -> çift çıkar -> sınıflandır -> (yaz).
    Döner: (rapor_dict, cikti_satirlari)."""
    ciftler, bilgi = fark_ciftleri(auto_yol, duz_yol)
    kayitlar, ozet = ciftleri_ogren(ciftler, sozluk_yol, yaz=uygula,
                                    kaynak=kaynak)
    rapor = {
        "arac": "ogren.py", "surum": SURUM, "kaynak": kaynak,
        "mod": "uygula" if uygula else "kuru",
        "zaman": datetime.now().isoformat(timespec="seconds"),
        "auto": str(auto_yol), "duzeltilmis": str(duz_yol),
        "sozluk": str(sozluk_yol),
        "hizalama": {**bilgi, "min_overlap_ratio": MIN_OVERLAP_RATIO},
        "ciftler": kayitlar, "ozet": ozet,
    }
    if ekstra:
        rapor.update(ekstra)

    satirlar = []
    mod_etiket = "UYGULA" if uygula else "KURU (yalnız rapor)"
    satirlar.append(f"=== ogren.py — SRT farkından güvenli sözlük öğrenimi "
                    f"[{mod_etiket}] ===")
    satirlar.append(f"auto        : {auto_yol}  ({bilgi['auto_blok']} blok)")
    satirlar.append(f"düzeltilmiş : {duz_yol}  ({bilgi['duz_blok']} blok)")
    satirlar.append(f"eşleşen blok: {bilgi['eslesen_blok']} "
                    f"(değişen {bilgi['degisen_blok']}; hizalamada olmayan "
                    f"auto {bilgi['eslesmeyen_auto']}, düz {bilgi['eslesmeyen_duz']})")
    satirlar.append(f"frekans sözlüğü: {ozet['frekans_sozluk']}")
    satirlar.append("")
    satirlar.append(f"{'eski':<24} -> {'yeni':<24} {'ilişki':<9} "
                    f"{'sınıf':<11} {'durum':<10} not")
    for r in kayitlar:
        sonuc = r.get("sozluk") or r["durum"]
        if not uygula and sonuc == "eklendi":
            sonuc = "ogrenilecek"          # kuru mod: yazılmadı, yazılacak
        satirlar.append(f"{r['eski'][:24]:<24} -> {r['yeni'][:24]:<24} "
                        f"{r['iliski']:<9} {r['sinif'] or '-':<11} "
                        f"{sonuc:<10} {r['neden']}")
    satirlar.append("")
    satirlar.append(f"Özet: {ozet['ogrenildi']} öğrenildi · {ozet['belirsiz']} "
                    f"belirsiz · {ozet['atlandi']} atlandı · "
                    f"{ozet['catisma']} çatışma · {ozet['zaten_var']} zaten var")
    if not uygula:
        satirlar.append("KURU MOD — sözlüğe yazılmadı (--uygula ile yazılır).")
    else:
        satirlar.append(f"Sözlük: {sozluk_yol} ({ozet['ogrenildi']} satır eklendi)")

    if cikti:
        Path(cikti).write_text(json.dumps(rapor, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        satirlar.append(f"Rapor yazıldı: {cikti}")
    return rapor, satirlar


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="auto SRT ile kullanıcı düzeltmesinden güvenli kelime "
                    "çifti öğren (varsayılan KURU mod: yalnız rapor).",
        epilog='Örnek: py -3 ogren.py "auto.srt" "duzeltilmis.srt" --uygula')
    ap.add_argument("auto_srt", help="araç çıktısı (OCR) SRT")
    ap.add_argument("duzeltilmis_srt", help="kullanıcının düzelttiği SRT")
    ap.add_argument("--uygula", action="store_true",
                    help="öğrenilen çiftleri kullanici-sozlugu.txt'e yaz "
                         "(verilmezse yalnız rapor)")
    ap.add_argument("-o", "--output", help="JSON rapor yolu")
    ap.add_argument("--sozluk", default=None,
                    help="hedef sözlük (varsayılan: kullanici-sozlugu.txt, "
                         "bu dosyanın yanında)")
    args = ap.parse_args(argv)

    auto_y = Path(args.auto_srt)
    duz_y = Path(args.duzeltilmis_srt)
    for p, ad in ((auto_y, "auto SRT"), (duz_y, "düzeltilmiş SRT")):
        if not p.is_file():
            print(f"[HATA] {ad} bulunamadı: {p}")
            return 2
    sozluk_yol = Path(args.sozluk) if args.sozluk else \
        Path(__file__).resolve().parent / "kullanici-sozlugu.txt"

    rapor, satirlar = kos(auto_y, duz_y, sozluk_yol, uygula=args.uygula,
                          cikti=args.output)
    print("\n".join(satirlar))
    return 0


if __name__ == "__main__":
    sys.exit(main())
