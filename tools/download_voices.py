#!/usr/bin/env python3
"""
Download Piper voice models into Resources/piper/ (run on a workstation or the robot).

    python tools/download_voices.py                 # default LIMA voice (en_US-sam-medium)
    python tools/download_voices.py en_US-libritts_r-medium

Requires the optional voice dependency: pip install -e ".[voice]"
Voice files come from https://huggingface.co/rhasspy/piper-voices. Each voice has its
own license (see the MODEL_CARD next to it there); check it before redistributing.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gigi.core.voice_settings import DEFAULT_VOICE, PIPER_DIR  # noqa: E402


def main(argv):
    voices = argv or [DEFAULT_VOICE]
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "piper.download_voices", "--download-dir", str(PIPER_DIR), *voices]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
