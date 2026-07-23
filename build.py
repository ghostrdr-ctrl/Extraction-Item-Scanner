"""Build a standalone Windows .exe with PyInstaller.

    python build.py

Produces dist/ExtractionItemScanner.exe -- a single, double-clickable file with
no Python install required on the target machine.
"""

import subprocess
import sys

NAME = "ExtractionItemScanner"


def main() -> int:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",        # single .exe
        "--windowed",       # no console window
        "--name", NAME,
        "--add-data", "item_index.py;.",
        "app.py",
    ]
    print(">", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
