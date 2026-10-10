# hardsub2srt

hardsub2srt, videoya gömülü altyazıları OCR ile zamanlı Türkçe `.srt` dosyasına dönüştüren yerel bir Windows/Python aracıdır. Video ve OCR işlemi bu makinede çalışır.

**Türkçe** · [English](README.en.md)

## Hızlı başlangıç (Windows)

1. Python 3.10+ ve FFmpeg kurun; `ffmpeg` ile `ffprobe` komutlarının PATH üzerinde olduğunu doğrulayın.
2. Depo klasöründe bağımlılıkları yükleyin:

   ```powershell
   py -3 -m pip install -r requirements.txt
   ```

   EasyOCR/PyTorch ilk çalıştırmada model dosyalarını indirebilir. NVIDIA GPU kullanımı PyTorch/CUDA kurulumuna bağlıdır; CUDA'lı PyTorch paketini makine ve sürücünüze uygun biçimde ayrıca kurmanız gerekebilir. Bu depo bir `.exe` veya kilitli/tekrar üretilebilir ortam paketi içermez.

3. Tek video için:

   ```powershell
   py -3 hardsub2srt.py "D:\Videolar\bolum.mp4" -o "D:\Altyazilar\bolum.srt"
   ```

   OCR, CUDA destekli Torch kullanılabilir durumdaysa GPU'yu seçer; aksi halde CPU'ya düşer. `--cpu` GPU'yu açıkça kapatır:

   ```powershell
   py -3 hardsub2srt.py "D:\Videolar\bolum.mp4" -o "D:\Altyazilar\bolum.srt" --cpu
   ```

   `cikar.bat` sürükle-bırak için, `toplu.bat` klasördeki toplu işler için Windows başlatıcılarıdır. CLI çıkış yolu `-o` ile belirtilir.

## Yerel web arayüzü

`arayuz.bat` başlatıcısını çalıştırın veya `py -3 ui_server.py` komutunu kullanın. Arayüz `http://127.0.0.1:8765` adresinde yalnızca bu bilgisayara bağlı yerel Flask sunucusunu açar. Tarayıcı arayüzdür; Python OCR motorunu yerel sunucunun başlattığı alt süreç çalıştırır.

Arayüzden birden çok video dosyası veya klasör seçilebilir. Windows seçicileri yerel dosya yollarını sunucuya verir; video içeriği tarayıcıya yüklenmez. Dosya/klasör seçicisi Windows'a özgüdür; diğer sistemlerde yolu elle girin.

Kuyruk tek işçiyle sırayla çalışır. Bir `/api/ekle` isteği en fazla 100 video alır; arayüz daha büyük listeleri 100'lük parçalara böler. Çalışan ve bekleyen toplam iş sayısı üst sınırı 2.000'dir. Durum yanıtı son 100 kaydı gösterir ve çalışan işi ayrıca sabitler. Aynı kaynak dosyanın bekleyen/çalışan tekrarları elenir. **Kuyruk bellektedir:** sunucu kapatılır veya yeniden başlatılırsa bekleyen işler ve oturum içi geçmiş kalıcı değildir.

Her UI işi, seçilen çıktı klasöründe `runs/video-<ad>/<tarih-saat>_<iş-kimliği>/` altında ayrı bir klasör alır; yeni iş eski koşum çıktısının üstüne yazmaz. Arayüz SRT'nin yanında istatistik/koşum JSON'ları ve istenmişse `qa/` görselleri, VTT karşılaştırma raporu veya ASS üretir. CLI'de ise belirtilen `-o` yolu kullanılır.

## Çıktılar ve kalite kontrolü

Bir `-o ...\bolum.srt` koşumu en azından SRT ve `.stats.json` üretir; araç sürümü, video eşleşme bilgisi ve parametreler `.hardsub2srt.json` yan dosyasında tutulur. Yan dosyalar video dosyası değildir; ancak yerel video adı, boyutu/mtime ve kısmi hash gibi eşleştirme bilgileri içerebilir. Bunları paylaşmadan önce inceleyin.

OCR motoru görünen altyazıyı çıkarır; kendi başına çeviri yapmaz. İsteğe bağlı olarak yerel arayüzde `/ocr-inceleme` sayfasından görselle doğrulanmış seçili bloklar için bir sohbet paketi hazırlayabilir, bu paketi seçtiğin AI'ya kendin gönderip yanıtını uygulamaya aktarabilirsin. Program GPT/DeepSeek API'sini çağırmaz ve bu içerikleri ağa göndermez; dış AI'ya yükleme kullanıcı tarafından yapılır. Kabul ettiğin çevirileri ayrıca proje ve dil kapsamına bağlı yerel belleğe kaydedebilir, terim önerilerini taslağa elle uygulayabilirsin. Ayrıntılar: [yerel inceleme ve öğrenme akışı](docs/REVIEW-BUNDLE.md). `vtt-qa.py` ile `regresyon/gt_gate.py` yalnızca OCR metnini zaman uyumlu kaynak altyazıyla kıyaslar; çeviri kalitesini ölçmez. `gt_gate.py` video ve trusted VTT varlıklarını dışarıdan ister; eksik/belirsiz varlıklar geçiş sayılmaz. Kullanım: [regresyon/README-kisa.md](regresyon/README-kisa.md). Video, VTT ve anime kareleri depoya eklenmemelidir.

İsteğe bağlı **Yerel AI inceleme paketi** ayarı (UI'de varsayılan kapalı) veya CLI `--review-pack`, başarılı hard-sub OCR koşumunda final SRT'yi, her güvenle eşleşebilen cue için altyazı-bandı kırpımını ve `manifest.json` dosyasını `<ad>.review-pack/` altında üretir. Yalnız final cue aralığı tek bir özgün OCR segmentiyle birebir eşleşirse crop alınır; birleşmiş/kararsız cue'lar manifestte `unavailable` kalır. Tek bir OpenCV video yakalama oturumu tekrar kullanılır; cue başına yeni FFmpeg süreci açılmaz. `--ust-ana` kaynak bandı korunur. Zaman, frame index / bildirilen FPS üzerinden tahmin edilir; değişken kare hızlı videolarda kaynak PTS eşleşmesi garanti edilmez. Paket en fazla 1.000 benzersiz kırpım, 128 MiB toplam veri, crop başına 8 MiB ve 1280 piksel en uzun kenar sınırına sahiptir. Sayaç veya byte bütçesi dolduğunda yeni kareler okunmaz; aynı kare/banttan daha önce kaydedilmiş kırpımlar yeniden kullanılır. Soft-sub girdisinde OCR kare eşlemesi olmadığı için paket atlanır. Var olan paket hiçbir zaman üzerine yazılmaz. Bu özellik GPT/DeepSeek API'sini çağırmaz ve dosya yüklemez; paket içeriği SRT metni ile görsel altyazı karelerini barındırır, bu nedenle dışarıya elle paylaşmadan önce gözden geçirin. Biçim ve kullanım: [yerel inceleme paketi](docs/REVIEW-BUNDLE.md).

## Bağımlılıklar ve sorun giderme

`requirements.txt` Python paketlerini listeler: NumPy, OpenCV, EasyOCR, RapidOCR, ONNX Runtime ve Flask. Video okuma için FFmpeg/ffprobe ayrıca gerekir. `py -3 -m pip show torch` ve `py -3 -c "import torch; print(torch.cuda.is_available())"` ile mevcut PyTorch/CUDA durumunu kontrol edebilirsiniz. UI veya CLI `ffmpeg`/`ffprobe` bulunamadığını bildirirse FFmpeg'i PATH'e ekleyip yeni terminal açın. GPU bulunmuyorsa veya CUDA uyumsuzsa `--cpu` açık seçenektir; işlem belirgin biçimde daha yavaş olabilir.

- UI açılmıyorsa `arayuz.bat` terminal çıktısına ve Flask kurulumuna bakın; 8765 portunun başka süreçte dolu olup olmadığını kontrol edin.
- Kuyruk sayacı/iş hata mesajlarını izleyin. Bir iş hata aldıysa işin log ve koşum klasörüne bakın. Sunucuyu yeniden başlatmak bekleyen kuyruğu kurtarmaz.
- Model ilk indirmeleri ve GPU/PyTorch paket boyutu internet, Python ve sürücü sürümüne göre değişir.

## Gizlilik ve isteğe bağlı ölçüm katkısı

OCR ve AI incelemesi yerel çalışır. GPT/DeepSeek API çağrısı yoktur. Katkı API kodu isteğe bağlı prototip olarak eklendi; **VDS'ye dağıtılmamıştır** ve hiçbir uzak gönderim yapılmadı. Yerel `/katki` sayfasında tamamlanmış koşum için önce ağsız bir önizleme hazırlanır; sadece ayrı onay kutusu ve **Onayla ve gönder** tıklaması HTTPS isteği başlatır. URL/token ve saklama gün sayısı ayarları yoksa düğme kapalıdır; ekranda gösterilen gün sayısı API politikasıyla eşleşmelidir. Gönderilen v1 JSON yalnız sayısal süre/kare/cue/düşük güven özetlerini, OCR aygıt ve motor sınıfını taşır. Video, görüntü, SRT/VTT/metin, dosya adı/yolu, kullanıcı adı, hash veya kalıcı cihaz kimliği kabul edilmez. CER gönderimi UI'de kapalıdır; API sözleşmesinde yerel güvenilir VTT ve ayrıca CER onayı zorunludur. Saklama süresi VDS işletmecisi tarafından açıkça konfigüre edilmeden API başlamaz.

VDS kurulum adımları, token yaşam döngüsü, retention/silme ve TLS proxy sınırları: [İsteğe bağlı katkı API'si](docs/CONTRIBUTION-API.md). Bu API'yi canlıya almak için gerçek domain/TLS, Windows servis hesabı, kalıcı/veri yedekli disk, güvenlik duvarı ve retention kararı gerekir. GitHub Pages OCR/API sunucusu değildir. Tasarım notları:

- [Yerel istemci ve mimari yol haritası](docs/LOCAL-ARCHITECTURE-ROADMAP.md)
- [İsteğe bağlı katkı API'si ve VDS hazırlığı](docs/CONTRIBUTION-API.md)
- [Tarayıcı içi OCR teknik planı](docs/BROWSER-OCR-PORT-PLAN.md)
- [OCR ve çeviri öğrenme sistemi planı](docs/LEARNING-SYSTEM-PLAN.md)
- [Öğrenme sistemi Phase 0 kaynaklı baseline](docs/LEARNING-SYSTEM-BASELINE.md)
- [Yerel OCR inceleme paketi biçimi](docs/REVIEW-BUNDLE.md)
- [Katkı kuralları](CONTRIBUTING.md)

## Dosyalar

| Dosya | Amaç |
|---|---|
| `hardsub2srt.py` | CLI çıkarım/OCR motoru |
| `ui_server.py` | localhost Flask UI, iş kuyruğu, yerel alt süreçler |
| `contribution_client.py`, `contribution_api.py` | varsayılanı kapalı, açık onaylı sayısal katkı istemcisi ve WSGI API |
| `schemas/contribution-metrics-v1.schema.json` | katkı payload'ının strict v1 sözleşmesi |
| `srt_format.py` | SRT zaman biçimi yardımcıları |
| `cikar.bat`, `arayuz.bat`, `toplu.bat` | Windows başlatıcıları |
| `srt2ass.py` | SRT'den ASS üretimi |
| `vtt-qa.py` | SRT/VTT metin ve zaman kıyası |
| `ogren.py`, `kullanici-sozlugu.txt` | Yerel düzeltme/öğrenme araçları ve sözlük |
| `regresyon/` | Kare regresyonu ve video+GT OCR kapısı |
| `docs/` | Mimari ve geliştirme notları |

## Lisans

MIT. OCR modellerinin, PyTorch/CUDA'nın, FFmpeg'in ve diğer üçüncü taraf bileşenlerin ayrı lisans ve dağıtım koşulları olabilir; model ağırlıkları bu depoda dağıtılmaz.
