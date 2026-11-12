"""Launcher: double-click / `python Hop.py` opens the window.
Pass arguments for the command line, e.g.  python Hop.py send photo.jpg --to "Studio Mac"
"""
from hop.__main__ import run

if __name__ == "__main__":
    run()
