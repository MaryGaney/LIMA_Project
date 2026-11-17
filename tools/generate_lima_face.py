#!/usr/bin/env python3
"""
Generate the LIMA face sprite frames (Assets/face/lima_face.001.png ... .015.png).

Design goals (LIMA design research): friendly and a little cute, clearly a robot
(not a realistic human face), and not childlike. Built for a purple robot body:
a deep plum screen keeps glare low at night and frames soft mint eyes, which
contrast with both the screen and the purple shell.

Frame numbers follow the face engine's convention (see face_definitions.py):
    1 idle | 1-7 blink (eyes) | 1-4 talk (mouth, 1 = closed) | 8-9 smile (mouth)
    10-11 look_right | 12-13 look_left | 14 look_up | 15 look_down
"look_right" is the robot's right, so the eyes move toward the viewer's left.

    python tools/generate_lima_face.py            # write the frames
    python tools/generate_lima_face.py --preview  # also write a contact sheet to data/
Edit PALETTE / GEOMETRY below and rerun to restyle.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "Assets" / "face"
W, H = 1280, 720
SS = 2  # supersampling for smooth edges

PALETTE = {
    "screen": (30, 20, 52),       # deep plum
    "eye": (150, 244, 218),       # soft mint
    "eye_glow": (90, 220, 190),
    "mouth": (150, 244, 218),
    "cheek": (200, 140, 255),     # lavender blush, drawn translucent
}
GEOMETRY = {
    "eye_cx": (470, 810),   # left/right eye centres (viewer's view)
    "eye_cy": 165,          # inside the Eyes band (0-295 px)
    "eye_w": 112,
    "eye_h": 150,
    "cheek_cy": 330,        # inside the Nose band (295-468 px)
    "mouth_cy": 560,        # inside the Mouth band (468-720 px)
    "mouth_w": 120,
}

# eye state per frame: (openness 0..1, dx, dy, happy)
EYES = {
    1: (1.0, 0, 0, False), 2: (0.7, 0, 0, False), 3: (0.35, 0, 0, False), 4: (0.06, 0, 0, False),
    5: (0.35, 0, 0, False), 6: (0.7, 0, 0, False), 7: (1.0, 0, 0, False),
    8: (1.0, 0, 0, True), 9: (1.0, 0, 0, True),
    10: (1.0, -30, 0, False), 11: (1.0, -60, 0, False),
    12: (1.0, 30, 0, False), 13: (1.0, 60, 0, False),
    14: (0.92, 0, -28, False), 15: (0.75, 0, 26, False),
}
# mouth state per frame: ("smile", size) | ("open", height) | ("grin", height)
MOUTH = {1: ("smile", 1.0), 2: ("open", 20), 3: ("open", 36), 4: ("open", 54), 8: ("smile", 1.35), 9: ("grin", 30)}


def s(v):
    return int(round(v * SS))


def draw_eye(d: ImageDraw.ImageDraw, cx, cy, openness, happy, color):
    g = GEOMETRY
    w, h = g["eye_w"], g["eye_h"]
    if happy:
        # upward-curved "smiling" eye: a thick arc
        box = (s(cx - w / 2), s(cy - h * 0.15), s(cx + w / 2), s(cy + h * 0.55))
        d.arc(box, start=200, end=340, fill=color, width=s(22))
        return
    eh = max(h * openness, 14)
    r = min(w, eh) / 2
    d.rounded_rectangle((s(cx - w / 2), s(cy - eh / 2), s(cx + w / 2), s(cy + eh / 2)), radius=s(r), fill=color)


def draw_mouth(d: ImageDraw.ImageDraw, kind, size, color):
    g = GEOMETRY
    cx, cy, mw = W / 2, g["mouth_cy"], g["mouth_w"]
    if kind == "smile":
        w = mw * size
        d.arc((s(cx - w / 2), s(cy - 40 * size), s(cx + w / 2), s(cy + 22 * size)), start=25, end=155,
              fill=color, width=s(14))
    elif kind == "open":
        w = mw * 0.55 + size * 0.6
        d.rounded_rectangle((s(cx - w / 2), s(cy - size / 2), s(cx + w / 2), s(cy + size / 2 + 6)),
                            radius=s(min(w, size + 6) / 2), fill=color)
    elif kind == "grin":
        w = mw * 1.2
        d.chord((s(cx - w / 2), s(cy - size * 1.6), s(cx + w / 2), s(cy + size * 1.3)), start=0, end=180, fill=color)


def render_frame(n: int) -> Image.Image:
    g, p = GEOMETRY, PALETTE
    base = Image.new("RGBA", (s(W), s(H)), p["screen"] + (255,))

    # features drawn on their own layer so we can add a soft glow underneath
    feat = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(feat)
    openness, dx, dy, happy = EYES.get(n, EYES[1])
    for cx in g["eye_cx"]:
        draw_eye(d, cx + dx, g["eye_cy"] + dy, openness, happy, p["eye"] + (255,))
    kind, size = MOUTH.get(n, MOUTH[1])
    draw_mouth(d, kind, size, p["mouth"] + (255,))

    glow = feat.filter(ImageFilter.GaussianBlur(s(14)))
    tint = Image.new("RGBA", base.size, p["eye_glow"] + (0,))
    tint.putalpha(glow.getchannel("A").point(lambda a: int(a * 0.55)))
    base = Image.alpha_composite(base, tint)

    cheeks = Image.new("RGBA", base.size, (0, 0, 0, 0))
    dc = ImageDraw.Draw(cheeks)
    for cx in g["eye_cx"]:
        ox = -50 if cx < W / 2 else 50
        dc.ellipse((s(cx + ox - 55), s(g["cheek_cy"] - 22), s(cx + ox + 55), s(g["cheek_cy"] + 22)),
                   fill=p["cheek"] + (70,))
    cheeks = cheeks.filter(ImageFilter.GaussianBlur(s(10)))
    base = Image.alpha_composite(base, cheeks)
    base = Image.alpha_composite(base, feat)
    return base.resize((W, H), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="also write data/lima_face_preview.png")
    args = ap.parse_args()
    frames = {}
    for n in range(1, 16):
        img = render_frame(n)
        img.save(OUT_DIR / f"lima_face.{n:03d}.png")
        frames[n] = img
    print(f"Wrote 15 frames to {OUT_DIR}")
    if args.preview:
        thumb_w, thumb_h = W // 4, H // 4
        sheet = Image.new("RGB", (thumb_w * 5, thumb_h * 3), (0, 0, 0))
        for i, n in enumerate(range(1, 16)):
            sheet.paste(frames[n].convert("RGB").resize((thumb_w, thumb_h)), ((i % 5) * thumb_w, (i // 5) * thumb_h))
        out = ROOT / "data" / "lima_face_preview.png"
        out.parent.mkdir(exist_ok=True)
        sheet.save(out)
        print(f"Preview: {out}")


if __name__ == "__main__":
    main()
