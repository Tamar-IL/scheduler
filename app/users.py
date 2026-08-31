# -*- coding: utf-8 -*-
"""Accounts and sessions.

A timetable is months of a person's decisions, so it needs an owner. This is
the smallest thing that gives it one honestly: PBKDF2-SHA256 with a per-user
salt, a random session token in an HttpOnly cookie, and one directory of
schools per user. All standard library — a scheduling program should not
gain a database and a web framework to remember who you are.

What this deliberately is *not*: it has no password reset, no email, and no
roles. A school office runs this on one machine for a handful of people, and
every one of those features needs infrastructure (a mail server, a recovery
policy) that would be a bigger promise than the code could keep. An
administrator who forgets a password is reset by whoever can reach the
`data/` directory, which is the same person who could read the timetables
anyway.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import pathlib
import re
import secrets
import threading
import unicodedata
from dataclasses import dataclass, field

#: Cost of one password check. High enough that guessing is slow, low enough
#: that a login on an office laptop still feels instant (~0.1s).
ITERATIONS = 240_000

#: How long a session lasts without use. Refreshed on every request, so a
#: person who works daily is never logged out.
SESSION_DAYS = 30

#: Failed attempts before an account stops answering for a while. Counted per
#: account rather than per address: the threat here is somebody at the next
#: desk trying passwords, not a botnet.
MAX_ATTEMPTS = 8
LOCKOUT_MINUTES = 15

_LOCK = threading.RLock()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _stamp(when: datetime.datetime) -> str:
    return when.replace(microsecond=0).isoformat()


def _parse(text: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def normalise(username: str) -> str:
    """One spelling per account.

    Hebrew and Latin both admit several byte sequences for what a person sees
    as the same name, so the stored key is NFKC-folded and case-flattened —
    otherwise an account could be registered twice and neither login would
    feel wrong.
    """
    return unicodedata.normalize("NFKC", (username or "").strip()).casefold()


def _hash(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                              iterations)
    return base64.b64encode(raw).decode("ascii")


@dataclass
class User:
    id: str
    username: str
    display: str
    salt: str
    hash: str
    iterations: int = ITERATIONS
    created: str = ""
    failures: int = 0
    locked_until: str = ""

    def public(self) -> dict:
        return {"id": self.id, "username": self.username,
                "display": self.display, "created": self.created}

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Users:
    """The account file, and the live sessions against it."""
    root: pathlib.Path
    _users: dict[str, User] = field(default_factory=dict)
    _sessions: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self):
        self.root = pathlib.Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._load()

    # ------------------------------------------------------------- files

    @property
    def _users_file(self) -> pathlib.Path:
        return self.root / "users.json"

    @property
    def _sessions_file(self) -> pathlib.Path:
        return self.root / "sessions.json"

    def _load(self) -> None:
        if self._users_file.exists():
            raw = json.loads(self._users_file.read_text(encoding="utf-8"))
            self._users = {u["username"]: User(**u) for u in raw}
        if self._sessions_file.exists():
            try:
                self._sessions = json.loads(
                    self._sessions_file.read_text(encoding="utf-8"))
            except ValueError:
                self._sessions = {}
        self._prune()

    def _save(self) -> None:
        _write(self._users_file, [u.to_dict() for u in self._users.values()])
        _write(self._sessions_file, self._sessions)

    def _prune(self) -> None:
        now = _now()
        self._sessions = {
            token: s for token, s in self._sessions.items()
            if (_parse(s.get("expires", "")) or now) > now
            and s.get("user") in {u.id for u in self._users.values()}}

    # ---------------------------------------------------------- accounts

    @property
    def empty(self) -> bool:
        """No accounts yet — the first visitor is registering the office."""
        return not self._users

    def count(self) -> int:
        return len(self._users)

    def by_id(self, user_id: str) -> User | None:
        return next((u for u in self._users.values() if u.id == user_id), None)

    def register(self, username: str, password: str,
                 display: str = "") -> User:
        key = normalise(username)
        problem = self.check_new(key, password)
        if problem:
            raise ValueError(problem)
        with _LOCK:
            if key in self._users:
                raise ValueError("שם המשתמש כבר תפוס")
            salt = secrets.token_bytes(16)
            user = User(
                id=secrets.token_hex(8), username=key,
                display=(display or username).strip(),
                salt=base64.b64encode(salt).decode("ascii"),
                hash=_hash(password, salt), iterations=ITERATIONS,
                created=_stamp(_now()))
            self._users[key] = user
            self._save()
            return user

    @staticmethod
    def check_new(username: str, password: str) -> str:
        """Why this account cannot be created — in a sentence, or empty."""
        if len(username) < 3:
            return "שם המשתמש חייב להיות באורך 3 תווים לפחות"
        if not re.fullmatch(r"[\w.\-@]+", username, flags=re.UNICODE):
            return ("שם המשתמש יכול להכיל אותיות, ספרות, נקודה, מקף "
                    "וקו תחתון בלבד")
        if len(password) < 8:
            return "הסיסמה חייבת להיות באורך 8 תווים לפחות"
        if password.strip() != password:
            return "הסיסמה אינה יכולה להתחיל או להסתיים ברווח"
        return ""

    def authenticate(self, username: str, password: str) -> User:
        """The user, or a Hebrew sentence saying why not.

        A wrong name and a wrong password give the same answer on purpose:
        the login screen should not be a way to find out who has an account.
        """
        key = normalise(username)
        with _LOCK:
            user = self._users.get(key)
            if user is None:
                # Spend roughly the same time as a real check would, so the
                # response time does not answer the question either.
                _hash(password, b"decoy-salt-000000", ITERATIONS)
                raise ValueError("שם משתמש או סיסמה שגויים")
            locked = _parse(user.locked_until)
            if locked and locked > _now():
                minutes = max(1, int((locked - _now()).total_seconds() // 60))
                raise ValueError(
                    f"החשבון נעול בעקבות ניסיונות כושלים. "
                    f"יש לנסות שוב בעוד {minutes} דקות.")
            expected = _hash(password, base64.b64decode(user.salt),
                             user.iterations)
            if not hmac.compare_digest(expected, user.hash):
                user.failures += 1
                if user.failures >= MAX_ATTEMPTS:
                    user.locked_until = _stamp(
                        _now() + datetime.timedelta(minutes=LOCKOUT_MINUTES))
                    user.failures = 0
                self._save()
                raise ValueError("שם משתמש או סיסמה שגויים")
            user.failures, user.locked_until = 0, ""
            self._save()
            return user

    def change_password(self, user: User, current: str, new: str) -> None:
        self.authenticate(user.username, current)
        problem = self.check_new(user.username, new)
        if problem:
            raise ValueError(problem)
        with _LOCK:
            salt = secrets.token_bytes(16)
            user.salt = base64.b64encode(salt).decode("ascii")
            user.hash = _hash(new, salt)
            user.iterations = ITERATIONS
            # Every other session belonging to this account goes: changing a
            # password is what a person does when they think someone else has
            # been using it.
            self._sessions = {t: s for t, s in self._sessions.items()
                              if s.get("user") != user.id}
            self._save()

    # ---------------------------------------------------------- sessions

    def start_session(self, user: User) -> str:
        with _LOCK:
            token = secrets.token_urlsafe(32)
            self._sessions[token] = {
                "user": user.id,
                "expires": _stamp(_now() + datetime.timedelta(
                    days=SESSION_DAYS))}
            self._prune()
            self._save()
            return token

    def session_user(self, token: str | None) -> User | None:
        """Who this cookie belongs to, refreshing the expiry as a side effect."""
        if not token:
            return None
        with _LOCK:
            session = self._sessions.get(token)
            if session is None:
                return None
            expires = _parse(session.get("expires", ""))
            if expires is None or expires <= _now():
                self._sessions.pop(token, None)
                self._save()
                return None
            user = self.by_id(session["user"])
            if user is None:
                return None
            # Sliding expiry, written back only about once a day: a rewrite
            # on every request would make every page load a disk write.
            if expires - _now() < datetime.timedelta(days=SESSION_DAYS - 1):
                session["expires"] = _stamp(
                    _now() + datetime.timedelta(days=SESSION_DAYS))
                self._save()
            return user

    def end_session(self, token: str | None) -> None:
        if not token:
            return
        with _LOCK:
            if self._sessions.pop(token, None) is not None:
                self._save()

    # ------------------------------------------------------------ storage

    def workspace(self, user: User) -> pathlib.Path:
        """Where this user's schools live.

        One directory per account, named by its opaque id rather than by the
        username, so renaming an account later never has to move files and a
        username can hold any character the account rules allow.
        """
        path = self.root / "workspaces" / user.id
        path.mkdir(parents=True, exist_ok=True)
        return path


def _write(path: pathlib.Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)
