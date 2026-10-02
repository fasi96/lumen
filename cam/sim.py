# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["opencv-python-headless", "numpy"]
# ///
"""Simulate camera interactions and see how the bubble behaves.

    uv run sim.py [OUTDIR] [--only NAME,...]

Synthetic people (head, shoulders, arms, hands) act out scripted scenes in a
fake 16:9 camera view. Their masks go through the real Framer and Crowd, just
like the segmenter's output does live, so this tests the bubble's behaviour
without needing anyone in front of the camera. For each scene it writes a video
(left: the camera view and the framing box; right: the bubble) and a row of
measurements to OUTDIR/report.json.
"""
import argparse
import json
import math
import os
import subprocess
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blob import Crowd, Framer, smooth_polygon  # noqa: E402

FPS = 30
W, H = 448, 252          # the fake camera view (16:9); masks are made at this size
PIC = 256                # the bubble picture


# ───────────────────────── people

def lerp(a, b, t):
    return a + (b - a) * t


def ease(t):
    t = min(1, max(0, t))
    return t * t * (3 - 2 * t)


class Person:
    """A head-and-shoulders figure. Positions are fractions of the frame (x of width, y of height)."""

    def __init__(self, x=0.5, y=0.42, r=0.105, skin=(120, 150, 205), shirt=(60, 60, 70)):
        self.x, self.y, self.r = x, y, r
        self.hands = {"L": None, "R": None}       # hand offset from its shoulder, in head radii
        self.skin, self.shirt = skin, shirt
        self.visible = True
        self.neck = True          # False: a dark collar, the segmenter misses the neck
        self.arms = True          # False: the segmenter sees the hands but not the arms

    def draw(self, mask, img):
        if not self.visible:
            return
        h, w = mask.shape
        R = self.r * h
        hx, hy = self.x * w, self.y * h
        torso = (int(hx), int(hy + 3.4 * R))
        axes = (int(2.5 * R), int(2.6 * R))
        cv2.ellipse(mask, torso, axes, 0, 0, 360, 1.0, -1)
        cv2.ellipse(img, torso, axes, 0, 0, 360, self.shirt, -1, cv2.LINE_AA)
        neck = [(int(hx - 0.45 * R), int(hy + 0.6 * R)), (int(hx + 0.45 * R), int(hy + 1.3 * R))]
        if self.neck:
            cv2.rectangle(mask, *neck, 1.0, -1)
        else:
            cv2.ellipse(mask, torso, (axes[0] + 2, axes[1] + 14), 0, 180, 360, 0.0, 16)   # the gap under the chin
        cv2.rectangle(img, *neck, self.skin, -1)
        cv2.ellipse(mask, (int(hx), int(hy)), (int(0.86 * R), int(R)), 0, 0, 360, 1.0, -1)
        cv2.ellipse(img, (int(hx), int(hy)), (int(0.86 * R), int(R)), 0, 0, 360, self.skin, -1, cv2.LINE_AA)
        for side, off in self.hands.items():
            if off is None:
                continue
            sx = hx + (-1.9 if side == "L" else 1.9) * R
            sy = hy + 2.3 * R
            ex, ey = sx + off[0] * R, sy + off[1] * R
            th = max(2, int(0.75 * R))
            if self.arms:
                cv2.line(mask, (int(sx), int(sy)), (int(ex), int(ey)), 1.0, th)
            cv2.line(img, (int(sx), int(sy)), (int(ex), int(ey)), self.shirt, th, cv2.LINE_AA)
            cv2.circle(mask, (int(ex), int(ey)), int(0.6 * R), 1.0, -1)
            cv2.circle(img, (int(ex), int(ey)), int(0.6 * R), self.skin, -1, cv2.LINE_AA)


def room():
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (178, 186, 192)
    cv2.rectangle(img, (0, int(H * 0.72)), (W, H), (120, 132, 140), -1)
    for i, x in enumerate((0.12, 0.28, 0.66, 0.84)):
        c = [(70, 110, 170), (160, 90, 60), (60, 140, 90), (120, 70, 140)][i]
        cv2.rectangle(img, (int(x * W), int(H * 0.16)), (int(x * W) + 34, int(H * 0.16) + 44), c, -1)
    return img


# ───────────────────────── scenes: (seconds, fn(t, people) → mutate people; t in 0..1)

def wave(t, you, *_):
    # Hand up beside the head, waving side to side.
    up = ease(t / 0.15) * (1 - ease((t - 0.85) / 0.15))
    if up < 0.05:
        you.hands["R"] = None
        return
    swing = math.sin(t * 2 * math.pi * 5) * 0.9
    you.hands["R"] = (lerp(0.5, 1.2 + swing, up), lerp(1.5, -3.3, up))


def both_hands(t, you, *_):
    up = ease(t / 0.2) * (1 - ease((t - 0.75) / 0.2))
    if up < 0.05:
        you.hands = {"L": None, "R": None}
        return
    you.hands = {"L": (lerp(-0.3, -1.8, up), lerp(1.5, -4.6, up)), "R": (lerp(0.3, 1.8, up), lerp(1.5, -4.6, up))}


def hands_up_mid(t, you, *_):
    # Both hands raised, but not to the very top of the camera's view.
    up = ease(t / 0.2) * (1 - ease((t - 0.75) / 0.2))
    if up < 0.05:
        you.hands = {"L": None, "R": None}
        return
    you.y = 0.5
    you.hands = {"L": (lerp(-0.3, -1.6, up), lerp(1.5, -3.6, up)), "R": (lerp(0.3, 1.6, up), lerp(1.5, -3.6, up))}


def point(t, you, *_):
    out = ease(t / 0.2) * (1 - ease((t - 0.8) / 0.2))
    you.hands["R"] = None if out < 0.05 else (lerp(0.5, 5.5, out), lerp(1.5, -0.8, out))


def lean(t, you, *_):
    you.x = 0.5 + 0.16 * math.sin(t * 2 * math.pi)


def fast_turn(t, you, *_):
    you.x = 0.5 + 0.12 * math.sin(t * 2 * math.pi * 3)


def chin(t, you, *_):
    # Hand up to the chin and resting there (hand in front of the body).
    on = ease(t / 0.2) * (1 - ease((t - 0.8) / 0.2))
    you.hands["R"] = None if on < 0.05 else (lerp(0.5, -1.3, on), lerp(1.5, -1.6, on))


def leave_return(t, you, *_):
    if t < 0.35:
        you.x = lerp(0.5, 1.3, ease(t / 0.35))
    elif t < 0.6:
        you.x = 1.3
    else:
        you.x = lerp(1.3, 0.5, ease((t - 0.6) / 0.35))


def stand_up(t, you, *_):
    you.y = 0.42 - 0.55 * ease(t / 0.3) * (1 - ease((t - 0.65) / 0.3))


def lean_in(t, you, *_):
    k = ease(t / 0.3) * (1 - ease((t - 0.7) / 0.3))
    you.r = lerp(0.105, 0.24, k)
    you.y = lerp(0.42, 0.5, k)


def edge_peek(t, you, other):
    # Someone half in view at the frame's edge, and the segmenter flickering on them.
    other.x, other.y, other.r = 0.97, 0.45, 0.1
    other.visible = (int(t * 60) % 5) != 0


def walk_behind(t, you, other):
    other.r, other.y = 0.06, 0.36
    other.x = lerp(-0.15, 1.15, t)


def two_apart(t, you, other):
    sep = 0.02 + 0.5 * ease(t / 0.35) * (1 - ease((t - 0.7) / 0.25))
    you.x, other.x = 0.5 - sep / 2 - 0.03, 0.5 + sep / 2 + 0.03


def two_wave(t, you, other):
    you.x, other.x = 0.3, 0.7
    wave(t, you)
    wave((t + 0.3) % 1, other)


SCENES = {
    "idle": (3, lambda t, you, o: setattr(you, "x", 0.5 + 0.004 * math.sin(t * 9))),
    "wave": (5, wave),
    "both_hands": (5, both_hands),
    "hands_up_mid": (5, hands_up_mid),
    "point": (4, point),
    "chin": (4, chin),
    "lean": (5, lean),
    "fast_turn": (4, fast_turn),
    "lean_in": (5, lean_in),
    "stand_up": (5, stand_up),
    "leave_return": (8, leave_return),
    "edge_peek": (4, edge_peek),
    "walk_behind": (6, walk_behind),
    "two_apart": (8, two_apart),
    "two_wave": (5, two_wave),
    "noisy_mask": (4, wave),       # the wave again, with a jittery, holey mask like a bad segmenter day
    "neck_gap": (4, lambda t, you, o: (setattr(you, "neck", False), lean(t, you))),
    "floating_hand": (4, lambda t, you, o: (setattr(you, "arms", False), wave(t, you))),
}
TWO = {"edge_peek", "walk_behind", "two_apart", "two_wave"}


# ───────────────────────── run one scene

def noisy(mask, rng):
    m = cv2.GaussianBlur(mask, (0, 0), 1.5)
    m += rng.normal(0, 0.18, m.shape).astype(np.float32)
    m = cv2.GaussianBlur(m, (0, 0), 1.0)
    if rng.random() < 0.15:             # sometimes a chunk drops out (a hand vanishes for a frame)
        x, y = rng.integers(0, W - 60), rng.integers(0, H - 60)
        m[y : y + 60, x : x + 60] *= 0.2
    return np.clip(m, 0, 1)


def run(name, outdir):
    secs, fn = SCENES[name]
    rng = np.random.default_rng(7)
    you = Person()
    other = Person(0.8, 0.42, 0.1, skin=(110, 140, 180), shirt=(40, 90, 150))
    other.visible = name in TWO
    crowd, framer = Crowd(), Framer()
    bg = room()
    frames = int(secs * FPS)
    out = os.path.join(outdir, f"{name}.mp4")
    enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{W * 2 + PIC * 2 + 24}x{PIC * 2}", "-r", str(FPS), "-i", "-",
                            "-c:v", "libx264", "-crf", "24", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    wide_mask = None
    stats = {"coverage": [], "spill": [], "polys": [], "blobs": [], "area": [], "edge": []}
    splits = 0
    for f in range(frames):
        t = f / frames
        you.hands = dict(you.hands)
        fn(t, you, other)
        mask = np.zeros((H, W), np.float32)
        img = bg.copy()
        for p in (other, you):         # the other person is behind you
            p.draw(mask, img)
        if name == "noisy_mask":
            mask = noisy(mask, rng)
        if f % 4 == 0:                 # live, the wide view is looked at every 4th frame
            wide_mask = cv2.resize(mask, (256, 256), interpolation=cv2.INTER_AREA)
        crop = framer.update(wide_mask, 1 / FPS, W / H)
        pic = Framer.cut(img, crop, PIC)
        pmask = Framer.cut(mask, crop, PIC)
        polys, radial = crowd.update(pmask, 1 / FPS)
        splits += len(crowd.splits)

        # What the bubble shows, and how well it fits the people in it.
        bm = np.zeros((PIC * 2, PIC * 2), np.uint8)
        cv2.fillPoly(bm, [(smooth_polygon(p, 3) * PIC * 2).astype(np.int32) for p in polys], 1, cv2.LINE_AA)
        bm = cv2.resize(bm, (PIC, PIC), interpolation=cv2.INTER_AREA).astype(bool)
        person = pmask > 0.5
        head_zone = person.copy()
        head_zone[int(PIC * 0.8):] = False             # shoulders run off the bottom on purpose
        stats["coverage"].append(float((bm & head_zone).sum() / max(1, head_zone.sum())))
        stats["spill"].append(float((bm & ~person).sum() / max(1, bm.sum())))
        stats["polys"].append(len(polys))
        stats["blobs"].append(len(crowd.blobs))
        stats["area"].append(float(bm.mean()))
        allp = np.vstack(polys)
        stats["edge"].append(float(((allp < 0.03) | (allp > 0.97)).any(1).mean()))

        # Left: the camera view with the framing box. Right: the bubble on a dark backdrop.
        left = cv2.resize(img, (W * 2, H * 2))
        (cx, cy), side = crop
        s = side * H * 2
        x0, y0 = int(cx * W * 2 - s / 2), int(cy * H * 2 - s / 2)
        cv2.rectangle(left, (x0, y0), (int(x0 + s), int(y0 + s)), (60, 220, 255), 2)
        left = np.vstack([left, np.zeros((PIC * 2 - H * 2, W * 2, 3), np.uint8)])
        cv2.putText(left, f"{name}  t={f / FPS:4.1f}s", (10, PIC * 2 - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (230, 230, 230), 1, cv2.LINE_AA)
        big = cv2.resize(pic, (PIC * 2, PIC * 2))
        a = cv2.resize(bm.astype(np.float32), (PIC * 2, PIC * 2))[..., None]
        right = (np.full_like(big, (46, 30, 28)) * (1 - a) + big * a).astype(np.uint8)
        for p in polys:
            cv2.polylines(right, [(smooth_polygon(p, 3) * PIC * 2).astype(np.int32)], True, (255, 255, 255), 2, cv2.LINE_AA)
        info = f"polys {len(polys)}  blobs {len(crowd.blobs)}  cover {stats['coverage'][-1]:.0%}"
        cv2.putText(right, info, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1, cv2.LINE_AA)
        if crowd.splits:
            cv2.putText(right, "SPLIT", (PIC * 2 - 90, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 200, 255), 2, cv2.LINE_AA)
        enc.stdin.write(np.hstack([left, np.zeros((PIC * 2, 24, 3), np.uint8), right]).tobytes())
    enc.stdin.close()
    enc.wait()

    warm = int(1.5 * FPS)            # leave out the blob growing in at the start
    stats = {k: v[warm:] for k, v in stats.items()}
    area = np.array(stats["area"])
    polys = np.array(stats["polys"])
    return {
        "scene": name,
        "seconds": secs,
        "coverage_mean": round(float(np.mean(stats["coverage"])), 3),
        "coverage_min": round(float(np.min(stats["coverage"])), 3),
        "spill_mean": round(float(np.mean(stats["spill"])), 3),
        "jitter": round(float(np.abs(np.diff(area)).mean() / max(area.mean(), 1e-6)), 4),
        "shape_changes": int((np.diff(polys) != 0).sum()),
        "max_polys": int(polys.max()),
        "max_blobs": int(max(stats["blobs"])),
        "splits": splits,
        "edge_touch": round(float(np.mean(stats["edge"])), 3),
        "video": out,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", nargs="?", default="sim-out")
    ap.add_argument("--only")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    names = a.only.split(",") if a.only else list(SCENES)
    rows = [run(n, a.outdir) for n in names]
    with open(os.path.join(a.outdir, "report.json"), "w") as f:
        json.dump(rows, f, indent=1)
    cols = ["scene", "coverage_mean", "coverage_min", "spill_mean", "jitter", "shape_changes", "max_polys", "max_blobs", "splits", "edge_touch"]
    print(" ".join(f"{c[:13]:>13}" for c in cols))
    for r in rows:
        print(" ".join(f"{str(r[c])[:13]:>13}" for c in cols))
