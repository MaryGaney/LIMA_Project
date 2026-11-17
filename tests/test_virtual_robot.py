"""
Unit tests for the virtual robot (gigi.virtual) and the Virtual Robot Console API.

These tests use the bridge in terminal mode (no console server), so they need no
display, microphone, camera, speakers, LLM, or robot hardware.
"""

import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from gigi.virtual.bridge import VirtualBridge, set_bridge  # noqa: E402


def offline_bridge():
    bridge = VirtualBridge(use_console=False)
    set_bridge(bridge)
    return bridge


class TestVirtualConfig(unittest.TestCase):
    def test_virtual_mode_forces_workstation_and_virtual_movement(self):
        env = dict(os.environ, GIGI_VIRTUAL="1", GIGI_IS_ROBOT="true", PYTHONPATH=SRC_DIR)
        code = (
            "from gigi.core import config as c;"
            "print(c.VIRTUAL_ROBOT, c.IS_ROBOT, c.HAS_MOVEMENT, c.USE_NPU_TRANSCRIPTION)"
        )
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.split(), ["True", "False", "True", "False"])


class TestVirtualMovement(unittest.TestCase):
    def setUp(self):
        self.bridge = offline_bridge()

    def test_uses_virtual_controller_not_i2c(self):
        from gigi.virtual.movement import VirtualMotorController, VirtualMovement

        m = VirtualMovement()
        self.assertIsInstance(m.motors, VirtualMotorController)
        self.assertTrue(m.is_calibrated)
        self.assertEqual(m.calc_normalized_angle("neck"), 0.0)

    def test_real_gesture_trajectory_is_recorded(self):
        from gigi.virtual.movement import VirtualMovement

        m = VirtualMovement()
        t = m.movement_thread("wave_hello")
        t.start()
        t.join(timeout=5)
        peak = max(state["right_shoulder"] for _, state in m.history)
        self.assertGreater(peak, 0.7, "wave_hello should raise the right shoulder")
        self.assertIn(2, m.motors.pwm, "right_shoulder channel should have been commanded")

    def test_release_marks_servos_released(self):
        from gigi.virtual.movement import VirtualMovement

        m = VirtualMovement()
        m.move_motors({"neck": 0.5})
        self.assertAlmostEqual(m.calc_normalized_angle("neck"), 0.5, places=1)
        m.release()
        self.assertTrue(m.motors.released)


class TestVirtualHearingAndSpeech(unittest.TestCase):
    def setUp(self):
        self.bridge = offline_bridge()

    def test_typed_input_becomes_transcript(self):
        from gigi.virtual.hearing import VirtualHearing

        hearing = VirtualHearing()
        face = mock.Mock()
        hearing.face = face
        self.bridge.queue_local_input("my favorite color is blue")
        stop = threading.Event()
        t = hearing.hearing_thread(stop_event=stop)
        t.start()
        t.join(timeout=5)
        self.assertEqual(hearing.texts, ["my favorite color is blue"])
        self.assertTrue(stop.is_set())
        self.assertIsNone(face.feedback_state)

    def test_silence_produces_no_transcript(self):
        from gigi.virtual.hearing import VirtualHearing

        hearing = VirtualHearing()
        self.bridge.queue_local_input("")
        hearing.run_hearing()
        self.assertEqual(hearing.texts, [])

    def test_real_tts_failure_falls_back_to_captions(self):
        from gigi.expression import speech as real_speech
        from gigi.virtual.speech import VirtualSpeech, make_speech

        with mock.patch.dict(os.environ, {"GIGI_VIRTUAL_SPEECH": "real"}), \
                mock.patch.object(real_speech.Speech, "__init__", side_effect=AttributeError("no torch")):
            speech = make_speech(child=False, gender="female", languages=["en"])
        self.assertIsInstance(speech, VirtualSpeech)

    def test_speech_caption_timing_and_envelope(self):
        from gigi.virtual.speech import VirtualSpeech, estimate_duration

        speech = VirtualSpeech()
        key = speech.update_audio_objects(text="Hello there friend")
        obj = speech.audio_objects[key]
        self.assertAlmostEqual(obj["duration"], estimate_duration("Hello there friend"))
        self.assertEqual(len(obj["envelope"]), int(-(-obj["duration"] // speech.sample_rate)))
        start = [None]
        stop = threading.Event()
        t0 = time.time()
        speech.play_audio(key, stop_event=stop, start_time_container=start, stop_condition="audio")
        self.assertIsNotNone(start[0])
        self.assertTrue(stop.is_set())
        self.assertGreaterEqual(time.time() - t0, obj["duration"] - 0.1)


class TestVirtualVision(unittest.TestCase):
    def setUp(self):
        self.bridge = offline_bridge()
        self.bridge.set_local_scene(
            {
                "people": [
                    {"id": 1, "name": "Alex", "present": True, "x": 0.25, "emotion": "happy"},
                    {"id": 2, "name": "Unknown", "present": True, "x": 0.75, "emotion": "sad"},
                ]
            }
        )

    def test_scene_populates_face_cache_and_last_data(self):
        from gigi.virtual.vision import VirtualVision

        v = VirtualVision()
        v.sync_scene()
        data = v.get_last_data()
        self.assertEqual(len(data), 2)
        alex = data["0001"]
        self.assertEqual(alex["name"], "Alex")
        self.assertEqual(alex["emotion"], "happy")
        self.assertGreater(alex["offset"][0], 0, "a face left of centre has a positive x offset")
        self.assertEqual(v.found["face"], v.last_data)

    def test_person_leaving_is_removed(self):
        from gigi.virtual.vision import VirtualVision

        v = VirtualVision()
        v.sync_scene()
        self.bridge.set_local_scene({"people": [{"id": 1, "name": "Alex", "present": False}]})
        v.sync_scene()
        self.assertEqual(v.get_last_data(), {})
        self.assertEqual(v.face_cache.get_all_faces(), {})

    def test_activity_naming_is_mirrored_to_scene(self):
        from gigi.virtual.vision import VirtualVision

        v = VirtualVision()
        v.sync_scene()
        v.face_cache.update_face_name(2, "Jordan", "0_0")
        v.sync_scene()
        names = {p["id"]: p["name"] for p in self.bridge.get_scene()["people"]}
        self.assertEqual(names[2], "Jordan")

    def test_look_for_and_frames(self):
        from gigi.virtual.vision import VirtualVision

        v = VirtualVision()
        v.run_vision()
        try:
            result = v.look_for({"name": "Alex"}, timeout=2.0, show_camera_feed=False)
            self.assertTrue(result["found"])
            deadline = time.time() + 2
            while v.get_latest_frame() is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(v.get_latest_frame().shape, (480, 640, 3))
        finally:
            v.stop_vision()


class TestVirtualLLM(unittest.TestCase):
    def setUp(self):
        self.bridge = offline_bridge()

    def _conversation(self, fail):
        class FakeConversation:
            def _call_npu(self, messages, model=None, raise_on_error=False):
                if fail:
                    raise ConnectionError("no LLM")
                return "model reply"

        return FakeConversation()

    def test_auto_mode_uses_model_when_available(self):
        from gigi.virtual.llm import install_virtual_llm

        conv = self._conversation(fail=False)
        with mock.patch.dict(os.environ, {"GIGI_VIRTUAL_LLM": "auto"}):
            install_virtual_llm(conv)
        self.assertEqual(conv._call_npu([{"role": "user", "content": "hi"}]), "model reply")

    def test_auto_mode_falls_back_to_wizard(self):
        from gigi.virtual.llm import install_virtual_llm

        conv = self._conversation(fail=True)
        with mock.patch.dict(os.environ, {"GIGI_VIRTUAL_LLM": "auto"}):
            install_virtual_llm(conv)
        with mock.patch("builtins.input", return_value="wizard reply"):
            self.assertEqual(conv._call_npu([{"role": "user", "content": "hi"}]), "wizard reply")


class TestVirtualConsoleAPI(unittest.TestCase):
    def setUp(self):
        from gigi.virtual.console.app import create_app

        self.app = create_app()
        self.client = self.app.test_client()

    def test_page_and_ping(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertTrue(self.client.get("/api/ping").get_json()["ok"])

    def test_student_input_queue(self):
        self.client.post("/api/input", json={"text": "hello robot"})
        data = self.client.get("/api/input/next?timeout=0.1").get_json()
        self.assertEqual(data, {"available": True, "text": "hello robot"})
        self.assertFalse(self.client.get("/api/input/next?timeout=0.1").get_json()["available"])

    def test_events_roundtrip(self):
        self.client.post("/api/events", json={"kind": "say", "text": "Hi!"})
        events = self.client.get("/api/events?since=0").get_json()["events"]
        self.assertEqual(events[-1]["text"], "Hi!")
        self.assertEqual(self.client.post("/api/events", json={}).status_code, 400)

    def test_scene_validation(self):
        r = self.client.put("/api/scene", json={"people": [{"id": 3, "name": "Sam", "x": 4}]})
        person = r.get_json()["people"][0]
        self.assertEqual(person["x"], 1.0)
        self.assertEqual(self.client.put("/api/scene", json={}).status_code, 400)

    def test_wizard_llm_cycle(self):
        rid = self.client.post("/api/llm/request", json={"messages": []}).get_json()["id"]
        self.assertFalse(self.client.get(f"/api/llm/response/{rid}?timeout=0.1").get_json()["ready"])
        self.client.post("/api/llm/reply", json={"id": rid, "reply": "Let's take a breath."})
        self.assertEqual(
            self.client.get(f"/api/llm/response/{rid}").get_json(), {"ready": True, "reply": "Let's take a breath."}
        )

    def test_activity_list_excludes_calibration(self):
        data = self.client.get("/api/activities").get_json()
        targets = [a["target"] for a in data["core"]]
        self.assertIn("math_quest", targets)
        self.assertFalse(any("calibrat" in t for t in targets))
        self.assertEqual(self.client.post("/api/run", json={"target": "calibrate_motors"}).status_code, 404)
        self.assertEqual(self.client.post("/api/run", json={"target": "no_such_activity"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
