"""Sending by Hop code, to anyone, on any network.

Every device has a Hop code like 7KQ2-9XMD-4TRA. It's the first 60 bits of the SHA-256 of
the device's Ed25519 public key, so a code can't be claimed by anyone else: when you add a
code, Hop fetches the key from hop.pxlvzn.in and checks it hashes to that code.

When both devices are on different networks, the files travel through a small relay
(hop.pxlvzn.in/api/hop), sealed end to end before they leave this computer:

  1. A random 256-bit key K encrypts every 1 MiB piece (NaCl secretbox, bound to its slot).
  2. The list of files and K go in an "offer" sealed with NaCl box from the sender's key to
     the recipient's key, so only the recipient can open it and they know who sent it.
  3. The relay stores the pieces for up to 7 days and rings the recipient's doorbell (a push
     message on ntfy.sh to a topic derived from their code, so an idle Hop costs the relay
     nothing). The recipient accepts or declines, downloads, checks each file's SHA-256,
     then deletes everything from the relay.

The relay only ever sees Hop codes, sizes and ciphertext.
"""

import hashlib
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request

import nacl.public
import nacl.signing
import nacl.utils

from . import __version__
from . import crypto
from .crypto import b64d, b64e

API = os.environ.get("HOP_RELAY_API", "https://hop.pxlvzn.in/api/hop")
B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CHUNK = 1 << 20          # the file layout on disk (same as LAN transfers)
PIECE = 8 * CHUNK        # what travels through the relay: fewer, bigger uploads
BATCH = 64
WORKERS = 4
NTFY = os.environ.get("HOP_NTFY", "https://ntfy.sh")


class RelayError(Exception):
    pass


# ---- codes -----------------------------------------------------------------

def code_of(vk):
    n = int.from_bytes(hashlib.sha256(b"hop-id" + bytes(vk)).digest()[:8], "big") >> 4
    out = ""
    for _ in range(12):
        out = B32[n & 31] + out
        n >>= 5
    return out


def norm_code(s):
    s = "".join(ch for ch in str(s or "").upper() if ch.isalnum())
    s = s.replace("I", "1").replace("L", "1").replace("O", "0")
    if len(s) != 12 or any(ch not in B32 for ch in s):
        raise RelayError("That isn't a Hop code. It looks like 7KQ2-9XMD-4TRA.")
    return s


def pretty(code):
    return "%s-%s-%s" % (code[:4], code[4:8], code[8:])


def piece_map(files):
    """Global piece index -> (file index, piece index in that file). Empty files have none."""
    out = []
    for fi, f in enumerate(files):
        for pi in range((f["size"] + PIECE - 1) // PIECE):
            out.append((fi, pi))
    return out


def bell_topic(code):
    return "hop-" + hashlib.sha256(("hop-bell/" + code).encode()).hexdigest()[:32]


def _ssl_ctx():
    from .updater import _ssl_ctx as ctx
    return ctx()


# ---- identity --------------------------------------------------------------

class Identity(object):
    def __init__(self, settings):
        seed = settings.get("id_seed")
        if not seed:
            seed = b64e(nacl.utils.random(32))
            settings.set("id_seed", seed)
        self.sk = nacl.signing.SigningKey(b64d(seed))
        self.vk = bytes(self.sk.verify_key)
        self.vk_b64 = b64e(self.vk)
        self.code = code_of(self.vk)
        self.box_sk = self.sk.to_curve25519_private_key()

    def signed(self, op, **fields):
        """A request body signed the way the server checks it (see api/hop/_lib.js)."""
        body = dict(fields, vk=self.vk_b64, ts=int(time.time() * 1000))
        msg = "\n".join(["hop/" + op, str(body["ts"])] + [str(fields[k]) for k in fields])
        body["sig"] = b64e(self.sk.sign(msg.encode("utf-8")).signature)
        return body


def box_pk(vk_b64):
    return nacl.signing.VerifyKey(b64d(vk_b64)).to_curve25519_public_key()


# ---- the relay client --------------------------------------------------------

class Remote(object):
    def __init__(self, node):
        self.node = node
        self.settings = node.settings
        self.me = Identity(self.settings)
        self.code = self.me.code
        self.registered = False
        self.listening = False
        self.last_ring = int(time.time())
        self.online = False
        self.error = None
        self.max_bytes = 2 * 1024 ** 3
        self.stop_ev = threading.Event()
        self.wake = threading.Event()
        self.seen = set()  # offers already shown this session
        self.lock = threading.Lock()

    # ---- http ----
    def _post(self, op, body, timeout=30):
        return self._req("POST", "%s/%s" % (API, op), json.dumps(body).encode(), "application/json", timeout)

    def _req(self, method, url, data=None, ctype=None, timeout=60, raw=False):
        headers = {"User-Agent": "Hop/" + __version__}
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
                body = r.read()
                return body if raw else json.loads(body.decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error") or ""
            except Exception:
                pass
            err = RelayError(msg or "Hop's server said %d" % e.code)
            err.status = e.code
            raise err
        except (OSError, ValueError) as e:
            raise RelayError("Can't reach Hop's server. Check your internet connection. (%s)" % getattr(e, "reason", e))

    # ---- lifecycle ----
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def stop(self):
        self.stop_ev.set()
        self.wake.set()

    def status(self):
        return {"code": self.code, "pretty": pretty(self.code), "online": self.online, "error": self.error,
                "enabled": bool(self.settings.get("relay"))}

    def register(self):
        """Publish this code's key and name. Only when they change, to keep the relay cheap."""
        name = self.node.name or "Hop"
        if self.settings.get("relay_name") == name and time.time() - (self.settings.get("relay_at") or 0) < 30 * 86400:
            self.registered = True
            return
        r = self._post("register", self.me.signed("register", name=name))
        if r.get("code") != self.code:
            raise RelayError("Hop's server disagrees about this device's code")
        self.max_bytes = int(r.get("max_bytes") or self.max_bytes)
        self.settings.set("relay_name", name)
        self.settings.set("relay_at", int(time.time()))
        self.registered = True

    def _set_online(self, ok, err=None):
        changed = ok != self.online or err != self.error
        self.online, self.error = ok, err
        if changed:
            self.node.emit("relay", self.status())

    def _loop(self):
        threading.Thread(target=self._listen, daemon=True).start()
        backoff = 5
        check = True  # look at the inbox once at startup, then whenever the doorbell rings
        while not self.stop_ev.is_set():
            if not self.settings.get("relay"):
                self._set_online(False)
                self.wake.wait(30)
                self.wake.clear()
                check = True
                continue
            try:
                if not self.registered:
                    self.register()
                if check:
                    check = False
                    self.check_inbox()
                self._set_online(self.listening)
                backoff = 5
                self.wake.wait(60)
                if self.wake.is_set():
                    self.wake.clear()
                    check = True
                    time.sleep(1)  # a burst of rings is one inbox check
            except RelayError as e:
                self._set_online(False, str(e))
                check = True
                self.wake.wait(backoff)
                self.wake.clear()
                backoff = min(backoff * 2, 120)
            except Exception as e:  # pragma: no cover - never let the loop die
                self._set_online(False, "%s: %s" % (type(e).__name__, e))
                check = True
                self.wake.wait(30)
                self.wake.clear()

    def _listen(self):
        """Stay subscribed to this code's doorbell; any message means "check your inbox"."""
        backoff = 3
        while not self.stop_ev.is_set():
            if not self.settings.get("relay"):
                self.listening = False
                self.stop_ev.wait(10)
                continue
            url = "%s/%s/json?since=%d" % (NTFY, bell_topic(self.code), self.last_ring)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Hop/" + __version__})
                with urllib.request.urlopen(req, timeout=90, context=_ssl_ctx()) as r:
                    self.listening = True
                    self._set_online(self.registered and self.error is None)
                    backoff = 3
                    for line in r:
                        if self.stop_ev.is_set():
                            return
                        try:
                            ev = json.loads(line.decode("utf-8"))
                        except ValueError:
                            continue
                        if ev.get("event") == "message":
                            self.last_ring = int(ev.get("time") or time.time())
                            self.wake.set()
            except Exception:
                pass
            self.listening = False
            self._set_online(False, self.error)
            self.stop_ev.wait(backoff)
            backoff = min(backoff * 2, 60)
            self.wake.set()  # we may have missed a ring while disconnected

    # ---- people ----
    def lookup(self, code):
        code = norm_code(code)
        if code == self.code:
            raise RelayError("That's this computer's own code")
        r = self._req("GET", "%s/lookup?code=%s" % (API, code), timeout=20)
        if code_of(b64d(r.get("vk", ""))) != code:
            raise RelayError("Hop's server gave a key that doesn't match that code")
        return {"code": code, "name": (r.get("name") or "Someone")[:40], "vk": r["vk"]}

    def add(self, code):
        p = self.lookup(code)
        self.settings.add_contact(p["code"], p["name"], p["vk"], int(time.time()))
        return p

    def contacts(self):
        return self.settings.get("contacts") or {}

    # ---- sending ----
    def send(self, out):
        """Run an Outgoing whose peer_id is "code:XXXX". Called on its own thread."""
        code = out.peer_id.split(":", 1)[1]
        c = self.contacts().get(code)
        if not c:
            raise RelayError("Add this person by their Hop code first")
        if out.total > self.max_bytes:
            raise RelayError("Over the %s limit for sending over the internet. Send it on the same Wi-Fi instead." % _human(self.max_bytes))
        # 1. Hash
        out.status = "preparing"
        done = [0]

        def prog(n):
            done[0] += n
            out.sent = done[0]
            self.node._emit_t(out)
        for f in out.files:
            if out.cancelled.is_set():
                return
            if f["sha256"] is None:
                f["sha256"] = crypto.file_sha256(f["path"], prog)
        out.sent = 0
        pieces = piece_map(out.files)
        tid = getattr(out, "relay_tid", None) or crypto.random_id(16)
        out.relay_tid = tid
        key = getattr(out, "relay_key", None) or crypto.new_key()
        out.relay_key = key
        # 2. Reserve the transfer and upload the pieces
        r = self._post("send", self.me.signed("send", to=code, tid=tid, size=out.total, pieces=len(pieces)))
        offer_url = r["offer"]
        out.status = "sending"
        self.node._emit_t(out, True)
        for start in range(0, len(pieces), BATCH):
            if out.cancelled.is_set():
                return
            urls = self._post("urls", self.me.signed("urls", tid=tid, start=start, count=min(BATCH, len(pieces) - start)))["urls"]
            self._upload(out, key, tid, pieces, start, urls)
        if out.cancelled.is_set():
            return
        # 3. The sealed offer, then ring their doorbell
        offer = {"v": 1, "tid": tid, "name": self.node.name, "key": b64e(key),
                 "files": [{"name": f["name"], "size": f["size"], "sha256": f["sha256"]} for f in out.files]}
        sealed = nacl.public.Box(self.me.box_sk, box_pk(c["vk"])).encrypt(json.dumps(offer).encode("utf-8"))
        env = json.dumps({"vk": self.me.vk_b64, "box": b64e(bytes(sealed))}).encode("utf-8")
        self._req("PUT", offer_url, env, "application/json")
        self._post("sent", self.me.signed("sent", tid=tid))
        out.sent = out.total
        out.status = "done"

    def _upload(self, out, key, tid, pieces, start, urls):
        q = queue.Queue()
        for i, url in enumerate(urls):
            q.put((start + i, url))
        errors = []

        def worker():
            handles = {}
            try:
                while not out.cancelled.is_set() and not errors:
                    try:
                        gi, url = q.get_nowait()
                    except queue.Empty:
                        return
                    fi, pi = pieces[gi]
                    f = out.files[fi]
                    fh = handles.get(fi) or handles.setdefault(fi, open(f["path"], "rb"))
                    fh.seek(pi * PIECE)
                    data = fh.read(PIECE)
                    body = crypto.seal(key, "relay/%s/%d" % (tid, gi), data)
                    for tries in range(5):
                        try:
                            self._req("PUT", url, body, "application/octet-stream", timeout=300, raw=True)
                            break
                        except RelayError:
                            if tries == 4 or out.cancelled.is_set():
                                raise
                            time.sleep(1.5 * (tries + 1))
                    with out.lock:
                        out.sent += len(data)
                    out.rate.add(len(data))
                    self.node._emit_t(out)
            except (RelayError, OSError) as e:
                errors.append(e)
            finally:
                for fh in handles.values():
                    fh.close()

        ths = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        if errors:
            raise RelayError(str(errors[0]))

    def cancel_sent(self, out):
        tid = getattr(out, "relay_tid", None)
        if tid:
            try:
                self._post("done", self.me.signed("done", tid=tid), timeout=10)
            except RelayError:
                pass

    # ---- receiving ----
    def check_inbox(self):
        r = self._post("inbox", self.me.signed("inbox"))
        for it in r.get("items") or []:
            tid = it.get("tid")
            with self.lock:
                if tid in self.seen or tid in self.node.incoming:
                    continue
                self.seen.add(tid)
            try:
                self._take_offer(tid, it["offer"])
            except (RelayError, ValueError, KeyError, TypeError) as e:
                self.node.emit("toast", {"text": "Couldn't open a transfer sent by code: %s" % e, "error": True})

    def _take_offer(self, tid, url):
        env = self._req("GET", url, timeout=30)
        vk = b64d(env["vk"])
        sender = code_of(vk)
        try:
            plain = nacl.public.Box(self.me.box_sk, box_pk(env["vk"])).decrypt(b64d(env["box"]))
        except Exception:
            raise RelayError("it wasn't sealed for this computer")
        offer = json.loads(plain.decode("utf-8"))
        if offer.get("tid") != tid:
            raise RelayError("mixed-up transfer")
        contact = self.contacts().get(sender)
        name = (contact or {}).get("name") or str(offer.get("name") or "Someone")[:40]
        if not contact:
            name = "%s (%s)" % (name, pretty(sender))
        inc = self.node._new_incoming("code:" + sender, tid, {"files": offer["files"]}, kind="remote", pname=name,
                                      trusted=bool(contact))
        inc.relay_key = b64d(offer["key"])
        inc.sender_vk = env["vk"]
        self.node._add_incoming(inc)

    def decided(self, inc):
        """The user accepted or declined a transfer sent by code."""
        if inc.accepted:
            threading.Thread(target=self.download, args=(inc,), daemon=True).start()
        else:
            self._done(inc.tid)

    def _done(self, tid):
        try:
            self._post("done", self.me.signed("done", tid=tid), timeout=20)
        except RelayError:
            pass

    def download(self, inc):
        try:
            pieces = piece_map(inc.files)
            todo = self._missing(inc, pieces)
            inc.status = "receiving"
            inc.error = None
            self.node._emit_t(inc, True)
            for round_ in range(3):
                for part in _runs(todo):
                    if inc.status not in ("receiving",):
                        return
                    a, b = part[0], part[-1] + 1
                    urls = self._post("pull", self.me.signed("pull", tid=inc.tid, start=a, count=b - a))["urls"]
                    self._fetch(inc, pieces, [(gi, urls[gi - a]) for gi in part])
                # Files that failed their hash start over (rare): fetch their pieces again.
                todo = self._missing(inc, pieces)
                if not todo:
                    break
            if any(f["status"] != "done" for f in inc.files):
                raise RelayError("Some files didn't arrive intact")
            self._done(inc.tid)
        except RelayError as e:
            if inc.status != "cancelled":
                inc.status = "failed"
                inc.error = str(e)
                self.node._save_state(inc, force=True)
                with self.lock:
                    self.seen.discard(inc.tid)
        finally:
            self.node._emit_t(inc, True)

    @staticmethod
    def _missing(inc, pieces):
        per = PIECE // CHUNK
        out = []
        for gi, (fi, pi) in enumerate(pieces):
            f = inc.files[fi]
            if f["status"] == "done":
                continue
            want = range(pi * per, min(f["chunks"], (pi + 1) * per))
            if any(ci not in f["have"] for ci in want):
                out.append(gi)
        return out

    def _fetch(self, inc, pieces, jobs):
        q = queue.Queue()
        for j in jobs:
            q.put(j)
        errors = []

        def worker():
            while not errors and inc.status == "receiving":
                try:
                    gi, url = q.get_nowait()
                except queue.Empty:
                    return
                fi, pi = pieces[gi]
                per = PIECE // CHUNK
                for tries in range(5):
                    try:
                        blob = self._req("GET", url, timeout=300, raw=True)
                        data = crypto.open_(inc.relay_key, "relay/%s/%d" % (inc.tid, gi), blob)
                        f = inc.files[fi]
                        for k in range(per):
                            ci = pi * per + k
                            part = data[k * CHUNK:(k + 1) * CHUNK]
                            if not part:
                                break
                            if ci not in f["have"] and f["status"] != "done":
                                self.node._store_chunk(inc, fi, ci, part)
                        break
                    except RelayError as e:
                        if tries == 4:
                            errors.append(e)
                            return
                        time.sleep(1.5 * (tries + 1))
                    except crypto.CryptoError:
                        errors.append(RelayError("A piece was tampered with on the way"))
                        return
                    except Exception as e:
                        errors.append(RelayError(str(e)))
                        return

        ths = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        if errors:
            raise errors[0]


def _runs(idx):
    """Split sorted piece indexes into groups that each span at most BATCH pieces."""
    part = []
    for i in idx:
        if part and i - part[0] >= BATCH:
            yield part
            part = []
        part.append(i)
    if part:
        yield part


def _human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return ("%d %s" % (n, u)) if u == "B" else ("%.0f %s" % (n, u))
        n /= 1024.0
