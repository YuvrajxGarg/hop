"""Makes icon.ico, AppIcon.icns and the small PNGs from assets/icon.png.
    node assets/make_icon.js && python assets/make_icon.py
"""
import os

from PIL import Image

here = os.path.dirname(os.path.abspath(__file__))
png = os.path.join(here, "icon.png")
im = Image.open(png).convert("RGBA")
im.save(os.path.join(here, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
im.save(os.path.join(here, "AppIcon.icns"))
im.resize((256, 256), Image.LANCZOS).save(os.path.join(here, "..", "docs", "icon-256.png"))
im.resize((180, 180), Image.LANCZOS).save(os.path.join(here, "..", "hop", "web", "icon.png"))
print("wrote", png)
