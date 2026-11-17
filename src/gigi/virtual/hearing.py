"""
Virtual hearing (speech recognition stand-in).

``VirtualHearing`` matches the ``gigi.perception.hearing.Hearing`` interface used by
the robot and activities, but the "transcript" is whatever the tester types in the
Virtual Robot Console (or the terminal). No microphone, VAD, Whisper, or Vosk is
loaded. Pronunciation scoring and speaker identification are not simulated.
"""

from __future__ import annotations

import threading
from typing import Optional

from gigi.virtual.bridge import get_bridge

SILENCE_DURATION = 3.5


class VirtualHearing:
    def __init__(self, languages="en", verbose=False):
        print("Initializing virtual hearing (typed student input) ...")
        self.verbose = verbose
        self.languages = [languages] if isinstance(languages, str) else list(languages or ["en"])
        self.words = None
        self.texts = []
        self.raw_audio_buffer = []
        self.face = None
        self.model = None
        self.use_rknn = False
        self.audio_processor = None
        self.pronunciation_mode = False
        self.pronunciation_grammar = []
        self.pronunciation_engine = "virtual"
        self.citrinet_gop = None
        self.use_citrinet_gop = False
        self.vosk_model = None
        self.silence_duration = SILENCE_DURATION
        self._raw_buf_duration = 2.0
        self.stream_lock = threading.Lock()
        self.bridge = get_bridge()

    # ------------------------------------------------------------------
    # Face feedback helpers (the "listening ear" icon)
    # ------------------------------------------------------------------
    def _set_listening(self, active: bool):
        face = getattr(self, "face", None)
        if face is None:
            return
        face.feedback_state = "listening" if active else None
        if hasattr(face, "set_reading_status"):
            face.set_reading_status("listening" if active else "idle")

    def _receive(self, stop_event: Optional[threading.Event], mode: str) -> Optional[str]:
        self._set_listening(True)
        self.bridge.emit("listening", mode=mode, expected_words=self.words if isinstance(self.words, str) else None)
        try:
            text = self.bridge.wait_for_input(stop_event=stop_event)
        finally:
            self._set_listening(False)
        if text:
            text = text.strip()
            self.texts.append(text)
            self.bridge.emit("heard", text=text)
        else:
            self.bridge.emit("heard", text="", silent=True)
        if stop_event is not None:
            stop_event.set()
        return text

    # ------------------------------------------------------------------
    # Hearing API
    # ------------------------------------------------------------------
    def listen(self, stop_event=None):
        self._receive(stop_event, "listen")

    def listen_fluid(self, stop_event=None, check_callback=None, n_transcripts=1, silence_duration=5.0):
        self.silence_duration = silence_duration
        self._receive(stop_event, "listen_fluid")

    def hearing_thread(self, stop_event=None):
        if stop_event is None:
            stop_event = threading.Event()
        return threading.Thread(target=self.listen, args=[stop_event])

    def hearing_fluid_thread(self, stop_event=None, check_callback=None, n_transcripts=1, silence_duration=5.0):
        if stop_event is None:
            stop_event = threading.Event()
        return threading.Thread(
            target=self.listen_fluid, args=[stop_event, check_callback, n_transcripts, silence_duration]
        )

    def run_hearing(self):
        self.clear_audio_buffer()
        t = self.hearing_thread()
        t.start()
        t.join()

    def get_full_audio(self):
        return None

    def clear_audio_buffer(self):
        self.raw_audio_buffer = []

    def contains_speech(self, audio_data):
        return False

    def load_vosk_model(self):
        return None

    def load_citrinet_gop(self):
        return None
