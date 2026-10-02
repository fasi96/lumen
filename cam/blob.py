"""The camera blob: soft outlines that hug the people in the frame.

Every frame, a person-segmentation model marks which pixels are people. Each
person gets a Blob: a radius per angle around their head, so it bulges out to
take in a raised hand or leans with the head, and never folds into odd shapes.
Radii move on springs that overshoot going out and wobble coming back, which
is what makes it feel like jelly instead of a traced silhouette.

With more than one person, the blobs are joined like drops of liquid (a smooth
union of their distance fields): close together they are one gooey shape; as
people move apart the join stretches thin and snaps, and each keeps their own.

Coordinates are normalised to the square picture: (0, 0) top-left, 1 = side.
"""
import math
import os

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "models", "selfie_segmenter.tflite")


class Segmenter:
    """MediaPipe selfie segmenter: RGB frame → person mask (0..1 floats, 256×256)."""

    def __init__(self):
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import ImageSegmenter, ImageSegmenterOptions, RunningMode

        opts = ImageSegmenterOptions(base_options=BaseOptions(model_asset_path=MODEL),
                                     running_mode=RunningMode.VIDEO, output_confidence_masks=True)
        self.seg = ImageSegmenter.create_from_options(opts)
        self.t_ms = 0

    def __call__(self, rgb, dt):
        import mediapipe as mp

        self.t_ms += max(1, int(dt * 1000))
        small = np.ascontiguousarray(cv2.resize(rgb, (256, 256), interpolation=cv2.INTER_AREA))
        res = self.seg.segment_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=small), self.t_ms)
        return np.squeeze(res.confidence_masks[0].numpy_view()).astype(np.float32)


class Finder(Segmenter):
    """The person segmenter plus a face detector, for "Center me"."""

    FACE_MODEL = os.path.join(HERE, "models", "blaze_face_short_range.tflite")

    def __init__(self):
        super().__init__()
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions

        self.faces = FaceDetector.create_from_options(FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=self.FACE_MODEL), min_detection_confidence=0.4))

    def face(self, bgr):
        """Centre of the biggest face, as fractions of the frame, or None."""
        import mediapipe as mp

        h, w = bgr.shape[:2]
        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        found = self.faces.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)).detections
        if not found:
            return None
        b = max(found, key=lambda d: d.bounding_box.width * d.bounding_box.height).bounding_box
        return np.array([(b.origin_x + b.width / 2) / w, (b.origin_y + b.height / 2) / h])


def _circular_smooth(x, sigma):
    r = int(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.concatenate([x[-r:], x, x[:r]]), k, "valid") if x.ndim == 1 else \
        np.stack([_circular_smooth(x[:, i], sigma) for i in range(x.shape[1])], 1)


class Blob:
    N = 96            # rays around the centre
    BASE = 0.27       # resting radius around the face (for one person filling the frame)
    PAD = 0.05        # breathing room between the person's edge and the blob's edge
    GRID = 128        # the mask is sampled at this resolution

    def __init__(self, centre=(0.5, 0.5), radii=None, base=None):
        a = np.linspace(0, 2 * math.pi, self.N, endpoint=False)
        self.cos, self.sin = np.cos(a), np.sin(a)
        down = np.clip(self.sin, 0, 1)                  # +y is down
        # How far each ray may reach: hands go almost to the frame's edge; the
        # rays pointing down only see shoulders, which run off the frame.
        self.rmax = 0.49 - 0.14 * down ** 2
        self.shape = 1 + 0.06 * -self.sin               # a touch taller than wide
        self.stretch = np.clip(1 - 1.6 * down, 0, 1)
        self.base_r = self.BASE if base is None else base
        self.r = self.base_r * self.shape if radii is None else np.array(radii, float)
        self.v = np.zeros(self.N)
        self.c = np.array(centre, float)
        self.cv = np.zeros(2)
        self.t = 0.0
        self.steps = np.linspace(0, 0.5, 72)
        self.dying = False
        self.absorb_to = None
        self.missed = 0      # frames in a row nobody matched this blob
        self.seen = np.array(centre, float)   # the head it follows: where it's heading, not where it is
        self.last_mask = None                 # the person mask it followed last frame

    @property
    def base(self):
        return self.base_r * self.shape

    def _targets(self, mask, c, limit=None):
        g = self.GRID
        m = mask if mask.shape[0] == g else cv2.resize(mask, (g, g), interpolation=cv2.INTER_AREA)
        # Sample every ray at every step in one go.
        xs = c[0] + self.cos[:, None] * self.steps[None, :]
        ys = c[1] + self.sin[:, None] * self.steps[None, :]
        inside = (xs >= 0) & (xs < 1) & (ys >= 0) & (ys < 1)
        xi = np.clip((xs * g).astype(int), 0, g - 1)
        yi = np.clip((ys * g).astype(int), 0, g - 1)
        hit = (m[yi, xi] > 0.5) & inside
        # Outermost person pixel along each ray (0 when the ray sees nobody).
        idx = np.where(hit.any(1), hit.shape[1] - 1 - np.argmax(hit[:, ::-1], 1), 0)
        reach = np.where(hit.any(1), self.steps[idx] + self.PAD, 0)
        # Smooth around the circle: enough that a finger isn't a spike, little
        # enough that a hand still reads as a hand-shaped bulge.
        reach = _circular_smooth(reach, 2.4)
        extra = np.maximum(reach - self.base, 0) * self.stretch
        top = self.rmax if limit is None else np.minimum(self.rmax, limit)
        return np.clip(self.base + extra, 0, top)

    @staticmethod
    def head(mask):
        """Where someone's head is in their mask: the centre of its top part."""
        rows = mask.sum(1)
        if rows.max() < 1:
            return None
        ys = np.nonzero(rows > rows.max() * 0.15)[0]
        top, bottom = ys[0], ys[-1]
        band = mask[top : top + max(2, int((bottom - top) * 0.45))]
        w = band.sum()
        if w < 1:
            return None
        yy, xx = np.mgrid[0 : band.shape[0], 0 : band.shape[1]]
        g = mask.shape[0]
        return np.array([(xx * band).sum() / w / g, (top + (yy * band).sum() / w) / g + 0.06])

    def update(self, mask, dt, head=None, limit=None):
        dt = min(dt, 1 / 20)
        self.t += dt
        if self.dying:
            # Shrink away, drifting into whoever absorbed it.
            target = np.zeros(self.N)
            ct = self.absorb_to if self.absorb_to is not None else self.c
        else:
            ct = head if head is not None else (self.head(mask) if mask is not None else None)
            ct = np.clip(ct if ct is not None else np.array([0.5, 0.5]), 0.12, 0.88)
            target = self._targets(mask, self.c, limit) if mask is not None else self.base
        # The centre follows the head gently, so the whole blob leans with it.
        k = 30.0
        self.cv += (k * (ct - self.c) - 2 * 0.8 * math.sqrt(k) * self.cv) * dt
        self.c += self.cv * dt
        # Springs: fast and jiggly going out, wobbly coming back.
        out = target > self.r
        k = np.where(out, 230.0, 70.0)
        damp = np.where(out, 0.32, 0.45) * 2 * np.sqrt(k)
        self.v += (k * (target - self.r) - damp * self.v) * dt
        self.r = np.maximum(self.r + self.v * dt, 0)
        # Never past the picture's edge, or the blob gets a flat side; squeeze softly instead.
        m = 0.02
        with np.errstate(divide="ignore"):
            bx = np.where(self.cos > 0, (1 - m - self.c[0]) / self.cos, np.where(self.cos < 0, (m - self.c[0]) / self.cos, np.inf))
            by = np.where(self.sin > 0, (1 - m - self.c[1]) / self.sin, np.where(self.sin < 0, (m - self.c[1]) / self.sin, np.inf))
        edge = np.minimum(bx, by)
        self.r = np.where(self.r > edge - 0.03, edge - 0.03 * np.exp(-(self.r - edge + 0.03) / 0.03), self.r)

    def outline(self):
        """Blob outline in normalised coords, with a slow living wobble on top."""
        a = np.linspace(0, 2 * math.pi, self.N, endpoint=False)
        t = self.t
        wobble = 0.009 * (np.sin(3 * a + 1.3 * t) + 0.7 * np.sin(5 * a - 0.9 * t + 1) + 0.5 * np.sin(2 * a + 0.6 * t + 2))
        r = np.maximum(self.r + wobble * (self.r > 0.02), 0)
        return np.stack([self.c[0] + self.cos * r, self.c[1] + self.sin * r], 1)


class Crowd:
    """One blob per person, joined like liquid drops.

    update() returns (polygons, radial): with one person a single radial outline
    (radial=True, so the card shape can morph from it); with more, the outlines of
    their smooth union. `splits` lists where a blob just broke in two, for the
    window's splash of droplets; `away` is true while nobody is in view.
    """

    MIN_AREA = 0.03      # a person is at least this much of the picture
    BACKGROUND = 0.4     # someone under this fraction of the main person's size is in the background: ignored
    FIELD = 176          # resolution of the union's distance field
    GOO = 0.035          # how far apart two blobs reach for each other…
    GOO_JOINED = 0.07    # …and once joined, how far they stretch before the join snaps (sticky)
    STEADY = 14          # frames (~½ s) someone must be there, or gone, before blobs split or merge
    AWAY_R = 0.1         # the droplet the bubble shrinks to while nobody's there

    def __init__(self):
        self.blobs = []
        self.pending = []    # (head, frames seen) of people who aren't blobs yet
        self.splits = []
        self.joined = True
        self.empty = 0       # frames in a row with nobody in view
        self.away = False
        self.base = Blob.BASE    # the resting circle around one person (the Room setting)

    def people(self, mask):
        """Who is in the picture: [(head, mask, resting radius, area)], biggest first."""
        g = Blob.GRID
        m = cv2.resize(mask, (g, g), interpolation=cv2.INTER_AREA)
        b = (cv2.GaussianBlur(m, (0, 0), 1.2) > 0.5).astype(np.uint8)
        # Close small gaps so a thin arm doesn't cut a hand off.
        b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
        n, labels, stats, cents = cv2.connectedComponentsWithStats(b)
        parts = [dict(ids=[i], area=stats[i, cv2.CC_STAT_AREA] / (g * g),
                      x0=stats[i, cv2.CC_STAT_LEFT], x1=stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH],
                      c=cents[i] / g) for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] / (g * g) > 0.002]
        # A head and a body split apart (a dark collar, a scarf) are one person:
        # stacked parts that overlap sideways get joined.
        big = sorted([p for p in parts if p["area"] >= self.MIN_AREA * 0.5], key=lambda p: -p["area"])
        persons = []
        for p in big:
            for q in persons:
                ov = min(p["x1"], q["x1"]) - max(p["x0"], q["x0"])
                if ov > 0.5 * min(p["x1"] - p["x0"], q["x1"] - q["x0"]):
                    q["ids"] += p["ids"]
                    q["area"] += p["area"]
                    q["x0"], q["x1"] = min(p["x0"], q["x0"]), max(p["x1"], q["x1"])
                    break
            else:
                persons.append(dict(p, ids=list(p["ids"])))
        persons = [q for q in persons if q["area"] >= self.MIN_AREA]
        if not persons:
            return []
        # Someone much smaller than the main person is walking by in the background.
        main = max(q["area"] for q in persons)
        persons = [q for q in persons if q["area"] >= self.BACKGROUND * main]
        # Loose bits (a hand the segmenter cut off its arm) belong to the nearest person.
        for p in parts:
            if any(p["ids"][0] in q["ids"] for q in persons) or p["area"] >= self.MIN_AREA * 0.5:
                continue
            near = min(persons, key=lambda q: abs((q["x0"] + q["x1"]) / 2 / g - p["c"][0]))
            if abs((near["x0"] + near["x1"]) / 2 / g - p["c"][0]) < 0.35:
                near["ids"].append(p["ids"][0])
        out = []
        for q in persons:
            pm = np.isin(labels, q["ids"]).astype(np.float32)
            head = Blob.head(pm)
            if head is not None:
                # On your own you get the room's resting circle; with others, each
                # gets one sized to them (someone further back gets a smaller one).
                base = self.base if len(persons) == 1 else float(np.clip(math.sqrt(q["area"]) * 0.5, 0.12, self.base))
                out.append((head, pm, base, q["area"]))
        out.sort(key=lambda p: -p[3])
        return out[:4]

    def limits(self, b, others):
        """Rays of b that point at a neighbour stop short, so each bubble stays round on that side."""
        lim = np.full(Blob.N, np.inf)
        a = np.linspace(0, 2 * math.pi, Blob.N, endpoint=False)
        for o in others:
            d = o.c - b.c
            dist = float(np.hypot(*d))
            if dist < 1e-3:
                continue
            toward = np.cos(a - math.atan2(d[1], d[0]))
            cap = np.where(toward > 0.5, max(b.base_r, dist * 0.46) / np.maximum(toward, 0.5) * 0.9, np.inf)
            lim = np.minimum(lim, cap)
        return lim

    def update(self, mask, dt):
        self.splits = []
        found = self.people(mask) if mask is not None else []
        live = [b for b in self.blobs if not b.dying]
        # Match people to blobs by the head each blob follows.
        pairs, used = [], set()
        for pi, (head, *_rest) in enumerate(found):
            best = min(((np.hypot(*(b.seen - head)), bi) for bi, b in enumerate(live) if bi not in used), default=None)
            # While away, whoever shows up is who the droplet grows back into.
            if best and (best[0] < 0.3 or self.away):
                used.add(best[1])
                pairs.append((pi, live[best[1]]))
        matched = {pi for pi, _ in pairs}
        many = len(found) > 1

        # Someone new. If they're splitting off a blob (their pixels were part of
        # what it followed a moment ago), that blob keeps covering them until they
        # get their own, so nothing vanishes. Anyone else waits unseen until they've
        # been there a moment, so a flicker at the edge never gets a bubble.
        extra, pend, spawn = {}, [], []
        for pi, (head, pm, base, _) in enumerate(found):
            if pi in matched:
                continue
            seen = next((n for h, n in self.pending if np.hypot(*(h - head)) < 0.15), 0) + 1
            share = [(float((pm * b.last_mask).sum()) / max(1.0, float(pm.sum())), b)
                     for b in live if b.last_mask is not None]
            ov, src = max(share, key=lambda x: x[0], default=(0.0, None))
            src = src if ov > 0.3 and not self.away else None
            # Someone coming out of a blob is clearly real: they get theirs almost at once.
            if seen < (4 if src is not None else self.STEADY) and live:
                pend.append((head, seen))
                if src is not None:
                    extra.setdefault(id(src), []).append(pm)
                continue
            spawn.append((head, pm, base, src))
        self.pending = pend

        for pi, b in pairs:
            head, pm, base, _ = found[pi]
            for more in extra.get(id(b), []):
                pm = np.maximum(pm, more)
            b.base_r += (base - b.base_r) * min(1, dt * 3)
            b.missed = 0
            b.seen = head
            b.last_mask = pm
            others = [o for o in live if o is not b] if many else []
            b.update(pm, dt, head, self.limits(b, others) if others else None)

        for head, pm, base, src in spawn:
            child = Blob(head, None, base)
            if src is not None:
                # It starts as the lobe of the parent that was already covering them;
                # the parent lets go, the goo between them thins, and snaps.
                child.r = child._targets(pm, child.c)
            else:
                child.r = np.zeros(Blob.N)        # first person, or back from away: grows in
            child.seen = head
            child.last_mask = pm
            child.update(pm, dt, head)
            self.blobs.append(child)

        # Someone gone (or merged into another): after a moment, shrink into the nearest blob.
        for bi, b in enumerate(live):
            if bi in used:
                continue
            b.missed += 1
            if b.missed < self.STEADY and len(live) > 1:
                b.t += dt            # hold still for now; they may flicker back
                continue
            others = [o for o in self.blobs if o is not b and not o.dying]
            if others:
                b.dying = True
                b.absorb_to = min(others, key=lambda o: np.hypot(*(o.c - b.c))).c
            else:
                b.update(None, dt)       # nobody at all: see below
        for b in self.blobs:
            if b.dying:
                b.update(None, dt)
        self.blobs = [b for b in self.blobs if not (b.dying and b.r.mean() < 0.01)]
        if not self.blobs:
            self.blobs = [Blob(base=self.base)]

        # Nobody in view for a moment: shrink to a small droplet until someone's back.
        self.empty = 0 if found else self.empty + 1
        self.away = self.empty > self.STEADY
        if self.away:
            for b in self.blobs:
                b.base_r += (self.AWAY_R - b.base_r) * min(1, dt * 4)
                b.seen = b.c.copy()

        if len(self.blobs) == 1:
            self.last_polys = [self.blobs[0].outline()]
            return self.last_polys, True
        return self.union(), False

    def union(self):
        """Outlines of the blobs' smooth union: gooey when close, separate when apart."""
        g = self.FIELD
        fields = []
        for b in self.blobs:
            m = np.zeros((g, g), np.uint8)
            cv2.fillPoly(m, [(smooth_polygon(b.outline(), 2) * g).astype(np.int32)], 1)
            if not m.any():
                continue
            inside = cv2.distanceTransform(m, cv2.DIST_L2, 3)
            outside = cv2.distanceTransform(1 - m, cv2.DIST_L2, 3)
            fields.append((outside - inside) / g)
        if not fields:
            return [self.blobs[0].outline()]
        # Sticky goo: once joined, the join holds until they're further apart.
        k = self.GOO_JOINED if self.joined else self.GOO
        f = np.stack(fields)
        lo = f.min(0)
        field = lo - k * np.log(np.exp(-(f - lo) / k).sum(0))      # smooth minimum
        field = cv2.resize(field, (g * 2, g * 2), interpolation=cv2.INTER_LINEAR)
        cs, _ = cv2.findContours((field < 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        polys = []
        for c in cs:
            if cv2.contourArea(c) < (g * 2) ** 2 * 0.004:
                continue
            c = c[:, 0, :].astype(np.float32)
            # Even spacing, then smoothing, so the pixel stair-steps disappear.
            seg = np.hypot(*np.diff(np.vstack([c, c[:1]]), axis=0).T)
            s = np.concatenate([[0], np.cumsum(seg)])
            u = np.linspace(0, s[-1], 110, endpoint=False)
            closed = np.vstack([c, c[:1]])
            p = np.stack([np.interp(u, s, closed[:, 0]), np.interp(u, s, closed[:, 1])], 1)
            polys.append(_circular_smooth(p, 1.5) / (g * 2))
        self.joined = len(polys) == 1
        # The goo just snapped: two blobs that shared one shape last frame are in
        # different shapes now. Splash where they were joined.
        def shape_of(pt, ps):
            return next((k for k, q in enumerate(ps) if cv2.pointPolygonTest(q.astype(np.float32), tuple(map(float, pt)), False) >= 0), None)
        live = [b for b in self.blobs if not b.dying and b.r.mean() > 0.05]
        before = getattr(self, "last_polys", [])
        for k, a in enumerate(live):
            for b in live[k + 1:]:
                was = shape_of(a.c, before), shape_of(b.c, before)
                now = shape_of(a.c, polys), shape_of(b.c, polys)
                if was[0] is not None and was[0] == was[1] and now[0] != now[1]:
                    self.splits.append(((a.c + b.c) / 2).tolist())
        self.last_polys = polys
        return polys or [self.blobs[0].outline()]


def smooth_polygon(pts, sub=4):
    """Closed Catmull-Rom through pts, so the edge is round rather than faceted."""
    p0, p1, p2, p3 = np.roll(pts, 1, 0), pts, np.roll(pts, -1, 0), np.roll(pts, -2, 0)
    out = []
    for s in np.linspace(0, 1, sub, endpoint=False):
        s2, s3 = s * s, s * s * s
        out.append(0.5 * (2 * p1 + (-p0 + p2) * s + (2 * p0 - 5 * p1 + 4 * p2 - p3) * s2 + (-p0 + 3 * p1 - 3 * p2 + p3) * s3))
    return np.stack(out, 1).reshape(-1, 2)


def alpha_mask(outline, size, ss=2):
    """Anti-aliased 0..1 mask of the outline at size×size pixels."""
    big = np.zeros((size * ss, size * ss), np.uint8)
    pts = (smooth_polygon(outline) * size * ss).astype(np.int32)
    cv2.fillPoly(big, [pts], 255, cv2.LINE_AA)
    return cv2.resize(big, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32) / 255


class Framer:
    """Auto-framing: a square crop of the wide camera view that follows the people in it.

    One person: head, shoulders and a bit of room. More: it widens to fit
    everyone. The crop eases toward its target, so moving around pans smoothly.
    Boxes are in pixels of the full frame: (x, y, side).
    """

    ZOOM = 1.12          # one person: crop side = frame height / ZOOM (the Room setting changes it)

    def __init__(self):
        self.zoom = self.ZOOM
        self.c = None        # crop centre (normalised to frame width / height)
        self.v = np.zeros(2)
        self.side = None     # crop side as a fraction of frame height
        self.sv = 0.0

    def update(self, mask, dt, aspect):
        dt = min(dt, 0.05)
        side = 1 / max(1.0, self.zoom)
        target = np.array([0.5, 0.55])
        if mask is not None:
            m = mask[: mask.shape[0] * 3 // 4]         # heads and shoulders, not laps
            cols = m.sum(0)
            w = m.sum()
            if w > 80:
                xs = np.nonzero(cols > cols.max() * 0.08)[0]
                x0, x1 = xs[0] / mask.shape[1], xs[-1] / mask.shape[1]
                rows = m.sum(1)
                head_top = np.argmax(rows > rows.max() * 0.25) / mask.shape[0]
                # Width of everyone, in frame-height units; widen the crop to fit them.
                span = (x1 - x0) * aspect
                side = float(np.clip(max(side, span * 1.25), side, 1.0))
                cx = (x0 + x1) / 2 if span > 0.6 else (np.arange(len(cols)) * cols).sum() / cols.sum() / mask.shape[1]
                target = np.array([cx, head_top + side * 0.36])
                # Hands up above the head: move up, and zoom out if needed, so they stay in.
                reach_top = np.argmax(rows > 1.5) / mask.shape[0]
                if reach_top < head_top - 0.03:
                    side = float(np.clip(max(side, (head_top - reach_top + 0.04) / 0.36), side, 1.0))
                    target = np.array([cx, reach_top - 0.04 + side / 2])
        if self.c is None:
            self.c, self.side = target.copy(), side
        k = 10.0
        self.v += (k * (target - self.c) - 2 * 0.95 * math.sqrt(k) * self.v) * dt
        self.c += self.v * dt
        self.sv += (6.0 * (side - self.side) - 2 * 0.95 * math.sqrt(6.0) * self.sv) * dt
        self.side += self.sv * dt
        half_w = self.side / 2 / aspect
        self.c = np.clip(self.c, [half_w, self.side / 2], [1 - half_w, 1 - self.side / 2])
        return self.c, self.side

    @staticmethod
    def cut(img, crop, out):
        """Cut the crop (centre, side from update) out of the frame into out×out, sub-pixel smooth."""
        h, w = img.shape[:2]
        (cx, cy), side = crop
        s = side * h
        k = out / s
        m = np.array([[k, 0, -(cx * w - s / 2) * k], [0, k, -(cy * h - s / 2) * k]], np.float32)
        return cv2.warpAffine(img, m, (out, out), flags=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
