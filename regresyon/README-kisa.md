# hardsub2srt regresyon seti — kısa kullanım

Sabit 12 kare üzerinde OCR kıyası. **hardsub2srt.py'a dokunmaz** — yalnız
import eder (import bozuksa temiz alt süreçte mikro koşumla kendini dener).

## Kod değişikliğinden sonra (3 satır)

```sh
py -3 regresyon.py --cikti yeni.json            # 1) değişiklik sonrası koşum (CPU, ~2-4 dk)
py -3 regresyon.py --karsilastir yeni.json      # 2) baseline ile kıyas
                                                # 3) SONUC satırına bak: KÖTÜLEŞME ŞÜPHESİ -> elle bak
```

## Bir kez kurulum (set değişirse)

```sh
py -3 regresyon.py --kare-cikar     # ffmpeg ile kareler/*.png (video glob'u noktalar.json'da)
py -3 regresyon.py --bant-yenile    # detect_style bantlarını noktalar.json'a dondur
py -3 regresyon.py                  # baseline.json üret
```

## Dosyalar

| Dosya | Ne |
|---|---|
| `noktalar.json` | 12 nokta: bölüm + zaman + neden + dondurulmuş bant koordinatı |
| `kareler/e<NNNN>_t<sn>.png` | sabit girdi kareleri (tam kare; video değil PNG → video değişse bile kıyas geçerli kalır, _op-rapor.md §c.1 dersi) |
| `baseline.json` | aracın bugünkü OCR çıktısı (sürüm + tarih + conf kayıtlı) |
| `regresyon.py` | koşum + kıyas betiği |

## Kıyas mantığı

Nokta başına normalize edilmiş metin Levenshtein'i: `CER = edit distance / max(uzunluk)`.
Rapor: nokta tablosu + ortalama CER + en kötü 3 nokta.

- Tüm noktalar birebir aynıysa → `DEĞİŞİKLİK YOK` (çıkış 0).
- Ortalama CER > 0.02 veya tek nokta > 0.15 (metin ≥ 10 kr) → `KÖTÜLEŞME ŞÜPHESİ` (çıkış 1).
- Eşikler: `--tol-ort`, `--tol-tek`.

## Notlar

- Varsayılan OCR **CPU**'dur (`--gpu` ile değiştir) — GPU arka plan toplu koşumda
  meşgulken baseline bozulmasın; baseline ile kıyas aynı cihazda koşulmalı.
- OCR zinciri aracın kendisiyle aynı: 3 varyant konsensus (`OCR_VARIANTS`) +
  conf<0.75'te ikinci motor oyu (`get_second_engine`) + `_consensus_pick`;
  tophat maskesinde ham bant, thr'de `binarize_white`; küçük fontta 2x LANCZOS.
- Bant koordinatları `--bant-yenile` ile aracın `detect_style` +
  `_dar_bant_genislet` zincirinden bir kez ölçülüp **dondurulur**; kıyas
  koşumları aynı koordinatı kullanır (OCR katmanı izole). Bant algılama
  katmanını da denetlemek istersen `--bant-yenile` çıktısını eskiyle kıyasla.
- `scan_band` (video taraması) bu setin kapsamı dışındadır — tek kare, OCR odaklı.
- ⛔ **Telif sınırı:** `kareler/*.png` dosyaları anime karesi içerir — **repoya (GitHub) konmaz**,
  yalnız yerel kalır. Repoya gidenler: `regresyon.py`, `noktalar.json`, `baseline.json`,
  `README-kisa.md`. Kareleri sete yeni katan, kendi ffmpeg kopyasını `--kare-cikar` ile üretir.
