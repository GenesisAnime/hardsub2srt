# Geliştirme günlüğü

Bu dosya projenin ne zaman, ne için hangi adımı attığını ve her adımın
ölçümünü kaydeder. "Çalışıyor" demeden önce neyle ölçtüğümüzü de yazıyoruz —
ileride aynı tartışmaya dönmemek için. Kararların tam gerekçesi
[NASIL-VE-NEDEN.md](NASIL-VE-NEDEN.md)'de, tuzaklar [OGRENMELER.md](OGRENMELER.md)'de.

## 2026-09-27 — 05.10 · İlk 14 tur: araç sıfırdan öğrenen bir sisteme dönüştü

- **Çıkarma motoru**: hardsub videodan Türkçe SRT çıkarımı kuruldu. Otomatik
  altyazı bandı algılama, ana OCR motoru + ikinci görüş zinciri (RapidOCR /
  PP-OCRv6, paddle kurulumu gerektirmeden), gürültü ayrımı (`_ekran.srt`),
  Türkçe düzeltme katmanı (`--duzelt`).
- **Öğrenme döngüsü tamamlandı**: `ogren.py` (güvenli çift sınıflandırıcı,
  zehirlenme testi 7/7 geçti), arayüzde "Kaydet = öğren", kalite panosu.
- **Wano arızaları → kök nedenler → kod**: 5 bölümün incelenmesi dört düzeltmeyi
  kod taşıdı (+177/−19): eşik tavanı 246→240, dar bant tetiği 150→180, beyaz-fon
  korumasına örnekleme penceresine video-sonu eklenmesi, stats'a video-hash.
- **Ölçümler** (hepsi GT ya da koşum istatistiğiyle):
  - E1049: 17→59 blok, 0,71→2,47 blok/dk — çözüldü.
  - E1075: konuşma 127→786 sn, 74→293 blok — kurtarıldı.
  - E02 (GT kıyas): CER %0,48, recall %100.
  - BLEND-S (stilize font): CER %1,49.
- Kalan 22'lik yeniden koşum kümesi ve birkaç açık soruyla tur kapatıldı.

## 06.10 — E1074 dersi, 20 bölümün toplu koşumu, altyapı

- **E1074 tam koşumu alarma ölüm teyidi getirdi**: önceki turda 240 sn'lik kısmi
  koşum "altyazısız rip" alarmı vermişti; tam koşum (23,85 dk) 47 blok, 78,9 sn
  konuşma, 1,97 blok/dk üretti, alarm tetiklenmedi. Kısmi koşumdaki pencere
  (8,5 sn konuşma < 30 sn eşiği) yapısal yanlış-pozitifti. Ders: alarm diyeceksen
  tam pencere ölç, kısmi koşumda alarmı yalnız uyarı yap.
- **Yeniden koşum kümesi netleşti**: rapordaki "E1049-E1080: 22 bölüm" ifadesini
  belirti kümelerinden birleştirince 23 aday çıktı; E1049/E1074/E1075 yeni kodla
  zaten koşulduğu için **20 bölüm** kalıp toplu koşuma girdi (koşum başına kare
  onayı + stats ölçümü + şüphelilere ikinci tur).
- **İki kopya senkronu kuruldu**: araç klasörü ile geliştirme kopyası SHA256 ile
  karşılaştırıldı; `ogren.py` geliştirme kopyasına hiç senkronlanmamıştı, sözlük
  4 girişten geriydi. Tek komutluk senkron betiği yazıldı, 7/7 hash teyitli
  (`0c4ca0a`).
- **`toplu.bat`'a done-listesi**: işlenen videolar `kosuldu.list`'e yazılıyor,
  listedekiler atlanıyor (`--yeni` ile bypass). Bileşen testi 4/4: exact
  eşleşme, farklı boyut reddi, `&`'li dosya adı güvenliği, liste formatı
  (`44971de`).
- **Üç kod düzeltmesi** (`317d659`):
  - apostrof-yutma tablosu ("FilFin → Fil'in") — birebir eşleşme, yanlış-pozitif
    karşısında genel patern kuralının ölçülmüş çöp imzasına dokunduğu gerekçesiyle
    tablo seçildi;
  - beyaz-fon pencere "sınır-1" incelemesi — kod değişmedi, ızgara matematiği
    pencere içinde kaldığını kanıtladı;
  - meta yan-dosyası `<srt>.hardsub2srt.json` — video-hash + parametre + blok
    istatistiği SRT'yle seyahat eder (video-SRT eşleşme kilidi).
- **Regresyon seti kuruldu** (`6f42c45`): rapordaki en sert vakalardan
  seçilmiş 12 nokta / 11 bölüm, kareler ffmpeg ile sabitlendi, baseline 12/12
  noktada üretildi (~2 dk, CPU), kıyas modu iki yönde test edildi (sentetik
  sapmada doğru alarm, kendisiyle birebirde "DEĞİŞİKLİK YOK"). Raporun
  (952,128) gibi sayılarının sn/kare değil **piksel koordinatı** olduğu teyit edildi.
- **8 yanlış klasör SRT'si arşive taşındı** (başka dizinin SRT'leri çıktı
  klasörüne karışmıştı; videolar zaten ayrılmıştı — done-listesi tekrarını engeller).

## 06-07.10 — GitHub'a açılış ve topluluk tasarımı

- **Repo açıldı**: `GenesisAnime/hardsub2srt` — public, MIT, topics'li. Repo
  içeriği stabil sürümden (commit `317d659`) kuruldu; kişisel yollar temizlendi
  (arayüz örnek yolları genelleştirildi, regresyon betiğinin varsayılan aracı
  yolu repo-köküne çözümlenir hale getirildi); `requirements.txt` import
  teyidiyle yazıldı.
- **Telif çerçevesi dokümanlara işlendi**: repoya altyazı metni ve anime karesi
  girmiyor; paylaşılacak veri istatistik, kelime düzeyi sözlük girişleri ve
  ölçüm verileri. Regresyon kareleri yerel kalır. Kurallar CONTRIBUTING'te.
- **Çok-kullanıcı öğrenme döngüsü tasarlandı** (docs'taki tasarım notu):
  anonim öğrenme paketi → Issue eki → oylamalı birleştirme → sürümle dağıtım.
- **Dil tercihi**: "player" için "oynatıcı", dokümanlarda doğal anlatım —
  hem kod yorumları hem docs buna göre elden geçirildi.

## Devam eden (bu not yazılırken)

- 20 bölümün toplu koşumu (koşum başına kare onayı + stats + şüphelilere
  alternatif parametre ikinci turu).
- Low-conf blok kurtarma turu: conf'u düşük bloklara upscale/kontrast ikinci
  deneme + ikinci motor oyu; kötüleşme yasağı (blok metni asla kötüleşmez) ve
  stats'a `kurtarma` alanı yazılması test ediliyor.

## Sıradaki

- Toplu koşum sonuçlarıyla eski→yeni karşılaştırma tablosu ve resmîleştirme.
- Karışım madenciliği: font profiline göre hangi motor ve ayar kombinasyonu
  daha iyi — 20 bölümün verisi eklenecek.
- Öğrenme paketi dışa aktarma düğmesi + birleştirici betik (topluluk döngüsünün
  ilk kod parçası).
- GPU boşaldığında: regresyon baseline'ı GPU'yla yeniden üretip ölçüm betiğinin
  varsayılanını GPU'ya çevirmek (aynı cihaz kuralı gereği önce baseline).
