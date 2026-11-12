"""Small JSON settings store in the per-user app data folder.

Paired devices (and the keys shared with them) live here too, readable only by you.
Set HOP_HOME to use another folder, e.g. to run two copies on one computer.
"""

import json
import os
import platform
import re
import sys
import threading

APP_NAME = "Hop"

DEFAULTS = {
    "device_id": None,           # random, created on first run
    "device_name": None,         # shown to other devices
    "save_dir": None,            # None -> ~/Downloads/Hop
    "auto_accept": False,        # accept files from paired devices without asking
    "open_after": False,         # reveal received files when a transfer finishes
    "auto_check": True,          # check GitHub Releases for updates
    "auto_install": True,        # download in the background, install on quit
    "welcomed": False,
    "staged_version": None,
    "last_checked": None,
    "notified_version": None,
    "peers": {},                 # device_id -> {"name", "key", "paired"}
}

PUBLIC = ("device_name", "save_dir", "auto_accept", "open_after", "auto_check", "auto_install", "welcomed")


def app_data_dir():
    if os.environ.get("HOP_HOME"):
        d = os.environ["HOP_HOME"]
    else:
        if sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        elif os.name == "nt":
            base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        else:
            base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def default_device_name():
    n = platform.node() or "Computer"
    n = re.sub(r"\.(local|lan|home)$", "", n, flags=re.I)
    n = n.replace("-", " ").strip()
    return n[:40] or "Computer"


def default_save_dir():
    return os.path.join(os.path.expanduser("~"), "Downloads", "Hop")


class Settings(object):
    def __init__(self, path=None):
        self.path = path or os.path.join(app_data_dir(), "settings.json")
        self.lock = threading.RLock()
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass
        if not self.data.get("device_id"):
            self.set("device_id", os.urandom(8).hex())
        if not self.data.get("device_name"):
            self.set("device_name", default_device_name())

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
            self._save()

    def _save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError:
            pass

    @property
    def save_dir(self):
        return self.get("save_dir") or default_save_dir()

    # paired devices
    def peer(self, device_id):
        return (self.data.get("peers") or {}).get(device_id)

    def add_peer(self, device_id, name, key_b64, when):
        with self.lock:
            peers = dict(self.data.get("peers") or {})
            peers[device_id] = {"name": name, "key": key_b64, "paired": when}
            self.data["peers"] = peers
            self._save()

    def forget_peer(self, device_id):
        with self.lock:
            peers = dict(self.data.get("peers") or {})
            peers.pop(device_id, None)
            self.data["peers"] = peers
            self._save()

    def public(self):
        d = {k: self.data.get(k) for k in PUBLIC}
        d["save_dir"] = self.save_dir
        return d
