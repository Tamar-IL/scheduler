# -*- coding: utf-8 -*-
"""Independent hard-constraint checker.

Deliberately shares no logic with `solver.py`: it re-derives every rule from
the timetable itself.  A bug in the CP-SAT translation cannot hide here,
because nothing in this file knows the CP-SAT model exists.

Every violation is tagged with the id of the catalogue entry it comes from.
The tag is what lets the editing screen say *which rule* a manual change
broke and offer the rule's own explanation, and what lets a rule the
administrator switched off stop being reported — without a second, divergent
implementation of the same check somewhere in the platform.
"""
from __future__ import annotations

import checks
import school
from model import (DAYS, LATE_PERIOD, LONG_DAY_HOURS, PERIODS_PER_DAY,
                   Timetable)


def _violations(tt: Timetable, relax: frozenset[str] = frozenset(),
                require_complete: bool = True) -> list[tuple[str, str]]:
    """[(constraint id, Hebrew sentence)] for every hard rule broken.

    `relax` holds catalogue ids the administrator has switched off; those
    checks are skipped.  `require_complete` is what a half-finished manual
    edit needs: a lesson the user has lifted out of the grid is not yet a
    violation, it is simply not placed, and the caller reports it as such.
    """
    bad: list[tuple[str, str]] = []

    def note(rule: str, message: str) -> None:
        if rule not in relax:
            bad.append((rule, message))

    grid_class: dict[tuple[str, int, int], list[str]] = {}
    grid_teacher: dict[tuple[str, int, int], list[str]] = {}

    # -- every meeting placed once, inside the grid, doubles kept together
    if require_complete and len(tt.placement) != len(tt.meetings):
        note("all_placed",
             f"שובצו {len(tt.placement)} מפגשים מתוך {len(tt.meetings)}")
    for mid, mt in enumerate(tt.meetings):
        if mid not in tt.placement:
            if require_complete:
                note("all_placed", f"לא שובץ: {mt.klass}/{mt.subject}")
            continue
        d, p = tt.placement[mid]
        if p + mt.length - 1 > PERIODS_PER_DAY[d]:
            note("all_placed",
                 f"{mt.klass}/{mt.subject} חורג מסוף היום ב{DAYS[d]}")
        for q in range(p, p + mt.length):
            if mt.klass:
                grid_class.setdefault((mt.klass, d, q), []).append(mt.subject)
            for t in mt.teachers:
                grid_teacher.setdefault((t, d, q), []).append(
                    f"{mt.klass or '—'}/{mt.subject}")

    for (c, d, p), items in grid_class.items():
        if len(items) > 1:
            note("single_booking",
                 f"כיתה {c} ב{DAYS[d]} שעה {p}: {' + '.join(items)}")
    for (t, d, p), items in grid_teacher.items():
        if len(items) > 1:
            note("single_booking",
                 f"{t} ב{DAYS[d]} שעה {p}: {' + '.join(items)}")

    # -- classes: solid block from the first period, within the day's bounds
    for c in school.CLASSES:
        for d in range(len(DAYS)):
            busy = [p for p in range(1, PERIODS_PER_DAY[d] + 1)
                    if (c.name, d, p) in grid_class]
            lo, hi = c.bounds(d)
            if not lo <= len(busy) <= hi:
                note("class_span",
                     f"כיתה {c.name} ב{DAYS[d]}: {len(busy)} שעות "
                     f"(מותר {lo}–{hi})")
            if busy and busy != list(range(1, len(busy) + 1)):
                note("contiguity",
                     f"כיתה {c.name} ב{DAYS[d]}: חלון לתלמידות (שעות {busy})")

    # -- one meeting of a subject per class per day
    seen: dict[tuple[str, str, int], int] = {}
    for mid, mt in enumerate(tt.meetings):
        if mt.klass and mid in tt.placement:
            key = (mt.klass, mt.subject, tt.placement[mid][0])
            seen[key] = seen.get(key, 0) + 1
    for (c, s, d), n in seen.items():
        if n > 1:
            note("subject_per_day",
                 f"כיתה {c}: {s} מופיע {n} פעמים ב{DAYS[d]}")

    # -- teachers
    for t in school.TEACHERS:
        working = []
        weekly_gaps = 0
        longs = 0
        late_days = 0
        for d in range(len(DAYS)):
            busy = [p for p in range(1, PERIODS_PER_DAY[d] + 1)
                    if (t.name, d, p) in grid_teacher]
            if not busy:
                continue
            working.append(d)
            if d in t.off_fixed:
                note("teacher_off_days",
                     f"{t.name} משובצת ב{DAYS[d]} — יום חופשי קבוע")
            if t.max_per_day is not None and len(busy) > t.max_per_day:
                note("teacher_max_per_day",
                     f"{t.name} ב{DAYS[d]}: {len(busy)} שעות "
                     f"(מקסימום {t.max_per_day})")
            for p in busy:
                if p in t.forbidden_periods:
                    note("teacher_forbidden_periods",
                         f"{t.name} משובצת בשעה {p} ב{DAYS[d]}")
                if p > t.latest_period_on.get(d, PERIODS_PER_DAY[d]):
                    note("teacher_forbidden_periods",
                         f"{t.name} ב{DAYS[d]}: שעה {p} מאוחר מהמותר")
            if len(busy) < t.min_per_day:
                note("teacher_min_per_day",
                     f"{t.name} ב{DAYS[d]}: {len(busy)} שעות בלבד "
                     f"(מינימום {t.min_per_day})")
            if len(busy) <= 2:
                late = [p for p in busy if p in school.SHORT_DAY_BANNED_PERIODS]
                if late:
                    note("teacher_min_per_day",
                         f"{t.name} ב{DAYS[d]}: יום של {len(busy)} שעות "
                         f"בסוף היום (שעות {late})")
            weekly_gaps += (max(busy) - min(busy) + 1) - len(busy)
            if len(busy) >= LONG_DAY_HOURS:
                longs += 1
            if max(busy) >= LATE_PERIOD:
                late_days += 1

        off_days = [d for d in range(len(DAYS)) if d not in working]
        if t.off_choice and not any(d in off_days for d in t.off_choice):
            note("teacher_off_choice",
                 f"{t.name}: אין יום חופשי מבין "
                 f"{', '.join(DAYS[d] for d in t.off_choice)}")
        wants_none = t.name in school.NO_WINDOW_TEACHERS
        allowed = (0 if wants_none else
                   school.HOMEROOM_MAX_WINDOWS if t.homeroom
                   else t.max_windows)
        if weekly_gaps > allowed:
            note("no_window_teachers" if wants_none else "teacher_gaps",
                 f"{t.name}: {weekly_gaps} חלונות בשבוע (מותר {allowed})")
        if t.max_late_days is not None and late_days > t.max_late_days:
            note("teacher_late_days",
                 f"{t.name}: {late_days} ימים המסתיימים אחרי שעה "
                 f"{LATE_PERIOD - 1} (מותר {t.max_late_days})")
        if t.min_late_days and late_days < 1:
            note("teacher_late_days",
                 f"{t.name}: אף יום אינו מסתיים אחרי שעה {LATE_PERIOD - 1} "
                 f"(נדרש לפחות 1, רצוי {t.min_late_days})")
        if require_complete and t.exact_working_days is not None \
                and len(working) != t.exact_working_days:
            note("teacher_working_days",
                 f"{t.name}: {len(working)} ימי עבודה "
                 f"(נדרש {t.exact_working_days})")
        if t.max_long_days is not None and longs > t.max_long_days:
            note("teacher_long_days",
                 f"{t.name}: {longs} ימים ארוכים (מותר {t.max_long_days})")

        # no more than five hours with one class in one day
        for c in school.CLASSES:
            for d in range(len(DAYS)):
                n = sum(1 for p in range(1, PERIODS_PER_DAY[d] + 1)
                        if any(x.startswith(f"{c.name}/")
                               for x in grid_teacher.get((t.name, d, p), [])))
                if n > 5:
                    note("same_class_five",
                         f"{t.name} עם כיתה {c.name} ב{DAYS[d]}: "
                         f"{n} שעות (מותר 5)")

    # A subject confined, across a set of classes, to one day pattern.
    for subject, klasses, options in school.SUBJECT_DAY_GROUPS:
        used = {tt.placement[mid][0] for mid, mt in enumerate(tt.meetings)
                if mt.subject == subject and mt.klass in klasses
                and mid in tt.placement}
        if used and not any(used <= set(o) for o in options):
            note("subject_day_groups",
                 f"{subject} בכיתות {', '.join(klasses)} בימים "
                 + ", ".join(DAYS[d] for d in sorted(used))
                 + " — מותר רק "
                 + " או ".join("+".join(DAYS[d] for d in o)
                               for o in options))

    # Subjects pinned to one day of the week (פ. שבוע on Friday).
    for mid, mt in enumerate(tt.meetings):
        fixed = school.SUBJECT_FIXED_DAY.get(mt.subject)
        if fixed is None or mid not in tt.placement:
            continue
        day, exempt = fixed
        d = tt.placement[mid][0]
        if mt.klass in exempt:
            if d == day:
                note("subject_fixed_day",
                     f"{mt.klass}/{mt.subject} ב{DAYS[day]} — "
                     f"כיתה זו דווקא לא ביום זה")
        elif d != day:
            note("subject_fixed_day",
                 f"{mt.klass}/{mt.subject} ב{DAYS[d]} — "
                 f"חייב להיות ב{DAYS[day]}")

    # Subjects confined to a window of periods (ספריה).
    for mid, mt in enumerate(tt.meetings):
        pair = (school.SUBJECT_PERIODS.get(mt.subject),
                school.CLASS_SUBJECT_PERIODS.get((mt.klass, mt.subject)))
        windows = [w for w in pair if w]
        if windows and mid in tt.placement:
            first = max(w[0] for w in windows)
            last = min(w[1] for w in windows)
            d, p = tt.placement[mid]
            if not first <= p <= last - mt.length + 1:
                note("class_subject_periods" if pair[1] else "subject_periods",
                     f"{mt.subject} ב{DAYS[d]} שעה {p} — מותר רק "
                     f"בשעות {first}–{last}")

    # Lessons the school placed by hand, and days it cut short.
    for (k, d, p), subject in school.PINNED.items():
        got = grid_class.get((k, d, p), [])
        if got != [subject]:
            note("pinned_lessons",
                 f"כיתה {k} ב{DAYS[d]} שעה {p}: "
                 f"{' + '.join(got) or '—'} במקום {subject}")
    for (k, d), last in school.DAY_ENDS_AT.items():
        over = [p for p in range(last + 1, PERIODS_PER_DAY[d] + 1)
                if (k, d, p) in grid_class]
        if over:
            note("pinned_lessons",
                 f"כיתה {k} ב{DAYS[d]} ממשיכה לשעות {over} "
                 f"(היום נגמר בשעה {last})")

    # Lessons that must be the last thing their teacher does that day.
    for teacher, subject in school.SUBJECT_ENDS_DAY:
        for mid, mt in enumerate(tt.meetings):
            if mt.subject != subject or teacher not in mt.teachers \
                    or mid not in tt.placement:
                continue
            d, p = tt.placement[mid]
            after = [q for q in range(p + mt.length, PERIODS_PER_DAY[d] + 1)
                     if (teacher, d, q) in grid_teacher]
            if after:
                note("subject_ends_day",
                     f"{teacher}: {subject} ב{DAYS[d]} שעה {p} אינו "
                     f"סוף היום שלה (מלמדת גם בשעות {after})")

    # Homeroom teachers opening the day in their own class.  A minimum count
    # over the whole week, so it only means anything once everything is
    # placed — a half-edited grid is under-counted, not broken.
    if require_complete:
        for t in school.TEACHERS:
            spec = school.OPEN_OWN_CLASS.get(t.homeroom or "")
            if spec is None:
                continue
            minimum, _ = spec
            opens = sum(1 for d in range(len(DAYS))
                        if any(x.startswith(f"{t.homeroom}/")
                               for x in grid_teacher.get((t.name, d, 1), [])))
            if opens < minimum:
                note("open_own_class",
                     f"{t.name} פותחת את כיתה {t.homeroom} ב-{opens} ימים "
                     f"בלבד (נדרש {minimum})")

    def _idle_days(name: str) -> set[int]:
        return {d for d in range(len(DAYS))
                if not any((name, d, p) in grid_teacher
                           for p in range(1, PERIODS_PER_DAY[d] + 1))}

    for a, b in school.DISTINCT_DAYS_OFF:
        shared = _idle_days(a) & _idle_days(b)
        if shared:
            note("distinct_off",
                 f"{a} ו-{b} חופשיות באותו יום "
                 f"({', '.join(DAYS[d] for d in sorted(shared))})")
    return bad


def violations(tt: Timetable, relax: frozenset[str] = frozenset(),
               require_complete: bool = True) -> list[tuple[str, str]]:
    """Tagged violations, for callers that want to know which rule broke."""
    return _violations(tt, relax, require_complete)


def validate(tt: Timetable) -> tuple[bool, list[str]]:
    bad = _violations(tt)
    return (not bad), [message for _, message in bad]
