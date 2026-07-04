"""DBParity icon generator (raster from code - the asset is reproducible).

Concept: two IDENTICAL columns of binary code (source and target) with
an equals sign between them - "the data matched bit for bit". At 16 px
it degrades into a blue square with an "=" sign and stays recognizable.

Usage: python3 scripts/make_icon.py  ->  docs/icon-{512,256,128}.png,
docs/favicon.ico. The vector master is docs/logo.svg (drawn by hand,
its design is kept in sync with this script).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024                       # draw at 2x and downscale - smooth edges
RADIUS = int(SIZE * 0.22)
TOP, BOTTOM = (26, 93, 184), (18, 56, 110)      # tabler-blue -> dark
DIGITS = "1011010"                # identical in both columns - that is the point
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
OUT = Path(__file__).resolve().parent.parent / "docs"


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("DejaVu font not found - install fonts-dejavu")


def build() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # vertical gradient clipped by a rounded square
    grad = Image.new("RGBA", (SIZE, SIZE))
    gd = ImageDraw.Draw(grad)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        gd.line([(0, y), (SIZE, y)], fill=tuple(
            int(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=255)
    img.paste(grad, (0, 0), mask)

    # two identical columns of binary digits
    font = _font(int(SIZE * 0.115))
    rows = len(DIGITS)
    y0, y1 = SIZE * 0.16, SIZE * 0.84
    for cx in (SIZE * 0.27, SIZE * 0.73):
        for i, ch in enumerate(DIGITS):
            y = y0 + (y1 - y0) * i / (rows - 1)
            draw.text((cx, y), ch, font=font, fill=(255, 255, 255, 230),
                      anchor="mm")

    # equals sign: two rounded bars between the columns
    bar_w, bar_h = SIZE * 0.20, SIZE * 0.055
    for cy in (SIZE * 0.455, SIZE * 0.575):
        draw.rounded_rectangle(
            [SIZE / 2 - bar_w / 2, cy - bar_h / 2,
             SIZE / 2 + bar_w / 2, cy + bar_h / 2],
            radius=bar_h / 2, fill=(255, 255, 255, 255))
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    master = build()
    for size in (512, 256, 128):
        master.resize((size, size), Image.LANCZOS).save(
            OUT / f"icon-{size}.png")
    master.resize((64, 64), Image.LANCZOS).save(
        OUT / "favicon.ico",
        sizes=[(16, 16), (32, 32), (48, 48)])
    print("done:", ", ".join(p.name for p in sorted(OUT.glob("icon-*.png"))),
          "+ favicon.ico")


if __name__ == "__main__":
    main()
