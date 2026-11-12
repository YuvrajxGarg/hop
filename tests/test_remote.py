"""Hop codes and the relay's request format (the server half lives in pxlvzn.in apps/hop/api)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import nacl.signing  # noqa: E402

from hop.crypto import b64d  # noqa: E402
from hop.remote import B32, PIECE, Identity, RelayError, bell_topic, code_of, norm_code, piece_map, pretty  # noqa: E402
from hop.settings import Settings  # noqa: E402


class Codes(unittest.TestCase):
    def test_code_is_stable_and_crockford(self):
        vk = bytes(range(32))
        c = code_of(vk)
        self.assertEqual(c, code_of(vk))
        self.assertEqual(len(c), 12)
        self.assertTrue(all(ch in B32 for ch in c))
        self.assertNotEqual(c, code_of(bytes(32)))

    def test_typing_is_forgiving(self):
        c = code_of(bytes(range(32)))
        self.assertEqual(norm_code(pretty(c).lower()), c)
        self.assertEqual(norm_code(" %s " % pretty(c)), c)
        self.assertEqual(norm_code("0OIL" + "0" * 8), "0011" + "0" * 8)
        for bad in ("", "123", "UUUUUUUUUUUU", "7KQ2-9XMD-4TRA-X"):
            with self.assertRaises(RelayError):
                norm_code(bad)

    def test_identity_signs_what_the_server_checks(self):
        s = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        me = Identity(s)
        self.assertEqual(Identity(s).code, me.code)  # persisted
        body = me.signed("send", to="ABCDEFGHJKMN", tid="a" * 32, size=5, pieces=1)
        msg = "\n".join(["hop/send", str(body["ts"]), "ABCDEFGHJKMN", "a" * 32, "5", "1"]).encode()
        nacl.signing.VerifyKey(b64d(body["vk"])).verify(msg, b64d(body["sig"]))
        self.assertEqual(code_of(b64d(body["vk"])), me.code)
        self.assertTrue(bell_topic(me.code).startswith("hop-"))

    def test_piece_map(self):
        files = [{"size": 0}, {"size": 1}, {"size": PIECE}, {"size": PIECE + 1}]
        self.assertEqual(piece_map(files), [(1, 0), (2, 0), (3, 0), (3, 1)])


if __name__ == "__main__":
    unittest.main()
