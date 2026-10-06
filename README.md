# hardsub2srt

> Hardsub (videoya gömülü) altyazıyı OCR ile Türkçe `.srt` dosyasına çevirir —
> iki motorlu OCR, Türkçe düzeltme katmanı, otomatik öğrenme döngüsü ve kalite
> güvence araçlarıyla.

**Türkçe** · [English](README.en.md)

---

## Ne yapar?

Gömülü altyazılı (hardsub) bir video alır, altyazı bandını otomatik bulur,
kare kare OCR'lar ve zamanlanmış Türkçe `.srt` üretir. Oynatıcı uyumluluğu için
SRT dosyasına hiçbir özel işaret girmez; eşleşme bilgisi yan dosyaya yazılır.

| Katman | Açıklama |
|---|---|
| **Bant algılama** | Piksel-ölçümlü otomatik altyazı bandı; dar bantları genişletir, beyaz-fon videolarda koruma |
| **İki motorlu OCR** | Ana motor (EasyOCR) + PaddleOCR (PP-OCRv6) ikinci motor: düşük güvenli bloklarda oy ve takas |
| **Düzeltme** | Türkçe diakritik, harf-yutma, apostrof tablosu, kullanıcı sözlüğü (`kullanici-sozlugu.txt`) |
| **Öğrenme** | `ogren.py` + arayüzde "Kaydet = öğren" — her düzeltme gelecekteki koşumları iyileştirir |
| **Kalite güvence** | QA montaj kareleri, gürültü ayrımı (`_ekran.srt`), blok istatistikleri, video-hash meta (`.hardsub2srt.json`) |
| **Ölçüm** | `vtt-qa.py` GT kıyası + 12 noktalık regresyon seti (`regresyon/`) |

## Hızlı başlangıç

```sh
py -3 hardsub2srt.py "dizi.mp4"          # dizi.srt üretir
py -3 hardsub2srt.py "dizi.mp4" --cpu    # GPU'suz makinede
py -3 ui_server.py                       # arayüz: http://127.0.0.1:8765
toplu.bat                                # klasördeki tüm videolar (Windows, sürükle-bırak)
```

Koşum sonunda ürettiği dosyalar: `dizi.srt` + `dizi.stats.json` (istatistik) +
`dizi.hardsub2srt.json` (video-hash, parametreler — video-SRT eşleşme kilidi).

## Ölçüm

Doğruluk iddiası GT (doğru altyazı) kıyasıyla ölçülür, tahminle söylenmez:

| Test | Sonuç |
|---|---|
| E02 GT kıyası (vtt-qa) | CER %0,48, recall %100 |
| BLEND-S (stilize font) | CER %1,49 |
| Regresyon seti (12 nokta) | `py -3 regresyon.py --karsilastir <yeni.json>` — kötüleşme eşiği geçilirse çıkış 1 |

## Topluluk öğrenme döngüsü (yapım aşaması)

Düşüncem şu: kullanıcıların düzeltmeleri altyazı metni toplanmadan geri aksın.
Program anonim bir "öğrenme paketi" üretir, kullanıcı bunu GitHub'da bir
Issue'ya ekler, paketler oylamayla birleştirilip sözlüğe işlenir ve yeni
sürümle herkese dağılır. Nasıl çalışacağı
[TOPLULUK-OGRENME-TASARIMI.md](TOPLULUK-OGRENME-TASARIMI.md)'de yazıyor.

Bu repoya altyazı metni ya da anime karesi yüklenmiyor; paylaşılacak şey
yalnız istatistik, kelime düzeyi sözlük girişleri ve ölçüm verileri.
Kurallar [CONTRIBUTING.md](CONTRIBUTING.md)'de.

## Dosya haritası

| Dosya | Ne |
|---|---|
| `hardsub2srt.py` | Çıkarım motoru (CLI) |
| `ui_server.py` | Flask arayüz: koşum, düzeltme, öğrenme, kalite panosu |
| `ogren.py` | Otomatik öğrenme CLI (güvenli çift sınıflandırıcı) |
| `vtt-qa.py` | GT/VTT kıyas ölçümü |
| `toplu.bat` | Toplu koşum + done-listesi (koşulmuşları atlar, `--yeni` ile bypass) |
| `senkron-vault.ps1` | Geliştirme kopyası senkronu (dahili) |
| `regresyon/` | 12 nokta OCR regresyon seti + kıyas betiği |
| `docs/` | Gelişim günlüğü, karar kayıtları ve öğrenmeler |
| `kullanici-sozlugu.txt` | Kelime-düzeyi düzeltme sözlüğü (toplulukla büyür) |

## Dokümantasyon

Projenin nasıl geliştiğini ve neden böyle inşa edildiğini merak ederseniz:

- [Gelişim günlüğü](docs/GELISTIRME-GUNLUGU.md) — her adım ne zaman, ne için
  ve hangi ölçümle atıldı
- [Karar kayıtları](docs/NASIL-VE-NEDEN.md) — telif duvarından düzeltme
  kurallarına, 10 önemli kararın gerekçesi
- [Öğrenmeler](docs/OGRENMELER.md) — geliştirme sırasında yakalanan tuzaklar
  ve dersler

## Yol haritası

1. Öğrenme paketi dışa aktarma + `birlestir.py` (oylamalı birleştirme)
2. Low-conf blok kurtarma turu (upscale/kontrast ikinci deneme)
3. Karışım madenciliği: font profili başına en iyi motor/parametre seçimi
4. Tek dosyalık `.exe` dağıtımı (CPU varsayılan, GPU opsiyonel)
