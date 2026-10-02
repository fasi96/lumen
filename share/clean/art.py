"""Images for the share page.

  run art.py storyboard VIDEO OUT.jpg     → prints {"interval", "cols", "w", "h", "count"}
  run art.py card VIDEO OUT.jpg --title T [--meta "Sam · 2:41"] [--badge "4 ums removed"]

storyboard: one JPEG grid of small frames, for previews while hovering the progress bar.
card: the 1200×630 link preview (WhatsApp, Slack, X): a frame of the video, darkened
toward the bottom, a play button, the title and a line of facts.
"""

import argparse
import json
import math
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_BOLD = "/usr/share/fonts/noto/NotoSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
TW, TH = 160, 90          # storyboard tile


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def storyboard(video, out):
    d = duration(video)
    count = int(min(100, max(10, d / 2)))
    interval = d / count
    cols = 10
    rows = math.ceil(count / cols)
    vf = (f"fps={count}/{d:.3f},scale={TW}:{TH}:force_original_aspect_ratio=decrease,"
          f"pad={TW}:{TH}:(ow-iw)/2:(oh-ih)/2:color=black,tile={cols}x{rows}")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-vf", vf, "-frames:v", "1", "-q:v", "5", out], check=True)
    print(json.dumps({"interval": round(interval, 3), "cols": cols, "w": TW, "h": TH, "count": count}))


def wrap(draw, text, font, width, lines=2):
    words, out, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            out.append(cur)
            cur = w
            if len(out) == lines:
                break
    if len(out) < lines and cur:
        out.append(cur)
    if len(out) == lines and " ".join(out) != text:      # didn't fit: ellipsis
        last = out[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1]
        out[-1] = last.rstrip() + "…"
    return out


def sparkle(draw, cx, cy, r):
    """Four-point star (the font has no ✦ glyph)."""
    k = r * 0.28
    draw.polygon([(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k),
                  (cx, cy + r), (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)], fill=(255, 255, 255))


def card(video, out, title, meta="", badge=""):
    W, H = 1200, 630
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        d = duration(video)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(min(1.5, d / 3)), "-i", video,
                        "-frames:v", "1", f.name], check=True)
        frame = Image.open(f.name).convert("RGB")
    # cover-fit the frame
    s = max(W / frame.width, H / frame.height)
    frame = frame.resize((math.ceil(frame.width * s), math.ceil(frame.height * s)), Image.LANCZOS)
    x, y = (frame.width - W) // 2, (frame.height - H) // 2
    img = frame.crop((x, y, x + W, y + H))

    # blur and darken toward the bottom so the title always reads over busy screens
    shade = Image.new("L", (1, H))
    for j in range(H):
        shade.putpixel((0, j), int(255 * min(1, max(0, (j / H - 0.35) / 0.4)) ** 0.9))
    shade = shade.resize((W, H))
    soft = img.filter(ImageFilter.GaussianBlur(10))
    soft = Image.blend(soft, Image.new("RGB", (W, H), (8, 9, 12)), 0.82)
    img = Image.composite(soft, img, shade)
    img = Image.blend(img, Image.new("RGB", (W, H), (8, 9, 12)), 0.22)

    draw = ImageDraw.Draw(img, "RGBA")
    # play button with a soft shadow
    cx, cy, r = W // 2, int(H * 0.40), 58
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((cx - r - 6, cy - r + 4, cx + r + 6, cy + r + 16), fill=(0, 0, 0, 120))
    img.paste(glow.filter(ImageFilter.GaussianBlur(14)), (0, 0), glow.filter(ImageFilter.GaussianBlur(14)))
    draw = ImageDraw.Draw(img, "RGBA")
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 240))
    draw.polygon([(cx - 16, cy - 26), (cx - 16, cy + 26), (cx + 28, cy)], fill=(20, 21, 24))

    # product mark, top-left
    wf = ImageFont.truetype(FONT_BOLD, 28)
    ww = draw.textlength("Lumen", font=wf)
    draw.rounded_rectangle((40, 38, 40 + 70 + ww, 94), radius=28, fill=(8, 9, 12, 190))   # reads over busy screens
    draw.rounded_rectangle((50, 46, 90, 86), radius=11, fill=(139, 140, 246, 255))
    sparkle(draw, 70, 66, 12)
    draw.text((100, 47), "Lumen", font=wf, fill=(245, 246, 248))

    pad = 64
    tf = ImageFont.truetype(FONT_BOLD, 54)
    lines = wrap(draw, title, tf, W - 2 * pad)
    mf = ImageFont.truetype(FONT_REG, 28)
    y = H - pad - 34 - len(lines) * 66
    for ln in lines:
        draw.text((pad, y), ln, font=tf, fill=(245, 246, 248))
        y += 66
    y += 10
    x = pad
    if badge:
        bf = ImageFont.truetype(FONT_BOLD, 24)
        bw = draw.textlength(badge, font=bf) + 60
        draw.rounded_rectangle((x, y - 2, x + bw, y + 38), radius=20, fill=(139, 140, 246, 235))
        sparkle(draw, x + 24, y + 18, 9)
        draw.text((x + 42, y + 4), badge, font=bf, fill=(255, 255, 255))
        x += bw + 16
    if meta:
        draw.text((x, y + 1), meta, font=mf, fill=(200, 203, 208))
    img.save(out, "JPEG", quality=88, optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["storyboard", "card"])
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--title", default="Screen recording")
    ap.add_argument("--meta", default="")
    ap.add_argument("--badge", default="")
    a = ap.parse_args()
    if a.what == "storyboard":
        storyboard(a.video, a.out)
    else:
        card(a.video, a.out, a.title, a.meta, a.badge)


if __name__ == "__main__":
    main()
