# Katkı kuralları

## Gizlilik ve telif

Bu depoya şu içerikleri eklemeyin:

- Video, anime karesi, QA ekran görüntüsü veya telifli başka medya.
- Ham `.srt`/`.vtt`, replikler, cümle düzeyinde OCR örneği veya kişisel altyazı dosyası.
- Video/çıktıların mutlak yerel yolu, kullanıcı adı, tam dosya adı ya da bunlardan türetilmiş hash gibi yerel iş akışını tanımlayabilecek metadata.

OCR çıktılarını veya koşum yan ürünlerini issue/PR ekine koymayın. İstatistik paylaşımı gerekiyorsa önce alanları kendiniz gözden geçirin ve yalnız gerekli, gizlilikten arındırılmış özetleri paylaşın. Bu depoda kullanıcı koşumlarını otomatik alan telemetry API'si veya otomatik yükleme özelliği yoktur.

## Kod ve dokümantasyon katkısı

- Değişiklik amacını ve kullanıcıya etkisini açıkça yazın.
- OCR davranışını değiştiriyorsanız GT referanslı `regresyon/gt_gate.py` ölçümünü ve hangi koşum/ayarların kullanıldığını belirtin. Kaynak video ve trusted VTT depoya eklenmez; kullanılabilir değillerse bunu açıkça yazın, ölçülmemiş sonucu başarı gibi göstermeyin.
- `regresyon/README-kisa.md` içindeki iki regresyon yönteminin kapsamını birbirine karıştırmayın: sabit kare kıyası OCR bileşenini ölçer; `gt_gate.py` sabit video penceresindeki transkripsiyon metnini zaman uyumlu VTT referansına karşı ölçer.
- Değiştirilen Python dosyalarında `py -3 -m py_compile <dosya>` ile sözdizimi kontrolü yapın. Gerekiyorsa test komutlarını ve sonuçlarını PR'da raporlayın; gerçek videoya erişim yoksa OCR doğruluğu iddiası ileri sürmeyin.

## Sözlük değişiklikleri

`kullanici-sozlugu.txt` yalnız kelime düzeyindeki düzeltmeleri içermelidir. Replik, cümle veya bağlamlı altyazı metni eklemeyin. Her kuralın neden güvenli ve genel olduğunu PR açıklamasında belirtin.

## Lisans ve üçüncü taraf modeller

Kod MIT lisanslıdır. Üçüncü taraf OCR kütüphaneleri/model ağırlıkları, PyTorch/CUDA ve FFmpeg ayrı lisans ve dağıtım şartlarına tabidir. Model ağırlıklarını bu depoya eklemeyin; dağıtım veya yeni model kaynağı önermeden önce lisansını ve boyutunu belgeleyin.
