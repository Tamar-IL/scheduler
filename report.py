# -*- coding: utf-8 -*-
"""Rendering: terminal grids and a standalone HTML page."""
from __future__ import annotations

import html

import school
from model import DAYS, PERIODS_PER_DAY, Timetable


def _cell(m, mode: str = "class") -> tuple[str, str]:
    """Subject plus the thing the reader does *not* already know.

    A class grid is headed by the class, so its cells name the teacher.  A
    teacher grid is headed by the teacher, so its cells must name the class —
    otherwise every cell just repeats the heading.
    """
    if m is None:
        return "", ""
    if mode == "teacher":
        if m.klass is None:
            return m.subject, "ללא כיתה"
        if len(m.teachers) > 1:
            return m.subject, f"כיתה {m.klass} · הקבצה"
        return m.subject, f"כיתה {m.klass}"
    return m.subject, "/".join(m.teachers)


def terminal(tt: Timetable, klass: str, mode: str = "class") -> str:
    grid = tt.by_class(klass) if mode == "class" else tt.by_teacher(klass)
    width = 22
    head = "שעה".center(4) + "".join(d.center(width) for d in DAYS)
    lines = [f"כיתה {klass}", head, "-" * len(head)]
    for p in range(1, max(PERIODS_PER_DAY) + 1):
        row = str(p).center(4)
        for d in range(len(DAYS)):
            if p > PERIODS_PER_DAY[d]:
                row += "".center(width)
                continue
            s, t = _cell(grid.get((d, p)), mode)
            row += (f"{s} · {t}" if s else "—").center(width)
        lines.append(row)
    return "\n".join(lines)


_CSS = """
:root{--bg:#fbfaf7;--fg:#1c1b19;--line:#dcd8d0;--head:#efece5;--muted:#6b6862;
      --accent:#7a5cff;--free:#f5f3ef;}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#16151a;--fg:#eceaf2;--line:#302e38;--head:#232028;--muted:#9b96a6;
  --accent:#a894ff;--free:#1c1b21;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);direction:rtl;
 font:15px/1.5 "Segoe UI",Arial,sans-serif;padding:28px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:17px;margin:32px 0 8px;padding-bottom:5px;
   border-bottom:2px solid var(--accent)}
.meta{color:var(--muted);font-size:13px;margin-bottom:20px}
.wrap{overflow-x:auto;margin-bottom:14px}
table{border-collapse:collapse;width:100%;min-width:640px}
th,td{border:1px solid var(--line);padding:5px 7px;text-align:center;
      font-size:12.5px;vertical-align:middle}
th{background:var(--head);font-weight:600}
td.free{background:var(--free);color:var(--muted)}
td .s{display:block;font-weight:600}
td .t{display:block;color:var(--muted);font-size:11px}
.gap{background:#ffd9d9;color:#7a1f1f}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .gap{
  background:#4a1f26;color:#ffc9c9}}
.note{background:var(--head);border-right:3px solid var(--accent);
      padding:10px 14px;margin:14px 0;font-size:13.5px}
"""


def _table(cells, periods_of_day, gap_marks=frozenset(),
           mode: str = "class") -> str:
    rows = []
    head = "<tr><th>שעה</th>" + "".join(f"<th>{d}</th>" for d in DAYS) + "</tr>"
    for p in range(1, max(PERIODS_PER_DAY) + 1):
        tds = [f"<th>{p}</th>"]
        for d in range(len(DAYS)):
            if p > periods_of_day[d]:
                tds.append('<td class="free"></td>')
                continue
            m = cells.get((d, p))
            if m is None:
                cls = "gap" if (d, p) in gap_marks else "free"
                tds.append(f'<td class="{cls}">'
                           + ("חלון" if cls == "gap" else "—") + "</td>")
            else:
                s, t = _cell(m, mode)
                tds.append(f'<td><span class="s">{html.escape(s)}</span>'
                           f'<span class="t">{html.escape(t)}</span></td>')
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return ('<div class="wrap"><table>' + head + "".join(rows)
            + "</table></div>")


def summary(tt: Timetable) -> str:
    """The two tallies the school actually checks a timetable against."""
    lines = ["  פתיחת היום בכיתה (מחנכת מלמדת שעה 1 בכיתתה):"]
    for t in school.TEACHERS:
        spec = school.OPEN_OWN_CLASS.get(t.homeroom or "")
        if spec is None:
            continue
        minimum, preferred = spec
        grid = tt.by_teacher(t.name)
        days = [DAYS[d] for d in range(len(DAYS))
                if (m := grid.get((d, 1))) is not None and m.klass == t.homeroom]
        mark = "✓" if len(days) >= minimum else f"✗ חסר {minimum - len(days)}"
        extra = "" if len(days) >= preferred else f" (מועדף {preferred})"
        lines.append(f"    {t.name:14} כיתה {t.homeroom:3} "
                     f"{len(days)}/{minimum} {mark}{extra}  {', '.join(days)}")

    lines.append("")
    with_window, without = [], []
    for t in school.TEACHERS:
        grid = tt.by_teacher(t.name)
        total, where = 0, []
        for d in range(len(DAYS)):
            busy = sorted(p for (dd, p) in grid if dd == d)
            if not busy:
                continue
            n = (max(busy) - min(busy) + 1) - len(busy)
            total += n
            if n:
                where.append(f"{DAYS[d]} ({len(busy)} ש')")
        (with_window if total else without).append((t.name, where))
    lines.append(f"  חלונות: {len(with_window)} מורות עם חלון, "
                 f"{len(without)} בלי (מתוך {len(school.TEACHERS)}):")
    for name, where in with_window:
        lines.append(f"    {name:14} {', '.join(where)}")
    if without:
        lines.append(f"    ללא חלון: {', '.join(n for n, _ in without)}")
    return "\n".join(lines)


def fifth_day(tt: Timetable) -> str:
    """§11.2 — audit the one non-opening day of each homeroom teacher.

    Re-derived from the finished timetable, like `validate.py`: it does not
    ask the solver what it intended, it looks at what came out.
    """
    lines = ["  היום החמישי של המחנכת (§11.2) — היום שאינו יום פתיחה:",
             f"    {'מחנכת':14}{'יום':8}{'שעות':14}"
             f"{'מתחילה':8}{'מסיימת':8}{'חלון':7}{'ארוך':7}"]
    for t in school.TEACHERS:
        if not t.homeroom:
            continue
        grid = tt.by_teacher(t.name)
        per_day = {d: sorted(p for (dd, p) in grid if dd == d)
                   for d in range(len(DAYS))}
        working = [d for d, ps in per_day.items() if ps]
        opens = [d for d in working
                 if (m := grid.get((d, 1))) is not None and m.klass == t.homeroom]
        rest = [d for d in working if d not in opens]
        longest = max(len(per_day[d]) for d in working)
        klass = tt.by_class(t.homeroom)
        if not rest:
            lines.append(f"    {t.name:14}{'—':8}"
                         f"פותחת בכל {len(opens)} ימי העבודה שלה")
            continue
        for d in rest:
            ps = per_day[d]
            class_last = max((q for (dd, q) in klass if dd == d), default=0)
            late_start = "✓" if 1 not in ps else "✗"
            ends_last = "✓" if ps and max(ps) == class_last else "✗"
            gaps = (max(ps) - min(ps) + 1) - len(ps)
            has_gap = "✓" if gaps else "✗"
            is_long = "✓" if len(ps) == longest else f"✗ {len(ps)}<{longest}"
            span = f"{min(ps)}–{max(ps)} ({len(ps)} ש')"
            lines.append(f"    {t.name:14}{DAYS[d]:8}{span:14}"
                         f"{late_start:8}{ends_last:8}{has_gap:7}{is_long:7}")
    return "\n".join(lines)


def _teacher_grids(tt: Timetable) -> list[str]:
    """Every teacher's week, headed by her name and her weekly hour count.

    A homeroom teacher's two חינוך hours are not lessons and occupy no period,
    so they are named beside the count rather than folded into it.
    """
    parts = []
    for t in school.TEACHERS:
        grid = tt.by_teacher(t.name)
        gaps = set()
        for d in range(len(DAYS)):
            busy = sorted(p for (dd, p) in grid if dd == d)
            if busy:
                gaps |= {(d, p) for p in range(busy[0], busy[-1] + 1)
                         if (d, p) not in grid}
        extra = (f" + {school.HOMEROOM_EDUCATION_HOURS} חינוך"
                 if t.homeroom else "")
        parts.append(f"<h3>{html.escape(t.name)} — {len(grid)} שעות"
                     f"{extra}</h3>")
        parts.append(_table(grid, PERIODS_PER_DAY, gaps, mode="teacher"))
    return parts


def sheet_page(tt: Timetable, title: str) -> str:
    """The hand-out: the ten class grids, then the seventeen teacher grids.

    No notes and no §11.2 audit — this is the page the school reads, not the
    one that explains what the solver had to trade away.
    """
    parts = [f"<!doctype html><html lang='he' dir='rtl'><head>"
             f"<meta charset='utf-8'><meta name='viewport' "
             f"content='width=device-width,initial-scale=1'>"
             f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
             f"<body><h1>{html.escape(title)}</h1>"]
    parts.append("<h2>מערכות הכיתות</h2>")
    for c in school.CLASSES:
        parts.append(f"<h3>כיתה {html.escape(c.name)}</h3>")
        parts.append(_table(tt.by_class(c.name), PERIODS_PER_DAY, mode="class"))
    parts.append("<h2>מערכות המורות</h2>")
    parts += _teacher_grids(tt)
    parts.append("</body></html>")
    return "".join(parts)


def html_page(tt: Timetable, title: str, notes: list[str]) -> str:
    parts = [f"<!doctype html><html lang='he' dir='rtl'><head>"
             f"<meta charset='utf-8'><meta name='viewport' "
             f"content='width=device-width,initial-scale=1'>"
             f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
             f"<body><h1>{html.escape(title)}</h1>"]
    if notes:
        parts.append('<div class="note">' +
                     "<br>".join(html.escape(n) for n in notes) + "</div>")

    parts.append("<h2>ביקורת §11.2 — היום החמישי</h2>")
    parts.append("<pre style='overflow-x:auto;font-size:12.5px;"
                 "background:var(--head);padding:12px;border-radius:4px'>"
                 + html.escape(fifth_day(tt)) + "</pre>")

    parts.append("<h2>מערכות הכיתות</h2>")
    for c in school.CLASSES:
        parts.append(f"<h3>כיתה {html.escape(c.name)}</h3>")
        parts.append(_table(tt.by_class(c.name), PERIODS_PER_DAY,
                            mode="class"))

    parts.append("<h2>מערכות המורות</h2>")
    for t in school.TEACHERS:
        grid = tt.by_teacher(t.name)
        gaps = set()
        for d in range(len(DAYS)):
            busy = sorted(p for (dd, p) in grid if dd == d)
            if busy:
                gaps |= {(d, p) for p in range(busy[0], busy[-1] + 1)
                         if (d, p) not in grid}
        hours = len(grid)
        parts.append(f"<h3>{html.escape(t.name)} — {hours} שעות"
                     + (f", {len(gaps)} חלונות" if gaps else ", ללא חלונות")
                     + "</h3>")
        parts.append(_table(grid, PERIODS_PER_DAY, gaps, mode="teacher"))
    parts.append("</body></html>")
    return "".join(parts)
