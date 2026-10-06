# Nasıl ve neden — karar kayıtları

Projede verilen önemli kararların kaydı: hangi durumda, hangi seçenekleri
görerek, neyi seçtik ve nasıl anladık ki doğru seçmişiz. Yeni kararlar da
aynı formatta buraya eklenir.

## 1. Topluluktan altyazı metni değil, hata şekli toplanır

- **Durum:** Açık kaynağa açılırken kullanıcıların verisiyle programı
  geliştirme fikri vardı. Programın en değerli verisi altyazı metni — ama o,
  lisanslı çeviridir.
- **Seçenekler:** (a) metni topla, riski üstlen; (b) veri toplamayı bırak;
  (c) metni değil, hataların şeklini topla.
- **Karar:** (c). Toplanan: OCR karışım istatistikleri, kelime düzeyi sözlük
  girişleri, video-hash'li koşum istatistikleri, açık onayla kırpılmış bant karesi.
- **Neden yeterli:** %100'e götürecek bilgi zaten hataların şekli — hangi
  kombinasyonun hangi durumda bozulduğu. Metnin kendisi yalnız telif riski
  taşır, öğrenme açısından ekstra bilgi vermez.
- **Nasıl göreceğiz:** birleştirici birleştirilmiş sözlük sürümlerinde
  regresyon CER'i düşmeye devam ediyorsa model çalışıyor demektir.

## 2. Öğrenme paketi Issue ekiyle gelir, PR değil

- **Durum:** paketi nasıl toplarız?
- **Karar:** program "öğrenme paketi dışa aktar" düğmesiyle anonim JSON
  üretir; kullanıcı GitHub Issue'ya ekler. PR yalnız teknik kullanıcılara
  açık (sözlük satırı düzenleyip gönderebilirler).
- **Neden:** .exe ile çalışacak insan PR açamaz; dosya eklemek herkesin
  yapabildiği şey. Bu seçim katkı hacmini teknik olmayanlardan da alır.

## 3. Sözlük birleştirmede oy kuralı

- **Durum:** iki kullanıcı aynı hataya farklı düzeltme derse ne olur?
- **Karar:** ≥2 bağımsız kullanıcı aynı düzeltmeyi verdiyse otomatik kabul;
  tek oy "aday" havuzunda insan onayı bekler.
- **Neden:** tek kişilik hata sinyali zayıf; çoğunluk gören hatanın gerçek
  olma olasılığı yüksek. İnsan onayı tek katman değil, yalnız belirsiz
  vakalarda devrede.

## 4. Video-SRT eşleşme bilgisi SRT'nin içine değil, yan dosyaya

- **Durum:** bir SRT hangi videodan çıktı sorusu zamanla bozulan eşleşmeler
  üretiyordu (bir bölümün videosu sonradan değişmişti).
- **Seçenekler:** (a) SRT içine comment — SRT formatında comment yoktur,
  yazılırsa oynatıcılar bozulur; (b) ayrı yan dosya.
- **Karar:** (b). Her koşum `<srt>.hardsub2srt.json` yazar: video hash'i
  (sha1 ilk 1 MB + mtime + boyut), parametreler, blok istatistiği, araç sürümü.
- **Nasıl göreceğiz:** her koşumda meta dosyası stats ile çapraz kontrol
  ediliyor; E1074 kısa koşumunda birebir eşleşme doğrulandı.

## 5. Apostrof yutması: genel patern kuralı değil, birebir tablo

- **Durum:** OCR bazı kelimelerde apostrofı yutuyor ("Fil'in" → "FilFin").
  Düzeltme katmanına genel bir kural konabilir miydi?
- **Karar:** hayır — sabit tablo (`FilFin → Fil'in`), yeni vakalar ölçülünce
  satır satır eklenir.
- **Neden:** genel kural (kelime-içi büyük harf sınırına apostrof ekle) bu
  hattın ölçülmüş çöp imzasına ("OurpıIse", conf 0,81) aynı sinyalle dokunur —
  çöpü "onarır" gibi görünürdü. Ayrıca Türkçede apostrof yalnız özel addan
  sonra geçerli; özel-ad bilgisi olmadan güvenli kural yazılamaz. Birebir
  tablo yapısal olarak sıfır yanlış-pozitif üretir.
- **Nasıl göreceğiz:** birim testleri çelme örnekleriyle (bilginin, Nami'nin,
  iPhone...) değişmediğini doğruladı; tablo girişleri `.duzeltme-gunlugu.json`
  ve stats'a "apostrof" sınıfıyla yazılır — sessiz davranış değişikliği yok.

## 6. Toplu koşumda done-listesi

- **Durum:** klasördeki her video sırayla koşuluyordu; bir kere koşulmuş
  videolar tekrar koşuluyor, başka dizinin videoları yanlışlıkla işlenmişti.
- **Karar:** `kosuldu.list` (ad|boyut) — koşum sonrası ekle, başta kontrol
  et, `--yeni` ile bypass.
- **Nasıl göreceğiz:** bileşen testleri 4/4 (exact eşleşme, boyut farkı reddi,
  özel karakter güvenliği, format).

## 7. Regresyon seti videodan değil, sabit kare PNG'den beslenir

- **Durum:** kod değişikliğinin doğruluğu bozup bozmadığını hızlı görmek
  için kıyas seti gerekiyordu.
- **Karar:** 12 nokta (rapordaki en sert vakalar + düşük riskli kontroller)
  ffmpeg ile tek kareye dondurulur; OCR girdisi video değil PNG.
- **Neden:** bir bölümün videosu sonradan değişmişti — girdi değişince
  "kod kötüleşti" ölçümü anlamsızlaşır. Sabit PNG, kıyas geçerliliğini video
  değişiminden bağımsız yapar. Bant koordinatları da dondurulur; bant algılama
  katmanı ayrıca `--bant-yenile` çıktısıyla izlenir.
- **Kural:** baseline ile kıyas **aynı cihazda** koşulmalı — cihaz farkı
  conf skorlarını minik değiştirir, sahte kötüleşme aları üretir.

## 8. Canlı klasör kanon, geliştirme kopyasına senkron betiğiyle gider

- **Durum:** araç iki yerde tutuluyor (günlük kullanım klasörü + geliştirme
  kopyası). İki kopya çürümüştü: `ogren.py` geliştirme kopyasına hiç
  senkronlanmamış, sözlük 4 girişten geriydi.
- **Karar:** günlük kullanılan klasör kanon; tek komutla hash öncesi/sonrası
  teyitli senkron. README yeniden yazılmadan önce otomatik yedek alınır.
- **Nasıl göreceğiz:** senkron betiği 7/7 hash teyidi yazıyor; çelişki varsa
  exit 1.

## 9. GPU varsayılan, `--cpu` bayrağı kalır

- **Durum:** geliştirme boyunca GPU'lu makinede koşuldu; ölçüm testleri bazen
  CPU'ya kaçtı (GPU toplu koşumda meşgulken).
- **Karar:** aracın varsayılanı GPU kalır; `--cpu` bilinçli bir kaçış kapısı
  olarak durur (GPU'suz kullanıcılar için). Ölçüm betiğinin varsayılanı da
  GPU'ya çevrilecek — ama ancak baseline GPU'yla yeniden üretildikten sonra,
  çünkü kıyas aynı cihazda yapılmak zorunda.
- **Neden:** kullanıcı tercihi; ve ölçüm disiplini: varsayılanı değiştirmek
  için önce aynı koşullarda ölçüm gerekiyor.

## 10. Kare PNG'leri repoya girmez

- **Durum:** regresyon seti anime karesi içeriyor.
- **Karar:** kareler yerel kalır, `.gitignore`'da; repoya yalnız betik,
  nokta tanımları, baseline JSON ve kullanım notu girer. Kareyi isteyen
  `--kare-cikar` ile kendi videolarından üretir.
- **Neden:** telif — repo telifli görüntü dağıtmaz (bkz. karar 1).
