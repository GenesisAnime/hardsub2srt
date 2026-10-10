# İsteğe bağlı ölçüm katkısı

## Durum

Yerel istemci ve Flask WSGI API kodu hazırdır; **VDS'ye dağıtılmamıştır ve uzak veri gönderilmemiştir**. API yalnız sayısal OCR koşum özetlerini kabul eder. OCR, video, SRT/VTT, crop veya AI sağlayıcı çağrısı bu serviste çalışmaz. GitHub Pages bu API'yi barındırmaz.

## Yerel UI akışı

`/katki` sayfası tamamlanmış mevcut oturum işlerinden yalnızca iş kimliklerini listeler. Önizleme oluşturmak ağ isteği yapmaz. Payload, mevcut istatistik sözlüğünden ve yerel çalışma süresinden allowlist ile kurulur. Kullanıcı JSON'u ve `H2S_CONTRIB_RETENTION_DAYS` yapılandırmasından gelen saklama gün sayısını görebilir; ayrı onay kutusu ve **Onayla ve gönder** tıklaması olmadan hiçbir uzak istek yapılmaz. URL/token/retention bilgisi yoksa gönder düğmesi kapalıdır. İstemci gün sayısını gönderimde bildirir, API kendi policy ayarıyla karşılaştırır ve uyuşmazlıkta reddeder. Gönderim başarılı olunca **Gönderdiğim katkıyı sil** düğmesi görünür ve yalnız bu gönderim ID'sini API'ye DELETE eder. URL HTTPS olmalı; istemci redirect izlemez ve yalnız `/v1/contributions` yoluna gönderir.

Gönderilen v1 alanları: gönderim başına UUID, UTC zamanı, süre, taranan kare sayısı, cue sayısı, düşük güven cue sayısı/oranı, OCR aygıt sınıfı (`cuda`/`cpu`/`unknown`), motor sınıfı ve konuşma süresi. Tam video/seri/dosya adı, mutlak yol, SRT/VTT metni, görüntü, hash, kullanıcı adı, IP veya kalıcı cihaz kimliği payload'da bulunmaz. CER UI'de etkin değildir. İleride eklenecekse güvenilir yerel VTT referansı ve ayrı CER onayı olmadan payload oluşturma/gönderme başarısız olur; API de ayrı CER consent başlığı ister.

Sözleşme: [`schemas/contribution-metrics-v1.schema.json`](../schemas/contribution-metrics-v1.schema.json). Şema alanları tam allowlist'tir; hem istemci hem sunucu bilinmeyen üst seviye/metrics alanlarını reddeder.

## Windows VDS kurulumu — canlıya alınmadı

Canlı kurulum için VDS işletim sistemi, DNS adı, TLS sertifikası, güvenlik duvarı ve kalıcı disk konumu doğrulanmalıdır. Bu depo bunları kurmaz. API portu yalnız loopback'te dinler; internet trafiği TLS reverse proxy üzerinden gelir. Uzak istemcinin HTTPS adresi `/` köküne işaret edebilir; istemci yalnız `/v1/contributions` yolunu kullanır.

1. Depoyu API için ayrılmış, servis hesabına yazma verilmeyen bir uygulama klasörüne kurun. `py -3 -m pip install -r requirements-contribution-api.txt` çalıştırın. `Waitress` uygulama klasörünü yalnız okuyup çalıştırabilmelidir; `LocalSystem` ile çalıştırmayın.
2. Saklama süresini ürün/hukuk incelemesinden sonra açıkça seçin. `H2S_CONTRIB_RETENTION_DAYS` 1–3650 arası tam sayı olmalıdır; `H2S_CONTRIB_DB` mutlak SQLite dosya yolu olmalı ve repo/kaynak ağacının dışında bulunmalıdır. API startup sırasında Windows ACL/owner ve process identity doğrular; SID/ACL okunamaz, data dizini yok, owner yanlış, geniş principal/izin var veya uygulama hesabı eşleşmiyorsa fail-closed açılmaz. İstemciye aynı gün sayısını verin; UI bunu onaydan önce gösterir ve API farklı değeri reddeder. Placeholder değerler servisi çalıştırmaz.
3. `deploy/hardsub-contrib-service.xml.example` dosyasını WinSW executable'ının yanına aynı adla kopyalayın; Waitress executable, AppRoot, DB/log/retention ve purge account placeholder'larını değiştirin. Servisi elle kurup başlatmayın. WinSW XML'deki uygulama komutu `waitress-serve --listen=127.0.0.1:8787 contribution_api:app` olmalıdır. Yönetici PowerShell'de aşağıdaki komut WinSW servisini kurar, **stopped/disabled** haldeyken LocalSystem'den dedicated service account'a geçirir, SID'yi bu adımdan sonra çözümler ve ACL'leri kurar. Betik sonunda servis disabled kalır; başarısız olursa servis başlamaz:

   ```powershell
   $retentionDays = [int](Read-Host 'Retention gün sayısı (ürün/hukuk kararı)')
   .\deploy\configure_contribution_windows.ps1 `
     -WinSWExe 'C:\Services\hardsub\app\hardsub-contribution-api.exe' `
     -AppRoot 'C:\Services\hardsub\app' `
     -DataDirectory 'D:\HardsubData\contrib' `
     -LogDirectory 'D:\HardsubData\logs' `
     -CertificateDirectory 'D:\HardsubData\tls' `
     -PurgeAccount 'VDS01\hardsub-purge' `
     -CaddyAccount 'NT SERVICE\Caddy' `
     -RetentionDays $retentionDays
   ```

   Placeholder paths/accounts/gün sayısı canlı değer değildir. Script LocalSystem'i reddeder, servisi `NT SERVICE\hardsub-contribution-api` sanal hizmet hesabına geçirir, stopped/disabled durumunu doğrular ve ACL'leri sıfırlayıp kısıtlar: app root API için RX-only, data/log API için Modify, data purge için Modify, cert/key Caddy için RX-only, SYSTEM/Administrators FullControl. Data dizininin sahibi API service SID olur; DB dosyası API veya ayrı purge SID sahibi olabilir. Purge hesabı yalnız yerel `BUILTIN\Users` üyesi olabilir ve Administrators dahil başka bir local group'a üyelik reddedilir. Script başarısız olursa servisi başlatmayın.
4. `deploy/register_contribution_purge_task.ps1` ile her gün 03:15 için Scheduled Task kaydedin; task ayrı, non-admin `PurgeAccount` ile S4U çalışır. Hesaba yerel dosya erişimi ve `Log on as a batch job` hakkı verilmeli; Task Scheduler bunu kuramazsa deploy bloklu kalır. Örnek:

   ```powershell
   $retentionDays = [int](Read-Host 'API ile aynı retention gün sayısı')
   .\deploy\register_contribution_purge_task.ps1 `
     -TaskName 'hardsub2srt-retention-purge' `
     -PurgeAccount 'VDS01\hardsub-purge' `
     -PythonExe 'C:\Python312\python.exe' `
     -AppRoot 'C:\Services\hardsub\app' `
     -WinSWXml 'C:\Services\hardsub\hardsub-contribution-api.exe.xml' `
     -DatabasePath 'D:\HardsubData\contrib\contributions.sqlite3' `
     -RetentionDays $retentionDays `
     -ServiceAccount 'NT SERVICE\hardsub-contribution-api'
   ```

   Komut `deploy/purge_contributions.ps1` üzerinden aynı retention süresiyle API'nin `purge` komutunu çağırır. Bu scheduled task gerçek Windows kimlik/ACL kabul testi yapılmadan canlı sayılmaz.
5. Caddy'yi doğrulanmış alan adı/IP ve **bağımsız olarak temin edilip yenilenmesi doğrulanmış, public-trust TLS sertifikasıyla** yapılandırın. `deploy/Caddyfile.example` public certificate/key yollarını açıkça bekler. Caddy'nin bare IP için yerel CA sertifikası tarayıcılar/istemciler tarafından varsayılan güvenilir değildir; Caddy'nin tek başına IP sertifikası sağlayacağını varsaymayın. IP SAN kullanılıyorsa CA/client desteği, kısa sertifika ömrü, otomatik yenileme ve HTTP-01/TLS-ALPN için gereken inbound 80/443 erişimi ayrıca doğrulanmalıdır. Uygun Windows IP sertifika yenileyicisi/domain ve port erişimi doğrulanana kadar dağıtım blokludur. Script sertifika klasöründe yalnız Caddy hesabına Read/Execute verir. Windows Firewall'da yalnız Caddy'nin HTTPS portlarını açın; 8787 dışarı açmayın.
6. API token'ını `py -3 contribution_api.py issue-token` ile CLI'dan üretin. Ham token yalnız bir kere gösterilir; parola yöneticisine alın ve yerel istemci tarafında `H2S_CONTRIB_TOKEN` ortam değişkenine dışarıdan verin. İstemcinin `H2S_CONTRIB_URL` değeri `https://<gerçek-alan-adı>` biçiminde olmalıdır. Token iptali: `py -3 contribution_api.py revoke-token` (gizli giriş istemi).
7. `/healthz`, firewall, TLS renewal, token iptali, rate limit, deletion, Windows task identity, ACL ve purge doğrulandıktan sonra, ve ancak ondan sonra servisi etkinleştirip başlatın: `sc.exe config hardsub-contribution-api start= demand` ardından `Start-Service hardsub-contribution-api`. `/healthz` ve privacy kontrolleri geçince kullanıcıların URL/token yapılandırmasını dağıtın. Bu dalda hiçbir VDS kurulumu, domain/TLS, secret veya payload gönderimi yapılmadı.

## API güvenlik ve depolama

- `/healthz`: yalnız `status` döndürür; token istemez.
- `POST /v1/contributions`: 16 KiB sınırı, bearer token, JSON şeması, `Idempotency-Key`, açık `X-H2S-Metrics-Consent: v1`; CER varsa ayrıca `X-H2S-CER-Consent: v1`. Tekrarlanan aynı idempotency anahtarı ve aynı payload yeni satır üretmez; farklı payload 409 döner.
- `DELETE /v1/contributions/<submission_id>`: sadece o submission'ı oluşturan aynı bearer token silebilir; diğer token'ların kaydı 404 döner.
- Token'lar DB'de SHA-256 özeti olarak tutulur; ham token issue sırasında bir kez gösterilir. Rate limit token başına 60 istek/saat (tek proses belleğinde), submission ID primary key/replay dedupe sağlar. SQLite yalnız allowlist payload'ını ve alım/consent zamanını saklar.
- Loglar request body, token ve IP içermez; `werkzeug` access logger kapalıdır. Proxy ve servis sarmalayıcı loglarını da privacy-safe ayarlayın. Servis yalnız loopback bind eder.
- `H2S_CONTRIB_RETENTION_DAYS` varsayılanı yoktur. Servisin açılması için açık operatör seçimi şarttır. Silme isteği için API deletion endpoint'i vardır; gönderim yanıtındaki ID'yi kullanıcıya/yerel uygulamaya gösterin.
- SQLite WAL modu kullanılır ve her writer connection `secure_delete=ON` açar. Kullanıcı silme isteği ana tablodaki kaydı hemen siler; WAL/checkpoint ve disk blokları anında forensic olarak silinmiş sayılmaz. Günlük retention task'i expired rows'ları siler, `wal_checkpoint(TRUNCATE)` ve `VACUUM` yapar. Bu mantıksal DB/WAL temizliğidir, depolama aygıtı üzerinde garantili güvenli overwrite değildir. SQLite backup'ları, shadow copies ve volume snapshots aynı retention süresine göre ayrı temizlenmelidir; canlı DB kopyasını tek başına kopyalamayın, SQLite online backup API kullanın veya tutarlı yedek için servisi durdurun ve DB+WAL'ı birlikte alın.

## İşletmeci yapılandırması

Örnekler: [`deploy/contribution.env.example`](../deploy/contribution.env.example), [`deploy/hardsub-contrib-service.xml.example`](../deploy/hardsub-contrib-service.xml.example), [`deploy/configure_contribution_windows.ps1`](../deploy/configure_contribution_windows.ps1), [`deploy/register_contribution_purge_task.ps1`](../deploy/register_contribution_purge_task.ps1), [`deploy/purge_contributions.ps1`](../deploy/purge_contributions.ps1) ve [`deploy/Caddyfile.example`](../deploy/Caddyfile.example). Bunlar placeholder'dır; production secret veya domain değildir. Sunucu sadece loopback'e bind eder ve HTTPS sertifikası/proxy olmadan public deployment yapılmamalıdır. Caddy şablonu TLS sertifikasını dışarıdan bekler; bare-IP sertifikasının Caddy tarafından otomatik güvenilir üretildiği iddia edilmez.

## Uygulanmamış

VDS'nin canlı konfigürasyonu ve erişim doğrulaması, DNS/IP seçimi, public TLS sertifika edinme/yenileme, port 80/443 erişimi, servis sarmalayıcı kurulumu, disk yedekleme/erişim, retention süresinin hukuki/ürün kararı, istemci için gerçek token dağıtımı, yayınlama, Windows runtime acceptance ve gerçek uçtan uca gönderme/silme ölçümü bu dalda yapılmadı. API üzerinde AI sağlayıcı entegrasyonu yoktur.
