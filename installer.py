"""
M3sel Bertaraf - kurulum sihirbazi
===================================
Tek dosyalik kurucu. Programi Program Files altina kurar, kisayollari olusturur,
Denetim Masasi > Program Ekle/Kaldir kaydini yazar ve kendini kaldirici olarak
kurulum klasorune kopyalar.

Calisma bicimleri:
    installer.exe                    -> kurulum sihirbazi
    installer.exe --uninstall        -> kaldirma (kendini gecici klasore kopyalar)
    installer.exe --purge <klasor>   -> asil silme isi (gecici kopya tarafindan)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import winreg
import zipfile
from tkinter import filedialog, messagebox, ttk

APP_NAME = "M3sel Bertaraf"
APP_VERSION = "1.2"
PUBLISHER = "M3sel"
EXE_NAME = "M3sel Bertaraf.exe"
UNINST_NAME = "Kaldir.exe"
PAYLOAD = "payload.zip"
TASK_NAME = "M3sel Bertaraf Otomatik Baslatma"
REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\M3selBertaraf"

BG = "#12131a"
BG_PANEL = "#1a1c26"
FG = "#e6e8f0"
FG_DIM = "#8b90a6"
ACCENT = "#5865f2"
ACCENT_HOVER = "#6b76f5"
GREEN = "#3ba55d"
RED = "#ed4245"

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def res_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def default_dir() -> str:
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    return os.path.join(pf, APP_NAME)


def run_hidden(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, creationflags=NO_WINDOW, capture_output=True, text=True)


def powershell(script: str) -> subprocess.CompletedProcess:
    return run_hidden(["powershell", "-NoProfile", "-NonInteractive",
                       "-ExecutionPolicy", "Bypass", "-Command", script])


# ---------------------------------------------------------------------------
# Kurulum adimlari
# ---------------------------------------------------------------------------

def make_shortcut(lnk_path: str, target: str, workdir: str, icon: str) -> None:
    os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath = '{tgt}';"
        "$s.WorkingDirectory = '{wd}';"
        "$s.IconLocation = '{ico}';"
        "$s.Description = 'DPI sansur bertaraf araci';"
        "$s.Save()"
    ).format(lnk=lnk_path.replace("'", "''"), tgt=target.replace("'", "''"),
             wd=workdir.replace("'", "''"), ico=icon.replace("'", "''"))
    powershell(script)


def write_registry(install_dir: str, size_kb: int) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, REG_KEY, 0, winreg.KEY_WRITE) as k:
        exe = os.path.join(install_dir, EXE_NAME)
        unins = os.path.join(install_dir, UNINST_NAME)
        vals = {
            "DisplayName": APP_NAME,
            "DisplayVersion": APP_VERSION,
            "Publisher": PUBLISHER,
            "DisplayIcon": exe,
            "InstallLocation": install_dir,
            "UninstallString": f'"{unins}"',
            "QuietUninstallString": f'"{unins}" --sessiz',
        }
        for name, val in vals.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def create_autostart_task(exe: str) -> bool:
    """Yonetici yetkisi gerektigi icin Run anahtari yerine zamanlanmis gorev."""
    res = run_hidden([
        "schtasks", "/create", "/tn", TASK_NAME, "/tr", f'"{exe}"',
        "/sc", "onlogon", "/rl", "highest", "/f",
    ])
    return res.returncode == 0


def remove_autostart_task() -> None:
    run_hidden(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])


def install(install_dir: str, desktop: bool, startmenu: bool,
            autostart: bool, progress) -> None:
    payload = res_path(PAYLOAD)
    if not os.path.exists(payload):
        raise RuntimeError(f"Kurulum paketi bulunamadi: {PAYLOAD}")

    progress(5, "Onceki surum kontrol ediliyor...")
    exe = os.path.join(install_dir, EXE_NAME)
    if os.path.exists(exe):
        run_hidden(["taskkill", "/f", "/im", EXE_NAME])
        time.sleep(0.8)

    progress(10, "Klasor hazirlaniyor...")
    os.makedirs(install_dir, exist_ok=True)

    progress(15, "Dosyalar aciliyor...")
    with zipfile.ZipFile(payload) as zf:
        names = zf.namelist()
        total = max(1, len(names))
        for i, name in enumerate(names):
            zf.extract(name, install_dir)
            if i % 40 == 0:
                progress(15 + int(60 * i / total), f"Dosyalar aciliyor... ({i}/{total})")

    # Kaldirici (Kaldir.exe) paketin icinden gelir; ayri ve kucuk bir programdir.
    progress(78, "Kaldirici kontrol ediliyor...")
    if not os.path.exists(os.path.join(install_dir, UNINST_NAME)):
        raise RuntimeError(
            f"{UNINST_NAME} kurulum paketinde bulunamadi. Kurulum dosyasi bozuk, "
            "yeniden indirin.")

    progress(84, "Kayit defteri girdisi yaziliyor...")
    size_kb = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, fs in os.walk(install_dir) for f in fs
    ) // 1024
    try:
        write_registry(install_dir, size_kb)
    except PermissionError:
        raise RuntimeError("Kayit defterine yazilamadi. Kurucuyu yonetici olarak calistirin.")

    icon = exe
    if startmenu:
        progress(90, "Baslat menusu kisayolu...")
        sm = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                          "Microsoft", "Windows", "Start Menu", "Programs")
        make_shortcut(os.path.join(sm, f"{APP_NAME}.lnk"), exe, install_dir, icon)

    if desktop:
        progress(94, "Masaustu kisayolu...")
        pub = os.environ.get("PUBLIC", r"C:\Users\Public")
        make_shortcut(os.path.join(pub, "Desktop", f"{APP_NAME}.lnk"), exe, install_dir, icon)

    if autostart:
        progress(97, "Otomatik baslatma gorevi...")
        create_autostart_task(exe)
    else:
        remove_autostart_task()

    progress(100, "Kurulum tamamlandi.")


def uninstall(install_dir: str, progress) -> None:
    """Kaldirma isini kaldir.py'ye devreder; tek bir uygulama olsun diye
    burada ayri bir kopya tutulmuyor."""
    import kaldir
    kaldir.uninstall_all(install_dir, lambda ok, msg, pct: progress(pct, msg))


# ---------------------------------------------------------------------------
# Arayuz
# ---------------------------------------------------------------------------

class Wizard(tk.Tk):
    def __init__(self, mode: str = "install", target: str | None = None):
        super().__init__()
        self.mode = mode
        self.target = target
        self.title(f"{APP_NAME} Kurulumu" if mode == "install" else f"{APP_NAME} Kaldirma")
        self.geometry("620x430")
        self.resizable(False, False)
        self.configure(bg=BG)

        ico = res_path("m3sel.ico")
        if os.path.exists(ico):
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass

        self.var_dir = tk.StringVar(value=target or default_dir())
        self.var_desktop = tk.BooleanVar(value=True)
        self.var_startmenu = tk.BooleanVar(value=True)
        self.var_autostart = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="")
        self.var_run = tk.BooleanVar(value=True)

        self._style()
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True)

        if mode == "install":
            self.page_welcome()
        else:
            self.page_uninstall_confirm()

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TCheckbutton", background=BG, foreground=FG,
                     font=("Segoe UI", 10), focuscolor=BG)
        st.map("TCheckbutton", background=[("active", BG)], foreground=[("active", FG)])
        st.configure("TEntry", fieldbackground=BG_PANEL, foreground=FG, bordercolor="#2c2f3d")
        st.configure("Bar.Horizontal.TProgressbar", background=ACCENT,
                     troughcolor=BG_PANEL, bordercolor=BG_PANEL,
                     lightcolor=ACCENT, darkcolor=ACCENT)
        st.configure("Sm.TButton", font=("Segoe UI", 9), padding=(10, 4))

    # -- yardimcilar -------------------------------------------------------

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def label(self, parent, text, size=10, color=FG, bold=False, **kw):
        font = ("Segoe UI Semibold" if bold else "Segoe UI", size)
        return tk.Label(parent, text=text, bg=BG, fg=color, font=font,
                        justify="left", anchor="w", **kw)

    def big_button(self, parent, text, cmd, color=ACCENT):
        return tk.Button(parent, text=text, command=cmd, bg=color, fg="white",
                         activebackground=ACCENT_HOVER, activeforeground="white",
                         font=("Segoe UI Semibold", 11), relief="flat",
                         cursor="hand2", bd=0, width=14)

    # -- sayfalar ----------------------------------------------------------

    def page_welcome(self):
        self.clear()
        f = tk.Frame(self.body, bg=BG, padx=32, pady=26)
        f.pack(fill="both", expand=True)

        self.label(f, APP_NAME, size=22, bold=True).pack(anchor="w")
        self.label(f, f"Surum {APP_VERSION}  -  DPI sansur bertaraf araci",
                   size=10, color=FG_DIM).pack(anchor="w", pady=(2, 18))

        self.label(f, "Bu kurulum programi bilgisayariniza kuracak, kisayollari\n"
                      "olusturacak ve Program Ekle/Kaldir listesine ekleyecek.",
                   size=10, color=FG_DIM).pack(anchor="w", pady=(0, 18))

        self.label(f, "Kurulum klasoru", size=10, bold=True).pack(anchor="w")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(6, 16))
        ttk.Entry(row, textvariable=self.var_dir).pack(side="left", fill="x", expand=True, ipady=3)
        ttk.Button(row, text="Gozat", style="Sm.TButton", command=self.browse).pack(side="left", padx=(8, 0))

        ttk.Checkbutton(f, text="Masaustu kisayolu olustur",
                        variable=self.var_desktop).pack(anchor="w", pady=2)
        ttk.Checkbutton(f, text="Baslat menusune ekle",
                        variable=self.var_startmenu).pack(anchor="w", pady=2)
        ttk.Checkbutton(f, text="Windows acilisinda otomatik baslat",
                        variable=self.var_autostart).pack(anchor="w", pady=2)

        bar = tk.Frame(f, bg=BG)
        bar.pack(side="bottom", fill="x", pady=(20, 0))
        self.big_button(bar, "KUR", self.do_install).pack(side="right", ipady=6)
        ttk.Button(bar, text="Iptal", style="Sm.TButton",
                   command=self.destroy).pack(side="right", padx=(0, 10))

    def page_uninstall_confirm(self):
        self.clear()
        f = tk.Frame(self.body, bg=BG, padx=32, pady=26)
        f.pack(fill="both", expand=True)

        self.label(f, f"{APP_NAME} Kaldirma", size=20, bold=True).pack(anchor="w")
        self.label(f, self.var_dir.get(), size=9, color=FG_DIM).pack(anchor="w", pady=(4, 20))
        self.label(f, "Program, kisayollari, otomatik baslatma gorevi ve ayar\n"
                      "dosyasi silinecek. Devam edilsin mi?",
                   size=10, color=FG_DIM).pack(anchor="w")

        bar = tk.Frame(f, bg=BG)
        bar.pack(side="bottom", fill="x", pady=(20, 0))
        self.big_button(bar, "KALDIR", self.do_uninstall, color=RED).pack(side="right", ipady=6)
        ttk.Button(bar, text="Vazgec", style="Sm.TButton",
                   command=self.destroy).pack(side="right", padx=(0, 10))

    def page_progress(self, title: str):
        self.clear()
        f = tk.Frame(self.body, bg=BG, padx=32, pady=26)
        f.pack(fill="both", expand=True)
        self.label(f, title, size=18, bold=True).pack(anchor="w", pady=(30, 20))
        self.pb = ttk.Progressbar(f, style="Bar.Horizontal.TProgressbar",
                                  mode="determinate", maximum=100, length=540)
        self.pb.pack(anchor="w", pady=(0, 10))
        self.label(f, "", size=9, color=FG_DIM, textvariable=self.var_status).pack(anchor="w")

    def page_done(self, ok: bool, msg: str, offer_run: bool):
        self.clear()
        f = tk.Frame(self.body, bg=BG, padx=32, pady=26)
        f.pack(fill="both", expand=True)

        self.label(f, "Tamamlandi" if ok else "Hata", size=20, bold=True,
                   color=GREEN if ok else RED).pack(anchor="w", pady=(30, 10))
        tk.Message(f, text=msg, bg=BG, fg=FG_DIM, font=("Segoe UI", 10),
                   width=540, justify="left").pack(anchor="w")

        if ok and offer_run:
            ttk.Checkbutton(f, text=f"{APP_NAME} programini simdi calistir",
                            variable=self.var_run).pack(anchor="w", pady=(18, 0))

        bar = tk.Frame(f, bg=BG)
        bar.pack(side="bottom", fill="x", pady=(20, 0))
        self.big_button(bar, "KAPAT", self.finish,
                        color=ACCENT if ok else RED).pack(side="right", ipady=6)

    # -- eylemler ----------------------------------------------------------

    def browse(self):
        d = filedialog.askdirectory(title="Kurulum klasoru sec")
        if d:
            self.var_dir.set(os.path.join(d, APP_NAME) if os.path.basename(d) != APP_NAME else d)

    def _progress(self, pct: int, msg: str):
        def upd():
            self.pb["value"] = pct
            self.var_status.set(msg)
        self.after(0, upd)

    def _work(self, fn, title, done_msg, offer_run):
        self.page_progress(title)

        def run():
            try:
                fn()
                self.after(0, lambda: self.page_done(True, done_msg, offer_run))
            except Exception as exc:
                self.after(0, lambda: self.page_done(False, str(exc), False))

        threading.Thread(target=run, daemon=True).start()

    def do_install(self):
        d = self.var_dir.get().strip()
        if not d or len(d) < 4 or not os.path.splitdrive(d)[0]:
            messagebox.showerror(APP_NAME, "Gecerli bir kurulum klasoru secin.")
            return
        self.var_dir.set(d)
        self._work(
            lambda: install(d, self.var_desktop.get(), self.var_startmenu.get(),
                            self.var_autostart.get(), self._progress),
            "Kuruluyor...",
            f"{APP_NAME} kuruldu.\n\n{d}\n\n"
            "Programi acip BASLAT'a bastiktan sonra Discord'u acin. "
            "Program yonetici yetkisiyle calisir, her acilista Windows onay isteyecektir.",
            offer_run=True,
        )

    def do_uninstall(self):
        self._work(
            lambda: uninstall(self.var_dir.get(), self._progress),
            "Kaldiriliyor...",
            f"{APP_NAME} bilgisayarinizdan kaldirildi.",
            offer_run=False,
        )

    def finish(self):
        if self.mode == "install" and self.var_run.get():
            exe = os.path.join(self.var_dir.get(), EXE_NAME)
            if os.path.exists(exe):
                try:
                    os.startfile(exe)
                except OSError:
                    pass
        self.destroy()


# ---------------------------------------------------------------------------

def read_install_dir() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_KEY) as k:
            return winreg.QueryValueEx(k, "InstallLocation")[0]
    except OSError:
        return default_dir()


def main() -> None:
    args = sys.argv[1:]

    # Gecici kopya: asil silme isini bu yapar.
    if "--purge" in args:
        target = args[args.index("--purge") + 1]
        root = tk.Tk()
        root.withdraw()
        w = Wizard(mode="uninstall", target=target)
        w.do_uninstall()
        w.mainloop()
        return

    # Kaldirma istegi: kendini silemeyecegi icin gecici klasore kopyalanip
    # oradan yeniden baslar.
    if "--uninstall" in args:
        here = os.path.dirname(os.path.abspath(sys.executable))
        install_dir = read_install_dir()
        if os.path.normcase(here) == os.path.normcase(os.path.normpath(install_dir)):
            tmp = os.path.join(tempfile.gettempdir(), f"m3sel-kaldir-{os.getpid()}.exe")
            shutil.copy2(sys.executable, tmp)
            subprocess.Popen([tmp, "--purge", install_dir])
            return
        Wizard(mode="uninstall", target=install_dir).mainloop()
        return

    Wizard(mode="install").mainloop()


if __name__ == "__main__":
    main()
