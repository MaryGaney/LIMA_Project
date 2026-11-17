#!/usr/bin/env python3
"""
Build a listening page for choosing the robot's voice.

Synthesizes the same mentor lines with every installed Piper voice (and, for the
multi-speaker LibriTTS-R model, a shortlist of speakers), measures each sample's
median pitch, and writes data/voice_audition/index.html with audio players.

    python tools/voice_audition.py
    python tools/voice_audition.py --libritts-sample 120 --pitch -1 1

Pitch is only a rough guide: listeners judge gender from more than pitch, so pick
the final voice by listening together (ideally with students from the LLC).
Output is git-ignored.
"""

from __future__ import annotations

import argparse
import json
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from gigi.core.config import DATA_DIR  # noqa: E402
from gigi.core.voice_settings import PIPER_DIR, installed_piper_voices  # noqa: E402

OUT_DIR = DATA_DIR / "voice_audition"
LINES = [
    "Hi, I'm LIMA, a robot peer mentor here in the Luddy Living Learning Center.",
    "That sounds like a stressful week. Want to make a quick study plan together?",
    "Counseling and Psychological Services can help with that, and your Peer Mentor can follow up with you.",
]
# Median F0 band often perceived as gender-ambiguous in adult speech (approximate).
AMBIGUOUS_F0 = (145.0, 175.0)


def median_f0(wav: np.ndarray, sr: int) -> float:
    import librosa

    f0, voiced, _ = librosa.pyin(wav, fmin=70, fmax=350, sr=sr, frame_length=1024)
    f0 = f0[voiced] if voiced is not None else f0
    f0 = f0[~np.isnan(f0)]
    return float(np.median(f0)) if len(f0) else float("nan")


def synth(voice, text, speaker_id=None, pitch=0.0):
    from piper.config import SynthesisConfig

    chunks = list(voice.synthesize(text, syn_config=SynthesisConfig(speaker_id=speaker_id)))
    wav = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
    sr = chunks[0].sample_rate
    if pitch:
        import librosa

        wav = librosa.effects.pitch_shift(wav, sr=sr, n_steps=pitch)
    return wav, sr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--libritts-sample", type=int, default=100, help="LibriTTS-R speakers to scan (of 904)")
    ap.add_argument("--libritts-keep", type=int, default=8, help="Ambiguous-pitch speakers to keep")
    ap.add_argument("--pitch", type=float, nargs="*", default=[], help="Extra pitch shifts (semitones) for single-speaker voices")
    args = ap.parse_args()

    from piper import PiperVoice

    voices = installed_piper_voices()
    if not voices:
        print("No Piper voices installed. Run: python tools/download_voices.py")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []

    for name in voices:
        voice = PiperVoice.load(str(PIPER_DIR / f"{name}.onnx"))
        n_speakers = voice.config.num_speakers
        if n_speakers > 1:
            step = max(1, n_speakers // max(args.libritts_sample, 1))
            scanned = []
            for spk in range(0, n_speakers, step):
                wav, sr = synth(voice, LINES[0], speaker_id=spk)
                scanned.append((spk, median_f0(wav, sr)))
                print(f"  {name} speaker {spk}: {scanned[-1][1]:.0f} Hz")
            mid = sum(AMBIGUOUS_F0) / 2
            in_band = [s for s in scanned if AMBIGUOUS_F0[0] <= s[1] <= AMBIGUOUS_F0[1]]
            pick = sorted(in_band or scanned, key=lambda s: abs(s[1] - mid))[: args.libritts_keep]
            candidates = [(spk, 0.0) for spk, _ in pick]
        else:
            candidates = [(None, 0.0)] + [(None, p) for p in args.pitch]

        for spk, pitch in candidates:
            label = name + ("" if spk is None else f" / speaker {spk}") + (f" / pitch {pitch:+g}" if pitch else "")
            stem = label.replace(" / ", "__").replace(" ", "_").replace("+", "p")
            files, f0s = [], []
            for i, line in enumerate(LINES):
                wav, sr = synth(voice, line, speaker_id=spk, pitch=pitch)
                path = OUT_DIR / f"{stem}__{i}.wav"
                sf.write(path, wav, sr)
                files.append(path.name)
                f0s.append(median_f0(wav, sr))
            f0 = float(np.nanmedian(f0s))
            rows.append({"label": label, "voice": name, "speaker_id": spk, "pitch": pitch, "f0": f0, "files": files})
            print(f"{label}: median pitch {f0:.0f} Hz")

    rows.sort(key=lambda r: abs(r["f0"] - sum(AMBIGUOUS_F0) / 2))
    body = []
    for r in rows:
        band = AMBIGUOUS_F0[0] <= r["f0"] <= AMBIGUOUS_F0[1]
        players = "".join(f'<audio controls preload="none" src="{html.escape(f)}"></audio>' for f in r["files"])
        settings = {"engine": "piper", "voice": r["voice"], "speaker_id": r["speaker_id"], "pitch_semitones": r["pitch"]}
        body.append(
            f"<tr><td><b>{html.escape(r['label'])}</b><br><code>{html.escape(json.dumps(settings))}</code></td>"
            f"<td>{r['f0']:.0f} Hz{' (in band)' if band else ''}</td><td>{players}</td></tr>"
        )
    page = f"""<!doctype html><meta charset="utf-8"><title>Voice audition</title>
<style>body{{font:14px system-ui;margin:24px;max-width:1200px}}td{{padding:8px;border-bottom:1px solid #ddd;vertical-align:top}}
audio{{display:block;margin:2px 0;width:320px}}</style>
<h1>LIMA voice audition</h1>
<p>Same three lines in each voice, sorted by closeness of median pitch to the middle of
{AMBIGUOUS_F0[0]:.0f}-{AMBIGUOUS_F0[1]:.0f} Hz. Pitch is only a rough guide; choose by listening.
Copy the chosen settings into the LIMA web app (Staff > Robot voice) or data/settings/voice.json.</p>
<table><tr><th>Voice</th><th>Median pitch</th><th>Samples</th></tr>{''.join(body)}</table>"""
    (OUT_DIR / "index.html").write_text(page, encoding="utf-8")
    print(f"\nOpen {OUT_DIR / 'index.html'} in a browser to listen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
