# Phase 5 — yerel OCR deney hazırlığı ve değerlendirme sözleşmesi

## Bu aşamada ne var

`ocr_experiment.py`, Phase 2'nin `verified-ocr-dataset-*/manifest.jsonl` dışa aktarımını ve ona karşılık gelen ayrı metadata dosyasını yerelde doğrular. Geçerli girdiler için `split-manifest.json` ve `experiment-report.json` oluşturur. Araç OCR çalıştırmaz, model eğitmez, veri kümesini değiştirmez ve ağa bağlanmaz. Metadata, split ve rapor çıktıları hassas yerel dosyalardır; paylaşılabilir paket sayılmaz.

Phase 2 dışa aktarımı yalnızca geçerli, etkin insan `accepted`/`corrected` OCR kararlarından türetilen görsel/metin çiftlerini içerir. Bu araca verilen dosyayı hazırlayan kişi de dışa aktarımın gerçekten yerel UI'den geldiğini ve etkin kararları temsil ettiğini doğrulamalıdır; manifest imzalı değildir. AI önerisi bu veri akışına giremez.

## Metadata girdisi

Her `cue_id` için `source_id`, `episode_id` ve lisans alanı zorunludur. Lisans durumu yalnız `public_domain`, `permissive_license`, `permission_granted` veya `user_owned` değerlerinden biri olabilir. `identifier` lisans/izin türünü, `evidence_ref` ise mutlak yol içermeyen yerel göreli kanıt belge kimliğini belirtir. Bu alanları doldurmak tek başına hukuki doğrulama değildir; veri kümesi sorumlusu kullanım ve deney için yeterli hakkı ayrıca teyit etmelidir. Lisans belirsizse çalışmayı durdurun.

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
        "evidence_ref": "rights/permission-record-2026-001.txt"
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

Komut crop dosyalarının göreli ve kök dışına taşmayan yol olduğunu, SHA-256 değerlerinin eşleştiğini ve OpenCV ile görüntünün çözülebildiğini denetler. `cue_id` tekrarını, yanlış manifest şemasını, eksik/ekstra metadata satırını, boş insan doğrulanmış metni, lisans belirsizliğini ve bozuk crop'u reddeder. Phase 2 export şemasındaki `review_event_id` kabul edilmiş/düzeltilmiş insan kararını göstermelidir. Çıktıdaki grup ataması `source_id + episode_id` birleşimini bölünemez kabul eder. Aynı gruptaki hiçbir cue farklı split'e düşmez. En az üç ayrı grup yoksa veya herhangi bir split boş kalacaksa çıktı üretimi durur.

Deterministik sıralama `SHA256(seed + NUL + canonical_group_id)` ile yapılır; sıralanmış gruplar sabit 80/10/10 hedefli grup adetlerine ayrılır. Bu, grup sayısına göre bölmedir; örnek/cue sayısına göre tam 80/10/10 garantisi vermez. Kısıtlı grup sayısında oranlar yaklaşık olur ve gerçek adetler raporda yazılır. Yeni veri geldikçe mevcut test gruplarını sessizce değiştirmeyin: deney manifestini dondurun ve yeni bir deney sürümü açın.

## Metrik tanımları ve rapor

Makinece okunur şemalar `schemas/ocr-experiment-metadata-v1.schema.json`, `schemas/ocr-experiment-split-v1.schema.json` ve `schemas/ocr-experiment-report-v1.schema.json` dosyalarındadır. `experiment-report.json` hazırlıkta `PREPARED_NOT_RUN` ve null metriklerle yazılır. Null, sıfır başarı demek değildir; ölçülmedi demektir. Bu ilk araç sadece crop çiftlerini hazırlar; trusted timed reference manifesti ve engine adapter henüz yoktur. `vtt-qa.py`/`regresyon/gt_gate.py` video ile referans altyazıyı karşılaştırır ama bu image/text export'una doğrudan bağlanmaz.

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
- source/episode grup bilgisi veya kanıtlanabilir uygun lisans/izin yoksa: split ve rapor üretme.
- Üçten az bağımsız grup varsa: train/validation/test kurma; değerlendirmeyi yetersiz veri olarak durdur.
- Trusted, zamanla uyumlu source-language reference yoksa: OCR accuracy, model gain veya regression pass raporlama.
- Baseline/aday engine, config, device/backend, metrik normalization/alignment veya predeclared gate eksikse: sonuç üretimini reddet.
- Bu teslimatta model eğitimi, model ağırlığı değişimi ve kullanıcı videosu/corpus işlemi **yapılmadı**. Yeterli veri, lisans ve engine-specific reference gelene kadar bunlar bloklu kalır.
