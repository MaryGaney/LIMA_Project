"""
Tests for LIMA peer-mentor features: data store lifecycle, safety triage, the check-in
runner (with a fake robot), the LIMA web app, voice settings, and the LIMA face.

All names are fictional. No robot hardware, audio, or network is used.
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from gigi.mentor import safety  # noqa: E402
from gigi.mentor.checkin import CheckinRunner, parse_scale, parse_yesno  # noqa: E402
from gigi.mentor.content import list_flows, load_config, load_flow  # noqa: E402
from gigi.mentor.store import MentorStore  # noqa: E402


class FakeIO:
    """Scripted student: each listen() returns the next reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.said = []
        self.rates = []

    def say(self, text, muted=False):
        self.said.append(text)

    def listen(self):
        return self.replies.pop(0) if self.replies else "stop"

    def set_rate(self, rate):
        self.rates.append(rate)


class TempStoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MentorStore(Path(self.tmp.name) / "lima.db")
        self.config = load_config()

    def tearDown(self):
        self.tmp.cleanup()

    def run_flow(self, flow_id, replies, session=None):
        io = FakeIO(replies)
        runner = CheckinRunner(io, self.store, load_flow(flow_id), self.config, session=session,
                               typed_timeout=0.5, poll=0.01)
        return runner.run(), io


class TestContent(unittest.TestCase):
    def test_all_checkins_load_and_use_known_step_types(self):
        flows = list_flows()
        self.assertGreaterEqual(len(flows), 5)
        known = {"say", "ask", "resources", "events", "flag"}

        def walk(steps):
            for s in steps:
                self.assertIn(s["type"], known)
                for branch in (s.get("branches") or {}).values():
                    walk(branch)

        for f in flows:
            walk(load_flow(f["id"])["steps"])

    def test_invalid_flow_id_rejected(self):
        with self.assertRaises(ValueError):
            load_flow("../secrets")


class TestSafety(unittest.TestCase):
    def test_urgent(self):
        for text in ["I've been thinking about killing myself", "sometimes I want to die", "I hurt myself last week"]:
            self.assertEqual(safety.assess(text).level, "urgent", text)

    def test_followup(self):
        for text in ["I've felt overwhelmed for weeks and stopped going to class", "I usually eat alone",
                     "I'm so homesick"]:
            self.assertEqual(safety.assess(text).level, "followup", text)

    def test_none(self):
        self.assertEqual(safety.assess("I like playing guitar and my CS class is fun").level, "none")

    def test_parsers(self):
        self.assertEqual(parse_scale("probably a 2"), 2)
        self.assertEqual(parse_scale("four I guess"), 4)
        self.assertIsNone(parse_scale("pretty good"))
        self.assertTrue(parse_yesno("yeah sure"))
        self.assertFalse(parse_yesno("no thanks"))


class TestCheckinRunner(TempStoreCase):
    def test_first_meeting_collects_profile_proposals(self):
        session, io = self.run_flow("first_meeting", [
            "Sam", "yes", "Intro to Programming and Calculus", "Informatics", "guitar and climbing", "4",
            "the hackathon"])
        self.assertEqual(session["status"], "ended")
        self.assertEqual(session["consent"], 1)
        fields = {p["field"]: p["value"] for p in self.store.proposals(session["id"])}
        self.assertEqual(fields["hobbies"], "guitar and climbing")
        self.assertEqual(fields["classes"], "Intro to Programming and Calculus")
        self.assertTrue(any("disclos" in t.lower() or "transcript" in t.lower() for t in io.said[:6]),
                        "robot must explain what it keeps before asking anything personal")

    def test_decline_consent_deletes_everything(self):
        session, io = self.run_flow("weekly_checkin", ["Sam", "no"])
        self.assertEqual(session["status"], "declined")
        self.assertTrue(session["transcript_deleted"])
        self.assertEqual(self.store.messages(session["id"]), [])

    def test_urgent_disclosure_stops_script_and_flags(self):
        session, io = self.run_flow("weekly_checkin", ["Sam", "yes", "I don't want to be alive anymore", "stop"])
        self.assertEqual(session["flag_level"], "urgent")
        self.assertTrue(any("988" in t for t in io.said))
        self.assertFalse(any("How are your classes" in t for t in io.said), "must not continue the cheerful script")

    def test_followup_disclosure_flags_and_continues(self):
        session, io = self.run_flow("weekly_checkin", [
            "Sam", "yes", "2", "I've felt overwhelmed for weeks and stopped going to class", "okay", "fine"])
        self.assertEqual(session["flag_level"], "followup")
        self.assertTrue(any("Peer Mentor can follow up" in t for t in io.said))

    def test_unknown_question_is_not_guessed(self):
        session, io = self.run_flow("resource_finder", ["Sam", "yes", "Where can I park a boat?", "no"])
        self.assertTrue(any("don't want to guess" in t for t in io.said))
        self.assertEqual(session["flag_level"], "note")

    def test_scholarship_question_points_to_advisor(self):
        session, io = self.run_flow("resource_finder", ["Sam", "yes", "Will dropping a class affect my scholarship?", "no"])
        self.assertTrue(any("advisor" in t for t in io.said))

    def test_skip_and_stop(self):
        session, io = self.run_flow("first_meeting", ["Sam", "yes", "skip", "stop"])
        self.assertTrue(any("skip that one" in t for t in io.said))
        self.assertEqual(session["status"], "ended")

    def test_returning_student_is_remembered_after_review(self):
        first, _ = self.run_flow("first_meeting", ["Jordan", "yes", "Data Structures", "skip", "chess", "5", "skip"])
        reviewer = {"id": self.store.add_staff("pm1", "PM One", "mentor", "correct-horse-1"), "username": "pm1"}
        props = {p["id"]: p["value"] for p in self.store.proposals(first["id"])}
        self.store.complete_review(first["id"], reviewer, props)
        self.assertEqual(self.store.messages(first["id"]), [], "transcript deleted after review")
        second, io = self.run_flow("weekly_checkin", ["Jordan", "yes", "4", "good", "yes", "fine", "nothing"])
        self.assertTrue(any("chess" in t for t in io.said), "robot should remember hobbies")
        self.assertTrue(any("Data Structures" in t for t in io.said), "robot should remember classes")

    def test_typed_session_from_web(self):
        student = self.store.find_or_create_student("Riley", "rileyx")
        pending = self.store.create_session("exam_prep", student_id=student["id"], mode="type", rate=1.3)
        claimed = self.store.claim_pending_session("exam_prep")
        self.assertEqual(claimed["id"], pending["id"])
        for text in ["yes", "Midterm in C211", "4", "no"]:
            self.store.post_typed(claimed["id"], text)
        session, io = self.run_flow("exam_prep", [], session=claimed)
        self.assertEqual(io.rates[0], 1.3, "student's speed applied to the robot voice")
        typed = [m for m in self.store.messages(session["id"]) if m["speaker"] == "student"]
        self.assertTrue(all(m["channel"] == "typed" for m in typed))
        self.assertEqual(session["status"], "ended")


class TestStoreLifecycle(TempStoreCase):
    def test_retention_purge(self):
        s = self.store.create_session("weekly_checkin", status="active")
        self.store.add_message(s["id"], "student", "hello")
        with self.store.connect() as db:
            db.execute("UPDATE sessions SET ended = 0, status = 'ended' WHERE id = ?", (s["id"],))
        self.assertEqual(self.store.purge_expired(14), 1)
        self.assertEqual(self.store.messages(s["id"]), [])
        self.assertTrue(any("retention" in r["action"] for r in self.store.audit_log()))

    def test_password_rules_and_auth(self):
        with self.assertRaises(ValueError):
            self.store.add_staff("x", "X", "mentor", "short")
        self.store.add_staff("Casey", "Casey", "staff", "long-enough-pw")
        self.assertIsNotNone(self.store.authenticate("casey", "long-enough-pw"))
        self.assertIsNone(self.store.authenticate("casey", "wrong-password"))

    def test_name_only_matching_is_unambiguous(self):
        a = self.store.find_or_create_student("Alex")
        self.assertEqual(self.store.find_or_create_student("alex")["id"], a["id"])
        self.store.find_or_create_student("Alex", "alex2")  # second Alex with username
        self.assertNotEqual(self.store.find_or_create_student("Alex")["id"], a["id"],
                            "ambiguous name must not reuse someone else's profile")


class TestPortal(TempStoreCase):
    def setUp(self):
        super().setUp()
        from gigi.mentor.portal.app import create_app

        os.environ["GIGI_LIMA_SECRET"] = "test-secret"
        self.app = create_app(store=self.store, virtual_console="http://127.0.0.1:1", config=self.config)
        self.client = self.app.test_client()
        self.admin = self.store.add_staff("admin1", "Admin", "admin", "admin-password-1")
        self.mentor = self.store.add_staff("mentor1", "Mentor", "mentor", "mentor-password-1")
        self.other = self.store.add_staff("mentor2", "Other", "mentor", "mentor-password-2")

    def csrf(self):
        self.client.get("/login")
        with self.client.session_transaction() as sess:
            return sess["csrf"]

    def login(self, username, password):
        return self.client.post("/login", data={"username": username, "password": password, "csrf": self.csrf()})

    def test_requires_login_and_csrf(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        r = self.client.post("/login", data={"username": "admin1", "password": "admin-password-1", "csrf": "bad"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.login("admin1", "admin-password-1").status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_lockout_after_failures(self):
        for _ in range(5):
            self.login("admin1", "nope-nope-nope")
        self.assertEqual(self.login("admin1", "admin-password-1").status_code, 429)

    def test_review_saves_profile_and_deletes_transcript(self):
        student = self.store.find_or_create_student("Sam", "samx")
        s = self.store.create_session("first_meeting", student_id=student["id"], status="ended")
        self.store.add_message(s["id"], "student", "I like <script>alert(1)</script> pottery")
        self.store.propose(s["id"], "hobbies", "pottery")
        self.login("mentor1", "mentor-password-1")
        page = self.client.get(f"/conversations/{s['id']}").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", page, "student text must be escaped")
        prop = self.store.proposals(s["id"])[0]
        r = self.client.post(f"/conversations/{s['id']}/review", data={
            "csrf": self.csrf(), "action": "save", f"accept_{prop['id']}": "1", f"value_{prop['id']}": "pottery"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.store.messages(s["id"]), [])
        self.assertEqual(self.store.get_student(student["id"])["profile"]["hobbies"], ["pottery"])
        actions = [a["action"] for a in self.store.audit_log()]
        self.assertIn("view_transcript", actions)

    def test_mentor_cannot_see_other_mentors_students(self):
        student = self.store.find_or_create_student("Taylor", "tay")
        self.store.update_student(student["id"], mentor_id=self.other)
        s = self.store.create_session("weekly_checkin", student_id=student["id"], status="ended")
        self.login("mentor1", "mentor-password-1")
        self.assertEqual(self.client.get(f"/conversations/{s['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"/students/{student['id']}").status_code, 404)
        self.assertEqual(self.client.get("/voice").status_code, 403)
        self.assertEqual(self.client.get("/users").status_code, 403)

    def test_kiosk_start_typed_chat(self):
        with mock.patch("gigi.mentor.launcher.start_checkin", return_value=(True, "ok")) as start:
            r = self.client.post("/student", data={"csrf": self.csrf(), "name": "Morgan", "flow": "weekly_checkin",
                                                   "mode": "type", "rate": "1.4"})
        self.assertEqual(r.status_code, 302)
        start.assert_called_once()
        token = r.headers["Location"].rsplit("/", 1)[-1]
        s = self.store.get_session_by_token(token)
        self.assertEqual((s["mode"], s["rate"]), ("type", 1.4))
        self.client.post(f"/student/api/{token}/say", json={"text": "hello"})
        self.assertEqual(self.store.take_typed(s["id"]), "hello")
        self.client.post(f"/student/api/{token}/settings", json={"rate": 9, "mode": "talk", "muted": True})
        s = self.store.get_session(s["id"])
        self.assertEqual((s["mode"], s["muted"], s["rate"]), ("talk", 1, 1.6))

    def test_kiosk_hides_chat_after_session_ends(self):
        s = self.store.create_session("weekly_checkin", status="ended")
        self.store.add_message(s["id"], "robot", "Hi there")
        self.store.update_session(s["id"], ended=1.0)
        data = self.client.get(f"/student/api/{s['token']}/messages").get_json()
        self.assertEqual(data["messages"], [])

    def test_kiosk_reports_robot_unavailable(self):
        r = self.client.post("/student", data={"csrf": self.csrf(), "name": "Morgan", "flow": "weekly_checkin"})
        self.assertEqual(r.status_code, 503)


class TestVoiceSettings(unittest.TestCase):
    def test_validation_clamps_and_rejects_paths(self):
        from gigi.core.voice_settings import validate

        v = validate({"rate": 5, "pitch_semitones": -10, "voice": "../../etc/passwd", "speaker_id": "12"})
        self.assertEqual(v["rate"], 1.6)
        self.assertEqual(v["pitch_semitones"], -4.0)
        self.assertEqual(v["voice"], "en_US-sam-medium")
        self.assertEqual(v["speaker_id"], 12)

    def test_save_and_tag(self):
        from gigi.core.voice_settings import load_voice_settings, save_voice_settings, voice_tag

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "voice.json"
            save_voice_settings({"rate": 1.25}, path=path)
            s = load_voice_settings(path)
            self.assertEqual(s["rate"], 1.25)
            self.assertEqual(voice_tag(s, "piper"), "en_US-sam-medium-r1.25-p+0.0")

    def test_engine_falls_back_when_piper_missing(self):
        from gigi.core import voice_settings as vs

        with mock.patch.object(vs, "piper_available", return_value=False):
            self.assertEqual(vs.resolve_engine(vs.validate({"engine": "auto"}), "nix"), "nix")


class TestLimaFace(unittest.TestCase):
    def test_lima_is_default_and_frames_exist(self):
        from gigi.core.config import ASSETS_DIR, DEFAULT_FACE
        from gigi.expression.face_definitions import CHARACTERS

        if not os.environ.get("GIGI_FACE"):
            self.assertEqual(DEFAULT_FACE, "lima")
        lima = CHARACTERS["lima"]
        frames = {i for seq in lima["part_sequence"].values() for _, ids in seq for i in ids}
        for i in frames:
            self.assertTrue((ASSETS_DIR / "face" / f"lima_face.{i:03d}.png").exists(), i)
        self.assertEqual(set(lima["part_sequence"]), set(CHARACTERS["fuzzy"]["part_sequence"]))


class TestRouting(unittest.TestCase):
    def test_daemon_routes_mentor_checkins(self):
        from gigi.core.daemon import find_script

        info, err = find_script("mentor_checkin_weekly_checkin")
        self.assertIsNone(err)
        self.assertEqual(info["args"], ["weekly_checkin"])
        self.assertTrue(re.search(r"mentor_checkin\.py$", info["path"]))


if __name__ == "__main__":
    unittest.main()
