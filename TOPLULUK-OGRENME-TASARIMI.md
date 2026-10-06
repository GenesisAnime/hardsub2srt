# Topluluk öğrenme döngüsü — tasarım notu

Bu proje açık kaynağa açılırken aklımızdaki model şu: insanlar programı kendi
bilgisayarlarında koşturur, altyazılar iyileşir ama bir noktada herkes tıkanır —
çünkü her bilgisayarda farklı font, farklı rip, farklı zor kare var. Güzelliği
şurada: birbirimizin tıkandığı yerler farklı. Birinin tıkandığı kare, diğerinin
düzgün çıktığı kare olabilir; hataları paylaştıkça programın hiç görmediği
vakaları öğrenme şansı doğuyor.

Bu not o döngünün nasıl kurulacağını anlatır; kod yazılmadan önceki iş planıdır.

## Önce telif meselesi

Bir yerde durmamız lazım: altyazı metnini buraya toplayamayız. Kullanıcının
çıkardığı .srt lisanslı çeviridir; repoya giren her replik telif sorunu demek.
O yüzden toplanacak şey metnin kendisi değil, hataların şekli:

- OCR karışım istatistikleri — "rn"nin "n"e, "i"nin "ı"ya dönmesi gibi, hangi
  durumda kaç kez
- kelime düzeyi düzeltme girişleri ("FilFin → Fil'in" gibi)
- hangi videoda (hash), hangi ayarlarla kaç blok çıktığı
- gerektiğinde, açık onay istenerek, tek bir kırpılmış altyazı bandı karesi

Bunlar telifli değil ve %100'e götürecek şey tam olarak bunlar.

## Döngü

1. Program zaten her koşumda video hash'ini ve istatistikleri yan dosyaya
   yazıyor; temeli hazır.
2. Arayüze "öğrenme paketi dışa aktar" düğmesi gelecek: tek tıkla anonim bir
   .json üretir. İçerik kaydedilmeden önce önizlenir, onay verilir; yol
   bilgisi ve kimlik yok.
3. Bu dosya GitHub'da bir Issue'ya eklenir. PR bilinçli seçilmedi: .exe ile
   gelen insan PR açamaz, dosya ekleyebilir. PR'ı teknik kullanıcılar için
   açık bırakıyoruz (sözlüğe satır ekleyip göndermek gibi).
4. Geliştirmede birleştirici bir betik paketleri okur: iki farklı insan aynı
   hatayı aynı şekilde düzelttiyse otomatik kabul; tek oy kaldıysa aday
   havuzunda bekler, insan karar verir.
5. Sonuç yeni sözlük dosyasına girer, sonraki sürümle herkese dağılır.
   Döngü böyle kapanır.

## Ölçüm

"İyileşti" demeyi söylemek kolay, ölçmek zor. Her sürümde regresyon seti
koşacak ve CER sayısı sürüm notlarında yayınlanacak. Kullanıcı düzeltmeleri
eklendikçe bu sayı düşmeli; düşmüyorsa değişiklik işe yaramamış demektir.
Hedef %100'e yaklaşmak — ama bunu iddia olarak değil, sürüm sürüm yayınlanan
ölçümle göstereceğiz.

## .exe

Tek dosyalık bir .exe hedefleniyor. CPU modeli varsayılan olsun ki GPU'su
olmayan da çalışabilsin; GPU opsiyonel kalsın. Sözlük dosyasını exe'den ayrı
dağıtacağız ki küçük düzeltmeler sürüm beklemeden ulaşsın. PaddleOCR ve GPU
bağımlılıkları paketi epey büyütüyor — bu ayrı bir mühendislik işi, ilk sürüm
için CPU modeli yeterli olabilir.

## Açık sorular

- İlk sürümün kapsamı: yalnız araç mı, arayüz ve betikleriyle birlikte mi.
- Zor kare paylaşımı varsayılan açık mı kapalı mı — önerimiz kapalı; paylaşmak
  isteyen açıkça isteyerek paylaşsın.
