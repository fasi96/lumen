#!/usr/bin/env python3
"""
Voice Tuner: make the recorder's mic sound nice, by ear.

Record a short sample of your voice, then drag sliders (or pick a preset) and hear
the result straight away. Every change is rendered by ffmpeg with exactly the filter
chain the screen recorder will use, so what you hear here is what your MP4s get.
Every mic has its own profile and its own sample: Save writes that mic's entry in
~/.config/gif-record/voice.json ({profiles: {source: {preset, params, filter}},
fallback}); the recorder applies whichever profile belongs to the mic it records
with. Which mic that is gets chosen in the recorder panel, not here. The server
quits when the window closes.
"""

import json
import os
import re
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
CONF_DIR = os.path.join(HOME, ".config/gif-record")
VOICE_FILE = os.path.join(CONF_DIR, "voice.json")
REC_SETTINGS = os.path.join(CONF_DIR, "settings.json")
CACHE = os.path.join(HOME, ".cache/voice-tuner")
SAMPLES = os.path.join(CACHE, "samples")
MODEL = os.path.join(HOME, ".local/share/voice-models/std.rnnn")
THEME_COLORS = os.path.join(HOME, ".local/state/omarchy/current/theme/colors.toml")
PORT = 47614
TITLE = "Lumen · Voice"
MAX_SECONDS = 30
WARMUP = 1.0

# key: (min, max, step, default, label, unit, hint)
PARAMS = {
    "gain":      (-12, 12, 0.5, 0, "Input trim", "dB", "Turn down if the sample clips"),
    "rumble":    (20, 250, 5, 80, "Rumble cut", "Hz", "Removes desk thumps, fans, AC hum"),
    "denoise":   (0, 100, 5, 60, "Noise removal", "%", "AI noise removal (RNNoise); too much sounds robotic"),
    "warmth":    (-6, 6, 0.5, 0, "Warmth", "dB", "Body of the voice, around 150 Hz"),
    "mud":       (-8, 0, 0.5, -2, "Mud cut", "dB", "Boxy, muffled room sound, around 350 Hz"),
    "presence":  (-6, 8, 0.5, 2, "Clarity", "dB", "Words cut through, around 3.5 kHz"),
    "air":       (0, 8, 0.5, 1, "Air", "dB", "Sparkle on top, above 10 kHz"),
    "deess":     (0, 100, 5, 20, "De-ess", "%", "Tames sharp S and T sounds"),
    "evenness":  (0, 100, 5, 35, "Evenness", "%", "Compression: quiet and loud parts closer together"),
    "loudness":  (-24, -12, 1, -16, "Loudness", "LUFS", "-16 is the usual target for online video"),
}

PRESETS = {
    "Raw":        {"gain": 0, "rumble": 20, "denoise": 0, "warmth": 0, "mud": 0, "presence": 0, "air": 0, "deess": 0, "evenness": 0, "loudness": -16},
    "Natural":    {"gain": 0, "rumble": 80, "denoise": 50, "warmth": 0, "mud": -1, "presence": 1, "air": 1, "deess": 10, "evenness": 25, "loudness": -16},
    "Podcast":    {"gain": 0, "rumble": 90, "denoise": 75, "warmth": 3, "mud": -3, "presence": 3, "air": 2, "deess": 35, "evenness": 60, "loudness": -16},
    "Crisp":      {"gain": 0, "rumble": 110, "denoise": 70, "warmth": -1, "mud": -4, "presence": 5, "air": 4, "deess": 45, "evenness": 50, "loudness": -16},
    "Noisy room": {"gain": 0, "rumble": 130, "denoise": 100, "warmth": 0, "mud": -2, "presence": 2, "air": 0, "deess": 20, "evenness": 40, "loudness": -16},
}


def clean(raw):
    out = {}
    for k, (lo, hi, _step, default, *_rest) in PARAMS.items():
        try:
            out[k] = min(max(float(raw.get(k, default)), lo), hi)
        except (TypeError, ValueError):
            out[k] = default
    return out


def voice_filter(p):
    """The ffmpeg chain for the mic track. The recorder reads this string verbatim."""
    f = ["aresample=48000", "aformat=channel_layouts=mono"]
    if p["gain"]:
        f.append(f"volume={p['gain']}dB")
    # Always at least 20 Hz: the mic waking from standby leaves a DC offset.
    f.append(f"highpass=f={max(p['rumble'], 20):g}:poles=2")
    if p["denoise"] > 0:
        f.append(f"arnndn=m={MODEL}:mix={p['denoise'] / 100:.2f}")
    if p["warmth"]:
        f.append(f"equalizer=f=150:t=q:w=0.8:g={p['warmth']:g}")
    if p["mud"]:
        f.append(f"equalizer=f=350:t=q:w=1.2:g={p['mud']:g}")
    if p["presence"]:
        f.append(f"equalizer=f=3500:t=q:w=0.9:g={p['presence']:g}")
    if p["air"]:
        f.append(f"treble=g={p['air']:g}:f=10000:t=s")
    if p["deess"] > 0:
        f.append(f"deesser=i={p['deess'] / 100:.2f}:m=0.5:f=0.5")
    if p["evenness"] > 0:
        e = p["evenness"] / 100
        f.append(f"acompressor=threshold={-12 - 20 * e:.1f}dB:ratio={1.5 + 4.5 * e:.2f}:attack=5:release=80:knee=4")
    f.append(f"loudnorm=I={p['loudness']:g}:TP=-1.5:LRA=11")
    f.append("aresample=48000")
    return ",".join(f)


def reference_filter(p):
    """The untouched sample at the same loudness, for a fair A/B."""
    return f"aresample=48000,aformat=channel_layouts=mono,highpass=f=20,loudnorm=I={p['loudness']:g}:TP=-1.5:LRA=11,aresample=48000"


def render(mic, chain):
    # Through a temp file: a WAV piped to stdout has no length in its header.
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f"render-{threading.get_ident()}.wav")
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", sample_path(mic), "-af", chain, out],
                           capture_output=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="replace")[-400:])
        with open(out, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(out):
            os.remove(out)


# ---- Mics

def mics():
    try:
        data = json.loads(subprocess.run(["pactl", "-f", "json", "list", "sources"],
                                         capture_output=True, text=True).stdout)
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for s in data:
        if s.get("monitor_source") or s["name"].endswith(".monitor"):
            continue
        out.append({"name": s["name"], "label": mic_label(s["name"], s.get("description")),
                    "muted": bool(s.get("mute"))})
    return out


def mic_label(name, description=None):
    return "Laptop mic" if name.startswith("alsa_input.pci-") else (description or name)


def sample_path(mic):
    return os.path.join(SAMPLES, re.sub(r"[^A-Za-z0-9._-]", "_", mic) + ".wav")


def default_mic():
    return subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True).stdout.strip()


def recorder_mic():
    """The mic the next recording would use, so the tuner opens on it."""
    try:
        with open(REC_SETTINGS) as f:
            m = json.load(f).get("mic", "")
    except (OSError, json.JSONDecodeError):
        m = ""
    names = [x["name"] for x in mics()]
    return m if m in names else default_mic()


# ---- Recording the sample, with a live level meter

class Recorder:
    def __init__(self):
        self.proc = None
        self.since = 0
        self.rms = -120.0
        self.peak = -120.0
        self.max_peak = -120.0
        self.error = ""
        self.mic = ""

    @property
    def active(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, mic):
        if self.active:
            return
        os.makedirs(SAMPLES, exist_ok=True)
        self.mic = mic
        tmp = sample_path(mic) + ".part.wav"
        self.rms = self.peak = self.max_peak = -120.0
        self.error = ""
        # astats every 100 ms, printed to stdout; the wav goes to the file untouched.
        meter = ("asetnsamples=n=4800,astats=metadata=1:reset=1,"
                 "ametadata=mode=print:file=/dev/stdout:direct=1")
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "pulse", "-t", str(MAX_SECONDS), "-i", mic or "default",
             # the first second is the mic waking from standby: a loud stuck value
             # that decays as DC offset. The page shows "warming up" meanwhile.
             "-ss", str(WARMUP), "-ac", "1", "-ar", "48000", "-map", "0:a", tmp,
             "-map", "0:a", "-af", meter, "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.since = time.time()
        threading.Thread(target=self._meter, args=(self.proc, tmp), daemon=True).start()

    def _meter(self, proc, tmp):
        for line in proc.stdout:
            m = re.match(r"lavfi\.astats\.Overall\.(RMS|Peak)_level=(-?[\d.]+|-inf)", line.strip())
            if not m:
                continue
            v = -120.0 if m.group(2) == "-inf" else max(float(m.group(2)), -120.0)
            if time.time() - self.since < WARMUP:
                continue
            if m.group(1) == "RMS":
                self.rms = v
            else:
                self.peak = v
                self.max_peak = max(self.max_peak, v)
        proc.wait()
        if os.path.exists(tmp) and os.path.getsize(tmp) > 48000:
            os.replace(tmp, sample_path(self.mic))
        else:
            self.error = (proc.stderr.read() or "Recording failed")[-300:]

    def stop(self):
        if self.active:
            self.proc.send_signal(signal.SIGINT)   # ffmpeg finalises the wav on SIGINT
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            time.sleep(0.2)   # let the meter thread move the file into place


REC = Recorder()


def sample_info(mic):
    path = sample_path(mic)
    if not os.path.exists(path):
        return None
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-af", "highpass=f=20,volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    peak = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr)
    mean = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    return {
        "seconds": int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else 0,
        "peak": float(peak.group(1)) if peak else None,
        "mean": float(mean.group(1)) if mean else None,
        "mtime": os.path.getmtime(path),
    }


# ---- Saved state

def fallback_profile():
    p = clean(PRESETS["Natural"])
    return {"preset": "Natural", "params": p, "filter": voice_filter(p)}


def load_voice():
    try:
        with open(VOICE_FILE) as f:
            v = json.load(f)
    except (OSError, json.JSONDecodeError):
        v = {}
    if "profiles" not in v:   # the old single-profile file
        v = {"profiles": {}}
    v["fallback"] = fallback_profile()
    return v


def profiles_for_page():
    return {name: {"preset": pr.get("preset", "Custom"), "params": clean(pr.get("params", {}))}
            for name, pr in load_voice()["profiles"].items()}


def save(body):
    mic = str(body.get("mic") or "")
    if not mic:
        raise ValueError("no mic")
    p = clean(body.get("params", {}))
    v = load_voice()
    v["profiles"][mic] = {"label": mic_label(mic, body.get("label")),
                          "preset": str(body.get("preset") or "Custom")[:40],
                          "params": p, "filter": voice_filter(p)}
    os.makedirs(CONF_DIR, exist_ok=True)
    with open(VOICE_FILE + ".tmp", "w") as f:
        json.dump(v, f, indent=2)
    os.replace(VOICE_FILE + ".tmp", VOICE_FILE)


def theme():
    out = {}
    try:
        with open(THEME_COLORS) as f:
            text = f.read()
        for k in ("background", "foreground", "accent", "selection", "muted", "bright_red"):
            m = re.search(rf'(?m)^{k}\s*=\s*"(#[0-9a-fA-F]{{6}})"', text)
            if m:
                out[k] = m.group(1)
    except OSError:
        pass
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            with open(os.path.join(HERE, "tuner.html"), "rb") as f:
                self.send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/state":
            meta = {k: {"min": v[0], "max": v[1], "step": v[2], "default": v[3], "label": v[4],
                        "unit": v[5], "hint": v[6]} for k, v in PARAMS.items()}
            ms = mics()
            self.send(200, json.dumps({
                "params": meta, "presets": PRESETS, "theme": theme(),
                "profiles": profiles_for_page(), "fallback": fallback_profile(),
                "mics": ms, "mic": recorder_mic(), "systemDefault": default_mic(),
                "samples": {m["name"]: sample_info(m["name"]) for m in ms},
                "maxSeconds": MAX_SECONDS, "warmup": WARMUP,
            }))
        elif self.path == "/level":
            self.send(200, json.dumps({
                "recording": REC.active, "elapsed": time.time() - REC.since if REC.active else 0,
                "rms": REC.rms, "peak": REC.peak, "maxPeak": REC.max_peak, "error": REC.error,
                "sample": None if REC.active else sample_info(REC.mic),
            }))
        else:
            self.send(404, "{}")

    def do_POST(self):
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{PORT}"):
            return self.send(403, "{}")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self.send(400, "{}")
        try:
            if self.path == "/record":
                REC.start(str(body.get("mic") or ""))
                self.send(200, "{}")
            elif self.path == "/stop":
                REC.stop()
                self.send(200, json.dumps({"sample": sample_info(REC.mic), "error": REC.error}))
            elif self.path in ("/render", "/reference"):
                mic = str(body.get("mic") or "")
                if not os.path.exists(sample_path(mic)):
                    return self.send(409, '{"error": "no sample"}')
                p = clean(body.get("params", {}))
                chain = voice_filter(p) if self.path == "/render" else reference_filter(p)
                self.send(200, render(mic, chain), "audio/wav")
            elif self.path == "/save":
                save(body)
                self.send(200, "{}")
            else:
                self.send(404, "{}")
        except Exception as e:  # noqa: BLE001 — show the reason in the page
            self.send(500, json.dumps({"error": str(e)}))


# ---- Window

def clients():
    return json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True).stdout or "[]")


def dsp(cmd):
    subprocess.run(["hyprctl", "dispatch", cmd], capture_output=True)


def wait_title(title, secs=6):
    for _ in range(int(secs * 10)):
        for c in clients():
            if c.get("title") == title:
                return c
        time.sleep(0.1)
    return None


def float_center(addr, w, h):
    mon = json.loads(subprocess.run(["hyprctl", "activeworkspace", "-j"], capture_output=True, text=True).stdout)
    mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True).stdout)
    m = next((m for m in mons if m["id"] == mon.get("monitorID")), mons[0])
    mw, mh = int(m["width"] / m["scale"]), int(m["height"] / m["scale"])
    h = min(h, mh - 80)
    x, y = m["x"] + (mw - w) // 2, m["y"] + (mh - h) // 2
    sel = f'window = "address:{addr}"'
    dsp(f'hl.dsp.window.float({{ action = "enable", {sel} }})')
    dsp(f"hl.dsp.window.resize({{ x = {w}, y = {h}, {sel} }})")
    dsp(f"hl.dsp.window.move({{ x = {x}, y = {y}, {sel} }})")


def open_window():
    subprocess.Popen(
        ["uwsm-app", "--", "chromium", f"--user-data-dir={HOME}/.cache/voice-tuner/chromium", "--no-first-run",
         "--ozone-platform=wayland", "--autoplay-policy=no-user-gesture-required",
         f"--app=http://127.0.0.1:{PORT}/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    c = wait_title(TITLE, 15)
    if not c:
        return
    addr = c["address"]
    float_center(addr, 560, 980)
    while any(x.get("address") == addr for x in clients()):
        time.sleep(1.5)
    REC.stop()
    os._exit(0)


def main():
    for c in clients():   # already open: just focus it
        if c.get("title") == TITLE:
            dsp(f'hl.dsp.focus({{ window = "address:{c["address"]}" }})')
            return
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=open_window, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
