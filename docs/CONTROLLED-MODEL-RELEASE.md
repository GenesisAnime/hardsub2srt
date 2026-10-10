# Phase 6 — kontrollü yerel OCR model sürümü

**Durum: `GATE_SCAFFOLD_ONLY` · release bloklu.** Bu teslimat bir model sürümü, model paketi veya yayın değildir. `phase6_release.py` yalnızca aday manifestini, girdilerin kimliğini ve release önkoşullarını doğrulamaya yönelik fail-closed bir iskelettir. Bugünkü Phase 5 rapor şeması yalnız `PREPARED_NOT_RUN`/blocked hazırlık raporu üretebildiği için geçerli Phase 5 `EVALUATED + PASS` sonucu yoktur. Bu depoda aday ağırlıklar, trusted lisanslı eğitim/değerlendirme korpusu ve bağımsızca pinlenmiş insan hak inceleme makbuzu bulunmuyor.

## Kullanıcı ve veri sınırı

- Program yerel çalışır. Phase 6 aracı ağ bağlantısı kurmaz, hiçbir dosyayı yüklemez veya başka yere kopyalamaz.
- AI çeviri kontrolü GPT/DeepSeek'e elle aktarılıp kullanıcının seçtiği sohbet içinde yapılır. Bu faz sağlayıcı API'si eklemez.
- Model dosyalarını hiçbir zaman yüklemez veya çalıştırmaz. Python/PyTorch pickle ağırlıklarını açmak da bu araca dahil değildir.
- Phase 5 v1 raporunda `rights_verified` daima `false`, `experiment_run_blocked` daima `true` olur. Lisans kanıtı dosyası ya da metadata içindeki `human_review` alanı tek başına hukuki/hak onayı değildir.
- `schemas/trusted-rights-review-receipts-v1.json` hem hak inceleme makbuzları hem Phase 5 değerlendirme raporu için boş SHA-256 pin listeleriyle gelir. Adayın yanında bulunan, kendi kendine yazılmış hak beyanı/PASS raporu güven kökü sayılmaz. İleride hash eklemek ayrı, açık insan incelemesi ve kod/değişiklik onayı gerektirir. Hash pini yalnız o tam belgenin içeriğini sabitler; hukuki görüşün veya testin doğruluğunu otomatik kanıtlamaz.

## Validator

```powershell
py -3 phase6_release.py "D:\local\candidate\manifest.json" `
  --phase5-report "D:\local\candidate\experiment-report.json" `
  --json
```

Manifestin yolu aday klasörünü belirler. Manifestteki rapor ve hak inceleme makbuzu yolları ile model dosyaları bu kök klasöre göreli `forward-slash` yolu olmalı ve klasör dışına çıkmamalıdır. Validator raporun ve tüm model dosyalarının SHA-256/byte sayısını karşılaştırır. Faz 5 PASS raporu için `schema_version=2`, EVALUATED durumu, önceden ilan edilmiş test koşulları, ölçülmüş OCR metrikleri ve boş olmayan sabit trust store'da aynı rapor hash'inin bulunması gerekir. Faz 5 v1 raporu bu koşulları sağlayamaz. Eksik dosya, belirsiz veri, geçersiz JSON, uyumsuz hash, boş/uygunsuz hak trust store veya eksik alan hata üretir. Çıkış kodu `2` bloklu girdi, `1` yapısal kontrolü geçen ama yine de **yayın izni vermeyen** girdidir. Aracın hiçbir durumda `release_permitted:true` sonucu yoktur; şu an yalnız gate teşhisi yapar.

Candidate manifest biçimi [model-release-manifest-v1.schema.json](../schemas/model-release-manifest-v1.schema.json) ile sürümlenir. İçinde şu kayıtlar zorunludur:

- Semver aday sürümü, kaynak commit'i, eğitim tarifi kimliği, Phase 5 dataset manifest hash'i ve eğitim motoru.
- Phase 5 raporunun göreli yolu ve hash'i; raporda `status=EVALUATED` ve hesaplanmış `release_gate.status=PASS` şartı. Phase 5 v1 şeması bu PASS alanını tanımlamadığı ve deneyin bloklu olduğunu söylediği için bugünkü raporlar reddedilir. Gelecekte gerçek değerlendirme adaptörü/raporu gelmeden bu alanı elle eklemek PASS kanıtı oluşturmaz.
- Her model dosyası için güvenli göreli yol, byte sayısı ve SHA-256.
- İşletim sistemi, Python, OCR backend ve GPU/CPU desteği.
- Veri biçimi sürümü ve geçiş adımları; veri koruyan, uygulanabilir geri alma adımları.
- GPU varsayılandır, CPU kullanıcının açık tercihiyle seçilebilir. Otomatik model değiştirme yasaktır; etkinleştirme ayrıca kullanıcı onayı ister.
- Hak review receipt'i ve hash'i. Dosyanın kendi beyanı yeterli değildir; receipt hash'i ayrı güvenli trust store içinde pinlenmelidir. Bu depodaki trust store boş olduğundan gate bloklanır.

## Tam sürüme geçmeden önce gerekenler

1. Kullanıcı tarafından yetkilendirilmiş ve hakları insan tarafından incelenmiş veri; kaynak/kapsam ve saklama koşulları kayıtlı olmalı.
2. Mevcut Phase 5 hazırlık aracından ayrı gerçek eğitim/değerlendirme adaptörü ve güvenilir zaman hizalı kaynak dili referans seti.
3. Split, metrik tanımı, coverage ve regresyon eşiği sonuç görülmeden dondurulmalı. Dondurulmuş testte CER/WER yanında cue precision/recall, timing ve grup başına sonuçlar ölçülmeli. PASS'i sistem kendisi hesaplamalı; rapora sonradan elle yazılmamalı.
4. İnsan hak review'ı için yetkili inceleyen ve denetlenebilir trust kökü. Trust store değişikliği ancak ayrı insan gözden geçirmesiyle yapılmalı; dosyaya yazılan `reviewed=true` alanına güvenilmemeli.
5. Aday ağırlıklarının kaynak commit/dataset/reçete ile bağı, hash doğrulaması, destek matrisinde gerçek smoke/compatibility kanıtı, geçiş ve geri alma pratiği.
6. Kullanıcıya gösterilen sürüm notları ve açık etkinleştirme/geri alma akışı. Mevcut model, GPU varsayılanı ve CPU opt-in aday onaylanana kadar kullanılabilir kalmalı.

Bu koşullar gerçekleşmeden eğitim çalıştırma, gerçek videoları/corpus'u işleme, model ağırlığı oluşturma/değiştirme, GitHub release yayınlama, API/deploy veya otomatik güncelleme yapılmaz.
