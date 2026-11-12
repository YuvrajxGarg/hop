"""Hop's engine: one small HTTP server per device plus the client that talks to it.

Every Hop runs the same node. It
  * answers pairing requests (SPAKE2, see crypto.py),
  * receives files pushed by a paired computer, in 1 MiB encrypted chunks,
  * serves the phone page, so a phone that scans the QR code can send and receive
    from its browser with nothing installed,
  * pushes files to other computers.

Transfers resume. The transfer id is derived from the sender, the receiver and the
files' SHA-256 hashes, so sending the same files again picks up where it stopped,
even after either app restarts. Each finished file is checked against its SHA-256
before it's moved into place.
"""

import hashlib
import http.client
import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import sys
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from . import __version__
from . import crypto
from .crypto import b64d, b64e
from .discovery import Discovery

CHUNK = 1 << 20
PROTO = 1
DEFAULT_PORT = 47470
WORKERS = 4
MAX_JSON = 8 << 20
MAX_FILES = 20000
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    WEB_DIR = os.path.join(sys._MEIPASS, "hop", "web")

PLATFORM = "mac" if sys.platform == "darwin" else ("windows" if os.name == "nt" else "linux")


class HopError(Exception):
    pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # dropped connections are normal on Wi-Fi; the protocol retries


# ---- helpers -----------------------------------------------------------------

def lan_ip():
    """The address other devices on the Wi-Fi reach us at (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def chunks_for(size):
    return (size + CHUNK - 1) // CHUNK


def to_ranges(nums):
    """[0,1,2,5,6] -> [[0,3],[5,7]] (half-open)."""
    out = []
    for n in sorted(nums):
        if out and out[-1][1] == n:
            out[-1][1] = n + 1
        else:
            out.append([n, n + 1])
    return out


def from_ranges(ranges, limit):
    s = set()
    for a, b in ranges or []:
        a, b = max(0, int(a)), min(int(limit), int(b))
        s.update(range(a, b))
    return s


_BAD = re.compile(r'[\x00-\x1f<>:"|?*\\]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}


def safe_relpath(name):
    """A sender-supplied path made safe to create under the save folder."""
    name = unicodedata.normalize("NFC", str(name)).replace("\\", "/")
    parts = []
    for p in name.split("/"):
        p = _BAD.sub("_", p).strip().rstrip(".")
        if not p or p in (".", ".."):
            continue
        if p.split(".")[0].upper() in _RESERVED:
            p = "_" + p
        parts.append(p[:200])
    if not parts:
        parts = ["file"]
    return os.path.join(*parts[-16:])


def unique_path(path, taken=()):
    if not os.path.exists(path) and path not in taken:
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while True:
        cand = "%s (%d)%s" % (stem, n, ext)
        if not os.path.exists(cand) and cand not in taken:
            return cand
        n += 1


def collect(paths):
    """Expand files and folders into [(abs_path, relative_name)], folders keep their structure."""
    out = []
    for p in paths:
        p = os.path.abspath(os.path.expanduser(p))
        if os.path.isfile(p):
            out.append((p, os.path.basename(p)))
        elif os.path.isdir(p):
            base = os.path.dirname(p.rstrip(os.sep)) or p
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    if f.startswith(".") or f in ("Thumbs.db", "desktop.ini"):
                        continue
                    fp = os.path.join(root, f)
                    if os.path.isfile(fp):
                        out.append((fp, os.path.relpath(fp, base).replace(os.sep, "/")))
    return out


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


class Rate(object):
    """Smoothed bytes/second."""

    def __init__(self):
        self.t = time.time()
        self.acc = 0
        self.value = 0.0

    def add(self, n):
        self.acc += n
        now = time.time()
        dt = now - self.t
        if dt >= 0.5:
            inst = self.acc / dt
            self.value = inst if not self.value else self.value * 0.6 + inst * 0.4
            self.acc = 0
            self.t = now


# ---- transfers -------------------------------------------------------------

class Incoming(object):
    def __init__(self, tid, peer_id, peer_name, kind, files, save_dir):
        self.tid = tid
        self.peer_id = peer_id
        self.peer_name = peer_name
        self.kind = kind  # "computer" | "phone"
        self.files = files  # [{name, size, sha256, chunks, have:set, status, final}]
        self.save_dir = save_dir
        self.status = "asking"
        self.decided = threading.Event()
        self.accepted = False
        self.error = None
        self.rate = Rate()
        self.lock = threading.Lock()
        self.flocks = [threading.Lock() for _ in files]
        self.last_emit = 0.0
        self.last_save = 0.0
        self.started = time.time()
        self.total = sum(f["size"] for f in files)

    @property
    def done_bytes(self):
        n = 0
        for f in self.files:
            if f["status"] == "done":
                n += f["size"]
            else:
                n += min(f["size"], len(f["have"]) * CHUNK)
        return n

    def need(self):
        return {str(i): to_ranges(set(range(f["chunks"])) - f["have"])
                for i, f in enumerate(self.files) if f["status"] != "done" and len(f["have"]) < f["chunks"]}

    @property
    def part_dir(self):
        return os.path.join(self.save_dir, ".hop-partial", self.tid)


class Outgoing(object):
    def __init__(self, tid, peer_id, peer_name, kind, files):
        self.tid = tid
        self.peer_id = peer_id
        self.peer_name = peer_name
        self.kind = kind
        self.files = files  # [{path, name, size, sha256, chunks}]
        self.status = "preparing"
        self.error = None
        self.cancelled = threading.Event()
        self.rate = Rate()
        self.sent = 0
        self.total = sum(f["size"] for f in files)
        self.lock = threading.Lock()
        self.served = {}  # phone pulls: (fi, ci) set


# ---- the node --------------------------------------------------------------

class Node(object):
    def __init__(self, settings, emit=None, port=None, discover=True, relay=False):
        self.settings = settings
        self.relay = relay
        self.remote = None
        self.emit_cb = emit or (lambda ev, payload: None)
        self.id = settings.get("device_id")
        self.port = port
        self.discover = discover
        self.httpd = None
        self.discovery = None
        self.manual = {}  # device_id -> peer dict added by address
        self.incoming = {}
        self.outgoing = {}
        self.order = []  # transfer ids, newest last
        self.pair_sessions = {}  # pid -> {...}
        self.pair_pending = {}  # device_id -> {pid, msg_b, host, port} (we're the one typing the code)
        self.lock = threading.RLock()
        self.web_sid = crypto.random_id(6)
        self.web_key = crypto.new_key()
        self.web_seen = 0.0
        self.web_clients = {}
        self.ip = lan_ip()

    # ---- lifecycle ----
    @property
    def name(self):
        return self.settings.get("device_name")

    def start(self):
        handler = _make_handler(self)
        ports = [self.port] if self.port is not None else [DEFAULT_PORT, DEFAULT_PORT + 1, DEFAULT_PORT + 2, 0]
        last = None
        for p in ports:
            try:
                self.httpd = _Server(("0.0.0.0", p), handler)
                break
            except OSError as e:
                last = e
        if not self.httpd:
            raise HopError("Couldn't open a network port: %s" % last)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True).start()
        if self.discover:
            self.discovery = Discovery(self.id, self.name, self.port, __version__, PLATFORM, lambda: self.emit("peers", self.peers()))
            self.discovery.start([self.ip])
        if self.relay:
            from .remote import Remote
            self.remote = Remote(self).start()
        return self

    def stop(self):
        for o in list(self.outgoing.values()):
            o.cancelled.set()
        for i in list(self.incoming.values()):
            if i.accepted and i.status != "done":
                self._save_state(i, force=True)
        if self.remote:
            self.remote.stop()
        if self.discovery:
            self.discovery.stop()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    def emit(self, ev, payload):
        try:
            self.emit_cb(ev, payload)
        except Exception:
            pass

    def rename(self, name):
        name = (name or "").strip()[:40]
        if name:
            self.settings.set("device_name", name)
            if self.discovery:
                self.discovery.rename(name)

    # ---- peers ----
    def peers(self):
        """Devices we can see or have paired with, for the UI."""
        out = {}
        found = self.discovery.peers() if self.discovery else []
        for p in found + list(self.manual.values()):
            out[p["id"]] = dict(p, online=True)
        for did, info in (self.settings.get("peers") or {}).items():
            if did in out:
                out[did]["name"] = out[did].get("name") or info.get("name")
            else:
                out[did] = {"id": did, "name": info.get("name"), "online": False, "platform": ""}
            out[did]["paired"] = True
        for p in out.values():
            p.setdefault("paired", False)
        if self.remote:
            from .remote import pretty
            for code, c in self.remote.contacts().items():
                out["code:" + code] = {"id": "code:" + code, "name": c.get("name"), "online": True, "paired": True,
                                       "platform": "", "remote": True, "code": pretty(code)}
        return sorted(out.values(), key=lambda p: (not p["online"], (p.get("name") or "").lower()))

    def find_peer(self, ref):
        """A peer by id, name (case-insensitive) or host[:port]."""
        ps = self.peers()
        for p in ps:
            if p["id"] == ref:
                return p
        for p in ps:
            if (p.get("name") or "").lower() == str(ref).lower() and p["online"]:
                return p
        if re.match(r"^[\w.\-]+(:\d+)?$", str(ref)) and ("." in str(ref) or ":" in str(ref)):
            return self.add_by_address(ref)
        return None

    def add_by_address(self, addr):
        host, _, port = str(addr).strip().partition(":")
        port = int(port or DEFAULT_PORT)
        hello = self._json("GET", host, port, "/hop/hello", timeout=4)
        if hello.get("app") != "hop":
            raise HopError("That isn't a Hop device")
        if hello["id"] == self.id:
            raise HopError("That's this computer")
        p = {"id": hello["id"], "name": hello.get("name"), "host": host, "port": port,
             "platform": hello.get("platform", ""), "version": hello.get("version", ""), "manual": True}
        self.manual[p["id"]] = p
        self.emit("peers", self.peers())
        return dict(p, online=True, paired=bool(self.settings.peer(p["id"])))

    def _addr(self, peer_id):
        for p in self.peers():
            if p["id"] == peer_id and p.get("online"):
                return p["host"], p["port"]
        raise HopError("That device isn't on this network right now")

    def key_for(self, peer_id):
        if peer_id.startswith("web:"):
            parts = peer_id.split(":")
            if len(parts) >= 2 and parts[1] == self.web_sid:
                return self.web_key
            return None
        info = self.settings.peer(peer_id)
        return b64d(info["key"]) if info else None

    def forget(self, peer_id):
        self.settings.forget_peer(peer_id)
        self.emit("peers", self.peers())

    # ---- phone link ----
    def web_url(self):
        return "http://%s:%d/#%s.%s" % (self.ip, self.port, self.web_sid, b64e(self.web_key))

    def new_web_link(self):
        self.web_sid = crypto.random_id(6)
        self.web_key = crypto.new_key()
        self.web_clients = {}
        return self.web_url()

    # ---- low-level client ----
    def _http(self, method, host, port, path, body=None, headers=None, timeout=30):
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            h = {"User-Agent": "Hop/" + __version__}
            h.update(headers or {})
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def _json(self, method, host, port, path, obj=None, timeout=30):
        body = json.dumps(obj).encode() if obj is not None else None
        try:
            st, data = self._http(method, host, port, path, body, {"Content-Type": "application/json"}, timeout)
        except (OSError, http.client.HTTPException) as e:
            raise HopError("Couldn't reach the device (%s)" % e)
        try:
            res = json.loads(data.decode() or "{}")
        except ValueError:
            res = {}
        if st >= 400:
            raise HopError(res.get("error") or "The device said no (%d)" % st)
        return res

    def _sealed(self, peer_id, path, obj, timeout=40, conn=None):
        """POST a sealed JSON message to a paired peer; returns its sealed JSON answer."""
        key = self.key_for(peer_id)
        if not key:
            raise HopError("not paired")
        host, port = self._addr(peer_id)
        body = crypto.seal(key, "req:" + path, json.dumps(obj).encode())
        try:
            st, data = self._http("POST", host, port, path, body, {"X-Hop-From": self.id, "Content-Type": "application/octet-stream"}, timeout)
        except (OSError, http.client.HTTPException) as e:
            raise HopError("Couldn't reach %s (%s)" % (self._peer_name(peer_id), e))
        if st == 401:
            raise HopError("unpaired")
        if st >= 400:
            try:
                msg = json.loads(data.decode()).get("error")
            except Exception:
                msg = None
            raise HopError(msg or "The other device said no (%d)" % st)
        try:
            return json.loads(crypto.open_(key, "res:" + path, data).decode())
        except crypto.CryptoError:
            raise HopError("The answer didn't check out. Pair the devices again.")

    def _peer_name(self, peer_id):
        for p in self.peers():
            if p["id"] == peer_id:
                return p.get("name") or "the other device"
        return "the other device"

    # ---- pairing: our side types the code ----
    def pair_begin(self, peer_id):
        host, port = self._addr(peer_id)
        res = self._json("POST", host, port, "/hop/pair/begin", {"id": self.id, "name": self.name, "platform": PLATFORM})
        self.pair_pending[peer_id] = {"pid": res["pid"], "msg_b": b64d(res["msg_b"]), "host": host, "port": port}
        return True

    def pair_finish(self, peer_id, code):
        pend = self.pair_pending.pop(peer_id, None)
        if not pend:
            raise HopError("Start pairing again")
        msg_a, confirm_a, key, confirm_b = crypto.pair_initiate(code, self.id, peer_id, pend["msg_b"])
        try:
            res = self._json("POST", pend["host"], pend["port"], "/hop/pair/finish",
                             {"pid": pend["pid"], "id": self.id, "msg_a": b64e(msg_a), "confirm_a": b64e(confirm_a)})
        except HopError as e:
            raise HopError("That code didn't match" if "code" in str(e).lower() else str(e))
        import hmac
        if not hmac.compare_digest(b64d(res.get("confirm_b", "")), confirm_b):
            raise HopError("The other device couldn't prove it had the code")
        self.settings.add_peer(peer_id, self._peer_name(peer_id), b64e(key), int(time.time()))
        self.emit("peers", self.peers())
        return True

    def pair_cancel(self, peer_id):
        pend = self.pair_pending.pop(peer_id, None)
        if pend:
            try:
                self._json("POST", pend["host"], pend["port"], "/hop/pair/cancel", {"pid": pend["pid"]}, timeout=4)
            except HopError:
                pass

    # ---- pairing: our side shows the code ----
    def _pair_begin_req(self, obj):
        sid = str(obj.get("id", ""))[:64]
        if not re.match(r"^[0-9a-f]{8,64}$", sid):
            raise HopError("bad id")
        with self.lock:
            for pid, s in list(self.pair_sessions.items()):
                if s["sender"] == sid or s["expires"] < time.time():
                    self.pair_sessions.pop(pid, None)
                    self.emit("pair_end", {"pid": pid})
            code = crypto.new_code()
            pid = crypto.random_id(8)
            resp = crypto.PairResponder(code, sid, self.id)
            name = str(obj.get("name") or "A computer")[:40]
            self.pair_sessions[pid] = {"responder": resp, "sender": sid, "name": name, "expires": time.time() + 300,
                                       "platform": str(obj.get("platform", ""))[:12]}
        self.emit("pair_code", {"pid": pid, "code": code[:3] + " " + code[3:], "name": name})
        return {"pid": pid, "msg_b": b64e(resp.msg_b)}

    def _pair_finish_req(self, obj):
        with self.lock:
            s = self.pair_sessions.pop(str(obj.get("pid")), None)  # one guess per code
        if not s or s["expires"] < time.time() or s["sender"] != obj.get("id"):
            raise HopError("Pairing expired. Start again.")
        self.emit("pair_end", {"pid": obj.get("pid")})
        try:
            key, confirm_b = s["responder"].finish(b64d(obj["msg_a"]), b64d(obj["confirm_a"]))
        except (crypto.CryptoError, KeyError, ValueError):
            self.emit("pair_failed", {"name": s["name"]})
            raise HopError("Wrong code")
        self.settings.add_peer(s["sender"], s["name"], b64e(key), int(time.time()))
        self.emit("paired", {"id": s["sender"], "name": s["name"]})
        self.emit("peers", self.peers())
        return {"ok": True, "confirm_b": b64e(confirm_b)}

    def _pair_cancel_req(self, obj):
        with self.lock:
            if self.pair_sessions.pop(str(obj.get("pid")), None):
                self.emit("pair_end", {"pid": obj.get("pid")})
        return {"ok": True}

    # ---- public transfer state for the UI ----
    def public(self, t):
        out = isinstance(t, Outgoing)
        done = t.sent if out else t.done_bytes
        names = [f["name"] for f in t.files]
        d = {"tid": t.tid, "dir": "out" if out else "in", "peer": t.peer_id, "peer_name": t.peer_name, "kind": t.kind,
             "status": t.status, "error": t.error, "count": len(t.files), "names": names[:3], "total": t.total,
             "done": min(done, t.total), "speed": t.rate.value if t.status in ("sending", "receiving") else 0,
             "folder": None, "first": None, "label": None}
        tops = {n.replace("\\", "/").split("/")[0] for n in names}
        if len(tops) == 1 and "/" in names[0].replace("\\", "/"):
            d["label"] = tops.pop()
        if not out:
            finals = [f.get("final") for f in t.files if f.get("final")]
            if finals:
                d["first"] = finals[0]
                d["folder"] = t.save_dir
        return d

    def transfers(self):
        with self.lock:
            ts = [self.incoming.get(t) or self.outgoing.get(t) for t in self.order]
        return [self.public(t) for t in ts if t]

    def _emit_t(self, t, force=False):
        now = time.time()
        if force or now - getattr(t, "last_emit", 0) > 0.2:
            t.last_emit = now
            self.emit("transfer", self.public(t))

    def _track(self, t):
        with self.lock:
            if t.tid in self.order:
                self.order.remove(t.tid)
            self.order.append(t.tid)

    # ---- receiving ----
    def _offer_req(self, peer_id, obj):
        tid = str(obj.get("tid", ""))
        if not re.match(r"^[0-9a-f]{16,64}$", tid):
            raise HopError("bad transfer id")
        with self.lock:
            inc = self.incoming.get(tid)
            if inc and inc.peer_id != peer_id:
                raise HopError("transfer belongs to another device")
            if inc and inc.status == "done" and time.time() - getattr(inc, "finished", 0) > 60:
                inc = None  # the same files sent again later: a new copy, not a no-op
            if inc is None:
                inc = self._new_incoming(peer_id, tid, obj)
                self.incoming[tid] = inc
                self._track(inc)
                is_new = True
            else:
                is_new = False
                if inc.status in ("failed", "cancelled", "declined") and inc.accepted:
                    inc.status = "receiving"
                    inc.error = None
        if is_new:
            if inc.accepted:
                self._prepare(inc)
            else:
                self.emit("offer", self.public(inc))
            self._emit_t(inc, True)
        inc.decided.wait(25 if not peer_id.startswith("web:") else 0)
        if not inc.decided.is_set():
            return {"state": "asking"}
        if not inc.accepted:
            return {"state": "declined"}
        if inc.status in ("asking",):
            inc.status = "receiving"
        return {"state": "accepted", "need": inc.need()}

    def _new_incoming(self, peer_id, tid, obj, kind=None, pname=None, trusted=True):
        files = obj.get("files") or []
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            raise HopError("bad file list")
        save_dir = self.settings.save_dir
        clean = []
        for f in files:
            size = int(f.get("size", -1))
            if size < 0:
                raise HopError("bad file size")
            sha = f.get("sha256")
            if sha is not None and not re.match(r"^[0-9a-f]{64}$", str(sha)):
                raise HopError("bad hash")
            clean.append({"name": safe_relpath(f.get("name", "file")), "size": size, "sha256": sha,
                          "chunks": chunks_for(size), "have": set(), "status": "waiting", "final": None})
        if kind:
            pass
        elif peer_id.startswith("web:"):
            kind, pname = "phone", str(obj.get("from_name") or "Phone")[:40]
        else:
            kind, pname = "computer", self._peer_name(peer_id)
        inc = Incoming(tid, peer_id, pname, kind, clean, save_dir)
        # Resume: chunks already on disk from an earlier attempt.
        st = os.path.join(inc.part_dir, "state.json")
        try:
            with open(st, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            if saved.get("peer") == peer_id and len(saved.get("files", [])) == len(clean):
                for f, s in zip(clean, saved["files"]):
                    if s.get("size") == f["size"] and os.path.exists(os.path.join(inc.part_dir, "%d.part" % clean.index(f))):
                        f["have"] = from_ranges(s.get("have"), f["chunks"])
        except (OSError, ValueError, TypeError):
            pass
        if kind == "phone" or (trusted and self.settings.get("auto_accept")) or any(f["have"] for f in clean):
            inc.accepted = True
            inc.status = "receiving"
            inc.decided.set()
        return inc

    def _add_incoming(self, inc):
        """A transfer that arrived by Hop code (the relay), not over the LAN."""
        with self.lock:
            self.incoming[inc.tid] = inc
            self._track(inc)
        if inc.accepted:
            self._prepare(inc)
            self.remote.decided(inc)
        else:
            self.emit("offer", self.public(inc))
        self._emit_t(inc, True)

    def decide(self, tid, accept):
        inc = self.incoming.get(tid)
        if not inc or inc.decided.is_set():
            return
        inc.accepted = bool(accept)
        if accept:
            inc.status = "receiving"
            self._prepare(inc)
        else:
            inc.status = "declined"
        inc.decided.set()
        self._emit_t(inc, True)
        if inc.kind == "remote" and self.remote:
            self.remote.decided(inc)

    def _prepare(self, inc):
        os.makedirs(inc.part_dir, exist_ok=True)
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(os.path.dirname(inc.part_dir), 0x02)
            except Exception:
                pass
        for i, f in enumerate(inc.files):
            p = os.path.join(inc.part_dir, "%d.part" % i)
            if not os.path.exists(p):
                with open(p, "wb") as fh:
                    fh.truncate(f["size"])
            if f["chunks"] == 0 and f["status"] != "done":
                self._finalize(inc, i)
        self._save_state(inc, force=True)

    def _save_state(self, inc, force=False):
        now = time.time()
        if not force and now - inc.last_save < 2:
            return
        inc.last_save = now
        data = {"peer": inc.peer_id, "files": [{"name": f["name"], "size": f["size"], "have": to_ranges(f["have"])} for f in inc.files]}
        try:
            tmp = os.path.join(inc.part_dir, "state.json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, os.path.join(inc.part_dir, "state.json"))
        except OSError:
            pass

    def _chunk_req(self, peer_id, tid, fi, ci, blob):
        key = self.key_for(peer_id)
        data = crypto.open_(key, "chunk/%s/%d/%d" % (tid, fi, ci), blob)
        inc = self.incoming.get(tid)
        if not inc or inc.peer_id != peer_id or not inc.accepted or inc.status in ("cancelled", "declined"):
            raise HopError("no such transfer")
        if not 0 <= fi < len(inc.files):
            raise HopError("bad file")
        f = inc.files[fi]
        if not 0 <= ci < f["chunks"]:
            raise HopError("bad chunk")
        self._store_chunk(inc, fi, ci, data)

    def _store_chunk(self, inc, fi, ci, data):
        f = inc.files[fi]
        want = min(CHUNK, f["size"] - ci * CHUNK)
        if len(data) != want:
            raise HopError("chunk is the wrong size")
        with inc.flocks[fi]:
            if ci in f["have"] or f["status"] == "done":
                return
            with open(os.path.join(inc.part_dir, "%d.part" % fi), "r+b") as fh:
                fh.seek(ci * CHUNK)
                fh.write(data)
            f["have"].add(ci)
            f["status"] = "receiving"
            complete = len(f["have"]) == f["chunks"]
        inc.rate.add(len(data))
        if inc.status != "receiving":
            inc.status = "receiving"
        if complete:
            self._finalize(inc, fi)
        else:
            self._save_state(inc)
            self._emit_t(inc)

    def _finalize(self, inc, fi):
        f = inc.files[fi]
        part = os.path.join(inc.part_dir, "%d.part" % fi)
        with inc.flocks[fi]:
            if f["status"] == "done":
                return
            if f["sha256"]:
                f["status"] = "verifying"
                self._emit_t(inc, True)
                if crypto.file_sha256(part) != f["sha256"]:
                    f["have"] = set()
                    f["status"] = "retry"
                    self._save_state(inc, force=True)
                    return
            with inc.lock:
                dst = unique_path(os.path.join(inc.save_dir, f["name"]))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(part, dst)
                f["final"] = dst
                f["status"] = "done"
        self._save_state(inc, force=True)
        if all(x["status"] == "done" for x in inc.files):
            inc.status = "done"
            inc.finished = time.time()
            shutil.rmtree(inc.part_dir, ignore_errors=True)
            try:
                os.rmdir(os.path.dirname(inc.part_dir))
            except OSError:
                pass
            self._emit_t(inc, True)
            self.emit("received", self.public(inc))
        else:
            self._emit_t(inc, True)

    def _finish_req(self, peer_id, obj):
        inc = self.incoming.get(str(obj.get("tid")))
        if not inc or inc.peer_id != peer_id:
            raise HopError("no such transfer")
        need = inc.need()
        return {"state": inc.status, "need": need}

    def _cancel_req(self, peer_id, obj):
        tid = str(obj.get("tid"))
        inc = self.incoming.get(tid)
        if inc and inc.peer_id == peer_id and inc.status != "done":
            inc.status = "cancelled"
            inc.error = "%s stopped sending" % inc.peer_name
            inc.decided.set()
            self._save_state(inc, force=True)
            self._emit_t(inc, True)
        out = self.outgoing.get(tid)
        if out and out.peer_id == peer_id and out.status != "done":
            out.cancelled.set()
            out.status = "cancelled"
            self._emit_t(out, True)
        return {"ok": True}

    def cancel(self, tid):
        """Stop a transfer from this side (either direction). Received chunks stay, so it can resume."""
        out = self.outgoing.get(tid)
        if out and out.status not in ("done",):
            out.cancelled.set()
            out.status = "cancelled"
            self._emit_t(out, True)
            if out.kind == "computer":
                threading.Thread(target=self._try_cancel_remote, args=(out.peer_id, tid), daemon=True).start()
            elif out.kind == "remote" and self.remote:
                threading.Thread(target=self.remote.cancel_sent, args=(out,), daemon=True).start()
        inc = self.incoming.get(tid)
        if inc and inc.status not in ("done",):
            if not inc.decided.is_set():
                self.decide(tid, False)
                return
            inc.status = "cancelled"
            self._save_state(inc, force=True)
            self._emit_t(inc, True)
            if inc.kind == "computer":
                threading.Thread(target=self._try_cancel_remote, args=(inc.peer_id, tid), daemon=True).start()

    def _try_cancel_remote(self, peer_id, tid):
        try:
            self._sealed(peer_id, "/hop/cancel", {"tid": tid}, timeout=5)
        except HopError:
            pass

    def remove(self, tid):
        with self.lock:
            t = self.incoming.get(tid) or self.outgoing.get(tid)
            if t and t.status in ("done", "failed", "cancelled", "declined"):
                self.incoming.pop(tid, None)
                self.outgoing.pop(tid, None)
                if tid in self.order:
                    self.order.remove(tid)
                if isinstance(t, Incoming) and t.status != "done":
                    shutil.rmtree(t.part_dir, ignore_errors=True)

    # ---- sending to a computer ----
    def send(self, peer_id, paths, wait=False):
        files = collect(paths)
        if not files:
            raise HopError("Nothing to send")
        if peer_id == "phone":
            return self.share_to_phone(files)
        if peer_id.startswith("code:"):
            return self.send_by_code(peer_id, files, wait)
        if not self.key_for(peer_id):
            raise HopError("unpaired")
        tmp = crypto.random_id(12)
        out = Outgoing(tmp, peer_id, self._peer_name(peer_id), "computer",
                       [{"path": p, "name": n, "size": os.path.getsize(p), "sha256": None, "chunks": chunks_for(os.path.getsize(p))}
                        for p, n in files])
        with self.lock:
            self.outgoing[tmp] = out
            self._track(out)
        self._emit_t(out, True)
        th = threading.Thread(target=self._run_send, args=(out,), daemon=True)
        th.start()
        if wait:
            th.join()
        return out

    def send_by_code(self, peer_id, files, wait=False):
        if not self.remote:
            raise HopError("Sending by Hop code is turned off")
        c = self.remote.contacts().get(peer_id.split(":", 1)[1])
        if not c:
            raise HopError("Add this person by their Hop code first")
        tmp = crypto.random_id(12)
        out = Outgoing(tmp, peer_id, c.get("name") or "Someone", "remote",
                       [{"path": p, "name": n, "size": os.path.getsize(p), "sha256": None, "chunks": chunks_for(os.path.getsize(p))}
                        for p, n in files])
        with self.lock:
            self.outgoing[tmp] = out
            self._track(out)
        self._emit_t(out, True)
        th = threading.Thread(target=self._run_remote, args=(out,), daemon=True)
        th.start()
        if wait:
            th.join()
        return out

    def _run_remote(self, out):
        from .remote import RelayError
        try:
            self.remote.send(out)
        except (RelayError, HopError) as e:
            if out.status != "cancelled":
                out.status = "failed"
                out.error = str(e)
        except Exception as e:  # pragma: no cover
            out.status = "failed"
            out.error = "%s: %s" % (type(e).__name__, e)
        finally:
            if out.cancelled.is_set() and out.status != "done":
                out.status = "cancelled"
            self._emit_t(out, True)

    def retry(self, tid):
        inc = self.incoming.get(tid)
        if inc and inc.kind == "remote" and inc.accepted and inc.status in ("failed", "cancelled") and self.remote:
            inc.status = "receiving"
            threading.Thread(target=self.remote.download, args=(inc,), daemon=True).start()
            return
        out = self.outgoing.get(tid)
        if out and out.status in ("failed", "cancelled") and out.kind == "remote":
            out.cancelled = threading.Event()
            out.status = "preparing"
            out.error = None
            threading.Thread(target=self._run_remote, args=(out,), daemon=True).start()
            self._emit_t(out, True)
            return
        if out and out.status in ("failed", "cancelled") and out.kind == "computer":
            out.cancelled = threading.Event()
            out.status = "preparing"
            out.error = None
            threading.Thread(target=self._run_send, args=(out,), daemon=True).start()
            self._emit_t(out, True)

    def _rekey(self, out, new_tid):
        with self.lock:
            self.outgoing.pop(out.tid, None)
            if out.tid in self.order:
                self.order[self.order.index(out.tid)] = new_tid
            old = out.tid
            out.tid = new_tid
            self.outgoing[new_tid] = out
        self.emit("retid", {"old": old, "tid": new_tid})

    def _run_send(self, out):
        try:
            # 1. Hash (once). The id makes the transfer resumable.
            if any(f["sha256"] is None for f in out.files):
                out.status = "preparing"
                done = [0]

                def prog(n):
                    done[0] += n
                    out.sent = done[0]
                    out.error = None
                    self._emit_t(out)
                for f in out.files:
                    if out.cancelled.is_set():
                        return
                    f["sha256"] = crypto.file_sha256(f["path"], prog)
                h = hashlib.sha256()
                h.update(("%s>%s" % (self.id, out.peer_id)).encode())
                for f in out.files:
                    h.update(("\0%s\0%d\0%s" % (f["name"], f["size"], f["sha256"])).encode())
                self._rekey(out, h.hexdigest()[:32])
            out.sent = 0
            manifest = {"tid": out.tid, "files": [{"name": f["name"], "size": f["size"], "sha256": f["sha256"]} for f in out.files]}

            attempts = 0
            while not out.cancelled.is_set():
                try:
                    # 2. Offer and wait for the other side to accept.
                    out.status = "asking"
                    self._emit_t(out, True)
                    deadline = time.time() + 600
                    while True:
                        res = self._sealed(out.peer_id, "/hop/offer", manifest, timeout=40)
                        if res["state"] != "asking" or out.cancelled.is_set() or time.time() > deadline:
                            break
                    if out.cancelled.is_set():
                        return
                    if res["state"] == "declined":
                        out.status = "declined"
                        out.error = "%s declined" % out.peer_name
                        return
                    if res["state"] == "asking":
                        raise HopError("No answer from %s" % out.peer_name)
                    # 3. Send what's missing, check, repeat for anything that failed its hash.
                    for _round in range(3):
                        need = res.get("need") or {}
                        if not need:
                            break
                        out.status = "sending"
                        out.sent = out.total - sum(
                            min(out.files[int(fi)]["size"] - a * CHUNK, (b - a) * CHUNK) for fi, rs in need.items() for a, b in rs)
                        self._push_chunks(out, need)
                        if out.cancelled.is_set():
                            return
                        res = self._sealed(out.peer_id, "/hop/finish", {"tid": out.tid}, timeout=600)
                    if res.get("need"):
                        raise HopError("Some files didn't arrive intact")
                    out.sent = out.total
                    out.status = "done"
                    return
                except HopError as e:
                    if str(e) == "unpaired":
                        out.status = "failed"
                        out.error = "%s doesn't know this computer any more. Pair again." % out.peer_name
                        self.settings.forget_peer(out.peer_id)
                        return
                    attempts += 1
                    if attempts > 20 or out.cancelled.is_set():
                        raise
                    out.status = "waiting"
                    out.error = "%s. Retrying…" % e
                    self._emit_t(out, True)
                    out.cancelled.wait(min(15, 1.5 * attempts))
        except HopError as e:
            if out.status != "cancelled":
                out.status = "failed"
                out.error = str(e)
        except Exception as e:  # pragma: no cover - unexpected; surface it
            out.status = "failed"
            out.error = "%s: %s" % (type(e).__name__, e)
        finally:
            if out.cancelled.is_set() and out.status != "done":
                out.status = "cancelled"
            self._emit_t(out, True)

    def _push_chunks(self, out, need):
        key = self.key_for(out.peer_id)
        host, port = self._addr(out.peer_id)
        q = queue.Queue()
        for fi, ranges in sorted(need.items(), key=lambda kv: int(kv[0])):
            for a, b in ranges:
                for ci in range(a, b):
                    q.put((int(fi), ci))
        errors = []

        def worker():
            conn = None
            handles = {}
            try:
                while not out.cancelled.is_set() and not errors:
                    try:
                        fi, ci = q.get_nowait()
                    except queue.Empty:
                        return
                    f = out.files[fi]
                    fh = handles.get(fi)
                    if fh is None:
                        fh = handles[fi] = open(f["path"], "rb")
                    fh.seek(ci * CHUNK)
                    data = fh.read(CHUNK)
                    body = crypto.seal(key, "chunk/%s/%d/%d" % (out.tid, fi, ci), data)
                    for tries in range(4):
                        try:
                            if conn is None:
                                conn = http.client.HTTPConnection(host, port, timeout=60)
                            conn.request("PUT", "/hop/chunk/%s/%d/%d" % (out.tid, fi, ci), body=body,
                                         headers={"X-Hop-From": self.id, "Content-Type": "application/octet-stream"})
                            r = conn.getresponse()
                            msg = r.read()
                            if r.status >= 400:
                                raise HopError(_err(msg) or "The other device refused a chunk (%d)" % r.status)
                            break
                        except (OSError, http.client.HTTPException) as e:
                            try:
                                conn.close()
                            except Exception:
                                pass
                            conn = None
                            if tries == 3:
                                errors.append(HopError("Connection lost (%s)" % e))
                                return
                            time.sleep(0.5 * (tries + 1))
                    with out.lock:
                        out.sent += len(data)
                    out.rate.add(len(data))
                    self._emit_t(out)
            except HopError as e:
                errors.append(e)
            except OSError as e:
                errors.append(HopError("Couldn't read %s (%s)" % (os.path.basename(out.files[fi]["path"]), e)))
            finally:
                for fh in handles.values():
                    fh.close()
                if conn:
                    conn.close()

        ths = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        if errors:
            raise errors[0]

    # ---- sending to a phone (the phone pulls) ----
    def share_to_phone(self, files):
        tid = crypto.random_id(16)
        out = Outgoing(tid, "phone", "Phone", "phone",
                       [{"path": p, "name": n.replace("/", " - "), "size": os.path.getsize(p), "sha256": None,
                         "chunks": chunks_for(os.path.getsize(p))} for p, n in files])
        out.status = "ready"
        with self.lock:
            self.outgoing[tid] = out
            self._track(out)
        self._emit_t(out, True)
        return out

    def _inbox_req(self, peer_id):
        self.web_seen = time.time()
        items = []
        for tid in list(self.order):
            o = self.outgoing.get(tid)
            if o and o.kind == "phone" and o.status in ("ready", "sending", "done"):
                items.append({"tid": tid, "files": [{"name": f["name"], "size": f["size"], "chunks": f["chunks"]} for f in o.files],
                              "done": o.status == "done"})
        return {"items": items, "name": self.name}

    def _pull_req(self, peer_id, tid, fi, ci):
        out = self.outgoing.get(tid)
        if not out or out.kind != "phone" or out.status not in ("ready", "sending", "done"):
            raise HopError("no such file")
        f = out.files[fi]
        if not 0 <= ci < max(1, f["chunks"]):
            raise HopError("bad chunk")
        with open(f["path"], "rb") as fh:
            fh.seek(ci * CHUNK)
            data = fh.read(CHUNK)
        with out.lock:
            if (fi, ci) not in out.served:
                out.served[(fi, ci)] = True
                out.sent += len(data)
                out.rate.add(len(data))
                if out.status == "ready":
                    out.status = "sending"
                if out.sent >= out.total and all((i, c) in out.served for i, x in enumerate(out.files) for c in range(x["chunks"])):
                    out.status = "done"
        self._emit_t(out, out.status == "done")
        return crypto.seal(self.web_key, "pull/%s/%d/%d" % (tid, fi, ci), data)


def _err(raw):
    try:
        return json.loads(raw.decode()).get("error")
    except Exception:
        return None


# ---- HTTP server -------------------------------------------------------------

def _make_handler(node):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Hop/" + __version__
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode())

        def _body(self, limit):
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > limit:
                raise HopError("too large")
            return self.rfile.read(n) if n else b""

        def _sealed_in(self, path, limit=MAX_JSON):
            peer = self.headers.get("X-Hop-From", "")
            key = node.key_for(peer)
            body = self._body(limit)
            if not key:
                return None, None, None
            try:
                obj = json.loads(crypto.open_(key, "req:" + path, body).decode())
            except (crypto.CryptoError, ValueError):
                return None, None, None
            return peer, key, obj

        def _reply(self, key, path, obj):
            self._send(200, crypto.seal(key, "res:" + path, json.dumps(obj).encode()), "application/octet-stream")

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path == "/hop/hello":
                    return self._json(200, {"app": "hop", "proto": PROTO, "id": node.id, "name": node.name,
                                            "platform": PLATFORM, "version": __version__})
                m = re.match(r"^/hop/pull/([0-9a-f]{16,64})/(\d+)/(\d+)$", path)
                if m:
                    peer = self.headers.get("X-Hop-From", "")
                    if not peer.startswith("web:") or node.key_for(peer) is None:
                        return self._json(401, {"error": "This link has expired. Scan the code again."})
                    data = node._pull_req(peer, m.group(1), int(m.group(2)), int(m.group(3)))
                    return self._send(200, data, "application/octet-stream")
                static = {"/": "phone.html", "/index.html": "phone.html", "/phone.js": "phone.js",
                          "/phone.css": "phone.css", "/nacl-fast.min.js": "nacl-fast.min.js", "/icon.png": "icon.png"}
                if path in static:
                    fp = os.path.join(WEB_DIR, static[path])
                    with open(fp, "rb") as fh:
                        data = fh.read()
                    ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or ctype.endswith("javascript"):
                        ctype += "; charset=utf-8"
                    return self._send(200, data, ctype, {"Content-Security-Policy":
                                      "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; connect-src 'self'"})
                self._json(404, {"error": "not found"})
            except HopError as e:
                self._json(400, {"error": str(e)})
            except OSError:
                self._json(404, {"error": "not found"})

        do_HEAD = do_GET

        def do_PUT(self):
            path = urlparse(self.path).path
            m = re.match(r"^/hop/chunk/([0-9a-f]{16,64})/(\d+)/(\d+)$", path)
            if not m:
                return self._json(404, {"error": "not found"})
            try:
                body = self._body(CHUNK + crypto.OVERHEAD + 512)
                peer = self.headers.get("X-Hop-From", "")
                if node.key_for(peer) is None:
                    return self._json(401, {"error": "unpaired"})
                node._chunk_req(peer, m.group(1), int(m.group(2)), int(m.group(3)), body)
                self._send(204)
            except crypto.CryptoError:
                self._json(401, {"error": "chunk failed authentication"})
            except (HopError, OSError) as e:
                self._json(409, {"error": str(e)})

        def do_POST(self):
            path = unquote(urlparse(self.path).path)
            try:
                if path.startswith("/hop/pair/"):
                    obj = json.loads(self._body(64 << 10).decode() or "{}")
                    fn = {"/hop/pair/begin": node._pair_begin_req, "/hop/pair/finish": node._pair_finish_req,
                          "/hop/pair/cancel": node._pair_cancel_req}.get(path)
                    if not fn:
                        return self._json(404, {"error": "not found"})
                    try:
                        return self._json(200, fn(obj))
                    except HopError as e:
                        return self._json(403, {"error": str(e)})
                if path not in ("/hop/offer", "/hop/finish", "/hop/cancel", "/hop/inbox"):
                    return self._json(404, {"error": "not found"})
                peer, key, obj = self._sealed_in(path)
                if not peer:
                    return self._json(401, {"error": "unpaired"})
                if path == "/hop/offer":
                    res = node._offer_req(peer, obj)
                elif path == "/hop/finish":
                    res = node._finish_req(peer, obj)
                elif path == "/hop/cancel":
                    res = node._cancel_req(peer, obj)
                else:
                    res = node._inbox_req(peer)
                self._reply(key, path, res)
            except HopError as e:
                self._json(409, {"error": str(e)})
            except (ValueError, KeyError, TypeError):
                self._json(400, {"error": "bad request"})

    return Handler
