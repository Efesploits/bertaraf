"""
M3sel Bertaraf - pencereli DPI bertaraf araci
==============================================
GoodbyeDPI benzeri sansur bertaraf motorunun grafik arayuzu.
Yonetici yetkisi ister (WinDivert surucusu icin zorunlu).
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import core
import guncelle

APP_NAME = "M3sel Bertaraf"
APP_VERSION = core.APP_VERSION
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "M3selBertaraf")
CONFIG_PATH = os.path.join(CONFIG_DIR, "ayarlar.json")

# --- Renkler ---------------------------------------------------------------
BG = "#12131a"
BG_PANEL = "#1a1c26"
BG_LOG = "#0d0e14"
FG = "#e6e8f0"
FG_DIM = "#8b90a6"
ACCENT = "#5865f2"        # discord moru
ACCENT_HOVER = "#6b76f5"
GREEN = "#3ba55d"
RED = "#ed4245"
AMBER = "#faa61a"

MODES = [
    ("Discord (Onerilen)", "fake_disorder"),
    ("Agresif (sahte paket + duz bolme)", "fake_split"),
    ("Ters bolme (sahte paketsiz)", "disorder"),
    ("Hafif (sadece bolme)", "split"),
]

FOOLINGS = [
    ("badsum - bozuk saglama toplami", "badsum"),
    ("badseq - pencere disi sira no", "badseq"),
    ("ttl - dusuk yasam suresi", "ttl"),
]

TEST_HOSTS = ["discord.com", "gateway.discord.gg", "cdn.discordapp.com"]


# ---------------------------------------------------------------------------
# Yonetici yetkisi
# ---------------------------------------------------------------------------

def ensure_admin() -> bool:
    """Yonetici degilse programi yukseltilmis olarak yeniden baslatir."""
    if core.is_admin():
        return True
    if "--elevated" in sys.argv:
        return False  # yukseltme denendi ama olmadi -> sonsuz donguye girme

    if getattr(sys, "frozen", False):
        exe, params = sys.executable, "--elevated"
    else:
        exe = sys.executable
        script = os.path.abspath(__file__)
        params = f'"{script}" --elevated'

    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return rc > 32  # basarili -> bu ornegi kapat
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Arayuz
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("940x660")
        self.minsize(820, 560)
        self.configure(bg=BG)

        self.log_queue: queue.Queue = queue.Queue(maxsize=5000)
        self.settings = core.Settings()
        self.engine = core.Engine(self.settings, self.log_queue)
        self.log_lines = 0
        self._start_time = 0.0

        self._set_icon()
        self._build_vars()
        self._load_config()
        self._build_style()
        self._build_ui()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_logs)
        self.after(1000, self._tick)

        self._banner()

        if self.var_autostart.get():
            self.after(600, self.start_engine)

        # Acilista sessiz guncelleme kontrolu: yalnizca yeni surum varsa konusur.
        self.after(3000, lambda: self.check_update(silent=True))

    def _set_icon(self) -> None:
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "m3sel.ico")
        if os.path.exists(path):
            try:
                self.iconbitmap(path)
            except tk.TclError:
                pass

    # -- degiskenler -------------------------------------------------------

    def _build_vars(self) -> None:
        self.var_mode = tk.StringVar(value=MODES[0][0])
        self.var_fooling = tk.StringVar(value=FOOLINGS[0][0])
        self.var_http = tk.BooleanVar(value=True)
        self.var_quic = tk.BooleanVar(value=True)
        self.var_doh = tk.BooleanVar(value=True)
        self.var_dns = tk.BooleanVar(value=True)
        self.var_dnsaddr = tk.StringVar(value="1.1.1.1")
        self.var_onlylist = tk.BooleanVar(value=False)
        self.var_verbose = tk.BooleanVar(value=False)
        self.var_autostart = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="Durduruldu")
        self.var_stats = tk.StringVar(value="Hazir")

    # -- ayar dosyasi ------------------------------------------------------

    def _load_config(self) -> None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return
        self.var_mode.set(data.get("mode_label", self.var_mode.get()))
        self.var_fooling.set(data.get("fooling_label", self.var_fooling.get()))
        self.var_http.set(data.get("http", True))
        self.var_quic.set(data.get("quic", True))
        self.var_doh.set(data.get("doh", True))
        self.var_dns.set(data.get("dns", True))
        self.var_dnsaddr.set(data.get("dns_server", "1.1.1.1"))
        self.var_onlylist.set(data.get("only_list", False))
        self.var_verbose.set(data.get("verbose", False))
        self.var_autostart.set(data.get("autostart", False))

    def _save_config(self) -> None:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump({
                    "mode_label": self.var_mode.get(),
                    "fooling_label": self.var_fooling.get(),
                    "http": self.var_http.get(),
                    "quic": self.var_quic.get(),
                    "doh": self.var_doh.get(),
                    "dns": self.var_dns.get(),
                    "dns_server": self.var_dnsaddr.get(),
                    "only_list": self.var_onlylist.get(),
                    "verbose": self.var_verbose.get(),
                    "autostart": self.var_autostart.get(),
                }, fh, indent=2)
        except Exception:
            pass

    # -- gorunum -----------------------------------------------------------

    def _build_style(self) -> None:
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=BG_PANEL)
        st.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        st.configure("Dim.TLabel", background=BG, foreground=FG_DIM, font=("Segoe UI", 9))
        st.configure("PanelDim.TLabel", background=BG_PANEL, foreground=FG_DIM, font=("Segoe UI", 9))
        st.configure("Panel.TLabel", background=BG_PANEL, foreground=FG, font=("Segoe UI", 10))
        st.configure("Title.TLabel", background=BG, foreground=FG,
                     font=("Segoe UI Semibold", 20))
        st.configure("TCheckbutton", background=BG_PANEL, foreground=FG,
                     font=("Segoe UI", 10), focuscolor=BG_PANEL)
        st.map("TCheckbutton",
               background=[("active", BG_PANEL)],
               foreground=[("active", FG)])
        st.configure("TCombobox", fieldbackground=BG, background=BG, foreground=FG,
                     arrowcolor=FG, bordercolor="#2c2f3d", lightcolor=BG, darkcolor=BG)
        st.configure("TEntry", fieldbackground=BG, foreground=FG, bordercolor="#2c2f3d")
        st.configure("Small.TButton", font=("Segoe UI", 9), padding=(10, 5))

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(18, 14, 18, 12))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        # --- baslik -------------------------------------------------------
        head = ttk.Frame(root)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)

        ttk.Label(head, text="M3sel Bertaraf", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(head, text="  DPI sansur bertaraf motoru", style="Dim.TLabel").grid(row=0, column=1, sticky="w", pady=(8, 0))

        self.dot = tk.Canvas(head, width=12, height=12, bg=BG, highlightthickness=0)
        self.dot.grid(row=0, column=2, padx=(0, 8))
        self._dot_id = self.dot.create_oval(1, 1, 11, 11, fill=RED, outline="")
        self.lbl_status = ttk.Label(head, textvariable=self.var_status,
                                    font=("Segoe UI Semibold", 11))
        self.lbl_status.grid(row=0, column=3, sticky="e")

        # --- ayar paneli --------------------------------------------------
        panel = tk.Frame(root, bg=BG_PANEL, highlightthickness=1,
                         highlightbackground="#262939")
        panel.grid(row=1, column=0, sticky="ew", pady=(14, 10))
        panel.columnconfigure(1, weight=1)

        ttk.Label(panel, text="Yontem", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 6))
        self.cmb_mode = ttk.Combobox(panel, textvariable=self.var_mode, state="readonly",
                                     values=[m[0] for m in MODES], width=36)
        self.cmb_mode.grid(row=0, column=1, sticky="w", pady=(12, 6))

        ttk.Label(panel, text="DNS sunucusu", style="Panel.TLabel").grid(
            row=0, column=2, sticky="e", padx=(20, 6), pady=(12, 6))
        self.ent_dns = ttk.Entry(panel, textvariable=self.var_dnsaddr, width=14)
        self.ent_dns.grid(row=0, column=3, sticky="w", padx=(0, 14), pady=(12, 6))

        ttk.Label(panel, text="Yanilma", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=14, pady=(0, 6))
        self.cmb_fooling = ttk.Combobox(panel, textvariable=self.var_fooling,
                                        state="readonly", width=36,
                                        values=[f[0] for f in FOOLINGS])
        self.cmb_fooling.grid(row=1, column=1, sticky="w", pady=(0, 6))

        checks = tk.Frame(panel, bg=BG_PANEL)
        checks.grid(row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=(2, 12))

        opts = [
            ("HTTP (80) trafigini de isle", self.var_http),
            ("QUIC engelle (UDP/443)", self.var_quic),
            ("DNS'i DoH ile duzelt", self.var_doh),
            ("DNS'i yonlendir", self.var_dns),
            ("Sadece engelli site listesi", self.var_onlylist),
            ("Ayrintili log", self.var_verbose),
            ("Acilista otomatik baslat", self.var_autostart),
        ]
        for i, (text, var) in enumerate(opts):
            ttk.Checkbutton(checks, text=text, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=6, pady=3)

        # --- dugmeler -----------------------------------------------------
        bar = ttk.Frame(root)
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        bar.columnconfigure(1, weight=1)

        self.btn_toggle = tk.Button(
            bar, text="BASLAT", command=self.toggle,
            bg=ACCENT, fg="white", activebackground=ACCENT_HOVER, activeforeground="white",
            font=("Segoe UI Semibold", 12), relief="flat", cursor="hand2",
            width=16, height=1, bd=0)
        self.btn_toggle.grid(row=0, column=0, sticky="w", ipady=6)

        right = ttk.Frame(bar)
        right.grid(row=0, column=2, sticky="e")
        self.btn_test = ttk.Button(right, text="Teshis", style="Small.TButton",
                                   command=self.run_test)
        self.btn_test.pack(side="left", padx=4)
        self.btn_update = ttk.Button(right, text="Guncelle", style="Small.TButton",
                                     command=lambda: self.check_update(False))
        self.btn_update.pack(side="left", padx=4)
        ttk.Button(right, text="Logu Kaydet", style="Small.TButton",
                   command=self.save_log).pack(side="left", padx=4)
        ttk.Button(right, text="Temizle", style="Small.TButton",
                   command=self.clear_log).pack(side="left", padx=4)

        # --- log ----------------------------------------------------------
        logframe = tk.Frame(root, bg=BG_LOG, highlightthickness=1,
                            highlightbackground="#262939")
        logframe.grid(row=3, column=0, sticky="nsew")
        logframe.rowconfigure(0, weight=1)
        logframe.columnconfigure(0, weight=1)

        self.txt = tk.Text(logframe, bg=BG_LOG, fg=FG, insertbackground=FG,
                           font=("Consolas", 10), relief="flat", wrap="word",
                           padx=12, pady=8, state="disabled")
        self.txt.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(logframe, orient="vertical", command=self.txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt.configure(yscrollcommand=sb.set)

        self.txt.tag_configure("time", foreground="#5a5f75")
        self.txt.tag_configure("info", foreground=FG)
        self.txt.tag_configure("ok", foreground=GREEN)
        self.txt.tag_configure("discord", foreground="#7d8bff")
        self.txt.tag_configure("warn", foreground=AMBER)
        self.txt.tag_configure("err", foreground=RED)
        self.txt.tag_configure("dim", foreground=FG_DIM)
        self.txt.tag_configure("head", foreground=ACCENT, font=("Consolas", 10, "bold"))

        # --- durum cubugu -------------------------------------------------
        ttk.Label(root, textvariable=self.var_stats, style="Dim.TLabel").grid(
            row=4, column=0, sticky="w", pady=(8, 0))

    # -- log ---------------------------------------------------------------

    def _write(self, level: str, msg: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", datetime.now().strftime("%H:%M:%S  "), "time")
        self.txt.insert("end", msg + "\n", level)
        self.log_lines += 1
        if self.log_lines > 3000:                      # bellek sismesin
            self.txt.delete("1.0", "500.0")
            self.log_lines -= 500
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _drain_logs(self) -> None:
        try:
            for _ in range(200):
                level, msg = self.log_queue.get_nowait()
                self._write(level, msg)
        except queue.Empty:
            pass
        self.after(120, self._drain_logs)

    def _banner(self) -> None:
        self._write("head", f"{APP_NAME} v{APP_VERSION}")
        self._write("dim", "GoodbyeDPI mantigiyla calisan yerli DPI bertaraf motoru.")
        self._write("dim", "Yonetici yetkisi: " + ("var" if core.is_admin() else "YOK"))
        if not core.is_admin():
            self._write("err", "Yonetici yetkisi olmadan surucu acilamaz. Programi yonetici olarak calistirin.")
        self._write("info", "BASLAT'a bas, sonra Discord'u ac.")
        self._write("dim", "-" * 78)

    def clear_log(self) -> None:
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")
        self.log_lines = 0

    def save_log(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Logu kaydet", defaultextension=".txt",
            initialfile=f"m3sel-log-{datetime.now():%Y%m%d-%H%M%S}.txt",
            filetypes=[("Metin dosyasi", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.txt.get("1.0", "end"))
            self._write("ok", f"Log kaydedildi: {path}")
        except Exception as exc:
            self._write("err", f"Log kaydedilemedi: {exc}")

    # -- motor -------------------------------------------------------------

    def _collect_settings(self) -> None:
        label = self.var_mode.get()
        self.settings.mode = dict(MODES).get(label, "fake_disorder")
        self.settings.fooling = dict(FOOLINGS).get(self.var_fooling.get(), "badsum")
        self.settings.handle_http = self.var_http.get()
        self.settings.block_quic = self.var_quic.get()
        self.settings.doh_fix = self.var_doh.get()
        self.settings.dns_redirect = self.var_dns.get()
        self.settings.dns_server = self.var_dnsaddr.get().strip() or "1.1.1.1"
        self.settings.only_hostlist = self.var_onlylist.get()
        self.settings.verbose = self.var_verbose.get()

    def toggle(self) -> None:
        if self.engine.running:
            self.stop_engine()
        else:
            self.start_engine()

    def start_engine(self) -> None:
        if self.engine.running:
            return
        if not core.is_admin():
            messagebox.showerror(
                APP_NAME,
                "Yonetici yetkisi gerekli.\n\nProgrami kapatip sag tik > "
                "'Yonetici olarak calistir' ile tekrar acin.")
            return

        self._collect_settings()
        self._write("info", f"Yontem: {self.var_mode.get()}  |  "
                            f"Yanilma: {self.var_fooling.get()}")
        try:
            self.engine.start()
        except Exception as exc:
            self._write("err", str(exc))
            messagebox.showerror(APP_NAME, str(exc))
            return

        self._start_time = time.monotonic()
        self._set_state(True)
        self._save_config()

    def stop_engine(self) -> None:
        if not self.engine.running:
            return
        self.engine.stop()
        self._set_state(False)
        self._save_config()

    def _set_state(self, running: bool) -> None:
        if running:
            self.var_status.set("Calisiyor")
            self.lbl_status.configure(foreground=GREEN)
            self.dot.itemconfigure(self._dot_id, fill=GREEN)
            self.btn_toggle.configure(text="DURDUR", bg=RED, activebackground="#f25457")
            state = "disabled"
        else:
            self.var_status.set("Durduruldu")
            self.lbl_status.configure(foreground=RED)
            self.dot.itemconfigure(self._dot_id, fill=RED)
            self.btn_toggle.configure(text="BASLAT", bg=ACCENT, activebackground=ACCENT_HOVER)
            state = "readonly"
        self.cmb_mode.configure(state=state)
        self.cmb_fooling.configure(state=state)
        self.ent_dns.configure(state="disabled" if running else "normal")

    def _tick(self) -> None:
        s = self.engine.stats
        if self.engine.running:
            up = int(time.monotonic() - self._start_time)
            h, rem = divmod(up, 3600)
            m, sec = divmod(rem, 60)
            self.var_stats.set(
                f"Sure {h:02d}:{m:02d}:{sec:02d}   |   "
                f"Bertaraf edilen baglanti: {s.desynced}   |   "
                f"Sahte paket: {s.fakes_sent}   |   "
                f"QUIC dusurulen: {s.quic_dropped}   |   "
                f"DNS duzeltilen: {s.dns_fixed}")
        else:
            self.var_stats.set("Hazir - baslatmak icin BASLAT'a basin")
        self.after(1000, self._tick)

    # -- test --------------------------------------------------------------

    def run_test(self) -> None:
        """Engelin hangi katmanda oldugunu olcer. Motor kapaliyken calistirip
        acikken tekrarlamak, programin ise yarayip yaramadigini dogrudan
        gosterir."""
        self.btn_test.configure(state="disabled")

        def put(level, msg):
            self.log_queue.put((level, msg))

        def work():
            state = "ACIK" if self.engine.running else "KAPALI"
            put("head", f"Teshis basliyor (motor {state}). Bu 20-40 saniye surebilir.")
            try:
                core.diagnose_all(TEST_HOSTS, put)
            except Exception as exc:
                put("err", f"Teshis basarisiz: {exc}")
            finally:
                if not self.engine.running:
                    put("dim", "Ipucu: simdi BASLAT'a basip teshisi tekrarlayin. "
                               "Sonuclar degismiyorsa yontemi degistirin.")
                self.after(0, lambda: self.btn_test.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    # -- guncelleme --------------------------------------------------------

    def check_update(self, silent: bool) -> None:
        """Yeni surum arar. silent=True ise sadece yeni surum varsa konusur."""
        if not silent:
            self.btn_update.configure(state="disabled")
            self._write("info", "Guncelleme araniyor...")

        def work():
            rel, msg = guncelle.check()
            def done():
                self.btn_update.configure(state="normal")
                if rel is None:
                    if not silent:
                        self._write("dim" if "guncel" in msg else "warn", msg)
                    return
                self._write("ok", msg)
                self._offer_update(rel)
            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _offer_update(self, rel: guncelle.Release) -> None:
        notes = rel.notes[:400] + ("..." if len(rel.notes) > 400 else "")
        boyut = f"{rel.size / 1024 / 1024:.1f} MB" if rel.size else "bilinmiyor"
        if not messagebox.askyesno(
                APP_NAME,
                f"Yeni surum var: {rel.tag}\n"
                f"Kullandiginiz surum: v{APP_VERSION}\n"
                f"Indirme boyutu: {boyut}\n\n"
                f"{notes}\n\n"
                "Kurulum dosyasi indirilip calistirilsin mi?\n"
                "Program kapanacak ve kurulum penceresi acilacak."):
            self._write("dim", "Guncelleme iptal edildi.")
            return

        self.btn_update.configure(state="disabled")
        self._write("info", f"{rel.tag} indiriliyor...")

        last = [0]

        def progress(got, total):
            pct = int(got * 100 / total) if total else 0
            if pct >= last[0] + 10:
                last[0] = pct
                self.log_queue.put(("dim", f"  indiriliyor... %{pct}"))

        def work():
            try:
                path = guncelle.download(rel, progress)
            except Exception as exc:
                self.log_queue.put(("err", f"Indirme basarisiz: {exc}"))
                self.after(0, lambda: self.btn_update.configure(state="normal"))
                return
            self.after(0, lambda: self._launch_installer(path))

        threading.Thread(target=work, daemon=True).start()

    def _launch_installer(self, path: str) -> None:
        self._write("ok", f"Indirildi: {path}")
        self._write("info", "Kurulum baslatiliyor, program kapaniyor...")
        try:
            if self.engine.running:
                self.engine.stop()
            self._save_config()
            os.startfile(path)
        except Exception as exc:
            self._write("err", f"Kurulum baslatilamadi: {exc}")
            self.btn_update.configure(state="normal")
            return
        self.after(800, self.destroy)

    # -- kapanis -----------------------------------------------------------

    def _on_close(self) -> None:
        if self.engine.running:
            if not messagebox.askokcancel(
                    APP_NAME, "Motor calisiyor. Kapatilsin mi?\n"
                              "Kapattiginizda engeller geri gelebilir."):
                return
            self.engine.stop()
        self._save_config()
        self.destroy()


def main() -> None:
    if os.name != "nt":
        print("Bu program yalnizca Windows uzerinde calisir.")
        return

    if not core.is_admin():
        if ensure_admin():
            return  # yukseltilmis kopya acildi, bu kopyayi kapat

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    App().mainloop()


if __name__ == "__main__":
    main()
