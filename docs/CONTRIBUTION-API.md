# İsteğe bağlı ölçüm katkısı

## Durum

Yerel istemci ve Flask WSGI API kodu hazırdır; **VDS'ye dağıtılmamıştır ve uzak veri gönderilmemiştir**. API yalnız sayısal OCR koşum özetlerini kabul eder. OCR, video, SRT/VTT, crop veya AI sağlayıcı çağrısı bu serviste çalışmaz. GitHub Pages bu API'yi barındırmaz.

## Yerel UI akışı

`/katki` sayfası tamamlanmış mevcut oturum işlerinden yalnızca iş kimliklerini listeler. Önizleme oluşturmak ağ isteği yapmaz. Payload, mevcut istatistik sözlüğünden ve yerel çalışma süresinden allowlist ile kurulur. Kullanıcı JSON'u ve `H2S_CONTRIB_RETENTION_DAYS` yapılandırmasından gelen saklama gün sayısını görebilir; ayrı onay kutusu ve **Onayla ve gönder** tıklaması olmadan hiçbir uzak istek yapılmaz. URL/token/retention bilgisi yoksa gönder düğmesi kapalıdır. İstemci gün sayısını gönderimde bildirir, API kendi policy ayarıyla karşılaştırır ve uyuşmazlıkta reddeder. Gönderim başarılı olunca **Gönderdiğim katkıyı sil** düğmesi görünür ve yalnız bu gönderim ID'sini API'ye DELETE eder. URL HTTPS olmalı; istemci redirect izlemez ve yalnız `/v1/contributions` yoluna gönderir.

Gönderilen v1 alanları: gönderim başına UUID, UTC zamanı, süre, taranan kare sayısı, cue sayısı, düşük güven cue sayısı/oranı, OCR aygıt sınıfı (`cuda`/`cpu`/`unknown`), motor sınıfı ve konuşma süresi. Tam video/seri/dosya adı, mutlak yol, SRT/VTT metni, görüntü, hash, kullanıcı adı, IP veya kalıcı cihaz kimliği payload'da bulunmaz. CER UI'de etkin değildir. İleride eklenecekse güvenilir yerel VTT referansı ve ayrı CER onayı olmadan payload oluşturma/gönderme başarısız olur; API de ayrı CER consent başlığı ister.

Sözleşme: [`schemas/contribution-metrics-v1.schema.json`](../schemas/contribution-metrics-v1.schema.json). Şema alanları tam allowlist'tir; hem istemci hem sunucu bilinmeyen üst seviye/metrics alanlarını reddeder.

## Windows VDS kurulumu — canlıya alınmadı

Canlı kurulum için VDS işletim sistemi, DNS adı, TLS sertifikası, güvenlik duvarı ve kalıcı disk konumu doğrulanmalıdır. Bu depo bunları kurmaz. API portu yalnız loopback'te dinler; internet trafiği TLS reverse proxy üzerinden gelir. Uzak istemcinin HTTPS adresi `/` köküne işaret edebilir; istemci yalnız `/v1/contributions` yolunu kullanır.

1. Depoyu API için ayrılmış, yazma erişimi kısıtlı bir klasöre kurun. `py -3 -m pip install -r requirements-contribution-api.txt` çalıştırın.
2. Servis hesabı için ortam değişkenlerini **depo dışından** tanımlayın: `H2S_CONTRIB_DB` mutlak ve yedeklenen SQLite dosya yolu; `H2S_CONTRIB_RETENTION_DAYS` ise işletmeci/hukuk incelemesinden sonra açıkça seçilmiş 1–3650 gün tam sayı olmalıdır. Bu değer yoksa/placeholder ise WSGI import aşamasında servis başlamaz. İstemcide de aynı gün sayısını `/katki` önizlemesinde göstermek için ayarlayın; API request header'ını server policy ile karşılaştırır. Örnek env dosyası değer yerine placeholder içerir; placeholder ile servis çalışmaz. `deploy/hardsub-contrib-service.xml.example` WinSW için örnektir; dosyayı dışarıda kopyalayıp tüm placeholder'ları değiştirin. WinSW servisini dedicated düşük yetkili Windows hesabıyla çalıştırın.
3. WinSW servis tanımında şu uygulama komutu kullanılır: `waitress-serve --listen=127.0.0.1:8787 contribution_api:app`. Waitress access logging kapalı kalmalıdır; `log mode="none"` servis sarmalayıcı çıktısını kapatır. Servis hesabına yalnız DB klasörü için gereken NTFS haklarını verin. İstek gövdesi, token, istemci IP'si ve URL query loglanmamalıdır.
4. Caddy'yi doğrulanmış alan adı/IP ve **bağımsız olarak temin edilip yenilenmesi doğrulanmış, public-trust TLS sertifikasıyla** yapılandırın. `deploy/Caddyfile.example` public certificate/key yollarını açıkça bekler. Caddy'nin bare IP için yerel CA sertifikası tarayıcılar/istemciler tarafından varsayılan güvenilir değildir; Caddy'nin tek başına IP sertifikası sağlayacağını varsaymayın. IP SAN kullanılıyorsa CA/client desteği, kısa sertifika ömrü, otomatik yenileme ve HTTP-01/TLS-ALPN için gereken inbound 80/443 erişimi ayrıca doğrulanmalıdır. Uygun Windows IP sertifika yenileyicisi/domain ve port erişimi doğrulanana kadar dağıtım blokludur. Windows Firewall'da yalnız Caddy'nin HTTPS portlarını açın; 8787 dışarı açmayın.
5. API token'ını `py -3 contribution_api.py issue-token` ile CLI'dan üretin. Ham token yalnız bir kere gösterilir; parola yöneticisine alın ve yerel istemci tarafında `H2S_CONTRIB_TOKEN` ortam değişkenine dışarıdan verin. İstemcinin `H2S_CONTRIB_URL` değeri `https://<gerçek-alan-adı>` biçiminde olmalıdır. Token iptali: `py -3 contribution_api.py revoke-token` (gizli giriş istemi).
6. Task Scheduler ile günde bir kez `py -3 contribution_api.py purge` komutunu, aynı `H2S_CONTRIB_DB` ve retention ortam değişkenleriyle çalıştırın. Bu komut yalnız `received_at` retention eşiğini geçen kayıtları siler. Yedeklerin de aynı saklama/silme politikasına uymasını ve aralıklarla geri yükleme/silme işlemini doğrulayın.
7. TLS, firewall, `/healthz`, token iptali, rate limit, deletion ve retention purge doğrulandıktan sonra ancak kullanıcıların URL/token yapılandırmasını dağıtın. Bu iş dalında hiçbir VDS kurulumu, alan adı/TLS, token secret veya payload gönderimi yapılmadı.

## API güvenlik ve depolama

- `/healthz`: yalnız `status` döndürür; token istemez.
- `POST /v1/contributions`: 16 KiB sınırı, bearer token, JSON şeması, `Idempotency-Key`, açık `X-H2S-Metrics-Consent: v1`; CER varsa ayrıca `X-H2S-CER-Consent: v1`. Tekrarlanan aynı idempotency anahtarı ve aynı payload yeni satır üretmez; farklı payload 409 döner.
- `DELETE /v1/contributions/<submission_id>`: sadece o submission'ı oluşturan aynı bearer token silebilir; diğer token'ların kaydı 404 döner.
- Token'lar DB'de SHA-256 özeti olarak tutulur; ham token issue sırasında bir kez gösterilir. Rate limit token başına 60 istek/saat (tek proses belleğinde), submission ID primary key/replay dedupe sağlar. SQLite yalnız allowlist payload'ını ve alım/consent zamanını saklar.
- Loglar request body, token ve IP içermez; `werkzeug` access logger kapalıdır. Proxy ve servis sarmalayıcı loglarını da privacy-safe ayarlayın. Servis yalnız loopback bind eder.
- `H2S_CONTRIB_RETENTION_DAYS` varsayılanı yoktur. Servisin açılması için açık operatör seçimi şarttır. Silme isteği için API deletion endpoint'i vardır; gönderim yanıtındaki ID'yi kullanıcıya/yerel uygulamaya gösterin.

## İşletmeci yapılandırması

Örnekler: [`deploy/contribution.env.example`](../deploy/contribution.env.example), [`deploy/hardsub-contrib-service.xml.example`](../deploy/hardsub-contrib-service.xml.example) ve [`deploy/Caddyfile.example`](../deploy/Caddyfile.example). Bunlar placeholder'dır; production secret veya domain değildir. Sunucu sadece loopback'e bind eder ve HTTPS sertifikası/proxy olmadan public deployment yapılmamalıdır. Caddy şablonu TLS sertifikasını dışarıdan bekler; bare-IP sertifikasının Caddy tarafından otomatik güvenilir üretildiği iddia edilmez.

## Uygulanmamış

VDS'nin canlı konfigürasyonu ve erişim doğrulaması, DNS/IP seçimi, public TLS sertifika edinme/yenileme, port 80/443 erişimi, servis sarmalayıcı kurulumu, disk yedekleme/erişim, retention süresinin hukuki/ürün kararı, istemci için gerçek token dağıtımı, yayınlama, Windows runtime acceptance ve gerçek uçtan uca gönderme/silme ölçümü bu dalda yapılmadı. API üzerinde AI sağlayıcı entegrasyonu yoktur.
