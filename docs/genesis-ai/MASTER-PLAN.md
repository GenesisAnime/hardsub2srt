# GenesisAnime AI — Master Plan ve Teknik Şartname

**Sürüm:** Taslak v0.1 · **Tarih:** 2026-10-12 · **Durum:** Planlandı; yeni model eğitimi henüz yapılmadı  
**Ana takip:** [Issue #13](https://github.com/GenesisAnime/hardsub2srt/issues/13) · **Alt görevler:** [#14](https://github.com/GenesisAnime/hardsub2srt/issues/14), [#15](https://github.com/GenesisAnime/hardsub2srt/issues/15), [#16](https://github.com/GenesisAnime/hardsub2srt/issues/16), [#17](https://github.com/GenesisAnime/hardsub2srt/issues/17), [#18](https://github.com/GenesisAnime/hardsub2srt/issues/18), [#19](https://github.com/GenesisAnime/hardsub2srt/issues/19).

> **Okuma rehberi:** İlk dört bölüm insanlara yönelik ürün ve karar özeti; devamı mühendisler ve AI kodlama ajanları için uygulanabilir şartnamedir. **Mevcut** ile **önerilen** özellikleri karıştırmayın. Belge tek başına kodun çalıştığını, hakların temiz olduğunu veya bir benchmark'ın yeniden koşulduğunu kanıtlamaz.

## 1. Neden bu proje?

HardSub2SRT, anime/video görüntüsüne gömülmüş (*hardsub*) Türkçe altyazıyı OCR ile okuyup zamanlanmış `.srt` dosyasına dönüştüren yerel bir araçtır. Kullanıcının daha büyük hedefi, bu ürünü zamanla GenesisAnime AI çatısında OCR, metin düzeltme, çeviri ve ses tanıma yetenekleriyle geliştirmektir.

**İlk ve değişmeyen öncelik:** Türkçe hardsub → doğru, eksiksiz, zamanlaması kabul edilebilir SRT. Japonca OCR, Japonca→Türkçe çeviri, ASR, çok modlu LLM ve otomatik düzeltme ilk sürümün kapsamı dışındadır. Bunlar sonraki modüller için tasarım kısıtlarıdır, teslim sözü değildir.

**Başarı:** Yeni OCR backend'i mevcut EasyOCR/RapidOCR taban çizgisine karşı görülmemiş animelerde ölçülebilir fayda sunar; RTX 4060 8 GB üzerinde yerel çalışır; SRT zamanlamasını ve mevcut çalışma akışlarını bozmaz; kullanıcı eski OCR backend'ine geri dönebilir.

## 2. Kesinleşen kararlar / varsayımlar / açık konular

| Konu | Karar veya durum |
|---|---|
| Proje adı | GenesisAnime AI (çatı), GenesisAnime OCR v1 (ilk modül için çalışma adı) |
| Eğitim | Vast.ai üzerinde kiralık NVIDIA RTX 4090, 24 GB VRAM |
| Son kullanıcı / yerel çalışma | Masaüstü RTX 4060, 8 GB VRAM; 32 GB sistem RAM |
| İlk dil | Türkçe hardsub |
| Eğitim stratejisi | Sıfırdan genel LLM pretraining **yapma**; önce hazır hafif OCR recognizer fine-tuning |
| Model adayları | Önce PaddleOCR tanıyıcı; TrOCR küçük/base karşılaştırma adayı; kesin seçim benchmark sonrası |
| Veri hedefi | ~10.000 **insan doğrulamalı** crop–metin çifti; 1k ve 3–5k ara kapılar |
| Veri çeşitliliği | Çok sayıda farklı anime, bölüm, font, sahne ve çözünürlük; 10–20 anime / 30–50 bölüm yalnız planlama örneği |
| Veri bölme | Anime/bölüm kaynak gruplarını karıştırmadan train/validation/test; oranlar yaklaşık 80/10/10 ancak gruplara göre ayarlanır |
| VRAM | 8 GB sert donanım sınırı; 3 GB OCR + 2 GB video + 3 GB pay **yalnız örnek bütçe**, ölçülmüş değer değil |
| Dağıtım | Yerel ve mümkün olduğunca çevrimdışı; model dosyaları sürümlü, yükleme davranışı açık |
| Model ailesi | OCR, LLM/düzeltme, çeviri, ASR ayrı bileşenler; tek dev LLM zorunlu değil |
| Açık kararlar | Kesin kalite/hız eşikleri, lisanslı veri kaynakları, eğitim checkpoint lisansı, gerçek 4060 throughput |

**Dikkat:** “10.000 SS ve 10.000 SRT” demek 10.000 ayrı SRT dosyası değildir. İhtiyaç, her crop için görüntüdeki **doğru kaynak metnin** bulunmasıdır. OCR'ın kendi ürettiği SRT otomatik olarak doğru etiket değildir. SRT tek başına görsel OCR eğitimi sağlamaz; metin düzeltme, dil uyarlama ve uygun izinle sentetik crop üretiminde yardımcı olabilir.

## 3. Ürün mimarisi ve veri akışı

```text
Video
  │
  ▼
Mevcut frame sampling / altyazı bandı tespiti
  │
  ├── Tespit başarısız örnekleri (ayrı hata veri havuzu)
  ▼
Altyazı crop'u ──► OCR backend arayüzü
                      ├── Mevcut EasyOCR / RapidOCR (fallback)
                      └── GenesisAnime OCR v1 (önerilen yeni backend)
                               │
                               ▼
                       Kaynak metin + güven/tanı bilgisi
                               │
                               ▼
                    Mevcut cue birleştirme / zamanlama
                               │
                               ▼
                             SRT
                               │
                               ├── review-pack / insan incelemesi
                               └── gelecekte: isteğe bağlı metin düzeltme / çeviri
```

**Mimari ilkeler:**
1. **Tanıma (recognition)** ve **altyazı bulma (detection)** farklı sorunlardır. Kırpılmış crop ile eğitilen tanıyıcı, hiç bulunmayan altyazıyı kurtarmaz. Tam kare ve başarısız tespitler için ayrı veri/benchmark gerekir.
2. Metin doğruluğu ile SRT zamanlaması ayrı ölçülür. Daha iyi CER otomatik olarak daha iyi SRT demek değildir.
3. Gelecekte LLM, kaynak metni değiştirmeden önce ayrı **öneri** üretir. Sessizce metin uyduramaz; otomatik uygulama ayrıca açıkça tasarlanıp test edilmelidir.
4. Model sağlayıcıları sürümlenebilir; varsayılan davranış değiştiğinde regresyon testi zorunludur.
5. Çeviri ve ASR daha sonra ayrı veri kümeleri, izinler ve değerlendirme metrikleriyle gelir.

## 4. Depoda zaten olanlar: yeniden yapmayın

Kaynak: [README](../../README.md), [Learning System Baseline](../LEARNING-SYSTEM-BASELINE.md), [First Dataset Quickstart](../FIRST-DATASET-QUICKSTART.md), [Review Bundle](../REVIEW-BUNDLE.md), [Learning System Plan](../LEARNING-SYSTEM-PLAN.md).

- `hardsub2srt.py`: yerel video→SRT, EasyOCR/RapidOCR, CUDA seçimi, OCR segmentleri, son işlem ve zamanlama.
- `--review-pack` / UI **Yerel AI inceleme paketi oluştur**: final cue tek özgün OCR segmentiyle **benzersiz ve tam** eşleşirse temsilî altyazı bandı crop'u, manifest ve SRT kopyası oluşturur. Birleşmiş/belirsiz cue için eşleşme garanti değildir.
- `http://127.0.0.1:8765/ocr-inceleme`: insan görüntüyü inceler, doğru metni kabul eder veya düzeltir; kararlar `review-events.jsonl` içinde tutulur.
- **Doğrulanmış OCR veri kümesini dışa aktar**: yalnız geçerli, etkin, insan kabulü/düzeltmesi olan crop–metin çiftlerini `verified-ocr-dataset-.../` ve `manifest.jsonl` olarak dışa aktarır. Belirsiz, geri alınmış, stale, bozuk veya eşleşmeyen örnekler dışarıda kalır.
- `ocr_dataset_merge.py`: birden fazla doğrulanmış export'u köken zincirini koruyarak birleştirir.
- `ocr_dataset_metadata.py`, `ocr_experiment.py`, `ocr_crop_eval.py`, `ocr_video_eval.py`, `ocr_video_eval_templates.py`: metadata, grup split'i ve ölçüm/hazırlık hattı. **Bu araçlar tek başına model eğitmez.**
- `vtt-qa.py`, `regresyon/gt_gate.py`: mevcut sınırlı SRT regresyon ölçümü.
- Mevcut yerel AI OCR/çeviri incelemesi, çeviri belleği ve kullanıcı sözlüğü: eğitim ground truth'undan **ayrı** tutulur.

**Mevcut kanıtın sınırı:** `docs/LEARNING-SYSTEM-BASELINE.md` bir BLEND-S 240 sn örneğinde önceden belgelenmiş 3/201 ≈ %1,49 corpus CER ve 6 hizalı cue raporlar; test bu plan hazırlanırken yeniden çalıştırılmamıştır. Bu **genel başarı oranı değildir**. Temsili çok kaynaklı baseline hâlâ eksiktir.

**Review-pack sınırları:** README'ye göre paket başına en fazla 1.000 benzersiz crop, toplam 128 MiB, crop başına 8 MiB ve en uzun kenar 1280 px. Zaman kare indeksi/bildirilen FPS'den yaklaşık hesaplanır; VFR'de PTS hassasiyeti garanti değildir. Büyük veri hedefi için çok sayıda paket gerekir; sınırları sessizce kaldırmayın.

## 5. Veri sözleşmesi

### 5.1 Örnek düzeyi mantıksal alanlar

Aşağıdaki **önerilen mantıksal şema** mevcut `manifest.jsonl` formatının birebir kopyası değildir. Mevcut alanları kırmadan adaptör/metadata ile eşleyin:

```json
{
  "sample_id": "stable-id",
  "crop_path": "crops/example.jpg",
  "crop_sha256": "<sha256>",
  "source_id": "source-A",
  "episode_id": "episode-01",
  "frame_index": 12345,
  "start_ms": 120000,
  "end_ms": 122500,
  "ocr_engine": "rapidocr",
  "ocr_text": "Burada ne yapiyorsun?",
  "verified_text": "Burada ne yapıyorsun?",
  "review_status": "accepted_or_corrected",
  "review_event_id": "event-id",
  "rights_record_id": "local-rights-record"
}
```

- Örnek kimliği ve crop hash'i sabit/tekrarlanabilir olmalı; aynı görüntünün tekrarları izlenmeli.
- `ocr_text` tahmin, `verified_text` insan doğrulaması; birbirine kopyalanıp otomatik onaylanamaz.
- Kaynak/bölüm kimliği gerçek kökeni temsil eder; split'e uydurmak için sahte kimlik üretilemez.
- Crop eşleşmesi belirsizse `verified_text` atamayın.
- `rights_record_id` bir kayıt işaretçisidir, **hukuki izin kanıtı değildir**.
- Hatalı OCR, düşük güven, zor font, karanlık sahne, parlama ve iki satırlı örnekleri özellikle saklayın.
- Altyazı bulunamayan tam kareler OCR recognition etiketleriyle karıştırılmaz; detection benchmark'ına gider.

### 5.2 Kalite kuralları

1. İnsan doğrulaması görüntüde **gerçekte yazan metni** temel alır; çeviri veya dilbilgisi iyileştirmesi yapmaz.
2. Türkçe `ı/i`, `İ/I`, `ş`, `ğ`, `ç`, `ö`, `ü`, noktalama ve satır sonları için açık anotasyon politikası yazılır.
3. Görüntü okunamıyorsa `uncertain`; eğitim dışında.
4. Aynı anime/bölüm, mümkünse aynı kaynak serinin yakın varyantları split sızıntısına karşı grup olarak tutulur.
5. Exact/near duplicate kontrolü, sınıf/fon dağılımı ve train/test kaynak çakışması raporlanır.
6. Veri sayısı yalnızca **doğrulanmış ve kullanılabilir** örneklerle raporlanır. Ham SS sayısı ayrı tutulur.
7. Haklar ve izinler insan tarafından incelenir; anime kareleri, SRT, kaynak yolları, review eventleri ve hassas metadata varsayılan olarak **yerel kalır**. Public GitHub'a yüklenmez.

### 5.3 Veri toplama hedefleri

| Kapı | Veri | Yapılacak |
|---|---:|---|
| D0 | 100–300 | Etiket şeması, inceleme UX ve eşleşme hataları denetimi |
| D1 | 1.000 | İlk gerçek baseline ve küçük fine-tuning denemesi |
| D2 | 3.000–5.000 | Model adaylarını karşılaştırma, hata analizi |
| D3 | 10.000 | Daha güvenilir çok kaynaklı model seçimi ve held-out test |
| D4 | 20.000–30.000+ | Yalnız hata analizinin gerektirdiği çeşitlilik için genişletme |

Sayılar **araştırma hedefidir**, başarı garantisi veya asgari bilimsel eşik değildir. 35 bölüm × ~300 cue ≈ 10.500 örneği yalnız kaba kapasite hesabıdır; doğrulama, eşleşme, tekrar ve hak filtreleri sonrası sayı düşer.

## 6. Veri hazırlama: mevcut akış

1. Uygun kullanım hakkı olan video için UI'de **Yerel AI inceleme paketi oluştur** seçeneğini açın; veya `py -3 hardsub2srt.py "C:\\VIDEO\\input.mp4" -o "C:\\OUT\\output.srt" --review-pack`.
2. `http://127.0.0.1:8765/ocr-inceleme` ekranında crop ile OCR metnini tek tek karşılaştırın; kabul/düzelt/belirsiz kararlarını kaydedin.
3. **Doğrulanmış OCR veri kümesini dışa aktar** düğmesini kullanın.
4. Birden fazla pack/export'u `ocr_dataset_merge.py` ile birleştirin; dosyaları elle kopyalayıp lineage'i bozmayın.
5. `ocr_dataset_metadata.py` ile şablon üretin; gerçek `source_id`, `episode_id`, hak/izin kanıtlarını ve insan incelemesini doldurun.
6. `ocr_experiment.py` ile split ve değerlendirme planını sonuçlara bakmadan dondurun. Bu işlem eğitim yapmaz; durum `PREPARED_NOT_RUN` olabilir.
7. `ocr_crop_eval.py` ve `ocr_video_eval.py` için gerçek aday/baseline çıktıları hazırlayıp ölçün; değerlendirme belgelerindeki girdileri ve hash şartlarını izleyin.

**Önemli:** Birleşik export'un doğrulanması üst review-pack, SRT hash'i, crop hash'i ve review olaylarına dayanır. Paketleri senkronize edilmeyen yerel klasörde bütün hâliyle koruyun. Tam ve güncel komutlar için [FIRST-DATASET-QUICKSTART](../FIRST-DATASET-QUICKSTART.md) yetkili operasyon belgesidir; burada verilen komutlar şematik örnektir.

## 7. Ölçüm sözleşmesi (eğitimden önce)

**Ölçümler**
- **Recognition:** corpus CER = toplam Levenshtein karakter mesafesi / toplam GT karakteri; WER; exact-match cue; Türkçe karakter kırılımı.
- **Detection:** kaçırılan altyazı, yanlış pozitif, crop coverage. OCR recognition sonuçlarıyla birleştirip gizlemeyin.
- **End-to-end SRT:** eşleşmiş/kaçırılmış cue, başlangıç ve bitiş zaman hatası, tolerans içindeki cue yüzdesi, çakışma, bozuk SRT.
- **Performans:** bölüm süresi, GPU peak allocated/reserved/driver VRAM (hangisi ölçüldüyse belirt), RAM, crop/s, model yükleme süresi.
- **Ekonomi:** RTX 4090 GPU-saat, saatlik fiyat, toplam kira/depolama maliyeti.

**Raporlama:** Veri sürümü, source/episode sayısı, model sürümü, commit, cihaz, batch size, ön işleme, metrik normalizasyonu ve güven aralığı/dağılım kaydedilmeli. Micro ve episode-macro sonuçları ayrı sunulmalı. Başlangıç/bitiş zaman farkları kaynak referansla kıyaslanmalı. Yalnız kolay eşleşen cue'ların CER'ini raporlamak yanıltıcıdır; coverage mutlaka verilmeli.

**Türkçe CER standardı:** Unicode NFC normalizasyonu ve satır sonu/boşluk politikasını **ilk baseline'dan önce** yazılı kararlaştırın. Büyük/küçük harf ve noktalama duyarlılığını açıkça belirtin; hatayı saklamak için sonuçtan sonra normalizasyon değiştirmeyin.

**Kabul eşikleri:** Yeni model baseline ölçülmeden CER/hız için sayısal vaat verilmez. İlk rapordan sonra dondurulmuş kriterler belirlenir: ör. kabul edilebilir CER gerilemesi, minimum coverage, maksimum peak VRAM, bölüm başına süre ve zamanlama toleransı. Bunlar sonuç görülmeden sabitlenir.

## 8. Model geliştirme stratejisi

### 8.1 Neden sıfırdan LLM değil?
- OCR görüntü→metin problemidir; genel metin LLM'i tek başına görsel okuyamaz.
- 10.000 crop, büyük genel LLM pretraining için yeterli veri değildir.
- RTX 4090 eğitimde güçlü olsa da 4060 8 GB kullanım sınırını kaldırmaz.
- Küçük önceden eğitilmiş OCR recognizer'ı fine-tune etmek daha hesaplı ve ölçülebilir bir ilk deneydir.
- İleride dil modeli gerekiyorsa küçük açık ağırlıklı bir LLM'yi LoRA/QLoRA ile metin düzeltme/çeviri için **ayrı** özelleştirebiliriz; dil modelinin metni uydurma riski ayrıca ölçülür.

### 8.2 Aday değerlendirme
- **A:** PaddleOCR ailesinden Türkçe karakterleri destekleyen hafif recognizer. Model sürümü, lisans, preprocessing ve export desteği incelenmeli.
- **B:** TrOCR small/base veya eşdeğer açık ağırlıklı recognizer. Gerçek 4060 hız/VRAM karşılaştırması olmadan seçilmez.
- **C (gelecek):** Küçük VLM yalnız zor örnekleri ikinci aşamada doğrulama adayı; ana motor olması şart değil.
- Mevcut EasyOCR/RapidOCR mutlaka kontrol grubu ve fallback olarak korunur.

### 8.3 Deney tasarımı
1. Baseline ve sabit held-out test seti hazırla.
2. Küçük veriyle eğitim hattını doğrula (overfit sanity test, veri bozulması kontrolleri).
3. 1k/3k/5k/10k learning curve; sabit split ve mümkünse birden çok seed.
4. Augmentation: arka plan/kontrast, blur, compression, ölçek, kontur, font çeşitliliği; **metin etiketini değiştiren** dönüşümlerden kaçın.
5. Checkpoint'leri sürümle, eğitim metriklerini izle; validation ile seç, test ile karar kapısı uygula.
6. Error buckets: Türkçe diakritikler, satır kırılımı, noktalama, outline, parlak efekt, düşük çözünürlük, tespit hatası.
7. Kazanç yoksa model büyütmeden önce etiket/eşleşme ve veri dağılımı hatalarını araştır.

### 8.4 Vast.ai operasyonu
- Kiralama öncesi uygun CUDA/PyTorch sürümü, disk alanı, persistent storage, SSH güvenliği, maliyet ve checkpoint kurtarma planı yaz.
- Veri hakları/mahremiyeti kontrol edilmeden ham anime crop/SRT'lerini üçüncü taraf kiralık GPU'ya yükleme.
- Başlangıç smoke test'i: GPU görünürlüğü, 100 örneklik dataloader, 1 epoch, checkpoint kaydet/yükle.
- Uzun eğitimi yalnız smoke test sonrası başlat; log ve checkpoint'leri kalıcı depoya yedekle, işi durdurunca instance maliyetini kapat.
- GPU saati ile toplam maliyeti ayrı raporla; fiyatı sabit varsayma.

## 9. RTX 4060 8 GB entegrasyon şartnamesi

- **Seçilebilir backend:** kullanıcı/CLI mevcut EasyOCR, RapidOCR veya yeni GenesisAnime OCR seçebilmeli; varsayılanı değiştirmek ayrı karardır.
- **Uyumluluk:** mevcut SRT, review-pack, QA, UI ve batch iş akışları bozulmamalı.
- **Model paketleme:** ağırlık sürümü, checksum, lisans, yükleme ve offline fallback belgelenmeli; bilinmeyen ağırlıklar sessizce indirilmemeli.
- **Kaynak kullanımı:** inference + frame decoding + CUDA runtime birlikte 8 GB içinde ölçülmeli; 32 GB RAM'e güvenip GPU OOM görmezden gelinmemeli.
- **Optimizasyon sırası:** önce crop/batch ve preprocessing, sonra FP16/ONNX Runtime, gerekirse TensorRT; her optimizasyonun CER ve hız regresyonu ölçülmeli.
- **Uzun çalışma:** 20–25 dakikalık bölüm ve art arda çok bölüm için bellek sızıntısı, kaynak boşaltma, kuyruk, hata sonrası devam ve model yeniden yükleme testleri.
- **Kabul:** tek crop demo değil, gerçek uçtan uca video testi; ölçülen peak VRAM ve toplam süre ile yayın kararı.

## 10. Yol haritası ve kalite kapıları

| Faz | İş | Yaklaşık süre | Çıkış / kanıt |
|---|---|---|---|
| A · #14 | Repo envanteri + mevcut OCR baseline | 1–2 hafta | Reproducible benchmark raporu |
| B · #15 | Veri toplama, insan doğrulaması, lisans, split | 2–6+ hafta (paralel) | 1k→3–5k→10k doğrulanmış örnek, manifest |
| C · #16 | RTX 4090 fine-tuning ve model karşılaştırması | 1–3 hafta | Checkpoint, eğitim günlüğü, CER/WER |
| D · #17 | RTX 4060 optimizasyonu + HardSub2SRT entegrasyonu | 1–3 hafta | Backend, fallback, VRAM/hız testleri |
| E · #18 | Kör test, regresyon, pilot ve OCR v1 sürüm | 1–2 hafta | Release kararı, model kartı |
| F · #19 | LLM, çeviri, ASR ve GenesisAnime AI platformu | OCR v1 sonrası | Modüler tasarım ve yeni veri planı |

Süreler **tahmin**; insan etiketleme ve veri hakları en büyük belirsizlik. Faz B, Faz A ile paralel olabilir. Faz C, veri/benchmark kapısı olmadan “tamamlandı” sayılmaz. Faz F, OCR v1'i geciktirmemelidir.

## 11. Gelecek GenesisAnime AI modülleri

**OCR v1:** Türkçe hardsub crop→kaynak metin.  
**Language v1:** OCR tahmini + güven/bağlam→*önerilen* düzeltme. Girdi/çıktı sözleşmesi ve “kaynağa sadakat” testleri gerekir. Eğitim için yanlış OCR→insan düzeltilmiş doğru metin çiftleri kullanılabilir; salt SRT dil korpusu yeterli doğruluk etiketi değildir.  
**Translate v1:** doğrulanmış kaynak altyazı→hedef dil; hakları uygun paralel altyazı çiftleri, özel isim sözlüğü, bölüm bağlamı, insan kalite rubriği gerekir. Mevcut yerel çeviri belleğini yeniden icat etmeyin.  
**Speech v1:** ses→transkript + zaman; ASR modeli ve ses–metin eşleşmesi gerekir; OCR veri kümesi bunun yerine geçmez.  
**Platform:** `ModelProvider` benzeri ortak arabirim, model registry, sürüm/cihaz yönetimi, görev kuyruğu ve tek GPU'da modelleri sırayla yükleme. 8 GB'a bütün modelleri aynı anda sığdırma varsayımı yapılmaz.

**Uzun vadeli amaç tek marka/ürün deneyimidir; tek ağırlık dosyası değildir.** Tek multimodal model ancak ayrı benchmark ve maliyet gerekçesi varsa araştırılır.

## 12. Risk kaydı

| Risk | Önlem |
|---|---|
| OCR tahminini etiket sanmak | Yalnız insan onaylı `verified_text` eğitime |
| Crop–cue yanlış eşleşmesi | Mevcut manifestin benzersiz/tam eşleşme koşulu |
| Aynı bölümün train/test'e sızması | Kaynak/bölüm gruplu split, hash denetimi |
| Az sayıda fonta aşırı uyum | Farklı anime/font/sahne ve kör test |
| 4060 OOM / yavaşlık | Gerçek cihazda uçtan uca profil, hafif model |
| LLM'in yanlış düzeltmesi | OCR kaynak metnini sakla, öneri ve onay ayrımı |
| SRT zaman kayması / VFR | Bağımsız zamanlama benchmark'ı |
| Telif ve gizlilik | İnsan hak incelemesi, yerel veri, kamuya veri yüklememe |
| Vast.ai veri/gider kaybı | Smoke test, kalıcı checkpoint, bütçe limiti |
| Kod ajanının mevcut özellikleri yeniden yazması | Önce repo ve doküman envanteri, küçük PR |
| Yanıltıcı benchmark | Frozen split, coverage, kaynak bazlı sonuçlar |
| Gelecek özelliklerin OCR'ı geciktirmesi | F fazını v1 sonrası tut |

## 13. İnsanlar için işleyiş ve takip

- **Ana plan:** Issue #13, kararlar ve haftalık durum.
- **İş paketleri:** #14–#19; her PR ilgili issue'yu belirtir.
- **Durum dili:** Planned → In progress → Blocked/Review → Done. “Done” için test/rapor/PR kanıtı gerekir.
- **Haftalık rapor:** toplam ham crop, doğrulanmış çift, anime/bölüm sayısı, train/val/test grupları, baseline/adaya ait CER/WER, eksik cue, RTX 4060 VRAM/süre, RTX 4090 saat/maliyet, engeller ve sonraki karar.
- **Değişiklik yönetimi:** yeni hedef, kapsam veya kalite eşiği önerisi ana issue'da karar kaydı olarak yazılır. Eşikler test sonucuna göre geriye dönük ayarlanmaz.
- **Paylaşım:** GitHub'a yalnız kod, sentetik test fixture'ları, kişisel/telifli içerik taşımayan toplulaştırılmış ölçüm ve temizlenmiş metadata konur.

### İlk somut yapılacaklar

1. [#14] Mevcut baseline araçlarını, sürümleri ve 4060 ölçüm prosedürünü netleştir.
2. [#15] 100–300 örneklik insan doğrulama pilotu; review-pack → export → merge → split yolunu gerçek izinli veride doğrula.
3. [#14] Türkçe CER normalizasyonu, coverage ve timing ölçüm sözleşmesini dondur.
4. [#15] 1.000 doğrulanmış örnekle ilk dataset kartı.
5. [#16] Ancak bundan sonra RTX 4090 smoke test ve küçük fine-tuning.

## 14. AI kodlama ajanları için çalışma sözleşmesi

> Bu bölüm, gelecekte repoyu açacak AI ajanının kullanıcı niyetini kaybetmemesi için yazılmıştır.

**Göreve başlamadan:**
1. `README.md`, `docs/LEARNING-SYSTEM-BASELINE.md`, `docs/LEARNING-SYSTEM-PLAN.md`, `docs/FIRST-DATASET-QUICKSTART.md`, `docs/REVIEW-BUNDLE.md` oku.
2. İlgili issue (#14–#19) ve en son commit/PR'ları incele. Belgelenmiş varsayımları güncel kodla doğrula.
3. Mevcut export, review, merge, split ve eval araçlarını yeniden yazma; mevcut API ve testleri koru.
4. **Önce test/ölçüm planı**, sonra en küçük uyumlu kod değişikliği; ayrı branch/PR kullan.
5. Hakları doğrulanmamış veri üzerinde training veya üçüncü taraf GPU'ya upload yapma.
6. Gerçek video, SRT, crop, kişisel yerel path, erişim tokenı, özel checkpoint'i public GitHub'a commit etme.
7. Kullanıcı onayı olmadan pahalı GPU kiralama, veri silme, model indirme/yayınlama veya ana branch'e merge etme.
8. Sonuçları kanıtlarıyla raporla; deney koşulmadıysa `NOT_RUN`, veri yoksa `UNAVAILABLE`, hak incelenmediyse `RIGHTS_NOT_VERIFIED` yaz. Başarı uydurma.
9. OCR tahmini, AI önerisi, insan doğrulaması ve onaylı çeviriyi **ayrı güven düzeyleri** olarak koru.
10. Var olan UI/CLI sözleşmesini ve SRT zamanlamasını değiştiren PR'da açık regresyon testi iste.

**Her PR açıklaması:**
```md
## Amaç / bağlı issue
## Değişen dosyalar ve davranış
## Önceki davranışla uyumluluk
## Test komutları ve gerçek sonuçları
## Veri kaynağı, split ve hak durumu
## CER/WER/coverage/timing (ilgiliyse)
## RTX 4060 VRAM/süre (ilgiliyse)
## Güvenlik/gizlilik/lisans etkisi
## Geri alma planı
## Bilinen eksikler
```

**Yapılmaması gerekenler:** Kendi SRT'sini GT diye eğitmek; anime/bölüm split sızıntısı; tek BLEND-S örneğini genel başarı diye sunmak; eğitim kodu yokken “model eğitildi” demek; crop-only OCR modelinden otomatik tam kare tespiti beklemek; AI düzeltmesini sessizce SRT'ye yazmak; kullanıcı onayı olmadan public dataset/model yayımlamak.

## 15. Tanımlar

- **Hardsub:** Videonun pikseline gömülü altyazı.
- **SRT cue:** Zaman aralığı + altyazı metni.
- **Crop/SS:** Kareden alınmış altyazı görüntüsü; tam kareyle aynı şey değildir.
- **OCR:** Görüntüden metin okuma.
- **Ground truth (GT):** İnsan tarafından kaynak görüntüye göre doğrulanmış doğru metin.
- **CER/WER:** Karakter/kelime hata oranı; düşük daha iyidir.
- **Fine-tuning:** Önceden eğitilmiş modelin hedef veriyle uyarlanması.
- **LLM:** Büyük dil modeli; metin/bağlam işlerinde kullanılır.
- **VLM:** Görüntü ve metni birlikte işleyen model.
- **ASR:** Konuşmayı ses üzerinden yazıya dönüştürme.
- **Split leakage:** Aynı/çok benzer kaynakların eğitim ve testte bulunması.
- **Fallback:** Yeni model başarısızsa mevcut backend'e dönebilme.

---

**Belge bakım ilkesi:** Kararlar ve kapsam değiştikçe bu dosyayı PR ile güncelleyin. İş durumu ve güncel ölçüm bağlantıları için GitHub Issues'u kaynak kabul edin. Bu dosya teknik hedef ve sınırların kalıcı kaydıdır.
