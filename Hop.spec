# PyInstaller build recipe for macOS (.app) and Windows (folder with .exe).
#   pyinstaller --noconfirm Hop.spec
import os
import sys

from PyInstaller.utils.hooks import collect_all

root = SPECPATH
version = open(os.path.join(root, "VERSION")).read().strip()

datas, binaries, hiddenimports = [], [], []
for pkg in ("zeroconf", "nacl", "spake2", "segno"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += [
    (os.path.join(root, "hop", "ui"), os.path.join("hop", "ui")),
    (os.path.join(root, "hop", "web"), os.path.join("hop", "web")),
    (os.path.join(root, "VERSION"), "."),
]
hiddenimports += ["certifi"]

a = Analysis(
    [os.path.join(root, "Hop.py")],
    pathex=[root],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "_tkinter"] if sys.platform == "darwin" else [],
)
pyz = PYZ(a.pure)
icon = os.path.join(root, "assets", "AppIcon.icns" if sys.platform == "darwin" else "icon.ico")

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Hop",
    console=False,
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Hop")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Hop.app",
        icon=icon,
        bundle_identifier="com.yuvrajgarg.hop",
        version=version,
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "CFBundleDisplayName": "Hop",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSRequiresAquaSystemAppearance": False,
            # macOS 15+ asks before an app talks to the local network.
            "NSLocalNetworkUsageDescription": "Hop finds your other devices on this Wi-Fi and sends files to them directly.",
            "NSBonjourServices": ["_hop._tcp"],
        },
    )
