# hardsub2srt

Videodaki gömülü altyazıyı yerel OCR ile zamanlı SRT’ye çıkarır; gömülü altyazı akışı varsa FFmpeg ile dışa aktarır. **OCR metni okur, çeviri yapmaz.** Ana kullanım Windows ve Python’dır. [İngilizce README](README.en.md) · [Doküman haritası](docs/README.md)

## Hızlı başlangıç

Windows’ta Python 3.10+ ve FFmpeg/ffprobe gerekir. Kurulum ve ilk çalıştırma adımları: [Kurulum ve ilk çalıştırma](docs/KURULUM-VE-ILK-CALISTIRMA.md).

Tek video:

```powershell
py -3 hardsub2srt.py "D:\Videolar\bolum.mkv" -o "D:\Altyazilar\bolum.srt"
```

`-o` zorunludur. CLI aynı `-o` hedefi ve aynı adlı yan dosyalarla yeniden çalıştırıldığında mevcut dosyaların üzerine yazar. Önceki çıktıyı korumak için yeni bir hedef adı seçin. İnceleme paketi de istenmişse mevcut `<ad>.review-pack` klasörü açılmaz veya üzerine yazılmaz.

## Hardsub, softsub ve çıktı

- **Hardsub:** Altyazı video karelerinin içine işlenmiştir; araç karelerden metni OCR ile okur.
- **Softsub:** FFprobe bir altyazı akışı bulursa araç OCR yapmadan FFmpeg ile ilk akışı SRT’ye dönüştürmeyi dener. Akış kodlayıcısının FFmpeg tarafından desteklenmesi gerekir; mevcut CLI’de akış seçme seçeneği yoktur.

Hardsub OCR çıktısını çevirmez veya metnin doğru olduğunu garanti etmez. Hardsub OCR koşumunda CLI, SRT’nin yanında aynı ada bağlı istatistik ve eşleşme yan dosyaları da yazar; bunlarda video adı, boyut/değişiklik zamanı ve kısmi hash bulunabilir. Paylaşmadan önce yan dosyaları inceleyin.

CLI ayarları için `py -3 hardsub2srt.py --help` kullanın. Temel seçenekler:

- `--cpu`: OCR’ı CPU’da zorlar.
- `--nvdec`: FFmpeg’in NVIDIA video decode desteğini dener. Bu, OCR’ı GPU’da çalıştıran PyTorch CUDA ayarından ayrıdır; NVDEC varsayılan olarak kapalıdır ve başarısız olursa decode CPU’ya düşer.
- `--qa 0`: QA kare montajını kapatır. Tam CLI koşusunda varsayılan 6 örnektir; `--limit-seconds` ile sınırlı koşuda varsayılan kapalıdır.
- `--review-pack`: özgün OCR segmentiyle güvenle eşleşen hardsub cue’ları için görsel inceleme paketi üretir. Softsub doğrudan çıkarımda bu paket oluşturulmaz.

PowerShell’de `cikar.bat` tek video için sürükle-bırak, `toplu.bat` toplu iş içindir. Ayrıntılı seçenekler ve çıktı davranışı: [Kullanım rehberi](docs/KULLANIM-REHBERI.md).

## Yerel web arayüzü

`arayuz.bat` çalıştırın veya `py -3 ui_server.py` komutunu verin; arayüz `http://127.0.0.1:8765` adresinde yalnızca bu bilgisayarda açılır. Arayüz işleri seçilen çıktı kökü altında her koşu için yeni `runs/video-<ad>/<zaman>_<iş-kimliği>/` klasörüne yazar; önceki koşu klasörlerini üzerine yazmaz. Kuyruk bellektedir ve sunucu yeniden başlatılınca bekleyen işler geri yüklenmez.

Arayüzün QA montajı varsayılanı 4 örnektir; 0 ile kapatılır. OCR için CUDA destekli PyTorch kullanılabiliyorsa GPU seçilir, aksi durumda CPU’ya düşer. `--cpu` arayüzde de seçilebilir.

## Gizlilik ve AI incelemesi

Video, OCR ve arayüz işlemleri yereldir; program GPT/DeepSeek API’sine bağlanmaz. Kullanıcı isterse seçilen inceleme dosyalarını ZIP olarak indirip kendi seçtiği AI sohbetine elle gönderebilir. AI yanıtı öneridir; görsel OCR kararı veya çeviri belleği kaydı olmak için normal insan onay adımı gerekir. Review-pack SRT ve kare kırpımları içerir; eşitleme klasörlerine kaydedilirse OneDrive gibi araçlar bunları ayrıca senkronize edebilir. İsteğe bağlı `/katki` istemcisi ancak HTTPS hedefi, token ve saklama gün sayısı yapılandırıldığında gönderim seçeneğini açar; önizleme sonrasında kullanıcı ayrı onay vererek yalnız izin verilen sayısal ölçüm özetini gönderir. Video, kare, SRT/VTT veya altyazı metni gönderilmez. Ayrıntılar: [İsteğe bağlı katkı API’si](docs/CONTRIBUTION-API.md).

## Destek ve sınırlar

Windows birincil kullanım platformudur. CI, Python 3.14 üzerinde Windows ve Ubuntu’da otomatik test çalıştırır; Ubuntu CI sonucu tam OCR/FFmpeg kullanıcı akışının desteklendiğini kanıtlamaz. macOS uçtan uca kullanımı bu repoda doğrulanmamıştır. OCR’ın GPU kullanması CUDA uyumlu PyTorch ve sürücülere bağlıdır; NVDEC ayrı bir video-decode seçeneğidir.

OCR veya kullanıcı verisi olmadan yapılan değişiklik testleri için [Katkı kuralları](CONTRIBUTING.md) ve tam belge listesi için [Doküman haritası](docs/README.md) sayfasına bakın.

MIT lisansı. Üçüncü taraf kütüphane, model ve FFmpeg bileşenleri kendi lisans/dağıtım koşullarına tabidir; model ağırlıkları bu depoda bulunmaz.
