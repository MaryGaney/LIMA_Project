"""
Virtual Robot Console: a local web page for running Gigi activities without the robot.

The console launches an activity as a subprocess with ``GIGI_VIRTUAL=1`` (just like
the daemon does on the robot) and becomes that process's eyes, ears, and body:

* the robot's speech appears as captions (optionally voiced by the browser),
* the tester types the student's replies,
* a simulated room supplies the people the camera "sees",
* servo commands are drawn on a joint view, and
* the real animated face is mirrored in the page.

Run with ``gigi virtual`` and open http://127.0.0.1:5050. Bind to localhost only.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, render_template, request

from gigi.core.config import PROJECT_ROOT
from gigi.virtual.bridge import DEFAULT_SCENE

SANDBOX_TARGET = "virtual_sandbox_chat"
MAX_EVENTS = 5000


class ConsoleState:
    """All console state lives in memory; nothing is written to disk unless exported."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.events: List[Dict[str, Any]] = []
        self.seq = itertools.count(1)
        self.inputs: deque = deque()
        self.scene: Dict[str, Any] = json.loads(json.dumps(DEFAULT_SCENE))
        self.face_jpeg: Optional[bytes] = None
        self.face_state: Dict[str, str] = {}
        self.face_version = 0
        self.llm_requests: Dict[str, Dict[str, Any]] = {}
        self.process: Optional[subprocess.Popen] = None
        self.process_name: Optional[str] = None

    # events --------------------------------------------------------------
    def add_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        with self.cond:
            event = dict(event)
            event["id"] = next(self.seq)
            event.setdefault("t", time.time())
            self.events.append(event)
            if len(self.events) > MAX_EVENTS:
                self.events = self.events[-MAX_EVENTS // 2 :]
            self.cond.notify_all()
            return event

    def events_since(self, since: int) -> List[Dict[str, Any]]:
        with self.lock:
            return [e for e in self.events if e["id"] > since]


def create_app(state: Optional[ConsoleState] = None, launch_env: Optional[Dict[str, str]] = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder=None)
    app.config["CONSOLE_STATE"] = state or ConsoleState()
    st: ConsoleState = app.config["CONSOLE_STATE"]
    extra_env = dict(launch_env or {})

    # ------------------------------------------------------------------
    # Page
    # ------------------------------------------------------------------
    @app.route("/")
    def index():
        return render_template("console.html")

    @app.route("/api/ping")
    def ping():
        return jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Robot -> console events
    # ------------------------------------------------------------------
    @app.route("/api/events", methods=["POST"])
    def post_event():
        event = request.get_json(silent=True) or {}
        if "kind" not in event:
            return jsonify({"error": "kind is required"}), 400
        st.add_event(event)
        return jsonify({"ok": True})

    @app.route("/api/events", methods=["GET"])
    def get_events():
        since = int(request.args.get("since", 0))
        return jsonify({"events": st.events_since(since)})

    @app.route("/api/face_frame", methods=["POST"])
    def post_face_frame():
        with st.lock:
            st.face_jpeg = request.get_data()
            st.face_version += 1
            raw = request.headers.get("X-Face-State", "")
            st.face_state = dict(kv.split("=", 1) for kv in raw.split(",") if "=" in kv)
        return jsonify({"ok": True})

    @app.route("/api/face_frame", methods=["GET"])
    def get_face_frame():
        with st.lock:
            data = st.face_jpeg
        if not data:
            return Response(status=204)
        return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.route("/api/face_state")
    def get_face_state():
        with st.lock:
            return jsonify({"version": st.face_version, "state": st.face_state})

    # ------------------------------------------------------------------
    # Simulated room
    # ------------------------------------------------------------------
    @app.route("/api/scene", methods=["GET"])
    def get_scene():
        with st.lock:
            return jsonify(st.scene)

    @app.route("/api/scene", methods=["PUT", "POST"])
    def put_scene():
        scene = request.get_json(silent=True) or {}
        people = scene.get("people")
        if not isinstance(people, list):
            return jsonify({"error": "people list is required"}), 400
        cleaned = []
        for i, p in enumerate(people, start=1):
            cleaned.append(
                {
                    "id": int(p.get("id", i)),
                    "name": str(p.get("name") or "Unknown")[:40],
                    "present": bool(p.get("present", True)),
                    "x": min(max(float(p.get("x", 0.5)), 0.0), 1.0),
                    "emotion": str(p.get("emotion") or "neutral")[:20],
                    "gesture": str(p.get("gesture") or "Unknown")[:20],
                    "note": str(p.get("note") or "")[:200],
                }
            )
        with st.lock:
            st.scene = {"people": cleaned}
        st.add_event({"kind": "scene", "people": cleaned})
        return jsonify(st.scene)

    @app.route("/api/scene/rename", methods=["POST"])
    def rename_person():
        data = request.get_json(silent=True) or {}
        with st.lock:
            for p in st.scene["people"]:
                if str(p["id"]) == str(data.get("id")):
                    p["name"] = str(data.get("name", p["name"]))[:40]
            people = list(st.scene["people"])
        st.add_event({"kind": "scene", "people": people, "by": "robot"})
        return jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Student input (tester -> robot hearing)
    # ------------------------------------------------------------------
    @app.route("/api/input", methods=["POST"])
    def post_input():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", ""))
        with st.cond:
            st.inputs.append(text)
            st.cond.notify_all()
        return jsonify({"ok": True, "queued": len(st.inputs)})

    @app.route("/api/input/next")
    def next_input():
        timeout = min(float(request.args.get("timeout", 1.0)), 10.0)
        deadline = time.time() + timeout
        with st.cond:
            while not st.inputs:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return jsonify({"available": False})
                st.cond.wait(remaining)
            return jsonify({"available": True, "text": st.inputs.popleft()})

    # ------------------------------------------------------------------
    # Wizard-of-Oz LLM
    # ------------------------------------------------------------------
    @app.route("/api/llm/request", methods=["POST"])
    def llm_request():
        data = request.get_json(silent=True) or {}
        rid = uuid.uuid4().hex[:12]
        with st.cond:
            st.llm_requests[rid] = {"id": rid, "messages": data.get("messages", []), "ready": False, "reply": None}
        st.add_event({"kind": "llm_request", "request_id": rid, "messages": data.get("messages", [])})
        return jsonify({"id": rid})

    @app.route("/api/llm/response/<rid>")
    def llm_response(rid):
        timeout = min(float(request.args.get("timeout", 1.0)), 10.0)
        deadline = time.time() + timeout
        with st.cond:
            while True:
                req = st.llm_requests.get(rid)
                if req is None:
                    return jsonify({"error": "unknown request"}), 404
                if req["ready"]:
                    st.llm_requests.pop(rid, None)
                    return jsonify({"ready": True, "reply": req["reply"]})
                remaining = deadline - time.time()
                if remaining <= 0:
                    return jsonify({"ready": False})
                st.cond.wait(remaining)

    @app.route("/api/llm/reply", methods=["POST"])
    def llm_reply():
        data = request.get_json(silent=True) or {}
        rid = data.get("id")
        with st.cond:
            req = st.llm_requests.get(rid)
            if req is None:
                return jsonify({"error": "unknown request"}), 404
            req["reply"] = str(data.get("reply", ""))
            req["ready"] = True
            st.cond.notify_all()
        st.add_event({"kind": "llm_answered", "request_id": rid})
        return jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Activity launching
    # ------------------------------------------------------------------
    @app.route("/api/activities")
    def list_activities():
        from gigi.core.daemon import scan_activity_plans, scan_custom_interactions, scan_files

        demos, scripts, _ = scan_files()
        seen, core = set(), []
        for info in sorted(demos.values(), key=lambda x: x["stem"]):
            if info["filename"] in seen or info["stem"] in ("calibrate_motors", "mentor_checkin"):
                continue  # calibration drives real servos; never offer it here
            seen.add(info["filename"])
            rel = os.path.relpath(info["dir"], os.path.join(str(PROJECT_ROOT), "src", "gigi", "activities"))
            core.append({"target": info["stem"], "label": info["stem"].replace("_", " ").title(),
                         "group": rel.replace("\\", "/").split("/")[0]})
        return jsonify(
            {
                "sandbox": [{"target": SANDBOX_TARGET, "label": "Open conversation sandbox (prompt testing)"}],
                "core": core,
                "activity_plans": [{"target": p["folder"], "label": p["title"]} for p in scan_activity_plans()],
                "custom_interactions": [
                    {"target": c["folder"], "label": c["title"]} for c in scan_custom_interactions()
                ],
                "mentor_checkins": _mentor_checkins(),
            }
        )

    def _mentor_checkins():
        try:
            from gigi.mentor.content import list_flows

            return [{"target": f"mentor_checkin_{f['id']}", "label": f["title"]} for f in list_flows()]
        except Exception:
            return []

    def _resolve(target: str) -> Optional[Dict[str, Any]]:
        if target == SANDBOX_TARGET:
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sandbox_chat.py")
            return {"path": path, "dir": os.path.dirname(path), "filename": "sandbox_chat.py"}
        from gigi.core.daemon import find_script

        info, _ = find_script(target)
        if info and "calibrat" in info.get("stem", ""):
            return None
        return info

    def _pump_output(proc: subprocess.Popen, name: str):
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip()
            if line:
                st.add_event({"kind": "log", "text": line[:2000]})
        code = proc.wait()
        st.add_event({"kind": "activity_end", "name": name, "code": code})
        with st.lock:
            if st.process is proc:
                st.process = None
                st.process_name = None

    @app.route("/api/run", methods=["POST"])
    def run_activity():
        data = request.get_json(silent=True) or {}
        target = str(data.get("target", "")).strip()
        info = _resolve(target)
        if not info:
            return jsonify({"error": f"Activity '{target}' not found"}), 404
        _stop_process()
        with st.cond:
            st.inputs.clear()
            st.llm_requests.clear()

        host = request.host
        env = dict(os.environ)
        env.update(extra_env)
        env.update(
            {
                "GIGI_VIRTUAL": "1",
                "GIGI_VIRTUAL_CONSOLE": f"http://{host}",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
            }
        )
        if data.get("llm_mode") in ("auto", "real", "woz"):
            env["GIGI_VIRTUAL_LLM"] = data["llm_mode"]
        if data.get("speech_mode") in ("captions", "real"):
            env["GIGI_VIRTUAL_SPEECH"] = data["speech_mode"]
        src_dir = str(PROJECT_ROOT / "src")
        env["PYTHONPATH"] = src_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

        cmd = [sys.executable, "-u", info["path"]] + list(info.get("args", []))
        proc = subprocess.Popen(
            cmd,
            cwd=info["dir"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with st.lock:
            st.process = proc
            st.process_name = target
        st.add_event({"kind": "activity_start", "name": target, "pid": proc.pid})
        threading.Thread(target=_pump_output, args=(proc, target), daemon=True).start()
        return jsonify({"ok": True, "pid": proc.pid})

    def _stop_process():
        with st.lock:
            proc = st.process
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    @app.route("/api/stop", methods=["POST"])
    def stop_activity():
        _stop_process()
        return jsonify({"ok": True})

    @app.route("/api/status")
    def status():
        with st.lock:
            running = st.process is not None and st.process.poll() is None
            pending = [r["id"] for r in st.llm_requests.values() if not r["ready"]]
            return jsonify({"running": running, "activity": st.process_name if running else None,
                            "queued_inputs": len(st.inputs), "pending_llm": pending})

    # ------------------------------------------------------------------
    # Transcript export / reset
    # ------------------------------------------------------------------
    @app.route("/api/transcript")
    def transcript():
        with st.lock:
            rows = [
                {k: e.get(k) for k in ("t", "kind", "text", "source", "reply", "name", "people", "silent")}
                for e in st.events
                if e["kind"] in ("say", "heard", "llm", "gesture", "scene", "activity_start", "activity_end",
                                 "register_face")
            ]
        body = json.dumps({"note": "Virtual robot session (synthetic test data)", "events": rows}, indent=2)
        return Response(body, mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=virtual_session.json"})

    @app.route("/api/reset", methods=["POST"])
    def reset():
        with st.cond:
            st.events.clear()
            st.inputs.clear()
        return jsonify({"ok": True})

    return app


def main(host: str = "127.0.0.1", port: int = 5050, activity: Optional[str] = None, open_browser: bool = True):
    app = create_app()
    url = f"http://{host}:{port}"
    print(f"Virtual Robot Console running at {url}")
    if host not in ("127.0.0.1", "localhost"):
        print("WARNING: the console is reachable from other machines on this network.")
    if activity:
        def _autostart():
            time.sleep(1.0)
            try:
                import requests

                requests.post(f"{url}/api/run", json={"target": activity}, timeout=5)
            except Exception as e:
                print(f"Could not auto-start '{activity}': {e}")

        threading.Thread(target=_autostart, daemon=True).start()
    if open_browser:
        try:
            import webbrowser

            threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
