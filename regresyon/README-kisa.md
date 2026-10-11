# hardsub2srt OCR regresyonları — kısa kullanım

İki farklı kontrol vardır: `regresyon.py` sabit karelerdeki motor çıktısını
eski çıktıyla kıyaslar; `gt_gate.py` ürünü sabit video kesitinde yeniden
çalıştırır ve OCR metnini güvenilir referans altyazıyla ölçer. İkincisi
çeviri kalitesini değil, ekrandaki altyazının OCR transkripsiyonunu denetler.

## VTT referanslı kapı

```powershell
$videoRoot = Read-Host "BLEND-S video klasörünün yolu"
$referenceRoot = Read-Host "samples/references/BLEND-S/S01E01 klasörünün tam yolu"
py -3 regresyon/gt_gate.py `
  --video-root $videoRoot `
  --reference-root $referenceRoot `
  --report "gt-sonuc.json"
```

Case manifesti `gt_cases.json` içindedir. Şu an yalnızca BLEND-S S01E01'in
ilk 240 saniyesi kapıya bağlıdır: sabit bant (820,260), `thr`, eşik 240;
ürün aracı yeni SRT'yi geçici klasörde üretir ve `vtt-qa.py` ile aynı
hizalama/CER yöntemini kullanır. Korpus CER, hizalanmış çiftlerdeki toplam
Levenshtein uzaklığının toplam GT karakter sayısına bölümüdür. Eşik
uydurulmadı: README'deki wf-5/bf-2 ölçümünden korpus CER ≤ 0.035; taban
3 edit / 201 GT karakter = CER 0.014925, 6 hizalı çifttir. Her koşuda
baseline, güncel CER ve farkı raporlanır; kapı belgelenmiş 0.035 üst sınırına
göre karar verir. Bu BLEND-S örnek kapısının referans baseline/eşiğidir; tüm video/fontlar için genel doğruluk garantisi veya çeviri puanı değildir.

Varsayılan ürün OCR seçimi geçerlidir (CUDA varsa GPU, yoksa ürünün CPU
düşüşü). Yalnızca açıkça CPU koşmak için `--cpu` ekleyin. Eşleşmeyen video,
referans veya belirsiz birden çok video `[UNAVAILABLE]` olur ve kapı çıkış
kodu 2 ile biter; bu durum geçiş sayılmaz. OCR hatası, uyumsuz zamanlama,
kapsam eksikliği veya CER eşiği aşımı başarısızlıktır (çıkış 1).

## Sabit kare baseline kıyası

```sh
py -3 regresyon/regresyon.py --cikti yeni.json
py -3 regresyon/regresyon.py --karsilastir yeni.json
```

Bu ayrı kontrol OCR çıktısını `baseline.json` ile kıyaslar (varsayılan CPU;
GPU'yu açıkça seçmek için `--gpu`). Kareler video değil sabit PNG'dir.

### Bir kez kurulum (sabit kare seti değişirse)

`noktalar.json` içindeki `video_glob` değerleri yerel video yollarıdır. Kendi videolarınızı depoya eklemeden, örneğin `videos/` altında tutun ve globları kendi adlarınıza göre düzenleyin.

```sh
py -3 regresyon/regresyon.py --kare-cikar     # ffmpeg ile kareler/*.png (video glob'u noktalar.json'da)
py -3 regresyon/regresyon.py --bant-yenile    # detect_style bantlarını noktalar.json'a dondur
py -3 regresyon/regresyon.py                  # baseline.json üret
```

## Dosyalar

| Dosya | Ne |
|---|---|
| `noktalar.json` | 12 nokta: bölüm + zaman + neden + dondurulmuş bant koordinatı |
| `kareler/e<NNNN>_t<sn>.png` | sabit girdi kareleri (tam kare PNG; videolar değişse bile kıyas girdisi sabit kalır) |
| `baseline.json` | sabit kare setindeki OCR çıktısı (sürüm + tarih + conf kayıtlı) |
| `regresyon.py` | koşum + kıyas betiği |
| `gt_cases.json` | VTT kapısının desteklenen vakaları, dokümante tabanları ve eşikleri |
| `gt_gate.py` | videoyu yeniden OCR edip trusted VTT ile CER kapısı |

## Kıyas mantığı

Nokta başına normalize edilmiş metin Levenshtein'i: `CER = edit distance / max(uzunluk)`.
Rapor: nokta tablosu + ortalama CER + en kötü 3 nokta.

- Tüm noktalar birebir aynıysa → `DEĞİŞİKLİK YOK` (çıkış 0).
- Ortalama CER > 0.02 veya tek nokta > 0.15 (metin ≥ 10 kr) → `KÖTÜLEŞME ŞÜPHESİ` (çıkış 1).
- Eşikler: `--tol-ort`, `--tol-tek`.

## Notlar

- Bu sabit kare kıyasının varsayılan OCR'u **CPU**'dur (`--gpu` ile değiştir);
  baseline ile kıyas aynı cihazda koşulmalı. VTT kapısı ürün aracının GPU
  varsayılanını kullanır; `--cpu` açıkça istenebilir.
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
  `gt_cases.json`, `gt_gate.py`, `README-kisa.md`. Kareleri sete yeni katan,
  kendi ffmpeg kopyasını `--kare-cikar` ile üretir. Referans VTT'ler ve videolar
  yerel kalır; eksik kaynak `[UNAVAILABLE]` üretir, sessiz geçiş olmaz.

## Şu an kapsanmayan vakalar

- **86tr:** mevcut `_mx-gt-86.vtt`, güncel 86tr çıktısıyla zaman/metin olarak
  uyumlu değil (ölçülen CER 1.0311, vtt-qa zaman uyumu uyarısı); gate'e alınmadı.
- **Wano:** ölçülen bölümler için güvenilir, zaman uyumlu VTT referansı yok.
  Yeni GT dosyası sağlanınca ayrı, belgeli eşik belirlenebilir.
