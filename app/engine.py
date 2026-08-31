# -*- coding: utf-8 -*-
"""Generating a timetable: phase 0, the hardening ladder, the polish, the check.

This is `main.py`'s run, made callable and reportable.  The sequence is the
same one and for the same reasons — count before searching, enforce the
mandatory tier and step down a rung when it turns out to be over-determined,
tidy what no constraint can see, then re-derive every hard rule independently
— but it takes a `SchoolSpec` instead of a module and it says where it is up
to, because a person is watching a progress bar rather than a terminal.

Two additions the command line does not need:

* **Locked lessons.**  A row the administrator has pinned is added to the
  model as an equality after it is built, exactly as `solver.score` pins a
  whole placement.  This is what makes "re-solve, but leave these alone" work.
* **A budget split.**  The ladder's rungs share the time limit, so a run that
  is told to take two minutes takes two minutes rather than two per rung.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import checks
import polish
import solver
import validate
from model import Timetable, align, expand
from app import activate
from app import constraints as cat
from app import spec as spec_mod
from app import timetable as tt_mod

Progress = Callable[[str, float], None]


@dataclass
class SolveReport:
    status: str = ""
    objective: int = 0
    best_bound: int = 0
    wall_time: float = 0.0
    rung: str = ""
    #: Phase-0 findings, as {level, topic, message}.
    findings: list[dict] = field(default_factory=list)
    errors: int = 0
    tight: int = 0
    penalties: dict[str, int] = field(default_factory=dict)
    hard_ok: bool = False
    problems: list[str] = field(default_factory=list)
    polished: int = 0
    #: Every rung tried, and what happened, so a bad result can be read.
    attempts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def preflight(spec: spec_mod.SchoolSpec) -> SolveReport:
    """Phase 0 alone: the counting arguments, in milliseconds.

    Worth its own entry point — an administrator who has just uploaded a
    staff list wants to know it is impossible *now*, not after five minutes
    of search.
    """
    report = SolveReport()
    with activate.activated(spec):
        findings = checks.run(spec.requirements())
        report.findings = [{"level": f.level, "topic": f.topic,
                            "message": f.message} for f in findings]
        report.errors, report.tight = checks.summarise(findings)
    return report


def _pin(sch: solver.Scheduler, pins: dict[int, tuple[int, int]]) -> list[str]:
    """Nail meetings to slots after the model is built.

    A pin whose slot the model has already ruled out — a lesson locked onto a
    period a later rule forbids — cannot be honoured, and forcing it would
    return a bare INFEASIBLE.  It is dropped and named instead.
    """
    lost = []
    for mid, slot in pins.items():
        var = sch.x.get(mid, {}).get(tuple(slot))
        if var is None:
            mt = sch.meetings[mid]
            lost.append(f"{mt.subject} בכיתה {mt.klass or '—'}: "
                        f"המקום שננעל אינו אפשרי עוד ולכן השחרר")
            continue
        sch.m.Add(var == 1)
    return lost


def generate(spec: spec_mod.SchoolSpec, settings: cat.Settings,
             time_limit: float = 120.0, workers: int = 8,
             anchor: tt_mod.Document | None = None,
             locked: list[tt_mod.Row] | None = None,
             harden: list[str] | None = None,
             anchor_weight: int = solver.W_STRONG,
             progress: Progress | None = None,
             log: bool = False) -> tuple[tt_mod.Document | None, SolveReport]:
    """Build a timetable for this school.  Returns (document, report)."""
    def say(text: str, done: float) -> None:
        if progress:
            progress(text, done)

    report = SolveReport()
    with activate.activated(spec):
        reqs = spec.requirements()
        say("בדיקות היתכנות מוקדמות", 0.02)
        findings = checks.run(reqs)
        report.findings = [{"level": f.level, "topic": f.topic,
                            "message": f.message} for f in findings]
        report.errors, report.tight = checks.summarise(findings)

        meetings = expand(reqs)
        relax = settings.relax()
        weights = settings.weights(spec)

        # A placement is matched onto the current lesson list by lesson
        # identity, never by position: rows carry ids of their own, meetings
        # are numbered by where they fall in the subject table, and a school
        # that has since gained or lost an hour renumbers every meeting after
        # it.  `model.align` is the translation, and it simply leaves out a
        # lesson with no counterpart.
        def placement_of(rows) -> dict[int, tuple[int, int]]:
            return align([[r.klass, r.subject, r.teachers, r.length,
                           r.day, r.period] for r in rows if r.placed],
                         meetings)

        anchor_place: dict[int, tuple[int, int]] = {}
        hint: dict[int, tuple[int, int]] = {}
        if anchor is not None:
            anchor_place = placement_of(anchor.rows)
            hint = dict(anchor_place)
        pins = placement_of(locked or [])

        # An anchored run starts from the rules the approved timetable already
        # keeps: that set is one the anchor itself proves consistent, so it
        # cannot forbid what the school is living with, and the two blind top
        # rungs are not worth the minutes.
        kept: frozenset[str] = frozenset()
        if anchor_place:
            say("מדידת המערכת המאושרת", 0.08)
            paid = solver.score(reqs, anchor_place,
                                relax | solver.TEACHER_REQUESTS,
                                time_limit=max(30.0, time_limit * 0.15),
                                workers=workers, weights=weights)
            if paid is not None:
                kept = (solver.MANDATORY_LABELS - set(paid)
                        - solver.NEVER_HARDEN)

        extra = frozenset(harden or ())
        ladder: list[tuple[str, object, float]] = []
        if anchor_place and (kept or extra):
            ladder.append(("כללי החובה שהמערכת המאושרת כבר מקיימת",
                           kept | extra, 0.55))
        elif not anchor_place:
            ladder.append(("כל כללי החובה כאילוצים קשיחים", True, 0.15))
            ladder.append((f"הכל קשיח פרט ל'{solver.FIFTH_WINDOW}'",
                           solver.MANDATORY_LABELS - {solver.FIFTH_WINDOW}
                           | extra, 0.55))
        elif extra:
            ladder.append(("הכללים שנדרשו במפורש", extra, 0.55))
        ladder.append(("כל כללי החובה מתומחרים", False, 1.0))

        result, spent = None, 0.0
        for n, (label, harden_set, share) in enumerate(ladder):
            budget = max(20.0, time_limit * share - spent)
            say(f"פתרון — {label}", 0.1 + 0.8 * n / max(1, len(ladder)))
            started = time.time()
            sch = solver.Scheduler(
                reqs, time_limit=budget, workers=workers, relax=relax,
                harden=harden_set, hint=hint, anchor=anchor_place,
                anchor_weight=anchor_weight, weights=weights)
            report.problems += _pin(sch, pins)
            attempt = sch.solve(log=log)
            spent += time.time() - started
            report.attempts.append({"rung": label, "status": attempt.status,
                                    "seconds": round(attempt.wall_time, 1)})
            if attempt.timetable is not None:
                result, report.rung = attempt, label
                break
            say(f"אין פתרון ברמה '{label}' — יורדים דרגה", 0.1)

        if result is None:
            report.status = "INFEASIBLE"
            return None, report

        # The solver is indifferent to which of a homeroom teacher's own
        # lessons opens the day; the school is not.  This exchange is
        # invisible to every constraint, and `validate` still sees the result.
        say("ליטוש", 0.92)
        if spec.rules.opening_subject:
            opened = polish.open_with_torah(result.timetable)
            report.polished = sum(1 for mid, slot
                                  in result.timetable.placement.items()
                                  if opened[mid] != slot)
            if report.polished:
                result.timetable = Timetable(opened, result.timetable.meetings)

        say("אימות בלתי תלוי", 0.96)
        ok, problems = validate.validate(result.timetable)
        report.status = result.status
        report.objective = result.objective
        report.best_bound = result.best_bound
        report.wall_time = round(spent, 1)
        report.penalties = dict(result.penalties)
        report.hard_ok = ok
        report.problems += problems

        doc = tt_mod.Document.from_timetable(result.timetable)
        if anchor is not None:
            # Carry the approved placement and the locks across, so a repair
            # stays a repair on the next round too.
            approved_by_lesson: dict[tuple, list[tuple[int, int]]] = {}
            for r in anchor.rows:
                if r.uid in anchor.approved:
                    approved_by_lesson.setdefault(
                        (r.klass, r.subject, tuple(r.teachers), r.length),
                        []).append(anchor.approved[r.uid])
            for r in doc.rows:
                key = (r.klass, r.subject, tuple(r.teachers), r.length)
                if approved_by_lesson.get(key):
                    doc.approved[r.uid] = approved_by_lesson[key].pop(0)
        say("הושלם", 1.0)
        return doc, report


def rescore(spec: spec_mod.SchoolSpec, settings: cat.Settings,
            doc: tt_mod.Document, time_limit: float = 60.0,
            workers: int = 4) -> dict | None:
    """The authoritative price of a timetable, from the solver itself.

    `app.evaluate` gives the same answer in milliseconds and is what the
    editing screens use; this is the second opinion, for the moment before a
    timetable is approved.
    """
    with activate.activated(spec):
        reqs = spec.requirements()
        placement = align([[r.klass, r.subject, r.teachers, r.length,
                            r.day, r.period] for r in doc.rows if r.placed],
                          expand(reqs))
        return solver.score(reqs, placement, settings.relax(),
                            time_limit=time_limit, workers=workers,
                            weights=settings.weights(spec))
