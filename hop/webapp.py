"""Hop's desktop window: an HTML/CSS interface (hop/ui) in a native window via pywebview.

WebKit on macOS, Edge WebView2 on Windows, so both platforms get the same UI.
Set HOP_UI_DEV=<port> to serve the same UI to a normal browser instead (for development
and screenshots): the page then talks to this Api over localhost.
"""

import json
import os
import subprocess
import sys
import threading

from . import __version__
from .node import HopError, Node, collect, human
from .settings import Settings
from .updater import Updater

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    UI_DIR = os.path.join(sys._MEIPASS, "hop", "ui")

BG = "#0b0b0c"
SETTABLE = ("device_name", "save_dir", "auto_accept", "open_after", "auto_check", "auto_install", "welcomed")


def qr_svg(text):
    try:
        import segno
        q = segno.make(text, error="m")
        return q.svg_inline(scale=1, border=0, dark="#0b0b0c", light=None, omitsize=True)
    except Exception:
        return ""


class Api(object):
    def __init__(self, emit_js=None):
        self.window = None
        self.emit_js = emit_js
        self.settings = Settings()
        self.selection = []  # paths waiting to be sent
        self.updater = Updater(self.settings, lambda st: self._emit("update", st))
        self.node = Node(self.settings, emit=self._on_node)
        self.node_error = None
        try:
            self.node.start()
        except HopError as e:
            self.node_error = str(e)

    # ---- helpers ----
    def _emit(self, event, payload):
        if self.emit_js:
            self.emit_js(event, payload)
            return
        if self.window:
            try:
                self.window.evaluate_js("window.App && App.on(%s, %s)" % (json.dumps(event), json.dumps(payload)))
            except Exception:
                pass

    def _on_node(self, event, payload):
        self._emit(event, payload)
        if event == "received" and self.settings.get("open_after") and payload.get("first"):
            self.reveal(payload["first"])

    def _sel_public(self):
        files = collect(self.selection)
        total = sum(os.path.getsize(p) for p, _ in files)
        names = [os.path.basename(p.rstrip(os.sep)) for p in self.selection]
        return {"count": len(files), "total": total, "size": human(total), "names": names[:3], "items": len(self.selection)}

    # ---- called from JS ----
    def info(self):
        return {"version": __version__, "platform": sys.platform, "settings": self.settings.public(),
                "update": self.updater.state(), "home": os.path.expanduser("~"), "name": self.node.name,
                "ip": self.node.ip, "port": self.node.port, "error": self.node_error,
                "bonjour": bool(self.node.discovery and self.node.discovery.available),
                "peers": self.node.peers(), "transfers": self.node.transfers(), "selection": self._sel_public()}

    def set_setting(self, key, value):
        if key in SETTABLE:
            if key == "device_name":
                self.node.rename(value)
            else:
                self.settings.set(key, value)
            if key == "auto_install" and value and self.updater.phase == "available":
                threading.Thread(target=self.updater.stage_newest, daemon=True).start()
        return self.settings.public()

    def peers(self):
        return self.node.peers()

    def add_address(self, addr):
        try:
            return {"ok": True, "peer": self.node.add_by_address(addr)}
        except HopError as e:
            return {"ok": False, "error": str(e)}

    def forget(self, peer_id):
        self.node.forget(peer_id)
        return self.node.peers()

    # selection
    def add_paths(self, paths):
        for p in paths:
            if os.path.exists(p) and p not in self.selection:
                self.selection.append(p)
        sel = self._sel_public()
        self._emit("selection", sel)
        return sel

    def clear_selection(self):
        self.selection = []
        sel = self._sel_public()
        self._emit("selection", sel)
        return sel

    def pick_files(self):
        import webview
        res = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        if res:
            self.add_paths(list(res))

    def pick_folder(self):
        import webview
        res = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        if res:
            self.add_paths([res[0] if isinstance(res, (list, tuple)) else res])

    def choose_save_dir(self):
        import webview
        res = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        if res:
            d = res[0] if isinstance(res, (list, tuple)) else res
            self.settings.set("save_dir", d)
        return self.settings.public()

    # sending
    def send(self, peer_id):
        if not self.selection:
            return {"ok": False, "error": "Add some files first"}
        try:
            self.node.send(peer_id, list(self.selection))
        except HopError as e:
            if str(e) == "unpaired":
                return {"ok": False, "pair": True}
            return {"ok": False, "error": str(e)}
        self.clear_selection()
        return {"ok": True}

    def pair_begin(self, peer_id):
        try:
            self.node.pair_begin(peer_id)
            return {"ok": True}
        except HopError as e:
            return {"ok": False, "error": str(e)}

    def pair_finish(self, peer_id, code):
        try:
            self.node.pair_finish(peer_id, code)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pair_cancel(self, peer_id):
        self.node.pair_cancel(peer_id)

    # receiving
    def decide(self, tid, accept):
        self.node.decide(tid, accept)

    def cancel(self, tid):
        self.node.cancel(tid)

    def retry(self, tid):
        self.node.retry(tid)

    def remove(self, tid):
        self.node.remove(tid)

    # phone
    def phone(self, fresh=False):
        url = self.node.new_web_link() if fresh else self.node.web_url()
        return {"url": url, "qr": qr_svg(url), "host": "%s:%d" % (self.node.ip, self.node.port)}

    # updates
    def check_updates(self):
        return self.updater.check(manual=True)

    def download_update(self):
        threading.Thread(target=self.updater.stage_newest, daemon=True).start()

    def install_update(self):
        def go():
            if self.updater.install() == "open_page":
                self.open_url(self.updater.state()["page"])
        threading.Thread(target=go, daemon=True).start()

    def open_url(self, url):
        if not url.startswith("https://github.com/"):
            return
        import webbrowser
        webbrowser.open(url)

    def reveal(self, path):
        if not path:
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path])
            elif os.name == "nt":
                if os.path.isfile(path):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                else:
                    os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path) if os.path.isfile(path) else path])
        except Exception:
            pass

    def open_save_dir(self):
        d = self.settings.save_dir
        os.makedirs(d, exist_ok=True)
        self.reveal(d)


# ---- native window chrome (same as Tag and Dwn) ---------------------------

def _style_mac(window):
    try:
        import AppKit
        from PyObjCTools import AppHelper

        def apply():
            w = window.native
            w.setStyleMask_(w.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)
            w.setTitlebarAppearsTransparent_(True)
            w.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            w.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
            w.setBackgroundColor_(AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.043, 0.043, 0.047, 1.0))
            w.setMovableByWindowBackground_(False)
            try:
                w.contentView().superview().subviews().lastObject().setBackgroundColor_(AppKit.NSColor.clearColor())
            except Exception:
                pass
            if hasattr(w, "setToolbarStyle_"):
                w.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnifiedCompact)

        AppHelper.callAfter(apply)
    except Exception:
        pass


def _style_windows(window):
    try:
        import ctypes

        hwnd = window.native.Handle.ToInt32()
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                break
        caption = ctypes.c_int(0x000C0B0B)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), 4)
    except Exception:
        pass


def main():
    if os.environ.get("HOP_UI_DEV"):
        return dev_server(int(os.environ["HOP_UI_DEV"]))
    import webview
    from webview.dom import DOMEventHandler

    api = Api()
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
    window = webview.create_window(
        "Hop", url=os.path.join(UI_DIR, "index.html"), js_api=api,
        width=1120, height=720, min_size=(880, 580), background_color=BG, easy_drag=False,
    )
    api.window = window

    def on_drop(e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if paths:
            api.add_paths(paths)

    def on_loaded():
        try:
            window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception:
            pass

    def quit_for_install():
        try:
            window.destroy()
        finally:
            threading.Timer(3.0, lambda: os._exit(0)).start()

    def on_closing():
        api.updater.install_on_quit()
        api.node.stop()

    api.updater.on_quit_for_install = quit_for_install
    window.events.closing += on_closing
    window.events.loaded += lambda: api.updater.start()
    if sys.platform == "darwin":
        window.events.before_show += lambda: _style_mac(window)
    elif os.name == "nt":
        window.events.shown += lambda: _style_windows(window)
    window.events.loaded += on_loaded
    webview.start(debug=bool(os.environ.get("HOP_DEBUG")))


# ---- development: the same UI in a regular browser ----------------------------

def dev_server(port):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import mimetypes

    events = []
    cond = threading.Condition()

    def push(ev, payload):
        with cond:
            events.append((ev, payload))
            cond.notify_all()

    api = Api(emit_js=push)
    api.window = None

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path.startswith("/__events"):
                since = int(self.path.split("since=")[-1]) if "since=" in self.path else 0
                with cond:
                    if len(events) <= since:
                        cond.wait(20)
                    out = events[since:]
                    n = len(events)
                return self._send(200, json.dumps({"n": n, "events": out}).encode())
            fp = os.path.join(UI_DIR, (path.strip("/") or "index.html"))
            if not os.path.abspath(fp).startswith(UI_DIR) or not os.path.isfile(fp):
                return self._send(404, b"{}")
            with open(fp, "rb") as f:
                body = f.read()
            if fp.endswith("index.html"):
                # Tell the page it's in the browser bridge. (pywebview also serves the UI over
                # http, so the page can't tell the two apart from the URL.)
                body = body.replace(b"</head>", b"<script>window.HOP_DEV = true;</script></head>", 1)
            self._send(200, body, mimetypes.guess_type(fp)[0] or "text/plain")

        def do_POST(self):
            name = self.path.split("/__api/")[-1]
            n = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(n).decode() or "[]")
            if name.startswith("_") or not hasattr(api, name):
                return self._send(404, b"{}")
            res = getattr(api, name)(*args)
            self._send(200, json.dumps(res).encode())

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    print("Hop UI (dev) on http://127.0.0.1:%d  ·  node on port %d" % (port, api.node.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        api.node.stop()
