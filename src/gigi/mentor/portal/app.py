"""
LIMA web app (sample): student check-in kiosk + Peer Mentor / staff portal.

Students (no login, /student):
    start a check-in, choose to talk or type, mute the robot's voice, set its speed,
    and chat with LIMA by typing.
Peer Mentors and staff (login, /):
    see flagged and unreviewed check-ins, read transcripts, keep the facts worth
    remembering in the student's profile, then delete the transcript; manage
    profiles; (staff/admin) set the robot voice; (admin) manage accounts and audit log.

Run on the robot:     gigi lima --host 0.0.0.0     (LAN; put HTTPS in front for real use)
Run on a laptop:      gigi lima --virtual          (uses the Virtual Robot Console)

This is a prototype. Before real student data: IU single sign-on instead of local
passwords, HTTPS, an approved data-handling plan (FERPA), and staff review of all
robot content. See docs/LIMA_MENTOR.md.
"""

from __future__ import annotations

import functools
import os
import secrets
import time
from pathlib import Path
from typing import Any

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from gigi.core.config import DATA_DIR
from gigi.core import voice_settings as vs
from gigi.mentor import launcher
from gigi.mentor.content import list_flows, load_config
from gigi.mentor.store import FLAG_ORDER, MentorStore, ROLES

LOGIN_LOCK_SECONDS = 300
LOGIN_MAX_FAILURES = 5


def _secret_key() -> str:
    env = os.environ.get("GIGI_LIMA_SECRET")
    if env:
        return env
    path = Path(DATA_DIR) / "lima" / "secret_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_text(encoding="utf-8").strip()


def create_app(store: MentorStore | None = None, virtual_console: str | None = None,
               config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder=None)
    app.config.update(
        SECRET_KEY=_secret_key(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("GIGI_LIMA_HTTPS", "0") == "1",
        PERMANENT_SESSION_LIFETIME=8 * 3600,
    )
    st = store or MentorStore()
    cfg = config or load_config()
    failures: dict[str, list[float]] = {}
    st.purge_expired(cfg.get("retention_days", 14))

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------
    @app.before_request
    def load_user():
        g.user = st.get_staff(session["staff_id"]) if session.get("staff_id") else None
        if g.user and not g.user["active"]:
            session.clear()
            g.user = None
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)

    @app.context_processor
    def inject():
        return {"user": g.get("user"), "csrf": session.get("csrf"), "flag_order": FLAG_ORDER,
                "fmt_time": lambda t: time.strftime("%b %d, %I:%M %p", time.localtime(t)) if t else "-"}

    def check_csrf():
        if request.form.get("csrf") != session.get("csrf"):
            abort(400, "Form expired. Go back, reload, and try again.")

    def login_required(*roles):
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                if not g.user:
                    return redirect(url_for("login", next=request.path))
                if roles and g.user["role"] not in roles:
                    abort(403)
                if request.method == "POST":
                    check_csrf()
                return fn(*a, **kw)
            return wrapper
        return deco

    def mentor_scope():
        return g.user["id"] if g.user["role"] == "mentor" else None

    def can_see_student(student):
        return student and (g.user["role"] != "mentor" or student["mentor_id"] in (None, g.user["id"]))

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            check_csrf()
            username = request.form.get("username", "").strip().lower()
            key = f"{username}|{request.remote_addr}"
            recent = [t for t in failures.get(key, []) if time.time() - t < LOGIN_LOCK_SECONDS]
            if len(recent) >= LOGIN_MAX_FAILURES:
                flash("Too many attempts. Wait a few minutes and try again.", "error")
                return render_template("login.html"), 429
            user = st.authenticate(username, request.form.get("password", ""))
            if user:
                failures.pop(key, None)
                session.clear()
                session["staff_id"] = user["id"]
                session["csrf"] = secrets.token_urlsafe(24)
                st.audit(user["username"], "login", "-")
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
            failures[key] = recent + [time.time()]
            flash("Username or password is incorrect.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        check_csrf()
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------------
    # Dashboard and conversations
    # ------------------------------------------------------------------
    @app.route("/")
    @login_required()
    def dashboard():
        st.purge_expired(cfg.get("retention_days", 14))
        sessions = st.list_sessions(mentor_id=mentor_scope())
        return render_template("dashboard.html", sessions=sessions, retention=cfg.get("retention_days", 14))

    @app.route("/conversations/<int:sid>")
    @login_required()
    def conversation(sid):
        s = st.get_session(sid)
        student = st.get_student(s["student_id"]) if s and s["student_id"] else None
        if not s or (student and not can_see_student(student)):
            abort(404)
        if not s["transcript_deleted"]:
            st.audit(g.user["username"], "view_transcript", f"session:{sid}")
        return render_template("conversation.html", s=s, student=student, messages=st.messages(sid),
                               proposals=st.proposals(sid))

    @app.route("/conversations/<int:sid>/review", methods=["POST"])
    @login_required()
    def review(sid):
        s = st.get_session(sid)
        student = st.get_student(s["student_id"]) if s and s["student_id"] else None
        if not s or (student and not can_see_student(student)):
            abort(404)
        if request.form.get("action") == "discard":
            st.delete_transcript(sid, actor=g.user["username"], reason="discarded without saving")
            with st.connect() as db:
                db.execute("UPDATE sessions SET reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                           (g.user["id"], time.time(), sid))
            flash("Transcript deleted. Nothing was added to the profile.", "ok")
            return redirect(url_for("dashboard"))
        accepted = {}
        for p in st.proposals(sid):
            if request.form.get(f"accept_{p['id']}"):
                accepted[p["id"]] = request.form.get(f"value_{p['id']}", p["value"])[:300]
        st.complete_review(sid, g.user, accepted, request.form.get("followup_note", ""))
        flash(f"Saved {len(accepted)} item(s) to the profile and deleted the transcript.", "ok")
        return redirect(url_for("dashboard"))

    # ------------------------------------------------------------------
    # Students
    # ------------------------------------------------------------------
    @app.route("/students")
    @login_required()
    def students():
        return render_template("students.html", students=st.list_students(mentor_id=mentor_scope()))

    @app.route("/students/<int:student_id>", methods=["GET", "POST"])
    @login_required()
    def student(student_id):
        s = st.get_student(student_id)
        if not can_see_student(s):
            abort(404)
        if request.method == "POST":
            if request.form.get("action") == "delete":
                if g.user["role"] == "mentor":
                    abort(403)
                st.delete_student(student_id, actor=g.user["username"])
                flash("Student profile and all their check-in records were deleted.", "ok")
                return redirect(url_for("students"))
            profile = dict(s["profile"])
            for field in ("classes", "hobbies"):
                profile[field] = [x.strip() for x in request.form.get(field, "").split(",") if x.strip()]
            for field in ("major", "looking_forward", "event_preference"):
                profile[field] = request.form.get(field, "").strip()
            mentor = request.form.get("mentor_id") or None
            st.update_student(
                student_id,
                preferred_name=request.form.get("preferred_name", s["preferred_name"]).strip()[:60],
                pronouns=request.form.get("pronouns", "").strip()[:30],
                staff_notes=request.form.get("staff_notes", "").strip()[:4000],
                mentor_id=int(mentor) if mentor and g.user["role"] != "mentor" else s["mentor_id"],
                profile=profile,
            )
            st.audit(g.user["username"], "edit_profile", f"student:{student_id}")
            flash("Profile saved.", "ok")
            return redirect(url_for("student", student_id=student_id))
        mentors = [u for u in st.list_staff() if u["role"] == "mentor" and u["active"]]
        return render_template("student.html", s=s, history=st.sessions_for_student(student_id), mentors=mentors)

    # ------------------------------------------------------------------
    # Robot voice (staff/admin)
    # ------------------------------------------------------------------
    @app.route("/voice", methods=["GET", "POST"])
    @login_required("staff", "admin")
    def voice():
        if request.method == "POST":
            new = vs.save_voice_settings({
                "engine": request.form.get("engine", "auto"),
                "voice": request.form.get("voice", vs.DEFAULT_VOICE),
                "speaker_id": request.form.get("speaker_id") or None,
                "rate": request.form.get("rate", 1.0),
                "pitch_semitones": request.form.get("pitch_semitones", 0.0),
            })
            st.audit(g.user["username"], "set_voice", str(new))
            flash("Voice settings saved. The robot uses them from its next sentence.", "ok")
            return redirect(url_for("voice"))
        current = vs.load_voice_settings()
        return render_template("voice.html", current=current, voices=vs.installed_piper_voices(),
                               piper=vs.piper_available(), engines=vs.ENGINES,
                               rate_range=(vs.RATE_MIN, vs.RATE_MAX), pitch_range=(vs.PITCH_MIN, vs.PITCH_MAX))

    # ------------------------------------------------------------------
    # Accounts and audit (admin)
    # ------------------------------------------------------------------
    @app.route("/users", methods=["GET", "POST"])
    @login_required("admin")
    def users():
        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action == "add":
                    st.add_staff(request.form["username"], request.form["display_name"], request.form["role"],
                                 request.form["password"])
                    st.audit(g.user["username"], "add_user", request.form["username"])
                elif action == "password":
                    st.set_staff_password(int(request.form["id"]), request.form["password"])
                    st.audit(g.user["username"], "reset_password", f"staff:{request.form['id']}")
                elif action in ("deactivate", "activate"):
                    if int(request.form["id"]) == g.user["id"]:
                        raise ValueError("You cannot deactivate your own account.")
                    st.set_staff_active(int(request.form["id"]), action == "activate")
                    st.audit(g.user["username"], action, f"staff:{request.form['id']}")
                flash("Saved.", "ok")
            except Exception as e:
                flash(f"Could not save: {e}", "error")
            return redirect(url_for("users"))
        return render_template("users.html", staff=st.list_staff(), roles=ROLES)

    @app.route("/audit")
    @login_required("admin")
    def audit():
        return render_template("audit.html", rows=st.audit_log())

    # ------------------------------------------------------------------
    # Student kiosk (no login)
    # ------------------------------------------------------------------
    @app.route("/student", methods=["GET", "POST"])
    def kiosk_start():
        flows = list_flows()
        if request.method == "POST":
            check_csrf()
            name = request.form.get("name", "").strip()
            flow = request.form.get("flow", "")
            if not name or flow not in [f["id"] for f in flows]:
                flash("Please enter your name and pick a check-in.", "error")
                return render_template("kiosk_start.html", flows=flows), 400
            student = st.find_or_create_student(name, request.form.get("iu_username"))
            prefs = student["prefs"]
            sess = st.create_session(
                flow, student_id=student["id"],
                mode=request.form.get("mode") or prefs.get("input_mode", "talk"),
                muted=bool(request.form.get("muted")),
                rate=vs.validate({"rate": request.form.get("rate") or prefs.get("speech_rate", 1.0)})["rate"],
            )
            ok, message = launcher.start_checkin(flow, virtual_console=virtual_console)
            if not ok:
                st.update_session(sess["id"], status="declined", ended=time.time())
                st.delete_transcript(sess["id"], actor="system", reason="robot could not start")
                flash(message, "error")
                return render_template("kiosk_start.html", flows=flows), 503
            return redirect(url_for("kiosk_chat", token=sess["token"]))
        return render_template("kiosk_start.html", flows=flows)

    def _kiosk_session(token):
        s = st.get_session_by_token(token)
        if not s or s["transcript_deleted"]:
            abort(404)
        return s

    def _kiosk_visible(s):
        # On a shared kiosk the next student must not be able to scroll back through
        # someone else's chat, so the transcript disappears shortly after the session ends.
        return s["status"] in ("pending", "active") or (s["ended"] and time.time() - s["ended"] < 60)

    @app.route("/student/s/<token>")
    def kiosk_chat(token):
        s = _kiosk_session(token)
        return render_template("kiosk_chat.html", s=s, rate_range=(vs.RATE_MIN, vs.RATE_MAX))

    @app.route("/student/api/<token>/messages")
    def kiosk_messages(token):
        s = _kiosk_session(token)
        after = int(request.args.get("after", 0))
        msgs = st.messages(s["id"], after) if _kiosk_visible(s) else []
        return jsonify({"status": s["status"], "mode": s["mode"], "muted": bool(s["muted"]), "rate": s["rate"],
                        "messages": [m for m in msgs if m["speaker"] in ("robot", "student")]})

    @app.route("/student/api/<token>/say", methods=["POST"])
    def kiosk_say(token):
        s = _kiosk_session(token)
        text = str((request.get_json(silent=True) or {}).get("text", "")).strip()
        if text and s["status"] in ("pending", "active"):
            st.post_typed(s["id"], text)
        return jsonify({"ok": True})

    @app.route("/student/api/<token>/settings", methods=["POST"])
    def kiosk_settings(token):
        s = _kiosk_session(token)
        data = request.get_json(silent=True) or {}
        fields = {}
        if data.get("mode") in ("talk", "type"):
            fields["mode"] = data["mode"]
        if "muted" in data:
            fields["muted"] = 1 if data["muted"] else 0
        if "rate" in data:
            fields["rate"] = vs.validate({"rate": data["rate"]})["rate"]
        st.update_session(s["id"], **fields)
        return jsonify({"ok": True, **fields})

    @app.route("/student/api/<token>/end", methods=["POST"])
    def kiosk_end(token):
        s = _kiosk_session(token)
        if s["status"] in ("pending", "active"):
            st.post_typed(s["id"], "stop")
        return jsonify({"ok": True})

    return app


def main(host: str = "127.0.0.1", port: int = 5080, virtual: bool = False):
    console = os.environ.get("GIGI_VIRTUAL_CONSOLE") or ("http://127.0.0.1:5050" if virtual else None)
    app = create_app(virtual_console=console)
    store = MentorStore()
    if not any(u["role"] == "admin" for u in store.list_staff()):
        print("No admin account yet. Create one with:  gigi lima-user add <username> --role admin")
    print(f"LIMA web app: http://{host}:{port}/  (students: /student)"
          + (f"  [virtual robot at {console}]" if console else ""))
    if host not in ("127.0.0.1", "localhost") and os.environ.get("GIGI_LIMA_HTTPS") != "1":
        print("WARNING: reachable on the network over plain HTTP. Use only on a trusted network for testing.")
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
