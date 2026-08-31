# -*- coding: utf-8 -*-
"""The timetable as a document that can be edited.

A solved timetable is `{meeting number: slot}`, and meeting numbers come from
the position of a lesson in the subject table.  That is exactly the wrong
representation for editing: deleting a lesson renumbers every meeting after
it, so every open editor, every undo step and every saved anchor would be
pointing at the wrong lessons without saying so.

So the editable form is a list of **rows**, each with an id of its own that
nothing else can shift.  A row is one placeable lesson — the same thing the
solver calls a meeting — plus where it currently sits, or nothing if the user
has lifted it out of the grid.  Rows are the truth while editing; the school
specification stays the statement of what *ought* to be taught, and
`coverage()` is the comparison between the two.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Iterable

import school
from model import DAYS, PERIODS_PER_DAY, Meeting, Timetable, expand
from app import constraints as cat
from app import evaluate as ev
from app import spec as spec_mod


@dataclass
class Row:
    """One placeable lesson, and where it is."""
    uid: int
    klass: str | None
    subject: str
    teachers: list[str]
    length: int = 1
    day: int | None = None
    period: int | None = None
    #: A row the administrator has nailed down: alternatives will not offer
    #: to move it and a re-solve is asked to keep it where it is.
    locked: bool = False

    @property
    def placed(self) -> bool:
        return self.day is not None and self.period is not None

    def slots(self) -> list[tuple[int, int]]:
        if not self.placed:
            return []
        return [(self.day, self.period + i) for i in range(self.length)]

    def to_dict(self) -> dict:
        return {"uid": self.uid, "klass": self.klass, "subject": self.subject,
                "teachers": list(self.teachers), "length": self.length,
                "day": self.day, "period": self.period, "locked": self.locked}


@dataclass
class Document:
    """A whole timetable, mid-edit."""
    rows: list[Row] = field(default_factory=list)
    #: Where the rows sat when the school last approved the timetable.  What
    #: `--anchor` is on the command line: the thing a repair stays near.
    approved: dict[int, tuple[int, int]] = field(default_factory=dict)
    _next_uid: int = 1

    # ------------------------------------------------------------ building

    @classmethod
    def from_timetable(cls, tt: Timetable) -> "Document":
        doc = cls()
        for mid, mt in enumerate(tt.meetings):
            slot = tt.placement.get(mid)
            doc.rows.append(Row(uid=doc._take_uid(), klass=mt.klass,
                                subject=mt.subject, teachers=list(mt.teachers),
                                length=mt.length,
                                day=slot[0] if slot else None,
                                period=slot[1] if slot else None))
        return doc

    @classmethod
    def blank(cls, spec: spec_mod.SchoolSpec) -> "Document":
        """Every lesson the school requires, none of them placed yet."""
        doc = cls()
        for mt in expand(spec.requirements()):
            doc.rows.append(Row(uid=doc._take_uid(), klass=mt.klass,
                                subject=mt.subject, teachers=list(mt.teachers),
                                length=mt.length))
        return doc

    def _take_uid(self) -> int:
        uid, self._next_uid = self._next_uid, self._next_uid + 1
        return uid

    def copy(self) -> "Document":
        return Document(rows=[Row(**r.to_dict()) for r in self.rows],
                        approved=dict(self.approved), _next_uid=self._next_uid)

    def row(self, uid: int) -> Row:
        for r in self.rows:
            if r.uid == uid:
                return r
        raise KeyError(f"אין שיעור מספר {uid}")

    # ------------------------------------------- the engine's view of it

    def meetings(self) -> list[Meeting]:
        """The rows as the engine's meetings, numbered by position.

        The numbering is regenerated on every call and never stored, which is
        the whole point: row ids are stable, meeting numbers are not, and
        nothing outside this method is allowed to depend on them.
        """
        return [Meeting(i, r.klass, r.subject, tuple(r.teachers), r.length)
                for i, r in enumerate(self.rows)]

    def placement(self) -> dict[int, tuple[int, int]]:
        return {i: (r.day, r.period)
                for i, r in enumerate(self.rows) if r.placed}

    def anchor(self) -> dict[int, tuple[int, int]]:
        """The approved placement, keyed the way the solver wants it."""
        index = {r.uid: i for i, r in enumerate(self.rows)}
        return {index[uid]: slot for uid, slot in self.approved.items()
                if uid in index}

    def as_timetable(self) -> Timetable:
        return Timetable(self.placement(), self.meetings())

    def mark_approved(self) -> None:
        self.approved = {r.uid: (r.day, r.period) for r in self.rows
                         if r.placed}

    # -------------------------------------------------------- occupancy

    def at(self, klass: str | None, day: int, period: int) -> Row | None:
        for r in self.rows:
            if r.klass == klass and (day, period) in r.slots():
                return r
        return None

    def teacher_at(self, teacher: str, day: int,
                   period: int) -> Row | None:
        for r in self.rows:
            if teacher in r.teachers and (day, period) in r.slots():
                return r
        return None

    def class_day(self, klass: str, day: int) -> list[Row]:
        return sorted((r for r in self.rows
                       if r.klass == klass and r.placed and r.day == day),
                      key=lambda r: r.period)

    # ------------------------------------------------------------- edits
    # Each returns the changed row.  None of them checks a rule: the caller
    # evaluates the result and decides what to do about it, which is what
    # lets the screen show a conflict rather than refuse the edit.

    def move(self, uid: int, day: int | None, period: int | None) -> Row:
        r = self.row(uid)
        r.day, r.period = day, period
        return r

    def unplace(self, uid: int) -> Row:
        return self.move(uid, None, None)

    def swap(self, uid_a: int, uid_b: int) -> tuple[Row, Row]:
        a, b = self.row(uid_a), self.row(uid_b)
        (a.day, a.period), (b.day, b.period) = ((b.day, b.period),
                                                (a.day, a.period))
        return a, b

    def exchange(self, uid: int, day: int, period: int,
                 partners: list[int]) -> Row:
        """Move a lesson somewhere occupied, and rehouse what was there.

        The general form of a swap, and the only form class contiguity
        allows: a double period trading places with two singles displaces
        *two* lessons, and both have to land in the slots it vacated or the
        class's day changes length.  With no partners this is a plain move,
        which is what makes it the single entry point for "take this option".
        """
        row = self.row(uid)
        home = (row.day, row.period)
        row.day, row.period = day, period
        if not partners:
            return row
        if home[0] is None:
            raise KeyError("שיעור שאינו משובץ אינו יכול להתחלף עם אחר")
        at = home[1]
        for other in sorted((self.row(u) for u in partners),
                            key=lambda r: (r.day, r.period)):
            other.day, other.period = home[0], at
            at += other.length
        return row

    def retitle(self, uid: int, klass: str | None = None,
                subject: str | None = None,
                teachers: list[str] | None = None) -> Row:
        """Change what a lesson *is* — its class, subject or teachers."""
        r = self.row(uid)
        if klass is not None:
            r.klass = klass or None
        if subject is not None:
            r.subject = subject
        if teachers is not None:
            r.teachers = list(teachers)
        return r

    def add(self, klass: str | None, subject: str, teachers: list[str],
            length: int = 1, day: int | None = None,
            period: int | None = None) -> Row:
        r = Row(uid=self._take_uid(), klass=klass or None, subject=subject,
                teachers=list(teachers), length=length, day=day, period=period)
        self.rows.append(r)
        return r

    def remove(self, uid: int) -> Row:
        r = self.row(uid)
        self.rows.remove(r)
        self.approved.pop(uid, None)
        return r

    def set_locked(self, uid: int, locked: bool) -> Row:
        r = self.row(uid)
        r.locked = bool(locked)
        return r

    # ------------------------------------------------------- persistence

    def to_dict(self) -> dict:
        return {"rows": [r.to_dict() for r in self.rows],
                "approved": {str(k): list(v) for k, v in self.approved.items()},
                "next_uid": self._next_uid}

    @classmethod
    def from_dict(cls, raw: dict) -> "Document":
        rows = [Row(uid=int(r["uid"]), klass=r.get("klass"),
                    subject=r["subject"], teachers=list(r.get("teachers") or []),
                    length=int(r.get("length", 1)),
                    day=None if r.get("day") is None else int(r["day"]),
                    period=None if r.get("period") is None else int(r["period"]),
                    locked=bool(r.get("locked")))
                for r in raw.get("rows") or []]
        return cls(rows=rows,
                   approved={int(k): tuple(v) for k, v
                             in (raw.get("approved") or {}).items()},
                   _next_uid=int(raw.get("next_uid")
                                 or (max((r.uid for r in rows), default=0) + 1)))


# ---------------------------------------------------------------- coverage

def coverage(spec: spec_mod.SchoolSpec, doc: Document) -> list[dict]:
    """Where the grid and the subject table disagree.

    Manual editing can add an hour or drop one, and the school's own table is
    what says whether that was intended.  Reported rather than forbidden: an
    administrator who deletes a lesson on purpose should see one clear line
    saying the table now expects one hour less, not a refusal.
    """
    want: dict[tuple, int] = {}
    for lesson in spec.lessons:
        key = (lesson.klass, lesson.subject, tuple(lesson.teachers))
        want[key] = want.get(key, 0) + lesson.hours
    have: dict[tuple, int] = {}
    for r in doc.rows:
        key = (r.klass, r.subject, tuple(r.teachers))
        have[key] = have.get(key, 0) + r.length

    out = []
    for key in sorted(set(want) | set(have), key=lambda k: (str(k[0]), k[1])):
        klass, subject, teachers = key
        a, b = want.get(key, 0), have.get(key, 0)
        if a == b:
            continue
        where = f"{subject} בכיתה {klass}" if klass else subject
        who = "/".join(teachers) or "—"
        out.append({
            "klass": klass, "subject": subject, "teachers": list(teachers),
            "required": a, "present": b,
            "message": (f"{where} ({who}): במערכת {b} שעות, בטבלת השיעורים {a}"
                        f" ({b - a:+d})"),
        })
    return out


# -------------------------------------------------------------- evaluation

def review(spec: spec_mod.SchoolSpec, settings: cat.Settings,
           doc: Document, require_complete: bool = True) -> dict:
    """Everything the screens need to say about a timetable, in one pass."""
    meetings = doc.meetings()
    evaluation = ev.evaluate(spec, settings, doc.placement(), meetings,
                             spec.requirements(), anchor=doc.anchor(),
                             require_complete=require_complete)
    labels = cat.by_label(spec)
    out = evaluation.to_dict()
    out["coverage"] = coverage(spec, doc)
    out["penalty_detail"] = [{
        "label": label, "cost": cost,
        "constraint": labels[label].id if label in labels else "",
        "title": labels[label].title if label in labels else label,
        "explanation": labels[label].explanation if label in labels else "",
    } for label, cost in sorted(evaluation.penalties.items(),
                                key=lambda kv: -kv[1])]
    # A row id is what the screens address; the evaluator speaks in meeting
    # numbers, so the translation happens once, here.
    by_index = {i: r.uid for i, r in enumerate(doc.rows)}
    for item in out["unplaced"]:
        item["uid"] = by_index.get(item.pop("id"))
    return out
