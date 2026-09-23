# -*- coding: utf-8 -*-
"""The constraint catalogue: every rule the engine knows, as a listable thing.

The engine states its rules in two places — hard ones as CP-SAT constraints
with a `relax` key that switches them off, priced ones as a penalty label
with a weight.  Neither is a list a person can read, and neither says what a
rule is *for*.  This module is that list: one entry per rule, carrying its
category, a Hebrew explanation, whether it is hard or priced, and the handle
the engine already exposes for it.

Nothing here re-implements a rule.  An entry is a *description of* a rule
plus the exact lever the solver already has, which is what keeps the screen
honest: switching a rule off on the screen produces a `relax` key or a zero
weight, and the solver's own behaviour changes.  A rule with no lever is
listed as `locked` and says so — no-double-booking has no meaningful "off".

Weights are the engine's tiers, so a priority chosen here is the same number
`solver.py` minimises.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import solver
from app import spec as spec_mod

# ------------------------------------------------------------- categories

SYSTEM = "system"        # universal: what any timetable must satisfy
TEACHER = "teacher"      # about a teacher's week
CLASS = "class"          # about a class's week
SCHOOL = "school"        # this school's own decisions
OTHER = "other"          # everything else the engine prices

CATEGORY_TITLES = {
    SYSTEM: "אילוצי מערכת כלליים",
    TEACHER: "אילוצים ברמת המורה",
    CLASS: "אילוצים ברמת הכיתה",
    SCHOOL: "אילוצים ייחודיים לבית הספר",
    OTHER: "כללי שיבוץ נוספים",
}
CATEGORY_ORDER = [SYSTEM, TEACHER, CLASS, SCHOOL, OTHER]

#: The priority ladder, exactly as the solver weighs it.  A rule set to 0 is
#: not minimised at all; a hard rule set to 0 is relaxed instead.
PRIORITIES = [
    (0, "כבוי"),
    (solver.W_MINOR, "נמוכה"),
    (solver.W_PREFER, "בינונית"),
    (solver.W_STRONG, "גבוהה"),
    (solver.W_MANDATORY, "חובה"),
    (solver.W_CRITICAL, "קריטית"),
]
PRIORITY_NAME = dict(PRIORITIES)


def priority_name(weight: int) -> str:
    """The nearest rung's name, so a hand-typed weight still reads."""
    if weight <= 0:
        return "כבוי"
    best = min((w for w, _ in PRIORITIES if w > 0),
               key=lambda w: (abs(w - weight), w))
    return PRIORITY_NAME[best]


# ----------------------------------------------------------------- entries

@dataclass
class Field:
    """One editable parameter of a rule.

    A rule is not only on or off: "at most five hours a day" is a number per
    teacher, and "these teachers want no window" is a list of names.  Those
    live in the `SchoolSpec`, so a field is a pair of closures over it —
    which keeps the knowledge of *where* a value lives next to the rule it
    belongs to, instead of in a form somewhere else that has to be kept in
    step.

    `kind` is what the screen draws:

    * `names`   — pick any number from `domain`; value is a list
    * `numbers` — an optional number per entry of `domain`; value is a map,
                  and a blank means "no limit", which is not the same as 0
    * `ranges`  — two numbers per entry, labelled by `parts`
    * `bools`   — a fixed set of switches, `parts` holding (key, label)
    * `int`     — one number for the whole school
    * `flag`    — one switch for the whole school
    """
    key: str
    label: str
    kind: str
    get: Callable[[spec_mod.SchoolSpec], Any]
    put: Callable[[spec_mod.SchoolSpec, Any], None]
    domain: Callable[[spec_mod.SchoolSpec], list[str]] = lambda s: []
    parts: tuple = ()
    help: str = ""
    minimum: int = 0
    maximum: int = 20


def _teachers(s):
    return s.teacher_names


def _classes(s):
    return s.class_names


def _subjects(s):
    return s.subjects


def _days(s):
    return list(s.grid.days)


def _homerooms(s):
    return [c.name for c in s.classes if s.homeroom_of(c.name)]


def _set_list(attr):
    """Replace a plain list on `rules`.

    Unknown names never reach here: `apply_fields` refuses them against the
    field's `domain` first, so a typo is an error rather than a quiet drop.
    """
    def put(spec, value):
        setattr(spec.rules, attr, [str(v) for v in value])
    return put


def _teacher_number(attr):
    """A per-teacher number, where blank means the limit does not apply."""
    def get(spec):
        return {t.name: getattr(t, attr) for t in spec.teachers}

    def put(spec, value):
        for t in spec.teachers:
            if t.name in value:
                raw = value[t.name]
                setattr(t, attr, None if raw in (None, "") else int(raw))
    return get, put


def _day_list(attr):
    """Days are indices in the engine and names on the screen."""
    def get(spec):
        return [spec.grid.days[int(d)] for d in getattr(spec.rules, attr)
                if 0 <= int(d) < len(spec.grid.days)]

    def put(spec, value):
        setattr(spec.rules, attr,
                sorted({spec.grid.index(v) for v in value}))
    return get, put


@dataclass
class Constraint:
    id: str
    category: str
    title: str
    explanation: str
    #: "hard" — a CP-SAT constraint; "soft" — a priced penalty.
    kind: str = "hard"
    #: The solver's `relax` key.  Empty means the rule cannot be switched off.
    relax_key: str = ""
    #: The penalty labels this rule produces, given the school.  A callable
    #: because several labels name the subject, class or day they are about.
    labels: Callable[[spec_mod.SchoolSpec], list[str]] = lambda s: []
    default_weight: int = 0
    #: What the rule currently applies to, for the screen's "scope" line.
    scope: Callable[[spec_mod.SchoolSpec], list[str]] = lambda s: []
    #: A rule with no lever at all: listed, explained, never switchable.
    locked: bool = False
    #: The parameters of the rule, editable on the constraint screen.  A rule
    #: with none is one whose shape comes from the uploaded files — the
    #: hand-placed lessons, the parallel-teaching groups — and the screen
    #: says so rather than offering a form that could only half work.
    fields: tuple = ()

    @property
    def switchable(self) -> bool:
        return not self.locked and bool(self.relax_key or self.kind == "soft")


def _names(*attrs: str):
    """Scope from a plain list-valued rule field."""
    def get(spec: spec_mod.SchoolSpec) -> list[str]:
        out: list[str] = []
        for a in attrs:
            value = getattr(spec.rules, a)
            out += [str(x) for x in (value.keys() if isinstance(value, dict)
                                     else value)]
        return out
    return get


def _label(*labels: str):
    return lambda spec: list(labels)


CATALOGUE: list[Constraint] = [
    # ---------------------------------------------------- universal / hard
    Constraint(
        "single_booking", SYSTEM, "מורה וכיתה במקום אחד בכל שעה",
        "אותה מורה אינה יכולה ללמד שתי כיתות באותה שעה, וכיתה אינה יכולה "
        "ללמוד שני שיעורים באותה שעה. זהו הבסיס של כל מערכת שעות ואין "
        "אפשרות לבטלו.",
        locked=True),
    Constraint(
        "all_placed", SYSTEM, "כל שיעור משובץ פעם אחת בדיוק",
        "כל שעה שנדרשה בטבלת השיעורים מקבלת מקום, ושיעור כפול נשאר רצוף "
        "באותו יום. אי אפשר לבטל — מערכת שאינה משבצת הכול אינה מערכת.",
        locked=True),
    Constraint(
        "contiguity", SYSTEM, "יום הכיתה הוא רצף אחד מהשעה הראשונה",
        "לכיתה אין חלונות: היום מתחיל בשעה 1 ונמשך ברצף עד סופו. זהו הכלל "
        "החזק ביותר במודל — הוא מה שהופך בעיית כיסוי לבלתי אפשרית ולא רק "
        "לא נוחה. ביטולו יאפשר חורים במערכת התלמידות.",
        relax_key="contiguity"),
    Constraint(
        "class_span", CLASS, "אורך יום הכיתה בתוך התחום שהוגדר",
        "לכל כיתה ולכל יום נקבע מינימום ומקסימום שעות. הפותר לא יחרוג מהם.",
        relax_key="class_span",
        scope=lambda s: [c.name for c in s.classes]),
    Constraint(
        "class_preferred_span", CLASS, "אורך היום המדויק שהכיתה ביקשה",
        "כיתה שנאמר עליה מספר שעות מדויק ליום — למשל חמש שעות בכיתות "
        "הנמוכות — תקבל בדיוק את המספר הזה. מתומחר ולא קשיח, כדי שחריגה "
        "תדווח במקום להחזיר 'אין פתרון'.",
        kind="soft", labels=_label("אורך יום חריג בכיתה"),
        default_weight=solver.W_MANDATORY,
        scope=lambda s: [c.name for c in s.classes if c.preferred]),
    Constraint(
        "subject_per_day", SYSTEM, "מקצוע פעם אחת ביום לכיתה",
        "אותו מקצוע לא יופיע פעמיים באותו יום באותה כיתה. זה מה שהופך "
        "'ארבע שעות חשבון' לפיזור על ארבעה ימים ולא לגוש אחד.",
        relax_key="subject_per_day"),

    # ------------------------------------------------------------ teachers
    Constraint(
        "teacher_off_days", TEACHER, "ימי חופש קבועים",
        "מורה לא תשובץ ביום שהוגדר לה כחופשי. יום חופש קבוע הוא קשיח תמיד; "
        "בחירה בין כמה ימים אפשריים נשלטת בכלל הבא.",
        locked=True,
        scope=lambda s: [t.name for t in s.teachers if t.off_fixed]),
    Constraint(
        "teacher_off_choice", TEACHER, "בחירת יום חופש מתוך כמה אפשרויות",
        "מורה שנאמר עליה 'חופשי בשני או בשלישי' תקבל בדיוק אחד מהם. "
        "בדיקות שלב 0 מצמצמות מראש בחירה שמשאירה כיתה ללא כיסוי.",
        relax_key="day_off",
        scope=lambda s: [t.name for t in s.teachers if t.off_choice]),
    Constraint(
        "teacher_forbidden_periods", TEACHER, "שעות אסורות ושעת סיום מוקדמת",
        "שעות שהמורה אינה זמינה בהן בכלל, ויום שבו היא חייבת לסיים מוקדם. "
        "אלה נתוני זמינות ולכן הם קשיחים תמיד.",
        locked=True,
        scope=lambda s: [t.name for t in s.teachers
                         if t.forbidden_periods or t.latest_period_on]),
    Constraint(
        "teacher_max_per_day", TEACHER, "תקרת שעות ליום",
        "מספר השעות המרבי שמורה מלמדת ביום אחד.",
        relax_key="max_per_day",
        scope=lambda s: [t.name for t in s.teachers
                         if t.max_per_day is not None],
        fields=(Field("max_per_day", "תקרת שעות ליום", "numbers",
                      *_teacher_number("max_per_day"), domain=_teachers,
                      minimum=1, maximum=14,
                      help="ריק = אין תקרה למורה הזו."),)),
    Constraint(
        "teacher_min_per_day", TEACHER, "רצפת שעות ליום עבודה",
        "מורה אינה מגיעה לבית הספר עבור שיעור אחד או שניים. מי שהותר לה "
        "יום של שעתיים לא תבזבז אותו על סוף היום.",
        locked=True,
        scope=lambda s: [f"{t.name} ({t.min_per_day})" for t in s.teachers],
        fields=(Field("min_per_day", "רצפת שעות ליום עבודה", "numbers",
                      *_teacher_number("min_per_day"), domain=_teachers,
                      minimum=1, maximum=8,
                      help="הכלל עצמו קבוע, אבל המספר לכל מורה ניתן "
                           "לשינוי. 3 הוא ברירת המחדל."),)),
    Constraint(
        "teacher_working_days", TEACHER, "מספר ימי עבודה מדויק",
        "מורה שהיקף משרתה קובע לה מספר ימים — שלושה, ארבעה — תעבוד בדיוק "
        "כך.",
        relax_key="working_days",
        scope=lambda s: [t.name for t in s.teachers
                         if t.exact_working_days is not None],
        fields=(Field("exact_working_days", "מספר ימי עבודה", "numbers",
                      *_teacher_number("exact_working_days"),
                      domain=_teachers, minimum=1, maximum=7,
                      help="ריק = מספר הימים נקבע מהשיבוץ."),)),
    Constraint(
        "distinct_off", TEACHER, "מורות שאינן יכולות להיעדר באותו יום",
        "זוגות מורות שחייבות יום חופש שונה — בדרך כלל מפני שהן מכסות זו "
        "את זו.",
        relax_key="distinct_off",
        scope=_names("distinct_days_off")),
    Constraint(
        "teacher_gaps", TEACHER, "תקרת חלונות שבועית",
        "חלון הוא שעה פנויה שיש לפניה ואחריה שיעור. הכלל מתיר לכל מורה "
        "חלון אחד בשבוע; למחנכת מותר שני חלונות, והשני מתומחר כדי שיישאר "
        "חריג.",
        relax_key="gaps",
        fields=(Field("homeroom_max_windows", "תקרת חלונות למחנכת", "int",
                      lambda s: s.rules.homeroom_max_windows,
                      lambda s, v: setattr(s.rules, "homeroom_max_windows",
                                           max(0, int(v))),
                      minimum=0, maximum=5),
                Field("max_windows", "תקרת חלונות למורה שאינה מחנכת",
                      "numbers", *_teacher_number("max_windows"),
                      domain=_teachers, minimum=0, maximum=5))),
    Constraint(
        "window_on_longest_day", TEACHER, "חלון רק ביום הארוך ביותר",
        "חלון קיים כדי לנוח, וזה אומר משהו רק ביום ארוך. לכן כל חלון חייב "
        "ליפול ביום הארוך ביותר של המורה. מתומחר מעל כל דרגת החובה, ולא "
        "קשיח: אם שיעוריה אינם ניתנים לרצף ביום מסוים נוצר לה חור בעל "
        "כורחה, ולגרסה הקשיחה אין פתרון.",
        kind="soft", labels=_label("חלון שאינו ביום הארוך ביותר"),
        default_weight=solver.W_CRITICAL, relax_key="window_day"),
    Constraint(
        "window_majority", TEACHER, "חלון לרוב המורות",
        "בית הספר רוצה שלרוב המורות יהיה חלון אחד, לא אף אחד. יעד ברמת "
        "הצוות שהחוסר בו מתומחר, ולצדו דחיפה עדינה לכל מורה שיכולה לקבל "
        "חלון.",
        kind="soft",
        labels=_label("חלון לרוב המורות", "מורה ללא חלון כלל",
                      "חלון שני למחנכת", "חלונות"),
        default_weight=solver.W_STRONG,
        fields=(Field("window_majority", "לשאוף לחלון לרוב המורות", "flag",
                      lambda s: bool(s.rules.window_majority),
                      lambda s, v: setattr(s.rules, "window_majority",
                                           bool(v)),
                      help="כבוי = המערכת תנסה למזער חלונות במקום לחלק "
                           "אותם."),)),
    Constraint(
        "no_window_teachers", TEACHER, "מורות שביקשו לא לקבל חלון כלל",
        "תקרה של אפס חלונות. בניגוד לדרישת חלון, תקרה כזו תמיד אפשרית — "
        "פשוט דוחסים את היום — ולכן היא קשיחה. מורה כזו גם אינה נספרת "
        "ביעד 'חלון לרוב המורות'.",
        relax_key="no_window", scope=_names("no_window_teachers"),
        fields=(Field("no_window_teachers", "מורות שביקשו ללא חלון", "names",
                      lambda s: list(s.rules.no_window_teachers),
                      _set_list("no_window_teachers"), domain=_teachers),)),
    Constraint(
        "teacher_long_days", TEACHER, "תקרת ימים ארוכים",
        "מורה שהוגבלה למספר ימים ארוכים בשבוע (יום ארוך = מספר השעות "
        "שהוגדר בלוח הזמנים של בית הספר).",
        relax_key="long_days",
        scope=lambda s: [t.name for t in s.teachers
                         if t.max_long_days is not None],
        fields=(Field("max_long_days", "מקסימום ימים ארוכים", "numbers",
                      *_teacher_number("max_long_days"), domain=_teachers,
                      minimum=0, maximum=7,
                      help="ריק = ללא הגבלה."),)),
    Constraint(
        "teacher_late_days", TEACHER, "ימים המסתיימים מאוחר",
        "כמה ימים בשבוע המורה מלמדת עד השעות האחרונות — תקרה למי שביקשה "
        "פחות, ורצפה כדי שהעומס המאוחר לא ייפול כולו על אותן מורות.",
        relax_key="late_days", kind="soft",
        labels=_label("סיום מאוחר: מספר ימים חסר"),
        default_weight=solver.W_MANDATORY,
        scope=lambda s: [t.name for t in s.teachers
                         if t.min_late_days or t.max_late_days is not None],
        fields=(Field("min_late_days", "מינימום ימי סיום מאוחר", "numbers",
                      *_teacher_number("min_late_days"), domain=_teachers,
                      minimum=0, maximum=6),
                Field("max_late_days", "מקסימום ימי סיום מאוחר", "numbers",
                      *_teacher_number("max_late_days"), domain=_teachers,
                      minimum=0, maximum=6, help="ריק = ללא הגבלה."))),
    Constraint(
        "same_class_five", TEACHER, "עד חמש שעות עם אותה כיתה ביום",
        "מורה לא תלמד את אותה כיתה יותר מחמש שעות ביום אחד.",
        relax_key="same_class_5"),
    Constraint(
        "teacher_spread", TEACHER, "פיזור: שני ימים קצרים למורה",
        "איזון בין ימים ארוכים לקצרים — שני ימים של עד ארבע שעות לכל מורה, "
        "פרט למי שהוחרגה מהעדפה זו.",
        kind="soft", labels=_label("פיזור: 2 ימים קצרים למורה"),
        default_weight=solver.W_MINOR,
        fields=(Field("no_spread_preference", "מורות שמוחרגות מהפיזור",
                      "names", lambda s: list(s.rules.no_spread_preference),
                      _set_list("no_spread_preference"), domain=_teachers,
                      help="מי שמסומנת כאן לא תישפט על חלוקת ימים ארוכים "
                           "וקצרים."),)),

    # ------------------------------------------------------------- classes
    Constraint(
        "prefer_one_short_day", CLASS, "יום קצר אחד לכיתה",
        "כיתה שהיום שלה יכול להימשך עד השעה השביעית תקבל לפחות יום אחד "
        "של חמש שעות.",
        kind="soft", labels=_label("יום קצר אחד לכיתה"),
        default_weight=solver.W_STRONG, scope=_names("prefer_one_short_day"),
        fields=(Field("prefer_one_short_day", "כיתות שיקבלו יום קצר", "names",
                      lambda s: list(s.rules.prefer_one_short_day),
                      _set_list("prefer_one_short_day"), domain=_classes),)),
    Constraint(
        "pinned_lessons", CLASS, "שיעורים שבית הספר קבע בעצמו",
        "שיעור שהוצב ידנית ביום ובשעה מסוימים, ויום שנקבע לו סוף מוקדם. "
        "אלה החלטות של בית הספר ולא כללים כלליים.",
        relax_key="pinned",
        scope=lambda s: [f"{p[0]} {s.grid.days[int(p[1])]} {p[2]}"
                         for p in s.rules.pinned]),
    Constraint(
        "class_subject_periods", CLASS, "מקצוע בטווח שעות מסוים בכיתה",
        "הגבלה על השעות שמקצוע מסוים יכול לתפוס בכיתה מסוימת — צרה יותר "
        "מההגבלה הכלל-בית-ספרית על אותו מקצוע. שתי ההגבלות נחתכות.",
        relax_key="class_periods", scope=_names("class_subject_periods")),
    Constraint(
        "subject_periods", OTHER, "מקצוע בטווח שעות קבוע",
        "מקצוע שמותר לו רק בחלק מסוים של היום — שעת ספרייה באמצע הבוקר, "
        "למשל. קשיח: זו זמינות של משאב, לא העדפה.",
        locked=True, scope=_names("subject_periods")),
    Constraint(
        "subject_fixed_day", OTHER, "מקצוע ביום קבוע בשבוע",
        "מקצוע שחייב ליפול ביום מסוים, עם רשימת כיתות שדווקא לא באותו יום.",
        locked=True, scope=_names("subject_fixed_day")),
    Constraint(
        "subject_day_groups", OTHER, "מקצוע המרוכז בקבוצת ימים אחת",
        "מקצוע שנלמד במקבילה בכמה כיתות ולכן חייב ליפול על אותה שלישיית "
        "ימים בכולן — מתוך רשימת אפשרויות סגורה.",
        relax_key="english_days",
        scope=lambda s: [g[0] for g in s.rules.subject_day_groups]),
    Constraint(
        "not_first_period", OTHER, "מקצועות שעדיף לא לפתוח בהם את היום",
        "העדפה בלבד: המקצוע יימנע מהשעה הראשונה אם אפשר.",
        kind="soft",
        labels=lambda s: [f"{x} בשעה ראשונה" for x in s.rules.not_first_period],
        default_weight=solver.W_PREFER, scope=_names("not_first_period"),
        fields=(Field("not_first_period", "מקצועות שלא בשעה הראשונה", "names",
                      lambda s: list(s.rules.not_first_period),
                      _set_list("not_first_period"), domain=_subjects),)),
    Constraint(
        "subject_ends_day", OTHER, "מקצוע שהוא סוף היום של המורה",
        "שיעור שאחריו המורה אינה מלמדת עוד באותו יום. נאמר על היום שלה, "
        "לא על היום של הכיתה — הכיתה עשויה להמשיך בלעדיה.",
        relax_key="ends_day",
        scope=lambda s: [" / ".join(p) for p in s.rules.subject_ends_day]),
    Constraint(
        "subject_at_day_start", OTHER, "מקצוע שפותח את היום בכיתה",
        "בקשה שמקצוע מסוים יפתח יום בכיתה מסוימת. מתומחר מתחת לכלל פתיחת "
        "היום של המחנכת, כדי שהדוח יגיד מי ויתר למי במקום ששני הכללים "
        "יעלו זה את מחירו של זה.",
        kind="soft",
        labels=lambda s: [f"{k.split('|')[1]} בכיתה {k.split('|')[0]}: "
                          f"פתיחת יום" for k in s.rules.subject_at_day_start],
        default_weight=solver.W_STRONG, scope=_names("subject_at_day_start")),

    # ------------------------------------------- this school's own choices
    Constraint(
        "open_own_class", SCHOOL, "המחנכת פותחת את היום בכיתתה",
        "המחנכת מלמדת את השעה הראשונה בכיתה שלה במספר ימים שנקבע. זהו "
        "הכלל שקובע את צורת השבוע של כל מחנכת.",
        kind="soft",
        labels=_label("מחנכת פותחת את היום בכיתתה",
                      "מחנכת: פתיחה מועדפת נוספת"),
        default_weight=solver.W_MANDATORY, scope=_names("open_own_class"),
        fields=(Field("open_own_class", "כמה ימים המחנכת פותחת", "ranges",
                      lambda s: {k: list(v)
                                 for k, v in s.rules.open_own_class.items()},
                      lambda s, v: setattr(
                          s.rules, "open_own_class",
                          {k: [int(x[0]), int(x[1])] for k, x in v.items()}),
                      domain=_homerooms, parts=("נדרש", "מועדף"),
                      minimum=0, maximum=7),)),
    Constraint(
        "open_on_day", SCHOOL, "יום פתיחה שנקבע בשם",
        "יום מסוים שבו מחנכת חייבת לפתוח את כיתתה — למשל תחילת השבוע. "
        "מתומחר מעל דרגת החובה מפני שהוא מתווכח עם כלל היום החמישי, ובלי "
        "הפרש מחיר הפותר אדיש בין השניים.",
        kind="soft", labels=_label("מחנכת: פתיחה ביום שנקבע"),
        default_weight=solver.W_CRITICAL, scope=_names("open_on_day")),
    Constraint(
        "fifth_day", SCHOOL, "היום החמישי של המחנכת",
        "המחנכת עובדת חמישה ימים; בארבעה היא פותחת את כיתתה, והיום החמישי "
        "מקבל צורה משלו: התחלה מאוחרת, סיום בשעה האחרונה של הכיתה, "
        "ואפשרות לדרוש שיהיה יומה הארוך ביותר. כל רכיב מתומחר בנפרד כדי "
        "שדוח העונשין יגיד איזו מחנכת ואיזה רכיב.",
        kind="soft", relax_key="fifth_day",
        fields=(Field("fifth_day", "רכיבי היום החמישי", "bools",
                      lambda s: dict(s.rules.fifth_day),
                      lambda s, v: s.rules.fifth_day.update(
                          {k: bool(x) for k, x in v.items()}),
                      parts=(("late_start", "אינה מלמדת שעה ראשונה"),
                             ("late_end", "מסיימת בשעה האחרונה של הכיתה"),
                             ("window", "החלון השבועי נופל ביום הזה"),
                             ("longest", "זהו יומה הארוך ביותר"))),),
        labels=_label("יום חמישי: מתחילה מאוחר",
                      "יום חמישי: מסיימת בשעה האחרונה של הכיתה",
                      "יום חמישי: חלון ביום זה",
                      "יום חמישי: היום הארוך ביותר"),
        default_weight=solver.W_MANDATORY),
    Constraint(
        "late_start_and_end", SCHOOL, "יום שמתחיל מאוחר ומסתיים מאוחר",
        "למחנכות שנקבע להן כך: לפחות יום אחד בשבוע שאינו מתחיל בשעה "
        "הראשונה וגם אינו נגמר מוקדם.",
        kind="soft", labels=_label("מחנכת: יום שמתחיל מאוחר ומסתיים מאוחר"),
        default_weight=solver.W_MANDATORY, scope=_names("late_start_and_end"),
        fields=(Field("late_start_and_end", "מחנכות שהכלל חל עליהן", "names",
                      lambda s: list(s.rules.late_start_and_end),
                      _set_list("late_start_and_end"), domain=_teachers),)),
    Constraint(
        "late_start_only", SCHOOL, "התחלה מאוחרת פעם בשבוע",
        "מורה שביקשה יום אחד המתחיל בשעה שנייה או שלישית.",
        kind="soft", labels=_label("מורה: התחלה מאוחרת פעם בשבוע"),
        default_weight=solver.W_MANDATORY, scope=_names("late_start_only")),
    Constraint(
        "late_start_away_from_off", SCHOOL, "התחלה מאוחרת לא בצמוד ליום חופש",
        "יום חופש שיש לצדו התחלה מאוחרת משאיר את הכיתה שני בקרים ברצף בלי "
        "המחנכת שלה. לכן שני שכניו של יום החופש נפתחים כרגיל — משני "
        "הצדדים, מפני שהנימוק הוא הכיתה ולא המורה.",
        kind="soft", labels=_label("מחנכת: התחלה מאוחרת בצמוד ליום חופש"),
        default_weight=solver.W_MANDATORY,
        scope=_names("late_start_away_from_off"),
        fields=(Field("late_start_away_from_off", "מחנכות שהכלל חל עליהן",
                      "names",
                      lambda s: list(s.rules.late_start_away_from_off),
                      _set_list("late_start_away_from_off"),
                      domain=_teachers),)),
    Constraint(
        "no_last_period_on", SCHOOL, "מחנכת בשעה האחרונה ביום שנקבע",
        "רצוי מאוד שמחנכת לא תלמד את השעה האחרונה ביום שסומן. רצוי — לא "
        "אסור — ולכן מתומחר.",
        kind="soft",
        labels=lambda s: [f"מחנכת בשעה האחרונה ביום {s.grid.days[d]}"
                          for d in s.rules.no_seventh_on],
        default_weight=solver.W_STRONG,
        scope=lambda s: [s.grid.days[d] for d in s.rules.no_seventh_on],
        fields=(Field("no_seventh_on", "הימים שהכלל חל עליהם", "names",
                      *_day_list("no_seventh_on"), domain=_days),)),
    Constraint(
        "anchor", OTHER, "היצמדות למערכת שאושרה",
        "כששואלים את המערכת שאלה חדשה על מערכת קיימת, כל שיעור שזז מהמקום "
        "שאושר עולה כסף. כך התשובה היא תיקון של המערכת שבית הספר כבר קרא, "
        "ולא מערכת חדשה שנראית אחרת לגמרי אף שהיא מנוקדת באותה מידה.",
        kind="soft", labels=_label("שינוי מהשיבוץ שאושר"),
        default_weight=solver.W_STRONG),
]

BY_ID = {c.id: c for c in CATALOGUE}


# ---------------------------------------------------------------- settings

@dataclass
class Settings:
    """One school's answers to the catalogue: on/off and priority."""
    #: {constraint id: {"enabled": bool, "weight": int}}
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)

    def state(self, c: Constraint) -> dict:
        o = self.overrides.get(c.id, {})
        return {"enabled": bool(o.get("enabled", True)),
                "weight": int(o.get("weight", c.default_weight))}

    def set(self, cid: str, enabled: bool | None = None,
            weight: int | None = None) -> None:
        c = BY_ID.get(cid)
        if c is None:
            raise KeyError(f"אין אילוץ בשם {cid}")
        if c.locked:
            raise ValueError(f"{c.title}: אילוץ שאי אפשר לשנות")
        o = self.overrides.setdefault(cid, {})
        if enabled is not None:
            o["enabled"] = bool(enabled)
        if weight is not None:
            o["weight"] = max(0, int(weight))

    # -- what the solver is actually given -------------------------------

    def relax(self) -> frozenset[str]:
        """Relax keys for every hard rule the administrator switched off."""
        return frozenset(c.relax_key for c in CATALOGUE
                         if c.relax_key and not self.state(c)["enabled"])

    def relax_ids(self) -> frozenset[str]:
        """The same rules, by catalogue id — what the validator filters on."""
        return frozenset(c.id for c in CATALOGUE
                         if c.relax_key and not self.state(c)["enabled"])

    def weights(self, spec: spec_mod.SchoolSpec) -> dict[str, int]:
        """Penalty-label overrides: a changed priority, or 0 for switched off.

        Only labels that actually differ from the engine's own default are
        returned, so an untouched school produces an empty dict and the
        solver behaves exactly as it did before the screen existed.
        """
        out: dict[str, int] = {}
        for c in CATALOGUE:
            if c.kind != "soft":
                continue
            st = self.state(c)
            weight = 0 if not st["enabled"] else st["weight"]
            if weight == c.default_weight:
                continue
            for label in c.labels(spec):
                out[label] = weight
        return out

    def to_dict(self) -> dict:
        return {"overrides": {k: dict(v) for k, v in self.overrides.items()}}


def from_dict(raw: dict | None) -> Settings:
    raw = raw or {}
    return Settings(overrides={k: dict(v) for k, v
                               in (raw.get("overrides") or {}).items()})


# ------------------------------------------------------------ presentation

def listing(spec: spec_mod.SchoolSpec, settings: Settings) -> list[dict]:
    """The constraint screen's data: categories, each with its rules."""
    groups = []
    for cat in CATEGORY_ORDER:
        items = []
        for c in CATALOGUE:
            if c.category != cat:
                continue
            st = settings.state(c)
            scope = c.scope(spec)
            items.append({
                "id": c.id, "title": c.title, "explanation": c.explanation,
                "kind": c.kind, "locked": c.locked,
                "switchable": c.switchable,
                "enabled": st["enabled"],
                "weight": st["weight"],
                "priority": priority_name(st["weight"]) if c.kind == "soft"
                            else ("קשיח" if st["enabled"] else "כבוי"),
                "default_weight": c.default_weight,
                "relax_key": c.relax_key,
                "labels": c.labels(spec),
                "scope": scope,
                "applies": len(scope),
                "fields": [{
                    "key": f.key, "label": f.label, "kind": f.kind,
                    "help": f.help, "domain": f.domain(spec),
                    "parts": [list(p) if isinstance(p, tuple) else p
                              for p in f.parts],
                    "min": f.minimum, "max": f.maximum,
                    "value": f.get(spec),
                } for f in c.fields],
            })
        groups.append({"id": cat, "title": CATEGORY_TITLES[cat],
                       "constraints": items})
    return groups


def apply_fields(spec: spec_mod.SchoolSpec, constraint_id: str,
                 values: dict) -> list[str]:
    """Write edited parameters back into the school.

    Returns what the change made invalid, or an empty list.  The check runs
    *after* the write and before the caller saves, so a name that no longer
    exists or a floor above a ceiling is refused as a whole rather than
    leaving the school half-edited — which is why the caller hands in a copy
    of the spec and keeps it only if this comes back empty.
    """
    c = BY_ID.get(constraint_id)
    if c is None:
        raise KeyError(f"אין אילוץ בשם {constraint_id}")
    known = {f.key: f for f in c.fields}
    for key, value in (values or {}).items():
        field = known.get(key)
        if field is None:
            raise ValueError(f"{c.title}: אין פרמטר בשם {key}")
        if field.kind == "names":
            allowed = set(field.domain(spec))
            unknown = [v for v in value if v not in allowed]
            if unknown:
                raise ValueError(f"{field.label}: {', '.join(unknown)} "
                                 f"אינם קיימים בבית הספר")
        field.put(spec, value)
    return spec.problems()


def by_label(spec: spec_mod.SchoolSpec) -> dict[str, Constraint]:
    """Penalty label -> the catalogue entry that produced it.

    This is what lets a penalty in the objective breakdown, or a violation in
    the conflict list, be shown with its explanation instead of as a bare
    Hebrew string.
    """
    out: dict[str, Constraint] = {}
    for c in CATALOGUE:
        for label in c.labels(spec):
            out.setdefault(label, c)
    return out
