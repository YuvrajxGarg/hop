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
SETTABLE = ("device_name", "save_dir", "auto_accept", "open_after", "auto_check", "auto_install", "welcomed", "relay")


def qr_svg(text):
    try:
        import segno
        q = segno.make(text, error="m")
        return q.svg_inline(scale=1, border=0, dark="#0b0b0c", light=None, omitsize=True)
    except Exception:
        return ""


class Api(object):
    def __init__(self, emit_js=None):
        self._window = None
        self._emit_js = emit_js
        self._settings = Settings()
        self._selection = []  # paths waiting to be sent
        self._updater = Updater(self._settings, lambda st: self._emit("update", st))
        self._node = Node(self._settings, emit=self._on_node, relay=True)
        self._node_error = None
        try:
            self._node.start()
        except HopError as e:
            self._node_error = str(e)

    # ---- helpers ----
    def _emit(self, event, payload):
        if self._emit_js:
            self._emit_js(event, payload)
            return
        if self._window:
            try:
                self._window.evaluate_js("window.App && App.on(%s, %s)" % (json.dumps(event), json.dumps(payload)))
            except Exception:
                pass

    def _on_node(self, event, payload):
        self._emit(event, payload)
        if event == "received" and self._settings.get("open_after") and payload.get("first"):
            self.reveal(payload["first"])

    def _sel_public(self):
        files = collect(self._selection)
        total = sum(os.path.getsize(p) for p, _ in files)
        names = [os.path.basename(p.rstrip(os.sep)) for p in self._selection]
        return {"count": len(files), "total": total, "size": human(total), "names": names[:3], "items": len(self._selection)}

    # ---- called from JS ----
    def info(self):
        return {"version": __version__, "platform": sys.platform, "settings": self._settings.public(),
                "update": self._updater.state(), "home": os.path.expanduser("~"), "name": self._node.name,
                "ip": self._node.ip, "port": self._node.port, "error": self._node_error,
                "bonjour": bool(self._node.discovery and self._node.discovery.available),
                "relay": self._node.remote.status() if self._node.remote else None,
                "peers": self._node.peers(), "transfers": self._node.transfers(), "selection": self._sel_public()}

    def set_setting(self, key, value):
        if key in SETTABLE:
            if key == "device_name":
                self._node.rename(value)
            else:
                self._settings.set(key, value)
            if key == "device_name" and self._node.remote:
                self._node.remote.registered = False  # re-register so others see the new name
                self._node.remote.wake.set()
            if key == "relay" and self._node.remote:
                self._node.remote.wake.set()
                self._emit("relay", self._node.remote.status())
            if key == "auto_install" and value and self._updater.phase == "available":
                threading.Thread(target=self._updater.stage_newest, daemon=True).start()
        return self._settings.public()

    def peers(self):
        return self._node.peers()

    def add_address(self, addr):
        try:
            return {"ok": True, "peer": self._node.add_by_address(addr)}
        except HopError as e:
            return {"ok": False, "error": str(e)}

    def forget(self, peer_id):
        if str(peer_id).startswith("code:"):
            self._settings.forget_contact(peer_id.split(":", 1)[1])
        else:
            self._node.forget(peer_id)
        return self._node.peers()

    def add_code(self, code):
        from .remote import RelayError
        if not self._node.remote:
            return {"ok": False, "error": "Sending by Hop code is turned off"}
        try:
            p = self._node.remote.add(code)
        except RelayError as e:
            return {"ok": False, "error": str(e)}
        self._emit("peers", self._node.peers())
        return {"ok": True, "peer": {"id": "code:" + p["code"], "name": p["name"]}}

    def copy(self, text):
        """Clipboard fallback for webviews that block navigator.clipboard."""
        text = str(text or "")
        try:
            if sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
            elif os.name == "nt":
                subprocess.run(["clip"], input=text.encode("utf-16-le"), check=False)
        except Exception:
            pass
        return True

    # selection
    def add_paths(self, paths):
        for p in paths:
            if os.path.exists(p) and p not in self._selection:
                self._selection.append(p)
        sel = self._sel_public()
        self._emit("selection", sel)
        return sel

    def clear_selection(self):
        self._selection = []
        sel = self._sel_public()
        self._emit("selection", sel)
        return sel

    def pick_files(self):
        import webview
        res = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        if res:
            self.add_paths(list(res))

    def pick_folder(self):
        import webview
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if res:
            self.add_paths([res[0] if isinstance(res, (list, tuple)) else res])

    def choose_save_dir(self):
        import webview
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if res:
            d = res[0] if isinstance(res, (list, tuple)) else res
            self._settings.set("save_dir", d)
        return self._settings.public()

    # sending
    def send(self, peer_id):
        if not self._selection:
            return {"ok": False, "error": "Add some files first"}
        try:
            self._node.send(peer_id, list(self._selection))
        except HopError as e:
            if str(e) == "unpaired":
                return {"ok": False, "pair": True}
            return {"ok": False, "error": str(e)}
        self.clear_selection()
        return {"ok": True}

    def pair_begin(self, peer_id):
        try:
            self._node.pair_begin(peer_id)
            return {"ok": True}
        except HopError as e:
            return {"ok": False, "error": str(e)}

    def pair_finish(self, peer_id, code):
        try:
            self._node.pair_finish(peer_id, code)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pair_cancel(self, peer_id):
        self._node.pair_cancel(peer_id)

    # receiving
    def decide(self, tid, accept):
        self._node.decide(tid, accept)

    def cancel(self, tid):
        self._node.cancel(tid)

    def retry(self, tid):
        self._node.retry(tid)

    def remove(self, tid):
        self._node.remove(tid)

    # phone
    def phone(self, fresh=False):
        url = self._node.new_web_link() if fresh else self._node.web_url()
        return {"url": url, "qr": qr_svg(url), "host": "%s:%d" % (self._node.ip, self._node.port)}

    # updates
    def check_updates(self):
        return self._updater.check(manual=True)

    def download_update(self):
        threading.Thread(target=self._updater.stage_newest, daemon=True).start()

    def install_update(self):
        def go():
            if self._updater.install() == "open_page":
                self.open_url(self._updater.state()["page"])
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
        d = self._settings.save_dir
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
    api._window = window

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
            t = threading.Timer(3.0, lambda: os._exit(0))
            t.daemon = True
            t.start()

    def on_closing():
        api._updater.install_on_quit()
        api._node.stop()

    api._updater.on_quit_for_install = quit_for_install
    window.events.closing += on_closing
    window.events.loaded += lambda: api._updater.start()
    if sys.platform == "darwin":
        window.events.before_show += lambda: _style_mac(window)
    elif os.name == "nt":
        window.events.shown += lambda: _style_windows(window)
    window.events.loaded += on_loaded
    webview.start(debug=bool(os.environ.get("HOP_DEBUG")))
    if os.name == "nt":
        # The window is closed. Leave without Python's normal shutdown: tearing down pythonnet and
        # WinForms there can crash, and a crash report keeps the process alive, which stalls the
        # update swap waiting for it to exit (the app then never reopened after an update).
        os._exit(0)


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
    api._window = None

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
    print("Hop UI (dev) on http://127.0.0.1:%d  ·  node on port %d" % (port, api._node.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        api._node.stop()
