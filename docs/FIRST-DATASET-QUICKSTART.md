# İlk gerçek OCR veri kümesi: hızlı başlangıç

Bu akış, insanın görüntüden doğruladığı OCR örneklerini hazırlayıp sabit SRT çıktılarıyla ölçüm raporu üretir. UI görüntü ve metin etiketlerini toplar; CLI metadata, split ve değerlendirme dosyalarını hazırlar. Hiçbir adım model eğitmez veya videoyu değerlendirme sırasında yeniden OCR etmez.

> **Örneklerdeki bütün yollar ve kimlikler sentetik yer tutucudur.** Bunlar gerçek kaynak, bölüm, lisans veya kullanım izni bildirmez. Gerçek çalışmada yalnızca kendi kullanım hakkınızı insan olarak doğruladığınız materyalleri kullanın.

> Her review pack ayrı bir video/bölüm işidir ve ayrı bir doğrulanmış export üretir. Bunları **`ocr_dataset_merge.py` ile** birleştirin; program her cue’yu kaynak export’a ve etkin review olayına bağlayan yerel lineage kaydını korur. Farklı export’ları elle kopyalamayın, tek videonun cue’larına sahte episode kimlikleri vermeyin.

## Başlamadan önce

- En az **3 bağımsız `source_id + episode_id` grubu** gerekir; her grup aynı split içinde kalır. Üç grup yalnızca teknik alt sınırdır ve güvenilir genelleme için çok azdır. Mümkünse daha geniş, çeşitli bir corpus hazırlayın.
- Her grup için video/medyanın tam dosyasını ve ona bağlı, kaynak dilde, insan tarafından zaman hizası kontrol edilmiş bir referans SRT’yi bulundurun. Kaynak SRT içeriğinin gerçekten videoyla eşleştiğini ve bütün cue zamanlarını inceleyin.
- Her eser ve kullanım amacı için izin/lisans dayanağını ve **ayrı insan hak incelemesini** tamamlayın. Kanıt dosyasının veya doldurulmuş bir formun bulunması tek başına hukuki izin değildir.
- Her grup için aynı medyadan üretilmiş bir **baseline SRT** ve bir **aday SRT** hazırlayın. Motor, model, yapılandırma, cihaz ve backend bilgilerini kaydedin. Değerlendirme aracı bu SRT’leri üretmez ve bu bilgilerin doğruluğunu bağımsız olarak teyit etmez.
- Dosyaları yerelde tutun. Deney metadata’sı, kaynak/alignment SRT’leri, hash’ler ve raporlar hassas olabilir; bunları GitHub’a veya paylaşım klasörüne koymayın.

## 1. Review pack oluşturun veya açın

**UI:** Video eklerken Gelişmiş ayarlar içinden **Yerel AI inceleme paketi oluştur** seçeneğini açın ve işi çalıştırın. OCR tamamlanınca çıktıdaki `<ad>.review-pack` klasörü oluşur. Mevcut bir paketi incelemek için yerel arayüzde **Yerel OCR Doğrulama** sayfasını (`http://127.0.0.1:8765/ocr-inceleme`) açıp klasör seçiciden bu klasörü seçin veya yerel yolu girin.

**CLI alternatifi:** Proje klasöründe aşağıdaki biçimi kullanın; video ve çıktı yolları yalnız yer tutucudur:

```powershell
py -3 hardsub2srt.py "C:\LOCAL-ONLY-PLACEHOLDER\input-video.mp4" `
  -o "C:\LOCAL-ONLY-PLACEHOLDER\output.srt" --review-pack
```

Bu seçenek yerelde SRT, manifest ve güvenle eşleşen altyazı bandı kırpımlarını üretir. Yalnızca crop-to-cue eşleşmesi kesin olan bloklar görsel doğrulamaya açılır. Bu aşama kullanım hakkını incelemez ve OCR modeline eğitim vermez.

## 2. Görüntü ve OCR metnini insan olarak doğrulayın

UI’de her görüntüyü okuyup OCR metniyle karşılaştırın:

- **Görüntü doğru:** Görseldeki yazı OCR metniyle aynıysa kaydedin.
- **Düzeltmeyi kaydet:** Görselden okuyabildiğiniz gerçek kaynak metnini yazıp kaydedin.
- **Belirsiz:** Yazı seçilemiyor veya eşleşme şüpheliyse bu seçeneği kullanın. Belirsiz kayıt OCR veri kümesine girmez.

AI önerileri, SRT metnini düzenlemek veya kaynak metni onaylamak için insan incelemesinin yerine geçmez. Çeviri belleği de OCR etiketleriyle karıştırılmaz.

## 3. Her review pack için doğrulanmış export alın

**UI:** Her pakette **Doğrulanmış OCR veri kümesini dışa aktar** düğmesine basın. Yalnızca etkin, insan tarafından kabul edilmiş/düzeltilmiş ve geçerli crop’a bağlı kayıtlar `verified-ocr-dataset-.../` klasörüne girer. `manifest.jsonl` ile crop dosyaları bulunur; SRT kopyası export’a eklenmez. Kaynak `.review-pack` klasörlerini değiştirmeyin.

## 4. Üç veya daha fazla review-pack export’unu lineage ile birleştirin

**Önce OneDrive dışına bayt içeriğini değiştirmeden tam klasör kopyası alın.** Kullanıcının `.review-pack` klasörleri şu anda `C:\Users\naton\OneDrive\Desktop\.srt` altında olduğundan, yeni bir yerel ve senkronize edilmeyen kök oluşturun (ör. `C:\LOCAL-ONLY-PLACEHOLDER\local-review-root`) ve her `.review-pack` klasörünün tamamını oraya kopyalayın. `.review-pack` klasörünü ve içindeki `manifest.json`, kopyalanmış SRT, `review-events.jsonl`, `crops/` ve `verified-ocr-dataset-*` export klasörlerini birlikte koruyun. Kopyaları doğruladıktan sonra bile OneDrive’daki asılları taşımayın veya silmeyin.

Yalnız `verified-ocr-dataset-*` alt klasörünü kopyalamayın: lineage doğrulayıcısı export’u üst review-pack’in OCR manifesti, SRT hash’i, etkin review-event günlüğü ve crop hash’leriyle tekrar eşleştirir. Üst pack dosyaları yoksa doğrulanmış kaynak zinciri kurulamaz ve merge durur. `--root` yeni yerel kopyaların kökü olmalı; her `--source-export` bu köke göreli, kendi `.review-pack` klasörünün içindeki export yoludur. `--out-name` aynı kökte var olmayan yeni bir klasör adı olmalıdır.

```powershell
py -3 ocr_dataset_merge.py `
  --root "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root" `
  --source-export "run-01\synthetic.review-pack\verified-ocr-dataset-PLACEHOLDER" `
  --source-export "run-02\synthetic.review-pack\verified-ocr-dataset-PLACEHOLDER" `
  --source-export "run-03\synthetic.review-pack\verified-ocr-dataset-PLACEHOLDER" `
  --out-name "verified-ocr-dataset-composite-SYNTHETIC"
```

Araç en az 3 farklı review pack/export ister; aynı pack’i iki kez eklemeyi, cue-ID çakışmasını, kök dışı yolları, hash uyuşmazlığını ve toplam crop sınırını reddeder. Sadece doğrulanmış export satırlarını ve hash’i aynı JPEG crop’ları kopyalar. Her satırın hangi export, review-pack manifesti/SRT’si ve etkin review-event günlüğünden geldiğini `source-lineage-v1.json` dosyasında tutar; özgün pack/export dosyalarını değiştirmez. Her pack’teki bütün cue’lar ileride metadata’da **aynı tek source+episode grubuna**, farklı pack’ler ise farklı gerçek gruplara bağlanmalıdır. Program eser/bölüm kimliklerini tahmin etmez veya doğrulamaz.

Birleşik export yalnız Phase 2 görsel etiketlerini ve kaynak lineage’ini taşır; hak kanıtı veya hak onayı içermez. Rights/group metadata’sını bir sonraki adımda doldurun.

## 5. Birleşik veri kümesi için boş metadata şablonu alın

Review ekranındaki **Boş Phase 5 metadata şablonu oluştur** düğmesi tek açık review-pack içindeki export için çalışır. Birleşik export birden fazla pack’in yanında olduğundan CLI şablon aracını kullanın (çıktı klasörü önceden var olmalı, hedef dosya henüz bulunmamalı ve dataset’in dışında kalmalı):

```powershell
py -3 ocr_dataset_metadata.py `
  "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\verified-ocr-dataset-composite-SYNTHETIC" `
  --out "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\dataset-metadata.template.json"
```

Şablondaki her cue için aşağıdaki alanları gerçek kayıt ve hak kanıtıyla doldurun:

- `source_id` ve `episode_id`: sabit ve tutarlı grup kimlikleri. Aynı eser/bölüm grubundaki tüm cue’lar aynı kimlik çiftini kullanmalı.
- `license.status`, `license.identifier`, `license.evidence_path`, `license.evidence_sha256`: lisans/izin türü, dayanak kaydı, metadata dosyasına göreli kanıt yolu ve kanıt dosyasının gerçek SHA-256 değeri. Status yalnızca `public_domain`, `permissive_license`, `permission_granted` veya `user_owned` olabilir; uygun olanı insan olarak doğrulayın.
- `license.human_review`: insan incelemesinden sonra `status: "reviewed"`, reviewer işareti ve timezone içeren ISO-8601 tarih.

Şablon bu alanları **boş** bırakır. Şablonu, içindeki `human_review` alanını veya kanıt dosyasını oluşturmak **kullanım hakkı vermez ve hak onayı değildir**. İnsan hak incelemesi tamamlanmamışsa deneyi ilerletmeyin. Dosyayı ve kanıtını yalnızca yerel, senkronize edilmeyen bir klasörde saklayın.

## 6. Grup split’ini ve hazırlık raporunu dondurun

Sonuçları görmeden önce split seed’ini, baseline/aday engine kimliklerini, izin verilen CER artışını ve zaman toleransını seçin. Kimlikler ölçüm boyunca değişmemeli. Metadata ve hash’leri doğrulayan, `train`/`validation`/`test` gruplarını donduran komut:

```powershell
py -3 ocr_experiment.py `
  "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\verified-ocr-dataset-composite-SYNTHETIC" `
  --metadata "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\dataset-metadata.json" `
  --out "C:\LOCAL-ONLY-PLACEHOLDER\experiment-PLACEHOLDER" `
  --seed "SYNTHETIC-FROZEN-SEED" `
  --baseline "synthetic-baseline-engine-v1" `
  --candidate "synthetic-candidate-engine-v1" `
  --max-cer-regression 0.002 `
  --timing-tolerance-ms 500
```

Yukarıdaki engine kimlikleri, seed ve eşikler örnektir; bunları gerçek, önceden kararlaştırılmış değerlerle değiştirin. `--out` yeni ve henüz var olmayan bir klasör olmalıdır. Komut `split-manifest.json` ve `experiment-report.json` üretir; split atamasını veya test gruplarını sonuca baktıktan sonra değiştirmeyin.

Bu yalnız **hazırlıktır**: rapor `PREPARED_NOT_RUN`, metrikler `null`, `experiment_run_blocked: true` ve `rights_verified: false` kalır. Komut OCR çalıştırmaz, model eğitmez ve hakları doğrulamaz. Hak incelemesini `reviewed` olarak tamamlamadan sonraki kapılar açılmaz.

## 7. Zaman referansı ve SRT tahmin manifest taslaklarını üretin

Deney raporu ve frozen split oluştuktan, metadata ve insan hak incelemesi doğrulandıktan sonra taslakları **yerel, önceden var olan ve OneDrive/UNC/mapped drive dışında** bir klasörde oluşturun. Aşağıdaki yer tutucu klasörler gerçekte önceden var olmalı. `--protect-dir` alanları referans ve prediction SRT’lerinin sonradan konacağı klasörleri korur:

```powershell
py -3 ocr_video_eval_templates.py `
  "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\verified-ocr-dataset-composite-SYNTHETIC" `
  --metadata "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\dataset-metadata.json" `
  --experiment "C:\LOCAL-ONLY-PLACEHOLDER\experiment-PLACEHOLDER\experiment-report.json" `
  --out-parent "C:\LOCAL-ONLY-PLACEHOLDER\phase5-drafts"
```

Helper benzersiz bir alt klasörde `timed-reference-manifest-v1.draft.json` ve `prediction-manifest-v1.draft.json` üretir. Bu dosyalar **geçerli evaluator girdisi değildir**. Onları inceleyip strict v1 biçimine elle tamamlayın; `draft-v1`, `template_only` ve `INCOMPLETE_NOT_FOR_EVALUATION` alanları v1 dosyasında kalmamalıdır.

Her iki manifest dosyası helper'ın ürettiği aynı klasörün kökünde durur. Bu klasörde `references/` ve `predictions/` alt klasörlerini oluşturup SRT dosyalarını buralara yerleştirin. Manifestteki yollar bu köke göreli olmalı (ör. `references/SYNTHETIC_GROUP_01.srt`, `predictions/baseline/SYNTHETIC_GROUP_01.srt`).

### Referans manifesti

Her frozen `source_id + episode_id` grubu için bir kayıt gerekir. Split ve kimlikleri draft’tan aynen alın. Şunları tamamlayın:

- `language`: tek bir kaynak dil etiketi; bir rapor farklı dilleri karıştıramaz.
- `media_sha256`: referans ve prediction için aynı, **tam kaynak video dosyasının** SHA-256 değeri.
- `reference_srt_path` ve `reference_srt_sha256`: manifest klasörünün altında duran zaman hizalı kaynak SRT’sinin göreli yolu ve dosya SHA-256 değeri.
- `alignment_review`: yalnızca kişi referans SRT’sini videoyla cue cue karşılaştırıp gözden geçirdikten sonra `status: "reviewed"`, reviewer işareti, timezone’lu `reviewed_at` ve aynı `media_sha256`.

Referans SRT’si her grup için doğru kaynak dilde olmalı ve başlangıç/bitiş zamanları videoyla insan tarafından hizalanmalıdır. Manifestin bu alanları kabul etmesi, kişinin kimliğini, referansın güvenilirliğini veya yasal yetkiyi bağımsız doğrulamaz.

### Prediction manifesti

Her grup ve iki engine için tam birer kayıt gerekir. Baseline ve aday SRT’lerini manifest klasörü altına koyun. Her kayıtta:

- Frozen `engine_id`, `source_id`, `episode_id`, `split` değerlerini aynen koruyun.
- `media_sha256` değerini referans kaydıyla aynı girin.
- `prediction_srt_path` ve `prediction_srt_sha256` için baseline/aday SRT’sinin manifest klasörüne göreli yolunu ve gerçek SHA-256’sını yazın.
- `model_sha256`, `config_sha256`, `device` (`gpu` veya `cpu`) ve `backend` değerlerini gerçek çalıştırma kayıtlarından doldurun. Aynı engine için config/device/backend bütün gruplarda sabit olmalıdır.

Bu alanlar beyan edilen SRT üretim bilgilerini ve dosyaları hash ile bağlar; evaluator OCR motorunu çağırmadığından, SRT’nin gerçekten beyan edilen model/cihazla üretildiğini kanıtlamaz.

## 8. Sabit SRT ölçüm raporunu çalıştırın

İnsan hizalaması tamam, bütün referans/prediction kayıtları v1 şemasında ve hash’leri güncel olduktan sonra:

```powershell
py -3 ocr_video_eval.py `
  "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\verified-ocr-dataset-composite-SYNTHETIC" `
  --metadata "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\dataset-metadata.json" `
  --experiment "C:\LOCAL-ONLY-PLACEHOLDER\experiment-PLACEHOLDER\experiment-report.json" `
  --references "C:\LOCAL-ONLY-PLACEHOLDER\phase5-drafts\phase5-measurement-templates-PLACEHOLDER\timed-reference-manifest-v1.json" `
  --predictions "C:\LOCAL-ONLY-PLACEHOLDER\phase5-drafts\phase5-measurement-templates-PLACEHOLDER\prediction-manifest-v1.json" `
  --out "C:\LOCAL-ONLY-PLACEHOLDER\reports\video-eval-PLACEHOLDER.json"
```

Komutun örneği yalnız argüman biçimini gösterir; template helper klasör adını kendisi üretir. Gerçek klasör/dosya yollarını helper çıktısındaki değerlerle değiştirin. Raporun parent klasörünü önceden oluşturun; `--out` yeni dosya olmalı ve dataset, metadata, experiment, references ve predictions klasörlerinin dışında kalmalıdır.

## UI ve CLI sınırı

| İş | Nerede yapılır |
|---|---|
| OCR’ı kişinin bilgisayarında çalıştırma; review pack üretme | Yerel web UI veya `hardsub2srt.py --review-pack` CLI |
| Crop’ı görüp OCR metnini kabul/düzelt/belirsiz bırakma | Yerel `/ocr-inceleme` UI |
| Doğrulanmış crop/metin export’u ve boş metadata şablonu | Yerel `/ocr-inceleme` UI; metadata için `ocr_dataset_metadata.py` alternatif CLI |
| Rights/group metadata’sını araştırma ve insan hak incelemesini tamamlama | İnsan tarafından, metadata JSON ve kanıt dosyalarında; yazılım hak kararı vermez |
| Frozen grup split’i ve PREPARED_NOT_RUN raporu | `ocr_experiment.py` CLI |
| Timed-reference/prediction manifest taslakları | `ocr_video_eval_templates.py` CLI; taslakları inceleyip tamamlamak kullanıcı işidir |
| Hash/split/hizalama denetimi ve fixed-SRT aggregate raporu | `ocr_video_eval.py` CLI |

## Sonuç nasıl yorumlanır?

`ocr_video_eval.py` yalnızca verilen sabit SRT’lerle zaman hizalı kaynak referansları kıyaslar. Videoyu açmaz, OCR çalıştırmaz, eğitim yapmaz ve izinleri veya motor çalıştırma bilgisini doğrulamaz. Rapor daima `MEASURED_ADAPTER_ONLY_NOT_RELEASE_PASS`, `rights_verified: false` ve Phase 6 blocked döndürür. Bunu genel doğruluk garantisi, model kazanımı veya Phase 6 `PASS` olarak sunmayın. Daha kapsamlı veri/izin, bağımsız çalıştırma kanıtı ve release kapısı için [offline deney sözleşmesine](OCR-OFFLINE-EXPERIMENT.md) dönün.
