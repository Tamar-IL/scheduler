# -*- coding: utf-8 -*-
"""A whole school, as data.

`school.py` describes one school in Python.  A `SchoolSpec` describes any
school in JSON: the same fields, none of them hard-coded, all of them
round-trippable to disk and editable from the constraint screen.

The engine is not asked to change shape for this.  `app.activate` installs a
spec into the engine's globals and takes it out again, so `solver.py` and
`validate.py` keep reading exactly what they read before.  Everything here is
therefore about *representation*: what a school is, what it means for one to
be well-formed, and how it survives a trip through a file.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import model

# --------------------------------------------------------------- the week

#: A Sunday-to-Friday week with a shortened Friday: the default a new school
#: starts from, and the one the built-in example uses.
DEFAULT_DAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי"]
DEFAULT_PERIODS = [7, 7, 7, 7, 7, 4]


@dataclass
class Grid:
    """The week: what the days are called and how long each one runs."""
    days: list[str] = field(default_factory=lambda: list(DEFAULT_DAYS))
    periods_per_day: list[int] = field(
        default_factory=lambda: list(DEFAULT_PERIODS))
    #: A day counts as "long" for a teacher at this many hours.
    long_day_hours: int = 6
    #: "Finishing late" means teaching at or beyond this period.
    late_period: int = 6

    def index(self, day: str | int) -> int:
        """Day names are what a spreadsheet holds; indices are what the
        engine holds.  Everything crossing that boundary comes through here.
        """
        if isinstance(day, int):
            return day
        name = str(day).strip()
        if name in self.days:
            return self.days.index(name)
        if name.isdigit():
            return int(name)
        raise KeyError(f"אין יום בשם {day!r} בשבוע של בית הספר")

    def name(self, day: int) -> str:
        return self.days[day]


# ------------------------------------------------------------- the people

@dataclass
class TeacherSpec:
    name: str
    declared: int | None = None
    off_fixed: list[int] = field(default_factory=list)
    off_choice: list[int] = field(default_factory=list)
    max_per_day: int | None = None
    min_per_day: int = 3
    forbidden_periods: list[int] = field(default_factory=list)
    #: {day: last period she may teach}, keyed by day index.
    latest_period_on: dict[int, int] = field(default_factory=dict)
    exact_working_days: int | None = None
    max_long_days: int | None = None
    min_late_days: int = 0
    max_late_days: int | None = None
    max_windows: int = 1
    homeroom: str | None = None

    def to_model(self) -> model.Teacher:
        return model.Teacher(
            name=self.name, declared=self.declared,
            off_fixed=tuple(self.off_fixed), off_choice=tuple(self.off_choice),
            max_per_day=self.max_per_day, min_per_day=self.min_per_day,
            forbidden_periods=tuple(self.forbidden_periods),
            latest_period_on={int(k): int(v)
                              for k, v in self.latest_period_on.items()},
            exact_working_days=self.exact_working_days,
            max_long_days=self.max_long_days,
            min_late_days=self.min_late_days,
            max_late_days=self.max_late_days,
            max_windows=self.max_windows, homeroom=self.homeroom)


@dataclass
class ClassSpec:
    name: str
    #: {day index: (min hours, max hours)}
    day_span: dict[int, tuple[int, int]] = field(default_factory=dict)
    #: {day index: the exact figure the school stated}, priced not enforced.
    preferred: dict[int, int] = field(default_factory=dict)

    def to_model(self) -> model.SchoolClass:
        return model.SchoolClass(
            name=self.name,
            day_span={int(k): tuple(v) for k, v in self.day_span.items()},
            preferred={int(k): int(v) for k, v in self.preferred.items()})


@dataclass
class LessonSpec:
    """One row of the subject table: who teaches what, to whom, how often."""
    klass: str | None
    subject: str
    teachers: list[str]
    hours: int
    #: An explicit meeting split.  Empty means "derive it from `doubles`",
    #: which is what an imported file normally wants.
    pattern: list[int] = field(default_factory=list)


# -------------------------------------------------------------- the rules
# Everything `school.py` states as a module constant, per school.  The
# defaults are the neutral ones: a brand-new school gets the universal rules
# (contiguity, windows, day lengths, loads) and none of the local ones.

@dataclass
class Rules:
    # -- teacher-shaped
    distinct_days_off: list[list[str]] = field(default_factory=list)
    late_start_and_end: list[str] = field(default_factory=list)
    late_start_only: dict[str, list[int]] = field(default_factory=dict)
    no_spread_preference: list[str] = field(default_factory=list)
    no_window_teachers: list[str] = field(default_factory=list)
    subject_ends_day: list[list[str]] = field(default_factory=list)
    open_on_day: dict[str, list[int]] = field(default_factory=dict)
    late_start_away_from_off: list[str] = field(default_factory=list)
    # -- class-shaped
    prefer_one_short_day: list[str] = field(default_factory=list)
    open_own_class: dict[str, list[int]] = field(default_factory=dict)
    #: [class, day, period, subject]
    pinned: list[list[Any]] = field(default_factory=list)
    #: [class, day, last period]
    day_ends_at: list[list[Any]] = field(default_factory=list)
    # -- subject-shaped
    not_first_period: list[str] = field(default_factory=list)
    subject_periods: dict[str, list[int]] = field(default_factory=dict)
    #: {"class|subject": [first, last]}
    class_subject_periods: dict[str, list[int]] = field(default_factory=dict)
    #: {"class|subject": how many meetings should open a day}
    subject_at_day_start: dict[str, int] = field(default_factory=dict)
    #: {subject: [day, [exempt classes]]}
    subject_fixed_day: dict[str, list[Any]] = field(default_factory=dict)
    #: [[subject, [classes], [[day, …], …]], …]
    subject_day_groups: list[list[Any]] = field(default_factory=list)
    doubles: dict[str, Any] = field(default_factory=dict)
    # -- school-wide
    short_day_banned_periods: list[int] = field(default_factory=lambda: [6, 7])
    opening_subject: str = ""
    fifth_day: dict[str, bool] = field(default_factory=lambda: {
        "late_start": True, "late_end": True,
        "window": False, "longest": True})
    homeroom_max_windows: int = 2
    no_seventh_on: list[int] = field(default_factory=list)
    window_on_longest_day: bool = True
    window_majority: bool = True
    homeroom_education_hours: int = 0
    #: {class: the weekly total its source file stated}.  Advisory: where it
    #: disagrees with the lesson rows, phase 0 reports the gap and the rows
    #: win.  Empty means the school stated no such summary.
    class_declared_hours: dict[str, int] = field(default_factory=dict)


@dataclass
class SchoolSpec:
    """Everything the engine needs to know about one school."""
    name: str = "בית ספר חדש"
    year: str = ""
    grid: Grid = field(default_factory=Grid)
    teachers: list[TeacherSpec] = field(default_factory=list)
    classes: list[ClassSpec] = field(default_factory=list)
    lessons: list[LessonSpec] = field(default_factory=list)
    rules: Rules = field(default_factory=Rules)

    # ------------------------------------------------------------ lookups

    @property
    def teacher_names(self) -> list[str]:
        return [t.name for t in self.teachers]

    @property
    def class_names(self) -> list[str]:
        return [c.name for c in self.classes]

    @property
    def subjects(self) -> list[str]:
        seen: list[str] = []
        for lesson in self.lessons:
            if lesson.subject not in seen:
                seen.append(lesson.subject)
        return seen

    def teacher(self, name: str) -> TeacherSpec | None:
        return next((t for t in self.teachers if t.name == name), None)

    def klass(self, name: str) -> ClassSpec | None:
        return next((c for c in self.classes if c.name == name), None)

    def homeroom_of(self, klass: str) -> str | None:
        return next((t.name for t in self.teachers if t.homeroom == klass),
                    None)

    def copy(self) -> "SchoolSpec":
        return from_dict(to_dict(self))

    # -------------------------------------------------------- consistency

    def problems(self) -> list[str]:
        """Everything wrong with the school *before* any timetabling.

        These are the mistakes a spreadsheet makes — a lesson naming a
        teacher who is not on the staff list, a day index off the end of the
        week — as opposed to the counting arguments in `checks.py`, which are
        about whether a correct school can be timetabled at all.
        """
        bad: list[str] = []
        ndays = len(self.grid.days)
        names, classes = set(self.teacher_names), set(self.class_names)
        if len(names) != len(self.teachers):
            bad.append("יש שמות מורות כפולים ברשימת הצוות")
        if len(classes) != len(self.classes):
            bad.append("יש שמות כיתות כפולים ברשימת הכיתות")
        if not self.teachers:
            bad.append("לא הוגדרו מורות")
        if not self.classes:
            bad.append("לא הוגדרו כיתות")
        if not self.lessons:
            bad.append("לא הוגדר אף שיעור")

        def day_ok(d, where):
            if not isinstance(d, int) or not 0 <= d < ndays:
                bad.append(f"{where}: יום {d} אינו קיים בשבוע של בית הספר")

        for t in self.teachers:
            for d in list(t.off_fixed) + list(t.off_choice):
                day_ok(d, t.name)
            for d in t.latest_period_on:
                day_ok(int(d), t.name)
            if t.homeroom and t.homeroom not in classes:
                bad.append(f"{t.name}: מחנכת של כיתה {t.homeroom} שאינה קיימת")
            if set(t.off_fixed) & set(t.off_choice):
                bad.append(f"{t.name}: אותו יום מופיע גם כחופש קבוע וגם כבחירה")
            for p in t.forbidden_periods:
                if p < 1 or p > max(self.grid.periods_per_day):
                    bad.append(f"{t.name}: שעה אסורה {p} מחוץ לטווח היום")
        for c in self.classes:
            for d in range(ndays):
                if d not in c.day_span:
                    bad.append(f"כיתה {c.name}: לא הוגדר אורך יום ב"
                               f"{self.grid.days[d]}")
                    continue
                lo, hi = c.day_span[d]
                if lo > hi:
                    bad.append(f"כיתה {c.name} ב{self.grid.days[d]}: "
                               f"מינימום {lo} גדול מהמקסימום {hi}")
                if hi > self.grid.periods_per_day[d]:
                    bad.append(f"כיתה {c.name} ב{self.grid.days[d]}: "
                               f"{hi} שעות ביום של "
                               f"{self.grid.periods_per_day[d]} שעות")
        for lesson in self.lessons:
            where = f"{lesson.subject} בכיתה {lesson.klass or '—'}"
            if lesson.klass is not None and lesson.klass not in classes:
                bad.append(f"{lesson.subject}: כיתה {lesson.klass} אינה קיימת")
            if not lesson.teachers:
                bad.append(f"{where}: לא צוינה מורה")
            for t in lesson.teachers:
                if t not in names:
                    bad.append(f"{where}: המורה {t} אינה ברשימת הצוות")
            if lesson.hours <= 0:
                bad.append(f"{where}: {lesson.hours} שעות")
            if lesson.pattern and sum(lesson.pattern) != lesson.hours:
                bad.append(f"{where}: פיצול {lesson.pattern} אינו מסתכם "
                           f"ב-{lesson.hours} שעות")
        homerooms = [t.homeroom for t in self.teachers if t.homeroom]
        for c in sorted(set(homerooms)):
            if homerooms.count(c) > 1:
                bad.append(f"לכיתה {c} יש יותר ממחנכת אחת")
        return bad

    # ------------------------------------------------------- requirements

    def requirements(self) -> list[model.Requirement]:
        """The lesson rows, split into the meetings the solver places."""
        out = []
        for lesson in self.lessons:
            pattern = (tuple(lesson.pattern) if lesson.pattern
                       else self.pattern(lesson.subject, lesson.hours))
            out.append(model.Requirement(lesson.klass, lesson.subject,
                                         tuple(lesson.teachers), lesson.hours,
                                         pattern))
        return out

    def pattern(self, subject: str, hours: int) -> tuple[int, ...]:
        """Singles and doubles, from the school's own doubling ladder."""
        for threshold, doubles in self.rules.doubles.get(subject, ()):
            if hours >= threshold:
                n = min(doubles, hours // 2)
                return (2,) * n + (1,) * (hours - 2 * n)
        return (1,) * hours


# ----------------------------------------------------------- (de)serialise
# Plain functions rather than methods so the shapes stay obvious: a spec is
# JSON, and every key here is a name an imported file or the constraint
# screen may legitimately produce.

def to_dict(spec: SchoolSpec) -> dict:
    return {
        "name": spec.name,
        "year": spec.year,
        "grid": {"days": list(spec.grid.days),
                 "periods_per_day": list(spec.grid.periods_per_day),
                 "long_day_hours": spec.grid.long_day_hours,
                 "late_period": spec.grid.late_period},
        "teachers": [{
            "name": t.name, "declared": t.declared,
            "off_fixed": list(t.off_fixed), "off_choice": list(t.off_choice),
            "max_per_day": t.max_per_day, "min_per_day": t.min_per_day,
            "forbidden_periods": list(t.forbidden_periods),
            "latest_period_on": {str(k): v
                                 for k, v in t.latest_period_on.items()},
            "exact_working_days": t.exact_working_days,
            "max_long_days": t.max_long_days,
            "min_late_days": t.min_late_days,
            "max_late_days": t.max_late_days,
            "max_windows": t.max_windows, "homeroom": t.homeroom,
        } for t in spec.teachers],
        "classes": [{
            "name": c.name,
            "day_span": {str(k): list(v) for k, v in c.day_span.items()},
            "preferred": {str(k): v for k, v in c.preferred.items()},
        } for c in spec.classes],
        "lessons": [{
            "klass": lesson.klass, "subject": lesson.subject,
            "teachers": list(lesson.teachers), "hours": lesson.hours,
            "pattern": list(lesson.pattern),
        } for lesson in spec.lessons],
        "rules": _jsonable(copy.deepcopy(spec.rules.__dict__)),
    }


def _jsonable(value):
    """Tuples survive a round trip as lists; keys survive as strings."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _int_keys(d: dict | None) -> dict:
    return {int(k): v for k, v in (d or {}).items()}


def from_dict(raw: dict) -> SchoolSpec:
    g = raw.get("grid") or {}
    grid = Grid(days=list(g.get("days") or DEFAULT_DAYS),
                periods_per_day=list(g.get("periods_per_day")
                                     or DEFAULT_PERIODS),
                long_day_hours=int(g.get("long_day_hours", 6)),
                late_period=int(g.get("late_period", 6)))
    teachers = [TeacherSpec(
        name=t["name"], declared=t.get("declared"),
        off_fixed=[int(d) for d in t.get("off_fixed") or []],
        off_choice=[int(d) for d in t.get("off_choice") or []],
        max_per_day=t.get("max_per_day"),
        min_per_day=int(t.get("min_per_day", 3)),
        forbidden_periods=[int(p) for p in t.get("forbidden_periods") or []],
        latest_period_on=_int_keys(t.get("latest_period_on")),
        exact_working_days=t.get("exact_working_days"),
        max_long_days=t.get("max_long_days"),
        min_late_days=int(t.get("min_late_days", 0)),
        max_late_days=t.get("max_late_days"),
        max_windows=int(t.get("max_windows", 1)),
        homeroom=t.get("homeroom") or None) for t in raw.get("teachers") or []]
    classes = [ClassSpec(
        name=c["name"],
        day_span={int(k): tuple(v)
                  for k, v in (c.get("day_span") or {}).items()},
        preferred={int(k): int(v)
                   for k, v in (c.get("preferred") or {}).items()})
        for c in raw.get("classes") or []]
    lessons = [LessonSpec(
        klass=lesson.get("klass"), subject=lesson["subject"],
        teachers=list(lesson.get("teachers") or []),
        hours=int(lesson["hours"]),
        pattern=[int(x) for x in lesson.get("pattern") or []])
        for lesson in raw.get("lessons") or []]
    rules = Rules()
    for key, value in (raw.get("rules") or {}).items():
        if hasattr(rules, key):
            setattr(rules, key, copy.deepcopy(value))
    # The doubling ladder is read as tuples so it stays hashable and an
    # accidental mutation cannot travel back into the stored spec.
    rules.doubles = {k: tuple(tuple(x) for x in v)
                     for k, v in (rules.doubles or {}).items()}
    rules.fifth_day = {k: bool(v) for k, v in (rules.fifth_day or {}).items()}
    return SchoolSpec(name=raw.get("name", ""), year=raw.get("year", ""),
                      grid=grid, teachers=teachers, classes=classes,
                      lessons=lessons, rules=rules)
