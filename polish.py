# -*- coding: utf-8 -*-
"""Label-level tidying that no constraint can see.

Two single-period meetings with the same teacher, the same class and the same
day may exchange slots without any rule noticing: the same teacher is busy in
the same periods, the same class is busy in the same periods, and each subject
still appears once that day.  Everything here is such an exchange, applied
after the solver has finished and re-checked by `validate.py` like any other
placement.  Nothing in this file is a constraint — if the exchange cannot be
made the timetable is left exactly as the solver returned it.
"""
from __future__ import annotations

import school
from model import DAYS, Meeting, Timetable


def _swappable(mt: Meeting) -> bool:
    """A meeting whose slot carries no meaning of its own.

    A subject the school pinned to one day of the week is pinned there
    because of what that day is — פ. שבוע opens Friday on purpose — so it
    keeps its period even though moving it inside the day would break no
    rule.
    """
    return (mt.length == 1
            and mt.subject not in school.SUBJECT_PERIODS
            and mt.subject not in school.SUBJECT_FIXED_DAY
            and mt.subject not in school.NOT_FIRST_PERIOD
            and not any(mt.subject == s and t in mt.teachers
                        for t, s in school.SUBJECT_ENDS_DAY))


def open_with_torah(tt: Timetable) -> dict[int, tuple[int, int]]:
    """Start the day with תורה on the days the homeroom teacher opens it.

    §11.1 says she teaches the first period in her own class; the school
    wants that period to be תורה whenever the class has תורה that day.  On
    days it does not, the opening lesson is left alone.
    """
    placement = dict(tt.placement)
    subject = school.OPENING_SUBJECT
    for t in school.TEACHERS:
        if not t.homeroom:
            continue
        own = [mid for mid, mt in enumerate(tt.meetings)
               if mt.klass == t.homeroom and t.name in mt.teachers]
        for d in range(len(DAYS)):
            if (t.homeroom, d, 1) in school.PINNED:
                continue          # the school chose this period itself
            opening = [mid for mid in own if placement.get(mid) == (d, 1)]
            if len(opening) != 1:
                continue
            first = opening[0]
            if tt.meetings[first].subject == subject \
                    or not _swappable(tt.meetings[first]):
                continue
            later = [mid for mid in own
                     if tt.meetings[mid].subject == subject
                     and placement.get(mid, (-1, 0))[0] == d
                     and (t.homeroom, *placement[mid]) not in school.PINNED
                     and _swappable(tt.meetings[mid])]
            if not later:
                continue
            placement[first], placement[later[0]] = \
                placement[later[0]], placement[first]
    return placement
