"""
Starts a LIMA check-in on the robot from the web app.

* On the robot: sends ``{"command": "run", "name": "mentor_checkin_<id>"}`` to the
  Gigi daemon's TCP port (default 5005), the same command the mobile app sends.
  The daemon accepts one client at a time, so this fails with "busy" while the
  mobile app is connected.
* In virtual testing (``GIGI_VIRTUAL_CONSOLE`` set or ``--virtual``): asks the
  Virtual Robot Console to run it.
"""

from __future__ import annotations

import json
import os
import socket

from gigi.core.config import DEFAULT_TCP_PORT


def start_checkin(flow_id: str, virtual_console: str | None = None, timeout: float = 5.0) -> tuple[bool, str]:
    target = f"mentor_checkin_{flow_id}"
    console = virtual_console or os.environ.get("GIGI_VIRTUAL_CONSOLE")
    if console:
        try:
            import requests

            r = requests.post(f"{console.rstrip('/')}/api/run", json={"target": target}, timeout=timeout)
            data = r.json()
            return (r.ok, "Started on the virtual robot." if r.ok else data.get("error", "Could not start."))
        except Exception as e:
            return False, f"Virtual Robot Console not reachable at {console} ({e}). Start it with: gigi virtual"

    host = os.environ.get("GIGI_DAEMON_HOST", "127.0.0.1")
    port = int(os.environ.get("GIGI_DAEMON_PORT", DEFAULT_TCP_PORT))
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            reader = sock.makefile("r", encoding="utf-8")
            greeting = json.loads(reader.readline() or "{}")
            if greeting.get("status") == "busy":
                return False, "The robot is connected to another app. Disconnect it or start the check-in from that app."
            sock.sendall((json.dumps({"command": "run", "name": target}) + "\n").encode("utf-8"))
            reply = json.loads(reader.readline() or "{}")
    except OSError as e:
        return False, f"Could not reach the robot daemon at {host}:{port} ({e})."
    if reply.get("status") == "starting":
        return True, "The robot is starting your check-in."
    return False, reply.get("message", "The robot could not start the check-in.")
