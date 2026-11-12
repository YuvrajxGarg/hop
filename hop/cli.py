"""Command-line interface.

    hop send <files/folders...> [--to DEVICE]   send to a computer on your network
    hop receive [-o FOLDER] [--yes]             wait for files (prints pairing codes)
    hop phone [files...]                        QR code for a phone; optional files to offer it
    hop devices                                 list Hop devices nearby
    hop forget DEVICE                           unpair a device
"""

import argparse
import os
import queue
import sys
import threading
import time

from . import __version__, crypto
from .node import HopError, Node, human
from .settings import Settings


def _bar(done, total, speed, width=28):
    frac = (done / float(total)) if total else 1.0
    fill = int(frac * width)
    return "[%s%s] %3d%%  %s / %s  %s/s" % ("#" * fill, "." * (width - fill), frac * 100, human(done), human(total), human(speed))


def _print_qr(url):
    try:
        import segno
        segno.make(url, error="m").terminal(compact=True)
    except Exception:
        pass
    print("  " + url)


class Console(object):
    def __init__(self, yes=False):
        self.events = queue.Queue()
        self.yes = yes

    def emit(self, ev, payload):
        self.events.put((ev, payload))


def _start(args, console, discover=True):
    s = Settings()
    if getattr(args, "name", None):
        s.set("device_name", args.name)
    if getattr(args, "output", None):
        s.set("save_dir", os.path.abspath(os.path.expanduser(args.output)))
    node = Node(s, emit=console.emit, port=getattr(args, "port", None), discover=discover)
    try:
        node.start()
    except HopError as e:
        print("! " + str(e))
        sys.exit(1)
    return node


def _pick(node, ref):
    if ref:
        p = None
        for _ in range(40):  # give Bonjour a few seconds to find it
            try:
                p = node.find_peer(ref)
            except HopError as e:
                print("! " + str(e))
                return None
            if p and p.get("online"):
                break
            time.sleep(0.1)
        if not p:
            print("! No device called %r is on this network." % ref)
        return p
    print("Looking for devices…")
    for _ in range(30):
        online = [p for p in node.peers() if p["online"]]
        if online:
            time.sleep(0.6)
            break
        time.sleep(0.1)
    online = [p for p in node.peers() if p["online"]]
    if not online:
        print("! No Hop devices found. Open Hop on the other computer, or use --to <ip address>.")
        return None
    if len(online) == 1:
        return online[0]
    for i, p in enumerate(online, 1):
        print("  %d. %s%s" % (i, p["name"], "  (paired)" if p["paired"] else ""))
    while True:
        a = input("Send to which one? ").strip()
        if a.isdigit() and 1 <= int(a) <= len(online):
            return online[int(a) - 1]


def cmd_send(args):
    c = Console()
    node = _start(args, c)
    try:
        peer = _pick(node, args.to)
        if not peer:
            return 1
        if not node.settings.peer(peer["id"]):
            print("Pairing with %s. A 6-digit code is showing on it now." % peer["name"])
            node.pair_begin(peer["id"])
            code = input("Code: ")
            try:
                node.pair_finish(peer["id"], code)
            except (HopError, crypto.CryptoError) as e:
                print("! " + str(e))
                return 1
            print("Paired. Next time there's no code.")
        out = node.send(peer["id"], args.paths)
        last = None
        while True:
            try:
                c.events.get(timeout=0.3)
            except queue.Empty:
                pass
            st = out.status
            if st in ("asking",) and last != st:
                print("Waiting for %s to accept…" % peer["name"])
            elif st == "waiting" and last != st:
                print("  " + (out.error or "Reconnecting…"))
            elif st in ("sending", "preparing"):
                label = "hashing " if st == "preparing" else ""
                sys.stdout.write("\r  " + label + _bar(out.sent, out.total, out.rate.value) + "   ")
                sys.stdout.flush()
            last = st
            if st in ("done", "failed", "declined", "cancelled"):
                break
        sys.stdout.write("\n")
        if out.status == "done":
            print("Sent %d file%s (%s) to %s." % (len(out.files), "" if len(out.files) == 1 else "s", human(out.total), peer["name"]))
            return 0
        print("! " + (out.error or out.status))
        return 1
    except HopError as e:
        print("\n! " + str(e))
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to resume.")
        return 130
    finally:
        node.stop()


def _receive_loop(node, c, stop_when_idle=False):
    shown = {}
    while True:
        ev, p = c.events.get()
        if ev == "pair_code":
            print("\n%s wants to pair. Type this code on it:  %s\n" % (p["name"], p["code"]))
        elif ev == "paired":
            print("Paired with %s." % p["name"])
        elif ev == "pair_failed":
            print("! %s typed the wrong code." % p["name"])
        elif ev == "offer":
            names = ", ".join(p["names"]) + (" and %d more" % (p["count"] - 3) if p["count"] > 3 else "")
            if c.yes:
                node.decide(p["tid"], True)
                continue
            threading.Thread(target=_ask, args=(node, p, names), daemon=True).start()
        elif ev == "transfer" and p["dir"] == "in":
            if p["status"] == "receiving":
                sys.stdout.write("\r  %s  %s   " % (p["peer_name"], _bar(p["done"], p["total"], p["speed"])))
                sys.stdout.flush()
            elif p["status"] == "done" and shown.get(p["tid"]) != "done":
                print("\nReceived %d file%s from %s -> %s" % (p["count"], "" if p["count"] == 1 else "s", p["peer_name"], p["folder"]))
            elif p["status"] in ("cancelled", "failed") and shown.get(p["tid"]) != p["status"]:
                print("\n! %s" % (p["error"] or p["status"]))
            shown[p["tid"]] = p["status"]


def _ask(node, p, names):
    a = input("\n%s wants to send %s (%s). Accept? [y/N] " % (p["peer_name"], names, human(p["total"])))
    node.decide(p["tid"], a.strip().lower() in ("y", "yes"))


def cmd_receive(args):
    c = Console(yes=args.yes)
    node = _start(args, c)
    print("Hop is ready on %s as \"%s\". Files go to %s" % (node.ip, node.name, node.settings.save_dir))
    if node.discovery and node.discovery.error:
        print("  (Bonjour unavailable: %s. Others can still use  hop send --to %s:%d)" % (node.discovery.error, node.ip, node.port))
    print("Press Ctrl+C to stop.")
    try:
        _receive_loop(node, c)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        node.stop()


def cmd_phone(args):
    c = Console(yes=True)
    node = _start(args, c)
    if args.paths:
        node.send("phone", args.paths)
    print("Scan this with your phone's camera (same Wi-Fi):\n")
    _print_qr(node.web_url())
    print("\nFiles from the phone go to %s. Press Ctrl+C to stop." % node.settings.save_dir)
    try:
        _receive_loop(node, c)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        node.stop()


def cmd_devices(args):
    c = Console()
    node = _start(args, c)
    try:
        time.sleep(args.wait)
        ps = node.peers()
        if not ps:
            print("No Hop devices found.")
        for p in ps:
            state = "online" if p["online"] else "offline"
            where = "%s:%s" % (p["host"], p["port"]) if p.get("host") else ""
            print("%-28s %-8s %-7s %s %s" % (p["name"], state, "paired" if p["paired"] else "", where, p["id"]))
        return 0
    finally:
        node.stop()


def cmd_forget(args):
    s = Settings()
    for did, info in (s.get("peers") or {}).items():
        if did == args.device or (info.get("name") or "").lower() == args.device.lower():
            s.forget_peer(did)
            print("Forgot %s." % info.get("name"))
            return 0
    print("! No paired device called %r." % args.device)
    return 1


def cmd_selftest(args):
    """Checks the bundled crypto, pairing and QR libraries (used by the release smoke test)."""
    r = crypto.PairResponder("123456", "aa" * 8, "bb" * 8)
    msg_a, ca, key, cb = crypto.pair_initiate("123456", "aa" * 8, "bb" * 8, r.msg_b)
    k2, cb2 = r.finish(msg_a, ca)
    assert key == k2 and cb == cb2
    assert crypto.open_(key, "x", crypto.seal(key, "x", b"hop")) == b"hop"
    import segno
    assert segno.make("http://192.168.1.2:47470/#a.b").symbol_size()
    from . import discovery
    assert discovery.Zeroconf is not None
    print("selftest ok")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hop", description="Send files between your computers and phones over Wi-Fi. No cloud.")
    p.add_argument("--version", action="version", version="Hop " + __version__)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("send", help="send files or folders to another computer")
    s.add_argument("paths", nargs="+")
    s.add_argument("--to", help="device name, id, or ip[:port]")
    s.add_argument("--port", type=int, default=0)

    r = sub.add_parser("receive", help="wait for files")
    r.add_argument("-o", "--output", help="folder for received files (remembered)")
    r.add_argument("-y", "--yes", action="store_true", help="accept from paired devices without asking")
    r.add_argument("--name", help="name other devices see (remembered)")
    r.add_argument("--port", type=int)

    ph = sub.add_parser("phone", help="show a QR code so a phone can send and receive in its browser")
    ph.add_argument("paths", nargs="*", help="files to offer the phone")
    ph.add_argument("-o", "--output", help="folder for files from the phone (remembered)")
    ph.add_argument("--port", type=int)

    d = sub.add_parser("devices", help="list Hop devices on this network")
    d.add_argument("--wait", type=float, default=2.5)
    d.add_argument("--port", type=int, default=0)

    f = sub.add_parser("forget", help="unpair a device")
    f.add_argument("device")

    sub.add_parser("selftest", help=argparse.SUPPRESS)

    args = p.parse_args(argv)
    fn = {"selftest": cmd_selftest, "send": cmd_send, "receive": cmd_receive, "phone": cmd_phone, "devices": cmd_devices, "forget": cmd_forget}.get(args.cmd)
    if not fn:
        p.print_help()
        return 1
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
