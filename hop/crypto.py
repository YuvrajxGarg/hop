"""Hop's cryptography, kept small and boring.

Pairing   SPAKE2 over a 6-digit code. Someone sniffing the Wi-Fi sees only the two
          SPAKE2 messages, which don't let them test guesses offline; each code allows
          a single online guess. Both sides then prove they hold the same key.
Transfer  XSalsa20-Poly1305 (NaCl secretbox) with a random 24-byte nonce per message.
          Every message carries "associated data" (which transfer, file and chunk it is)
          inside the authenticated plaintext, so chunks can't be swapped or replayed into
          a different slot. The phone page uses tweetnacl, which speaks the same format.
"""

import base64
import hashlib
import hmac
import os
import secrets
import struct

import nacl.exceptions
import nacl.secret
import nacl.utils
from spake2 import SPAKE2_A, SPAKE2_B

KEY_BYTES = nacl.secret.SecretBox.KEY_SIZE  # 32
OVERHEAD = nacl.secret.SecretBox.NONCE_SIZE + nacl.secret.SecretBox.MACBYTES  # 40


class CryptoError(Exception):
    pass


def b64e(b):
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def b64d(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def new_key():
    return nacl.utils.random(KEY_BYTES)


def new_code():
    """Six digits, shown as 123 456."""
    return "%06d" % secrets.randbelow(1000000)


def normalize_code(code):
    return "".join(ch for ch in str(code) if ch.isdigit())


def seal(key, ad, data):
    """Encrypt `data` bound to the label `ad` (str). Output: nonce(24) || box."""
    if isinstance(ad, str):
        ad = ad.encode("utf-8")
    plain = struct.pack(">H", len(ad)) + ad + data
    return bytes(nacl.secret.SecretBox(key).encrypt(plain))


def open_(key, ad, blob):
    if isinstance(ad, str):
        ad = ad.encode("utf-8")
    try:
        plain = nacl.secret.SecretBox(key).decrypt(bytes(blob))
    except (nacl.exceptions.CryptoError, ValueError, TypeError):
        raise CryptoError("message failed authentication")
    if len(plain) < 2:
        raise CryptoError("short message")
    (n,) = struct.unpack(">H", plain[:2])
    if plain[2:2 + n] != ad:
        raise CryptoError("message is for a different slot")
    return plain[2 + n:]


# ---- pairing ---------------------------------------------------------------

def _derive(raw):
    """Split the SPAKE2 output into a transfer key and two confirmation tags."""
    key = hashlib.blake2b(raw, digest_size=32, person=b"hop-key").digest()
    ca = hashlib.blake2b(raw, digest_size=32, person=b"hop-confirm-A").digest()
    cb = hashlib.blake2b(raw, digest_size=32, person=b"hop-confirm-B").digest()
    return key, ca, cb


class PairResponder(object):
    """The receiving side: shows the code, answers one guess."""

    def __init__(self, code, sender_id, receiver_id):
        self.code = code
        self.s = SPAKE2_B(code.encode(), idA=sender_id.encode(), idB=receiver_id.encode())
        self.msg_b = self.s.start()

    def finish(self, msg_a, confirm_a):
        try:
            raw = self.s.finish(msg_a)
        except Exception:
            raise CryptoError("bad pairing message")
        key, ca, cb = _derive(raw)
        if not hmac.compare_digest(ca, confirm_a):
            raise CryptoError("wrong code")
        return key, cb


def pair_initiate(code, sender_id, receiver_id, msg_b):
    """The sending side: the user typed `code`. Returns (msg_a, confirm_a, key, expected_confirm_b)."""
    s = SPAKE2_A(normalize_code(code).encode(), idA=sender_id.encode(), idB=receiver_id.encode())
    msg_a = s.start()
    try:
        raw = s.finish(msg_b)
    except Exception:
        raise CryptoError("bad pairing message")
    key, ca, cb = _derive(raw)
    return msg_a, ca, key, cb


def file_sha256(path, progress=None, chunk=4 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
            if progress:
                progress(len(b))
    return h.hexdigest()


def random_id(n=8):
    return os.urandom(n).hex()
