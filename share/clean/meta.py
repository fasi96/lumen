"""Title, summary and chapters for a shared recording, written from its transcript.

  run meta.py transcript.json [duration_seconds] [--engine auto|local|cloud]

local — Qwen2.5 3B (models/*.gguf) through llama.cpp on this laptop (GPU if there's
        room, else the processor); nothing leaves the machine
cloud — a small Llama on your own Cloudflare account (Workers AI, free allowance)
auto  — local if llama.cpp and the model are installed, else cloud if set up.
Prints {} when there's too little speech, or when no engine is available.
"""

import glob
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = (glob.glob(os.path.join(HERE, "models", "*.gguf")) or [None])[0]


def local_ok():
    # Any failure counts, not just "not installed": a llama_cpp whose CUDA runtime
    # is missing raises RuntimeError on import, and that used to crash the whole step.
    try:
        import llama_cpp  # noqa: F401
        return MODEL is not None
    except Exception:  # noqa: BLE001
        return False


def cloud_conf():
    try:
        c = json.load(open(os.path.expanduser("~/.config/vshare/config.json")))["cloudflare"]
        return c["url"].rstrip("/"), c["token"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def run_local(prompt):
    from llama_cpp import Llama
    sys.path.insert(0, HERE)
    from clean import gpu_ok
    llm = Llama(model_path=MODEL, n_ctx=8192, n_gpu_layers=-1 if gpu_ok(2600) else 0, verbose=False)
    r = llm.create_chat_completion(messages=[{"role": "user", "content": prompt}],
                                   response_format={"type": "json_object"}, temperature=0.3, max_tokens=400)
    return r["choices"][0]["message"]["content"]


def run_cloud(prompt):
    base, token = cloud_conf()
    req = urllib.request.Request(f"{base}/api/complete", data=json.dumps({"prompt": prompt}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": "lumen-meta/1.0",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("text", "")


PROMPT = """You name screen recordings for a Loom-style share page. Below is a timestamped transcript
of a {dur:.0f}-second recording (spoken language: {lang}).

Reply with ONLY a JSON object, no prose, no code fence:
{{"title": "...", "summary": "...", "chapters": [{{"t": 0, "title": "..."}}]}}

- title: what the recording shows or explains, specific and plain, sentence case, at most 60 characters,
  in English. No quotes, no emoji, no "Video of"/"Recording of".
- summary: one or two sentences, at most 200 characters, in English, saying what a viewer will learn or see.
- chapters: only if the recording is at least 60 seconds long AND covers two or more distinct parts;
  otherwise []. 2 to 6 chapters, the first at t=0, each t (seconds) taken from the transcript where that
  part starts, titles at most 4 words.

Transcript:
{lines}"""


def main():
    doc = json.load(open([a for a in sys.argv[1:] if not a.startswith("--")][0]))
    cues = doc.get("cues") or []
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a not in ("auto", "local", "cloud")]
    dur = float(args[1]) if len(args) > 1 else (cues[-1]["e"] if cues else 0)
    words = sum(len(c["text"].split()) for c in cues)
    if words < 6:
        print("{}")
        return
    lines = "\n".join(f"[{c['s']:.0f}s] {c['text']}" for c in cues)[:24000]
    engine = sys.argv[sys.argv.index("--engine") + 1] if "--engine" in sys.argv else "auto"
    requested = engine
    if engine == "auto":
        engine = "local" if local_ok() else "cloud" if cloud_conf() else None
    prompt = PROMPT.format(dur=dur, lang=doc.get("lang") or "unknown", lines=lines)
    meta = {}
    # Try the chosen engine. Only on "auto" may a failed local model fall back to the
    # cloud: "local" (This computer only) means nothing leaves the machine, not even text.
    for eng in [engine] + (["cloud"] if requested == "auto" and engine == "local" and cloud_conf() else []):
        try:
            text = run_local(prompt) if eng == "local" else run_cloud(prompt) if eng == "cloud" else ""
            m = re.search(r"\{.*\}", text, re.S)
            meta = json.loads(m.group(0)) if m else {}
        except Exception as e:  # noqa: BLE001 — a title is a bonus; keep the default on any failure
            print(f"meta: {eng} engine failed: {e}", file=sys.stderr)
            meta = {}
        if meta.get("title"):
            break
    out = {}
    if isinstance(meta.get("title"), str) and meta["title"].strip():
        out["title"] = meta["title"].strip()[:80]
    if isinstance(meta.get("summary"), str) and meta["summary"].strip():
        out["summary"] = meta["summary"].strip()[:280]
    ch = [c for c in meta.get("chapters") or [] if isinstance(c, dict) and "t" in c and c.get("title")]
    if len(ch) >= 2 and dur >= 60:          # small models ignore the "60 s or longer" rule; enforce it
        out["chapters"] = [{"t": max(0.0, min(float(c["t"]), dur)), "title": str(c["title"])[:40]}
                           for c in sorted(ch, key=lambda c: float(c["t"]))]
        out["chapters"][0]["t"] = 0.0
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
