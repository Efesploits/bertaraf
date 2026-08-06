"""guncelle.py testleri: surum karsilastirma, adres denetimi, cevap ayristirma."""
import io
import json
import urllib.error
import urllib.request

import guncelle


def test_version_compare():
    assert guncelle.is_newer("v1.1", "1.0")
    assert guncelle.is_newer("v2.0", "1.9.9")
    assert guncelle.is_newer("1.0.1", "1.0")
    assert not guncelle.is_newer("v1.0", "1.0")
    assert not guncelle.is_newer("v0.9", "1.0")
    assert not guncelle.is_newer("", "1.0")
    assert guncelle.parse_version("v1.2.3") == (1, 2, 3)
    print("  OK  surum karsilastirma (1.0 < 1.0.1 < 1.1 < 2.0)")


def test_host_check():
    ok = [
        "https://github.com/x/y/releases/download/v1.1/a.exe",
        "https://objects.githubusercontent.com/abc/a.exe",
    ]
    kotu = [
        "http://github.com/x/y/a.exe",          # https degil
        "https://github.com.saldirgan.net/a.exe",
        "https://raw.evil.tld/a.exe",
        "ftp://github.com/a.exe",
        "",
    ]
    for u in ok:
        assert guncelle._host_ok(u), u
    for u in kotu:
        assert not guncelle._host_ok(u), u
    print(f"  OK  adres denetimi: {len(ok)} kabul, {len(kotu)} red")


class _Resp(io.BytesIO):
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _fake_api(payload, monkey=[]):
    def opener(req, timeout=None):
        return _Resp(json.dumps(payload).encode())
    monkey.append(urllib.request.urlopen)
    urllib.request.urlopen = opener


def _restore(orig):
    urllib.request.urlopen = orig


def test_check_finds_new_release():
    orig = urllib.request.urlopen
    _fake_api({
        "tag_name": "v9.9",
        "body": "yeni surum notlari",
        "assets": [
            {"name": "M3sel-Bertaraf-tasinabilir.zip", "size": 1,
             "browser_download_url": "https://github.com/a/b/z.zip"},
            {"name": "M3sel-Bertaraf-Kurulum.exe", "size": 22000000,
             "browser_download_url": "https://github.com/a/b/releases/download/v9.9/k.exe"},
        ],
    })
    try:
        rel, msg = guncelle.check()
    finally:
        _restore(orig)
    assert rel is not None, msg
    assert rel.tag == "v9.9" and rel.version == "9.9"
    assert rel.url.endswith("k.exe"), "zip degil exe secilmeliydi"
    assert rel.size == 22000000
    print(f"  OK  yeni surum bulundu: {rel.tag}, dogru varlik secildi")


def test_check_same_version():
    orig = urllib.request.urlopen
    _fake_api({"tag_name": f"v{guncelle.APP_VERSION}", "assets": []})
    try:
        rel, msg = guncelle.check()
    finally:
        _restore(orig)
    assert rel is None and "guncel" in msg.lower(), msg
    print("  OK  ayni surumde guncelleme onerilmiyor")


def test_check_rejects_foreign_host():
    orig = urllib.request.urlopen
    _fake_api({
        "tag_name": "v9.9",
        "assets": [{"name": "kurulum.exe", "size": 1,
                    "browser_download_url": "https://evil.tld/k.exe"}],
    })
    try:
        rel, msg = guncelle.check()
    finally:
        _restore(orig)
    assert rel is None and "guvenlik" in msg.lower(), msg
    print("  OK  GitHub disi indirme adresi reddedildi")


def test_check_no_release():
    orig = urllib.request.urlopen
    def raiser(req, timeout=None):
        raise urllib.error.HTTPError(guncelle.API_LATEST, 404, "Not Found", {}, None)
    urllib.request.urlopen = raiser
    try:
        rel, msg = guncelle.check()
    finally:
        _restore(orig)
    assert rel is None and "yayinlanmis bir surum yok" in msg, msg
    print("  OK  surum yokken anlasilir mesaj veriliyor")


def test_check_missing_asset():
    orig = urllib.request.urlopen
    _fake_api({"tag_name": "v9.9", "assets": []})
    try:
        rel, msg = guncelle.check()
    finally:
        _restore(orig)
    assert rel is None and "kurulum dosyasi eklenmemis" in msg, msg
    print("  OK  surum var ama dosya yokken elle indirme adresi veriliyor")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"\n{len(tests)} test gecti.")
