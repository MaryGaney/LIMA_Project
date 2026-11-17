"""
Runs a LIMA one-on-one check-in script (Assets/mentor/checkins/*.json).

Step types:
  say        {"text"}                               robot says a line
  ask        {"text", "kind", "save_as", "acks", "branches", "if_profile", "unless_profile", "for_mentor"}
             kind: text | list | scale (1-5 -> low/mid/high) | yesno
             save_as fields listed in store.PROFILE_FIELDS become profile proposals for staff review
  resources  {"topics": [...]} or {"from_answer": "<save_as>"}
  events     shares upcoming events from mentor_config.json
  flag       {"level": note|followup|urgent, "reason"}   (reason must not quote the student)

Text can use {name}, {profile.<field>}, and {answer.<save_as>}.
Students can say or type "skip" for any question and "stop" to end.

The robot is reached through a small I/O object (``say``, ``listen``, ``set_rate``), so
the same runner drives the real robot, the virtual robot, and unit tests.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Protocol

from gigi.mentor import safety
from gigi.mentor.store import PROFILE_FIELDS, MentorStore

STOP_WORDS = {"stop", "end", "quit", "/end", "end session", "stop please", "i want to stop"}
SKIP_WORDS = {"skip", "pass", "/skip", "skip it", "next", "i'd rather not say", "rather not say"}
SCALE_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
YES = re.compile(r"\b(yes|yeah|yep|sure|ok|okay|of course|definitely|please|y)\b", re.I)
NO = re.compile(r"\b(no|nope|nah|not really|don'?t|n)\b", re.I)


NOTHING_FOR_MENTOR = re.compile(r"\s*(no|nope|nothing|nah|not really|i'?m good|all good)\b", re.I)


class EndSession(Exception):
    pass


class RobotIO(Protocol):
    def say(self, text: str, muted: bool = False) -> None: ...
    def listen(self) -> str: ...
    def set_rate(self, rate: float) -> None: ...


def join_list(items: Any) -> str:
    if isinstance(items, str):
        return items
    items = [str(i) for i in items or []]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def parse_scale(text: str) -> int | None:
    m = re.search(r"\b([1-5])\b", text)
    if m:
        return int(m.group(1))
    for word, value in SCALE_WORDS.items():
        if re.search(rf"\b{word}\b", text, re.I):
            return value
    return None


def parse_yesno(text: str) -> bool | None:
    if NO.search(text) and not re.search(r"\bwhy not\b", text, re.I):
        return False
    if YES.search(text):
        return True
    return None


class CheckinRunner:
    def __init__(self, io: RobotIO, store: MentorStore, flow: dict[str, Any], config: dict[str, Any],
                 session: dict[str, Any] | None = None, typed_timeout: float = 900.0, poll: float = 0.3,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.io, self.store, self.flow, self.config = io, store, flow, config
        self.session = session
        self.typed_timeout, self.poll = typed_timeout, poll
        self.clock, self.sleep = clock, sleep
        self.student: dict[str, Any] | None = None
        self.answers: dict[str, str] = {}
        self._ack_index = 0
        self._rate = None

    # ------------------------------------------------------------------
    # Talking and listening
    # ------------------------------------------------------------------
    def _refresh(self) -> dict[str, Any]:
        self.session = self.store.get_session(self.session["id"]) or self.session
        rate = float(self.session.get("rate") or 1.0)
        if rate != self._rate:
            self._rate = rate
            self.io.set_rate(rate)
        return self.session

    def render(self, text: str) -> str:
        profile = (self.student or {}).get("profile", {})

        def sub(m: re.Match) -> str:
            key = m.group(1)
            if key == "name":
                return (self.student or {}).get("preferred_name", "friend")
            if key.startswith("profile."):
                return join_list(profile.get(key[8:], ""))
            if key.startswith("answer."):
                return self.answers.get(key[7:], "")
            return m.group(0)

        return re.sub(r"\{([\w.]+)\}", sub, text)

    def say(self, text: str) -> None:
        text = self.render(text)
        session = self._refresh()
        self.store.add_message(session["id"], "robot", text)
        self.io.say(text, muted=bool(session.get("muted")))

    def get_reply(self) -> str | None:
        """Next student reply (typed or spoken). Returns None for skip; raises EndSession for stop."""
        session = self._refresh()
        text = self.store.take_typed(session["id"])
        channel = "typed"
        if text is None and session["mode"] == "type":
            deadline = self.clock() + self.typed_timeout
            while text is None and self.clock() < deadline:
                self.sleep(self.poll)
                session = self._refresh()
                if session["status"] == "ended":
                    raise EndSession()
                text = self.store.take_typed(session["id"])
                if text is None and session["mode"] == "talk":
                    break
            if text is None and session["mode"] == "type":
                self.say("I'll stop here for now since I haven't heard back. You can start a new check-in any time.")
                raise EndSession()
        if text is None:
            text, channel = (self.io.listen() or "").strip(), "voice"
            if not text:
                text = self.store.take_typed(session["id"])
                channel = "typed"
        text = (text or "").strip()
        if text:
            self.store.add_message(session["id"], "student", text, channel=channel)
        lowered = text.lower().strip(" .!")
        if lowered in STOP_WORDS:
            raise EndSession()
        self._triage(text)
        if not text or lowered in SKIP_WORDS:
            return None
        return text

    def ask(self, question: str) -> str | None:
        self.say(question)
        return self.get_reply()

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------
    def _triage(self, text: str) -> None:
        result = safety.assess(text)
        if result.level == "urgent":
            self.store.flag_session(self.session["id"], "urgent", result.reason)
            for line in self.config["crisis"]["message"]:
                self.say(line)
            self.say(self.config["crisis"]["after"])
            self.get_reply_raw()
            self.say("Okay. A staff member has been notified. You can also go to the LLC front desk "
                     "or call or text 988 any time.")
            raise EndSession(True)
        if result.level == "followup":
            self.store.flag_session(self.session["id"], "followup", result.reason)
            self.say(self.config["followup_message"])
            self._share_resources(self._match_resources(text), limit=1)

    def get_reply_raw(self) -> str:
        """A reply without further triage (used right after crisis resources)."""
        session = self._refresh()
        text = self.store.take_typed(session["id"])
        if text is None and session["mode"] != "type":
            text = self.io.listen()
        text = (text or "").strip()
        if text:
            self.store.add_message(session["id"], "student", text)
        return text

    # ------------------------------------------------------------------
    # Resources and events
    # ------------------------------------------------------------------
    def _match_resources(self, text: str) -> list[str]:
        text = text.lower()
        return [key for key, r in self.config["resources"].items() if any(k in text for k in r["keywords"])]

    def _share_resources(self, keys: list[str], limit: int = 2) -> None:
        for key in keys[:limit]:
            self.say(self.config["resources"][key]["text"])

    def step_resources(self, step: dict[str, Any]) -> None:
        keys = list(step.get("topics") or [])
        if step.get("from_answer"):
            keys = self._match_resources(self.answers.get(step["from_answer"], ""))
            if not keys and self.answers.get(step["from_answer"]):
                self.say("I'm not sure about that one, and I don't want to guess. Your Peer Mentor or the LLC "
                         "front desk will know. I'll note your question so they can follow up.")
                self.store.flag_session(self.session["id"], "note", "Student asked a question LIMA could not answer.")
                return
        self._share_resources(keys)

    def step_events(self) -> None:
        events = self.config.get("events", [])[:3]
        if events:
            self.say("Here are a few things coming up in the LLC.")
            for e in events:
                self.say(re.sub(r"^SAMPLE:\s*", "", e))

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------
    def _ack(self, step: dict[str, Any]) -> None:
        acks = step.get("acks") or []
        if acks:
            self.say(acks[self._ack_index % len(acks)])
            self._ack_index += 1

    def step_ask(self, step: dict[str, Any]) -> None:
        profile = (self.student or {}).get("profile", {})
        if step.get("if_profile") and not profile.get(step["if_profile"]):
            return
        if step.get("unless_profile") and profile.get(step["unless_profile"]):
            return
        kind = step.get("kind", "text")
        answer = self.ask(step["text"])
        branch_key = None
        if answer and kind == "scale":
            value = parse_scale(answer)
            if value is None:
                answer = self.ask("Could you give me a number from one to five?")
                value = parse_scale(answer or "")
            if value is not None:
                branch_key = "low" if value <= 2 else "mid" if value == 3 else "high"
        elif answer and kind == "yesno":
            value = parse_yesno(answer)
            branch_key = None if value is None else ("yes" if value else "no")
        if answer is None:
            self.say("No problem, we can skip that one.")
            return
        save_as = step.get("save_as")
        if save_as:
            self.answers[save_as] = answer
            if save_as in PROFILE_FIELDS:
                self.store.propose(self.session["id"], save_as, answer)
        if step.get("for_mentor"):
            if re.match(NOTHING_FOR_MENTOR, answer):
                self.say("Okay! You can always reach out to your Peer Mentor directly, too.")
                return
            self.store.flag_session(self.session["id"], "note", "Student left a message for their Peer Mentor.")
        branches = step.get("branches") or {}
        if branch_key and branch_key in branches:
            self.run_steps(branches[branch_key])
        else:
            self._ack(step)

    def run_steps(self, steps: list[dict[str, Any]]) -> None:
        for step in steps:
            kind = step.get("type")
            if kind == "say":
                self.say(step["text"])
            elif kind == "ask":
                self.step_ask(step)
            elif kind == "resources":
                self.step_resources(step)
            elif kind == "events":
                self.step_events()
            elif kind == "flag":
                self.store.flag_session(self.session["id"], step.get("level", "note"), step.get("reason", ""))

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def _identify(self) -> None:
        if self.session and self.session.get("student_id"):
            self.student = self.store.get_student(self.session["student_id"])
            return
        if self.session is None:
            self.session = self.store.create_session(self.flow["id"], status="active")
            self.store.update_session(self.session["id"], started=self.clock())
        name = None
        for _ in range(2):
            name = self.ask(f"Hi! I'm {self.config.get('robot_name', 'LIMA')}. What name would you like me to call you?")
            if name:
                break
        name = re.sub(r"^(my name is|i'?m|i am|call me|it'?s)\s+", "", (name or "friend").strip(), flags=re.I)
        self.student = self.store.find_or_create_student(name.split(".")[0][:40])
        self.store.update_session(self.session["id"], student_id=self.student["id"])

    def _consent(self) -> bool:
        for line in self.config["disclosure"]:
            self.say(line)
        for _ in range(2):
            answer = self.ask(self.config["consent_question"])
            if answer is None:
                continue
            value = parse_yesno(answer)
            if value is not None:
                return value
            self.say("Sorry, I didn't catch that. Please say yes or no.")
        return False

    def _greet(self) -> None:
        profile = self.student.get("profile", {})
        name = self.student["preferred_name"]
        if profile.get("hobbies") or profile.get("classes"):
            memory = (f" Last time you told me you enjoy {join_list(profile['hobbies'])}."
                      if profile.get("hobbies") else
                      f" Last time you mentioned taking {join_list(profile['classes'])}.")
            self.say(f"Welcome back, {name}!{memory}")
        else:
            self.say(f"Hi {name}, thanks for stopping by.")

    def run(self) -> dict[str, Any]:
        session_id = self.session["id"] if self.session else None
        try:
            self._identify()
            session_id = self.session["id"]
            self.store.update_session(session_id, status="active")
            if not self._consent():
                self.say(self.config["declined"])
                self.store.update_session(session_id, consent=0, status="declined", ended=self.clock())
                self.store.delete_transcript(session_id, actor="robot", reason="student declined")
                return self.store.get_session(session_id)
            self.store.update_session(session_id, consent=1)
            self._greet()
            self.run_steps(self.flow.get("steps", []))
            self.say(self.config["goodbye"])
        except EndSession as e:
            session_id = session_id or (self.session or {}).get("id")
            if session_id is not None and not (e.args and e.args[0]):
                self.say("Okay, we can stop here. Thanks for talking with me.")
        finally:
            if session_id is not None:
                final = self.store.get_session(session_id)
                if final and final["status"] == "active":
                    self.store.update_session(session_id, status="ended", ended=self.clock())
                if self.student:
                    s = self.store.get_session(session_id)
                    self.store.set_pref(self.student["id"], "input_mode", s["mode"])
                    self.store.set_pref(self.student["id"], "speech_rate", s["rate"])
                    self.store.set_pref(self.student["id"], "muted", bool(s["muted"]))
                    with self.store.connect() as db:
                        db.execute("UPDATE students SET last_seen = ? WHERE id = ?", (self.clock(), self.student["id"]))
        return self.store.get_session(session_id) if session_id else {}
