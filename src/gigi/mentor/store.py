"""
LIMA mentor data store (SQLite, robot-local, git-ignored).

Data lifecycle (from the LIMA design research):
  1. A check-in session writes its transcript here while it runs.
  2. A Peer Mentor or staff member reviews it in the LIMA web app, accepts the
     profile facts worth keeping (classes, hobbies, ...), and the transcript text is
     deleted. Session metadata (date, check-in, flag level, reviewer) is kept.
  3. Unreviewed transcripts are deleted automatically after ``retention_days``.
  4. The student profile (accepted facts and preferences) is kept so LIMA can
     remember the student next time, until staff delete it.

Every read of a transcript and every deletion is recorded in the audit table
(without transcript content).
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from gigi.core.config import DATA_DIR

DB_PATH = Path(os.environ.get("GIGI_LIMA_DB", str(DATA_DIR / "lima" / "lima.db")))

ROLES = ("mentor", "staff", "admin")
FLAG_ORDER = {"none": 0, "note": 1, "followup": 2, "urgent": 3}
LIST_FIELDS = ("classes", "hobbies")
PROFILE_FIELDS = ("classes", "hobbies", "major", "looking_forward", "event_preference")

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
    role TEXT NOT NULL, password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY, preferred_name TEXT NOT NULL, iu_username TEXT UNIQUE, pronouns TEXT DEFAULT '',
    mentor_id INTEGER REFERENCES staff(id), profile_json TEXT NOT NULL DEFAULT '{}',
    prefs_json TEXT NOT NULL DEFAULT '{}', staff_notes TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, updated REAL NOT NULL, last_seen REAL);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY, token TEXT UNIQUE NOT NULL, student_id INTEGER REFERENCES students(id),
    flow TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'talk', muted INTEGER NOT NULL DEFAULT 0,
    rate REAL NOT NULL DEFAULT 1.0, status TEXT NOT NULL DEFAULT 'pending', consent INTEGER,
    flag_level TEXT NOT NULL DEFAULT 'none', flag_reasons TEXT NOT NULL DEFAULT '[]',
    created REAL NOT NULL, started REAL, ended REAL, reviewed_by INTEGER, reviewed_at REAL,
    transcript_deleted INTEGER NOT NULL DEFAULT 0, followup_note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
    t REAL NOT NULL, speaker TEXT NOT NULL, text TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'voice');
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
    t REAL NOT NULL, text TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
    field TEXT NOT NULL, value TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, t REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_inbox_session ON inbox(session_id, consumed);
"""


class MentorStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA journal_mode = WAL")
            try:
                yield db
                db.commit()
            finally:
                db.close()

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def audit(self, actor: str, action: str, target: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO audit (t, actor, action, target) VALUES (?, ?, ?, ?)",
                       (time.time(), actor, action, target))

    def audit_log(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY t DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------------------
    # Staff accounts
    # ------------------------------------------------------------------
    def add_staff(self, username: str, display_name: str, role: str, password: str) -> int:
        from werkzeug.security import generate_password_hash

        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        if len(password) < 10:
            raise ValueError("password must be at least 10 characters")
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO staff (username, display_name, role, password_hash, created) VALUES (?, ?, ?, ?, ?)",
                (username.strip().lower(), display_name.strip(), role, generate_password_hash(password), time.time()),
            )
            return int(cur.lastrowid)

    def set_staff_password(self, staff_id: int, password: str) -> None:
        from werkzeug.security import generate_password_hash

        if len(password) < 10:
            raise ValueError("password must be at least 10 characters")
        with self.connect() as db:
            db.execute("UPDATE staff SET password_hash = ? WHERE id = ?", (generate_password_hash(password), staff_id))

    def set_staff_active(self, staff_id: int, active: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE staff SET active = ? WHERE id = ?", (1 if active else 0, staff_id))

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        from werkzeug.security import check_password_hash

        with self.connect() as db:
            row = db.execute("SELECT * FROM staff WHERE username = ? AND active = 1",
                             (username.strip().lower(),)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            return dict(row)
        return None

    def get_staff(self, staff_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
        return dict(row) if row else None

    def list_staff(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, username, display_name, role, active, created FROM staff ORDER BY role, username")]

    # ------------------------------------------------------------------
    # Students and profiles
    # ------------------------------------------------------------------
    @staticmethod
    def _student(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        s = dict(row)
        s["profile"] = json.loads(s.pop("profile_json") or "{}")
        s["prefs"] = json.loads(s.pop("prefs_json") or "{}")
        return s

    def find_or_create_student(self, preferred_name: str, iu_username: str | None = None) -> dict[str, Any]:
        name = preferred_name.strip()[:60] or "Friend"
        username = (iu_username or "").strip().lower()[:40] or None
        now = time.time()
        with self.connect() as db:
            row = None
            if username:
                row = db.execute("SELECT * FROM students WHERE iu_username = ?", (username,)).fetchone()
            if row is None and not username:
                # Without a username we can only match on name, and only if it is unambiguous.
                rows = db.execute("SELECT * FROM students WHERE lower(preferred_name) = lower(?)", (name,)).fetchall()
                row = rows[0] if len(rows) == 1 else None
            if row is None:
                cur = db.execute(
                    "INSERT INTO students (preferred_name, iu_username, created, updated) VALUES (?, ?, ?, ?)",
                    (name, username, now, now))
                row = db.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._student(row)

    def get_student(self, student_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._student(db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone())

    def list_students(self, mentor_id: int | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM students"
        args: tuple = ()
        if mentor_id is not None:
            q += " WHERE mentor_id = ? OR mentor_id IS NULL"
            args = (mentor_id,)
        with self.connect() as db:
            return [self._student(r) for r in db.execute(q + " ORDER BY preferred_name", args)]

    def update_student(self, student_id: int, **fields: Any) -> None:
        allowed = {"preferred_name", "iu_username", "pronouns", "mentor_id", "staff_notes"}
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                args.append(v)
            elif k in ("profile", "prefs"):
                sets.append(f"{k}_json = ?")
                args.append(json.dumps(v))
        if not sets:
            return
        sets.append("updated = ?")
        args.extend([time.time(), student_id])
        with self.connect() as db:
            db.execute(f"UPDATE students SET {', '.join(sets)} WHERE id = ?", args)

    def merge_profile_fact(self, student_id: int, field: str, value: str) -> None:
        student = self.get_student(student_id)
        if not student:
            return
        profile = student["profile"]
        if field in LIST_FIELDS:
            items = [v.strip() for v in str(value).replace(" and ", ",").split(",") if v.strip()]
            current = list(profile.get(field, []))
            for item in items:
                if item.lower() not in [c.lower() for c in current]:
                    current.append(item)
            profile[field] = current[-12:]
        else:
            profile[field] = str(value).strip()
        self.update_student(student_id, profile=profile)

    def set_pref(self, student_id: int, key: str, value: Any) -> None:
        student = self.get_student(student_id)
        if student:
            prefs = student["prefs"]
            prefs[key] = value
            self.update_student(student_id, prefs=prefs)

    def delete_student(self, student_id: int, actor: str) -> None:
        with self.connect() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM sessions WHERE student_id = ?", (student_id,))]
            for sid in ids:
                db.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
                db.execute("DELETE FROM inbox WHERE session_id = ?", (sid,))
                db.execute("DELETE FROM proposals WHERE session_id = ?", (sid,))
            db.execute("DELETE FROM sessions WHERE student_id = ?", (student_id,))
            db.execute("DELETE FROM students WHERE id = ?", (student_id,))
        self.audit(actor, "delete_student", f"student:{student_id}")

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    @staticmethod
    def _session(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        s = dict(row)
        s["flag_reasons"] = json.loads(s.get("flag_reasons") or "[]")
        return s

    def create_session(self, flow: str, student_id: int | None = None, mode: str = "talk",
                       muted: bool = False, rate: float = 1.0, status: str = "pending") -> dict[str, Any]:
        token = secrets.token_urlsafe(18)
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO sessions (token, student_id, flow, mode, muted, rate, status, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (token, student_id, flow, mode if mode in ("talk", "type") else "talk", 1 if muted else 0,
                 float(rate), status, time.time()))
            return self._session(db.execute("SELECT * FROM sessions WHERE id = ?", (cur.lastrowid,)).fetchone())

    def claim_pending_session(self, flow: str, max_age: float = 180.0) -> dict[str, Any] | None:
        """Called by the robot: take the newest web-started session for this check-in."""
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE status = 'pending' AND flow = ? AND created > ? "
                "ORDER BY created DESC LIMIT 1", (flow, time.time() - max_age)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE sessions SET status = 'active', started = ? WHERE id = ?", (time.time(), row["id"]))
            return self._session(db.execute("SELECT * FROM sessions WHERE id = ?", (row["id"],)).fetchone())

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._session(db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())

    def get_session_by_token(self, token: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._session(db.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone())

    def update_session(self, session_id: int, **fields: Any) -> None:
        allowed = {"student_id", "mode", "muted", "rate", "status", "consent", "started", "ended", "followup_note"}
        sets = [f"{k} = ?" for k in fields if k in allowed]
        args = [fields[k] for k in fields if k in allowed]
        if sets:
            with self.connect() as db:
                db.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", (*args, session_id))

    def flag_session(self, session_id: int, level: str, reason: str) -> None:
        if level not in FLAG_ORDER:
            level = "followup"
        with self.connect() as db:
            row = db.execute("SELECT flag_level, flag_reasons FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return
            reasons = json.loads(row["flag_reasons"] or "[]")
            if reason not in reasons:
                reasons.append(reason)
            new_level = level if FLAG_ORDER[level] > FLAG_ORDER.get(row["flag_level"], 0) else row["flag_level"]
            db.execute("UPDATE sessions SET flag_level = ?, flag_reasons = ? WHERE id = ?",
                       (new_level, json.dumps(reasons), session_id))

    def list_sessions(self, mentor_id: int | None = None, include_reviewed: bool = False) -> list[dict[str, Any]]:
        q = ("SELECT s.*, st.preferred_name, st.mentor_id FROM sessions s "
             "LEFT JOIN students st ON st.id = s.student_id WHERE s.status != 'pending'")
        args: list[Any] = []
        if not include_reviewed:
            q += " AND s.reviewed_at IS NULL AND s.transcript_deleted = 0"
        if mentor_id is not None:
            q += " AND (st.mentor_id = ? OR st.mentor_id IS NULL)"
            args.append(mentor_id)
        q += " ORDER BY s.created DESC"
        with self.connect() as db:
            rows = [self._session(r) for r in db.execute(q, args)]
        rows.sort(key=lambda s: (-FLAG_ORDER.get(s["flag_level"], 0), -(s["created"] or 0)))
        return rows

    def sessions_for_student(self, student_id: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [self._session(r) for r in db.execute(
                "SELECT * FROM sessions WHERE student_id = ? ORDER BY created DESC", (student_id,))]

    # ------------------------------------------------------------------
    # Messages and typed input
    # ------------------------------------------------------------------
    def add_message(self, session_id: int, speaker: str, text: str, channel: str = "voice") -> None:
        with self.connect() as db:
            db.execute("INSERT INTO messages (session_id, t, speaker, text, channel) VALUES (?, ?, ?, ?, ?)",
                       (session_id, time.time(), speaker, text, channel))

    def messages(self, session_id: int, after_id: int = 0) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, t, speaker, text, channel FROM messages WHERE session_id = ? AND id > ? ORDER BY id",
                (session_id, after_id))]

    def post_typed(self, session_id: int, text: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO inbox (session_id, t, text) VALUES (?, ?, ?)",
                       (session_id, time.time(), text.strip()[:2000]))

    def take_typed(self, session_id: int) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT id, text FROM inbox WHERE session_id = ? AND consumed = 0 ORDER BY id LIMIT 1",
                             (session_id,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE inbox SET consumed = 1 WHERE id = ?", (row["id"],))
            return row["text"]

    # ------------------------------------------------------------------
    # Profile proposals and review
    # ------------------------------------------------------------------
    def propose(self, session_id: int, field: str, value: str) -> None:
        if value and value.strip():
            with self.connect() as db:
                db.execute("INSERT INTO proposals (session_id, field, value) VALUES (?, ?, ?)",
                           (session_id, field, value.strip()[:300]))

    def proposals(self, session_id: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM proposals WHERE session_id = ? ORDER BY id", (session_id,))]

    def complete_review(self, session_id: int, reviewer: dict[str, Any], accepted: dict[int, str],
                        followup_note: str = "") -> None:
        """Save accepted facts to the profile, then delete the transcript text."""
        session = self.get_session(session_id)
        if session is None:
            return
        if session["student_id"]:
            for prop in self.proposals(session_id):
                if prop["id"] in accepted:
                    self.merge_profile_fact(session["student_id"], prop["field"], accepted[prop["id"]])
        with self.connect() as db:
            db.execute("UPDATE proposals SET status = CASE WHEN id IN (%s) THEN 'accepted' ELSE 'rejected' END "
                       "WHERE session_id = ?" % ",".join(str(int(i)) for i in accepted or [0]), (session_id,))
            db.execute("UPDATE sessions SET reviewed_by = ?, reviewed_at = ?, followup_note = ? WHERE id = ?",
                       (reviewer["id"], time.time(), followup_note.strip()[:1000], session_id))
        self.delete_transcript(session_id, actor=reviewer["username"], reason="reviewed")

    def delete_transcript(self, session_id: int, actor: str, reason: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM inbox WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM proposals WHERE session_id = ? AND status = 'pending'", (session_id,))
            db.execute("UPDATE sessions SET transcript_deleted = 1 WHERE id = ?", (session_id,))
        self.audit(actor, f"delete_transcript ({reason})", f"session:{session_id}")

    def purge_expired(self, retention_days: float) -> int:
        """Delete transcripts of sessions that ended more than ``retention_days`` ago."""
        cutoff = time.time() - retention_days * 86400
        with self.connect() as db:
            ids = [r["id"] for r in db.execute(
                "SELECT id FROM sessions WHERE transcript_deleted = 0 AND COALESCE(ended, created) < ?", (cutoff,))]
        for sid in ids:
            self.delete_transcript(sid, actor="system", reason=f"retention {retention_days:g} days")
        return len(ids)
