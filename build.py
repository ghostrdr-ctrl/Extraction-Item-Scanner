"""Build a standalone Windows .exe with PyInstaller.

    python build.py

Produces "dist/Extraction Item Scanner.exe" -- a single, double-clickable file
with no Python install required on the target machine.
"""

import subprocess
import sys

NAME = "Extraction Item Scanner"


def main() -> int:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",        # single .exe
        "--windowed",       # no console window
        "--name", NAME,
        "--icon", "icon.ico",           # exe / taskbar icon
        "--add-data", "icon.ico;.",     # bundled so the window can load it too
        "app.py",
    ]
    print(">", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
