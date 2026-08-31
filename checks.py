# -*- coding: utf-8 -*-
"""Phase 0 — counting checks that run before any search.

Most "the solver hangs forever" reports are really infeasible input.  Every
check here is a *necessary* condition proved by counting, so it costs
milliseconds and produces a sentence a human can act on, instead of a
timeout.  Passing all of them does not prove the timetable exists — that is
the solver's job — but failing one proves it does not.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import model
import school
from model import DAYS, PERIODS_PER_DAY, Requirement, Teacher

ERROR, TIGHT, INFO = "ERROR", "TIGHT", "INFO"


@dataclass
class Finding:
    level: str
    topic: str
    message: str

    def __str__(self) -> str:
        mark = {ERROR: "✗", TIGHT: "!", INFO: "·"}[self.level]
        return f"  {mark} [{self.topic}] {self.message}"


# ------------------------------------------------------------- availability

def possible_off_sets(t: Teacher) -> list[frozenset[int]]:
    """Every way this teacher's days off could resolve."""
    fixed = set(t.off_fixed)
    if t.off_choice:
        options = [fixed | {d} for d in t.off_choice]
    else:
        options = [fixed]
    if t.exact_working_days is not None:
        expanded = []
        for off in options:
            working = [d for d in range(len(DAYS)) if d not in off]
            extra = len(working) - t.exact_working_days
            if extra < 0:
                continue
            for combo in itertools.combinations(working, extra):
                expanded.append(frozenset(off | set(combo)))
        options = expanded
    return [frozenset(o) for o in options]


def certainly_off(t: Teacher) -> set[int]:
    """Days off under every possible resolution of her choices."""
    sets = possible_off_sets(t)
    return set.intersection(*[set(s) for s in sets]) if sets else set()


def day_capacity(t: Teacher, day: int) -> int:
    """Most hours this teacher could possibly teach on the given day."""
    periods = [p for p in range(1, PERIODS_PER_DAY[day] + 1)
               if p not in t.forbidden_periods
               and p <= t.latest_period_on.get(day, PERIODS_PER_DAY[day])]
    cap = len(periods)
    if t.max_per_day is not None:
        cap = min(cap, t.max_per_day)
    return cap


def week_capacity(t: Teacher) -> int:
    """Best-case weekly capacity over all resolutions of her days off."""
    return max(
        sum(day_capacity(t, d) for d in range(len(DAYS)) if d not in off)
        for off in possible_off_sets(t)
    )


# ------------------------------------------------------------------ checks

def run(reqs: list[Requirement]) -> list[Finding]:
    out: list[Finding] = []
    out += _class_totals(reqs)
    out += _teacher_totals(reqs)
    out += _teacher_capacity(reqs)
    out += _class_day_spans(reqs)
    out += _daily_supply(reqs)
    out += _subject_day_spread(reqs)
    out += _class_day_coverage(reqs)
    out += _homeroom_openings(reqs)
    out += _minimum_day_length(reqs)
    out += _day_off_narrowing(reqs)
    out += _fifth_day_capacity(reqs)
    out += _late_start_choice(reqs)
    return out


def _load_by_teacher(reqs) -> dict[str, int]:
    load: dict[str, int] = {}
    for r in reqs:
        for name in r.teachers:
            load[name] = load.get(name, 0) + r.hours
    return load


def _class_totals(reqs) -> list[Finding]:
    declared = school.CLASS_DECLARED_HOURS
    got: dict[str, int] = {}
    for r in reqs:
        if r.klass:
            got[r.klass] = got.get(r.klass, 0) + r.hours
    out = []
    for c, want in declared.items():
        have = got.get(c, 0)
        if have != want:
            out.append(Finding(
                TIGHT, "סה\"כ כיתה",
                f"כיתה {c}: הטבלה מסתכמת ב-{have} שעות, בשורת הסיכום {want} "
                f"({have - want:+d}). ממשיכים לפי הטבלה."))
    return out


def _teacher_totals(reqs) -> list[Finding]:
    load = _load_by_teacher(reqs)
    out = []
    for t in school.TEACHERS:
        have, want = load.get(t.name, 0), t.declared
        if want is not None and have != want:
            out.append(Finding(
                TIGHT, "סה\"כ מורה",
                f"{t.name}: משובצות {have} שעות, בטבלת המורות {want} "
                f"({have - want:+d}). ממשיכים לפי הטבלה."))
    return out


def _teacher_capacity(reqs) -> list[Finding]:
    load = _load_by_teacher(reqs)
    out = []
    for t in school.TEACHERS:
        need, cap = load.get(t.name, 0), week_capacity(t)
        if need > cap:
            out.append(Finding(
                ERROR, "עומס מורה",
                f"{t.name}: {need} שעות לשבץ אך לכל היותר {cap} שעות פנויות "
                f"({', '.join(DAYS[d] for d in sorted(certainly_off(t)))} חופשי)."))
        elif need > cap - 2:
            out.append(Finding(
                TIGHT, "עומס מורה",
                f"{t.name}: {need} שעות מתוך {cap} אפשריות — כמעט ללא מרווח."))
    return out


def _class_day_spans(reqs) -> list[Finding]:
    total: dict[str, int] = {}
    for r in reqs:
        if r.klass:
            total[r.klass] = total.get(r.klass, 0) + r.hours
    out = []
    for c in school.CLASSES:
        lo = sum(c.bounds(d)[0] for d in range(len(DAYS)))
        hi = sum(c.bounds(d)[1] for d in range(len(DAYS)))
        have = total.get(c.name, 0)
        if not lo <= have <= hi:
            out.append(Finding(
                ERROR, "אורך יום",
                f"כיתה {c.name}: {have} שעות שבועיות, אך מבנה הימים מאפשר "
                f"בין {lo} ל-{hi}."))
        elif have == hi:
            out.append(Finding(
                TIGHT, "אורך יום",
                f"כיתה {c.name}: {have} שעות — כל יום במקסימום, אין גמישות."))
    return out


def _daily_supply(reqs) -> list[Finding]:
    """Teacher-hours available on each day vs. class-hours that must be filled."""
    out = []
    for d in range(len(DAYS)):
        demand = sum(c.bounds(d)[0] for c in school.CLASSES)
        available = [t for t in school.TEACHERS if d not in certainly_off(t)]
        supply = sum(day_capacity(t, d) for t in available)
        if supply < demand:
            out.append(Finding(
                ERROR, "כיסוי יומי",
                f"{DAYS[d]}: הכיתות זקוקות ל-{demand} שעות הוראה אך זמינות "
                f"רק {supply} ({len(available)} מורות)."))
        elif supply == demand:
            out.append(Finding(
                TIGHT, "כיסוי יומי",
                f"{DAYS[d]}: {demand} שעות נדרשות מול {supply} זמינות — רוויה "
                f"מוחלטת. כל אחת מ-{len(available)} המורות חייבת ללמד את כל "
                f"היום, ללא חלונות וללא גמישות."))
    return out


def _subject_day_spread(reqs) -> list[Finding]:
    """A subject split into k meetings needs k different days to put them on."""
    out = []
    for r in reqs:
        if r.klass is None:
            continue
        meetings = len(r.pattern)
        blocked: set[int] = set()
        limit = len(DAYS)
        for name in r.teachers:
            t = school.BY_NAME[name]
            blocked |= certainly_off(t)
            if t.exact_working_days is not None:
                limit = min(limit, t.exact_working_days)
        days = min(len(DAYS) - len(blocked), limit)
        # A double needs a day long enough to hold two consecutive periods.
        if max(r.pattern) > 1:
            klass = next(c for c in school.CLASSES if c.name == r.klass)
            days = min(days, sum(1 for d in range(len(DAYS))
                                 if d not in blocked
                                 and klass.bounds(d)[1] >= max(r.pattern)))
        if meetings > days:
            out.append(Finding(
                ERROR, "פיזור מקצוע",
                f"{r.klass}/{r.subject}: נדרשים {meetings} ימים נפרדים אך "
                f"למורות ({'/'.join(r.teachers)}) יש רק {days} ימים משותפים."))
        elif meetings == days and meetings > 1:
            out.append(Finding(
                TIGHT, "פיזור מקצוע",
                f"{r.klass}/{r.subject}: {meetings} מפגשים ב-{days} ימים "
                f"אפשריים — הימים נקבעים מאליהם "
                f"({', '.join(DAYS[d] for d in range(len(DAYS)) if d not in blocked)})."))
    return out


def _class_day_coverage(reqs) -> list[Finding]:
    """Can each class actually be *covered* on each day?

    A class must be taught every period of its day.  Only teachers who
    already teach that class can do it, and only on days they are not off.
    If their combined hours fall short of the class's shortest possible day,
    no timetable exists — whatever the rest of the model says.
    """
    hours: dict[tuple[str, str], int] = {}
    for r in reqs:
        if not r.klass:
            continue
        for name in r.teachers:
            hours[(r.klass, name)] = hours.get((r.klass, name), 0) + r.hours

    out = []
    for c in school.CLASSES:
        staff = [(n, h) for (k, n), h in hours.items() if k == c.name]
        for d in range(len(DAYS)):
            # The preferred length is the real requirement where the school
            # stated one; bounds are only what is physically permitted.
            need = c.preferred.get(d, c.bounds(d)[0])
            avail = [(n, h) for n, h in staff
                     if d not in certainly_off(school.BY_NAME[n])]
            supply = sum(min(h, day_capacity(school.BY_NAME[n], d))
                         for n, h in avail)
            if supply < need:
                missing = sorted((n for n, _ in staff
                                  if d in certainly_off(school.BY_NAME[n])))
                out.append(Finding(
                    ERROR, "כיסוי כיתה",
                    f"כיתה {c.name} ב{DAYS[d]}: נדרשות {need} שעות, אך המורות "
                    f"של הכיתה שזמינות ביום זה יכולות לתת {supply} בלבד "
                    f"({', '.join(f'{n} {h}' for n, h in sorted(avail))}). "
                    f"חופשי ביום זה: {', '.join(missing)}."))
            elif supply == need:
                out.append(Finding(
                    TIGHT, "כיסוי כיתה",
                    f"כיתה {c.name} ב{DAYS[d]}: {need} שעות נדרשות מול {supply} "
                    f"זמינות — כל שעה של כל מורה זמינה חייבת ללכת לכיתה זו."))
    return out


def _minimum_day_length(reqs) -> list[Finding]:
    """A floor per working day caps how many days a teacher can spread over."""
    load = _load_by_teacher(reqs)
    out = []
    for t in school.TEACHERS:
        need = load.get(t.name, 0)
        if not need:
            continue
        max_days = need // t.min_per_day
        must_work = len(DAYS) - len(certainly_off(t))
        spec = school.OPEN_OWN_CLASS.get(t.homeroom or "")
        if spec and spec[0] > max_days:
            out.append(Finding(
                ERROR, "מינימום שעות ליום",
                f"{t.name}: {need} שעות ולפחות {t.min_per_day} ליום מאפשרים "
                f"{max_days} ימי עבודה, אך היא נדרשת לפתוח את כיתה "
                f"{t.homeroom} ב-{spec[0]} ימים."))
        elif max_days < must_work:
            out.append(Finding(
                INFO, "מינימום שעות ליום",
                f"{t.name}: {need} שעות ולפחות {t.min_per_day} ליום — לכל היותר "
                f"{max_days} ימי עבודה מתוך {must_work} אפשריים, כלומר תקבל "
                f"ימים חופשיים נוספים."))
    return out


def fifth_day_ceiling(t: Teacher, need: int) -> tuple[int, int] | None:
    """Most hours (ד) would let this homeroom teacher teach in a week.

    On the prescribed day she skips period 1, keeps a window, and stops at
    her class's last period L, so she teaches at most L-2 hours.  (ד) says
    that day is her *longest*, which caps every other day at the same number.
    Returns (per-day cap, weekly ceiling), or None when (ד) is switched off.
    """
    if not school.FIFTH_DAY.get("longest"):
        return None
    by_class = {c.name: c for c in school.CLASSES}
    c = by_class.get(t.homeroom or "")
    if c is None:
        return None
    # The class's real day length, not the widest the bounds allow: where the
    # school stated an exact figure, that figure is the day.
    last = max(c.preferred.get(d, c.bounds(d)[1])
               for d in model.full_days())
    cap = last - (1 if school.FIFTH_DAY["late_start"] else 0)                - (1 if school.FIFTH_DAY["window"] else 0)
    if t.max_per_day is not None:
        cap = min(cap, t.max_per_day)
    ceiling = max(sum(min(cap, day_capacity(t, d))
                      for d in range(len(DAYS)) if d not in off)
                  for off in possible_off_sets(t))
    return cap, ceiling


def fifth_day_longest_impossible(reqs) -> dict[str, tuple[int, int, int]]:
    """{teacher: (cap, ceiling, load)} for whom (ד) is disproved by counting."""
    load = _load_by_teacher(reqs)
    out = {}
    for t in school.TEACHERS:
        need = load.get(t.name, 0)
        got = fifth_day_ceiling(t, need)
        if got and need > got[1]:
            out[t.name] = (got[0], got[1], need)
    return out


def viable_days_off(reqs) -> dict[str, tuple[int, ...]]:
    """Narrow every `off_choice` to the days that leave her classes coverable.

    A teacher's day off removes her hours from every class she teaches.  If
    the remaining staff of one of those classes cannot fill that class's day,
    the choice is not a choice at all.  Counting settles it before the solver
    explores the branch.
    """
    by_class = {c.name: c for c in school.CLASSES}
    hours: dict[tuple[str, str], int] = {}
    for r in reqs:
        if not r.klass:
            continue
        for n in r.teachers:
            hours[(r.klass, n)] = hours.get((r.klass, n), 0) + r.hours

    out: dict[str, tuple[int, ...]] = {}
    for t in school.TEACHERS:
        if not t.off_choice:
            continue
        viable = []
        for d in t.off_choice:
            ok = True
            for k in {kk for (kk, n) in hours if n == t.name}:
                c = by_class[k]
                need = c.preferred.get(d, c.bounds(d)[0])
                supply = sum(
                    min(h, day_capacity(school.BY_NAME[n], d))
                    for (kk, n), h in hours.items()
                    if kk == k and n != t.name
                    and d not in certainly_off(school.BY_NAME[n]))
                if supply < need:
                    ok = False
                    break
            if ok:
                viable.append(d)
        out[t.name] = tuple(viable)
    return out


def _day_off_narrowing(reqs) -> list[Finding]:
    out = []
    for name, viable in viable_days_off(reqs).items():
        t = school.BY_NAME[name]
        if not viable:
            out.append(Finding(
                ERROR, "יום חופש",
                f"{name}: אף אחת מהאפשרויות "
                f"({', '.join(DAYS[d] for d in t.off_choice)}) אינה מותירה את "
                f"כיתותיה ניתנות לכיסוי."))
        elif len(viable) < len(t.off_choice):
            dropped = [d for d in t.off_choice if d not in viable]
            out.append(Finding(
                TIGHT, "יום חופש",
                f"{name}: מתוך {', '.join(DAYS[d] for d in t.off_choice)} "
                f"נותר רק {', '.join(DAYS[d] for d in viable)} — "
                f"{', '.join(DAYS[d] for d in dropped)} אינו אפשרי (הכיתה לא "
                f"תכוסה). היום נקבע מאליו והפותר מקבל אותו כנתון."))
    return out


def _fifth_day_capacity(reqs) -> list[Finding]:
    """§11.2(ד) — settled before the solver spends a second on it."""
    out = []
    by_name = {t.name: t for t in school.TEACHERS}
    for name, (cap, ceiling, need) in fifth_day_longest_impossible(reqs).items():
        t = by_name[name]
        out.append(Finding(
            ERROR, "יום חמישי (§11.2ד)",
            f"{name}: ביום המבוקש היא מוגבלת ל-{cap} שעות (כיתה {t.homeroom} "
            f"מסתיימת בשעה {cap + 2}, בלי שעה 1 ובלי שעת החלון), וסעיף (ד) "
            f"מחיל את התקרה הזו על כל ימיה — לכל היותר {ceiling} שעות בשבוע "
            f"מול {need} שעות שיש לה. (ד) אינו אפשרי עבורה; (א)(ב)(ג) כן, "
            f"והפותר אוכף אותם."))
    return out



def _homeroom_openings(reqs) -> list[Finding]:
    """A homeroom teacher cannot open her class on more days than she works."""
    out = []
    for t in school.TEACHERS:
        spec = school.OPEN_OWN_CLASS.get(t.homeroom or "")
        if spec is None:
            continue
        minimum, _ = spec
        days = len(DAYS) - len(certainly_off(t))
        if t.exact_working_days is not None:
            days = min(days, t.exact_working_days)
        if minimum > days:
            out.append(Finding(
                ERROR, "פתיחת יום",
                f"{t.name} אמורה לפתוח את כיתה {t.homeroom} ב-{minimum} ימים "
                f"אך עובדת {days} ימים בלבד."))
        elif minimum == days:
            out.append(Finding(
                TIGHT, "פתיחת יום",
                f"{t.name} חייבת לפתוח את כיתה {t.homeroom} בכל אחד "
                f"מ-{days} ימי העבודה שלה."))
    return out


def late_start_days(t: Teacher) -> tuple[int, ...]:
    """Days that can still carry this teacher's one late start.

    A shortened day cannot (too few periods, and the homerooms saturate it),
    nor can a day she is off, nor a day the school named for her to open her
    own class, nor — for the teachers in `school.LATE_START_AWAY_FROM_OFF` —
    either neighbour of a day off.  Taken as the best case over the ways an
    `off_choice` could resolve, so an empty answer really does prove there is
    no late start to be had.
    """
    guard = t.name in school.LATE_START_AWAY_FROM_OFF
    named = set(school.OPEN_ON_DAY.get(t.name, ()))
    best: set[int] = set()
    for off in possible_off_sets(t) or [frozenset()]:
        days = set(model.full_days()) - set(off) - named
        if guard:
            for d in off:
                days -= {d - 1, d + 1}
        best |= days
    return tuple(sorted(best))


def _late_start_choice(reqs) -> list[Finding]:
    """How much room is left for the one late-start day, once the days off,
    the named opening days and their neighbours are taken out."""
    out = []
    for name in school.LATE_START_AWAY_FROM_OFF:
        t = school.BY_NAME.get(name)
        if t is None:
            continue
        days = late_start_days(t)
        off = ", ".join(DAYS[d] for d in sorted(certainly_off(t))) or "—"
        if not days:
            out.append(Finding(
                ERROR, "התחלה מאוחרת",
                f"{name}: אין יום שיכול לשאת את ההתחלה המאוחרת — חופש ב{off}, "
                f"שני שכניו פסולים, וגם "
                f"{', '.join(DAYS[d] for d in school.OPEN_ON_DAY.get(name, ()))}"
                f" שמור לפתיחת הכיתה."))
        elif len(days) == 1:
            out.append(Finding(
                TIGHT, "התחלה מאוחרת",
                f"{name}: ההתחלה המאוחרת נקבעת מאליה ל{DAYS[days[0]]} — "
                f"חופש ב{off} פוסל גם את שכניו, ו"
                f"{', '.join(DAYS[d] for d in school.OPEN_ON_DAY.get(name, ()))}"
                f" שמור לפתיחת הכיתה."))
        else:
            out.append(Finding(
                INFO, "התחלה מאוחרת",
                f"{name}: ההתחלה המאוחרת יכולה ליפול ב"
                f"{', '.join(DAYS[d] for d in days)}."))
    return out


def summarise(findings: list[Finding]) -> tuple[int, int]:
    return (sum(1 for f in findings if f.level == ERROR),
            sum(1 for f in findings if f.level == TIGHT))
