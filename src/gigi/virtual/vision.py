"""
Virtual vision (camera + face perception stand-in).

``VirtualVision`` subclasses the real ``Vision`` class and keeps its public API,
face cache, ``last_data`` format, and ``look_for`` logic. Instead of reading a
camera and running face detection, it reads a simulated room from the Virtual
Robot Console: fictional people with a name (or "Unknown"), horizontal position,
emotion, gesture, and whether they are present. It also draws a simple synthetic
camera frame so code that expects frames keeps working.

Face recognition accuracy, lighting, occlusion, and emotion-model errors are not
simulated: perception is always "correct" unless the tester sets it otherwise.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional
from queue import Queue

import numpy as np

from gigi.perception import vision_helper as vh
from gigi.perception.vision import Vision
from gigi.virtual.bridge import get_bridge

FRAME_W, FRAME_H = 640, 480
EMOTION_COLORS = {
    "happy": (80, 200, 80),
    "sad": (200, 120, 60),
    "angry": (60, 60, 220),
    "fear": (180, 80, 180),
    "surprise": (60, 200, 220),
    "neutral": (200, 200, 200),
}


class VirtualFaceDatabase:
    """In-memory face 'database' for virtual runs. Stores names only, never images."""

    def __init__(self):
        self.names = []
        self.bridge = get_bridge()

    def load_db(self):
        return {}

    def save_face_to_db(self, name, encoding=None):
        if name and name not in self.names:
            self.names.append(name)
        self.bridge.emit("register_face", name=name)

    def save_face_img_to_db(self, name, img=None):
        self.save_face_to_db(name)

    def recognize_face_once(self, encoding, tolerance=None):
        return None

    def generate_face_id(self):
        return f"face_{len(self.names) + 1:04d}"


class VirtualVision(Vision):
    POLL_INTERVAL = 0.25

    def __init__(self, camera_source=None, auto_start=False):
        # Do not call Vision.__init__: it loads face/emotion models from Resources/.
        self.camera_source = camera_source
        self.cap = None
        self.is_robot = True  # suppress the extra OpenCV camera window
        self.bridge = get_bridge()

        self.face_db = VirtualFaceDatabase()
        self.emotion_detector = None
        self.face_cache = vh.ImprovedFaceCache()

        self.timestamped_data: Dict[str, Any] = {}
        self.last_data: Dict[str, Any] = {}
        self.face_mesh = None
        self.hands = None
        self.face_queue = Queue()
        self.emotion_queue = Queue()
        self.gesture_queue = Queue()
        self.result_queue = Queue()

        self.stop_event = threading.Event()
        self.capture_thread = None
        self.vision_thread = None
        self.display_thread = None
        self.running = False
        self.processing_flags = {"face_detection": 5, "face_recognition": 5, "emotion": 5, "gesture": 5}
        self.gesture_names = {0: "Unknown", 1: "Thumbs Up", 2: "Thumbs Down"}
        self.hand_gesture = "Unknown"
        self.active_workers = {"face_recognition": False, "emotion": False, "gesture": False}
        self.last_process_time = {k: 0 for k in self.processing_flags}
        self.threads = []
        self.latest_frame = None
        self.raw_frame = None
        self.frame_lock = threading.Lock()

        self._scene_names: Dict[int, str] = {}
        self._scene: Dict[str, Any] = {"people": []}
        print("Initializing virtual vision (simulated room in the Virtual Robot Console) ...")
        if auto_start:
            self.run_vision()

    # ------------------------------------------------------------------
    # Scene -> face cache
    # ------------------------------------------------------------------
    def sync_scene(self, scene: Optional[Dict[str, Any]] = None) -> None:
        """Apply the simulated room to the face cache and ``last_data``."""
        scene = scene if scene is not None else self.bridge.get_scene()
        self._scene = scene
        now = time.time()
        present_ids = set()
        processing = self.processing_flags.get("face_detection", 5) != 0

        with self.face_cache.lock:
            for person in scene.get("people", []):
                if not person.get("present", True) or not processing:
                    continue
                pid = int(person.get("id", 0))
                present_ids.add(pid)
                x = int(float(person.get("x", 0.5)) * FRAME_W)
                y = int(FRAME_H * 0.42)
                half = 60
                box = (x - half, y - half, x + half, y + half)
                scene_name = person.get("name") or "Unknown"
                entry = self.face_cache.faces.get(pid)
                if entry is None:
                    entry = {
                        "name": scene_name,
                        "last_recognition": now,
                        "last_emotion": now,
                        "recognition_attempted": True,
                        "is_new": scene_name == "Unknown",
                    }
                    self.face_cache.faces[pid] = entry
                    self._scene_names[pid] = scene_name
                elif self._scene_names.get(pid) != scene_name:
                    # The tester renamed this person in the console.
                    entry["name"] = scene_name
                    self._scene_names[pid] = scene_name
                entry.update(
                    {
                        "x": x,
                        "y": y,
                        "box": box,
                        "position_key": self.face_cache.get_position_key(x, y),
                        "emotion": person.get("emotion") or "Unknown",
                        "gesture": person.get("gesture") or "Unknown",
                        "last_seen": now,
                    }
                )
            for pid in list(self.face_cache.faces):
                if pid not in present_ids:
                    del self.face_cache.faces[pid]
            self.face_cache.next_id = max([self.face_cache.next_id] + [p + 1 for p in present_ids])

        for key in list(self.last_data):
            if int(key) not in present_ids:
                del self.last_data[key]
        vh.VisionHelpers.update_data_structure(self.face_cache, self.last_data, self.timestamped_data, FRAME_W, FRAME_H)
        if len(self.timestamped_data) > 600:
            for k in list(self.timestamped_data)[:300]:
                del self.timestamped_data[k]

        self._push_names_to_console()

    def _push_names_to_console(self):
        """If an activity named an Unknown person (e.g. Make Friends), mirror it to the console."""
        for pid, entry in self.face_cache.get_all_faces().items():
            name = entry.get("name")
            if name and name not in ("Unknown", "Recognizing...") and self._scene_names.get(pid) == "Unknown":
                self._scene_names[pid] = name
                self.bridge.rename_person(pid, name)

    # ------------------------------------------------------------------
    # Synthetic camera frame
    # ------------------------------------------------------------------
    def render_frame(self) -> np.ndarray:
        import cv2

        frame = np.full((FRAME_H, FRAME_W, 3), (48, 40, 36), dtype=np.uint8)
        cv2.putText(frame, "VIRTUAL CAMERA", (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (160, 160, 160), 1)
        for data in self.face_cache.get_all_faces().values():
            x1, y1, x2, y2 = [int(v) for v in data.get("box", (0, 0, 0, 0))]
            color = EMOTION_COLORS.get(str(data.get("emotion", "")).lower(), (200, 200, 200))
            cv2.circle(frame, ((x1 + x2) // 2, (y1 + y2) // 2), (x2 - x1) // 2, color, 2)
            label = f"{data.get('name', '?')} ({data.get('emotion', '?')})"
            cv2.putText(frame, label, (x1 - 10, y2 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        return frame

    # ------------------------------------------------------------------
    # Loops replacing camera capture and model inference
    # ------------------------------------------------------------------
    def _capture_loop(self):
        while not self.stop_event.is_set():
            try:
                frame = self.render_frame()
                with self.frame_lock:
                    self.latest_frame = frame
                    self.raw_frame = frame
            except Exception as e:
                print(f"[VirtualVision] Frame render error: {e}")
            time.sleep(0.1)

    def _vision_loop(self):
        while not self.stop_event.is_set():
            try:
                self.sync_scene()
            except Exception as e:
                print(f"[VirtualVision] Scene sync error: {e}")
            time.sleep(self.POLL_INTERVAL)

    def _display_loop(self):
        return

    def display_loop(self):
        return

    def run_vision(self, show_window=False):
        if not self.running:
            self.sync_scene()
        super().run_vision(show_window=False)

    def cleanup(self):
        self.stop_vision()
