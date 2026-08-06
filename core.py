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

APP_VERSION = "1.0"
REPO = "Efesploits/bertaraf"

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
    fooling: str = "badsum"          # badsum | badseq | ttl -> sahte paketi sunucuya
                                     # ulastirmama yontemi
    split_offset: int = 2            # SNI bulunamazsa kullanilacak bolme noktasi
    fake_ttl: int = 6                # fooling == "ttl" oldugunda kullanilir
    handle_http: bool = True         # 80. portu da isle
    block_quic: bool = True          # UDP/443 (QUIC) dusur -> TCP'ye zorla
    doh_fix: bool = True             # DNS cevaplarini DoH ile dogrusuyla degistir
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
    dns_fixed: int = 0


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------

DOH_SERVERS = [
    ("1.1.1.1", "/dns-query"),
    ("8.8.8.8", "/resolve"),
    ("9.9.9.9", "/dns-query"),
]


def resolve_doh(host: str, timeout: float = 8.0) -> list[str]:
    """Alan adini DNS-over-HTTPS ile cozer. Saglayicinin DNS'i devrede olmadigi
    icin zehirlenmis cevap donmez."""
    import json
    import urllib.request

    for server, path in DOH_SERVERS:
        try:
            req = urllib.request.Request(
                f"https://{server}{path}?name={host}&type=A",
                headers={"accept": "application/dns-json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                data = json.load(fh)
            ips = [a["data"] for a in data.get("Answer", [])
                   if a.get("type") == 1 and _looks_like_ipv4(a.get("data", ""))]
            if ips:
                return ips
        except Exception:
            continue
    return []


def _looks_like_ipv4(s: str) -> bool:
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def dns_parse_question(payload: bytes) -> tuple[str, int, int] | None:
    """DNS sorgusundaki alan adini, tipini ve soru bolumunun bittigi konumu
    dondurur."""
    try:
        if len(payload) < 12:
            return None
        qdcount = struct.unpack_from("!H", payload, 4)[0]
        if qdcount != 1:
            return None
        pos = 12
        labels = []
        while True:
            if pos >= len(payload):
                return None
            n = payload[pos]
            pos += 1
            if n == 0:
                break
            if n & 0xC0:                     # sikistirma sorguda beklenmez
                return None
            labels.append(payload[pos:pos + n].decode("ascii", "ignore"))
            pos += n
        if pos + 4 > len(payload):
            return None
        qtype = struct.unpack_from("!H", payload, pos)[0]
        return ".".join(labels), qtype, pos + 4
    except Exception:
        return None


def dns_build_response(query: bytes, qend: int, ips: list[str], ttl: int = 300) -> bytes:
    """Sorgunun kendisinden, verilen IP'leri iceren bir DNS cevabi kurar."""
    txid = query[:2]
    flags = struct.pack("!H", 0x8180)        # QR=1, RD=1, RA=1, RCODE=0
    counts = struct.pack("!HHHH", 1, len(ips), 0, 0)
    question = query[12:qend]

    answers = b""
    for ip in ips:
        rdata = bytes(int(x) for x in ip.split("."))
        answers += (b"\xc0\x0c"                       # soru adina isaretci
                    + struct.pack("!HHIH", 1, 1, ttl, 4)
                    + rdata)
    return txid + flags + counts + question + answers


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
        self._doh_cache: dict[str, list[str]] = {}
        self._doh_pending: set[str] = set()
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
        if self.settings.dns_redirect or self.settings.doh_fix:
            udp_parts.append("(outbound and udp.DstPort == 53)")
        if self.settings.dns_redirect:
            udp_parts.append("(inbound and udp.SrcPort == 53)")
        if udp_parts:
            udp_filter = "ip and udp and (" + " or ".join(udp_parts) + ")"
            self._spawn("UDP", udp_filter, self._udp_loop)

        if self.settings.doh_fix:
            threading.Thread(target=self._prefetch_doh, daemon=True,
                             name="m3sel-doh").start()

        self.log("Motor calisiyor. Discord'u simdi acabilirsin.", "ok")

    def _prefetch_doh(self) -> None:
        """Liste alan adlarinin gercek adreslerini onceden cozer, boylece ilk
        DNS sorgusu geldiginde cevap hazir olur."""
        hosts = [h for h in self.settings.hostlist if _is_discord(h)] or self.settings.hostlist
        found = 0
        for host in hosts:
            if not self._running.is_set():
                return
            ips = resolve_doh(host)
            if ips:
                with self._lock:
                    self._doh_cache[host.lower()] = ips
                found += 1
        if found:
            self.log(f"DoH: {found} alan adinin gercek adresi onceden alindi.", "dim")
        else:
            self.log("DoH sunucularina ulasilamadi, DNS duzeltmesi devre disi.", "warn")

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

        Sahte paket DPI'ye ulasmali ama sunucuya ulasmamalidir. Uc yontem:
          badsum : TCP saglama toplami kasten bozulur. Sunucu paketi atar,
                   DPI'lerin cogu saglama toplamini kontrol etmez. En saglami.
          badseq : Sira numarasi pencere disina alinir. Sunucu yok sayar.
          ttl    : Dusuk TTL ile gonderilir, sunucuya varmadan yolda olur."""
        try:
            fake_payload = build_fake_tls(max(64, length))
            fake = self._clone(packet, fake_payload, seq_delta=0)
            fooling = self.settings.fooling

            if fooling == "ttl":
                fake.ipv4.ttl = max(1, self.settings.fake_ttl)
                w.send(fake)

            elif fooling == "badsum":
                # Once dogru saglama toplamlarini hesaplat, sonra TCP'ninkini
                # boz ve ag yigininin duzeltmemesi icin "gecerli" olarak isaretle.
                fake.recalculate_checksums()
                cks = struct.unpack_from("!H", fake.tcp.raw, 16)[0]
                struct.pack_into("!H", fake.tcp.raw, 16, cks ^ 0xFFFF)
                fake.tcp_checksum = True
                fake.ip_checksum = True
                w.send(fake, recalculate_checksum=False)

            else:  # badseq
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

        # DoH duzeltmesi: saglayicinin zehirli cevabini beklemeden, dogru
        # adresi kendimiz cevaplariz.
        if s.doh_fix and packet.is_outbound and packet.dst_port == 53:
            if self._answer_from_doh(w, packet):
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

    def _answer_from_doh(self, w: pydivert.WinDivert, packet: Packet) -> bool:
        """Sorgu ilgilendigimiz bir alan adi icinse cevabi kendimiz uretip
        istemciye geri gonderir ve sorguyu dusurur. True donerse paket islendi."""
        q = dns_parse_question(packet.udp.payload or b"")
        if not q:
            return False
        host, qtype, qend = q
        if qtype not in (1, 28):             # sadece A ve AAAA
            return False
        if not self._in_hostlist(host):
            return False

        key = host.lower()
        with self._lock:
            ips = self._doh_cache.get(key)
            pending = key in self._doh_pending
            if ips is None and not pending:
                self._doh_pending.add(key)

        if ips is None:
            # Cevap henuz yok: sorguyu normal yoluna birak, arka planda coz.
            if not pending:
                threading.Thread(target=self._resolve_later, args=(key,),
                                 daemon=True).start()
            return False

        # AAAA sorgusuna bos cevap doneriz; istemci IPv4'e duser.
        answer = dns_build_response(packet.udp.payload, qend,
                                    ips if qtype == 1 else [])

        reply = Packet(bytearray(packet.raw.tobytes()), packet.interface,
                       pydivert.Direction.INBOUND)
        reply.src_addr, reply.dst_addr = packet.dst_addr, packet.src_addr
        reply.src_port, reply.dst_port = packet.dst_port, packet.src_port
        reply.udp.payload = answer
        w.send(reply)

        self.stats.dns_fixed += 1
        if self.settings.verbose or self.stats.dns_fixed <= 5:
            shown = ", ".join(ips[:2]) if qtype == 1 else "IPv4'e yonlendirildi"
            self.log(f"DNS duzeltildi: {host} -> {shown}", "ok")
        return True

    def _resolve_later(self, host: str) -> None:
        ips = resolve_doh(host)
        with self._lock:
            self._doh_pending.discard(host)
            if ips:
                self._doh_cache[host] = ips


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _resolve_system(host: str, tries: int = 3) -> list[str]:
    """Sistem cozumleyicisiyle cozer. Gecici hatayi kalici engelle karistirmamak
    icin birkac kez dener."""
    for i in range(tries):
        try:
            return sorted({r[4][0] for r in socket.getaddrinfo(host, 443, socket.AF_INET)})
        except Exception:
            if i < tries - 1:
                time.sleep(0.6)
    return []


def _tcp_ok(ip: str, port: int = 443, timeout: float = 6.0) -> tuple[bool, str]:
    try:
        start = time.perf_counter()
        with socket.create_connection((ip, port), timeout=timeout):
            return True, f"{(time.perf_counter() - start) * 1000:.0f} ms"
    except Exception as exc:
        return False, str(exc)


def _tls_ok(ip: str, sni: str | None, verify: bool = False,
            timeout: float = 8.0) -> tuple[bool, str]:
    """Belirtilen IP'ye baglanip istenen SNI ile TLS el sikismasi dener."""
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if verify:
        ctx.load_default_certs()
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((ip, 443), timeout=timeout) as raw:
            raw.settimeout(timeout)
            with ctx.wrap_socket(raw, server_hostname=sni) as tls:
                return True, tls.version() or "TLS"
    except Exception as exc:
        return False, type(exc).__name__ + ": " + str(exc)[:90]


def diagnose(host: str, log) -> str:
    """Engelin hangi katmanda oldugunu bulur: DNS mi, IP mi, SNI/DPI mi?

    log(seviye, mesaj) seklinde cagrilir. Donen deger kisa teshis kodudur:
    dns | ip | sni | yok | belirsiz
    """
    log("head", f"--- {host} teshisi ---")

    # 1) DNS karsilastirmasi -------------------------------------------------
    sys_ips = _resolve_system(host)
    doh_ips = resolve_doh(host)
    log("info", f"Sistem DNS : {', '.join(sys_ips) if sys_ips else 'cozulemedi'}")
    log("info", f"DoH (gercek): {', '.join(doh_ips) if doh_ips else 'ulasilamadi'}")

    if not doh_ips:
        log("warn", "DoH sunucularina ulasilamadi; karsilastirma yapilamiyor.")

    dns_poisoned = bool(sys_ips and doh_ips and not set(sys_ips) & set(doh_ips))
    if dns_poisoned:
        log("err", "Sistem DNS'i farkli adres donduruyor -> DNS zehirlenmesi.")
    elif sys_ips and doh_ips:
        log("ok", "DNS temiz (iki kaynak ayni adresi veriyor).")
    elif not sys_ips:
        log("err", "Sistem DNS alan adini hic cozemiyor -> DNS seviyesinde engel.")
        dns_poisoned = True

    target = (doh_ips or sys_ips)
    if not target:
        log("err", "Hicbir adres elde edilemedi, test durduruldu.")
        return "dns"
    ip = target[0]

    # 2) TCP -----------------------------------------------------------------
    tcp, detail = _tcp_ok(ip)
    if tcp:
        log("ok", f"TCP 443 baglantisi kuruldu ({ip}, {detail}).")
    else:
        log("err", f"TCP 443 baglantisi kurulamadi ({ip}): {detail}")
        log("err", "-> IP seviyesinde engel. Paket bolme bunu asamaz, VPN gerekir.")
        return "ip"

    # 3) TLS: gercek SNI vs zararsiz SNI -------------------------------------
    real_ok, real_err = _tls_ok(ip, host)
    fake_ok, _ = _tls_ok(ip, FAKE_SNI.decode())
    none_ok, _ = _tls_ok(ip, None)

    def row(label: str, ok: bool, extra: str = "") -> None:
        log("ok" if ok else "err",
            f"  {label:<26s}: {'BASARILI' if ok else 'BASARISIZ'}{extra}")

    row(f"TLS - {host}", real_ok, "" if real_ok else f"  [{real_err}]")
    row("TLS - zararsiz SNI", fake_ok)
    row("TLS - SNI gonderilmeden", none_ok)

    if real_ok:
        ver_ok, ver_err = _tls_ok(ip, host, verify=True)
        if not ver_ok:
            log("err", f"Sertifika dogrulanamadi -> araya girilmis olabilir. [{ver_err}]")
            return "sni"
        if dns_poisoned:
            log("warn", "Baglanti kuruluyor ama sistem DNS'i hala zehirli. "
                        "'DNS'i DoH ile duzelt' secenegi acik olmali.")
            return "dns"
        log("ok", "Bu alan adina erisim var. Engel bu katmanda degil.")
        return "yok"

    if fake_ok or none_ok:
        log("err", "Ayni IP'ye zararsiz SNI ile baglanilabiliyor ama gercek SNI ile "
                   "baglanilamiyor.")
        log("err", "-> SNI/DPI engeli. Program tam da bunu asmali; yontem degistirin.")
        return "sni"

    log("warn", "TLS hicbir SNI ile kurulamadi; sunucu veya ag tarafinda baska bir "
                "sorun olabilir.")
    return "belirsiz"


def diagnose_all(hosts: list[str], log) -> None:
    """Birden fazla alan adi icin teshis calistirir ve toplu sonuc yazar."""
    results = {}
    for host in hosts:
        try:
            results[host] = diagnose(host, log)
        except Exception as exc:
            log("err", f"{host} teshisi basarisiz: {exc}")
            results[host] = "belirsiz"

    log("head", "--- SONUC ---")
    codes = set(results.values())
    for host, code in results.items():
        label = {"dns": "DNS engeli", "ip": "IP engeli", "sni": "SNI/DPI engeli",
                 "yok": "erisim var", "belirsiz": "belirsiz"}[code]
        log("ok" if code == "yok" else "warn", f"  {host}: {label}")

    if codes == {"yok"}:
        log("ok", "Hicbir katmanda engel bulunamadi. Discord hala acilmiyorsa sorun "
                  "programda degil: Discord onbellegini silin (%appdata%\\discord\\Cache) "
                  "ve uygulamayi tepsiden tamamen kapatip yeniden acin.")
    elif "ip" in codes:
        log("err", "IP seviyesinde engel var. Paket bolme bu engeli asamaz; VPN gerekir.")
    elif "dns" in codes:
        log("warn", "DNS seviyesinde engel var. 'DNS'i DoH ile duzelt' secenegini acip "
                    "motoru yeniden baslatin, sonra teshisi tekrarlayin.")
    elif "sni" in codes:
        log("warn", "SNI/DPI engeli var. Motor calisirken bu testi tekrarlayin: hala "
                    "basarisizsa yontemi sirayla degistirin "
                    "(Discord -> Agresif -> Ters bolme -> Hafif) ve her denemede "
                    "yanilma yontemini de degistirin (badsum -> badseq -> ttl).")
