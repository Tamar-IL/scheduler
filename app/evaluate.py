# -*- coding: utf-8 -*-
"""What a timetable costs, worked out in plain Python.

`solver.score()` answers the same question exactly, by pinning a placement
into a fresh CP-SAT model — and takes seconds to do it.  Editing needs the
answer forty times in a row: every candidate slot in "find alternatives" is
a whole timetable that has to be priced before it can be ranked, and every
drag of a lesson has to re-price the week before the mouse button is
released.  So this module re-derives the same penalties directly from a
placement, in one pass over the meetings.

It is a *second* implementation of the priced rules, which is a real cost —
so `tests/test_platform.py` pins it against `solver.score()` on the school's
own approved timetable, label by label.  Where the two could differ they are
made to agree here, not there: the solver is the authority.

The hard rules are not re-implemented.  `validate.violations()` already
re-derives them independently of the solver, and it is what this module
calls; a hard rule therefore has exactly one checker in the whole system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import checks
import school
import solver
import validate
from model import (DAYS, LATE_PERIOD, LONG_DAY_HOURS, PERIODS_PER_DAY,
                   Meeting, Timetable, full_days)
from app import constraints as cat
from app import spec as spec_mod


# ------------------------------------------------------------- the grids

@dataclass
class Grids:
    """Who is where, indexed the way every rule below wants to read it."""
    teacher: dict[str, dict[int, list[int]]]
    klass: dict[str, dict[int, list[int]]]

    def hours(self, teacher: str, day: int) -> int:
        return len(self.teacher[teacher][day])

    def gaps(self, teacher: str, day: int) -> int:
        busy = self.teacher[teacher][day]
        return (busy[-1] - busy[0] + 1 - len(busy)) if busy else 0

    def week_gaps(self, teacher: str) -> int:
        return sum(self.gaps(teacher, d) for d in range(len(DAYS)))

    def class_hours(self, klass: str, day: int) -> int:
        return len(self.klass[klass][day])

    def working_days(self, teacher: str) -> list[int]:
        return [d for d in range(len(DAYS)) if self.teacher[teacher][d]]

    def longest(self, teacher: str) -> int:
        return max((self.hours(teacher, d) for d in range(len(DAYS))),
                   default=0)


def grids(placement: dict[int, tuple[int, int]],
          meetings: list[Meeting]) -> Grids:
    teacher = {t.name: {d: [] for d in range(len(DAYS))}
               for t in school.TEACHERS}
    klass = {c.name: {d: [] for d in range(len(DAYS))} for c in school.CLASSES}
    for mid, (d, p) in placement.items():
        mt = meetings[mid]
        for q in range(p, p + mt.length):
            if mt.klass in klass:
                klass[mt.klass][d].append(q)
            for name in mt.teachers:
                if name in teacher:
                    teacher[name][d].append(q)
    for table in (teacher, klass):
        for days in table.values():
            for d in days:
                days[d].sort()
    return Grids(teacher, klass)


# ------------------------------------------------------------- the result

@dataclass
class Evaluation:
    #: [{"rule", "title", "message"}] — hard rules broken, from `validate`.
    violations: list[dict] = field(default_factory=list)
    #: Meetings with nowhere to be, after a delete or a half-finished drag.
    unplaced: list[dict] = field(default_factory=list)
    #: penalty label -> cost, the same breakdown the solver prints.
    penalties: dict[str, int] = field(default_factory=dict)
    total: int = 0
    #: Per-teacher and per-class figures the quality screen shows.
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations and not self.unplaced

    def to_dict(self) -> dict:
        return {"ok": self.ok, "violations": self.violations,
                "unplaced": self.unplaced, "penalties": self.penalties,
                "total": self.total, "metrics": self.metrics}


# ------------------------------------------------------------ the pricing

class _Pricer:
    """One pass over the placement, accumulating the priced rules.

    Written as a class only so the twenty rule methods can share the grids
    without threading six arguments through each of them.
    """

    def __init__(self, spec: spec_mod.SchoolSpec, reqs, placement, meetings,
                 weights: dict[str, int], relax: frozenset[str] = frozenset(),
                 anchor=None, anchor_weight: int = solver.W_STRONG):
        self.relax = relax
        self.spec = spec
        self.reqs = reqs
        self.placement = placement
        self.meetings = meetings
        self.weights = weights
        self.anchor = anchor or {}
        self.anchor_weight = anchor_weight
        self.g = grids(placement, meetings)
        self.out: dict[str, int] = {}
        self.homerooms = [t for t in school.TEACHERS if t.homeroom]
        self.full = set(full_days())

    # -- accumulation ----------------------------------------------------

    def add(self, label: str, count: int, weight: int) -> None:
        if count <= 0:
            return
        weight = self.weights.get(label, weight)
        if weight <= 0:
            return
        self.out[label] = self.out.get(label, 0) + count * weight

    # -- the rules -------------------------------------------------------

    def run(self) -> dict[str, int]:
        self.class_day_length()
        self.windows()
        self.late_days()
        self.homeroom_openings()
        self.late_start_and_end()
        self.late_start_only()
        self.open_on_day()
        self.late_start_away_from_off()
        self.subject_at_day_start()
        self.short_class_day()
        self.teacher_spread()
        self.last_period_days()
        self.not_first_period()
        self.fifth_day()
        self.anchor_moves()
        return dict(self.out)

    def class_day_length(self) -> None:
        for c in school.CLASSES:
            for d, want in c.preferred.items():
                self.add("אורך יום חריג בכיתה",
                         abs(self.g.class_hours(c.name, d) - want),
                         solver.W_MANDATORY)

    def windows(self) -> None:
        flags = []
        for t in school.TEACHERS:
            weekly = self.g.week_gaps(t.name)
            wants_none = (t.name in school.NO_WINDOW_TEACHERS
                          and "no_window" not in self.relax)
            allowed = (0 if wants_none else
                       school.HOMEROOM_MAX_WINDOWS if t.homeroom
                       else t.max_windows)

            # Every window must fall on her longest day.  A day that ties for
            # longest counts as longest — the solver's ">=" is not strict.
            if (school.WINDOW_ON_LONGEST_DAY
                    and "window_day" not in self.relax):
                longest = self.g.longest(t.name)
                for d in range(len(DAYS)):
                    if self.g.gaps(t.name, d) and self.g.hours(t.name, d) < longest:
                        self.add("חלון שאינו ביום הארוך ביותר", 1,
                                 solver.W_CRITICAL)
            if "gaps" in self.relax:
                # With the ceiling relaxed there is nothing keeping the
                # windows down but their price, so the solver prices all of
                # them at the mandatory tier.  Mirror that, not the ceiling.
                self.add("חלונות מעבר לאחד", weekly, solver.W_MANDATORY)
            elif allowed > 1:
                self.add("חלון שני למחנכת", max(0, weekly - 1),
                         solver.W_PREFER)
            if wants_none:
                continue
            if school.WINDOW_MAJORITY:
                flags.append(1 if weekly else 0)
                self.add("מורה ללא חלון כלל", 0 if weekly else 1,
                         solver.W_PREFER)
            else:
                self.add("חלונות", weekly, solver.W_MINOR)
        if school.WINDOW_MAJORITY and flags:
            majority = (len(flags) + 1) // 2
            self.add("חלון לרוב המורות", max(0, majority - sum(flags)),
                     solver.W_STRONG)

    def _late_day_count(self, name: str) -> int:
        return sum(1 for d in range(len(DAYS))
                   if any(p >= LATE_PERIOD for p in self.g.teacher[name][d]))

    def late_days(self) -> None:
        if "late_days" in self.relax:
            return
        for t in school.TEACHERS:
            if not t.min_late_days:
                continue
            self.add("סיום מאוחר: מספר ימים חסר",
                     max(0, t.min_late_days - self._late_day_count(t.name)),
                     solver.W_MANDATORY)

    def _opens_own(self, t, d: int) -> bool:
        """She teaches the first period of the day, in her own class."""
        return any(self.meetings[mid].klass == t.homeroom
                   and t.name in self.meetings[mid].teachers
                   for mid, slot in self.placement.items() if slot == (d, 1))

    def homeroom_openings(self) -> None:
        for t in self.homerooms:
            spec = school.OPEN_OWN_CLASS.get(t.homeroom or "")
            if spec is None:
                continue
            minimum, preferred = spec
            opens = sum(1 for d in range(len(DAYS)) if self._opens_own(t, d))
            self.add("מחנכת פותחת את היום בכיתתה", max(0, minimum - opens),
                     solver.W_MANDATORY)
            if preferred > minimum:
                self.add("מחנכת: פתיחה מועדפת נוספת",
                         max(0, preferred - opens), solver.W_PREFER)

    def late_start_and_end(self) -> None:
        for name in school.LATE_START_AND_END:
            ok = any(PERIODS_PER_DAY[d] >= LATE_PERIOD
                     and 1 not in self.g.teacher[name][d]
                     and any(p >= LATE_PERIOD for p in self.g.teacher[name][d])
                     for d in range(len(DAYS)))
            self.add("מחנכת: יום שמתחיל מאוחר ומסתיים מאוחר", 0 if ok else 1,
                     solver.W_MANDATORY)

    def late_start_only(self) -> None:
        for name, allowed in school.LATE_START_ONLY.items():
            ok = any(1 not in self.g.teacher[name][d]
                     and any(p in allowed for p in self.g.teacher[name][d])
                     for d in range(len(DAYS)))
            self.add("מורה: התחלה מאוחרת פעם בשבוע", 0 if ok else 1,
                     solver.W_MANDATORY)

    def open_on_day(self) -> None:
        for name, days in school.OPEN_ON_DAY.items():
            t = school.BY_NAME.get(name)
            if t is None:
                continue
            for d in days:
                self.add("מחנכת: פתיחה ביום שנקבע",
                         0 if self._opens_own(t, d) else 1, solver.W_CRITICAL)

    def _off_days(self, t) -> set[int]:
        """The days the model would call "off" for this teacher.

        A fixed day off always; and for a teacher choosing between days, the
        one she is actually idle on.  An idle day that is not one of her
        options is not a day off — it is a day the timetable happened to
        leave empty, and the rules about days off do not apply to it.
        """
        off = set(t.off_fixed)
        for d in t.off_choice:
            if not self.g.teacher[t.name][d]:
                off.add(d)
                break
        return off

    def late_start_away_from_off(self) -> None:
        for name in school.LATE_START_AWAY_FROM_OFF:
            t = school.BY_NAME.get(name)
            if t is None:
                continue
            off = self._off_days(t)
            for d in range(len(DAYS)):
                busy = self.g.teacher[name][d]
                if not busy or 1 in busy:
                    continue
                for e in (d - 1, d + 1):
                    if 0 <= e < len(DAYS) and e in off:
                        self.add("מחנכת: התחלה מאוחרת בצמוד ליום חופש", 1,
                                 solver.W_MANDATORY)

    def subject_at_day_start(self) -> None:
        for (cname, subject), n in school.SUBJECT_AT_DAY_START.items():
            got = sum(1 for mid, (d, p) in self.placement.items()
                      if p == 1 and self.meetings[mid].klass == cname
                      and self.meetings[mid].subject == subject)
            self.add(f"{subject} בכיתה {cname}: פתיחת יום", max(0, n - got),
                     solver.W_STRONG)

    def short_class_day(self) -> None:
        for cname in school.PREFER_ONE_SHORT_DAY:
            ok = any(self.g.class_hours(cname, d) == 5 for d in self.full)
            self.add("יום קצר אחד לכיתה", 0 if ok else 1, solver.W_STRONG)

    def teacher_spread(self) -> None:
        for t in school.TEACHERS:
            if t.name in school.NO_SPREAD_PREFERENCE:
                continue
            shorts = sum(1 for d in self.full if self.g.hours(t.name, d) <= 4)
            self.add("פיזור: 2 ימים קצרים למורה", max(0, 2 - shorts),
                     solver.W_MINOR)

    def last_period_days(self) -> None:
        longest = max(PERIODS_PER_DAY)
        for d in school.NO_SEVENTH_ON:
            if PERIODS_PER_DAY[d] < longest:
                continue
            for t in self.homerooms:
                self.add(f"מחנכת בשעה האחרונה ביום {DAYS[d]}",
                         1 if PERIODS_PER_DAY[d] in self.g.teacher[t.name][d]
                         else 0, solver.W_STRONG)

    def not_first_period(self) -> None:
        for mid, (_, p) in self.placement.items():
            mt = self.meetings[mid]
            if p == 1 and mt.subject in school.NOT_FIRST_PERIOD:
                self.add(f"{mt.subject} בשעה ראשונה", 1, solver.W_PREFER)

    def fifth_day(self) -> None:
        """§11.2 — the shape of a homeroom teacher's one non-opening day.

        The solver *chooses* which day carries the rules and pays the
        cheapest bill; from a finished placement the same choice is made the
        same way — over the same candidate days, taking the minimum — so the
        two agree on a timetable neither of them built.
        """
        cfg = school.FIFTH_DAY
        if not any(cfg.values()) or "fifth_day" in self.relax:
            return
        no_longest = checks.fifth_day_longest_impossible(self.reqs)
        for t in self.homerooms:
            off = checks.certainly_off(t)
            only = (set(checks.late_start_days(t))
                    if t.name in school.LATE_START_AWAY_FROM_OFF else None)
            best: dict[str, int] | None = None
            for d in range(len(DAYS)):
                if d not in self.full or d in off:
                    continue
                if only is not None and d not in only:
                    continue
                if not self.g.teacher[t.name][d]:
                    continue          # `pick` implies she works that day
                busy = self.g.teacher[t.name][d]
                bill: dict[str, int] = {}
                if cfg.get("late_start") and 1 in busy:
                    bill["יום חמישי: מתחילה מאוחר"] = 1
                if cfg.get("late_end"):
                    last = self.g.class_hours(t.homeroom, d)
                    if not busy or max(busy) != last:
                        bill["יום חמישי: מסיימת בשעה האחרונה של הכיתה"] = 1
                if cfg.get("window") and not self.g.gaps(t.name, d):
                    bill["יום חמישי: חלון ביום זה"] = 1
                if cfg.get("longest") and t.name not in no_longest:
                    if len(busy) < self.g.longest(t.name):
                        bill["יום חמישי: היום הארוך ביותר"] = 1
                cost = sum(self.weights.get(k, solver.W_MANDATORY)
                           for k in bill)
                # Ties are real — two days can break one component each — and
                # the solver picks either.  Break them the way a reader does:
                # the fifth day is the one she does *not* open her class on.
                key = (cost, 1 if self._opens_own(t, d) else 0)
                if best is None or key < best["__key__"]:
                    best = dict(bill, __key__=key)
            if best:
                for label, n in best.items():
                    if label != "__key__":
                        self.add(label, n, solver.W_MANDATORY)

    def anchor_moves(self) -> None:
        moved = sum(1 for mid, slot in self.anchor.items()
                    if tuple(self.placement.get(mid, ())) != tuple(slot))
        self.add("שינוי מהשיבוץ שאושר", moved, self.anchor_weight)


# ------------------------------------------------------------ the metrics

def _metrics(spec: spec_mod.SchoolSpec, g: Grids) -> dict:
    """The figures a person judges a timetable by, rather than the objective.

    Fairness is reported, not scored: the objective already prices the rules
    the school stated, and a second aggregate number pulling against them
    would be a rule nobody asked for.  What the screen needs is the spread —
    who carries the long days, who has no window — so it is computed here and
    shown.
    """
    teachers = []
    for t in school.TEACHERS:
        days = g.working_days(t.name)
        hours = [g.hours(t.name, d) for d in days]
        teachers.append({
            "name": t.name,
            "homeroom": t.homeroom,
            "hours": sum(hours),
            "declared": t.declared,
            "days": len(days),
            "day_hours": {DAYS[d]: g.hours(t.name, d) for d in days},
            "windows": g.week_gaps(t.name),
            "window_days": [DAYS[d] for d in range(len(DAYS))
                            if g.gaps(t.name, d)],
            "longest_day": max(hours, default=0),
            "shortest_day": min(hours, default=0),
            "long_days": sum(1 for h in hours if h >= LONG_DAY_HOURS),
            "late_days": sum(1 for d in days
                             if any(p >= LATE_PERIOD for p in g.teacher[t.name][d])),
            "first_periods": sum(1 for d in days if 1 in g.teacher[t.name][d]),
        })
    classes = [{
        "name": c.name,
        "homeroom": spec.homeroom_of(c.name),
        "hours": sum(g.class_hours(c.name, d) for d in range(len(DAYS))),
        "day_hours": {DAYS[d]: g.class_hours(c.name, d)
                      for d in range(len(DAYS))},
    } for c in school.CLASSES]

    windows = [t["windows"] for t in teachers]
    spread = [t["longest_day"] - t["shortest_day"] for t in teachers]
    return {
        "teachers": teachers,
        "classes": classes,
        "summary": {
            "teachers": len(teachers),
            "classes": len(classes),
            "with_window": sum(1 for w in windows if w),
            "without_window": sum(1 for w in windows if not w),
            "total_windows": sum(windows),
            "widest_day_spread": max(spread, default=0),
            "average_day_spread": (round(sum(spread) / len(spread), 2)
                                   if spread else 0),
        },
    }


# ------------------------------------------------------------------- API

def evaluate(spec: spec_mod.SchoolSpec, settings: cat.Settings,
             placement: dict[int, tuple[int, int]], meetings: list[Meeting],
             reqs=None, anchor=None,
             anchor_weight: int = solver.W_STRONG,
             require_complete: bool = True) -> Evaluation:
    """Price a placement and list everything wrong with it.

    Must be called with `spec` activated — every rule below reads the engine
    globals, exactly as the solver does.
    """
    reqs = reqs if reqs is not None else spec.requirements()
    tt = Timetable(dict(placement), meetings)
    titles = {c.id: c.title for c in cat.CATALOGUE}
    tagged = validate.violations(tt, settings.relax_ids(),
                                 require_complete)

    penalties = _Pricer(spec, reqs, dict(placement), meetings,
                        settings.weights(spec), settings.relax(),
                        anchor, anchor_weight).run()
    ev = Evaluation(
        violations=[{"rule": r, "title": titles.get(r, r), "message": m}
                    for r, m in tagged],
        unplaced=[{"id": mid, "klass": meetings[mid].klass,
                   "subject": meetings[mid].subject,
                   "teachers": list(meetings[mid].teachers),
                   "length": meetings[mid].length}
                  for mid in range(len(meetings)) if mid not in placement],
        penalties=penalties,
        total=sum(penalties.values()),
        metrics=_metrics(spec, grids(placement, meetings)))
    return ev
