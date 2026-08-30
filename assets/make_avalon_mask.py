"""Turn the AVALON wireframe-cube logo into an alpha mask for the console.

The source is a single-colour glyph, so only its coverage matters: we keep the
shape in alpha and flatten RGB to white. Painting then happens in CSS via
`background-color: var(--ax)`, which is what lets the mark carry AVALON's
identity accent instead of being locked to the gold it was drawn in.

Handles either a real alpha channel or a white-matted export.
"""
import base64
import pathlib

import numpy as np
from PIL import Image

SRC = pathlib.Path(r"C:\Users\karth\Downloads\AVALON_transparent.png")
OUT = pathlib.Path(__file__).with_name("avalon_logo_mask.png")

im = Image.open(SRC).convert("RGBA")
a = np.asarray(im).astype(np.float32)
alpha = a[..., 3].copy()

# A white-matted export has a fully opaque alpha; recover coverage from ink
# darkness instead so we never end up masking the whole canvas.
if (alpha > 250).mean() > 0.95:
    lum = a[..., :3].max(axis=2)          # gold on white -> low where ink is
    alpha = np.clip(255.0 - lum, 0, 255)

ys, xs = np.nonzero(alpha > 8)
if not len(ys):
    raise SystemExit("no ink found in source")
alpha = alpha[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
print("cropped:", alpha.shape[1], "x", alpha.shape[0])

# Retina width for a ~104px max display. LANCZOS on alpha alone keeps the thin
# cube edges from dissolving at small sizes.
maxw = 448
h = max(1, round(alpha.shape[0] * maxw / alpha.shape[1]))
al = Image.fromarray(alpha.astype(np.uint8), "L").resize((maxw, h), Image.LANCZOS)
arr = np.asarray(al).astype(np.float32)

# Normalise so the strokes reach full opacity (the source is a thin, slightly
# anti-aliased line; without this the mark reads washed out).
if arr.max() > 0:
    arr = np.clip(arr * (255.0 / arr.max()), 0, 255)

rgb = np.full((h, maxw, 3), 255, np.uint8)
out = Image.fromarray(np.dstack([rgb, arr.astype(np.uint8)]), "RGBA")
out.save(OUT, optimize=True)

b = OUT.read_bytes()
print("final:", out.size, "bytes", len(b), "b64", len(base64.b64encode(b)))
