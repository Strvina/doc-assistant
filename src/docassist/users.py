"""People who sign in, and what each may do (ADR-048).

Three roles, each with the rights of the ones before it:

- ``employee`` — asks questions, opens the sources, keeps their own conversations;
- ``editor`` — also adds and deletes documents and sees the gaps in the documentation;
- ``admin`` — also sees usage and cost, manages the inbox folder and the users.

Users and their sign-ins live in the app's SQLite file (next to the conversations). A
password is stored only as a salted scrypt hash. How someone proves who they are is kept
apart from the roles, so another way to sign in (a company Microsoft account) can be added
without touching what a role may do.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from docassist.db import Database

ROLES = ("employee", "editor", "admin")  # in rising order of rights
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200  # scrypt would hash megabytes just as gladly: a cheap way to stall us
_USERNAME = re.compile(r"[a-z0-9][a-z0-9._@-]{2,63}")

# scrypt cost: ~65 ms and 16 MB per check on a laptop — slow for someone guessing passwords
# from a stolen file, unnoticeable for one sign-in.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,      -- lower case
    password_hash TEXT    NOT NULL,             -- scrypt$n$r$p$salt$hash (hex)
    role          TEXT    NOT NULL,             -- employee | editor | admin
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS user_sessions (
    session_id TEXT PRIMARY KEY,                -- the browser's session cookie
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS user_sessions_user ON user_sessions (user_id);
"""


class UserError(ValueError):
    """A change to the users that cannot be made. The message says why; `code` names the
    reason for a UI that words it in its own language."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class User:
    id: int
    username: str
    role: str
    disabled: bool = False
    created_at: str = ""

    def has(self, role: str) -> bool:
        """True when this user has at least the rights of `role`."""
        return has_role(self.role, role)


def has_role(role: str, minimum: str) -> bool:
    return ROLES.index(role) >= ROLES.index(minimum)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        expected = bytes.fromhex(digest)
        actual = _scrypt(password, bytes.fromhex(salt), int(n), int(r), int(p))
    except ValueError:  # not a hash of ours
        return False
    return scheme == "scrypt" and hmac.compare_digest(actual, expected)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)


# Checked when the name is unknown, so "no such user" takes as long as "wrong password".
_NO_USER_HASH = hash_password(secrets.token_hex(16))


class UserStore:
    def __init__(self, path: str | Path) -> None:
        self._db = Database(Path(path), lambda db: db.executescript(_SCHEMA))

    def _connect(self) -> sqlite3.Connection:
        db = self._db.connect()
        db.execute("PRAGMA foreign_keys = ON")  # deleting a user ends their sign-ins
        db.row_factory = sqlite3.Row
        return db

    # --- users -----------------------------------------------------------------------------

    def add(self, username: str, password: str, role: str = "employee") -> User:
        username = _checked_username(username)
        _check_role(role)
        _check_password(password)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            with closing(self._connect()) as db, db:
                cursor = db.execute(
                    "INSERT INTO users (username, password_hash, role, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (username, hash_password(password), role, now),
                )
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise UserError(f"A user named {username!r} already exists", "taken") from exc
        assert user_id is not None  # set by every INSERT
        return User(user_id, username, role, created_at=now)

    def all(self) -> list[User]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_user(row) for row in rows]

    def count(self) -> int:
        with closing(self._connect()) as db:
            count: int = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return count

    def get(self, user_id: int) -> User | None:
        return self._one("SELECT * FROM users WHERE id = ?", user_id)

    def by_name(self, username: str) -> User | None:
        return self._one("SELECT * FROM users WHERE username = ?", username.strip().lower())

    def verify(self, username: str, password: str) -> User | None:
        """The user, when the name and password match and the account is not disabled."""
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
            ).fetchone()
        if len(password) > MAX_PASSWORD_LENGTH:
            return None
        if row is None:
            verify_password(password, _NO_USER_HASH)
            return None
        if not verify_password(password, row["password_hash"]) or row["disabled"]:
            return None
        return _user(row)

    def set_role(self, user_id: int, role: str) -> None:
        _check_role(role)
        self._change(user_id, "role = ?", role, keeps_admin=role == "admin")

    def set_disabled(self, user_id: int, disabled: bool) -> None:
        """A disabled user cannot sign in and is signed out everywhere."""
        self._change(user_id, "disabled = ?", int(disabled), keeps_admin=not disabled)
        if disabled:
            self.sign_out_everywhere(user_id)

    def set_password(self, user_id: int, password: str) -> None:
        """A new password signs the user out everywhere (whoever knew the old one too)."""
        _check_password(password)
        self._change(user_id, "password_hash = ?", hash_password(password), keeps_admin=True)
        self.sign_out_everywhere(user_id)

    def remove(self, user_id: int) -> None:
        with closing(self._connect()) as db, db:
            self._keep_an_admin(db, user_id)
            if not db.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount:
                raise UserError("No such user", "missing")

    def _change(self, user_id: int, assignment: str, value: object, *, keeps_admin: bool) -> None:
        # `assignment` is one of the fixed column assignments above, never user input.
        with closing(self._connect()) as db, db:
            if not keeps_admin:
                self._keep_an_admin(db, user_id)
            updated = db.execute(f"UPDATE users SET {assignment} WHERE id = ?", (value, user_id))
            if not updated.rowcount:
                raise UserError("No such user", "missing")

    @staticmethod
    def _keep_an_admin(db: sqlite3.Connection, user_id: int) -> None:
        """Refuse a change that would leave nobody who can manage the users."""
        others = db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
            (user_id,),
        ).fetchone()[0]
        target = db.execute("SELECT role, disabled FROM users WHERE id = ?", (user_id,)).fetchone()
        if target and target["role"] == "admin" and not target["disabled"] and not others:
            raise UserError("This is the only administrator: make another one first", "last_admin")

    def _one(self, sql: str, *params: object) -> User | None:
        with closing(self._connect()) as db:
            row = db.execute(sql, params).fetchone()
        return _user(row) if row else None

    # --- sign-ins --------------------------------------------------------------------------

    def sign_in(self, session_id: str, user_id: int) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO user_sessions (session_id, user_id, created_at)"
                " VALUES (?, ?, ?)",
                (session_id, user_id, now),
            )

    def sign_out(self, session_id: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_id,))

    def sign_out_everywhere(self, user_id: int) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))

    def signed_in(self, session_id: str) -> User | None:
        """Who a session cookie belongs to. Read on every request, so a changed role or a
        disabled account takes effect at once."""
        return self._one(
            "SELECT u.* FROM user_sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.session_id = ? AND u.disabled = 0",
            session_id,
        )


def _user(row: sqlite3.Row) -> User:
    return User(row["id"], row["username"], row["role"], bool(row["disabled"]), row["created_at"])


def _checked_username(username: str) -> str:
    username = username.strip().lower()
    if not _USERNAME.fullmatch(username):
        raise UserError(
            "A user name has 3 to 64 letters, digits or . _ - @ and starts with a letter or digit",
            "username",
        )
    return username


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise UserError(f"Unknown role {role!r}. Use one of: {', '.join(ROLES)}", "role")


def _check_password(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise UserError(
            f"A password has {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters", "password"
        )
