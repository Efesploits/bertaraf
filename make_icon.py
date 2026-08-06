"""Program simgesini (m3sel.ico) uretir. Sadece derlemeden once bir kez calisir."""
from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]
BG = (24, 25, 33, 255)
ACCENT = (88, 101, 242, 255)
CUT = (237, 66, 69, 255)


def draw(size: int) -> Image.Image:
    s = size * 8  # supersampling
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    r = s * 0.22
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=BG)

    # kalkan
    pad = s * 0.22
    top, bottom = pad, s - pad * 0.85
    mid = s / 2
    w = (s - pad * 2) / 2
    d.polygon(
        [(mid - w, top), (mid + w, top), (mid + w, top + (bottom - top) * 0.45),
         (mid, bottom), (mid - w, top + (bottom - top) * 0.45)],
        fill=ACCENT,
    )

    # kalkani ikiye bolen kesik -> "paketi bol" fikri
    cw = max(1, int(s * 0.055))
    d.line([(mid - w * 1.35, top + (bottom - top) * 0.62),
            (mid + w * 1.35, top + (bottom - top) * 0.30)],
           fill=BG, width=int(cw * 2.6))
    d.line([(mid - w * 1.35, top + (bottom - top) * 0.62),
            (mid + w * 1.35, top + (bottom - top) * 0.30)],
           fill=CUT, width=cw)

    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    frames = [draw(n) for n in SIZES]
    frames[-1].save("m3sel.ico", format="ICO",
                    sizes=[(n, n) for n in SIZES], append_images=frames[:-1])
    print("m3sel.ico olusturuldu:", SIZES)
