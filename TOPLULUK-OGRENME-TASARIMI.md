# Çok-kullanıcı öğrenme döngüsü — tasarım v1 (2026-10-06)

Hedef: kullanıcılar programı kendi bilgisayarlarında koşturur; telif-güvenli veri geri akar;
her sürümün doğruluğu ölçülerek yükselir. Bu doküman GitHub'a açık projenin veri mimarisidir.

## 1. Veri kategorileri (telif güvenliği)

| Kategori | İçerik | Telif durumu |
|---|---|---|
| istatistik | video-hash, parametre seti, blok sayısı, konuşma sn, low-conf oranı | temiz |
| karışım matrisi | OCR karakter karışım sayıları (rn↔n, ö-i vs.) | temiz |
| sözlük girişi | yanlış→doğru KELİME düzeyi eşleme (ogren.py günlüğünden) | temiz |
| zor kare | bölüm+sn koordinatı, kırpılmış altyazı-bant PNG (opsiyonel, açık onayla) | gri — tek kare, bant kırpılmış |

⛔ Pakete ASLA girmeyecekler: SRT metni, cümle/tam replik düzeyi düzeltmeler,
tam video kareleri, dosya yolları, kullanıcı kimliği.
GitHub repoya telifli altyazı dağıtmama kuralı CONTRIBUTING'e birebir yazılır.

## 2. Döngü akışı

1. **Üretim:** Araç her koşumda `<srt>.hardsub2srt.json` meta yazar (yapıldı, commit 317d659).
2. **Dışa aktar:** UI'da "Öğrenme paketi dışa aktar" → ogren.py öğrenme günlüğü + meta
   istatistikleri + karışım matrisi birleştirilip tek anonim `paket-<tarih>.json` üretir.
   Kullanıcı paketi yüklemeden önce içeriğin önizlemesini görür ve onaylar.
3. **Gönderim:** Kullanıcı paketi GitHub **Issue**'ya ekler (şablonlu — PR'dan çok daha kolay,
   .exe kullanıcısı için tek tık). Teknik kullanıcılar sözlük dosyasını düzenleyip **PR** da açabilir.
4. **Birleştirme:** Bakımcı `birlestir.py` koşar:
   - paketleri okur, sözlük çakışmalarını oylar (≥2 bağımsız kullanıcı aynı düzeltmeyi verdiyse
     otomatik kabul; tek oy → "aday" havuzu, insan onayı);
   - karışım matrislerini toplar → düzeltme katmanı için kural adayları çıkarır;
   - çıktı: güncel `kullanici-sozlugu.txt` + `istatistik-sureli.json` + sürüm notu.
5. **Dağıtım:** Yeni exe/sözlük sürümü yayınlanır → kullanıcılar günceller → döngü kapanır.

## 3. %100 hedefine ölçüm köprüsü

- `regresyon/` kare seti her sürümde koşar → CER trendi README'de yayınlanır (sürüm başına).
- low-conf kurtarma turu ve karışım madenciliği, kullanıcı verisi geldikçe beslenir.
- Ölçüm kuralı: "iyileşti" demek için GT-CER + low-conf oranı ikisi birden düşmeli.

## 4. .exe yol haritası (ayrı büyük iş)

- PyInstaller tek dosya; PaddleOCR **CPU** modeli varsayılan (~200-400 MB), GPU opsiyonel mod.
- Güncelleme: exe ayrı, sözlük/istatistik ayrı dosya → sözlük güncellemesi exe çıkmadan dağıtılabilir.
- İlk GitHub sürümünden önce: LICENSE (telif uyarılı), CONTRIBUTING (veri kuralı), Issue şablonları.

## 5. Kararları bekleyenler

- Repo adı ve açılış sürümü kapsamı (araç + UI + betikler mi; kurs içeriği mi).
- Zor kare paylaşımının opt-in varsayılan mı, açık onay mı olacağı (öneri: açık onay).
