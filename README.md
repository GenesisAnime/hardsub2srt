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

Kuyruk tek işçiyle sırayla çalışır. Bir `/api/ekle` isteği en fazla 100 video alır; arayüz daha büyük listeleri 100'lük parçalara böler. Çalışan ve bekleyen toplam iş sayısı üst sınırı 2.000'dir. Durum yanıtı son 100 kaydı gösterir ve çalışan işi ayrıca sabitler. Aynı kaynak dosyanın bekleyen/çalışan tekrarları elenir. **Kuyruk işleri ve canlı ayrıntılı log bellektedir:** sunucu yeniden başlarsa bekleyen işler geri yüklenmez. Son koşumlar için sınırlı tanılama özeti Windows'ta `%LOCALAPPDATA%\hardsub2srt\job-history.json`, Linux/macOS'ta `$XDG_STATE_HOME/hardsub2srt/job-history.json` (XDG ayarlı değilse `~/.local/state/hardsub2srt/job-history.json`) altında tutulur; `/api/is-gecmisi` en son 20 kaydı verir (`?limit=1..50`). Geçmiş en fazla 100 kayıt, 30 gün ve 64 KiB ile sınırlıdır; video/çıktı yolu, ad, SRT veya görüntü içeriği içermez. Bu özet kuyruğu geri yüklemez.

Her UI işi, seçilen çıktı klasöründe `runs/video-<ad>/<tarih-saat>_<iş-kimliği>/` altında ayrı bir klasör alır; yeni iş eski koşum çıktısının üstüne yazmaz. Arayüz SRT'nin yanında istatistik/koşum JSON'ları ve istenmişse `qa/` görselleri, VTT karşılaştırma raporu veya ASS üretir. CLI'de ise belirtilen `-o` yolu kullanılır.

## Çıktılar ve kalite kontrolü

Bir `-o ...\bolum.srt` koşumu en azından SRT ve `.stats.json` üretir; araç sürümü, video eşleşme bilgisi ve parametreler `.hardsub2srt.json` yan dosyasında tutulur. Yan dosyalar video dosyası değildir; ancak yerel video adı, boyutu/mtime ve kısmi hash gibi eşleştirme bilgileri içerebilir. Bunları paylaşmadan önce inceleyin.

Ana, `_ekran.srt` ve isteğe bağlı `_ust.srt` yazıcıları bir cue'nun bitişini
sonraki cue başlangıcına kırpabilir. Kırpma yapılan koşularda `.stats.json`
`writer_end_clamps` alanı ham ve yazılmış bitişi, kırpma miktarını ve sıfır
tabanlı cue indeksini içerir; `_ust.srt` için aynı bilgi `ust_bant` stats
alanında ve `_ust.json` içinde bulunur. Tanılama metin veya kare görüntüsü
içermez. Bu kayıtlar SRT ayrıştırma denetiminden ayrıdır. `ocr_video_eval.py`
içindeki `_parse_srt_bytes`, UTF-8 ve boyut/cue sınırlarını, blok biçimini,
zaman damgası biçimini ve her cue için pozitif süre ile boş olmayan/metin
uzunluğu sınırını denetler; cue sıralamasını veya cue'lar arası çakışmayı
doğrulamaz. `writer_end_clamps` ise yalnızca yazıcının kırptığı cue'ları ve
delta değerini kaydeder; zamanlama doğruluğunu sınamaz veya kanıtlamaz.
Zamanlar kare indeksi / bildirilen FPS ile
türetilir ve yaklaşık değerlerdir; kaynak PTS saklanmadığından özellikle VFR
videolarda gerçek sunum zamanını kanıtlamaz. Tanılama bir düzeltme veya doğruluk
iddiası değildir. Kayıt yalnızca bu OCR koşumunun yazdığı SRT baytlarını
tanımlar; SRT sonradan elle değiştirilir veya zamanlanırsa stats otomatik
güncellenmez ve artık düzenlenmiş dosyanın durumunu yansıtmayabilir.

İsteğe bağlı review-pack, `.stats.json` içindeki `main_srt_sha256` mevcut ana
SRT'nin hash'iyle eşleşirse, kırpılmış cue için ham bitişi ve kırpma farkını
paket manifestine ve manuel AI OCR istek paketine ekler. Eşleşme yoksa bu
alanlar atlanır; paket bunu `raw_timing_metadata` durumunda açıklar. Bu
değerler de frame/FPS tabanlı yaklaşık tanılamadır; AI yanıtı hâlâ SRT'de
yazılmış `start_ms`/`end_ms` değerlerini korur ve zamanlama değiştiremez.

Mevcut bir SRT'yi ayrıca salt-okunur kalite raporundan geçirmek için
`python srt_lint.py "bolum.srt"` çalıştırın. Araç parse sorunlarını hata olarak;
kronoloji, çakışma, karakter/sn (CPS) ve kısa/uzun süre bulgularını ise yalnızca
sezgisel uyarı olarak yazar. CPS ve süre eşikleri komut satırından değiştirilebilir;
`--cps-warning 0 --min-duration-ms 0 --max-duration-ms 0` tüm sezgisel uyarıları
kapatır. Varsayılan eşikler evrensel kalite standardı değildir ve dile/altyazı
biçemine göre yanlış uyarı üretebilir. Araç SRT'yi değiştirmez, OCR kuyruğuna veya
üretim pipeline'ına bağlı değildir; parse hatası nedeniyle döndüğü kod da yalnızca
bu elle başlatılan rapor komutuna aittir.

OCR motoru görünen altyazıyı çıkarır; kendi başına çeviri yapmaz. İsteğe bağlı olarak yerel arayüzde `/ocr-inceleme` sayfasından görselle doğrulanmış seçili bloklar için bir sohbet paketi hazırlayabilir, bu paketi seçtiğin AI'ya kendin gönderip yanıtını uygulamaya aktarabilirsin. Program GPT/DeepSeek API'sini çağırmaz ve bu içerikleri ağa göndermez; dış AI'ya yükleme kullanıcı tarafından yapılır. Kabul ettiğin çevirileri ayrıca proje ve dil kapsamına bağlı yerel belleğe kaydedebilir, terim önerilerini taslağa elle uygulayabilirsin. Ayrıntılar: [yerel inceleme ve öğrenme akışı](docs/REVIEW-BUNDLE.md). `vtt-qa.py` ile `regresyon/gt_gate.py` yalnızca OCR metnini zaman uyumlu kaynak altyazıyla kıyaslar; çeviri kalitesini ölçmez. `gt_gate.py` video ve trusted VTT varlıklarını dışarıdan ister; eksik/belirsiz varlıklar geçiş sayılmaz. Kullanım: [regresyon/README-kisa.md](regresyon/README-kisa.md). Video, VTT ve anime kareleri depoya eklenmemelidir.

### AI ile OCR görsel incelemesi ve çeviri incelemesi

İki manuel AI akışı farklı amaçlarla çalışır. **AI OCR görsel incelemesi**, geçerli kırpımı olan OCR bloklarında insan henüz metni onaylamadan kullanılabilir; OCR metnini ve kırpım görselini AI'ya sen gönderirsin, yanıt uygulamada yalnızca öneri olarak kaydedilir. Bu öneri kendiliğinden OCR etiketi, eğitim verisi, sözlük düzeltmesi veya çeviri belleği olmaz. Etiket olması için normal görsel incelemede metni kabul etmen ya da düzeltmen gerekir.

**AI çeviri incelemesi** ise yalnızca insan tarafından görsel olarak kabul edilmiş/düzeltilmiş kaynak metni ve geçerli kırpımı olan bloklarda açılır. AI yanıtı da öneridir; kullanıcı açıkça kabul/düzenlemeden çeviri belleğine girmez. Uygulama GPT/DeepSeek API'sini çağırmaz; paketleri dış AI'ya sen elle gönderirsin.

İnceleme ZIP'i, yan dosyası ve AI öneri günlüğü seçilen `.review-pack` klasörüne yazılır; OneDrive bu dosyaları otomatik olarak eşitleyebilir. Yalnız yerel kullanım istiyorsan **paketin tamamını** (manifest, SRT, `review-events.jsonl` ve `crops/` dahil) eşitlenmeyen yerel bir klasöre kopyalayıp inceleme akışını o kopyayla aç. Ayrıntılar: [yerel inceleme ve öğrenme akışı](docs/REVIEW-BUNDLE.md) ve [AI OCR görsel incelemesi](docs/AI-OCR-GORSEL-INCELEME.md).

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
| `job_history.py`, `windows_job.py` | sınırlı yerel iş geçmişi ve Windows çocuk-süreç sahipliği |
| `ocr_review.py`, `ai_ocr_review.py`, `review_bundle.py` | insan OCR görsel incelemesi, manuel AI görsel önerileri ve inceleme paketi üretimi |
| `translation_review.py`, `translation_memory.py` | doğrulanmış kaynak metne bağlı çeviri önerileri ve kullanıcı onaylı yerel çeviri belleği |
| `ocr_dataset_metadata.py`, `ocr_dataset_merge.py` | veri kümesi hak/provenans metadata'sı ve güvenli paket birleştirme |
| `ocr_experiment.py`, `ocr_crop_eval.py`, `ocr_video_eval.py`, `ocr_video_eval_templates.py`, `phase6_release.py` | çevrimdışı OCR deneyleri, kırpım/tam video ölçümü ve sürüm kapısı |
| `contribution_client.py`, `contribution_api.py` | varsayılanı kapalı, açık onaylı sayısal katkı istemcisi ve WSGI API |
| `schemas/contribution-metrics-v1.schema.json` | katkı payload'ının strict v1 sözleşmesi |
| `srt_format.py` | SRT zaman biçimi yardımcıları |
| `srt_lint.py` | Var olan SRT için salt-okunur yapı ve sezgisel kalite raporu |
| `docs/RUN-TIMING.md` | OCR çalışma süresi istatistik alanları ve ölçüm kapsamı |
| `cikar.bat`, `arayuz.bat`, `toplu.bat` | Windows başlatıcıları |
| `srt2ass.py` | SRT'den ASS üretimi |
| `vtt-qa.py` | SRT/VTT metin ve zaman kıyası |
| `ogren.py`, `kullanici-sozlugu.txt` | Yerel düzeltme/öğrenme araçları ve sözlük |
| `regresyon/` | Kare regresyonu ve video+GT OCR kapısı |
| `docs/AI-OCR-GORSEL-INCELEME.md`, `docs/REVIEW-BUNDLE.md` | AI OCR görsel öneri ve insan inceleme akışları |
| `docs/LEARNING-SYSTEM-PLAN.md`, `docs/LEARNING-SYSTEM-BASELINE.md` | aşamalar ve ölçülmüş mevcut durum |
| `docs/OCR-OFFLINE-EXPERIMENT.md`, `docs/FIRST-DATASET-QUICKSTART.md` | çevrimdışı değerlendirme ve veri kümesi hazırlığı |
| `docs/LOCAL-ARCHITECTURE-ROADMAP.md`, `docs/` | yerel sistem mimarisi ve diğer geliştirme notları |

## Lisans

MIT. OCR modellerinin, PyTorch/CUDA'nın, FFmpeg'in ve diğer üçüncü taraf bileşenlerin ayrı lisans ve dağıtım koşulları olabilir; model ağırlıkları bu depoda dağıtılmaz.
