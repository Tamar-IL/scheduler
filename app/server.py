# -*- coding: utf-8 -*-
"""The web application: a JSON API and the files that draw it.

Built on `http.server`, on purpose.  The scheduler already asks for ortools
and, for the Word hand-out, python-docx; a school office should be able to
run this with those two and nothing else.  There is exactly one administrator
at a keyboard, so a threading HTTP server with a lock around the solver is
not a compromise — it is the shape of the problem.

Solving is the one slow thing, so it runs as a background job with a progress
line the browser polls.  Everything else — evaluating an edit, finding
alternatives for a lesson, re-listing the constraints — is milliseconds and
answers inside the request.
"""
from __future__ import annotations

import http.server
import io
import json
import mimetypes
import pathlib
import socketserver
import threading
import traceback
import urllib.parse
import uuid

from app import activate
from app import alternatives as alt
from app import constraints as cat
from app import engine
from app import importer
from app import samples
from app import spec as spec_mod
from app import store as store_mod
from app import timetable as tt_mod
from app import users as users_mod

WEB = pathlib.Path(__file__).resolve().parent / "web"


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------- multipart

def _parse_multipart(body: bytes, boundary: bytes) -> dict[str, list]:
    """Fields and files from a multipart body.

    Written out rather than imported: `cgi` is gone in 3.13 and `email` wants
    the whole message re-assembled with headers.  The format is simple enough
    that doing it directly is shorter than either, and it keeps uploaded
    bytes exactly as they arrived — which matters, because a CSV with a
    byte-order mark has to reach the importer with it.
    """
    out: dict[str, list] = {}
    # The delimiter is CRLF *plus* "--boundary": the two bytes before it
    # belong to the delimiter, not to the part.  Splitting on the boundary
    # alone and trimming afterwards would eat the final newline of every
    # uploaded file — invisible in a CSV, fatal in a zip.
    sep = b"\r\n--" + boundary
    for part in (b"\r\n" + body).split(sep):
        part = part.lstrip(b"\r\n")
        if not part or part.startswith(b"--"):
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        headers = {}
        for line in head.decode("utf-8", "replace").split("\r\n"):
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        disposition = headers.get("content-disposition", "")
        name = _disposition(disposition, "name")
        if name is None:
            continue
        filename = _disposition(disposition, "filename")
        if filename:
            out.setdefault(name, []).append((filename, data))
        else:
            out.setdefault(name, []).append(data.decode("utf-8", "replace"))
    return out


def _disposition(header: str, key: str) -> str | None:
    for chunk in header.split(";"):
        k, _, v = chunk.strip().partition("=")
        if k.strip().lower() == key:
            return v.strip().strip('"')
    return None


# -------------------------------------------------------------------- jobs

class Jobs:
    """Background solves, one at a time.

    CP-SAT is given every worker the machine has, and the engine's globals
    hold one school; running two solves at once would be slower *and* wrong.
    So the queue is a lock, and a second request is told the first is running
    rather than being silently interleaved with it.
    """

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._busy = threading.Lock()

    def start(self, label: str, work) -> str:
        if not self._busy.acquire(blocking=False):
            raise HttpError(409, "פתרון אחר כבר רץ. יש להמתין לסיומו.")
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {"id": job_id, "label": label,
                                  "state": "running", "progress": 0.0,
                                  "message": "מתחיל", "result": None,
                                  "error": ""}

        def report(message: str, done: float) -> None:
            with self._lock:
                self._jobs[job_id].update(message=message, progress=done)

        def run() -> None:
            try:
                result = work(report)
                with self._lock:
                    self._jobs[job_id].update(state="done", progress=1.0,
                                              message="הושלם", result=result)
            except Exception as exc:                        # noqa: BLE001
                traceback.print_exc()
                with self._lock:
                    self._jobs[job_id].update(state="failed",
                                              error=f"{exc}",
                                              message="נכשל")
            finally:
                self._busy.release()

        threading.Thread(target=run, daemon=True).start()
        return job_id

    @property
    def busy(self) -> bool:
        """A solve is running.  Restarting now would lose it silently."""
        return self._busy.locked()

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise HttpError(404, "אין משימה כזו")
            return dict(job)


# ------------------------------------------------------------------- views

class Api:
    """The application, for one signed-in user.

    A fresh instance per request, because the store it reads is that user's
    own directory. `Jobs` is deliberately *not* per user: a solve takes every
    core the machine has, so the queue has to be shared by everyone.
    """

    def __init__(self, store: store_mod.Store, jobs: "Jobs",
                 user=None, users=None):
        self.store = store
        self.jobs = jobs
        self.user = user
        self.users = users

    # -- helpers ---------------------------------------------------------

    def project(self, project_id: str) -> store_mod.Project:
        if not self.store.exists(project_id):
            raise HttpError(404, "בית הספר לא נמצא")
        return self.store.load(project_id)

    def _doc(self, project: store_mod.Project) -> tt_mod.Document:
        if project.doc is None:
            raise HttpError(400, "עדיין לא נבנתה מערכת שעות לבית ספר זה")
        return project.doc

    def _state(self, project: store_mod.Project,
               review: bool = True) -> dict:
        out = {"school": project.summary(),
               "grid": {"days": project.spec.grid.days,
                        "periods": project.spec.grid.periods_per_day},
               "teachers": [t.name for t in project.spec.teachers],
               "classes": [c.name for c in project.spec.classes],
               "subjects": project.spec.subjects,
               "homerooms": {t.homeroom: t.name for t in project.spec.teachers
                             if t.homeroom},
               "report": project.report}
        if project.doc is not None:
            out["rows"] = [r.to_dict() for r in project.doc.rows]
            out["approved"] = bool(project.doc.approved)
            if review:
                with activate.activated(project.spec):
                    out["review"] = tt_mod.review(project.spec,
                                                  project.settings,
                                                  project.doc)
        return out

    # -- routes ----------------------------------------------------------

    def get_schools(self, _payload) -> dict:
        return {"schools": self.store.list()}

    def post_import(self, payload) -> dict:
        files = payload.get("files") or []
        if not files:
            raise HttpError(400, "לא נבחרו קבצים")
        name = (payload.get("name") or [""])[0] if isinstance(
            payload.get("name"), list) else payload.get("name") or ""
        year = (payload.get("year") or [""])[0] if isinstance(
            payload.get("year"), list) else payload.get("year") or ""
        spec, report = importer.read_files(files, name=name, year=year)
        if spec is None:
            return {"ok": False, "report": report.to_dict()}
        project = self.store.create(spec)
        if report.restored:
            # Re-importing this program's own backup brings the approved
            # timetable and the rule settings back with the school, so a
            # restore is a restore rather than a fresh start.
            if report.restored.get("timetable"):
                project.doc = tt_mod.Document.from_dict(
                    report.restored["timetable"])
            if report.restored.get("settings"):
                project.settings = cat.from_dict(report.restored["settings"])
        for filename, data in files:
            self.store.keep_upload(project.id, filename, data)
        pre = engine.preflight(spec)
        project.report = {**pre.to_dict(), "status": "לא נפתר עדיין"}
        self.store.save(project)
        return {"ok": True, "id": project.id, "report": report.to_dict(),
                "checks": pre.to_dict()}

    def post_demo(self, _payload) -> dict:
        spec, report = samples.demo_spec()
        if spec is None:                                    # pragma: no cover
            raise HttpError(500, "; ".join(report.errors))
        project = self.store.create(spec)
        pre = engine.preflight(spec)
        project.report = {**pre.to_dict(), "status": "לא נפתר עדיין"}
        self.store.save(project)
        return {"ok": True, "id": project.id, "checks": pre.to_dict()}

    def get_school(self, payload) -> dict:
        return self._state(self.project(payload["id"]))

    def post_delete(self, payload) -> dict:
        self.store.delete(payload["id"])
        return {"ok": True}

    def get_checks(self, payload) -> dict:
        project = self.project(payload["id"])
        return engine.preflight(project.spec).to_dict()

    def get_constraints(self, payload) -> dict:
        project = self.project(payload["id"])
        return {"groups": cat.listing(project.spec, project.settings),
                "priorities": [{"weight": w, "name": n}
                               for w, n in cat.PRIORITIES]}

    def post_constraints(self, payload) -> dict:
        project = self.project(payload["id"])
        try:
            if payload.get("enabled") is not None \
                    or payload.get("weight") is not None:
                project.settings.set(payload["constraint"],
                                     enabled=payload.get("enabled"),
                                     weight=payload.get("weight"))
            if payload.get("params"):
                # Edited onto a copy: `apply_fields` reports what the change
                # broke, and a school that fails its own consistency check
                # must not be the one that gets saved.
                draft = project.spec.copy()
                problems = cat.apply_fields(draft, payload["constraint"],
                                            payload["params"])
                if problems:
                    raise HttpError(400, " · ".join(problems[:4]))
                project.spec = draft
        except (KeyError, ValueError) as exc:
            raise HttpError(400, str(exc)) from exc
        self.store.save(project)
        out = {"groups": cat.listing(project.spec, project.settings)}
        if project.doc is not None:
            with activate.activated(project.spec):
                out["review"] = tt_mod.review(project.spec, project.settings,
                                              project.doc)
        return out

    def post_generate(self, payload) -> dict:
        project = self.project(payload["id"])
        keep = bool(payload.get("anchor")) and project.doc is not None
        anchor = project.doc if keep else None
        locked = ([r for r in project.doc.rows if r.locked]
                  if project.doc else [])
        limit = float(payload.get("time_limit") or 120)
        workers = int(payload.get("workers") or 8)
        harden = list(payload.get("harden") or [])

        def work(report):
            doc, solve_report = engine.generate(
                project.spec, project.settings, time_limit=limit,
                workers=workers, anchor=anchor, locked=locked,
                harden=harden, progress=report)
            fresh = self.store.load(project.id)
            self.store.snapshot(fresh, "לפני בניית מערכת חדשה")
            if doc is not None:
                fresh.doc = doc
            fresh.report = solve_report.to_dict()
            fresh.report["status"] = solve_report.status
            self.store.save(fresh)
            return {"solved": doc is not None,
                    "report": solve_report.to_dict()}

        return {"job": self.jobs.start("בניית מערכת שעות", work)}

    def get_job(self, payload) -> dict:
        return self.jobs.get(payload["id"])

    def post_edit(self, payload) -> dict:
        project = self.project(payload["id"])
        doc = self._doc(project)
        before = doc.copy()
        op = payload.get("op")
        try:
            self._apply(project, doc, op, payload)
        except KeyError as exc:
            raise HttpError(400, str(exc)) from exc
        with activate.activated(project.spec):
            outcome = alt.check_edit(project.spec, project.settings,
                                     before, doc)
        self.store.snapshot(self.store.load(project.id), f"לפני {op}")
        self.store.save(project)
        return outcome

    def _apply(self, project, doc: tt_mod.Document, op: str,
               payload: dict) -> None:
        if op == "move":
            doc.move(int(payload["uid"]), _opt_int(payload.get("day")),
                     _opt_int(payload.get("period")))
        elif op == "swap":
            doc.swap(int(payload["uid"]), int(payload["other"]))
        elif op == "exchange":
            # Taking an option from the alternatives list, whatever shape it
            # turned out to have: a plain move, a swap, or a double trading
            # places with two singles.
            doc.exchange(int(payload["uid"]), int(payload["day"]),
                         int(payload["period"]),
                         [int(u) for u in payload.get("partner") or []])
        elif op == "unplace":
            doc.unplace(int(payload["uid"]))
        elif op == "lock":
            doc.set_locked(int(payload["uid"]), bool(payload.get("locked")))
        elif op == "retitle":
            doc.retitle(int(payload["uid"]), klass=payload.get("klass"),
                        subject=payload.get("subject"),
                        teachers=payload.get("teachers"))
        elif op == "add":
            doc.add(payload.get("klass") or None, payload["subject"],
                    list(payload.get("teachers") or []),
                    int(payload.get("length") or 1),
                    _opt_int(payload.get("day")),
                    _opt_int(payload.get("period")))
        elif op == "delete":
            doc.remove(int(payload["uid"]))
        elif op == "approve":
            doc.mark_approved()
        else:
            raise HttpError(400, f"פעולה לא מוכרת: {op!r}")

    def post_alternatives(self, payload) -> dict:
        project = self.project(payload["id"])
        doc = self._doc(project)
        with activate.activated(project.spec):
            return alt.find(project.spec, project.settings, doc,
                            int(payload["uid"]))

    def post_rescore(self, payload) -> dict:
        """The solver's own verdict, for the moment before approval."""
        project = self.project(payload["id"])
        doc = self._doc(project)
        paid = engine.rescore(project.spec, project.settings, doc,
                              time_limit=float(payload.get("time_limit") or 45))
        return {"ok": paid is not None,
                "penalties": paid or {},
                "total": sum((paid or {}).values())}

    def get_versions(self, payload) -> dict:
        return {"versions": self.store.versions(payload["id"])}

    def post_restore(self, payload) -> dict:
        project = self.project(payload["id"])
        self.store.restore(project, payload["version"])
        return self._state(project)

    def post_undo(self, payload) -> dict:
        project = self.project(payload["id"])
        try:
            self.store.undo(project)
        except KeyError as exc:
            raise HttpError(400, str(exc)) from exc
        return self._state(project)


def _opt_int(value):
    return None if value in (None, "", "null") else int(value)


def _template_bundle() -> list[tuple[str, bytes]]:
    """The example files to hand out: the two CSVs, and the same content as
    one Word document where python-docx is there to write it."""
    files = list(samples.template_files())
    word = samples.template_docx()
    if word is not None:
        files.append((samples.WORD_FILE, word))
    return files


# ----------------------------------------------------------------- exports

def export(project: store_mod.Project, kind: str,
           view: str = "both") -> tuple[str, str, bytes]:
    """(filename, content type, bytes) for one download."""
    import tempfile

    import report as report_mod

    title = f"{project.spec.name} — מערכת שעות {project.spec.year}".strip(" —")
    if kind in ("template", "source"):
        files = (_template_bundle() if kind == "template"
                 else samples.spec_to_csv(project.spec))
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in files:
                zf.writestr(name, data)
        return (f"{kind}.zip", "application/zip", buf.getvalue())

    if project.doc is None:
        raise HttpError(400, "אין מערכת שעות לייצוא")

    with activate.activated(project.spec):
        tt = project.doc.as_timetable()
        if kind == "html":
            return (f"{project.spec.name}.html", "text/html; charset=utf-8",
                    report_mod.sheet_page(tt, title).encode("utf-8"))
        if kind == "json":
            payload = {"school": spec_mod.to_dict(project.spec),
                       "timetable": project.doc.to_dict(),
                       "settings": project.settings.to_dict(),
                       "report": project.report}
            return (f"{project.spec.name}.json", "application/json",
                    json.dumps(payload, ensure_ascii=False,
                               indent=1).encode("utf-8"))
        if kind in ("docx", "docx-final"):
            try:
                import word
            except ImportError as exc:
                raise HttpError(
                    503, "ייצוא ל-Word דורש את החבילה python-docx. "
                         "התקנה: pip install python-docx") from exc
            locked = kind == "docx-final"
            suffix = "סופי" if locked else "לעריכה"
            path = pathlib.Path(tempfile.mkdtemp()) / "sheet.docx"
            word.sheet_docx(tt, title, path, locked=locked, view=view)
            data = path.read_bytes()
            return (f"{project.spec.name} — {suffix}.docx",
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document", data)
    raise HttpError(400, f"סוג ייצוא לא מוכר: {kind}")


# ------------------------------------------------------------------ server

#: Sent by the browser on every state-changing request. A cross-site form
#: cannot add a header without a CORS preflight the server never answers, so
#: requiring one is a complete defence against a form on another page
#: submitting to this one — including the multipart upload, which a
#: same-site-strict cookie alone does not cover on a first navigation.
GUARD_HEADER = "X-Timetable"

COOKIE = "tt_session"


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Timetable/1.0"
    #: Set by `serve`.
    workspaces: "Workspaces" = None

    def log_message(self, fmt, *args):    # quieter than the default
        if not self.path.startswith("/api/jobs/"):
            print(f"  {self.command} {self.path} — {fmt % args}")

    # -- who is asking ---------------------------------------------------

    def _cookie(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        for chunk in raw.split(";"):
            name, _, value = chunk.strip().partition("=")
            if name == COOKIE:
                return value
        return None

    def _set_cookie(self, token: str | None) -> None:
        """Sessions live in an HttpOnly cookie the page cannot read.

        `Secure` only behind a TLS proxy, which says so in
        `X-Forwarded-Proto`: on plain http://127.0.0.1 the flag would stop
        the cookie being set at all.  A client forging the header can only
        make its own cookie stricter, so trusting it costs nothing.
        `SameSite=Strict` and the guard header carry the weight either way.
        """
        secure = "; Secure" if self._behind_tls() else ""
        if token:
            self._cookies.append(
                f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
                f"Max-Age={users_mod.SESSION_DAYS * 86400}{secure}")
        else:
            self._cookies.append(f"{COOKIE}=; Path=/; HttpOnly; "
                                 f"SameSite=Strict; Max-Age=0{secure}")

    def _behind_tls(self) -> bool:
        proto = self.headers.get("X-Forwarded-Proto", "")
        return proto.split(",")[0].strip().lower() == "https"

    def api_for(self, user) -> Api:
        return self.workspaces.api(user)

    # -- plumbing --------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str,
              filename: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for cookie in getattr(self, "_cookies", ()):
            self.send_header("Set-Cookie", cookie)
        if filename:
            quoted = urllib.parse.quote(filename)
            self.send_header("Content-Disposition",
                             f"attachment; filename*=UTF-8''{quoted}")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False)
                   .encode("utf-8"), "application/json; charset=utf-8")

    def _read_body(self) -> tuple[dict, list]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            boundary = _disposition(ctype, "boundary")
            parts = _parse_multipart(raw, boundary.encode())
            payload: dict = {}
            files: list = []
            for name, values in parts.items():
                if name == "files":
                    files = [v for v in values if isinstance(v, tuple)]
                else:
                    payload[name] = values[0]
            return payload, files
        if raw:
            return json.loads(raw.decode("utf-8")), []
        return {}, []

    # -- routing ---------------------------------------------------------

    def do_GET(self):                                        # noqa: N802
        self._cookies = []
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in
                 urllib.parse.parse_qs(parsed.query).items()}
        try:
            if path.startswith("/api/"):
                return self._json(200, self._route_get(path, query))
            return self._static(path)
        except HttpError as exc:
            return self._json(exc.status, {"error": exc.message})
        except Exception as exc:                             # noqa: BLE001
            traceback.print_exc()
            return self._json(500, {"error": f"שגיאה בשרת: {exc}"})

    def do_POST(self):                                       # noqa: N802
        self._cookies = []
        parsed = urllib.parse.urlparse(self.path)
        try:
            # Anything that changes state must carry the guard header, which
            # only same-origin script can set: a form on another site cannot
            # add one without a CORS preflight this server never answers.
            if self.headers.get(GUARD_HEADER) is None:
                raise HttpError(403, "בקשה ללא סימון מקור. יש לרענן את הדף.")
            payload, files = self._read_body()
            if files:
                payload["files"] = files
            return self._json(200, self._route_post(parsed.path, payload))
        except HttpError as exc:
            return self._json(exc.status, {"error": exc.message})
        except Exception as exc:                             # noqa: BLE001
            traceback.print_exc()
            return self._json(500, {"error": f"שגיאה בשרת: {exc}"})

    do_DELETE = do_POST

    # -- authentication --------------------------------------------------

    def _user(self):
        """The signed-in user, or None. Never raises."""
        return self.workspaces.users.session_user(self._cookie())

    def _require_user(self):
        if not self.workspaces.auth:
            return None
        user = self._user()
        if user is None:
            raise HttpError(401, "נדרשת התחברות")
        return user

    def _auth_get(self, parts: list[str]):
        """The one endpoint the page may call before signing in."""
        if parts == ["api", "auth", "me"]:
            ws = self.workspaces
            user = self._user()
            return {"auth": ws.auth,
                    "first_run": ws.auth and ws.users.empty,
                    "user": user.public() if user else None}
        return None

    def _auth_post(self, parts: list[str], payload: dict):
        ws = self.workspaces
        if parts == ["api", "auth", "register"]:
            if not ws.auth:
                raise HttpError(400, "השרת פועל ללא חשבונות")
            try:
                user = ws.users.register(payload.get("username", ""),
                                         payload.get("password", ""),
                                         payload.get("display", ""))
            except ValueError as exc:
                raise HttpError(400, str(exc)) from exc
            self._set_cookie(ws.users.start_session(user))
            return {"user": user.public()}
        if parts == ["api", "auth", "login"]:
            if not ws.auth:
                raise HttpError(400, "השרת פועל ללא חשבונות")
            try:
                user = ws.users.authenticate(payload.get("username", ""),
                                             payload.get("password", ""))
            except ValueError as exc:
                raise HttpError(401, str(exc)) from exc
            self._set_cookie(ws.users.start_session(user))
            return {"user": user.public()}
        if parts == ["api", "auth", "logout"]:
            ws.users.end_session(self._cookie())
            self._set_cookie(None)
            return {"ok": True}
        if parts == ["api", "auth", "password"]:
            user = self._require_user()
            if user is None:
                raise HttpError(400, "השרת פועל ללא חשבונות")
            try:
                ws.users.change_password(user, payload.get("current", ""),
                                         payload.get("new", ""))
            except ValueError as exc:
                raise HttpError(400, str(exc)) from exc
            # Changing a password ends every other session, including this
            # one, so the browser that did it is handed a fresh cookie.
            self._set_cookie(ws.users.start_session(user))
            return {"ok": True}
        return None

    # -- routing ---------------------------------------------------------

    def _route_get(self, path: str, query: dict):
        parts = [p for p in path.split("/") if p]      # api, …
        if parts == ["api", "health"]:
            # Unauthenticated on purpose: the auto-updater asks it before
            # restarting.  It says only whether *some* solve is running.
            return {"busy": self.workspaces.jobs.busy}
        answered = self._auth_get(parts)
        if answered is not None:
            return answered
        api = self.api_for(self._require_user())
        if parts == ["api", "schools"]:
            return api.get_schools(query)
        if parts[:2] == ["api", "jobs"] and len(parts) == 3:
            return api.get_job({"id": parts[2]})
        if parts[:2] == ["api", "help"]:
            return {"columns": [{"section": s, "text": t}
                                for s, t in samples.COLUMN_HELP],
                    "files": [name for name, _ in _template_bundle()]}
        if parts[:2] == ["api", "school"] and len(parts) >= 3:
            project_id = urllib.parse.unquote(parts[2])
            rest = parts[3:]
            if not rest:
                return api.get_school({"id": project_id})
            if rest == ["constraints"]:
                return api.get_constraints({"id": project_id})
            if rest == ["checks"]:
                return api.get_checks({"id": project_id})
            if rest == ["versions"]:
                return api.get_versions({"id": project_id})
        raise HttpError(404, f"אין נתיב כזה: {path}")

    def _route_post(self, path: str, payload: dict):
        parts = [p for p in path.split("/") if p]
        answered = self._auth_post(parts, payload)
        if answered is not None:
            return answered
        api = self.api_for(self._require_user())
        if parts == ["api", "import"]:
            return api.post_import(payload)
        if parts == ["api", "demo"]:
            return api.post_demo(payload)
        if parts[:2] == ["api", "school"] and len(parts) >= 3:
            project_id = urllib.parse.unquote(parts[2])
            payload = dict(payload, id=project_id)
            rest = parts[3:]
            table = {
                (): api.post_delete if self.command == "DELETE"
                    else api.get_school,
                ("constraints",): api.post_constraints,
                ("generate",): api.post_generate,
                ("edit",): api.post_edit,
                ("alternatives",): api.post_alternatives,
                ("rescore",): api.post_rescore,
                ("restore",): api.post_restore,
                ("undo",): api.post_undo,
            }
            handler = table.get(tuple(rest))
            if handler is not None:
                return handler(payload)
        raise HttpError(404, f"אין נתיב כזה: {path}")

    # -- files and downloads ---------------------------------------------

    def _static(self, path: str):
        if path.startswith("/download/"):
            return self._download(path)
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB / name).resolve()
        if not str(target).startswith(str(WEB.resolve())) \
                or not target.is_file():
            raise HttpError(404, "לא נמצא")
        ctype = mimetypes.guess_type(target.name)[0] or "text/plain"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        return self._send(200, target.read_bytes(), ctype)

    def _download(self, path: str):
        parsed = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in
                 urllib.parse.parse_qs(parsed.query).items()}
        parts = [p for p in parsed.path.split("/") if p]     # download, …
        if len(parts) == 2 and parts[1] == "template":
            import zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, data in _template_bundle():
                    zf.writestr(name, data)
            return self._send(200, buf.getvalue(), "application/zip",
                              "קבצי-דוגמה.zip")
        if len(parts) == 4 and parts[1] == "school":
            api = self.api_for(self._require_user())
            project = api.project(urllib.parse.unquote(parts[2]))
            name, ctype, data = export(project, parts[3],
                                       query.get("view", "both"))
            return self._send(200, data, ctype, name)
        raise HttpError(404, "לא נמצא")


class Workspaces:
    """One store per account — or one store for everybody, without accounts.

    `--no-auth` is not a way around the login screen: it is the single-user
    case, stated honestly. A machine in one person's own office does not need
    an account to protect a directory only they can reach, and demanding one
    would be ceremony. Nothing else about the program changes, so a school
    that later shares the machine turns accounts on and moves its `schools`
    directory into the new workspace.
    """

    def __init__(self, data: str, auth: bool = True):
        self.root = pathlib.Path(data)
        self.auth = auth
        self.users = users_mod.Users(self.root)
        # Shared by every account on purpose: a solve wants every core the
        # machine has, so two of them must queue rather than compete.
        self.jobs = Jobs()

    def api(self, user) -> Api:
        root = (self.users.workspace(user) if user is not None
                else self.root / "schools")
        return Api(store_mod.Store(root), self.jobs, user, self.users)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(host: str = "127.0.0.1", port: int = 8000, data: str = "data",
          auth: bool = True) -> None:
    Handler.workspaces = Workspaces(data, auth)
    with Server((host, port), Handler) as httpd:
        print(f"מערכת שעות — http://{host}:{port}")
        print(f"  נתונים: {pathlib.Path(data).resolve()}")
        print("  חשבונות: " + ("פעילים" if auth else "כבויים (--no-auth)"))
        print("  לעצירה: Ctrl+C")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nנעצר.")
