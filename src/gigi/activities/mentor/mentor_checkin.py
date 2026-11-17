"""
LIMA one-on-one check-in activity.

    python mentor_checkin.py <checkin_id>      e.g. weekly_checkin (see Assets/mentor/checkins/)

Started by the daemon (`run mentor_checkin_<id>`), the LIMA web app, or the Virtual
Robot Console. If a student started the session from the web app, the robot picks
up that session (name, typing vs talking, speech speed); otherwise it asks for
the student's name by voice.
"""

import sys

from gigi.core.robot import GigiRobot
from gigi.mentor.checkin import CheckinRunner
from gigi.mentor.content import list_flows, load_config, load_flow
from gigi.mentor.robot_io import GigiRobotIO
from gigi.mentor.store import MentorStore

ACTIVITY_NAME = "lima_mentor_session"


def main(argv):
    flows = [f["id"] for f in list_flows()]
    flow_id = argv[0] if argv else "weekly_checkin"
    if flow_id.startswith("mentor_checkin_"):
        flow_id = flow_id[len("mentor_checkin_"):]
    if flow_id not in flows:
        print(f"Unknown check-in '{flow_id}'. Available: {', '.join(flows)}")
        return 2

    store = MentorStore()
    config = load_config()
    store.purge_expired(config.get("retention_days", 14))
    flow = load_flow(flow_id)
    session = store.claim_pending_session(flow_id)

    robot = GigiRobot(wakeup=True, activity=ACTIVITY_NAME)
    io = GigiRobotIO(robot)
    try:
        result = CheckinRunner(io, store, flow, config, session=session).run()
        print(f"[LIMA] Check-in '{flow_id}' finished (session {result.get('id')}, flag {result.get('flag_level')}).")
    finally:
        io.restore()
        if robot.movement:
            robot.movement.release()
        robot.stop_character()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
