"""
Robot voice settings: engine, voice model, speaking rate, and pitch.

Settings live in ``data/settings/voice.json`` on each robot (git-ignored) and are
re-read before every new utterance, so changes made from the LIMA web app take
effect on the robot's next sentence. Environment variables override the file:
``GIGI_TTS_ENGINE``, ``GIGI_TTS_VOICE``, ``GIGI_TTS_RATE``.

Engines:
    auto   Piper if it is installed and the voice model is present, else the legacy engine
    piper  Piper neural TTS (https://github.com/OHF-Voice/piper1-gpl); runs on the Orange Pi CPU
    nix    legacy Nix TTS (robot default before LIMA)
    silero legacy Silero TTS (Windows workstations; needs PyTorch)
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from gigi.core.config import DATA_DIR, RESOURCES_DIR

SETTINGS_PATH = Path(os.environ.get("GIGI_VOICE_SETTINGS", str(DATA_DIR / "settings" / "voice.json")))
PIPER_DIR = RESOURCES_DIR / "piper"

# en_US-sam-medium is trained on the open "Sam" non-binary voice dataset
# (Accenture Labs / CereProc), Apache-2.0. See docs/VOICE.md.
DEFAULT_VOICE = "en_US-sam-medium"
ENGINES = ("auto", "piper", "nix", "silero")
RATE_MIN, RATE_MAX = 0.6, 1.6
PITCH_MIN, PITCH_MAX = -4.0, 4.0

DEFAULTS: dict[str, Any] = {
    "engine": "auto",
    "voice": DEFAULT_VOICE,
    "speaker_id": None,
    "rate": 1.0,
    "pitch_semitones": 0.0,
}

_lock = threading.Lock()


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        return round(min(max(float(value), lo), hi), 2)
    except (TypeError, ValueError):
        return default


def validate(settings: dict[str, Any]) -> dict[str, Any]:
    """Return a cleaned copy of ``settings`` with every field in range."""
    out = dict(DEFAULTS)
    engine = str(settings.get("engine", out["engine"])).lower()
    out["engine"] = engine if engine in ENGINES else "auto"
    voice = str(settings.get("voice") or DEFAULT_VOICE)
    # Voice names are file stems inside Resources/piper; refuse path tricks.
    out["voice"] = voice if all(c.isalnum() or c in "_-" for c in voice) else DEFAULT_VOICE
    spk = settings.get("speaker_id")
    out["speaker_id"] = int(spk) if isinstance(spk, (int, float, str)) and str(spk).strip().isdigit() else None
    out["rate"] = _clamp(settings.get("rate", 1.0), RATE_MIN, RATE_MAX, 1.0)
    out["pitch_semitones"] = _clamp(settings.get("pitch_semitones", 0.0), PITCH_MIN, PITCH_MAX, 0.0)
    return out


def load_voice_settings(path: Path | None = None) -> dict[str, Any]:
    path = Path(path or SETTINGS_PATH)
    data: dict[str, Any] = {}
    with _lock:
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    if os.environ.get("GIGI_TTS_ENGINE"):
        data["engine"] = os.environ["GIGI_TTS_ENGINE"]
    if os.environ.get("GIGI_TTS_VOICE"):
        data["voice"] = os.environ["GIGI_TTS_VOICE"]
    if os.environ.get("GIGI_TTS_RATE"):
        data["rate"] = os.environ["GIGI_TTS_RATE"]
    return validate(data)


def save_voice_settings(updates: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Merge ``updates`` into the stored settings and return the result."""
    path = Path(path or SETTINGS_PATH)
    with _lock:
        current: dict[str, Any] = {}
        try:
            if path.exists():
                current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        current.update({k: v for k, v in updates.items() if k in DEFAULTS})
        cleaned = validate(current)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return cleaned


def piper_model_path(voice: str) -> Path:
    return PIPER_DIR / f"{voice}.onnx"


def piper_available() -> bool:
    try:
        import piper  # noqa: F401

        return True
    except ImportError:
        return False


def installed_piper_voices() -> list[str]:
    if not PIPER_DIR.is_dir():
        return []
    return sorted(p.stem for p in PIPER_DIR.glob("*.onnx") if (p.parent / f"{p.name}.json").exists())


def resolve_engine(settings: dict[str, Any], legacy_engine: str) -> str:
    """Pick the engine that will actually run, given what is installed."""
    engine = settings["engine"]
    piper_ready = piper_available() and piper_model_path(settings["voice"]).exists()
    if engine in ("auto", "piper") and piper_ready:
        return "piper"
    if engine == "piper":
        print(f"[Voice] Piper requested but not ready (package or {settings['voice']}.onnx missing); "
              f"using {legacy_engine}. Run: python tools/download_voices.py")
    return legacy_engine if engine in ("auto", "piper") else engine


def voice_tag(settings: dict[str, Any], engine: str) -> str:
    """Short, filesystem-safe identifier used to namespace the speech cache."""
    spk = "" if settings.get("speaker_id") is None else f"-s{settings['speaker_id']}"
    voice = settings["voice"] if engine == "piper" else engine
    return f"{voice}{spk}-r{settings['rate']:.2f}-p{settings['pitch_semitones']:+.1f}"
