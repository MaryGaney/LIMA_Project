"""
Virtual speech output.

``VirtualSpeech`` matches the parts of ``gigi.expression.speech.Speech`` that the
robot and activities use. Instead of synthesizing audio it sends each utterance to
the console as a caption (which the browser can voice with its built-in speech
synthesis) and waits for an estimated speaking duration so turn-taking timing stays
realistic. A synthetic amplitude envelope keeps the real face's mouth animating.

Set ``GIGI_VIRTUAL_SPEECH=real`` to use the real TTS engine instead (it still
reports captions to the console).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Any, Dict, Optional

import numpy as np

from gigi.virtual.bridge import get_bridge

WORDS_PER_SECOND = float(os.environ.get("GIGI_VIRTUAL_SPEECH_RATE", "2.6"))
ENVELOPE_STEP = 0.1  # seconds per mouth frame; matches Speech.sample_rate


def estimate_duration(text: str, words_per_second: float = WORDS_PER_SECOND) -> float:
    words = len(text.split())
    return max(0.6, words / max(words_per_second, 0.1))


def synthetic_envelope(text: str, duration: float, step: float = ENVELOPE_STEP) -> np.ndarray:
    """Deterministic pseudo-syllable envelope in [0, 1] for mouth animation."""
    n = max(1, int(np.ceil(duration / step)))
    seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    syllable = 0.5 + 0.5 * np.sin(np.arange(n) * 2.1)
    env = np.clip(0.25 + 0.55 * syllable + rng.uniform(-0.15, 0.15, n), 0.0, 1.0)
    env[-1] = 0.0
    return env


class VirtualSpeech:
    """Caption-based stand-in for the robot's text-to-speech engine."""

    def __init__(self, languages="en", activity=None, child=False, gender="female", verbose=False):
        print("Initializing virtual speech (captions in the Virtual Robot Console) ...")
        self.languages = [languages] if isinstance(languages, str) else list(languages or ["en"])
        self.child = child
        self.gender = gender
        self.verbose = verbose
        self.sample_rate = ENVELOPE_STEP
        self.speaker_sample_rate = 22050
        self.audio_objects: Dict[str, Dict[str, Any]] = {}
        self.recorded_audio: Dict[str, Any] = {}
        self.keep_record = False
        self.activity = None
        self.bridge = get_bridge()
        self.set_activity(activity)

    def set_activity(self, activity_name):
        self.activity = activity_name

    # ------------------------------------------------------------------
    # Audio objects
    # ------------------------------------------------------------------
    def update_audio_objects(self, file=None, text=None) -> str:
        label = text
        duration = None
        if label is None and file is not None:
            label = f"[plays audio file: {os.path.basename(str(file))}]"
            duration = self._file_duration(file)
        label = label or ""
        rate = self._rate()
        if duration is None:
            duration = estimate_duration(label, WORDS_PER_SECOND * rate)

        key = "virtual://" + hashlib.md5(f"{label}|{file}|{rate}".encode("utf-8")).hexdigest()
        if key not in self.audio_objects:
            self.audio_objects[key] = {
                "text": label,
                "file": file,
                "duration": duration,
                "envelope": synthetic_envelope(label, duration, self.sample_rate),
                "rate": rate,
            }
        return key

    @staticmethod
    def _rate() -> float:
        """Speaking speed from the robot voice settings (the LIMA web app can change it)."""
        try:
            from gigi.core.voice_settings import load_voice_settings

            return load_voice_settings()["rate"]
        except Exception:
            return 1.0

    @staticmethod
    def _file_duration(file) -> Optional[float]:
        try:
            import soundfile as sf

            info = sf.info(str(file))
            return float(info.frames) / float(info.samplerate)
        except Exception:
            return None

    def get_envelope(self, file, max_length=-1, y=None, sr=None):
        obj = self.audio_objects.get(file)
        return obj["envelope"] if obj else np.zeros(1)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def play_audio(self, file, stop_event=None, start_time_container=None, stop_condition=None):
        obj = self.audio_objects.get(file)
        if obj is None:
            return
        self.bridge.emit("say", text=obj["text"], duration=obj["duration"], activity=self.activity,
                         rate=obj.get("rate", 1.0))
        if start_time_container is not None:
            start_time_container[0] = time.time()
        end = time.time() + obj["duration"]
        while time.time() < end:
            if stop_event is not None and stop_event.is_set():
                break
            time.sleep(0.05)
        if stop_condition is not None and "audio" in stop_condition and stop_event is not None:
            stop_event.set()

    def generate_audio(self, text=None, file=None, stop_event=None, stop_condition=None, start_time_container=None):
        key = self.update_audio_objects(text=text, file=file)
        self.play_audio(key, stop_event, start_time_container, stop_condition)

    def audio_thread(self, text=None, file=None, stop_event=None, stop_condition=None, start_time_container=None):
        if stop_event is None:
            stop_event = threading.Event()
        return threading.Thread(
            target=self.generate_audio, args=(text, file, stop_event, stop_condition, start_time_container)
        )

    def play_audio_thread(self, file, stop_event=None, start_time_container=None, stop_condition=None):
        if stop_event is None:
            stop_event = threading.Event()
        return threading.Thread(target=self.play_audio, args=(file, stop_event, start_time_container, stop_condition))

    def run_speech(self, text=None, file=None):
        t = self.audio_thread(text, file)
        t.start()
        t.join()


def make_speech(**kwargs):
    """Factory used by the robot: virtual captions by default, real TTS on request."""
    if os.environ.get("GIGI_VIRTUAL_SPEECH", "captions").lower() != "real":
        return VirtualSpeech(**kwargs)

    from gigi.expression.speech import Speech

    bridge = get_bridge()

    class ReportingSpeech(Speech):
        """Real TTS that also reports captions to the console."""

        def update_audio_objects(self, file=None, text=None):
            key = super().update_audio_objects(file=file, text=text)
            self._virtual_labels = getattr(self, "_virtual_labels", {})
            self._virtual_labels[key] = text or f"[plays audio file: {os.path.basename(str(file))}]"
            return key

        def _report(self, key):
            label = getattr(self, "_virtual_labels", {}).get(key, "")
            bridge.emit("say", text=label, duration=None, activity=self.activity)

        def play_audio(self, file, *args, **kwargs):
            self._report(file)
            return super().play_audio(file, *args, **kwargs)

        def generate_audio(self, text=None, file=None, *args, **kwargs):
            bridge.emit("say", text=text or f"[plays audio file: {file}]", duration=None, activity=self.activity)
            return super().generate_audio(text, file, *args, **kwargs)

    try:
        return ReportingSpeech(**kwargs)
    except Exception as e:
        # Real TTS needs its engine installed (Silero via PyTorch on Windows, Nix TTS on
        # the robot). Fall back to captions so the activity still runs.
        message = (
            f"Real TTS could not start ({type(e).__name__}: {e}). Using captions instead. "
            "See docs/VIRTUAL_ROBOT.md, 'Real TTS'."
        )
        print(f"[Virtual] {message}")
        bridge.emit("notice", text=message)
        return VirtualSpeech(**kwargs)
