# -*- coding: utf-8 -*-
"""Turning two uploaded files into a school.

The administrator has a staff list and a list of when people cannot teach.
Neither is written for a program: columns are named in Hebrew, days are
written out, "6-7" and "6,7" mean the same thing, and the same idea arrives
under three different headings depending on who typed it.

So the reader is deliberately tolerant about *form* and strict about
*meaning*.  Every column is matched against a list of names it may go by; a
value it cannot make sense of produces a numbered, quotable line — "גיליון
מורים שורה 7: אין יום בשם 'יום ד' בשבוע של בית הספר" — instead of a silently
dropped row.  Nothing is guessed: a row that cannot be read is reported and
skipped, and the import as a whole succeeds only when the school it produced
is internally consistent.

The two files the product asks for map onto four tables.  They can arrive as
four sheets of one workbook, as `## section` blocks in one CSV, or as two
files each holding some of them — `read_files` merges whatever it is given
and says what was missing.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

from app import docx_read
from app import spec as spec_mod
from app import xlsx

# ---------------------------------------------------------------- sections

TEACHERS, CLASSES, LESSONS, LIMITS, DAYS, SCHOOL = (
    "teachers", "classes", "lessons", "limits", "days", "school")

#: What a sheet, or a `## heading`, may be called.  Matched case- and
#: space-insensitively, so "רשימת מורות" and "מורות" are the same sheet.
SECTION_NAMES = {
    TEACHERS: ("מורים", "מורות", "צוות", "רשימת מורות", "רשימת מורים",
               "teachers", "staff"),
    CLASSES: ("כיתות", "רשימת כיתות", "classes"),
    LESSONS: ("שיעורים", "מקצועות", "טבלת שיעורים", "משימות הוראה",
              "lessons", "subjects"),
    LIMITS: ("אילוצים", "מגבלות", "אילוצי מורות", "ימים ושעות חסומים",
             "זמינות", "limits", "availability", "constraints"),
    DAYS: ("ימים", "מבנה שבוע", "days", "week"),
    SCHOOL: ("בית ספר", "פרטי בית הספר", "school"),
}

#: Column headings, by the field they fill.  First match wins, so put the
#: unambiguous spelling first.
COLUMNS = {
    TEACHERS: {
        "name": ("שם המורה", "שם", "מורה", "name", "teacher"),
        "declared": ("שעות שבועיות", "שעות", "היקף משרה", "hours"),
        "homeroom": ("מחנכת של", "מחנכת", "כיתת אם", "homeroom"),
        "max_per_day": ("מקסימום שעות ליום", "מקס שעות ליום", "max per day"),
        "min_per_day": ("מינימום שעות ליום", "מינ שעות ליום", "min per day"),
        "exact_working_days": ("ימי עבודה", "מספר ימי עבודה", "working days"),
        "max_long_days": ("מקסימום ימים ארוכים", "max long days"),
        "min_late_days": ("מינימום ימי סיום מאוחר", "min late days"),
        "max_late_days": ("מקסימום ימי סיום מאוחר", "max late days"),
        "max_windows": ("מקסימום חלונות", "חלונות", "max windows"),
    },
    CLASSES: {
        "name": ("שם הכיתה", "כיתה", "name", "class"),
        "day": ("יום", "day"),
        "min": ("מינימום שעות", "מינימום", "min"),
        "max": ("מקסימום שעות", "מקסימום", "max"),
        "exact": ("שעות מדויקות", "מדויק", "exact"),
    },
    LESSONS: {
        "klass": ("כיתה", "class"),
        "subject": ("מקצוע", "שיעור", "subject"),
        "teachers": ("מורות", "מורה", "שם המורה", "teachers", "teacher"),
        "hours": ("שעות", "שעות שבועיות", "hours"),
        "pattern": ("פיצול", "מבנה", "pattern"),
    },
    LIMITS: {
        "name": ("שם המורה", "מורה", "שם", "name", "teacher"),
        "kind": ("סוג", "סוג האילוץ", "type"),
        "day": ("יום", "ימים", "day", "days"),
        "periods": ("שעות", "שעה", "periods", "period"),
        "note": ("הערה", "note"),
    },
    DAYS: {
        "day": ("יום", "day"),
        "periods": ("מספר שעות", "שעות", "periods"),
    },
    SCHOOL: {
        "name": ("שם בית הספר", "שם", "name"),
        "year": ("שנה", "שנת לימודים", "year"),
    },
}

#: The `סוג` column of the limits file.  Everything the administrator might
#: reasonably write for one of the four things the engine can express.
LIMIT_KINDS = {
    "off": ("יום חופש", "חופש", "יום חופשי", "לא עובדת", "אינה עובדת",
            "day off", "off"),
    "off_choice": ("בחירת יום חופש", "יום חופש לבחירה", "חופש באחד מהימים",
                   "אחד מהימים", "day off choice"),
    "blocked": ("שעות חסומות", "שעות אסורות", "לא זמינה", "חסום",
                "blocked", "unavailable"),
    "until": ("סיום מוקדם", "עד שעה", "מסיימת בשעה", "until", "ends by"),
}


def _norm(text: str) -> str:
    return re.sub(r"[\s‏‎]+", " ", str(text or "")).strip()


def _key(text: str) -> str:
    return _norm(text).replace("-", " ").replace("_", " ").lower()


def _match(value: str, options) -> bool:
    return _key(value) in {_key(o) for o in options}


def _section_of(title: str) -> str | None:
    for section, names in SECTION_NAMES.items():
        if _match(title, names):
            return section
    return None


# ------------------------------------------------------------------ report

@dataclass
class ImportReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: {section: how many rows were read}
    counts: dict[str, int] = field(default_factory=dict)
    #: A full backup also brings its timetable and rule settings back.
    restored: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors,
                "warnings": self.warnings, "notes": self.notes,
                "counts": self.counts}


# ------------------------------------------------------------ table reading

def _tables_from_rows(rows: list[list[str]]) -> dict[str, list[list[str]]]:
    """Split one flat sheet into sections marked by `## name` lines.

    A CSV can only hold one table, and the school needs four.  A line whose
    first cell starts with "##" starts a new one; a file with no such line at
    all is offered to the header sniffer as a single table.
    """
    out: dict[str, list[list[str]]] = {}
    current = "__single__"
    out[current] = []
    for row in rows:
        first = _norm(row[0] if row else "")
        if first.startswith("##"):
            current = _norm(first.lstrip("#"))
            out.setdefault(current, [])
            continue
        if any(_norm(c) for c in row):
            out[current].append(row)
    if not out["__single__"]:
        del out["__single__"]
    return out


def _sniff(rows: list[list[str]]) -> str | None:
    """Which table is this, judging by its header row alone."""
    if not rows:
        return None
    head = [_key(c) for c in rows[0]]
    best, score = None, 0
    for section, columns in COLUMNS.items():
        hit = sum(1 for names in columns.values()
                  if any(_key(n) in head for n in names))
        # A lessons table is the only one with both a subject and an hours
        # column; a limits table is the only one with a "kind" column.  The
        # count alone would confuse "מורים" with "שיעורים", which share two
        # headings, so the discriminating column breaks the tie.
        if section == LESSONS and "מקצוע" not in head and "subject" not in head:
            hit = 0
        if hit > score:
            best, score = section, hit
    return best if score >= 2 else None


def _header_map(header: list[str], section: str) -> dict[str, int]:
    head = [_key(c) for c in header]
    out = {}
    for field_name, names in COLUMNS[section].items():
        for n in names:
            if _key(n) in head:
                out[field_name] = head.index(_key(n))
                break
    return out


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return _norm(row[index])


def _int(value: str) -> int | None:
    value = _norm(value)
    if not value:
        return None
    try:
        return int(float(value.replace(",", ".")))
    except ValueError:
        return None


def _split_ints(value: str) -> list[int]:
    """"2,1,1,1" -> [2, 1, 1, 1].

    Unlike `_periods` this keeps order and repeats, because a meeting split
    is a sequence — two doubles and two singles is not the same statement as
    "the numbers 1 and 2 appear".
    """
    out = []
    for part in re.split(r"[,;/ ]+", _norm(value)):
        if part.isdigit():
            out.append(int(part))
    return out


def _periods(value: str) -> list[int]:
    """"6,7", "6-7" and "6 7" all mean the sixth and seventh periods."""
    out: list[int] = []
    for part in re.split(r"[,;/ ]+", _norm(value)):
        if not part:
            continue
        span = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if span:
            out += list(range(int(span.group(1)), int(span.group(2)) + 1))
        elif part.isdigit():
            out.append(int(part))
    return sorted(set(out))


# --------------------------------------------------------------- the reader

class _Reader:
    def __init__(self, grid: spec_mod.Grid):
        self.grid = grid
        self.report = ImportReport()
        self.teachers: dict[str, spec_mod.TeacherSpec] = {}
        self.classes: dict[str, spec_mod.ClassSpec] = {}
        self.lessons: list[spec_mod.LessonSpec] = []
        #: Filled only when the file carries a "בית ספר" block; the platform
        #: otherwise names the school from the upload form.
        self.name = ""
        self.year = ""

    # -- helpers ---------------------------------------------------------

    def err(self, where: str, message: str) -> None:
        self.report.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.report.warnings.append(f"{where}: {message}")

    def days_of(self, where: str, value: str) -> list[int]:
        """One cell holding one day, several days, or "כל הימים"."""
        text = _norm(value)
        if not text or _match(text, ("כל הימים", "כל ימות השבוע", "all")):
            return list(range(len(self.grid.days)))
        out = []
        for part in re.split(r"[,;+/]| ו-", text):
            part = _norm(part).lstrip("ב")
            if not part:
                continue
            try:
                out.append(self.grid.index(part))
            except KeyError:
                self.err(where, f"אין יום בשם {part!r} בשבוע של בית הספר "
                                f"({', '.join(self.grid.days)})")
        return out

    def teacher(self, where: str, name: str) -> spec_mod.TeacherSpec | None:
        t = self.teachers.get(_norm(name))
        if t is None and _norm(name):
            self.err(where, f"המורה {name!r} אינה מופיעה ברשימת הצוות")
        return t

    # -- sections --------------------------------------------------------

    def read_days(self, rows: list[list[str]]) -> None:
        cols = _header_map(rows[0], DAYS)
        names, periods = [], []
        for n, row in enumerate(rows[1:], start=2):
            day = _cell(row, cols.get("day"))
            count = _int(_cell(row, cols.get("periods")))
            if not day:
                continue
            if count is None or count <= 0:
                self.err(f"גיליון ימים שורה {n}",
                         f"{day}: מספר השעות ביום חייב להיות מספר חיובי")
                continue
            names.append(day)
            periods.append(count)
        if names:
            self.grid.days = names
            self.grid.periods_per_day = periods
            self.report.counts[DAYS] = len(names)

    def read_school(self, rows: list[list[str]]) -> None:
        cols = _header_map(rows[0], SCHOOL)
        for row in rows[1:]:
            self.name = _cell(row, cols.get("name")) or self.name
            self.year = _cell(row, cols.get("year")) or self.year

    def read_teachers(self, rows: list[list[str]]) -> None:
        cols = _header_map(rows[0], TEACHERS)
        if "name" not in cols:
            self.err("גיליון מורות", "חסרה עמודת 'שם המורה'")
            return
        for n, row in enumerate(rows[1:], start=2):
            where = f"גיליון מורות שורה {n}"
            name = _cell(row, cols["name"])
            if not name:
                continue
            if name in self.teachers:
                self.err(where, f"המורה {name} מופיעה יותר מפעם אחת")
                continue
            t = spec_mod.TeacherSpec(name=name)
            t.declared = _int(_cell(row, cols.get("declared")))
            t.homeroom = _cell(row, cols.get("homeroom")) or None
            t.max_per_day = _int(_cell(row, cols.get("max_per_day")))
            floor = _int(_cell(row, cols.get("min_per_day")))
            if floor is not None:
                t.min_per_day = floor
            t.exact_working_days = _int(_cell(row, cols.get(
                "exact_working_days")))
            t.max_long_days = _int(_cell(row, cols.get("max_long_days")))
            t.min_late_days = _int(_cell(row, cols.get("min_late_days"))) or 0
            t.max_late_days = _int(_cell(row, cols.get("max_late_days")))
            windows = _int(_cell(row, cols.get("max_windows")))
            if windows is not None:
                t.max_windows = windows
            self.teachers[name] = t
        self.report.counts[TEACHERS] = len(self.teachers)

    def read_classes(self, rows: list[list[str]]) -> None:
        cols = _header_map(rows[0], CLASSES)
        if "name" not in cols:
            self.err("גיליון כיתות", "חסרה עמודת 'שם הכיתה'")
            return
        for n, row in enumerate(rows[1:], start=2):
            where = f"גיליון כיתות שורה {n}"
            name = _cell(row, cols["name"])
            if not name:
                continue
            c = self.classes.setdefault(name, spec_mod.ClassSpec(name=name))
            days = self.days_of(where, _cell(row, cols.get("day")))
            lo = _int(_cell(row, cols.get("min")))
            hi = _int(_cell(row, cols.get("max")))
            exact = _int(_cell(row, cols.get("exact")))
            if exact is not None:
                lo = lo if lo is not None else max(0, exact - 1)
                hi = hi if hi is not None else exact + 1
            if lo is None or hi is None:
                self.err(where, f"כיתה {name}: חסר מינימום או מקסימום שעות ליום")
                continue
            if lo > hi:
                self.err(where, f"כיתה {name}: מינימום {lo} גדול ממקסימום {hi}")
                continue
            for d in days:
                # "כל הימים, 5 שעות" is how a school states its ordinary day,
                # and it is not a mistake on a shortened day — the day is
                # simply shorter.  So the ceiling is trimmed to the day and
                # said out loud, rather than refused.
                room = self.grid.periods_per_day[d]
                if lo > room:
                    self.err(where, f"כיתה {name} ב{self.grid.days[d]}: "
                                    f"מינימום {lo} שעות ביום של {room} שעות")
                    continue
                if hi > room:
                    self.warn(where, f"כיתה {name} ב{self.grid.days[d]}: "
                                     f"המקסימום צומצם מ-{hi} ל-{room}, "
                                     f"אורך היום")
                c.day_span[d] = (lo, min(hi, room))
                if exact is not None and exact <= room:
                    c.preferred[d] = exact
                else:
                    c.preferred.pop(d, None)
        self.report.counts[CLASSES] = len(self.classes)

    def read_lessons(self, rows: list[list[str]]) -> None:
        cols = _header_map(rows[0], LESSONS)
        missing = [k for k in ("subject", "teachers", "hours")
                   if k not in cols]
        if missing:
            self.err("גיליון שיעורים",
                     "חסרות עמודות: " + ", ".join(
                         COLUMNS[LESSONS][k][0] for k in missing))
            return
        for n, row in enumerate(rows[1:], start=2):
            where = f"גיליון שיעורים שורה {n}"
            subject = _cell(row, cols.get("subject"))
            if not subject:
                continue
            if "|" in subject:
                self.err(where, f"שם המקצוע {subject!r} מכיל '|', "
                                f"שהוא התו המפריד בין מורות")
                continue
            hours = _int(_cell(row, cols["hours"]))
            if hours is None or hours <= 0:
                self.err(where, f"{subject}: מספר השעות חייב להיות מספר חיובי")
                continue
            names = [_norm(x) for x in re.split(
                r"[|+]", _cell(row, cols["teachers"])) if _norm(x)]
            if not names:
                self.err(where, f"{subject}: לא צוינה מורה")
                continue
            for name in names:
                self.teacher(where, name)
            klass = _cell(row, cols.get("klass")) or None
            if klass and klass not in self.classes:
                self.err(where, f"{subject}: כיתה {klass} אינה ברשימת הכיתות")
                continue
            pattern = _split_ints(_cell(row, cols.get("pattern")))
            if pattern and sum(pattern) != hours:
                self.warn(where, f"{subject}: הפיצול {pattern} אינו מסתכם "
                                 f"ב-{hours} שעות — הפיצול לא נלקח")
                pattern = []
            self.lessons.append(spec_mod.LessonSpec(
                klass=klass, subject=subject, teachers=names, hours=hours,
                pattern=pattern))
        self.report.counts[LESSONS] = len(self.lessons)

    def read_limits(self, rows: list[list[str]]) -> None:
        cols = _header_map(rows[0], LIMITS)
        if "name" not in cols:
            self.err("גיליון אילוצים", "חסרה עמודת 'שם המורה'")
            return
        seen = 0
        for n, row in enumerate(rows[1:], start=2):
            where = f"גיליון אילוצים שורה {n}"
            name = _cell(row, cols["name"])
            if not name:
                continue
            t = self.teacher(where, name)
            if t is None:
                continue
            raw_kind = _cell(row, cols.get("kind"))
            periods = _periods(_cell(row, cols.get("periods")))
            kind = next((k for k, names in LIMIT_KINDS.items()
                         if _match(raw_kind, names)), None)
            if kind is None:
                if raw_kind:
                    self.err(where, f"סוג אילוץ לא מוכר: {raw_kind!r}. "
                                    f"המותרים: " + ", ".join(
                                        v[0] for v in LIMIT_KINDS.values()))
                    continue
                # An empty type column is the common shorthand: a day with no
                # periods is a day off, a day with periods is a block.
                kind = "blocked" if periods else "off"
            days = self.days_of(where, _cell(row, cols.get("day")))
            if kind in ("off", "off_choice") and not _cell(row,
                                                           cols.get("day")):
                self.err(where, f"{name}: יום חופש בלי לציין יום")
                continue

            if kind == "off":
                for d in days:
                    if d not in t.off_fixed:
                        t.off_fixed.append(d)
            elif kind == "off_choice":
                for d in days:
                    if d not in t.off_choice:
                        t.off_choice.append(d)
            elif kind == "blocked":
                if not periods:
                    self.err(where, f"{name}: שעות חסומות בלי לציין שעות")
                    continue
                # A block on every day is a property of the teacher; a block
                # on one day is a property of that day.  The engine has both,
                # so the file does not have to say which it meant.
                if len(days) == len(self.grid.days):
                    for p in periods:
                        if p not in t.forbidden_periods:
                            t.forbidden_periods.append(p)
                else:
                    for d in days:
                        t.latest_period_on[d] = min(
                            t.latest_period_on.get(d, 10 ** 6),
                            min(periods) - 1)
            elif kind == "until":
                if not periods:
                    self.err(where, f"{name}: סיום מוקדם בלי לציין שעה")
                    continue
                for d in days:
                    t.latest_period_on[d] = min(
                        t.latest_period_on.get(d, 10 ** 6), max(periods))
            seen += 1
        self.report.counts[LIMITS] = seen

    # -- assembly --------------------------------------------------------

    def build(self, name: str, year: str) -> spec_mod.SchoolSpec:
        for t in self.teachers.values():
            t.off_fixed = sorted(set(t.off_fixed))
            t.off_choice = sorted(set(t.off_choice) - set(t.off_fixed))
            t.forbidden_periods = sorted(set(t.forbidden_periods))
            t.latest_period_on = {d: p for d, p in t.latest_period_on.items()
                                  if p < self.grid.periods_per_day[d]}
        # A class with no stated day length still needs one, or nothing can
        # be placed in it; the default is the whole day, which is honest —
        # the school said nothing, so nothing is forbidden.
        for c in self.classes.values():
            for d in range(len(self.grid.days)):
                c.day_span.setdefault(d, (0, self.grid.periods_per_day[d]))
        spec = spec_mod.SchoolSpec(
            name=name or self.name or "בית ספר", year=year or self.year,
            grid=self.grid,
            teachers=list(self.teachers.values()),
            classes=list(self.classes.values()), lessons=self.lessons)
        # Homeroom teachers are the one rule the platform switches on by
        # itself, because the file states it: a teacher named as homeroom of
        # a class is expected to open that class's day.  Three days required
        # and four preferred, because four is what a five-day week with one
        # late-starting day gives — and a small school whose specialists take
        # the first period of one class cannot always reach it.  The
        # constraint screen is where a school that can raises it.
        homerooms = [t.homeroom for t in spec.teachers if t.homeroom]
        if homerooms:
            spec.rules.open_own_class = {c: [3, 4] for c in homerooms}
            self.report.notes.append(
                "מחנכות: הופעל הכלל 'המחנכת פותחת את היום בכיתתה' — "
                "לפחות 3 ימים, רצוי 4. ניתן לשנות במסך האילוצים.")
        for problem in spec.problems():
            self.report.errors.append(problem)
        return spec


# ------------------------------------------------------------------- entry

def _rows_from_bytes(filename: str, data: bytes) -> dict[str, list[list[str]]]:
    """{table title: rows} for one uploaded file, whatever its format."""
    lower = filename.lower()
    if lower.endswith(".xlsx"):
        return xlsx.read_workbook(data)
    if lower.endswith(".docx"):
        return docx_read.read_tables(data)
    if lower.endswith(".doc"):
        # The old binary format is not a zip of XML and cannot be read here.
        # Saying so beats decoding it as text and reporting forty unreadable
        # rows from what is really one fixable problem.
        raise ValueError(
            "קובץ doc ישן אינו נתמך. יש לפתוח אותו ב-Word ולשמור "
            "בפורמט docx (קובץ ← שמירה בשם ← Word Document).")
    text = data.decode("utf-8-sig", errors="replace")
    if lower.endswith(".json") or text.lstrip().startswith("{"):
        return {"__json__": json.loads(text)}
    # Excel writes semicolon-separated CSV in some locales; sniff both.
    sample = text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    if "\t" in sample and sample.count("\t") > sample.count(delimiter):
        delimiter = "\t"
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return _tables_from_rows(rows)


def read_files(files: list[tuple[str, bytes]], name: str = "",
               year: str = "") -> tuple[spec_mod.SchoolSpec | None,
                                        ImportReport]:
    """Build one school from however many files the administrator uploaded.

    A file may be a whole exported school (JSON), a workbook with a sheet per
    table, or a CSV holding one table or several separated by `##` headings.
    Later files win on a table both of them carry, which is what makes
    "staff list" plus "constraints list" work as two uploads.
    """
    grid = spec_mod.Grid()
    reader = _Reader(grid)
    tables: dict[str, list[list[str]]] = {}

    for filename, data in files:
        try:
            found = _rows_from_bytes(filename, data)
        except ValueError as exc:
            reader.report.errors.append(f"{filename}: {exc}")
            continue
        except Exception as exc:                       # noqa: BLE001
            reader.report.errors.append(
                f"{filename}: הקובץ אינו קריא ({exc})")
            continue
        if "__json__" in found:
            raw = found["__json__"]
            # A full backup carries the school, its timetable and its rule
            # settings; a bare school definition carries only the first.  The
            # backup is the shape this program itself exports, so it has to
            # be the shape it can read back.
            backup = raw.get("school") if isinstance(raw, dict) else None
            try:
                spec = spec_mod.from_dict(backup or raw)
            except Exception as exc:                   # noqa: BLE001
                reader.report.errors.append(
                    f"{filename}: קובץ JSON שאינו הגדרת בית ספר ({exc})")
                continue
            reader.report.notes.append(
                f"{filename}: נטען גיבוי מלא" if backup
                else f"{filename}: נטענה הגדרת בית ספר שלמה")
            if backup:
                reader.report.restored = {
                    "timetable": raw.get("timetable"),
                    "settings": raw.get("settings")}
            for problem in spec.problems():
                reader.report.errors.append(problem)
            return (spec if reader.report.ok else None), reader.report
        for title, rows in found.items():
            section = _section_of(title) or _sniff(rows)
            if section is None:
                if any(rows):
                    reader.report.warnings.append(
                        f"{filename} / {title or 'ללא כותרת'}: לא זוהה סוג "
                        f"הטבלה — יש לסמן אותה בכותרת (למשל '## מורות') או "
                        f"לוודא ששמות העמודות תואמים לקובץ הדוגמה")
                continue
            tables[section] = rows
            reader.report.notes.append(
                f"{filename}: זוהתה טבלת {title or section} "
                f"({max(0, len(rows) - 1)} שורות)")

    # Order matters: days define the week, teachers and classes have to exist
    # before a lesson or a limit can refer to them.
    for section, read in ((SCHOOL, reader.read_school),
                          (DAYS, reader.read_days),
                          (TEACHERS, reader.read_teachers),
                          (CLASSES, reader.read_classes),
                          (LESSONS, reader.read_lessons),
                          (LIMITS, reader.read_limits)):
        if section in tables and tables[section]:
            read(tables[section])
        elif section in (TEACHERS, CLASSES, LESSONS):
            reader.report.errors.append(
                f"חסרה טבלת {SECTION_NAMES[section][0]} — "
                f"יש להוריד את קובץ הדוגמה ולהשוות אליו")

    spec = reader.build(name, year)
    return (spec if reader.report.ok else None), reader.report
