# -*- coding: utf-8 -*-
"""CLI: check the input, solve, verify the result, write the timetable out."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import checks
import model
import polish
import report
import school
import solver
import validate
from model import Timetable, align, expand


def _placement(saved: dict, meetings: list,
               free_rewritten: bool = False) -> dict[int, tuple[int, int]]:
    """Read a saved placement, by lesson identity where the file allows it.

    Files written before `placed` existed are keyed by meeting number, which
    is only right while the subject table has not changed since.

    A slot the school has since given to a particular lesson cannot still
    hold the old one, so those rows go.  That also decides *which* lesson the
    anchor gives up when the subject table has shed an hour: left to itself
    the surplus comes off the end of the week, and the end of the week is
    Friday, which has no slack at all.

    `free_rewritten` drops whole days the school has rewritten.  Pricing an
    anchor never needs it — a lesson that cannot stay put simply pays — but
    *pinning* one does: a class's day is a solid block, so a day with a
    lesson moved into or out of it has to be re-arranged as a whole, and
    pinning yesterday's version of it has no solution.
    """
    rows = saved.get("placed")
    if rows is None:
        return {int(k): tuple(v) for k, v in saved["placement"].items()}
    rows = [r for r in rows
            if school.PINNED.get((r[0], r[4], r[5]), r[1]) == r[1]]
    if free_rewritten:
        rewritten = ({(k, d) for k, d, _ in school.PINNED}
                     | set(school.DAY_ENDS_AT))
        rows = [r for r in rows if (r[0], r[4]) not in rewritten]
    return align(rows, meetings)


def _fifth_days(placement: dict[int, tuple[int, int]],
                meetings: list) -> dict[str, int]:
    """Each homeroom teacher's non-opening day, read out of a placement.

    Choosing ten fifth days at once is the combinatorial core of this
    instance; reading them off a timetable the school has already approved
    turns §11.2 into a repair problem, which is the only thing that has ever
    solved it well.  The teachers in `school.LATE_START_AWAY_FROM_OFF` are
    left out on purpose: counting settles their day (`checks.late_start_days`)
    and the approved timetable predates the rule, so its answer is the stale
    one being replaced.
    """
    out: dict[str, int] = {}
    for t in school.TEACHERS:
        if not t.homeroom or t.name in school.LATE_START_AWAY_FROM_OFF:
            continue
        for d in range(len(model.DAYS)):
            first = min((p for mid, (dd, p) in placement.items()
                         if dd == d and t.name in meetings[mid].teachers),
                        default=None)
            if first is not None and first != 1:
                out[t.name] = d
    return out


def _word(tt: Timetable, path: str):
    """Write the hand-out as .docx, and treat the writer as optional.

    python-docx is the one dependency that is not needed to *solve*, so a
    machine without it should still get its timetable and be told why the
    Word file is missing, rather than losing the whole run to an ImportError
    at the very last step.
    """
    try:
        import word
    except ImportError:
        print("  (python-docx חסר — הקובץ ל-Word לא נכתב. "
              "התקנה: pip install python-docx)")
        return None
    return word.sheet_docx(tt, 'מערכת שעות תשפ"ז',
                           pathlib.Path(path)).resolve()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="בניית מערכת שעות בית ספרית")
    ap.add_argument("--time-limit", type=float, default=300.0,
                    help="שניות לפתרון (ברירת מחדל 300)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="timetable.html")
    ap.add_argument("--sheet", default="כולל מורות.html",
                    help="דף ההגשה: מערכות הכיתות ואחריהן מערכות המורות")
    ap.add_argument("--word", default="כולל מורות.docx",
                    help="דף ההגשה גם כקובץ Word לעריכה. ריק = לא לכתוב.")
    ap.add_argument("--relax", default="",
                    help="כללים לביטול, מופרדים בפסיק (לאבחון חוסר-פתירות)")
    ap.add_argument("--checks-only", action="store_true")
    ap.add_argument("--save", default="solution.json",
                    help="קובץ לשמירת השיבוץ, לרינדור מחדש ללא פתרון נוסף")
    ap.add_argument("--load", default=None,
                    help="טעינת שיבוץ שמור ורינדור בלבד")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--hint-from", default=None,
                    help="שיבוץ שמור לשימוש כזרע התחלה לפותר")
    ap.add_argument("--priced", action="store_true",
                    help="לדלג על הניסיון הקשיח ולתמחר מיד (אבחון)")
    ap.add_argument("--anchor", default=None,
                    help="שיבוץ מאושר שממנו לא לזוז אלא אם כלל מחייב זאת")
    ap.add_argument("--anchor-weight", type=int, default=solver.W_STRONG,
                    help="מחיר הזזת מפגש מהשיבוץ המאושר")
    ap.add_argument("--pin-fifth", action="store_true",
                    help="לקבע את היום החמישי של כל מחנכת כפי שהוא בשיבוץ "
                         "המאושר (--anchor), במקום לחפש אותו מחדש")
    ap.add_argument("--harden", default="",
                    help="תוויות עונשין להקשחה מעבר לאלה שהעוגן כבר מקיים, "
                         "מופרדות בפסיק. כלל חדש שהעוגן שובר לא ייכנס לרשימת "
                         "ההקשחה מאליו — כאן אומרים שהוא בכל זאת חייב.")
    args = ap.parse_args(argv)

    reqs = school.requirements()

    print("── שלב 0: בדיקות היתכנות מוקדמות ──")
    findings = checks.run(reqs)
    for f in findings:
        print(f)
    errors, tight = checks.summarise(findings)
    print(f"\n  {errors} שגיאות, {tight} נקודות מתוחות\n")
    if args.checks_only:
        return 1 if errors else 0

    if args.load:
        saved = json.loads(pathlib.Path(args.load).read_text(encoding="utf-8"))
        meetings = expand(reqs)
        tt = Timetable(_placement(saved, meetings), meetings)
        ok, problems = validate.validate(tt)
        print(f"נטען {args.load}: "
              + ("כל האילוצים הקשיחים מתקיימים ✓" if ok
                 else f"{len(problems)} הפרות"))
        for p in problems:
            print(f"   {p}")
        print(report.summary(tt))
        out = pathlib.Path(args.out)
        out.write_text(report.html_page(tt, 'מערכת שעות תשפ"ז',
                                        saved.get("notes", [])),
                       encoding="utf-8")
        sheet = pathlib.Path(args.sheet)
        sheet.write_text(report.sheet_page(tt, 'מערכת שעות תשפ"ז'),
                         encoding="utf-8")
        print(f"נכתב: {out.resolve()}")
        print(f"נכתב: {sheet.resolve()}")
        if args.word:
            print(f"נכתב: {_word(tt, args.word)}")
        return 0 if ok else 3

    print("── שלב 1: פתרון ──")
    relax = frozenset(x.strip() for x in args.relax.split(",") if x.strip())

    hint = {}
    if args.hint_from and pathlib.Path(args.hint_from).exists():
        seed = json.loads(pathlib.Path(args.hint_from).read_text(encoding="utf-8"))
        hint = _placement(seed, expand(reqs))
        print(f"  זרע התחלה מתוך {args.hint_from} ({len(hint)} מפגשים)")

    # An approved placement is both the seed and the thing to stay near: a
    # new rule should be answered by repairing the timetable the school has
    # already read, not by returning an unrecognisable one that scores the
    # same.
    anchor, kept, fifth = {}, frozenset(), None
    if args.anchor and pathlib.Path(args.anchor).exists():
        approved = json.loads(
            pathlib.Path(args.anchor).read_text(encoding="utf-8"))
        meetings = expand(reqs)
        anchor = _placement(approved, meetings)
        hint = hint or anchor
        if args.pin_fifth:
            fifth = _fifth_days(anchor, meetings)
            print("  ימים חמישיים מקובעים מתוך השיבוץ המאושר: "
                  + ", ".join(f"{n} — {model.DAYS[d]}"
                              for n, d in fifth.items()))
        # Scored with the per-teacher requests set aside: the approved
        # timetable was built before they were made, so measuring it against
        # them would only report that it is the thing being replaced.
        # Score the whole anchor first.  Freeing the rewritten days is only
        # for an anchor that predates them: it leaves the scoring solve to
        # re-fill those days with everything else nailed down, which is a far
        # harder problem than the real search and so under-reports what the
        # timetable achieves — and every rule it under-reports is one the
        # ladder then fails to harden.
        budget = max(60.0, args.time_limit * 0.15)
        paid = solver.score(reqs, anchor, relax | solver.TEACHER_REQUESTS,
                            time_limit=budget, workers=args.workers)
        if paid is None:
            paid = solver.score(reqs, _placement(approved, meetings, True),
                                relax | solver.TEACHER_REQUESTS,
                                time_limit=budget, workers=args.workers)
        kept = frozenset() if paid is None else (
            solver.MANDATORY_LABELS - set(paid) - solver.NEVER_HARDEN)
        print(f"  שיבוץ מאושר מתוך {args.anchor} ({len(anchor)} מפגשים), "
              f"מחיר הזזה {args.anchor_weight}")
        if paid is None:
            print("  השיבוץ המאושר אינו חוקי במודל הנוכחי — אין מה לאכוף ממנו")
        else:
            print(f"  {len(kept)} מתוך {len(solver.MANDATORY_LABELS)} כללי "
                  f"חובה מתקיימים בו במלואם ויאוכפו כאילוצים קשיחים")
            for label, cost in sorted(paid.items(), key=lambda kv: -kv[1]):
                if label in solver.MANDATORY_LABELS:
                    print(f"     {cost:6}  {label}  — כבר לא מתקיים, יישאר "
                          f"מתומחר")

    # The mandatory rules are worth *enforcing* once phase 0 has shown they
    # are satisfiable — pricing them only makes CP-SAT pay 1000 where it
    # could have pruned.  But the full set is over-determined, so step down
    # a rung at a time and keep the strongest one that actually solves.
    # Whatever ends up priced is exactly what the school has to decide about.
    #
    # An anchor names a better first rung than guessing does: every mandatory
    # rule the approved timetable already keeps is one a new timetable must
    # not lose, and the anchor itself is the witness that that set holds
    # together.  It cannot forbid what the school is already living with, so
    # the two blind top rungs are not worth the minutes.
    # A rule the school has just asked for is one the anchor fails by
    # definition, so it never reaches `kept` on its own — and priced at 1000
    # against an anchor that charges 100 a move, it is cheaper to pay than to
    # repair.  Naming it here is how "this one is not negotiable" is said.
    extra = frozenset(x.strip() for x in args.harden.split(",") if x.strip())
    unknown = extra - solver.MANDATORY_LABELS
    if unknown:
        print(f"  אזהרה: תוויות שאינן בדרגת החובה: {', '.join(unknown)}")
    if anchor and kept:
        ladder = [] if args.priced else [
            ("כללי החובה שהשיבוץ המאושר כבר מקיים", kept | extra, 0.5)]
    elif anchor:
        ladder = [] if not extra or args.priced else [
            ("הכללים שנדרשו במפורש", extra, 0.5)]
    else:
        ladder = [] if args.priced else [
            ("כל כללי החובה כאילוצים קשיחים", True, 0.12),
            (f"הכל קשיח פרט ל'{solver.FIFTH_WINDOW}'",
             solver.MANDATORY_LABELS - {solver.FIFTH_WINDOW}, 0.55),
        ]
    ladder.append(("כל כללי החובה מתומחרים", False, 1.0))

    result, spent = None, 0.0
    for label, harden, share in ladder:
        budget = max(30.0, args.time_limit * share - spent)
        print(f"  ניסיון: {label}  (עד {budget:.0f} שניות)", flush=True)

        rung_hint = hint
        if harden is not False:
            # Feasibility first: does this rung solve at all?
            probe = solver.Scheduler(reqs, time_limit=budget * 0.4,
                                     workers=args.workers, relax=relax,
                                     harden=harden, hint=rung_hint,
                                     optimise=False, anchor=anchor,
                                     fixed_fifth=fifth,
                                     anchor_weight=args.anchor_weight)
            pr = probe.solve(log=args.log)
            spent += pr.wall_time
            print(f"    היתכנות: {pr.status}  ({pr.wall_time:.1f} שניות)",
                  flush=True)
            if pr.timetable is None:
                print("    אין פתרון ברמה הזו — יורדים דרגה כדי לדעת "
                      "איזה כלל אשם.", flush=True)
                continue
            rung_hint = pr.timetable.placement
            budget = max(30.0, budget * 0.6)

        sch = solver.Scheduler(reqs, time_limit=budget, workers=args.workers,
                               relax=relax, harden=harden, hint=rung_hint,
                               anchor=anchor, fixed_fifth=fifth,
                               anchor_weight=args.anchor_weight)
        attempt = sch.solve(log=args.log)
        spent += attempt.wall_time
        print(f"    סטטוס: {attempt.status}   זמן: {attempt.wall_time:.1f} שניות",
              flush=True)
        if attempt.timetable is not None:
            result = attempt
            break
        print("    אין פתרון ברמה הזו — יורדים דרגה כדי לדעת איזה כלל אשם.",
              flush=True)

    if result is None:
        print("  לא נמצא פתרון באף רמה.")
        return 2
    if result.timetable is None:
        print("  לא נמצא פתרון.")
        return 2
    # The solver is indifferent to which of a teacher's own lessons opens the
    # day; the school is not.  Exchanging two of her single lessons inside one
    # day is invisible to every constraint, so it happens here rather than as
    # another thing for CP-SAT to search over.  `validate` still sees the
    # result, so the exchange cannot smuggle a violation in.
    opened = polish.open_with_torah(result.timetable)
    swapped = sum(1 for mid, slot in result.timetable.placement.items()
                  if opened[mid] != slot)
    if swapped:
        result.timetable = Timetable(opened, result.timetable.meetings)
        print(f"  {swapped} שיעורים הוחלפו כדי לפתוח את היום ב"
              f"{school.OPENING_SUBJECT}")

    print(f"  ציון עונשין: {result.objective} (חסם תחתון {result.best_bound})")
    for k, v in sorted(result.penalties.items(), key=lambda kv: -kv[1]):
        print(f"     {v:6}  {k}")
    if not result.penalties:
        print("     כל האילוצים הרכים מסופקים במלואם.")

    print("\n── סיכום כללי בית הספר ──")
    print(report.summary(result.timetable))

    print("\n── שלב 2: אימות בלתי תלוי ──")
    ok, problems = validate.validate(result.timetable)
    if ok:
        print("  ✓ כל האילוצים הקשיחים מתקיימים.")
    else:
        print(f"  ✗ {len(problems)} הפרות:")
        for p in problems:
            print(f"     {p}")

    notes = [f"סטטוס פתרון: {result.status}, ציון {result.objective}"]
    notes += [f"{k}: {v}" for k, v in sorted(result.penalties.items(),
                                             key=lambda kv: -kv[1])]
    out = pathlib.Path(args.out)
    out.write_text(report.html_page(result.timetable, 'מערכת שעות תשפ"ז', notes),
                   encoding="utf-8")
    sheet = pathlib.Path(args.sheet)
    sheet.write_text(report.sheet_page(result.timetable, 'מערכת שעות תשפ"ז'),
                     encoding="utf-8")
    tt = result.timetable
    word_path = _word(tt, args.word) if args.word else None
    pathlib.Path(args.save).write_text(json.dumps(
        {"placement": {str(k): list(v) for k, v in tt.placement.items()},
         # ...and again by lesson identity, so the file still means the same
         # thing after the subject table changes underneath it.
         "placed": [[m.klass, m.subject, list(m.teachers), m.length,
                     *tt.placement[mid]]
                    for mid, m in enumerate(tt.meetings) if mid in tt.placement],
         "notes": notes, "objective": result.objective, "status": result.status},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  נכתב: {out.resolve()}")
    print(f"  נכתב: {sheet.resolve()}")
    if word_path:
        print(f"  נכתב: {word_path}")
    print(f"  שיבוץ נשמר: {pathlib.Path(args.save).resolve()}"
          f"  (רינדור מחדש: --load {args.save})")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
