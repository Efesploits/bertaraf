"""
M3sel Bertaraf - kaldirma araci
================================
Programi ve geride kalan her seyi siler:

  1. Calisan program                (taskkill)
  2. WinDivert cekirdek surucusu    (sc stop + sc delete)  <- en onemlisi
  3. Otomatik baslatma gorevi       (schtasks)
  4. Kisayollar                     (masaustu + baslat menusu, kullanici ve ortak)
  5. Program Ekle/Kaldir kaydi      (HKLM ve HKCU)
  6. Ayar dosyasi                   (%APPDATA%\\M3selBertaraf)
  7. Gecici kurulum dosyalari       (%TEMP%)
  8. Kurulum klasoru                (kendisi dahil)

Kendini silemeyecegi icin once gecici klasore kopyalanip oradan calisir.

Kullanim:
    Kaldir.exe              -> pencereli kaldirma
    Kaldir.exe --sessiz     -> soru sormadan kaldir
    Kaldir.exe --purge DIR  -> asil silme isi (gecici kopya kendini boyle cagirir)
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import winreg
from tkinter import messagebox, ttk

APP_NAME = "M3sel Bertaraf"
EXE_NAME = "M3sel Bertaraf.exe"
TASK_NAME = "M3sel Bertaraf Otomatik Baslatma"
REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\M3selBertaraf"
CONFIG_DIRNAME = "M3selBertaraf"
DRIVER_SERVICES = ("WinDivert", "WinDivert1.4", "WinDivert1.3")

BG = "#12131a"
BG_LOG = "#0d0e14"
FG = "#e6e8f0"
FG_DIM = "#8b90a6"
ACCENT = "#5865f2"
GREEN = "#3ba55d"
RED = "#ed4245"
AMBER = "#faa61a"

NO_WINDOW = 0x08000000


def run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, creationflags=NO_WINDOW, capture_output=True,
                              text=True, timeout=timeout)
    except Exception:
        return subprocess.CompletedProcess(args, 1, "", "calistirilamadi")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def read_install_dir() -> str:
    """Kurulum klasorunu kayit defterinden okur; yoksa kendi klasorunu kullanir."""
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, REG_KEY) as k:
                path = winreg.QueryValueEx(k, "InstallLocation")[0]
                if path and os.path.isdir(path):
                    return os.path.normpath(path)
        except OSError:
            continue
    return os.path.dirname(os.path.abspath(sys.executable))


# ---------------------------------------------------------------------------
# Adimlar - her biri (basarili, aciklama) doner
# ---------------------------------------------------------------------------

def step_kill_app() -> tuple[bool, str]:
    res = run(["taskkill", "/f", "/im", EXE_NAME])
    time.sleep(0.6)
    if res.returncode == 0:
        return True, "Calisan program kapatildi."
    return True, "Program zaten kapaliydi."


def step_remove_driver() -> tuple[bool, str]:
    """WinDivert surucusunu durdurup kaydini siler. Kaldirmanin en cok
    unutulan adimi budur: program silinse bile surucu sistemde kalir."""
    found, removed, pending = [], [], []
    for name in DRIVER_SERVICES:
        q = run(["sc", "query", name])
        if q.returncode != 0:
            continue
        found.append(name)
        run(["sc", "stop", name])
        time.sleep(0.4)
        d = run(["sc", "delete", name])
        out = (d.stdout + d.stderr).lower()
        if d.returncode == 0:
            removed.append(name)
        elif "1072" in out or "marked for deletion" in out or "silinmek" in out:
            pending.append(name)

    if not found:
        return True, "WinDivert surucusu zaten kayitli degildi."
    parts = []
    if removed:
        parts.append(f"WinDivert surucusu silindi ({', '.join(removed)}).")
    if pending:
        parts.append(f"{', '.join(pending)} silinmek uzere isaretlendi, "
                     "yeniden baslatmada gidecek.")
    if not removed and not pending:
        return False, ("WinDivert surucusu silinemedi. Yonetici yetkisi gerekir "
                       "veya baska bir DPI araci kullaniyor olabilir.")
    return True, " ".join(parts)


def step_remove_task() -> tuple[bool, str]:
    q = run(["schtasks", "/query", "/tn", TASK_NAME])
    if q.returncode != 0:
        return True, "Otomatik baslatma gorevi yoktu."
    res = run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
    if res.returncode == 0:
        return True, "Otomatik baslatma gorevi silindi."
    return False, "Otomatik baslatma gorevi silinemedi."


def shortcut_paths() -> list[str]:
    """Kisayolun bulunabilecegi tum yerler: ortak ve kullaniciya ozel."""
    pd = os.environ.get("ProgramData", r"C:\ProgramData")
    pub = os.environ.get("PUBLIC", r"C:\Users\Public")
    app = os.environ.get("APPDATA", "")
    home = os.path.expanduser("~")
    name = f"{APP_NAME}.lnk"
    return [
        os.path.join(pd, "Microsoft", "Windows", "Start Menu", "Programs", name),
        os.path.join(app, "Microsoft", "Windows", "Start Menu", "Programs", name),
        os.path.join(pub, "Desktop", name),
        os.path.join(home, "Desktop", name),
        os.path.join(home, "OneDrive", "Desktop", name),
        os.path.join(home, "Masaustu", name),
    ]


def step_remove_shortcuts() -> tuple[bool, str]:
    n = 0
    for p in shortcut_paths():
        try:
            if os.path.exists(p):
                os.remove(p)
                n += 1
        except OSError:
            pass
    return True, (f"{n} kisayol silindi." if n else "Silinecek kisayol yoktu.")


def step_remove_registry() -> tuple[bool, str]:
    n = 0
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            winreg.DeleteKey(root, REG_KEY)
            n += 1
        except OSError:
            pass
    return True, (f"Program Ekle/Kaldir kaydi silindi ({n})." if n
                  else "Program Ekle/Kaldir kaydi zaten yoktu.")


def step_remove_config() -> tuple[bool, str]:
    cfg = os.path.join(os.environ.get("APPDATA", ""), CONFIG_DIRNAME)
    if not os.path.isdir(cfg):
        return True, "Ayar dosyasi yoktu."
    shutil.rmtree(cfg, ignore_errors=True)
    if os.path.isdir(cfg):
        return False, "Ayar dosyasi silinemedi."
    return True, "Ayar dosyasi silindi."


def step_remove_temp() -> tuple[bool, str]:
    tmp = tempfile.gettempdir()
    n = 0
    try:
        for f in os.listdir(tmp):
            low = f.lower()
            if low.startswith(("m3sel-bertaraf-kurulum", "m3sel-kaldir")) and low.endswith(".exe"):
                if os.path.join(tmp, f) == os.path.abspath(sys.executable):
                    continue  # su an calisan kopya
                try:
                    os.remove(os.path.join(tmp, f))
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return True, (f"{n} gecici dosya silindi." if n else "Gecici dosya yoktu.")


def step_remove_files(install_dir: str) -> tuple[bool, str]:
    if not os.path.isdir(install_dir):
        return True, "Kurulum klasoru zaten yoktu."
    for _ in range(12):
        shutil.rmtree(install_dir, ignore_errors=True)
        if not os.path.exists(install_dir):
            return True, f"Kurulum klasoru silindi: {install_dir}"
        time.sleep(0.5)
    kalan = sum(len(f) for _, _, f in os.walk(install_dir))
    return False, (f"Kurulum klasoru tam silinemedi ({kalan} dosya kaldi). "
                   "Bilgisayari yeniden baslatip klasoru elle silin: " + install_dir)


def uninstall_all(install_dir: str, report) -> bool:
    """Tum adimlari sirayla calistirir. report(basarili, mesaj, yuzde) cagrilir."""
    steps = [
        ("Program kapatiliyor", step_kill_app),
        ("WinDivert surucusu kaldiriliyor", step_remove_driver),
        ("Otomatik baslatma gorevi siliniyor", step_remove_task),
        ("Kisayollar siliniyor", step_remove_shortcuts),
        ("Kayit defteri temizleniyor", step_remove_registry),
        ("Ayarlar siliniyor", step_remove_config),
        ("Gecici dosyalar siliniyor", step_remove_temp),
        ("Dosyalar siliniyor", lambda: step_remove_files(install_dir)),
    ]
    tum_basarili = True
    for i, (title, fn) in enumerate(steps):
        report(None, title + "...", int(100 * i / len(steps)))
        try:
            ok, msg = fn()
        except Exception as exc:
            ok, msg = False, f"{title}: {exc}"
        tum_basarili = tum_basarili and ok
        report(ok, msg, int(100 * (i + 1) / len(steps)))
    return tum_basarili


# ---------------------------------------------------------------------------
# Arayuz
# ---------------------------------------------------------------------------

class Uninstaller(tk.Tk):
    def __init__(self, install_dir: str):
        super().__init__()
        self.install_dir = install_dir
        self.title(f"{APP_NAME} Kaldirma")
        self.geometry("620x480")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.basarili = None

        ico = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(
            os.path.abspath(__file__))), "m3sel.ico")
        if os.path.exists(ico):
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass

        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Bar.Horizontal.TProgressbar", background=ACCENT,
                     troughcolor="#1a1c26", bordercolor="#1a1c26",
                     lightcolor=ACCENT, darkcolor=ACCENT)

        f = tk.Frame(self, bg=BG, padx=24, pady=20)
        f.pack(fill="both", expand=True)

        tk.Label(f, text=f"{APP_NAME} Kaldirma", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 18), anchor="w").pack(fill="x")
        tk.Label(f, text=install_dir, bg=BG, fg=FG_DIM, font=("Segoe UI", 9),
                 anchor="w").pack(fill="x", pady=(2, 12))

        self.txt = tk.Text(f, bg=BG_LOG, fg=FG, font=("Consolas", 9), relief="flat",
                           height=14, wrap="word", padx=10, pady=8, state="disabled")
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("ok", foreground=GREEN)
        self.txt.tag_configure("err", foreground=RED)
        self.txt.tag_configure("run", foreground=FG_DIM)
        self.txt.tag_configure("head", foreground=FG)

        self.pb = ttk.Progressbar(f, style="Bar.Horizontal.TProgressbar",
                                  maximum=100, length=560)
        self.pb.pack(fill="x", pady=(10, 12))

        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x")
        self.btn = tk.Button(bar, text="KALDIR", command=self.start, bg=RED,
                             fg="white", activebackground="#f25457",
                             activeforeground="white", relief="flat", bd=0,
                             cursor="hand2", font=("Segoe UI Semibold", 11), width=14)
        self.btn.pack(side="right", ipady=6)
        self.btn_cancel = tk.Button(bar, text="Vazgec", command=self.destroy, bg=BG,
                                    fg=FG_DIM, activebackground=BG, relief="flat",
                                    bd=0, cursor="hand2", font=("Segoe UI", 10))
        self.btn_cancel.pack(side="right", padx=(0, 14))

        self.write("head", "Silinecekler:")
        for line in ("  - Program dosyalari",
                     "  - WinDivert cekirdek surucusu (sistemde kayitli servis)",
                     "  - Otomatik baslatma gorevi",
                     "  - Masaustu ve Baslat menusu kisayollari",
                     "  - Program Ekle/Kaldir kaydi",
                     "  - Ayarlar (%APPDATA%\\M3selBertaraf)"):
            self.write("run", line)
        if not is_admin():
            self.write("err", "\nUyari: yonetici yetkisi yok. Surucu ve kayit "
                              "defteri adimlari basarisiz olabilir.")
        self.write("run", "")

    def write(self, tag: str, msg: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + "\n", tag)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def report(self, ok, msg, pct):
        def upd():
            self.pb["value"] = pct
            if ok is None:
                self.write("run", msg)
            else:
                self.write("ok" if ok else "err", ("  [tamam] " if ok else "  [HATA] ") + msg)
        self.after(0, upd)

    def start(self):
        self.btn.configure(state="disabled")
        self.btn_cancel.configure(state="disabled")

        def work():
            ok = uninstall_all(self.install_dir, self.report)
            def done():
                self.basarili = ok
                if ok:
                    self.write("ok", "\nKaldirma tamamlandi. Geride bir sey kalmadi.")
                else:
                    self.write("err", "\nBazi adimlar tamamlanamadi (yukarida [HATA] "
                                      "olanlar). Bilgisayari yeniden baslatip tekrar deneyin.")
                self.btn.configure(text="KAPAT", state="normal", bg=ACCENT,
                                   activebackground="#6b76f5", command=self.destroy)
            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------------------

def self_delete() -> None:
    """Gecici klasordeki kendi kopyasini, cikildiktan sonra silinmek uzere
    isaretler. Boylede geriye tek bir dosya bile kalmaz."""
    me = os.path.abspath(sys.executable)
    if os.path.normcase(os.path.dirname(me)) != os.path.normcase(tempfile.gettempdir()):
        return  # kurulum klasorundeki asil kopya, dokunma
    try:
        subprocess.Popen(
            ["cmd", "/c", "ping -n 4 127.0.0.1 >nul & del /f /q \"%s\"" % me],
            creationflags=NO_WINDOW | 0x00000008)  # gizli + bagimsiz
    except Exception:
        pass


def main() -> None:
    args = sys.argv[1:]
    sessiz = "--sessiz" in args

    if "--purge" in args:
        i = args.index("--purge")
        target = args[i + 1] if i + 1 < len(args) else read_install_dir()
        if sessiz:
            uninstall_all(target, lambda ok, msg, pct: None)
        else:
            Uninstaller(target).mainloop()
        self_delete()
        return

    install_dir = read_install_dir()
    here = os.path.dirname(os.path.abspath(sys.executable))

    # Kendi klasorunu silemez: gecici klasore kopyalanip oradan devam eder.
    if os.path.normcase(here) == os.path.normcase(install_dir):
        try:
            tmp = os.path.join(tempfile.gettempdir(), f"m3sel-kaldir-{os.getpid()}.exe")
            shutil.copy2(sys.executable, tmp)
            cmd = [tmp, "--purge", install_dir] + (["--sessiz"] if sessiz else [])
            subprocess.Popen(cmd, creationflags=0x00000008)  # DETACHED_PROCESS
            return
        except Exception as exc:
            if not sessiz:
                messagebox.showerror(APP_NAME, f"Kaldirma baslatilamadi: {exc}")
            return

    if sessiz:
        uninstall_all(install_dir, lambda ok, msg, pct: None)
    else:
        Uninstaller(install_dir).mainloop()


if __name__ == "__main__":
    main()
