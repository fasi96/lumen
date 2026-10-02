# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mediapipe", "opencv-python-headless", "numpy"]
# ///
"""Render the blob over a video (or a camera) to judge how it moves.

    uv run proto.py IN.mp4 OUT.mp4 [--crop X,Y,S] [--from SEC] [--to SEC]

Left: the camera crop the blob sees. Right: the blob on a desktop-ish backdrop.
"""
import argparse
import subprocess
import time

import cv2
import numpy as np

from blob import Blob, Segmenter, alpha_mask

ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("out")
ap.add_argument("--crop", help="X,Y,S square crop of the source")
ap.add_argument("--from", dest="t0", type=float, default=0)
ap.add_argument("--to", dest="t1", type=float, default=1e9)
ap.add_argument("--size", type=int, default=540)
args = ap.parse_args()

cap = cv2.VideoCapture(args.src)
fps = cap.get(cv2.CAP_PROP_FPS) or 30
cap.set(cv2.CAP_PROP_POS_MSEC, args.t0 * 1000)
S = args.size
W, H = S * 2, S

# A calm dark backdrop with a soft light, so the blob's edge reads clearly.
yy, xx = np.mgrid[0:H, 0:S].astype(np.float32)
glow = np.exp(-(((xx - S * 0.3) / (S * 0.9)) ** 2 + ((yy - H * 0.2) / (H * 0.9)) ** 2))
bg = np.stack([22 + 40 * glow, 24 + 30 * glow, 40 + 70 * glow], -1)

enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                        "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", args.out],
                       stdin=subprocess.PIPE)
seg, blob = Segmenter(), Blob()
dt = 1 / fps
n, spent = 0, 0.0
while True:
    ok, frame = cap.read()
    if not ok or cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 > args.t1:
        break
    if args.crop:
        x, y, s = map(int, args.crop.split(","))
        frame = frame[y : y + s, x : x + s]
    else:
        h, w = frame.shape[:2]
        s = min(h, w)
        frame = frame[(h - s) // 2 : (h + s) // 2, (w - s) // 2 : (w + s) // 2]
    rgb = cv2.cvtColor(cv2.resize(frame, (S, S), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB)

    t = time.perf_counter()
    mask = seg(rgb, dt)
    blob.update(mask, dt)
    a = alpha_mask(blob.outline(), S)[..., None]
    spent += time.perf_counter() - t
    n += 1

    # Soft drop shadow under the blob, then the camera cut to the blob.
    shadow = cv2.GaussianBlur(np.roll(a[..., 0], 10, 0), (0, 0), 14)[..., None] * 0.55
    right = bg * (1 - shadow) * (1 - a) + rgb * a
    left = rgb.astype(np.float32)
    edge = cv2.resize(mask, (S, S)) > 0.5
    left_dbg = left.copy()
    left_dbg[edge] = left_dbg[edge] * 0.75 + np.array([139, 140, 246]) * 0.25   # tint = what the model sees as "you"
    enc.stdin.write(np.hstack([left_dbg, right]).clip(0, 255).astype(np.uint8).tobytes())

enc.stdin.close()
enc.wait()
print(f"{n} frames, {spent / max(n, 1) * 1000:.1f} ms per frame (segment + blob + mask)")
