"""Self-update support via GitHub Releases.

The app checks the repo's ``releases/latest`` endpoint (public, no auth needed
once the repo is public), compares the tag against the bundled version, and can
download the new ``.exe`` and swap itself in place.

Self-update only applies to the packaged one-file build (``sys.frozen``). When
running from source there is nothing to swap -- use ``git pull`` instead.

The Windows swap trick: a running ``.exe`` cannot be deleted, but it *can* be
renamed. So we download alongside the current exe, rename the running exe to
``*.old``, move the new exe into its place, relaunch, and exit. On the next
launch ``cleanup_old()`` deletes the leftover ``*.old`` file.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import urllib.request

from version import __version__

REPO = "ghostrdr-ctrl/Extraction-Item-Scanner"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ASSET_NAME = "Extraction Item Scanner.exe"
_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def parse_version(tag: str) -> tuple[int, int, int]:
    """Turn ``"v1.2.3"`` (or ``"1.2"``) into a comparable ``(1, 2, 3)`` tuple."""
    tag = (tag or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in tag.split(".")[:3]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])  # type: ignore[return-value]


def current_version() -> str:
    return __version__


def is_frozen() -> bool:
    """True when running as the PyInstaller one-file .exe."""
    return bool(getattr(sys, "frozen", False))


def current_exe() -> str:
    return sys.executable


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def _urlopen(url: str):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"ExtractionItemScanner/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    return urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx)


class UpdateInfo:
    def __init__(self, latest_tag: str, url: str, notes: str, page: str):
        self.latest_tag = latest_tag
        self.url = url
        self.notes = notes
        self.page = page
        self.current = f"v{__version__}"
        self.available = parse_version(latest_tag) > parse_version(__version__)


def check_for_update() -> UpdateInfo:
    """Query GitHub for the latest release. Raises on network / HTTP error."""
    with _urlopen(API_LATEST) as resp:
        data = json.load(resp)
    latest_tag = data.get("tag_name", "") or ""
    asset_url = ""
    for asset in data.get("assets", []):
        if asset.get("name") == ASSET_NAME:
            asset_url = asset.get("browser_download_url", "")
            break
    return UpdateInfo(
        latest_tag=latest_tag,
        url=asset_url,
        notes=data.get("body", "") or "",
        page=data.get("html_url", "") or RELEASES_PAGE,
    )


# ---------------------------------------------------------------------------
# Applying an update (frozen build only)
# ---------------------------------------------------------------------------

def cleanup_old() -> None:
    """Remove the leftover ``*.old`` file from a previous self-update."""
    if not is_frozen():
        return
    try:
        os.remove(current_exe() + ".old")
    except OSError:
        pass


def download_and_apply(url: str, progress_cb=None) -> str:
    """Download the new exe, swap it in, relaunch it, and return the exe path.

    The caller is expected to exit the current process right after this returns
    so the relaunched copy takes over. ``progress_cb(done, total)`` is called
    from *this* thread as bytes arrive -- marshal to the UI thread yourself.
    """
    if not is_frozen():
        raise RuntimeError(
            "Self-update is only available in the packaged .exe. "
            "When running from source, update with 'git pull'."
        )
    if not url:
        raise RuntimeError("No downloadable .exe was found on the latest release.")

    exe = current_exe()
    new_path = exe + ".new"
    old_path = exe + ".old"

    # 1. Download to <exe>.new (same folder -> same volume -> atomic rename).
    with _urlopen(url) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        done = 0
        with open(new_path, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)

    # 2. Swap: rename running exe out of the way, move new one in.
    try:
        os.remove(old_path)
    except OSError:
        pass
    os.rename(exe, old_path)
    try:
        os.rename(new_path, exe)
    except OSError:
        # Roll back if the move failed, so the app still runs.
        os.rename(old_path, exe)
        raise

    # 3. Launch the new copy and hand off.
    subprocess.Popen([exe], close_fds=True)
    return exe
