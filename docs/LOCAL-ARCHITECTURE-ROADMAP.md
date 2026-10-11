# Yerel istemci, API sınırı ve mimari yol haritası

Bu belge mevcut yerel istemciyi, isteğe bağlı katkı API prototipini ve dağıtım öncesi kalan işleri ayırır. VDS API kodu vardır; **VDS'ye dağıtılmamıştır**. Telemetry varsayılan kapalıdır. GitHub Pages OCR çalıştırmaz ve bu dalda Pages sitesi/merkezi hesap uygulanmamıştır.

## Bugünkü çalışma şekli

- `hardsub2srt.py` Python CLI'si video meta verisi/kare çıkarımı için `ffprobe`/`ffmpeg`, kare işleme için OpenCV/NumPy, OCR için EasyOCR/PyTorch ve isteğe bağlı RapidOCR/ONNX Runtime yollarını kullanır.
- `--cpu` açıkça seçilmedikçe araç CUDA destekli PyTorch erişilebiliyorsa GPU kullanmayı dener; yoksa CPU'ya düşer. Kullanılan aygıt koşum istatistiğine yazılır. GPU varsayılanı ürün sözleşmesidir.
- `ui_server.py` Flask'ı `127.0.0.1:8765` üzerinde açar. Sıralı işçi thread'i her video için yerel `hardsub2srt.py` alt süreci başlatır. Windows native dosya/klasör seçimi PowerShell STA alt sürecinde yapılır; medya tarayıcıya yüklenmez.
- Kuyruk bellektedir: tek API isteği en fazla 100 girdi; UI bu boyutta parçalara böler; toplam bekleyen+çalışan tavanı 2.000. `/api/durum` son 100 kayıtla birlikte çalışan işi ayrıca verir. Sunucu yeniden başlatılırsa kuyruk kalıcılaştırılmaz.
- Son koşuların yalnızca tanılama özeti ayrıca yerel durum klasöründe tutulur: Windows `%LOCALAPPDATA%\hardsub2srt\job-history.json`, Linux/macOS `$XDG_STATE_HOME/hardsub2srt/job-history.json` (XDG ayarlı değilse `~/.local/state`). UNC/ağ sürücüsü, OneDrive kökü veya reparse/junction yolu seçilmişse yerel varsayılan dizine düşer; güvenli yerel varsayılan da yoksa kalıcı tanılama kapanır. macOS özel XDG yolu için yerel mount bilgisi doğrulanmalı; `mount` sorgusu başarısızsa varsayılan yerel dizine düşer. CloudStorage/iCloud/Dropbox/Google Drive/Box konumları eşitlenen dizin kabul edilip reddedilir. `GET /api/is-gecmisi` en son 20 kaydı döndürür (`?limit=1..50`). En fazla 100 iş / 30 gün / 64 KiB saklanır. Yalnız iş kimliği, sabit durum/aşama/hata kodu, CPU/GPU tercihi, zaman, süre ve varsa süreç çıkış kodu yazılır; kuyruk yeniden başlatma sonrası geri yüklenmez.
- Tanılama kaydı en iyi çaba ile yazılır: bozuk, desteklenmeyen veya aşırı büyük dosya yok sayılır; yazma sorunu OCR işini durdurmaz. Eski `LOG` ve `ISLER` davranışı canlı oturum için aynen sürer. SRT, kare/kırpım, video veya çıktı içeriği, video adı/yolu, çıktı yolu, komut, kimlik bilgisi, süreç çıktısı ve traceback kalıcı kayda girmez. Bu özellik yalnız eklendikten sonraki koşulara kanıt sağlar; önceki boş `_is-24` koşusu geriye dönük açıklanamaz.
- Her kayıt yalnız oturuma özgü `id`, o oturumdaki kuyruk kimliği `queue_id`, `status`, `phase`, `mode`, `started_at`, `updated_at`, `duration_seconds`, `return_code` ve sabit `error_code` alanlarını içerir. Hata kodları `setup_failed`, `process_start_failed`, `worker_failed`, `child_nonzero`, `missing_srt`, `cancelled` ve yeniden başlatmada `server_restart` değerleriyle sınırlıdır. Geçmiş, kuyruk kartlarının yerine geçmez; kullanıcı `GET /api/is-gecmisi` ile son işleri görebilir.
- Windows'ta OCR alt süreci `CREATE_SUSPENDED` ile başlar; `KILL_ON_JOB_CLOSE` bayraklı Job Object'e atanıp ilk thread güvenle devam ettirildikten sonra OCR çalışabilir. Sunucu beklenmedik kapanırsa atanmış çocuk süreç ağacı Windows tarafından bitirilir; atama veya thread'i devam ettirme başarısızsa süreç sonlandırılıp beklenir ve OCR çalıştırılmaz. **Kalan dar sınır:** Windows süreci oluşturduktan sonra fakat Job Object atamasından önce Flask kapanırsa, OCR kodu hiç çalışmamış askıda bir çocuk süreç kalabilir. Bu, ikinci OCR'un GPU'da çakışmasını engeller ancak askıda süreç için mutlak kapanış garantisi vermez. Normal çıkışta süreç bekletilir; Job Object handle kapanışı hata verirse yeniden denenir ve yeni kuyruk işi handle kapanışı doğrulanana kadar başlamaz.
- Her UI işi seçilen çıktı klasörü altında çakışmayan yeni `runs/video-<ad>/<timestamp>_<id>/` yoluna yazılır. CLI açık `-o` hedefini kullanır.
- Araç stats/run metadata, altyazı, istenirse QA görüntüleri, ASS veya referans VTT kıyas raporu üretebilir. Bazı metadata yerel video adı/mtime/boyut/kısmi hash içerir. Bu veriler kullanıcı dosyalarının kendisi değildir, fakat paylaşılmadan önce gözden geçirilmelidir.
- İsteğe bağlı türetilmiş metrik sözleşmesi `schemas/contribution-metrics-v1.schema.json`, istemci allowlist'i `contribution_client.py`, ayrı Flask WSGI alıcısı `contribution_api.py` içindedir. UI `/katki` sayfası gerçek HTTPS hedefini, retention süresini ve token-hash üzerinden gönderimler arası ilişkilendirilebilirliği açıklar; gönderim ayrı onay gerektirir ve son 100 silme kimliğini yalnız tarayıcıda saklar. API'nin 404 silme yanıtı eksik kayıt ile token uyuşmazlığını ayırmadığından UI bu durumda yerel kimliği korur ve yerel liste temizliğini ayrı işlem yapar. `H2S_CONTRIB_URL` (HTTPS) veya token yoksa gönderim kapalıdır. Uzak sunucuya payload gönderimi bu geliştirmede yapılmadı.

## Önerilen dağıtım sınırı

GitHub Pages yalnız statik HTML/CSS/JavaScript dosyaları sunar; Python/FFmpeg/PyTorch alt süreci çalıştıran bir sunucu değildir. VDS API kullanıcının PC'sindeki GPU'ya erişemez. Bu nedenle ilk üretim tasarımı üç rolü ayırmalıdır:

1. **Pages — statik sunum ve dokümantasyon.** Sürüm, indirilebilir imzalı paket, kurulum ve katkı onayı akışını gösterir. API anahtarı veya video içeriği Pages içine gömülmez.
2. **Yerel worker — işleme.** Mevcut Python motoru yerel kurulum/portable paket içinde kalır; GPU seçimi ve `--cpu` seçeneği korunur. Browser UI ile bağ için localhost üzerinde dar yetkili bir yerel servis veya mevcut Flask UI kullanılabilir. CORS, Origin kontrolü, CSRF benzeri yerel istek riski ve sadece loopback bind gözden geçirilmelidir.
3. **VDS API — yalnız opsiyonel katkı alımı.** Kimlik doğrulamalı, rate limitli bir endpoint kullanıcı onayından sonra türetilmiş ve asgariye indirilmiş ölçüm alabilir. İş kuyruğu, sürüm uyumluluğu, veri saklama/silme politikası ve reddedilen girdilerin davranışı API tasarımının parçası olmalıdır.

## Telemetry paylaşım tasarımı (kod prototipi hazır; VDS yayını bekliyor)

İlk paylaşım biçimi doğrudan otomatik yükleme yerine şu özellikleri olan kullanıcı tarafından başlatılan bir “önizle, dışa aktar, onayla, gönder” akışı olmalı:

- **Kapsam görünür:** v1 yalnız gönderim başına UUID/zamanı, toplam süre, taranan kare/cue/düşük güven sayıları ve oranı, `cuda`/`cpu`/`unknown` aygıt sınıfı, `easyocr`/`rapidocr`/`unknown` OCR motor sınıfı, konuşma süresi ve şema sürümünü alır. Kullanılamayan alan uydurulmaz; video adı, model sürümü, OS, OCR parametreleri ve aşama süreleri şu an gönderilmez.
- **İçerik hariç:** video, kare, ham SRT/VTT, replik veya kelime listesi, mutlak dosya yolu, kullanıcı adı, video hash'i ve rastgele tanımlanabilir tam video adı gönderilmez. CER ancak kullanıcının kendi güvenilir referansı yerelde seçilip, yalnız sayısal özet üretilerek ve bu alan ayrıca onaylanarak eklenebilir.
- **Açık rıza:** `/katki` önizlemesi lokal çalışır; gönderim ayrı checkbox + düğme ile yapılır. Endpoint/token yokken sıfır istek yapılır. Saklama politikası ve token-hash linkability onaydan önce gösterilir; gerçek gün sayısı işletmeci tarafından `H2S_CONTRIB_RETENTION_DAYS` ile seçilmelidir. Bu değer boş/placeholder ise API başlamaz. UI, en fazla 100 silme UUID'sini hedef hostname ile localStorage'da tutar; payload ve bearer token kaydedilmez.
- **Güvenlik:** HTTPS, kısa ömürlü/token başına scope, replay/idempotency anahtarı, boyut ve oran sınırı, JSON şema doğrulaması, kötü amaçlı içerik kontrolü, gizli anahtarı statik sitede tutmama. CORS kimlik doğrulama yerine geçmez.
- **MVP veri yolu:** sürümlü tam allowlist JSON, önizleme, açık gönderim, HTTPS-only/redirectsiz istemci ve SQLite'ye yalnız izinli alanları kaydeden ayrı Flask WSGI API kodlandı. API bearer token özetlerini saklar, 16 KiB/rate sınırı uygular, idempotency key/replay dedupe yapar, aynı token'a scoped silme sağlar ve retention purge CLI sunar. VDS/domain/TLS/servis hesabı/kalıcı disk/token dağıtımı yapılmadı; endpoint yayımlanmadı.

## Aşamalar ve riskler

| Aşama | Kapsam/durum | Başarı ölçütü | Kalan risk/iş |
|---|---|---|---|
| 0 — istemciyi dağıtılabilir yap | Python/CUDA/FFmpeg kurulum matrisini belgelemek; bağımlılıkları kilitlemek/işletim sistemi bazında kurulum doğrulamak; portable/installer denemesi | temiz Windows VM'de CPU; destekli makinede CUDA; kullanıcı videosu çıkışını koruma | PyTorch/CUDA paket boyutu, driver uyumu, model indirme ve lisans koşulları |
| 1 — sürüm/feedback şeması | **Yerel prototip kodlandı:** strict v1 schema, allowlist/redaction, preview ve açık onay; CER varsayılan dışarıda ve ayrı consent gerektirir | Unknown fields/raw content reddi; env yokken UI send disabled; preview ağsız | Uçtan uca tarayıcı kabul testi ve privacy review henüz yapılmadı |
| 2 — opt-in API pilotu | **API kodu hazır, pilot dağıtımı yapılmadı:** bearer token hash, rate/size limits, replay dedupe, token-scoped delete, explicit retention purge, loopback WSGI app | VDS'de TLS, schema, deletion, retention ve privacy ölçümleri | Domain/TLS, Windows service account, DB backup/ACL, retention karar ve deployment gerekli |
| 3 — dağıtım sitesi | **Uygulanmadı.** Bu dal API/yerel client prototipiyle sınırlı | Pages release/setup ve paket provenance | Ayrı website/release işi gerekli; statik site secret taşıyamaz |

**Sıradaki adım:** VDS işletim sistemi ve DNS/TLS erişimini doğrulayın; retention süresini açıkça kararlaştırın; sonra Windows servis hesabı, kalıcı SQLite yolu, Caddy/IIS TLS proxy, firewall, token ve scheduled purge kurun. Bu bilgiler ve dış erişim olmadan canlı deploy/payload gönderimi yapılmaz. VDS OCR veya otomatik SRT/video yüklemesi bu mimari için gerekli değildir.

## Birincil kaynaklar

- [W3C WebGPU specification](https://www.w3.org/TR/webgpu/) — güvenli bağlam ve adapter seçimi.
- [MDN File API](https://developer.mozilla.org/en-US/docs/Web/API/File_API/Using_files_from_web_applications) ve [MDN WebCodecs API](https://developer.mozilla.org/en-US/docs/Web/API/WebCodecs_API) — browser file/media API sınırları.
- [ONNX Runtime Web](https://onnxruntime.ai/docs/tutorials/web/), [execution provider/operator desteği](https://onnxruntime.ai/docs/execution-providers/), [WebGPU](https://onnxruntime.ai/docs/tutorials/web/ep-webgpu.html) ve [deployment](https://onnxruntime.ai/docs/tutorials/web/deploy.html) — browser inference seçenekleri ve dağıtım.
- [FFmpeg.wasm performance](https://ffmpegwasm.netlify.app/docs/performance/) ve [FAQ](https://ffmpegwasm.netlify.app/docs/faq/) — tarayıcı içi decode/işleme maliyetleri.
- [GitHub Pages: What is GitHub Pages?](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages) — statik yayın sınırları.
