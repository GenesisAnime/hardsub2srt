# Katkı kuralları (CONTRIBUTING)

## Kırmızı çizgi: telif

Bu repo telifli içerik dağıtmaz. Aşağıdakiler **asla** gönderilmez
(Issue, PR, ekran görüntüsü — hiçbir yolla):

1. Altyazı metni (SRT çıktısı, cümle/replik düzeyi düzeltme örnekleri)
2. Tam video kareleri / anime görüntüsü
3. Video dosyaları veya video yolları

## Gönderilebilen veri (telif sorunu olmayan)

| Veri | Nasıl |
|---|---|
| **Öğrenme paketi** | Programın "paket dışa aktar" çıktısı: kelime-düzeyi sözlük girişleri + OCR hata istatistikleri + karışım matrisi. Issue'ya ek olarak → şablon: `ogrenme-paketi` |
| **Sözlük PR'ı** | Teknik kullanıcılar `kullanici-sozlugu.txt`'ye satır ekleyip PR açabilir (yanlış→doğru kelime; cümle değil) |
| **Hata raporu** | Koşum istatistikleri (stats.json içeriği — metin değil), araç sürümü, `--cpu/--gpu` |

Zor kare paylaşımı (kırpılmış altyazı-bant PNG) yalnız geliştiricinin özel
talebiyle ve açık onayla olur — varsayılan olarak kapalıdır.

## Kod PR'ı

1. `py -3 -m py_compile <değişen dosya>` geçmeli.
2. **Regresyon seti zorunlu:**
   ```sh
   py -3 regresyon/regresyon.py --cikti yeni.json
   py -3 regresyon/regresyon.py --karsilastir yeni.json
   ```
   `KÖTÜLEŞME ŞÜPHESİ` çıktısı alınan PR alınmaz. (Kareler repoda yoktur;
   `--kare-cikar` ile kendi kopyanı üretirsin — videolar sende olmak zorunda,
   bu nedenle regresyon koşumu bakım-içidir, PR ön koşulu değil.)
3. Değişiklik ölçümle açıklanmalı: "daha iyi" iddiası GT-CER veya low-conf
   oranı düşüşüyle desteklenmelidir. İki denemenin aynı sonucu vermesi,
   testin ayırt edici olmadığını gösterir.
4. OCR davranışını değiştiren her değişiklik stats'a alan yazar
   (sessiz davranış değişikliği yok).

## Öğrenme paketinin işlenmesi

Paketler oylamalı birleştiriciyle işlenir: ≥2 bağımsız kullanıcı aynı düzeltmeyi
verdiyse otomatik kabul; tek oy "aday" havuzuna düşer, bakımcı onaylar. Onaylanan
girişler bir sonraki sürümdeki `kullanici-sozlugu.txt`'ye gider; her sürümün
regresyon CER trendi README'de yayınlanır.

## Davranış

Hata raporunda araç sürümünü, stats.json içeriğini ve ne gördüğünü yaz.
Tartışmada tahminden çok ölçüme bakalım.
