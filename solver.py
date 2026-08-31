# -*- coding: utf-8 -*-
"""CP-SAT model.

Rules the school stated as mandatory but which interact badly are modelled as
*very expensive* soft constraints rather than hard ones.  That is deliberate:
a returned timetable whose expensive penalties are all zero proves the hard
version is satisfiable, while a bare INFEASIBLE would prove nothing about
which rule caused it.  The objective breakdown then reads as a diagnosis.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

import checks
import school
from model import (DAYS, LONG_DAY_HOURS, LATE_PERIOD, PERIODS_PER_DAY,
                   Meeting, Timetable, expand, full_days)

# Penalty tiers.  A is for rules the school called חובה, B for רצוי מאוד,
# C for ordinary preferences.
#: Above the mandatory tier: a rule the school corrected us on explicitly,
#: which must never be traded away to buy a lower one.
W_CRITICAL = 5000
W_MANDATORY = 1000
W_STRONG = 100
W_PREFER = 10
W_MINOR = 2

#: Every label priced at the mandatory tier.  `main.py` hardens them as a set
#: and steps down when the set turns out to be over-determined.
FIFTH_WINDOW = "יום חמישי: חלון ביום זה"
MANDATORY_LABELS = frozenset({
    "אורך יום חריג בכיתה",
    "מחנכת פותחת את היום בכיתתה",
    "מחנכת: יום שמתחיל מאוחר ומסתיים מאוחר",
    "מורה: התחלה מאוחרת פעם בשבוע",
    "יום חמישי: מתחילה מאוחר",
    "יום חמישי: מסיימת בשעה האחרונה של הכיתה",
    FIFTH_WINDOW,
    "יום חמישי: היום הארוך ביותר",
    "סיום מאוחר: מספר ימים חסר",
    "חלון שאינו ביום הארוך ביותר",
    "מחנכת: פתיחה ביום שנקבע",
    "מחנכת: התחלה מאוחרת בצמוד ליום חופש",
})


#: Labels that must never be promoted into hard constraints, whatever the
#: ladder or an anchor says about them.  A window can be *forced* — if a
#: teacher's lessons cannot be packed contiguously on some day she gets a
#: hole whether anyone wants one or not — so "put every window on her longest
#: day" has no hard version; hardening it returns UNKNOWN even from a
#: near-feasible warm start (§14.1).  It stays priced at W_CRITICAL, which is
#: what actually keeps it.  An anchor keeps this rule easily, so without this
#: set it lands in `kept` and silently costs the anchored rung every time.
NEVER_HARDEN = frozenset({"חלון שאינו ביום הארוך ביותר"})


#: `relax` keys for the rules that came from one teacher — or one class —
#: asking for something.  A timetable built before the request cannot meet
#: it, so scoring such a timetable — which is how `main.py` decides what to
#: harden — has to set them aside; pinning a placement the model forbids
#: returns INFEASIBLE and no breakdown at all, which reads as "nothing is
#: violated" and hardens everything.
#:
#: `class_periods` earns its place the hard way: the one lesson it moves has
#: a teacher whose day is otherwise full, so freeing that lesson — or even
#: its whole class-day — still leaves no slot, and the scoring solve comes
#: back INFEASIBLE however much of the anchor is let go.
TEACHER_REQUESTS = frozenset({"no_window", "ends_day", "class_periods"})


def score(reqs, placement: dict[int, tuple[int, int]],
          relax: frozenset[str] = frozenset(), time_limit: float = 60.0,
          workers: int = 4,
          weights: dict[str, int] | None = None) -> dict[str, int] | None:
    """What a finished placement costs, rule by rule.

    Builds the model and pins every meeting where the placement put it, so
    the breakdown is the same one `solve` reports — this is how a timetable
    the school has already read gets compared with a new one on equal terms.
    `None` means the placement breaks a rule the model holds as hard, so
    there is no breakdown to give.

    Give it real time whenever the placement is *partial*.  A rule counts as
    kept only if the scoring solve manages to keep it, so a rushed pass over
    the free meetings under-reports the timetable and hardens too little —
    which then costs far more time in the search that follows.
    """
    s = Scheduler(reqs, time_limit=time_limit, workers=workers, relax=relax,
                  weights=weights)
    # A slot the school has since ruled out has no variable left to pin — and
    # because a class's day is a solid block (H6), the lesson cannot simply
    # step aside: the whole day it sat in has to be re-arranged.  So that day
    # is freed and the rest of the week stays pinned.  The alternative is an
    # INFEASIBLE scoring solve, which returns no breakdown at all — and an
    # empty breakdown reads as "nothing is violated" and hardens everything.
    freed = {(s.meetings[mid].klass, slot[0]) for mid, slot in placement.items()
             if tuple(slot) not in s.x.get(mid, {})}
    for mid, slot in placement.items():
        opts = s.x.get(mid, {})
        if tuple(slot) not in opts:
            continue
        if (s.meetings[mid].klass, slot[0]) in freed:
            continue
        for key, var in opts.items():
            s.m.Add(var == (1 if key == tuple(slot) else 0))
    result = s.solve()
    return None if result.timetable is None else result.penalties


@dataclass
class Result:
    status: str
    timetable: Timetable | None
    objective: int
    best_bound: int
    wall_time: float
    penalties: dict[str, int] = field(default_factory=dict)


class Scheduler:
    def __init__(self, reqs, time_limit: float = 120.0, workers: int = 8,
                 relax: frozenset[str] = frozenset(), harden=False,
                 hint: dict[int, tuple[int, int]] | None = None,
                 optimise: bool = True,
                 fixed_fifth: dict[str, int] | None = None,
                 fifth_hard: frozenset[str] = frozenset(),
                 anchor: dict[int, tuple[int, int]] | None = None,
                 anchor_weight: int = W_STRONG,
                 weights: dict[str, int] | None = None):
        # `harden` turns the mandatory tier into real constraints.  Pricing
        # them is what makes a *failure* diagnosable, but once phase 0 has
        # shown they are satisfiable, pricing only makes CP-SAT pay 1000 where
        # it could have pruned.  main.py tries hard first and falls back to
        # priced on INFEASIBLE — which is exactly the case where the
        # breakdown is worth having.
        self.harden = harden
        self.hint = hint or {}
        # With the mandatory tier hardened, *finding* a timetable is the hard
        # part and the objective only slows the first solution down.  A
        # feasibility-only pass answers "does this rung solve at all" far
        # faster, and its placement then seeds the optimising pass.
        self.optimise = optimise
        # Which day is each homeroom teacher's fifth day.  Choosing it is the
        # combinatorial core of §11.2 (10 teachers x ~5 days); pinning it from
        # a solved core turns the rest into a much smaller repair problem.
        self.fixed_fifth = fixed_fifth or {}
        # Teachers whose fifth-day late start and late finish are already
        # achieved and worth nailing down, so the search can spend itself on
        # the window instead of re-deriving what is already right.
        self.fifth_hard = fifth_hard
        # A placement the school has already approved.  Moving a meeting off
        # it is priced, so a new rule is answered by repairing the timetable
        # the school liked instead of by rebuilding one from scratch — two
        # runs that both score well can still look nothing like each other.
        self.anchor = anchor or {}
        self.anchor_weight = anchor_weight
        # An administrator's priority for a priced rule, by label.  Zero
        # switches the rule off — which is how the constraint screen disables
        # a *soft* rule; a hard one has no price to zero out and is disabled
        # through `relax` instead.
        self.weights = dict(weights or {})
        self.relax = relax
        self.reqs = reqs
        self.meetings: list[Meeting] = expand(reqs)
        self.time_limit = time_limit
        self.workers = workers
        self.m = cp_model.CpModel()
        self.classes = {c.name: c for c in school.CLASSES}
        self._penalty_terms: dict[str, list] = {}
        self.fifth_picks: dict[str, list] = {}
        self._build()

    # ------------------------------------------------------------- helpers

    def _penalise(self, name: str, expr, weight: int):
        weight = self.weights.get(name, weight)
        if weight <= 0:
            return                      # switched off on the constraint screen
        if weight >= W_MANDATORY and (self.harden is True
                                      or name in (self.harden or ())):
            self.m.Add(expr == 0)
            return
        self._penalty_terms.setdefault(name, []).append((expr, weight))

    def _bool(self, name: str):
        return self.m.NewBoolVar(name)

    # --------------------------------------------------------------- build

    def _build(self):
        self._placements()
        self._occupancy()
        self._class_days()
        self._subject_spread()
        self._pinned()
        self._teacher_days()
        self._teacher_gaps()
        self._teacher_rules()
        self._subject_ends_day()
        self._english_days()
        self._fifth_day()
        self._preferences()
        self._anchor()
        self._objective()
        self._hint()

    def _placements(self):
        """x[mid][(day, start)] — one true per meeting."""
        self.x: dict[int, dict[tuple[int, int], cp_model.IntVar]] = {}
        for mid, mt in enumerate(self.meetings):
            opts = {}
            for d in range(len(DAYS)):
                if any(d in checks.certainly_off(school.BY_NAME[t])
                       for t in mt.teachers):
                    continue
                last = PERIODS_PER_DAY[d]
                if mt.klass:
                    last = min(last, self.classes[mt.klass].bounds(d)[1])
                first = 1
                fixed = school.SUBJECT_FIXED_DAY.get(mt.subject)
                if fixed is not None:
                    day, exempt = fixed
                    # Exempt classes must avoid the day; everyone else is
                    # confined to it.
                    if (d != day) if mt.klass not in exempt else (d == day):
                        continue
                for window in (school.SUBJECT_PERIODS.get(mt.subject),
                               None if "class_periods" in self.relax else
                               school.CLASS_SUBJECT_PERIODS.get(
                                   (mt.klass, mt.subject))):
                    if window:
                        first = max(first, window[0])
                        last = min(last, window[1])
                for p in range(first, last - mt.length + 2):
                    span = range(p, p + mt.length)
                    if any(q in school.BY_NAME[t].forbidden_periods
                           or q > school.BY_NAME[t].latest_period_on.get(
                               d, PERIODS_PER_DAY[d])
                           for t in mt.teachers for q in span):
                        continue
                    opts[(d, p)] = self._bool(f"x{mid}_{d}_{p}")
            if not opts:
                raise ValueError(f"no legal slot for {mt.klass}/{mt.subject}")
            self.m.AddExactlyOne(opts.values())
            self.x[mid] = opts

    def _occupancy(self):
        """Which x-variables put a meeting in a given slot."""
        self.occ: dict[int, dict[tuple[int, int], list]] = {}
        for mid, mt in enumerate(self.meetings):
            per_slot: dict[tuple[int, int], list] = {}
            for (d, p), var in self.x[mid].items():
                for q in range(p, p + mt.length):
                    per_slot.setdefault((d, q), []).append(var)
            self.occ[mid] = per_slot

        # A class, and a teacher, can be in at most one place at a time.
        self.class_busy: dict[tuple[str, int, int], cp_model.IntVar] = {}
        for c in school.CLASSES:
            mids = [i for i, mt in enumerate(self.meetings) if mt.klass == c.name]
            for d in range(len(DAYS)):
                for p in range(1, PERIODS_PER_DAY[d] + 1):
                    terms = [v for i in mids for v in self.occ[i].get((d, p), [])]
                    b = self._bool(f"cb_{c.name}_{d}_{p}")
                    self.m.Add(sum(terms) == b) if terms else self.m.Add(b == 0)
                    self.class_busy[(c.name, d, p)] = b

        self.teacher_busy: dict[tuple[str, int, int], cp_model.IntVar] = {}
        for t in school.TEACHERS:
            mids = [i for i, mt in enumerate(self.meetings) if t.name in mt.teachers]
            for d in range(len(DAYS)):
                for p in range(1, PERIODS_PER_DAY[d] + 1):
                    terms = [v for i in mids for v in self.occ[i].get((d, p), [])]
                    b = self._bool(f"tb_{t.name}_{d}_{p}")
                    self.m.Add(sum(terms) == b) if terms else self.m.Add(b == 0)
                    self.teacher_busy[(t.name, d, p)] = b

    def _class_days(self):
        """A class's day is a solid block starting at the first period."""
        self.class_hours: dict[tuple[str, int], cp_model.IntVar] = {}
        for c in school.CLASSES:
            for d in range(len(DAYS)):
                P = PERIODS_PER_DAY[d]
                lo, hi = c.bounds(d)
                if "class_span" in self.relax:
                    lo, hi = 0, PERIODS_PER_DAY[d]
                if "contiguity" not in self.relax:
                    for p in range(1, P):
                        # no holes: period p+1 busy implies period p busy
                        self.m.Add(self.class_busy[(c.name, d, p)]
                                   >= self.class_busy[(c.name, d, p + 1)])
                h = self.m.NewIntVar(lo, hi, f"ch_{c.name}_{d}")
                self.m.Add(h == sum(self.class_busy[(c.name, d, p)]
                                    for p in range(1, P + 1)))
                self.class_hours[(c.name, d)] = h
                want = c.preferred.get(d)
                if want is not None:
                    dev = self.m.NewIntVar(0, max(hi - want, want - lo),
                                           f"chdev_{c.name}_{d}")
                    self.m.Add(dev >= h - want)
                    self.m.Add(dev >= want - h)
                    self._penalise("אורך יום חריג בכיתה", dev, W_MANDATORY)

    def _subject_spread(self):
        """At most one meeting of a subject per class per day.

        This is what turns "חשבון over 4 days" and "אנגלית over 3 days" into
        real constraints: the pattern fixes the number of meetings, and this
        forces them onto distinct days.
        """
        groups: dict[tuple[str, str], list[int]] = {}
        for mid, mt in enumerate(self.meetings):
            if mt.klass:
                groups.setdefault((mt.klass, mt.subject), []).append(mid)
        if "subject_per_day" in self.relax:
            return
        for mids in groups.values():
            if len(mids) < 2:
                continue
            for d in range(len(DAYS)):
                starts = [v for i in mids for (dd, _), v in self.x[i].items()
                          if dd == d]
                if starts:
                    self.m.AddAtMostOne(starts)

    def _pinned(self):
        """Lessons the school placed by hand, and days it cut short.

        Both are about a class's grid rather than a teacher's, which is how
        the school states them: "period 7 on Sunday goes away" says nothing
        about who was teaching it.
        """
        if "pinned" in self.relax:
            return
        for (k, d, p), subject in school.PINNED.items():
            here = [v for mid, mt in enumerate(self.meetings)
                    if mt.klass == k and mt.subject == subject
                    for v in self.occ[mid].get((d, p), [])]
            if not here:
                raise ValueError(f"{k}/{subject} cannot reach {DAYS[d]} {p}")
            self.m.Add(sum(here) == 1)
        for (k, d), last in school.DAY_ENDS_AT.items():
            for p in range(last + 1, PERIODS_PER_DAY[d] + 1):
                self.m.Add(self.class_busy[(k, d, p)] == 0)

    def _teacher_days(self):
        """Days off, working-day indicators, daily loads."""
        # Counting may already have ruled some choices out (see
        # checks.viable_days_off): a day off that leaves one of her classes
        # uncoverable is not a branch worth exploring.
        viable = checks.viable_days_off(self.reqs)
        self.off: dict[tuple[str, int], cp_model.IntVar] = {}
        self.works: dict[tuple[str, int], cp_model.IntVar] = {}
        self.hours: dict[tuple[str, int], cp_model.IntVar] = {}
        for t in school.TEACHERS:
            for d in range(len(DAYS)):
                P = PERIODS_PER_DAY[d]
                busy = [self.teacher_busy[(t.name, d, p)] for p in range(1, P + 1)]
                off = self._bool(f"off_{t.name}_{d}")
                if d in t.off_fixed:
                    self.m.Add(off == 1)
                elif d not in t.off_choice:
                    self.m.Add(off == 0)
                for b in busy:
                    self.m.Add(b <= 1 - off)
                self.off[(t.name, d)] = off

                w = self._bool(f"w_{t.name}_{d}")
                self.m.AddMaxEquality(w, busy)
                self.works[(t.name, d)] = w

                cap = checks.day_capacity(t, d)
                h = self.m.NewIntVar(0, cap, f"th_{t.name}_{d}")
                self.m.Add(h == sum(busy))
                self.hours[(t.name, d)] = h
                if t.max_per_day is not None and "max_per_day" not in self.relax:
                    self.m.Add(h <= t.max_per_day)
                # A teacher does not travel in for one or two lessons.  The few
                # allowed a two-lesson day may not spend it on the last periods.
                self.m.Add(h >= t.min_per_day).OnlyEnforceIf(w)
                if t.min_per_day < 3:
                    tiny = self._bool(f"tiny_{t.name}_{d}")
                    self.m.Add(h <= 2).OnlyEnforceIf(tiny)
                    self.m.Add(h >= 3).OnlyEnforceIf(tiny.Not())
                    for q in school.SHORT_DAY_BANNED_PERIODS:
                        if q <= P:
                            self.m.Add(self.teacher_busy[(t.name, d, q)] == 0
                                       ).OnlyEnforceIf(tiny)

            if t.off_choice and "day_off" not in self.relax:
                allowed = viable.get(t.name) or t.off_choice
                self.m.AddExactlyOne(
                    [self.off[(t.name, d)] for d in allowed])
                for d in t.off_choice:
                    if d not in allowed:
                        self.m.Add(self.off[(t.name, d)] == 0)
            if t.exact_working_days is not None and "working_days" not in self.relax:
                self.m.Add(sum(self.works[(t.name, d)]
                               for d in range(len(DAYS))) == t.exact_working_days)

        for a, b in ([] if "distinct_off" in self.relax else school.DISTINCT_DAYS_OFF):
            for d in range(len(DAYS)):
                self.m.Add(self.off[(a, d)] + self.off[(b, d)] <= 1)

    def _teacher_gaps(self):
        """A gap is a free period with teaching on both sides of it.

        The school allows each teacher at most one per week — hard.
        """
        self.gaps: dict[tuple[str, int], cp_model.IntVar] = {}
        window_flags = []
        for t in school.TEACHERS:
            weekly = []
            for d in range(len(DAYS)):
                P = PERIODS_PER_DAY[d]
                busy = {p: self.teacher_busy[(t.name, d, p)]
                        for p in range(1, P + 1)}
                day_gaps = []
                for p in range(2, P):
                    before = self._bool(f"bf_{t.name}_{d}_{p}")
                    after = self._bool(f"af_{t.name}_{d}_{p}")
                    self.m.AddMaxEquality(before, [busy[q] for q in range(1, p)])
                    self.m.AddMaxEquality(after, [busy[q] for q in range(p + 1, P + 1)])
                    g = self._bool(f"gap_{t.name}_{d}_{p}")
                    self.m.Add(g <= before)
                    self.m.Add(g <= after)
                    self.m.Add(g <= 1 - busy[p])
                    self.m.Add(g >= before + after + (1 - busy[p]) - 2)
                    day_gaps.append(g)
                gd = self.m.NewIntVar(0, max(P - 2, 0), f"gaps_{t.name}_{d}")
                self.m.Add(gd == sum(day_gaps)) if day_gaps else self.m.Add(gd == 0)
                self.gaps[(t.name, d)] = gd
                weekly.append(gd)

                # A window is only a rest if the day around it is long.  So
                # any day holding one must be her longest day of the week —
                # an absolute rule about *which* day, not a nudge about how
                # many hours.  Priced at the mandatory tier so a teacher it
                # cannot hold for is named rather than silently shortchanged.
                if school.WINDOW_ON_LONGEST_DAY and "window_day" not in self.relax:
                    has = self._bool(f"wingap_{t.name}_{d}")
                    self.m.Add(gd >= 1).OnlyEnforceIf(has)
                    self.m.Add(gd == 0).OnlyEnforceIf(has.Not())
                    # Priced above the mandatory tier, not hard: hard makes
                    # the search collapse, but at this weight the solver will
                    # never trade a real rest away to buy a lower-tier rule.
                    # Priced, not hard.  A window can be *forced* on a
                    # teacher — if her lessons cannot be packed contiguously
                    # on some day she gets a hole whether anyone wants it or
                    # not — so "give her none instead" is not always an
                    # available move, and a hard rule here has no solution.
                    # The weight still outranks every mandatory rule, so a
                    # real rest is never traded away for a lower one.
                    viol = self._bool(f"winshort_{t.name}_{d}")
                    for e in range(len(DAYS)):
                        if e != d:
                            self.m.Add(self.hours[(t.name, d)]
                                       >= self.hours[(t.name, e)]
                                       ).OnlyEnforceIf([has, viol.Not()])
                    self._penalise("חלון שאינו ביום הארוך ביותר", viol,
                                   W_CRITICAL)

            # One window a week is the rule.  The school allows a homeroom
            # teacher a second where the rest of the rules need it — allowed,
            # not free: the second one is priced so it stays the exception.
            # A teacher who asked for none gets a ceiling of zero instead.
            wants_none = (t.name in school.NO_WINDOW_TEACHERS
                          and "no_window" not in self.relax)
            allowed = (0 if wants_none else
                       school.HOMEROOM_MAX_WINDOWS if t.homeroom
                       else t.max_windows)
            if "gaps" not in self.relax:
                self.m.Add(sum(weekly) <= allowed)
                if allowed > 1:
                    extra = self.m.NewIntVar(0, allowed - 1,
                                             f"extrawin_{t.name}")
                    self.m.Add(extra >= sum(weekly) - 1)
                    self._penalise("חלון שני למחנכת", extra, W_PREFER)
            else:
                self._penalise("חלונות מעבר לאחד", sum(weekly), W_MANDATORY)

            if wants_none:
                # She is not part of the school-wide wish for a window, so
                # she must not drag its majority target down either.
                continue
            if school.WINDOW_MAJORITY:
                has = self._bool(f"haswin_{t.name}")
                self.m.Add(sum(weekly) >= 1).OnlyEnforceIf(has)
                self.m.Add(sum(weekly) == 0).OnlyEnforceIf(has.Not())
                window_flags.append(has)
            else:
                self._penalise("חלונות", sum(weekly), W_MINOR)

        # The school asked for a window for most teachers, not none: a majority
        # should have exactly one.  Some cannot — אלישבע teaches 22 of כיתה א's
        # 29 hours and the other 7 are already spoken for — so this is a
        # majority target whose shortfall is priced, not a per-teacher rule.
        if window_flags:
            majority = (len(window_flags) + 1) // 2
            have = self.m.NewIntVar(0, len(window_flags), "with_window")
            self.m.Add(have == sum(window_flags))
            deficit = self.m.NewIntVar(0, majority, "window_deficit")
            self.m.Add(deficit >= majority - have)
            self._penalise("חלון לרוב המורות", deficit, W_STRONG)
            # Beyond the majority, every teacher who *can* have a rest should
            # get one.  Priced per teacher and gently, so it pushes without
            # outranking the rules about where the window may fall.
            for flag in window_flags:
                self._penalise("מורה ללא חלון כלל", 1 - flag, W_PREFER)

    def _subject_ends_day(self):
        """Some lessons must be the last thing their teacher does that day.

        Stated as "nothing after it", not as "it is at the class's last
        period": the rule is about her day, and the class may well go on
        without her.  Being last also rules out being first — her daily
        minimum is three hours, so a day that ends on its first lesson is
        already impossible.
        """
        if "ends_day" in self.relax:
            return
        for teacher, subject in school.SUBJECT_ENDS_DAY:
            for mid, mt in enumerate(self.meetings):
                if mt.subject != subject or teacher not in mt.teachers:
                    continue
                for (d, p), var in self.x[mid].items():
                    tail = [self.teacher_busy[(teacher, d, q)]
                            for q in range(p + mt.length,
                                           PERIODS_PER_DAY[d] + 1)]
                    if tail:
                        self.m.Add(sum(tail) == 0).OnlyEnforceIf(var)

    def _anchor(self):
        """Stay on the approved placement unless a rule needs the move."""
        if not self.anchor or not self.anchor_weight:
            return
        for mid, slot in self.anchor.items():
            var = self.x.get(mid, {}).get(tuple(slot))
            if var is not None:
                self._penalise("שינוי מהשיבוץ שאושר", 1 - var,
                               self.anchor_weight)

    def _teacher_rules(self):
        # Days that run past the fifth period.  Two different complaints hide
        # here: a teacher who finishes early *every* day carries none of the
        # late load, and a teacher pinned late too often carries too much.
        self.late_days: dict[str, list] = {}
        for t in school.TEACHERS:
            if t.min_late_days == 0 and t.max_late_days is None:
                continue
            flags = []
            for d in range(len(DAYS)):
                P = PERIODS_PER_DAY[d]
                tail = [self.teacher_busy[(t.name, d, p)]
                        for p in range(LATE_PERIOD, P + 1)]
                if not tail:
                    continue
                lt = self._bool(f"late_{t.name}_{d}")
                self.m.AddMaxEquality(lt, tail)
                flags.append(lt)
            self.late_days[t.name] = flags
            if not flags or "late_days" in self.relax:
                continue
            if t.max_late_days is not None:
                self.m.Add(sum(flags) <= t.max_late_days)
            if t.min_late_days:
                # At least one such day is hard; the rest is priced, so an
                # impossible target is reported instead of failing silently.
                self.m.Add(sum(flags) >= 1)
                short = self.m.NewIntVar(0, t.min_late_days,
                                         f"latedef_{t.name}")
                self.m.Add(short >= t.min_late_days - sum(flags))
                self._penalise("סיום מאוחר: מספר ימים חסר", short, W_MANDATORY)

        # זהבי: at most one long day.
        for t in school.TEACHERS:
            if t.max_long_days is None or "long_days" in self.relax:
                continue
            longs = []
            for d in range(len(DAYS)):
                lg = self._bool(f"long_{t.name}_{d}")
                self.m.Add(self.hours[(t.name, d)] >= LONG_DAY_HOURS
                           ).OnlyEnforceIf(lg)
                self.m.Add(self.hours[(t.name, d)] <= LONG_DAY_HOURS - 1
                           ).OnlyEnforceIf(lg.Not())
                longs.append(lg)
            self.m.Add(sum(longs) <= t.max_long_days)

        # No teacher spends more than 5 hours with the same class in one day.
        per_class: dict[tuple[str, str], list[int]] = {}
        for mid, mt in enumerate(self.meetings):
            for t in mt.teachers:
                if mt.klass:
                    per_class.setdefault((t, mt.klass), []).append(mid)
        for (t, c), mids in per_class.items():
            if sum(self.meetings[i].length for i in mids) <= 5 \
                    or "same_class_5" in self.relax:
                continue
            for d in range(len(DAYS)):
                terms = [v for i in mids
                         for p in range(1, PERIODS_PER_DAY[d] + 1)
                         for v in self.occ[i].get((d, p), [])]
                if terms:
                    self.m.Add(sum(terms) <= 5)

    def _english_days(self):
        """A subject confined, across a set of classes, to one day pattern.

        אנגלית in ו–ח is the case it was written for: both options run
        Monday…Thursday with a different middle day, so the three days are
        never a consecutive run, and because רבקה טסה teaches all three
        classes in parallel and works exactly three days, choosing the
        pattern chooses her week as well.
        """
        if "english_days" in self.relax:
            return
        self.day_group_choice = {}
        for g, (subject, klasses, options) in enumerate(
                school.SUBJECT_DAY_GROUPS):
            mids = [i for i, mt in enumerate(self.meetings)
                    if mt.subject == subject and mt.klass in klasses]
            if not mids:
                continue
            choice = []
            for n, option in enumerate(options):
                picked = self._bool(f"dayopt_{g}_{n}")
                for i in mids:
                    for (d, _), var in self.x[i].items():
                        if d not in option:
                            self.m.Add(var == 0).OnlyEnforceIf(picked)
                choice.append(picked)
            self.m.AddExactlyOne(choice)
            self.day_group_choice[subject] = choice

    def _fifth_day(self):
        """§11.2 — the shape of a homeroom teacher's one non-opening day.

        She works five days; four of them she opens her own class (§11.1),
        so exactly one day is left, and the school specified what it looks
        like: no first period, finishing at the class's last period, her one
        weekly window in it, and it being her longest day.

        The four pull against each other, so each is priced separately at the
        mandatory tier: the objective breakdown then says which teacher fails
        which component, instead of a bare INFEASIBLE.
        """
        if "fifth_day" in self.relax:
            return
        cfg = school.FIFTH_DAY
        # (ד) is disproved by counting for the teachers whose classes end
        # early; asking CP-SAT to search for it would only burn time and
        # return the same unavoidable penalty.  Phase 0 already reported it.
        no_longest = checks.fifth_day_longest_impossible(self.reqs)
        full = set(full_days())
        for t in school.TEACHERS:
            if not t.homeroom:
                continue
            k, off = t.homeroom, checks.certainly_off(t)
            # Counting may already have settled which day this is, the same
            # way checks.viable_days_off settles a day off: for a teacher who
            # must keep her late start away from her day off, the days off,
            # both their neighbours and the day reserved for opening her own
            # class are not branches worth exploring.  No timetable is lost —
            # a day she opens can never be the day she starts late, so the
            # pruning only stops the *label* "fifth day" from being pinned on
            # a day that would immediately pay for wearing it.
            only = (set(checks.late_start_days(t))
                    if t.name in school.LATE_START_AWAY_FROM_OFF else None)
            picks = []
            for d in range(len(DAYS)):
                # A shortened day cannot be it: it has too few periods for
                # a late start and a late finish to leave her daily floor.
                if d not in full or d in off:
                    continue
                if only is not None and d not in only:
                    continue
                P = PERIODS_PER_DAY[d]
                pick = self._bool(f"fifth_{t.name}_{d}")
                self.m.AddImplication(pick, self.works[(t.name, d)])
                if t.name in self.fixed_fifth:
                    self.m.Add(pick == (1 if self.fixed_fifth[t.name] == d
                                        else 0))
                picks.append(pick)
                busy = {p: self.teacher_busy[(t.name, d, p)]
                        for p in range(1, P + 1)}

                if cfg["late_start"]:
                    # miss ⇔ picked and still teaching period 1
                    miss = self._bool(f"f_a_{t.name}_{d}")
                    self.m.Add(miss >= pick + busy[1] - 1)
                    if t.name in self.fifth_hard:
                        self.m.Add(miss == 0)
                    else:
                        self._penalise("יום חמישי: מתחילה מאוחר", miss,
                                       W_MANDATORY)

                if cfg["late_end"]:
                    # Her last lesson is the class's last period.  Two halves:
                    # she teaches nothing past the end of her class's day
                    # (linear in the periods, not quadratic), and she does
                    # teach the period the class ends on.
                    hits = []
                    for q in range(1, P + 1):
                        eq = self._bool(f"f_eq_{t.name}_{d}_{q}")
                        self.m.Add(self.class_hours[(k, d)] == q
                                   ).OnlyEnforceIf(eq)
                        hit = self._bool(f"f_hit_{t.name}_{d}_{q}")
                        self.m.AddBoolAnd([eq, busy[q]]).OnlyEnforceIf(hit)
                        hits.append(hit)
                    ends = self._bool(f"f_end_{t.name}_{d}")
                    self.m.AddMaxEquality(ends, hits)
                    # ...and nothing after it.  Conditioned on `ends`, never
                    # on `pick`: when (ב) is priced rather than hardened the
                    # solver must stay free to drop it and pay.
                    for q in range(1, P + 1):
                        self.m.Add(self.class_hours[(k, d)] >= q
                                   ).OnlyEnforceIf([ends, busy[q]])
                    miss = self._bool(f"f_b_{t.name}_{d}")
                    self.m.Add(miss >= pick - ends)
                    if t.name in self.fifth_hard:
                        self.m.Add(miss == 0)
                    else:
                        self._penalise(
                            "יום חמישי: מסיימת בשעה האחרונה של הכיתה",
                            miss, W_MANDATORY)

                if cfg["window"]:
                    miss = self._bool(f"f_c_{t.name}_{d}")
                    self.m.Add(miss >= pick - self.gaps[(t.name, d)])
                    self._penalise("יום חמישי: חלון ביום זה", miss,
                                   W_MANDATORY)

                if cfg["longest"] and t.name not in no_longest:
                    longest = self._bool(f"f_long_{t.name}_{d}")
                    for e in range(len(DAYS)):
                        if e != d:
                            self.m.Add(self.hours[(t.name, d)]
                                       >= self.hours[(t.name, e)]
                                       ).OnlyEnforceIf(longest)
                    miss = self._bool(f"f_d_{t.name}_{d}")
                    self.m.Add(miss >= pick - longest)
                    self._penalise("יום חמישי: היום הארוך ביותר", miss,
                                   W_MANDATORY)

            if picks:
                self.m.AddExactlyOne(picks)
                self.fifth_picks[t.name] = picks

    def _preferences(self):
        # Homeroom teachers of ג–ח: at least one day starting after the first
        # period and finishing late.
        for name in school.LATE_START_AND_END:
            ok_days = []
            for d in range(len(DAYS)):
                P = PERIODS_PER_DAY[d]
                if P < LATE_PERIOD:
                    continue
                late = self._bool(f"lse_{name}_{d}")
                self.m.Add(self.teacher_busy[(name, d, 1)] == 0
                           ).OnlyEnforceIf(late)
                self.m.AddBoolOr([self.teacher_busy[(name, d, p)]
                                  for p in range(LATE_PERIOD, P + 1)]
                                 ).OnlyEnforceIf(late)
                ok_days.append(late)
            miss = self._bool(f"lse_miss_{name}")
            self.m.Add(sum(ok_days) >= 1).OnlyEnforceIf(miss.Not())
            self.m.Add(sum(ok_days) == 0).OnlyEnforceIf(miss)
            self._penalise("מחנכת: יום שמתחיל מאוחר ומסתיים מאוחר",
                           miss, W_MANDATORY)

        # A day the school named for a homeroom teacher to open on.  Her
        # other openings are free to move; this one is not.
        for name, days in school.OPEN_ON_DAY.items():
            t = school.BY_NAME[name]
            mids = [i for i, mt in enumerate(self.meetings)
                    if mt.klass == t.homeroom and name in mt.teachers]
            for d in days:
                terms = [v for i in mids for v in self.occ[i].get((d, 1), [])]
                if not terms:
                    continue
                miss = self._bool(f"openday_{name}_{d}")
                self.m.Add(sum(terms) + miss >= 1)
                # Above the mandatory tier, because the rule it argues with
                # is §11.2(א) and the two cost exactly the same: opening on a
                # fifth day is what breaking (א) *is*, so at equal weight the
                # search is indifferent and simply keeps whichever it found
                # first.  The school named this day; (א) it already breaks
                # elsewhere.  Priced, not hard: if the day cannot be had, the
                # breakdown says so instead of returning INFEASIBLE.
                self._penalise("מחנכת: פתיחה ביום שנקבע", miss, W_CRITICAL)

        # A late start pressed up against a day off leaves the class two
        # mornings in a row without its homeroom teacher, so both neighbours
        # of a day off must be opened normally.  Stated about the timetable
        # and not about the §11.2 pick: `pick` is only the day the fifth-day
        # rules are measured on, and (א) itself is priced, so tying the rule
        # to it would let a late start reappear on a day the pick avoided.
        for name in school.LATE_START_AWAY_FROM_OFF:
            for d in range(len(DAYS)):
                for e in (d - 1, d + 1):
                    if not 0 <= e < len(DAYS):
                        continue
                    miss = self._bool(f"nearoff_{name}_{d}_{e}")
                    # off on e and working on d ⇒ she teaches period 1 on d.
                    self.m.Add(miss + self.teacher_busy[(name, d, 1)] + 1
                               - self.off[(name, e)]
                               >= self.works[(name, d)])
                    self._penalise("מחנכת: התחלה מאוחרת בצמוד ליום חופש",
                                   miss, W_MANDATORY)

        # A subject the school wants to open a day with.  Priced low, and
        # the measurement is worth keeping: forcing the period-1 slot with
        # every hard rule in place solves to OPTIMAL, so this is *feasible* —
        # but feasible is not free.  Every optimised search that keeps it
        # pays 5000 for a window landing off its teacher's longest day
        # (11776 with it, 7766 without, over three warm-started rounds each).
        # §14 prices that window above the whole mandatory tier exactly so a
        # real rest is never sold to buy a lower rule, so this rule is the
        # one that gives way, and at 100 it gives way quietly.
        for (cname, subject), n in school.SUBJECT_AT_DAY_START.items():
            starts = []
            for mid, mt in enumerate(self.meetings):
                if mt.klass != cname or mt.subject != subject:
                    continue
                starts += [v for (d, p), v in self.x[mid].items() if p == 1]
            if not starts:
                continue
            short = self.m.NewIntVar(0, n, f"start_{cname}_{subject}")
            self.m.Add(short >= n - sum(starts))
            self._penalise(f"{subject} בכיתה {cname}: פתיחת יום", short,
                           W_STRONG)

        # אלישבע asked for one day beginning at the 2nd or 3rd period.
        for name, allowed in school.LATE_START_ONLY.items():
            ok_days = []
            for d in range(len(DAYS)):
                P = PERIODS_PER_DAY[d]
                late = self._bool(f"ls_{name}_{d}")
                self.m.Add(self.teacher_busy[(name, d, 1)] == 0
                           ).OnlyEnforceIf(late)
                self.m.AddBoolOr([self.teacher_busy[(name, d, p)]
                                  for p in allowed if p <= P]
                                 ).OnlyEnforceIf(late)
                ok_days.append(late)
            miss = self._bool(f"ls_miss_{name}")
            self.m.Add(sum(ok_days) >= 1).OnlyEnforceIf(miss.Not())
            self.m.Add(sum(ok_days) == 0).OnlyEnforceIf(miss)
            self._penalise("מורה: התחלה מאוחרת פעם בשבוע", miss, W_MANDATORY)

        # ו–ח should get at least one day of exactly five hours.
        for cname in school.PREFER_ONE_SHORT_DAY:
            fives = []
            for d in full_days():
                f = self._bool(f"five_{cname}_{d}")
                self.m.Add(self.class_hours[(cname, d)] == 5).OnlyEnforceIf(f)
                self.m.Add(self.class_hours[(cname, d)] != 5).OnlyEnforceIf(f.Not())
                fives.append(f)
            miss = self._bool(f"five_miss_{cname}")
            self.m.Add(sum(fives) >= 1).OnlyEnforceIf(miss.Not())
            self.m.Add(sum(fives) == 0).OnlyEnforceIf(miss)
            self._penalise("יום קצר אחד לכיתה", miss, W_STRONG)

        # Two short days apiece, where the staff table asks for spreading.
        for t in school.TEACHERS:
            if t.name in school.NO_SPREAD_PREFERENCE:
                continue
            shorts = []
            for d in full_days():
                s = self._bool(f"sd_{t.name}_{d}")
                self.m.Add(self.hours[(t.name, d)] <= 4).OnlyEnforceIf(s)
                self.m.Add(self.hours[(t.name, d)] >= 5).OnlyEnforceIf(s.Not())
                shorts.append(s)
            short_cnt = self.m.NewIntVar(0, len(shorts), f"shorts_{t.name}")
            self.m.Add(short_cnt == sum(shorts))
            deficit = self.m.NewIntVar(0, 2, f"sdef_{t.name}")
            self.m.Add(deficit >= 2 - short_cnt)
            self._penalise("פיזור: 2 ימים קצרים למורה", deficit, W_MINOR)

        # Homeroom teachers open the day in their own classroom.  "Opening" is
        # exactly "she is teaching her own class at period 1", which the
        # occupancy index already expresses; the class and teacher no-overlap
        # constraints keep the sum within 0..1 on their own.  Friday counts.
        for t in school.TEACHERS:
            spec = school.OPEN_OWN_CLASS.get(t.homeroom or "")
            if spec is None:
                continue
            minimum, preferred = spec
            mids = [i for i, mt in enumerate(self.meetings)
                    if mt.klass == t.homeroom and t.name in mt.teachers]
            opens = []
            for d in range(len(DAYS)):
                terms = [v for i in mids for v in self.occ[i].get((d, 1), [])]
                if not terms:
                    continue
                o = self._bool(f"open_{t.name}_{d}")
                self.m.Add(sum(terms) == o)
                opens.append(o)
            total = self.m.NewIntVar(0, len(opens), f"opens_{t.name}")
            self.m.Add(total == sum(opens))
            short = self.m.NewIntVar(0, minimum, f"opendef_{t.name}")
            self.m.Add(short >= minimum - total)
            self._penalise("מחנכת פותחת את היום בכיתתה", short, W_MANDATORY)
            if preferred > minimum:
                extra = self.m.NewIntVar(0, preferred, f"openpref_{t.name}")
                self.m.Add(extra >= preferred - total)
                self._penalise("מחנכת: פתיחה מועדפת נוספת", extra, W_PREFER)

        # רצוי מאוד: a homeroom teacher should not be teaching the seventh
        # period on Thursday.  Priced at the "strongly preferred" tier — the
        # school was explicit that it is neither required nor forbidden.
        for t in school.TEACHERS:
            if not t.homeroom:
                continue
            for d in school.NO_SEVENTH_ON:
                P = PERIODS_PER_DAY[d]
                if P < max(PERIODS_PER_DAY):
                    continue
                self._penalise(f"מחנכת בשעה האחרונה ביום {DAYS[d]}",
                               self.teacher_busy[(t.name, d, P)], W_STRONG)

        # אומנות is better not first thing in the morning.
        for mid, mt in enumerate(self.meetings):
            if mt.subject in school.NOT_FIRST_PERIOD:
                first = [v for (d, p), v in self.x[mid].items() if p == 1]
                if first:
                    self._penalise(f"{mt.subject} בשעה ראשונה",
                                   sum(first), W_PREFER)

    def _objective(self):
        if not self.optimise:
            return
        terms = []
        for name, items in self._penalty_terms.items():
            for expr, weight in items:
                terms.append(weight * expr)
        self.m.Minimize(sum(terms))

    def _hint(self):
        """Seed the search with a known placement — the previous stage's."""
        for mid, slot in self.hint.items():
            var = self.x.get(mid, {}).get(tuple(slot))
            if var is not None:
                self.m.AddHint(var, 1)

    # --------------------------------------------------------------- solve

    def solve(self, log: bool = False) -> Result:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_workers = self.workers
        solver.parameters.log_search_progress = log
        status = solver.Solve(self.m)
        name = solver.StatusName(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return Result(name, None, 0, 0, solver.WallTime())

        placement = {}
        for mid in range(len(self.meetings)):
            for (d, p), var in self.x[mid].items():
                if solver.Value(var):
                    placement[mid] = (d, p)
                    break
        breakdown = {}
        for label, items in self._penalty_terms.items():
            total = sum(weight * solver.Value(expr) for expr, weight in items)
            if total:
                breakdown[label] = total
        return Result(name, Timetable(placement, self.meetings),
                      int(solver.ObjectiveValue()), int(solver.BestObjectiveBound()),
                      solver.WallTime(), breakdown)
