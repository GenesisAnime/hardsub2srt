# Depo çalışma notları

- Kullanıcıya dönük dokümantasyonda varsayılan dil Türkçe olsun; yaygın teknik terimleri (OCR, CLI, GPU, branch, commit, pull request, CI) koruyun.
- Yeni özellik, mevcut davranış, plan ve bloklu işi ayırın. “Çalışıyor”, “destekleniyor”, doğruluk/performans veya dağıtım iddialarını güncel kaynak ve ölçümle doğrulayın. CI testleri gerçek video OCR’ı veya GPU inference’ını kanıtlamaz.
- README’yi giriş sayfası olarak kısa tutun; ayrıntı ve adım adım komutları dokümanlara taşıyın. Doküman haritasında dil ve statüyü (kullanım, tasarım, tarihsel, blocked) gösterin.
- Gerçek video, anime karesi, SRT/VTT metni, kullanıcı adı, özel hostname, token, parola, API key veya gerçek kişisel mutlak yolu commit etmeyin. Örneklerde sentetik içerik ve açık yer tutucular kullanın; değişiklik öncesi/sonrası Markdown taraması yapın.
- AI provider’a içerik göndermeyi otomatikleştirmeyin. AI önerisini insan OCR kararı, eğitim etiketi veya kullanıcı onaylı çeviri olarak tanımlamayın.
- Python değişikliklerinde ilgili testleri çalıştırın; tüm paket için Windows yerel komutu `py -3 -m pytest -q -rs`. Test koşulmadıysa raporda belirtin. Dokümantasyon değişikliğinde `git diff --check` ve göreli Markdown linklerini doğrulayın.
- Commit başlığını ve varsa gövdesini kısa Türkçe yazın; yaygın teknik jargon İngilizce kalabilir. PowerShell’de Türkçe karakter içeren commit mesajını `-m` ile vermeyin. Mesajı UTF-8 dosyaya kaydedip `git commit -F <dosya>` kullanın.
