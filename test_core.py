"""core.py icin bagimsiz testler (ag/surucu gerektirmez)."""
import ssl
import core


def make_client_hello(host: str) -> bytes:
    ctx = ssl.create_default_context()
    inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
    obj = ctx.wrap_bio(inc, out, server_hostname=host)
    try:
        obj.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return out.read()


def test_sni_parse():
    for host in ("discord.com", "gateway.discord.gg", "cdn.discordapp.com", "a.b.c.example.org"):
        data = make_client_hello(host)
        res = core.parse_client_hello(data)
        assert res is not None, f"SNI bulunamadi: {host}"
        name, off = res
        assert name == host, f"{name} != {host}"
        assert data[off:off + len(host)].decode() == host, "konum yanlis"
        print(f"  OK  SNI={name!r} offset={off} paket={len(data)}B")


def test_fake_packet():
    fake = core.build_fake_tls(517)
    assert len(fake) == 517
    res = core.parse_client_hello(fake)
    assert res is not None, "sahte paket ClientHello olarak ayristirilamadi"
    print(f"  OK  sahte paket SNI={res[0]!r} uzunluk={len(fake)}B")


def test_http_host():
    req = b"GET /api HTTP/1.1\r\nUser-Agent: x\r\nHost: discord.com\r\nAccept: */*\r\n\r\n"
    res = core.parse_http_host(req)
    assert res == ("discord.com", req.index(b"discord.com")), res
    print(f"  OK  HTTP Host={res[0]!r} offset={res[1]}")


def test_split_math():
    host = "discord.com"
    data = make_client_hello(host)
    name, off = core.parse_client_hello(data)
    split = off + max(1, len(name) // 2)
    first, second = data[:split], data[split:]
    assert first + second == data, "bolme kayipsiz degil"
    assert host.encode() not in first, "alan adi ilk parcada butun halde kalmis"
    assert host.encode() not in second, "alan adi ikinci parcada butun halde kalmis"
    print(f"  OK  bolme {len(first)}+{len(second)}B, alan adi iki parcaya ayrildi")


def test_non_tls_ignored():
    assert core.parse_client_hello(b"GET / HTTP/1.1\r\n\r\n") is None
    assert core.parse_client_hello(b"\x17\x03\x03\x00\x10" + b"\x00" * 16) is None
    assert core.parse_client_hello(b"") is None
    print("  OK  TLS disi trafik yok sayiliyor")


def test_hostlist():
    eng = core.Engine.__new__(core.Engine)
    eng.settings = core.Settings()
    assert eng._in_hostlist("gateway.discord.gg")
    assert eng._in_hostlist("cdn.discordapp.com")
    assert not eng._in_hostlist("bankam.com.tr")
    print("  OK  alan adi listesi eslesmesi")


if __name__ == "__main__":
    for fn in (test_sni_parse, test_fake_packet, test_http_host,
               test_split_math, test_non_tls_ignored, test_hostlist):
        print(fn.__name__)
        fn()
    print("\nTum testler gecti.")
