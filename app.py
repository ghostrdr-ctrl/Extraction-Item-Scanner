"""Extraction Item Scanner -- desktop price lookup for Passport POS extractions.

Load a JSON or XML extraction file, then find any item by scanning its barcode
with a USB scanner (keyboard-wedge) or by typing part of the SKU / description.
The matched price is shown in a large, register-friendly panel.

Build a standalone Windows .exe with:  python build.py   (see README.md)
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

import item_index as ix
import updater
from version import __version__

APP_TITLE = "Extraction Item Scanner"
REPO_URL = "https://github.com/ghostrdr-ctrl/Extraction-Item-Scanner"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".extraction_scanner.json")

DISCLAIMER = (
    "Extraction Item Scanner is an independent, unofficial tool.\n\n"
    "It is NOT approved, endorsed, sponsored by, or affiliated with "
    "Gilbarco Inc., Gilbarco Veeder-Root, or any of their parent, subsidiary, "
    "or affiliated companies. “Gilbarco”, “Passport”, and all "
    "related names, marks, and logos are the property of their respective "
    "owners and are used here only to describe file compatibility.\n\n"
    "This software is provided “as is”, without warranty of any kind, "
    "express or implied. Always verify prices against your official POS system "
    "before relying on them. Use at your own risk."
)

# Palette (light, high-contrast, works on a shop counter monitor)
BG = "#f4f6f8"
CARD = "#ffffff"
ACCENT = "#0b6e4f"
ACCENT_DK = "#08533b"
MUTED = "#6b7280"
HIT = "#0b6e4f"
MISS = "#b91c1c"
INACTIVE = "#b45309"


# ---------------------------------------------------------------------------
# Tiny config persistence (remember the last file opened)
# ---------------------------------------------------------------------------

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# High-DPI support (Windows)
# ---------------------------------------------------------------------------

def enable_dpi_awareness() -> None:
    """Tell Windows this process handles its own DPI scaling.

    Without this, Windows bitmap-stretches the whole window on displays scaled
    above 100%, so text and controls look blurry. Must be called *before* the
    Tk root is created so Tk reports the monitor's real DPI. Once aware, Tk
    already sets its font scaling to dpi/72 automatically -- so point-sized
    fonts come out crisp and correctly sized; only pixel dimensions (handled by
    ScannerApp._px) still need manual scaling.

    We request "System DPI aware", which is the most reliable mode for Tk 8.6:
    crisp on the primary display and correctly sized, without the mixed-monitor
    repaint bugs that Per-Monitor awareness triggers in Tk.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:  # Windows 8.1+ : PROCESS_SYSTEM_DPI_AWARE = 1
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except (AttributeError, OSError):
        pass
    try:  # Vista+ fallback
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class ScannerApp(tk.Tk):
    def __init__(self):
        enable_dpi_awareness()  # must run before the Tk root is created
        super().__init__()

        # Scale pixel dimensions for the monitor's DPI. Point-sized fonts are
        # already handled by Tk's own dpi/72 scaling; this covers everything
        # specified in raw pixels (geometry, row height, column widths, …).
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96.0)

        self.title(f"{APP_TITLE}  v{__version__}")
        self.geometry(f"{self._px(980)}x{self._px(680)}")
        self.minsize(self._px(760), self._px(520))
        self.configure(bg=BG)

        self.index: ix.ItemIndex | None = None
        self.current_file: str = ""
        self._search_after: str | None = None
        self._load_queue: queue.Queue = queue.Queue()
        self._update_queue: queue.Queue = queue.Queue()
        self._pending_update: updater.UpdateInfo | None = None

        self._build_fonts()
        self._build_styles()
        self._build_menu()
        self._build_widgets()

        # Clean up any leftover file from a previous self-update, then check
        # GitHub for a newer release in the background (silent if none / offline).
        updater.cleanup_old()
        self.after(800, lambda: self.check_updates(manual=False))

        cfg = load_config()
        last = cfg.get("last_file", "")
        if last and os.path.exists(last):
            self.after(150, lambda: self.load_file(last))
        else:
            self._set_status("Open an extraction file (JSON or XML) to begin.")

    def _px(self, n: float) -> int:
        """Scale a 96-DPI pixel value to the current display's DPI."""
        return int(round(n * self.scale))

    # -- look & feel --------------------------------------------------------

    def _build_fonts(self):
        self.f_price = tkfont.Font(family="Segoe UI", size=54, weight="bold")
        self.f_desc = tkfont.Font(family="Segoe UI", size=22, weight="bold")
        self.f_meta = tkfont.Font(family="Segoe UI", size=11)
        self.f_search = tkfont.Font(family="Segoe UI", size=18)
        self.f_label = tkfont.Font(family="Segoe UI", size=10, weight="bold")

    def _build_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=self._px(26), font=("Segoe UI", 10),
                        background=CARD, fieldbackground=CARD)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    def _build_menu(self):
        menubar = tk.Menu(self)
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Check for Updates…",
                             command=lambda: self.check_updates(manual=True))
        helpmenu.add_command(label="View on GitHub",
                             command=lambda: webbrowser.open(REPO_URL))
        helpmenu.add_separator()
        helpmenu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=helpmenu)
        self.config(menu=menubar)

    def _build_widgets(self):
        # ---- top bar: file controls ----
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=16, pady=(14, 6))

        tk.Button(top, text="Open Extraction File…", command=self.open_dialog,
                  font=self.f_label, bg=ACCENT, fg="white", activebackground=ACCENT_DK,
                  activeforeground="white", relief="flat", padx=14, pady=6,
                  cursor="hand2").pack(side="left")
        tk.Button(top, text="Reload", command=self.reload_file, font=self.f_label,
                  bg="#e5e7eb", fg="#111827", relief="flat", padx=12, pady=6,
                  cursor="hand2").pack(side="left", padx=(8, 0))

        # Update button -- hidden until a newer release is found on GitHub.
        self.update_btn = tk.Button(top, text="", command=self.on_update_click,
                                    font=self.f_label, bg="#b45309", fg="white",
                                    activebackground="#92400e", activeforeground="white",
                                    relief="flat", padx=12, pady=6, cursor="hand2")
        # not packed yet

        self.file_lbl = tk.Label(top, text="No file loaded", font=self.f_meta,
                                 bg=BG, fg=MUTED, anchor="w")
        self.file_lbl.pack(side="left", padx=12)

        # ---- search box ----
        sbar = tk.Frame(self, bg=BG)
        sbar.pack(fill="x", padx=16, pady=(4, 8))
        tk.Label(sbar, text="Scan or search:", font=self.f_label, bg=BG,
                 fg="#111827").pack(side="left", padx=(0, 10))
        self.query = tk.StringVar()
        self.entry = tk.Entry(sbar, textvariable=self.query, font=self.f_search,
                              relief="solid", bd=1, bg="white")
        self.entry.pack(side="left", fill="x", expand=True, ipady=self._px(6))
        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Escape>", lambda e: self._clear_search())

        # ---- result card ----
        card = tk.Frame(self, bg=CARD, relief="flat", highlightbackground="#e5e7eb",
                        highlightthickness=1)
        card.pack(fill="x", padx=16, pady=(0, 10))
        # Keep the description wrap width matched to the card so it stays correct
        # at any DPI and any window width.
        card.bind("<Configure>",
                  lambda e: self.desc_lbl.config(wraplength=max(self._px(200),
                                                                e.width - self._px(40))))
        self.desc_lbl = tk.Label(card, text="-", font=self.f_desc, bg=CARD,
                                 fg="#111827", anchor="w", justify="left",
                                 wraplength=self._px(920))
        self.desc_lbl.pack(fill="x", padx=18, pady=(14, 2))
        self.price_lbl = tk.Label(card, text="", font=self.f_price, bg=CARD,
                                  fg=ACCENT, anchor="w")
        self.price_lbl.pack(fill="x", padx=16, pady=(0, 2))
        self.meta_lbl = tk.Label(card, text="Scan a barcode or start typing.",
                                 font=self.f_meta, bg=CARD, fg=MUTED, anchor="w",
                                 justify="left")
        self.meta_lbl.pack(fill="x", padx=18, pady=(0, 16))

        # ---- results table ----
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        cols = ("desc", "price", "code", "id", "status")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        headings = {
            "desc": ("Description", 380, "w"),
            "price": ("Price", 90, "e"),
            "code": ("Scan Code", 150, "w"),
            "id": ("Item ID", 130, "w"),
            "status": ("", 70, "center"),
        }
        for key, (text, width, anchor) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=self._px(width), anchor=anchor,
                             stretch=(key == "desc"))
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("inactive", foreground=INACTIVE)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Return>", self._on_tree_enter)

        # ---- status bar ----
        self.status = tk.Label(self, text="", font=self.f_meta, bg="#111827",
                               fg="#e5e7eb", anchor="w", padx=12, pady=5)
        self.status.pack(fill="x", side="bottom")

        self._row_items: list[ix.Item] = []
        self.entry.focus_set()

    # -- loading ------------------------------------------------------------

    def open_dialog(self):
        path = filedialog.askopenfilename(
            title="Open extraction file",
            filetypes=[("Extraction files", "*.json *.xml"),
                       ("JSON", "*.json"), ("XML", "*.xml"), ("All files", "*.*")],
        )
        if path:
            self.load_file(path)

    def reload_file(self):
        if self.current_file:
            self.load_file(self.current_file)
        else:
            self.open_dialog()

    def load_file(self, path: str):
        self._set_status(f"Loading {os.path.basename(path)} …")
        self.file_lbl.config(text=os.path.basename(path))
        self.config(cursor="watch")

        def worker():
            # Never touch Tk from this thread; hand results back via the queue,
            # which the main loop drains in _poll_load().
            try:
                items = ix.load_items(path)
                index = ix.ItemIndex(items)
                store = ix.store_name(path)
                self._load_queue.put(("ok", path, index, store))
            except Exception as exc:  # surface any parse error to the user
                self._load_queue.put(("err", path, exc, None))

        threading.Thread(target=worker, daemon=True).start()
        self.after(50, self._poll_load)

    def _poll_load(self):
        try:
            kind, path, payload, store = self._load_queue.get_nowait()
        except queue.Empty:
            self.after(50, self._poll_load)
            return
        if kind == "ok":
            self._on_loaded(path, payload, store)
        else:
            self._on_load_error(path, payload)

    def _on_loaded(self, path: str, index: ix.ItemIndex, store: str):
        self.index = index
        self.current_file = path
        self.config(cursor="")
        save_config({"last_file": path})
        priced = sum(1 for i in index.items if i.price > 0)
        store_txt = f"  •  {store}" if store else ""
        self._set_status(
            f"Loaded {len(index):,} items ({priced:,} priced){store_txt}  •  "
            f"{os.path.basename(path)}"
        )
        self.file_lbl.config(text=f"{os.path.basename(path)}  ({len(index):,} items)")
        self._clear_search()
        self.entry.focus_set()

    def _on_load_error(self, path: str, exc: Exception):
        self.config(cursor="")
        self._set_status(f"Failed to load {os.path.basename(path)}: {exc}")
        messagebox.showerror(APP_TITLE, f"Could not load file:\n\n{exc}")

    # -- search / scan ------------------------------------------------------

    def _on_key(self, event):
        # Ignore navigation keys; debounce live search.
        if event.keysym in ("Return", "Up", "Down", "Escape"):
            return
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(120, self._run_search)

    def _run_search(self):
        self._search_after = None
        if not self.index:
            return
        results = self.index.search(self.query.get())
        self._fill_tree(results)

    def _on_enter(self, event):
        """USB scanners send the code followed by Enter -- exact lookup here."""
        if not self.index:
            self.open_dialog()
            return
        code = self.query.get().strip()
        if not code:
            return
        hit = self.index.lookup_code(code)
        if hit is not None:
            self._show_item(hit, scanned=True)
            self._fill_tree([hit] + [i for i in self.index.search(code) if i is not hit])
        else:
            results = self.index.search(code)
            if results:
                self._show_item(results[0], scanned=False)
                self._fill_tree(results)
            else:
                self._show_miss(code)
                self._fill_tree([])
        # Select-all so the next scan overwrites the box.
        self.entry.select_range(0, "end")

    def _clear_search(self):
        self.query.set("")
        self._fill_tree([])
        self.desc_lbl.config(text="-")
        self.price_lbl.config(text="")
        self.meta_lbl.config(text="Scan a barcode or start typing.", fg=MUTED)
        self.entry.focus_set()

    # -- table --------------------------------------------------------------

    def _fill_tree(self, items: list[ix.Item]):
        self.tree.delete(*self.tree.get_children())
        self._row_items = items
        for i, it in enumerate(items):
            status = "" if it.active else "inactive"
            tags = () if it.active else ("inactive",)
            self.tree.insert(
                "", "end", iid=str(i),
                values=(it.description or "-", _money(it.price),
                        it.primary_code, it.item_id,
                        "" if it.active else "inactive"),
                tags=tags,
            )

    def _on_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self._row_items):
            self._show_item(self._row_items[idx], scanned=False)

    def _on_tree_enter(self, event):
        self._on_select(event)

    # -- result card --------------------------------------------------------

    def _show_item(self, it: ix.Item, scanned: bool):
        self.desc_lbl.config(text=it.description or "(no description)")
        self.price_lbl.config(text=_money(it.price), fg=HIT if it.active else INACTIVE)
        codes = ", ".join(it.scan_codes) if it.scan_codes else "(no barcode)"
        bits = [f"Item ID: {it.item_id or '-'}", f"Barcode: {codes}"]
        if it.department:
            bits.append(f"Dept: {it.department}")
        if it.code_format:
            bits.append(f"Format: {it.code_format}")
        if not it.active:
            bits.append("⚠ INACTIVE ITEM")
        prefix = "✓ Scanned  " if scanned else ""
        self.meta_lbl.config(text=prefix + "   ".join(bits),
                            fg=INACTIVE if not it.active else MUTED)

    def _show_miss(self, code: str):
        self.desc_lbl.config(text="No match")
        self.price_lbl.config(text="-", fg=MISS)
        self.meta_lbl.config(text=f"✗ Nothing found for “{code}”. "
                                  f"Check the code or search by description.", fg=MISS)

    # -- updates ------------------------------------------------------------

    def check_updates(self, manual: bool):
        """Kick off a background check against GitHub Releases.

        *manual* True comes from the Help menu and reports "you're up to date"
        or errors; the silent startup check stays quiet unless an update exists.
        """
        if manual:
            self._set_status("Checking for updates…")

        def worker():
            try:
                info = updater.check_for_update()
                self._update_queue.put(("ok", info, manual))
            except Exception as exc:
                self._update_queue.put(("err", exc, manual))

        threading.Thread(target=worker, daemon=True).start()
        self.after(50, self._poll_update_check)

    def _poll_update_check(self):
        try:
            kind, payload, manual = self._update_queue.get_nowait()
        except queue.Empty:
            self.after(50, self._poll_update_check)
            return
        if kind == "err":
            if manual:
                messagebox.showwarning(
                    APP_TITLE,
                    "Could not check for updates.\n\n"
                    f"{payload}\n\nCheck your internet connection and try again.",
                )
                self._set_status("Update check failed.")
            return

        info: updater.UpdateInfo = payload
        if info.available:
            self._pending_update = info
            self.update_btn.config(text=f"⬆  Update to {info.latest_tag}")
            self.update_btn.pack(side="right")
            self._set_status(
                f"Update available: {info.latest_tag} (you have v{__version__}). "
                f"Click “Update” at the top right."
            )
        elif manual:
            messagebox.showinfo(
                APP_TITLE,
                f"You're up to date.\n\nInstalled version: v{__version__}",
            )
            self._set_status(f"Up to date (v{__version__}).")

    def on_update_click(self):
        info = self._pending_update
        if not info:
            return
        if not updater.is_frozen():
            messagebox.showinfo(
                APP_TITLE,
                f"A newer version ({info.latest_tag}) is available.\n\n"
                "You're running from source, so update with 'git pull' (or "
                "download the new .exe from the Releases page).",
            )
            webbrowser.open(info.page)
            return

        notes = (info.notes or "").strip()
        if len(notes) > 500:
            notes = notes[:500] + "…"
        msg = (f"Update from v{__version__} to {info.latest_tag}?\n\n"
               "The app will download the new version, replace itself, and "
               "restart automatically.")
        if notes:
            msg += f"\n\nWhat's new:\n{notes}"
        if not messagebox.askyesno(APP_TITLE, msg):
            return
        self._run_self_update(info)

    def _run_self_update(self, info: updater.UpdateInfo):
        # Modal progress dialog.
        dlg = tk.Toplevel(self)
        dlg.title("Updating…")
        dlg.configure(bg=BG)
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)  # no close mid-update
        tk.Label(dlg, text=f"Downloading {info.latest_tag}…", font=self.f_label,
                 bg=BG, fg="#111827").pack(padx=self._px(24), pady=(self._px(18), self._px(8)))
        bar = ttk.Progressbar(dlg, mode="determinate", length=self._px(320),
                              maximum=100)
        bar.pack(padx=self._px(24), pady=(0, self._px(6)))
        pct = tk.Label(dlg, text="0%", font=self.f_meta, bg=BG, fg=MUTED)
        pct.pack(padx=self._px(24), pady=(0, self._px(18)))
        dlg.update_idletasks()
        dlg.grab_set()

        progress_q: queue.Queue = queue.Queue()

        def progress_cb(done, total):
            progress_q.put(("progress", done, total))

        def worker():
            try:
                updater.download_and_apply(info.url, progress_cb)
                progress_q.put(("done", None, None))
            except Exception as exc:
                progress_q.put(("error", exc, None))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                kind, a, b = progress_q.get_nowait()
            except queue.Empty:
                self.after(60, poll)
                return
            if kind == "progress":
                done, total = a, b
                if total:
                    p = int(done * 100 / total)
                    bar.config(mode="determinate", value=p)
                    pct.config(text=f"{p}%  ({done // 1024:,} / {total // 1024:,} KB)")
                else:
                    bar.config(mode="indeterminate")
                    bar.start(12)
                    pct.config(text=f"{done // 1024:,} KB")
                self.after(60, poll)
            elif kind == "done":
                pct.config(text="Restarting…")
                self.after(400, self._finish_update)
            else:  # error
                dlg.grab_release()
                dlg.destroy()
                messagebox.showerror(
                    APP_TITLE,
                    f"Update failed:\n\n{a}\n\n"
                    "Your current version is unchanged. You can download the "
                    "latest .exe manually from the Releases page.",
                )
                self._set_status("Update failed - current version kept.")

        self.after(60, poll)

    def _finish_update(self):
        # The new copy has been launched by updater.download_and_apply(); quit
        # this one so it can take over.
        self.destroy()

    # -- about --------------------------------------------------------------

    def show_about(self):
        dlg = tk.Toplevel(self)
        dlg.title(f"About {APP_TITLE}")
        dlg.configure(bg=CARD)
        dlg.transient(self)
        dlg.resizable(False, False)

        pad = self._px(24)
        tk.Label(dlg, text=APP_TITLE, font=self.f_desc, bg=CARD, fg="#111827"
                 ).pack(anchor="w", padx=pad, pady=(pad, 0))
        tk.Label(dlg, text=f"Version {__version__}", font=self.f_meta, bg=CARD,
                 fg=MUTED).pack(anchor="w", padx=pad, pady=(0, self._px(12)))

        legal = tk.Label(dlg, text=DISCLAIMER, font=self.f_meta, bg=CARD,
                         fg="#374151", justify="left", wraplength=self._px(440))
        legal.pack(anchor="w", padx=pad)

        link = tk.Label(dlg, text=REPO_URL, font=self.f_meta, bg=CARD,
                        fg=ACCENT, cursor="hand2")
        link.pack(anchor="w", padx=pad, pady=(self._px(12), 0))
        link.bind("<Button-1>", lambda e: webbrowser.open(REPO_URL))

        btns = tk.Frame(dlg, bg=CARD)
        btns.pack(fill="x", padx=pad, pady=pad)
        tk.Button(btns, text="Check for Updates…", font=self.f_label,
                  bg="#e5e7eb", fg="#111827", relief="flat", padx=12, pady=6,
                  cursor="hand2",
                  command=lambda: self.check_updates(manual=True)).pack(side="left")
        tk.Button(btns, text="Close", font=self.f_label, bg=ACCENT, fg="white",
                  activebackground=ACCENT_DK, activeforeground="white", relief="flat",
                  padx=16, pady=6, cursor="hand2",
                  command=dlg.destroy).pack(side="right")

        dlg.update_idletasks()
        # center over the main window
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        dlg.grab_set()

    # -- misc ---------------------------------------------------------------

    def _set_status(self, text: str):
        self.status.config(text=text)


def _money(value: float) -> str:
    return f"${value:,.2f}"


def main():
    enable_dpi_awareness()  # ScannerApp() also calls this; harmless if repeated
    app = ScannerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
