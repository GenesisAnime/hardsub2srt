# Phase 5 — yerel OCR deney hazırlığı ve değerlendirme sözleşmesi

## Bu aşamada ne var

`ocr_experiment.py`, Phase 2'nin `verified-ocr-dataset-*/manifest.jsonl` dışa aktarımını ve ona karşılık gelen ayrı metadata dosyasını yerelde doğrular. Geçerli girdiler için `split-manifest.json` ve `experiment-report.json` oluşturur. Araç OCR çalıştırmaz, model eğitmez, veri kümesini değiştirmez ve ağa bağlanmaz. Metadata, split ve rapor çıktıları hassas yerel dosyalardır; paylaşılabilir paket sayılmaz.

Phase 2 dışa aktarımı yalnızca geçerli `accepted`/`corrected` kararlarını hedefler. Araç, export klasörünün doğrudan üst dizinindeki `.review-pack` ile `manifest.json`, SRT ve `review-events.jsonl` zincirini tekrar doğrular; event'in aktif son karar olduğunu, event/cue/hash/metin/zaman/confidence eşleşmelerini kontrol eder. Zincir doğrulanamazsa çıktıdaki provenance `self_attested` olur ve `experiment_run_blocked` true kalır. Başarılı yerel bağlantı da hesabın/kişinin kimliğini kanıtlamaz; yerel UI journal'ına bağlı olduğunu gösterir. AI önerileri export'a alınmaz.

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

Önceden sabitlenecek değerler (split tohumu, baseline/aday tanımı ve CER gerileme eşiği) sonuçları incelemeden seçilmelidir. Yeni, boş bir `--out` dizini verin:

```powershell
py -3 ocr_experiment.py "D:\yerel\verified-ocr-dataset-..." `
  --metadata "D:\yerel\dataset-metadata.json" `
  --out "D:\yerel\experiment-2026-01" `
  --seed "phase5-v1-frozen" `
  --baseline "current-engine-config-id" `
  --candidate "candidate-engine-config-id" `
  --max-cer-regression 0.002 `
  --timing-tolerance-ms 500
```

Komut crop dosyalarının göreli ve kök dışına taşmayan yol olduğunu, SHA-256 değerlerinin eşleştiğini ve önce JPEG boyut marker'larının güvenli sınırlar içinde olduğunu, ardından OpenCV ile görüntünün çözülebildiğini denetler. Tek crop en çok 1280×1280 ve benzersiz crop toplamı en çok 128 MiB'dir; metadata 16 MiB, her rights evidence 16 MiB ve toplam kanıt dosyaları 64 MiB ile sınırlıdır. `cue_id` tekrarını, `ocr_confidence` alanı da dahil yanlış Phase 2 manifest şemasını, eksik/ekstra metadata satırını, boş insan doğrulanmış metni, lisans kanıtı dosyası/hash'i eksikliğini ve bozuk crop'u reddeder. `review_event_id` tek başına yeterli değildir: üst `.review-pack` ile `review-events.jsonl` içinde halen etkin karara ve aynı cue/crop/hash/SRT/zaman/metin/confidence değerlerine bağlanmalıdır. Bu zincir bulunmazsa provenance self-attested kalır ve deney bloklanır. Grup anahtarı NFC + boşluk birleştirme + casefold sonrası `source_id + episode_id` birleşimidir. Aynı gruptaki hiçbir cue farklı split'e düşmez. En az üç ayrı grup yoksa veya split boş kalacaksa çıktı üretimi durur.

Deterministik sıralama `SHA256(seed + NUL + canonical_group_id)` ile yapılır; sıralanmış gruplar sabit 80/10/10 hedefli grup adetlerine ayrılır. Bu, grup sayısına göre bölmedir; örnek/cue sayısına göre tam 80/10/10 garantisi vermez. Kısıtlı grup sayısında oranlar yaklaşık olur; rapor hem grup hem cue satır sayılarını ayrı verir. Yeni veri geldikçe mevcut test gruplarını sessizce değiştirmeyin: deney manifestini dondurun ve yeni bir deney sürümü açın.

## Metrik tanımları ve rapor

Makinece okunur şemalar `schemas/ocr-experiment-metadata-v1.schema.json`, `schemas/ocr-experiment-split-v1.schema.json`, `schemas/ocr-experiment-report-v1.schema.json` ve crop-only ölçüm için `schemas/ocr-crop-evaluation-report-v1.schema.json` dosyalarındadır. `experiment-report.json` hazırlıkta daima `PREPARED_NOT_RUN`, `rights_verified:false`, `experiment_run_blocked:true` ve null metriklerle yazılır. Null, sıfır başarı demek değildir; ölçülmedi demektir. Rights dosyalarının varlığı/hash'i yalnızca kanıt dosyasını öz-beyan edilen kayda bağlar; hukuki izni doğrulamaz. `ocr_crop_eval.py` yalnız still metin CER/WER/exact/coverage ölçer. `ocr_video_eval.py` sabit SRT ve zamanlı referans dosyaları için ayrı measurement-only adapter'dır; gerçek video/OCR çalıştırmaz ve release PASS üretemez. `vtt-qa.py`/`regresyon/gt_gate.py` mevcut regresyon araçları ayrı kalır.

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
program doğrulamaz. Parent review-pack/event zinciri her engine başlamadan önce
tekrar doğrulanır; kendi başına taşınmış/self-attested export reddedilir.

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
seçer. Raporda motor sürümü, config özeti ve gerçek cihaz/provider bilgisi
bulunur. RapidOCR oturumundan etkin ONNX provider okunamazsa işlem durur.

Her engine aynı frozen `test` split satırlarını işler ve her engine'den hemen
önce manifest, metadata, crop SHA/decode, split ataması ve etkin Phase 2 karar
zinciri tekrar doğrulanır. Çıktı yalnız aggregate metriklerdir; crop, OCR
çıktısı, insan kaynak metni veya cue ID rapora yazılmaz. Rapor, dataset/review
pack/metadata klasörlerinin dışında yeni bir JSON dosyası olmalıdır.

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

`ocr_video_eval.py`, gelecekte izinli ve insan tarafından zaman hizası
incelenmiş source-language referansları hazır olduğunda sabit tam bölüm SRT
çıktılarını karşılaştıran **ölçüm adaptörüdür**. Videoyu açmaz, OCR/model
çalıştırmaz, eğitim yapmaz ve ağ erişimi kullanmaz. Bu nedenle OCR motorunun
gerçekten belirtilen video/model/device ile çalıştığını doğrulamaz; engine,
config hash'i, device ve backend `prediction-manifest-v1` içinde kullanıcı
tarafından beyan edilir. Rapor bunu açıkça doğrulanmamış yazar.

Örnek çağrı (önce `ocr_experiment.py` hazırlık dosyalarını üretmiş olmalı):

```powershell
py -3 ocr_video_eval.py "D:\yerel\verified-ocr-dataset-..." `
  --metadata "D:\yerel\dataset-metadata.json" `
  --experiment "D:\yerel\experiment-...\experiment-report.json" `
  --references "D:\yerel\timed-references\timed-reference-manifest-v1.json" `
  --predictions "D:\yerel\fixed-outputs\prediction-manifest-v1.json" `
  --out "D:\yerel-raporlar\video-eval-2026-01.json"
```

Input formats are strict and documented by
`schemas/ocr-timed-reference-manifest-v1.schema.json` and
`schemas/ocr-prediction-manifest-v1.schema.json`. Both contain exactly one row
per frozen `source_id + episode_id` group; the prediction manifest has one row
for each of the prepared baseline and candidate. Their split assignment must
match the existing `split-manifest.json`. The helper first calls
`ocr_crop_eval.validate_run`, which rechecks the active Phase 2 event chain,
metadata/evidence hashes, human rights review attestation and the existing
frozen split. It does not invent a replacement split contract. It also requires
the exact sibling `split-manifest.json` and `PREPARED_NOT_RUN` experiment report.

Timed-reference record example:

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

Prediction records use the same `source_id`, `episode_id`, `split` and
`media_sha256`, plus `engine_id` (exactly the frozen baseline/candidate IDs),
`prediction_srt_path`, `prediction_srt_sha256`, `model_sha256`,
`config_sha256`, `device` (`gpu` or `cpu`) and `backend`. Put relative SRT files under their respective
manifest directories. `prediction-manifest-v1` must contain both engine outputs
for every split group; engine config/device/backend must stay constant across
the groups for that engine. These fields bind the provided files and declare
how they were produced, but this fixed-output adapter cannot verify execution.

Each timed reference row binds a local relative SRT path and SHA-256 to source,
episode, split and the media SHA-256. `alignment_review.status=reviewed`,
reviewer, timezone timestamp and the same media hash are required. This is a
human attestation, not independent proof that the subtitle is authorized,
source-language, synchronized, or reviewed by a verified person. Rights remain
`false` in every report. Each prediction row similarly binds a local SRT and
SHA-256 to that exact media hash plus self-declared engine config/device/backend.
All referenced paths must remain under their manifest directory; hash or group
mismatch stops evaluation. Input SRTs are strict UTF-8, bounded in size/cue
count, and every nonempty block must parse; malformed blocks are not silently
skipped. One report requires a single common `language` value across every
timed reference; mixed-language references are rejected so WER tokenization is
not pooled across incompatible languages. The current dataset metadata schema
has no language field to compare against, so the reference language remains a
human declaration and the report includes it.

Cues use deterministic maximum-cardinality one-to-one temporal matching.
Candidate pairs need intersection / **reference cue duration** >= 0.5. The
Hopcroft-Karp traversal orders reference cues by candidate degree then file
order; each adjacency list prefers larger overlap, then prediction file order.
This guarantees the largest number of valid pairs, while the overlap ordering
is a stable tie-break preference (it does not claim globally maximal total
overlap among all maximum-cardinality matchings). To bound dense interval work,
one shared counter allows at most **2,000,000 pair operations total**: each
prediction visited while pruning the active list and each remaining
reference/prediction overlap comparison counts once. It also retains at most
**2,000,000 qualifying edges**; each bound is checked before processing or
appending the over-limit item. Either limit stops scoring, including when all
overlaps are below threshold. Text
CER/WER and normalization reuse `ocr_crop_eval.py`; unmatched references count
as empty OCR text, while extra prediction cues lower precision. Timing reports
matched-cue mean absolute start/end error and share with both errors within the
previously frozen tolerance. Reports include train/validation/test, micro and
per-group results, but never cue text, raw source/episode IDs, or absolute
paths (group rows use SHA-256 pseudonyms).

The output schema `schemas/ocr-video-evaluation-report-v1.schema.json` is
deliberately **not** the Phase 6 input schema. It records the candidate's
observed test CER delta and whether it exceeds the predeclared CER limit, but
does not convert that numerical comparison into release eligibility. Its status is always
`MEASURED_ADAPTER_ONLY_NOT_RELEASE_PASS`, `phase6_eligible=false`,
`rights_verified=false`, and release status `BLOCKED_ADAPTER_ONLY`. It reports
predeclared CER comparison facts but makes no release decision; Phase 5 v1 has
no predeclared coverage threshold, which is called out instead of invented
after results. Synthetic tests exercise parsing, matching, metrics and the
non-release report shape. A real run is still blocked because this user has no
authorized three-group dataset or trusted timed references yet.

## Tam Phase 5 hâlâ neden bloklu

Crop-level CER/WER and the fixed-SRT adapter measure different surfaces. The
adapter can calculate metrics only after prepared rights/provenance gates and
human-attested time-aligned references exist. It cannot independently verify
lawful use, reference trust, or the model/device execution, and its report
cannot unlock Phase 6. Trusted data, independent rights validation, actual
candidate execution provenance, a predeclared coverage gate and release-grade
evidence remain outstanding. The prep report stays `PREPARED_NOT_RUN`; the
crop report remains crop-only; the video adapter report remains measurement-
only. Model training and full-video OCR execution are not implemented here.

Tam video değerlendirme için daha sonra eklenmesi gereken metriklerin tanımı:

- **CER:** referans karakterlerine bölünen Levenshtein karakter düzenleme sayısı; NFC + boşluk normalize edilir, harf büyüklüğü ve noktalama korunur. Empty reference örneği CER hesaplamasından çıkarılır ve ayrı raporlanır.
- **WER:** aynı normalization sonrası Unicode whitespace ile tokenization; referans kelime sayısına bölünen kelime düzenleme sayısı. Dil özel tokenizer kullanılmıyorsa bunu WER'in sınırlaması olarak belirtin.
- **Exact match:** aynı normalize edilmiş metne sahip eşleşmiş cue sayısı / referans cue sayısı; eşleşmeyen referans cue yanlış sayılır.
- **Cue recall:** zaman aralığı eşleştirme algoritması ve toleransı sabitlenmiş trusted reference cue'larının eşleşen sayısı / toplam referans cue sayısı.
- **Cue precision:** eşleşen OCR cue sayısı / toplam OCR cue sayısı. Bu tanımlar `vtt-qa.py`'nin mevcut örtüşme eşik sistemiyle farklıysa adapter farkı açıkça raporlamalı.
- **Zaman:** eşleştirilmiş cue'larda start ve end mutlak farklarının ayrı ortalaması ve, yalnızca referans hizalaması bunu destekliyorsa, önceden belirlenmiş tolerans içinde olan çiftlerin oranı. Tolerans deney başlamadan dondurulmalıdır.
- **Coverage:** referans cue'larının doğrulanabilir, hizalanmış ve ölçülebilir olan kısmı; unavailable/ambiguous/missing referanslar ve nedenleri ayrıca sayılmalıdır.
- **Aggregates:** her episode/source için ayrı sonuç; cue-weighted micro (toplam edit / toplam referans birimi), group macro (grup skorlarının basit ortalaması). Hem train, validation hem test ayrı raporlanmalı; release gate yalnız dondurulmuş test grubunu kullanır.

Her gerçek ölçüm baseline ve aday için aynı reference, cue alignment, normalization, OCR engine/config, device/backend ve koşu parametrelerini kaydetmelidir. Fark varsa açıkça gösterilmelidir. Translation metrikleri bu OCR raporuna eklenmez; ayrı bilingual-human set ve ayrı schema gerekir.

## Önceden ilan edilen regresyon kapısı

Rapor çalıştırılmadan önce `--max-cer-regression` sabitlenir. Aday, dondurulmuş test setinde baseline CER'ına göre bu değerden fazla kötüleşirse ve/veya tanımlı test kapsamı/coverage koşullarını karşılamazsa model yayın kapısı **fail** sayılır. Her episode/source grubunun sonucu ayrıca gösterilir; iyi bir toplam skorun kötüleşen grubu gizlemesine izin verilmez. Bu hazırlık aracı gate uygulatmıyor çünkü henüz iki motoru aynı trusted-reference test corpus'unda çalıştıran Phase 5 adapter'ı ve corpus yok. Null gate sonucu geçiş değildir.

## Kesin durma koşulları

- İnsan accepted/corrected export doğrulanamıyorsa; crop hash/decode, cue ID veya schema hatalıysa: hazırlığı reddet.
- source/episode grup bilgisi veya hash'i eşleşen lisans kanıt dosyası yoksa split üretme. Kanıt varlığı hukuki uygunluk sayılmaz; insan rights review tamamlanmamışsa deney çalıştırma.
- Parent review-pack zinciri doğrulanamıyorsa provenance `self_attested` yazılır ve deney çalıştırma kapısı kapalı kalır.
- Üçten az bağımsız grup varsa: train/validation/test kurma; değerlendirmeyi yetersiz veri olarak durdur.
- Trusted, zamanla uyumlu source-language reference yoksa: OCR accuracy, model gain veya regression pass raporlama.
- Baseline/aday engine, config, device/backend, metrik normalization/alignment veya predeclared gate eksikse: sonuç üretimini reddet.
- Bu teslimatta model eğitimi, model ağırlığı değişimi ve kullanıcı videosu/corpus işlemi **yapılmadı**. Yeterli veri, lisans ve engine-specific reference gelene kadar bunlar bloklu kalır.
