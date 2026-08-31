# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two layers.

**The engine** (`model`, `checks`, `solver`, `validate`, `report`, `polish`, `word`,
`main`) is a feasibility prover for one specific school's weekly timetable (תשפ"ז):
17 teachers, 10 classes, 187 requirements, 304 meetings, hard-coded in `school.py`.
Everything below the heading "Architecture" is about it and still holds.

**The platform** (`app/`, `serve.py`) is the generic product built on top: upload
two files → adjust constraints → generate → review conflicts → edit by hand or take
a suggested alternative → validate → export to Word. It serves any school, and
`school.py` is now one profile among them.

All user-facing strings, penalty labels, and reports are in Hebrew; code and
comments are in English. Files are UTF-8; on Windows set `PYTHONIOENCODING=utf-8`
before running anything that prints.

## Commands

```bash
pip install ortools           # solving
pip install python-docx       # only for the Word hand-out

python serve.py --open                 # the platform, http://127.0.0.1:8000
python serve.py --no-auth              # one shared directory, no accounts
python serve.py --seed-builtin --seed-for USER   # register school.py as a
                                       # project in that account, with
                                       # solution.json loaded as its anchor
python -m unittest discover -s tests -t .
```

### The engine, directly

```bash
python main.py                          # checks + solve + validate + HTML + solution.json
python main.py --priced                 # skip the hardening ladder, price everything (diagnosis)
python main.py --checks-only            # phase 0 only, milliseconds; exit 1 if any ERROR
python main.py --time-limit 1500 --workers 12   # a real run; 90s is not enough
python main.py --load solution.json     # re-render a saved placement, no solving
python main.py --relax gaps             # drop a hard rule to diagnose infeasibility
python main.py --log                    # CP-SAT search log

# answering a new request without losing the timetable the school approved
python main.py --anchor solution.json --time-limit 1800 --workers 12

# ...and what it actually takes on this instance: pin the fifth days the
# approved timetable already settled, and say which new rules are not
# negotiable.  Without both, the anchored rung times out and the ladder
# falls through to all-priced, which regresses rules the school already has.
python main.py --anchor solution.json --pin-fifth     --harden "מחנכת: התחלה מאוחרת בצמוד ליום חופש"     --time-limit 2400 --workers 12
```

`main.py` writes three files: `--out timetable.html`, the diagnostic one (notes,
the §11.2 audit, window counts per teacher), and `--sheet "כולל מורות.html"`,
the hand-out — the ten class grids followed by the seventeen teacher grids and
nothing else. The school reads the sheet; the other page explains what the
solver had to trade away.  `--word "כולל מורות.docx"` writes the sheet a third
time as a Word document — same content, editable — and `--word ""` skips it.
It is the only output with a dependency the solver does not need, so a missing
`python-docx` prints a line and costs nothing else.

```bash
python -m unittest discover -s tests -t .
python -m unittest tests.test_scheduler.TestChecks.test_real_data_has_no_hard_errors -v
```

`--relax` keys (comma-separated): `contiguity`, `class_span`, `subject_per_day`,
`day_off`, `working_days`, `distinct_off`, `max_per_day`, `gaps`, `long_days`,
`same_class_5`, `fifth_day`, `no_window`, `ends_day`, `class_periods`, `pinned`,
`english_days`, `late_days`, `window_day`.

Exit codes: 0 ok · 1 phase-0 errors (`--checks-only`) · 2 no solution · 3 solved but
the independent validator found hard-constraint violations.

## The platform (`app/`)

### It swaps the engine's globals rather than rewriting the engine

`solver.py` and `validate.py` read `school.TEACHERS`, `model.DAYS` and about thirty
more names as module globals. `app/activate.activated(spec)` installs a `SchoolSpec`
into them for the duration of a call and restores it afterwards — exactly what
`tests/test_scheduler.tiny_school` has always done, generalised. Consequences worth
knowing:

- **One school at a time per process.** `activated()` takes a re-entrant lock, and
  the server runs one solve at a time. That is not a compromise: CP-SAT wants the
  cores and there is one administrator at the keyboard.
- **`DAYS` and `PERIODS_PER_DAY` are rewritten in place** (`model.configure_grid`),
  because `from model import DAYS` binds the list. The *scalars* — `LONG_DAY_HOURS`,
  `LATE_PERIOD` — are bound by value, so the activator re-sets them in every module
  that imported them.
- **`activate._SCHOOL_ATTRS` must list every `school.py` constant.** One that is
  missing leaks from one school into the next, silently.
  `TestActivation.test_every_school_constant_is_installed_and_restored` diffs the
  list against the module; add a constant there when you add one to `school.py`.
- **`school.requirements` is swapped too.** It reads the module's own `_TABLE`,
  which does not describe the installed school; the platform passes
  `spec.requirements()` everywhere, and the swap stops an accidental call from
  quietly answering about a different school.

### Weekday names became arithmetic

`solver.py` and `checks.py` used to say `d == FRI` to mean "the shortened day".
They now say `model.full_days()` — days that run to the week's longest period count
— which is exactly Friday for this school and re-derives itself for a school whose
short day is a different one, or which has none. Do not put a day index back.

### The constraint catalogue is a description, not a second implementation

`app/constraints.py` lists every rule with its category, Hebrew explanation, and the
lever the engine already exposes: a `relax_key` for a hard rule, a penalty `label`
for a priced one. Switching a rule off produces the relax key; changing its priority
produces a weight override that `Scheduler(weights=...)` applies inside `_penalise`.
A rule with no lever is `locked=True` and the screen says so. Adding a rule to the
engine means adding an entry here — `TestConstraintScreen` checks that every soft
entry names labels the model really prices.

### `app/evaluate.py` is a second implementation, and is pinned to the first

Editing needs a priced timetable forty times in a row — every candidate slot in
"find alternatives" is a whole week that has to be scored — and `solver.score()`
takes seconds. So the priced rules are re-derived in plain Python, in milliseconds.
That duplication is only tolerable because `TestEvaluatorAgreesWithTheSolver` pins
it to `solver.score()` on the school's own approved timetable, label by label. The
solver is the authority: where they could differ, change `evaluate.py`.

The one accepted difference: which day carries each §11.2 component. The solver
picks the fifth day to minimise, ties and all, so the four `יום חמישי:` labels are
compared as one sum.

Hard rules are *not* re-implemented. `validate.violations()` returns
`(constraint id, Hebrew sentence)` pairs and is what the editing screens call, so a
hard rule has exactly one checker in the system.

### Accounts

`app/users.py` is PBKDF2-SHA256 with a per-user salt, a random session token
in an HttpOnly SameSite=Strict cookie, and one workspace directory per
account. `server.Workspaces` builds a `Store` rooted at that directory, so a
`Store` never sees more than one account's schools — the isolation is a path,
not a filter, which is the version that cannot be forgotten at a call site.

Three things that are load-bearing rather than decorative:

- **`GUARD_HEADER` on every POST/DELETE.** SameSite=Strict does not cover a
  multipart form posted from another page; a required header does, because a
  cross-origin form cannot set one and the server answers no CORS preflight.
- **A wrong username and a wrong password return the same sentence**, and the
  unknown-user path still spends a PBKDF2 round, so neither the message nor
  the timing says who has an account.
- **`Jobs` is shared across accounts, deliberately.** A solve wants every
  core; two must queue rather than compete.

There is no password reset and no email, on purpose — see the README for why.

### Rows, not meeting numbers

Meetings are numbered by position in the subject table, so deleting a lesson
renumbers everything after it. `app/timetable.Document` therefore holds **rows**
with ids of their own; `meetings()` regenerates the numbering on every call and
nothing stores it. Any placement crossing that boundary — an anchor, a lock, a
re-score — goes through `model.align`, which matches on lesson identity.

### Editing is always a whole-week question

Nothing in `app/` asks "is this cell free". A candidate is turned into a trial
`Document`, reviewed, and priced against the current one; the delta is the change in
the objective the solver itself minimises. `Document.exchange(uid, day, period,
partners)` is the single "take this option" operation — a plain move with no
partners, a swap with one, a double trading places with two singles when the class's
day length has to be conserved.

## Architecture (the engine)

Three stages, each of which can reject the input on its own, in `main.py` order:

1. **`checks.py` — count before searching.** Every check is a *necessary* condition
   proved by arithmetic, so it costs milliseconds and returns an actionable Hebrew
   sentence instead of a hung solver. Failing one proves no timetable exists; passing
   them all proves nothing. `Finding(level, topic, message)` with levels
   `ERROR`/`TIGHT`/`INFO`. This is where the כיתה ב2-on-Tuesday problem and the
   §11.2(ד) contradiction were found.
2. **`solver.py` — CP-SAT.** Hard rules become constraints; rules the school called
   mandatory but which collide become *very expensive* soft constraints
   (`W_MANDATORY=1000`, `W_STRONG=100`, `W_PREFER=10`, `W_MINOR=2`).
   `Scheduler(harden=...)` promotes the mandatory tier back into real
   constraints — `True` for all of `solver.MANDATORY_LABELS`, or a subset of
   labels. `main.py` walks a **hardening ladder** from all-hard down to
   all-priced and keeps the strongest rung that solves, passing the previous
   rung's placement in as a CP-SAT hint. Pricing is what makes a failure
   diagnosable, but once phase 0 shows a rule is satisfiable, pricing it only
   makes CP-SAT pay 1000 where it could have pruned — the ladder gets both.
   Whatever is still priced on the winning rung is exactly what the school has
   to decide about. Keep `MANDATORY_LABELS` in sync with the labels passed to
   `_penalise` at that weight; `TestHardeningLadder` enforces this. Four labels
   were renamed when the engine went generic, because they named this school's
   teacher, grade range, subject or weekday: `אורך יום חריג בכיתה` (was
   `…בכיתות א-ב`), `מורה: התחלה מאוחרת פעם בשבוע` (was `אלישבע: …`),
   `יום קצר אחד לכיתה` (was `כיתות ו–ח: …`), and the seventh-period rule, now
   `מחנכת בשעה האחרונה ביום {day}`. A saved `notes` list from before the rename
   still carries the old strings; nothing reads them back.
3. **`validate.py` — independent re-derivation.** Shares no line of code with
   `solver.py`; it re-derives every hard rule from the finished timetable. A bug in
   the CP-SAT translation cannot hide there. Never import solver logic into it.

`model.py` is the domain (time grid, `Teacher`, `SchoolClass`, `Requirement`,
`Meeting`, `Timetable`) and is deliberately free of solver concepts. `report.py`
renders terminal grids, the §11.2 audit, and a standalone RTL HTML page.
`word.py` renders the same hand-out as .docx and shares `report._cell`, so the
two cannot disagree about what is scheduled; it builds each table's XML in one
parse because driving ~1500 cells through the python-docx object model takes
90+ seconds against about one.

### The pricing principle — do not "fix" it into hard constraints

A rule the school called *mandatory* that turns out to conflict is priced, not
enforced. A timetable returned with penalty **0** on such a rule proves the hard
version is satisfiable; a bare `INFEASIBLE` proves nothing about *which* rule is at
fault. The objective breakdown printed by `main.py` is the diagnosis. Each
sub-requirement gets its own penalty label so the report names the teacher *and* the
component.

### The data flow that everything depends on

`school._TABLE` (`subject -> {class: "teacher:hours"}`, `|` = הקבצה/parallel teaching)
→ `school.requirements()` → `Requirement(hours, pattern)` → `model.expand()` →
`Meeting`s of length 1 or 2. `school._pattern()` decides the split (חשבון and upper-
grade אנגלית get doubles); `solver._subject_spread()` then forces one meeting of a
subject per class per day, which is what turns "6 hours over 4 days" into a real
constraint. Changing a pattern silently changes how many days a subject spreads over.

### Solving this instance — the ladder alone is not enough

`main.py`'s hardening ladder produces a far worse result than is achievable. What
works is **pin-and-iterate**: solve the core with §11.2 off, read each homeroom
teacher's non-opening day out of that solution, pin those days (`Scheduler(
fixed_fifth=...)`) and make her late start / late finish hard (`fifth_hard=...`),
then re-derive the pins from each improved solution and repeat. Choosing ten fifth
days simultaneously is the combinatorial core; pinning them turns §11.2 into a repair
problem. Progression on this instance was 7112 → 5122 → 4134 → 4112. Each round is
~7 minutes at 12 workers. Warm-start every round with the previous placement
(`hint=`), and use `optimise=False` to ask only "does this rung solve at all".

### Answering a new request: anchor, do not rebuild

The school reads a timetable, lives with it, and then asks for one more thing.
Re-solving from scratch answers the request and returns a timetable that
scores as well and looks nothing like the one they read — which reads as
having ignored everything else they said. `--anchor solution.json` prices
every meeting that moves off the approved placement (`W_STRONG` each by
default, `--anchor-weight` to change it) and seeds the search with it, so the
answer is a *repair*.

An anchor also names the ladder's first rung. `solver.score()` re-scores the
approved placement, and every mandatory rule it already keeps is hardened —
that set is one the anchor itself proves consistent, so the rung cannot forbid
what the school is already living with, and the two blind top rungs are not
worth the minutes. Rules the anchor already fails stay priced.

Two things the anchored ladder needs that are easy to get wrong:

- **`solver.NEVER_HARDEN`.** An anchor keeps `חלון שאינו ביום הארוך ביותר`
  effortlessly, so it lands in `kept` — and hardening it has no solution
  (§14.1, and re-confirmed here: UNKNOWN alone at 150s, while the rest of
  `kept` is OPTIMAL in 89s). Without subtracting it the anchored rung fails
  *every time*, silently, and the run drops through to all-priced.
- **`--harden`.** A rule the school has just asked for is one the anchor
  fails by definition, so it never reaches `kept` on its own — and priced at
  `W_MANDATORY` against an anchor charging `W_STRONG` a move, paying 1000 is
  cheaper than moving eleven lessons, so the solver simply buys its way out.
  Naming it on the command line is how "this one is not negotiable" gets
  said; the round-7 rules hardened on their own solve in 15s.

Score the anchor with `solver.TEACHER_REQUESTS` relaxed: those rules are why you are
re-solving, so a timetable built before they existed cannot meet them, and
pinning a placement the model forbids returns INFEASIBLE and an *empty*
breakdown — which silently reads as "nothing is violated" and hardens
everything.

### The two ב homerooms: Sunday opening, and the late start that must not touch the day off

`school.OPEN_ON_DAY` names Sunday for both זהבי ליוי (ב1) and חיה לוי (ב2) —
each opens her own class the first period of the week. `school.
LATE_START_AWAY_FROM_OFF` then says their one late-start day may not sit
*next to* a day off, on **either** side: the school's reason is the class,
not the teacher, and two consecutive mornings without her are the same
whichever order they come in.

The two rules together determine the answer by counting, and
`checks.late_start_days()` prints it in phase 0 before any search: drop
Friday (saturated), the days off, the Sunday reserved for the opening, and
both neighbours of the day off, and זהבי is left with **Monday** and חיה with
**Thursday** — one candidate each. Nothing is hard-coded to those days; change
a day off and the arithmetic re-derives.

Stated about the timetable, not about §11.2's `pick`: `pick` is only the day
the fifth-day rules are *measured* on and (א) is itself priced, so hanging the
rule on `pick` would let a late start reappear on a day the pick avoided.

### One class's ceiling on one subject

`school.CLASS_SUBJECT_PERIODS[("ו", "מדעים-טבע")] = (1, 5)` — hard, and
narrower than the subject-wide `SUBJECT_PERIODS`; both windows are
intersected, in `solver._placements` and in `validate` alike.
`school.SUBJECT_AT_DAY_START` asks for one of the two at period 1, priced at
`W_STRONG` and deliberately *below* §11.1: a class's first period belongs to
its homeroom teacher on four days of five, so anyone else can only have the
fifth, and at equal weight the two rules would bid each other up instead of
the breakdown saying which one gave way.

`class_periods` is in `solver.TEACHER_REQUESTS` for a reason worth
remembering: the one lesson it moves is taught by אפרת נתנאל, whose Wednesday
is otherwise full, so freeing that lesson — or even its whole class-day, which
is what H6 would demand — still leaves it nowhere to go, and the anchor
scoring solve returns INFEASIBLE however much of the placement is let go.

### Two rules that belong to one teacher

`school.NO_WINDOW_TEACHERS` and `school.SUBJECT_ENDS_DAY` are סימה זאבי's:
no window at all, and אומנות as the last lesson of her day. Both are hard,
and the asymmetry with the school-wide window rules is the point — a window
can be *forced* on a teacher (see below), so requiring one is not always
satisfiable, but a ceiling of zero always is: pack her day. She is also
dropped from the `WINDOW_MAJORITY` target, so wanting none does not read as
the school failing to give her one. `NOT_FIRST_PERIOD` is the weaker
school-wide version of the אומנות wish and still governs יוכי זר's lessons.

### `polish.py` — exchanges no constraint can see

Two single-period meetings with the same teacher, the same class and the same
day may swap slots without any rule noticing. `open_with_torah()` uses that to
give the opening period to תורה on the days a homeroom teacher opens her own
class, which is what the school wants §11.1 to look like. It runs after the
solve and before `validate`, so it cannot smuggle a violation in, and it is
not a constraint: where the class has no תורה that day, or the opening lesson
is pinned there for a reason (פ. שבוע on Friday, ספריה, אומנות), the day is
left exactly as the solver returned it.

### Structural facts to know before changing anything

- **H6, class contiguity** (`class_busy[p] >= class_busy[p+1]`) is the strongest rule
  in the model: a class's day is a solid block from period 1, no student gaps. It is
  what makes coverage problems infeasible rather than merely awkward.
- **Friday is exactly saturated**: 10 classes × 4 hours = 40 = the capacity of the ten
  homeroom teachers. Any change touching Friday breaks the system.
- **Windows are inverted from the obvious goal**: at most one per teacher per week is
  hard, but the school wants *most* teachers to have one, so a majority target is
  priced (`school.WINDOW_MAJORITY`). Some teachers structurally cannot have one.
- **`solver.py` and `validate.py` read `school.TEACHERS` / `school.CLASSES` as module
  globals.** Tests swap them via the `tiny_school` context manager in
  `tests/test_scheduler.py` and the platform swaps them via `app/activate.py`; any
  new module-level school constant must be added to **both** that manager's
  save/restore tuple and `activate._SCHOOL_ATTRS`, or it leaks between schools.
- Teachers with `off_choice` have no *certainly* off day — `checks.certainly_off()`
  returns the intersection over all resolutions, and `possible_off_sets()` enumerates
  them. Capacity arguments must take the best case over those, not one guess.

## Constraints and their source of truth

`CONSTRAINTS (1).md` is the authority on what every rule means, where it came from,
and whether it is hard or priced. Read it before touching `school.py`. Two standing
rules from it:

- Where the sources disagree, the **subject table wins**; staff-table totals are
  reported by `checks._teacher_totals()` and never enforced. Six such gaps are open
  (חנה +3, סימה זאבי +2, and four ×+1).
- §11 (round 4) is implemented: openings raised to 4 for all ten homerooms (§11.1),
  the fifth-day structure (§11.2), the ב1/ב2 **כתיב** swap (§11.4 — it replaced an
  earlier הבעה swap; both are load-neutral one-for-one exchanges that make ב2's
  Tuesday coverable, since זהבי works Tuesday and חיה does not).

**§11.2(ד), "the fifth day is her longest day", does not apply to every teacher —
this is a decision the school made, not a shortcut.** (ד) is a maximum disguised as a
superlative: it forces every other day down to the level of her most crippled day.
`checks.fifth_day_longest_impossible()` disproves it by counting for 7 of the 10
homeroom teachers — skipping period 1 and keeping a window caps that day at
`class_last - 2` hours, and (ד) applies that cap to her whole week, below her load.
The school was shown the arithmetic and ruled that lower-grade homeroom teachers
simply do not get a long day. So:

- **(א)(ב)(ג) are enforced for all ten homeroom teachers.**
- **(ד) is enforced only where the counting says it fits** — today מוריה נקי (ו),
  הדסה ויזמן (ז), רותי אסולין (ח), whose classes run to period 7.

The split is computed per teacher from her load and her class's day length, never
hard-coded to a grade list, so it re-derives itself if the data changes. Phase 0
prints the arithmetic for every exempted teacher on every run. Do not turn (ד) back
on for the other seven without changing the data it is derived from.

**§11.2(ג) is cancelled — do not re-enable it.** It put the window *on the fifth
day*, but the fifth day is by construction her **shortest** (it loses period 1 and
stops at the class's last period), so it produced windows in 3-hour days. The school
rejected that: a window exists so the teacher can rest, which only means something on
a long day. It is replaced by `school.WINDOW_ON_LONGEST_DAY` — **every window, for
every teacher, must fall on her longest day** — priced at `W_CRITICAL` (5000), above
the whole mandatory tier, so a real rest is never traded away to buy a lower rule.
`school.FIFTH_DAY["window"]` is `False` with the reasoning inline.

That rule is **priced, not hard, and the reason matters**: a window can be *forced*.
If a teacher's lessons cannot be packed contiguously on some day she gets a hole
whether anyone wants it or not, so "give her no window instead" is not always an
available move and the hard version has no solution. Verified empirically — hardening
it returns UNKNOWN even from a near-feasible warm start.

Because the window left the fifth day, the (ד) ceiling rose from `L-2` to `L-1` and
the exempt set shrank from seven teachers to three (א/ב homerooms only). That is the
derived split working as intended — nothing was hand-edited.'
