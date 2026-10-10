"""Render the Wakeful icon: a rounded navy square with an open eye.

One-off tool, not part of the app. Regenerate everything in assets/ with:

    uv run --with pillow python assets/make_icon.py assets
    iconutil -c icns assets/icon.iconset -o assets/icon.icns   # macOS only
    python -c "import base64,pathlib;print(base64.b64encode(pathlib.Path(\"assets/icon_128.png\").read_bytes()).decode())"

and paste the last line into src/wakeful/icon.py.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
S = 1024  # master size, supersampled 4x for smooth edges
SS = 4
W = S * SS
BG = (28, 44, 92)        # navy
EYE = (255, 255, 255)
IRIS = (255, 196, 64)    # warm amber: "awake"
PUPIL = (28, 44, 92)

img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
r = int(W * 0.22)
d.rounded_rectangle((0, 0, W - 1, W - 1), radius=r, fill=BG)

# Eye: intersection of two circles (lens shape), drawn via mask.
cx, cy = W / 2, W / 2
eye_w = W * 0.72
eye_h = W * 0.40
mask = Image.new("L", (W, W), 0)
md = ImageDraw.Draw(mask)
# radius of the circles whose intersection has width eye_w and height eye_h
R = (eye_h / 2) / 2 + (eye_w / 2) ** 2 / (2 * eye_h)
off = R - eye_h / 2
md.ellipse((cx - R, cy - off - R, cx + R, cy - off + R), fill=255)
m2 = Image.new("L", (W, W), 0)
ImageDraw.Draw(m2).ellipse((cx - R, cy + off - R, cx + R, cy + off + R), fill=255)
from PIL import ImageChops
lens = ImageChops.multiply(mask, m2)
white = Image.new("RGBA", (W, W), EYE)
img.paste(white, (0, 0), lens)

ir = eye_h * 0.42
d.ellipse((cx - ir, cy - ir, cx + ir, cy + ir), fill=IRIS)
pr = ir * 0.52
d.ellipse((cx - pr, cy - pr, cx + pr, cy + pr), fill=PUPIL)
hr = pr * 0.38
d.ellipse((cx - pr * 0.45 - hr, cy - pr * 0.45 - hr, cx - pr * 0.45 + hr, cy - pr * 0.45 + hr), fill=EYE)

master = img.resize((S, S), Image.LANCZOS)
master.save(OUT / "icon.png")
# Windows .ico with the standard sizes
master.save(OUT / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
# iconset for macOS iconutil
iconset = OUT / "icon.iconset"
iconset.mkdir(exist_ok=True)
for n in (16, 32, 128, 256, 512):
    master.resize((n, n), Image.LANCZOS).save(iconset / f"icon_{n}x{n}.png")
    master.resize((n * 2, n * 2), Image.LANCZOS).save(iconset / f"icon_{n}x{n}@2x.png")
# small PNG for the Tk window / dock when running from source
master.resize((128, 128), Image.LANCZOS).save(OUT / "icon_128.png")
print("ok")
