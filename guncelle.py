"""
M3sel Bertaraf - guncelleme kontrolu
=====================================
GitHub Releases uzerinden yeni surum arar, kurulum dosyasini indirir ve
calistirir. Indirme adresi yalnizca GitHub alan adlarindan kabul edilir.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass

from core import APP_VERSION, REPO

API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ALLOWED_HOSTS = ("github.com", "githubusercontent.com")


@dataclass
class Release:
    version: str
    tag: str
    url: str            # kurulum dosyasinin indirme adresi
    notes: str
    size: int


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3). Ayristirilamayan parcalar 0 sayilir."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def _host_ok(url: str) -> bool:
    from urllib.parse import urlparse
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        return False
    return any(p.hostname == h or p.hostname.endswith("." + h) for h in ALLOWED_HOSTS)


def check(timeout: float = 12.0) -> tuple[Release | None, str]:
    """Yeni surum var mi diye bakar.

    Doner: (Release | None, aciklama). Release None ise aciklama nedenini
    soyler; guncel olmak da bir nedendir."""
    try:
        req = urllib.request.Request(
            API_LATEST,
            headers={"accept": "application/vnd.github+json",
                     "user-agent": f"M3selBertaraf/{APP_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            data = json.load(fh)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, ("Depoda henuz yayinlanmis bir surum yok. "
                          f"Surumler: {RELEASES_PAGE}")
        return None, f"Surum bilgisi alinamadi (HTTP {exc.code})."
    except Exception as exc:
        return None, f"Surum bilgisi alinamadi: {exc}"

    tag = data.get("tag_name") or ""
    if not is_newer(tag):
        return None, f"En guncel surumu kullaniyorsunuz (v{APP_VERSION})."

    asset = None
    for a in data.get("assets", []):
        name = (a.get("name") or "").lower()
        if name.endswith(".exe") and "kurulum" in name:
            asset = a
            break
    if asset is None:
        for a in data.get("assets", []):
            if (a.get("name") or "").lower().endswith(".exe"):
                asset = a
                break
    if asset is None:
        return None, (f"{tag} yayinlanmis ama kurulum dosyasi eklenmemis. "
                      f"Elle indirin: {RELEASES_PAGE}")

    url = asset.get("browser_download_url") or ""
    if not _host_ok(url):
        return None, "Indirme adresi GitHub disinda, guvenlik icin durduruldu."

    return Release(
        version=tag.lstrip("vV"),
        tag=tag,
        url=url,
        notes=(data.get("body") or "").strip(),
        size=int(asset.get("size") or 0),
    ), f"Yeni surum bulundu: {tag}"


def download(rel: Release, progress=None, timeout: float = 60.0) -> str:
    """Kurulum dosyasini gecici klasore indirir, dosya yolunu doner."""
    if not _host_ok(rel.url):
        raise ValueError("Indirme adresi GitHub disinda.")

    dest = os.path.join(tempfile.gettempdir(),
                        f"M3sel-Bertaraf-Kurulum-{rel.tag}.exe")
    req = urllib.request.Request(
        rel.url, headers={"user-agent": f"M3selBertaraf/{APP_VERSION}"})

    with urllib.request.urlopen(req, timeout=timeout) as fh, open(dest, "wb") as out:
        total = int(fh.headers.get("Content-Length") or rel.size or 0)
        got = 0
        while True:
            chunk = fh.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if progress:
                progress(got, total)

    if os.path.getsize(dest) < 1024 * 1024:
        os.remove(dest)
        raise ValueError("Indirilen dosya beklenenden kucuk, iptal edildi.")
    return dest
