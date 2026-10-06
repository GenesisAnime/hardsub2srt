# -*- coding: utf-8 -*-
"""
hardsub2srt — videodan altyazı çıkarıp SRT üretir.

İki mod:
  1) softsub: ffprobe altyazı streami görürse ffmpeg ile birebir çıkarır
     (metin ve zaman damgaları kaynaktan kopyalanır, OCR yok).
  2) hardsub: altyazı bandını beyaz-piksel maskesiyle frame frame izler,
     metin değiştiğinde OCR yapar, zaman damgalarını frame PTS'inden yazar.

OCR doğruluğu için her segment 3 varyantla okunur (binarize base / mag_ratio=2 /
low_text) ve konsensüs metni seçilir — tek parametrenin kör noktası böyle aşılır.
GPU doluluğu için segmentler --batch'lik gruplar halinde toplanır: tespit
görüntü başına, TANIMA tüm kutularla tek çağrıda (EasyOCR 1.7.2'nin readtext'i
liste girdiyi desteklemediğinden batch, detect+recognize aşamaları ayrılarak
kurulur; --batch 1 = eski tekli davranış).

conf<0.75 (düşük güven; --conf-thr ile değişir) segmentlerine ikinci motor oy
verir. Sıra --ocr ile
seçilir (varsayılan auto): PP-OCRv6 small (rapidocr >=3.9, onnxruntime —
paddle kurulumu GEREKMEZ) → PP-OCRv5 (latin tanıma modeli) → RapidOCR
(PP-OCRv4 nesli) → EasyOCR ['en']. Ölçüm (02.10.2026, 18 etiketli frame,
_v6-results.json): PP-OCRv6 small Türkçe satır CER'inde EasyOCR'ın (0.096 vs
0.116) ve PP-OCRv5'in (0.144) üstünde; ama birincil-motor eşiği (tr CER_line
<= 0.09) 0.006 kaçırıldığı için ana motor EasyOCR kalır, v6 ikinci görüş
zincirinin başına geçti (pp-ocrv5'in yerine: tüm metriklerde pp5'in üstünde
ölçüldü — pp5 --ocr paddle ile erişilebilir kalır). v6 model dosyaları ilk
kullanımda site-packages altına iner (~31 MB; det small 9.9 + rec small
21.2). İkinci motorun okuması konsensus havuzuna +1 aday olarak katılır;
ÖLÇÜLÜ İSTİSNA (8. tur, TAKAS_*): konsensus conf'u 0.30 altına çökmüşken
ve ikinci motor >=0.80 güvenle en az 0.50 farkla üstünken metin ikinci
motordan alınır (örnek ölçüm: 00:10:25 konsensus conf=0.20/CER 0.132 vs
PP-OCRv6 conf=0.93/CER 0.038). İstatistikte ocr_mode / second_engine /
second_engine_votes / second_engine_takas raporlanır.

Decode varsayılan olarak CPU'dadır (ölçüm: bu makinede NVDEC grab süreci
başına ~177 ms init bedeli ödüyor, 240 sn tarama 4s→7s yavaşladı); --nvdec
ile açılır. Açıkken ffmpeg hata verir ya da hiç frame üretmezse kalıcı
olarak CPU decode'a düşülür (stderr'e bir satır bilgi yazar).

⛔ "İkinci motor (rapidocr) CPU'da çalışıyor, 240 sn'de ~2 dk 44 sn
kaybediliyor" iddiası ÖLÇÜMLE ÇÜRÜTÜLDÜ (03.10.2026). Gerçek:
  * iddianın dayandığı "motor yüklendi → ilk uyarı" aralığı, RapidOCR'ın
    çalıştığı süreyi DEĞİL EasyOCR'ın ilk segmentleri işlediği süreyi ölçer.
  * RapidOCR yalnız conf<0.75 olan segmentlere DANIŞIR: BOCCHI 240 sn'de
    449 segmentin 47'si. Ölçülen katkı birkaç saniye mertebesindedir.
  * Sonuç (BOCCHI 240 sn, ayni makine, ardisik iki kosu):
        OCR 1. gecis  323 s (CPU) -> 310 s (GPU)
        OCR 2. gecis  335 s (CPU) -> 312 s (GPU)
        TOPLAM       658 s        -> 622 s     (36 s, %5.5 kazanc)
        duvar saati  704.1 s      -> 671.7 s   (%4.6 kazanc)
    CIKTI BAYT BAYT AYNI (68400084…), yani hız kazancı bedava geldi.
  * Yine de yapildi: onnxruntime-gpu 1.26.0 (CUDA-12 build) kuruldu,
    CUDA 12 runtime + cuDNN 9 DLL'leri torch yoluna eklendi, ve saglayici
    "istendi" degil "KULLANILDI" diye OLCULUP yaziliyor (_ort_cuda_probe).
    ORT 1.30/1.29 CUDA 13 istiyor (cublasLt64_13.dll) ve bu makinede
    YOK; Python 3.14 icin kurulabilen en yeni CUDA-12 build'i 1.26.0'dir.

Altyazı stili (bant konumu + renk/parlaklık) VARSAYILAN olarak videodan
algılanır: ilk/son %5 atlanıp ~20 kare örneklenir, alt 2/3'te örneklerin en az
%30'unda tekrar eden parlak metin satırları bant sayılır; metin çekirdeği saf
beyazsa thr, değilse tophat modu seçilir. --band-y / --band-h / --white-thr /
--mask elle verilirse YALNIZ o parametreler override eder, kalanları algılama
belirler; --no-auto ile tamamen eski sabitlere dönülür.

ÜST BANT (--ust-bant, VARSAYILAN KAPALI): anime'lerde diyalog dışı üst yazılar
(mesaj kutusu, tabela/levha, gün yeri, konum kartı) vardır; bunlar diyalog
SRT'sini kirletmesin diye AYRI dosyaya toplanır. Bayrak verilirse ana diyalog
taraması BİTTİKTEN SONRA ikinci bir geçiş yapılır:
  - bölge: cercevenin üst %35'i (y=0 → ~height×0.35), KENDI bant konumu;
  - thr/tophat modu bu bölge için BAĞIMSIZ seçilir (detect_style_upper) —
    üst yazılar kutulu/renkli/konturlu olduğu için diyalog bandından farklı
    mod çıkabilir; --mask/--white-thr/--band-* üst banda UYGULANMAZ;
  - statik eleme: taranan aralığın tamamı ikinci bir decode geçişinde
    taranır, "maskede hiç değişmeyen" pikseller (logo/watermark) maske'den
    düşürülür; hareketli karakter/tabela kalır (_static_drop_map);
  - OCR zinciri diyalogla AYNI (3 varyant konsensus + conf<--conf-thr ikinci
    görüş oyu); oto-eşik tekrar denemesi üst bantta YAPILMAZ (süre);
  - KONU KISITLAMASI (değişik C, onaylı karar): "üst bant = diyalog değil"
    önermesi her sürümde doğru değil. Ana geçişin algıladığı diyalog bandının
    ÜST kenarı, üst bölgenin ALT sınırı olur (UST_DIALOG_GAP payıyla).
    Diyalog bandı üst bölgenin dışındaysa hiç değişmez (strict no-op); üstte
    bırakacak yer kalmazsa tarama hiç yapılmaz, iki dosya da BOŞ yazılır
    (ters/sıfır yükseklikte crop ffmpeg'i kırar);
  - GÜVEN KAPISI (değişik B, onaylı karar): conf < UST_CONF_THR (0.30) bloklar
    <ad>_ust.srt'ye yazılmaz. Ölçüm (BLEND-S 240 sn): gerçek bloklar 0.76-1.00,
    çöp bloklar 0.010-0.023 → 12 bloktan 8'i kalır, ölçülen hiçbir gerçek blok
    düşmez. Düşen bloklar günlüğe ve stats.ust_bant'a sayı+guven+metin olarak
    yazılır. Ana diyalog SRT'si ETKİLENMEZ.
  - DİYALOG SIZINTISI — ÖLÇÜLDÜ, ELENMEDİ (03.10.2026): "üst bant = diyalog
    değil" denetimi iki sinyalle sınandı (konum ve zaman) ve İKİSİ DE
    AYIRICI DEĞİLDİ; ölçüm BLEND-S 240 sn'de tam kare satır profilinden
    yapıldı: gerçek üst merkez-oranı [0.0583..0.2069], üstte çizilen diyalog
    [0.0634..0.0699] → aralıklar İÇ İÇE (kesişim 0.0634-0.0699). Zaman
    örtüşmesi de ters işaret verir: ana diyalogla çakışan tek blok (#1) gerçek
    üsttür, çünkü konuşma üstte çizilirken bant boştur. Bu yüzden eleme
    kuralı GÖNDERİLMEDİ (yapılırsa ya gerçek üst ya da diyalog kalır).
    Yerine TEŞHİS: merkezi UST_SUS_Y_LO..UST_SUS_Y_HI içinde ve ana diyalogla
    zaman örtüşmesi olmayan bloklar "diyalog_supheli" olarak İŞARETLENİR —
    günlük, <ad>_ust.json (her blok: diyalog_ckismesi + diyalog_supheli) ve
    stats.ust_bant'a yazılır. HİÇBİR BLOK SİLİNMEZ. Ayrıntı ve tam ölçüm
    tablosu: UST_SUS_Y_LO/HI yorumu.
  - çıktı: <ad>_ust.srt (temiz, konum işareti YOK) + <ad>_ust.json
    (index / start / end / bbox_y_ratio / bbox_y_band_ratio / text / conf).
    Dosyalar koşulsuz yazılır (0 blok = boş dosya) ve geçişten ÖNCE silinir,
    böylece bayat bir koşunun çıktısı yeni koşunun sanılamaz.
  - stats.json'a ust_bant: {blok, statik_elenen, ...} eklenir — BAYRAK
    KAPALIYKEN anahtar hiç yazılmaz, yani bayraksız koşunun stats.json'u da
    bit-bit aynıdır.

--no-ayir-gurultu ve mikro bloklar (değişik A, hata düzeltmesi): _micro_cleanup
mikro blokları `merged`den çıkarıp `micro_moved`a koyar. Bu liste yalnız
--ayir-gurultu AÇIKKEN _ekran.srt'ye yazılıyordu; bayrak kapalıyken bloklar
HİÇBİRE yazılmıyor, yani sessizce siliniyordu (ölçüm: 1. Bolum 60 sn,
varsayılan 12+1=13 blok, --no-ayir-gurultu 12+0=12 blok; kaybolan blok
00:00:16,808 "##} #"). Artık bayrak kapalıyken mikro bloklar ANA SRT'ye
kronolojik olarak geri konur — bayrağın anlamı "ayırma"dır, "silme" değil.

03.10.2026 teşhis düzeltmeleri (ölçümlü):
  - Oto-eşik: çekirdek parlaklığı yalnız algılanan altyazı satırında ölçülür,
    eşik = min(246, çekirdek-6); OCR sonrası ortalama conf < 0.4 ise eşik
    5 düşürülüp tarama+OCR BİR KEZ tekrarlanır (krem/sarı resmi TR
    altyazıları — ölçüm 86tr: esik 250'de 44/45 çöp, 245'te 7/48).
  - Algılama None dönerse alt %25'te 3 aday banda 60 sn'lik hızlı tarama;
    bloklar çöp profilliyse "hardsub yok olabilir" uyarısı + stats.
  - Gürültü ayrımı (varsayılan açık): çöp profilli bloklar ana SRT'den
    <ad>_ekran.srt'ye TAŞINIR (silinmez); --no-ayir-gurultu = eski davranış.
  - Zaman çakışan çift okumalar tek blokta uzlaşır; ardışık merge gap 0.7.
  - <0.4 sn'lik mikro bloklar komşusuna katılır, katılamayan _ekran'a gider.
  - Satır gruplama eşiği font yüksekliğine ölçeklenir (28px sabiti değil).

OCR, CUDA'lı torch kuruluysa GPU'da değilse CPU'da çalışır; --cpu ile GPU
zorla devre dışı bırakılır. --lang virgüllü liste alır (ör. tr,en).

Kullanım:
  py -3 hardsub2srt.py "video.mp4" -o "out.srt"
  py -3 hardsub2srt.py "video.mp4" -o "out.srt" --limit-seconds 120   (test)
  py -3 hardsub2srt.py "video.mp4" -o "out.srt" --batch 32            (GPU batch)
  py -3 hardsub2srt.py "video.mp4" -o "out.srt" --qa 12 --qa-dir qa   (doğrulama montajı)
  py -3 hardsub2srt.py "video.mp4" -o "out.srt" --lang tr,en          (çok dil)
  py -3 hardsub2srt.py "video.mp4" -o "out.srt" --cpu                 (OCR'u CPU'da zorla)
  py -3 hardsub2srt.py "video.mp4" -o "out.srt" --nvdec               (NVDEC decode)
  py -3 hardsub2srt.py "video.mp4" -o "out.srt" --ocr paddle          (2. görüş: yalnız PP-OCRv5)
  py -3 hardsub2srt.py "video.mp4" -o "out.srt" --ocr easyocr         (eski 2. görüş zinciri)
  py -3 hardsub2srt.py "video.mp4" -o "out.srt" --ust-bant \
      --qa 4 --qa-dir _ust-qa                                     (üst bant: ayrı SRT)

QA montajı tam koşulda (--limit-seconds 0) varsayılan AÇIK (6 örnek); --qa 0
ile kapatılır. Test koşularında (--limit-seconds > 0) varsayılan kapalıdır.

04.10.2026 — BLEND-S GT düzeltme turu (4 düzeltme, ölçümlü):
  1) TÜRKÇE POST-FIX (--duzelt, VARSAYILAN AÇIK; --no-duzelt kapatır):
     OCR metni ana SRT'ye yazılmadan sözlükle onarılır — diakritik
     restorasyonu (s↔ş/c↔ç/g↔ğ/ı↔i, 1-2 nokta), harf-yutma (1 harf ekleme),
     harf-değiştirme (1 harf), kaynaşma ayırma (2 parça), noktalama
     artıkları ve apostrof-yutma tablosu (06.10.2026, E1074: 'FilFin' ->
     'Fil'in'). Her aday sözlük frekansıyla ölçülür: kazanan aday ikinciye
     karşı >=10x egemen olmalı. Her değişiklik <ad>.duzeltme-gunlugu.json'a
     yazılır (Kural 1: sessiz değişiklik yok). UI'daki 'Öğret' ile yazılan
     kullanici-sozlugu.txt (`eski<TAB>yeni`) kuralları da aynı katmanda
     uygulanır; kullanıcı beyanı SON sözdür (ham ve post-fix yüzeyinde
     denenir). Sözlük: turkce-sozluk.txt
     (OPUS OpenSubtitles v2018 tr frekans listesi, freq>=5, ~987K kelime);
     dosya yoksa gömülü ~300 kelimelik çekirdek devreye girer.
     Ölçüm (BLEND-S S01E01, _gt2-blends.vtt): diyalog CER %5.41 -> %3.15
     (36 -> 21 edit / 666 karakter; kalibrasyon _pf-kalibrasyon3.py).
  2) EKRAN-YAZI SINIFLANDIRICISI (_split_noise üstüne): mevcut conf/süre/
     harf profili KORUNUR; eklenen kurallar bloğu <ad>_ekran.srt'ye taşır:
     (a) CJK oranı >%15; (b1) >=4-harfli kelimelerin <%30'u sözlükte VE
     sözlük-kaplama <%50 VE (conf<0.15 VEYA orta-harf anomalisi); (b2)
     jenerik: >=5 kelime VE pay<%60 VE conf<0.15; (c) <=2 harf dışı tamamen
     sembol. Denge (kalibrasyon, önceki SRT'nin 310 bloğu): GT'nin 4 ekran
     bloğu (OurpıIse / Japonca kredi / inya / Vietnamca jenerik) ayrıldı,
     20 GT diyalog bloğunun HİÇBİRİ ayrılmadı; yalnız 1 çöp blok (#300)
     eklendi. conf<0.15 tabanı probe2 ölçümü: 3 ekran bloğunun conf'u
     0.033-0.077, GT diyalog conf'larının en düşüğü 0.248.
  3) FADE-IN ZAMAN RAFİNASYONU (scan_band): segment medyan dolgusunun
     %70'inden az dolguyla başlayan segmentlerin başlangıcı, dolgunun %70'i
     geçildiği ilk kareye taşınır (ölçüm: B12 start ~1.4 sn erkendi).
     stats.fade_duzeltilen sayar.
  4) UPSCALE2X EŞİĞİ 45 -> 50 px (UPSCALE_TEXT_H): algılanan satır
     yüksekliği <50 px ise OCR girişi 2x. Ölçüm (_pf-texth-probe.json):
     4 test videosunda text_h 60-80 px -> dördünde de davranış değişmedi.

04.10.2026 — KULLANICI SÖZLÜĞÜ ENTEGRASYONU: UI'daki 'Öğret' hedefi
kullanici-sozlugu.txt (`eski<TAB>yeni`) post-fix zincirine bağlandı; kural
ham OCR ve post-fix yüzeyinde uygulanır, günlüğe "kullanici" sınıfıyla
yazılır (kullanıcı beyanı son söz; --no-duzelt kapatır).

04.10.2026 — 8. TUR (algılama kalitesi; ölçüm zemini _kal-oku.py):
  * DÜŞÜK-KONSENSUS TAKASI: ikinci motorun okuması, konsensus fiilen
    çökmüşken (conf<0.30) ve net üstünken (>=0.80, fark>=0.50) metni
    devralır; metin kısa parça olamaz (>=%60 uzunluk — tam bölüm koşusunda
    ölçülen 'buldum.' içerik kaybını engeller) — stats.second_engine_takas
    sayar ve <ad>.takas-gunlugu.json'a eski→yeni + iki conf değerini yazar
    (sessiz değişiklik yok).
  * ZAYIF SÖZLÜK GİRDİSİ: OPUS listesindeki seyrek çöp girdiler
    (ör. 'gibb' 133) artık erken-dönüşle korunmaz; tek-harf-değişimli
    egemen komşu (>=100x, ör. 'gibi' 2.430.246) kazanır.
  * --jenerik "DD:SS-DD:SS"[, ...]: kullanıcı beyanıyla zaman aralığı;
    aralıkta BAŞLAYAN bloklar ana SRT'ye yazılmaz, <ad>_ekran.srt'ye
    TAŞINIR (silinmez). Kayan kredi içerik kuralıyla yakalanamadığı için
    (Vietnamca jenerik: sözlük kapsamı %82 > eşik) zaman kuralı kalıcı
    çözümdür; stats.jenerik raporlar.

04.10.2026 — İKİ DÜZELTME (ölçümlü):
  1) SATIR-DÜZEYİ SFX TEMİZLİĞİ (_satir_sfx_temizle, post-fix zincirinin
     ilk adımı): blok-harf-oranı yüksekken (>0.5) blok-bazlı kurallar
     (_split_noise/_sfx_ayir) blok İÇİNE gömülü SFX satırını kaçıracak —
     ölçülen vaka: BOCCHI S01E08 blok 166 "Kit-aura! Kit-aura!\nV;+ 4+ @ R
     k7" (blok harf-oranı 0.63). Kural: satır harf-oranı <0.4 VE boşluksuz
     uzunluk <30 VE (sembol ağırlıklı VEYA sözlüksüz) VE blokta en az bir
     Türkçe-sağlam satır (_diyalog_kirintisi) -> satır DÜŞÜRÜLÜR, günlüğe
     "sfx-satir" sınıfıyla yazılır. Korumalar: tek satırlık bloklar, CJK-
     çoğunluk satırlar ve tümü-çöp bloklar dokunulmaz.
  2) TAKAS × CJK KREDİ (TAKAS_CJK_ILK_CONF=0.45): 8. tur takası metni
     gerçek CJK'ya çevirince Türkçe süzgeci (word_re) bloğu İKİ dosyadan
     da düşürüyordu — ölçülen vaka: BLEND-S 00:03:58 Japonca kredisi (ilk
     konsensus 0.019 → PP-OCRv6 0.998; kanıt _kal2-blends takas-gunlugu +
     vtt-qa ekran_eslesmeyen). Kural: İLK konsensus conf'u <0.45 ve takas
     adayı CJK-çoğunlukluysa takas YAPILMAZ; blok ikinci motorun CJK
     okumasıyla <ad>_ekran.srt'ye yönlendirilir (istatistik etiketi
     "takas-cjk", karar <ad>.takas-gunlugu.json'da). Yüksek-conf CJK
     blokları ("OTURMAK" panelleri) ve karışık-dilli krediler (CJK oranı
     ~0.14) etkilenmez.

04.10.2026 — BLOK GÜVEN DÖKÜMÜ (ui_server v1.3 "Kontrol Et" paneli + toplu
  pano zemini): her koşu sonunda <ad>.bloklar.json yazılır — ana SRT +
  _ekran bloklarının TEK dizideki dökümü: {index, start, end, metin, conf,
  kaynak: "ana"|"ekran"}. start/end, SRT'ye yazılan kırpılmış zamanların
  aynısıdır (sonraki bloğun başına kırpma dahil); index, kendi dosyasının
  0 tabanlı blok sırasıdır (ui srt_parse ile aynı). conf, OCR konsensus
  güvenidir (post-fix metni değiştirse bile değişmez), 4 haneye yuvarlanır.
  YALNIZ EK DOSYA: SRT / _ekran / stats / günlük çıktılarında hiçbir
  değişiklik yoktur (parite korunur). Eski çıktılarda bu dosya YOKTUR; ui
  paneli conf'suz listeyle zarifçe düşer ve "yeniden koşun" der.

05.10.2026 — WANO TEŞHİS DÜZELTMELERİ (ölçümlü; inceleme _op-rapor.md,
  One Piece 124 bölümlük batch E1017-E1080 çöküşü):
  1) DAR BANT SAĞLIK KONTROLÜ (_dar_bant_genislet): algılanan bant
     h<150px (1080p tabanında ölçekli) ise taban ekran tabanında kalmak
     şartıyla YUKARI genişletilir (hedef ~180px) — ölçülen arıza
     imzaları E1071 (976,104) · E1007 (952,128) · E1074/75 (940,140)
     iki satırlı diyaloğun üst satırını kesiyordu (95px fontta iki satır
     ~200px). h=184/196 olanlar tetiklenmez (tek satır rahat sığar);
     düzeltme yalnız şüpheli-dar durumu etkiler. Tetiklenen koşulda
     band_source="auto-genisletildi" yazılır. Tetik eşiği görevdeki
     "<140" değil 150: E1074/75'in ölçülen h=140'ı da bu arıza ailesinden.
  2) ADAY-TARAMA EŞİK ÖLÇÜMÜ (_candidate_band + _serit_cekirdek): fallback
     yolu çekirdek ölçümü OLMADAN sabit white_thr=250 kullanıyordu
     (ölçümlü vaka E1049/E1050: detect_style None döndü → aday bant
     (810,270) + thr 250 → diyalog büyük ölçüde kaçtı; oto-eşik denemesi
     de hiç tetiklenmedi çünkü başlık kartı/künye conf≥0.4 okuyordu —
     stats.auto_esik_kayit.denendi=false). Artık her aday bandın kendi
     metin çekirdeği ölçülür: core≥248 → thr=min(246,core-6), değilse
     tophat modu — detect_style'ın Düzeltme 1a/1b kuralıyla birebir aynı.
  3) VERİM-SANITY UYARISI: koşu sonu blok/dk<4 VE low-conf oranı >%50
     ise stderr'e yol gösteren uyarı + stats.verim_uyari=true (E1049:
     17 blok / ~24 dk, low-conf 10/17 — koşu "başarılı" bitiyordu).
     DAVRANIŞ DEĞİŞMEZ; normal koşunun stats.json'u yeni anahtar
     eklenmeden aynı kalır (altyazisiz-rip deseni).
  4) VIDEO KİMLİĞİ: stats.video_kimlik = {ad, mtime, boyut, sha1_ilk_1MB}
     — ölçümlü vaka E02: SRT farklı bir ripten üretilmiş, video dosyası
     sonradan değişmişti (mtime 30.09); kayıtta hiçbir iz yoktu ve
     uyumsuzluk ancak görsel örneklemeyle yakalandı. sha1 yalnız İLK
     1 MB (tam-dosya hash'i 400+ MB videoya ikinci tam okuma katar;
     ilk-1MB + boyut + mtime üçlüsü rip değişimini ayırt eder). Eski
     "video" anahtarı ad olarak KORUNUR (mevcut okuyucular bozulmaz)
     — TUR 2'DE DEĞİŞTİ: "video" artık kimlik SÖZLÜĞÜDÜR (aşağıda).

05.10.2026 — WANO DÜZELTME TURU 2 (ölçümlü; tur 1'in değerlerini günceller):
  1) EŞİK TAVANI 246 → 240 (THR_TAVAN sabiti): Eleber ripinde glif
     çekirdeği gri 242-252 ölçüldü; eski koşuda fiilen kullanılan 250
     eşiği maskenin %41'ini eritiyordu. Piksel sayımı (E1049 t=715,
     _wn-full-1049-715.png, bant 810-1080): >240: 5008 · >246: 4564 ·
     >250: 2939 piksel — _wn-mask-1049-715-thr240.png katı glifler,
     -thr250.png paramparça. min(240, core-6) artık üç yerde:
     detect_style, detect_style_upper, _candidate_band.
     DEFAULT_WHITE_THR 250 → 240 (--no-auto ve algılama-başarısızlığı
     yolunun tabanı). OTO-EŞİK TETİĞİ GÜÇLENDİRİLDİ: ort. conf < 0.55
     VEYA low-conf oranı > %40 (önce yalnız ort < 0.4) → eşik -10
     (önce -5). Ölçümlü vaka E1049: low-conf 10/17 (%59) iken deneme
     hiç tetiklenmemişti (auto_esik_kayit.denendi=false).
  2) DAR BANT TETİĞİ 150 → 180: ölçüm (E1049 t=1145,
     _wn-full-1049-1145.png, thr240 satır profili ≥8 piksel): iki
     satırlı diyalog y=945..1039 (üst satır 945-975, alt satır
     1000-1039). E1071'in ölçülen (976,104) bandı üst satırı 945'ten
     BİÇER (31 px kayıp); E1007'nin (952,128) bandı 7 px biyer. Taban
     180 → y=900, üst satıra 45 px marj. h=184/196 bölümler HÂLÂ
     tetiklenmez (parite korunur).
  3) BEYAZ-FON KORUMASI (_beyaz_fon_payi): görev kuralı "bant dolgusu
     > %15 → tophat" ÖLÇÜMLE YANLIŞ ÇIKTI: BLEND-S (sağlıklı video)
     ort. bant dolgusu %20.5, max %69 (t=742 chibi beyaz-fon sahnesi
     + diyalog — mevcut thr yolu bunu CER %2 ile karşılıyor). Ayırışan
     ölçüm AĞIR KARE PAYI: dolgusu >%50 olan örnek karelerin payı
     > %15 ise video tophat'a düşer. Ölçümler (esik 240): E1075 t=1420
     Gear-5 karesi dolgu %54.8 (ağır kare; 4 karenin 1'i → pay ~%25),
     E1049/E1050/E1051/E1074 kareleri %0.0-2.2, E1007 %0.0-4.5, E02
     örnekleri max %34.7, 86tr max %35.2 (ağır kare 0). Sabitler:
     BEYAZ_FON_KARE_DOLGU=0.50, BEYAZ_FON_PAY=0.15. Geçişte mevcut
     tophat yolu + ham-bant OCR (use_raw) otomatik devreye girer;
     karar stderr'e "[auto] beyaz fon tespiti → tophat moduna geçildi"
     ile, stats'a beyaz_fon_gecis=true ile yazılır (yalnız geçiş
     VARKEN — normal koşunun stats.json'u aynı kalır).
     TUR 3 (05.10.2026): ÖLÇÜMLÜ EK — ilk pencere (5%-95%) video sonunu
     kapsamıyor; E1075 Gear-5 sahnesi (son %5) korumaya hiç görünmüştü.
     _beyaz_fon_payi artık İKİ PENCERE döndürür (genel + son-11s/37 örnek);
     karar her ikisine ayrı ayrı uygulanır, tetikleyen pencere log'da
     "pencere=genel|son" yazar. Ağır-kare tanımı (> %50) ve %15 eşiği
     AYNI kaldı; pencere uzunluğunun "son %15" DEĞİL mutlak 11s olma
     gerekçesi (flaş konum ölçümleri + matematik) BEYAZ_FON_SON_PENCERE_SN
     yorumunda. Simülasyon: E1075 %21.6 TETİK · BLEND-S %2.7 · 86tr %0.0.
  4) VIDEO KİMLİĞİ stats["video"] SÖZLÜĞÜ: tur 1'in ayrı "video_kimlik"
     anahtarı yerine "video" artık {ad, mtime, boyut, sha1_ilk_1MB}
     sözlüğü (E02 vakasının kalıcı önlemi; görev spesifikasyonu).
     "video"yu düz isim olarak okuyan kod YOK (ölçüldü: _op-katman1.py
     kendi "video" alanını üretiyor, ui_server okumuyor); isim bilgi
     kaybı olmasın diye sözlüğün "ad" alanında yaşar.
"""

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
import time
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# --no-auto ile kullanılan eski sabitler (1080p)
DEFAULT_BAND_Y = 860
DEFAULT_BAND_H = 220
DEFAULT_WHITE_THR = 240    # 05.10.2026 tur 2: 250 -> 240 (Eleber glif
                           # cekirdegi 242-252; 250 maskenin %41'ini eritiyor)
DEFAULT_MASK = "thr"
DEFAULT_TOPHAT_THR = 120   # scan_band varsayılanı; --tophat-thr ile örtülür

# 05.10.2026 Wano tur 2: thr modu eşik tavanı. Formül min(THR_TAVAN, core-6)
# — Eleber ripinde glif çekirdeği gri 242-252; eski koşunun fiilen 250 eşiği
# maskeyi %41 eritiyordu (piksel sayımı E1049 t=715 bant 810-1080:
# >240: 5008 · >246: 4564 · >250: 2939 piksel; _wn-mask-1049-715 PNG çifti).
THR_TAVAN = 240

# 05.10.2026 Wano tur 2: beyaz-fon koruması. Görev kuralı "bant dolgusu
# > %15 → tophat" ÖLÇÜMLE ÇÜRÜLDÜ: BLEND-S (sağlıklı) bant dolgusu ort.
# %20.5 / max %69 (t=742 chibi beyaz-fon + diyalog; mevcut thr yolu CER %2
# ile karşılıyor) — ortalama kuralı sağlıklı videoyu tetiklerdi. Ayrışan
# ölçüm AĞIR KARE PAYI: dolgusu >%50 olan örnek karelerin payı. E1075
# t=1420 Gear-5 karesi %54.8 (ağır; 4 karenin 1'i → pay ~%25); E02/86tr/
# BLEND-S 240sn/E1049/E1074/E1007 ölçümlerinde ağır kare 0. Pay > %15 →
# video tophat moduna düşer (mevcut tophat yolu + ham-bant OCR).
BEYAZ_FON_KARE_DOLGU = 0.50   # kare dolgusu üst sınırı (üstü = ağır kare)
BEYAZ_FON_PAY = 0.15          # ağır kare payı eşiği (üstü = beyaz-fon videosu)

# 05.10.2026 tur 3 — SON PENCERE eki: ilk pencere (5%-95%) video SONUNDAKI
# beyaz-fon sahnelerini kaçırmış. Ölçümlü vaka E1075 (Gear-5): sahne t=1420-1430,
# süre 1430.7s → örnek tavanı 0.95·duration=1359s'le 62s önce kesiyor; koruma
# sahneyi hiç görmüyor, thr maskesi beyaz fon+metni tek blob yapıyor (OCR çorbası).
# Son-bölge dolgu ölçümleri (0.3-0.5s ızgara, ağır-kare tanımı AYNI: >%50):
#   E1075  band(940,140) esik 240: ağır flaş ~1420.3-1423.1 (DOLGU TİTREK:
#          32-72% arası dalgalanır; ağır-toplam ~1.7s), videonun sonuna 7.6-10.4s
#   BLEND-S band(820,260) esik 240: düz beyaz kart ~1402.3-1403.8 (%70.2 SABIT,
#          ağır-toplam ~1.5s), sonuna 16.3-17.8s — SAĞLIKLI video, tetiklememeli
#   86tr   band(768,312) esik 240: tek-kare flaş ~1413.8-1414.3 (%73.7), sonuna
#          13.1-13.6s — SAĞLIKLI video, tetiklememeli
# "Son %15" (duration·0.15 ≈ 213s) penceresi bu flaşlar için MATEMATİKSEL olarak
# çalışamaz: düzgün örneklemede pencere-payı ≈ ağır-süre/pencere = 1.7/213 ≈
# %0.8 — %15 eşiğine asla ulaşamaz. Pencere uzunluğu bu yüzden MUTLAK saniye:
# 11s, üç videonun flaş KONUMLARINI ayırır (sağlıklı flaşlar son-11s penceresinin
# DIŞINDA kalır: BLEND-S'ye 5.3s, 86tr'ye 2.1s pay; E1075'in flaşı içeride).
# Örnek sayısı 37 (aralık ~0.3s) — titrek ağır-dolgu koşularını çözümlemek için;
# deterministik simülasyon ölçümü (exact-frame, 05.10.2026):
#   E1075  8/37 = %21.6 > %15 → TETİK (pencere="son")
#   BLEND-S 1/37 = %2.7 · 86tr 0/37 = %0.0 → tetik yok (parite korunur)
# Sınır: video sonundan 11s'den DAHA önce biten kısa flaşlar bu pencereye
# düşmez — onlar ilk pencerenin (5%-95%) ve diğer kuralların kapsamındadır.
BEYAZ_FON_SON_PENCERE_SN = 11.0  # son pencere uzunluğu (mutlak saniye)
BEYAZ_FON_SON_ORNEK = 37         # son pencere örnek sayısı (~0.3s aralık)

# OCR girişi 2x büyütme eşiği (04.10.2026: 45 -> 50 px, görev fix 4).
# ÖLÇÜM (_pf-texth-probe.json): 4 test videosunun algılanan text_h'i
# 60-80 px (BLEND-S 70, ep1 60, ep2 60, 86tr 80) -> bu değişiklik dört test
# videosunda davranış DEĞİŞTİRMEZ; 45-49 px bandındaki videolar için.
UPSCALE_TEXT_H = 50

# --ust-bant: üst bölge = çerçevenin üst %35'i (y=0 → ~height×0.35).
# Diyalog bandı (varsayılan y~828-860) bundan çok aşağıda olduğu için
# bölgeler kesin ayrışır; üst yazılar (mesaj kutusu/tabela/gün yeri) ana
# diyalog SRT'sine değil, <ad>_ust.srt'e gider. BAYRAK VARSAYILAN KAPALI.
UST_REGION_FRAC = 0.35

# DEĞİŞİK B (onaylı kullanıcı kararı): üst bant çıktısında guven kapısı.
# Ölçüm tabanı: BLEND-S S01E01 240 sn, --ust-bant (03.10.2026):
#   12 blok -> guvenler 0.76/0.76/0.77/0.88/0.96/0.96/0.99/1.00 (GERÇEK)
#             ve 0.0101/0.0113/0.0124/0.0234 (ÇÖP, 4 blok).
# Ayrım TAM: en düşük gerçek 0.7595 > en yüksek çöp 0.0234. 0.30 eşiği
# 12 -> 8 blok verir ve ölçülen 8 gerçek bloğun HİÇBİRİNİ düşürmez.
UST_CONF_THR = 0.30

# DEĞİŞİK C (onaylı kullanıcı kararı): üst tarama, ana geçişte ALGILANAN
# diyalog bandına kıstırılır — üst bölgenin alt sınırı diyalog bandının üst
# kenarına inmelidir (üstteki bölge diyalogsa "üst bant" diyalog olmaktan
# çıkar). GAP: kenar çizgisinin kendisinin OCR'a girmemesi için pay.
# UST_MIN_H: kıstırma sonrası bölge bu kadarın altındaysa TARAMA YAPILMAZ
# (sıfır yükseklikte crop=iw:0 ffmpeg'i kırar; bos dosya yazılır).
UST_DIALOG_GAP = 8
UST_MIN_H = 48

# =====================================================================
# ÜST BANT — DİYALOG SIZINTISI TEŞHİSİ (03.10.2026, ÖLÇÜMLÜ)
#
# AMAÇ: "_ust.srt yalnız gerçek üst yazı içerir, diyalog sızmaz".
# Bu satırlar o denetimi ÖLÇTÜ ve SONUÇ: ölçülen iki sinyal de
# ayırıcı DEĞİLDİR; bu yüzden HİÇBİR BLOK SİLİNMEZ.
#
# ÖLÇÜM (BLEND-S S01E01, 0-240 sn, --ust-bant): guven kapısından geçen
# 8 bloğun her biri için TAM KARE (1080 satır) beyaz-çekirdek satır
# profili ölçüldü (eşik 246 = stats.ust_bant.esik). "merkez oranı" =
# metin satırının orta noktasının y/1080 değeri.
#
#   #  metin                                  merkez   sınıf
#   1  İş Başvurusu Sonuç Bildirisi           0.0644   GERÇEK ÜST
#   2  Demek bu işe de alınmadın, ha?         0.0690   DİYALOG
#   3  Maika..                                0.0634   DİYALOG
#   4  Hayır! Kendi kazandığım para ...       0.0699   DİYALOG
#   5  Aksi halde                             0.0667   DİYALOG
#   6  TREN GECİKECEK                         0.0583   GERÇEK ÜST
#   7  Başka bir ülkeden midir ki?            0.0685   DİYALOG
#   8  Service                                0.2069   GERÇEK ÜST
#
#   GERÇEK ÜST aralığı : [0.0583 .. 0.2069]
#   DİYALOG    aralığı  : [0.0634 .. 0.0699]
#   KESİŞİM             : [0.0634 .. 0.0699]
#
# YANİ GERÇEK ÜST ve DİYALOG AYRIK ARALIKLAR DEĞİL, İÇ İÇE GEÇİYOR. Gerçek
# üst "TREN GECİKECEK" (0.0583) beş diyalog bloğundan da DAHA YUKARIDA;
# gerçek üst "İş Başvurusu" (0.0644) diyalog aralığının İÇİNDE. Öyleyse
# "y_ratio < EŞİK -> düşür" kuralı: EŞİK hangi değer olursa olsun ya
# gerçek üst düşer ya da diyalog kalır. Eşik 0.0699'un altındaysa #1 (gerçek
# üst) düşer; 0.0699 ve üstüyse beş diyalog bloğunun HİÇBİRİ düşmez.
#
# ZAMAN ÖRTÜŞMESİ DE AYIRICI DEĞİL — tersine ters işaret verir. Ana
# diyalog SRT'siyle çakışan tek üst blok #1'dir ve o GERÇEK ÜST'tür.
# Kalan 7 blok çakışmaz; çünkü bu yayında konuşma üstte çizilirken
# diyalog bandı BOŞTUR (bant yalnız TEK konumu tarar). Yani "çakışma yok"
# demek diyalog demek DEĞİL, gerçek üst de çakışmayabilir.
#
# SONUÇ: eleme kuralı GÖNDERİLMEDİ. UST_SUS_Y_LO/HI yalnız TEŞHİS
# bandıdır; blok düşürülmez, işaretlenir (günlük + <ad>_ust.json +
# stats.ust_bant). Ölçüde bu bandın İÇİNDE kalan 8 bloğun 6'sı düşer
# (5 DİYALOG + 1 GERÇEK ÜST) — yani bayrağın KENDİSİ de tek başına
# karar veremez; bu yüzden karar değil, teşhis.
UST_SUS_Y_LO = 0.060     # ölçüm: en düşük DİYALOG merkezi 0.0634
UST_SUS_Y_HI = 0.072     # ölçüm: en yüksek DİYALOG merkezi 0.0699

# NVDEC: main içinde yalnız --nvdec verilirse ["-hwaccel", "cuda"] olur
# (varsayılan: CPU decode); başarısızlık halinde kalıcı olarak []'e düşer.
HWACCEL = []

# 3 OCR varyantı: binarize base / 2x iç büyütme / düşük metin eşiği
OCR_VARIANTS = (dict(), dict(mag_ratio=2), dict(low_text=0.3))

LOW_CONF_THR = 0.75    # bu eşiğin altındaki segmentlere ikinci motor oy verir
                       # (02.10: 0.6 idi; 03.10 ölçümüyle 0.75 — orta-güvenli
                       # karışmaları ikinci görüşle düzeltir, --conf-thr ile
                       # değiştirilebilir)

# --- 8. tur (04.10.2026): DÜŞÜK-KONSENSUS TAKASI --------------------------
# Ölçüm zemini _kal-oku.py (BLEND-S GT 24 cue; aynı karede 3-varyant
# konsensus vs PP-OCRv6):
#   00:10:25 "ama sana göre değil gibb seni zolamayacağım 1 Mafka"
#             konsensus conf=0.20 CER=0.132   ← fiilen çökmüş okuma
#             PP-OCRv6   conf=0.93 CER=0.038   ← neredeyse birebir doğru
#   00:03:58 Japonca kredi   konsensus conf=0.02 CER=1.037
#             PP-OCRv6 conf=1.00 CER=0.074
#   00:22:14 Vietnamca jenerik konsensus conf=0.01 CER=0.371
#             PP-OCRv6 conf=0.89 CER=0.138
# AMA "PP her zaman daha iyi" DEĞİL: 00:05:50'de konsensus conf=0.50
# CER=0.143 iken PP conf=0.87 CER=0.286 (daha kötü); 00:06:08'de PP "P"
# (conf=0.84, CER=1.0). Bu yüzden takas ÜÇ ŞART BİRLİKTEyken yapılır ve
# ölçülen sette yalnız kazanan vakaları tetikler; kaybedenler dokunulmaz.
TAKAS_KONSENS_CONF = 0.30   # konsensus bu eşiğin altındaysa "çökmüş" say
TAKAS_IKINCI_CONF = 0.80    # ikinci motor bu güvenin altındaysa karışma
TAKAS_CONF_FARK = 0.50      # ayrışma net değilse (fark küçük) karışma
TAKAS_UZUNLUK_ORANI = 0.60  # ikinci motor metni konsensusun bu oranından
                            # KISA olamaz — tam bölüm koşusunda ölçüldü:
                            # 00:10:31 konsensus "'Sonundabiniş buldum." (conf
                            # 0.293) → PP "buldum." (conf 1.00) takası
                            # cümlenin başını DÜŞÜRÜYORDU; 00:05:29 "22" →
                            # "_" gibi içeriksiz parçalar da bloklanır.

# --- düzeltme 2 (04.10.2026): TAKAS × CJK KREDİ ETKİLEŞİMİ ----------------
# BLEND-S ~00:03:58 (Japonca kredi): takas metni gerçek CJK'ya çevirince
# (ilk konsensus conf 0.019 → ikinci motor 0.998) Türkçe süzgeci (word_re +
# vowel_re) CJK metni düşürüyor ve blok İKİ SRT'DEN DE kayboluyordu (ana'da
# yok, _ekran'da da yok; kanıt: _kal2-blends takas-gunlugu + vtt-qa
# ekran_eslesmeyen "Japonca kredi"). Kural: İLK konsensus conf'u bu eşiğin
# altındaysa ve takas adayı CJK-çoğunlukluysa takas YAPILMAZ; blok ikinci
# motorun CJK okumasıyla <ad>_ekran.srt'ye yönlendirilir (istatistik
# etiketi "takas-cjk") — blok kaybolmaz (Kural 1), diyalog akışı da
# kirletmez. SADECE ilk-conf çok düşükken uygulanır: normal CJK içerikli
# yüksek-conf bloklar (ör. BLEND-S 00:12:41 "OTURMAK" panelleri) ve
# karışık-dilli kredi metinleri ("程 鈴木路惠 … QUANG PHU …", CJK oranı
# ~0.14) bu kuraldan ETKİLENMEZ (ölçüm: _kal2-blends takas kayıtlarında
# CJK-çoğunluk hedef yalnız 00:03:58 kredisi ve 00:19:23 "一一" çöpü).
TAKAS_CJK_ILK_CONF = 0.45

# ikinci görüş motoru: tembel kurulur, oy verir; metni tek başına değiştirmez
# — İSTİSNA: yukarıdaki ölçülü takas koşulu (TAKAS_*) sağlanırsa metin
# ikinci motordan alınır (8. tur).
_SECOND = {"state": "untried", "name": None, "fn": None}

# --- Task 1 (03.10.2026): ikinci OCR motoru (rapidocr) icin CUDA olcumu ---
#
# Onceki not ("ORT 1.30 GPU wheel'i CUDA 13 istiyor, torch cu126 ortaminda
# CUDAExecutionProvider DLL yuklenemedi") KISMEN DOGRUYDU ve bir yanlis
# karara yol acti: GPU HICBIR DENENMEMISTI. Olcum (03.10.2026):
#   * ORT 1.30.0 -> cublasLt64_13.dll istiyor (CUDA 13) -> bu makinede YOK
#   * ORT 1.29.0 -> cublasLt64_13.dll istiyor (CUDA 13) -> YOK
#   * ORT 1.26.0 -> cublasLt64_12.dll + cudnn64_9.dll istiyor (CUDA 12)
#     -> BU MAKINEDE CALISIYOR (nvidia-smi surucu 617.14 / CUDA UMD 13.4)
#   * Python 3.14 icin 1.24.1'den eski GPU wheel'i YOK (cp314 tekeri
#     yok), yani 1.26.0 kurulabilen en YENI CUDA-12 build'idir.
#
# OLCULEN IKILI TUZAK (bu yuzden "istendi" degil "kullanildi" yaziyoruz):
#   torch import EDILMEDEN once CUDA EP acilamiyor ("cublasLt64_12.dll
#   is missing") ve onnxruntime sessizce CPU'ye DUSUYOR:
#       GET_PROVIDERS: ['CPUExecutionProvider']
#   torch import EDILDIKTEN SONRA ayni kod:
#       GET_PROVIDERS: ['CUDAExecutionProvider', 'CPUExecutionProvider']
#   ...oysa `ort.get_device()` IKISINDE DE 'GPU' diyor. Yani get_device()
#   YALAN SOYLER; tek guvenilir olcu get_providers() listesidir.
_ORT = {"cuda": False, "durum": "belirlenmedi", "surum": None, "yol": None,
        "dll_yolu": None}


def _ort_cuda_probe(gpu_flag):
    """CUDAExecutionProvider'ın bu süreçte GERÇEKTEN kullanılabilir olduğunu
    ölçer; `_ORT` sözlüğünü doldurur. Gövde: `os`, `onnxruntime`.

    `--cpu` verilmişse hiç denenmez. Model dosyası yoksa doğrulanamaz —
    bu durumda CUDA istenir ama "doğrulanamadı" diye kaydedilir (yalan
    yok, ama garanti de yok)."""
    import os                                   # yalnız burada gerekli
    if not gpu_flag:
        _ORT.update(cuda=False, durum="--cpu ile zorlandi")
        return False
    if _ORT["durum"] != "belirlenmedi":
        return _ORT["cuda"]
    try:
        import onnxruntime as ort
    except Exception as e:                          # noqa: BLE001
        _ORT.update(cuda=False, durum=f"onnxruntime yok ({type(e).__name__})")
        return False
    _ORT["surum"], _ORT["yol"] = ort.__version__, ort.__file__
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        _ORT.update(cuda=False, durum="CUDA EP derlenmemis (CPU-only wheel)")
        return False
    # CUDA 12 runtime + cuDNN 9 DLL'leri torch ile gelir; arama yoluna
    # acikca EKLENIR ki import sirasi degisince de calissin.
    try:
        import torch
        tl = str(Path(torch.__file__).parent / "lib")
        os.add_dll_directory(tl)
        _ORT["dll_yolu"] = tl
    except Exception as e:                          # noqa: BLE001
        _ORT.update(cuda=False, durum=f"torch DLL yolu yok ({type(e).__name__})")
        return False
    # GERCEK OTURUM: saglayici listesinde CUDA gercekten var mi?
    model = None
    try:
        import rapidocr
        mdir = Path(rapidocr.__file__).parent / "models"
        for name in ("PP-OCRv6_det_small.onnx", "ch_PP-OCRv5_det_mobile.onnx"):
            cand = mdir / name
            if cand.exists():
                model = str(cand)
                break
    except Exception:                               # noqa: BLE001
        model = None
    if model is None:
        _ORT.update(cuda=True, durum="istendi, DOGRULANAMADI (model dosyasi "
                                     "yok; CUDA DLL'leri torch yolunda)")
        return True
    try:
        so = ort.SessionOptions()
        so.log_severity_level = 3                   # sessiz hata yerine sonuc
        s = ort.InferenceSession(model, so,
                                 providers=["CUDAExecutionProvider",
                                            "CPUExecutionProvider"])
        used = [p for p in s.get_providers() if p != "CPUExecutionProvider"]
    except Exception as e:                          # noqa: BLE001
        _ORT.update(cuda=False,
                    durum=f"CUDA oturumu acilamadi ({type(e).__name__})")
        return False
    if not used:
        _ORT.update(cuda=False, durum="CUDA istendi ama CPU'ye dustu "
                                      "(get_providers=%s)"
                                      % (s.get_providers(),))
        return False
    _ORT.update(cuda=True, durum=f"KULLANILDI ({','.join(used)})")
    return True


# PP-OCRv5 motoru (rapidocr >=3): ayrı tembel tekil; ikinci görüş zincirinin
# auto/paddle modlarındaki adımı. 03.10.2026 ölçümü: GPU'da çalışıyor —
# onnxruntime-gpu 1.26.0 (CUDA 12 build) + torch'un taşıdığı cuDNN 9, ve
# CUDA DLL'leri torch import edildikten sonra erişilebilir oluyor
# (bkz. _ort_cuda_probe ve _ORT sözlüğü).
_PP5 = {"state": "untried", "engine": None}

# PP-OCRv6 motoru (rapidocr >=3.9): ikinci görüş zincirinin auto moddaki İLK
# adımı (ölçüm 02.10.2026, _v6-results.json: PP-OCRv5'in tüm metriklerinde
# üstünde — tr satır CER 0.096 vs 0.144). Aynı tembel tekil deseni.
_PP6 = {"state": "untried", "engine": None}


def note(msg):
    """Bilgi/karar satırlarını stderr'e yaz (sonuç akışı stdout'ta kalır)."""
    print(msg, file=sys.stderr)


def run_ffprobe(path):
    """Video akış bilgisi: fps, süre, frame sayısı, altyazı streamleri."""
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = v["avg_frame_rate"].split("/")
    fps = float(num) / float(den)
    subs = [{"index": s["index"], "codec": s["codec_name"],
             "lang": s.get("tags", {}).get("language", "?")}
            for s in data["streams"] if s["codec_type"] == "subtitle"]
    return {"fps": fps, "duration": float(data["format"]["duration"]),
            "nb_frames": int(v.get("nb_frames", 0)), "subtitles": subs,
            "width": v["width"], "height": v["height"]}


def extract_softsub(path, out_srt, sub_index):
    """Softsub streamini kaynaktan birebir SRT'ye çevir."""
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(path),
                    "-map", f"0:s:{sub_index}", "-c:s", "srt", str(out_srt)],
                   check=True)


def grab_gray(path, t_sec, w, h):
    """Verilen andaki tam kareyi gri tonlama (rawvideo) olarak getir.

    NVDEC açıkken decode donanımda denenir; hata halinde kalıcı olarak CPU
    decode'a düşer, CPU'da da başarısızsa None döner (eski davranış)."""
    global HWACCEL
    base = [FFMPEG, "-v", "error", "-ss", f"{t_sec:.6f}", "-i", str(path),
            "-frames:v", "1", "-vf", "format=gray", "-f", "rawvideo",
            "-pix_fmt", "gray", "-"]
    attempts = [HWACCEL] if HWACCEL else []
    attempts.append([])
    proc = None
    for i, hw in enumerate(attempts):
        proc = subprocess.run(base[:3] + hw + base[3:], capture_output=True)
        if proc.returncode == 0 and len(proc.stdout) >= w * h:
            return np.frombuffer(proc.stdout[:w * h], np.uint8).reshape(h, w)
        if hw:                               # NVDEC başarısız → kalıcı düşüş
            HWACCEL = []
            note("[i] NVDEC grab'de basarisiz — CPU decode'a donuldu")
    return None


# 06.10.2026 — meta yan-dosyası (<srt adı>.hardsub2srt.json) sürüm etiketi:
# kod davranışı değiştikçe GÜNCELLENİR; SRT'nin yanında seyahat eden meta
# dosyası hangi araç sürümünün ürettiğini de kaydeder.
ARAC_SURUM = "hardsub2srt 2026-10-06"


def _video_kimlik(video):
    """Koşuda kullanılan videonun kimlik kaydı (05.10.2026, E02 vakası).

    Ölçümlü vaka: E02 SRT'si farklı bir ripten üretilmişti; video dosyası
    koşudan SONRA değişmişti (mtime 30.09 15:04) ve kayıtta hiçbir iz
    yoktu — uyumsuzluk ancak görsel örneklemeyle yakalandı (_op-rapor.md
    §c.1). Bu kayıt sonrakilerin aynısını önler: SRT ile video
    eşleşmesi sonradan sha1+boyut+mtime üçlüsüyle doğrulanabilir.

    sha1 yalnız İLK 1 MB: tam-dosya hash'i 400+ MB'lık videoya ikinci bir
    tam okuma katar; ilk-1MB + boyut + mtime üçlüsü rip değişimini ayırt
    eder (farklı rip => farklı başlangıç baytları; boyut/mtime da değişir)."""
    st = video.stat()
    h = hashlib.sha1()
    with video.open("rb") as f:
        h.update(f.read(1024 * 1024))
    return {"ad": video.name,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(st.st_mtime)),
            "boyut": st.st_size,
            "sha1_ilk_1MB": h.hexdigest()}


def _beyaz_fon_son_pencere(path, band_y, band_h, width, height, duration,
                           esik, pencere_sn=BEYAZ_FON_SON_PENCERE_SN,
                           ornek=BEYAZ_FON_SON_ORNEK):
    """SON PENCERE ağır-kare ölçümü (05.10.2026 tur 3; ölçüm tablosu ve
    pencere uzunluğu gerekçesi BEYAZ_FON_SON_PENCERE_SN yorumunda).

    İlk pencere (5%-95%) video sonundaki beyaz-fon sahnelerini kaçırır
    (E1075 Gear-5 vakası: sahne son %5'te). Bu ölçüm videonun SON
    `pencere_sn` saniyesine `ornek` adet kare dizer (0.5 kaydırmalı ızgara:
    t = duration - pencere + pencere·(i+0.5)/ornek) ve AYNI ağır-kare
    tanımını uygular (şerit dolgusu > BEYAZ_FON_KARE_DOLGU).

    Döner: dict(pay=0.0-1.0, agir=k, toplam=n) — grab başarısızsa o kare
    sayıma girmez; pencere süreden uzunsa pencere = duration'a kırpılır."""
    if duration <= 0:
        return {"pay": 0.0, "agir": 0, "toplam": 0}
    pencere = min(float(pencere_sn), duration)
    agir = toplam = 0
    for i in range(ornek):
        t = duration - pencere + pencere * (i + 0.5) / ornek
        g = grab_gray(path, t, width, height)
        if g is None:
            continue
        strip = g[band_y:band_y + band_h]
        if strip.size == 0:
            continue
        toplam += 1
        if float((strip > esik).mean()) > BEYAZ_FON_KARE_DOLGU:
            agir += 1
    return {"pay": agir / toplam if toplam else 0.0,
            "agir": agir, "toplam": toplam}


def _beyaz_fon_payi(path, band_y, band_h, width, height, duration, esik,
                    samples=20, frames=None):
    """Ağır beyaz-fon karelerinin payı (0.0-1.0) — thr→tophat korumasının
    ölçümü (05.10.2026 tur 2; sabitler ve ölçüm tablosu BEYAZ_FON_*
    yorumunda; tur 3: SON PENCERE eki).

    Her örnek karede bant dilimi `esik` üstü piksellerin oranı
    BEYAZ_FON_KARE_DOLGU'yu aşarsa kare "ağır" sayılır; pay = ağır/toplam.
    `frames` verilirse (detect_style'ın zaten tuttuğu tam kareler) ekstra
    decode YOK; verilmezse (_candidate_band yolu) kendi örneklerini çeker.

    Tur 3 (05.10.2026): dönüş, İKİ PENCERE taşır —
      "genel": ilk pencere (5%-95%; yukarıdaki eski ölçüm),
      "son":   _beyaz_fon_son_pencere (video sonu; Gear-5 vakası).
    Karar (pay > BEYAZ_FON_PAY) her iki pencereye AYRI ayrı uygulanır;
    tetikleyen pencere karar log'unda "pencere=" ile yazılır."""
    if frames is None:
        if duration <= 0:
            return {"genel": 0.0,
                    "son": {"pay": 0.0, "agir": 0, "toplam": 0}}
        ts = [duration * (0.05 + 0.9 * (i + 0.5) / samples)
              for i in range(samples)]
        frames = []
        for t in ts:
            g = grab_gray(path, t, width, height)
            if g is not None:
                frames.append(g)
    agir = toplam = 0
    for g in frames:
        strip = g[band_y:band_y + band_h]
        if strip.size == 0:
            continue
        toplam += 1
        if float((strip > esik).mean()) > BEYAZ_FON_KARE_DOLGU:
            agir += 1
    genel = agir / toplam if toplam else 0.0
    son = _beyaz_fon_son_pencere(path, band_y, band_h, width, height,
                                 duration, esik)
    return {"genel": genel, "son": son}


def detect_style(path, width, height, duration, samples=20):
    """Altyazı stilini otomatik algıla: bant konumu/yüksekliği + metin rengi.

    Yöntem:
      1) İlk/son %5 atlanıp eşit aralıklı ~20 kare örneklenir; her kare
         tam kare alınır (bant sınırı YOK), analiz 1/4 ölçekte yapılır.
      2) Her örnekte morfolojik top-hat (gri, 7x7 elips kernel) -> parlak
         küçük yapılar (metin benzeri); eşik ~120.
      3) Satır histogramı: örneklerin en az ~%30'unda aktif olan satır
         kümeleri aday satırdır (sahne yazıları/jenerik tek seferlik olduğu
         için düşük tekrar oranıyla elenir). Bant yalnız alt 2/3'te aranır
         (üstte logo olabilir). En büyük piksel kütlesine sahip küme =
         altyazı satırı.
      3b) Bant taban-çapalı kurulur: altyazı alta hizalıdır, ikinci satır
         YUKARI büyür ve %30 eşiğini geçemeyebilir (ölçüm ep2: 2/20 örnek) —
         bant yüksekliği kuraldan gelir: 3.6 x tek satır + üst pay (2 satır
         kapasitesi); taban ekran tabanına kadar uzar (filigran/banner altta
         olabilir); tek satır = en büyük kütleli küme.
      4) Metin çekirdeği parlaklığı YALNIZ algılanan altyazı satırının satır
         aralığında ölçülür (Düzeltme 1a, 03.10.2026): tüm bant üzerinden
         ölçüm parlak sahnelerde şişiyordu (ölçüm 86tr: bant-geneli core>=252
         → esik 250 krem metni yok etti; gerçekte metin çekirdeği ~251).
         Çekirdek (üst %20) ortalaması >= 248 ise metin saf/açık beyazdır ->
         thr modu, white_thr = min(246, cekirdek - 6) (Düzeltme 1b: 86tr'de
         bu 245 verir — elle doğrulanmış kurtarma değeri); değilse tophat
         modu (OCR ham bandı alır). OCR sonrası düşük-conf ağırlıklı koşuda
         eşik main() içinde bir kez daha düşürülür (kendini iyileştirme).

    Döner: dict(band_y, band_h, mask, white_thr, text_h, samples, core)
    ya da algılayamazsa None.
    """
    if duration <= 0 or width < 64 or height < 64:
        return None
    ts = [duration * (0.05 + 0.9 * (i + 0.5) / samples) for i in range(samples)]
    frames = []
    for t in ts:
        g = grab_gray(path, t, width, height)
        if g is not None:
            frames.append(g)
    if len(frames) < max(6, samples // 2):
        return None

    dw, dh = width // 4, height // 4
    sy = height / dh
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    row_min = max(2, int(dw * 0.015))       # satır "aktif" piksel eşiği
    row_hits = np.zeros(dh, np.int32)       # satır kaç örnekte aktifti
    row_mass = np.zeros(dh, np.float32)     # satırın ortalama piksel sayısı
    for g in frames:
        small = cv2.resize(g, (dw, dh), interpolation=cv2.INTER_AREA)
        th = cv2.morphologyEx(small, cv2.MORPH_TOPHAT, kernel)
        counts = (th > 120).sum(axis=1)
        row_hits += (counts >= row_min)
        row_mass += counts / len(frames)

    need = max(3, int(round(0.3 * len(frames))))
    qual = row_hits >= need
    qual[:dh // 3] = False                  # yalnız alt 2/3
    idx = np.where(qual)[0]
    if idx.size == 0:
        return None

    clusters = []                           # satır içi küçük bölünmeleri birleştir
    start = prev = int(idx[0])
    for r in idx[1:]:
        r = int(r)
        if r - prev <= 4:                   # ~16px: tek satırın kendi bölünmesi
            prev = r
        else:
            clusters.append((start, prev))
            start = prev = r
    clusters.append((start, prev))
    scored = [(float(row_mass[a:b + 1].sum()), a, b)
              for a, b in clusters if b - a + 1 >= 3]
    if not scored:
        return None
    scored.sort(reverse=True)               # en büyük kütle = altyazı satırı
    _, r0, r1 = scored[0]
    line_rows = r1 - r0 + 1
    if line_rows < 3:                       # tek harf / gürültü
        return None
    # Bant taban-çapalı: altyazı alta hizalıdır, ikinci satır YUKARI doğru
    # büyür ve %30 tekrar eşiğini geçemeyebilir (ölçüm ep2: hits=2/20) —
    # bu yüzden yükseklik kuraldan gelir: 3.6 x tek satır (iki satır
    # kapasitesi; ölçüm ep1: iki satır açıklığı 128px, çekirdek satır 40px)
    # + üstte 4 satır (~16px) pay.
    top_row = r1 - int(round(3.6 * line_rows)) - 4
    top_row = max(dh // 3, top_row)
    band_y = max(0, int(top_row * sy))
    # Bant tabanı ekran tabanına kadar uzar: fansub filigranı / banner gibi
    # gerçek metinler ana satırın ALTINDA da olabilir (ölçüm ep2: 'İyi
    # seyirler dileriz' + #DEPREM y~1000-1070, ana satır tabanı 992 — alt
    # payı bunları kırpıyordu; eski doğrulanmış bantlar da tabana kadardı:
    # 860+220 ve 820+260 = 1080).
    band_h = height - band_y
    if band_h < 60:
        return None
    # tek satır glif yüksekliği (top-hat çekirdeği glifi ~%20 küçük ölçer);
    # 2x büyütme kararı bunda: <45px küçük font sayılır
    text_h = int(line_rows * sy * 1.25)

    # metin çekirdek parlaklığı: YALNIZ algılanan altyazı satırının satır
    # aralığında (pad = 2 örnek satırı), tam çözünürlükte top-hat isabetli
    # piksellerin gri değerleri, üst %20'nin ortalaması (Düzeltme 1a)
    vals = []
    y0 = max(0, int(r0 * sy) - band_y - 2 * int(sy))
    y1 = min(band_h, int((r1 + 1) * sy) - band_y + 2 * int(sy))
    for g in frames:
        strip = g[band_y + y0:band_y + y1]
        if strip.size == 0:
            continue
        th = cv2.morphologyEx(strip, cv2.MORPH_TOPHAT, kernel)
        m = th > 120
        if m.any():
            vals.append(strip[m])
    core = 0.0
    if vals:
        v = np.sort(np.concatenate(vals))
        if v.size >= 100:
            core = float(v[-max(1, v.size // 5):].mean())
    if core >= 248:
        esik = min(THR_TAVAN, int(core) - 6)
        # Beyaz-fon koruması (05.10.2026 tur 2): thr aday eşiğinde ağır
        # dolgulu karelerin payı > %15 ise fon+metin tek blob yapar; ölçüm
        # zaten tutulan frames üzerinden, ekstra decode YOK. Ölçüm tablosu
        # BEYAZ_FON_* sabitlerinin yorumunda. Tur 3: SON PENCERE — karar
        # iki pencereye ayrı ayrı uygulanır, tetikleyen pencere log'da.
        olcum = _beyaz_fon_payi(path, band_y, band_h, width, height,
                                duration, esik, frames=frames)
        pay, son = olcum["genel"], olcum["son"]
        tetik = None
        if pay > BEYAZ_FON_PAY:
            tetik = "genel"
        elif son["pay"] > BEYAZ_FON_PAY:
            tetik = "son"
        if tetik:
            note(f"[auto] beyaz fon tespiti → tophat moduna geçildi "
                 f"(pencere={tetik}; agir-dolgu (>%{BEYAZ_FON_KARE_DOLGU * 100:.0f}) "
                 f"kare payi genel %{pay * 100:.1f} / son-pencere "
                 f"%{son['pay'] * 100:.1f} ({son['agir']}/{son['toplam']} kare) "
                 f"> %{BEYAZ_FON_PAY * 100:.0f}, esik={esik}; thr maskesi "
                 f"fon+metni tek blob yapiyor)")
            return {"band_y": band_y, "band_h": band_h, "mask": "tophat",
                    "white_thr": DEFAULT_WHITE_THR, "text_h": text_h,
                    "samples": len(frames), "core": core, "beyaz_fon": True}
        return {"band_y": band_y, "band_h": band_h, "mask": "thr",
                "white_thr": esik, "text_h": text_h,
                "samples": len(frames), "core": core}
    return {"band_y": band_y, "band_h": band_h, "mask": "tophat",
            "white_thr": DEFAULT_WHITE_THR, "text_h": text_h,
            "samples": len(frames), "core": core}


def detect_style_upper(path, width, height, duration, frac=UST_REGION_FRAC,
                       samples=20):
    """ÜST BANT stili — diyalog bandından BAĞIMSIZ algılama (--ust-bant).

    detect_style'in kopyası DEĞİLDİR; üç bilinçli fark var:
      1) bant konumu 0'a sabittir (üst %35 = y 0 → height×frac);
      2) metin çekirdeği parlaklığı ÜST BÖLGE'nin TAMAMINDA ölçülür;
      3) TEKRAR kuralı KALDIRILDI — aşağıda ölçümle gerekçelendirildi.

    (3) NEDEN TEKRAR YOK (ölçüm 03.10.2026, _ust-diag-out.txt, Naruto ep1):
    detect_style aday satırı "örneklerin en az %30'unda tekrar eden" satır
    olarak seçer; bu DİYALOG için doğru (altyazı sürekli ekranda). Üst yazı
    ise GEÇİCİDİR — tabela 3 sn görünür, mesaj kutusu kapanır. Aynı kural
    üst bantta ölçüldüğünde: 20 örnekten en iyi satır 2/20, gereken eşik 6/20
    → sıfır satır geçer → algılama HER ZAMAN None dönerdi (ölçülen: Naruto
    ep1 3.4 sn'de None). Yani kural kopyalanırsa üst bandın stil ölçümü
    ölü kod olur. Bunun yerine satır KÜTLE (kare başına ortalama parlak piksel)
    kullanılır: geçici bir tabelanın tek bir karedeki kütlesi yeter.

    Mod seçimi (thr ↔ tophat) üst bölgenin tamamındaki top-hat isabetli
    piksellerin çekirdek parlaklığına bakar — diyalog bandından bağımsız,
    üst yazının kendi stilinden karar verir (kutulu/renkli/konturlu üst yazı
    tophat'a düşer).

    --mask/--white-thr/--band-y/--band-h üst banda UYGULANMAZ (bayrak
    sözleşmesi: bunlar diyalog bandının elle override'ıdır).

    Döner: detect_style ile aynı anahtarlar (band_y daima 0) ya da None.
    """
    if duration <= 0 or width < 64 or height < 64:
        return None
    y_crop = max(16, int(round(height * frac)))
    ts = [duration * (0.05 + 0.9 * (i + 0.5) / samples) for i in range(samples)]
    frames = []
    for t in ts:
        g = grab_gray(path, t, width, height)
        if g is not None:
            frames.append(g)
    if len(frames) < max(6, samples // 2):
        return None

    dw = width // 4
    dh = height // 4
    sy = height / dh
    rows = max(6, min(dh - 1, int(round(dh * frac))))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    row_min = max(2, int(dw * 0.015))
    row_mass = np.zeros(rows, np.float32)     # satırın ortalama piksel sayısı
    for g in frames:
        small = cv2.resize(g[:y_crop], (dw, rows), interpolation=cv2.INTER_AREA)
        th = cv2.morphologyEx(small, cv2.MORPH_TOPHAT, kernel)
        row_mass += (th > 120).sum(axis=1) / len(frames)

    # satır kümeleri (kütle tabanlı; tekrar şartı yok)
    clusters = []                            # satır içi bölünmeleri birleştir
    start = prev = None
    for r in range(rows):
        if row_mass[r] >= 0.5:
            if start is None:
                start = prev = r
            elif r - prev <= 4:              # ~16px: tek satırın bölünmesi
                prev = r
            else:
                clusters.append((start, prev))
                start = prev = r
        elif start is not None:
            clusters.append((start, prev))
            start = prev = None
    if start is not None:
        clusters.append((start, prev))

    # metin çekirdeği parlaklığı: ÜST BÖLGENİN TAMAMI, tam çözünürlükte
    # top-hat isabetli piksellerin üst %20 ortalaması. Satır kümesine
    # BAĞLI DEĞİL — geçici yazılar hiçbir kümeyi "kalıcı" kılmaz, ama
    # çekirdek parlaklığı yine de üst yazının stili hakkında doğru bilgi
    # verir. (detect_style bunu yalnız TEKRAR EDEN satırda ölçer; burada
    # ölçüm alanı banttır, kümeler yalnız metin yüksekliği içindir.)
    vals = []
    for g in frames:
        strip = g[:y_crop]
        if strip.size == 0:
            continue
        th = cv2.morphologyEx(strip, cv2.MORPH_TOPHAT, kernel)
        m = th > 120
        if m.any():
            vals.append(strip[m])
    n_vals = sum(v.size for v in vals)
    # Karanlık/stilize üst bantta top-hat 120 ile yeterli örnek toplanamayabilir
    # (ölçüm Naruto ep1: 20 karede 100'den az isabet -> core=0.0, yani
    # "ölçemedim" ile "çekirdek koyu" birbirine karışıyordu). Eşik kademeli
    # düşürülür; amaç SAYI toplamak, üst yazının parlaklığını ölçmek değil.
    for alt_thr in (90, 70, 50):
        if n_vals >= 100:
            break
        extra = []
        for g in frames:
            strip = g[:y_crop]
            if strip.size == 0:
                continue
            th = cv2.morphologyEx(strip, cv2.MORPH_TOPHAT, kernel)
            m = th > alt_thr
            if m.any():
                extra.append(strip[m])
        if sum(v.size for v in extra) > n_vals:
            vals = extra
            n_vals = sum(v.size for v in extra)
    core = 0.0
    if vals:
        v = np.sort(np.concatenate(vals))
        if v.size >= 100:
            core = float(v[-max(1, v.size // 5):].mean())

    # metin yüksekliği: en büyük kütleli küme; yoksa bölge yüksekliğinden
    # türetilmiş bir varsayılan (üst yazı genelde büyük punto olduğu için
    # 2x büyütme üst bantta nadiren gerekir).
    band_h = max(60, y_crop)
    scored = [(float(row_mass[a:b + 1].sum()), a, b)
              for a, b in clusters if b - a + 1 >= 3]
    if scored:
        scored.sort(reverse=True)
        _, r0, r1 = scored[0]
        line_rows = r1 - r0 + 1
        text_h = int(line_rows * sy * 1.25)
    else:
        text_h = max(24, int(y_crop * 0.18))
    if core >= 248:
        return {"band_y": 0, "band_h": band_h, "mask": "thr",
                "white_thr": min(THR_TAVAN, int(core) - 6), "text_h": text_h,
                "samples": len(frames), "core": core}
    return {"band_y": 0, "band_h": band_h, "mask": "tophat",
            "white_thr": DEFAULT_WHITE_THR, "text_h": text_h,
            "samples": len(frames), "core": core}


def _static_drop_map(path, band_y, band_h, width, max_seconds,
                     mask_mode="thr", white_thr=250, tophat_thr=120):
    """Zaman içinde HİÇ değişmeyen maske piksellerini bul (logo/watermark eleme).

    Taranan aralığın TAMAMI, scan_band ile BİREBİR aynı ölçekte (thr 1/4,
    tophat 1/2) ikinci bir decode geçişiyle taranır — maske üretimi
    scan_band'in kopyasıdır, yoksa harita yanlış pikselleri elerdi. Kural:
        seen    = piksel en az bir karede maskede
        changed = ardışık kareler arasında en az bir kez farklı
        drop    = seen AND NOT changed
    Yani ekranda SABİT duran filigran/logo/watermark düşer; hareketli
    karakter, tabela animasyonu, dönen kamera ve değişen ışık kalır
    (hepsi zamanla değişir).

    Döner: (drop ndarray | None, taranan_kare_sayisi)."""
    global HWACCEL
    if mask_mode == "tophat":
        ds_w, ds_h = width // 2, band_h // 2
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    else:
        ds_w, ds_h = width // 4, band_h // 4
        kernel = None
    if ds_w < 8 or ds_h < 8:
        return None, 0
    vf = f"crop=iw:{band_h}:0:{band_y},scale={ds_w}:{ds_h},format=gray"
    frame_bytes = ds_w * ds_h

    def _once(use_hw):
        pre = [FFMPEG, "-v", "error"]
        if max_seconds:
            pre += ["-t", str(max_seconds)]
        proc = subprocess.Popen(pre + list(use_hw) + ["-i", str(path), "-vf", vf,
                                 "-f", "rawvideo", "-"], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        seen = np.zeros((ds_h, ds_w), bool)
        changed = np.zeros((ds_h, ds_w), bool)
        prev = None
        n = -1
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            n += 1
            fr = np.frombuffer(buf, np.uint8).reshape(ds_h, ds_w)
            if kernel is not None:
                fr = cv2.morphologyEx(fr, cv2.MORPH_TOPHAT, kernel)
                m = fr > tophat_thr
            else:
                m = fr > white_thr
            seen |= m
            if prev is not None:
                changed |= np.logical_xor(m, prev)
            prev = m
        proc.wait()
        err = proc.stderr.read().decode(errors="replace")
        return seen, changed, n + 1, proc.returncode, err

    attempts = [HWACCEL] if HWACCEL else []
    attempts.append([])
    seen = changed = None
    scanned = 0
    rc, err = 0, ""
    for i, hw in enumerate(attempts):
        seen, changed, scanned, rc, err = _once(hw)
        if (rc == 0 and scanned > 0) or i == len(attempts) - 1:
            break
        if hw:                                # NVDEC başarısız → kalıcı düşüş
            HWACCEL = []
            note("[i] NVDEC statik taramada basarisiz — CPU decode'a donuldu")
    if rc != 0 or scanned <= 0:
        note(f"[ust] statik eleme haritasi alinamadi (rc={rc}) — "
             f"statik eleme YAPILMADI")
        return None, scanned
    return np.logical_and(seen, np.logical_not(changed)), scanned


def _serit_cekirdek(path, band_y, band_h, width, height, duration,
                    samples=10):
    """Bir seridin metin çekirdek parlaklığı — detect_style'ın Düzeltme 1a
    ölçümünün tek seride uygulanmış hâli: serit içindeki top-hat isabetli
    piksellerin ÜST %20 ortalaması.

    05.10.2026 (Wano teşhisi): _candidate_band bu ölçüm OLMADAN sabit
    white_thr=250 kullanıyordu; ölçümlü vaka E1049/E1050 — detect_style
    None döndü (ince/stilize metin tophat>120 satır eşiğini geçemedi),
    fallback sabit 250 ile koştu, diyalog kaçtı ve oto-eşik denemesi de
    tetiklenmedi (başlık kartı/künye conf>=0.4 okudu). Ölçüm, tahminin
    yerine geçer: eşiği bandın KENDİ metninden hesaplarız.

    Döner: 0.0-255 float (ölçülemiyorsa 0.0)."""
    if duration <= 0 or band_h <= 0:
        return 0.0
    ts = [duration * (0.05 + 0.9 * (i + 0.5) / samples) for i in range(samples)]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    vals = []
    for t in ts:
        g = grab_gray(path, t, width, height)
        if g is None:
            continue
        strip = g[band_y:band_y + band_h]
        if strip.size == 0:
            continue
        th = cv2.morphologyEx(strip, cv2.MORPH_TOPHAT, kernel)
        m = th > 120
        if m.any():
            vals.append(strip[m])
    if not vals:
        return 0.0
    v = np.sort(np.concatenate(vals))
    if v.size < 100:
        return 0.0
    return float(v[-max(1, v.size // 5):].mean())


def _candidate_band(path, width, height, duration, probe_seconds=60.0):
    """Algılama None döndüğünde alt bölgede kaydırmalı aday bant taraması.

    Düzeltme 2b (03.10.2026): tek varsayılana düşmek yerine alt %25 içinde
    3 aday bant (taban-çapalı, h = H - y) her biri ~60 sn'lik hızlı taramayla
    işaretlenir; en çok segment-kütlesi toplayan aday döner. Hepsi boşsa None
    (eski varsayılanlara düşülür). Döner: detect_style ile aynı anahtarlar +
    "fallback": True.

    05.10.2026 (Wano teşhisi, düzeltme 2): EŞİK ARTIK ÖLÇÜLÜR. Eski kod her
    adayı DEFAULT_WHITE_THR=250 ile tarıyordu; krem/açık-gri metin bu
    eşikte maskeye GİRMİYOR ve üç adayın kütlesi de ~0 çıkıyordu (E1049/
    E1050 vakası: 6-17 blok, speech 17-31 sn). Artık her aday bandın
    çekirdek parlaklığı _serit_cekirdek ile ölçülür: core>=248 -> thr modu,
    esik=min(246, core-6) (detect_style 1a/1b ile aynı kural); core<248 ->
    tophat modu (renkli/soft metin saf beyaz eşiğinde silinir; E1007'nin
    oto-seçtiği mod). Ölçülen değerler note() ile kayda geçer (Kural: log
    yalan söylemez)."""
    if duration <= 0:
        return None
    probe = min(probe_seconds, duration)
    best = None
    for frac in (0.75, 0.79, 0.83):
        y = max(0, int(height * frac))
        h = height - y
        if h < 60:
            continue
        core = _serit_cekirdek(path, y, h, width, height, duration)
        if core >= 248:
            mod, esik = "thr", min(THR_TAVAN, int(core) - 6)
        else:
            mod, esik = "tophat", DEFAULT_TOPHAT_THR
        segs, _scanned, _fd = scan_band(path, y, h, width, esik,
                                        0.35, probe, mask_mode=mod)
        segs = [(a, b) for a, b in segs if b - a + 1 >= 3]
        mass = sum(b - a + 1 for a, b in segs)
        note(f"[auto] aday bant y={y}, h={h}: cekirdek={core:.0f}, mod={mod}, "
             f"esik={esik}: {len(segs)} segment, "
             f"kutle={mass} ({probe:.0f}sn hizli tarama)")
        if best is None or mass > best[0]:
            best = (mass, y, h, mod, esik, core)
    # ~1 sn'lik altyazı işareti bile yeter (24 fps); boşsa None
    if best is not None and best[0] >= 24:
        mass, y, h, mod, esik, core = best
        if mod == "thr":
            # Beyaz-fon koruması (05.10.2026 tur 2): aday-tarama thr seçtiyse
            # seçilen bantta ağır-dolgu payı ölçülür; fon+metin tek blob
            # olmasın (detect_style kapısıyla aynı kural/sabitler). Tur 3:
            # SON PENCERE — karar iki pencereye ayrı ayrı uygulanır.
            olcum = _beyaz_fon_payi(path, y, h, width, height, duration,
                                    esik, samples=12)
            pay, son = olcum["genel"], olcum["son"]
            tetik = None
            if pay > BEYAZ_FON_PAY:
                tetik = "genel"
            elif son["pay"] > BEYAZ_FON_PAY:
                tetik = "son"
            if tetik:
                note(f"[auto] beyaz fon tespiti → tophat moduna geçildi "
                     f"(aday bant y={y}, h={h}; pencere={tetik}; agir-dolgu "
                     f"(>%{BEYAZ_FON_KARE_DOLGU * 100:.0f}) kare payi genel "
                     f"%{pay * 100:.1f} / son-pencere %{son['pay'] * 100:.1f} "
                     f"({son['agir']}/{son['toplam']} kare) > "
                     f"%{BEYAZ_FON_PAY * 100:.0f}; thr maskesi fon+metni "
                     f"tek blob yapiyor)")
                mod, esik = "tophat", DEFAULT_TOPHAT_THR
                beyaz_fon = True
            else:
                beyaz_fon = False
        else:
            beyaz_fon = False
        sonuc = {"band_y": y, "band_h": h, "mask": mod,
                 "white_thr": esik,
                 "text_h": max(28, int(h * 0.26)), "samples": 0, "core": core,
                 "fallback": True}
        if beyaz_fon:
            sonuc["beyaz_fon"] = True
        return sonuc
    return None


# 05.10.2026 — DAR BANT SAĞLIK KONTROLÜ (Wano E1017-E1080 çöküşü) ----------
# Ölçülen arıza imzaları (stats.json, 124 bölümlük batch; _op-rapor.md b.3):
#   E1071 (976,104) · E1007 (952,128) · E1074/75 (940,140) · E1076 (932,148)
# 95px fontta iki satırlı diyalog ~200px yer kaplar; h<150 bandı ikinci
# satırı KESER ve kendi kendini güçlendiren döngü başlar: üst satır hiç
# yakalanmadığı için algılama dar bandı her koşuda yeniden üretir (inceleme
# raporu: "kısa bant da iki satırlı diyalogların üst satırını keserek hatayı
# büyütüyor"). E1007'de bu dar bant + iki satır kesilmesi low-conf'u %64'e
# taşıdı (96/150 blok).
# TETİK bilinçli olarak görev metnindeki "<140" değil 150: E1074/75'in
# ölçülen h=140'ı da bu aileden. h=184/196 olan bölümler tetikLENMEZ (tek
# satır rahat sığar) — düzeltme yalnız şüpheli-dar durumu etkiler, sağlıklı
# videoların bandı bit-bit aynı kalır (parite).
# TUR 2 (05.10.2026): tetik 150 → 180. Yeni ölçüm (E1049 t=1145,
# _wn-full-1049-1145.png, thr240 satır profili ≥8 piksel): iki satırlı
# diyalog beyaz-çekirdek span'ı y=945..1039 — üst satır 945-975, alt satır
# 1000-1039. E1071'in (976,104) bandı üst satırı 945'ten BİÇER (31 px
# kayıp), E1007'nin (952,128) bandı 7 px biyer; 150 tabanıyla üretilen
# y=900 bile yeterliydi ama 150-179 px'e sıkışan bantlar (ör. E1076 932,148)
# kurtulamıyordu. Taban 180 → y=900 sabit, üst satıra 45 px marj; hedef de
# 180 olduğu için genişletme tam 180'e çeker (h ≥ 180 garantisi).
BANT_MIN_H_1080 = 180     # tetik eşiği (1080p tabanında; yükseklikle ölçeklenir;
                          # tur 2: 150 -> 180, ölçüm üstte)
BANT_HEDEF_H_1080 = 180   # genişletme hedefi (taban ekran tabanında kalır)


def _dar_bant_genislet(det, height):
    """detect_style başarılı ama bant, iki satırlı diyaloğun üst satırını
    kesecek kadar darsa bandı YUKARI doğru genişletir.

    Taban = ekran tabanı kuralı KORUNUR (altyazı alta hizalıdır; bant
    tabanı ekran tabanına kadar uzar — detect_style'ın kendi kuralı);
    genişletme yalnız band_y'yi yukarı çeker: hedef
    BANT_HEDEF_H_1080×(H/1080), mevcut h daha buysa dokunulmaz.

    Tetik: h < BANT_MIN_H_1080×(H/1080) ya da bant videonun üst %40'ının
    İÇİNDE kalıyorsa (savunma dalı — mevcut algılayıcılar tabana çapalı
    olduğu için bu ikinci koşul bugünkü kodla tetiklenemez; üstte
    yerleşimli bir bandı aşağı genişletmek BOZARDI, o yüzden o durumda
    dokunma kararı verilir).

    Döner: genişletilmiş kopya ya da None (dokunma)."""
    olcek = height / 1080.0
    min_h = BANT_MIN_H_1080 * olcek
    tetik = det["band_h"] < min_h or \
        (det["band_y"] + det["band_h"]) <= height * 0.4
    if not tetik:
        return None
    if (det["band_y"] + det["band_h"]) < height - 2:
        # Üst yerleşimli bant (üst %40 koşulu): aşağı genişletme bandı
        # metinden uzaklaştırırdı. Bugünkü algılayıcılar tabana çapalı
        # olduğu için bu dal ölçülemez; dokunma kararı güvenli olandır.
        return None
    hedef = max(int(round(BANT_HEDEF_H_1080 * olcek)), det["band_h"])
    if hedef <= det["band_h"]:
        return None
    yeni = dict(det)
    yeni["band_y"] = max(0, height - hedef)
    yeni["band_h"] = height - yeni["band_y"]
    return yeni


def _fade_refine(basla, dens, oran=0.7):
    """Fade-in başlangıç rafinasyonu (04.10.2026, GT ölçümü B12: start
    ~1.4 sn erken üretiliyordu).

    Segment medyan dolgusunun %70'inden az dolguyla başlayan segmentlerin
    başlangıcı, dolgunun bu eşiği geçtiği İLK kareye taşınır. Dolgunluklar
    segment içinde frame frame toplanır (scan_band _once). Döner:
    (yeni_baslangic, duzeltildi_mi)."""
    if len(dens) < 3:
        return basla, False
    medyan = sorted(dens)[len(dens) // 2]
    if medyan <= 0:
        return basla, False
    esik = oran * medyan
    if dens[0] >= esik:
        return basla, False
    for i, d in enumerate(dens):
        if d >= esik:
            return basla + i, True
    return basla, False


def scan_band(path, band_y, band_h, width, white_thr, diff_thr, max_seconds,
              mask_mode="thr", tophat_thr=120, static_drop=None):
    """Aşama 1: altyazı bandını frame frame izle, metin segmentlerini bul.

    mask_mode="thr": beyazlık eşiği (saf beyaz metin için, 1/4 ölçek).
    mask_mode="tophat": morfolojik top-hat — parlak küçük yapıları yakalar,
    geniş parlak alanları (duvar/gökyüzü) söndürür (1/2 ölçek, 5x5 kernel).

    static_drop (yalnız --ust-bant): scan_band ile aynı ölçekte üretilmiş
    "hiç değişmeyen piksel" haritası (_static_drop_map). Verilirse maske
    her frameden sonra bu piksellerden arındırılır — sabit logo/watermark
    maskeyi tek başına açık tutmaz. None (VARSAYILAN) = eski yol BİT-BİT:
    `keep` None'dır ve `mask` satırına hiç dokunulmaz.

    FADE-IN RAFİNASYONU (04.10.2026): segment başlangıç karelerinin
    dolgunluğu segment medyanının %70'inin altındaysa başlangıç, eşiği
    geçen ilk kareye taşınır (_fade_refine; B12 ölçümü: ~1.4 sn erken).

    NVDEC açıkken decode donanımda denenir; ffmpeg hata verir ya da hiç
    frame üretmezse kalıcı olarak CPU decode'a düşülür ve tarama baştan yapılır.

    Döner: (segments, frames_scanned, fade_duzeltilen)
    segments: [(start_frame, end_frame)]  — end dahil.
    """
    global HWACCEL
    if mask_mode == "tophat":
        ds_w, ds_h = width // 2, band_h // 2
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    else:
        ds_w, ds_h = width // 4, band_h // 4
        kernel = None
    keep = None if static_drop is None else np.logical_not(static_drop)
    vf = f"crop=iw:{band_h}:0:{band_y},scale={ds_w}:{ds_h},format=gray"
    frame_bytes = ds_w * ds_h

    def _once(use_hw):
        pre = [FFMPEG, "-v", "error"]
        if max_seconds:
            pre += ["-t", str(max_seconds)]
        pop_args = pre + list(use_hw) + ["-i", str(path), "-vf", vf,
                                         "-f", "rawvideo", "-"]
        proc = subprocess.Popen(pop_args, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        segments = []          # tamamlanmış segmentler
        cur_start = None       # aktif segment başlangıcı
        prev_mask = None       # önceki frame maskesi
        anchor_mask = None     # segment başındaki maske (kademeli sürüklenme için)
        cur_dens = []          # aktif segmentin kare dolgunlukları (fade rafinasyonu)
        fade_n = 0             # fade-in ile başlangıcı düzeltilen segment sayısı
        n = -1
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            n += 1
            frame = np.frombuffer(buf, np.uint8).reshape(ds_h, ds_w)
            if kernel is not None:
                frame = cv2.morphologyEx(frame, cv2.MORPH_TOPHAT, kernel)
                mask = frame > tophat_thr
            else:
                mask = frame > white_thr
            if keep is not None:
                mask = np.logical_and(mask, keep)
            filled = int(mask.sum())

            if filled < 20:                      # metin yok
                if cur_start is not None:
                    bas, d = _fade_refine(cur_start, cur_dens)
                    segments.append((bas, n - 1))
                    fade_n += d
                    cur_start = None
                prev_mask = None
                anchor_mask = None
                continue

            if cur_start is None:
                cur_start = n
                cur_dens = [filled]
                prev_mask = mask
                anchor_mask = mask
                continue

            def xor_ratio(m1, m2):
                union = int(np.logical_or(m1, m2).sum())
                if union == 0:
                    return 0.0
                return int(np.logical_xor(m1, m2).sum()) / union

            # anlık değişim VE segment başına göre sürüklenme (crossfade yakalanır)
            if xor_ratio(mask, prev_mask) > diff_thr or \
                    xor_ratio(mask, anchor_mask) > diff_thr:
                bas, d = _fade_refine(cur_start, cur_dens)
                segments.append((bas, n - 1))
                fade_n += d
                cur_start = n
                cur_dens = [filled]
                anchor_mask = mask
            else:
                cur_dens.append(filled)
            prev_mask = mask

        if cur_start is not None:
            bas, d = _fade_refine(cur_start, cur_dens)
            segments.append((bas, n))
            fade_n += d
        proc.wait()
        err = proc.stderr.read().decode(errors="replace")
        return segments, n + 1, proc.returncode, err, fade_n

    attempts = [HWACCEL] if HWACCEL else []
    attempts.append([])
    segments = []
    scanned = 0
    rc, err = 0, ""
    fade_n = 0
    for i, hw in enumerate(attempts):
        segments, scanned, rc, err, fade_n = _once(hw)
        if (rc == 0 and scanned > 0) or i == len(attempts) - 1:
            break
        if hw:                               # NVDEC başarısız → kalıcı düşüş
            HWACCEL = []
            note("[i] NVDEC taramada basarisiz — CPU decode'a donuldu")
    if rc != 0:
        sys.exit(f"ffmpeg tarama hatası:\n{err}")
    return segments, scanned, fade_n


def grab_band(path, t_sec, band_y, band_h):
    """Verilen andaki altyazı bandını tam çözünürlükte renkli getir.

    NVDEC açıkken decode donanımda denenir; hata halinde kalıcı olarak CPU
    decode'a düşer, CPU'da da başarısızsa hata yükseltir (eski: check=True)."""
    global HWACCEL
    base = [FFMPEG, "-v", "error", "-ss", f"{t_sec:.6f}", "-i", str(path),
            "-frames:v", "1", "-vf", f"crop=iw:{band_h}:0:{band_y}",
            "-f", "image2pipe", "-vcodec", "bmp", "-"]
    attempts = [HWACCEL] if HWACCEL else []
    attempts.append([])
    proc = None
    for i, hw in enumerate(attempts):
        proc = subprocess.run(base[:3] + hw + base[3:], capture_output=True)
        if proc.returncode == 0 and proc.stdout:
            return cv2.imdecode(np.frombuffer(proc.stdout, np.uint8),
                                cv2.IMREAD_COLOR)
        if hw:                               # NVDEC başarısız → kalıcı düşüş
            HWACCEL = []
            note("[i] NVDEC grab'de basarisiz — CPU decode'a donuldu")
    err = proc.stderr.decode(errors="replace") if proc is not None else ""
    raise RuntimeError(f"ffmpeg grab hatasi: {err[:300]}")


def binarize_white(img, thr):
    """Beyaz metni eşikle: siyah zemin üzerinde beyaz metin (BGR döner)."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, m = cv2.threshold(g, thr, 255, cv2.THRESH_BINARY)
    return cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)


def _group_lines(res):
    """Ham OCR satırlarını (bbox, text, conf) satır gruplu (metin, min-conf)
    ikilisine çevir: üstten alta satır grupla, grup içi soldan sağa birleştir.

    Düzeltme 5 (03.10.2026): 28px sabiti font yüksekluğüne ölçeklenir —
    satır eşiği max(28, 0.5 x medyan kutu yüksekliği); büyük/stilize
    fontlarda (ör. One Piece ~95px) baseline kaymasına bağlı yanlış satır
    bölünmesini önler (0.75 çarpanıyla _pp-bench ep2_06/12 + ep1_01'de iki
    GERÇEK satır birleştiği ölçüldü — 0.5'e çekildi). Ayrıca kutu, içinde
    bulunduğu satırın y-ortalamasından > 3 x esik uzaklaşırsa yeni satır
    sayılır (aşırı eğik satırlarda kutu kaybını engeller; bench'te hiç
    tetiklenmedi — koruyucu kural)."""
    if not res:
        return "", 0.0
    items = []
    for bbox, text, conf in res:
        ys = [p[1] for p in bbox]
        xs = [p[0] for p in bbox]
        h = (max(ys) - min(ys)) if len(ys) > 1 else 0.0
        items.append((sum(ys) / len(ys), min(xs), text, conf, h))
    items.sort(key=lambda it: it[0])          # üstten alta
    hts = sorted(it[4] for it in items if it[4] > 0)
    med_h = hts[len(hts) // 2] if hts else 0.0
    row_thr = max(28.0, 0.5 * med_h)          # satır aralığı eşiği (px)
    lines, cur, prev_y = [], [items[0]], items[0][0]
    cur_sum, cur_n = items[0][0], 1           # satırın y-ortalaması için
    for it in items[1:]:
        dy_prev = it[0] - prev_y
        dy_line = it[0] - cur_sum / cur_n
        if dy_prev > row_thr or dy_line > 3.0 * row_thr:
            lines.append(cur)
            cur = [it]
            cur_sum, cur_n = it[0], 1
        else:
            cur.append(it)
            cur_sum += it[0]
            cur_n += 1
        prev_y = it[0]
    lines.append(cur)
    text_lines, confs = [], []
    for line in lines:
        line.sort(key=lambda it: it[1])       # soldan sağa
        text_lines.append(" ".join(it[2] for it in line))
        confs.append(min(it[3] for it in line))
    return "\n".join(text_lines), (min(confs) if confs else 0.0)


def ocr_lines(reader, img, **kw):
    """Tek görüntüden satır gruplu metin + minimum güven döndür."""
    return _group_lines(reader.readtext(img, detail=1, paragraph=False, **kw))


def ocr_lines_batch(reader, imgs, batch_size=16, **kw):
    """Birden çok bandı toplayıp TANIMAYI tek çağrıda batch'le (GPU doluluğu).

    EasyOCR 1.7.2'nin readtext'i liste girdiyi desteklemez (reformat_input
    ValueError atar); bu yüzden EasyOCR'ın kendi aşamaları kullanılır:
      - tespit (detect): görüntü başına — readtext ile birebir aynı yol,
      - tanıma (recognize): tüm görüntülerin gri halleri dikey bitiştirilir,
        kutular y-kaydırılıp alt görüntü sınırlarına kelepçelenir; en-boy
        oranına göre sıralanıp batch_size'lık kovalara bölünür ve her kova
        TEK recognize çağrısıyla okunur; sonuçlar kutu koordinatındaki y'ye
        göre görüntülere geri dağıtılır.
    Kelepçe şart: detect'in add_margin payı bant üst kenarını aşabilir; normal
    yolda get_image_list zaten görüntü sınırlarına kelepçeler — aynı davranış.
    Kova şart: EasyOCR get_text tüm kutuları partinin en geniş kutusuna
    pad'ler; geniş-dar karışık partide kısa kutular boşuna dev tensor'a
    pad'lenir — benzer oranlar birlikte okunur.

    kw varyant parametreleridir (mag_ratio / low_text ...). Her görüntü için
    satır gruplu (metin, conf) döndürür; sıra girdiyle aynıdır.
    """
    n = len(imgs)
    if n == 0:
        return []
    if n == 1:
        return [ocr_lines(reader, imgs[0], **kw)]
    det_kw = dict(min_size=20, text_threshold=0.7, low_text=0.4,
                  link_threshold=0.4, canvas_size=2560, mag_ratio=1.,
                  slope_ths=0.1, ycenter_ths=0.5, height_ths=0.5,
                  width_ths=0.5, add_margin=0.1, reformat=False)
    det_kw.update(kw)
    from easyocr.utils import reformat_input
    gap = 16                                 # bantlar arası siyah ayraç
    greys, hlists, flists, offs, hts = [], [], [], [], []
    y = 0
    for im in imgs:
        img_rgb, grey = reformat_input(im)
        h_agg, f_agg = reader.detect(img_rgb, **det_kw)
        hk = grey.shape[0]
        greys.append(grey)
        offs.append(y)
        hts.append(hk)
        hl = []
        for b in h_agg[0]:                   # [x_min, x_max, y_min, y_max]
            y0 = min(max(b[2] + y, y), y + hk - 1)
            y1 = min(max(b[3] + y, y + 1), y + hk)
            if y1 > y0:
                hl.append([b[0], b[1], y0, y1])
        hlists.append(hl)
        flists.append([[[p[0], min(max(p[1] + y, y), y + hk - 1)]
                        for p in box] for box in f_agg[0]])
        y += hk + gap
    if len({g.shape for g in greys}) != 1:   # beklenmedik boyut farkı: tekli yol
        return [ocr_lines(reader, im, **kw) for im in imgs]
    parts = []
    for i, g in enumerate(greys):
        parts.append(g)
        if i < n - 1:
            parts.append(np.zeros((gap, greys[0].shape[1]), np.uint8))
    stacked = np.vstack(parts)
    all_h = [b for hl in hlists for b in hl]
    all_f = [b for fl in flists for b in fl]
    if not all_h and not all_f:
        return [("", 0.0)] * n
    items = [("h", b) for b in all_h] + [("f", b) for b in all_f]

    def _ratio(kind, b):
        if kind == "h":
            w, h = b[1] - b[0], b[3] - b[2]
        else:
            xs = [p[0] for p in b]
            ys = [p[1] for p in b]
            w, h = max(xs) - min(xs), max(1, int(max(ys) - min(ys)))
        return w / max(1, h)

    items.sort(key=lambda it: _ratio(*it))
    per = [[] for _ in range(n)]
    for gi in range(0, len(items), batch_size):
        grp = items[gi:gi + batch_size]
        gh = [b for k, b in grp if k == "h"]
        gf = [b for k, b in grp if k == "f"]
        res = reader.recognize(stacked, gh, gf, decoder="greedy", beamWidth=5,
                               batch_size=len(grp), workers=0, allowlist=None,
                               blocklist=None, detail=1, rotation_info=None,
                               paragraph=False, contrast_ths=0.1,
                               adjust_contrast=0.5, filter_ths=0.003,
                               y_ths=0.5, x_ths=1.0,
                               reformat=False, output_format="standard")
        for item in res:
            box, text, conf = item[0], item[1], item[2]
            yy = box[0][1]
            for k in range(n):
                if offs[k] <= yy < offs[k] + hts[k]:
                    per[k].append((box, text, conf))
                    break
    return [_group_lines(r) for r in per]


def _takas_uygun(kons_conf, ikinci_conf, kons_metin, ikinci_metin):
    """Düşük-konsensus takası uygun mu? (8. tur; sabitler ve ölçüm TAKAS_*)

    Dört şart BİRLİKTE aranır: konsensus fiilen çökmüş (TAKAS_KONSENS_CONF),
    ikinci motor yüksek güvenli (TAKAS_IKINCI_CONF), ayrışma net
    (TAKAS_CONF_FARK) ve ikinci motor metni kısa parça DEĞİL
    (TAKAS_UZUNLUK_ORANI). Ölçülen BLEND-S GT setinde kazanan vakalar
    (00:03:58, 00:10:25, 00:22:14) geçer; kaybedenler (00:05:50'de
    konsensus 0.50; 00:06:08'de PP 'P'; 00:10:31'de PP 'buldum.' parçası)
    hiç tetiklenmez. Uzunluk kıyası boşluksuz karakter sayısıyladır."""
    if not (ikinci_metin and ikinci_metin.strip()) or not kons_metin:
        return False
    k = "".join(kons_metin.split())
    i2 = "".join(ikinci_metin.split())
    return bool(k and len(i2) >= TAKAS_UZUNLUK_ORANI * len(k)
                and kons_conf < TAKAS_KONSENS_CONF
                and ikinci_conf >= TAKAS_IKINCI_CONF
                and ikinci_conf - kons_conf >= TAKAS_CONF_FARK)


def _cjk_cogunluk(metin):
    """Metin CJK-çoğunluklu mu? (boşluksuz karakterlerin >%50'si U+3000-U+9FFF)

    Düzeltme 2'nin takas-adayı testi: ölçüm vakası BLEND-S 00:03:58 —
    '企画協力 芳文社「まんがタイムきらら」編集部 土居航洋' tamamı CJK
    (oran 1.00). Karışık kredi satırları ('程 鈴木路惠 … QUANG PHU …',
    oran ~0.14) çoğunluk DEĞİLDİR ve bu kuraldan etkilenmez (mevcut takas
    davranışı korunur). _split_noise'daki a-cjk eşiğinden (>0.15) BİLINÇLI
    olarak sıkıdır: burada karar TAKASI engellemek, yalnız çoğunluk
    CJK'ysa."""
    ns = [c for c in metin if not c.isspace()]
    if not ns:
        return False
    return sum(1 for c in ns if _CJK_RE.match(c)) * 2 > len(ns)


def _consensus_pick(cands_confs):
    """(metin, conf) aday havuzundan çift yönlü benzerlik skoruyla seç.

    Havuzdaki her aday diğerleriyle SequenceMatcher.ratio() skorlanır; toplam
    skor + uzunluk en yüksek aday döner. İkinci motorun oyu da buraya +1 aday
    olarak düşer — tek başına metni asla değiştirmez."""
    cands, confs = [], []
    for text, conf in cands_confs:
        if text:
            cands.append(text)
            confs.append(conf)
    if not cands:
        return "", 0.0
    if len(cands) == 1:
        return cands[0], confs[0]
    best, best_key = cands[0], (-1.0, -1)
    for cand in cands:
        score = sum(difflib.SequenceMatcher(None, cand, o).ratio()
                    for o in cands)
        key = (score, len(cand))
        if key > best_key:
            best, best_key = cand, key
    conf = max(c for c, t in zip(confs, cands) if t == best)
    return best, conf


def _letter_ratio(text):
    """Harf oranı: boşluksuz karakterlerin kaçı harf (Türkçe dahil).

    Düzeltme 2c'nin "karaktersiz metin" ölçüsü: rakam/simbol ağırlıklı
    okumalar düşük oran verir."""
    t = [c for c in text if not c.isspace()]
    if not t:
        return 0.0
    return sum(1 for c in t if c.isalpha()) / len(t)


# --- F1: mikro blok "gercek altyazi mi, cop mu" korumasi (veri kaybi onlemi)
#
# Sorun: _micro_cleanup suresi 0.4 sn altinda olan her blogu, guven ve harf
# oranina BAKMADAN _ekran'a tasiyordu. Gercek bir altyazi satiri 0.125 sn
# (3 frame) surebilir.
#
# OLCUM (kaynak: _v8-probe-conf.py, bu iki blogun conf/harf_orani/sure
# degerlerini guncel kodun tasima kararindan once disari sarmalayarak olcer):
#   One Piece S01E02, fps=23.98 — ana SRT'den silinen iki GERCEK satir:
#     "Gercekten, Luffy-san!"            dur=0.125 conf=0.990 harf_orani=0.850
#     "E-evet. Ama daha hazir degilim ." dur=0.375 conf=0.536 harf_orani=0.889
#   ikisi de 46 blokluk ana SRT'den dusup _core-onepiece_ekran.srt'e
#   tasinmisti; ana SRT'de 3:06.864 -> 3:15.580 ve 3:43.394 -> 3:48.107
#   bosluklari birakiyordu.
#
# COP TAVANI (ayni yontem, BOCCHI S01E01 240 sn): 20 cop blogunun EN YUKSEK
# conf'u 0.483 ("Gibsu"), gerisi 0.273 ve alti.
#
# Karar siniri iki kademeli:
#   kademe 1 (guclu) : conf >= 0.75            -> ayni esikle okundu, kesin
#                                                 gercek altyazi
#   kademe 2 (zayif) : conf >= 0.50 VE harf_orani >= 0.80 VE bosluklu
#                                                 olmayan karakter >= 12
#   kademe 2 olmezse: cop sayilir (tasinir).
#
# Neden harf orani TEK BASINA koruma degil (denendi, calismiyor):
#   gercek satirlar 0.850 / 0.889; BOCCHI copu 1.000 (Gibsu, TNA, Yuusu,
#   IDEA NoTHINg, Gibsvn, MiaA/Gilsun, FA NoThing s, EA NoTHIn s).
#   Harf orani >= 0.60 kurali BOCCHI'nin 20 blogundan 8'ini (conf 0.004-0.483)
#   ana SRT'ye geri getirirdi — filigran da harften olusuyor. Burada harf
#   orani yalniz kademe 2'nin ICINDE ve conf tabani gectikten sonra calisir:
#   bu yuzden tek basina hicbir copu ADMIYOR, sadece guven tabaninin
#   yaninda ek guvenlik verir. Ayni sekilde karakter sayisi: "Siz" (3),
#   "Gibsu" (5) gibi kisa parcalarin guveni OCR'da yuksek oynakli oldugu
#   icin guven tabanina ek olarak uzunluk da istenir.
MICRO_CONF_STRONG = 0.75
MICRO_CONF_WEAK = 0.50
MICRO_LETTER_WEAK = 0.80
MICRO_CHARS_WEAK = 12


def _micro_looks_junk(text, conf,
                      conf_strong=MICRO_CONF_STRONG,
                      conf_weak=MICRO_CONF_WEAK,
                      letter_weak=MICRO_LETTER_WEAK,
                      chars_weak=MICRO_CHARS_WEAK):
    """Mikro blok cop mu? SUREDEN tamamen bagimsiz sinyaller: guven, harf
    orani, karakter sayisi.

    conf >= conf_strong ise KORUNUR: OCR bu blogu tam uzunlukteki bir
    altyaziyla ayni guvenle okuduysa kisa gorunmesi gercek bir satirdir,
    sadece ekranda az kaldi demektir."""
    if conf >= conf_strong:
        return False
    if conf >= conf_weak:
        if sum(1 for c in text if not c.isspace()) >= chars_weak and \
                _letter_ratio(text) >= letter_weak:
            return False
    return True


def _char_reconcile(t1, c1, t2, c2):
    """Düzeltme 3: benzer uzunlukta iki okumanın konum-hizalı uzlaşması.

    Eşleşen konumlardaki karakterler korunur; ayrışan konumlarda yüksek
    conf'lu okumanın karakteri kazanır (2 kaynaktan konum-bazlı çoğunluk).
    Döner: (metin, max-conf)."""
    if not t1:
        return t2, c2
    if not t2:
        return t1, c1
    src, other = (t1, t2) if c1 >= c2 else (t2, t1)
    out = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(
            None, src, other).get_opcodes():
        if op == "equal":
            out.append(src[a1:a2])
        elif op == "replace":
            # src = conf'u daha yuksek olan okuma; ayrilan konumda src'deki
            # karakter zaten seciliyor. Eski yazim `s1[i] if s1[i] == s2[i]
            # else s1[i]` idi: iki dal da ayni ifadeyi uretiyordu (tasima,
            # olcu degil). Dongu s1'in tamamini tek tek eklemekten baska bir
            # sey yapmiyordu; tek append birebir ayni sonucu verir.
            out.append(src[a1:a2])
        elif op == "delete":
            out.append(src[a1:a2])        # yüksek conf okumanın fazlası korunur
        # "insert": düşük conf okumanın fazlası alınmaz
    return "".join(out), max(c1, c2)


def _merge_readings(results, fps, SIM=0.70):
    """Ardışık OCR okumalarını bloklara birleştir (diyalog + üst bant ortak).

    Girdi  : [[start_f, end_f, text, conf], ...] — ZATEN boş/köpük süzülmüş.
    Çıktı  : aynı biçimde birleştirilmiş liste.

    Kurallar (değiştirilmedi, diyalog yolundan birebir taşındı):
      1) BİREBİR aynı metin + gap < 0.7 sn            -> süre uzar (gap 0.7)
      2) Çakışan (ov > %60) okumalar tek blokta uzlaşır, SIM düşük olsa
         bile; metin olarak EN UZUN SÜRE görünen (en stabil) okuma seçilir
      3) gap < 0.7 sn ve SIM >= SIM                    -> birleşir
      4) 0 <= gap <= 1.2 sn ve SIM >= 0.75             -> birleşir
    NOT: `results` listesi 1. adımda yerinde (in-place) değişir — bu, iki
    geçiş arasında da aynı olduğu için davranış değişmez, ama çağıran
    `results`'i sonradan kullanıyorsa dikkat etsin.
    """
    # ardışık aynı metin + kısa boşluk → birleştir (gap 0.7, Düzeltme 4)
    merged = []
    for r in results:
        if merged and r[2] == merged[-1][2] and \
                (r[0] - merged[-1][1]) / fps < 0.7:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    # ardışık benzer metin (fade mutasyonları) → birleştir; metin olarak
    # en uzun süre görünen (en stabil) okuma seçilir. Düzeltme 3+4:
    # gap 0.5→0.7; çakışan çift okumalar tek blokta uzlaşır; aralığı
    # ≤ 1.2 sn ve SIM ≥ 0.75 olan ardışık okumalar da tek blok olur.
    stable = []   # [a, b, text, conf, text_dur]
    for a, b, text, conf in merged:
        # F8b: DAHIL (inclusive) frame sayimi. scan_band segmentleri
        # (baslangic, bitis) DONUSLU verir ve bitis frame'i de
        # kapsanir ("end dahil", scan_band docstring); dosyanin geri
        # kalaninda ayni konvansiyon zaten kullaniliyor:
        #   min_frames filtresi : b - a + 1 >= args.min_frames
        #   _micro_cleanup      : (b - a + 1) / fps
        #   konusma suresi      : (b - a + 1) / fps
        # Buradaki (b - a) / fps ve ov = min(b1,b2)-max(a1,a2) bir
        # frame eksik sayiyordu (23.98 fps'te 41.7 ms). Dogru olan
        # dahil sayimdir.
        dur = (b - a + 1) / fps
        if stable:
            prev = stable[-1]
            gap = (a - prev[1]) / fps
            ratio = difflib.SequenceMatcher(None, text, prev[2]).ratio()
            ov = min(prev[1], b) - max(prev[0], a) + 1   # çakışma (frame)
            d_prev = (prev[1] - prev[0] + 1) / fps
            if ov > 0 and ov / fps > 0.60 * min(dur, d_prev):
                # Düzeltme 3: çakışan çift okuma — SIM düşük olsa bile
                # TEK blokta uzlaşma (start=min, end=max, conf=max)
                if ratio >= SIM:
                    prev[1] = max(prev[1], b)
                    if dur > prev[4]:
                        prev[2], prev[3], prev[4] = text, conf, dur
                else:
                    if abs(len(prev[2]) - len(text)) <= max(
                            3, int(0.15 * max(len(prev[2]), len(text), 1))):
                        t2, c2 = _char_reconcile(prev[2], prev[3], text, conf)
                        prev[2], prev[3] = t2, c2
                    elif conf > prev[3]:
                        prev[2], prev[3] = text, conf
                    prev[0] = min(prev[0], a)
                    prev[1] = max(prev[1], b)
                    prev[4] = max(prev[4], dur)
                continue
            if gap < 0.7 and ratio >= SIM:
                prev[1] = b
                if dur > prev[4]:
                    prev[2], prev[3], prev[4] = text, conf, dur
                continue
            if 0 <= gap <= 1.2 and ratio >= 0.75:
                # fade/jenerik kaynaklı çift okuma (ölçüm: One Piece
                # açılışında karaoke satırı değişince aynı altyazı iki
                # bloğa bölünüyordu)
                prev[1] = b
                if dur > prev[4]:
                    prev[2], prev[3], prev[4] = text, conf, dur
                continue
        stable.append([a, b, text, conf, dur])
    return [[a, b, text, conf] for a, b, text, conf, _ in stable]


def _upper_bbox(reader, img_in, band_h, height, scale=1):
    """OCR girdisindeki kutu y'lerinden blok konumunu ORAN olarak ver.

    --ust-bant çıktısının `<ad>_ust.json` yarısı: SRT'nin içine konum
    işareti yazılmaz (SRT temiz kalır), konum ayrı dosyada yaşar.

    Döner {"ust": band içi oran, "tam": çerçeve oranı, "y0", "y1"} ya da
    None. Üst bandın band_y'si daima 0 olduğu için ek ofset yoktur; `scale`
    2x büyütülmüş OCR girdisini 1x'e böler (img_in'in satır koordinatı
    bandın GÖRELI koordinatıdır).
    """
    try:
        from easyocr.utils import reformat_input
        img_rgb, _grey = reformat_input(img_in)
        h_agg, _f_agg = reader.detect(
            img_rgb, min_size=20, text_threshold=0.7, low_text=0.4,
            link_threshold=0.4, canvas_size=2560, mag_ratio=1.,
            slope_ths=0.1, ycenter_ths=0.5, height_ths=0.5, width_ths=0.5,
            add_margin=0.1, reformat=False)
        boxes = (h_agg[0] if h_agg else []) or []
        if not boxes:
            return None
        y0 = min(float(b[2]) for b in boxes) / scale
        y1 = max(float(b[3]) for b in boxes) / scale
        if y1 <= y0 or y1 > band_h * 1.35:      # tespit kutusu kaymış
            return None
        cy = (y0 + y1) / 2.0
        return {"ust": cy / band_h, "tam": cy / height,
                "y0": y0 / height, "y1": y1 / height}
    except Exception:
        return None


def _micro_cleanup(blocks, fps, max_dur=0.4, sim_thr=0.70):
    """Düzeltme 4: <max_dur sn'lik mikro bloklar komşuya katılır.

    Önce metin benzerliği: mikro, SOL komşusuna (out[-1]) SIM >= sim_thr
    benziyorsa ona katılır (zaman birleşir, uzun süreli metin korunur);
    benzimiyorsa sağ komşusu için bekletilir, sağ komşu da benzemiyorsa ANA
    SRT'den çıkarılıp _ekran listesine taşınır (silme yok — Kural 1).
    Benzer mikro zincirleri tek pend'de birleşir. Döner:
    (ana_blok, taşınanlar).

    F1 (veri kaybi onlemi): tasinma karari artik yalniz SUREYE degil,
    blogun KENDI conf/harf_orani/karakter sayisina bakar
    (_micro_looks_junk). Gercek okunmus bir mikro blok cop sayilmaz ve
    ana SRT'ye geri konur; olcum ve esikler icin _micro_looks_junk
    ustundeki notlara bakin."""
    out, moved = [], []
    pend = None                                # sağ komşusunu bekleyen mikro

    def _drop_or_keep(micro):
        """Mikro blogu cop degilse ana listeye geri koy (sira korunur)."""
        if _micro_looks_junk(micro[2], micro[3]):
            moved.append(micro)
        else:
            out.append(list(micro))
    for it in blocks:
        a, b, text, conf = it
        dur = (b - a + 1) / fps
        if dur < max_dur and text.strip():
            # 1) sol komşuya benziyor mu?
            if out and out[-1][2].strip() and \
                    difflib.SequenceMatcher(None, text,
                                            out[-1][2]).ratio() >= sim_thr:
                pa, pb, ptext, pconf = out[-1]
                pdur = (pb - pa + 1) / fps
                if pdur >= dur:
                    out[-1] = [min(pa, a), max(pb, b), ptext, pconf]
                else:
                    out[-1] = [min(pa, a), max(pb, b), text, conf]
                continue
            # 2) bekleyen mikro benzer mi (mikro zinciri)?
            if pend is not None:
                if pend[2].strip() and \
                        difflib.SequenceMatcher(None, text,
                                                pend[2]).ratio() >= sim_thr:
                    pa, pb, ptext, pconf = pend
                    pdur = (pb - pa + 1) / fps
                    if pdur >= dur:
                        pend = [min(pa, a), max(pb, b), ptext, pconf]
                    else:
                        pend = [min(pa, a), max(pb, b), text, conf]
                    continue
                _drop_or_keep(pend)           # bekleyen katılamadı
                pend = None
            pend = [a, b, text, conf]
            continue
        # mikro olmayan blok: bekleyen mikro varsa sağ komşu benzerliği
        if pend is not None:
            pa, pb, ptext, pconf = pend
            pdur = (pb - pa + 1) / fps
            ratio = difflib.SequenceMatcher(None, ptext, text).ratio() \
                if (ptext.strip() and text.strip()) else 0.0
            if ratio >= sim_thr:
                a2, b2 = min(pa, a), max(pb, b)
                if pdur >= dur:
                    it = [a2, b2, ptext, pconf]
                else:
                    it = [a2, b2, text, conf]
                pend = None
            else:
                _drop_or_keep(pend)
                pend = None
        dur = (it[1] - it[0] + 1) / fps        # birleşme sonrası yeniden ölç
        if dur < max_dur and it[2].strip():
            pend = list(it)                    # birleşik blok hâlâ mikro
        else:
            out.append(list(it))
    if pend is not None:
        _drop_or_keep(pend)
    return out, moved


def _split_noise(blocks, fps, ayir=True, conf_thr=0.45, dur_thr=0.6,
                 letter_thr=0.5, sozluk=None, istatistik=None):
    """Düzeltme 2c: çöp profilli blokları ana SRT'den AYRI listeye taşır.

    Çöp profili: conf < 0.45 VE süre < 0.6 sn VE harf oranı < 0.5. Taşıma
    vardır, silme yok (Kural 1); --ayir-gurultu kapalıysa eski davranış
    (her şey ana listede). Döner: (ana_blok, gurultu_blok).

    EKRAN-YAZI SINIFLANDIRICISI (04.10.2026, BLEND-S GT ölçümü) — mevcut
    profil KORUNUR, üstüne ek kurallar; `sozluk` verilmişse:
      (a)  CJK karakter (U+3000-U+9FFF) oranı >%15
      (b1) >=4-harfli kelimelerin <%30'u sözlükte VE sözlük-kaplama <%50
           VE (conf<0.15 VEYA orta-harf anomalisi) — ekran kartı/jenerik
      (b2) jenerik: >=5 kelime VE pay<%60 VE conf<0.15 — ad-listesi
           jenerikleri (Vietnamca jenerik ölçümü: adların 7/13'ü OPUS-TR
           listesinde düşük frekans gürültüsü olarak var: hong 15535)
      (c)  <=2 harf dışı tamamen sembol
    conf<0.15 tabanı probe2 ölçümü: ekran bloklarının conf'u 0.033-0.077,
    GT diyalog conf'larının en düşüğü 0.248. Denge (kalibrasyon
    _pf-kalibrasyon3.py, önceki SRT'nin 310 bloğu): GT'nin 4 ekran bloğu
    ayrıldı, 20 GT diyalog bloğunun hiçbiri ayrılmadı. `istatistik` verilirse
    kural bazlı sayılar oraya yazılır."""
    if not ayir:
        return [list(b) for b in blocks], []
    ana, gurultu = [], []
    for a, b, text, conf in blocks:
        dur = (b - a + 1) / fps
        if conf < conf_thr and dur < dur_thr and _letter_ratio(text) < letter_thr:
            gurultu.append([a, b, text, conf])
            continue
        if sozluk is not None:
            vur, kural = ekran_profili(text, sozluk, conf)
            if vur:
                gurultu.append([a, b, text, conf])
                if istatistik is not None:
                    istatistik[kural] = istatistik.get(kural, 0) + 1
                continue
        ana.append([a, b, text, conf])
    return ana, gurultu


# --- Task 3 (03.10.2026): TEKRARLAYAN filigran/watermark kümeleri ----------
#
# SORUN: BOCCHI ana SRT'sinde 2 blok kalıyordu (00:02:55,257 ve 00:02:57,634)
# — "IDEA NoThing" filigranı. _split_noise profili (conf<0.45 ∧ dur<0.6 ∧
# harf_oranı<0.5) bunları SUREDE kaçırıyor: 0.792 sn ve 0.876 sn.
#
# ⛔ SURE KURALI GENISLETILEMEZ. Ölçüm (2. Bolum): gerçek diyalog bloğu
# "Biraz ne?" 0.833 sn — iki çöp bloğun ARASINDA. Bu yüzden süre tek başına
# ayırıcı DEĞİLDİR; yalnız küme içinde ORTAK bir özellik olarak kullanılır.
#
# GERÇEK AYIRICI: TEKRAR. Filigran 00:02:54,297 → 00:03:00,136 arasında
# ~6 sn'de 16 kez görünüyor (14'ü zaten _ekran'da, 2'si ana SRT'de).
# Gerçek diyalog bir satırı 6 sn'de 16 kez tekrarlamaz.
#
# ÖLÇÜM (bu eşikler sayıdan geldi, tahminden değil):
#   * normalize = küçük harf + Türkçe diakritik→ASCII + rakam dışı → boşluk
#   * 45 ana SRT tarandı (_v6/_v7/_v8/_v9/_va/_vb). 6 sn penceresinde
#     TAM AYNI gerçek metnin EN YÜKSEK tekrar sayısı = 2
#     ("Lanet olsun.", "Kişisel bir fırtına istiyorsan;"). Eşik 4 => 2× emniyet.
#   * BOCCHI filigran kümesi: 16 blok / 5.84 sn, MEDYAN SÜRE 0.166 sn
#     (ortalama 0.261, en uzun 0.876). Gerçek diyalog medyanları:
#     2. Bolum 2.267 sn · One Piece 2.814 sn · 86 2.670 sn.
#     Medyan-süre eşiği 0.5 sn => filigran 0.166'ya 3× marjla geçer,
#     gerçek diyalog 2.3 sn'den 4.6× uzakta kalır.
#   * 2. Bolum "Biraz ne?" (0.833 sn): 6 sn penceresinde benzerlik ≥0.60
#     kume = 1 blok => kural TETİKLENMEZ (doğrudan ölçüldü).
#
# YANLIŞ-POZİTİF RİSKİ (dürüstçe): kural üç sinyalin bileşkesidir —
# benzerlik + zaman penceresi + küme medyan süresi. Tek başına TEKRAR
# yetersizdir ("Hayır!" defalarca söylenir) ama 6 sn'de 4 kez + 0.5 sn'den
# kısa medyan süre birleşimi ölçülen gerçek altyazıda hiç oluşmadı.
# Yine de risk sıfır değildir: hızlı tempoda bir karakter "Hayır! Hayır!
# Hayır!" derse ve okuma kısa tutulursa kümelenebilir. Bu yüzden kural
# SİLMEZ, `_ekran.srt`'ye TAŞIR; kullanıcı her bloğu görebilir.
# Taşıma yapan bir kural kayıp riski taşımaz; sadece yanlış dosyada durma
# riski taşır.
#
# KONUM SİNYALİ NEDEN YOK: ana geçiş bloğu [a, b, text, conf] tutuyor
# (bkz. _extract) — kutu/y bilgisi yalnız `--ust-bant` çıktısında var. Ana
# tarama zaten bant-sınırlı olduğu için dikey konum neredeyse sabittir ve
# ayırıcılık katmaz. Bu, ölçülmüş bir eksiklik olarak kayıtta durur.
REP_SIM = 0.60           # normalize metin benzerliği
REP_WIN = 6.0            # saniye penceresi
REP_MIN = 4              # küme üye sayısı
REP_MEDIAN_DUR = 0.5     # küme medyan süresi (sn) üst sınırı
_REP_TR = str.maketrans("çÇğĞıİöÖşŞüÜ", "cCgGIiOoSsUu")


def _rep_norm(text):
    """Tekrarları yakalayabilmek için metni sadeleştir.

    Küçük harf + Türkçe diakritikleri ASCII'ye indirger, harf/rakam
    dışındaki her şeyi tek boşluğa çevirir. Böylece
    "29245.8 8 / IDEA NoTHING 7s / 5272" ile "IDEA NoThing" kıyaslanabilir.
    """
    t = (text or "").translate(_REP_TR).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def _split_repeat(ana, gurultu, fps, sim=REP_SIM, win=REP_WIN,
                  min_k=REP_MIN, med_dur=REP_MEDIAN_DUR):
    """Tekrarlayan filigran kümesini `_ekran` listesine TAŞIR (silme yok).

    KÜMELEME `_split_noise`'tan SONRA, `ana + gurultu` (= tüm bloklar)
    üzerinde kurulur. Bu ÖNEMLİ: ayrılmadan önce BOCCHI'de küme 16 üyelidir
    ve ana SRT'deki 2 blok da içindedir; ayrıldıktan sonra ana SRT'de yalnız
    2 blok kaldığı için küme 1 üyeye düşer ve kural TUTMAZDI
    (ölçüm: _vc-rep-membership.py).

    Döner: (ana, gurultu, istenen_uyeler, kume_bilgisi)."""
    tum = list(ana) + list(gurultu)
    if len(tum) < min_k:
        return ana, gurultu, [], []
    tum.sort(key=lambda r: r[0])
    norm = [_rep_norm(r[2]) for r in tum]
    kume = []                       # [(uye_indeksleri, medyan_sure)]
    for i in range(len(tum)):
        if not norm[i]:
            continue
        grp = [i]
        for j in range(i + 1, len(tum)):
            if (tum[j][0] - tum[i][0]) / fps > win:
                break
            if norm[j] and difflib.SequenceMatcher(
                    None, norm[i], norm[j]).ratio() >= sim:
                grp.append(j)
        if len(grp) >= min_k:
            med = sorted((tum[k][1] - tum[k][0] + 1) / fps for k in grp)
            med = med[len(med) // 2]
            kume.append((grp, med))
    # Süre süzgeci GEÇEN kümelerin üyeleri seçilir. Aynı üye iki kümede
    # sayılmasın diye büyükten küçüğe işlenip zaten seçilmiş olan atlanır.
    secilen, bilgi = set(), []
    for grp, med in sorted(kume, key=lambda x: (-len(x[0]), x[0][0])):
        if med > med_dur:
            continue
        yeni = [k for k in grp if k not in secilen]
        if len(yeni) < 2:
            continue                     # kümenin tamamı zaten seçilmiş
        secilen.update(yeni)
        uyeler = [tum[k] for k in grp]
        bilgi.append({"adet": len(uyeler),
                      "medyan_sure": round(med, 3),
                      "baslangic_s": round(uyeler[0][0] / fps, 2),
                      "ornek": [u[2][:40] for u in uyeler[:3]]})
    if not secilen:
        return ana, gurultu, [], []
    # Yalnız ANA listedekileri taşınır (gurultudakiler zaten _ekran'da).
    tasinan = [r for r in ana if any(r is tum[k] for k in secilen)]
    if not tasinan:
        return ana, gurultu, [], []
    yeni_ana = [r for r in ana if not any(r is tum[k] for k in secilen)]
    return (yeni_ana, sorted(gurultu + tasinan, key=lambda r: r[0]),
            tasinan, bilgi)


# --- 8. tur (04.10.2026): --jenerik: zaman aralığıyla kredi ayırma -------
# Kayan jenerik/kredi blokları İÇERİK kuralıyla yakalanamaz: BLEND-S
# 00:22:14'teki Vietnamca kredi bloğu sözlük eşiğini geçiyor ve ekran
# sınıflandırıcısından kaçıyor (ölçüm: _pf2-cer-rapor.json 'ekran' çifti —
# ana SRT'de kalmıştı). Kayan kredi OCR'a göre karakter karakter değişir;
# güvenilir sinyal İÇERİK değil ZAMANDIR. Kullanıcı jenerik penceresini
# işaretler; aralıkta BAŞLAYAN bloklar ana SRT'ye girmez, <ad>_ekran.srt'ye
# TAŞINIR (silinmez). "Başlayan" kuralı bilinçli: aralığa taşan ama içinde
# başlamayan diyalog bloğu KORUNUR (kullanıcının şartı: "diyalog
# etkilenmesin"). Çoklu aralık virgülle verilir.


def _saniye_coz(parca):
    """'SS' | 'DD:SS' | 'SS:DD:SS' -> saniye (float). Geçersizse ValueError."""
    try:
        f = [float(x) for x in parca.strip().split(":")]
    except ValueError:
        raise ValueError(f"sayi degil: {parca!r}")
    if len(f) == 1:
        return f[0]
    if len(f) == 2:
        return f[0] * 60 + f[1]
    if len(f) == 3:
        return f[0] * 3600 + f[1] * 60 + f[2]
    raise ValueError(f"bicim: {parca!r}")


def _jenerik_aralik_coz(metin):
    """--jenerik değerini [(bas, bit), ...] listesine çevir.

    Biçim: 'SS' / 'DD:SS' / 'SS:DD:SS', '-' ile aralık, ',' ile çoklu:
    '22:05-22:25' ya da '00:22:05-00:22:25, 00:23:40-00:23:55'.
    Bit <= bas ise hata (argparse bunu 'invalid value' olarak raporlar)."""
    araliklar = []
    for parca in metin.split(","):
        parca = parca.strip()
        if not parca:
            continue
        if "-" not in parca:
            raise ValueError(f"aralik '-' icermeli: {parca!r}")
        sol, sag = parca.split("-", 1)
        a, b = _saniye_coz(sol), _saniye_coz(sag)
        if b <= a:
            raise ValueError(f"bit <= bas: {parca!r}")
        araliklar.append((a, b))
    if not araliklar:
        raise ValueError("bos deger")
    return araliklar


def _jenerik_ayir(bloklar, araliklar, fps):
    """Zaman aralığında BAŞLAYAN blokları ana diyalog listesinden ayır.

    Döner: (kalan, tasinan, bilgi). Kullanıcı beyanıdır (tahmin değil);
    hiçbir blok silinmez — taşınanlar <ad>_ekran.srt'ye yazılır."""
    if not araliklar:
        return list(bloklar), [], []
    kalan, tasinan, bilgi = [], [], []
    for r in bloklar:
        t = r[0] / fps
        if any(a <= t < b for a, b in araliklar):
            tasinan.append(r)
            bilgi.append({"start": round(t, 3),
                          "end": round((r[1] + 1) / fps, 3),
                          "text": r[2][:70]})
        else:
            kalan.append(r)
    return kalan, tasinan, bilgi


# =====================================================================
# İNCELEME TURU (04.10.2026, _rev-RAPOR.md bulguları) — beş kural --------
# Derinlik incelemesi (25 ana SRT + 21 kare kanıt) ölçümlü boşluklar
# buldu; hepsi TAŞIMA/TEMİZLİK kuralıdır, hiçbiri SİLMEZ (Kural 1):
#
#  1) ALT YAZISIZ RIP UYARISI (S02E09/E11 vakası): iki bölüm 1'er blokla
#     ("...Project-86" jeneriği) kaldı; 5 tam kare kanıtı (_rev-e09/e11-
#     full-*.png) konuşan karakter var, karede altyazı yok — boş SRT DOĞRU
#     ama kullanıcıya bildirilmiyordu. Koşu sonunda toplam konuşma < 30 sn
#     VEYA (blok <= 3 VE toplam konuşma < 60 sn) ise stderr uyarısı +
#     stats["altyazisiz_rip_muhtemel"] = True (yalnız uyarı varken yazılır).
#  2) MIKRO-FRAGMAN GERİ BİRLEŞTİRME (BOCCHI vakası): gerçek diyalog
#     "Kes şunu!" (12:08.4-12:09.3) _ekran'da 0.125 sn'lik kırıntılarda
#     kalmıştı (mikro_geri_kondu: 0). Ekran havuzundaki bloklarda harf
#     oranı >= 0.6 + >= 2 sesli + sözlükte kelime taşıyan satırlar
#     ("Türkçe-karakter ağırlıklı") ana SRT'ye geri konur; ardışık
#     kırıntılar tek blok olur; kalan çöp satırlar _ekran'da kalır.
#     Denge (GT, _pf2-blends_ekran.srt): Japonca kredi / "OurpıIse" /
#     "inya" GERİ DÖNMEZ (CJK ve sözlük eşiği); ölçüm README'de.
#  3) TEK BAŞINA CJK KALINTISI (6+ örnek: 中/テ/著/二 diyalog satırında):
#     post-fix'te çevresi boşluk/satır sınırı olan TEK CJK karakter
#     (U+3000-U+9FFF) kaldırılır, günlüğe "ekran-kalinti" sınıfıyla yazılır.
#     Tam CJK satırlar DOKUNULMAZ (o, ekran sınıflandırıcısının işidir).
#  4) ELLIPSIS ONARIMI (3 bozulum biçimi): satır sonu "_" ve ".." ->
#     "..."; tek "." dokunulmaz (gerçek nokta olabilir). Günlük sınıfı:
#     "ellipsis". Ön ölçüm (eski ana SRT'ler): ".." 23 satır, "_" 17 satır.
#  5) SFX ÇÖPÜ AYRIMI (BOCCHI "V;+ 4+ @ R k7"): _split_noise eşiğinin
#     (conf<0.45 ∧ dur<0.6 ∧ harf<0.5) genişletmesi: harf oranı < 0.4 ∧
#     süre < 2 sn ∧ komşu blokla metin bağı yok -> _ekran. Ön ölçüm (eski
#     ana SRT'ler): BLEND-S 1 aday (22:04 çöp bloğu), BOCCHI/86/1.Bolum 0
#     aday — GT diyalog profili buna girmez.
# =====================================================================
ALT_RIP_KONUSMA_SN = 30.0   # toplam konuşma eşiği (sn)
ALT_RIP_BLOK = 3            # az blok şüphesi: blok sayısı üst sınırı
ALT_RIP_TOPLAM_SN = 60.0    # az blokla beraber toplam konuşma eşiği (sn)

SFX_HARF = 0.4              # blok harf oranı alt sınırı (altı = çöp adayı)
SFX_DUR = 2.0               # blok süre üst sınırı (sn)
SFX_SIM = 0.60              # komşu metin bağı eşiği (REP_SIM ile aynı)

MIKRO_GERI_MAX_DUR = 0.5    # mikro fragman süre penceresi (BOCCHI: 0.125-0.25)
MIKRO_GERI_HARF = 0.6       # satır harf oranı eşiği
MIKRO_GERI_SESLI = 2        # en az sesli harf sayısı
MIKRO_GERI_SIM = 0.60       # komşu/zincir metin benzerlik eşiği
MIKRO_GERI_GAP = 0.5        # ardışık fragman birleştirme boşluğu (sn)
MIKRO_GERI_KOMSU_PENCE = 1.5  # komşu metin bağı zaman penceresi (sn)
_SESLI_RE = re.compile(r"[AaEeIiİoOöÖuUüÜ]")
_HARF_RUN3 = re.compile(r"[A-Za-zçğıöşüÇĞİÖŞÜ]{3,}")


def altyazisiz_rip_muhtemel(blok_sayisi, konusma_sn):
    """Koşu sonunda "video altyazısız rip olabilir" kararı (kural 1).

    İki tetik: toplam konuşma < ALT_RIP_KONUSMA_SN (her blok sayısında) ya
    da blok sayısı <= ALT_RIP_BLOK iken toplam konuşma < ALT_RIP_TOPLAM_SN.
    Ölçüm zemini: S02E09/S02E11 stats.json (blocks=1, speech_seconds=2.0,
    band_source='aday-tarama') -> True; BOCCHI (354, 878.1) ve BLEND-S
    (305, 719.2) -> False. Saf fonksiyondur, koşuya gerek olmadan
    mevcut stats değerleriyle test edilebilir."""
    if konusma_sn < ALT_RIP_KONUSMA_SN:
        return True
    return blok_sayisi <= ALT_RIP_BLOK and konusma_sn < ALT_RIP_TOPLAM_SN


def _sfx_ayir(ana, fps):
    """Kural 5: SFX çöp profilli ana bloklarını _ekran havuzuna taşır.

    Profil: harf oranı < SFX_HARF ∧ süre < SFX_DUR ∧ komşu blokla metin
    bağı YOK (normalize benzerlik >= SFX_SIM değil). Komşu bağı şartı
    "Kit-aura! / V;+ ..." gibi diyalog-SFX KARIŞIKI blokları korur: bloğun
    kendisi harf taşıyorsa oran zaten eşiği geçer. Taşıma vardır, silme
    yok (Kural 1). Döner: (kalan_ana, tasinanlar)."""
    kalan, tasinan = [], []
    n = len(ana)
    for i, blok in enumerate(ana):
        a, b, text, conf = blok
        dur = (b - a + 1) / fps
        if dur < SFX_DUR and _letter_ratio(text) < SFX_HARF:
            tn = _rep_norm(text)
            bag = False
            if tn:
                for j in (i - 1, i + 1):
                    if 0 <= j < n:
                        tn2 = _rep_norm(ana[j][2])
                        if tn2 and difflib.SequenceMatcher(
                                None, tn, tn2).ratio() >= SFX_SIM:
                            bag = True
                            break
            if bag:
                kalan.append(blok)
            else:
                tasinan.append(blok)
        else:
            kalan.append(blok)
    return kalan, tasinan


def _diyalog_kirintisi(satir, sozluk):
    """Kural 2'nin satır testi: gerçek diyalog kırıntısı mı?

    Dört şart BİRLİKTE: CJK içermemesi, harf oranı >= MIKRO_GERI_HARF,
    >= MIKRO_GERI_SESLI sesli harf ve >=3 harfli en az bir kelimenin
    sözlükte FREQ_MIN üstü frekansla bulunması ("Türkçe-karakter
    ağırlıklı"). Ölçüm tabanı (_pf2-blends_ekran.srt + _ekran Kaya Bocchi):
    "Kes şunu!" / "Beni öldürüyorsun." True; "OurpıIse", "inya", "JI I
    VII", "IijUi", "Anhi", "V:+ 47t 4 R 17", "49" False."""
    if _CJK_RE.search(satir):
        return False
    ns = [c for c in satir if not c.isspace()]
    if not ns:
        return False
    if sum(1 for c in ns if c.isalpha()) / len(ns) < MIKRO_GERI_HARF:
        return False
    if len(_SESLI_RE.findall(satir)) < MIKRO_GERI_SESLI:
        return False
    return any(sozluk.get(_tr_lower(m.group(0)), 0) >= FREQ_MIN
               for m in _HARF_RUN3.finditer(satir))


# --- düzeltme 1 (04.10.2026): SATIR-DÜZEYİ SFX TEMİZLİĞİ -----------------
# BOCCHI S01E08 ana SRT blok 166: diyalog bloğunun İÇİNDE 'V;+ 4+ @ R k7'
# tarzı SFX/noise satırı. Blok-harf-oranı 0.63 ((14+3)/(16+9)) olduğu için
# blok-bazlı kurallar yetmiyor: _split_noise harf<0.5, _sfx_ayir harf<0.4 —
# blok ikisini de geçiyor. Çare satır bakışı: blokta en az bir TÜRKÇE-sağlam
# satır varken, ölçülen çöp profilindeki satır bloktan DÜŞÜRÜLÜR.
# Ölçüm tabanı (_imp-bocchi blok 166 'V;+ 4+ @ R k7'): boşluksuz 9 karakter,
# harf 3 (V,R,k) -> oran 0.33; sembol 6/9 ağırlıklı; 3+ harfli kelime yok.
# Kalan satır 'Kit-aura! Kit-aura!' harf 14/16 = 0.875 -> düşmez; sözlükte
# 'kit' 4567 / 'aura' 901 (OPUS-TR) Türkçe-sağlam testini geçirir.
SFX_SATIR_HARF = 0.4        # satır harf oranı alt sınırı (altı = çöp adayı)
SFX_SATIR_UZUN = 30         # satır boşluksuz uzunluk üst sınırı


def _satir_sfx_mi(satir, sozluk):
    """Tek satır SFX/çöp adayı mı? (_satir_sfx_temizle'nin satır testi)

    Dört şart BİRLİKTE: harf oranı < SFX_SATIR_HARF, boşluksuz uzunluk <
    SFX_SATIR_UZUN ve (sembol ağırlıklı VEYA >=3 harfli hiçbir kelimesi
    sözlükte FREQ_MIN üstü değil). CJK-çoğunluk satır KORUNUR (ekran
    sınıflandırıcısının işidir; _duzelt_metin ekran-kalinti guard'ıyla aynı
    eşik: cjk*2 >= boşluksuz uzunluk)."""
    ns = [c for c in satir if not c.isspace()]
    if not ns or len(ns) >= SFX_SATIR_UZUN:
        return False
    if sum(1 for c in ns if c.isalpha()) / len(ns) >= SFX_SATIR_HARF:
        return False
    cjk = sum(1 for c in ns if _CJK_RE.match(c))
    if cjk * 2 >= len(ns):                     # CJK çoğunluk: dokunma
        return False
    sembol = len(ns) - sum(1 for c in ns if c.isalpha())
    kelime_var = any(sozluk.get(_tr_lower(m.group(0)), 0) >= FREQ_MIN
                     for m in _HARF_RUN3.finditer(satir))
    return sembol * 2 > len(ns) or not kelime_var


def _satir_sfx_temizle(metin, sozluk, kayit=None, zaman=None):
    """Ana SRT bloğu İÇİNE gömülü SFX satırlarının satır-düzeyi temizliği
    (düzeltme 1; post-fix zincirinin ilk adımı).

    Kural: satır _satir_sfx_mi profilindeyse VE blokta en az bir
    TÜRKÇE-sağlam satır (_diyalog_kirintisi) varsa satır bloktan DÜŞÜRÜLÜR.
    Günlük sınıfı 'sfx-satir'; düşen satırın içeriği 'eski' alanında kalır
    (Kural 1: sessiz silme yok).

    KORUMALAR: (1) tek satırlık bloklara dokunulmaz; (2) bloğun TÜM
    satırları çöpse dokunulmaz ('en az bir Türkçe-sağlam satır' şartı bunu
    garanti eder — o blok _split_noise/_ekran işidir); (3) CJK-çoğunluk
    satırlar düşürülmez. Döner: (yeni_metin, değişiklikler[])."""
    satirlar = metin.split("\n")
    if len(satirlar) <= 1:
        return metin, []
    if not any(s.strip() and _diyalog_kirintisi(s, sozluk) for s in satirlar):
        return metin, []
    kalan, dusen, degis = [], [], []
    for s in satirlar:
        if _satir_sfx_mi(s, sozluk):
            dusen.append(s)
        else:
            kalan.append(s)
    if not dusen:
        return metin, []
    for s in dusen:
        degis.append({"zaman": zaman, "eski": s, "yeni": "",
                      "sinif": "sfx-satir",
                      "detay": "blok içi SFX satırı düşürüldü (harf<%.2f, "
                               "boşluksuz uzunluk<%d, sembol ağırlıklı ya "
                               "da sözlüksüz)" % (SFX_SATIR_HARF,
                                                  SFX_SATIR_UZUN)})
        if kayit is not None:
            kayit.append(degis[-1])
    return "\n".join(kalan), degis


def _mikro_geri(ana, adaylar, fps, sozluk):
    """Kural 2: _ekran havuzundaki mikro fragmanlardaki diyalog kırıntılarını
    ana SRT'ye geri birleştirir (BOCCHI vakası).

    Akış: (1) aday bloklar (süre <= MIKRO_GERI_MAX_DUR) satır satır
    _diyalog_kirintisi ile taranır; (2) çıkarılan metin ana SRT'deki
    komşu bloklarda zaten VARSA (MIKRO_GERI_KOMSU_PENCE penceresi,
    normalize benzerlik >= MIKRO_GERI_SIM) blok DÖNMEZ — "Kit-aura!" ve
    "Beni öldürüyorsun." kırıntıları ana SRT'de bulunduğu için çift
    kayıt üretilmez; (3) ardışık kırıntılar (gap <= MIKRO_GERI_GAP,
    benzer metin) TEK blok olur; (4) kırıntının çöp satırları yerinde
    _ekran'da kalır (Kural 1: içerik kaybı yok), yalnız temiz blok
    ana listeye eklenir. tekrar (--split_repeat) ve jenerik (--jenerik)
    ile taşınan bloklar aday DEĞİLDİR — kullanıcı beyanı geri dönmez.

    Döner: (yeni_ana, geri_bloklar, cikarilan_id, bilgi). `cikarilan_id`
    tamamen tüketilen aday blokların id() kümesidir (çağıran ekran
    listesinden süzer); artık satırlar aday bloklarının ÜZERİNE yerinde
    yazılmıştır (aynı liste nesneleri micro_moved/gurultu/ekran_bloklari
    içinde yaşar)."""
    frag = []
    for idx, blok in enumerate(adaylar):
        a, b, text, conf = blok
        if (b - a + 1) / fps > MIKRO_GERI_MAX_DUR:
            continue
        satirlar = text.split("\n")
        iyi = [s for s in satirlar
               if s.strip() and _diyalog_kirintisi(s, sozluk)]
        if iyi:
            frag.append({"idx": idx, "a": a, "b": b, "iyi": iyi,
                         "conf": conf})
    if not frag:
        return list(ana), [], set(), []
    # komşu metin bağı: çıkarılan içerik ana SRT'de zaten varsa dönme
    ana_norm = [(_rep_norm(r[2]), r[0], r[1]) for r in ana]
    secilen = []
    for f in frag:
        cn = _rep_norm("\n".join(f["iyi"]))
        s = f["a"] / fps - MIKRO_GERI_KOMSU_PENCE
        e = (f["b"] + 1) / fps + MIKRO_GERI_KOMSU_PENCE
        bag = False
        if cn:
            for nn, na, nb in ana_norm:
                if nb / fps < s or na / fps > e or not nn:
                    continue
                if difflib.SequenceMatcher(
                        None, cn, nn).ratio() >= MIKRO_GERI_SIM:
                    bag = True
                    break
        if not bag:
            secilen.append(f)
    if not secilen:
        return list(ana), [], set(), []
    # ardışık fragmanlar tek blok (zamansal komşu birleştirme)
    secilen.sort(key=lambda f: f["a"])
    zincir = []
    for f in secilen:
        if zincir:
            son = zincir[-1]
            gap = (f["a"] - son["b"]) / fps
            n1 = _rep_norm("\n".join(son["iyi"]))
            n2 = _rep_norm("\n".join(f["iyi"]))
            if gap <= MIKRO_GERI_GAP and n1 and n2 and \
                    difflib.SequenceMatcher(None, n1, n2).ratio() >= \
                    MIKRO_GERI_SIM:
                son["b"] = max(son["b"], f["b"])
                if len("\n".join(f["iyi"])) > len("\n".join(son["iyi"])):
                    son["iyi"] = f["iyi"]
                son["conf"] = max(son["conf"], f["conf"])
                son["uye"].append(f)
                continue
        zincir.append({"a": f["a"], "b": f["b"], "iyi": list(f["iyi"]),
                       "conf": f["conf"], "uye": [f]})
    # kalan çöp satırlar aday bloğunda yerinde kalır (Kural 1)
    cikarilan = set()
    for z in zincir:
        for u in z["uye"]:
            blok = adaylar[u["idx"]]
            kalan_satir = [s for s in blok[2].split("\n")
                           if s.strip() and s not in z["iyi"]]
            if kalan_satir:
                blok[:] = [blok[0], blok[1], "\n".join(kalan_satir), blok[3]]
            else:
                cikarilan.add(id(blok))
    geri_bloklar = [[z["a"], z["b"], "\n".join(z["iyi"]), z["conf"]]
                    for z in zincir]
    bilgi = [{"start": round(z["a"] / fps, 3),
              "end": round((z["b"] + 1) / fps, 3),
              "metin": "\n".join(z["iyi"])[:60],
              "fragman": len(z["uye"])} for z in zincir]
    yeni_ana = sorted(ana + geri_bloklar, key=lambda r: r[0])
    return yeni_ana, geri_bloklar, cikarilan, bilgi


# =====================================================================
# TÜRKÇE POST-FIX KATMANI + EKRAN-YAZI SINIFLANDIRICISI (04.10.2026) ---
#
# BLEND-S GT ölçümü (_gt2-rapor.json): diyalog CER %5.41, ekran %58.
# Hata sınıfları: kelime kaynaşması %31, ekran-yazısı %31, diakritik %23,
# harf yutma %15. Bu bölümün motoru _pf-kalibrasyon3.py'de ÖLÇÜMLÜ
# geliştirildi ve birebir taşındı:
#   * diyalog CER: %5.41 -> %3.15 (36 -> 21 edit / 666 GT karakter)
#   * ekran ayrımı: GT'nin 4 ekran bloğu ayrılıyor, 20 GT diyalog bloğunun
#     hiçbiri ayrılmıyor (denge, önceki SRT'nin 310 bloğu üzerinde)
#
# ONARIM AİLESİ (hepsi sözlük-kanıtlı, tekil/egemen ve KAYITLI):
#   diakritik       : 1-2 nokta değişikliği (s<->ş, c<->ç, g<->ğ, ı<->i);
#                     sözlükte OLAN kelimeye de uygulanır — yanlış-yazım
#                     formları listede var ('anlasılan' 11, 'basarısız' 38,
#                     'tesekkürler' 4987; OPUS ölçümü) ve doğru form >=10x
#                     egemense yükseltilir ('anlaşılan' 39044)
#   harf-yutma      : 1 harf EKLEME ('zolamayacağım'->'zorlamayacağım')
#   harf-değiştirme : 1 harf değiştirme ('kanigor'->'kanıyor',
#                     'teşekkir'->'teşekkür') — yalnız küçük-harf kelime
#   kaynakma        : 2 parça ayırma ('icinitesekkürler'->'için teşekkürler')
#   noktalama       : satır başı kesme, sondaki ':s', boşluklu '.', bağımsız
#                     1-2 basamaklı sayı artığı
#
# GÜVENLİK KURALLARI (her biri bir kalibrasyon hatasından doğdu):
#   * kazanan sözlük adayı ikinciye karşı >=DOMINANS (10x) frekans-egemen
#     olmalı; split skoru işlem başına cezalanır (0.3^ops) ve >=300 olmalı
#     ('sikim kıçın' 93 -> engellendi; 'burada çalışmaz' 3975 -> geçti)
#   * split parçaları >=4 harf ('Basarısız'->'Başarı sız' hatası)
#   * ek-veto: kelimenin son 3/4 harfi atılınca sözlükte çıkıyorsa kelime
#     ekli bir biçimdir, bölünmez ('etmemiştik'->'etme mistik' hatası)
#   * büyük-harfli kelimelere harf-ekle/değiştir uygulanmaz ('Maika'->'marka')
#   * birden çok split adayı skor beraberliğinde AYNI metni ÜRETMEYORSA
#     dokunulmaz (uzlaşma kuralı)
#   * sınıflandırıcının görünümü YALNIZ diakritik onarımı uygular — ağır
#     onarım ('inya'->'inşa') ekran bloğunu sözlükte gösterip ayrımı
#     engelliyordu (kalibrasyon v5 hatası)
# =====================================================================
DUZELT_MIN_LEN = 3            # bu uzunluğun altındaki kelimeye dokunulmaz
DUZELT_SPLIT_MIN = 7          # kaynaşma adayı en az bu kadar harf
DUZELT_SPLIT_MIN_PARCA = 4    # split parçasının en küçük uzunluğu
DUZELT_GENIS_MAX = 14         # bu uzunluğun üzerinde harf-ekle/değiştir yok
FREQ_MIN = 50                 # onarım adayının en küçük sözlük frekansı
DOMINANS = 10                 # kazanan adayın ikinciye karşı frekans egemenliği
SPLIT_OP_CEZA = 0.3           # split skorunda her onarım işlemi cezası
SPLIT_SKOR_MIN = 300          # işlem-cezalı split skor tabanı
ZAYIF_SOZLUK_FREQ = 300       # 8. tur: seyrek sözlük girdisi eşiği (altı
                              # "geçerli kelime" sayılmaz, onarılabilir)
ZAYIF_SOZLUK_DOMINANS = 100   # seyrek girdiyi deviren komşu frekans oranı
EKRAAN_CONF = 0.15            # ekran sınıflandırıcısı conf tabanı (probe2:
                              # ekran blokları 0.033-0.077; GT diyalog min 0.248)

_TR_ALFABE = "abcçdefgğhıijklmnoöprsştuüvyz"
_DIAC_ALTS = {"s": "ş", "ş": "s", "c": "ç", "ç": "c", "g": "ğ", "ğ": "g",
              "ı": "i", "i": "ı"}
_TR_UP = str.maketrans("abcçdefgğhıijklmnoöprsştuüvyz",
                       "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ")
_TR_LOW = str.maketrans("Iİ", "ıi")
_KELIME_RE = re.compile(r"^([^A-Za-zçğıöşüÇĞİÖŞÜ0-9]*)([A-Za-zçğıöşüÇĞİÖŞÜ]+)"
                        r"([^A-Za-zçğıöşüÇĞİÖŞÜ0-9]*)$")
_HARF_RUN = re.compile(r"[A-Za-zçğıöşüÇĞİÖŞÜ]{4,}")
_CJK_RE = re.compile(r"[\u3000-\u9fff]")
_SAYI_RE = re.compile(r"(?<=\S)\s\d{1,2}(?=\s\S)")

# 06.10.2026 — apostrof-yutma tablosu (ölçümlü, E1074): kaynak karede
# "Fil'in Banyosu" yazarken OCR "FilFin Banyosu" üretti (apostrof glifi
# yutuldu, ardındaki parça büyük harfle kelimeye yapıştı). Mekanizma
# seçimi:
#   (a) genel patern kuralı (kelime-içi büyük-harf sınırına apostrof ekle)
#       REDDEDİLDİ — kelime-içi büyük harf bu hattın ÇÖP imzasıyla AYNI
#       (_orta_harf_anomalisi; ölçülen vaka "OurpıIse", SRT#24, conf 0.81):
#       kural çöpü sahte kelimelere "onarır" ve ölçülmemiş yanlış-pozitif
#       üretir (Kural 1). Türkçede apostrof yalnız özel addan sonra geçerli
#       olduğundan, özel-ad bilmeden kural güvenli olamaz.
#   (b) sabit düzeltme tablosu SEÇİLDİ — çekirdeğe birebir eşleşme,
#       yapısal olarak 0 yanlış-pozitif; ogren.py öğret-akışıyla aynı
#       aile: yeni vaka ölçüldükçe satır eklenir. Değişiklik "apostrof"
#       sınıfıyla günlüğe yazılır (Kural 1: sessiz değişiklik yok).
_APOSTROF_DUZELT = {"FilFin": "Fil'in"}

_SOZLUK = {"sozluk": None, "kaynak": None}
_KULLANICI = {"kurallar": None, "kaynak": None}

# turkce-sozluk.txt bulunamazsa devreye giren çekirdek liste (OPUS
# OpenSubtitles v2018 tr frekans sırasının ilk 300 kelimesi).
_CEKIRDEK_SOZLUK = (
    "bir bu ne ve için mi de ben çok ama var evet da mı değil şey hayır "
    "daha sen kadar bana yok onu seni bunu beni gibi iyi tamam her benim "
    "sana ki neden ya zaman senin sadece burada nasıl olduğunu hiç sonra "
    "şimdi en öyle mu şu misin hadi önce biraz musun güzel oldu böyle ona "
    "lütfen yani bile artık bak geri onun istiyorum peki eğer kim çünkü "
    "biliyorum gerçekten başka tek olarak belki doğru bay büyük buraya "
    "biri olan olur adam hey olacak in hiçbir efendim demek yardım biz "
    "ile nerede sanırım tanrım tüm orada bilmiyorum ın gün fazla bunun "
    "ederim şeyi teşekkürler yeni et son merhaba kötü gece şeyler biliyor "
    "iki sorun tam bütün hemen harika olsun gerek ol siz onları size "
    "üzgünüm gel diye küçük devam teşekkür bayan tabii olabilir aynı ver "
    "kendi yüzden bize bizi haydi hakkında kız oluyor nin izin sizi iş "
    "dur buna git kimse geldi bizim asla yoksa mısın baba göre seninle "
    "vardı değilim önemli tekrar benimle işte içinde söyle özür mü nın "
    "dostum anne selam yine hala al lanet bugün üç uzun herkes aslında "
    "oh bakalım olmak para dakika olmaz geliyor bence gerçek pekala "
    "şekilde biliyorsun birlikte istiyorsun onlar şunu birkaç zaten ilk "
    "saat lazım onunla yapıyorsun neler ister emin pek gidelim bırak yer "
    "hep miyim buradan çocuk yıl yere sizin eve bekle söyledi nedir "
    "nereye burası dilerim dan hepsi un karşı hazır etmek olduğu an "
    "olmalı kaç istemiyorum çocuklar az kendini ilgili söz ediyorum eski "
    "dinle anda gerekiyor benden tane tanrı işe bunlar fakat işi zor "
    "kesinlikle yapmak kişi kadın görmek istiyor diğer misiniz sağ "
    "seviyorum kabul bundan niye onlara aman elbette kez yarın yapma "
    "edin hem senden falan oraya hoş yerde gidip zorunda yanlış kontrol "
    "özel doktor ediyor tatlım gereken dedim geç nereden hafta ye yemek "
    "birini bakın mutlu").split()


def _sozluk_yukle():
    """turkce-sozluk.txt'i aracın yanından yükler (tembel tekil).

    Biçim: 'kelime frekans' satırları (OPUS OpenSubtitles v2018 tr frekans
    listesi, freq>=5 süzülmüş, ~987K kelime, 15.7 MB). Dosya yoksa gömülü
    çekirdek liste kullanılır ve stderr'e BİR satır not yazılır."""
    if _SOZLUK["sozluk"] is not None:
        return _SOZLUK["sozluk"]
    yol = Path(__file__).resolve().parent / "turkce-sozluk.txt"
    soz = {}
    try:
        for satir in yol.read_text(encoding="utf-8").splitlines():
            p = satir.split()
            if len(p) == 2 and p[0] not in soz:
                try:
                    soz[p[0]] = int(p[1])
                except ValueError:
                    soz[p[0]] = 1
        _SOZLUK["sozluk"] = soz
        _SOZLUK["kaynak"] = f"{yol.name} ({len(soz)} kelime)"
        note(f"[duzelt] sozluk yuklendi: {yol.name} ({len(soz)} kelime)")
    except OSError as e:
        soz = {w: 1 for w in _CEKIRDEK_SOZLUK}
        _SOZLUK["sozluk"] = soz
        _SOZLUK["kaynak"] = (f"gömülü çekirdek ({len(soz)} kelime; "
                             f"{yol.name} yüklenemedi: {e})")
        note(f"[duzelt] {yol.name} yok — gömülü çekirdek sozluk "
             f"({len(soz)} kelime) kullaniliyor")
    return soz


def _kullanici_sozluk_yukle():
    """kullanici-sozlugu.txt: UI'daki 'Öğret' ile yazılan kurallar.

    Biçim: her satır `eski<TAB>yeni` (UI /api/ogret hedefi). Sıra korunur;
    aynı 'eski' birden çok geçerse SON satır kazanır. Dosya yoksa boş liste
    döner ve not basılmaz (öğretilmiş kural yoksa davranış değişmez)."""
    if _KULLANICI["kurallar"] is not None:
        return _KULLANICI["kurallar"]
    yol = Path(__file__).resolve().parent / "kullanici-sozlugu.txt"
    kurallar = {}
    try:
        for satir in yol.read_text(encoding="utf-8-sig",
                                   errors="replace").splitlines():
            if "\t" not in satir:
                continue
            eski, yeni = satir.split("\t", 1)
            eski, yeni = eski.strip(), yeni.strip()
            if eski and yeni and eski != yeni:
                kurallar[eski] = yeni
    except OSError:
        pass
    _KULLANICI["kurallar"] = list(kurallar.items())
    _KULLANICI["kaynak"] = f"{yol.name} ({len(kurallar)} kural)"
    if kurallar:
        note(f"[duzelt] kullanici sozlugu: {len(kurallar)} kural "
             f"({yol.name})")
    return _KULLANICI["kurallar"]


def _tr_lower(s):
    return s.translate(_TR_LOW).lower()


def _tr_upper(s):
    return s.translate(_TR_UP).upper()


def _diakritik_varyantlar(kucuk, max_edits=2, ops=False):
    """<=max_edits nokta değişikliği varyantları (sözlük kontrolü YOK).
    ops=True -> (varyant, işlem sayısı) verir."""
    pozlar = [i for i, c in enumerate(kucuk) if c in _DIAC_ALTS]
    if not pozlar or len(pozlar) > 12:
        return
    for k in range(1, min(max_edits, len(pozlar)) + 1):
        for konumlar in combinations(pozlar, k):
            yeni = list(kucuk)
            for p in konumlar:
                yeni[p] = _DIAC_ALTS[yeni[p]]
            yield ("".join(yeni), k) if ops else "".join(yeni)


def _genis_varyantlar(kucuk):
    """1 ağır işlem (sil/ekle/değiştir) varyantları; küçük-harf.
    sil: her uzunluk; ekle/değiştir: <=DUZELT_GENIS_MAX (maliyet sınırı)."""
    n = len(kucuk)
    for i in range(n):                          # sil
        yield kucuk[:i] + kucuk[i + 1:]
    if n > DUZELT_GENIS_MAX:
        return
    for i in range(n + 1):                      # ekle
        for h in _TR_ALFABE:
            yield kucuk[:i] + h + kucuk[i:]
    for i in range(n):                          # değiştir
        for h in _TR_ALFABE:
            if h != kucuk[i]:
                yield kucuk[:i] + h + kucuk[i + 1:]


def _egemen(havuz):
    """{aday: freq} havuzundan kazanan: freq>=FREQ_MIN ve ikinciye karşı
    >=DOMINANS egemen. Yoksa None."""
    if not havuz:
        return None
    sirali = sorted(havuz.items(), key=lambda kv: -kv[1])
    if sirali[0][1] < FREQ_MIN:
        return None
    if len(sirali) > 1 and sirali[0][1] < DOMINANS * sirali[1][1]:
        return None
    return sirali[0][0]


def _ek_veto(kucuk, sozluk):
    """Son 3/4 harfi atılınca sözlükte çıkıyorsa kelime ekli bir biçimdir
    -> bölme VETO. Ölçüm: 'etmemiştik'->'etme mistik' yanlış bölmesini
    önler; 'icinitesekkürler'/'buradaçalışmaz' veto YEMEZ (ölçüldü)."""
    for n in (3, 4):
        if len(kucuk) > n + 3 and kucuk[:-n] in sozluk:
            return True
    return False


def _parca_onar(kucuk, sozluk):
    """Bölme parçasının onarımı: (aday, freq, işlem sayısı) | None.

    - parça sözlükte VARSA: as-is; yalnız salt-diakritik varyant frekansı
      >=DOMINANS x ise o değişir ('tesekkürler' 4987 -> 'teşekkürler'
      682375; harf-değiştirme KATILMAZ — 'çalışmaz'->'çalışmak' gibi
      DOĞRU parçaları bozar; ölçüldü).
    - YOKSA: ağır(1) x diakritik(<=2) havuzu; _egemen; işlem = 1 ağır +
      kullanılan diakritik sayısı (GERÇEK sayı: 'akiçin'->'kıçın' 3 işlem
      -> skor 93 engellenir; 'çokı'->'çok' 1 işlem -> geçer)."""
    f = sozluk.get(kucuk)
    if f:
        en_iyi = None
        for v, ndiac in _diakritik_varyantlar(kucuk, 2, ops=True):
            fv = sozluk.get(v)
            if fv and fv >= DOMINANS * f and (en_iyi is None or fv > en_iyi[1]):
                en_iyi = (v, fv, ndiac)
        if en_iyi:
            return (en_iyi[0], en_iyi[1], en_iyi[2])
        return (kucuk, f, 0)
    havuz = {}                       # aday -> [max_freq, min_ops]
    for agir in _genis_varyantlar(kucuk):
        g = sozluk.get(agir)
        if g:
            eski = havuz.get(agir)
            if eski is None or g > eski[0]:
                havuz[agir] = [g, 1]
        for v, ndiac in _diakritik_varyantlar(agir, 2, ops=True):
            fv = sozluk.get(v)
            if fv:
                eski = havuz.get(v)
                if eski is None or fv > eski[0]:
                    havuz[v] = [fv, 1 + ndiac]
                elif fv == eski[0]:
                    havuz[v][1] = min(eski[1], 1 + ndiac)
    if not havuz:
        return None
    sirali = sorted(havuz.items(), key=lambda kv: -kv[1][0])
    if sirali[0][1][0] < FREQ_MIN:
        return None
    if len(sirali) > 1 and sirali[0][1][0] < DOMINANS * sirali[1][1][0]:
        return None
    return (sirali[0][0], sirali[0][1][0], sirali[0][1][1])


def _kaynasma_ayir(kucuk, sozluk, min_uzun=DUZELT_SPLIT_MIN,
                   min_parca=DUZELT_SPLIT_MIN_PARCA):
    """Tek çözümlü 2-parça ayırma: (parça1, parça2) | None.

    Skor = min(f1,f2) x 0.3^(işlem); SPLIT_SKOR_MIN tabanı. Skor
    beraberliğinde adaylar AYNI metni üretmiyorsa None (uzlaşma kuralı).
    Frekans-tekel gerekçesi: 'icinitesekkürler' için k=4 'için|teşekkürler'
    ve k=5 aynı sonucu verir — uzlaşır; gerçekten farklı adaylar
    çıkıyorsa güven yok."""
    if len(kucuk) < min_uzun or _ek_veto(kucuk, sozluk):
        return None
    adaylar = []
    for k in range(min_parca, len(kucuk) - min_parca + 1):
        p1, p2 = kucuk[:k], kucuk[k:]
        o1 = _parca_onar(p1, sozluk)
        if o1 is None:
            continue
        o2 = _parca_onar(p2, sozluk)
        if o2 is None:
            continue
        skor = min(o1[1], o2[1]) * (SPLIT_OP_CEZA ** (o1[2] + o2[2]))
        if skor < SPLIT_SKOR_MIN:
            continue
        adaylar.append((skor, o1[0], o2[0]))
    if not adaylar:
        return None
    adaylar.sort(key=lambda t: -t[0])
    if len(adaylar) > 1 and adaylar[0][0] <= adaylar[1][0]:
        ilk = (adaylar[0][1], adaylar[0][2])
        beraber = [a for a in adaylar if a[0] == adaylar[0][0]]
        if any((a[1], a[2]) != ilk for a in beraber[1:]):
            return None
    return (adaylar[0][1], adaylar[0][2])


def _token_yap(on, kucuk_duz, son, core):
    """Onarilan küçük-harf çekirdeği ile özgün büyük/küçük görünümü kurar."""
    if core.isupper():
        return on + _tr_upper(kucuk_duz) + son
    if core[0].isupper():
        return on + kucuk_duz.capitalize() + son
    return on + kucuk_duz + son


def _kelime_duzelt(token, sozluk):
    """Tek token -> (yeni_token, sinif, eski_core, yeni_core) | (token, None...).
    sinif: diakritik / harf-yutma / harf-degistirme / kaynakma."""
    m = _KELIME_RE.match(token)
    if not m:
        return token, None, None, None
    on, core, son = m.groups()
    if len(core) < DUZELT_MIN_LEN or not core.isalpha():
        return token, None, None, None
    kucuk = _tr_lower(core)
    # 1) diakritik (ağır işlem YOK): egemen aday; sözlükteki kelime de
    #    yükseltilebilir (yanlış-yazım formları listede var)
    havuz = {kucuk: sozluk[kucuk]} if kucuk in sozluk else {}
    for v in _diakritik_varyantlar(kucuk, 2):
        f = sozluk.get(v)
        if f:
            havuz[v] = max(havuz.get(v, 0), f)
    aday = _egemen(havuz)
    if aday and aday != kucuk:
        return _token_yap(on, aday, son, core), "diakritik", core, aday
    if kucuk in sozluk:
        # 8. tur: ZAYIF SÖZLÜK GİRDİSİ YÜKSELTMESİ. OPUS listesinde
        # çöp/artık girdiler var (ölçüm: 'gibb' 133, 'gibbs' 10347) ve
        # eski erken-dönüş bunları "geçerli kelime" sayıp koruyordu;
        # 'gibb' bu yüzden harf-değiştirmeye hiç girmiyordu. Girdi
        # SEYREKSE (f0 < ZAYIF_SOZLUK_FREQ) ve tek-harf-değişimli bir
        # komşu >= ZAYIF_SOZLUK_DOMINANS kat daha sık ise komşu kazanır
        # ('gibi' 2.430.246 / 'gibb' 133 -> 18 272x). Lowercase şartı ve
        # yalnız DEĞİŞTİRME (ekle/sil YOK) — mevcut güvenlik kurallarıyla
        # aynı aile. Ölçüm: _kal-oku.py token testi + GT koşusu.
        f0 = sozluk[kucuk]
        if core.islower() and f0 < ZAYIF_SOZLUK_FREQ:
            en_iyi = None
            for agir in _genis_varyantlar(kucuk):
                if len(agir) != len(kucuk):
                    continue
                fv = sozluk.get(agir)
                if fv and fv >= ZAYIF_SOZLUK_DOMINANS * f0 and \
                        (en_iyi is None or fv > en_iyi[1]):
                    en_iyi = (agir, fv)
            if en_iyi:
                return (_token_yap(on, en_iyi[0], son, core),
                        "harf-degistirme", core, en_iyi[0])
        return token, None, None, None
    # 2) geniş onarım (yalnız küçük-harf; EKLE/DEĞİŞTİR — silme yok:
    #    'zolamayacağım'->'olamayacağım' gibi yanlış silmeleri engeller)
    if core.islower():
        havuz = {}
        tur = {}
        for agir in _genis_varyantlar(kucuk):
            if len(agir) != len(kucuk):
                continue                     # yalnız değiştirme
            f = sozluk.get(agir)
            if f:
                havuz[agir] = max(havuz.get(agir, 0), f)
                tur[agir] = "harf-degistirme"
        for i in range(len(kucuk) + 1):      # harf ekleme (yutma)
            for h in _TR_ALFABE:
                agir = kucuk[:i] + h + kucuk[i:]
                f = sozluk.get(agir)
                if f:
                    havuz[agir] = max(havuz.get(agir, 0), f)
                    tur[agir] = "harf-yutma"
                for v, ndiac in _diakritik_varyantlar(agir, 2, ops=True):
                    fv = sozluk.get(v)
                    if fv:
                        havuz[v] = max(havuz.get(v, 0), fv)
                        tur[v] = "harf-yutma"
        aday = _egemen(havuz)
        if aday:
            return _token_yap(on, aday, son, core), tur[aday], core, aday
    # 3) kaynaşma ayırma
    if len(kucuk) >= DUZELT_SPLIT_MIN:
        coz = _kaynasma_ayir(kucuk, sozluk)
        if coz:
            if core.isupper():
                yeni = _tr_upper(coz[0]) + " " + _tr_upper(coz[1])
            elif core[0].isupper():
                yeni = coz[0].capitalize() + " " + coz[1]
            else:
                yeni = coz[0] + " " + coz[1]
            return on + yeni + son, "kaynakma", core, yeni
    return token, None, None, None


def _apostrof_tablo(tok):
    """Apostrof-yutma tablosunu tek tokena uygular (06.10.2026, E1074).

    Tablo, çekirdeğe (_KELIME_RE grup 2) birebir bakar; ön/son noktalama
    (grup 1/3) korunur. Tabloda yoksa token DOKUNULMAZ — bu işlev kendi
    kendine kelime uydurmaz."""
    m = _KELIME_RE.match(tok)
    if not m:
        return tok
    on, core, son = m.groups()
    duz = _APOSTROF_DUZELT.get(core)
    return tok if duz is None else on + duz + son


def _duzelt_metin(metin, sozluk, kayit=None, zaman=None):
    """Satır satır post-fix. Döner: (yeni_metin, değişiklikler[]).

    `kayit` listesi verilirse her atomik değişiklik {zaman, eski, yeni,
    sinif, detay} olarak eklenir (Kural 1: hiçbir değişiklik kayıtsız
    kalmaz)."""
    degis = []

    def not_al(eski, yeni, sinif, detay=None):
        degis.append({"zaman": zaman, "eski": eski, "yeni": yeni,
                      "sinif": sinif, "detay": detay})
        if kayit is not None:
            kayit.append(degis[-1])

    yeni_satirlar = []
    for satir in metin.split("\n"):
        orijinal = satir
        # --- ekran-kalinti (inceleme turu 3): tek başına duran CJK harf ---
        # Ölçüm (_rev-RAPOR.md (c)-2): 6+ örnek — "…söylemiştim. 中",
        # "テ Amanın…", "著 / Karides.", "二 Horeket etti!". Yalnız
        # çevresi boşluk/satır sınırı olan TEK CJK karakter (U+3000-U+9FFF)
        # kaldırılır; CJK'lı uzun satırlar (tam Japonca satır) DOKUNULMAZ —
        # satırın CJK çoğunluğuna geçmesi tam CJK satırı sayılır (o,
        # ekran sınıflandırıcısının işidir; ölçüm: BLEND-S 00:22:15 kredi
        # satırı "程 鈴木路惠 佐藤壮太郎" bu guard ile korunur).
        _cjk_artik = [p for p in satir.split(" ")
                      if len(p) == 1 and _CJK_RE.match(p)]
        _ns_cjk = [c for c in satir if not c.isspace()] if _cjk_artik else []
        if _cjk_artik and _ns_cjk and \
                sum(1 for c in _ns_cjk if _CJK_RE.match(c)) * 2 < len(_ns_cjk):
            _yeni = " ".join(p for p in satir.split(" ")
                             if p not in _cjk_artik)
            _yeni = re.sub(r"  +", " ", _yeni).strip()
            not_al(orijinal, _yeni, "ekran-kalinti",
                   "tek başına CJK karakter: " + " ".join(_cjk_artik))
            satir = _yeni
        # --- noktalama artıkları ---
        m = re.match(r"\s*'+", satir)
        if m:
            not_al(orijinal, satir[m.end():], "noktalama",
                   "satır başı kesme işareti")
            satir = satir[m.end():]
        m = re.search(r"\s+:s$", satir)
        if m:
            not_al(orijinal, satir[:m.start()], "noktalama", "sonda ':s'")
            satir = satir[:m.start()]
        m = re.search(r"\s+\.$", satir)
        if m:
            satir = satir[:m.start()] + "."
            not_al(orijinal, satir, "noktalama", "sondaki boşluklu nokta")
        while True:                          # bağımsız 1-2 basamaklı sayı
            m = _SAYI_RE.search(satir)
            if not m:
                break
            aday = satir[:m.start()] + " " + satir[m.end():]
            if re.search(r"[A-Za-zçğıöşüÇĞİÖŞÜ]", aday):
                satir = re.sub(r"  +", " ", aday).strip()
                not_al(orijinal, satir, "noktalama", "bağımsız sayı artığı")
            else:
                break
        # --- ellipsis onarimi (inceleme turu 4; YÜKSEK GÜVENLİ) ---
        # Ölçülen üç bozulum biçiminden ikisi onarılır: satır sonu "_" ->
        # "..." (Kaya Bocchi 00:13:55) ve satır sonu ".." -> "..." (BLEND-S
        # "Anlaşılan.."). Tek "." DOKUNULMAZ (gerçek nokta olabilir);
        # "...." gibi 3+ nokta kuyrukları da korunur.
        m = re.search(r"_+$", satir)
        if m:
            satir = satir[:m.start()] + "..."
            not_al(orijinal, satir, "ellipsis", "satır sonu '_' -> '...'")
        else:
            m = re.search(r"(?<!\.)\.\.$", satir)
            if m:
                satir = satir[:m.start()] + "..."
                not_al(orijinal, satir, "ellipsis", "satır sonu '..' -> '...'")
        # --- kelime onarımları ---
        parcalar = re.split(r"(\s+)", satir)
        cikti = []
        for tok in parcalar:
            if not tok or tok.isspace():
                cikti.append(tok)
                continue
            ap_tok = _apostrof_tablo(tok)          # 06.10.2026, E1074
            if ap_tok != tok:
                not_al(tok, ap_tok, "apostrof", "apostrof-yutma tablosu")
                cikti.append(ap_tok)
                continue
            yeni_tok, sinif, eski_c, yeni_c = _kelime_duzelt(tok, sozluk)
            if sinif:
                not_al(eski_c, yeni_c, sinif)
            cikti.append(yeni_tok)
        yeni_satirlar.append("".join(cikti))
    return "\n".join(yeni_satirlar), degis


def _kullanici_uygula(metin, kurallar, kayit=None, zaman=None):
    """UI 'Öğret' kurallarını uygular (birebir metin değişimi).

    Kural bloğun tamamında aranır; birden çok geçerse hepsi değişir ve
    kayda 'n yer' detayıyla TEK satır düşer (Kural 1: kayıtsız değişiklik
    yok). Döner: (yeni_metin, değişiklikler[])."""
    degis = []
    if not kurallar or not metin:
        return metin, degis
    for eski, yeni in kurallar:
        n = metin.count(eski)
        if not n:
            continue
        metin = metin.replace(eski, yeni)
        degis.append({"zaman": zaman, "eski": eski, "yeni": yeni,
                      "sinif": "kullanici",
                      "detay": "kullanici-sozlugu"
                               + (f" ({n} yer)" if n > 1 else "")})
        if kayit is not None:
            kayit.append(degis[-1])
    return metin, degis


def _duzelt_blok_metni(metin, sozluk, kullanici, kayit=None, zaman=None):
    """Blok post-fix zinciri: blok içi SFX satır temizliği (düzeltme 1) ->
    kullanıcı kuralları (ham OCR yüzeyi) -> sözlük onarımı -> kullanıcı
    kuralları (post-fix yüzeyi).

    UI 'Öğret' kuralı hangi yüzeyden öğretilmişse (ham ya da post-fix)
    yakalanır; sondaki geçiş kullanıcı beyanını SON SÖZ yapar. SFX satır
    temizliği EN BAŞTA çalışır: çöp satır sözlük onarımına girmeden
    düşer. Döner: (yeni_metin, değişiklikler[])."""
    metin, sfx_deg = _satir_sfx_temizle(metin, sozluk, kayit, zaman)
    metin, k1 = _kullanici_uygula(metin, kullanici, kayit, zaman)
    metin, d = _duzelt_metin(metin, sozluk, kayit, zaman)
    metin, k2 = _kullanici_uygula(metin, kullanici, kayit, zaman)
    return metin, sfx_deg + k1 + d + k2


def _orta_harf_anomalisi(metin):
    """Kelime içinde (ilk konum dışında) büyük harf; kelime tümü-büyük değil.
    Ölçüm: 'OurpıIse' (SRT#24, conf 0.81 — conf yolu onu yakalayamaz)
    tespit eder; 'Maika.', 'Yuppi!', 'OTURMAK' tetiklemez (kalibrasyon)."""
    for tok in metin.split():
        m = _KELIME_RE.match(tok)
        if not m:
            continue
        core = m.group(2)
        if core.isupper() or len(core) < 3:
            continue
        if any(c.isupper() for c in core[1:]):
            return True
    return False


def _gorunum_diakritik(metin, sozluk):
    """Sınıflandırıcı görünümü: yalnız diakritik yükseltmesi uygulanmış
    metin. Ağır onarım/split YOK — 'inya'->'inşa' gibi ağır onarım ekran
    bloğunu sözlükte gösterip ayrımı engelliyordu (kalibrasyon v5 hatası)."""
    cikti = []
    for tok in metin.split(" "):
        m = _KELIME_RE.match(tok) if tok else None
        if not m:
            cikti.append(tok)
            continue
        on, core, son = m.groups()
        kucuk = _tr_lower(core)
        havuz = {kucuk: sozluk[kucuk]} if kucuk in sozluk else {}
        for v in _diakritik_varyantlar(kucuk, 2):
            f = sozluk.get(v)
            if f:
                havuz[v] = max(havuz.get(v, 0), f)
        aday = _egemen(havuz)
        if aday and aday != kucuk:
            cikti.append(_token_yap(on, aday, son, core))
        else:
            cikti.append(tok)
    return " ".join(cikti)


def ekran_profili(metin, sozluk, conf=None):
    """(vurdu, kural) — metin ekran-yazısı profilli mi?

    Kurallar: a-cjk / b1-profil / b2-jenerik / c-sembol / (False, None).
    Sözlük şartı DİAKRİTİK-ONARILMIŞ GÖRÜNÜM üzerinden değerlendirilir:
    diakritik kaybından sözlükte görünmeyen gerçek diyalog ('Anlasılan.')
    yanlışlıkla ayrılmasın. Görünüm yalnız değerlendirme içindir; ekran
    bloklarına post-fix YAZILMAZ (ham OCR korunur)."""
    ns = [c for c in metin if not c.isspace()]
    if not ns:
        return False, None
    cjk = sum(1 for c in ns if _CJK_RE.match(c))
    if cjk / len(ns) > 0.15:
        return True, "a-cjk"
    harf = sum(1 for c in ns if c.isalpha())
    if harf <= 2:
        return True, "c-sembol"
    view = _gorunum_diakritik(metin, sozluk)
    runs = [_tr_lower(r.group(0)) for r in _HARF_RUN.finditer(view)]
    if runs:
        sozlukte = [w for w in runs if w in sozluk]
        pay = len(sozlukte) / len(runs)
        kaplama = sum(len(w) for w in sozlukte) / sum(len(w) for w in runs)
        kapı = ((conf is not None and conf < EKRAAN_CONF)
                or _orta_harf_anomalisi(metin))
        if pay < 0.30 and kaplama < 0.50 and kapı:
            return True, "b1-profil"
        # (b2) jenerik kuralı — ölçüm: Vietnamca jenerik (SRT#304)
        # adlarının 7/13'ü OPUS-TR listesinde düşük frekans gürültüsü olarak
        # VAR (hong 15535, dung 90) -> pay %54 ile (b1)'i geçiyor. Ama GT
        # diyalog conf'larının EN DÜŞÜĞÜ 0.248 (probe2 ölçümü) — conf<0.15
        # + >=5 kelime + pay<%60 bileşimi GT diyalogda hiç oluşmadı.
        if len(runs) >= 5 and pay < 0.60 and kapı:
            return True, "b2-jenerik"
    return False, None


def ocr_consensus(reader, img_bin):
    """3 varyantla oku, çift yönlü benzerlik skoru en yüksek metni seç.

    Tek görüntü yolu (ikinci motorun tek görüntü okuması da bunu kullanır)."""
    return _consensus_pick(ocr_lines(reader, img_bin, **kw)
                           for kw in OCR_VARIANTS)


def _rapidocr_selftest(engine):
    """RapidOCR'ın gerçekten okuyabildiğini beyaz-metin sentetiğiyle doğrula."""
    canvas = np.zeros((64, 320, 3), np.uint8)
    cv2.putText(canvas, "MERHABA 123", (8, 46), cv2.FONT_HERSHEY_SIMPLEX,
                1.1, (255, 255, 255), 2, cv2.LINE_AA)
    out = engine(canvas)
    res = out[0] if isinstance(out, tuple) else out
    return bool(res)


def _rapidocr_read(engine, img):
    """RapidOCR çıktısını EasyOCR satır formatına çevirip grupla."""
    try:
        out = engine(img)
    except Exception:
        return "", 0.0
    res = out[0] if isinstance(out, tuple) else out
    if not res:
        return "", 0.0
    return _group_lines([(box, text, float(conf)) for box, text, conf in res])


def _ppocrv5_init():
    """PP-OCRv5 (rapidocr >=3) motorunu tembel kur; kurulamazsa None.

    Model seçimi ölçümle sabitlendi (02.10.2026 benchmark): det = PP-OCRv5
    ch mobile, rec = PP-OCRv5 latin mobile (Türkçe diakritik seti bu modelde;
    ch rec modeli yerine latin seçildi). İlk kullanımda model dosyaları
    site-packages/rapidocr/models altına iner (~30 MB)."""
    if _PP5["state"] != "untried":
        return _PP5["engine"]
    _PP5["state"] = "failed"
    try:
        from rapidocr import RapidOCR, OCRVersion, ModelType
        params = {
            "Det.ocr_version": OCRVersion.PPOCRV5, "Det.lang_type": "ch",
            "Det.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.lang_type": "latin",
            "Rec.model_type": ModelType.MOBILE,
            "Cls.use_cls": False}
        if _ORT["cuda"]:
            params["EngineConfig.onnxruntime.use_cuda"] = True
        engine = RapidOCR(params=params)
        canvas = np.zeros((64, 320, 3), np.uint8)
        cv2.putText(canvas, "MERHABA 123", (8, 46), cv2.FONT_HERSHEY_SIMPLEX,
                    1.1, (255, 255, 255), 2, cv2.LINE_AA)
        out = engine(canvas)
        txts = getattr(out, "txts", None)
        if not txts:
            raise RuntimeError("sentetik test bos sonuc")
        _PP5.update(state="ok", engine=engine)
        return engine
    except Exception as e:
        _PP5["engine"] = None
        note(f"[i] PP-OCRv5 kurulamadi ({type(e).__name__}: {e})")
        return None


def _ppocrv6_init():
    """PP-OCRv6 (rapidocr >=3.9) small motorunu tembel kur; kurulamazsa None.

    Model seçimi ölçümle sabitlendi (02.10.2026 v6 doğrulama turu,
    _v6-results.json): det = PP-OCRv6 ch small + rec = PP-OCRv6 small.
    v6'nın rec'i tek çokdilli dosyadır (lang_type ch/tr aynı modele
    çözümlenir); SERVER varyanti YOK (TINY/SMALL/MEDIUM) ve medium olculdu:
    tr CER 0.109 (small 0.096) + 3.4x yavas (3743 ms vs 1091 ms) ->
    reddedildi. İlk kullanımda model dosyaları site-packages/rapidocr/models
    altına iner (~31 MB)."""
    if _PP6["state"] != "untried":
        return _PP6["engine"]
    _PP6["state"] = "failed"
    try:
        from rapidocr import RapidOCR, OCRVersion, ModelType
        params = {
            "Det.ocr_version": OCRVersion.PPOCRV6, "Det.lang_type": "ch",
            "Det.model_type": ModelType.SMALL,
            "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.lang_type": "ch",
            "Rec.model_type": ModelType.SMALL,
            "Cls.use_cls": False}
        if _ORT["cuda"]:
            params["EngineConfig.onnxruntime.use_cuda"] = True
        engine = RapidOCR(params=params)
        canvas = np.zeros((64, 320, 3), np.uint8)
        cv2.putText(canvas, "MERHABA 123", (8, 46), cv2.FONT_HERSHEY_SIMPLEX,
                    1.1, (255, 255, 255), 2, cv2.LINE_AA)
        out = engine(canvas)
        txts = getattr(out, "txts", None)
        if not txts:
            raise RuntimeError("sentetik test bos sonuc")
        _PP6.update(state="ok", engine=engine)
        return engine
    except Exception as e:
        _PP6["engine"] = None
        note(f"[i] PP-OCRv6 kurulamadi ({type(e).__name__}: {e})")
        return None


def _ppocr_read(engine, img):
    """PP-OCRv6/v5 çıktısını EasyOCR satır formatına çevirip grupla.

    rapidocr 3.x RapidOCROutput döndürür: boxes (N,4,2) numpy, txts/scores
    tuple. _group_lines (bbox, text, conf) ister; bbox 4-nokta poligona
    dönüştürülür (aynı satır gruplama kuralı: 28px). v6 ve v5 motorlarının
    çıktı biçimi aynıdır — ortak okuma fonksiyonu."""
    try:
        out = engine(img)
    except Exception:
        return "", 0.0
    txts = getattr(out, "txts", None)
    if out is None or txts is None or len(txts) == 0:
        return "", 0.0
    boxes = getattr(out, "boxes", None)
    if boxes is None or len(boxes) == 0:
        boxes = [None] * len(txts)
    res = []
    for box, txt, conf in zip(boxes, txts, out.scores):
        bbox = [[float(p[0]), float(p[1])] for p in box] \
            if box is not None else [[0.0, 0.0]]
        res.append((bbox, txt, float(conf)))
    return _group_lines(res)


def get_second_engine(lang_list, gpu_flag, ocr_mode="auto",
                      conf_thr=LOW_CONF_THR):
    """Düşük güvenli segmentler için ikinci görüş motorunu tembel kur.

    --ocr bayrağı zinciri seçer (varsayılan auto):
      auto    : PP-OCRv6 (rapidocr >=3.9; sentetik testle doğrulanır)
                → PP-OCRv5 → RapidOCR (sentetik testle) → EasyOCR ['en'] → yok
      paddle  : yalnız PP-OCRv5; kurulamazsa ikinci motor yok
      easyocr : PP motorları yok sayılır; RapidOCR → EasyOCR ['en'] (eski zincir)
    Ölçüm (02.10.2026, _v6-results.json): PP-OCRv6 small, PP-OCRv5'in tüm
    metriklerinde üstünde olduğu için auto zincirde pp5'in yerine geçti;
    pp5 --ocr paddle ile erişilebilir kalır.
    Döner: {"state": "ok"|"failed", "name": str|None, "fn": callable|None}
    """
    if _SECOND["state"] != "untried":
        return _SECOND
    if ocr_mode == "auto":
        engine6 = _ppocrv6_init()
        if engine6 is not None:
            _SECOND.update(state="ok", name="pp-ocrv6",
                           fn=lambda img: _ppocr_read(engine6, img))
            note(f"[i] ikinci motor: PP-OCRv6 (rapidocr; conf<{conf_thr} "
                 f"segmentlere 4. oy)")
            return _SECOND
        note("[i] PP-OCRv6 yok — PP-OCRv5'e dusuluyor")
    if ocr_mode in ("auto", "paddle"):
        engine5 = _ppocrv5_init()
        if engine5 is not None:
            _SECOND.update(state="ok", name="pp-ocrv5",
                           fn=lambda img: _ppocr_read(engine5, img))
            note(f"[i] ikinci motor: PP-OCRv5 (rapidocr; conf<{conf_thr} "
                 f"segmentlere 4. oy)")
            return _SECOND
        if ocr_mode == "paddle":
            _SECOND["state"] = "failed"
            note("[i] ikinci motor eklenmedi: --ocr paddle ve PP-OCRv5 "
                 "kurulamadi")
            return _SECOND
        note("[i] PP-OCRv5 yok — eski zincire (RapidOCR) dusuluyor")
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        if _rapidocr_selftest(engine):
            _SECOND.update(state="ok", name="rapidocr",
                           fn=lambda img: _rapidocr_read(engine, img))
            note(f"[i] ikinci motor: RapidOCR (conf<{conf_thr} "
                 f"segmentlere 4. oy)")
            return _SECOND
        note("[i] RapidOCR sentetik testini gecemedi (bos sonuc) — "
             "EasyOCR ['en'] deneniyor")
    except Exception as e:
        note(f"[i] RapidOCR kurulamadi ({type(e).__name__}: {e}) — "
             f"EasyOCR ['en'] deneniyor")
    if lang_list == ["en"]:
        _SECOND["state"] = "failed"
        note("[i] ikinci motor eklenmedi: zaten ['en'] okunuyor")
        return _SECOND
    try:
        import easyocr
        engine2 = easyocr.Reader(["en"], gpu=gpu_flag, verbose=False)
        _SECOND.update(state="ok", name="easyocr-en",
                       fn=lambda img: ocr_lines(engine2, img))
        note(f"[i] ikinci motor: EasyOCR ['en'] (conf<{conf_thr} "
             f"segmentlere 4. oy)")
        return _SECOND
    except Exception as e:
        _SECOND["state"] = "failed"
        note(f"[i] ikinci motor kurulamadi: {type(e).__name__}: {e}")
        return _SECOND


def fmt_ts(seconds):
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    global HWACCEL
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--band-y", type=int, default=None,
                    help="bant ust kenari; verilirse otomatik algilamayi "
                         "override eder")
    ap.add_argument("--band-h", type=int, default=None,
                    help="bant yuksekligi; verilirse override eder")
    ap.add_argument("--white-thr", type=int, default=None,
                    help="beyaz metin esigi (thr modu); verilirse override eder")
    ap.add_argument("--mask", choices=["thr", "tophat"], default=None,
                    help="segmantasyon maskesi: thr (saf beyaz) / tophat "
                         "(renkli-acik metin, parlak arka plan); verilirse "
                         "override eder")
    ap.add_argument("--tophat-thr", type=int, default=None,
                    help="tophat esigi (tophat modu; varsayilan 120; "
                         "verilirse oto-esik denemesi devre disi kalir)")
    ap.add_argument("--no-binarize", action="store_true",
                    help="OCR girdisini ham bandi kullan (binarize etme); "
                         "otomatik tophat modunda zaten uygulanir")
    ap.add_argument("--no-auto", action="store_true",
                    help=f"otomatik stil algilamayi kapat (eski sabitler: "
                         f"y={DEFAULT_BAND_Y} h={DEFAULT_BAND_H} "
                         f"{DEFAULT_MASK} esik={DEFAULT_WHITE_THR})")
    ap.add_argument("--diff-thr", type=float, default=0.35)
    ap.add_argument("--min-frames", type=int, default=3)
    ap.add_argument("--limit-seconds", type=float, default=0)
    ap.add_argument("--conf-thr", type=float, default=LOW_CONF_THR,
                    help="dusuk guven esigi: bu conf'un altindaki bloklara "
                         "ikinci motor oy verir ve low_conf sayilir "
                         "(varsayilan 0.75; eski davranis icin 0.6)")
    ap.add_argument("--ayir-gurultu", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="cop profilli bloklari ana SRT'den <ad>_ekran.srt "
                         "dosyasina TASIR (silmez; varsayilan acik; "
                         "--no-ayir-gurultu = eski davranis)")
    ap.add_argument("--jenerik", type=_jenerik_aralik_coz, default=[],
                    help="kayan jenerik/kredi zaman araliklari (KULLANICI "
                         "BEYANI, tahmin degil): 'DD:SS-DD:SS' veya "
                         "'SS:DD:SS-SS:DD:SS', virgulle coklu. Aralikta "
                         "BASLAYAN bloklar ana SRT'ye yazilmaz, "
                         "<ad>_ekran.srt'ye TASINIR (silinmez). Ornek: "
                         "--jenerik \"22:05-22:25\"")
    ap.add_argument("--duzelt", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="Turkce post-fix katmani: OCR metnini ana SRT'ye "
                         "yazilmadan sozlukle onarir (diakritik, harf-yutma, "
                         "harf-degistirme, kaynasma ayirma, noktalama "
                         "artiklari, apostrof tablosu). kullanici-sozlugu.txt "
                         "(UI Ogret) "
                         "kurallari da ayni katmanda uygulanir. Her "
                         "degisiklik <ad>.duzeltme-gunlugu."
                         "json'a yazilir (varsayilan acik; --no-duzelt = "
                         "ham OCR metni)")
    ap.add_argument("--lang", default="tr,en",
                    help="EasyOCR dil kodlari, virgulle birlestirilir "
                         "(orn: tr,en / ja / ko,zh_sim)")
    ap.add_argument("--ocr", choices=["auto", "easyocr", "paddle"],
                    default="auto",
                    help="dusuk guvenli segmentlerin ikinci gorUs motoru: "
                         "auto = PP-OCRv6 -> PP-OCRv5 -> RapidOCR -> "
                         "EasyOCR[en] (varsayilan), easyocr = eski zincir "
                         "(RapidOCR -> EasyOCR[en], PP motorlari yok "
                         "sayilir), paddle = yalniz PP-OCRv5")
    ap.add_argument("--cpu", action="store_true",
                    help="OCR'i CPU'da zorla calistir (varsayilan: CUDA "
                         "varsa GPU)")
    ap.add_argument("--batch", type=int, default=16,
                    help="GPU OCR batch buyuklugu: segmentler bu gruplarla "
                         "toplanir, tanima tek cagrida yapilir (1 = eski "
                         "tekli davranis)")
    ap.add_argument("--nvdec", action="store_true",
                    help="ffmpeg donanim decode'unu (NVDEC) ac (varsayilan: "
                         "kapali — olcumde bu makinede CPU decode daha hizli); "
                         "acikken basarisizlikta CPU'ya dusar")
    ap.add_argument("--qa", type=int, default=None,
                    help="eşit aralıklı N segment için QA montajı üret "
                         "(varsayılan: tam koşuda 6, test koşusunda kapalı; "
                         "0 = kapalı)")
    ap.add_argument("--qa-dir", default="qa")
    ap.add_argument("--ust-bant", action="store_true",
                    help="diyalog taramasindan SONRA ikinci gecis: cercevenin "
                         "ust %%35'indeki yazilari (mesaj kutusu, tabela, "
                         "gun yeri, konum karti) AYRI dosyalara yazar — "
                         "<ad>_ust.srt (temiz) + <ad>_ust.json (index / "
                         "start / end / bbox_y_ratio / text / conf). Statik "
                         "logo/watermark pikselleri maske'den elenir. Ana "
                         "diyalog SRT'si DEGISMEZ (varsayilan KAPALI)")
    ap.add_argument("--ust-ana", action="store_true",
                    help="ONAYLI IYI: ust bant ikinci gecisini calistirir "
                         "VE ust bandin bloklarini ANA SRT'ye ekler. Bu bayrak "
                         "bir TAHMIN degil, KULLANICI BEYANIDIR: 'bu yapimda "
                         "diyalog ustte de ciziliyor'. Siniflandirma YAPILMAZ "
                         "(olcum: konum, sure, guven, kelime, buyuk-harf ve "
                         "DIL sinyallerinin HICBIRI gercek-ust ile ust-"
                         "diyalogu ayirt edemiyor — bkz. _ust_pass notu). "
                         "Ust bandaki tabela/mesaj da ana SRT'ye gider; "
                         "hicbir blok SILINMEZ. Varsayilan KAPALI")
    ap.add_argument("--ust-conf-thr", type=float, default=UST_CONF_THR,
                    help="ust bant guven kapisi: bu conf'un ALTINDAKI bloklar "
                         "<ad>_ust.srt'ye YAZILMAZ (varsayilan 0.30; olcum: "
                         "BLEND-S 240 sn'de gercek bloklar 0.76-1.00, cop "
                         "bloklar 0.010-0.023 -> 12 bloktan 8'i kalir, olculen "
                         "hicbir gercek blok dusmez). Dusen bloklar stats."
                         "ust_bant icinde sayi + guven + metin olarak "
                         "raporlanir")
    ap.add_argument("--no-ocr", action="store_true",
                    help="sadece tarama istatistiği")
    args = ap.parse_args()
    if args.qa is None:
        args.qa = 0 if args.limit_seconds > 0 else 6
    args.batch = max(1, args.batch)
    sozluk = _sozluk_yukle()   # post-fix + ekran siniflandiricisi icin
    kullanici = _kullanici_sozluk_yukle() if args.duzelt else []
    # --tophat-thr varsayilani 120; None ile "verilmedi" ayirt edilir ki
    # oto-esik denemesi elle verilmis bir esigi degistirmesin (white_thr ile
    # ayni mantik). Es deger 120 -> davranis degismez.
    tophat_thr_el = args.tophat_thr is not None
    if not tophat_thr_el:
        args.tophat_thr = DEFAULT_TOPHAT_THR
    if args.nvdec:
        HWACCEL = ["-hwaccel", "cuda"]
        note("[i] NVDEC: acik (-hwaccel cuda) — basarisizsa CPU decode'a dusar")
    else:
        note("[i] NVDEC: kapali (varsayilan — bu makinede olcumde yavas; "
             "--nvdec ile acilir)")

    video = Path(args.video)
    out = Path(args.out)
    info = run_ffprobe(video)
    fps = info["fps"]
    print(f"[i] {video.name}: {info['width']}x{info['height']}, "
          f"fps={fps:.5f}, sure={info['duration']:.1f}s, "
          f"altyazi_stream={info['subtitles'] or 'yok'}")

    # --- Mod 1: softsub ---
    if info["subtitles"]:
        extract_softsub(video, out, 0)
        print(f"[OK] softsub bulundu ({info['subtitles'][0]['codec']}), "
              f"kaynaktan birebir cikarildi: {out}")
        return

    # --- Stil: otomatik algılama (varsayılan) / elle override ---
    manual = {"band_y": args.band_y, "band_h": args.band_h,
              "white_thr": args.white_thr, "mask": args.mask}
    overrides = [k for k, v in manual.items() if v is not None]
    cfg = {"band_y": DEFAULT_BAND_Y, "band_h": DEFAULT_BAND_H,
           "white_thr": DEFAULT_WHITE_THR, "mask": DEFAULT_MASK,
           "tophat_thr": args.tophat_thr}
    det = None
    band_source = "varsayilan"
    if args.no_auto:
        cfg.update({k: v for k, v in manual.items() if v is not None})
        if overrides:
            note(f"[auto] kapali (--no-auto): elle verilenler kullanildi "
                 f"({', '.join(overrides)}), kalanlar eski sabitler")
        band_source = "elle" if overrides else "varsayilan"
    elif len(overrides) == len(manual):
        cfg.update(manual)                 # hepsi elle: algılamaya gerek yok
        band_source = "elle"
    else:
        t_d = time.time()
        det = detect_style(video, info["width"], info["height"],
                           info["duration"])
        if det is not None:
            band_source = "auto"
            # 05.10.2026: dar bant sağlık kontrolü — iki satırlı diyaloğun
            # üst satırını kesecek kadar dar algılanan bant taban sabit
            # tutularak yukarı genişletilir (_dar_bant_genislet; Wano
            # E1071/E1007/E1074-75 imzaları).
            genis = _dar_bant_genislet(det, info["height"])
            if genis is not None:
                note(f"[auto] DAR BANT: y={det['band_y']}, h={det['band_h']}"
                     f" -> y={genis['band_y']}, h={genis['band_h']} (taban "
                     f"sabit; iki satirli diyalogun ust satiri kesilmesin)")
                det = genis
                band_source = "auto-genisletildi"
            cfg.update({k: det[k] for k in
                        ("band_y", "band_h", "white_thr", "mask")})
            cfg.update({k: v for k, v in manual.items() if v is not None})
            ov = f" | override: {','.join(overrides)}" if overrides else ""
            note(f"[auto] bant: y={cfg['band_y']}, h={cfg['band_h']} | "
                 f"mod: {cfg['mask']} | esik: {cfg['white_thr']} | "
                 f"ornek: {det['samples']} kare ({time.time()-t_d:.0f}s)"
                 f"{ov}")
            if det["text_h"] < UPSCALE_TEXT_H:
                note(f"[auto] metin yuksekligi ~{det['text_h']}px -> "
                     f"OCR girisi 2x buyutulacak")
            else:
                note(f"[auto] metin yuksekligi ~{det['text_h']}px -> "
                     f"OCR girisi 1x")
        else:
            # Düzeltme 2b: tek varsayılana düşmeden önce alt bölgede aday
            # bant taraması (bant konumu elle verilmediyse)
            fb = None
            if not any(k in overrides for k in ("band_y", "band_h")):
                fb = _candidate_band(video, info["width"], info["height"],
                                     info["duration"])
            if fb is not None:
                det = fb
                band_source = "aday-tarama"
                cfg.update({k: det[k] for k in
                            ("band_y", "band_h", "white_thr", "mask")})
                cfg.update({k: v for k, v in manual.items() if v is not None})
                note(f"[auto] aday bant secildi: y={cfg['band_y']}, "
                     f"h={cfg['band_h']} | mod: {cfg['mask']} | "
                     f"esik: {cfg['white_thr']}")
            else:
                cfg.update({k: v for k, v in manual.items() if v is not None})
                ov = f" | override: {','.join(overrides)}" if overrides else ""
                note(f"[auto] bant algilanamadi — varsayilanlara donuldu "
                     f"(y={DEFAULT_BAND_Y}, h={DEFAULT_BAND_H}, {DEFAULT_MASK}, "
                     f"esik={DEFAULT_WHITE_THR}){ov}")
    upscale2x = det is not None and det["text_h"] < UPSCALE_TEXT_H
    # otomatik seçilen tophat modunda OCR ham bandı alır (lavanta/renkli metin
    # beyaz eşiğinde silinir); elle verilen tophat'ta eski davranış korunur
    mask_from_auto = det is not None and "mask" not in overrides
    use_raw = args.no_binarize or (mask_from_auto and cfg["mask"] == "tophat")

    # --- Mod 2: hardsub OCR ---
    if args.no_ocr:
        # sadece tarama: OCR yok, SRT yazılmaz (eski davranış)
        print("[1/3] altyazi bandi taraniyor...")
        t0 = time.time()
        segments, scanned, _fd = scan_band(video, cfg["band_y"], cfg["band_h"],
                                           info["width"], cfg["white_thr"],
                                           args.diff_thr, args.limit_seconds,
                                           mask_mode=cfg["mask"],
                                           tophat_thr=args.tophat_thr)
        segments = [(a, b) for a, b in segments
                    if b - a + 1 >= args.min_frames]
        cov = sum(b - a + 1 for a, b in segments) / fps
        print(f"    {scanned} frame tarandi ({time.time()-t0:.0f}s), "
              f"{len(segments)} segment")
        print(f"[i] konusma suresi ~{cov:.0f}s — OCR atlandi (--no-ocr)")
        return

    lang_list = [s.strip().lower() for s in args.lang.split(",") if s.strip()]
    if not lang_list:
        lang_list = ["tr", "en"]
    if args.cpu:
        gpu_flag, device = False, "CPU (--cpu ile zorlandi)"
    else:
        dev_name = ""
        try:
            import torch
            gpu_flag = bool(torch.cuda.is_available())
            if gpu_flag:
                dev_name = torch.cuda.get_device_name(0)
        except Exception:
            gpu_flag = False
        device = f"GPU ({dev_name})" if gpu_flag else "CPU"
    # İkinci motor (rapidocr) kendi CUDA yolunu AYRI ölçer: EasyOCR'nin
    # GPU'da olması, onnxruntime'ın GPU'da olacağını GARANTİ ETMEZ.
    _ort_cuda_probe(gpu_flag)
    note(f"[i] OCR cihazi: {'GPU' if gpu_flag else 'CPU'}"
         f" | dil: {','.join(lang_list)}")
    note(f"[i] ikinci motor onnxruntime {_ORT['surum']} → CUDA {_ORT['durum']}")
    print(f"[2/3] OCR (EasyOCR {','.join(lang_list)}, {device}, "
          f"3-varyant konsensus, batch={args.batch})...")
    import easyocr
    reader = easyocr.Reader(lang_list, gpu=gpu_flag, verbose=False)

    def frame_time(f):
        return f / fps

    # Düzeltme 1c: oto-eşik iyileştirmesi yalnız otomatik algılanan thr
    # modunda ve eşik elle verilmemişken çalışır
    # F3: oto-eşik kendini iyileştirme artık tophat modunda da çalışır.
    # Denenen eşik modun kendi eşiğidir (thr -> white_thr, tophat ->
    # tophat_thr); tophat'ta white_thr'in hiçbir yerde okunmadığı ölçülmüştür
    # (scan_band: tophat'ta `mask = frame > tophat_thr`, ve use_raw=True
    # olduğu için OCR girdisi de binarize edilmez) — yani eski kapı
    # kaldırılsa bile white_thr'i düşürmek bit-bit aynı sonucu üretirdi.
    # Elle verilmiş eşikler (white_thr / tophat_thr) denemeyi yine kapatır.
    auto_thr = (det is not None and not args.no_auto
                and (cfg["mask"] != "tophat" or not tophat_thr_el)
                and "white_thr" not in overrides)

    word_re = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3,}")
    vowel_re = re.compile(r"[AaEeIiİoOöÖuUüÜ]")
    SIM = 0.70

    def _read_chunk(imgs):
        """Hazır OCR girdilerini oku: 3 varyant konsensus + düşük-conf 2. görüş.

        Diyalog bandı ve üst bant (--ust-bant) bu TEK fonksiyonu kullanır —
        "aynı zincir" şartı burada garanti edilir, iki yerde kopya değil.
        İkinci motor oyu (4. aday) havuza girer; 8. turdan beri ayrıca ÖLÇÜLÜ
        TAKAS vardır: konsensus fiilen çökmüşken (conf<TAKAS_KONSENS_CONF) ve
        ikinci motor net üstünken metin ikinci motordan alınır (_takas_uygun;
        sabitler ve dört ölçüm vakası TAKAS_* bloğunda). Konsensus metni ile
        takas metni AYNIysa takas sayılmaz (çift sayım yok).
        DÜZELTME 2 (04.10.2026): takas adayı CJK-çoğunlukluysa ve İLK
        konsensus conf'u TAKAS_CJK_ILK_CONF'un altındaysa takas YAPILMAZ;
        segment `takas_cjk` listesine işaretlenir (çağıran bloğu
        <ad>_ekran.srt'ye yönlendirir — takas-cjk rotası; ölçüm: BLEND-S
        00:03:58 Japonca kredisi takas sonrası word_re süzgeciyle İKİ
        dosyadan da düşüyordu).
        Döner: ([(text, conf), ...], oy_sayisi, takas, takas_cjk) — sıra
        girdiyle aynı; `takas` ve `takas_cjk` {"i", "eski", "yeni",
        "kons_conf", "ikinci_conf"} kayıtlarıdır (i = chunk içi segment
        indeksi; çağıran zamana çevirir). Sessiz değişiklik yok: çağıran bu
        kayıtları <ad>.takas-gunlugu.json'a yazar."""
        # varyant başına TEK batch'li tanıma (3 büyük GPU çağrısı)
        var_out = [ocr_lines_batch(reader, imgs, args.batch, **kw)
                   for kw in OCR_VARIANTS]
        out, votes, takas, takas_cjk = [], 0, [], []
        for j in range(len(imgs)):
            text, conf = _consensus_pick(var_out[v][j]
                                         for v in range(len(OCR_VARIANTS)))
            ilk_conf = conf        # düzeltme 2: İLK konsensus conf'u — ikinci
                                   # motor havuza girmeden ÖNCEKİ değer
            if text and conf < args.conf_thr:      # ikinci motora danış
                sec = get_second_engine(lang_list, gpu_flag, args.ocr,
                                        conf_thr=args.conf_thr)
                if sec["fn"] is not None:
                    try:
                        t2, c2 = sec["fn"](imgs[j])
                    except Exception:
                        t2 = ""
                    if t2:                        # 4. oy havuza girer
                        votes += 1
                        text, conf = _consensus_pick(
                            [var_out[v][j] for v in range(len(OCR_VARIANTS))]
                            + [(t2, c2)])
                        if _takas_uygun(conf, c2, text, t2) and \
                                t2.strip() != text.strip():
                            if ilk_conf < TAKAS_CJK_ILK_CONF and \
                                    _cjk_cogunluk(t2):
                                # düzeltme 2: CJK metin diyaloğa GİRMEZ;
                                # blok _ekran rotasına işaretlenir (Kural 1:
                                # kaybolmaz, diyalog akışı da kirletilmez)
                                takas_cjk.append(
                                    {"i": j, "eski": text, "yeni": t2,
                                     "kons_conf": round(conf, 4),
                                     "ikinci_conf": round(c2, 4)})
                            else:
                                takas.append({"i": j, "eski": text,
                                              "yeni": t2,
                                              "kons_conf": round(conf, 4),
                                              "ikinci_conf": round(c2, 4)})
                                text, conf = t2, c2
            out.append((text, conf))
        return out, votes, takas, takas_cjk

    def _band_inputs(segments, band_y, band_h, raw, up2, white_thr):
        """Segmentlerin orta karelerini OCR girdisine çevir (Aşama A).

        Ortak yol: grab -> (binarize | ham) -> isteğe bağlı 2x büyütme.
        Diyalog ve üst bant aynı hazırlığı kullanır; sadece band_y/band_h,
        mod kaynaklı raw/up2 ve eşik farklıdır."""
        imgs = []
        for a, b in segments:
            mid = (a + b) // 2
            img = grab_band(video, frame_time(mid), band_y, band_h)
            img_in = img if raw else binarize_white(img, white_thr)
            if up2:
                img_in = cv2.resize(img_in, None, fx=2, fy=2,
                                    interpolation=cv2.INTER_LANCZOS4)
            imgs.append(img_in)
        return imgs

    def _extract(cfgx, limit_s=None):
        """Tek deneme: tarama + OCR + birleştirme zinciri.

        `limit_s` verilirse tarama YALNIZ o kadar saniye tarar (eşik
        denemesinin UCUZ ÖNİNDEĞERi için kullanılır — bkz. Task 4).
        Verilmezse `args.limit_seconds` (yani normal tam gidiş) kullanılır.

        Döner: (merged, mikro_tasınanlar, scanned, second_votes,
        second_takas, fade_n, cjk_blok, cjk_kayit)."""
        print("[1/3] altyazi bandi taraniyor...")
        t0 = time.time()
        segments, scanned, fade_n = scan_band(
            video, cfgx["band_y"], cfgx["band_h"], info["width"],
            cfgx["white_thr"], args.diff_thr,
            args.limit_seconds if limit_s is None else limit_s,
            mask_mode=cfgx["mask"], tophat_thr=cfgx["tophat_thr"])
        segments = [(a, b) for a, b in segments
                    if b - a + 1 >= args.min_frames]
        print(f"    {scanned} frame tarandi ({time.time()-t0:.0f}s), "
              f"{len(segments)} segment")
        results = []                   # [start_f, end_f, text, conf]
        cjk_blok = []                  # düzeltme 2: _ekran'a yönlendirilen
                                       # takas-cjk blokları (ana akışa girmez)
        cjk_kayit = []                 # takas-gunlugu 'takas_cjk' kayıtları
        t0 = time.time()
        total_seg = len(segments)
        second_votes = 0
        second_takas = 0
        takas_kayit = []               # <ad>.takas-gunlugu.json (sessiz değil)
        for cs in range(0, total_seg, args.batch):
            chunk = segments[cs:cs + args.batch]
            imgs = _band_inputs(chunk, cfgx["band_y"], cfgx["band_h"],
                                use_raw, upscale2x, cfgx["white_thr"])
            pairs, v, tk, tc = _read_chunk(imgs)
            second_votes += v
            tk_harita = {e["i"]: e for e in tk}
            tc_harita = {e["i"]: e for e in tc}
            for j, (a, b) in enumerate(chunk):
                tce = tc_harita.get(j)
                if tce is not None:
                    # düzeltme 2: takas-cjk bloğu ana akışa GİRMEZ — Türkçe
                    # süzgeci (word_re/vowel_re) CJK metni düşürür ve blok
                    # iki dosyadan da kaybolur (ölçülmüş kayıp vakası).
                    # İçerik ikinci motorun CJK okumasıyla _ekran havuzuna
                    # konur; karar takas-gunlugu'na yazılır (Kural 1).
                    cjk_blok.append([a, b, tce["yeni"], tce["ikinci_conf"]])
                    cjk_kayit.append({
                        "zaman": fmt_ts(frame_time(a)),
                        "eski": tce["eski"], "yeni": tce["yeni"],
                        "kons_conf": tce["kons_conf"],
                        "ikinci_conf": tce["ikinci_conf"],
                        "karar": "takas red (ilk conf<%.2f + CJK çoğunluk)"
                                 " -> _ekran" % TAKAS_CJK_ILK_CONF})
                    continue
                results.append([a, b, pairs[j][0], pairs[j][1]])
                e = tk_harita.get(j)
                if e:
                    takas_kayit.append({
                        "zaman": fmt_ts(frame_time(a)),
                        "eski": e["eski"], "yeni": e["yeni"],
                        "kons_conf": e["kons_conf"],
                        "ikinci_conf": e["ikinci_conf"]})
            done = cs + len(chunk)
            if done < total_seg:
                remain = (time.time() - t0) / done * (total_seg - done)
                print(f"    %{done * 100 // total_seg} ({done}/{total_seg} seg) "
                      f"~kalan {remain:.0f} sn")

        results = [r for r in results if r[2].strip()]
        # çöp deseni filtresi: en az 3 harf ardışık + en az bir sesli harf
        results = [r for r in results
                   if word_re.search(r[2]) and vowel_re.search(r[2])]
        # ardışık aynı metin / benzer metin / çakışan okuma birleştirmesi
        # ortak fonksiyona taşındı (--ust-bant aynı zinciri kullanır); kod
        # birebir aynıdır, sıra ve yan etkiler değişmedi.
        merged = _merge_readings(results, fps, SIM)
        merged_raw = [list(r) for r in merged]   # mikro temizlik ÖNCESI ham çıktı
        merged, micro_moved = _micro_cleanup(merged, fps)
        print(f"    {len(merged)} altyazi bloku ({time.time()-t0:.0f}s)")
        if _SECOND["state"] == "ok" and _SECOND["name"]:
            ek = f" | {len(takas_kayit)} blokta takas (conf<" \
                 f"{TAKAS_KONSENS_CONF} & 2. motor >={TAKAS_IKINCI_CONF})" \
                if takas_kayit else ""
            print(f"    ikinci motor: {_SECOND['name']} — {second_votes} "
                  f"blokta oy verdi{ek}")
        return (merged, merged_raw, micro_moved, scanned, second_votes,
                len(takas_kayit), takas_kayit, fade_n, cjk_blok, cjk_kayit)

    (merged, merged_raw, micro_moved, scanned, second_votes, second_takas,
     takas_kayit, fade_n, cjk_blok, cjk_kayit) = _extract(cfg)

    def _ust_pass():
        """--ust-bant: üst %35 bölgesinin AYRI geçişi.

        Ana diyalog taraması BİTMİŞTİR — bu fonksiyon yalnız kendi bandını
        (y=0, h≈%35×H) kendi stiliyle (detect_style_upper: BAĞIMSIZ
        thr/tophat seçimi) tarar, statik logo piksellerini eler, diyalogla
        AYNI konsensus + ikinci görüş OCR zincirini kullanır ve sonucu
        <ad>_ust.srt + <ad>_ust.json'a yazar. Ana SRT'ye hiçbir şey
        EKLEMEZ (dosyayı hiç açmaz bile) — TEK İSTİSNADIR: `--ust-ana`
        verilirse (bkz. ana akıştaki Task 2 bloğu) bloklar ana SRT'ye
        eklenir.

        ⛔ SINIFLANDIRMA YAPILMAZ VE ÖLÇÜLMEDEN BİR KURAL UYGULANMAZ.
        03.10.2026 ölçümü (`_vc-ust-dil-olcumu.py`, BLEND-S 240 sn üst
        bandı): gerçek-üst ile üstte-çizilen diyalog arasında HİÇBİR
        sinyal ayırıcı değil —

            sinyal        gerçek-üst aralığı      üst-diyalog aralığı
            conf          0.9561 .. 1.0000        0.7595 .. 0.9853   OVERLAP
            süre           0.54  .. 3.00 sn        0.75  .. 4.75 sn   OVERLAP
            konum (y)      0.0602 .. 0.2069        0.0648 .. 0.0685   OVERLAP
            kelime         1 .. 4                  1 .. 7             OVERLAP
            büyük-harf %   0.143 .. 1.000          0.043 .. 0.200     OVERLAP
            Türkçe diakritik 0 .. 3               0 .. 4             OVERLAP

        DİL sinyali (`word_re` + `vowel_re`) de AYIRT EDİCİ DEĞİL: gerçek
        üst metinlerinin 3/3'ü ("İş Başvurusu Sonuç Bildirisi", "TREN
        GECİKECEK", "Service") de Türkçe sinyali veriyor. Yani "üst metni
        Türkçe, diyalog değil" gibi bir sezgi bu veride yanlıştır.

        Sonuç: otomatik sınıflandırıcı YOK. Uydurma bir kural, gerçek üst
        metni altyazı dosyasına sokma riskini taşır; bu, mevcut sızıntıdan
        daha kötüdür. Bu yüzden `--ust-ana` bir TAHMİN değil, kullanıcının
        "bu yapımda diyalog üstte de çiziliyor" BEYANIDIR.

        Döner: stats.json'a yazılacak ust_bant sözlüğü.
        """
        t_all = time.time()
        ust_srt = out.with_name(out.stem + "_ust.srt")
        ust_json = out.with_name(out.stem + "_ust.json")
        # Bayat çıktı taklit edilmesin: dosyalar GEÇİŞTEN ÖNCE silinir,
        # sonra koşulsuz yeniden yazılır (0 blok = boş dosya). Böylece
        # süreç üst banda çökse bile eski koşunun dosyası bu koşunun
        # çıktısı gibi görünemez.
        for stale in (ust_srt, ust_json):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                note(f"[ust] eski dosya silinemedi: {stale.name} ({e})")

        y_crop = max(24, int(round(info["height"] * UST_REGION_FRAC)))
        ucfg = detect_style_upper(video, info["width"], info["height"],
                                 info["duration"])
        auto_ust = ucfg is not None
        if ucfg is None:
            ucfg = {"band_y": 0, "band_h": y_crop, "mask": "tophat",
                    "white_thr": DEFAULT_WHITE_THR,
                    "text_h": max(24, int(y_crop * 0.18)),
                    "samples": 0, "core": 0.0}
        utophat = args.tophat_thr if tophat_thr_el else DEFAULT_TOPHAT_THR
        u_esik = ucfg["white_thr"] if ucfg["mask"] == "thr" else utophat

        # --- DEĞİŞİK C: üst bölge, ALGILANAN diyalog bandına kıstırılır ---
        # "üst bant = diyalog değil" önermesi her sürümde doğru değil: bazi
        # yayınlarda diyalog üstte durur. Ana geçiş bu konuşma bandını zaten
        # buldu (cfg["band_y"]), üst geçiş ondan ÖNCE değil, SONRA çalışır;
        # yani elde hazırdır. Üst bölgenin ALT sınırı diyalog bandının ÜST
        # kenarına indirilir, böylece tarama diyaloğa hiç girmez.
        #
        # Üç kenar durumu:
        #   a) diyalog bandı üst bölgenin ALTINDA/ÇIKTI (dia_y >= ham h)
        #      -> KISITLAMA YOK (strict no-op; bölge aynen kalır).
        #   b) diyalog bandı üst bölgenin İÇİNDE (0 < dia_y < ham h)
        #      -> alt sınır diyalog bandının üstüne GAP kadar çekilir.
        #   c) diyalog bandı en tepede/üstte (dia_y <= 0 ya da kalan h çok
        #      küçük) -> TERS/BOŞ BÖLGE oluşmaz; tarama hiç yapılmaz, iki
        #      dosya da BOŞ yazılır (bayat içerik taklit edilemez).
        # text_h de kırpılan yükseklikle sınırlanır: banttan uzun metin
        # ölçümü kalan bölgeyi tanımlayamaz.
        ust_h_ham = ucfg["band_h"]
        dia_y = cfg["band_y"]
        ust_h = ust_h_ham
        if dia_y > 0:
            ust_h = min(ust_h, dia_y - UST_DIALOG_GAP)
        ust_h = max(0, int(ust_h))
        ucfg["band_h"] = ust_h
        if ust_h < ust_h_ham:
            ucfg["text_h"] = max(8, min(ucfg["text_h"], ust_h))
        u_kisit = ust_h < ust_h_ham
        if u_kisit:
            note(f"[ust] DIALOG KISITLAMASI: bolge {ust_h_ham} -> {ust_h} px "
                 f"(diyalog bandi y={dia_y} h={cfg['band_h']}, "
                 f"pay={UST_DIALOG_GAP})")
        else:
            note(f"[ust] diyalog kisitlamasi YOK: diyalog bandi y={dia_y} "
                 f"ust bolgenin disinda (ust {ust_h_ham} px)")
        note(f"[ust] bolge y=0..{ucfg['band_h']} (cercevenin ust "
             f"%{UST_REGION_FRAC * 100:.0f}%, H={info['height']}) | "
             f"mod: {ucfg['mask']} | esik: {u_esik} | metin ~{ucfg['text_h']}px"
             f" | ornek: {ucfg['samples']} kare"
             f"{'' if auto_ust else ' | ALGILANAMADI -> varsayilan'}")

        # --- c) kenar durumu: üstte yer kalmadi -> tarama YAPILMAZ ---
        if ucfg["band_h"] < UST_MIN_H:
            note(f"[ust] bolge {ucfg['band_h']}px < {UST_MIN_H}px -> ust "
                 f"tarama YAPILMADI (ters/sifir yukseklikte crop kirmaz)")
            ust_srt.write_text("", encoding="utf-8")
            ust_json.write_text(json.dumps(
                {"video": video.name, "bolge": [0, ucfg["band_h"]],
                 "oran": UST_REGION_FRAC, "mask": ucfg["mask"],
                 "esik": u_esik, "tarama_yapilmadi": "diyalog bandi ust "
                 "bolgeyi tamamen kapsiyor", "bloklar": []},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[ust 3/3] SRT yazildi: {ust_srt} (0 blok) | konum: "
                  f"{ust_json} | TARAMA YAPILMADI (diyalog ustte)")
            print("    ust bantta blok bulunamadi — SRT ve json BOS "
                  "yazildi (onceki kosunun icerigi tasindi)")
            return {"blok": 0, "blok_guven_oncesi": 0, "guven_elenen": 0,
                    "guven_elenen_conf": [],
                    "guven_esik": args.ust_conf_thr,
                    "konuma_kisitlandi": True,
                    "tarama_yapilmadi": "diyalog bandi ust bolgeyi kapsiyor",
                    "bolge": [0, ucfg["band_h"]],
                    "bolge_kisit_oncesi": [0, ust_h_ham],
                    "diyalog_bandi": [dia_y, cfg["band_h"]],
                    "mask": ucfg["mask"], "esik": u_esik,
                    "metin_yuksekligi": ucfg["text_h"], "stil_kaynak":
                    "oto" if auto_ust else "varsayilan",
                    "segment": 0, "surezgec": 0, "dusuk_guven": 0,
                    "conf_thr": args.conf_thr, "second_engine_votes": 0,
                    "diyalog_supheli": 0, "diyalog_ckismeli": 0,
                    "konum_kurali": {"tur": "teshis", "eleme": False,
                                     "y_lo": UST_SUS_Y_LO,
                                     "y_hi": UST_SUS_Y_HI},
                    "srt": ust_srt.name, "json": ust_json.name,
                    "sure_sn": round(time.time() - t_all, 1)}, []
        u2x = ucfg["text_h"] < UPSCALE_TEXT_H
        u_raw = ucfg["mask"] == "tophat"

        # --- 3) statik eleme (logo/watermark) ---
        t_s = time.time()
        drop, sfr = _static_drop_map(video, 0, ucfg["band_h"], info["width"],
                                     args.limit_seconds,
                                     mask_mode=ucfg["mask"],
                                     white_thr=ucfg["white_thr"],
                                     tophat_thr=utophat)
        n_band = int(drop.size) if drop is not None else 0
        n_drop = int(drop.sum()) if drop is not None else 0
        statik_oran = n_drop / n_band if n_band else 0.0
        if drop is None:
            note("[ust] statik eleme: harita alinamadi — eleme YAPILMADI")
        else:
            note(f"[ust] statik eleme: {sfr} kare tarandi "
                 f"({time.time()-t_s:.0f}s) | {n_drop}/{n_band} piksel sabit "
                 f"= %{statik_oran * 100:.2f} -> maske'den dusuruldu")

        # --- 1) tarama (statik harita maskeye uygulanarak) ---
        t0 = time.time()
        segments, u_scanned, _ufd = scan_band(
            video, 0, ucfg["band_h"], info["width"], ucfg["white_thr"],
            args.diff_thr, args.limit_seconds, mask_mode=ucfg["mask"],
            tophat_thr=utophat, static_drop=drop)
        segments = [(a, b) for a, b in segments
                    if b - a + 1 >= args.min_frames]
        print(f"[ust 1/3] ust bant tarandi: {u_scanned} frame "
              f"({time.time()-t0:.0f}s), {len(segments)} segment")

        # --- 2) OCR (diyalogla AYNI zincir) ---
        t0 = time.time()
        u_results = []
        u_second = 0
        total_seg = len(segments)
        for cs in range(0, total_seg, args.batch):
            chunk = segments[cs:cs + args.batch]
            imgs = _band_inputs(chunk, 0, ucfg["band_h"], u_raw, u2x,
                                ucfg["white_thr"])
            pairs, v, _tk, _tc = _read_chunk(imgs)
            u_second += v
            for j, (a, b) in enumerate(chunk):
                u_results.append([a, b, pairs[j][0], pairs[j][1]])
            done = cs + len(chunk)
            if done < total_seg:
                remain = (time.time() - t0) / done * (total_seg - done)
                print(f"    %{done * 100 // total_seg} "
                      f"({done}/{total_seg} seg) ~kalan {remain:.0f} sn")

        # --- süzgeç + birleştirme (diyalogla aynı) ---
        u_ok = [r for r in u_results if r[2].strip()]
        u_ok = [r for r in u_ok
                if word_re.search(r[2]) and vowel_re.search(r[2])]
        u_dropped = len(u_results) - len(u_ok)
        ust = _merge_readings(u_ok, fps, SIM)
        ust = [r for r in ust if r[2].strip()]

        # --- DEĞİŞİK B (onaylı): üst bant guven kapısı ---
        # Ölçüm (BLEND-S 240 sn): gerçek bloklar 0.76-1.00, çöp bloklar
        # 0.010-0.023. Kapı UST_CONF_THR=0.30 ile ikisini de TAMEN ayırır.
        # Bu kapı SİLME değil ELER: düşen blok sayısı, guvenleri ve metin
        # örnekleri hem günlüğe hem stats.ust_bant'a yazılır, yani düşüş
        # hiçbir koşulda görünmez kalmaz (Kural 1: hiçbir şey sessizce
        # kaybolmaz). Ana diyalog SRT'si bu kapıdan ETKİLENMEZ.
        u_once = len(ust)
        u_dusen = [r for r in ust if r[3] < args.ust_conf_thr]
        ust = [r for r in ust if r[3] >= args.ust_conf_thr]
        if u_once == 0:
            note(f"[ust] guven kapisi: kapidan ONCE 0 blok — elenecek blok yok")
        elif u_dusen:
            note(f"[ust] guven kapisi (esik={args.ust_conf_thr}): {u_once} -> "
                 f"{len(ust)} blok, {len(u_dusen)} ELENDI")
            for r in u_dusen:
                note(f"    [ust] elendi conf={r[3]:.4f} "
                     f"t={frame_time(r[0]):.2f}s {r[2][:70]!r}")
        else:
            note(f"[ust] guven kapisi (esik={args.ust_conf_thr}): {u_once} -> "
                 f"{len(ust)} blok, 0 elendi")

        # --- 3b) blok konumu (bbox) + DİYALOG ŞÜPHESİ TEŞHİSİ ---
        # Bu blok HİÇBİR ŞEYİ ELMEZ. bbox hesabı SRT/json yazımından ÖNCE
        # yapılır, çünkü teşhis kararı yazımdan önce VERİLMELİ (Kural:
        # "log yalan söylemez" — karar verilmeden sayı yazılmaz).
        u_bb = []
        for a, b, _t, _c in ust:
            mid = (a + b) // 2
            bb = None
            try:
                img = grab_band(video, frame_time(mid), 0, ucfg["band_h"])
                img_in = img if u_raw else \
                    binarize_white(img, ucfg["white_thr"])
                if u2x:
                    img_in = cv2.resize(img_in, None, fx=2, fy=2,
                                        interpolation=cv2.INTER_LANCZOS4)
                bb = _upper_bbox(reader, img_in, ucfg["band_h"], info["height"],
                                 2 if u2x else 1)
            except Exception:
                bb = None
            u_bb.append(bb)

        # Zaman örtüşmesi: ana geçişin diyalog bloğu bu üst bloğun zamanıyla
        # çakışıyor mu? (merged = ana diyalog listesi, aynı zamanda ana SRT'nin
        # kaynağı — ikinci tarama gerekmez.)
        u_ckis = []
        for a, b, _t, _c in ust:
            s = frame_time(a)
            e = frame_time(b + 1)
            u_ckis.append(sum(1 for m in merged
                              if frame_time(m[0]) < e and s < frame_time(m[1] + 1)))
        u_ckis_n = sum(1 for x in u_ckis if x)

        # DİYALOG ŞÜPHESİ = konumu ölçülmüş, merkezi ölçülmüş diyalog
        # bandının içinde olan VE ana diyalogla zaman örtüşmesi olmayan blok.
        # ELEME DEĞİLDİR — bkz. UST_SUS_Y_LO/HI yorumu: ölçümde gerçek üst
        # ile üstte çizilen diyalog aralıkları İÇ İÇE geçtiği için bu koşul
        # tek başına karar veremez.
        u_sus = [bool(u_bb[i] is not None and u_ckis[i] == 0
                      and UST_SUS_Y_LO <= u_bb[i]["tam"] <= UST_SUS_Y_HI)
                 for i in range(len(ust))]
        u_sus_n = sum(u_sus)
        if u_sus_n:
            note(f"[ust] DIYALOG SUPHESI (konum+zaman): {u_sus_n}/{len(ust)} "
                 f"blok merkez y {UST_SUS_Y_LO}-{UST_SUS_Y_HI} araliginda ve "
                 f"ana diyalogla zaman ortusmesi YOK — SILINMEZ, isaretlenir "
                 f"(olcum: gercek ust ile ustte-cizilen diyalog araliklari "
                 f"ic ice gectigi icin karar verilemez)")
            for i, sus in enumerate(u_sus):
                if sus:
                    note(f"    [ust] supheli #{i + 1} merkez="
                         f"{u_bb[i]['tam']:.4f} t={frame_time(ust[i][0]):.2f}"
                         f"-{frame_time(ust[i][1] + 1):.2f}s "
                         f"{ust[i][2][:60]!r}")
        else:
            note(f"[ust] DIYALOG SUPHESI: 0/{len(ust)} blok")

        # --- 4) çıktı: SRT temiz + konum AYRI json'da ---
        with ust_srt.open("w", encoding="utf-8") as f:
            for i, (a, b, text, conf) in enumerate(ust, 1):
                start = frame_time(a)
                end = frame_time(b + 1)
                if i < len(ust):
                    end = min(end, frame_time(ust[i][0]))
                f.write(f"{i}\n{fmt_ts(start)} --> {fmt_ts(end)}\n{text}\n\n")
        u_data = {"video": video.name, "bolge": [0, ucfg["band_h"]],
                  "oran": UST_REGION_FRAC, "mask": ucfg["mask"],
                  "esik": u_esik,
                  "diyalog_supheli": u_sus_n, "bloklar": []}
        for i, (a, b, text, conf) in enumerate(ust, 1):
            bb = u_bb[i - 1]
            u_data["bloklar"].append({
                "index": i,
                "start": round(frame_time(a), 3),
                "end": round(frame_time(b + 1), 3),
                "text": text,
                "conf": round(conf, 4),
                "bbox_y_ratio": round(bb["tam"], 4) if bb else None,
                "bbox_y_band_ratio": round(bb["ust"], 4) if bb else None,
                "bbox_y0_ratio": round(bb["y0"], 4) if bb else None,
                "bbox_y1_ratio": round(bb["y1"], 4) if bb else None,
                "diyalog_ckismesi": u_ckis[i - 1],
                "diyalog_supheli": u_sus[i - 1]})
        ust_json.write_text(json.dumps(u_data, ensure_ascii=False, indent=2),
                            encoding="utf-8")

        u_low = sum(1 for _, _, _, c in ust if c < args.conf_thr)
        # "log lines must not lie": buradaki sayılar KESİLMİŞ `ust`
        # listesinden okunur, yazılmadan önce hesaplanır; kapı sayıları da
        # karar VERİLDİKTEN SONRA (ust = [...]) okunur.
        print(f"[ust 3/3] SRT yazildi: {ust_srt} ({len(ust)} blok) | "
              f"konum: {ust_json} | guveni dusuk (conf<{args.conf_thr}): "
              f"{u_low}/{len(ust)} | surezgec: {u_dropped} blok | "
              f"guven kapisi (conf>={args.ust_conf_thr}): {u_once} -> "
              f"{len(ust)} ({len(u_dusen)} elendi)")
        if not ust:
            print("    ust bantta blok bulunamadi — SRT ve json BOS "
                  "yazildi (onceki kosunun icerigi tasindi)")
        if u_second:
            note(f"[ust] ikinci motor oy: {u_second} blok")

        # --- 5) QA montajları (en fazla 4) ---
        # Ana SRT'nin montajları AYNI klasöre qa_*.png olarak yazılmaya
        # devam eder: "ana çıktı değişmedi" iddiası gözle doğrulanabilir
        # kalsın diye ikinci çıktı da görünür olmalı (F7 dersi).
        if args.qa and ust:
            qa_dir = Path(args.qa_dir)
            qa_dir.mkdir(parents=True, exist_ok=True)
            uh = min(info["height"], ucfg["band_h"] + 80)

            def _qa_ust(blk, out_png):
                a, b, text, conf = blk
                mid = (a + b) // 2
                img = grab_band(video, frame_time(mid), 0, uh)
                h, w = img.shape[:2]
                canvas = np.full((h + 70, w, 3), 30, np.uint8)
                canvas[:h] = img
                cv2.line(canvas, (0, min(ucfg["band_h"], h - 1)),
                         (w, min(ucfg["band_h"], h - 1)), (0, 0, 255), 2)
                label = f"UST: {text!r} (conf={conf:.2f}, " \
                        f"t={frame_time(mid):.2f}s)"
                cv2.putText(canvas, label[:170], (10, h + 45),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255),
                            1, cv2.LINE_AA)
                cv2.imwrite(str(out_png), canvas)

            n = min(args.qa, 4, len(ust))      # şartname: 4 montaj
            picks = [ust[i * len(ust) // n] for i in range(n)]
            for k, blk in enumerate(picks, 1):
                _qa_ust(blk, qa_dir / f"ust_{k:03d}_f{(blk[0]+blk[1])//2}.png")
            print(f"    QA ust montajlari: {qa_dir}/ust_*.png ({len(picks)} adet)")

        return {"blok": len(ust), "statik_elenen": round(statik_oran, 6),
                "statik_elenen_piksel": n_drop,
                "statik_olcusan_piksel": n_band,
                "statik_kare": sfr,
                "bolge": [0, ucfg["band_h"]], "mask": ucfg["mask"],
                "bolge_kisit_oncesi": [0, ust_h_ham],
                "konuma_kisitlandi": u_kisit,
                "diyalog_bandi": [dia_y, cfg["band_h"]],
                "esik": u_esik, "metin_yuksekligi": ucfg["text_h"],
                "upscale2x": u2x, "raw": u_raw,
                "stil_kaynak": "oto" if auto_ust else "varsayilan",
                "segment": len(segments), "surezgec": u_dropped,
                "blok_guven_oncesi": u_once,
                "guven_elenen": len(u_dusen),
                "guven_elenen_conf": [round(r[3], 4) for r in u_dusen],
                "guven_elenen_metin": [r[2][:70] for r in u_dusen],
                "guven_esik": args.ust_conf_thr,
                "dusuk_guven": u_low, "conf_thr": args.conf_thr,
                "diyalog_supheli": u_sus_n,
                "diyalog_ckismeli": u_ckis_n,
                "konum_kurali": {"tur": "teshis", "eleme": False,
                                 "y_lo": UST_SUS_Y_LO, "y_hi": UST_SUS_Y_HI},
                "second_engine_votes": u_second,
                "srt": ust_srt.name, "json": ust_json.name,
                "sure_sn": round(time.time() - t_all, 1)}, [list(x) for x in ust]

    # --- Düzeltme 1c: oto-eşik kendini iyileştirme (BİR KEZ) ---
    # Wano tur 2 (05.10.2026) tetiği: ort. conf < 0.55 VEYA low-conf oranı
    # > %40 (ilk 24 blok) ise eşik 10 düşürülüp tarama+OCR tekrarlanır;
    # SKOR artık çöpü ödüllendirmez (aşağıdaki _score). Eski tetik (yalnız
    # ort < 0.4, -5) E1049 vakasını kaçırdı: low-conf 10/17 (%59) iken
    # deneme hiç açılmadı (stats.auto_esik_kayit.denendi=false).
    # Kanıt: 86tr esik 250 → 44/45 çöp; 245 → 7/48 (elle ölçüm).
    # F3/F4/F5: modun kendi eşiğine uygulanır (bkz. aşağıdaki notlar).
    esik_denemesi = None
    # Task 4 (03.10.2026): bu alan YALNIZ KABUL halinde doluyordu; reddedilen
    # (ya da hic denenmeyen) kosularda null kalip "deneme hic olmadi" izlenimi
    # veriyordu. Artık `esik_kayit` HER denemeyi sonucuyla birlikte yazar.
    esik_kayit = {"denendi": False}
    if auto_thr and not args.no_ocr:
        probe = merged[:24]
        mean_conf = sum(r[3] for r in probe) / len(probe) if probe else 0.0
        low_oran = (sum(1 for r in probe if r[3] < args.conf_thr)
                    / len(probe)) if probe else 0.0
        tetikler = []
        if not merged:
            tetikler.append("cikti-bos")
        else:
            if mean_conf < 0.55:
                tetikler.append(f"ort-conf {mean_conf:.2f}<0.55")
            if low_oran > 0.40:
                tetikler.append(f"low-conf-orani %{low_oran * 100:.0f}>%40")
        if tetikler:
            # F3: denenen esik MODUN KENDI esigidir.
            #   thr modu    -> white_thr  (scan_band L388: mask = frame > white_thr)
            #   tophat modu -> tophat_thr (scan_band L387: mask = frame > tophat_thr)
            # ESKIDEN bu blok yalnizca mask=="thr" icinde calisiyordu; tophat
            # modunda white_thr HIC KULLANILMAZ (scan_band L386, ayrica
            # use_raw=L1181 ile binarize de yapilmaz). Yani eski kosullu bir
            # tophat kosusunda white_thr dusurulebilirdi ama bit-bit ayni
            # tarama+OCR ciktisini uretirdi: ~46 snlik tam bir gecis ve
            # _score(m2) > _score(merged) asla dogru cikmazdi. Duzeltme
            # tophat'ta esigi tophat_thr'ye uygular; boylece 2. Bolum ve
            # BOCCHI (mask: tophat, en kotu iki cikti) gercekten yarar gorur.
            esik_anahtari = "tophat_thr" if cfg["mask"] == "tophat" \
                else "white_thr"
            taban = 20 if esik_anahtari == "tophat_thr" else 230
            yeni = max(taban, cfg[esik_anahtari] - 10)
            if yeni < cfg[esik_anahtari]:
                note(f"[auto] esik yeniden denemesi ({'; '.join(tetikler)}): "
                     f"{esik_anahtari} {cfg[esik_anahtari]} -> {yeni}")
                cfg2 = dict(cfg)
                cfg2[esik_anahtari] = yeni

                def _score(mm):
                    """F4: blok SAYISI odullenmez.

                    Eski skor len(mm) * ort(conf) idi; esik dusuruldugunde
                    tarama daha cok, daha kucuk ve daha dusuk guvenli sahte
                    segment bulur ve bu cogalma skoru YUKSELTIRDI. Olcum
                    (86tr): esik 250 -> 45 blok/44 cop, 245 -> 48 blok/7 cop;
                    len*ort(conf) skoru 250'yi secerdi, yani asil duzeltme
                    calismazdi.

                    Yeni skor yalniz guvenilir bloklari hesaba katar:
                        skor = (guvenilir bloklarin conf toplami) - 2*cop
                    cop basina ceza, iki temiz blogun degerine esittir;
                    boylece bir sahte segment cogalması hicbir zaman kazandiramaz.
                    (esik_denemesi sifir blok = 0.0, tamamen cop = negatif)
                    """
                    if not mm:
                        return 0.0
                    iyi = [r[3] for r in mm if r[3] >= args.conf_thr]
                    return sum(iyi) - 2.0 * (len(mm) - len(iyi))

                # --- Task 4: UCUZ ON DEGER. --------------------------
                # Once her iki esik de YALNIZ ilk `ornek_sn` saniyede
                # taranir. Aday esik ornekte daha iyi degilse TAM gecis
                # HIC yapilmaz. Olcum gerekcesi: tam yeniden deneme
                # BOCCHI'de ~305 sn ekledi ve iki gercek kosuda da
                # (BOCCHI -4.0 -> -4.0, 2. Bolum -43.0 -> -51.9) reddedildi.
                ornek_sn = 25.0
                tam_sn = args.limit_seconds if args.limit_seconds > 0 \
                    else info["duration"]
                ornek_sn = min(ornek_sn, tam_sn)
                esik_kayit.update({
                    "denendi": True, "esik_anahtari": esik_anahtari,
                    "esik_once": cfg[esik_anahtari], "esik_yeni": yeni,
                    "tetik": "; ".join(tetikler),
                    "ort_conf": round(mean_conf, 3),
                    "low_oran": round(low_oran, 3),
                    "ornek_sn": round(ornek_sn, 1),
                    "tam_sn": round(tam_sn, 1)})
                t_esik = time.time()
                esik_kayit["tam_gecis_yapildi"] = False
                # referans (eski esik) ornegi: eldeki `merged` zaten tam
                # gecisin ciktisi; yalniz ilk penceredeki bloklar alinir.
                orn_f = ornek_sn * fps
                m0_ornek = [r for r in merged if r[0] < orn_f]
                m2_ornek, _mr, _mv, _s, _v, _tk, _tkl, _fd, _cb, _ck = \
                    _extract(cfg2, limit_s=ornek_sn)
                s0o, s1o = _score(m0_ornek), _score(m2_ornek)
                esik_kayit.update({
                    "ornek_skor_once": round(s0o, 2),
                    "ornek_skor_yeni": round(s1o, 2),
                    "ornek_blok_once": len(m0_ornek),
                    "ornek_blok_yeni": len(m2_ornek)})
                if s1o <= s0o:
                    esik_kayit.update({
                        "karar": "red-ornek",
                        "gerekce": f"ilk {ornek_sn:.0f} sn'de skor "
                                  f"{s0o:.1f} -> {s1o:.1f} (kazanci yok); "
                                  f"TAM gecis yapilmadi"})
                    note(f"[auto] esik yeniden denemesi REDDEDILDI "
                         f"(ORNEK {ornek_sn:.0f} sn): {esik_anahtari}={yeni} "
                         f"skor {s0o:.1f} -> {s1o:.1f}; tam gecis "
                         f"YAPILMADI ({time.time() - t_esik:.0f} sn)")
                else:
                    # Ornek kazanci gosterdi -> simdi tam gecise harcanir.
                    m2, mr2, mv2, s2, v2, tk2, tkl2, fd2, cb2, ck2 = \
                        _extract(cfg2)
                    esik_kayit["tam_gecis_yapildi"] = True
                    s0, s1_ = _score(merged), _score(m2)
                    esik_kayit.update({"tam_skor_once": round(s0, 2),
                                       "tam_skor_yeni": round(s1_, 2),
                                       "tam_blok_once": len(merged),
                                       "tam_blok_yeni": len(m2)})
                    if s1_ > s0:
                        merged, merged_raw, micro_moved = m2, mr2, mv2
                        scanned, second_votes, second_takas = s2, v2, tk2
                        takas_kayit, fade_n = tkl2, fd2
                        cjk_blok, cjk_kayit = cb2, ck2
                        cfg[esik_anahtari] = yeni
                        esik_denemesi = yeni
                        esik_kayit.update({"karar": "kabul",
                                           "gerekce": "ornekte ve tam "
                                                      "geciste kazandi"})
                        note(f"[OK] esik yeniden denemesi KABUL: "
                             f"{esik_anahtari}={yeni} (skor {s0:.1f} -> "
                             f"{s1_:.1f}, {len(m2)} blok)")
                    else:
                        esik_kayit.update({
                            "karar": "red-tam",
                            "gerekce": f"ornekte kazandi ama tam geciste "
                                       f"skor {s0:.1f} -> {s1_:.1f}"})
                        note(f"[auto] esik yeniden denemesi REDDEDILDI "
                             f"(tam gecis): {esik_anahtari}={yeni} "
                             f"(skor {s0:.1f} -> {s1_:.1f}; {len(m2)} blok, "
                             f"{sum(1 for r in m2 if r[3] < args.conf_thr)} "
                             f"dusuk guven)")
                esik_kayit["sure_s"] = round(time.time() - t_esik, 1)

    # --- Düzeltme 2c: gürültü ayrımı (taşıma var, silme yok) ---
    ekran_ist = {}
    ana, gurultu = _split_noise(merged, fps, ayir=args.ayir_gurultu,
                                sozluk=sozluk, istatistik=ekran_ist)
    # --- inceleme turu 5: SFX çöp profili (eşiğin genişletmesi) ---
    # Ölçüm (_rev-RAPOR.md (b) BOCCHI): "V;+ 4+ @ R k7" gibi SFX çöpü
    # mevcut profili (conf<0.45 ∧ dur<0.6) kaçırabiliyor. Genişletme:
    # harf oranı < SFX_HARF ∧ süre < SFX_DUR ∧ komşu metin bağı yok.
    # Ön ölçüm (eski ana SRT'ler): BLEND-S 1 aday (22:04 çöp bloğu),
    # BOCCHI/86/1.Bolum 0 aday — GT diyalog blokları profile girmez.
    if args.ayir_gurultu:
        ana, sfx_tasinan = _sfx_ayir(ana, fps)
        if sfx_tasinan:
            gurultu.extend(sfx_tasinan)
            ekran_ist["sfx-cop"] = ekran_ist.get("sfx-cop", 0) + \
                len(sfx_tasinan)
            note(f"[sfx] {len(sfx_tasinan)} blok _ekran'a tasindi "
                 f"(harf_orani<{SFX_HARF}, sure<{SFX_DUR}sn, komsu metin "
                 f"bagi yok; silinmedi)")
            for _r in sfx_tasinan[:6]:
                note(f"    [sfx] t={frame_time(_r[0]):.2f}s {_r[2][:60]!r}")
        else:
            note("[sfx] profil eslesmedi: 0 blok")
    # --- düzeltme 2: takas-cjk blokları istatistiği ve günlüğü ------------
    # Bloklar <ad>_ekran.srt'ye aşağıda eklenir (ekran_bloklari kurulumundan
    # sonra); kaynak etiketi "takas-cjk". --ayir-gurultu'dan BAĞIMSIZDIR
    # (--jenerik deseni): CJK kredi diyalog değildir ve eski davranışta
    # zaten ana SRT'de değildi (Türkçe süzgeci düşürüyordu — kayıp vakası).
    if cjk_blok:
        ekran_ist["takas-cjk"] = ekran_ist.get("takas-cjk", 0) + \
            len(cjk_blok)
        note(f"[takas-cjk] {len(cjk_blok)} blok ikinci motorun CJK okumasıyla "
             f"_ekran'a yonlendirildi (ilk conf<%.2f + CJK cogunluk; "
             f"diyalog akisina girmedi, silinmedi)"
             % TAKAS_CJK_ILK_CONF)
        for _r in cjk_blok[:6]:
            note(f"    [takas-cjk] t={frame_time(_r[0]):.2f}s {_r[2][:50]!r}")
    ekran_n = sum(ekran_ist.values())
    if ekran_n:
        detay = ", ".join(f"{k}: {v}" for k, v in sorted(ekran_ist.items()))
        note(f"[ekran] {ekran_n} blok ayridi ({detay})")
    else:
        note("[ekran] siniflandirici 0 blok ayirdi")
    # DEĞİŞİK A (HATA DÜZELTMESİ) + F2: --no-ayir-gurultu GERÇEKTEN kayıpsız.
    #
    # Ölçülen hata (1. Bolum, 60 sn):
    #     varsayilan (--ayir-gurultu) : ana 12 + _ekran.srt 1 = 13 blok
    #     --no-ayir-gurultu         : ana 12 + _ekran.srt 0 = 12 blok
    # Kaybolan tek blok 00:00:16,808 --> 00:00:17,059 "(svedix) ##} #" idi
    # (yani çöptü, gerçek içerik gitmedi) — ama bu tesadüf. _micro_cleanup
    # bu bloğu `merged` listesinden ÇIKARIP `micro_moved`'a koyar; F2
    # değişikliğinden sonra micro_moved YALNIZCA `ekran_bloklari` içinde
    # yaşıyordu, o da bayrak AÇIKKEN doluyordu. Bayrak KAPALIYKEN
    # micro_moved HİÇBİR YERE YAZILMIYORDU -> sessiz silme. Bayrağın
    # anlamı "gürültüyü AYIRMA"dır, "gürültüyü SİL" değil; bu aracın
    # kendi kuralı da (Kural 1) hiçbir şeyin silinmediğidir.
    # Artık bayrak kapalıyken bu bloklar ANA SRT'ye KRONOLOJİK olarak geri
    # konur. Ana SRT yazımı (aşağıda) zaten zamana göre sıralı `ana`
    # varsaydığı için (bir sonraki bloğun başlangıcına end kırpılır) sıralama
    # şart; anahtar 0 (başlangıç karesi) üzerinden sıralanır.
    if args.ayir_gurultu:
        ekran_bloklari = sorted(micro_moved + gurultu, key=lambda r: r[0])
        mikro_geri = 0
    else:
        ekran_bloklari = []
        mikro_geri = len(micro_moved)
        if mikro_geri:
            ana = sorted(ana + [list(m) for m in micro_moved],
                         key=lambda r: r[0])
            note(f"[ayir] --no-ayir-gurultu: {mikro_geri} mikro blok ana "
                 f"SRT'ye geri konuldu (silinmedi); ana SRT: {len(ana)} blok")
    if cjk_blok:
        # düzeltme 2: takas-cjk blokları _ekran havuzuna (bayraksız koşuda da)
        ekran_bloklari = sorted(ekran_bloklari + cjk_blok, key=lambda r: r[0])
    second_used = _SECOND["name"] if _SECOND["state"] == "ok" else None

    # --- Task 3: tekrarlayan filigran kümeleri (yalnız ayrım açıkken) ---
    # --no-ayir-gurltu "her blok ana SRT'de" demektir; bayrak kapalıyken
    # bu kural TETİKLENMEZ (değişmez #3 korunur).
    tekrar_kume, tekrar_tasinan = [], 0
    _tasinan = []
    if args.ayir_gurultu:
        # ⛔ KUMELEME HAVUZU `_ekran.srt`'ye GIDECEK BLOKLARIN TAMAMIDIR:
        # `micro_moved + gurultu`. Yalnız `gurultu` verilirse küme kurulamaz —
        # BOCCHI ölçümü: filigran bloklarının 14/14'ü 0.125-0.375 sn oldukları
        # için `_micro_cleanup` onları `micro_moved`'a koydu; `_split_noise`
        # çıktısı olan `gurultu` neredeyse boş kaldı ve kural (ilk denemede)
        # TETİKLENMEDİ — ana SRT'de 2 filigran bloğu kalmıştı.
        _havuz = list(micro_moved) + list(gurultu)
        _ana2, _gur2, _tasinan, _bilgi = _split_repeat(ana, _havuz, fps)
        if _tasinan:
            tekrar_tasinan, tekrar_kume = len(_tasinan), _bilgi
            ana = _ana2
            ekran_bloklari = sorted(ekran_bloklari + _tasinan,
                                    key=lambda r: r[0])
            note(f"[tekrar] FILIGRAN KUMESI: {tekrar_tasinan} blok ana "
                 f"SRT'den _ekran.srt'ye TASINDI (silinmedi); "
                 f"{len(_bilgi)} kume")
            for _k in _bilgi[:4]:
                note(f"    [tekrar] kume: {_k['adet']} blok "
                     f"t={_k['baslangic_s']}s medyan_sure="
                     f"{_k['medyan_sure']}sn {_k['ornek'][0]!r}")
        else:
            note(f"[tekrar] kural calisti, TEKRAR KUMESI BULUNMADI "
                 f"(sim>={REP_SIM}, pencere {REP_WIN}sn, >={REP_MIN} blok, "
                 f"medyan sure<={REP_MEDIAN_DUR}sn); hicbir blok tasinmadi")
    else:
        note("[tekrar] --no-ayir-gurultu: tekrarlayan kume kurali "
             "calismaz (her blok ana SRT'de kalir)")

    # --- 8. tur: --jenerik zaman aralığı ayrımı (kullanıcı beyanı) ---
    # --no-ayir-gurultu'dan BAĞIMSIZ çalışır: "şu aralık jenerik" demek,
    # "gürültüyü ayırma"dan daha güçlü ve açık bir beyandır.
    jenerik_bilgi = None
    if args.jenerik:
        ana, _jn_tasan, _jn_bilgi = _jenerik_ayir(ana, args.jenerik, fps)
        if _jn_tasan:
            ekran_bloklari = sorted(ekran_bloklari + _jn_tasan,
                                    key=lambda r: r[0])
            note(f"[jenerik] {len(_jn_tasan)} blok ana SRT'den _ekran.srt'ye "
                 f"TASINDI (silinmedi): "
                 + ", ".join(f"t={b['start']:.1f}s" for b in _jn_bilgi[:6]))
        else:
            note("[jenerik] verilen araliklarda baslayan blok bulunamadi "
                 "(hicbir blok tasinmadi)")
        jenerik_bilgi = {"aralik": [[round(a, 3), round(b, 3)]
                                    for a, b in args.jenerik],
                         "tasinan": len(_jn_tasan), "bloklar": _jn_bilgi}

    # --- inceleme turu 2: mikro-fragman geri birleştirme ---------------
    # Ölçüm (_rev-RAPOR.md (b) BOCCHI): gerçek diyalog "Kes şunu!"
    # (12:08.4-12:09.3) _ekran'da 0.125 sn'lik kırıntılarda kalmıştı.
    # Aday havuz: micro_moved + gurultu (tekrar ve jenerik ile taşınanlar
    # — kullanıcı beyanı/aday-tarama çıktısı — DÖNMEZ). Uygun kırıntılar
    # tek blok olarak ana SRT'ye geri konur; çöp satırlar _ekran'da kalır.
    mikro_geri_kume = []
    if args.ayir_gurultu and (micro_moved or gurultu):
        ana, _mg_geri, _mg_cikan, mikro_geri_kume = _mikro_geri(
            ana, list(micro_moved) + list(gurultu), fps, sozluk)
        if _mg_geri:
            ekran_bloklari = [b for b in ekran_bloklari
                              if id(b) not in _mg_cikan]
            note(f"[mikro-geri] {len(_mg_geri)} diyalog kırıntısı ana "
                 f"SRT'ye GERİ konuldu (kalan çöp satırlar _ekran'da "
                 f"korundu; silinmedi): " + "; ".join(
                     f"t={fmt_ts(frame_time(g[0]))} {g[2][:30]!r}"
                     for g in _mg_geri[:6]))
            for _mg in mikro_geri_kume:
                note(f"    [mikro-geri] t={_mg['start']}"
                     f"-{_mg['end']}s fragman={_mg['fragman']} "
                     f"{_mg['metin']!r}")
        else:
            note("[mikro-geri] uygun diyalog kırıntısı yok — blok dönmedi")

    # --- Düzeltme 2a: "hardsub yok" kararı (ham OCR çıktısıyla: mikro
    # temizlik çöpleri birleştirip süreyi uzattığı için karar ham listede
    # verilir — ölçüm: BOCCHI'de temizlik sonrası ortalama süre 0.83 sn'e
    # çıkıp koşulu boşa çıkarıyordu) ---
    hardsub_shr = "var"
    if not args.no_ocr:
        if not merged_raw:
            hardsub_shr = "yok-muhtemel"
        else:
            durs = [(b - a + 1) / fps for a, b, _, _ in merged_raw]
            mean_dur = sum(durs) / len(durs)
            mean_conf = sum(r[3] for r in merged_raw) / len(merged_raw)
            if mean_conf < 0.4 and (mean_dur < 0.5 or
                                    band_source == "aday-tarama"):
                # aday-bant koşusunda süre koşulu aranmaz: birincil dedektörün
                # reddettiği bant zaten şüpheli (ölçüm: Oshi'de 1.6 sn'lik
                # logo bloğu ortalama süreyi 1.1 sn'e çekip koşulu
                # boşa çıkarıyordu)
                hardsub_shr = "yok-muhtemel"
        if hardsub_shr == "yok-muhtemel":
            note("[!] Bu videoda hardsub bulunamadi olabilir "
                 "(logo/poster gurultusu). Yan dosyada .vtt var mi kontrol et.")

    # --- Task 2: ust bant ikinci gecisi ANA SRT'den ONCE calisir ---
    # Varsayilan davranis BITBIT AYNIDIR (bayrak yoksa ust_pass hic cagrilmaz,
    # ana SRT bayraksiz kosuyla ayni kalir). `--ust-ana` verildiginde ust
    # bandin BLOKLARI ana SRT'ye eklenir; arada TEK BIR SINIFLANDIRMA YOKTUR
    # (bkz. _ust_pass docstring'i: konum/dil ayirt edici DEGILDIR, olcduk).
    # Kullanici bu bayragi vererek "bu yapimda diyalog ustte de ciziliyor"
    # DEMEDIR; arac tahmin etmez. Hicbir blok SILINMEZ.
    ust_bant = None
    if args.ust_bant or args.ust_ana:
        ust_bant, _ust_bloklar = _ust_pass()
        if args.ust_ana:
            ana = sorted(ana + [list(x) for x in _ust_bloklar],
                         key=lambda r: r[0])
            note(f"[ust-ana] ust bandin {len(_ust_bloklar)} "
                 f"blogu ANA SRT'ye eklendi (siniflandirma yapilmadi; "
                 f"hicbir blok silinmedi)")
            ust_bant["ana_srte_eklendi"] = len(_ust_bloklar)

    # --- Türkçe post-fix (04.10.2026, BLEND-S GT ölçümü; --duzelt) ---
    # ana SRT'ye yazılacak her bloğun metni sözlükle onarılır; her atomik
    # değişiklik <ad>.duzeltme-gunlugu.json'a yazılır (Kural 1). Ekran
    # blokları (_ekran.srt) HAM OCR kalır — Japonca/Vietnamca metne
    # Türkçe onarımı uygulanmaz.
    duzelt_kayit = []
    duzelt_say = {}
    duzelt_degisen = 0
    if args.duzelt:
        for blok in ana:
            yeni_metin, deg = _duzelt_blok_metni(
                blok[2], sozluk, kullanici, duzelt_kayit,
                zaman=fmt_ts(frame_time(blok[0])))
            if deg:
                blok[2] = yeni_metin
                duzelt_degisen += 1
                for d in deg:
                    duzelt_say[d["sinif"]] = duzelt_say.get(d["sinif"], 0) + 1
        gunluk_yol = out.with_name(out.stem + ".duzeltme-gunlugu.json")
        gunluk_yol.write_text(
            json.dumps({"video": video.name, "sozluk": _SOZLUK["kaynak"],
                        "kullanici_sozluk": _KULLANICI["kaynak"],
                        "duzelt_acik": True,
                        "degisen_blok": duzelt_degisen,
                        "degisiklik": duzelt_kayit},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        ozet = ", ".join(f"{k}: {v}" for k, v in sorted(duzelt_say.items())) \
            if duzelt_say else "0 degisiklik"
        note(f"[duzelt] {ozet} — {duzelt_degisen} blok degisti "
             f"(gunluk: {gunluk_yol.name})")
    else:
        note("[duzelt] kapali (--no-duzelt): ana SRT ham OCR metnini tasir")

    # --- SRT yaz ---
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for i, (a, b, text, conf) in enumerate(ana, 1):
            start = frame_time(a)
            end = frame_time(b + 1)
            if i < len(ana):
                end = min(end, frame_time(ana[i][0]))
            f.write(f"{i}\n{fmt_ts(start)} --> {fmt_ts(end)}\n{text}\n\n")
    # F6: _ekran.srt HER ZAMAN yazilir. Eskiden yalnizca ekran_bloklari
    # doluysa dosya dokunulmazdi; 0 blok ayrilan bir kosuda bir onceki
    # kosunun _ekran.srt'i yeni bir dosya adiyla ("bu kosunun ciktisi")
    # hayatta kaliyordu. Artik dosya her kosuda yeniden yazilir; 0 blok
    # ayrilmissa BOS (0 bayt) birakilir -> bayat icerik taklit edilemez.
    ekran_path = out.with_name(out.stem + "_ekran.srt")
    with ekran_path.open("w", encoding="utf-8") as f:
        for i, (a, b, text, conf) in enumerate(ekran_bloklari, 1):
            start = frame_time(a)
            end = frame_time(b + 1)
            if i < len(ekran_bloklari):
                end = min(end, frame_time(ekran_bloklari[i][0]))
            f.write(f"{i}\n{fmt_ts(start)} --> {fmt_ts(end)}\n{text}\n\n")
    if ekran_bloklari:
        print(f"    gurultu ayrimi: {len(ekran_bloklari)} blok -> "
              f"{ekran_path.name}")
    else:
        print(f"    gurultu ayrimi: 0 blok -> {ekran_path.name} "
              f"(bos yazildi; onceki kosunun icerigi tasindi)"
              + (f" | {mikro_geri} mikro blok ana SRT'de "
                 f"(--no-ayir-gurultu: geri konuldu, silinmedi)"
                 if mikro_geri else ""))

    # --- blok güven dökümü (ui v1.3 "Kontrol Et" paneli + pano zemini) -----
    # YALNIZ EK DOSYA: ana + _ekran blokları (metin post-fix SONRASI, conf
    # OCR'dan) tek JSON dizisine dökülür. SRT/stats üretimini ETKİLEMEZ —
    # parite: aynı koşunun SRT'si bu bloktan önce de sonra da birebir aynı.
    # start/end, SRT'deki kırpılmış zamanların aynısı (aşağıdaki yazımla
    # aynı formül); index, kendi dosyasının 0 tabanlı sırası.
    def _dokum_zamani(sira, bloklar_listesi):
        a, b = bloklar_listesi[sira][0], bloklar_listesi[sira][1]
        end = frame_time(b + 1)
        if sira + 1 < len(bloklar_listesi):
            end = min(end, frame_time(bloklar_listesi[sira + 1][0]))
        return fmt_ts(frame_time(a)), fmt_ts(end)

    dokum = []
    for i, (_a, _b, metin, conf) in enumerate(ana):
        bas, son = _dokum_zamani(i, ana)
        dokum.append({"index": i, "start": bas, "end": son, "metin": metin,
                      "conf": round(conf, 4), "kaynak": "ana"})
    for i, (_a, _b, metin, conf) in enumerate(ekran_bloklari):
        bas, son = _dokum_zamani(i, ekran_bloklari)
        dokum.append({"index": i, "start": bas, "end": son, "metin": metin,
                      "conf": round(conf, 4), "kaynak": "ekran"})
    bloklar_yol = out.with_name(out.stem + ".bloklar.json")
    bloklar_yol.write_text(
        json.dumps(dokum, ensure_ascii=False, indent=2), encoding="utf-8")
    note(f"[i] blok dokumu: {bloklar_yol.name} ({len(ana)} ana + "
         f"{len(ekran_bloklari)} ekran blok; conf degerleriyle)")

    # --- 8. tur: takas günlüğü (SESSİZ DEĞİŞİKLİK YOK) ---
    # İkinci motor düşük-konsensus takasıyla metni devraldıysa, hangi
    # blokta neyin değiştiği burada kayıtlı kalır. F6 deseni: dosya HER
    # koşuda yeniden yazılır (0 takas -> boş değişiklik listesi; bayat
    # içerik yeni koşuyu taklit edemez).
    takas_yol = out.with_name(out.stem + ".takas-gunlugu.json")
    takas_yol.write_text(
        json.dumps({"video": video.name, "takas": len(takas_kayit),
                    "kural": {"konsensus_conf_ust": TAKAS_KONSENS_CONF,
                              "ikinci_conf_alt": TAKAS_IKINCI_CONF,
                              "conf_fark_alt": TAKAS_CONF_FARK},
                    "degisiklik": takas_kayit,
                    "takas_cjk": cjk_kayit,
                    "takas_cjk_kural": {"ilk_conf_ust": TAKAS_CJK_ILK_CONF,
                                        "cjk_oran_alt": 0.5}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    if takas_kayit:
        print(f"    takas: {len(takas_kayit)} blok ikinci motordan alindi "
              f"(gunluk: {takas_yol.name})")
    if cjk_kayit:
        print(f"    takas-cjk: {len(cjk_kayit)} blok takas RED -> _ekran "
              f"(gunluk: {takas_yol.name})")

    low = sum(1 for _, _, _, conf in ana if conf < args.conf_thr)
    print(f"[3/3] SRT yazildi: {out}")
    print(f"    guveni dusuk blok (conf<{args.conf_thr}): {low}/{len(ana)}")

    # --- 05.10.2026: verim-sanity (Wano E1017-E1080 çöküşü) ---------------
    # Ölçülen arıza: 20 bölümde blok/dk<4 ve düşük-conf baskındı (ör. E1049:
    # 17 blok / ~24 dk, low-conf 10/17; E1050: 6 blok, low-conf 5/6) — koşu
    # "başarılı" bitiyor, kimse bakmıyordu. blok/dk < 4 VE low-conf oranı
    # > %50 iken uyarı + stats işareti. DAVRANIŞ DEĞİŞMEZ: blok taşınmaz/
    # silinmez, yalnız kullanıcıya yol gösterilir (altyazısız-rip uyarısı
    # deseni: stats anahtarı yalnız uyarı VARKEN yazılır).
    taranan_sn = scanned / fps if fps > 0 else 0.0
    verim_uyari = False
    if ana and taranan_sn >= 60.0:
        blok_dk = len(ana) / (taranan_sn / 60.0)
        if blok_dk < 4.0 and low > 0.5 * len(ana):
            verim_uyari = True
            note(f"[!] Verim dusuk — bant yanlis algilanmis olabilir "
                 f"(auto: y={cfg['band_y']}, h={cfg['band_h']}; "
                 f"{blok_dk:.1f} blok/dk, low-conf {low}/{len(ana)}). "
                 f"--band-y/--band-h ile deneyin.")

    # --- inceleme turu 1: altyazısız rip uyarısı (S02E09/E11 vakası) ---
    # Ölçüm zemini: S02E09/S02E11 stats.json — blocks=1, speech_seconds=2.0;
    # boş SRT doğruydu ama kullanıcıya bildirilmiyordu. Uyarı stderr'e
    # (note) yazılır; stats anahtarı yalnız uyarı VARKEN eklenir (normal
    # koşunun stats.json'u değişmez).
    konusma_toplam = sum((b - a + 1) / fps for a, b, _, _ in ana)
    rip_uyari = altyazisiz_rip_muhtemel(len(ana), konusma_toplam)
    if rip_uyari:
        note(f"[!] Bu video altyazisiz olabilir (toplam konusma "
             f"{konusma_toplam:.0f} sn). Kaynak rip altyazisiz; yan dosya "
             f".vtt kontrol et.")

    # --- ÜST BANT (--ust-bant, varsayılan KAPALI) ---
    # Diyalog yolu burada TAMAMEN bitti; bu ikinci geçiş ana SRT'ye
    # dokunmaz, yalnız <ad>_ust.srt + <ad>_ust.json üretir (bayrak
    # `--ust-ana` verilmişse istisna: bkz. yukarıdaki Task 2 bloğu).
    # Bayrak hiç verilmemişse hiçbir şey çalışmaz (stats.json'a da anahtar
    # eklenmez → bayraksız koşunun stats.json'u bit-bit aynı kalır).

    # --- QA montajı (eşit aralıklı örnekleme, deterministik) ---
    if args.qa:
        qa_dir = Path(args.qa_dir)
        qa_dir.mkdir(parents=True, exist_ok=True)   # F8c: --qa-dir a/b çalışsın
        wide_y = max(0, cfg["band_y"] - 90)   # iki satır kırpılma riskini görmek için

        def _qa_png(blok, out_png, etiket):
            a, b, text, conf = blok
            mid = (a + b) // 2
            img = grab_band(video, frame_time(mid), wide_y,
                            cfg["band_h"] + 90)
            h, w = img.shape[:2]
            canvas = np.full((h + 70, w, 3), 30, np.uint8)
            canvas[:h] = img
            label = f"{etiket}: {text!r} (conf={conf:.2f}, " \
                    f"t={frame_time(mid):.2f}s)"
            cv2.putText(canvas, label[:160], (10, h + 45),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255),
                        1, cv2.LINE_AA)
            cv2.imwrite(str(out_png), canvas)

        n = min(args.qa, len(ana))
        picks = [ana[i * len(ana) // n] for i in range(n)]
        for k, blk in enumerate(picks, 1):
            _qa_png(blk, qa_dir / f"qa_{k:03d}_f{(blk[0] + blk[1]) // 2}.png",
                    "SRT")
        print(f"    QA montajlari: {qa_dir}/ ({len(picks)} adet)")

        # F7: AYRILAN bloklar da çizilir. Eskiden yalnız ana SRT örnekleniyordu;
        # _ekran'a taşınan bloklar hiç render edilmediği için --qa koşusu
        # satır düşürürken tertemiz görünüyordu (veri kaybı görünmez kalıyordu).
        # Ayrılan bloklar ayrı klasöre, "AYRILDI" etiketiyle yazılır.
        if ekran_bloklari:
            ay_dir = qa_dir / "ayrilan"
            ay_dir.mkdir(parents=True, exist_ok=True)
            m = min(args.qa, len(ekran_bloklari))
            ay_picks = [ekran_bloklari[i * len(ekran_bloklari) // m]
                        for i in range(m)]
            for k, blk in enumerate(ay_picks, 1):
                mid = (blk[0] + blk[1]) // 2
                _qa_png(blk, ay_dir / f"qayr_{k:03d}_f{mid}.png",
                        "AYRILDI(_ekran.srt)")
            print(f"    QA ayrilan bloklar: {ay_dir}/ ({len(ay_picks)} adet)")
            # Task 3: TEKRAR kuraliyla tasinan bloklar AYRICA cizilir.
            # "QA her ayrilan blogu render etmeli" kurali bu bloklar icin de
            # gecerli — aksi halde filigran temizlenince montaj tertemiz
            # gorunur ve regresyon gizlenir.
            if tekrar_tasinan:
                te_dir = qa_dir / "tekrar"
                te_dir.mkdir(parents=True, exist_ok=True)
                te_bloklar = [b for b in ekran_bloklari if b in _tasinan]
                for k, blk in enumerate(te_bloklar, 1):
                    mid = (blk[0] + blk[1]) // 2
                    _qa_png(blk, te_dir / f"qtek_{k:03d}_f{mid}.png",
                            "TEKRAR-FILIGRAN(_ekran.srt)")
                print(f"    QA tekrar/filigran bloklari: {te_dir}/ "
                      f"({len(te_bloklar)} adet)")

    # "video" 05.10.2026 tur 2'den beri kimlik SÖZLÜĞÜDÜR:
    # {ad, mtime, boyut, sha1_ilk_1MB} (E02 vakasının kalıcı önlemi;
    # eski düz-isim okuyucusu yok — bkz. modül docstring tur 2 madde 4).
    stats = {"video": _video_kimlik(video),
             "fps": fps, "scanned_frames": scanned,
             "blocks": len(ana), "low_conf": low, "conf_thr": args.conf_thr,
             "speech_seconds": round(konusma_toplam, 1),
             "band": [cfg["band_y"], cfg["band_h"]],
             "white_thr": cfg["white_thr"], "mask": cfg["mask"],
             "tophat_thr": cfg["tophat_thr"],
             "auto_detect": det is not None, "band_source": band_source,
             "auto_esik_denemesi": esik_denemesi,
             "auto_esik_kayit": esik_kayit,
             "hardsub_shr": hardsub_shr,
             "ayir_gurultu": args.ayir_gurultu,
             "ayrilan_gurultu": len(ekran_bloklari),
             "ekran_siniflandirici": {"adet": ekran_n, **ekran_ist},
             "fade_duzeltilen": fade_n,
             "duzelt": {"acik": args.duzelt, "sozluk": _SOZLUK["kaynak"],
                        "degisen_blok": duzelt_degisen,
                        "siniflar": duzelt_say,
                        "gunluk": (out.stem + ".duzeltme-gunlugu.json")
                        if args.duzelt else None},
             "tekrar_kurali": {"sim": REP_SIM, "pencere_sn": REP_WIN,
                               "min_uye": REP_MIN,
                               "medyan_sure_asgari": REP_MEDIAN_DUR,
                               "tasan_blok": tekrar_tasinan,
                               "kumeler": tekrar_kume},
             "mikro_geri_kondu": mikro_geri,
             "mikro_geri_ekran": mikro_geri_kume,
             "lang": lang_list,
             "device": device, "upscale2x": upscale2x,
             "batch": args.batch, "nvdec": bool(HWACCEL),
             "ocr_mode": args.ocr,
             "second_engine": second_used,
             "second_engine_votes": second_votes,
             "second_engine_takas": second_takas,
             "second_engine_cuda": _ORT["durum"],
             "onnxruntime": _ORT["surum"]}
    # ust_bant YALNIZ --ust-bant verildiğinde eklenir (bayraksız koşunun
    # stats.json'u bit-bit aynı kalmalı).
    if ust_bant is not None:
        stats["ust_bant"] = ust_bant
    # jenerik YALNIZ --jenerik verildiğinde eklenir (bayraksız koşunun
    # stats.json'u değişmemeli — --ust-bant ile aynı desen).
    if jenerik_bilgi is not None:
        stats["jenerik"] = jenerik_bilgi
    # altyazisiz-rip anahtarı yalnız uyarı VARKEN yazılır (normal koşunun
    # stats.json'u anahtar eklenmeden aynı kalır).
    if rip_uyari:
        stats["altyazisiz_rip_muhtemel"] = True
    # verim-sanity anahtarı da yalnız uyarı VARKEN yazılır (aynı desen).
    if verim_uyari:
        stats["verim_uyari"] = True
    # beyaz-fon geçişi yalnız GEÇİŞ VARKEN yazılır (aynı desen; normal
    # koşunun stats.json'u anahtar eklenmeden aynı kalır).
    if det is not None and det.get("beyaz_fon"):
        stats["beyaz_fon_gecis"] = True
    (out.with_suffix(".stats.json")).write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    istatistik: {out.with_suffix('.stats.json')}")

    # --- 06.10.2026: meta yan-dosyası (video-SRT eşleşme kilidi) ---
    # Her başarılı koşumda SRT'nin yanına <srt adı>.hardsub2srt.json yazılır
    # (stats.json'un AYRI bir kopyası; stats yazımı yukarıda, BOZULMADI).
    # Amaç: SRT tek başına taşındığında bile hangi video koşumundan ve hangi
    # parametrelerle üretildiği dosyayla seyahat etsin (E02 vakası,
    # _video_kimlik docstring'i). SRT'nin İÇİNE comment YAZILMAZ — SRT
    # formatında comment yok, oyuncular bozulur. Video-hash alanları
    # stats'tan KOPYALANIR, yeniden hesaplanmaz.
    meta_yol = out.with_name(out.stem + ".hardsub2srt.json")
    meta = {"arac": ARAC_SURUM,
            "kosum_zamani": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "video": stats["video"],
            "parametreler": {"white_thr": cfg["white_thr"],
                             "mask": cfg["mask"],
                             "tophat_thr": cfg["tophat_thr"],
                             "bant": [cfg["band_y"], cfg["band_h"]],
                             "diller": lang_list},
            "blok": stats["blocks"],
            "konusma_sn": stats["speech_seconds"],
            "low_conf_orani": (round(low / len(ana), 3) if ana else 0.0),
            "hardsub_shr": stats["hardsub_shr"]}
    meta_yol.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    meta: {meta_yol}")


if __name__ == "__main__":
    main()
