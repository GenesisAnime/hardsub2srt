# Kullanım rehberi

## Altyazı akışını anlama

- **Hardsub:** yazı video karelerine işlenmiştir. ffprobe gömülü bir altyazı akışı bulmazsa araç OCR ile görünen metni okur.
- **Softsub:** ffprobe gömülü bir altyazı akışı bulursa araç OCR yapmadan ilk altyazı akışını FFmpeg ile SRT’ye dönüştürmeyi dener. Geçerli sonuç codec/container desteğine bağlıdır. CLI’de akış seçme veya otomatik çeviri yoktur.

Her iki yol da çeviri yapmaz. Hardsub’da OCR, görüntüde okunan metni üretir; softsub’da mevcut altyazı metni çıkarılır. Dil değiştirmek istiyorsanız ayrı bir çeviri iş akışı gerekir.

## CLI örnekleri

```powershell
# Standart OCR
py -3 hardsub2srt.py "D:\Videolar\bolum.mkv" -o "D:\Altyazilar\bolum.srt"

# OCR'ı CPU'da çalıştır
py -3 hardsub2srt.py "D:\Videolar\bolum.mkv" -o "D:\Altyazilar\bolum.srt" --cpu

# Sınırlı ilk deneme; sınırlı CLI koşusunda QA montajı varsayılan olarak kapalıdır
py -3 hardsub2srt.py "D:\Videolar\bolum.mkv" -o "D:\Altyazilar\deneme.srt" --limit-seconds 60

# Tam koşuda altı QA örneği üret; QA istemiyorsanız --qa 0 verin
py -3 hardsub2srt.py "D:\Videolar\bolum.mkv" -o "D:\Altyazilar\bolum.srt" --qa 6

# Hardsub OCR için yerel insan inceleme paketi de üret
py -3 hardsub2srt.py "D:\Videolar\bolum.mkv" -o "D:\Altyazilar\bolum.srt" --review-pack
```

CLI tam koşusunda `--qa` verilmezse altı örnek seçilir; `--limit-seconds` ile sınırlı koşuda varsayılan QA kapalıdır. `--qa 0` her iki durumda da montajı kapatır. UI’de QA sayısı ayrı ayardır; varsayılan 4, 0 kapalıdır.

`--nvdec` yalnızca FFmpeg’in NVDEC donanım video decode yolunu dener. Bu, OCR modelinin PyTorch CUDA aygıtı seçimi değildir. NVDEC varsayılan olarak kapalıdır; başarısız olursa decode CPU’ya döner.

Üst bant metnini ayrı `_ust.srt` dosyasına almak için `--ust-bant` kullanılır. `--ust-ana` üst bant cue’larını ana SRT’ye ekler; bu seçenek yalnız video davranışını doğruladığınızda kullanılmalıdır. Tüm seçenekler için `py -3 hardsub2srt.py --help` çıktısına bakın.

## Yerel web arayüzü

`arayuz.bat` başlatıcısı veya `py -3 ui_server.py` komutu yerel Flask arayüzünü `http://127.0.0.1:8765` adresinde açar. Birden çok video seçilebilir; her iş kendi yeni `runs/video-<ad>/<zaman>_<iş-kimliği>/` klasörüne yazar. Yeni koşular önceki koşu klasörlerini ezmez.

Kuyruk tek işçiyle sırayla çalışır ve bellektedir; sunucu yeniden başlatıldığında bekleyen işler geri yüklenmez. Windows dosya/klasör seçicileri Windows’a özgüdür; diğer sistemlerde yolu elle girin.

## Çıktılar ve yeniden çalıştırma

CLI’de `-o` SRT hedefidir. Hardsub koşusu ana SRT ve `_ekran.srt` ile `.stats.json`, `.hardsub2srt.json` ve blok/takas gibi aynı gövdeye bağlı JSON yan dosyaları yazabilir. Mevcut aynı adlı CLI çıktılarını tekrar çalıştırmak üzerine yazar; gerekli dosyaları önceden yedekleyin veya farklı bir hedef kullanın.

`--review-pack` ayrıca `<SRT-gövdesi>.review-pack/` klasörü üretir; bu klasör zaten varsa CLI durur ve paketin üzerine yazmaz. Softsub doğrudan çıkarımında OCR kare-cue eşleşmesi olmadığından review-pack oluşturulmaz. Paket, SRT metni ve altyazı kare kırpımları içerebilir. AI sohbet paketi dışarı elle gönderilirse içerik seçtiğiniz sağlayıcıya gider; uygulama kendisi sağlayıcı API’si çağırmaz.

İnceleme/öğrenme akışları için [İnceleme paketi](REVIEW-BUNDLE.md) [EN], [Manuel AI OCR görsel incelemesi](AI-OCR-GORSEL-INCELEME.md) [TR] ve [Seed içe aktarma](SEED-IMPORT.md) [EN] belgelerine bakın.
