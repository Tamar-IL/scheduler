# -*- coding: utf-8 -*-
"""Install a `SchoolSpec` into the engine, and take it out again.

The engine reads its school from module globals — `school.TEACHERS`,
`model.DAYS`, and about thirty more.  That is a deliberate design in a
program written to prove one timetable feasible, and rewriting it into
dependency injection would touch every line of `solver.py` for no gain in
what the solver can express.  So the platform does the other thing: it swaps
the globals for the duration of a call and restores them afterwards, which is
exactly what the test suite has always done with `tiny_school`.

Two consequences worth stating plainly:

* **One school at a time per process.**  `activated()` takes a lock, so
  concurrent requests queue instead of corrupting each other's globals.  The
  server runs solves one at a time anyway — CP-SAT wants the cores.
* **The list objects are rewritten in place.**  `from model import DAYS`
  binds the list, not the name, so rebinding `model.DAYS` would leave five
  modules holding last school's week.  `model.configure_grid` writes through
  the existing lists; the scalars (`LONG_DAY_HOURS`, `LATE_PERIOD`) *are*
  bound by value, so they are re-set in every module that imported them.
"""
from __future__ import annotations

import contextlib
import threading

import checks
import model
import polish
import report
import school
import solver
import validate
from app import spec as spec_mod

#: Every engine module that might hold a by-value copy of a grid scalar.
_ENGINE_MODULES = (model, checks, solver, validate, report, polish, school)

#: The by-value scalars.  Rewriting `model.LATE_PERIOD` alone is not enough.
_SCALARS = ("LONG_DAY_HOURS", "LATE_PERIOD")

#: Every `school` attribute the engine reads.  Keeping the list here rather
#: than deriving it from `dir(school)` is on purpose: a new module-level
#: constant in `school.py` that nobody adds here would silently leak from one
#: school into the next, and `tests/test_platform.py` asserts the list is
#: complete by diffing it against the module.
_SCHOOL_ATTRS = (
    "TEACHERS", "CLASSES", "BY_NAME",
    "DISTINCT_DAYS_OFF", "LATE_START_AND_END", "LATE_START_ONLY",
    "NO_SPREAD_PREFERENCE", "PREFER_ONE_SHORT_DAY",
    "NOT_FIRST_PERIOD", "NO_WINDOW_TEACHERS", "SUBJECT_ENDS_DAY",
    "SUBJECT_PERIODS", "CLASS_SUBJECT_PERIODS", "SUBJECT_AT_DAY_START",
    "SHORT_DAY_BANNED_PERIODS", "PINNED", "DAY_ENDS_AT", "OPEN_ON_DAY",
    "LATE_START_AWAY_FROM_OFF", "OPENING_SUBJECT", "OPEN_OWN_CLASS",
    "FIFTH_DAY", "HOMEROOM_MAX_WINDOWS", "NO_SEVENTH_ON",
    "SUBJECT_DAY_GROUPS", "SUBJECT_FIXED_DAY", "WINDOW_ON_LONGEST_DAY",
    "WINDOW_MAJORITY", "HOMEROOM_EDUCATION_HOURS", "DOUBLE_PERIODS",
    "NON_TEACHING", "requirements", "CLASS_DECLARED_HOURS",
)

_LOCK = threading.RLock()


def _snapshot() -> dict:
    return {
        "days": list(model.DAYS),
        "periods": list(model.PERIODS_PER_DAY),
        "scalars": {(m.__name__, s): getattr(m, s)
                    for m in _ENGINE_MODULES for s in _SCALARS
                    if hasattr(m, s)},
        "school": {a: getattr(school, a) for a in _SCHOOL_ATTRS
                   if hasattr(school, a)},
    }


def _restore(saved: dict) -> None:
    model.configure_grid(saved["days"], saved["periods"])
    for (mod_name, attr), value in saved["scalars"].items():
        setattr(next(m for m in _ENGINE_MODULES if m.__name__ == mod_name),
                attr, value)
    for attr, value in saved["school"].items():
        setattr(school, attr, value)


def _pair(key: str) -> tuple[str, str]:
    """Split a "class|subject" composite key back into its two halves.

    JSON has no tuple keys, so the two rules keyed by (class, subject) are
    stored with a separator.  A subject containing "|" would be ambiguous;
    the importer rejects one, so the split is safe here.
    """
    klass, _, subject = key.partition("|")
    return klass, subject


def install(spec: spec_mod.SchoolSpec) -> None:
    """Point the engine at this school.  Prefer `activated()`."""
    g, r = spec.grid, spec.rules
    model.configure_grid(g.days, g.periods_per_day)
    for m in _ENGINE_MODULES:
        for attr, value in (("LONG_DAY_HOURS", g.long_day_hours),
                            ("LATE_PERIOD", g.late_period)):
            if hasattr(m, attr):
                setattr(m, attr, value)

    teachers = [t.to_model() for t in spec.teachers]
    school.TEACHERS = teachers
    school.CLASSES = [c.to_model() for c in spec.classes]
    school.BY_NAME = {t.name: t for t in teachers}

    school.DISTINCT_DAYS_OFF = [tuple(p) for p in r.distinct_days_off]
    school.LATE_START_AND_END = list(r.late_start_and_end)
    school.LATE_START_ONLY = {k: tuple(v) for k, v in r.late_start_only.items()}
    school.NO_SPREAD_PREFERENCE = set(r.no_spread_preference)
    school.PREFER_ONE_SHORT_DAY = list(r.prefer_one_short_day)
    school.NOT_FIRST_PERIOD = set(r.not_first_period)
    school.NO_WINDOW_TEACHERS = tuple(r.no_window_teachers)
    school.SUBJECT_ENDS_DAY = tuple(tuple(p) for p in r.subject_ends_day)
    school.SUBJECT_PERIODS = {k: tuple(v)
                              for k, v in r.subject_periods.items()}
    school.CLASS_SUBJECT_PERIODS = {_pair(k): tuple(v) for k, v
                                    in r.class_subject_periods.items()}
    school.SUBJECT_AT_DAY_START = {_pair(k): int(v) for k, v
                                   in r.subject_at_day_start.items()}
    school.SHORT_DAY_BANNED_PERIODS = tuple(r.short_day_banned_periods)
    school.PINNED = {(k, int(d), int(p)): s for k, d, p, s in r.pinned}
    school.DAY_ENDS_AT = {(k, int(d)): int(p) for k, d, p in r.day_ends_at}
    school.OPEN_ON_DAY = {k: tuple(int(d) for d in v)
                          for k, v in r.open_on_day.items()}
    school.LATE_START_AWAY_FROM_OFF = tuple(r.late_start_away_from_off)
    school.OPENING_SUBJECT = r.opening_subject
    school.OPEN_OWN_CLASS = {k: (int(v[0]), int(v[1]))
                             for k, v in r.open_own_class.items()}
    school.FIFTH_DAY = dict(r.fifth_day)
    school.HOMEROOM_MAX_WINDOWS = int(r.homeroom_max_windows)
    school.NO_SEVENTH_ON = {int(d) for d in r.no_seventh_on}
    school.SUBJECT_DAY_GROUPS = [
        (s, tuple(cs), tuple(tuple(int(d) for d in o) for o in opts))
        for s, cs, opts in r.subject_day_groups]
    school.SUBJECT_FIXED_DAY = {s: (int(v[0]), tuple(v[1]))
                                for s, v in r.subject_fixed_day.items()}
    school.WINDOW_ON_LONGEST_DAY = bool(r.window_on_longest_day)
    school.WINDOW_MAJORITY = bool(r.window_majority)
    school.HOMEROOM_EDUCATION_HOURS = int(r.homeroom_education_hours)
    school.DOUBLE_PERIODS = {k: tuple(tuple(x) for x in v)
                             for k, v in r.doubles.items()}
    # Lessons with no class are carried in `spec.lessons` like any other, so
    # the engine's separate list of them is emptied rather than rebuilt.
    school.NON_TEACHING = []
    # The class-hours summary row is advisory and school-specific.  A school
    # that stated one gets its own; one that did not gets no comparison
    # rather than the previous school's.
    school.CLASS_DECLARED_HOURS = dict(r.class_declared_hours)
    # `school.requirements()` reads the module's own hard-coded subject
    # table, which no longer describes the school now installed.  Every
    # engine entry point takes `reqs` as an argument, so the platform passes
    # `spec.requirements()` and never calls this — but leaving a function
    # that quietly answers about a different school is a trap, so it is
    # swapped too, and restored with everything else.
    school.requirements = spec.requirements


@contextlib.contextmanager
def activated(spec: spec_mod.SchoolSpec):
    """Run a block with the engine pointed at this school.

    Re-entrant: a nested `activated()` for the same or another school works,
    and each level restores what it found.
    """
    with _LOCK:
        saved = _snapshot()
        try:
            install(spec)
            yield spec
        finally:
            _restore(saved)


# ---------------------------------------------------------------- capture

def capture(name: str = "", year: str = "") -> spec_mod.SchoolSpec:
    """Read the school currently installed in the engine back out as a spec.

    This is how `school.py` — the one school the engine was written around —
    becomes an ordinary profile in the platform, and how the round-trip is
    tested: capture, serialise, reload, activate, and the requirements have
    to come out identical.
    """
    r = spec_mod.Rules(
        distinct_days_off=[list(p) for p in school.DISTINCT_DAYS_OFF],
        late_start_and_end=list(school.LATE_START_AND_END),
        late_start_only={k: list(v)
                         for k, v in school.LATE_START_ONLY.items()},
        no_spread_preference=sorted(school.NO_SPREAD_PREFERENCE),
        prefer_one_short_day=list(school.PREFER_ONE_SHORT_DAY),
        no_window_teachers=list(school.NO_WINDOW_TEACHERS),
        subject_ends_day=[list(p) for p in school.SUBJECT_ENDS_DAY],
        open_on_day={k: list(v) for k, v in school.OPEN_ON_DAY.items()},
        late_start_away_from_off=list(school.LATE_START_AWAY_FROM_OFF),
        open_own_class={k: list(v) for k, v in school.OPEN_OWN_CLASS.items()},
        pinned=[[k, d, p, s] for (k, d, p), s in school.PINNED.items()],
        day_ends_at=[[k, d, p] for (k, d), p in school.DAY_ENDS_AT.items()],
        not_first_period=sorted(school.NOT_FIRST_PERIOD),
        subject_periods={k: list(v)
                         for k, v in school.SUBJECT_PERIODS.items()},
        class_subject_periods={f"{k}|{s}": list(v) for (k, s), v
                               in school.CLASS_SUBJECT_PERIODS.items()},
        subject_at_day_start={f"{k}|{s}": v for (k, s), v
                              in school.SUBJECT_AT_DAY_START.items()},
        subject_fixed_day={s: [d, list(ex)] for s, (d, ex)
                           in school.SUBJECT_FIXED_DAY.items()},
        subject_day_groups=[[s, list(cs), [list(o) for o in opts]]
                            for s, cs, opts in school.SUBJECT_DAY_GROUPS],
        doubles={k: tuple(tuple(x) for x in v)
                 for k, v in school.DOUBLE_PERIODS.items()},
        short_day_banned_periods=list(school.SHORT_DAY_BANNED_PERIODS),
        opening_subject=school.OPENING_SUBJECT,
        fifth_day=dict(school.FIFTH_DAY),
        homeroom_max_windows=school.HOMEROOM_MAX_WINDOWS,
        no_seventh_on=sorted(school.NO_SEVENTH_ON),
        window_on_longest_day=school.WINDOW_ON_LONGEST_DAY,
        window_majority=school.WINDOW_MAJORITY,
        homeroom_education_hours=school.HOMEROOM_EDUCATION_HOURS,
        class_declared_hours=dict(school.CLASS_DECLARED_HOURS),
    )
    teachers = [spec_mod.TeacherSpec(
        name=t.name, declared=t.declared, off_fixed=list(t.off_fixed),
        off_choice=list(t.off_choice), max_per_day=t.max_per_day,
        min_per_day=t.min_per_day,
        forbidden_periods=list(t.forbidden_periods),
        latest_period_on=dict(t.latest_period_on),
        exact_working_days=t.exact_working_days,
        max_long_days=t.max_long_days, min_late_days=t.min_late_days,
        max_late_days=t.max_late_days, max_windows=t.max_windows,
        homeroom=t.homeroom) for t in school.TEACHERS]
    classes = [spec_mod.ClassSpec(name=c.name,
                                  day_span={d: tuple(v)
                                            for d, v in c.day_span.items()},
                                  preferred=dict(c.preferred))
               for c in school.CLASSES]
    lessons = [spec_mod.LessonSpec(klass=req.klass, subject=req.subject,
                                   teachers=list(req.teachers),
                                   hours=req.hours, pattern=[])
               for req in school.requirements()]
    # A requirement whose split the doubling ladder would not reproduce keeps
    # its own, so capture is lossless even for a hand-written pattern.
    out = spec_mod.SchoolSpec(
        name=name or 'בית הספר (נתוני המקור)', year=year,
        grid=spec_mod.Grid(days=list(model.DAYS),
                           periods_per_day=list(model.PERIODS_PER_DAY),
                           long_day_hours=model.LONG_DAY_HOURS,
                           late_period=model.LATE_PERIOD),
        teachers=teachers, classes=classes, lessons=lessons, rules=r)
    for lesson, req in zip(out.lessons, school.requirements()):
        if out.pattern(req.subject, req.hours) != req.pattern:
            lesson.pattern = list(req.pattern)
    return out
