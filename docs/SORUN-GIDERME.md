# Sorun giderme

## FFmpeg veya FFprobe bulunamıyor

Yeni PowerShell penceresinde:

```powershell
Get-Command ffmpeg
Get-Command ffprobe
ffmpeg -version
ffprobe -version
```

Komutlardan biri bulunmuyorsa FFmpeg kurulum dizinini `PATH`’e ekleyin ve yeni terminal açın. Her ikisi de bulunuyor ama video açılmıyorsa dosyanın başka bir oynatıcıda açıldığını ve container/codec uyumluluğunu doğrulayın.

## GPU kullanılmıyor

OCR GPU seçimi CUDA destekli PyTorch, GPU sürücüsü ve aynı Python environment’ındaki paketlere bağlıdır:

```powershell
py -3 -m pip show torch
py -3 -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

`False` ise OCR CPU’ya düşer; `--cpu` bunu açıkça seçer. CUDA kurulumu bu repoda sabitlenmiş değildir. CUDA’lı PyTorch kurup aynı komutlarla yeniden doğrulayın.

`--nvdec` video decode içindir; OCR modelinin GPU kullandığını göstermez. Varsayılan olarak kapalıdır ve decode başarısız olursa CPU’ya düşer. Video decode ile OCR aygıt durumunu ayrı değerlendirin.

## Softsub SRT’si beklenenden farklı veya çıkarılamıyor

ffprobe altyazı akışı bulursa CLI ilk gömülü akışı OCR yerine FFmpeg ile dönüştürmeyi dener. Yalnız ilk akış kullanılır; CLI’de track seçimi yoktur. Codec SRT’ye dönüştürülemiyorsa FFmpeg hata verir. Kaynakta gömülü akış yoksa OCR yolu kullanılır.

## Yerel arayüz açılmıyor

- `arayuz.bat` penceresindeki hatayı okuyun; Flask’ın aynı Python environment’ına kurulu olduğunu kontrol edin.
- Başka uygulama 8765 portunu kullanıyorsa onu kapatın veya portu kullanan servisi inceleyin.
- Arayüz yalnız bu bilgisayarda `127.0.0.1:8765` üzerinden dinler; aynı ağdaki başka bir cihazdan erişim sunmaz.

Kuyruk işleri sunucu belleğinde tutulur. Sunucu yeniden başladıysa bekleyen işler listeden kaybolur; UI iş geçmişi yalnızca sınırlı tanılama özetidir, işleri yeniden başlatmaz.

## Çıktı yazılmıyor veya eski çıktı değişti

`-o` ile verilen klasör oluşturulur. CLI aynı hedefe yeniden çalıştırıldığında ana SRT ve aynı ada bağlı yan dosyalar üzerine yazılır. Önceki çıktıyı korumak için yeni bir dosya adı verin. `--review-pack` ile oluşturulan paket zaten varsa CLI durur; eski paketi elle silmeden önce yedekleyin.

Arayüz her iş için yeni `runs/...` klasörü üretir; sonuçları arayüzün gösterdiği koşum dizininde arayın. QA görüntüsü bekliyorsanız UI’de QA sayısının 0 olmadığını, CLI’de ise `--qa` ayarını kontrol edin. CLI tam koşusunda varsayılan 6, sınırlı koşuda varsayılan 0’dır; UI varsayılanı 4’tür.

## Gizlilik ve paylaşılan dosyalar

`.stats.json` ve `.hardsub2srt.json` video adı, boyut/değişiklik zamanı veya kısmi eşleşme hash’i içerebilir. Review-pack ise SRT metni ve kare kırpımları içerir. Dosya eklerini paylaşmadan önce inceleyin; OneDrive/eşitleme klasöründeki verileri uygulamanın ağ isteği yapmaması engellemez.

Kurulum için [Kurulum ve ilk çalıştırma](KURULUM-VE-ILK-CALISTIRMA.md), dokümanların tamamı için [Doküman haritası](README.md) sayfasına dönün.
