# Two fake people drift apart and back: expect one blob → split → two → merge → one.
import sys, time; sys.path.insert(0, __import__("os").path.dirname(__file__))
import numpy as np, cv2
from blob import Crowd
c = Crowd(); log = []
t0 = time.perf_counter()
for f in range(150):
    sep = min(0.6, 0.05 + f * 0.006)          # two heads drift apart
    m = np.zeros((256, 256), np.float32)
    for x in (0.5 - sep / 2, 0.5 + sep / 2):
        cv2.circle(m, (int(x * 256), 112), 38, 1, -1)                    # head
        cv2.ellipse(m, (int(x * 256), 230), (60, 70), 0, 0, 360, 1, -1)  # shoulders
    polys, radial = c.update(m, 1 / 30)
    if f % 15 == 0 or c.splits:
        log.append(f"f{f:3d} sep={sep:.2f} blobs={len(c.blobs)} polys={len(polys)} radial={radial} splits={len(c.splits)}")
for f in range(90):                            # and back together
    sep = max(0.05, 0.6 - f * 0.01)
    m = np.zeros((256, 256), np.float32)
    for x in (0.5 - sep / 2, 0.5 + sep / 2):
        cv2.circle(m, (int(x * 256), 112), 38, 1, -1)
        cv2.ellipse(m, (int(x * 256), 230), (60, 70), 0, 0, 360, 1, -1)
    polys, radial = c.update(m, 1 / 30)
    if f % 15 == 0: log.append(f"back f{f:3d} sep={sep:.2f} blobs={len(c.blobs)} polys={len(polys)}")
print("\n".join(log)); print(f"{(time.perf_counter()-t0)/240*1000:.1f} ms/frame")
