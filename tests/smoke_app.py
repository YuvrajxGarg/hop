"""Smoke-test a built app: its bundled crypto works, and it sends a file to a Hop node.
    python tests/smoke_app.py <path to Hop executable>
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hop import crypto  # noqa: E402
from hop.node import Node  # noqa: E402
from hop.settings import Settings  # noqa: E402

exe = sys.argv[1]
d = tempfile.mkdtemp()
r = subprocess.run([exe, "selftest"], timeout=120)
assert r.returncode == 0, "selftest failed (exit %s)" % r.returncode

# A receiving node from source, already paired with the app (shared key written to both).
key = crypto.b64e(crypto.new_key())
app_home = os.path.join(d, "app")
os.makedirs(app_home)
app_id = "a1" * 8
rs = Settings(os.path.join(d, "recv.json"))
rs.set("save_dir", os.path.join(d, "inbox"))
rs.set("auto_accept", True)
rs.add_peer(app_id, "Built app", key, 0)
recv = Node(rs, port=0, discover=False).start()
with open(os.path.join(app_home, "settings.json"), "w") as f:
    json.dump({"device_id": app_id, "device_name": "Built app",
               "peers": {rs.get("device_id"): {"name": "Receiver", "key": key, "paired": 0}}}, f)

src = os.path.join(d, "clip.bin")
with open(src, "wb") as f:
    f.write(os.urandom(3 * 1024 * 1024 + 17))
env = dict(os.environ, HOP_HOME=app_home)
r = subprocess.run([exe, "send", src, "--to", "127.0.0.1:%d" % recv.port], timeout=180, env=env, stdin=subprocess.DEVNULL)
got = os.path.join(d, "inbox", "clip.bin")
for _ in range(50):
    if os.path.isfile(got):
        break
    time.sleep(0.1)
assert os.path.isfile(got), "app sent nothing (exit %s)" % r.returncode
with open(src, "rb") as a, open(got, "rb") as b:
    assert a.read() == b.read(), "file differs"
recv.stop()
print("smoke test passed: %s sent the file (exit %s)" % (exe, r.returncode))
