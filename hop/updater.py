"""Keeps Hop up to date from GitHub Releases (same model as Dwn).

- Every install first asks GitHub for the newest release, so it always jumps
  straight to the latest version and never installs one in between.
- With automatic updates on, the newest version downloads quietly in the
  background (verified with SHA-256 and unpacked) and is installed the next time
  the app quits, or right away with "Restart to update".

Release assets:
  macOS   Hop-mac.zip      + .sha256   (contains "Hop.app")
  Windows Hop-windows.zip  + .sha256   (contains the "Hop" folder)
"""

import datetime
import hashlib
import json
import os
import plistlib
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

from . import UPDATE_REPO, __version__
from .settings import app_data_dir

CHECK_EVERY = 3 * 3600
BUNDLE_ID = "com.yuvrajgarg.hop"
APP_NAME = "Hop"


def log(msg):
    """Append a line to update.log in the app data folder (for diagnosing update problems)."""
    try:
        path = os.path.join(app_data_dir(), "update.log")
        if os.path.exists(path) and os.path.getsize(path) > 256 * 1024:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s  %s  %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), __version__, msg))
    except Exception:
        pass


def asset_name():
    return "Hop-mac.zip" if sys.platform == "darwin" else "Hop-windows.zip"


def is_newer(a, b):
    def parts(v):
        out, cur = [], ""
        for ch in str(v):
            if ch.isdigit():
                cur += ch
            elif cur:
                out.append(int(cur))
                cur = ""
        if cur:
            out.append(int(cur))
        return out

    pa, pb = parts(a), parts(b)
    for i in range(max(len(pa), len(pb))):
        x = pa[i] if i < len(pa) else 0
        y = pb[i] if i < len(pb) else 0
        if x != y:
            return x > y
    return False


def _ssl_ctx():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _request(url, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={
        "Accept": accept, "User-Agent": "Hop/" + __version__, "Cache-Control": "no-cache"})
    return urllib.request.urlopen(req, timeout=30, context=_ssl_ctx())


class UpdateError(Exception):
    pass


def installed_location():
    """Where the running app lives, or None when running from source."""
    if not getattr(sys, "frozen", False):
        return None
    exe = os.path.abspath(sys.executable)
    if sys.platform == "darwin":
        app = os.path.dirname(os.path.dirname(os.path.dirname(exe)))  # .../X.app/Contents/MacOS/X
        return app if app.endswith(".app") else None
    if os.name == "nt":
        return os.path.dirname(exe)
    return None


def sync_installed_version():
    """Installed with the Windows setup: keep the version shown in Settings > Apps current.

    The setup registers the app under HKCU ...\\Uninstall\\PXLVZN.<App>_is1. Updates swap
    the app folder without running the setup again, so the app updates the entry itself.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PXLVZN.%s_is1" % APP_NAME
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            where = winreg.QueryValueEx(k, "InstallLocation")[0]
            here = installed_location()
            if not here or os.path.normcase(os.path.abspath(where.rstrip("\\"))) != os.path.normcase(here):
                return  # a different copy (say, an unzipped one) is running
            if winreg.QueryValueEx(k, "DisplayVersion")[0] != __version__:
                winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, __version__)
                log("set the installed version to %s" % __version__)
    except OSError:
        pass  # not installed with the setup
    except Exception as e:
        log("could not update the installed version: %s" % e)


class Updater(object):
    def __init__(self, settings, emit):
        self.settings = settings
        self.emit = emit  # emit(state_dict)
        self.phase = "idle"  # idle checking uptodate available downloading ready installing failed
        self.progress = 0.0
        self.error = None
        self.latest = None
        self.lock = threading.Lock()
        self.staging = False
        self.on_quit_for_install = None  # set by the UI: closes the window
        self.stage_dir = os.path.join(app_data_dir(), "Update")
        sync_installed_version()
        staged = settings.get("staged_version")
        if staged and not is_newer(staged, __version__):
            self._discard_staged()

    # ---- state -------------------------------------------------------------
    @property
    def staged_path(self):
        return os.path.join(self.stage_dir, APP_NAME + ".app" if sys.platform == "darwin" else APP_NAME)

    def state(self):
        rel = self.latest or {}
        return {
            "phase": self.phase, "progress": self.progress, "error": self.error,
            "current": __version__, "latest": rel.get("version"), "notes": rel.get("notes", ""),
            "page": rel.get("page") or "https://github.com/%s/releases" % UPDATE_REPO,
            "published": rel.get("published"), "last_checked": self.settings.get("last_checked"),
            "can_install": installed_location() is not None, "repo": UPDATE_REPO,
        }

    def _set(self, phase, **kw):
        if phase != self.phase or kw.get("error"):
            log("phase %s -> %s%s" % (self.phase, phase, (" (%s)" % kw["error"]) if kw.get("error") else ""))
        self.phase = phase
        self.progress = kw.get("progress", self.progress if phase == "downloading" else 0.0)
        self.error = kw.get("error")
        self.emit(self.state())

    @property
    def has_update(self):
        return self.phase in ("available", "downloading", "ready", "installing")

    # ---- scheduling --------------------------------------------------------
    def start(self):
        if getattr(self, "_started", False):
            return
        self._started = True

        def loop():
            time.sleep(6)
            while True:
                if self.settings.get("auto_check"):
                    self.check(manual=False)
                time.sleep(CHECK_EVERY)

        threading.Thread(target=loop, daemon=True).start()

    # ---- check -------------------------------------------------------------
    def fetch_latest(self):
        try:
            with _request("%s/repos/%s/releases/latest" % (os.environ.get("HOP_UPDATE_API", "https://api.github.com"), UPDATE_REPO)) as r:
                obj = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UpdateError("No releases have been published yet.")
            raise UpdateError("Couldn't reach the update server (HTTP %d)." % e.code)
        except Exception:
            raise UpdateError("Couldn't reach the update server. Check your connection.")
        assets = {a.get("name"): a.get("browser_download_url") for a in obj.get("assets", [])}
        name = asset_name()
        log("latest release: %s" % obj.get("tag_name"))
        rel = {
            "version": (obj.get("tag_name") or "").lstrip("vV"),
            "notes": obj.get("body") or "",
            "page": obj.get("html_url") or "https://github.com/%s/releases" % UPDATE_REPO,
            "zip": assets.get(name), "sha": assets.get(name + ".sha256"),
            "published": obj.get("published_at"),
        }
        self.latest = rel
        self.settings.set("last_checked", datetime.datetime.now().isoformat(timespec="seconds"))
        return rel

    def check(self, manual=False):
        if self.phase in ("downloading", "installing"):
            return self.state()
        was_ready = self.phase == "ready"
        if not was_ready:
            self._set("checking")
        try:
            rel = self.fetch_latest()
            if not is_newer(rel["version"], __version__) or not (rel["zip"] and rel["sha"]):
                # A release without this platform's build isn't ready for us yet; never offer it.
                self._set("uptodate")
            elif self.settings.get("staged_version") == rel["version"] and os.path.exists(self.staged_path):
                self._set("ready")
            else:
                self._set("available")
                if self.settings.get("auto_install") and installed_location():
                    threading.Thread(target=self.stage_newest, daemon=True).start()
        except UpdateError as e:
            if was_ready:
                self._set("ready")
            else:
                self._set("failed" if manual else "idle", error=str(e) if manual else None)
        return self.state()

    # ---- staging -----------------------------------------------------------
    def stage_newest(self):
        """Download, verify and unpack the newest release into the staging folder."""
        with self.lock:
            if self.staging:
                return False
            self.staging = True
        work = None
        try:
            rel = self.fetch_latest()
            if not is_newer(rel["version"], __version__) or not (rel["zip"] and rel["sha"]):
                self._set("uptodate")
                return False
            if self.settings.get("staged_version") == rel["version"] and os.path.exists(self.staged_path):
                self._set("ready")
                return True
            if not rel["zip"]:
                self._set("available")
                return False

            self._set("downloading", progress=0.0)
            work = tempfile.mkdtemp(prefix="HopUpdate-")
            zpath = os.path.join(work, asset_name())
            self._download(rel["zip"], zpath)

            if rel["sha"]:
                with _request(rel["sha"], accept="application/octet-stream") as r:
                    expected = r.read().decode("utf-8", "replace").split()[0].lower()
                h = hashlib.sha256()
                with open(zpath, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                if h.hexdigest() != expected:
                    raise UpdateError("The update didn't download intact. Try again.")

            out = os.path.join(work, "x")
            if sys.platform == "darwin":
                # ditto keeps the app bundle's symlinks and permissions intact.
                subprocess.run(["/usr/bin/ditto", "-x", "-k", zpath, out], check=True)
            else:
                with zipfile.ZipFile(zpath) as z:
                    z.extractall(out)
            new = os.path.join(out, os.path.basename(self.staged_path))
            self._verify(new, rel["version"])

            self._discard_staged()
            os.makedirs(self.stage_dir, exist_ok=True)
            shutil.move(new, self.staged_path)
            self.settings.set("staged_version", rel["version"])
            self._set("ready")
            return True
        except subprocess.CalledProcessError:
            self._set("failed", error="The update couldn't be unpacked. Try again.")
        except UpdateError as e:
            self._set("failed", error=str(e))
        except Exception as e:
            self._set("failed", error="Update failed: %s" % e)
        finally:
            if work:
                shutil.rmtree(work, ignore_errors=True)
            self.staging = False
        return False

    def _verify(self, new, version):
        if not os.path.exists(new):
            raise UpdateError("The update couldn't be unpacked. Try again.")
        if sys.platform == "darwin":
            with open(os.path.join(new, "Contents", "Info.plist"), "rb") as f:
                plist = plistlib.load(f)
            if plist.get("CFBundleIdentifier") != BUNDLE_ID:
                raise UpdateError("The update isn't Hop.")
            if plist.get("CFBundleShortVersionString") != version:
                raise UpdateError("The update isn't the version it said it was.")
            subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", new], stderr=subprocess.DEVNULL)
        else:
            if not os.path.isfile(os.path.join(new, APP_NAME + ".exe")):
                raise UpdateError("The update couldn't be unpacked. Try again.")
            vfile = os.path.join(new, "_internal", "VERSION")
            if os.path.isfile(vfile):
                with open(vfile) as f:
                    if f.read().strip() != version:
                        raise UpdateError("The update isn't the version it said it was.")

    def _download(self, url, dest):
        with _request(url, accept="application/octet-stream") as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, last = 0, 0.0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and time.time() - last > 0.2:
                    last = time.time()
                    self._set("downloading", progress=min(0.99, done / float(total)))
        if total and done != total:
            raise UpdateError("The download stopped. Check your connection and try again.")

    def _discard_staged(self):
        shutil.rmtree(self.staged_path, ignore_errors=True)
        self.settings.set("staged_version", None)

    # ---- install -----------------------------------------------------------
    def install(self):
        """'Restart to update': stage the newest version, then swap it in and relaunch."""
        log("install requested (location: %s)" % installed_location())
        if not installed_location():
            self._set("failed", error="This copy runs from source, so it can't update itself. Opening the download page.")
            return "open_page"
        if not self.stage_newest() and self.phase != "ready":
            return None
        self._set("installing")
        try:
            self._launch_swap(relaunch=True)
        except Exception as e:
            self._set("failed", error="Couldn't install the update: %s" % e)
            return None
        log("swap launched, quitting to restart")
        if self.on_quit_for_install:
            self.on_quit_for_install()
        return "restarting"

    def install_on_quit(self):
        staged = self.settings.get("staged_version")
        if (self.settings.get("auto_install") and staged and is_newer(staged, __version__)
                and os.path.exists(self.staged_path) and installed_location() and self.phase != "installing"):
            try:
                log("installing %s on quit" % staged)
                self._launch_swap(relaunch=False)
            except Exception as e:
                log("install on quit failed: %s" % e)

    def _launch_swap(self, relaunch):
        """A small script that waits for this process to exit, swaps in the staged app, optionally reopens it."""
        target = installed_location()
        if not os.access(os.path.dirname(target), os.W_OK):
            raise UpdateError("the app's folder isn't writable")
        pid = os.getpid()
        staged = self.staged_path
        if sys.platform == "darwin":
            script = os.path.join(tempfile.gettempdir(), "hop-swap-%d.sh" % pid)
            with open(script, "w") as f:
                f.write('#!/bin/sh\n'
                        'while kill -0 %d 2>/dev/null; do sleep 0.2; done\n'
                        '[ -d "%s" ] || exit 1\n'
                        'rm -rf "%s.old"\n'
                        'mv "%s" "%s.old" && mv "%s" "%s" && rm -rf "%s.old"\n'
                        '%s\n'
                        'rm -f "$0"\n' % (pid, staged, target, target, target, staged, target, target,
                                          'open "%s"' % target if relaunch else ""))
            subprocess.Popen(["/bin/sh", script], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            script = os.path.join(tempfile.gettempdir(), "%s-swap-%d.ps1" % (APP_NAME.lower(), pid))
            exe = os.path.join(target, APP_NAME + ".exe")
            q = lambda s: "'" + s.replace("'", "''") + "'"
            # Run from %TEMP%: a process whose working folder is inside the app folder locks it, and
            # then the old folder can't be moved aside. Every step retries for a while (WebView2
            # helpers and antivirus scans can hold files for a moment), logs to update.log, and the
            # app is always reopened, falling back to the old version if the swap didn't happen.
            ps = "\n".join([
                "$ErrorActionPreference = 'Continue'",
                "Set-Location -LiteralPath $env:TEMP",
                "[System.IO.Directory]::SetCurrentDirectory($env:TEMP)",  # Set-Location alone keeps the process's real working folder
                "$log = %s; $target = %s; $staged = %s; $exe = %s; $old = $target + '.old'"
                % (q(os.path.join(app_data_dir(), "update.log")), q(target), q(staged), q(exe)),
                "function Log($m) { try { Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + '  swap  ' + $m) -Encoding UTF8 } catch {} }",
                "$t = (Get-Date).AddSeconds(20)",
                "while (Get-Process -Id %d -ErrorAction SilentlyContinue) {" % pid,
                "  if ((Get-Date) -gt $t) { Log 'the app is still running, ending it'; Stop-Process -Id %d -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800; break }" % pid,
                "  Start-Sleep -Milliseconds 200",
                "}",
                "if (Test-Path -LiteralPath $staged) {",
                "  Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue",
                "  $moved = $false; $t = (Get-Date).AddSeconds(30)",
                "  while (-not $moved) {",
                "    try { Move-Item -LiteralPath $target -Destination $old -ErrorAction Stop; $moved = $true }",
                "    catch { if ((Get-Date) -gt $t) { Log ('could not move the old version aside: ' + $_); break }; Start-Sleep -Milliseconds 500 }",
                "  }",
                "  if ($moved) {",
                "    try { Move-Item -LiteralPath $staged -Destination $target -ErrorAction Stop; Log 'installed the new version'; Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue }",
                "    catch { Log ('could not move the new version in: ' + $_); Move-Item -LiteralPath $old -Destination $target -ErrorAction SilentlyContinue }",
                "  }",
                "} else { Log 'no staged update found' }",
                "Start-Process -FilePath $exe -WorkingDirectory $target" if relaunch else "",
                "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue",
                "",
            ])
            with open(script, "w", encoding="utf-8-sig") as f:  # BOM: Windows PowerShell reads BOM-less files as ANSI
                f.write(ps)
            flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
            subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                              "-File", script], creationflags=flags, close_fds=True, cwd=tempfile.gettempdir())
        self.settings.set("staged_version", None)
