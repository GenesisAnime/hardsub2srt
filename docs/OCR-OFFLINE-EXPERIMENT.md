# Phase 5 — yerel OCR deney hazırlığı ve değerlendirme sözleşmesi

## Bu aşamada ne var

`ocr_experiment.py`, Phase 2'nin `verified-ocr-dataset-*/manifest.jsonl` dışa aktarımını ve ona karşılık gelen ayrı metadata dosyasını yerelde doğrular. Geçerli girdiler için sonuçlardan önce `evaluation-plan-v1.json`, ardından hash-bağlı `split-manifest.json` ve `experiment-report.json` oluşturur. Araç OCR çalıştırmaz, model eğitmez, veri kümesini değiştirmez ve ağa bağlanmaz. Metadata, plan, split ve rapor çıktıları hassas yerel dosyalardır; paylaşılabilir paket sayılmaz.

Phase 2 dışa aktarımı yalnızca geçerli `accepted`/`corrected` kararlarını hedefler. Araç, export klasörünün doğrudan üst dizinindeki `.review-pack` ile `manifest.json`, SRT ve `review-events.jsonl` zincirini tekrar doğrular; event'in aktif son karar olduğunu, event/cue/hash/metin/zaman/confidence eşleşmelerini kontrol eder. Zincir doğrulanamazsa çıktıdaki provenance `self_attested` olur ve `experiment_run_blocked` true kalır. Başarılı yerel bağlantı da hesabın/kişinin kimliğini kanıtlamaz; yerel UI journal'ına bağlı olduğunu gösterir. AI önerileri export'a alınmaz.

### Birden fazla review-pack export'unu birleştirme

UI her `.review-pack` için ayrı bir verified export üretir. Phase 5 split'i en az üç bağımsız source+episode grubu istediği için bu export'ları satır/crop kopyalayarak elle birleştirmeyin. `ocr_dataset_merge.py` doğrulanmış export'ları, her cue'yu özgün export'a ve etkin review event'ine bağlayan `source-lineage-v1.json` ile bir araya getirir. Birleşik klasör yalnız export manifest satırlarını ve doğrulanmış crop baytlarını kopyalar; kaynak review pack/export'larına yazmaz.

Ortak kök mevcut ve yerel olmalı; kaynak review-pack'ler ile birleşik export bunun altında bulunur. Kaynak yolları ortak köke göreli verilir. Çıktı da ortak kökte yeni bir alt klasördür:

```powershell
py -3 ocr_dataset_merge.py `
  --root "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root" `
  --source-export "run-01\synthetic.review-pack\verified-ocr-dataset-PLACEHOLDER" `
  --source-export "run-02\synthetic.review-pack\verified-ocr-dataset-PLACEHOLDER" `
  --source-export "run-03\synthetic.review-pack\verified-ocr-dataset-PLACEHOLDER" `
  --out-name "verified-ocr-dataset-composite-SYNTHETIC"
```

Yollar sentetik örnektir. Her kaynak pack'teki tüm cue'lar metadata'da tek gerçek source+episode grubuna, farklı pack'ler ise farklı gerçek gruplara bağlanmalıdır. Cue çakışması, yinelenen pack, kaynak export hash'i, etkin karar veya crop uyuşmazlığı, kök dışına çıkan/reparse path ya da toplam boyut sınırı merge işlemini durdurur. `source-lineage-v1.json` kaynak export manifestini ve üst review manifesti, SRT ve event journal hash'lerini birbirine bağlar; validator bunları her hazırlık/değerlendirme çağrısında yeniden kontrol eder. Metadata, hak kanıtı ve insan hak incelemesi de gerekir; merge bunları üretmez veya onaylamaz.

Birleşik export için boş metadata şablonunu CLI ile oluşturun; review UI şablon düğmesi tek açık review-pack içindeki export'a bağlıdır:

```powershell
py -3 ocr_dataset_metadata.py `
  "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\verified-ocr-dataset-composite-SYNTHETIC" `
  --out "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\dataset-metadata.template.json"
```

Şablondaki kimlik, lisans ve hak incelemesi alanları boş/pending'dir. Bunları gerçek materyal ve incelenmiş kanıta göre doldurmak kullanıcı sorumluluğudur; şablon, evidence file ya da `human_review` işareti hukuki kullanım hakkı vermez.

## Metadata girdisi

### Boş metadata şablonu oluşturma

İnsan tarafından kabul edilmiş/düzeltilmiş Phase 2 kararlarından sonra, kaynak
ve bölüm kimliklerini veya hak durumunu hatırlamıyorsanız yalnızca başlangıç
şablonu üretin:

```powershell
py -3 ocr_dataset_metadata.py "D:\yerel\review-pack\verified-ocr-dataset-..." `
  --out "D:\yerel-metadata\dataset-metadata.template.json"
```

`--out` klasörü önceden var olmalı; hedef JSON daha önce bulunmamalı ve
doğrulanmış veri kümesinin dışında olmalıdır. Araç Phase 2 manifest şemasını,
cue ID'lerini, crop hash/decode bütünlüğünü ve üst review-pack'teki etkin insan
kararlarına bağlantıyı kontrol eder. Bağlantı doğrulanamazsa dosya üretmez.
Şablon yalnız cue ID'lerini taşır; SRT/kaynak metni ve mutlak yolları içermez.

Üretilen dosyada `source_id`, `episode_id`, lisans tanımı ve hak kanıtı alanları
boştur; `human_review.status` `pending` kalır. Hiçbir kimlik, lisans veya
kullanım hakkı tahmin edilmez. Dosya bilerek deney doğrulayıcısının zorunlu
alanlarını karşılamaz; doldurulup insan tarafından incelenmeden Phase 5
hazırlığına verilemez. Dosyanın veya kanıt ekinin varlığı hukuki izin kanıtı
sayılmaz. Bu komut bir kolaylık aracıdır, veri kümesi veya izin onayı değildir.

Her `cue_id` için `source_id`, `episode_id` ve lisans alanı zorunludur. Lisans durumu yalnız `public_domain`, `permissive_license`, `permission_granted` veya `user_owned` değerlerinden biri olabilir. Her kayıtta metadata dosyasına göreli `evidence_path` ve SHA-256 gerekir; dosyanın varlığı ve hash'i kontrol edilir. Ayrıca `human_review` alanı gözden geçiren kişi/tarihle işaretlenebilir. Ancak kanıt dosyasının varlığı veya bu alan **hukuki doğrulama değildir**; sistem raporu daima `rights_verified:false` yazar. İnsan hak incelemesi tamamlanmadan deney çalıştırma kapısı kapalı kalır. Lisans belirsizse durun.

Örnek `dataset-metadata.json`:

```json
{
  "schema_version": 1,
  "records": [
    {
      "cue_id": "cue-id-from-export",
      "source_id": "series-or-source-stable-local-id",
      "episode_id": "episode-stable-local-id",
      "license": {
        "status": "permission_granted",
        "identifier": "permission-record-2026-001",
        "evidence_path": "rights/permission-record-2026-001.txt",
        "evidence_sha256": "<64-lowercase-hex-digest-of-file>",
        "human_review": {
          "status": "pending",
          "reviewer": null,
          "reviewed_at": null
        }
      }
    }
  ]
}
```

## Hazırlama

Önceden sabitlenecek değerler (split tohumu, baseline/aday tanımı, CER gerileme eşiği, zaman toleransı ve **her test source+episode grubu için minimum coverage oranı**) sonuçları görmeden seçilmelidir. Proje varsayılanı veya evrensel yüzde yoktur; oranı kendi desteklenen kapsamınız için önceden belirleyin. `0.90` aşağıdaki komutta yalnız örnek değerdir. Yeni, boş bir `--out` dizini verin:

```powershell
py -3 ocr_experiment.py "D:\yerel\verified-ocr-dataset-..." `
  --metadata "D:\yerel\dataset-metadata.json" `
  --out "D:\yerel\experiment-2026-01" `
  --seed "phase5-v1-frozen" `
  --baseline "current-engine-config-id" `
  --candidate "candidate-engine-config-id" `
  --max-cer-regression 0.002 `
  --min-test-group-coverage 0.90 `
  --timing-tolerance-ms 500
```

Hazırlık, `evaluation-plan-v1.json` dosyasını sonuç ölçümünden önce yazar. Plan
dataset manifesti, metadata, split tohumu, engine kimlikleri, eşiği ve coverage
tanımını dondurur. Split ve `experiment-report.json` planın ham bayt SHA-256
özetini taşır; evaluation aracı plan yoksa, plan değiştirilmişse veya başka bir
experiment klasörünün planı kopyalanmışsa durur. Eşik değiştirmek için eski
dosyaları düzenlemeyin; yeni experiment klasörü ve yeni plan oluşturun. Bu hash
bağı yerel dosyalar arasındaki bütünlük kontrolüdür, dijital imza ya da dışarıdan
doğrulanmış trust pin değildir. Plan dosyasının değiştirilmesini mevcut split ve
rapor hash'lerine göre yakalar; plan, split ve raporun birlikte yeniden yazılıp
hash'lerin tekrar hesaplanmasını engellemez. Güvenilir timestamp, imza veya hak
onayı oluşturmaz.

### Eski Phase 5 hazırlık dosyaları

Coverage planı eklenmeden önce üretilmiş `schema_version=1` `PREPARED_NOT_RUN`
raporlarında plan/split hash alanları ve `evaluation-plan-v1.json` bulunmaz.
Güncel validator bu eski klasörleri bilerek reddeder. Eski dosyaları yerinde
düzenleyerek hash veya threshold eklemeyin; `ocr_experiment.py` komutunu açıkça
seçtiğiniz coverage oranıyla **yeni, boş bir `--out` klasöründe** yeniden
çalıştırın. Eski rapor tarihsel kayıt olarak kalır. Bu sürümde schema id'si v1
korunmuştur, ancak eski hazırlık artifact'ları güncel v1 validator sözleşmesine
uyumlu değildir.

Komut crop dosyalarının göreli ve kökün altında kalan yollar olduğunu, SHA-256 değerlerinin eşleştiğini ve JPEG boyut işaretçilerinin güvenli sınırlar içinde olduğunu denetler; ardından OpenCV ile görüntünün çözülebildiğini doğrular. Tek crop en çok 1280×1280, benzersiz crop'ların toplamı en çok 128 MiB olabilir. Metadata 16 MiB, her hak kanıtı dosyası 16 MiB ve tüm kanıt dosyaları toplamı 64 MiB ile sınırlıdır. Komut yinelenen `cue_id` değerlerini, `ocr_confidence` alanı da dahil yanlış Phase 2 manifest şemasını, eksik/fazla metadata satırlarını, boş insan doğrulamalı metni, eksik lisans kanıtı dosyası/hash'ini ve bozuk crop'u reddeder. `review_event_id` tek başına yeterli değildir: üst `.review-pack` ve `review-events.jsonl` üzerinden hâlâ etkin karara ve aynı cue/crop/hash/SRT/zaman/metin/confidence değerlerine bağlanmalıdır. Bu zincir yoksa provenance `self_attested` kalır ve deney bloklanır. Grup anahtarı, NFC + boşluk birleştirme + casefold uygulamasından sonra `source_id + episode_id` birleşimidir. Aynı gruptaki cue'lar farklı split'lere dağılmaz. En az üç ayrı grup yoksa veya herhangi bir split boş kalacaksa çıktı üretilmez.

Deterministik sıralama `SHA256(seed + NUL + canonical_group_id)` ile yapılır; sıralanmış gruplar sabit 80/10/10 hedefli grup adetlerine ayrılır. Bu, grup sayısına göre bölmedir; örnek/cue sayısına göre tam 80/10/10 garantisi vermez. Kısıtlı grup sayısında oranlar yaklaşık olur; rapor hem grup hem cue satır sayılarını ayrı verir. Yeni veri geldikçe mevcut test gruplarını sessizce değiştirmeyin: deney manifestini dondurun ve yeni bir deney sürümü açın.

## Metrik tanımları ve rapor

Makinece okunur şemalar `schemas/ocr-experiment-metadata-v1.schema.json`, `schemas/ocr-experiment-split-v1.schema.json`, yeni sabitlenmiş plan için `schemas/ocr-evaluation-plan-v1.schema.json`, `schemas/ocr-experiment-report-v1.schema.json`, video raporu için `schemas/ocr-video-evaluation-report-v1.schema.json` ve yalnız crop raporu için `schemas/ocr-crop-evaluation-report-v1.schema.json` dosyalarındadır. Hazırlık sırasında `experiment-report.json` daima `PREPARED_NOT_RUN`, `rights_verified:false`, `experiment_run_blocked:true` ve null metriklerle yazılır. Null, sıfır başarı değil, ölçüm yapılmadığı anlamına gelir. Hak dosyalarının varlığı/hash'i yalnızca kanıt dosyasını öz-beyan edilmiş kayda bağlar; yasal izni doğrulamaz. `ocr_crop_eval.py` yalnızca sabit görüntülerdeki metin için CER/WER/exact/coverage ölçer. `ocr_video_eval.py`, sabit SRT ve zaman hizalı referans dosyaları için ayrı, yalnız ölçüm yapan adapter'dır; gerçek video/OCR çalıştırmaz ve release PASS üretemez. `vtt-qa.py`/`regresyon/gt_gate.py` ayrı mevcut regresyon araçları olarak kalır.

Still-crop ölçüm adaptörü `ocr_crop_eval.py` ile eklenmiştir. Bu adapter yalnızca
Phase 2 insan onaylı crop + kaynak metni üzerinde OCR motorunun **crop metnini**
ölçer. Aşağıda tanımlanan ölçüm biçimleri iki alt kümeye ayrılır:

- Crop metninden gerçekten hesaplanabilenler: CER, WER, exact match ve coverage.
- Sabit crop'tan ölçülemeyenler: cue detection/precision/recall ve zamanlama.

## Crop-level metin ölçümünü çalıştırma

Önce yukarıdaki hazırlama adımıyla `split-manifest.json` üretin. Hazırlık
metadata'sındaki her kayıt için `license.human_review.status` `reviewed`
olmalıdır. Bu yalnız hak incelemesinin kullanıcı tarafından beyan edildiğini
gösterir; raporda `rights_verified` daima `false` kalır. Eser kullanma hakkını
program doğrulamaz. Üst review-pack/event zinciri her motor başlamadan hemen
önce yeniden doğrulanır; tek başına taşınmış/self-attested export reddedilir.

```powershell
py -3 ocr_crop_eval.py "D:\yerel\verified-ocr-dataset-..." `
  --metadata "D:\yerel\dataset-metadata.json" `
  --split "D:\yerel\experiment-2026-01\split-manifest.json" `
  --out "D:\yerel-raporlar\crop-eval-2026-01.json" `
  --engine easyocr-single `
  --engine easyocr-consensus3 `
  --engine rapidocr-v6-small `
  --rapid-det-model "D:\models\det.onnx" `
  --rapid-rec-model "D:\models\rec.onnx" `
  --rapid-dict "D:\models\dict.txt"
```

RapidOCR seçilirse det/rec ONNX ve sözlük yolları zorunlu, yerel ve mevcut
olmalıdır. EasyOCR yalnız mevcut model klasörünü kullanır (varsayılan
`~/.EasyOCR/model`, değiştirilebilir `--easyocr-model-dir`) ve
`download_enabled=False` ile kurulur. Eksik model hata verir; hiçbir model
ağırlığı indirilmez. GPU kullanılabiliyorsa varsayılandır; `--cpu` açıkça CPU
seçer. Raporda motor sürümü, config özeti, EasyOCR reader'ının configured device
değeri ve oluşturulan ONNX oturumlarının configured provider listesi bulunur.
Bunlar node-level inference profili değildir. RapidOCR oturumundan configured
provider listesi okunamazsa işlem durur.

Her motor aynı sabitlenmiş `test` split satırlarını işler. Her motor başlamadan
hemen önce manifest, metadata, crop SHA/decode, split ataması ve etkin Phase 2
karar zinciri yeniden doğrulanır. Çıktı yalnız aggregate metriklerden oluşur;
crop, OCR çıktısı, insan kaynak metni veya cue ID rapora yazılmaz. Rapor,
dataset/review-pack/metadata klasörlerinin dışında yeni bir JSON dosyası olmalıdır.

- CER/WER referansı insan tarafından görsel olarak onaylanmış kaynak metindir;
  metrik normalize ederken NFC + boşluk birleştirme/trim uygular, case ve
  noktalama işaretlerini korur.
- Exact match eşleşen normalize metin sayısını test satır sayısına böler.
- Coverage boş olmayan OCR çıktısı / toplam test crop satırı olarak ölçülür.
  Boş çıktı coverage'ı düşürür ve edit metriğinde boş tahmin sayılır.
- `micro` bütün test satırlarındaki toplam edit / toplam referans birimini;
  `group_macro` source+episode gruplarının basit ortalamasını verir. Ayrıca her
  source+episode için aggregate değer raporlanır.
- Her still crop zaten bir cue'nin görüntüsüdür. Bu nedenle bu adapter video
  üzerinde cue bulma başarısını veya zaman doğruluğunu ölçmez; ilgili alanlar
  `null` kalır. Sonuç **video doğruluğu değildir**, Phase 6 `PASS` değildir ve
  yayın kapısını açmaz. Hazırlık `experiment-report.json` dosyasını değiştirmez;
  ayrı şema `schemas/ocr-crop-evaluation-report-v1.schema.json` kullanılır.

Çıktı ve model kimlikleri yerel hassas veridir. Raporu ve metrikleri otomatik
olarak ağa gönderme yoktur. Model eğitimi yapılmaz. Bu adapter `vtt-qa.py` veya
`regresyon/gt_gate.py`'nin zaman hizalı video referans değerlendirmesinin yerini
almaz.

## Tam bölüm SRT ölçüm adaptörü

### İzinli Phase 5 girdileri için boş manifest taslakları

`ocr_video_eval_templates.py`, yalnız mevcut strict Phase 5 doğrulamasından
geçen dataset/metadata/experiment/split girdileri için yerel taslaklar üretir.
Üretmeden önce gerçek adapter'ın kullandığı `_validated_split` ve
`ocr_crop_eval.validate_run` çağrılır; Phase 2 etkin karar zinciri, hak kanıtı
ve review metadata, hash'ler ve sabitlenmiş grup split'i doğrulanamıyorsa
dosya yazılmaz. Bu yardımcı doğrulamayı gevşetmez.

```powershell
py -3 ocr_video_eval_templates.py "D:\yerel\verified-ocr-dataset-..." `
  --metadata "D:\yerel\dataset-metadata.json" `
  --experiment "D:\yerel\experiment-...\experiment-report.json" `
  --out-parent "D:\phase5-drafts" `
  --protect-dir "D:\yerel\future-references" `
  --protect-dir "D:\yerel\future-predictions"
```

`--out-parent` önceden var olan, yerel ve senkronize edilmeyen bir dizin olmalı
(UNC/mapped drive ve `OneDrive`, `OneDriveCommercial`, `OneDriveConsumer`
ortam değişkenlerindeki köklerin altı reddedilir) ve dataset, onun review-pack
parent'ı, metadata/experiment/split klasörleri dışında kalmalıdır.
`--protect-dir` her gelecek reference/prediction input klasörünü ek koruma
listesine alır. Yardımcı yeni ve benzersiz bir alt klasör oluşturur; iki taslak
JSON ile README önce hazırlık klasörüne yazılır, ardından atomik olarak taşınır.
Var olan çıktının üzerine yazılmaz.

`timed-reference-manifest-v1.draft.json` ve
`prediction-manifest-v1.draft.json` dosyaları özel
[`draft-v1 şemasını`](../schemas/ocr-video-evaluation-templates-draft-v1.schema.json)
kullanır. Format, `ocr_video_eval.py`'nin kabul ettiği manifestten kasıtlı
olarak farklıdır: `schema_version="draft-v1"`, `template_only=true` ve
`INCOMPLETE_NOT_FOR_EVALUATION` alanları nedeniyle gerçek evaluation girdisi
olarak reddedilir. Yalnız sabitlenmiş source/episode/split değerleri ile
baseline/candidate kimlikleri doldurulur. Dil, medya hash'i, SRT yolları/hash'leri,
alignment review (daima `pending`), model/config hash'i, device ve backend açıkça
boş bırakılır. Taslak hak onayı, güvenilir referans, değerlendirme sonucu veya
Phase 6 PASS oluşturmaz. Sentetik testlerde başarılı çıktı için validator
sınırı mock'lanır; gerçek strict validator ise boş dataset/hak girdisini
reddeder ve hiçbir çıktı yazmaz.

`ocr_video_eval.py`, gelecekte izinli ve insan tarafından zaman hizası
incelenmiş source-language referansları hazır olduğunda sabit tam bölüm SRT
çıktılarını karşılaştıran **ölçüm adaptörüdür**. Videoyu açmaz, OCR/model
çalıştırmaz, eğitim yapmaz ve ağ erişimi kullanmaz. Tek başına OCR motorunun
belirtilen video/model/device ile çalıştığını doğrulamaz. Eski manuel
`prediction-manifest-v1.json` biçiminde engine, config hash'i,
`configured_device` ve `session_configured_backend` çağıran tarafından beyan
edilir; eski `device`/`backend` alanları geriye dönük okunur. Runner tarafından
üretilen manifest aynı v1 dosya adını korur ancak `execution_receipt` bağı da
taşır. İki biçim de donanım kullanımının bağımsız kanıtı değildir.

Örnek çağrı (önce `ocr_experiment.py` hazırlık dosyalarını üretmiş olmalı):

```powershell
py -3 ocr_video_eval.py "D:\yerel\verified-ocr-dataset-..." `
  --metadata "D:\yerel\dataset-metadata.json" `
  --experiment "D:\yerel\experiment-...\experiment-report.json" `
  --references "D:\yerel\timed-references\timed-reference-manifest-v1.json" `
  --predictions "D:\yerel\fixed-outputs\prediction-manifest-v1.json" `
  --out "D:\yerel-raporlar\video-eval-2026-01.json"
```

Girdi biçimleri katıdır ve `schemas/ocr-timed-reference-manifest-v1.schema.json`
ile `schemas/ocr-prediction-manifest-v1.schema.json` şemalarında belgelenmiştir.
İki manifestte de sabitlenmiş her `source_id + episode_id` grubu için tam bir
satır bulunur; tahmin manifestinde hazırlanmış baseline ve candidate için ayrı
satır yer alır. Split atamaları mevcut `split-manifest.json` ile eşleşmelidir.
Yardımcı önce `ocr_crop_eval.validate_run` çağrısını yaparak etkin Phase 2 olay
zincirini, metadata/kanıt hash'lerini, insan hak inceleme beyanını ve mevcut
sabit split'i yeniden doğrular. Yeni bir split sözleşmesi üretmez. Ayrıca aynı
üst dizindeki tam `split-manifest.json` dosyasını ve `PREPARED_NOT_RUN` deney
raporunu zorunlu tutar.

Zaman hizalı referans kaydı örneği:

```json
{
  "schema_version": 1,
  "records": [{
    "source_id": "source-local-id",
    "episode_id": "episode-local-id",
    "split": "test",
    "language": "ja",
    "media_sha256": "<64 lowercase hex of exact media file>",
    "reference_srt_path": "references/episode.srt",
    "reference_srt_sha256": "<64 lowercase hex>",
    "alignment_review": {
      "status": "reviewed",
      "reviewer": "human reviewer marker",
      "reviewed_at": "2026-10-11T12:00:00Z",
      "media_sha256": "<same 64 lowercase media hash>"
    }
  }]
}
```

Tahmin kayıtları aynı `source_id`, `episode_id`, `split` ve
`media_sha256` değerlerinin yanı sıra `engine_id` (sabit baseline/candidate
ID'leriyle birebir aynı), `prediction_srt_path`, `prediction_srt_sha256`,
`model_sha256`, `config_sha256`, `configured_device` (`gpu` veya `cpu`) ve
`session_configured_backend` alanlarını kullanır. Göreli SRT dosyalarını ilgili
manifest dizinlerinin altına koyun. `prediction-manifest-v1` her split grubu
için iki motorun çıktısını da içermelidir; bir motorun engine config,
configured-device ve session-configured-backend değerleri tüm gruplarda sabit
kalmalıdır. Bu alanlar sağlanan dosyaları birbirine bağlar ve nasıl
üretildiklerini beyan eder, ancak bu sabit çıktı adaptörü çalıştırmayı doğrulayamaz.

Her zaman hizalı referans satırı göreli bir yerel SRT yolunu ve SHA-256 değerini
source, episode, split ve medya SHA-256 değerlerine bağlar.
`alignment_review.status=reviewed`, gözden geçiren kişi, saat dilimi içeren tarih
ve aynı medya hash'i zorunludur. Bu bir insan beyanıdır; altyazının izinli,
kaynak dilinde, eşzamanlı veya kimliği doğrulanmış biri tarafından incelenmiş
olduğunu bağımsız olarak kanıtlamaz. Her raporda rights değeri `false` kalır.
Her tahmin satırı da yerel SRT'yi ve SHA-256 değerini tam olarak aynı medya
hash'ine ve beyan edilmiş engine config/configured-device/session-configured-backend
değerlerine bağlar.

Başvurulan tüm yollar manifest dizininin altında kalmalıdır; hash veya grup
uyuşmazlığı değerlendirmeyi durdurur. Girdi SRT'leri katı UTF-8 biçiminde ve
boyut/cue sayısı sınırları içinde olmalıdır; boş olmayan her blok ayrıştırılmalı,
bozuk bloklar sessizce atlanmamalıdır. Tek bir raporda bütün zaman hizalı
referansların `language` değeri aynı olmalıdır. WER tokenization uyumsuz diller
arasında birleştirilmesin diye farklı dillerdeki referanslar reddedilir. Mevcut
dataset metadata şemasında karşılaştırılacak bir dil alanı yoktur; bu nedenle
referans dili insan beyanı olarak kalır ve rapora eklenir.

Cue'lar, en yüksek eşleşme sayısını bulan deterministik bire bir zamansal
eşleştirme kullanır. Eşleşecek aday çiftlerde kesişim / **referans cue süresi**
>= 0.5 olmalıdır. Hopcroft-Karp geçişi referans cue'larını önce aday derecesine,
sonra dosya sırasına göre dizer; her komşuluk listesinde daha büyük örtüşme,
ardından tahmin dosyası sırası tercih edilir. Böylece geçerli çift sayısı en
yüksek düzeye çıkar. Örtüşme sırası kararlı bir eşitlik bozma tercihidir; en
yüksek eşleşme sayısına sahip tüm çözümler arasında toplam örtüşmenin genel
olarak en büyük olduğunu iddia etmez. Yoğun aralık işlemlerini sınırlamak için
ortak sayaç toplamda en fazla **2.000.000 çift işlemi** kabul eder: etkin listeyi
budarken ziyaret edilen her tahmin ve kalan her referans/tahmin örtüşme
karşılaştırması bir işlem sayılır. Ayrıca en fazla **2.000.000 uygun kenar**
saklanır. Her sınır, aşırı öğe işlenmeden veya eklenmeden önce denetlenir.
Örtüşmelerin tümü eşiğin altında kalsa bile sınırlardan birine ulaşmak
puanlamayı durdurur. Metin CER/WER ve normalizasyon için `ocr_crop_eval.py`
yeniden kullanılır; eşleşmeyen referanslar boş OCR metni sayılır, fazla tahmin
cue'ları precision değerini düşürür. Zaman raporu eşleşmiş cue'larda
başlangıç/bitiş mutlak hatalarının ortalamasını ve her iki hatası da önceden
sabitlenmiş tolerans içinde olanların oranını verir. Raporlar
train/validation/test, micro ve grup başına sonuçları içerir; cue metni, ham
source/episode ID'leri veya mutlak yolları içermez (grup satırlarında SHA-256
takma adları kullanılır).

Çıktı şeması `schemas/ocr-video-evaluation-report-v1.schema.json` özellikle
Phase 6 girdi şeması **değildir**. Adayın gözlenen test CER farkını, plan hash'ini
ve her test grubu için baseline ile candidate coverage sonucunu kaydeder; ancak
bu karşılaştırmaları release uygunluğuna dönüştürmez. Her sabitlenmiş
source+episode test grubu için coverage `matched_cues / reference_cues` olarak
hesaplanır. Eşleşme, örtüşme / referans cue süresi >= 0.5 koşulunu isteyen
değerlendiricinin deterministik bire bir zamansal eşleştirmesidir; eşleşmeyen
referans cue'ları paydada kalır, fazla tahminler paydayı büyütmez. Her iki motor
da her grup için önceden ilan edilmiş eşiği karşılamalıdır. Eşiğe tam eşit grup
geçer; altındaki herhangi bir grup coverage kapısını `FAIL` yapar. Adayın corpus
CER'ı sabit limiti aşarsa veya zorunlu grup bazlı gerilememe kuralı varken
herhangi bir test grubunun candidate CER'ı gerilerse genel karşılaştırma da
başarısız olur. Başarısız karşılaştırma ölçüm raporunu kaydettikten sonra exit
code 1 döndürür. Eksik, yinelenen, sıfır paydalı, tutarsız veya beklenmeyen test
grubu coverage değeri rapor yazılmadan değerlendirmeyi durdurur. Durum daima
`MEASURED_ADAPTER_ONLY_NOT_RELEASE_PASS`, `phase6_eligible=false`,
`rights_verified=false`; release durumu ise `BLOCKED_ADAPTER_ONLY` olur. Önceden
belirlenmiş CER ve coverage bilgilerini raporlar, release kararı vermez. Sentetik
testler eşik doğrulamayı, düşük/yüksek ve tam sınır sonuçlarını, eksik/belirsiz
grupları ve plan değişikliği/yeniden oynatmayı kapsar. Bu kullanıcının henüz
izinli üç gruplu veri kümesi veya güvenilir zaman hizalı referansları olmadığı
için gerçek koşum blokludur.

## Tam Phase 5 neden hâlâ bloklu?

Crop-level CER/WER ile sabit SRT adaptörü farklı kapsamları ölçer. Adaptör,
yalnız hazırlıktaki hak/provenance kapıları geçildiğinde ve insan tarafından
onaylanmış zaman hizalı referanslar bulunduğunda metrik hesaplayabilir. Yasal
kullanım hakkını, referansın güvenilirliğini veya modelin/aygıtın gerçekten nasıl
çalıştırıldığını bağımsız olarak doğrulayamaz; raporu Phase 6 kapısını açmaz.
Güvenilir veri, bağımsız hak doğrulaması, adayın gerçek çalıştırılma kaydı ve
yayın düzeyinde kanıt hâlâ eksiktir. Grup başına coverage eşiği sonuçtan önce
hash ile bağlanan plana eklenmiştir; bu bir ölçüm kapısıdır, kalite kanıtı ya da
yayın izni değildir. Hazırlık raporu `PREPARED_NOT_RUN` olarak kalır; crop
raporu yalnızca crop metnini ölçer, video adaptörünün raporu da yalnızca ölçüm
sonucu verir. Ayrı yerel runner sonraki bölümde açıklanmıştır; model eğitimi
yapan bir araç veya akış yoktur.

## Sabit profilli yerel OCR runner

`phase5_execution.py`, ayrı ve isteğe bağlı bir yerel runner'dır. Çıktı
oluşturmadan önce sabitlenmiş Phase 5 çalıştırma planını, Phase 2 üst paket
bağlantısını, grup başına medya hash'lerini, iki yerel çevrimdışı profili, ayrıca
incelenmiş hak makbuzunu ve proje trust pin'ini doğrular. Yalnız seçili,
hash'i sabitlenmiş profili ve sabit OCR argümanlarını kullanarak ürün CLI'sini
çağırır. Runner SRT ve stats hash'lerini; profil/model/config/device/backend
kimliğini ve koşunun girdi bağlantılarını kaydeder. Test yürütücüsü sentetik
girdiler kullanır; model eğitmez ve OCR ağırlıklarını güncellemez.

Runner çıktısı `phase5-execution-receipt-v2.json` ile
`prediction-manifest-v1.json` dosyalarını içerir. Manifestteki
`execution_receipt` nesnesi makbuzun göreli yolunu, ham SHA-256 değerini ve run
ID'sini birbirine bağlar. Makbuz, kanonik prediction-record özetini ve kayıtları
tekrar içerir. Ölçümden önce adaptör bu dosyalar arası bağı hazırlanmış veri
kümesi/split/referans, medya hash'leri, profil/config kimliği, SRT ve stats
dosyaları ile cue sayıları üzerinden denetler. Sonuç
`runner_recorded_not_independently_verified` olarak işaretlenir; eski manuel v1
manifestleri `user_supplied_not_independently_verified` olarak kalır.

Bu bağ yerel tutarlılık kontrolüdür; dijital imza veya bağımsız çalıştırma
kanıtı değildir. Kriptografik nonce içermez: run ID yalnızca yerel ilişkilendirme
değeridir. Makbuzu ve manifesti yeniden yazabilen biri kendi içinde tutarlı yeni
bir çift oluşturabilir. Bu mekanizma GPU'nun hangi node üzerinde çalıştığını,
yasal kullanım hakkını, incelemeyi yapan kişinin kimliğini veya kanıtların
güvenilirliğini göstermez; `rights_verified:false` ve
`phase6_eligible:false` değerlerini hiçbir zaman değiştirmez.

Runner uygulanmıştır, ancak gerçek koşum şu anda blokludur: hakları incelenmiş
izinli bir veri kümesi veya güvenilir zaman hizalı referans kümesi yoktur; Phase
5 execution receipt registry ile Phase 6 trust registries boştur. Sentetik
sahte yürütücü testleri yalnız yerel işleyişi doğrular; gerçek video, OCR veya
model deneyi çalıştırılmamıştır. Ölçüm adaptörünü runner ile, raporunu da
`release PASS` ile karıştırmayın.

## Tam video değerlendirmesi için gereken ek metrikler

- **CER:** referans karakter sayısına bölünen Levenshtein karakter düzenleme
  sayısıdır. NFC ve boşluk normalizasyonu uygulanır; harf büyüklüğü ve noktalama
  korunur. Boş referans örnekleri CER hesabından çıkarılır ve ayrıca raporlanır.
- **WER:** aynı normalizasyondan sonra Unicode boşlukları üzerinden tokenization
  yapılır; kelime düzenleme sayısı referans kelime sayısına bölünür. Dile özel
  tokenizer kullanılmıyorsa WER'in bu sınırlaması belirtilmelidir.
- **Exact match:** normalize edilmiş metni aynı olan eşleşmiş cue sayısı /
  referans cue sayısıdır; eşleşmeyen referans cue yanlış sayılır.
- **Cue recall:** sabitlenmiş zaman aralığı eşleştirme algoritması ve toleransına
  göre eşleşen güvenilir referans cue sayısı / toplam referans cue sayısıdır.
- **Cue precision:** eşleşen OCR cue sayısı / toplam OCR cue sayısıdır. Bu
  tanımlar `vtt-qa.py`'nin mevcut örtüşme eşiği sisteminden farklıysa adaptör bu
  farkı açıkça raporlamalıdır.
- **Zaman:** eşleşmiş cue'larda başlangıç ve bitiş mutlak farklarının ayrı
  ortalamasıdır. Yalnız referans hizalaması destekliyorsa, önceden belirlenmiş
  tolerans içindeki çiftlerin oranı da verilir. Tolerans deneyden önce
  sabitlenmelidir.
- **Coverage:** doğrulanabilir, hizalanmış ve ölçülebilir referans cue'larının
  payıdır; kullanılamayan/belirsiz/eksik referanslar ve nedenleri ayrıca
  sayılmalıdır.
- **Aggregates:** her bölüm/kaynak için ayrı sonuç; cue ağırlıklı micro (toplam
  edit / toplam referans birimi) ve group macro (grup skorlarının basit
  ortalaması). Train, validation ve test ayrı raporlanmalı; yayın kapısı yalnız
  sabitlenmiş test grubunu kullanmalıdır.

Her gerçek ölçüm baseline ve aday için aynı referansı, cue hizalamasını,
normalizasyon kurallarını, OCR engine/config, configured-device/session-configured-backend
değerlerini ve koşu parametrelerini kaydetmelidir. Provider/device değerleri
çalışma yapılandırmasını belirtir; node-level inference profili değildir. Fark
varsa açıkça gösterilmelidir. Çeviri metrikleri bu OCR raporuna eklenmez; bunun
için ayrı, insanlarca değerlendirilen iki dilli bir veri kümesi ve ayrı schema
gerekir.

## Önceden ilan edilen gerileme kapısı

Rapor çalıştırılmadan önce `--max-cer-regression` ve zorunlu
`--min-test-group-coverage` sabitlenir. Aday, sabit test kümesinde baseline
CER'ına göre izin verilen artışı aşarsa veya baseline/aday motorlarından biri
herhangi bir test grubunda minimum coverage oranının altına düşerse coverage/CER
karşılaştırması başarısız olur. Her episode/source grubu ayrı raporlanır; yüksek
coverage'lı bir grup başka gruptaki düşüklüğü örtemez. Eşik sonuçlar görüldükten
sonra değiştirilemez; yeni bir değer yeni experiment planı gerektirir. Bu
karşılaştırmalar Phase 6 `release PASS` anlamına gelmez.

## Kesin durma koşulları

- İnsan tarafından `accepted`/`corrected` olarak onaylanmış export
  doğrulanamıyorsa veya crop hash/decode, cue ID ya da schema hatalıysa hazırlığı
  reddet.
- source/episode grubu bilgisi veya hash'i eşleşen lisans kanıtı dosyası yoksa
  split üretme. Kanıt dosyasının varlığı hukuki uygunluk değildir; insan hak
  incelemesi tamamlanmadıysa deneyi çalıştırma.
- Üst review-pack zinciri doğrulanamıyorsa provenance `self_attested` yazılır
  ve deney çalıştırma kapısı kapalı kalır.
- Üçten az bağımsız grup varsa train/validation/test split'i oluşturma;
  değerlendirmeyi veri yetersizliği nedeniyle durdur.
- Bir kaynak review-pack birden fazla source+episode grubu olarak
  işaretlenmişse veya iki pack aynı grubu iddia ediyorsa metadata lineage'ını
  reddet.
- Güvenilir, zaman hizalı, kaynak dilde referans yoksa OCR doğruluğu, model
  kazanımı veya gerileme testi başarısı raporlama.
- Baseline/aday engine, config, configured-device/session-configured-backend,
  metrik normalization/alignment ya da önceden belirlenmiş kapı eksikse sonuç
  üretimini reddet.
- Bu çalışmada model eğitimi, model ağırlığı değişimi veya kullanıcı
  videosu/veri kümesi işlemi **yapılmadı**. Yeterli veri, lisans ve engine'e özel
  referanslar sağlanana kadar bunlar bloklu kalır.
