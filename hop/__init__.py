import os
import sys


def _read_version():
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (getattr(sys, "_MEIPASS", None), os.path.dirname(here)):
        if base:
            try:
                with open(os.path.join(base, "VERSION")) as f:
                    return f.read().strip()
            except OSError:
                pass
    return "0.0.0"


__version__ = _read_version()

# GitHub repository the app checks for new releases.
UPDATE_REPO = "YuvrajxGarg/hop"
