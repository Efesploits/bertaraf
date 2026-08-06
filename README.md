# M3sel Bertaraf

GoodbyeDPI mantığıyla çalışan, **pencereli** (CMD değil) DPI sansür bertaraf aracı.
Türkiye'de Discord'a bağlanamama / mesaj gitmeme / ses kanalına girememe sorunları için yazıldı.

![tip](https://img.shields.io/badge/platform-Windows-blue) ![tip](https://img.shields.io/badge/y%C3%B6netici-gerekli-red)

---

## Kurulum

**[→ Son sürümü indir](https://github.com/Efesploits/bertaraf/releases/latest)**

Sürümler sayfasından **`M3sel-Bertaraf-Kurulum.exe`** dosyasını indir ve çalıştır.
Tek dosya, Python gerektirmez.

Kurulum sihirbazı programı `Program Files` altına kurar, masaüstü/Başlat menüsü
kısayollarını oluşturur, isteğe bağlı olarak Windows açılışında otomatik başlatır
(yönetici yetkili zamanlanmış görev olarak) ve Denetim Masası > Program Ekle/Kaldır
listesine ekler. Kaldırmak için oradan kaldır ya da kurulum klasöründeki
`Kaldir.exe`'yi çalıştır.

Kurulum istemiyorsan **`M3sel-Bertaraf-tasinabilir.zip`** dosyasını indir, aç, içindeki
`M3sel Bertaraf.exe`'ye çift tıkla.

Kullanım:

1. **`M3sel Bertaraf.exe`**'yi aç.
2. Windows UAC uyarısı çıkar → **Evet** de. (Ağ sürücüsü için zorunlu, seçenek yok.)
3. Mor **BAŞLAT** düğmesine bas.
4. Discord'u aç. Log penceresinde `discord.com -> bertaraf edildi` satırlarını göreceksin.

Program açıkken çalışır; kapatınca engeller geri gelir. Sürekli açık kalsın istiyorsan
**"Açılışta otomatik başlat"** kutusunu işaretle, bir dahaki açılışında kendi başlar.

---

## Ne yapıyor?

Sansür donanımı (DPI), TLS el sıkışmasının ilk paketindeki **SNI** alanından hangi siteye
gittiğini okuyup bağlantıyı kesiyor. Program bunu şöyle engelliyor:

| Aşama | Yapılan iş |
|---|---|
| **1. Yakalama** | WinDivert sürücüsüyle sadece TLS `ClientHello` ve HTTP istek paketleri yakalanır. Normal veri trafiği çekirdekte kalır, hız düşmez. |
| **2. Bölme** | Alan adı (`discord.com`) tam ortasından ikiye bölünüp iki ayrı TCP segmenti olarak gönderilir. DPI alan adını hiçbir parçada bütün göremez. |
| **3. Ters sıra** | İkinci parça önce gönderilir. DPI paketleri birleştirmez, sunucu birleştirir. |
| **4. Sahte paket** | Önden `www.microsoft.com` SNI'li, geçersiz sıra numaralı sahte bir `ClientHello` atılır. DPI onu kaydeder, sunucu pencere dışı olduğu için sessizce atar. |
| **5. QUIC engeli** | UDP/443 düşürülür, uygulamalar TCP+TLS'e döner (orayı zaten bertaraf ediyoruz). |
| **6. DNS düzeltme** | Discord alan adlarının gerçek adresleri DNS-over-HTTPS ile alınır; operatörün DNS sorgusuna **biz** doğru cevabı veririz. Operatör 53. portu şeffaf olarak kendine yönlendirse bile zehirli cevap işe yaramaz. |

**Yanılma yöntemi** (sahte paketin sunucuya ulaşmaması için):

| Yöntem | Nasıl |
|---|---|
| **badsum** (varsayılan) | TCP sağlama toplamı kasten bozulur. Sunucu paketi atar; DPI'ların çoğu sağlama toplamı kontrol etmez. |
| **badseq** | Sıra numarası pencere dışına alınır, sunucu yok sayar. |
| **ttl** | Düşük TTL ile gönderilir, sunucuya varmadan yolda ölür. Mesafe tahmini gerektirir. |

Sunucuya giden veri **hiç değişmez** — sadece paketlere bölünme şekli değişir.
Şifre çözülmez, trafik başka yere gitmez, proxy/VPN yoktur.

---

## Güncelleme

**Güncelle** düğmesi GitHub Releases'teki en son sürümü kontrol eder. Program açılışta
da sessizce bakar — yeni sürüm yoksa hiçbir şey söylemez.

Yeni sürüm varsa sürüm notlarını ve indirme boyutunu gösterir, onaylarsan kurulum
dosyasını indirip çalıştırır ve kendini kapatır. Kurulum eski sürümün üstüne yazar,
ayarların korunur. İndirme adresi yalnızca `github.com` / `githubusercontent.com`
altındaysa kabul edilir.

Yeni sürüm çıkarmak için: sürüm numarasını `core.py` içindeki `APP_VERSION` alanında
yükselt, commit'le, `git tag v1.1 && git push origin v1.1` yap. Actions gerisini halleder.

## Yöntemler

| Yöntem | Ne zaman |
|---|---|
| **Discord (Önerilen)** | Varsayılan. Sahte paket + ters bölme. Çoğu operatörde çalışan kombinasyon. |
| **Agresif** | Sahte paket + düz bölme. Ters sıra sorun çıkarırsa bunu dene. |
| **Ters bölme** | Sahte paketsiz. Agresif filtreleme yapan operatörlerde bazen daha stabil. |
| **Hafif** | Sadece bölme. En az müdahale, en hızlı; hafif engellemelerde yeter. |

**Çalışmazsa sırayla:** Discord → Agresif → Ters bölme → Hafif.
Her denemede Discord'u tamamen kapatıp (sistem tepsisinden de çık) tekrar aç.

---

## Ayarlar

- **HTTP (80) trafiğini de işle** — düz HTTP siteleri için. Açık kalabilir.
- **QUIC engelle** — Discord/tarayıcı UDP yerine TCP kullanır. Kapatırsan bazı sitelerde bypass atlanır.
- **DNS'i yönlendir** — operatör DNS'i devre dışı. Kurumsal ağ / iş bilgisayarındaysan kapat.
- **Sadece engelli site listesi** — sadece `core.py` içindeki listeye dokunur, diğer trafiğe hiç karışmaz. Bankacılık vb. hassas sitelerde sorun yaşarsan bunu aç.
- **Ayrıntılı log** — atlanan paketleri, QUIC/DNS sayaçlarını da yazar.

---

## Sorun giderme

**"WinDivert sürücüsü açılamadı — Erişim engellendi"**
Yönetici olarak çalıştırılmamış. `.exe`'ye sağ tık → *Yönetici olarak çalıştır*.

**"WinDivert sürücüsü açılamadı" ama yöneticisin**
Aynı anda GoodbyeDPI / zapret / ByeDPI açıktır. Onları kapat. Görev Yöneticisi'nden
`goodbyedpi.exe`, `winws.exe` süreçlerini bitir.

**Antivirüs siliyor**
WinDivert imzalı bir ağ sürücüsü ama sansür bypass araçlarında kullanıldığı için bazı
antivirüsler "riskware" etiketler. Klasörü istisna listesine ekleyin.

**Başlattım, `bertaraf edildi` yazıyor ama Discord hâlâ açılmıyor**

Bu satır sadece paketi böldüğümüzü söyler — engel DNS veya IP katmanındaysa bölmek
hiçbir işe yaramaz. Önce **hangi katmanda engellendiğini ölç**:

1. Motor **kapalıyken** **Teşhis** düğmesine bas, sonucu oku.
2. **BAŞLAT**'a bas, Teşhis'i tekrarla. İkisini karşılaştır.

Teşhis dört sonuçtan birini verir:

| Sonuç | Anlamı | Ne yapmalı |
|---|---|---|
| **DNS engeli** | Sistem DNS'i, DoH'un verdiğinden farklı adres döndürüyor (ya da hiç çözemiyor) | **DNS'i DoH ile düzelt** açık olmalı. Açıp motoru yeniden başlat, Teşhis'i tekrarla. |
| **SNI/DPI engeli** | Aynı IP'ye zararsız SNI ile bağlanılıyor, gerçek SNI ile bağlanılmıyor | Programın çözmesi gereken durum bu. Motor açıkken hâlâ çıkıyorsa **Yöntem** ve **Yanılma** kombinasyonlarını sırayla dene. |
| **IP engeli** | TCP bağlantısı hiç kurulmuyor | Paket bölme bunu aşamaz. VPN gerekir. |
| **Erişim var** | Hiçbir katmanda engel yok | Sorun programda değil: Discord'u sistem tepsisinden tamamen kapat, `%appdata%\discord\Cache` klasörünü sil, tekrar aç. |

Deneme sırası (her denemede Discord'u tepsiden tamamen kapatıp aç):

```
Discord + badsum  →  Discord + badseq  →  Discord + ttl
Agresif + badsum  →  Agresif + badseq
Ters bölme        →  Hafif
```

**İnternet yavaşladı**
"Sadece engelli site listesi"ni aç. Böylece yalnızca listedeki alan adlarına dokunulur.

---

## Kaynak koddan çalıştırma

```bash
pip install pydivert
python m3sel_bertaraf.py
```

Yönetici yetkisi yoksa program kendini UAC ile yeniden başlatır.

### Testler

```bash
python test_core.py
python test_engine.py
```

`test_engine.py` sürücü açmadan, sahte paketler üzerinde bölme/sıra numarası/sahte paket
mantığını doğrular — yönetici yetkisi gerektirmez.

### Otomatik derleme

`v` ile başlayan bir etiket push edildiğinde GitHub Actions programı ve kurulum
dosyasını Windows üzerinde derleyip Releases'e yükler:

```bash
git tag v1.0 && git push origin v1.0
```

Actions sekmesinden elle de tetiklenebilir (*Kurulum dosyası oluştur* → *Run workflow*);
o durumda çıktılar sürüm yerine artifact olarak kalır.

### Yeniden derleme

```bash
python make_icon.py
python -m PyInstaller --noconfirm --clean --windowed --uac-admin --name "M3sel Bertaraf" --icon m3sel.ico --collect-all pydivert m3sel_bertaraf.py
```

---

## Dosyalar

| Dosya | İş |
|---|---|
| `m3sel_bertaraf.py` | Arayüz (Tkinter): log paneli, ayarlar, sayaçlar, UAC yükseltme |
| `core.py` | Motor: TLS/HTTP ayrıştırma, paket bölme, sahte paket, QUIC/DNS |
| `test_core.py` | Ayrıştırma testleri |
| `test_engine.py` | Paket işleme testleri (sahte WinDivert tutamağıyla) |
| `installer.py` | Kurulum sihirbazı: dosya açma, kısayol, kayıt defteri, kaldırma |
| `guncelle.py` | Güncelleme kontrolü: Releases'ten sürüm arar, kurulumu indirir |
| `make_icon.py` | Simge üretici |
| `.github/workflows/build.yml` | Etiket push'unda derleyip Releases'e yükler |

---

## Yasal not

Bu araç trafiği şifrelemez, gizlemez veya başka bir sunucuya yönlendirmez; yalnızca kendi
paketlerinizin parçalanma biçimini değiştirir. Kendi bağlantınız üzerinde ve kendi
sorumluluğunuzda kullanın.
