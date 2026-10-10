# Phase 5 — yerel OCR deney hazırlığı ve değerlendirme sözleşmesi

## Bu aşamada ne var

`ocr_experiment.py`, Phase 2'nin `verified-ocr-dataset-*/manifest.jsonl` dışa aktarımını ve ona karşılık gelen ayrı metadata dosyasını yerelde doğrular. Geçerli girdiler için `split-manifest.json` ve `experiment-report.json` oluşturur. Araç OCR çalıştırmaz, model eğitmez, veri kümesini değiştirmez ve ağa bağlanmaz. Metadata, split ve rapor çıktıları hassas yerel dosyalardır; paylaşılabilir paket sayılmaz.

Phase 2 dışa aktarımı yalnızca geçerli `accepted`/`corrected` kararlarını hedefler. Araç, export klasörünün doğrudan üst dizinindeki `.review-pack` ile `manifest.json`, SRT ve `review-events.jsonl` zincirini tekrar doğrular; event'in aktif son karar olduğunu, event/cue/hash/metin/zaman/confidence eşleşmelerini kontrol eder. Zincir doğrulanamazsa çıktıdaki provenance `self_attested` olur ve `experiment_run_blocked` true kalır. Başarılı yerel bağlantı da hesabın/kişinin kimliğini kanıtlamaz; yerel UI journal'ına bağlı olduğunu gösterir. AI önerileri export'a alınmaz.

## Metadata girdisi

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

Makinece okunur şemalar `schemas/ocr-experiment-metadata-v1.schema.json`, `schemas/ocr-experiment-split-v1.schema.json` ve `schemas/ocr-experiment-report-v1.schema.json` dosyalarındadır. `experiment-report.json` hazırlıkta daima `PREPARED_NOT_RUN`, `rights_verified:false`, `experiment_run_blocked:true` ve null metriklerle yazılır. Null, sıfır başarı demek değildir; ölçülmedi demektir. Rights dosyalarının varlığı/hash'i yalnızca kanıt dosyasını öz-beyan edilen kayda bağlar; hukuki izni doğrulamaz. Bu ilk araç sadece crop çiftlerini hazırlar; trusted timed reference manifesti ve engine adapter henüz yoktur. `vtt-qa.py`/`regresyon/gt_gate.py` video ile referans altyazıyı karşılaştırır ama bu image/text export'una doğrudan bağlanmaz.

Bir sonraki değerlendirme uygulaması metrikleri şöyle hesaplamalı ve aynı tanımları kullanmalıdır:

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
