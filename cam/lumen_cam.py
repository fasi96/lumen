# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mediapipe", "opencv-python-headless", "numpy", "pygobject", "pycairo"]
# ///
"""Lumen's camera bubble: your webcam in a blob that floats over everything.

    lumen-cam [--device /dev/video0] [--src VIDEO --crop X,Y,S]

It is a layer-shell overlay, so screen recordings pick it up like any other
pixel. The camera thread works out *what* the blob looks like (the picture and
an outline that hugs you); the window draws it where you put it. Drag to move
it (it stays where you drop it), scroll to resize, hover for the toolbar.
Must start with libgtk4-layer-shell preloaded (the lumen-cam wrapper does it).
"""
import argparse
import colorsys
import json
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import cairo
import cv2
import gi
import numpy as np

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import GLib, Gtk, Gtk4LayerShell as Layer  # noqa: E402

cv2.setNumThreads(2)
from blob import Crowd, Finder, Framer, Segmenter, smooth_polygon  # noqa: E402

STATE = os.path.expanduser("~/.config/gif-record/cam.json")
REC_CONF = os.path.expanduser("~/.config/gif-record/settings.json")
SIZES = [150, 250, 350]          # small, medium, large (logical px)
WIN = int(SIZES[-1] * 1.7)       # the window leaves room to stretch and bounce
INSET = 22                       # gap kept between the bubble and the screen edge
EFFECTS = ["plain", "glow", "rainbow", "fire"]
SIZE_NAMES = ["Small", "Medium", "Large"]
SHAPES = ["Blob", "Circle", "Card"]      # blob follows you; circle is the classic bubble; card is a portrait tile
REC_STATE = os.path.expanduser("~/.cache/gif-record/state.json")
CONTROL = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/lumen-cam.sock"   # lumen-cam hide|show|place …
HYPR_SOCKET = f"{os.environ.get('XDG_RUNTIME_DIR', '')}/hypr/{os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')}/.socket.sock"
# How much space you get around you: (name, framing zoom, resting circle radius).
ROOMS = [("Snug", 1.35, 0.23), ("Normal", 1.12, 0.28), ("Roomy", 1.0, 0.36)]


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(**kw):
    s = load_state() | kw
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(s, f)


class Profile:
    """LUMEN_PROFILE=1: every 5 s, print how much of a CPU core each stage takes."""

    def __init__(self):
        self.on = bool(os.environ.get("LUMEN_PROFILE"))
        self.spent, self.t0, self.lock = {}, time.perf_counter(), threading.Lock()

    def start(self):
        # Processor time of this thread, so waiting for the camera doesn't count as work.
        last = [time.thread_time()]

        def lap(name):
            now = time.thread_time()
            if self.on:
                with self.lock:
                    self.spent[name] = self.spent.get(name, 0.0) + now - last[0]
            last[0] = now
        return lap

    def report(self):
        now = time.perf_counter()
        if not self.on or now - self.t0 < 5:
            return
        with self.lock:
            wall, rows = now - self.t0, sorted(self.spent.items(), key=lambda kv: -kv[1])
            self.spent, self.t0 = {}, now
        total = sum(v for _, v in rows)
        print(f"── {total / wall * 100:.0f}% of a core (processor time) in measured stages", file=sys.stderr)
        for name, v in rows:
            print(f"   {v / wall * 100:5.1f}%  {name}", file=sys.stderr)
        sys.stderr.flush()


PROF = Profile()


class Spring:
    """A value that chases a target like a weight on a spring (per axis if it's a vector)."""

    def __init__(self, value, k, ratio):
        self.x = np.array(value, float)
        self.v = np.zeros_like(self.x)
        self.target = self.x.copy()
        self.k, self.ratio = k, ratio

    def step(self, dt):
        c = 2 * self.ratio * math.sqrt(self.k)
        self.v += (self.k * (self.target - self.x) - c * self.v) * dt
        self.x += self.v * dt
        return self.x


# ───────────────────────── camera → picture + outline

class Source(threading.Thread):
    def __init__(self, args):
        super().__init__(daemon=True)
        self.args = args
        self.px = 400                # rendered pixel size; the window sets it for its scale
        self.running = True
        self.frame = None            # (BGRA bytes, px, polygons, radial, centre)
        self.splits = []             # where a blob just broke in two (picture coords)
        self.caption = ""            # demo mode: what's happening right now
        self.room = ROOMS[1]         # the window sets this from the Room setting
        v = load_state().get("view")
        self.view = np.array(v if v else [0.5, 0.5], float)   # where the still view is centred (fractions of the camera frame)
        self.view_to = self.view.copy()
        self.center_request = False
        self.notice = None           # a short message for the window to show ("Centered on you")
        self.seq = 0
        self.lock = threading.Lock()
        self.done = threading.Event()   # set once the thread has stopped and let go of the camera
        self.cap = None

    def open(self):
        a = self.args
        if a.src:
            return cv2.VideoCapture(a.src), True
        cap = cv2.VideoCapture(a.device, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 30)
        return cap, False

    def run_demo(self):
        # Demo mode: the camera is only the backdrop; two drawn people drive the blob.
        import demo
        cap, _ = self.open()
        crowd = Crowd()
        t0 = last = time.perf_counter()
        backdrop = None
        while self.running:
            ok, frame = cap.read() if cap.isOpened() else (False, None)
            now = time.perf_counter()
            dt, last = now - last, now
            px = self.px
            if ok:
                h, w = frame.shape[:2]
                s = min(h, w)
                frame = cv2.flip(frame[(h - s) // 2 : (h + s) // 2, (w - s) // 2 : (w + s) // 2], 1)
                backdrop = cv2.resize(frame, (px, px), interpolation=cv2.INTER_AREA)
            elif backdrop is None or backdrop.shape[0] != px:
                backdrop = np.full((px, px, 3), (60, 52, 48), np.uint8)
                time.sleep(1 / 30)
            crowd.base = self.room[2]
            img, mask = demo.overlay(backdrop, now - t0)
            polys, radial = crowd.update(mask, dt)
            self.caption = demo.caption(now - t0)
            self.publish(img, polys, radial, crowd)
        cap.release()

    def run(self):
        try:
            self.run_demo() if self.args.demo else self.run_camera()
        finally:
            self.done.set()

    def run_camera(self):
        cap, is_file = self.open()
        self.cap = cap
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        seg, crowd = Segmenter(), Crowd()
        finder = None                # a second camera AI, only woken by "Center me"
        looks = 0
        last = time.perf_counter()
        while self.running:
            lap = PROF.start()
            ok, frame = cap.read()
            lap("camera: read + decode")
            now = time.perf_counter()
            dt, last = now - last, now
            if not ok:
                if is_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                self.publish(None, *crowd.update(None, 0.5), crowd)      # camera gone: show the "no camera" blob
                time.sleep(0.5)
                cap.release()
                cap, is_file = self.open()
                self.cap = cap
                continue
            if self.args.crop:
                x, y, s = map(int, self.args.crop.split(","))
                frame = frame[y : y + s, x : x + s]
            px = self.px
            if not is_file:
                frame = cv2.flip(frame, 1)    # a mirror, like every video call
                # A still view: the middle of the camera, zoomed by the Room setting. The
                # picture never pans; only the bubble's shape moves to follow you.
                crowd.base = self.room[2]
                h, w = frame.shape[:2]
                side = 1 / max(1.0, self.room[1])
                if self.center_request:
                    # The camera AI settles over a few frames; look for a fifth of a second, then decide.
                    finder = finder or Finder()
                    looks = looks + 1 if looks else 1
                    found = self.center_on(finder, frame, side, w / h, final=looks >= 6)
                    if found or looks >= 6:
                        self.center_request, looks = False, 0
                # Slide to a new centre smoothly rather than jumping.
                self.view += (self.view_to - self.view) * min(1, dt * 6)
                img = Framer.cut(frame, (self.clamp_view(self.view, side, w / h), side), px)
                lap("camera: flip + framing crop")
            else:
                h, w = frame.shape[:2]
                s = min(h, w)
                img = cv2.resize(frame[(h - s) // 2 : (h + s) // 2, (w - s) // 2 : (w + s) // 2], (px, px), interpolation=cv2.INTER_AREA)
            person = seg(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), dt)
            lap("camera: camera AI (person outline)")
            polys, radial = crowd.update(person, dt)
            lap("camera: blob shape + springs")
            self.publish(img, polys, radial, crowd)
            lap("camera: hand the frame to the window")
            PROF.report()
            if is_file:   # play files at their own speed
                time.sleep(max(0, 1 / fps - (time.perf_counter() - now)))
        cap.release()

    @staticmethod
    def clamp_view(c, side, aspect):
        """Keep the square view inside the camera frame."""
        half_w = side / 2 / aspect
        return np.clip(c, [half_w, side / 2], [1 - half_w, 1 - side / 2])

    def center_on(self, finder, frame, side, aspect, final):
        """Centre the still view on whoever is in front of the camera right now. True once found.

        Looks for a face first (it works however you're sitting or lying); if there's
        no face to see, falls back to the top of the biggest person's outline.
        """
        head = finder.face(frame)
        if head is None:
            wide = cv2.resize(frame, (256, 256), interpolation=cv2.INTER_AREA)
            look = Crowd()
            look.MIN_AREA = 0.006        # in the whole camera view you can be quite small
            people = look.people(finder(cv2.cvtColor(wide, cv2.COLOR_BGR2RGB), 1 / 30))
            head = people[0][0] if people else None
        if head is None:
            if final:
                self.notice = "Couldn't find you"
            return False
        hx, hy = head                    # fractions of the frame
        # Head a little above the middle, like a video call.
        self.view_to = self.clamp_view(np.array([hx, hy + side * 0.1]), side, aspect)
        save_state(view=self.view_to.tolist())
        self.notice = "Centered on you"
        return True

    def publish(self, img, polys, radial, crowd):
        px = self.px
        if img is None:
            img = np.full((px, px, 3), (46, 38, 34), np.uint8)
            cv2.putText(img, "No camera", (px // 2 - px // 4, px // 2 + px // 40), cv2.FONT_HERSHEY_SIMPLEX,
                        px / 400, (210, 210, 210), max(1, px // 200), cv2.LINE_AA)
        bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        with self.lock:
            centre = crowd.blobs[0].c.copy() if radial else np.array([0.5, 0.5])
            self.frame = (bgra.tobytes(), px, polys, radial, centre)
            self.splits += crowd.splits
            self.seq += 1


class MicLevel(threading.Thread):
    """How loud you are right now (0..1), read from the recorder's mic.

    Only while a recording runs: the recorder has the mic open then anyway, and
    holding a Bluetooth mic open the rest of the time would switch the headset
    into call mode and make music sound worse.
    """

    REC_STATE = os.path.expanduser("~/.cache/gif-record/state.json")

    def __init__(self):
        super().__init__(daemon=True)
        self.level = 0.0
        self.running = True

    def recording(self):
        try:
            with open(self.REC_STATE) as f:
                st = json.load(f)
            with open(REC_CONF) as f:
                conf = json.load(f)
        except (OSError, ValueError):
            return False
        return st.get("status") == "recording" and st.get("format") == "mp4" and conf.get("audio", "none") != "none"

    def run(self):
        while self.running:
            if not self.recording():
                self.level *= 0.8
                time.sleep(0.5)
                continue
            try:
                mic = json.loads(subprocess.run(["gif-record", "mics"], capture_output=True, text=True).stdout)["mic"]
            except Exception:
                mic = None
            cmd = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"] + (["--target", mic] if mic else [])
            p = subprocess.Popen(cmd + ["-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            floor, n = -50.0, 0
            while self.running:
                chunk = p.stdout.read(640)        # 20 ms
                if not chunk:
                    break
                n += 1
                if n % 25 == 0 and not self.recording():
                    break
                x = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768
                db = 20 * math.log10(max(1e-5, float(np.sqrt((x * x).mean()))))
                floor = min(floor + 0.02, db) if db < floor + 6 else floor + 0.005   # track the room's noise floor
                target = min(1.0, max(0.0, (db - floor - 8) / 22))
                self.level += (target - self.level) * (0.5 if target > self.level else 0.12)
            p.terminate()
            self.level = 0.0


# ───────────────────────── the floating window

class Bubble(Gtk.Application):
    def __init__(self, args):
        super().__init__(application_id="dev.lumen.cam")
        self.args = args
        st = load_state()
        self.size_i = st.get("size", 1)
        self.last_big = self.size_i if self.size_i else 1
        self.shape_i = st.get("shape", 0) % len(SHAPES)
        self.effect_i = st.get("effect", 0)
        self.room_i = st.get("room", 1)
        self.toast, self.toast_until = "", 0.0
        self.src = Source(args)
        self.src.room = ROOMS[self.room_i]
        self.mic = MicLevel()
        # Physics state, in the monitor's logical px (y down).
        self.pos = None                               # set once the monitor size is known
        self.saved_pos = st.get("center")
        self.size = Spring([SIZES[self.size_i]], 210, 1.0)     # smooth, no overshoot
        self.pop = Spring([0.0], 260, 1.0)
        self.pop.target[:] = 1.0
        self.morph = Spring([float(self.shape_i > 0)], 120, 0.7)     # 0 = blob, 1 = a fixed shape…
        self.alt = Spring([float(self.shape_i == 2)], 120, 0.7)      # …which is 0 = circle, 1 = card
        self.pointer = None
        self.recording, self.rec_checked = False, 0.0
        self.hover = Spring([0.0], 300, 1.0)
        self.mode = "rest"                             # rest | drag | snap (the recorder placing it)
        self.vel = np.zeros(2)
        self.snap_to = None
        self.drag_off = np.zeros(2)
        self.surface = None
        self.surface_seq = -1
        self.buttons = []
        self.drops = []
        self.closing = False
        self.t0 = time.perf_counter()
        self.last_tick = None
        self.margins = (None, None)

    # ── setup
    def do_activate(self):
        win = Gtk.Window(application=self, title="Lumen camera")
        Layer.init_for_window(win)
        Layer.set_layer(win, Layer.Layer.OVERLAY)
        Layer.set_namespace(win, "lumen-cam")
        Layer.set_keyboard_mode(win, Layer.KeyboardMode.NONE)
        Layer.set_anchor(win, Layer.Edge.LEFT, True)
        Layer.set_anchor(win, Layer.Edge.TOP, True)
        Layer.set_exclusive_zone(win, -1)
        css = Gtk.CssProvider()
        css.load_from_string("window, window.background { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(WIN, WIN)
        self.area.set_draw_func(self.draw)
        win.set_child(self.area)
        win.set_default_size(WIN, WIN)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.area.add_controller(drag)
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.area.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *a: self.hover.target.__setitem__(0, 1.0))
        motion.connect("leave", lambda *a: (self.hover.target.__setitem__(0, 0.0), setattr(self, "pointer", None)))
        motion.connect("motion", lambda c, x, y: setattr(self, "pointer", (x, y)))
        self.area.add_controller(motion)
        right = Gtk.GestureClick(button=3)
        right.connect("released", lambda *a: self.close())
        self.area.add_controller(right)

        self.win = win
        win.present()
        self.src.start()
        threading.Thread(target=self.serve, daemon=True).start()
        self.mic.start()
        self.area.add_tick_callback(self.tick)

    def monitor_size(self):
        surf = self.win.get_surface()
        mon = self.win.get_display().get_monitor_at_surface(surf) if surf else None
        if not mon:
            return 1600, 1000
        g = mon.get_geometry()
        return g.width, g.height

    def bounds(self, d):
        w, h = self.monitor_size()
        r = d / 2 + INSET
        return np.array([r, r]), np.array([w - r, h - r])

    # ── input
    def cursor(self):
        """Where the mouse really is, in this monitor's coordinates (asked of Hyprland directly).

        Working it out from mouse events doesn't hold up while dragging: the window
        moves under the pointer, and events measured against where it was a moment
        ago make the bubble overshoot and shake.
        """
        try:
            s = socket.socket(socket.AF_UNIX)
            s.connect(HYPR_SOCKET)
            s.sendall(b"j/cursorpos")
            data = b""
            while chunk := s.recv(4096):
                data += chunk
            s.close()
            c = json.loads(data)
        except (OSError, ValueError):
            return None
        return np.array([c["x"], c["y"]], float) - self.monitor_origin()

    def monitor_origin(self):
        surf = self.win.get_surface()
        mon = self.win.get_display().get_monitor_at_surface(surf) if surf else None
        if not mon:
            return np.zeros(2)
        g = mon.get_geometry()
        return np.array([g.x, g.y], float)

    def on_drag_begin(self, g, x, y):
        self.press = (x, y)
        self.dragged = False
        c = self.cursor()
        self.grab_off = self.pos - c if c is not None else np.zeros(2)

    def on_drag_update(self, g, dx, dy):
        # Only marks the drag as started; the bubble follows the real cursor in tick().
        if not self.dragged and math.hypot(dx, dy) >= 4:
            self.dragged = True
            self.mode = "drag"

    def on_drag_end(self, g, dx, dy):
        if not self.dragged:
            self.click(*self.press)
            return
        # It stays exactly where you drop it.
        self.vel = np.zeros(2)
        self.mode = "rest"
        save_state(center=self.pos.tolist())

    def click(self, x, y):
        for (bx, by, r, action) in self.buttons:
            if math.hypot(x - bx, y - by) <= r + 3:
                action()
                return

    def on_scroll(self, c, dx, dy):
        self.set_size(min(len(SIZES) - 1, max(0, self.size_i + (1 if dy < 0 else -1))))
        return True

    # ── toolbar actions
    def set_size(self, i):
        if i == self.size_i:
            return
        self.size_i = i
        if i:
            self.last_big = i
        self.size.target[:] = SIZES[i]
        save_state(size=i)

    def next_room(self):
        self.room_i = (self.room_i + 1) % len(ROOMS)
        self.src.room = ROOMS[self.room_i]
        save_state(room=self.room_i)
        self.say(f"Room: {ROOMS[self.room_i][0]}")

    def say(self, text, seconds=1.4):
        self.toast, self.toast_until = text, time.perf_counter() + seconds

    def next_size(self):
        self.set_size((self.size_i + 1) % len(SIZES))

    def next_shape(self):
        self.shape_i = (self.shape_i + 1) % len(SHAPES)
        self.morph.target[:] = float(self.shape_i > 0)
        if self.shape_i:
            self.alt.target[:] = float(self.shape_i == 2)
        save_state(shape=self.shape_i)

    def next_effect(self):
        self.effect_i = (self.effect_i + 1) % len(EFFECTS)
        save_state(effect=self.effect_i)

    def center_me(self):
        self.src.center_request = True

    def close(self):
        self.closing = True
        self.pop.target[:] = 0.0
        self.pop.k, self.pop.ratio = 400, 1.0

    # ── commands from the recorder (lumen-cam hide|show|place X Y W H|quit)
    def serve(self):
        try:
            os.unlink(CONTROL)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(CONTROL)
        srv.listen(4)
        while True:
            conn, _ = srv.accept()
            with conn:
                line = conn.recv(256).decode(errors="replace").strip()
                GLib.idle_add(self.command, line)
                conn.sendall(b"ok\n")

    def command(self, line):
        cmd, *args = line.split()
        if cmd == "hide":
            # Out of the way (e.g. while you pick what to record): fade out, let clicks through.
            self.hidden = True
            self.pop.target[:] = 0.0
        elif cmd == "show":
            self.hidden = False
            self.pop.target[:] = 1.0
        elif cmd == "place" and len(args) == 4:
            self.place(*map(float, args))
        elif cmd == "center":
            self.src.center_request = True
        elif cmd == "quit":
            self.close()
        return False

    def place(self, x, y, w, h):
        """Make sure the bubble is inside this area (global coords): its monitor, then its bottom-left corner."""
        display = self.win.get_display()
        mons = display.get_monitors()
        cx, cy = x + w / 2, y + h / 2
        for i in range(mons.get_n_items()):
            m = mons.get_item(i)
            g = m.get_geometry()
            if g.x <= cx < g.x + g.width and g.y <= cy < g.y + g.height:
                if m != display.get_monitor_at_surface(self.win.get_surface()):
                    Layer.set_monitor(self.win, m)
                    self.margins = (None, None)
                    self.pos = None                 # re-placed below, once on the new monitor
                    self.saved_pos = None
                break
        GLib.timeout_add(80, self._place_inside, x, y, w, h)

    def _place_inside(self, x, y, w, h):
        o = self.monitor_origin()
        d = float(self.size.target[0])
        r = d / 2 + 24
        lo, hi = np.array([x, y]) - o + r, np.array([x + w, y + h]) - o - r
        if self.pos is None:
            self.pos = np.array([lo[0], hi[1]])
        if np.any(hi < lo):
            return False                            # an area smaller than the bubble: leave it be
        if np.any(self.pos < lo) or np.any(self.pos > hi):
            self.snap_to = np.array([lo[0], hi[1]])  # glide to the area's bottom-left corner
            self.vel = np.zeros(2)
            self.mode = "snap"
            save_state(center=self.snap_to.tolist())
        return False

    # ── physics, once per screen frame
    def tick(self, widget, clock):
        lap = PROF.start()
        r = self._tick(widget, clock)
        lap("window: physics (drag, springs, placement)")
        return r

    def _tick(self, widget, clock):
        now = time.perf_counter()
        dt = min(1 / 30, now - self.last_tick) if self.last_tick else 1 / 60
        self.last_tick = now
        d = self.size.step(dt)[0]
        lo, hi = self.bounds(d)
        if self.pos is None:
            self.pos = np.array(self.saved_pos, float) if self.saved_pos else np.array([lo[0], hi[1]])
            self.pos = np.clip(self.pos, lo, hi)

        prev = self.pos.copy()
        if self.mode == "drag":
            # Follows the real cursor, straight.
            c = self.cursor()
            if c is not None:
                self.pos = c + self.grab_off
        elif self.mode == "snap" and self.snap_to is not None:
            # Only the recorder moves it (into the area you picked): a smooth glide, no bounce.
            k, ratio = 140, 1.0
            self.vel += (k * (self.snap_to - self.pos) - 2 * ratio * math.sqrt(k) * self.vel) * dt
            self.pos += self.vel * dt
            if np.hypot(*(self.snap_to - self.pos)) < 0.5 and np.hypot(*self.vel) < 5:
                self.mode = "rest"
                save_state(center=self.snap_to.tolist())
        # Screen edges: it simply stops there.
        self.pos = np.clip(self.pos, lo, hi)
        self.pop.step(dt)
        self.morph.step(dt)
        self.alt.step(dt)
        # While a recording runs, the toolbar and labels stay hidden so they're never in the video.
        if now - self.rec_checked > 0.5:
            self.rec_checked = now
            try:
                with open(REC_STATE) as f:
                    self.recording = json.load(f).get("status") == "recording"
            except (OSError, ValueError):
                self.recording = False
        self.hover.step(dt)
        if self.closing and self.pop.x[0] < 0.02:
            self.quit()
            return GLib.SOURCE_REMOVE

        # Place the window so the bubble sits at pos; near the screen edge the
        # window stops and the bubble moves inside it instead.
        w, h = self.monitor_size()
        wx = int(min(max(self.pos[0] - WIN / 2, 0), max(0, w - WIN)))
        wy = int(min(max(self.pos[1] - WIN / 2, 0), max(0, h - WIN)))
        if (wx, wy) != self.margins:
            self.margins = (wx, wy)
            Layer.set_margin(self.win, Layer.Edge.LEFT, wx)
            Layer.set_margin(self.win, Layer.Edge.TOP, wy)

        surf = self.win.get_surface()
        scale = surf.get_scale() if surf else 1.6
        self.src.px = int(SIZES[self.size_i] * scale)
        self.area.queue_draw()
        return GLib.SOURCE_CONTINUE

    # ── drawing
    def to_window(self, pts, cx, cy, d):
        """Picture coords → window px: centred on the bubble, scaled by size × pop."""
        p = (np.asarray(pts, float) - 0.5) * d * float(self.pop.x[0])
        return p + np.array([cx, cy])

    def outlines_px(self, polys, radial, centre, cx, cy, d, t):
        """The blob outlines in window px, with card morph and voice pulse applied."""
        m = float(self.morph.x[0])
        Blob_N = 96

        def fixed():
            # The circle and the card, blended by how far the shape has morphed between them.
            a = np.linspace(0, 2 * math.pi, Blob_N, endpoint=False)
            ca, sa = np.cos(a), np.sin(a)
            card = (np.abs(ca / 0.36) ** 7 + np.abs(sa / 0.46) ** 7) ** (-1 / 7)     # a portrait superellipse
            k = float(self.alt.x[0])
            r = 0.44 * (1 - k) + card * k
            return np.stack([0.5 + ca * r, 0.5 + sa * r], 1)

        if radial and m > 0.001:
            polys = [polys[0] * (1 - m) + fixed() * m]
        elif not radial and m > 0.5:
            polys = [fixed()]       # several people fit in one circle or card
        out = []
        lvl = self.mic.level
        for pts in polys:
            c = (centre if radial else pts.mean(0)) * (1 - m) + np.array([0.5, 0.5]) * m
            if lvl > 0.01:
                a = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
                wave = 1 + 0.05 * lvl * (1 + 0.6 * np.sin(4 * a + 7 * t) + 0.3 * np.sin(7 * a - 5 * t))
                pts = c + (pts - c) * wave[:, None]
            out.append(smooth_polygon(self.to_window(pts, cx, cy, d), sub=3))
        return out

    def splash(self, at, cx, cy, d):
        """A blob just split in two: a pop of droplets."""
        p = self.to_window([at], cx, cy, d)[0]
        rng = np.random.default_rng()
        for _ in range(9):
            ang = rng.uniform(0, 2 * math.pi)
            sp = rng.uniform(140, 320)
            self.drops.append([p[0], p[1], math.cos(ang) * sp, math.sin(ang) * sp - 120, rng.uniform(2.5, 6.5), 0.0])

    def draw_drops(self, cr, dt):
        alive = []
        for dr in self.drops:
            dr[5] += dt
            if dr[5] > 0.75:
                continue
            dr[2] *= math.exp(-dt * 1.5)
            dr[3] += 700 * dt
            dr[0] += dr[2] * dt
            dr[1] += dr[3] * dt
            life = 1 - dr[5] / 0.75
            cr.arc(dr[0], dr[1], dr[4] * (0.4 + 0.6 * life), 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 0.85 * life)
            cr.fill()
            alive.append(dr)
        self.drops = alive

    def draw(self, area, cr, w, h):
        with self.src.lock:
            fr, seq = self.src.frame, self.src.seq
            splits, self.src.splits = self.src.splits, []
        if fr is None or self.pos is None:
            return
        data, px, polys, radial, centre = fr
        if seq != self.surface_seq:
            self.surface = cairo.ImageSurface.create_for_data(bytearray(data), cairo.FORMAT_ARGB32, px, px, px * 4)
            self.surface_seq = seq
        now = time.perf_counter()
        t = now - self.t0
        fdt = min(0.05, now - getattr(self, "last_draw", now))
        self.last_draw = now
        d = float(self.size.x[0])
        cx, cy = self.pos[0] - self.margins[0], self.pos[1] - self.margins[1]
        pop = float(self.pop.x[0])
        if pop < 0.01:
            if getattr(self, "hidden", False) and getattr(self, "input_box", None) != "none":
                self.input_box = "none"
                self.win.get_surface().set_input_region(cairo.Region())    # hidden: clicks go through
            return
        for at in splits:
            if now - getattr(self, "last_splash", 0) > 1.0:
                self.last_splash = now
                self.splash(at, cx, cy, d)
        lap = PROF.start()
        wpolys = self.outlines_px(polys, radial, centre, cx, cy, d, t)
        lap("window: outline → screen shape")
        allpts = np.vstack(wpolys)

        def path(off=(0, 0)):
            for poly in wpolys:
                q = poly + np.array(off)
                cr.move_to(*q[0])
                for x, y in q[1:]:
                    cr.line_to(x, y)
                cr.close_path()

        # Soft shadow: a few widening, fading strokes under the bubble.
        for width, alpha in ((22, 0.05), (14, 0.07), (7, 0.09)):
            path(off=(0, 6))
            cr.set_source_rgba(0, 0, 0, alpha * pop)
            cr.set_line_width(width)
            cr.fill_preserve()
            cr.stroke()
            cr.new_path()

        # The picture, cut to the blobs.
        cr.save()
        path()
        cr.clip()
        cr.translate(cx, cy)
        k = d * pop / px
        cr.scale(k, k)
        cr.translate(-px / 2, -px / 2)
        cr.set_source_surface(self.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        cr.restore()

        lap("window: shadow + picture cut to the blob")
        for poly in wpolys:
            self.draw_effect(cr, poly, t, pop)
        lap(f"window: edge effect ({EFFECTS[self.effect_i]})")
        if self.src.notice:
            self.say(self.src.notice)
            self.src.notice = None
        label = self.toast if now < self.toast_until else self.src.caption
        if label and not self.recording:
            cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(13)
            ext = cr.text_extents(label)
            tx, ty = cx - ext.width / 2, allpts[:, 1].min() - 14
            cr.set_source_rgba(0.06, 0.06, 0.09, 0.7 * pop)
            cr.rectangle(tx - 8, ty - ext.height - 6, ext.width + 16, ext.height + 12)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, pop)
            cr.move_to(tx, ty)
            cr.show_text(label)
        self.draw_drops(cr, fdt)
        self.draw_toolbar(cr, allpts, cx, t)
        lap("window: caption, droplets, toolbar")
        self.update_input_region(allpts)

    def draw_effect(self, cr, poly, t, pop):
        fx = EFFECTS[self.effect_i]
        if fx == "plain":
            cr.move_to(*poly[0])
            for x, y in poly[1:]:
                cr.line_to(x, y)
            cr.close_path()
            cr.set_source_rgba(1, 1, 1, 0.14 * pop)
            cr.set_line_width(1.2)
            cr.stroke()
            return
        c = poly.mean(0)
        n = len(poly)
        a = np.arctan2(poly[:, 1] - c[1], poly[:, 0] - c[0])
        if fx == "glow":
            # A bright rim with a slow ripple running around it.
            wave = 1 + 0.018 * np.sin(9 * a + 4 * t)
            q = c + (poly - c) * wave[:, None]
            for width, alpha in ((12, 0.06), (7, 0.12), (3.5, 0.35), (1.6, 0.95)):
                cr.move_to(*q[0])
                for x, y in q[1:]:
                    cr.line_to(x, y)
                cr.close_path()
                cr.set_source_rgba(0.9, 0.97, 1, alpha * pop)
                cr.set_line_width(width)
                cr.stroke()
        elif fx == "rainbow":
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            for width, alpha in ((9, 0.25), (3.5, 1.0)):
                cr.set_line_width(width)
                for i in range(n):
                    hue = (a[i] / (2 * math.pi) + t * 0.15) % 1
                    r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 1)
                    cr.set_source_rgba(r, g, b, alpha * pop)
                    cr.move_to(*poly[i])
                    cr.line_to(*poly[(i + 1) % n])
                    cr.stroke()
        elif fx == "fire":
            # Flames: an outward flicker that is strongest on top.
            up = np.clip(-(poly[:, 1] - c[1]) / (np.abs(poly - c).max() + 1), 0, 1)
            flick = (0.5 + 0.5 * np.sin(11 * a + 9 * t)) * (0.6 + 0.4 * np.sin(5 * a - 6 * t + 1))
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            for layer, (col, base, amp) in enumerate(((( 0.85, 0.1, 0.05), 10, 16), ((1, 0.45, 0.05), 7, 10), ((1, 0.85, 0.35), 3, 4))):
                for i in range(n):
                    j = (i + 1) % n
                    h = base + amp * flick[i] * (0.4 + 0.6 * up[i])
                    cr.set_line_width(h)
                    cr.set_source_rgba(*col, (0.55 if layer == 0 else 0.8) * pop)
                    cr.move_to(*poly[i])
                    cr.line_to(*poly[j])
                    cr.stroke()

    def tools(self):
        """The toolbar, left to right: (icon, action, label with its current value)."""
        return [
            (self.icon_size, self.next_size, f"Size: {SIZE_NAMES[self.size_i]}"),
            (self.icon_shape, self.next_shape, f"Shape: {SHAPES[self.shape_i]}"),
            (self.icon_sparkle, self.next_effect, f"Outline: {EFFECTS[self.effect_i].title()}"),
            (self.icon_room, self.next_room, f"Room: {ROOMS[self.room_i][0]}"),
            (self.icon_center, self.center_me, "Center me"),
            (self.icon_close, self.close, "Close"),
        ]

    def draw_toolbar(self, cr, poly, cx, t):
        a = float(self.hover.x[0])
        self.buttons, self.toolbar_box = [], None
        # Shown during recordings too: it only appears while the pointer is on the bubble.
        if a < 0.02 or self.mode == "drag":
            return
        tools = self.tools()
        n, r = len(tools), 12
        gap = 32 if self.size_i else 27          # a touch more compact on the small bubble
        w, h = gap * (n - 1) + 2 * r + 20, 2 * r + 12
        # A pill sitting on the bubble's bottom edge, half in and half out.
        x0, y = cx - w / 2, poly[:, 1].max() - 4
        self.toolbar_box = (x0, y - h / 2, w, h)
        cr.set_source_rgba(0, 0, 0, 0.25 * a)                 # a soft lift under the pill
        self.pill(cr, x0, y + 2, w, h)
        cr.fill()
        cr.set_source_rgba(0.09, 0.09, 0.12, 0.88 * a)
        self.pill(cr, x0, y, w, h)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.1 * a)
        cr.set_line_width(1)
        cr.stroke()
        hovered = None
        for i, (icon, act, label) in enumerate(tools):
            bx = x0 + 10 + r + i * gap
            over = self.pointer is not None and math.hypot(self.pointer[0] - bx, self.pointer[1] - y) <= r + 3
            if over:
                hovered = (bx, label)
                cr.set_source_rgba(1, 1, 1, 0.14 * a)
                cr.arc(bx, y, r + 1, 0, 2 * math.pi)
                cr.fill()
            cr.save()
            cr.translate(bx, y)
            cr.set_source_rgba(1, 1, 1, (1.0 if over else 0.82) * a)
            cr.set_line_width(1.6)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            icon(cr)
            cr.restore()
            self.buttons.append((bx, y, r, act))
        if hovered:
            # What the button does and what it's set to now, just below the pill.
            bx, label = hovered
            cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(12)
            ext = cr.text_extents(label)
            lx, ly = bx - ext.width / 2 - 8, y + h / 2 + 6
            cr.set_source_rgba(0.09, 0.09, 0.12, 0.88 * a)
            self.pill(cr, lx, ly + 10, ext.width + 16, 20)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, a)
            cr.move_to(lx + 8, ly + 14)
            cr.show_text(label)

    @staticmethod
    def pill(cr, x, cy, w, h):
        r = h / 2
        cr.new_sub_path()
        cr.arc(x + r, cy, r, math.pi / 2, 3 * math.pi / 2)
        cr.arc(x + w - r, cy, r, -math.pi / 2, math.pi / 2)
        cr.close_path()

    def icon_size(self, cr):
        # A circle that is as big as the current size, with a faint ring for the largest.
        cr.set_line_width(1.2)
        cr.arc(0, 0, 7.5, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.arc(0, 0, (3.2, 5.0, 7.5)[self.size_i], 0, 2 * math.pi)
        cr.fill()

    def icon_center(self, cr):
        # A crosshair: a ring with four ticks pointing in at the middle.
        cr.arc(0, 0, 5, 0, 2 * math.pi)
        cr.stroke()
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            cr.move_to(dx * 9, dy * 9)
            cr.line_to(dx * 6.5, dy * 6.5)
        cr.stroke()
        cr.arc(0, 0, 1.2, 0, 2 * math.pi)
        cr.fill()

    def icon_room(self, cr):
        # A person's head and shoulders inside corner brackets that sit further out the roomier it is.
        cr.arc(0, -1.5, 2.2, 0, 2 * math.pi)
        cr.fill()
        cr.arc(0, 5.5, 4, math.pi, 2 * math.pi)
        cr.fill()
        e = (4.5, 6.5, 8.5)[self.room_i]
        k = 2.6
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            cr.move_to(sx * e, sy * e + -sy * k)
            cr.line_to(sx * e, sy * e)
            cr.line_to(sx * e + -sx * k, sy * e)
        cr.stroke()

    def icon_shape(self, cr):
        # The current shape: a lumpy blob, a circle, or a portrait card.
        if self.shape_i == 0:
            a = np.linspace(0, 2 * math.pi, 24, endpoint=False)
            r = 6.5 + 1.3 * np.sin(3 * a + 0.6) + 0.7 * np.sin(5 * a)
            cr.move_to(r[0], 0)
            for ang, rad in zip(a[1:], r[1:]):
                cr.line_to(rad * math.cos(ang), rad * math.sin(ang))
            cr.close_path()
        elif self.shape_i == 1:
            cr.arc(0, 0, 7, 0, 2 * math.pi)
        else:
            cr.new_sub_path()
            rr, w, h = 2.5, 10, 14
            cr.arc(-w / 2 + rr, -h / 2 + rr, rr, math.pi, 1.5 * math.pi)
            cr.arc(w / 2 - rr, -h / 2 + rr, rr, 1.5 * math.pi, 0)
            cr.arc(w / 2 - rr, h / 2 - rr, rr, 0, 0.5 * math.pi)
            cr.arc(-w / 2 + rr, h / 2 - rr, rr, 0.5 * math.pi, math.pi)
            cr.close_path()
        cr.stroke()

    def icon_sparkle(self, cr):
        for s, (ox, oy) in ((6.5, (-1, 1)), (3, (5, -5))):
            cr.move_to(ox, oy - s)
            for dx, dy in ((0.28, -0.28), (1, 0), (0.28, 0.28), (0, 1), (-0.28, 0.28), (-1, 0), (-0.28, -0.28), (0, -1)):
                cr.line_to(ox + dx * s, oy + dy * s)
            cr.close_path()
            cr.fill()

    def icon_close(self, cr):
        cr.move_to(-4.5, -4.5)
        cr.line_to(4.5, 4.5)
        cr.move_to(4.5, -4.5)
        cr.line_to(-4.5, 4.5)
        cr.stroke()

    def update_input_region(self, poly):
        """Only the bubble takes clicks; the transparent rest of the window lets them through."""
        surf = self.win.get_surface()
        if not surf:
            return
        x0, y0 = np.floor(poly.min(0)).astype(int) - 4
        x1, y1 = np.ceil(poly.max(0)).astype(int) + 4
        # Room below for the toolbar and its label, which hang under the bubble's edge,
        # and to the sides when the toolbar is wider than a small bubble.
        y1 += 52
        tb = getattr(self, "toolbar_box", None)
        if tb:
            x0, x1 = min(x0, int(tb[0]) - 4), max(x1, int(tb[0] + tb[2]) + 4)
        box = (int(x0), int(y0), int(x1 - x0), int(y1 - y0))
        if box != getattr(self, "input_box", None):
            self.input_box = box
            surf.set_input_region(cairo.Region(cairo.RectangleInt(*box)))

    def do_shutdown(self):
        # Stop the camera thread and let go of the webcam *before* Python shuts down.
        # Left running, it gets killed mid-read inside OpenCV, which aborts the whole
        # process ("FATAL: exception not rethrown" → a "Process crashed" notification).
        self.src.running = False
        self.mic.running = False
        self.src.done.wait(3)
        Gtk.Application.do_shutdown(self)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="/dev/video0")
    ap.add_argument("--src", help="a video file to loop instead of the camera")
    ap.add_argument("--crop", help="X,Y,S square crop of the source")
    ap.add_argument("--demo", action="store_true", help="two drawn people act out splits, pops and merges")
    app = Bubble(ap.parse_args())
    # `lumen-cam stop` (SIGTERM) and Ctrl+C close it the same clean way as the × button.
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (app.quit(), GLib.SOURCE_REMOVE)[1])
    app.run([])
    # Everything that matters is stopped and released; skip the interpreter's own
    # teardown, which would otherwise race the camera AI's worker threads.
    sys.stderr.flush()
    os._exit(0)
