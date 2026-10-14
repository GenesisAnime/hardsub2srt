# Katkı kuralları

## Dil ve dokümantasyon

- Kullanıcıya yönelik yeni veya güncellenen dokümantasyonu doğal Türkçe yazın. Yerleşik teknik terimleri İngilizce kullanın: OCR, CLI, GPU, branch, commit, pull request, CI.
- `README.md` Türkçe ana giriş, `README.en.md` İngilizce giriştir. Mevcut İngilizce teknik dokümanları çevirmeden bırakın; yeni doküman haritasında dilini ve belgenin kullanım kılavuzu mu, tasarım notu mu olduğunu belirtin.
- Uygulanmış davranış, ölçülmüş sonuç, plan ve bloklu işi ayrı ifade edin. Kod, test veya CI kanıtlamıyorsa destek/performans/doğruluk iddiası yazmayın.
- Komutları ve seçenekleri güncel kaynakla karşılaştırın. Path, kullanıcı adı, medya adı veya makineye özgü bilgiyi örneğe koymayın; yer tutucu ve sentetik veri kullanın.

## Gizlilik ve telif

Bu depoya şunları eklemeyin:

- Video, anime karesi, QA ekran görüntüsü veya telifli başka medya.
- Ham `.srt`/`.vtt`, replikler, cümle düzeyinde OCR örneği veya kişisel altyazı dosyası.
- Kullanıcı adı, gerçek mutlak yerel yol, özel hostname, token, parola, API key veya bunlardan türetilmiş tanımlayıcı hash.

OCR çıktılarını veya koşum yan ürünlerini issue/PR ekine koymayın. İstatistik paylaşımı gerekiyorsa alanları kendiniz inceleyin ve yalnız gerekli, gizlilikten arındırılmış özetleri paylaşın. AI OCR/çeviri yanıtı öneridir; insan kararının yerine geçmez ve sağlayıcıya elle gönderim uygulama tarafından yapılmaz.

## Geliştirme ve doğrulama

Yerel testler, GitHub Actions ile aynı hafif bağımlılık kümesini kullanır; OCR motorları ve model ağırlıkları test için kurulmaz. Temiz bir Python test ortamında:

```powershell
py -3 -m pip install --upgrade pip
py -3 -m pip install pytest flask numpy opencv-python-headless
py -3 -m pytest -q -rs
```

CI, Python 3.14 ile Ubuntu ve Windows’ta `python -m pytest -q -rs` çalıştırır. Testler sentetik/runtime davranışını denetler; gerçek video OCR kalitesini veya GPU inference’ını ölçmez. CI, OCR runtime bağımlılıklarını kurmaz; ayrıca `opencv-python-headless` kullanır. Python dosyası değiştiyse `py -3 -m py_compile <dosya>` ek sözdizimi kontrolü olarak kullanılabilir; testlerin yerine geçmez. OCR davranışı değişiyorsa ilgili testlere ek olarak yetkili video ve trusted zaman hizalı referansla ölçüm gerektiğini, bu ölçüm yoksa açıkça belirtin.

## Sözlük, lisans ve commit mesajı

`kullanici-sozlugu.txt` yalnız kelime düzeyindeki düzeltmeleri içermelidir. Replik veya bağlamlı altyazı eklemeyin; her kuralın neden genel ve güvenli olduğunu pull request içinde açıklayın.

Kod MIT lisanslıdır. OCR kütüphaneleri/model ağırlıkları, PyTorch/CUDA, FFmpeg ve diğer üçüncü taraf bileşenlerin lisans/dağıtım koşulları ayrıdır. Model ağırlıklarını depoya eklemeyin.

Commit başlığını ve varsa gövdesini kısa Türkçe yazın; yaygın teknik jargon İngilizce kalabilir. PowerShell’de Türkçe karakter içeren commit mesajını `-m` ile vermeyin. Mesajı UTF-8 dosyaya kaydedip `git commit -F <dosya>` kullanın.
