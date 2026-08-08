"""kaldir.py testleri.

Gercek sistemi bozmamak icin sc/schtasks/taskkill cagrilari taklit edilir;
dosya, kisayol ve klasor adimlari gecici klasorde gercekten calistirilir.
"""
import os
import shutil
import tempfile

import kaldir


class FakeRun:
    """run() yerine gecer, cagrilari kaydeder ve istenen cikti/kodu doner."""

    def __init__(self, plan=None):
        self.plan = plan or {}
        self.calls = []

    def __call__(self, args, timeout=30):
        self.calls.append(args)
        key = " ".join(args[:2])
        rc, out = self.plan.get(key, (0, ""))
        import subprocess
        return subprocess.CompletedProcess(args, rc, out, "")


def test_driver_removed():
    fake = FakeRun({"sc query": (0, "RUNNING"), "sc delete": (0, "SUCCESS")})
    orig, kaldir.run = kaldir.run, fake
    try:
        ok, msg = kaldir.step_remove_driver()
    finally:
        kaldir.run = orig
    assert ok, msg
    assert ["sc", "stop", "WinDivert"] in fake.calls, "surucu durdurulmadi"
    assert ["sc", "delete", "WinDivert"] in fake.calls, "surucu kaydi silinmedi"
    assert "silindi" in msg
    print(f"  OK  surucu durduruldu + silindi ({len(fake.calls)} sc cagrisi)")


def test_driver_absent():
    fake = FakeRun({"sc query": (1060, "belirtilen hizmet yok")})
    orig, kaldir.run = kaldir.run, fake
    try:
        ok, msg = kaldir.step_remove_driver()
    finally:
        kaldir.run = orig
    assert ok and "zaten kayitli degildi" in msg, msg
    assert not any(a[1] == "delete" for a in fake.calls), "olmayan servis silinmeye calisildi"
    print("  OK  surucu yokken silme denenmiyor")


def test_driver_pending_reboot():
    fake = FakeRun({"sc query": (0, "RUNNING"),
                    "sc delete": (1072, "[SC] DeleteService FAILED 1072: marked for deletion")})
    orig, kaldir.run = kaldir.run, fake
    try:
        ok, msg = kaldir.step_remove_driver()
    finally:
        kaldir.run = orig
    assert ok and "yeniden baslatmada" in msg, msg
    print("  OK  silinmek uzere isaretlenen surucu dogru raporlaniyor")


def test_driver_failure_reported():
    fake = FakeRun({"sc query": (0, "RUNNING"), "sc delete": (5, "Erisim engellendi")})
    orig, kaldir.run = kaldir.run, fake
    try:
        ok, msg = kaldir.step_remove_driver()
    finally:
        kaldir.run = orig
    assert not ok and "silinemedi" in msg, msg
    print("  OK  silinemeyen surucu hata olarak bildiriliyor")


def test_shortcuts_removed_everywhere():
    tmp = tempfile.mkdtemp()
    yollar = [os.path.join(tmp, f"yer{i}", f"{kaldir.APP_NAME}.lnk") for i in range(4)]
    for p in yollar:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
    orig, kaldir.shortcut_paths = kaldir.shortcut_paths, lambda: yollar + [
        os.path.join(tmp, "olmayan", "x.lnk")]
    try:
        ok, msg = kaldir.step_remove_shortcuts()
    finally:
        kaldir.shortcut_paths = orig
    assert ok and "4 kisayol" in msg, msg
    assert not any(os.path.exists(p) for p in yollar)
    shutil.rmtree(tmp, ignore_errors=True)
    print("  OK  4 farkli konumdaki kisayol silindi, olmayan atlandi")


def test_shortcut_locations_cover_user_and_public():
    yollar = [p.lower() for p in kaldir.shortcut_paths()]
    assert any("public" in p and "desktop" in p for p in yollar), "ortak masaustu yok"
    assert any("programdata" in p and "start menu" in p for p in yollar), "ortak baslat menusu yok"
    assert any("appdata" in p and "start menu" in p for p in yollar), "kullanici baslat menusu yok"
    assert any("onedrive" in p for p in yollar), "OneDrive masaustu yok"
    print(f"  OK  {len(yollar)} kisayol konumu taraniyor (ortak + kullanici + OneDrive)")


def test_config_removed():
    tmp = tempfile.mkdtemp()
    cfg = os.path.join(tmp, kaldir.CONFIG_DIRNAME)
    os.makedirs(cfg)
    open(os.path.join(cfg, "ayarlar.json"), "w").write("{}")
    eski = os.environ.get("APPDATA")
    os.environ["APPDATA"] = tmp
    try:
        ok, msg = kaldir.step_remove_config()
    finally:
        if eski:
            os.environ["APPDATA"] = eski
    assert ok and not os.path.exists(cfg), msg
    shutil.rmtree(tmp, ignore_errors=True)
    print("  OK  ayar klasoru silindi")


def test_files_removed():
    tmp = tempfile.mkdtemp()
    hedef = os.path.join(tmp, "M3sel Bertaraf")
    os.makedirs(os.path.join(hedef, "_internal", "pydivert"))
    for p in ("M3sel Bertaraf.exe", "_internal/x.dll", "_internal/pydivert/WinDivert64.sys"):
        open(os.path.join(hedef, p.replace("/", os.sep)), "w").close()
    ok, msg = kaldir.step_remove_files(hedef)
    assert ok and not os.path.exists(hedef), msg
    shutil.rmtree(tmp, ignore_errors=True)
    print("  OK  kurulum klasoru alt klasorleriyle silindi")


def test_temp_leftovers_removed():
    tmp = tempfile.gettempdir()
    isimler = ["M3sel-Bertaraf-Kurulum-v9.9.exe", "m3sel-kaldir-9999.exe"]
    for n in isimler:
        open(os.path.join(tmp, n), "w").close()
    baska = os.path.join(tmp, "onemli-baska-dosya.exe")
    open(baska, "w").close()
    try:
        ok, msg = kaldir.step_remove_temp()
        assert ok and "2 gecici dosya" in msg, msg
        assert all(not os.path.exists(os.path.join(tmp, n)) for n in isimler)
        assert os.path.exists(baska), "ilgisiz dosya silinmis!"
    finally:
        for n in isimler + [os.path.basename(baska)]:
            try:
                os.remove(os.path.join(tmp, n))
            except OSError:
                pass
    print("  OK  gecici kurulum artiklari silindi, ilgisiz dosyaya dokunulmadi")


def test_all_steps_run_in_order():
    tmp = tempfile.mkdtemp()
    hedef = os.path.join(tmp, "kurulum")
    os.makedirs(hedef)
    open(os.path.join(hedef, "a.txt"), "w").close()

    fake = FakeRun({"sc query": (1060, ""), "schtasks /query": (1, "")})
    orig_run, kaldir.run = kaldir.run, fake
    orig_sc, kaldir.shortcut_paths = kaldir.shortcut_paths, lambda: []
    satirlar = []
    try:
        ok = kaldir.uninstall_all(hedef, lambda o, m, p: satirlar.append((o, m, p)))
    finally:
        kaldir.run, kaldir.shortcut_paths = orig_run, orig_sc

    adimlar = [m for o, m, p in satirlar if o is None]
    assert len(adimlar) == 8, f"8 adim beklendi, {len(adimlar)} calisti"
    assert satirlar[-1][2] == 100, "ilerleme 100'e ulasmadi"
    assert not os.path.exists(hedef), "klasor silinmedi"
    assert ok, [m for o, m, p in satirlar if o is False]
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  OK  8 adim sirayla calisti, ilerleme %0 -> %100")


def test_install_dir_fallback():
    """Kayit defteri girdisi yoksa kaldirici kendi klasorunu kullanmali."""
    yol = kaldir.read_install_dir()
    assert yol and os.path.isabs(yol), yol
    print(f"  OK  kurulum klasoru bulundu/geri dusuldu: {os.path.basename(yol)}")


def test_self_delete_only_touches_temp_copy():
    """Kurulum klasorundeki asil kopya kendini silmeye kalkmamali."""
    import subprocess as sp
    cagrilar = []
    orig_popen, kaldir.subprocess.Popen = kaldir.subprocess.Popen, \
        lambda *a, **k: cagrilar.append(a[0]) or sp.CompletedProcess(a, 0)
    orig_exe = kaldir.sys.executable
    try:
        kaldir.sys.executable = r"C:\Program Files\M3sel Bertaraf\Kaldir.exe"
        kaldir.self_delete()
        assert not cagrilar, "kurulum klasorundeki kopya kendini silmeye calisti"

        kaldir.sys.executable = os.path.join(tempfile.gettempdir(), "m3sel-kaldir-1.exe")
        kaldir.self_delete()
        assert len(cagrilar) == 1, "gecici kopya kendini silmedi"
        assert "del" in " ".join(cagrilar[0]).lower()
    finally:
        kaldir.subprocess.Popen = orig_popen
        kaldir.sys.executable = orig_exe
    print("  OK  yalnizca gecici kopya kendini siliyor")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"\n{len(tests)} test gecti.")
