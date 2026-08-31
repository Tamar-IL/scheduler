# -*- coding: utf-8 -*-
"""Tests.  Run with: python3 -m unittest discover -s tests -t ."""
from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import unittest
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import checks
import polish
import school
import solver
import validate
from model import (DAYS, FRI, MON, PERIODS_PER_DAY, SUN, THU, TUE, WED,
                   Requirement, SchoolClass, Teacher, Timetable, align, expand)


@contextlib.contextmanager
def tiny_school(teachers, classes):
    """Swap in a miniature school; the solver reads these as globals."""
    old = (school.TEACHERS, school.CLASSES, school.BY_NAME,
           school.DISTINCT_DAYS_OFF, school.LATE_START_AND_END,
           school.LATE_START_ONLY, school.PREFER_ONE_SHORT_DAY,
           school.OPEN_OWN_CLASS, school.NO_WINDOW_TEACHERS,
           school.SUBJECT_ENDS_DAY, school.PINNED, school.DAY_ENDS_AT,
           school.OPEN_ON_DAY, school.LATE_START_AWAY_FROM_OFF,
           school.CLASS_SUBJECT_PERIODS, school.SUBJECT_AT_DAY_START)
    school.TEACHERS, school.CLASSES = teachers, classes
    school.BY_NAME = {t.name: t for t in teachers}
    school.DISTINCT_DAYS_OFF, school.LATE_START_AND_END = [], []
    school.LATE_START_ONLY, school.PREFER_ONE_SHORT_DAY = {}, []
    school.OPEN_OWN_CLASS = {}
    school.NO_WINDOW_TEACHERS, school.SUBJECT_ENDS_DAY = (), ()
    school.PINNED, school.DAY_ENDS_AT, school.OPEN_ON_DAY = {}, {}, {}
    school.LATE_START_AWAY_FROM_OFF = ()
    school.CLASS_SUBJECT_PERIODS, school.SUBJECT_AT_DAY_START = {}, {}
    try:
        yield
    finally:
        (school.TEACHERS, school.CLASSES, school.BY_NAME,
         school.DISTINCT_DAYS_OFF, school.LATE_START_AND_END,
         school.LATE_START_ONLY, school.PREFER_ONE_SHORT_DAY,
         school.OPEN_OWN_CLASS, school.NO_WINDOW_TEACHERS,
         school.SUBJECT_ENDS_DAY, school.PINNED, school.DAY_ENDS_AT,
         school.OPEN_ON_DAY, school.LATE_START_AWAY_FROM_OFF,
         school.CLASS_SUBJECT_PERIODS, school.SUBJECT_AT_DAY_START) = old


def T(name, **kw):
    """A fixture teacher.  Miniature schools have one- and two-hour days by
    design, so they opt out of the real school's minimum-day-length floor."""
    kw.setdefault("min_per_day", 1)
    return Teacher(name, **kw)


def _span(lo, hi):
    s = {d: (lo, hi) for d in (SUN, MON, TUE, WED, THU)}
    s[FRI] = (0, 0)
    return s


class TestModel(unittest.TestCase):
    def test_pattern_must_match_hours(self):
        with self.assertRaises(ValueError):
            Requirement("א", "חשבון", ("x",), 5, (2, 2))

    def test_expand_splits_into_meetings(self):
        r = Requirement("ז", "חשבון", ("a", "b"), 6, (2, 2, 1, 1))
        ms = expand([r])
        self.assertEqual([m.length for m in ms], [2, 2, 1, 1])
        self.assertEqual(sum(m.length for m in ms), 6)
        self.assertTrue(all(m.teachers == ("a", "b") for m in ms))

    def test_real_school_hours_reconcile(self):
        reqs = school.requirements()
        # 318, not 319: כיתה ח's Sunday opening went to היסטוריה and the
        # hour came out of תורה rather than out of רותי's week.
        self.assertEqual(sum(r.hours for r in reqs if r.klass), 318)
        # 29 extra teacher-hours come from the parallel groups, +1 for ספריה
        self.assertEqual(sum(r.hours * len(r.teachers) for r in reqs), 348)


class TestChecks(unittest.TestCase):
    #: §11.2(ד) is disproved by counting and reported as an ERROR on purpose;
    #: it is a statement about the school's rules, not about coverage.
    KNOWN = "יום חמישי (§11.2ד)"

    def _coverage_errors(self):
        return [f for f in checks.run(school.requirements())
                if f.level == checks.ERROR and f.topic != self.KNOWN]

    def test_real_data_has_no_coverage_errors(self):
        self.assertEqual(self._coverage_errors(), [])

    def test_fifth_day_longest_is_impossible_for_the_early_finishers(self):
        """(ד) caps her whole week at what her fifth day can hold.

        The set is derived, not listed: it moves when the rules that shrink
        that day move.  What must hold is the direction — a homeroom teacher
        whose class runs to period 7 has room, one whose class stops at 5
        does not.
        """
        blocked = checks.fifth_day_longest_impossible(school.requirements())
        for cap, ceiling, need in blocked.values():
            self.assertLess(ceiling, need)
        # classes ending at period 5 can never satisfy it
        for name in ("אלישבע גלילי", "זהבי ליוי", "חיה לוי"):
            self.assertIn(name, blocked)
        # classes running to period 7 always can
        for name in ("מוריה נקי", "הדסה ויזמן", "רותי אסולין"):
            self.assertNotIn(name, blocked)
        # ...and the solver must not waste search on what counting settled.
        sch = solver.Scheduler(school.requirements(), time_limit=1)
        names = {v.name for v in sch.m.Proto().variables}
        self.assertFalse(any(n.startswith("f_long_אלישבע גלילי")
                             for n in names))
        self.assertTrue(any(n.startswith("f_long_מוריה נקי") for n in names),
                        "(ד) must still be enforced where it is possible")


class TestRoundTwoRules(unittest.TestCase):
    """The הבעה swap, the opening rule, and the window policy."""

    def test_ktiv_swap_is_load_neutral(self):
        """§11.4: זהבי teaches כתיב in חיה's class and vice versa.

        The swap exists to make כיתה ב2 coverable on Tuesday; it must not move
        anyone's weekly total or either class's hour count.  הבעה is back to
        the original table.
        """
        reqs = school.requirements()
        by = {(r.klass, r.subject): r.teachers for r in reqs}
        self.assertEqual(by[("ב1", "כתיב")], ("חיה לוי",))
        self.assertEqual(by[("ב2", "כתיב")], ("זהבי ליוי",))
        self.assertEqual(by[("ב1", "הבעה")], ("זהבי ליוי",))
        self.assertEqual(by[("ב2", "הבעה")], ("חיה לוי",))
        for c in ("ב1", "ב2"):
            self.assertEqual(sum(r.hours for r in reqs if r.klass == c), 29)
        load = {}
        for r in reqs:
            for n in r.teachers:
                load[n] = load.get(n, 0) + r.hours
        # חיה is down to her declared 22: class א's ספרות-עברית hour moved
        # to הודיה, which closed the gap the staff table complained about.
        self.assertEqual(load["חיה לוי"], 22)
        self.assertEqual(school.BY_NAME["חיה לוי"].declared, 22)
        self.assertEqual(load["זהבי ליוי"], 22)
        by = {(r.klass, r.subject): r.teachers for r in reqs}
        self.assertEqual(by[("א", "ספרות-עברית")], ("הודיה חבשוש",))

    def test_b2_tuesday_lessons_are_forced(self):
        """The only five ב2 lessons whose teachers work on Tuesday (§11.4)."""
        from checks import certainly_off
        from model import TUE
        avail = {(r.subject, r.teachers[0]) for r in school.requirements()
                 if r.klass == "ב2"
                 and TUE not in certainly_off(school.BY_NAME[r.teachers[0]])}
        self.assertEqual(avail, {("זה\"ב", "יהודית כהן"),
                                 ("אנגלית", "אפרת נתנאל"),
                                 ("ב. תפילה", "מוריה נקי"),
                                 ("לשון", "מוריה נקי"),
                                 ("כתיב", "זהבי ליוי")})

    def test_b2_tuesday_is_now_coverable(self):
        """The swap is only worth making if it closes the Tuesday shortfall."""
        errs = [f for f in checks.run(school.requirements())
                if f.level == checks.ERROR
                and f.topic != TestChecks.KNOWN]
        self.assertEqual(errs, [], f"expected no coverage errors, got {errs}")

    def test_every_class_has_an_opening_rule(self):
        homerooms = {t.homeroom for t in school.TEACHERS if t.homeroom}
        self.assertEqual(homerooms, set(school.OPEN_OWN_CLASS))
        for minimum, preferred in school.OPEN_OWN_CLASS.values():
            self.assertLessEqual(minimum, preferred)

    def test_impossible_opening_count_is_caught(self):
        """Asking for more openings than the teacher has working days."""
        t = T("מחנכת", off_fixed=(SUN, MON, TUE, WED), homeroom="דמה")
        c = SchoolClass("דמה", _span(1, 2))
        with tiny_school([t], [c]):
            school.OPEN_OWN_CLASS = {"דמה": (5, 5)}
            found = [f for f in checks.run(
                [Requirement("דמה", "מקצוע", ("מחנכת",), 2, (1, 1))])
                if f.level == checks.ERROR and f.topic == "פתיחת יום"]
            self.assertTrue(found)

    def test_validator_flags_too_few_openings(self):
        t = T("מחנכת", off_fixed=(FRI,), homeroom="דמה")
        other = T("אחרת", off_fixed=(FRI,))
        c = SchoolClass("דמה", _span(0, 2))
        reqs = [Requirement("דמה", "מ1", ("מחנכת",), 1, (1,)),
                Requirement("דמה", "מ2", ("אחרת",), 1, (1,))]
        ms = expand(reqs)
        # the homeroom teacher takes period 2, so she opens on no day at all
        tt = Timetable({0: (SUN, 2), 1: (SUN, 1)}, ms)
        with tiny_school([t, other], [c]):
            school.OPEN_OWN_CLASS = {"דמה": (1, 1)}
            ok, bad = validate.validate(tt)
        self.assertFalse(ok)
        self.assertTrue(any("פותחת" in b for b in bad), bad)


class TestRoundThreeRules(unittest.TestCase):
    """Day-off narrowing, the ספריה window, and the minimum working day."""

    def test_sima_biton_day_off_excludes_sun_wed_fri(self):
        t = school.BY_NAME["סימה ביטון"]
        self.assertEqual(set(t.off_choice), {MON, TUE, THU})
        for d in (SUN, WED, FRI):
            self.assertNotIn(d, t.off_choice)

    def test_library_hour_is_confined_to_periods_2_to_4(self):
        self.assertEqual(school.SUBJECT_PERIODS["ספריה"], (2, 4))
        sch = solver.Scheduler(school.requirements(), time_limit=1)
        mid = next(i for i, m in enumerate(sch.meetings) if m.subject == "ספריה")
        periods = {p for (_, p) in sch.x[mid]}
        self.assertTrue(periods and periods <= {2, 3, 4},
                        f"ספריה offered periods {sorted(periods)}")

    def test_only_three_teachers_may_work_a_two_lesson_day(self):
        allowed = {t.name for t in school.TEACHERS if t.min_per_day < 3}
        self.assertEqual(allowed,
                         {"הדסה ויזמן", "מרגלית לוי", "רותי אסולין"})
        for t in school.TEACHERS:
            self.assertGreaterEqual(t.min_per_day, 2)

    def test_validator_rejects_a_day_below_the_floor(self):
        t = T("מורה", off_fixed=(FRI,), min_per_day=3)
        c = SchoolClass("דמה", _span(0, 4))
        ms = expand([Requirement("דמה", "מ", ("מורה",), 2, (1, 1))])
        tt = Timetable({0: (SUN, 1), 1: (MON, 1)}, ms)   # two one-hour days
        with tiny_school([t], [c]):
            ok, bad = validate.validate(tt)
        self.assertFalse(ok)
        self.assertTrue(any("מינימום 3" in b for b in bad), bad)

    def test_validator_rejects_a_two_hour_day_at_the_end_of_the_day(self):
        t = T("מורה", off_fixed=(FRI,), min_per_day=2)
        c = SchoolClass("דמה", _span(0, 7))
        ms = expand([Requirement("דמה", "מ", ("מורה",), 2, (2,))])
        tt = Timetable({0: (SUN, 6)}, ms)                # periods 6-7 only
        with tiny_school([t], [c]):
            ok, bad = validate.validate(tt)
        self.assertFalse(ok)
        self.assertTrue(any("בסוף היום" in b for b in bad), bad)

    def test_validator_accepts_an_early_two_hour_day(self):
        t = T("מורה", off_fixed=(FRI,), min_per_day=2)
        c = SchoolClass("דמה", _span(0, 7))
        ms = expand([Requirement("דמה", "מ", ("מורה",), 2, (2,))])
        tt = Timetable({0: (SUN, 1)}, ms)
        with tiny_school([t], [c]):
            ok, bad = validate.validate(tt)
        self.assertTrue(ok, bad)


class TestHardeningLadder(unittest.TestCase):
    """`main.py` hardens rules by label, so the labels must stay in sync."""

    def test_window_rule_targets_the_longest_day_not_the_fifth(self):
        """A window is a rest only if the day around it is long."""
        self.assertFalse(school.FIFTH_DAY["window"],
                         "§11.2(ג) puts the window on the shortest day")
        self.assertTrue(school.WINDOW_ON_LONGEST_DAY)
        sch = solver.Scheduler(school.requirements(), time_limit=1)
        self.assertIn("חלון שאינו ביום הארוך ביותר", sch._penalty_terms)
        self.assertNotIn(solver.FIFTH_WINDOW, sch._penalty_terms)

    def test_mandatory_labels_match_what_the_model_prices(self):
        sch = solver.Scheduler(school.requirements(), time_limit=1)
        priced = {name for name, items in sch._penalty_terms.items()
                  if any(w >= solver.W_MANDATORY for _, w in items)}
        self.assertTrue(priced <= solver.MANDATORY_LABELS,
                        f"unlisted mandatory labels: "
                        f"{priced - solver.MANDATORY_LABELS}")

    def test_hardening_removes_the_label_from_the_objective(self):
        label = "מחנכת פותחת את היום בכיתתה"
        sch = solver.Scheduler(school.requirements(), time_limit=1,
                               harden=frozenset([label]))
        self.assertNotIn(label, sch._penalty_terms)
        # ...and leaves the others priced
        self.assertIn("יום חמישי: מתחילה מאוחר", sch._penalty_terms)

    def test_harden_true_clears_the_whole_mandatory_tier(self):
        sch = solver.Scheduler(school.requirements(), time_limit=1, harden=True)
        for name, items in sch._penalty_terms.items():
            self.assertTrue(all(w < solver.W_MANDATORY for _, w in items),
                            f"{name} still priced at the mandatory tier")


class TestSimaZeeviRequests(unittest.TestCase):
    """סימה זאבי asked for no window at all, and אומנות at the end of her day.

    Both belong to her alone, so they are stated as data in `school.py`
    rather than as something the solver knows about her by name.
    """

    def test_she_is_listed_for_both(self):
        self.assertIn("סימה זאבי", school.NO_WINDOW_TEACHERS)
        self.assertIn(("סימה זאבי", "אומנות"), school.SUBJECT_ENDS_DAY)

    def test_she_is_left_out_of_the_wish_for_a_window(self):
        """The school wants most teachers to have one; she is not a candidate."""
        sch = solver.Scheduler(school.requirements(), time_limit=1)
        names = {v.name for v in sch.m.Proto().variables}
        self.assertNotIn("haswin_סימה זאבי", names)
        self.assertIn("haswin_חנה אביטן", names)

    def test_validator_rejects_a_window_for_her(self):
        t = T("סימה זאבי", off_fixed=(FRI,))
        c = SchoolClass("דמה", _span(0, 3))
        reqs = [Requirement("דמה", "מ1", (t.name,), 1, (1,)),
                Requirement("דמה", "מ2", (t.name,), 1, (1,))]
        ms = expand(reqs)
        tt = Timetable({0: (SUN, 1), 1: (SUN, 3)}, ms)   # a hole at period 2
        with tiny_school([t], [c]):
            school.NO_WINDOW_TEACHERS = ("סימה זאבי",)
            ok, bad = validate.validate(tt)
        self.assertFalse(ok)
        self.assertTrue(any("חלונות" in b for b in bad), bad)

    def test_validator_rejects_art_that_is_not_her_last_lesson(self):
        t = T("סימה זאבי", off_fixed=(FRI,))
        c = SchoolClass("דמה", _span(0, 3))
        reqs = [Requirement("דמה", "אומנות", (t.name,), 1, (1,)),
                Requirement("דמה", "מ2", (t.name,), 1, (1,))]
        ms = expand(reqs)
        with tiny_school([t], [c]):
            school.SUBJECT_ENDS_DAY = (("סימה זאבי", "אומנות"),)
            ok, bad = validate.validate(Timetable({0: (SUN, 1), 1: (SUN, 2)}, ms))
            self.assertFalse(ok)
            self.assertTrue(any("אומנות" in b for b in bad), bad)
            # ...and accepts it the other way round
            ok, bad = validate.validate(Timetable({0: (SUN, 2), 1: (SUN, 1)}, ms))
        self.assertTrue(ok, bad)

    def test_solver_puts_art_last_and_leaves_no_window(self):
        t = T("סימה זאבי", off_fixed=(FRI,))
        other = T("אחרת", off_fixed=(FRI,))
        c = SchoolClass("דמה", _span(0, 4))
        reqs = [Requirement("דמה", "אומנות", (t.name,), 1, (1,)),
                Requirement("דמה", "מ2", (t.name,), 3, (1, 1, 1)),
                Requirement("דמה", "מ3", (other.name,), 2, (1, 1))]
        with tiny_school([t, other], [c]):
            school.NO_WINDOW_TEACHERS = ("סימה זאבי",)
            school.SUBJECT_ENDS_DAY = (("סימה זאבי", "אומנות"),)
            res = solver.Scheduler(reqs, time_limit=30).solve()
            self.assertIn(res.status, ("OPTIMAL", "FEASIBLE"))
            ok, bad = validate.validate(res.timetable)
        self.assertTrue(ok, bad)
        grid = res.timetable.by_teacher(t.name)
        for (d, p), m in grid.items():
            if m.subject == "אומנות":
                self.assertFalse([q for (dd, q) in grid if dd == d and q > p],
                                 "אומנות must be the last lesson of her day")


class TestOpeningLesson(unittest.TestCase):
    """`polish.py` may only exchange lessons no rule can tell apart."""

    def _homeroom(self, subject):
        t = T("מחנכת", off_fixed=(FRI,), homeroom="דמה")
        c = SchoolClass("דמה", {d: (0, 3) for d in range(6)})
        reqs = [Requirement("דמה", subject, (t.name,), 1, (1,)),
                Requirement("דמה", "תורה", (t.name,), 1, (1,))]
        return t, c, expand(reqs)

    def test_torah_takes_the_opening_period(self):
        t, c, ms = self._homeroom("חשבון")
        tt = Timetable({0: (SUN, 1), 1: (SUN, 2)}, ms)
        with tiny_school([t], [c]):
            school.OPEN_OWN_CLASS = {"דמה": (1, 1)}
            moved = polish.open_with_torah(tt)
        self.assertEqual(moved[1], (SUN, 1))
        self.assertEqual(moved[0], (SUN, 2))

    def test_a_day_without_torah_is_left_alone(self):
        t, c, ms = self._homeroom("חשבון")
        tt = Timetable({0: (SUN, 1), 1: (MON, 1)}, ms)
        with tiny_school([t], [c]):
            school.OPEN_OWN_CLASS = {"דמה": (1, 1)}
            self.assertEqual(polish.open_with_torah(tt), tt.placement)

    def test_a_pinned_subject_keeps_its_period(self):
        """פ. שבוע opens Friday on purpose; תורה does not displace it."""
        t, c, ms = self._homeroom("פ. שבוע")
        tt = Timetable({0: (FRI, 1), 1: (FRI, 2)}, ms)
        with tiny_school([t], [c]):
            school.OPEN_OWN_CLASS = {"דמה": (1, 1)}
            self.assertEqual(polish.open_with_torah(tt), tt.placement)

    def test_the_real_school_stays_valid_after_polishing(self):
        saved = json.loads((pathlib.Path(__file__).resolve().parents[1]
                            / "solution.json").read_text(encoding="utf-8"))
        ms = expand(school.requirements())
        tt = Timetable(align(saved["placed"], ms), ms)
        self.assertEqual(
            validate._violations(Timetable(polish.open_with_torah(tt), ms)),
            validate._violations(tt),
            "an exchange no rule can see must not add a violation")


class TestHandPlacedLessons(unittest.TestCase):
    """The school placed four lessons itself; nothing may move them."""

    def test_the_pins_are_where_the_school_put_them(self):
        self.assertEqual(school.PINNED[("ח", SUN, 1)], "היסטוריה")
        self.assertEqual(school.PINNED[("ה", SUN, 6)], 'ג"ג-מולדת')
        self.assertEqual(school.PINNED[("ה", THU, 4)], "תורה")
        self.assertEqual(school.DAY_ENDS_AT[("ח", SUN)], 6)
        self.assertEqual(school.DAY_ENDS_AT[("ה", THU)], 5)

    def test_torah_for_class_het_lost_the_hour_not_ruti(self):
        by = {(r.klass, r.subject): r.hours for r in school.requirements()}
        self.assertEqual(by[("ח", "תורה")], 4)
        self.assertEqual(by[("ח", "היסטוריה")], 2)

    def test_validator_rejects_a_pinned_slot_that_holds_something_else(self):
        t, other = T("מחנכת", off_fixed=(FRI,)), T("אחרת", off_fixed=(FRI,))
        c = SchoolClass("דמה", _span(0, 3))
        reqs = [Requirement("דמה", "מ1", (t.name,), 1, (1,)),
                Requirement("דמה", "מ2", (other.name,), 1, (1,))]
        ms = expand(reqs)
        with tiny_school([t, other], [c]):
            school.PINNED = {("דמה", SUN, 1): "מ2"}
            ok, bad = validate.validate(Timetable({0: (SUN, 1), 1: (SUN, 2)}, ms))
            self.assertFalse(ok)
            self.assertTrue(any("מ2" in b for b in bad), bad)
            ok, bad = validate.validate(Timetable({0: (SUN, 2), 1: (SUN, 1)}, ms))
        self.assertTrue(ok, bad)

    def test_validator_rejects_a_day_that_runs_past_its_end(self):
        t = T("מורה", off_fixed=(FRI,))
        c = SchoolClass("דמה", _span(0, 3))
        reqs = [Requirement("דמה", f"מ{i}", (t.name,), 1, (1,))
                for i in range(3)]
        ms = expand(reqs)
        with tiny_school([t], [c]):
            school.DAY_ENDS_AT = {("דמה", SUN): 2}
            ok, bad = validate.validate(
                Timetable({0: (SUN, 1), 1: (SUN, 2), 2: (SUN, 3)}, ms))
            self.assertFalse(ok)
            self.assertTrue(any("נגמר" in b for b in bad), bad)
            ok, bad = validate.validate(
                Timetable({0: (SUN, 1), 1: (SUN, 2), 2: (MON, 1)}, ms))
        self.assertTrue(ok, bad)

    def test_polish_leaves_a_pinned_opening_alone(self):
        """תורה takes the first period — except where the school said otherwise."""
        t = T("מחנכת", off_fixed=(FRI,), homeroom="דמה")
        c = SchoolClass("דמה", _span(0, 3))
        reqs = [Requirement("דמה", "היסטוריה", (t.name,), 1, (1,)),
                Requirement("דמה", "תורה", (t.name,), 1, (1,))]
        ms = expand(reqs)
        tt = Timetable({0: (SUN, 1), 1: (SUN, 2)}, ms)
        with tiny_school([t], [c]):
            school.OPEN_OWN_CLASS = {"דמה": (1, 1)}
            school.PINNED = {("דמה", SUN, 1): "היסטוריה"}
            self.assertEqual(polish.open_with_torah(tt), tt.placement)


class TestValidator(unittest.TestCase):
    """The validator must reject broken timetables — it is the safety net."""

    def _one_class(self):
        t = T("מורה א"), T("מורה ב")
        c = SchoolClass("דמה", _span(0, 4))
        return t, c

    def test_detects_teacher_double_booking(self):
        (t1, t2), c = self._one_class()
        reqs = [Requirement("דמה", "מ1", (t1.name,), 1, (1,)),
                Requirement("דמה", "מ2", (t1.name,), 1, (1,))]
        ms = expand(reqs)
        tt = Timetable({0: (SUN, 1), 1: (SUN, 1)}, ms)
        with tiny_school([t1, t2], [c]):
            ok, bad = validate.validate(tt)
        self.assertFalse(ok)
        self.assertTrue(any(t1.name in b for b in bad))

    def test_detects_student_gap(self):
        (t1, t2), c = self._one_class()
        reqs = [Requirement("דמה", "מ1", (t1.name,), 1, (1,)),
                Requirement("דמה", "מ2", (t2.name,), 1, (1,))]
        ms = expand(reqs)
        tt = Timetable({0: (SUN, 1), 1: (SUN, 3)}, ms)   # hole at period 2
        with tiny_school([t1, t2], [c]):
            ok, bad = validate.validate(tt)
        self.assertFalse(ok)
        self.assertTrue(any("חלון לתלמידות" in b for b in bad))

    def test_detects_scheduling_on_a_day_off(self):
        t1 = T("מורה א", off_fixed=(SUN,))
        c = SchoolClass("דמה", _span(0, 4))
        ms = expand([Requirement("דמה", "מ1", (t1.name,), 1, (1,))])
        tt = Timetable({0: (SUN, 1)}, ms)
        with tiny_school([t1], [c]):
            ok, bad = validate.validate(tt)
        self.assertFalse(ok)
        self.assertTrue(any("יום חופשי" in b for b in bad))

    def test_accepts_a_clean_timetable(self):
        (t1, t2), c = self._one_class()
        reqs = [Requirement("דמה", "מ1", (t1.name,), 1, (1,)),
                Requirement("דמה", "מ2", (t2.name,), 1, (1,))]
        ms = expand(reqs)
        tt = Timetable({0: (SUN, 1), 1: (SUN, 2)}, ms)
        with tiny_school([t1, t2], [c]):
            ok, bad = validate.validate(tt)
        self.assertTrue(ok, bad)


class TestSolverEndToEnd(unittest.TestCase):
    def test_student_contiguity_can_make_an_instance_impossible(self):
        """Both classes must open at period 1, but one teacher is off.

        Recorded because it is a real property of the model, not a bug: it is
        why a class's day is a solid block rather than a set of hours.
        """
        teachers = [T("א", off_fixed=(TUE, FRI)),
                    T("ב", off_fixed=(WED, FRI))]
        classes = [SchoolClass("כ1", _span(0, 4)), SchoolClass("כ2", _span(0, 4))]
        reqs = [Requirement("כ1", "מ1", ("א",), 6, (2, 2, 1, 1)),
                Requirement("כ1", "מ2", ("ב",), 4, (1, 1, 1, 1)),
                Requirement("כ2", "מ1", ("ב",), 6, (2, 2, 1, 1)),
                Requirement("כ2", "מ2", ("א",), 4, (1, 1, 1, 1))]
        with tiny_school(teachers, classes):
            self.assertEqual(
                solver.Scheduler(reqs, time_limit=30).solve().status,
                "INFEASIBLE")
            # ...and it becomes solvable the moment student gaps are allowed.
            relaxed = solver.Scheduler(reqs, time_limit=30,
                                       relax=frozenset(["contiguity"])).solve()
            self.assertIn(relaxed.status, ("OPTIMAL", "FEASIBLE"))


    def test_small_school_solves_and_validates(self):
        # Two teachers can cover two classes only while both classes can
        # start at the first period every day they are in session — with a
        # teacher off, a second class has nobody to open the day.  Kept
        # comfortably inside that limit.
        teachers = [T("א", off_fixed=(FRI,)), T("ב", off_fixed=(FRI,))]
        classes = [SchoolClass("כ1", _span(0, 4)), SchoolClass("כ2", _span(0, 4))]
        reqs = [Requirement("כ1", "מ1", ("א",), 4, (2, 1, 1)),
                Requirement("כ1", "מ2", ("ב",), 2, (1, 1)),
                Requirement("כ2", "מ1", ("ב",), 4, (2, 1, 1)),
                Requirement("כ2", "מ2", ("א",), 2, (1, 1))]
        with tiny_school(teachers, classes):
            res = solver.Scheduler(reqs, time_limit=30).solve()
            self.assertIn(res.status, ("OPTIMAL", "FEASIBLE"))
            ok, bad = validate.validate(res.timetable)
            self.assertTrue(ok, bad)
            # doubles must land on consecutive periods of one day
            for mid, m in enumerate(res.timetable.meetings):
                if m.length == 2:
                    slots = res.timetable.occupied(mid)
                    self.assertEqual(slots[0][0], slots[1][0])
                    self.assertEqual(slots[1][1] - slots[0][1], 1)

    def test_impossible_instance_is_reported_not_hung(self):
        """Two classes, one teacher, more hours than the week holds."""
        teachers = [T("יחידה", off_fixed=(FRI,))]
        classes = [SchoolClass("כ1", _span(0, 7)), SchoolClass("כ2", _span(0, 7))]
        reqs = [Requirement("כ1", "מ", ("יחידה",), 30, (1,) * 30),
                Requirement("כ2", "מ", ("יחידה",), 30, (1,) * 30)]
        with tiny_school(teachers, classes):
            errors, _ = checks.summarise(checks.run(reqs))
            self.assertGreater(errors, 0, "Phase 0 must reject this by counting")


class TestWordSheet(unittest.TestCase):
    """The .docx hand-out.

    Its first version put every heading first and every table afterwards —
    each table was correct and the document was unreadable, which no
    cell-level check catches.  So the assertion here is about *order*.
    """

    def _document(self):
        docx = self.docx
        import word
        reqs = school.requirements()
        meetings = expand(reqs)
        # An empty placement exercises the layout without needing a solve;
        # every cell renders as "free", which is the harder path anyway.
        tt = Timetable({}, meetings)
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "sheet.docx"
            word.sheet_docx(tt, "בדיקה", out)
            self.assertTrue(out.exists())
            return docx.Document(str(out))

    def setUp(self):
        try:
            import docx
        except ImportError:
            self.skipTest("python-docx not installed")
        self.docx = docx

    def test_every_heading_is_followed_by_its_grid(self):
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = self._document()
        seq = []
        for child in doc.element.body.iterchildren():
            if child.tag == qn("w:p"):
                par = Paragraph(child, doc)
                if par.text.strip():
                    seq.append(("H" if par.style.name.startswith(
                        ("Heading", "Title")) else "p", par.text.strip()))
            elif child.tag == qn("w:tbl"):
                seq.append(("TABLE", Table(child, doc)))

        tables = [x for k, x in seq if k == "TABLE"]
        self.assertEqual(len(tables),
                         len(school.CLASSES) + len(school.TEACHERS))
        for t in tables:
            self.assertEqual(len(t.columns), len(DAYS) + 1)
            self.assertEqual(len(t.rows), max(PERIODS_PER_DAY) + 1)

        # Every grid heading names one class or one teacher, and the very
        # next thing in the body must be that grid.
        named = ({f"כיתה {c.name}" for c in school.CLASSES}
                 | {t.name for t in school.TEACHERS})
        found = 0
        for i, (kind, text) in enumerate(seq):
            if kind != "H":
                continue
            if not any(text.startswith(n) for n in named):
                continue
            found += 1
            self.assertLess(i + 1, len(seq), f"{text} ends the document")
            self.assertEqual(seq[i + 1][0], "TABLE",
                             f"'{text}' is not followed by its grid")
        self.assertEqual(found, len(school.CLASSES) + len(school.TEACHERS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
