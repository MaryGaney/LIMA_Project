"""Connects the check-in runner to a GigiRobot (real or virtual) without leaking transcripts."""

from __future__ import annotations

import os
import time

from gigi.core.voice_settings import load_voice_settings, save_voice_settings


class NullInteractionLogger:
    """
    Replaces the robot's InteractionLogger during LIMA sessions. The normal logger
    writes conversations to data/<user>/ folders, which would keep transcripts
    outside the review-then-delete workflow.
    """

    is_initialized = False
    conversation_buffer: list = []
    visuals_buffer: list = []
    variables_buffer: dict = {}

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class GigiRobotIO:
    def __init__(self, robot):
        self.robot = robot
        robot.logger = NullInteractionLogger()
        self._original_rate = load_voice_settings()["rate"]

    def say(self, text: str, muted: bool = False) -> None:
        if muted:
            # Text-only mode for students who prefer no voice: show it in the web app, smile on screen.
            if self.robot.face:
                try:
                    self.robot.face.run_sequence("smile")
                except Exception:
                    pass
            time.sleep(min(4.0, 0.6 + len(text.split()) / 4.0))
            return
        self.robot.run_character(viseme_data={"text": text, "file": None})

    def listen(self) -> str:
        if not self.robot.hearing:
            return ""
        self.robot.hearing.texts = []
        self.robot.listen_backchannel(show_camera_feed=False)
        texts = self.robot.hearing.texts
        return texts[-1] if texts else ""

    def set_rate(self, rate: float) -> None:
        if abs(load_voice_settings()["rate"] - rate) > 0.009:
            save_voice_settings({"rate": rate})

    def restore(self) -> None:
        """Put the robot's default speaking rate back and remove cached audio of this session."""
        save_voice_settings({"rate": self._original_rate})
        speech = getattr(self.robot, "speech", None)
        records = getattr(speech, "recorded_audio", None)
        key = getattr(speech, "cache_key", None)
        if not records or key not in records:
            return
        # Personalized lines ("Welcome back, Sam") must not stay on disk as audio or index text.
        for path in list(records[key]):
            for p in (path, path.replace(".wav", ".npy")):
                try:
                    os.remove(p)
                except OSError:
                    pass
        records.pop(key, None)
        try:
            speech.save_recorded_audio()
        except Exception:
            pass
