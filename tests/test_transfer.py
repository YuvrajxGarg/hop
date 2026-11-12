"""End-to-end tests: two Hop nodes on this computer pair, send, resume and refuse strangers.
    python -m unittest discover tests
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hop import crypto, node as nodemod  # noqa: E402
from hop.node import CHUNK, HopError, Node, safe_relpath, to_ranges, from_ranges  # noqa: E402
from hop.settings import Settings  # noqa: E402


def _wait(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class Pair(object):
    """Two nodes with their own settings and save folders, found by address (no mDNS)."""

    def __init__(self, auto_accept=True):
        self.tmp = tempfile.mkdtemp(prefix="hoptest-")
        self.events = {"a": [], "b": []}
        self.a = self._node("a", auto_accept)
        self.b = self._node("b", auto_accept)
        self.a.add_by_address("127.0.0.1:%d" % self.b.port)
        self.b.add_by_address("127.0.0.1:%d" % self.a.port)

    def _node(self, tag, auto_accept):
        s = Settings(os.path.join(self.tmp, tag + ".json"))
        s.set("device_name", "Node " + tag.upper())
        s.set("save_dir", os.path.join(self.tmp, tag + "-in"))
        s.set("auto_accept", auto_accept)
        n = Node(s, emit=lambda ev, p, t=tag: self.events[t].append((ev, p)), port=0, discover=False)
        n.ip = "127.0.0.1"
        return n.start()

    def code_shown_on_b(self):
        for ev, p in reversed(self.events["b"]):
            if ev == "pair_code":
                return p["code"]

    def pair(self):
        self.a.pair_begin(self.b.id)
        self.a.pair_finish(self.b.id, self.code_shown_on_b())

    def close(self):
        self.a.stop()
        self.b.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def _make(path, size, seed=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(os.urandom(size))


def _same(a, b):
    with open(a, "rb") as x, open(b, "rb") as y:
        return x.read() == y.read()


class CryptoTests(unittest.TestCase):
    def test_seal_binds_slot(self):
        k = crypto.new_key()
        blob = crypto.seal(k, "chunk/x/0/1", b"hello")
        self.assertEqual(crypto.open_(k, "chunk/x/0/1", blob), b"hello")
        with self.assertRaises(crypto.CryptoError):
            crypto.open_(k, "chunk/x/0/2", blob)
        with self.assertRaises(crypto.CryptoError):
            crypto.open_(crypto.new_key(), "chunk/x/0/1", blob)
        bad = bytearray(blob)
        bad[-1] ^= 1
        with self.assertRaises(crypto.CryptoError):
            crypto.open_(k, "chunk/x/0/1", bytes(bad))

    def test_pake_wrong_code(self):
        r = crypto.PairResponder("123456", "aa" * 8, "bb" * 8)
        msg_a, ca, key, cb = crypto.pair_initiate("123 457", "aa" * 8, "bb" * 8, r.msg_b)
        with self.assertRaises(crypto.CryptoError):
            r.finish(msg_a, ca)

    def test_pake_right_code(self):
        r = crypto.PairResponder("123456", "aa" * 8, "bb" * 8)
        msg_a, ca, key, cb = crypto.pair_initiate("123-456", "aa" * 8, "bb" * 8, r.msg_b)
        k2, cb2 = r.finish(msg_a, ca)
        self.assertEqual(key, k2)
        self.assertEqual(cb, cb2)


class HelperTests(unittest.TestCase):
    def test_safe_relpath(self):
        self.assertEqual(safe_relpath("../../etc/passwd"), os.path.join("etc", "passwd"))
        self.assertEqual(safe_relpath("/abs/x.jpg"), os.path.join("abs", "x.jpg"))
        self.assertEqual(safe_relpath("C:\\Windows\\a.txt"), os.path.join("C_", "Windows", "a.txt"))
        self.assertEqual(safe_relpath(".."), "file")
        self.assertEqual(safe_relpath("con.txt"), "_con.txt")

    def test_ranges(self):
        self.assertEqual(to_ranges([0, 1, 2, 5, 6, 9]), [[0, 3], [5, 7], [9, 10]])
        self.assertEqual(from_ranges([[0, 3], [5, 7]], 6), {0, 1, 2, 5})


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.p = Pair()
        self.src = os.path.join(self.p.tmp, "src")

    def tearDown(self):
        self.p.close()

    def test_unpaired_is_refused(self):
        f = os.path.join(self.src, "a.bin")
        _make(f, 1000)
        with self.assertRaises(HopError):
            self.p.a.send(self.p.b.id, [f])

    def test_wrong_code_then_right_code(self):
        self.p.a.pair_begin(self.p.b.id)
        real = crypto.normalize_code(self.p.code_shown_on_b())
        wrong = "%06d" % ((int(real) + 1) % 1000000)
        with self.assertRaises(HopError):
            self.p.a.pair_finish(self.p.b.id, wrong)
        self.assertIsNone(self.p.b.settings.peer(self.p.a.id))
        # The code is burnt after one guess: the right code no longer works on that session.
        self.p.a.pair_pending[self.p.b.id] = None
        self.p.pair()
        self.assertTrue(self.p.a.settings.peer(self.p.b.id))
        self.assertEqual(self.p.a.settings.peer(self.p.b.id)["key"], self.p.b.settings.peer(self.p.a.id)["key"])

    def test_send_files_and_folder(self):
        self.p.pair()
        big = os.path.join(self.src, "clip.mov")
        _make(big, CHUNK * 3 + 12345)
        folder = os.path.join(self.src, "Shoot")
        _make(os.path.join(folder, "a.jpg"), 5000, 2)
        _make(os.path.join(folder, "raw", "b.dng"), CHUNK + 1, 3)
        open(os.path.join(folder, "empty.txt"), "wb").close()
        out = self.p.a.send(self.p.b.id, [big, folder], wait=True)
        self.assertEqual(out.status, "done", out.error)
        inbox = self.p.b.settings.save_dir
        self.assertTrue(_same(big, os.path.join(inbox, "clip.mov")))
        self.assertTrue(_same(os.path.join(folder, "raw", "b.dng"), os.path.join(inbox, "Shoot", "raw", "b.dng")))
        self.assertTrue(os.path.isfile(os.path.join(inbox, "Shoot", "empty.txt")))
        self.assertFalse(os.path.exists(os.path.join(inbox, ".hop-partial")))
        # The other direction works with the same pairing.
        back = os.path.join(self.src, "back.txt")
        _make(back, 10)
        o2 = self.p.b.send(self.p.a.id, [back], wait=True)
        self.assertEqual(o2.status, "done", o2.error)
        # Same name again doesn't overwrite.
        o3 = self.p.a.send(self.p.b.id, [big], wait=True)
        self.assertEqual(o3.status, "done", o3.error)
        self.assertTrue(os.path.isfile(os.path.join(inbox, "clip (1).mov")))

    def test_resume_after_interruption(self):
        self.p.pair()
        f = os.path.join(self.src, "long.bin")
        _make(f, CHUNK * 12 + 7)
        # Make the receiver drop the connection after a few chunks.
        real = self.p.b._chunk_req
        count = [0]

        def flaky(*a):
            count[0] += 1
            if count[0] > 5:
                raise OSError("simulated drop")
            return real(*a)
        self.p.b._chunk_req = flaky
        nodemod_workers = nodemod.WORKERS
        nodemod.WORKERS = 1
        try:
            out = self.p.a.send(self.p.b.id, [f])
            self.assertTrue(_wait(lambda: out.status == "waiting", 30), out.status)
            self.p.a.cancel(out.tid)
            _wait(lambda: out.status == "cancelled")
            self.assertTrue(_wait(lambda: self.p.b.incoming[out.tid].status == "cancelled"))
        finally:
            nodemod.WORKERS = nodemod_workers
        part_state = os.path.join(self.p.b.settings.save_dir, ".hop-partial", out.tid, "state.json")
        self.assertTrue(os.path.isfile(part_state))
        # Restart the receiver: resume info must come from disk.
        self.p.b._chunk_req = real
        self.p.b.incoming.clear()
        sent = []
        orig_push = self.p.a._push_chunks

        def spy(o, need):
            sent.append(sum(b - a for rs in need.values() for a, b in rs))
            return orig_push(o, need)
        self.p.a._push_chunks = spy
        out2 = self.p.a.send(self.p.b.id, [f], wait=True)
        self.assertEqual(out2.tid, out.tid)
        self.assertEqual(out2.status, "done", out2.error)
        self.assertLess(sent[0], 13)  # only the missing chunks went over
        self.assertTrue(_same(f, os.path.join(self.p.b.settings.save_dir, "long.bin")))

    def test_ask_and_decline(self):
        self.p.pair()
        self.p.b.settings.set("auto_accept", False)
        f = os.path.join(self.src, "x.txt")
        _make(f, 100)
        out = self.p.a.send(self.p.b.id, [f])
        self.assertTrue(_wait(lambda: any(ev == "offer" for ev, _ in self.p.events["b"])))
        tid = [p for ev, p in self.p.events["b"] if ev == "offer"][0]["tid"]
        self.p.b.decide(tid, False)
        self.assertTrue(_wait(lambda: out.status == "declined"), out.status)

    def test_ask_and_accept(self):
        self.p.pair()
        self.p.b.settings.set("auto_accept", False)
        f = os.path.join(self.src, "y.txt")
        _make(f, 100)
        out = self.p.a.send(self.p.b.id, [f])
        self.assertTrue(_wait(lambda: any(ev == "offer" for ev, _ in self.p.events["b"])))
        tid = [p for ev, p in self.p.events["b"] if ev == "offer"][0]["tid"]
        self.p.b.decide(tid, True)
        self.assertTrue(_wait(lambda: out.status == "done"), out.status)

    def test_forged_chunk_rejected(self):
        self.p.pair()
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.p.b.port)
        blob = crypto.seal(crypto.new_key(), "chunk/%s/0/0" % ("ab" * 16), b"x")
        conn.request("PUT", "/hop/chunk/%s/0/0" % ("ab" * 16), body=blob, headers={"X-Hop-From": self.p.a.id})
        self.assertEqual(conn.getresponse().status, 401)


class PhoneTests(unittest.TestCase):
    """The phone page's protocol, driven from Python with the QR code's key."""

    def setUp(self):
        self.p = Pair()

    def tearDown(self):
        self.p.close()

    def _req(self, method, path, body=None, peer=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.p.a.port)
        try:
            c.request(method, path, body=body, headers={"X-Hop-From": peer or ""})
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def test_phone_page_served(self):
        st, body = self._req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"phone.js", body)
        st, _ = self._req("GET", "/nacl-fast.min.js")
        self.assertEqual(st, 200)

    def test_phone_upload_and_download(self):
        url = self.p.a.web_url()
        sid, key = url.split("#")[1].split(".")
        key = crypto.b64d(key)
        peer = "web:%s:abc" % sid
        data = os.urandom(CHUNK + 99)
        tid = "cd" * 16
        man = {"tid": tid, "from_name": "iPhone", "files": [{"name": "IMG_1.HEIC", "size": len(data), "sha256": None}]}
        st, res = self._req("POST", "/hop/offer", crypto.seal(key, "req:/hop/offer", json.dumps(man).encode()), peer)
        self.assertEqual(st, 200)
        res = json.loads(crypto.open_(key, "res:/hop/offer", res))
        self.assertEqual(res["state"], "accepted")
        for ci in range(2):
            part = data[ci * CHUNK:(ci + 1) * CHUNK]
            st, _ = self._req("PUT", "/hop/chunk/%s/0/%d" % (tid, ci), crypto.seal(key, "chunk/%s/0/%d" % (tid, ci), part), peer)
            self.assertEqual(st, 204)
        got = os.path.join(self.p.a.settings.save_dir, "IMG_1.HEIC")
        self.assertTrue(_wait(lambda: os.path.isfile(got)))
        with open(got, "rb") as f:
            self.assertEqual(f.read(), data)
        # Desktop -> phone
        src = os.path.join(self.p.tmp, "for-phone.mp4")
        _make(src, CHUNK + 5)
        out = self.p.a.send("phone", [src])
        st, res = self._req("POST", "/hop/inbox", crypto.seal(key, "req:/hop/inbox", b"{}"), peer)
        items = json.loads(crypto.open_(key, "res:/hop/inbox", res))["items"]
        self.assertEqual(items[0]["files"][0]["name"], "for-phone.mp4")
        buf = b""
        for ci in range(2):
            st, blob = self._req("GET", "/hop/pull/%s/0/%d" % (out.tid, ci), None, peer)
            buf += crypto.open_(key, "pull/%s/0/%d" % (out.tid, ci), blob)
        with open(src, "rb") as f:
            self.assertEqual(f.read(), buf)
        self.assertEqual(out.status, "done")
        # A stale link is refused.
        self.p.a.new_web_link()
        st, _ = self._req("POST", "/hop/inbox", crypto.seal(key, "req:/hop/inbox", b"{}"), peer)
        self.assertEqual(st, 401)


if __name__ == "__main__":
    unittest.main()
