# -*- coding: utf-8 -*-
"""Where a school's work is kept between sessions.

One directory per school, plain JSON inside it, and an undo history that is
just the last few timetables written to disk.  No database: a school is a few
hundred kilobytes, one administrator edits it at a time, and a file the user
can copy, mail or put in a backup is worth more here than a server they have
to run.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
import shutil
import threading
import uuid
from dataclasses import dataclass, field

from app import constraints as cat
from app import spec as spec_mod
from app import timetable as tt_mod

#: How many timetables back "undo" reaches.
HISTORY = 40

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat(" ")


def _slug(text: str) -> str:
    """A directory name a person can recognise in a file browser."""
    keep = re.sub(r"[^\w֐-׿-]+", "-", (text or "").strip())
    return keep.strip("-")[:40] or "school"


@dataclass
class Project:
    """One school: its definition, its rule settings and its timetable."""
    id: str
    spec: spec_mod.SchoolSpec
    settings: cat.Settings = field(default_factory=cat.Settings)
    doc: tt_mod.Document | None = None
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    #: The last solve's story — status, penalties, phase-0 findings.
    report: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.spec.name, "year": self.spec.year,
            "teachers": len(self.spec.teachers),
            "classes": len(self.spec.classes),
            "lessons": len(self.spec.lessons),
            "hours": sum(lesson.hours for lesson in self.spec.lessons),
            "has_timetable": bool(self.doc and any(r.placed
                                                   for r in self.doc.rows)),
            "created": self.created, "updated": self.updated,
            "status": self.report.get("status", ""),
        }


class Store:
    def __init__(self, root: str | pathlib.Path = "data"):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- locations

    def _dir(self, project_id: str) -> pathlib.Path:
        # Ids are generated here and never taken from a request path without
        # being looked up in the index first, but a stray separator would
        # still escape the store, so the name is checked rather than trusted.
        if "/" in project_id or "\\" in project_id or project_id in ("", ".",
                                                                     ".."):
            raise KeyError(f"מזהה בית ספר לא תקין: {project_id!r}")
        return self.root / project_id

    def exists(self, project_id: str) -> bool:
        return (self._dir(project_id) / "school.json").exists()

    # -------------------------------------------------------------- CRUD

    def list(self) -> list[dict]:
        out = []
        for path in sorted(self.root.iterdir()):
            if not (path / "school.json").exists():
                continue
            try:
                out.append(self.load(path.name).summary())
            except Exception as exc:                       # noqa: BLE001
                out.append({"id": path.name, "name": path.name,
                            "error": f"לא ניתן לקרוא: {exc}"})
        return sorted(out, key=lambda s: s.get("updated", ""), reverse=True)

    def create(self, spec: spec_mod.SchoolSpec,
               settings: cat.Settings | None = None) -> Project:
        with _LOCK:
            project_id = f"{_slug(spec.name)}-{uuid.uuid4().hex[:6]}"
            project = Project(id=project_id, spec=spec,
                              settings=settings or cat.Settings())
            self.save(project)
            return project

    def load(self, project_id: str) -> Project:
        base = self._dir(project_id)
        raw = json.loads((base / "school.json").read_text(encoding="utf-8"))
        doc = None
        if (base / "timetable.json").exists():
            doc = tt_mod.Document.from_dict(json.loads(
                (base / "timetable.json").read_text(encoding="utf-8")))
        return Project(
            id=project_id,
            spec=spec_mod.from_dict(raw["spec"]),
            settings=cat.from_dict(raw.get("settings")),
            doc=doc,
            created=raw.get("created", ""), updated=raw.get("updated", ""),
            report=raw.get("report") or {})

    def save(self, project: Project) -> Project:
        with _LOCK:
            base = self._dir(project.id)
            base.mkdir(parents=True, exist_ok=True)
            project.updated = _now()
            _write(base / "school.json", {
                "spec": spec_mod.to_dict(project.spec),
                "settings": project.settings.to_dict(),
                "created": project.created, "updated": project.updated,
                "report": project.report})
            if project.doc is not None:
                _write(base / "timetable.json", project.doc.to_dict())
            return project

    def delete(self, project_id: str) -> None:
        with _LOCK:
            shutil.rmtree(self._dir(project_id), ignore_errors=True)

    # ----------------------------------------------------------- history

    def _history(self, project_id: str) -> pathlib.Path:
        path = self._dir(project_id) / "history"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def snapshot(self, project: Project, label: str) -> None:
        """Remember the timetable as it is now, before changing it.

        Called *before* an edit rather than after, so "undo" restores what
        the user was looking at when they acted.
        """
        if project.doc is None:
            return
        with _LOCK:
            folder = self._history(project.id)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            _write(folder / f"{stamp}.json",
                   {"label": label, "at": _now(),
                    "doc": project.doc.to_dict()})
            old = sorted(folder.glob("*.json"))[:-HISTORY]
            for path in old:
                path.unlink(missing_ok=True)

    def versions(self, project_id: str) -> list[dict]:
        out = []
        for path in sorted(self._history(project_id).glob("*.json"),
                           reverse=True):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"id": path.stem, "label": raw.get("label", ""),
                        "at": raw.get("at", "")})
        return out

    def restore(self, project: Project, version_id: str) -> Project:
        path = self._history(project.id) / f"{version_id}.json"
        if not path.exists():
            raise KeyError(f"אין גרסה בשם {version_id}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        # The restore is itself undoable: the timetable being replaced goes
        # onto the history first.
        self.snapshot(project, "לפני שחזור גרסה")
        project.doc = tt_mod.Document.from_dict(raw["doc"])
        return self.save(project)

    def undo(self, project: Project) -> Project:
        versions = self.versions(project.id)
        if not versions:
            raise KeyError("אין לאן לחזור")
        newest = versions[0]["id"]
        path = self._history(project.id) / f"{newest}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        project.doc = tt_mod.Document.from_dict(raw["doc"])
        path.unlink(missing_ok=True)
        return self.save(project)

    # ----------------------------------------------------------- uploads

    def keep_upload(self, project_id: str, filename: str,
                    data: bytes) -> pathlib.Path:
        """Keep what was uploaded, so an import can be argued with later."""
        folder = self._dir(project_id) / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w֐-׿.-]+", "_", filename)[:80] or "upload"
        path = folder / f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{safe}"
        path.write_bytes(data)
        return path


def _write(path: pathlib.Path, payload: dict) -> None:
    """Write JSON without risking a half-written file on top of a good one."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)
