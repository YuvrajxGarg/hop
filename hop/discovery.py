"""Finds other Hop devices on the local network with Bonjour / mDNS (zeroconf).

Each running Hop advertises `_hop._tcp.local.` with its device id and name in the TXT
record. No server, no internet: the announcements stay on your Wi-Fi.
"""

import socket
import threading
import time

SERVICE = "_hop._tcp.local."

try:
    from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf
except Exception:  # pragma: no cover - zeroconf missing: Hop still works by address and QR
    Zeroconf = None


def _txt(props, key):
    v = props.get(key.encode()) if props else None
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else (v or "")


class Discovery(object):
    def __init__(self, device_id, name, port, version, platform, on_change):
        self.device_id = device_id
        self.name = name
        self.port = port
        self.version = version
        self.platform = platform
        self.on_change = on_change
        self.found = {}  # device_id -> {"name", "host", "port", "platform", "seen"}
        self.lock = threading.Lock()
        self.zc = None
        self.info = None
        self.browser = None
        self.error = None

    @property
    def available(self):
        return Zeroconf is not None and self.zc is not None

    def start(self, addresses):
        if Zeroconf is None:
            self.error = "zeroconf isn't installed"
            return
        try:
            self.zc = Zeroconf(ip_version=IPVersion.V4Only)
            label = ("%s-%s" % (self.name, self.device_id[:6]))[:60]
            self.info = ServiceInfo(
                SERVICE, "%s.%s" % (label, SERVICE),
                addresses=[socket.inet_aton(a) for a in addresses if a],
                port=self.port,
                properties={"id": self.device_id, "name": self.name, "ver": self.version, "plat": self.platform, "proto": "1"},
                server="hop-%s.local." % self.device_id[:12],
            )
            self.zc.register_service(self.info, allow_name_change=True)
            self.browser = ServiceBrowser(self.zc, SERVICE, handlers=[self._handler])
        except Exception as e:
            self.error = str(e)
            self.stop()

    def rename(self, name):
        self.name = name
        if self.zc and self.info:
            try:
                self.info.properties = {"id": self.device_id, "name": name, "ver": self.version, "plat": self.platform, "proto": "1"}
                self.zc.update_service(self.info)
            except Exception:
                pass

    def _handler(self, zeroconf, service_type, name, state_change):
        threading.Thread(target=self._resolve, args=(zeroconf, service_type, name, state_change), daemon=True).start()

    def _resolve(self, zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Removed:
            with self.lock:
                gone = [k for k, v in self.found.items() if v.get("svc") == name]
                for k in gone:
                    self.found.pop(k, None)
            if gone:
                self.on_change()
            return
        info = zeroconf.get_service_info(service_type, name, timeout=3000)
        if not info:
            return
        did = _txt(info.properties, "id")
        if not did or did == self.device_id:
            return
        addrs = info.parsed_addresses(IPVersion.V4Only)
        if not addrs:
            return
        with self.lock:
            self.found[did] = {"id": did, "name": _txt(info.properties, "name") or name.split(".")[0],
                               "host": addrs[0], "port": info.port, "platform": _txt(info.properties, "plat"),
                               "version": _txt(info.properties, "ver"), "seen": time.time(), "svc": name}
        self.on_change()

    def peers(self):
        with self.lock:
            return [dict(v) for v in self.found.values()]

    def stop(self):
        try:
            if self.zc:
                if self.info:
                    self.zc.unregister_service(self.info)
                self.zc.close()
        except Exception:
            pass
        self.zc = None
