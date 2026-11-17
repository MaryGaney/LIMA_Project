"""
Open-conversation sandbox for the virtual robot.

A minimal loop for trying out conversation designs (for example the LIMA peer-mentor
persona) before they become a full activity: the robot greets, listens, sends the
exchange to the LLM (or the Wizard-of-Oz operator), and speaks the reply.

This is a prototyping tool for the Virtual Robot Console only. It has no referral,
escalation, or data-handling logic and must not be used with real students.

Optional environment variables:
    GIGI_SANDBOX_PROMPT_FILE   path to a text file with the system prompt
    GIGI_SANDBOX_GREETING      first line the robot says
    GIGI_SANDBOX_TURNS         maximum number of exchanges (default 20)
"""

import os
import sys

os.environ.setdefault("GIGI_VIRTUAL", "1")

from gigi.core.config import VIRTUAL_ROBOT  # noqa: E402
from gigi.core.robot import GigiRobot  # noqa: E402
from gigi.interaction.safety_filter import check_behavior  # noqa: E402

DEFAULT_PROMPT = (
    "You are a friendly social robot. Reply in one or two short spoken sentences. "
    "Do not use lists or markdown."
)
END_WORDS = {"bye", "goodbye", "stop", "/end", "end session"}


def load_prompt() -> str:
    path = os.environ.get("GIGI_SANDBOX_PROMPT_FILE")
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return DEFAULT_PROMPT


def main():
    if not VIRTUAL_ROBOT:
        print("sandbox_chat is for the virtual robot only (set GIGI_VIRTUAL=1).")
        sys.exit(1)

    system_prompt = load_prompt()
    greeting = os.environ.get("GIGI_SANDBOX_GREETING", "Hi! I'm a robot. What would you like to talk about?")
    max_turns = int(os.environ.get("GIGI_SANDBOX_TURNS", "20"))

    robot = GigiRobot(wakeup=True, activity="virtual_sandbox")
    history = []
    try:
        robot.run_character(viseme_data={"text": greeting, "file": None}, movement_data="wave_hello")
        history.append({"role": "assistant", "content": greeting})

        for _ in range(max_turns):
            robot.hearing.texts = []
            robot.listen_backchannel(show_camera_feed=False)
            heard = robot.hearing.texts[-1] if robot.hearing.texts else ""
            if not heard:
                robot.run_character(viseme_data={"text": "Take your time. I'm here when you're ready.", "file": None})
                continue
            if heard.lower().strip(" .!") in END_WORDS:
                break

            canned = check_behavior(heard)
            if canned:
                robot.run_character(viseme_data={"text": canned, "file": None})
                continue

            history.append({"role": "user", "content": heard})
            transcript = "\n".join(
                f"{'student' if m['role'] == 'user' else 'robot'}: {m['content']}" for m in history[-8:]
            )
            reply = robot.conversation.get_response(system_prompt, f"{transcript}\nrobot:")
            history.append({"role": "assistant", "content": reply})
            robot.run_character(viseme_data={"text": reply, "file": None})

        robot.run_character(viseme_data={"text": "Thanks for talking with me. Bye for now!", "file": None},
                            movement_data="wave_hello")
    finally:
        if robot.movement:
            robot.movement.release()
        robot.stop_character()


if __name__ == "__main__":
    main()
