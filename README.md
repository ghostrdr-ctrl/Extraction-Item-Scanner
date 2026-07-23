# Extraction Item Scanner

A small Windows desktop app for looking up item prices from a **Gilbarco
Passport POS extraction file**. Load a JSON or XML extraction, then find any
item by:

- **Scanning its barcode** with a USB scanner (keyboard-wedge - the default mode
  for virtually every retail USB scanner), or
- **Typing** part of the SKU / Item ID / description.

The matched price is shown in a large, register-friendly panel.

> [!IMPORTANT]
> **Not affiliated with Gilbarco.** This is an independent, unofficial tool. It
> is **NOT** approved, endorsed, sponsored by, or affiliated with Gilbarco Inc.,
> Gilbarco Veeder-Root, or any of their parent, subsidiary, or affiliated
> companies. "Gilbarco", "Passport", and all related names and marks belong to
> their respective owners and are used here only to describe file compatibility.
> The software is provided **"as is", without warranty of any kind**. Always
> verify prices against your official POS system before relying on them. See the
> full notice in the app under **Help → About**.

---

## Download & run (no install)

Grab the latest **`Extraction Item Scanner.exe`** from the
[**Releases**](https://github.com/ghostrdr-ctrl/Extraction-Item-Scanner/releases/latest)
page and double-click it. No Python, no install.

1. Click **Open Extraction File…** and pick your `.json` or `.xml` extraction.
2. Click in the search box (it's focused on launch) and **scan an item** - the
   scanner types the barcode and presses Enter, and the price pops up.
3. Or just start typing a description / item number to filter the list.

The last file you opened is remembered and re-loaded on the next launch.

## Automatic updates

The app knows its own version (shown in the title bar and under **Help →
About**) and checks this repo's GitHub Releases on launch:

- When a newer release is published, an **orange “⬆ Update to vX.Y.Z”** button
  appears at the top right.
- Click it to download the new `.exe`; the app replaces itself and restarts
  automatically. You can also check any time via **Help → Check for Updates…**.

Self-update works in the packaged `.exe`. If you run from source, the app points
you to `git pull` / the Releases page instead.

---

## Run from source

Requires Python 3.10+ (with Tkinter, included in the standard python.org Windows
installer). No third-party packages are needed at runtime.

```sh
python app.py
```

## Build the standalone .exe

```sh
pip install -r requirements.txt      # installs PyInstaller (build-time only)
python build.py
```

The result is **`"dist/Extraction Item Scanner.exe"`** - a single double-clickable
file. Copy it to the counter PC and run it.

## Cutting a release (for maintainers)

1. Bump `__version__` in **`version.py`** (e.g. `1.1.0` → `1.2.0`).
2. `python build.py` to produce `"dist/Extraction Item Scanner.exe"`.
3. Create a GitHub release whose **tag matches** the version, prefixed with `v`
   (e.g. `v1.2.0`), and attach the `.exe`:
   ```sh
   gh release create v1.2.0 "dist/Extraction Item Scanner.exe" \
     --title "v1.2.0" --notes "What changed…"
   ```

Installed apps compare their `version.py` against the latest release tag, so the
tag **must** be greater for the update prompt to appear. The attached asset must
be named `Extraction Item Scanner.exe`.

> The self-update download requires the repository to be **public** (it fetches
> the release asset without authentication). While the repo is private, the
> in-app update check will simply find nothing.

---

## Supported extraction formats

Both are auto-detected by content:

| Format | Shape | Item fields used |
| ------ | ----- | ---------------- |
| **JSON** | `{"Items": {"data": [ … ]}}` | `ItemId`, `Description`, `UnitPrice`, `ScanCodes[]`, `DepartmentId`, `Active` |
| **XML** | `PassportDataMaintenance` → `<ITTDetail>` | `POSCode` (+ `POSCodeFormat`), `Description`, `RegularSellPrice`, `ItemID`, `ActiveFlag` |

The XML reader streams `<ITTDetail>` elements, so it handles the large
full-store export (20k+ items, ~30 MB) quickly. A `POSCode` whose format is
`plu` is treated as a manual key, not a scannable barcode; real barcodes
(`upcA`, `ean13`, `ean8`, `gtin`) are indexed for scanning.

## How USB scanning works

A retail USB barcode scanner acts as a **keyboard**: it "types" the barcode
digits and then sends **Enter**. This app keeps the search box focused and
treats Enter as an exact-code lookup, so no drivers or special setup are needed
- plug in the scanner and scan.

Barcode matching is **leading-zero tolerant**: a code stored as `000083620218`
still matches a scan of `83620218` and vice-versa.

---

## Project layout

```
app.py               Tkinter GUI (search, big price panel, About, update banner)
item_index.py        Format detection, parsing, and the search/scan index (no GUI)
updater.py           GitHub-release update check + self-replace
version.py           Single source of truth for the app version
build.py             PyInstaller one-file build
icon.ico             App icon (exe, taskbar, and window)
requirements.txt     Build-time dependency (PyInstaller)
```

## Notes

- The app is pure standard-library at runtime (`tkinter`, `json`,
  `xml.etree`, `urllib`) - nothing to install to *run* it from source.
- The app is DPI-aware, so it renders crisply on displays scaled above 100%.
- **No store data is included in this repository.** Extraction files stay on
  your machine; the app reads whatever file you point it at and nothing is
  uploaded anywhere.

## Disclaimer

See the notice at the top of this file and in **Help → About**. Trademarks are
the property of their respective owners. This project is provided without
warranty; the authors are not liable for any losses arising from its use.
