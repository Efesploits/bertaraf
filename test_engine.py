"""Motorun paket isleme mantigini sahte paketlerle test eder.

WinDivert surucusu acilmaz; gonderme islemi sahte bir tutamak (handle) ile
yakalanir. Boylece bolme, sira numarasi ve sahte paket mantigi yonetici
yetkisi olmadan dogrulanabilir.
"""
import queue
import struct

import pydivert
from pydivert import Packet

import core
from test_core import make_client_hello


class FakeHandle:
    """w.send() cagrilarini toplayan sahte WinDivert tutamagi."""

    def __init__(self):
        self.sent: list[Packet] = []

    def send(self, packet, recalculate_checksum=True):
        if recalculate_checksum:
            packet.recalculate_checksums()
        self.sent.append(packet)
        return len(packet.raw)


def build_tcp_packet(payload: bytes, seq: int = 1000, dst_port: int = 443) -> Packet:
    """Gecerli bir IPv4 + TCP (PSH,ACK) paketi olusturur."""
    ip_hdr = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0x00, 20 + 20 + len(payload),
        0x1234, 0x4000, 64, 6, 0,
        bytes([192, 168, 1, 50]), bytes([162, 159, 128, 233]),
    )
    tcp_hdr = struct.pack(
        "!HHIIBBHHH",
        51234, dst_port, seq, 0xAABBCCDD,
        (5 << 4), 0x18, 64240, 0, 0,
    )
    return Packet(bytearray(ip_hdr + tcp_hdr + payload), (1, 0), pydivert.Direction.OUTBOUND)


def make_engine(**kw) -> core.Engine:
    s = core.Settings(**kw)
    return core.Engine(s, queue.Queue())


def drain(eng):
    out = []
    while True:
        try:
            out.append(eng.log_queue.get_nowait()[1])
        except queue.Empty:
            return out


def test_split_preserves_stream():
    hello = make_client_hello("discord.com")
    pkt = build_tcp_packet(hello, seq=5000)
    eng = make_engine(mode="split")
    w = FakeHandle()
    eng._handle_tcp(w, pkt)

    assert len(w.sent) == 2, f"2 paket beklendi, {len(w.sent)} geldi"
    a, b = w.sent
    assert a.tcp.payload + b.tcp.payload == hello, "veri akisi bozuldu"
    assert a.tcp.seq_num == 5000
    assert b.tcp.seq_num == 5000 + len(a.tcp.payload), "ikinci parcanin sira numarasi yanlis"
    assert b"discord.com" not in a.tcp.payload and b"discord.com" not in b.tcp.payload
    assert a.ipv4.packet_len == len(a.raw) and b.ipv4.packet_len == len(b.raw), "IP uzunlugu guncellenmemis"
    assert a.is_checksum_valid and b.is_checksum_valid, "checksum gecersiz"
    print(f"  OK  bolme {len(a.tcp.payload)}+{len(b.tcp.payload)}B, seq {a.tcp.seq_num}/{b.tcp.seq_num}, checksum gecerli")


def test_disorder_order():
    hello = make_client_hello("gateway.discord.gg")
    pkt = build_tcp_packet(hello, seq=7000)
    eng = make_engine(mode="disorder")
    w = FakeHandle()
    eng._handle_tcp(w, pkt)

    a, b = w.sent
    assert a.tcp.seq_num > b.tcp.seq_num, "ters sirada gonderilmemis"
    assert b.tcp.payload + a.tcp.payload == hello, "veri akisi bozuldu"
    print(f"  OK  ters sira: once seq={a.tcp.seq_num}, sonra seq={b.tcp.seq_num}")


def test_fake_badseq():
    hello = make_client_hello("discord.com")
    pkt = build_tcp_packet(hello, seq=100000)
    eng = make_engine(mode="fake_disorder", fooling="badseq")
    w = FakeHandle()
    eng._handle_tcp(w, pkt)

    assert len(w.sent) == 3, f"sahte + 2 parca = 3 paket beklendi, {len(w.sent)} geldi"
    fake = w.sent[0]
    assert fake.tcp.seq_num == (100000 - 0x10000) & 0xFFFFFFFF, "sahte paket badseq degil"
    assert core.parse_client_hello(fake.tcp.payload)[0] == core.FAKE_SNI.decode()
    assert fake.is_checksum_valid, "sahte paketin checksumi gecersiz"
    real = w.sent[1].tcp.payload + w.sent[2].tcp.payload
    assert w.sent[2].tcp.payload + w.sent[1].tcp.payload == hello or real == hello
    assert eng.stats.fakes_sent == 1 and eng.stats.desynced == 1
    print(f"  OK  sahte paket seq={fake.tcp.seq_num} (gercek {100000}), SNI={core.FAKE_SNI.decode()}")


def test_fake_ttl_mode():
    hello = make_client_hello("discord.com")
    pkt = build_tcp_packet(hello)
    eng = make_engine(mode="fake_split", fooling="ttl", fake_ttl=6)
    w = FakeHandle()
    eng._handle_tcp(w, pkt)
    assert w.sent[0].ipv4.ttl == 6, "TTL ayarlanmamis"
    assert w.sent[1].ipv4.ttl == 64, "gercek paketin TTL'i bozulmus"
    print("  OK  dusuk TTL modu: sahte paket ttl=6, gercek paket ttl=64")


def test_non_tls_passthrough():
    pkt = build_tcp_packet(b"\x17\x03\x03\x00\x20" + b"A" * 32, seq=42)
    eng = make_engine()
    w = FakeHandle()
    eng._handle_tcp(w, pkt)
    assert len(w.sent) == 1 and w.sent[0].tcp.payload == pkt.tcp.payload
    assert eng.stats.desynced == 0
    print("  OK  TLS disi trafik degistirilmeden gecti")


def test_hostlist_filter():
    hello = make_client_hello("bankam.example")
    pkt = build_tcp_packet(hello)
    eng = make_engine(only_hostlist=True, mode="split")
    w = FakeHandle()
    eng._handle_tcp(w, pkt)
    assert len(w.sent) == 1, "liste disi site bertaraf edilmemeli"

    hello2 = make_client_hello("cdn.discordapp.com")
    pkt2 = build_tcp_packet(hello2)
    w2 = FakeHandle()
    eng._handle_tcp(w2, pkt2)
    assert len(w2.sent) == 2, "listedeki site bertaraf edilmeli"
    print("  OK  liste modu: liste disi gecti, listedeki bolundu")


def test_http_split():
    req = b"GET /api/v9/science HTTP/1.1\r\nHost: discord.com\r\nAccept: */*\r\n\r\n"
    pkt = build_tcp_packet(req, seq=900, dst_port=80)
    eng = make_engine(mode="split", handle_http=True)
    w = FakeHandle()
    eng._handle_tcp(w, pkt)
    a, b = w.sent
    assert a.tcp.payload + b.tcp.payload == req
    assert b"discord.com" not in a.tcp.payload and b"discord.com" not in b.tcp.payload
    print(f"  OK  HTTP bolme {len(a.tcp.payload)}+{len(b.tcp.payload)}B")


def test_quic_drop():
    eng = make_engine(block_quic=True, dns_redirect=False)
    ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + 8 + 4, 1, 0, 64, 17, 0,
                         bytes([192, 168, 1, 50]), bytes([162, 159, 128, 233]))
    udp_hdr = struct.pack("!HHHH", 55000, 443, 12, 0)
    pkt = Packet(bytearray(ip_hdr + udp_hdr + b"QUIC"), (1, 0), pydivert.Direction.OUTBOUND)
    w = FakeHandle()
    eng._handle_udp(w, pkt)
    assert len(w.sent) == 0 and eng.stats.quic_dropped == 1
    print("  OK  QUIC paketi dusuruldu")


def test_dns_redirect_roundtrip():
    eng = make_engine(block_quic=False, dns_redirect=True, dns_server="1.1.1.1")
    ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + 8 + 4, 1, 0, 64, 17, 0,
                         bytes([192, 168, 1, 50]), bytes([195, 175, 39, 49]))
    out = Packet(bytearray(ip_hdr + struct.pack("!HHHH", 61000, 53, 12, 0) + b"\x00\x01\x00\x00"),
                 (1, 0), pydivert.Direction.OUTBOUND)
    w = FakeHandle()
    eng._handle_udp(w, out)
    assert w.sent[0].dst_addr == "1.1.1.1", "DNS yonlendirilmedi"
    assert eng.stats.dns_redirected == 1

    ip_in = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + 8 + 4, 1, 0, 64, 17, 0,
                        bytes([1, 1, 1, 1]), bytes([192, 168, 1, 50]))
    inb = Packet(bytearray(ip_in + struct.pack("!HHHH", 53, 61000, 12, 0) + b"\x00\x01\x00\x00"),
                 (1, 0), pydivert.Direction.INBOUND)
    w2 = FakeHandle()
    eng._handle_udp(w2, inb)
    assert w2.sent[0].src_addr == "195.175.39.49", "cevap adresi geri yazilmadi"
    print("  OK  DNS 1.1.1.1'e yonlendirildi, cevap adresi geri yazildi")


def test_fake_badsum():
    """Sahte paketin TCP saglama toplami kasten bozuk, gercek parcalarinki
    dogru olmali. Aksi halde sunucu gercek veriyi de atar."""
    hello = make_client_hello("discord.com")
    pkt = build_tcp_packet(hello, seq=4242)
    eng = make_engine(mode="fake_split", fooling="badsum")
    w = FakeHandle()
    eng._handle_tcp(w, pkt)

    fake, a, b = w.sent
    assert not fake.is_checksum_valid, "sahte paketin saglama toplami bozulmamis"
    assert fake.tcp.seq_num == 4242, "badsum modunda sira numarasi degismemeli"
    assert a.is_checksum_valid and b.is_checksum_valid, "gercek parcalar bozulmus"
    assert a.tcp.payload + b.tcp.payload == hello, "veri akisi bozuldu"
    print("  OK  badsum: sahte paket bozuk, gercek parcalar saglam")


def test_dns_question_parse():
    q = (b"\xab\xcd\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
         b"\x07discord\x03com\x00\x00\x01\x00\x01")
    res = core.dns_parse_question(q)
    assert res == ("discord.com", 1, len(q)), res
    assert core.dns_parse_question(b"\x00" * 4) is None
    print(f"  OK  DNS sorgusu ayristirildi: {res[0]} tip={res[1]}")


def test_dns_response_build():
    q = (b"\xab\xcd\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
         b"\x07discord\x03com\x00\x00\x01\x00\x01")
    host, qtype, qend = core.dns_parse_question(q)
    ips = ["162.159.128.233", "162.159.135.232"]
    r = core.dns_build_response(q, qend, ips)

    assert r[:2] == q[:2], "islem numarasi korunmadi"
    assert struct.unpack_from("!H", r, 2)[0] == 0x8180, "cevap bayraklari yanlis"
    assert struct.unpack_from("!H", r, 6)[0] == len(ips), "cevap sayisi yanlis"
    assert r[12:qend] == q[12:qend], "soru bolumu aynen kopyalanmadi"
    body = r[qend:]
    for i, ip in enumerate(ips):
        rec = body[i * 16:(i + 1) * 16]
        assert rec[:2] == b"\xc0\x0c", "ad isaretcisi yok"
        assert struct.unpack_from("!HH", rec, 2) == (1, 1), "tip/sinif yanlis"
        assert ".".join(str(x) for x in rec[12:16]) == ip, "IP yanlis kodlandi"
    print(f"  OK  DNS cevabi kuruldu: {len(ips)} A kaydi, {len(r)} bayt")


def _dns_query_packet(host: str, qtype: int = 1) -> Packet:
    qname = b"".join(bytes([len(p)]) + p.encode() for p in host.split(".")) + b"\x00"
    dns = (b"\xab\xcd\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
           + qname + struct.pack("!HH", qtype, 1))
    ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + 8 + len(dns), 1, 0, 64, 17, 0,
                         bytes([192, 168, 1, 50]), bytes([195, 175, 39, 49]))
    udp_hdr = struct.pack("!HHHH", 61000, 53, 8 + len(dns), 0)
    return Packet(bytearray(ip_hdr + udp_hdr + dns), (1, 0), pydivert.Direction.OUTBOUND)


def test_doh_answer_injected():
    """Onbellekte adresi olan bir alan adi sorulunca sorgu dusurulmeli ve
    istemciye dogru cevap enjekte edilmeli."""
    eng = make_engine(doh_fix=True, dns_redirect=False, block_quic=False)
    eng._doh_cache["discord.com"] = ["162.159.128.233"]

    pkt = _dns_query_packet("discord.com")
    w = FakeHandle()
    eng._handle_udp(w, pkt)

    assert len(w.sent) == 1, "cevap enjekte edilmedi"
    r = w.sent[0]
    assert r.is_inbound, "cevap istemciye dogru gonderilmiyor"
    assert r.src_addr == "195.175.39.49" and r.dst_addr == "192.168.1.50", "adresler ters"
    assert r.src_port == 53 and r.dst_port == 61000, "portlar ters"
    host, qtype, qend = core.dns_parse_question(r.udp.payload)
    assert host == "discord.com"
    assert r.udp.payload[qend + 12:qend + 16] == bytes([162, 159, 128, 233])
    assert eng.stats.dns_fixed == 1
    print("  OK  DoH cevabi enjekte edildi, sorgu dusuruldu")


def test_doh_aaaa_empty():
    """AAAA sorgusuna bos cevap donmeli ki istemci IPv4 kullansin."""
    eng = make_engine(doh_fix=True, dns_redirect=False, block_quic=False)
    eng._doh_cache["discord.com"] = ["162.159.128.233"]
    w = FakeHandle()
    eng._handle_udp(w, _dns_query_packet("discord.com", qtype=28))
    assert len(w.sent) == 1
    assert struct.unpack_from("!H", w.sent[0].udp.payload, 6)[0] == 0, "AAAA bos degil"
    print("  OK  AAAA sorgusuna bos cevap donduruldu")


def test_doh_ignores_other_hosts():
    """Liste disi alan adlarina dokunulmamali."""
    eng = make_engine(doh_fix=True, dns_redirect=False, block_quic=False)
    w = FakeHandle()
    eng._handle_udp(w, _dns_query_packet("bankam.example"))
    assert len(w.sent) == 1 and w.sent[0].is_outbound, "liste disi sorgu degistirilmis"
    assert eng.stats.dns_fixed == 0
    print("  OK  liste disi DNS sorgusu dokunulmadan gecti")


def test_broken_packet_does_not_crash():
    pkt = build_tcp_packet(b"\x16\x03\x01\xff\xff" + b"\x01" + b"\x00" * 10)
    eng = make_engine()
    w = FakeHandle()
    eng._handle_tcp(w, pkt)
    assert len(w.sent) == 1, "bozuk TLS paketi oldugu gibi gecmeliydi"
    print("  OK  bozuk/eksik TLS paketi cokmeden gecti")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"\n{len(tests)} test gecti.")
