"""
Virtual servos.

``VirtualMovement`` subclasses the real ``Movement`` class, so gestures, smoothing,
interpolation, and normalized-angle math all run through the production code. Only
the final PWM write is replaced: ``VirtualMotorController`` records the commanded
value and the joint state is streamed to the console's joint view.

This is a kinematic *command* model. It shows what the robot asked its servos to do;
it does not model servo speed, load, backlash, or a specific robot's calibration.
It never opens I2C, so it cannot move a physical robot.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Dict

from gigi.expression.gesture_definitions import BASIC_GESTURES
from gigi.expression.movement import Movement
from gigi.virtual.bridge import get_bridge

# Symmetric virtual joint ranges. These are NOT a robot calibration and must never be
# written to motorData_calibrated.json or used on hardware.
VIRTUAL_MOTOR_MAP: Dict[str, Any] = {
    "system_calibrated": True,
    "torso": {"channel": 0, "min": 200, "max": 400, "center": 300, "calibrated": True},
    "neck": {"channel": 1, "min": 200, "max": 400, "center": 300, "calibrated": True},
    "right_shoulder": {"channel": 2, "min": 200, "max": 400, "center": 300, "calibrated": True},
    "left_shoulder": {"channel": 3, "min": 200, "max": 400, "center": 300, "calibrated": True},
    "left_elbow": {"channel": 14, "min": 200, "max": 400, "center": 300, "calibrated": True},
    "right_elbow": {"channel": 15, "min": 200, "max": 400, "center": 300, "calibrated": True},
}
JOINTS = ["torso", "neck", "right_shoulder", "left_shoulder", "right_elbow", "left_elbow"]


class VirtualMotorController:
    """Drop-in for ``PCA9685Controller`` that records PWM values instead of writing I2C."""

    is_virtual = True
    simulated = True

    def __init__(self):
        self.pwm: Dict[int, int] = {}
        self.released = False

    def set_pwm(self, channel: int, on: int, off: int) -> None:
        self.pwm[int(channel)] = int(off)
        self.released = off >= 4096 and all(v >= 4096 for v in self.pwm.values())

    def set_all_pwm(self, on: int, off: int) -> None:
        for ch in list(self.pwm):
            self.pwm[ch] = int(off)

    def reset_motor(self, channel: int) -> None:
        self.pwm[int(channel)] = 0

    def reset_all_motors(self) -> None:
        self.pwm = {ch: 0 for ch in self.pwm}

    def software_reset(self) -> None:
        self.pwm = {}

    def close(self) -> None:
        pass


class VirtualMovement(Movement):
    """Real trajectory code + virtual servos + console reporting."""

    STREAM_INTERVAL = 1.0 / 15.0

    def __init__(self, verbose: bool = False, allow_uncalibrated: bool = False):
        # Deliberately do not call Movement.__init__: it loads the per-robot
        # calibration file and opens the PCA9685 controller.
        self.verbose = verbose
        self.allow_uncalibrated = allow_uncalibrated
        self.is_calibrated = True  # the virtual joint model is self-consistent by construction
        self.motors = VirtualMotorController()
        self.motor_map = copy.deepcopy(VIRTUAL_MOTOR_MAP)
        self.motor_map.pop("system_calibrated", None)
        self.current_positions = {m: v["center"] for m, v in self.motor_map.items()}
        self.bridge = get_bridge()
        self._last_stream = 0.0
        self.history = []  # (timestamp, {joint: normalized}) for tests and debugging
        print("Initializing virtual servos (joint view in the Virtual Robot Console) ...")
        self._report(force=True)

    def joint_state(self) -> Dict[str, float]:
        return {j: round(self.calc_normalized_angle(j), 3) for j in JOINTS}

    def _report(self, force: bool = False):
        now = time.time()
        state = self.joint_state()
        self.history.append((now, state))
        if len(self.history) > 2000:
            self.history = self.history[-1000:]
        if force or now - self._last_stream >= self.STREAM_INTERVAL:
            self._last_stream = now
            self.bridge.emit("joints", joints=state, released=self.motors.released)

    def move_motors(self, motors_) -> None:
        self.motors.released = False
        super().move_motors(motors_)
        self._report()

    def move_sequence(self, motor_seq, stop_event=None) -> None:
        super().move_sequence(motor_seq, stop_event=stop_event)
        self._report(force=True)

    def movement_thread(self, motor_data, stop_condition=None):
        if isinstance(motor_data, str):
            known = motor_data in BASIC_GESTURES
            self.bridge.emit("gesture", name=motor_data, known=known)
            if not known:
                print(f"[Virtual] Warning: unknown gesture '{motor_data}' (the real robot would not move).")
        elif isinstance(motor_data, dict):
            self.bridge.emit("gesture", name="pose", target={k: v for k, v in motor_data.items() if k != "duration"})
        return super().movement_thread(motor_data, stop_condition=stop_condition)

    def home_position(self, duration: float = 1.5) -> None:
        super().home_position(duration=duration)
        self._report(force=True)

    def release(self) -> None:
        super().release()
        self.motors.released = True
        self.bridge.emit("joints", joints=self.joint_state(), released=True)
