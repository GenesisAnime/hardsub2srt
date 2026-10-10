# Tarayıcı içi OCR portu — teknik plan ve karar kapıları

Bu belge uygulanmış bir özellik değildir. GitHub Pages üzerinde çalışan saf tarayıcı istemcisinin, bugün yerel Python/FFmpeg/PyTorch ile yapılan işi ne ölçüde karşılayabileceğini tarif eder.

## Teknik sınır

Kullanıcının dosya seçimine veya GPU kullanımına izin vermesi tarayıcıya seçilen dosyanın içeriğini ve Web API'lerini kullanma hakkı verir. `<input type=file>` / File API, seçilen `File` nesnesini verir; web uygulaması genel amaçlı mutlak Windows yolu edinmez. İzinler tarayıcıdan Python, PowerShell, `ffmpeg.exe`, CUDA Torch veya yerel alt süreç çalıştırmaz. WebGPU, JavaScript'ten tarayıcı ve sürücü aracılığıyla grafik/compute aygıtına erişim sağlar; yerel CUDA PyTorch erişimi değildir.

GitHub Pages statik web hosting'dir. Python OCR motoru veya sürekli worker çalıştırmaz. VDS API de kullanıcının bilgisayarındaki video karelerini veya GPU'sunu işlemez. Gerçekten browser-only deneyim istenirse OCR/decoder/pre-postprocess JavaScript/WASM/WebGPU ile yeniden uygulanmalı ve modeller indirilip tarayıcıda yürütülmelidir.

## Bileşen eşleme

| Bugünkü Python katmanı | Mevcut kod | Tarayıcı için iş |
|---|---|---|
| Video metadata/decoder | `hardsub2srt.py`: `run_ffprobe`, `grab_gray`; ffprobe/ffmpeg child process | Demux + seek/decode yeniden yazılmalı. WebCodecs yalnız desteklenen medya/container/codec bileşimlerinde kullanılabilir; geniş uyumluluk için WASM tabanlı FFmpeg/demuxer alternatifi gerekir. IndexedDB'ye tam video kopyalamak gereksiz ve pahalıdır. |
| Kare örnekleme/bant bulma | `detect_style`, `detect_style_upper`, `scan_band`, aday bant/serit fonksiyonları; OpenCV/NumPy | Algoritma/heuristic'ler JS/TypedArray veya OpenCV.js/WASM'a taşınır. Kareleri canvas/WebCodecs'ten alıp aynı örnekleme, tolerans ve zaman tabanı korunmalı. |
| Crop/resize/threshold | `grab_band`, `binarize_white`, kontrast/çizgi hazırlama | OpenCV.js ya da JS/WASM piksel kernelleri; piksel formatı, renk uzayı, interpolation ve eşik eşdeğerliği için fixture karşılaştırması gerekir. |
| OCR model | EasyOCR/PyTorch ana motor; RapidOCR/ONNX ikinci motor yolları | Gerçek model dosyalarını ve lisanslarını envanterle; uyumlu model grafiğini ONNX'e dönüştür/export et, shape/opset/ops doğrula. `onnxruntime-web` WASM CPU veya WebGPU kullanabilir; WASM operatör kapsamı geniş, WebGPU alt kümesi daha dar. GPU yoksa/uyumsuzsa WASM fallback gerekir. |
| OCR decode/postprocess/consensus | `ocr_lines`, batch engine wrappers, Türkçe normalizasyon, `_merge_readings`, oylama | Model output decoder/tokenizer, confidence dönüşümü, satır sıralama, Türkçe düzeltme, sözlük ve 2 motor konsensüsü JavaScript'e çevrilmeli; WebGPU graph output'unun mevcut OCR sonucuyla eşleşmesi gate ile ölçülmeli. |
| Segment ve SRT timing | blok birleştirme/tekrar-gürültü ayırma ve `main`; `srt_format.py` | Frame timestamp'lerinden cue başlangıç/bitiş üretme, gap/merge/split ve SRT biçimi JS'e taşınır. `fmt_ts` gibi küçük saf biçimleme fonksiyonu kolaydır; tek başına pipeline portu değildir. |
| QA/editor/sidecars | CLI JSON, UI sonuçları, QA montage/PNG ve `vtt-qa.py` | Canvas ile QA crop'ları; SRT/VTT parser, hizalama/CER, edit/save ve JSON raporları browser-local JS'e yeniden yazılır. File System Access API opsiyoneldir; indirilebilir Blob fallback gerekir. |

### Model ve runtime notu

`hardsub2srt.py` kaynağında RapidOCR/ONNX Runtime ve EasyOCR/PyTorch kod yolları vardır; bu, mevcut model dosyalarının repo içinde bulunduğunu, web uyumlu olduğunu veya browser'da aynı sonuç verdiğini kanıtlamaz. Depoda ağırlıklar dağıtılmıyor. İlk spike'tan önce model konumu, indirilen graph/lisans, ORT operatör desteği, giriş boyutu, çıktı decoder'ı, boyut ve RAM/VRAM ölçülmeli. Modeli sırf ONNX diye tarayıcıda çalışır saymayın.

## Minimum prototip

Bir UI portu değil, dar bir teknik spike ile karar verin:

1. Repo dışında lisanslı, kısa ve yerel test videosu ile sabit bir subtitle bandı ve bilinen başlangıç/bitiş zamanlarını seçin.
2. Önce bir uyumlu model üzerinde ONNX export ya da mevcut ONNX ağırlığını doğrulayın; modeli ONNX Runtime Web WASM'da çalıştırın. Girdi/çıktı tensorlarını küçük fixture ile Python çıktısına karşılaştırın.
3. Tarayıcı `<video>`/canvas veya WebCodecs ile yalnız gerekli timestamp karelerini çözsün; crop/resize/threshold ve tek OCR modeli çalışsın.
4. Tek OCR satırlarını timestamp'li cue'a ve SRT'ye dönüştürüp dosyayı indirsin. Sabit GT fixture'ında CER/alignment raporu üretsin.
5. Aynı fixture geçerse WebGPU execution provider'ı deneyin; destek yoksa WASM çalışmaya devam etsin. Soğuk açılış, model indirme, video decode, OCR gecikmesi, peak memory ve iptal/duraklatma ölçülsün.

İlk spike'ta otomatik bant keşfi, iki motor konsensüsü, tüm Türkçe düzeltme/öğrenme özellikleri, ASS, QA montage ve mevcut editör kapsam dışı kalmalı. Kalite kapısı geçmeden kullanıcıya tam özellikli ürün gibi sunulmamalı.

## Efor ve başlıca risk

- **Dar spike: orta efor.** Bir model artifact'ı/uyumlu opset, küçük codec örneği ve trusted GT fixture gerekir. Başarı garanti değil; model dışa aktarma/decoder çalışmazsa alternatif model seçimi gerekebilir.
- **Özellik eşitliği: yüksek efor ve risk.** Video container/codec uyumu, seek timestamp farkı, OpenCV piksel eşitliği, Türkçe postprocess ve çok-motorlu kararların yeniden yazımı, WebGPU op/cihaz farkları, yüksek RAM/VRAM ve kullanıcı kapatınca iş iptali çözülmelidir.
- **Dağıtım:** model ve WASM dosyaları büyükse Pages bandwidth/cache ve tarayıcı bellek limitlerine bakılmalı. HTTPS secure context gerekir; WebGPU bütün tarayıcı/cihazlarda mevcut değildir. Uyumlu cihaz yoksa WASM CPU fallback yavaş olabilir.
- **Doğruluk:** tarayıcıdaki sırf OCR-model CER'i yeterli değildir. Sabit örnekler SRT cue timing, low-conf, eksik/fazla cue, özel karakterler ve CSS/video renk dönüşümünü de ölçmelidir. Her değişiklik mevcut `regresyon/gt_gate.py` ve sabit kare kıyasına benzer kapılarla karşılaştırılmalı.

## Karar önerisi

Kullanıcıların mevcut makinesindeki CUDA/GPU ve Python motoru korunacaksa en düşük riskli ürün yolu **Pages = indirme/dokümantasyon, yerel Python worker = OCR, VDS = ayrı ve açık onaylı türetilmiş ölçüm API'si** biçimindedir. Browser-only OCR ayrı bir araştırma prototipi olarak ilerletilmeli; onun seçilen video izni yerel executable çalıştırma izni değildir.
