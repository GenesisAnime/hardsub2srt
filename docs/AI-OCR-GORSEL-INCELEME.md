# Manuel AI OCR görsel incelemesi

Bu akış, OCR'ın ilk ürettiği SRT metnini henüz insan onayı yokken seçili kırpımlarla birlikte GPT, DeepSeek veya başka bir görsel okuyabilen AI'ya **elle** inceletmek içindir. Uygulama AI sağlayıcısına bağlanmaz ve hiçbir içeriği ağ üzerinden göndermez. Kullanıcı indirilen ZIP'i kendi seçtiği sohbete ekler; AI yanıtını yerel arayüze kullanıcı yapıştırır.

## Kullanım

1. Yerel OCR inceleme ekranında `.review-pack` paketini açın. Her altyazı kartındaki **AI görsel incelemesine dahil et** kutusu, insan incelemesi beklemeyen geçerli kırpımlarda bulunur.
2. En fazla 100 cue seçip **Seçili görsellerle AI inceleme ZIP'i indir** düğmesine basın. ZIP `prompt.md`, `request.json`, `response-template.json` ve yalnızca seçilen `images/<cue-id>.jpg` kırpımlarını içerir.
3. Bu dosyaları AI sohbetine kendiniz ekleyin. Kırpım ve OCR metninin harici sağlayıcıya gönderilmesi kullanıcının açık eylemidir; sağlayıcının saklama politikasını ayrıca değerlendirin.
4. AI'dan tamamlanmış response template JSON'unu alın ve yerel sayfaya yapıştırın. İçe aktarma yalnızca ayrı `ai-ocr-review/proposals.jsonl` öneri günlüğüne yazar.
5. Kırpımı ve öneriyi inceleyin. OCR metnini kabul etmek/düzeltmek için karttaki normal **Görüntü doğru** veya **Düzeltmeyi kaydet** düğmesine basmanız gerekir. Bu insan kararı `review-events.jsonl` akışına ayrı yazılır. AI yanıtı tek başına hiçbir OCR dataset export'una giremez. Çeviri AI akışı da kaynak metin insan tarafından doğrulanana kadar kapalı kalır.

## Veri sözleşmesi

İstek cue başına yalnızca cue ID, sabit başlangıç/bitiş zamanı (ms), SRT'de karşılığı olan `source_text_ocr`, OCR confidence, JPEG kırpım dosya adı ve görüntüyü taşır. Mutlak kaynak yolu, cihaz/kullanıcı hesabı kimliği, tam video, manifest/SRT hash'leri veya yerel yan kayıt ZIP'e konmaz. Prompt; yalnız altyazı metninin okunmasını, sahne yazısı/jenerik/logo metninin altyazıya karıştırılmamasını ve belirsizliğin açıkça işaretlenmesini söyler.

Yanıt şeması `schemas/ai-ocr-visual-review-v1.schema.json` dosyasındadır. Her satır aynı `cue_id`, `start_ms`, `end_ms` değerlerini ve şu alanları taşır: `status` (`confirmed`, `correction_proposed`, `uncertain`), `observed_text`, `proposed_text`, `reason`. Cue/timing değişikliği, yinelenen ID, eksik satır, bilinmeyen alan, geçersiz durum, bayat paket/SRT/kırpım veya hatalı JSON reddedilir. Aynı yanıtın yeniden içe aktarılması yeni kayıt üretmez. Farklı bir yanıt revizyonu ayrı öneri olarak kaydedilebilir.

Her paketin yerel `ai-ocr-review/` klasöründe request ID, paket/SRT/kırpım hash eşlemesi ve 0600 kipli (desteklenen sistemlerde) `.local.json` yan kayıt bulunur; yan kayıt ZIP'e alınmaz. Öneriler append-only `proposals.jsonl` içinde tutulur. Kayıtlar `schema_version: 1`, `event_type: proposal`, rastgele event ID, istek/yanıt hash'i, cue/zaman, durum, öneri ve timestamp taşır. Bozuk/şema dışı günlükte yeni yazım durur; kısmi günlükten etiket üretilmez. AI öneri günlüğü OCR insan karar günlüğünü, dataset manifestini ve çeviri belleğini değiştirmez.

## Zaman ve doğruluk sınırı

Kırpım, review-pack'in OCR segmentiyle ilişkilendirilir. Review-pack oluşturucu kaynak video PTS bilgisini korumuyorsa kare/FPS üzerinden hesaplanan zaman yaklaşık hizalamadır; AI incelemesi zaman referansını kesinleştirmez ve zamanları değiştiremez. AI'nın `confirmed` demesi bile insan onayı değildir. Bu süreç görsel OCR karşılaştırması içindir; AI çeviri/semantik değerlendirmesi ayrı manuel akışta yapılır.

## Kapasite ve geri alma

- En fazla 100 cue ve sıkıştırılmış ZIP için 12 MiB sınırı vardır; cevap en çok 4 MiB'dir. Kırpımlar mevcut review-pack boyut/decode/hash kontrollerinden tekrar geçer.
- Kayıtlar yalnız yerel review-pack altında tutulur. Öneri geçmişini UI veya dış servise otomatik gönderme/senkronlama yoktur.
- AI önerilerini kabul etmek için ayrı bir düğme yoktur. Yanlış/istenmeyen AI proposal satırları geçmişte kalır; etkisiz bırakmak için yalnız normal insan OCR kararını verin. Yerel öneri geçmişini kaldırmak gerekirse önce `.review-pack` yedeği alın; sonra yalnız `ai-ocr-review/` klasörünü elle silin. Bu işlem insan karar günlüğüne dokunmaz.

## Sentetik doğrulama

Testlerde sahte JPEG ve sahte altyazı metni kullanılır; gerçek video/OCR veya sağlayıcı isteği yoktur. Bu testler uygulama bütünlüğünü doğrular, AI'nın OCR doğruluğunu ya da zaman hizalamasını ölçmez.
