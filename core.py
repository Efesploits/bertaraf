"""
M3sel Bertaraf - DPI bertaraf motoru
=====================================
GoodbyeDPI mantiginin Python/WinDivert ile yeniden yazilmis hali.

Calisma mantigi: Giden TCP paketleri WinDivert ile yakalanir, TLS ClientHello
paketi tespit edilirse SNI alan adi ortadan ikiye bolunerek (ve istege bagli
olarak sahte bir on paket gonderilerek) DPI'nin alan adini gormesi engellenir.
Paketler sunucuya normal TCP akisi olarak ulasir, sansur kutusu ise alan adini
tek parcada goremedigi icin baglantiyi kesemez.
"""

from __future__ import annotations

import ctypes
import queue
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

import pydivert
from pydivert import Packet
from pydivert import windivert_dll

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

TLS_HANDSHAKE = 0x16
TLS_CLIENT_HELLO = 0x01
EXT_SERVER_NAME = 0x0000

SHUTDOWN_BOTH = 3

# Sahte paket icin kullanilan zararsiz ClientHello govdesi (sunucu bunu
# gecersiz sira numarasi yuzunden atar, DPI ise "gordum" diye isaretler).
FAKE_SNI = b"www.microsoft.com"

DISCORD_HINTS = (
    "discord",
    "discordapp",
    "discord.media",
    "discordcdn",
    "discord.gg",
)

# Turkiye'de sik erisim sorunu yasanan alan adlari (varsayilan liste modu icin).
DEFAULT_HOSTLIST = [
    "discord.com",
    "discordapp.com",
    "discordapp.net",
    "discord.gg",
    "discord.media",
    "discord-attachments-uploads-prd.storage.googleapis.com",
    "gateway.discord.gg",
    "cdn.discordapp.com",
    "media.discordapp.net",
    "images-ext-1.discordapp.net",
    "images-ext-2.discordapp.net",
    "youtube.com",
    "googlevideo.com",
    "ytimg.com",
    "wikipedia.org",
    "medium.com",
    "soundcloud.com",
]


# ---------------------------------------------------------------------------
# Ayarlar
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    """Motor ayarlari. GUI bunlari degistirip motoru yeniden baslatir."""

    mode: str = "fake_disorder"      # split | disorder | fake_split | fake_disorder
    split_offset: int = 2            # SNI bulunamazsa kullanilacak bolme noktasi
    fake_ttl: int = 0                # 0 = badseq yontemi, >0 = dusuk TTL yontemi
    handle_http: bool = True         # 80. portu da isle
    block_quic: bool = True          # UDP/443 (QUIC) dusur -> TCP'ye zorla
    dns_redirect: bool = True        # DNS'i saglayici disina yonlendir
    dns_server: str = "1.1.1.1"
    only_hostlist: bool = False      # sadece listedeki alan adlarina uygula
    hostlist: list[str] = field(default_factory=lambda: list(DEFAULT_HOSTLIST))
    verbose: bool = False            # her paket icin log


@dataclass
class Stats:
    connections: int = 0
    desynced: int = 0
    fakes_sent: int = 0
    quic_dropped: int = 0
    dns_redirected: int = 0


# ---------------------------------------------------------------------------
# TLS ayristirma
# ---------------------------------------------------------------------------

def parse_client_hello(data: bytes) -> tuple[str, int] | None:
    """TLS ClientHello icindeki SNI alan adini ve paket icindeki byte konumunu
    dondurur. ClientHello degilse veya SNI yoksa None doner."""
    try:
        if len(data) < 45 or data[0] != TLS_HANDSHAKE:
            return None
        if data[1] != 0x03:  # TLS surumu major
            return None

        rec_len = struct.unpack_from("!H", data, 3)[0]
        body_start = 5
        body_end = min(len(data), body_start + rec_len)
        body = data[body_start:body_end]

        if not body or body[0] != TLS_CLIENT_HELLO:
            return None

        pos = 4          # handshake tipi (1) + uzunluk (3)
        pos += 2         # client_version
        pos += 32        # random
        if pos >= len(body):
            return None

        sid_len = body[pos]
        pos += 1 + sid_len

        if pos + 2 > len(body):
            return None
        cs_len = struct.unpack_from("!H", body, pos)[0]
        pos += 2 + cs_len

        if pos >= len(body):
            return None
        cm_len = body[pos]
        pos += 1 + cm_len

        if pos + 2 > len(body):
            return None
        ext_total = struct.unpack_from("!H", body, pos)[0]
        pos += 2
        ext_end = min(len(body), pos + ext_total)

        while pos + 4 <= ext_end:
            etype = struct.unpack_from("!H", body, pos)[0]
            elen = struct.unpack_from("!H", body, pos + 2)[0]
            pos += 4
            if etype == EXT_SERVER_NAME and elen >= 5:
                p = pos + 2          # server_name_list uzunlugu
                p += 1               # name_type
                nlen = struct.unpack_from("!H", body, p)[0]
                p += 2
                name = body[p:p + nlen].decode("ascii", "ignore")
                if name:
                    return name, body_start + p
                return None
            pos += elen
    except Exception:
        return None
    return None


def parse_http_host(data: bytes) -> tuple[str, int] | None:
    """Duz HTTP istegindeki Host basligini ve konumunu dondurur."""
    try:
        head = data[:1024]
        low = head.lower()
        idx = low.find(b"\r\nhost:")
        if idx < 0:
            return None
        vstart = idx + 7
        while vstart < len(head) and head[vstart:vstart + 1] == b" ":
            vstart += 1
        vend = low.find(b"\r\n", vstart)
        if vend < 0:
            return None
        host = head[vstart:vend].decode("ascii", "ignore").strip()
        if not host:
            return None
        return host, vstart
    except Exception:
        return None


def build_fake_tls(length: int) -> bytes:
    """Gercek paketle ayni uzunlukta, zararsiz bir sahte ClientHello uretir."""
    sni = FAKE_SNI
    sni_body = b"\x00" + struct.pack("!H", len(sni)) + sni
    sni_list = struct.pack("!H", len(sni_body)) + sni_body
    ext_sni = struct.pack("!HH", EXT_SERVER_NAME, len(sni_list)) + sni_list

    exts = ext_sni
    hello = (
        b"\x03\x03"                       # client_version TLS1.2
        + b"\x11" * 32                    # random
        + b"\x00"                         # session_id uzunlugu
        + struct.pack("!H", 2) + b"\x13\x01"   # cipher suites
        + b"\x01\x00"                     # compression
        + struct.pack("!H", len(exts)) + exts
    )
    hs = b"\x01" + struct.pack("!I", len(hello))[1:] + hello
    rec = b"\x16\x03\x01" + struct.pack("!H", len(hs)) + hs

    if len(rec) < length:
        rec = rec + b"\x00" * (length - len(rec))
    return rec[:length]


def _is_discord(host: str) -> bool:
    h = host.lower()
    return any(x in h for x in DISCORD_HINTS)


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

class Engine:
    """DPI bertaraf motoru. start() cagrildiginda arka planda calisir,
    log satirlarini verilen kuyruga birakir."""

    def __init__(self, settings: Settings, log_queue: "queue.Queue[tuple[str, str]]"):
        self.settings = settings
        self.log_queue = log_queue
        self.stats = Stats()

        self._threads: list[threading.Thread] = []
        self._handles: list[pydivert.WinDivert] = []
        self._running = threading.Event()
        self._dns_map: dict[int, tuple[str, int]] = {}
        self._lock = threading.Lock()

    # -- log ---------------------------------------------------------------

    def log(self, msg: str, level: str = "info") -> None:
        try:
            self.log_queue.put_nowait((level, msg))
        except queue.Full:
            pass

    # -- yasam dongusu -----------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._running.set()
        self.stats = Stats()
        self._dns_map.clear()

        # Filtreyi surucu seviyesinde daraltiyoruz: yalnizca TLS ClientHello ve
        # HTTP istek satiriyla baslayan paketler Python tarafina dusuyor.
        # Boylece normal veri trafigi cekirdekte kalir, hiz kaybi olmaz.
        parts = [
            "(tcp.DstPort == 443 and tcp.PayloadLength > 5"
            " and tcp.Payload[0] == 0x16 and tcp.Payload[1] == 0x03"
            " and tcp.Payload[5] == 0x01)"
        ]
        if self.settings.handle_http:
            parts.append(
                "(tcp.DstPort == 80 and tcp.PayloadLength > 16 and ("
                "tcp.Payload[0] == 0x47 or tcp.Payload[0] == 0x50 or"      # GET / POST,PUT,PATCH
                " tcp.Payload[0] == 0x48 or tcp.Payload[0] == 0x44 or"     # HEAD / DELETE
                " tcp.Payload[0] == 0x4F or tcp.Payload[0] == 0x43))"      # OPTIONS / CONNECT
            )
        tcp_filter = "outbound and ip and tcp and (" + " or ".join(parts) + ")"

        self._spawn("TCP", tcp_filter, self._tcp_loop)

        udp_parts = []
        if self.settings.block_quic:
            udp_parts.append("(outbound and udp.DstPort == 443)")
        if self.settings.dns_redirect:
            udp_parts.append("(outbound and udp.DstPort == 53)")
            udp_parts.append("(inbound and udp.SrcPort == 53)")
        if udp_parts:
            udp_filter = "ip and udp and (" + " or ".join(udp_parts) + ")"
            self._spawn("UDP", udp_filter, self._udp_loop)

        self.log("Motor calisiyor. Discord'u simdi acabilirsin.", "ok")

    def _spawn(self, name: str, filt: str, target) -> None:
        ok, _, err = pydivert.WinDivert.check_filter(filt)
        if not ok:
            self._running.clear()
            raise RuntimeError(f"{name} filtresi gecersiz: {err}")

        try:
            handle = pydivert.WinDivert(filt)
            handle.open()
        except Exception as exc:
            self._running.clear()
            raise RuntimeError(
                f"WinDivert surucusu acilamadi ({name}): {exc}\n\n"
                "Olasi nedenler:\n"
                "  - Program yonetici olarak calistirilmadi\n"
                "  - Baska bir DPI araci (GoodbyeDPI, zapret, ByeDPI) acik\n"
                "  - Antivirus WinDivert surucusunu engelliyor"
            ) from exc

        self._handles.append(handle)
        t = threading.Thread(target=target, args=(handle,), name=f"m3sel-{name}", daemon=True)
        t.start()
        self._threads.append(t)
        self.log(f"{name} filtresi acildi: {filt}", "dim")

    def stop(self) -> None:
        if not self.running:
            return
        self._running.clear()
        for h in self._handles:
            try:
                if h.is_open:
                    windivert_dll.WinDivertShutdown(h._handle, SHUTDOWN_BOTH)
            except Exception:
                pass
        for h in self._handles:
            try:
                if h.is_open:
                    h.close()
            except Exception:
                pass
        for t in self._threads:
            t.join(timeout=2.0)
        self._handles.clear()
        self._threads.clear()
        self.log("Motor durduruldu.", "warn")

    # -- TCP ---------------------------------------------------------------

    def _tcp_loop(self, w: pydivert.WinDivert) -> None:
        while self._running.is_set():
            try:
                packet = w.recv()
            except Exception:
                if self._running.is_set():
                    self.log("TCP dinleyici kapandi.", "warn")
                break
            try:
                self._handle_tcp(w, packet)
            except Exception as exc:
                self.log(f"Paket islenemedi, oldugu gibi gonderildi: {exc}", "warn")
                try:
                    w.send(packet)
                except Exception:
                    pass

    def _handle_tcp(self, w: pydivert.WinDivert, packet: Packet) -> None:
        payload = packet.tcp.payload
        if not payload:
            w.send(packet)
            return

        host = None
        offset = None
        if packet.dst_port == 443:
            found = parse_client_hello(payload)
            if found:
                host, offset = found
        elif packet.dst_port == 80:
            found = parse_http_host(payload)
            if found:
                host, offset = found

        if host is None:
            # Ilgilenmedigimiz paket: dokunmadan gecir.
            w.send(packet)
            return

        if self.settings.only_hostlist and not self._in_hostlist(host):
            if self.settings.verbose:
                self.log(f"atlandi (liste disi): {host}", "dim")
            w.send(packet)
            return

        self.stats.connections += 1
        try:
            self._desync(w, packet, payload, offset, host)
        except Exception:
            w.send(packet)
            raise

    def _in_hostlist(self, host: str) -> bool:
        h = host.lower()
        return any(h == d or h.endswith("." + d) or d in h
                   for d in self.settings.hostlist)

    def _desync(self, w: pydivert.WinDivert, packet: Packet,
                payload: bytes, sni_offset: int, host: str) -> None:
        """Paketi bol, gerekirse sahte on paket gonder."""
        mode = self.settings.mode

        # Bolme noktasi: SNI alan adinin tam ortasi.
        split = sni_offset + max(1, len(host) // 2)
        if split <= 0 or split >= len(payload):
            split = min(self.settings.split_offset, max(1, len(payload) - 1))

        first = payload[:split]
        second = payload[split:]

        sent_fake = False
        if mode.startswith("fake"):
            self._send_fake(w, packet, len(payload))
            sent_fake = True

        p1 = self._clone(packet, first, seq_delta=0)
        p2 = self._clone(packet, second, seq_delta=split)

        if mode.endswith("disorder"):
            w.send(p2)
            w.send(p1)
            order = "ters"
        else:
            w.send(p1)
            w.send(p2)
            order = "duz"

        self.stats.desynced += 1
        tag = "discord" if _is_discord(host) else "ok"
        detail = f"{len(first)}+{len(second)} bayt, {order}"
        if sent_fake:
            detail += ", sahte on paket"
        self.log(f"{host} -> bertaraf edildi ({detail})", tag)

    def _clone(self, packet: Packet, data: bytes, seq_delta: int) -> Packet:
        """Orijinal paketten yeni bir TCP segmenti uretir."""
        new = Packet(
            bytearray(packet.raw.tobytes()),
            packet.interface,
            packet.direction,
        )
        new.tcp.payload = data
        if seq_delta:
            new.tcp.seq_num = (packet.tcp.seq_num + seq_delta) & 0xFFFFFFFF
        return new

    def _send_fake(self, w: pydivert.WinDivert, packet: Packet, length: int) -> None:
        """DPI'yi yaniltmak icin sahte bir ClientHello gonderir.

        fake_ttl > 0 ise paket dusuk TTL ile gonderilir (sansur kutusunu gecer,
        sunucuya varmadan olur). Aksi halde gecersiz sira numarasi kullanilir;
        sunucu paketi pencere disi kabul edip sessizce atar."""
        try:
            fake_payload = build_fake_tls(max(64, length))
            fake = self._clone(packet, fake_payload, seq_delta=0)

            if self.settings.fake_ttl > 0:
                fake.ipv4.ttl = self.settings.fake_ttl
            else:
                fake.tcp.seq_num = (packet.tcp.seq_num - 0x10000) & 0xFFFFFFFF

            w.send(fake)
            self.stats.fakes_sent += 1
        except Exception as exc:
            self.log(f"Sahte paket gonderilemedi: {exc}", "warn")

    # -- UDP ---------------------------------------------------------------

    def _udp_loop(self, w: pydivert.WinDivert) -> None:
        while self._running.is_set():
            try:
                packet = w.recv()
            except Exception:
                if self._running.is_set():
                    self.log("UDP dinleyici kapandi.", "warn")
                break
            try:
                self._handle_udp(w, packet)
            except Exception as exc:
                self.log(f"UDP paketi islenemedi: {exc}", "warn")
                try:
                    w.send(packet)
                except Exception:
                    pass

    def _handle_udp(self, w: pydivert.WinDivert, packet: Packet) -> None:
        s = self.settings

        # QUIC: dusur ki tarayici/uygulama TCP+TLS'e dussun (orayi zaten
        # bertaraf ediyoruz). Paketi hic gondermiyoruz.
        if s.block_quic and packet.is_outbound and packet.dst_port == 443:
            self.stats.quic_dropped += 1
            if s.verbose and self.stats.quic_dropped % 50 == 1:
                self.log(f"QUIC dusuruldu ({self.stats.quic_dropped} paket)", "dim")
            return

        if s.dns_redirect:
            if packet.is_outbound and packet.dst_port == 53:
                if packet.dst_addr != s.dns_server:
                    with self._lock:
                        self._dns_map[packet.src_port] = (packet.dst_addr, packet.dst_port)
                    packet.dst_addr = s.dns_server
                    self.stats.dns_redirected += 1
                    if s.verbose:
                        self.log(f"DNS sorgusu {s.dns_server} adresine yonlendirildi", "dim")
                w.send(packet)
                return

            if packet.is_inbound and packet.src_port == 53:
                with self._lock:
                    orig = self._dns_map.get(packet.dst_port)
                if orig and packet.src_addr == s.dns_server:
                    packet.src_addr = orig[0]
                w.send(packet)
                return

        w.send(packet)


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def quick_check(host: str = "discord.com", port: int = 443, timeout: float = 5.0) -> tuple[bool, str]:
    """Basit TLS erisim testi: baglanti kuruluyor mu?"""
    try:
        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(b"\x16\x03\x01\x00\x01\x01")
            ms = (time.perf_counter() - start) * 1000
        return True, f"{host}:{port} erisilebilir ({ms:.0f} ms)"
    except Exception as exc:
        return False, f"{host}:{port} erisilemiyor: {exc}"
