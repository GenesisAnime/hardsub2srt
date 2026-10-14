# Kurulum ve ilk çalıştırma

Bu depo Windows’u birincil kullanıcı platformu kabul eder. Ubuntu, GitHub Actions’ta Python 3.14 ile test edilir; bu CI, Ubuntu’da tam video/OCR akışının doğrulandığı anlamına gelmez. macOS uçtan uca kullanım bu depoda doğrulanmamıştır.

## Gerekenler

- Python 3.10 veya üstü. CI test matrisinde kullanılan sürüm Python 3.14’tür; OCR runtime paketlerinin ve CUDA uyumluluğunun Python/sürücü kombinasyonuna göre değişebileceğini unutmayın.
- FFmpeg ve FFprobe; ikisi de `PATH` içinde bulunmalıdır.
- Video/OCR runtime paketleri `requirements.txt` içindedir. Depo kilitli bir environment veya `.exe` dağıtmaz.

FFmpeg’in resmi indirme sayfası Windows build sağlayıcılarını listeler: [ffmpeg.org/download.html](https://ffmpeg.org/download.html). Seçtiğiniz build’de `ffmpeg.exe` ve `ffprobe.exe` bulunmalı; `bin` klasörünü `PATH`’e ekleyin. GPU OCR için PyTorch’un [yerel kurulum seçicisini](https://pytorch.org/get-started/locally/) kullanarak Python ve sürücünüze uygun CUDA paketini seçin; `requirements.txt` CUDA wheel’ini seçmez.

PowerShell’de araçların bulunduğunu kontrol edin:

```powershell
py -3 --version
ffmpeg -version
ffprobe -version
```

## Python paketleri

```powershell
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt
```

Komutları PowerShell’de depo kökünden çalıştırın. EasyOCR/PyTorch ilk kullanımda model dosyaları indirebilir. CUDA’lı PyTorch ve sürücü kurulumu makineye bağlıdır; `requirements.txt` belirli bir CUDA wheel’ini sabitlemez. GPU bekliyorsanız aynı Python yorumlayıcısında `torch.cuda.is_available()` sonucunu doğrulayın. GPU yolu hazır değilse OCR için `--cpu` kullanın.

```powershell
py -3 -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

## İlk SRT

```powershell
py -3 hardsub2srt.py "D:\LOCAL-ONLY-PLACEHOLDER\input.mkv" -o "D:\LOCAL-ONLY-PLACEHOLDER\output.srt"
```

`-o` tam SRT hedefini belirtir. Rerun aynı SRT ve aynı ada bağlı yan dosyaların üzerine yazar; önceki çıktıyı korumak için yeni dosya adı kullanın. Hata durumunda FFmpeg/FFprobe’un PATH’te olduğunu, videonun açıldığını ve OCR paketlerinin kurulu olduğunu kontrol edin.

Tam CLI seçenekleri: `py -3 hardsub2srt.py --help`. Arayüz için `arayuz.bat` veya `py -3 ui_server.py` çalıştırın; tarayıcıda `http://127.0.0.1:8765` adresini açın. Windows seçicileri Windows’a özgüdür; başka sistemlerde dosya yollarını elle girmek gerekir.

### Çalışma platformu hakkında

GitHub CI, testleri Windows ve Ubuntu’da Python 3.14 ile çalıştırır; CI OCR modeli, gerçek video veya CUDA/GPU inference testi yapmaz. Bu nedenle platform/CI sonucu gerçek medya akışının doğrulandığı biçiminde yorumlanmamalıdır. macOS için kurulum veya uçtan uca destek iddiası yoktur.
