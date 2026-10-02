"""Transcribe a recording, cut filler words and long pauses, write captions.

  run clean.py INPUT OUTDIR [--fillers] [--silences] [--lang auto|en|ur|hi]
                            [--codec compatible|smallest] [--pause 1.2]
                            [--width N] [--always] [--progress FILE]

--always writes clean.mp4 even when nothing was cut (the recorder hands us an
un-encoded video and lets this be the single encode); --width shrinks it;
--progress is passed to ffmpeg's -progress so the bar can show the encode.

Writes OUTDIR/transcript.json (words with times on the *output* timeline),
OUTDIR/captions.vtt, OUTDIR/report.json, and OUTDIR/clean.mp4 when anything
was cut. The original is never touched. Run it through ./run so the CUDA
libraries from the venv are on the loader path.

How the cutting works (the same idea as Loom's "remove filler words"): Whisper
gives every word a start and end time; words that are fillers become cut ranges,
and so do pauses longer than --pause seconds (found with ffmpeg's silencedetect,
which is more reliable than gaps between Whisper's word times). What's left is
stitched back together with 10 ms audio fades at each join so the cuts don't click.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

# Hesitation sounds only: removing real words like "like" or "so" changes meaning.
# Covers English plus the usual Urdu/Hindi hesitations Whisper writes out.
FILLERS = {
    "um", "umm", "ummm", "uh", "uhh", "uhm", "uhmm", "erm", "er", "err", "ah", "ahh", "ahhh",
    "hmm", "hm", "hmmm", "mm", "mmm", "mhm", "eh", "ehh", "aa", "aaa",
    "अं", "उम", "अम", "हम्म", "आ", "आं", "ام", "اں", "ہمم", "آ",
}
# Nudges Whisper to write hesitations out instead of tidying them away.
VERBATIM = {
    "en": "Umm, so, uh, let me think... Hmm, okay. Uh, here's the thing, um, it's like this.",
    "ur": "اممم، تو، اہ، میں سوچتا ہوں... ہمم، ٹھیک ہے۔ اہ، بات یہ ہے کہ، امم۔",
    "hi": "उम्म, तो, अह, मैं सोचता हूँ... हम्म, ठीक है। अह, बात यह है कि, उम।",
}
MODEL = "large-v3-turbo"
PAD_BEFORE, PAD_AFTER = 0.03, 0.06   # around a filler word
PAUSE_KEEP = 0.35                    # how much of a long pause survives
GAP = 0.2                            # the natural gap left where an "uh" is cut out


def norm(w):
    return re.sub(r"[^\wऀ-ॿ؀-ۿ]", "", w.lower())


def gpu_ok(min_free_mib=1800):
    """A CUDA GPU with room for Whisper next to whatever else is resident."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            return False
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        return int(r.stdout.split()[0]) >= min_free_mib
    except Exception:  # noqa: BLE001 — no GPU tooling means no GPU
        return False


def cloud_conf():
    try:
        c = json.load(open(os.path.expanduser("~/.config/vshare/config.json")))["cloudflare"]
        return c["url"].rstrip("/"), c["token"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None


class Asr:
    """Whisper with word times, on one of three engines:
      gpu   — large-v3-turbo on this laptop's GPU (fast, nothing leaves the machine)
      cloud — the same model on your own Cloudflare account via Workers AI (no GPU
              needed; free daily allowance; the audio is sent for transcription)
      cpu   — a small model on the processor (slower, still fully local)
    "auto" picks gpu if there's a usable GPU, else cloud if Cloudflare is set up, else cpu."""

    def __init__(self, lang, engine="auto"):
        # "local" is the privacy choice: this computer only (GPU if usable, else CPU),
        # never Cloudflare. "auto" may use the cloud when there's no usable GPU.
        if engine == "local":
            engine = "gpu" if gpu_ok() else "cpu"
        elif engine == "auto":
            engine = "gpu" if gpu_ok() else "cloud" if cloud_conf() else "cpu"
        self.engine, self.lang = engine, lang
        self.model = None
        if engine in ("gpu", "cpu"):
            self._load(engine)

    def _load(self, engine):
        from faster_whisper import WhisperModel
        self.model = (WhisperModel(MODEL, device="cuda", compute_type="float16") if engine == "gpu"
                      else WhisperModel(MODEL, device="cpu", compute_type="int8"))
        self.engine = engine

    def words(self, audio):
        # If the cloud (no internet, quota used up) or the GPU (out of memory) fails,
        # finish on the processor rather than giving up and leaving the ums in.
        if self.engine in ("cloud", "gpu"):
            try:
                return self._cloud(audio) if self.engine == "cloud" else self._local(audio)
            except Exception as e:  # noqa: BLE001
                print(f"clean: {self.engine} transcription failed ({e}); retrying on the processor",
                      file=sys.stderr, flush=True)
                self._load("cpu")
        return self._local(audio)

    def _local(self, audio):
        if self.lang == "auto":
            # Detect first, then transcribe with that language's verbatim hint: without
            # a hint Whisper tidies "uh"s away, and an English hint on Urdu speech makes
            # it translate instead of transcribe.
            self.lang, _, _ = self.model.detect_language(audio, vad_filter=True)
        segs, _ = self.model.transcribe(
            audio, word_timestamps=True, vad_filter=True, language=self.lang,
            initial_prompt=VERBATIM.get(self.lang), condition_on_previous_text=False,
        )
        return [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
                for s in segs for w in s.words if w.word.strip()]

    def _cloud(self, audio, chunk_s=300):
        import urllib.parse
        import urllib.request
        base, token = cloud_conf()

        def call(samples, language=None, prompt=None):
            mp3 = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
                                  "-b:a", "48k", "-f", "mp3", "pipe:1"], input=samples.tobytes(), capture_output=True).stdout
            q = urllib.parse.urlencode({k: v for k, v in (("language", language), ("prompt", prompt)) if v})
            req = urllib.request.Request(f"{base}/api/transcribe?{q}", data=mp3, method="POST",
                                         headers={"Authorization": f"Bearer {token}", "User-Agent": "lumen-clean/1.0",
                                                  "Content-Type": "audio/mpeg"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())

        if self.lang == "auto":            # a short first pass just to learn the language
            self.lang = call(audio[: SR * 30]).get("language") or "en"
        out = []
        for start in range(0, len(audio), SR * chunk_s):
            r = call(audio[start: start + SR * chunk_s], self.lang, VERBATIM.get(self.lang))
            off = start / SR
            out += [{"w": w["w"], "s": round(w["s"] + off, 3), "e": round(w["e"] + off, 3)} for w in r.get("words", [])]
        return out


def silences(path, pause):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-vn", "-af",
                        f"silencedetect=noise=-38dB:d={pause}", "-f", "null", "-"],
                       capture_output=True, text=True)
    out, start = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    return out


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def merge(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 0.02:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


SR = 16000


def decode_audio(path):
    """The recording's audio as 16 kHz mono floats, via ffmpeg (so the cloud-only
    install doesn't need the speech-AI packages just to read a file)."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()
FRAME = 160          # 10 ms loudness slices


def loudness(audio):
    import numpy as np
    n = len(audio) // FRAME
    frames = audio[: n * FRAME].reshape(n, FRAME)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    return db, float(np.percentile(db, 10))


def is_filler(w):
    return norm(w["w"]) in FILLERS


def filler_cuts(words, audio, total):
    """Whisper reliably notices a filler but often misplaces it (an "uh" 20 ms long).
    Real words are placed well, so: the filler lives in the gap between the real
    words either side of it, and it's the stretch of voice in that gap."""
    db, floor = loudness(audio)
    voiced = db > floor + 12
    cuts, count = [], 0          # cuts are (start, end, what) — "what" feeds the page's markers
    real = [i for i, w in enumerate(words) if not is_filler(w)]
    done = set()
    for i, w in enumerate(words):
        if not is_filler(w):
            continue
        count += 1
        prev = max((j for j in real if j < i), default=None)
        nxt = min((j for j in real if j > i), default=None)
        g0 = words[prev]["e"] if prev is not None else 0.0
        g1 = words[nxt]["s"] if nxt is not None else total
        if (g0, g1) in done:          # "uh, um" in one gap: one cut covers both
            continue
        done.add((g0, g1))
        f0, f1 = int((g0 + 0.03) * 100), int((g1 - 0.03) * 100)
        idx = [k for k in range(max(f0, 0), min(f1, len(voiced))) if voiced[k]]
        if idx:
            # Take the quiet either side of the "uh" too, back to where the real words'
            # voices end and start, and leave a natural GAP between them. Cutting only the
            # "uh" itself left up to a second of dead air where it had been.
            k0 = idx[0]
            while k0 > 0 and not voiced[k0 - 1] and idx[0] - k0 < 150:
                k0 -= 1
            k1 = idx[-1] + 1
            while k1 < len(voiced) and not voiced[k1] and k1 - idx[-1] < 150:
                k1 += 1
            a = max(k0 / 100 + GAP / 2, idx[0] / 100 - 0.04 - 1.5)
            b = min(k1 / 100 - GAP / 2, (idx[-1] + 1) / 100 + 0.05 + 1.5)
            a, b = min(a, idx[0] / 100 - 0.02), max(b, (idx[-1] + 1) / 100 + 0.02)
            cuts.append((a, b, norm(w["w"])))
        elif w["e"] - w["s"] >= 0.12:  # glued to a word: trust Whisper only if plausible
            cuts.append((max(g0, w["s"] - PAD_BEFORE), min(g1, w["e"] + PAD_AFTER), norm(w["w"])))
    return [c for c in cuts if c[1] - c[0] > 0.06], count


def to_source(t, cuts):
    """Inverse of remap: a time in the cut output back to the original."""
    for a, b in cuts:
        if t >= a:
            t += b - a
        else:
            break
    return t


def cut_audio(audio, cuts):
    import numpy as np
    keeps, pos = [], 0
    fade = np.linspace(0, 1, 160, dtype=audio.dtype)   # 10 ms, like the video joins
    for a, b in cuts + [(len(audio) / SR, None)]:
        seg = audio[int(pos * SR): int(a * SR)].copy()
        if len(seg) > 320:
            seg[:160] *= fade
            seg[-160:] *= fade[::-1]
        keeps.append(seg)
        if b is None:
            break
        pos = b
    return np.concatenate(keeps) if keeps else audio


def cues(words, max_words=8, max_len=3.5):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["e"] - cur[0]["s"] > max_len
                    or re.search(r"[.?!۔।]$", cur[-1]["w"])):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def vtt_time(t):
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def cut_video(src, dst, keeps, codec, width=0, progress=None):
    parts, labels = [], []
    for i, (a, b) in enumerate(keeps):
        d = b - a
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.01,afade=t=out:st={max(0, d - 0.01):.3f}:d=0.01[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    scale = f",scale='min({width},iw)':-2:flags=lanczos" if width else ""
    parts.append(f"{''.join(labels)}concat=n={len(keeps)}:v=1:a=1[vc][a]")
    parts.append(f"[vc]null{scale}[v]")
    vcodec = (["-c:v", "libsvtav1", "-preset", "10", "-crf", "40"] if codec == "smallest"
              else ["-c:v", "libx264", "-preset", "faster", "-crf", "23"])
    script = dst + ".filter"
    with open(script, "w") as f:
        f.write(";\n".join(parts))
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", *(["-nostats", "-progress", progress] if progress else []),
                        "-i", src, "-/filter_complex", script,
                        "-map", "[v]", "-map", "[a]", *vcodec, "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst],
                       check=True, env={**os.environ, "SVT_LOG": "1"})
    finally:
        os.remove(script)


def cleanup_marks(cuts, what, total):
    """Where each cut landed on the output timeline and what it removed, for the
    share page's badge, progress-bar ticks and transcript traces."""
    marks, shift = [], 0.0
    for c0, c1 in cuts:
        inside = [lab for w0, w1, lab in what if w0 < c1 and w1 > c0]
        fillers = [lab for lab in inside if lab != "pause"]
        marks.append({"t": round(c0 - shift, 2), "kind": "filler" if fillers else "pause",
                      "label": ", ".join(dict.fromkeys(fillers)) if fillers else f"{c1 - c0:.1f} s pause",
                      "removed": round(c1 - c0, 2)})
        shift += c1 - c0
    # count cuts, not detections: a check pass often re-finds the tail of an "uh" already cut
    return {"fillers": sum(1 for m in marks if m["kind"] == "filler"), "pauses": sum(1 for m in marks if m["kind"] == "pause"),
            "seconds": round(shift, 1), "before": round(total, 1), "after": round(total - shift, 1), "marks": marks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("outdir")
    ap.add_argument("--fillers", action="store_true")
    ap.add_argument("--silences", action="store_true")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--engine", default="auto", choices=["auto", "local", "gpu", "cloud", "cpu"])
    ap.add_argument("--codec", default="compatible")
    ap.add_argument("--pause", type=float, default=1.2)
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--always", action="store_true")
    ap.add_argument("--progress")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    t0 = time.time()
    total = duration(a.input)
    has_audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                "-of", "csv=p=0", a.input], capture_output=True, text=True).stdout.strip() != ""
    words, lang, cuts, what, n_fill, remaining, passes = [], None, [], [], 0, 0, 0
    if has_audio:
        audio = decode_audio(a.input)
        asr = Asr(a.lang, a.engine)
        words = asr.words(audio)
        lang = asr.lang
        if a.fillers:
            fc, n_fill = filler_cuts(words, audio, total)
            what += fc
        if a.silences:
            half = PAUSE_KEEP / 2
            what += [(p0 + half, p1 - half, "pause") for p0, p1 in silences(a.input, a.pause)]
        cuts = [(c0, c1) for c0, c1 in merge([(c0, c1) for c0, c1, _ in what]) if c1 - c0 > 0.05]
        # Listen to the result and cut whatever filler is still audible. Cheap: audio only.
        while cuts and passes < 3:
            passes += 1
            out = cut_audio(audio, cuts)
            words = asr.words(out)
            if not a.fillers:
                break
            more, remaining = filler_cuts(words, out, len(out) / SR)
            if not more:
                break
            back = [(to_source(m0, cuts), to_source(m1, cuts), lab) for m0, m1, lab in more]
            what += back
            n_fill += len(back)
            cuts = [(c0, c1) for c0, c1 in merge(cuts + [(b0, b1) for b0, b1, _ in back]) if c1 - c0 > 0.05]
        if remaining and cuts:   # one last listen so the transcript matches the final cut
            words = asr.words(cut_audio(audio, cuts))
    t_asr = time.time() - t0

    # `words` is already on the output timeline (transcribed from the cut audio)
    out_words = [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in words if not (a.fillers and is_filler(w))]
    remaining = sum(1 for w in words if is_filler(w)) if a.fillers else 0

    cleaned = None
    if cuts or a.always:
        keeps, pos = [], 0.0
        for c0, c1 in cuts:
            if c0 > pos:
                keeps.append((pos, c0))
            pos = c1
        if pos < total:
            keeps.append((pos, total))
        cleaned = os.path.join(a.outdir, "clean.mp4")
        cut_video(a.input, cleaned, keeps, a.codec, a.width, a.progress)

    lines = [{"s": c[0]["s"], "e": c[-1]["e"], "text": " ".join(w["w"] for w in c)} for c in cues(out_words)]
    doc = {"lang": lang, "cues": lines, "words": out_words}
    if cuts:
        doc["cleanup"] = cleanup_marks(cuts, what, total)
    with open(os.path.join(a.outdir, "transcript.json"), "w") as f:
        json.dump(doc, f, ensure_ascii=False)
    with open(os.path.join(a.outdir, "captions.vtt"), "w") as f:
        f.write("WEBVTT\n\n")
        for c in cues(out_words):
            f.write(f"{vtt_time(c[0]['s'])} --> {vtt_time(c[-1]['e'])}\n{' '.join(w['w'] for w in c)}\n\n")
    fillers = doc["cleanup"]["fillers"] if cuts else 0
    report = {"lang": lang, "engine": asr.engine if has_audio else None, "words": len(out_words), "fillers_found": fillers, "fillers_still_heard": remaining,
              "verify_passes": passes,
              "seconds_removed": round(sum(b - a for a, b in cuts), 2), "cuts": len(cuts),
              "duration_in": round(total, 2), "clean": cleaned, "transcribe_s": round(t_asr, 1),
              "total_s": round(time.time() - t0, 1)}
    with open(os.path.join(a.outdir, "report.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
