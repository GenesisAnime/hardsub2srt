# Öğrenmeler — tuzaklar ve dersler

Geliştirme sırasında canlı yakalanan tuzakların kaydı. Her biri ya bir
yanlış ölçümün ya da bir çöküşün dersidir; aynı tuzağa ikinci kez düşmemek
için burada. Formül hep aynı: ne oldu, neden, bundan sonra ne yapılır.

## 1. Kısmi koşum alarmları yalan söyleyebilir

E1074'ün 240 sn'lik kısmi koşumu "altyazısız rip" alarmı verdi: o pencerede
yalnız 8,5 sn konuşma vardı, 30 sn eşiğinin altında. Tam koşum (23,85 dk)
78,9 sn konuşma, 47 blok, 1,97 blok/dk üretti — alarm hiç tetiklenmedi.
Eski kodun tam koşumu 17 blok üretip alarmı yemişti; yeni kod aynı videoda
47 blok çıkarıp alarmı da kapatmıştı.

**Ders:** alarm ölçümünü değerlendirmeden önce pencerenin tam mı kısmi mi
olduğuna bak. Kısmi koşumda alarm yerine uyarı yaz; karar tam koşum ölçümüne
kalsın.

## 2. Ortak repoda commit, yol belirterek açılır

Bir commit'te index'te bekleyen ~400 dosyanın tamamı commit'e karıştı —
mesaj yalnız iki dosyadan bahsediyordu ama `git show --stat` 400 satır
listeliyordu. `git reset --soft HEAD~1` + pathspec'li commit ile tek
dosyaya indirildi; diğer oturumların index durumu korundu.

**Ders:** birden fazla oturumun çalıştığı repoda düz `git commit` index'in
tamamını alır. `git commit -- <yol>` kalıbı zorunlu; commit sonrası
`git show --stat HEAD` ile neyin gittiğini gözle teyit et.

## 3. OneDrive klasöründe redirect'li uzun koşum sessiz ölür

`> log 2>&1` ile OneDrive klasörüne log yazan uzun koşumlar 1-3 dakikada
sessizce exit 1 ile ölüyor (dosya kilit/temizlik davranışı).

**Ders:** koşum logunu `%TEMP%`'e yaz. Kanıt: E1074 tam koşumu TEMP loguyla
5,5 dk sorunsuz koştu; aynı kalıbın OneDrive'a yazanları ölüyordu.

## 4. İki kopya her zaman çürür — hash'e bak, varsayma

"Araç iki yerde birebir aynı" varsayımı ölçümle çürüdü: bir dosya
(birebir beklenirken) geliştirme kopyasına hiç senkronlanmamıştı, sözlük
dosyası canlıda 5 girişe çıkmışken kopyada tek giriş duruyordu.

**Ders:** kopya eşitliğini boyut/mtime ile değil SHA256 ile karşılaştır;
senkronu tek komuta bağla (öncesi/sonrası hash tablosu yazsın) — elle
yapılan senkron unutulur.

## 5. Genel düzeltme kuralı, ölçmediğin çöpe de dokunur

Apostrof yutması için "kelime-içi büyük harf sınırına apostrof ekle" kuralı
kurmak çekiciydi — ama hattın ölçülmüş çöp imzası ("OurpıIse", conf 0,81)
aynı sinyale düşüyordu; kural çöpü sahte kelimeye "onaracaktı".

**Ders:** düzeltme kuralı yazmadan önce düzeltme katmanının mevcut çöp
örneklerini ölç — kuralın onaracağı dize, yanlış onaracağı dizeden daha
kolay görünür. Birebir tablo, ölçülmemiş genellemeden her zaman güvenli
başlangıçtır.

## 6. Cihaz farkı ölçümü bozar — kıyas aynı cihazda

OCR çıktıları GPU ile CPU arasında conf skorlarında minik değişir. Baseline
CPU'da üretilmişken kodu GPU ile koşup kıyaslamak sahte "kötüleşme" aları
üretir.

**Ders:** kıyas koşumuyla baseline'ı aynı cihazda koş; varsayılan cihazı
değiştireceksen önce baseline'ı yeni cihazla yeniden üret. "İki deneme aynı
sonucu veriyorsa ayırt edici değildir" kuralının kardeşi: "iki koşum farklı
koşulda ise kıyas değildir".

## 7. Rapordaki sayının birimi sordurulmalı

Ölçüm raporundaki "(952,128)" sayıları "saniye" ya da "kare numarası"
sanıldı; aslında bantın piksel koordinatıydı (üst kenar y, yükseklik h).
Zaman seçimi yanlış noktaya yapılabilirdi.

**Ders:** kaynaktan sayı alırken birimini kaynak bağlamından teyit et —
tablo başlığı ya da komşu cümle birimi söyler; sönmüyorsa kaynağın o
bölümünü oku.

## 8. Eşzamanlı ajan limiti iş sırası planlatır

Üç ajan aynı anda başlatıldığında ikisi "eşzamanlılık limiti" ile
başlatılmadan düştü.

**Ders:** uzun işleri (koşumlar) arka plana alırken kısa işleri sıraya yaz;
bir ajan bitince bildirimle sıradakini başlat. Düşen ajanın görevini
yeniden başlatmak maliyetsiz (hemen çöker, kota harcamaz).

## 9. Doküman dili de bir ürün

İlk yazılan tasarımda "Hedef:" açılışları, numaralı başlık sıraları, sembollü
uyarı yapıları vardı; okur bunu "AI yazmış" hissiyle okudu.

**Ders:** dokümanda doğal paragraf anlatımı, az sembol; bilgi yoğun tablo
faydalıysa kalır. Teknik terimlerde garip Türkçeleştirme yapma — doğru
Türkçe ("oynatıcı") ya da yaygın İngilizce terim.

## 10. "Çalışıyor" iddiası kanıtsız yazılmaz

Her kabulde aynı kalıp izlendi: iddia → ölçüm → hedefte geri okuma. Örnek:
meta yan-dosyası için 60 sn'lik gerçek koşum yapıldı ve içeriği stats ile
birebir çapraz kontrol edildi; done-listesi için bileşen testleri 4/4
koşuldu; regresyon kıyas modu sentetik sapmayla alarm üretti, kendi
baseline'ıyla "değişiklik yok" dedi.

**Ders:** test koşumunun çıktısını oku, çıkış koduna değil — 200/204
yanıtının içeriği boş olabilir.
