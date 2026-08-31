# -*- coding: utf-8 -*-
"""Run the timetable platform.

    python serve.py                    # http://127.0.0.1:8000
    python serve.py --port 9000 --data C:\\schools

On Windows, set PYTHONIOENCODING=utf-8 first — every message this program
prints is in Hebrew.
"""
from __future__ import annotations

import argparse
import sys

from app import server


def seed(data: str, solution: str | None, owner: str = "") -> str:
    """Register the school `school.py` describes as an ordinary project.

    The bridge from the work that already exists to the platform: the school
    the engine was written around becomes one profile among others, with the
    timetable it has already approved loaded as its anchor.  Nothing is
    special-cased for it afterwards.
    """
    import json
    import pathlib

    from model import Timetable, align, expand
    from app import activate, engine, store as store_mod
    from app import timetable as tt_mod

    from app import users as users_mod

    spec = activate.capture(name="בית הספר המובנה")
    # Where a school lives depends on whether the server runs with accounts:
    # each account owns a workspace, and without accounts everyone shares
    # one directory. Seeding has to land in the same place the server will
    # look, so it asks rather than assuming.
    root = pathlib.Path(data)
    users = users_mod.Users(root)
    if owner:
        user = next((u for u in users._users.values()
                     if u.username == users_mod.normalise(owner)), None)
        if user is None:
            raise SystemExit(f"אין חשבון בשם {owner!r}. "
                             f"יש להירשם באתר תחילה.")
        target, where = users.workspace(user), f"בחשבון {user.display}"
    elif users.empty:
        target, where = root / "schools", "בתיקייה המשותפת (--no-auth)"
    else:
        first = sorted(users._users.values(), key=lambda u: u.created)[0]
        target, where = users.workspace(first), f"בחשבון {first.display}"

    store = store_mod.Store(target)
    project = store.create(spec)
    lines = [f"נוצר: {project.id} — {where}"]

    path = pathlib.Path(solution) if solution else None
    if path and path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        with activate.activated(spec):
            meetings = expand(spec.requirements())
            rows = saved.get("placed")
            placement = (align(rows, meetings) if rows else
                         {int(k): tuple(v)
                          for k, v in saved["placement"].items()})
            project.doc = tt_mod.Document.from_timetable(
                Timetable(placement, meetings))
            project.doc.mark_approved()
        lines.append(f"נטענה מערכת מאושרת מתוך {path} "
                     f"({len(project.doc.approved)} שיעורים)")
    report = engine.preflight(spec)
    project.report = {**report.to_dict(), "status": "נטען"}
    store.save(project)
    lines.append(f"בדיקות שלב 0: {report.errors} שגיאות, "
                 f"{report.tight} נקודות מתוחות")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="פלטפורמת מערכות שעות")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data", default="data",
                    help="תיקיית הנתונים (בית ספר לתיקייה)")
    ap.add_argument("--no-auth", action="store_true",
                    help="ללא חשבונות: כל מי שמגיע לכתובת רואה את אותם "
                         "נתונים. למחשב אישי בלבד.")
    ap.add_argument("--open", action="store_true",
                    help="לפתוח את הדפדפן אוטומטית")
    ap.add_argument("--seed-builtin", metavar="SOLUTION", nargs="?",
                    const="solution.json", default=None,
                    help="לרשום את בית הספר המובנה שב-school.py כפרויקט, "
                         "ואם קיים קובץ שיבוץ — לטעון אותו כמערכת המאושרת")
    ap.add_argument("--seed-for", metavar="USERNAME", default="",
                    help="שם המשתמש שבחשבונו יירשם בית הספר המובנה")
    args = ap.parse_args(argv)

    if args.seed_builtin is not None:
        print(seed(args.data, args.seed_builtin, args.seed_for))

    if args.open:
        import threading
        import webbrowser
        threading.Timer(
            0.8, webbrowser.open,
            (f"http://{args.host}:{args.port}",)).start()
    server.serve(args.host, args.port, args.data, auth=not args.no_auth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
