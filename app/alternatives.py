# -*- coding: utf-8 -*-
"""Where else could this lesson go?

The question the screen asks is not "is this slot empty" — that is a lookup
anyone can do — but "what happens to the rest of the week if the lesson goes
there".  So every candidate is turned into a whole trial timetable and priced
against the current one: the answer names the cost, and where a slot is
impossible it names the rule and the sentence that rule produces.

There is no search here and nothing statistical.  The candidates are every
slot in the week; the ranking is the difference in the objective the solver
itself minimises.  That is what makes this work with the solver switched off.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from model import DAYS, PERIODS_PER_DAY
from app import constraints as cat
from app import spec as spec_mod
from app import timetable as tt_mod


@dataclass
class Option:
    day: int
    period: int
    #: "move" into free space, or "swap" with the lesson already there.
    kind: str
    #: The rows a swap would exchange with — more than one when a double
    #: period trades places with two singles.
    partner: list[int]
    ok: bool
    #: Change in the objective.  Negative is an improvement.
    delta: int
    #: Hard rules this option would break that are not broken today.
    blocking: list[dict]
    #: Priced rules that get better or worse, for the "why" line.
    changes: list[dict]

    def to_dict(self) -> dict:
        return {
            "day": self.day, "day_name": DAYS[self.day], "period": self.period,
            "kind": self.kind, "partner": list(self.partner), "ok": self.ok,
            "delta": self.delta, "blocking": self.blocking,
            "changes": self.changes,
        }


def _fingerprint(review: dict) -> set[tuple[str, str]]:
    return {(v["rule"], v["message"]) for v in review["violations"]}


def _trial(doc: tt_mod.Document, uid: int, day: int, period: int,
           partner: list[int]) -> tt_mod.Document:
    """The timetable as it would be after this option is taken.

    A swap keeps every lesson in the grid, which is what makes it the right
    move under class contiguity: lifting one out would shorten the class's
    day and shift everything after it.  A double period trading places with
    two singles is the same exchange, so the displaced lessons are laid back
    into the vacated slots in order.
    """
    trial = doc.copy()
    trial.exchange(uid, day, period, partner)
    return trial


def _collisions(doc: tt_mod.Document, row: tt_mod.Row, day: int,
                period: int) -> list[tt_mod.Row]:
    """Rows already occupying the slots this one would take."""
    wanted = {(day, period + i) for i in range(row.length)}
    hit = []
    for other in doc.rows:
        if other.uid == row.uid or not other.placed:
            continue
        if not wanted & set(other.slots()):
            continue
        same_class = other.klass == row.klass
        if same_class or set(other.teachers) & set(row.teachers):
            hit.append(other)
    return hit


def _exchange(row: tt_mod.Row, hits: list[tt_mod.Row]) -> list[int] | None:
    """Can this lesson simply trade places with what is already there?

    Only within one class, and only when the hours match exactly: the class's
    day is a solid block, so an exchange that does not conserve its length
    would move every lesson after it as well.  Anything else is reported as
    occupied rather than silently rearranged.
    """
    if not row.placed:
        return None                    # nowhere to send the displaced lessons
    if any(h.locked or h.klass != row.klass for h in hits):
        return None
    if sum(h.length for h in hits) != row.length:
        return None
    periods = sorted(p for h in hits for p in range(h.period,
                                                    h.period + h.length))
    if periods != list(range(periods[0], periods[0] + len(periods))):
        return None                    # the displaced lessons are not a block
    return [h.uid for h in hits]


def find(spec: spec_mod.SchoolSpec, settings: cat.Settings,
         doc: tt_mod.Document, uid: int,
         limit: int = 60) -> dict:
    """Every placement for one lesson, priced and explained.

    Must be called with `spec` activated.
    """
    row = doc.row(uid)
    base = tt_mod.review(spec, settings, doc, require_complete=False)
    base_bad = _fingerprint(base)
    titles = {c.id: c for c in cat.CATALOGUE}

    options: list[Option] = []
    for day in range(len(DAYS)):
        for period in range(1, PERIODS_PER_DAY[day] - row.length + 2):
            if row.placed and (day, period) == (row.day, row.period):
                continue
            hits = _collisions(doc, row, day, period)
            partner: list[int] = []
            if hits:
                swap = _exchange(row, hits)
                if swap is None:
                    who = "; ".join(
                        f"{h.subject}"
                        + (f" בכיתה {h.klass}" if h.klass else "")
                        + f" ({'/'.join(h.teachers)})" for h in hits)
                    options.append(Option(
                        day, period, "blocked", [], False, 0,
                        [{"rule": "single_booking",
                          "title": titles["single_booking"].title,
                          "message": f"השעה תפוסה: {who}"}], []))
                    continue
                partner = swap

            trial = _trial(doc, uid, day, period, partner)
            got = tt_mod.review(spec, settings, trial, require_complete=False)
            new_bad = [v for v in got["violations"]
                       if (v["rule"], v["message"]) not in base_bad]
            changes = []
            for label in sorted(set(got["penalties"]) | set(base["penalties"])):
                diff = got["penalties"].get(label, 0) - base["penalties"].get(label, 0)
                if diff:
                    changes.append({"label": label, "delta": diff})
            options.append(Option(
                day, period, "swap" if partner else "move", partner,
                not new_bad, got["total"] - base["total"], new_bad, changes))

    # Workable options first, cheapest first; then the blocked ones, in the
    # order of the week, so the screen can show them greyed out in place.
    ok = sorted([o for o in options if o.ok],
                key=lambda o: (o.delta, o.day, o.period))
    blocked = [o for o in options if not o.ok]
    return {
        "uid": uid,
        "lesson": row.to_dict(),
        "current": {"day": row.day, "period": row.period,
                    "day_name": DAYS[row.day] if row.placed else None},
        "base_total": base["total"],
        "options": [o.to_dict() for o in ok[:limit]],
        "blocked": [o.to_dict() for o in blocked],
        "counts": {"ok": len(ok), "blocked": len(blocked)},
    }


def check_edit(spec: spec_mod.SchoolSpec, settings: cat.Settings,
               before: tt_mod.Document, after: tt_mod.Document) -> dict:
    """What one manual change did to the whole timetable.

    The screen needs the difference, not the absolute state: a timetable with
    four standing problems that the user already knows about should not
    report four problems every time a lesson moves.  So the violations are
    split into the ones this edit introduced and the ones that were there
    before.
    """
    a = tt_mod.review(spec, settings, before, require_complete=False)
    b = tt_mod.review(spec, settings, after, require_complete=False)
    old = _fingerprint(a)
    introduced = [v for v in b["violations"]
                  if (v["rule"], v["message"]) not in old]
    now = _fingerprint(b)
    resolved = [v for v in a["violations"]
                if (v["rule"], v["message"]) not in now]
    changes = []
    for label in sorted(set(a["penalties"]) | set(b["penalties"])):
        diff = b["penalties"].get(label, 0) - a["penalties"].get(label, 0)
        if diff:
            changes.append({"label": label, "delta": diff})
    return {
        "ok": not introduced,
        "introduced": introduced,
        "resolved": resolved,
        "delta": b["total"] - a["total"],
        "changes": changes,
        "review": b,
    }
