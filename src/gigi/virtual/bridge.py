"""
Client-side link between a running activity (robot process) and the Virtual
Robot Console web server.

Every virtual subsystem talks to the console through one shared ``VirtualBridge``.
If the console is not running, the bridge falls back to the terminal: events are
printed and student input is read with ``input()``. That keeps virtual mode usable
for quick command-line runs and for unit tests.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

DEFAULT_CONSOLE_URL = "http://127.0.0.1:5050"

# Default virtual room: one fictional student, centred in front of the robot.
DEFAULT_SCENE: Dict[str, Any] = {
    "people": [
        {
            "id": 1,
            "name": "Alex",
            "present": True,
            "x": 0.5,
            "emotion": "neutral",
            "gesture": "Unknown",
            "note": "",
        }
    ]
}


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


class VirtualBridge:
    """Sends robot events to the console and receives student input from it."""

    def __init__(self, console_url: Optional[str] = None, use_console: Optional[bool] = None):
        self.console_url = (console_url or os.environ.get("GIGI_VIRTUAL_CONSOLE", DEFAULT_CONSOLE_URL)).rstrip("/")
        # Patient listening: wait for the tester to type instead of honouring the
        # robot's microphone timeout. The console has a "Stay silent" button.
        self.patient = _env_flag("GIGI_VIRTUAL_PATIENT", True)
        self.max_wait = float(os.environ.get("GIGI_VIRTUAL_MAX_WAIT", "900"))
        self._session = None
        self._events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._lock = threading.Lock()
        self._local_scene = {"people": [dict(p) for p in DEFAULT_SCENE["people"]]}
        self._local_inputs: List[str] = []

        if use_console is None:
            use_console = self._ping()
        self.connected = bool(use_console)

        if self.connected:
            sender = threading.Thread(target=self._sender_loop, name="virtual-bridge-sender", daemon=True)
            sender.start()
            print(f"[Virtual] Connected to Virtual Robot Console at {self.console_url}")
        else:
            print("[Virtual] Console not reachable; using terminal mode (type the student's replies here).")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _http(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _ping(self) -> bool:
        try:
            r = self._http().get(f"{self.console_url}/api/ping", timeout=0.75)
            return r.ok
        except Exception:
            return False

    def _sender_loop(self):
        while True:
            event = self._events.get()
            try:
                self._http().post(f"{self.console_url}/api/events", json=event, timeout=2)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Events (robot -> console)
    # ------------------------------------------------------------------
    def emit(self, kind: str, **payload: Any) -> None:
        """Queue an event for the console. Never blocks the robot loop."""
        event = {"kind": kind, "t": time.time(), **payload}
        if self.connected:
            self._events.put(event)
        elif kind in ("say", "heard", "listening", "llm", "gesture", "register_face"):
            self._print_event(event)

    @staticmethod
    def _print_event(event: Dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "say":
            print(f"\n[ROBOT SAYS] {event.get('text', '')}")
        elif kind == "heard":
            print(f"[STUDENT] {event.get('text', '')}")
        elif kind == "listening":
            print("[Virtual] Robot is listening...")
        elif kind == "gesture":
            print(f"[Virtual] Gesture: {event.get('name')}")
        elif kind == "llm":
            print(f"[Virtual] LLM ({event.get('source')}): {event.get('reply', '')}")
        elif kind == "register_face":
            print(f"[Virtual] Registered face name: {event.get('name')}")

    def send_face_frame(self, jpeg_bytes: bytes, state: Optional[Dict[str, Any]] = None) -> None:
        if not self.connected:
            return
        headers = {"Content-Type": "image/jpeg"}
        if state:
            headers["X-Face-State"] = ",".join(f"{k}={v}" for k, v in state.items() if v is not None)
        try:
            self._http().post(f"{self.console_url}/api/face_frame", data=jpeg_bytes, headers=headers, timeout=1)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Scene (console -> virtual vision)
    # ------------------------------------------------------------------
    def get_scene(self) -> Dict[str, Any]:
        if not self.connected:
            with self._lock:
                return {"people": [dict(p) for p in self._local_scene["people"]]}
        try:
            r = self._http().get(f"{self.console_url}/api/scene", timeout=1)
            if r.ok:
                return r.json()
        except Exception:
            pass
        return {"people": []}

    def set_local_scene(self, scene: Dict[str, Any]) -> None:
        """Terminal/test mode only: replace the simulated room."""
        with self._lock:
            self._local_scene = {"people": [dict(p) for p in scene.get("people", [])]}

    def rename_person(self, person_id: Any, name: str) -> None:
        if self.connected:
            try:
                self._http().post(
                    f"{self.console_url}/api/scene/rename", json={"id": person_id, "name": name}, timeout=1
                )
            except Exception:
                pass
        else:
            with self._lock:
                for p in self._local_scene["people"]:
                    if str(p.get("id")) == str(person_id):
                        p["name"] = name

    # ------------------------------------------------------------------
    # Student input (console -> virtual hearing)
    # ------------------------------------------------------------------
    def queue_local_input(self, text: str) -> None:
        """Terminal/test mode only: pre-load a student reply."""
        with self._lock:
            self._local_inputs.append(text)

    def wait_for_input(self, stop_event: Optional[threading.Event] = None, prompt: str = "") -> Optional[str]:
        """
        Block until the tester provides the student's reply.

        Returns the text, ``""`` when the tester chose "stay silent", or ``None``
        when the robot's own timeout ended the turn (non-patient mode only).
        """
        deadline = time.time() + self.max_wait

        if not self.connected:
            with self._lock:
                if self._local_inputs:
                    return self._local_inputs.pop(0)
            try:
                return input(f"[STUDENT reply{(' - ' + prompt) if prompt else ''}] > ").strip()
            except EOFError:
                return ""

        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set() and not self.patient:
                return None
            try:
                r = self._http().get(f"{self.console_url}/api/input/next", params={"timeout": 1.0}, timeout=3)
                if r.ok:
                    data = r.json()
                    if data.get("available"):
                        return data.get("text", "")
            except Exception:
                time.sleep(0.5)
        return ""

    # ------------------------------------------------------------------
    # Wizard-of-Oz LLM (console operator writes the robot's reply)
    # ------------------------------------------------------------------
    def request_wizard_reply(self, messages: List[Dict[str, str]]) -> str:
        if not self.connected:
            print("\n[Wizard-of-Oz] The robot needs a reply. Latest prompt:")
            if messages:
                print(messages[-1].get("content", "")[-800:])
            try:
                return input("[ROBOT reply (you are the wizard)] > ").strip()
            except EOFError:
                return ""

        r = self._http().post(f"{self.console_url}/api/llm/request", json={"messages": messages}, timeout=3)
        request_id = r.json()["id"]
        deadline = time.time() + self.max_wait
        while time.time() < deadline:
            try:
                resp = self._http().get(
                    f"{self.console_url}/api/llm/response/{request_id}", params={"timeout": 1.0}, timeout=3
                )
                data = resp.json()
                if data.get("ready"):
                    return data.get("reply", "")
            except Exception:
                time.sleep(0.5)
        return ""


_bridge: Optional[VirtualBridge] = None
_bridge_lock = threading.Lock()


def get_bridge() -> VirtualBridge:
    """Return the process-wide bridge, creating it on first use."""
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = VirtualBridge()
        return _bridge


def set_bridge(bridge: Optional[VirtualBridge]) -> None:
    """Install a specific bridge (used by tests)."""
    global _bridge
    with _bridge_lock:
        _bridge = bridge
