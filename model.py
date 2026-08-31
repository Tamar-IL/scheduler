# -*- coding: utf-8 -*-
"""Domain model for the school timetable scheduler.

Deliberately free of any solver concepts: these types describe the school,
not the search.  `solver.py` translates them into a CP-SAT model and
`validate.py` checks a finished timetable against them independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------- time grid

DAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי"]
SUN, MON, TUE, WED, THU, FRI = range(6)
PERIODS_PER_DAY = [7, 7, 7, 7, 7, 4]

#: A day counts as "long" for a teacher when she teaches at least this many
#: hours in it.  Used by the זהבי rule and the load-spreading preference.
LONG_DAY_HOURS = 6
#: "Finishing late" means teaching at or beyond this period.
LATE_PERIOD = 6


def configure_grid(days: list[str], periods_per_day: list[int]) -> None:
    """Re-point the week at another school's grid.

    `DAYS` and `PERIODS_PER_DAY` are imported by name all over the engine, so
    they are rewritten *in place*: rebinding the module attribute would leave
    every `from model import DAYS` holding the old list.  Everything else in
    the engine derives the week from these two lists, which is what lets one
    process schedule schools whose weeks do not look alike.
    """
    if len(days) != len(periods_per_day):
        raise ValueError("days and periods_per_day differ in length")
    DAYS[:] = list(days)
    PERIODS_PER_DAY[:] = list(periods_per_day)


def full_days() -> list[int]:
    """Days that run to the week's longest period count.

    Several rules are stated about "a normal day" and mean it: a teacher's
    longest day, the day a late start can fall on, the day a class can be
    short on.  A week's shortened day — Friday here — is excluded from all of
    them by counting rather than by name, so the rules re-derive themselves
    for a school whose short day is a different one, or which has none.
    """
    longest = max(PERIODS_PER_DAY)
    return [d for d in range(len(DAYS)) if PERIODS_PER_DAY[d] == longest]


def short_days() -> list[int]:
    full = set(full_days())
    return [d for d in range(len(DAYS)) if d not in full]


def slots_of_day(day: int) -> list[tuple[int, int]]:
    return [(day, p) for p in range(1, PERIODS_PER_DAY[day] + 1)]


def all_slots() -> list[tuple[int, int]]:
    return [s for d in range(len(DAYS)) for s in slots_of_day(d)]


def slot_name(slot: tuple[int, int]) -> str:
    return f"{DAYS[slot[0]]} {slot[1]}"


# ------------------------------------------------------------------ people

@dataclass
class Teacher:
    name: str
    #: Weekly hours as stated in the staff table (advisory — the subject
    #: table is authoritative; mismatches are reported by `checks.py`).
    declared: int | None = None
    #: Days she is definitely off.
    off_fixed: tuple[int, ...] = ()
    #: She must be off on exactly one of these days (a "שני או שלישי" rule).
    off_choice: tuple[int, ...] = ()
    #: Hard cap on teaching hours in a single day.
    max_per_day: int | None = None
    #: Floor on a day she works at all — a teacher should not travel in
    #: for one or two lessons.  Lowered to 2 for the few allowed to.
    min_per_day: int = 3
    #: Periods she may never be scheduled in, on any day.
    forbidden_periods: tuple[int, ...] = ()
    #: {day: last period she may teach} — e.g. רותי finishing early on Monday.
    latest_period_on: dict[int, int] = field(default_factory=dict)
    #: Exact number of days she works, when the staff table pins it down.
    exact_working_days: int | None = None
    #: At most this many "long" days (זהבי).
    max_long_days: int | None = None
    #: Fewest / most days she may finish *after* the fifth period (i.e. teach
    #: at period 6 or 7).  Friday, at four periods, is never such a day.
    min_late_days: int = 0
    max_late_days: int | None = None
    #: Windows allowed in a week.  The school allows homeroom teachers a
    #: second one where it is needed; the second is priced, not free.
    max_windows: int = 1
    #: Class she is homeroom teacher of.
    homeroom: str | None = None

    def is_off(self, day: int) -> bool:
        """True only for days that are off no matter how choices resolve."""
        return day in self.off_fixed


# ----------------------------------------------------------------- classes

@dataclass
class SchoolClass:
    name: str
    #: {day: (min_hours, max_hours)} — what is physically allowed.
    day_span: dict[int, tuple[int, int]]
    #: {day: hours} where the school stated one exact figure.  Kept separate
    #: from `day_span` so the solver can bend it at a stated price instead of
    #: returning INFEASIBLE and explaining nothing.
    preferred: dict[int, int] = field(default_factory=dict)

    def bounds(self, day: int) -> tuple[int, int]:
        return self.day_span[day]


# ------------------------------------------------------------- the lessons

@dataclass(frozen=True)
class Requirement:
    """A weekly teaching commitment, before it is split into meetings."""
    klass: str | None          # None = no class (סימה זאבי's library hour)
    subject: str
    teachers: tuple[str, ...]  # more than one ⇒ הקבצה, taught in parallel
    hours: int
    pattern: tuple[int, ...]   # meeting lengths, e.g. (2, 2, 1, 1)

    def __post_init__(self):
        if sum(self.pattern) != self.hours:
            raise ValueError(
                f"{self.klass}/{self.subject}: pattern {self.pattern} "
                f"does not sum to {self.hours} hours"
            )


@dataclass(frozen=True)
class Meeting:
    """One placeable occurrence: a single period or a double."""
    id: int
    klass: str | None
    subject: str
    teachers: tuple[str, ...]
    length: int

    @property
    def label(self) -> str:
        return f"{self.subject} ({'/'.join(self.teachers)})"


def expand(requirements: Iterable[Requirement]) -> list[Meeting]:
    """Turn weekly hour counts into the fixed objects the solver places.

    This is what lets every downstream constraint treat "6 hours somehow
    distributed" and "1 hour" identically.
    """
    meetings: list[Meeting] = []
    for req in requirements:
        for length in req.pattern:
            meetings.append(
                Meeting(len(meetings), req.klass, req.subject,
                        req.teachers, length)
            )
    return meetings


# ------------------------------------------------------------- the outcome

def descriptors(meetings: Iterable[Meeting]) -> list[tuple]:
    """What each meeting *is*, independent of where it sits in the list."""
    return [(m.klass, m.subject, m.teachers, m.length) for m in meetings]


def align(saved: list, meetings: list[Meeting]) -> dict[int, tuple[int, int]]:
    """Map a placement saved against an older meeting list onto this one.

    Meetings are numbered by position, so taking one hour out of the subject
    table renumbers every meeting after it — an anchor keyed by number would
    then pin the wrong lessons without saying so.  Matching on the lesson
    itself survives that: identical meetings are interchangeable by
    construction, so they are paired in slot order, and a meeting with no
    counterpart on either side is simply left unanchored.

    `saved` holds `[klass, subject, [teachers], length, day, period]` rows.
    """
    want: dict[tuple, list[tuple[int, int]]] = {}
    for klass, subject, teachers, length, day, period in saved:
        want.setdefault((klass, subject, tuple(teachers), length),
                        []).append((day, period))
    for slots in want.values():
        slots.sort()

    have: dict[tuple, list[int]] = {}
    for mid, key in enumerate(descriptors(meetings)):
        have.setdefault(key, []).append(mid)

    out: dict[int, tuple[int, int]] = {}
    for key, mids in have.items():
        for mid, slot in zip(mids, want.get(key, [])):
            out[mid] = slot
    return out


@dataclass
class Timetable:
    """meeting id -> starting slot."""
    placement: dict[int, tuple[int, int]]
    meetings: list[Meeting]

    def occupied(self, mid: int) -> list[tuple[int, int]]:
        day, start = self.placement[mid]
        length = self.meetings[mid].length
        return [(day, start + i) for i in range(length)]

    def by_class(self, klass: str) -> dict[tuple[int, int], Meeting]:
        out = {}
        for mid, m in enumerate(self.meetings):
            if m.klass == klass and mid in self.placement:
                for s in self.occupied(mid):
                    out[s] = m
        return out

    def by_teacher(self, teacher: str) -> dict[tuple[int, int], Meeting]:
        out = {}
        for mid, m in enumerate(self.meetings):
            if teacher in m.teachers and mid in self.placement:
                for s in self.occupied(mid):
                    out[s] = m
        return out
