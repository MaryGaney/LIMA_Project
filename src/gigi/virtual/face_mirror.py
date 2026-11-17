"""
Mirrors the robot's real animated face into the Virtual Robot Console.

The real ``Face`` renderer keeps running unchanged (it also opens its normal
desktop window). This background thread copies each new face image, encodes it
as JPEG, and sends it to the console together with the listening/guidance state.
"""

from __future__ import annotations

import threading
import time

from gigi.virtual.bridge import get_bridge


def start_face_mirror(face, fps: float = 8.0) -> threading.Thread:
    bridge = get_bridge()

    def worker():
        import cv2
        import numpy as np

        last_counter = -1
        last_state = None
        interval = 1.0 / max(fps, 1.0)
        while getattr(face, "face_running", True):
            time.sleep(interval)
            if not bridge.connected:
                continue
            counter = getattr(face, "face_update_counter", 0)
            state = {
                "listening": getattr(face, "feedback_state", None) == "listening",
                "guidance": getattr(face, "guidance", None),
                "showing_image": not getattr(face, "show_face", True),
            }
            if counter == last_counter and state == last_state:
                continue
            image = getattr(face, "last_face_image", None)
            if image is None or not isinstance(image, np.ndarray):
                continue
            try:
                h, w = image.shape[:2]
                scale = 480.0 / max(h, w)
                if scale < 1.0:
                    image = cv2.resize(image, (int(w * scale), int(h * scale)))
                if image.ndim == 3 and image.shape[2] == 4:
                    image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
                ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ok:
                    bridge.send_face_frame(buf.tobytes(), state)
                    last_counter, last_state = counter, state
            except Exception as e:
                print(f"[Virtual] Face mirror error: {e}")
                time.sleep(1.0)

    t = threading.Thread(target=worker, name="virtual-face-mirror", daemon=True)
    t.start()
    return t
