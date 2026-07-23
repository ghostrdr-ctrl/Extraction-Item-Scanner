"""Parse Passport POS extraction files (JSON / XML) into a searchable item index.

Two extraction formats are supported, auto-detected by content:

* JSON  -- ``{"Items": {"count": N, "data": [ {ItemId, Description,
           UnitPrice, ScanCodes[...] , ...}, ... ]}}``
* XML   -- Gilbarco Passport ``PassportDataMaintenance`` documents.  Each item
           is an ``<ITTDetail>`` element carrying an ``<ItemCode>`` (the scan
           barcode + its ``POSCodeFormat``) and an ``<ITTData>`` block with the
           description and ``RegularSellPrice``.  This covers both the small
           ``Items.xml`` export and the big full-store export, which embeds the
           same ``<ITTDetail>`` elements among many other sections.

The module has no GUI dependencies so it can be unit-tested on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Item:
    """A single sellable item, normalized across extraction formats."""

    item_id: str
    description: str
    price: float
    scan_codes: list[str] = field(default_factory=list)
    department: str = ""
    active: bool = True
    code_format: str = ""

    @property
    def primary_code(self) -> str:
        """Best code to show the user: first real barcode, else the item id."""
        return self.scan_codes[0] if self.scan_codes else self.item_id

    @property
    def all_codes(self) -> list[str]:
        """Every code this item can be found by (barcodes + internal id)."""
        codes = list(self.scan_codes)
        if self.item_id and self.item_id not in codes:
            codes.append(self.item_id)
        return codes


# ---------------------------------------------------------------------------
# Format detection + parsing
# ---------------------------------------------------------------------------

def load_items(path: str) -> list[Item]:
    """Load items from *path*, auto-detecting JSON vs XML by first non-space byte."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        head = fh.read(64).lstrip()
    if head.startswith("{") or head.startswith("["):
        return _parse_json(path)
    if head.startswith("<"):
        return _parse_xml(path)
    raise ValueError(
        "Unrecognized extraction file. Expected a JSON object or an XML document."
    )


def _to_float(value) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


def _parse_json(path: str) -> list[Item]:
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)

    # Tolerate a couple of shapes: {"Items": {"data": [...]}} or a bare list.
    if isinstance(data, dict):
        container = data.get("Items", data)
        rows = container.get("data", container) if isinstance(container, dict) else container
    else:
        rows = data
    if not isinstance(rows, list):
        raise ValueError("JSON file did not contain an item list.")

    items: list[Item] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scan_codes = [str(c).strip() for c in row.get("ScanCodes", []) if str(c).strip()]
        items.append(
            Item(
                item_id=str(row.get("ItemId", "") or "").strip(),
                description=str(row.get("Description", "") or "").strip(),
                price=_to_float(row.get("UnitPrice")),
                scan_codes=scan_codes,
                department=str(row.get("DepartmentId", "") or "").strip(),
                active=bool(row.get("Active", True)),
                code_format="",
            )
        )
    return items


# XML tag helper: strip any namespace prefix (these files are un-namespaced,
# but this keeps us safe if that ever changes).
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_xml(path: str) -> list[Item]:
    """Stream ``<ITTDetail>`` elements so the 29 MB full export stays cheap."""
    items: list[Item] = []
    for _event, elem in ET.iterparse(path, events=("end",)):
        if _local(elem.tag) != "ITTDetail":
            continue

        pos_code = ""
        code_format = ""
        description = ""
        item_id = ""
        price = 0.0
        active = True

        for child in elem.iter():
            tag = _local(child.tag)
            if tag == "POSCode":
                pos_code = (child.text or "").strip()
            elif tag == "POSCodeFormat":
                code_format = child.get("format", "")
            elif tag == "Description" and not description:
                description = (child.text or "").strip()
            elif tag == "ItemID":
                item_id = (child.text or "").strip()
            elif tag == "RegularSellPrice":
                price = _to_float(child.text)
            elif tag == "ActiveFlag":
                active = child.get("value", "yes").lower() != "no"

        if not item_id:
            item_id = pos_code
        # A "plu" POSCode is a manual key, not a scannable barcode.
        scan_codes = []
        if pos_code and code_format and code_format != "plu" and pos_code != item_id:
            scan_codes.append(pos_code)
        elif pos_code and code_format and code_format != "plu":
            scan_codes.append(pos_code)

        if item_id or description:
            items.append(
                Item(
                    item_id=item_id,
                    description=description,
                    price=price,
                    scan_codes=scan_codes,
                    department="",
                    active=active,
                    code_format=code_format,
                )
            )
        elem.clear()  # free memory as we stream
    return items


def store_name(path: str) -> str:
    """Best-effort store name from an XML header; "" for JSON / on any error."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            head = fh.read(2048)
        start = head.find("<Name>")
        if start != -1:
            end = head.find("</Name>", start)
            if end != -1:
                return head[start + 6 : end].replace("&apos;", "'").strip()
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------------------
# Search index
# ---------------------------------------------------------------------------

def _norm_code(code: str) -> str:
    """Normalize a barcode for tolerant matching: strip spaces & leading zeros."""
    code = code.strip().lstrip("0")
    return code or "0"


class ItemIndex:
    """Fast exact-code lookup + substring search over descriptions/codes/ids."""

    def __init__(self, items: Iterable[Item]):
        self.items: list[Item] = list(items)
        self._by_code: dict[str, Item] = {}
        # (description_lower, item, blob_lower) rows for substring search.
        self._rows: list[tuple[str, Item, str]] = []

        for it in self.items:
            for code in it.all_codes:
                # index both the raw code and its zero-stripped form
                self._by_code.setdefault(code, it)
                self._by_code.setdefault(_norm_code(code), it)
            blob = " ".join([it.description, it.item_id, *it.scan_codes]).lower()
            self._rows.append((it.description.lower(), it, blob))

    def __len__(self) -> int:
        return len(self.items)

    def lookup_code(self, code: str) -> Item | None:
        """Exact scan lookup (what a USB scanner produces). Zero-tolerant."""
        code = code.strip()
        if not code:
            return None
        return self._by_code.get(code) or self._by_code.get(_norm_code(code))

    def search(self, query: str, limit: int = 300) -> list[Item]:
        """Ranked substring search over description, item id and scan codes."""
        query = query.strip().lower()
        if not query:
            return []

        exact = self.lookup_code(query)
        starts: list[Item] = []
        contains: list[Item] = []
        seen: set[int] = set()

        if exact is not None:
            seen.add(id(exact))

        for desc_lower, item, blob in self._rows:
            if id(item) in seen:
                continue
            if query in blob:
                if desc_lower.startswith(query):
                    starts.append(item)
                else:
                    contains.append(item)
                seen.add(id(item))
                if len(starts) + len(contains) >= limit:
                    break

        result: list[Item] = []
        if exact is not None:
            result.append(exact)
        result.extend(starts)
        result.extend(contains)
        return result[:limit]
