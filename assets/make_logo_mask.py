"""Turn the bold infinity crop into a mask-image asset for .omx-logo.

Only the alpha channel matters for `mask-image`, so we flatten RGB to white and
keep the shape in alpha. Tight-cropped (no dead margin) so the glyph fills the
existing 30/32/104px square boxes at full width instead of the old 1254x1254
asset's ~20% coverage.
"""
import base64
import io

import numpy as np
from PIL import Image, ImageFilter

SRC = r"C:\Users\karth\Downloads\final_cropped_med.png"
OUT = r"C:\Users\karth\AppData\Local\Temp\claude\C--Users-karth\a4388b16-051e-432d-b0dc-20e444b50e69\scratchpad\logo_mask.png"

im = Image.open(SRC).convert("RGBA")
a = np.asarray(im).astype(np.float32)
alpha = a[..., 3]

# Trim to the ink bounding box (the source already keys white -> alpha 0).
ys, xs = np.nonzero(alpha > 6)
top, bottom, left, right = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
alpha = alpha[top:bottom, left:right]
print("cropped:", alpha.shape[1], "x", alpha.shape[0])

# Retina width for a ~104px max display; LANCZOS on alpha alone keeps the thin
# lattice struts from dissolving.
maxw = 520
h = round(alpha.shape[0] * maxw / alpha.shape[1])
alpha_im = Image.fromarray(alpha.astype(np.uint8), "L")

# The source crop is flat, fully-opaque gold; the original embedded mark was a
# semi-transparent, finer-lined render. Erode 1px at full res (genuinely thinner
# strokes, not just fainter) and drop the ink to 62% so the mark reads delicate
# instead of heavy at its 33px sidebar size.
alpha_im = alpha_im.filter(ImageFilter.MinFilter(3))
alpha_im = alpha_im.resize((maxw, h), Image.LANCZOS)
alpha_arr = (np.asarray(alpha_im).astype(np.float32) * 0.62).astype(np.uint8)

rgb = np.full((h, maxw, 3), 255, np.uint8)
out = Image.fromarray(np.dstack([rgb, alpha_arr]), "RGBA")
out.save(OUT, optimize=True)

b = open(OUT, "rb").read()
print("final:", out.size, "aspect", round(maxw / h, 3), "bytes", len(b),
      "b64", len(base64.b64encode(b)))
