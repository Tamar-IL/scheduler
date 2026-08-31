# -*- coding: utf-8 -*-
"""The school being scheduled — תשפ"ז.

Transcribed from the two source files.  This is the only module that is
specific to this school; everything else is general.

Where the sources disagree with themselves the *subject table* is treated as
authoritative and the staff-table totals as advisory: `checks.py` reports
every mismatch rather than silently picking a side.
"""
from __future__ import annotations

from model import (FRI, MON, SUN, THU, TUE, WED, Requirement, SchoolClass,
                   Teacher)

# --------------------------------------------------------------- teachers

TEACHERS = [
    Teacher("אלישבע גלילי",  declared=22, off_choice=(MON, TUE),
            max_per_day=5, homeroom="א"),
    Teacher("זהבי ליוי",     declared=22, off_fixed=(WED,),
            max_long_days=1, homeroom="ב1"),
    Teacher("חיה לוי",       declared=22, off_fixed=(TUE,),
            max_per_day=5, homeroom="ב2"),
    Teacher("יהודית כהן",    declared=22, off_fixed=(MON,), homeroom="ג"),
    Teacher("סימה ביטון",    declared=22, off_choice=(MON, TUE, THU),
            homeroom="ד1"),
    Teacher("אושרת אלבז",    declared=22, off_choice=(TUE, WED),
            max_late_days=2, latest_period_on={THU: 5}, homeroom="ד2"),
    Teacher("תמר אליהו",     declared=22, off_choice=(TUE, WED),
            max_late_days=2, homeroom="ה"),
    Teacher("מוריה נקי",     declared=22, off_fixed=(MON,), homeroom="ו"),
    Teacher("הדסה ויזמן",    declared=18, off_fixed=(THU,), min_per_day=2,
            homeroom="ז"),
    Teacher("רותי אסולין",   declared=19, off_fixed=(WED,), min_per_day=2,
            latest_period_on={MON: 5}, homeroom="ח"),
    Teacher("חנה אביטן",     declared=17, off_fixed=(TUE, FRI),
            forbidden_periods=(7,)),
    Teacher("סימה זאבי",     declared=22, off_fixed=(TUE, FRI)),
    Teacher("הודיה חבשוש",   declared=20, off_fixed=(SUN, FRI),
            exact_working_days=4),
    Teacher("רבקה טסה",      declared=14, off_fixed=(SUN, FRI),
            exact_working_days=3),
    Teacher("אפרת נתנאל",    declared=21, off_fixed=(SUN, FRI)),
    Teacher("יוכי זר",       declared=19, off_fixed=(TUE, FRI),
            min_late_days=2),
    Teacher("מרגלית לוי",    declared=15, off_fixed=(FRI,), min_per_day=2),
]

BY_NAME = {t.name: t for t in TEACHERS}

#: Pairs of teachers forbidden from taking the same day off.
DISTINCT_DAYS_OFF = [("סימה ביטון", "אושרת אלבז")]

#: Homeroom teachers of ג–ח must, at least once a week, start after the first
#: period *and* finish late.
LATE_START_AND_END = ["יהודית כהן", "סימה ביטון", "אושרת אלבז",
                      "תמר אליהו", "מוריה נקי", "הדסה ויזמן", "רותי אסולין"]

#: אלישבע asked to begin one day a week at the 2nd or 3rd period.
LATE_START_ONLY = {"אלישבע גלילי": (2, 3)}

#: Load spreading (≈2 long + 2 short days) is not attempted for these.
NO_SPREAD_PREFERENCE = {"רבקה טסה", "זהבי ליוי"}

# ---------------------------------------------------------------- classes

_LOWER = ["א", "ב1", "ב2"]          # exactly 5 hours a day
_MIDDLE = ["ג", "ד1", "ד2", "ה"]    # 5–6
_UPPER = ["ו", "ז", "ח"]            # 5–7

CLASS_NAMES = _LOWER + _MIDDLE + _UPPER


def _spans(lo: int, hi: int) -> dict[int, tuple[int, int]]:
    span = {d: (lo, hi) for d in (SUN, MON, TUE, WED, THU)}
    span[FRI] = (4, 4)   # every class does exactly 4 hours on Friday
    return span


CLASSES = (
    # "כיתות א-ב לומדות 5 שיעורים ביום" is stated flatly, but כיתה ב2 cannot be
    # covered on Tuesday under it (see checks._class_day_coverage).  It is kept
    # as a strongly-priced preference so the solver reports the shortfall
    # instead of failing silently.
    [SchoolClass(c, _spans(4, 6), preferred={d: 5 for d in (SUN, MON, TUE, WED, THU)})
     for c in _LOWER]
    + [SchoolClass(c, _spans(5, 6)) for c in _MIDDLE]
    + [SchoolClass(c, _spans(5, 7)) for c in _UPPER]
)

#: Upper classes should get at least one day of exactly 5 hours (soft).
PREFER_ONE_SHORT_DAY = _UPPER

#: Weekly hours per class as the summary row of the source file states them.
#: Advisory, like the staff-table totals: where it disagrees with the subject
#: table `checks._class_totals` reports the gap and the subject table wins.
CLASS_DECLARED_HOURS = {"א": 29, "ב1": 29, "ב2": 29, "ג": 31, "ד1": 32,
                        "ד2": 32, "ה": 32, "ו": 34, "ז": 35, "ח": 35}

# ----------------------------------------------------------------- lessons
# Each row: subject -> {class: "teacher:hours"}.  A "|" joins teachers who
# teach the class in parallel (הקבצה) — both are busy, the class is taught once.

_TABLE: dict[str, dict[str, str]] = {
    "תורה": {"א": "אלישבע גלילי:4", "ב1": "זהבי ליוי:4", "ב2": "חיה לוי:4",
             "ג": "יהודית כהן:4", "ד1": "סימה ביטון:4", "ד2": "אושרת אלבז:4",
             "ה": "תמר אליהו:5", "ו": "מוריה נקי:5", "ז": "הדסה ויזמן:5",
             # 4, not 5: the school gave כיתה ח's Sunday opening to היסטוריה
             # (see PINNED) and took the hour out of תורה rather than adding
             # one to רותי's week.
             "ח": "רותי אסולין:4"},
    "נביא": {"ג": "רבקה טסה:2", "ד1": "סימה ביטון:2", "ד2": "אושרת אלבז:2",
             "ה": "רותי אסולין:2", "ו": "מוריה נקי:2", "ז": "הדסה ויזמן:2",
             "ח": "רותי אסולין:2"},
    "יהדות/נושא": {"א": "אלישבע גלילי:3", "ב1": "זהבי ליוי:2", "ב2": "חיה לוי:2",
                   "ג": "יהודית כהן:2", "ד1": "סימה ביטון:2", "ד2": "אושרת אלבז:2",
                   "ה": "תמר אליהו:1", "ו": "מוריה נקי:1", "ז": "הדסה ויזמן:2",
                   "ח": "רותי אסולין:2"},
    "דינים": {"ג": "יהודית כהן:1", "ד1": "סימה ביטון:1", "ד2": "אושרת אלבז:1",
              "ה": "תמר אליהו:1", "ו": "מוריה נקי:1", "ז": "הדסה ויזמן:1",
              "ח": "תמר אליהו:2"},
    "פ. אבות": {"ו": "סימה זאבי:1", "ז": "סימה זאבי:1", "ח": "סימה זאבי:1"},
    "ב. תפילה": {"ב1": "יוכי זר:1", "ב2": "מוריה נקי:1", "ג": "יהודית כהן:1",
                 "ד1": "סימה ביטון:1", "ד2": "תמר אליהו:1", "ה": "תמר אליהו:1"},
    "פ. שבוע": {"א": "אלישבע גלילי:1", "ב1": "זהבי ליוי:1", "ב2": "חיה לוי:1",
                "ג": "הודיה חבשוש:1", "ד1": "סימה ביטון:1", "ד2": "אושרת אלבז:1",
                "ה": "תמר אליהו:1", "ו": "מוריה נקי:1", "ז": "הדסה ויזמן:1",
                "ח": "רותי אסולין:1"},
    "דקדוק": {"ה": "תמר אליהו:2", "ו": "הדסה ויזמן:2", "ז": "הדסה ויזמן:2",
              "ח": "הדסה ויזמן:2"},
    "לשון": {"ב1": "מוריה נקי:1", "ב2": "מוריה נקי:1", "ג": "מוריה נקי:1",
             "ד1": "סימה ביטון:1", "ד2": "אושרת אלבז:1"},
    # ב1/ב2 swapped (§11.4): זהבי teaches כתיב in חיה's class and חיה in
    # זהבי's.  זהבי works Tuesday and חיה does not, and כתיב is the fifth and
    # last lesson that makes ב2's Tuesday coverable at all.
    "כתיב": {"א": "אלישבע גלילי:1", "ב1": "חיה לוי:1", "ב2": "זהבי ליוי:1",
             "ג": "סימה ביטון:1", "ד1": "סימה ביטון:1", "ד2": "אושרת אלבז:1"},
    "ספרות-עברית": {"א": "הודיה חבשוש:1", "ב1": "זהבי ליוי:1", "ב2": "חיה לוי:1",
                    "ג": "חיה לוי:1", "ד1": "סימה ביטון:1", "ד2": "אושרת אלבז:1",
                    "ה": "תמר אליהו:1", "ו": "מוריה נקי:1", "ז": "הדסה ויזמן:1",
                    "ח": "חנה אביטן:1"},
    # הבעה is back to the original table (§11.4 moved the swap to כתיב).
    "הבעה": {"א": "אלישבע גלילי:1", "ב1": "זהבי ליוי:1", "ב2": "חיה לוי:1",
             "ג": "יהודית כהן:1", "ד1": "סימה ביטון:1", "ד2": "סימה ביטון:1",
             "ה": "תמר אליהו:1", "ו": "מוריה נקי:1", "ז": "חנה אביטן:1",
             "ח": "רותי אסולין:1"},
    "ה. הנקרא": {"א": "אלישבע גלילי:2", "ב1": "זהבי ליוי:2", "ב2": "חיה לוי:2",
                 "ג": "יהודית כהן:2", "ד1": "סימה ביטון:2", "ד2": "סימה ביטון:2",
                 "ה": "תמר אליהו:2", "ו": "מוריה נקי:2", "ז": "חנה אביטן:1",
                 "ח": "רותי אסולין:1"},
    "אנגלית": {"א": "אפרת נתנאל:1", "ב1": "אפרת נתנאל:1", "ב2": "אפרת נתנאל:1",
               "ג": "אפרת נתנאל:2", "ד1": "אפרת נתנאל:3", "ד2": "אפרת נתנאל:3",
               "ה": "הודיה חבשוש:3",
               "ו": "הודיה חבשוש|רבקה טסה:4", "ז": "הודיה חבשוש|רבקה טסה:4",
               "ח": "הודיה חבשוש|רבקה טסה:4"},
    "חשבון": {"א": "אלישבע גלילי:5", "ב1": "זהבי ליוי:5", "ב2": "חיה לוי:5",
              "ג": "יהודית כהן:4", "ד1": "אושרת אלבז:4", "ד2": "אושרת אלבז:4",
              "ה": "מרגלית לוי:4",
              "ו": "סימה זאבי|מרגלית לוי:5", "ז": "חנה אביטן|סימה זאבי:6",
              "ח": "סימה זאבי|מרגלית לוי:6"},
    # הנדסה is folded into חשבון for ז and ח (confirmed with the user).
    "הנדסה": {"א": "זהבי ליוי:1", "ב1": "חיה לוי:1", "ב2": "חיה לוי:1",
              "ג": "תמר אליהו:1", "ד1": "זהבי ליוי:1", "ד2": "זהבי ליוי:1",
              "ה": "תמר אליהו:1", "ו": "סימה זאבי:1"},
    "היסטוריה": {"ו": "חנה אביטן:1", "ז": "חנה אביטן:2", "ח": "רותי אסולין:2"},
    "מדעים-טבע": {"א": "אפרת נתנאל:2", "ב1": "חנה אביטן:2", "ב2": "חנה אביטן:2",
                  "ג": "חנה אביטן:2", "ד1": "אפרת נתנאל:2", "ד2": "אפרת נתנאל:2",
                  "ה": "אפרת נתנאל:2", "ו": "אפרת נתנאל:2", "ז": "הודיה חבשוש:2",
                  "ח": "הודיה חבשוש:2"},
    'ג"ג-מולדת': {"ב1": "יוכי זר:1", "ב2": "יוכי זר:1", "ג": "מוריה נקי:1",
                  "ד1": "חנה אביטן:1", "ד2": "חנה אביטן:1", "ה": "תמר אליהו:1",
                  "ו": "תמר אליהו:1", "ז": "רותי אסולין:2", "ח": "מוריה נקי:2"},
    "חינוך גופני": {c: "יוכי זר:1" for c in CLASS_NAMES},
    "אומנות": {"א": "יוכי זר:1", "ב1": "יוכי זר:1", "ב2": "יוכי זר:1",
               "ג": "יוכי זר:1", "ד1": "יוכי זר:1", "ד2": "יוכי זר:1",
               "ה": "סימה זאבי:1", "ו": "סימה זאבי:1"},
    "ציור/זמרה": {"א": "אלישבע גלילי:1", "ב1": "זהבי ליוי:1", "ב2": "חיה לוי:1"},
    'זה"ב': {"ב1": "יהודית כהן:1", "ב2": "יהודית כהן:1", "ג": "יהודית כהן:1",
             "ד1": "יהודית כהן:1", "ד2": "יהודית כהן:1", "ה": "יהודית כהן:1"},
    "קריאה": {"א": "אלישבע גלילי:3"},
    "חברה": {"א": "אלישבע גלילי:1", "ב1": "זהבי ליוי:1", "ב2": "חיה לוי:1",
             "ג": "יהודית כהן:1", "ד1": "סימה ביטון:1", "ד2": "אושרת אלבז:1",
             "ה": "תמר אליהו:1", "ו": "מוריה נקי:1", "ז": "הדסה ויזמן:1",
             "ח": "רותי אסולין:1"},
}

#: Not taught to any class — occupies סימה זאבי and shows only in her own grid.
NON_TEACHING = [("ספריה", "סימה זאבי", 1)]

#: Preferably not in the first period of the day (soft).
NOT_FIRST_PERIOD = {"אומנות"}

#: Teachers who asked for no window at all.  The rest of the school wants one
#: (see WINDOW_MAJORITY); these are the exceptions, so they are excluded from
#: that majority target and never priced for having none.  Hard: unlike the
#: general window rules this one is a *ceiling*, and a ceiling of zero is
#: always satisfiable by packing her day — it cannot be forced on her the way
#: a window can.
NO_WINDOW_TEACHERS = ("סימה זאבי",)

#: (teacher, subject) pairs whose lessons must be the last thing that teacher
#: does that day.  סימה זאבי teaches אומנות to ה and ו and asked for it at the
#: end of her day, never at its start — NOT_FIRST_PERIOD above is the weaker,
#: school-wide version of the same wish and still covers יוכי זר's אומנות.
SUBJECT_ENDS_DAY = (("סימה זאבי", "אומנות"),)

#: subject -> (earliest, latest) period it may occupy.  ספריה sits mid-morning.
SUBJECT_PERIODS = {"ספריה": (2, 4)}

#: (class, subject) -> (earliest, latest) period, narrowing SUBJECT_PERIODS for
#: one class only.  מדעים-טבע in כיתה ו was landing at the seventh period; the
#: school asked for it out of the tail of the day, and only for that class.
CLASS_SUBJECT_PERIODS = {("ו", "מדעים-טבע"): (1, 5)}

#: (class, subject) -> how many of that class's meetings of the subject the
#: school wants at the first period of the day.  The class's opening period
#: belongs to its homeroom teacher on four days of five (§11.1), so this can
#: only land on her fifth.  That day is available — forcing the slot with
#: everything else hard solves — but taking it costs a window off its
#: teacher's longest day, which §14 prices above the whole mandatory tier.
#: So this stays low: it is had when it is cheap, and dropped when it is not.
SUBJECT_AT_DAY_START = {("ו", "מדעים-טבע"): 1}

#: A teacher allowed a two-lesson day must not spend it on the last periods —
#: coming in just for the tail of the day is what the rule forbids.
SHORT_DAY_BANNED_PERIODS = (6, 7)

#: Lessons the school placed itself: (class, day, period) -> subject.  That
#: class's lesson of that subject goes exactly there, and nothing else may
#: have the slot.  This is how one specific instruction — "היסטוריה opens
#: כיתה ח on Sunday" — enters the model without becoming a general rule.
PINNED = {
    ("ח", SUN, 1): "היסטוריה",
    ("ה", SUN, 6): 'ג"ג-מולדת',
    ("ה", THU, 4): "תורה",
}

#: (class, day) -> the last period that class may use.  "Remove the seventh
#: lesson on Sunday" is a statement about the class's day, not about any one
#: teacher: with H6 the day is a solid block, so capping its last period is
#: the same as fixing its length.
DAY_ENDS_AT = {
    ("ח", SUN): 6,
    ("ה", THU): 5,
}

#: teacher -> days she must open her own class on, beyond the count in
#: OPEN_OWN_CLASS.  Priced at the mandatory tier because it argues with
#: §11.2(א), which wants exactly one day she does *not* open: she works five
#: days, so naming one leaves four, which is the minimum §11.1 asks for.
#: Both ב homerooms were named: כיתות ב1 and ב2 start the week with their own
#: teacher.
OPEN_ON_DAY = {"חיה לוי": (SUN,), "זהבי ליוי": (SUN,)}

#: Teachers whose one late-start day must not touch a day they are off.  A
#: day off with a late start on either side of it leaves the class two
#: mornings running without its homeroom teacher, which is the thing the
#: school objected to — so the day *before* the day off is barred exactly
#: like the day after.  Priced at the mandatory tier: it narrows the choice
#: of the fifth day rather than adding a new demand, and where it leaves no
#: choice at all the breakdown should say so instead of returning INFEASIBLE.
LATE_START_AWAY_FROM_OFF = ("זהבי ליוי", "חיה לוי")

#: The lesson the school wants a homeroom teacher's opening period to be, on
#: the days she opens her own class (§11.1).  Not a constraint: `polish.py`
#: exchanges the opening lesson with that day's תורה where the exchange costs
#: nothing, and leaves the day alone where the class has no תורה in it.
OPENING_SUBJECT = "תורה"

#: class -> (minimum, preferred) days on which its homeroom teacher must teach
#: the first period of the day in that class.  Friday counts.  §11.1 raised
#: ז and ח from 3 to 4: the rule is now the same for all ten homerooms.
OPEN_OWN_CLASS = {c: (4, 4) for c in CLASS_NAMES}

#: §11.2 — each homeroom teacher works five days; four of them are opening
#: days, and the fifth must have a specific shape.  Each component is priced
#: on its own so an impossible one names the teacher *and* the component.
#: Set a component to False to stop asking for it.
FIFTH_DAY = {
    "late_start": True,    # (א) she does not teach period 1 at all
    "late_end": True,      # (ב) her last lesson is the class's last period
    # (ג) is deliberately OFF.  Read literally it puts the window on the
    # fifth day — but that day is by construction her *shortest* (it loses
    # period 1 and stops at her class's last period), and a window in a
    # three-hour day is a hole, not a rest.  The school's intent is
    # WINDOW_ON_LONGEST_DAY below, which is what actually gets enforced.
    "window": False,
    "longest": True,       # (ד) it is her longest day of the week
}

#: A homeroom teacher may have a *second* window in the week where the rest
#: of the rules need it — allowed, not required, and priced so it stays the
#: exception rather than the norm.
HOMEROOM_MAX_WINDOWS = 2

#: רצוי מאוד: a homeroom teacher should not be teaching the seventh period on
#: Thursday.  Desirable, not forbidden — so it is priced, never enforced.
NO_SEVENTH_ON = {THU}

#: One subject, in one set of classes, confined to one of several day sets.
#: אנגלית in ו–ח is the case that produced the rule: both options start on
#: Monday and end on Thursday and only the middle day differs, so the three
#: days are never consecutive.  רבקה טסה teaches all three classes in
#: parallel and works exactly three days, so choosing the option fixes her
#: working days too.  A list, because another school may well have two such
#: subjects; each entry is (subject, classes, day options).
SUBJECT_DAY_GROUPS = [
    ("אנגלית", ("ו", "ז", "ח"), ((MON, WED, THU), (MON, TUE, THU))),
]

#: subject -> (the day it must fall on, classes exempt from that rule).  The
#: exempt classes must NOT have it on that day.
SUBJECT_FIXED_DAY = {"פ. שבוע": (FRI, ("ג",))}

#: A window exists so the teacher can rest *between* lessons, which only
#: means anything on a day that is actually long.  So every window must fall
#: on her longest day of the week.  This replaces §11.2(ג).
WINDOW_ON_LONGEST_DAY = True

#: The school wants most teachers to have exactly one window, not none.
#: Set False to go back to minimising windows instead.
WINDOW_MAJORITY = True

#: Hours of חינוך a homeroom teacher carries on top of her lessons.  They are
#: not placed in the grid — no class period is spent on them — so they show
#: only in the hour count next to her name on the printed sheet.
HOMEROOM_EDUCATION_HOURS = 2


#: subject -> [(from this many weekly hours, this many double periods), …],
#: read in order, most demanding first.  Everything not listed is taught in
#: single periods.  חשבון gets two doubles plus two singles over four days;
#: אנגלית in the upper classes one double plus two singles over three.  A
#: shorter allocation degrades to the first rung that fits.
DOUBLE_PERIODS = {
    "חשבון": ((6, 2), (4, 1)),
    "אנגלית": ((4, 1),),
}


def _pattern(subject: str, hours: int) -> tuple[int, ...]:
    """How a weekly hour count is split into singles and doubles."""
    for threshold, doubles in DOUBLE_PERIODS.get(subject, ()):
        if hours >= threshold:
            n = min(doubles, hours // 2)
            return (2,) * n + (1,) * (hours - 2 * n)
    return (1,) * hours


def requirements() -> list[Requirement]:
    reqs: list[Requirement] = []
    for subject, row in _TABLE.items():
        for klass, spec in row.items():
            names, _, hours = spec.rpartition(":")
            teachers = tuple(names.split("|"))
            h = int(hours)
            reqs.append(Requirement(klass, subject, teachers, h,
                                    _pattern(subject, h)))
    for subject, teacher, hours in NON_TEACHING:
        reqs.append(Requirement(None, subject, (teacher,), hours, (1,) * hours))
    return reqs
