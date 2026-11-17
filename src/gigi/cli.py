"""
Main Command Line Interface for Gigi Social Robot.

Commands:
    gigi start        Start the background daemon (Bluetooth + TCP server)
    gigi verify       Run hardware and cognitive subsystem tests
    gigi calibrate    Run interactive motor calibration wizard
    gigi demo         Run demonstration of robot capabilities
    gigi web          Start the web-based Activity Planner
    gigi virtual      Start the Virtual Robot Console (test activities without hardware)
    gigi lima         Start the LIMA web app (student check-ins + mentor/staff portal)
    gigi lima-user    Manage LIMA staff accounts
"""

import sys
import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("gigi.cli")


def cmd_start(args):
    print("Starting Gigi Robot Service...")
    from gigi.core.daemon import main as daemon_main
    daemon_main()


def cmd_verify(args):
    from gigi.verification.cli import run_verification
    targets = args.subsystem if args.subsystem else ["all"]
    success = run_verification(targets)
    sys.exit(0 if success else 1)


def cmd_calibrate(args):
    from gigi.verification.hardware.calibrate_motors import main as calibrate_main
    calibrate_main()


def cmd_web(args):
    print(f"Starting Gigi Web Activity Planner on port {args.port}...")
    from gigi.interaction.web.app import app
    app.run(host=args.host, port=args.port, debug=args.debug)


def cmd_virtual(args):
    import os

    os.environ["GIGI_VIRTUAL"] = "1"
    from gigi.virtual.console.app import main as console_main

    console_main(host=args.host, port=args.port, activity=args.activity, open_browser=not args.no_browser)


def cmd_lima(args):
    from gigi.mentor.portal.app import main as lima_main

    lima_main(host=args.host, port=args.port, virtual=args.virtual)


def cmd_lima_user(args):
    import getpass

    from gigi.mentor.store import MentorStore

    store = MentorStore()
    if args.action == "list":
        for u in store.list_staff():
            print(f"{u['id']:>3}  {u['username']:<20} {u['role']:<7} {'active' if u['active'] else 'deactivated'}")
        return
    if not args.username:
        sys.exit("username is required")
    password = getpass.getpass(f"Password for {args.username} (10+ characters): ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    if args.action == "add":
        store.add_staff(args.username, args.name or args.username, args.role, password)
        print(f"Added {args.role} '{args.username}'.")
    elif args.action == "passwd":
        match = [u for u in store.list_staff() if u["username"] == args.username.lower()]
        if not match:
            sys.exit("No such user.")
        store.set_staff_password(match[0]["id"], password)
        print("Password changed.")


def cmd_demo(args):
    print("Running Gigi Capabilities Demo...")
    from gigi.core.robot import GigiRobot
    robot = GigiRobot(wakeup=True, activity="Demo")
    robot.run_character(
        viseme_data={"text": "Hello! I am Gigi the social robot.", "file": None},
        movement_data="wave_hello",
    )


def main():
    parser = argparse.ArgumentParser(description="Gigi Robot CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # start
    parser_start = subparsers.add_parser("start", help="Start Gigi background daemon")
    parser_start.set_defaults(func=cmd_start)

    # verify
    parser_verify = subparsers.add_parser("verify", help="Verify hardware and AI subsystems")
    parser_verify.add_argument("subsystem", nargs="*", default=["all"], help="Subsystems to verify")
    parser_verify.set_defaults(func=cmd_verify)

    # calibrate
    parser_calib = subparsers.add_parser("calibrate", help="Calibrate robot motors")
    parser_calib.set_defaults(func=cmd_calibrate)

    # web
    parser_web = subparsers.add_parser("web", help="Start web activity planner")
    parser_web.add_argument("--host", default="0.0.0.0", help="Host address")
    parser_web.add_argument("--port", type=int, default=5000, help="Port number")
    parser_web.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser_web.set_defaults(func=cmd_web)

    # virtual
    parser_virtual = subparsers.add_parser("virtual", help="Start the Virtual Robot Console (no hardware needed)")
    parser_virtual.add_argument("--host", default="127.0.0.1", help="Host address (keep localhost)")
    parser_virtual.add_argument("--port", type=int, default=5050, help="Port number")
    parser_virtual.add_argument("--activity", default=None, help="Activity to start immediately, e.g. math_quest")
    parser_virtual.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    parser_virtual.set_defaults(func=cmd_virtual)

    # lima web app
    parser_lima = subparsers.add_parser("lima", help="Start the LIMA web app (check-ins + mentor portal)")
    parser_lima.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 on the robot to serve the LAN")
    parser_lima.add_argument("--port", type=int, default=5080, help="Port number")
    parser_lima.add_argument("--virtual", action="store_true", help="Start check-ins on the Virtual Robot Console")
    parser_lima.set_defaults(func=cmd_lima)

    parser_lu = subparsers.add_parser("lima-user", help="Manage LIMA staff accounts")
    parser_lu.add_argument("action", choices=["add", "passwd", "list"])
    parser_lu.add_argument("username", nargs="?")
    parser_lu.add_argument("--role", choices=["mentor", "staff", "admin"], default="mentor")
    parser_lu.add_argument("--name", help="Display name")
    parser_lu.set_defaults(func=cmd_lima_user)

    # demo
    parser_demo = subparsers.add_parser("demo", help="Run demonstration")
    parser_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
