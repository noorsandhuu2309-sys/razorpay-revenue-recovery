"""Render the OMNIX mark into a favicon for the workspace app.

The Vite scaffold shipped a purple template favicon that has nothing to do with
OMNIX. The mark itself is an alpha mask (flat white RGB, shape in alpha), so a
favicon is just that alpha tinted with the brand gold and centred on a square
transparent canvas — the mark is 2.2:1, so padding it into a square is what
keeps it from being squashed in the tab strip.
"""
import pathlib

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "omnix_logo_mask.png"
OUT = ROOT / "frontend" / "public" / "favicon.png"

GOLD = (211, 173, 85)  # --omx-gold
SIZE = 128

mask = Image.open(SRC).convert("RGBA").getchannel("A")

# The stored mask is knocked back to 62% so it reads delicate at 33px in the
# sidebar. At 128px in a tab strip that is nearly invisible, so restore it to
# full strength before tinting.
mask = mask.point(lambda a: min(255, int(a / 0.62)))

w = SIZE
h = max(1, round(mask.height * SIZE / mask.width))
mask = mask.resize((w, h), Image.LANCZOS)

icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
tinted = Image.new("RGBA", (w, h), GOLD + (0,))
tinted.putalpha(mask)
icon.paste(tinted, (0, (SIZE - h) // 2), tinted)

OUT.parent.mkdir(parents=True, exist_ok=True)
icon.save(OUT, optimize=True)
print(f"wrote {OUT} ({icon.size[0]}x{icon.size[1]}, mark {w}x{h})")
