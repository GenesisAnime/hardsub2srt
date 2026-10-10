# Yerel istemci, API sınırı ve mimari yol haritası

Bu belge planı anlatır. VDS API, telemetry gönderimi, GitHub Pages arayüzü ve merkezi kullanıcı hesabı bu kod tabanında uygulanmış özellikler değildir.

## Bugünkü çalışma şekli

- `hardsub2srt.py` Python CLI'si video meta verisi/kare çıkarımı için `ffprobe`/`ffmpeg`, kare işleme için OpenCV/NumPy, OCR için EasyOCR/PyTorch ve isteğe bağlı RapidOCR/ONNX Runtime yollarını kullanır.
- `--cpu` açıkça seçilmedikçe araç CUDA destekli PyTorch erişilebiliyorsa GPU kullanmayı dener; yoksa CPU'ya düşer. Kullanılan aygıt koşum istatistiğine yazılır. GPU varsayılanı ürün sözleşmesidir.
- `ui_server.py` Flask'ı `127.0.0.1:8765` üzerinde açar. Sıralı işçi thread'i her video için yerel `hardsub2srt.py` alt süreci başlatır. Windows native dosya/klasör seçimi PowerShell STA alt sürecinde yapılır; medya tarayıcıya yüklenmez.
- Kuyruk bellektedir: tek API isteği en fazla 100 girdi; UI bu boyutta parçalara böler; toplam bekleyen+çalışan tavanı 2.000. `/api/durum` son 100 kayıtla birlikte çalışan işi ayrıca verir. Sunucu yeniden başlatılırsa kuyruk kalıcılaştırılmaz.
- Her UI işi seçilen çıktı klasörü altında çakışmayan yeni `runs/video-<ad>/<timestamp>_<id>/` yoluna yazılır. CLI açık `-o` hedefini kullanır.
- Araç stats/run metadata, altyazı, istenirse QA görüntüleri, ASS veya referans VTT kıyas raporu üretebilir. Bazı metadata yerel video adı/mtime/boyut/kısmi hash içerir. Bu veriler kullanıcı dosyalarının kendisi değildir, fakat paylaşılmadan önce gözden geçirilmelidir.
- Bugünkü sistemde sayısal kullanıcı telemetrisi yükleyen API veya arka uç şeması yoktur. `README.md` ve `CONTRIBUTING.md` gelecekteki katkı kurallarını anlatır; kendiliğinden veri gönderimi değildir.

## Önerilen dağıtım sınırı

GitHub Pages yalnız statik HTML/CSS/JavaScript dosyaları sunar; Python/FFmpeg/PyTorch alt süreci çalıştıran bir sunucu değildir. VDS API kullanıcının PC'sindeki GPU'ya erişemez. Bu nedenle ilk üretim tasarımı üç rolü ayırmalıdır:

1. **Pages — statik sunum ve dokümantasyon.** Sürüm, indirilebilir imzalı paket, kurulum ve katkı onayı akışını gösterir. API anahtarı veya video içeriği Pages içine gömülmez.
2. **Yerel worker — işleme.** Mevcut Python motoru yerel kurulum/portable paket içinde kalır; GPU seçimi ve `--cpu` seçeneği korunur. Browser UI ile bağ için localhost üzerinde dar yetkili bir yerel servis veya mevcut Flask UI kullanılabilir. CORS, Origin kontrolü, CSRF benzeri yerel istek riski ve sadece loopback bind gözden geçirilmelidir.
3. **VDS API — yalnız opsiyonel katkı alımı.** Kimlik doğrulamalı, rate limitli bir endpoint kullanıcı onayından sonra türetilmiş ve asgariye indirilmiş ölçüm alabilir. İş kuyruğu, sürüm uyumluluğu, veri saklama/silme politikası ve reddedilen girdilerin davranışı API tasarımının parçası olmalıdır.

## Telemetry paylaşım tasarımı (gelecek; henüz yok)

İlk paylaşım biçimi doğrudan otomatik yükleme yerine şu özellikleri olan kullanıcı tarafından başlatılan bir “önizle, dışa aktar, onayla, gönder” akışı olmalı:

- **Kapsam görünür:** araca/model/OS sürümüne ilişkin bilgiler, OCR cihaz sınıfı (`cuda`, `cpu` gibi), parametrelerin gizlilikten arındırılmış alt kümesi, toplam süre ve ölçülebilir aşama süreleri, taranan kare/blok sayıları, güven/low-conf oranları, hata sınıfı ve rapor şeması sürümü.
- **İçerik hariç:** video, kare, ham SRT/VTT, replik veya kelime listesi, mutlak dosya yolu, kullanıcı adı, video hash'i ve rastgele tanımlanabilir tam video adı gönderilmez. CER ancak kullanıcının kendi güvenilir referansı yerelde seçilip, yalnız sayısal özet üretilerek ve bu alan ayrıca onaylanarak eklenebilir.
- **Açık rıza:** gönderim öncesinde alanlar, amaç, saklama süresi ve silme yolu gösterilir. Reddetme yerel OCR ve uygulama kullanımını etkilemez. İlk sürümde anonim telemetri için uzun ömürlü cihaz ID'si yerine gönderim başına rastgele ID kullanılır.
- **Güvenlik:** HTTPS, kısa ömürlü/token başına scope, replay/idempotency anahtarı, boyut ve oran sınırı, JSON şema doğrulaması, kötü amaçlı içerik kontrolü, gizli anahtarı statik sitede tutmama. CORS kimlik doğrulama yerine geçmez.
- **MVP veri yolu:** yerel istemci tek bir özet JSON oluşturur; kullanıcı preview ekranından gönderir; API ham dosya kabul etmez ve sadece şemaya uygun alanları saklar. Ayrı bir veri sözleşmesi ve saklama politikası olmadan endpoint yayımlanmaz.

## Aşamalar ve riskler

| Aşama | Kapsam | Başarı ölçütü | Risk |
|---|---|---|---|
| 0 — istemciyi dağıtılabilir yap | Python/CUDA/FFmpeg kurulum matrisini belgelemek; bağımlılıkları kilitlemek/işletim sistemi bazında kurulum doğrulamak; portable/installer denemesi | temiz Windows VM'de CPU; destekli makinede CUDA; kullanıcı videosu çıkışını koruma | PyTorch/CUDA paket boyutu, driver uyumu, model indirme ve lisans koşulları |
| 1 — sürüm/feedback şeması | paylaşılacak alanlar, consent UX, retention, threat model ve API sözleşmesi | örnek JSON'da kaynak yolu/altyazı/video kimliği yok; kullanıcı açık onayı olmadan network çağrısı yok | yeniden tanımlanabilir metadata ve anahtar kötüye kullanımı |
| 2 — opt-in API pilotu | VDS'de yalnız türetilmiş JSON kabulü; auth, rate-limit, saklama/silme, gözlemlenebilirlik | test fixture ile schema ve privacy doğrulaması; kullanıcı kapatınca sıfır çağrı | kötüye kullanım, auth, maliyet, kişisel veri yükümlülükleri |
| 3 — dağıtım sitesi | Pages üzerinde statik sürüm/kurulum/indirilebilir paket; API aynı origin varsayılmaz | HTTPS, checksum/signature, CORS ve release provenance | supply chain, tarayıcıda saklanan sırlar, yanlış release |

**Önerilen ilk adım:** mevcut Python motorunu koruyup temiz Windows kurulumunu/portable dağıtımı ölçmek; ondan sonra kullanıcı onaylı ve ham içerik almayan telemetry tasarımını prototiplemek. VDS OCR veya otomatik SRT/video yüklemesi bu mimari için gerekli değildir.

## Birincil kaynaklar

- [W3C WebGPU specification](https://www.w3.org/TR/webgpu/) — güvenli bağlam ve adapter seçimi.
- [MDN File API](https://developer.mozilla.org/en-US/docs/Web/API/File_API/Using_files_from_web_applications) ve [MDN WebCodecs API](https://developer.mozilla.org/en-US/docs/Web/API/WebCodecs_API) — browser file/media API sınırları.
- [ONNX Runtime Web](https://onnxruntime.ai/docs/tutorials/web/), [execution provider/operator desteği](https://onnxruntime.ai/docs/execution-providers/), [WebGPU](https://onnxruntime.ai/docs/tutorials/web/ep-webgpu.html) ve [deployment](https://onnxruntime.ai/docs/tutorials/web/deploy.html) — browser inference seçenekleri ve dağıtım.
- [FFmpeg.wasm performance](https://ffmpegwasm.netlify.app/docs/performance/) ve [FAQ](https://ffmpegwasm.netlify.app/docs/faq/) — tarayıcı içi decode/işleme maliyetleri.
- [GitHub Pages: What is GitHub Pages?](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages) — statik yayın sınırları.
