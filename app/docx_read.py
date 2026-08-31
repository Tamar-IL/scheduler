# -*- coding: utf-8 -*-
"""Reading tables out of a .docx, in the standard library.

School offices keep staff lists in Word as often as in Excel — a heading, a
table under it, another heading, another table.  That is already the shape
the importer wants, so a Word document needs no new format: the headings
name the tables and the tables are the tables.

Same discipline as `app/xlsx.py`: a .docx is a zip of XML, values come out as
text, and nothing else is attempted.  `python-docx` is a dependency of the
*export* path only, and a program that cannot read its own input without it
would be a program that fails differently on two machines.

What a cell means here is its text.  Merged cells are padded out with empty
strings so the columns of a row still line up with the header, which is the
one piece of Word's table model the importer actually depends on.
"""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _text(node) -> str:
    """Every run of text under a node, joined.

    Word splits a word across runs whenever the formatting changes — a bold
    letter, a spell-check mark — so a cell holding "מורות" can easily be four
    `w:t` elements.  Joining them is the whole trick.
    """
    return "".join(t.text or "" for t in node.iter(f"{_NS}t")).strip()


def _row(tr) -> list[str]:
    cells: list[str] = []
    for tc in tr.findall(f"{_NS}tc"):
        cells.append(_text(tc))
        span = tc.find(f"{_NS}tcPr/{_NS}gridSpan")
        if span is not None:
            # A merged cell covers several columns; the header row above it
            # did not merge, so the row has to be padded or every value
            # after it lands under the wrong heading.
            cells += [""] * (int(span.get(f"{_NS}val", "1")) - 1)
    return cells


def read_tables(data: bytes) -> dict[str, list[list[str]]]:
    """{table title: rows of text}, in document order.

    A table's title is the last non-empty paragraph before it — which is how
    a person labels a table in Word.  A table with nothing above it is given
    a positional name and left to the importer's header sniffer.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            document = ET.fromstring(zf.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(
            "לא ניתן לקרוא את קובץ ה-Word. יש לשמור אותו כ-docx "
            f"(קובץ doc ישן אינו נתמך) ולנסות שוב. ({exc})") from exc

    body = document.find(f"{_NS}body")
    if body is None:
        return {}

    out: dict[str, list[list[str]]] = {}
    heading = ""
    for node in body:
        if node.tag == f"{_NS}p":
            text = _text(node)
            if text:
                heading = text
        elif node.tag == f"{_NS}tbl":
            rows = [_row(tr) for tr in node.findall(f"{_NS}tr")]
            rows = [r for r in rows if any(c for c in r)]
            if not rows:
                continue
            # "## מורות" and "מורות" are the same instruction; a person
            # writing in Word will not type the hashes.
            title = heading.lstrip("#").strip() or f"טבלה {len(out) + 1}"
            while title in out:
                title += " "
            out[title] = rows
            heading = ""
    return out
