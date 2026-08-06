# M3sel Bertaraf

GoodbyeDPI mantığıyla çalışan, **pencereli** (CMD değil) DPI sansür bertaraf aracı.
Türkiye'de Discord'a bağlanamama / mesaj gitmeme / ses kanalına girememe sorunları için yazıldı.

![tip](https://img.shields.io/badge/platform-Windows-blue) ![tip](https://img.shields.io/badge/y%C3%B6netici-gerekli-red)

---

## Kurulum

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
| **6. DNS** | DNS sorguları operatör sunucusu yerine 1.1.1.1'e yönlendirilir (DNS üzerinden engellemeye karşı). |

Sunucuya giden veri **hiç değişmez** — sadece paketlere bölünme şekli değişir.
Şifre çözülmez, trafik başka yere gitmez, proxy/VPN yoktur.

---

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

**Başlattım, log akıyor ama Discord hâlâ açılmıyor**
1. Yöntemi değiştir (yukarıdaki sıra).
2. **Bağlantı Testi** düğmesine bas — üç adres de erişilebilir çıkıyorsa sorun DNS/hesap tarafında.
3. Discord'un kendi önbelleği: `%appdata%\discord\Cache` klasörünü sil.

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
| `make_icon.py` | Simge üretici |
| `.github/workflows/build.yml` | Etiket push'unda derleyip Releases'e yükler |

---

## Yasal not

Bu araç trafiği şifrelemez, gizlemez veya başka bir sunucuya yönlendirmez; yalnızca kendi
paketlerinizin parçalanma biçimini değiştirir. Kendi bağlantınız üzerinde ve kendi
sorumluluğunuzda kullanın.
