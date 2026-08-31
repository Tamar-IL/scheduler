# -*- coding: utf-8 -*-
"""A read-only .xlsx parser, in the standard library.

Administrators keep staff lists in Excel, so the platform has to read .xlsx.
`openpyxl` is the obvious way and is not installed here, and a scheduling
program should not gain a dependency for one import path — an .xlsx file is
a zip of XML, and reading cell text out of it is about a hundred lines.

Deliberately narrow: values as text, no formulas beyond their cached result,
no styles, no dates.  Every column the importer reads is a name, a number or
a short list, so that is all it needs.  Anything richer belongs in openpyxl,
and `read_workbook` raises a Hebrew sentence rather than guessing.
"""
from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _text(node) -> str:
    """All the text under a node, which is how a styled cell stores it."""
    return "".join(node.itertext()) if node is not None else ""


def _column(ref: str) -> int:
    """"C7" -> 2.  Cells may be sparse, so the index has to be read."""
    letters = re.match(r"[A-Z]+", ref or "")
    if not letters:
        return 0
    n = 0
    for ch in letters.group():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    return [_text(si) for si in ET.fromstring(raw).findall(f"{_NS}si")]


def _sheet_paths(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(sheet name, path inside the zip)] in workbook order."""
    book = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target")
              for r in rels.findall(f"{_PKG_REL}Relationship")}
    out = []
    for sheet in book.iter(f"{_NS}sheet"):
        path = target.get(sheet.get(f"{_REL}id"), "")
        if not path:
            continue
        if not path.startswith("/"):
            path = "xl/" + path.lstrip("/")
        out.append((sheet.get("name", ""), path.lstrip("/")))
    return out


def _rows(zf: zipfile.ZipFile, path: str, shared: list[str]) -> list[list[str]]:
    sheet = ET.fromstring(zf.read(path))
    out: list[list[str]] = []
    for row in sheet.iter(f"{_NS}row"):
        cells: list[str] = []
        for c in row.findall(f"{_NS}c"):
            idx = _column(c.get("r", ""))
            while len(cells) < idx:
                cells.append("")
            kind = c.get("t")
            if kind == "s":
                v = c.find(f"{_NS}v")
                i = int(v.text) if v is not None and v.text else -1
                cells.append(shared[i] if 0 <= i < len(shared) else "")
            elif kind == "inlineStr":
                cells.append(_text(c.find(f"{_NS}is")).strip())
            else:
                v = c.find(f"{_NS}v")
                cells.append((v.text or "").strip() if v is not None else "")
        out.append([x.strip() for x in cells])
    return out


def read_workbook(data: bytes) -> dict[str, list[list[str]]]:
    """{sheet name: rows of text}.  Raises ValueError on anything else."""
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            shared = _shared_strings(zf)
            return {name: _rows(zf, path, shared)
                    for name, path in _sheet_paths(zf)}
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(
            "לא ניתן לקרוא את קובץ ה-Excel. יש לשמור אותו כ-xlsx "
            f"(או לייצא ל-CSV) ולנסות שוב. ({exc})") from exc
