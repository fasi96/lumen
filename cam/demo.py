"""Demo mode: two drawn people act out a routine so the bubble's split, pop,
merge and away droplet can be seen live without anyone else on camera.

    lumen-cam --demo

The camera still shows in the picture, but instead of the camera AI's view of
you, the bubble follows these two figures (drawn see-through on top, so it's
clear what it's following). Coordinates are in the square picture (0..1).
"""
import math

import cv2
import numpy as np

from sim import Person, ease, lerp

LOOP = 26.0   # seconds

# (start, end, caption) — shown in the bubble's corner so each moment is named.
CAPTIONS = [
    (0, 3, "one person"),
    (3, 5.5, "waving"),
    (5.5, 9, "a second person steps out… pop"),
    (9, 12, "both waving"),
    (12, 15, "walking back together… merge"),
    (15, 17, "one bubble again"),
    (17, 19.5, "both walk off"),
    (19.5, 22, "nobody there: droplet"),
    (22, 26, "back again"),
]


def caption(t):
    t %= LOOP
    return next((c for a, b, c in CAPTIONS if a <= t < b), "")


def people(t):
    """The two figures at time t (seconds)."""
    t %= LOOP
    a = Person(0.5, 0.36, 0.075, skin=(120, 150, 235), shirt=(170, 110, 90))
    b = Person(0.5, 0.36, 0.075, skin=(235, 190, 120), shirt=(90, 150, 200))
    b.visible = False

    def seg(t0, t1):
        return ease((t - t0) / (t1 - t0))

    if t < 5.5:
        pass
    elif t < 12:
        b.visible = True
        s = seg(5.5, 9)
        a.x, b.x = lerp(0.5, 0.21, s), lerp(0.5, 0.79, s)
    elif t < 17:
        b.visible = True
        s = seg(12, 15)
        a.x, b.x = lerp(0.21, 0.44, s), lerp(0.79, 0.56, s)
    elif t < 22:
        b.visible = True
        s = seg(17, 19.5)
        a.x, b.x = lerp(0.44, 1.5, s), lerp(0.56, 1.62, s)
    else:
        s = seg(22, 25)
        a.x = lerp(-0.4, 0.5, s)

    def wave(p, t0, t1, phase=0.0):
        if not (t0 <= t < t1):
            return
        up = seg(t0, t0 + 0.4) * (1 - seg(t1 - 0.4, t1))
        swing = math.sin((t - t0) * 2 * math.pi * 1.6 + phase) * 0.9
        p.hands["R"] = (lerp(0.5, 1.2 + swing, up), lerp(1.5, -3.3, up))

    wave(a, 3, 5.5)
    wave(a, 9, 12)
    wave(b, 9.3, 12, 1.5)
    return [p for p in (b, a) if p.visible]


def frame(t, size):
    """The people's mask (size × size, 0..1) and a drawing of them (BGR + alpha) to lay over the camera."""
    mask = np.zeros((size, size), np.float32)
    art = np.zeros((size, size, 3), np.uint8)
    for p in people(t):
        p.draw(mask, art)
    return mask, art


def overlay(img, t):
    """Draw the figures see-through over the camera picture, with a caption."""
    n = img.shape[0]
    mask, art = frame(t, n)
    a = (cv2.GaussianBlur(mask, (0, 0), 1.0) * 0.55)[..., None]
    out = (img * (1 - a) + art * a).astype(np.uint8)
    for p in people(t):
        cv2.circle(out, (int(p.x * n), int(p.y * n)), max(3, n // 60), (255, 255, 255), -1, cv2.LINE_AA)
    return out, cv2.resize(mask, (256, 256), interpolation=cv2.INTER_AREA)
