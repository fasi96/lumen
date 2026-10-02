# ✦ Lumen

**Loom-style screen recordings for Omarchy, without the subscription.**

Click the ✦ in your top bar, record your screen with your voice and your face in a bubble, and when you stop, the ums and long pauses are already cut out and a share link is on your clipboard. The video lives in **your own** Cloudflare account, which is free for most people, or simply stays on your computer.

<!-- demo GIF goes here -->

<!-- ▶ Watch the 1-minute demo (a Lumen share link) goes here -->

## What it does

- **Records** an area you drag, one window, or a whole screen, as MP4 (or GIF).
- **Your mic**, optionally mixed with what the computer plays.
- **Removes ums and long pauses** automatically. The speech AI runs on your NVIDIA card, on your own Cloudflare account, or on your processor, and you choose where. The uncut recording is always kept.
- **Share links the moment you stop.** The link is copied before the upload even finishes. The page has a proper player, a clickable transcript, chapters, an optional password and expiry, and view counts, with a dashboard for all your videos.
- **Face bubble (beta)**: your webcam in a bubble that follows your outline, reaches for your hands and is recorded with everything else. Hover it to change its size, shape and outline.
- **Titles and summaries** written by AI for every shared video.

## Why Lumen, not Loom?

Loom is great, but on Linux it doesn't exist: Loom only makes desktop apps for Windows and Mac, and doesn't support its Chrome extension on Linux. And the parts that make Loom videos nice to watch cost extra.

| | **Lumen** | **Loom** |
|---|---|---|
| **Runs on Linux / Omarchy** | Yes, natively in your top bar | No |
| **Price** | Free¹ | Free plan, or $18–24 per person per month ($15–20 paid yearly) |
| **Video length and count** | No limit from Lumen (your Cloudflare storage: 10 GB free) | Free plan: 5 minutes, 25 videos |
| **Removes ums and long pauses** | Included | Only on Business + AI ($20–24 per person per month) |
| **AI titles, summaries, chapters** | Included | Only on Business + AI |
| **Where your videos live** | Your own Cloudflare account (on Cloudflare's servers), or just your computer | Loom's servers |
| **Speech AI can run on your own machine** | Yes | No |
| **Open source** | Yes (MIT) | No |

**When Loom is the better choice:** if your whole team shares a workspace, wants comments and reactions on videos, uses Windows or Mac apps, or needs editing tools, Loom does those things and Lumen doesn't (yet). Lumen is for recording a clear video on your own Linux machine and sending a link, without paying for it or handing your videos to someone else.

¹ Lumen itself is free. Share links use your own Cloudflare account. Its free tier covers 10 GB of videos and about 214 minutes a day of speech AI, and beyond that Cloudflare charges $0.015 per GB per month for storage and $0.0005 per minute of speech. Cloudflare asks for a card or PayPal before it switches storage on (usage isn't capped), so keep an eye on its dashboard if you record a lot. Without share links, nothing goes to Cloudflare at all.

<sub>Loom details from [loom.com/pricing](https://www.loom.com/pricing) and [Loom's device compatibility page](https://support.atlassian.com/loom/docs/loom-device-compatibility/); Cloudflare from its [R2](https://developers.cloudflare.com/r2/pricing/) and [Workers AI](https://developers.cloudflare.com/workers-ai/platform/pricing/) pricing pages. Checked October 2026.</sub>

## Install

You need [Omarchy](https://omarchy.org) 4.

```
omarchy plugin add https://github.com/fasi96/lumen --enable
```

Then click the **✦** in the bar → **Set up Lumen**. Setup takes about 3 minutes:

1. Installs anything missing (it asks for your password once).
2. Asks **where videos go**: your own Cloudflare account (share links) or just this computer.
3. Asks **where the speech AI runs**: automatic, this computer only, or off.
4. Your name for share pages, and whether to turn on the face bubble.
5. Adds the shortcut **Shift+Alt+Print** (start / stop), unless those keys are already taken.
6. Checks your mic.

Run it again any time: `lumen setup`, or **Settings → Setup & accounts…** in the popup.

### Share links need a (free) Cloudflare account

If you pick "My Cloudflare", setup creates everything in **your** account: storage for the videos (R2), a small database, and the share site. It walks you through signing in. Cloudflare's free tier covers 10 GB of videos and about 214 minutes a day of speech AI. Cloudflare needs a card or PayPal on file before it turns storage on, even for the free tier. Lumen's author has no access to your account or your videos.

## Use it

| | |
|---|---|
| **Record** | Click ✦ → **Start recording**, or press **Shift+Alt+Print** |
| **Stop** | Click the red timer in the bar, or press the shortcut again |
| **Your last recording** | At the bottom of the popup: open, copy, share, show in folder |
| **Shared videos** | Settings → **Shared videos…** (rename, password, delete, views) |
| **Face bubble** | Drag to move it, scroll to resize it, hover it for the toolbar. *Center me* (under the Face bubble switch) frames you |

From a terminal, `lumen help` lists everything: `lumen record`, `lumen stop`, `lumen share list`, `lumen cam center`, …

## Privacy

- Recordings are saved in `~/Videos`. Nothing is uploaded unless you turn on share links.
- With **"This computer only"**, your audio never leaves your machine.
- With share links, videos and transcripts go only to **your** Cloudflare account. Anyone who has a link can watch that video, so set a password on anything private.
- With **"This computer only"**, AI titles are only written if the local model can run on your machine; otherwise the video keeps a plain title. Nothing is sent to Cloudflare's AI.
- Lumen has no servers, accounts or analytics of its own.

## Known limitations

- **Graphics:** the main recorder needs Intel, AMD or NVIDIA graphics. Elsewhere (for example in a virtual machine), Lumen switches to a backup recorder automatically. The backup records your mic but **not desktop sound**.
- **Um removal without an NVIDIA card** runs on Cloudflare or your processor. On the processor it took about **3× longer** than on an NVIDIA card in our test, with the same result.
- **The face bubble is in beta.** It uses roughly one processor core while it's open.
- **Two people far apart** in the face bubble each show as half a person.
- Big **4K at 60 fps** recordings take a while to save. The default (1080p, 30 fps) saves about 3× faster.

## Uninstall

```
lumen uninstall
```

This removes the commands, the speech AI and its model, the shortcut and the bar plugin, and, if you say yes, your Lumen settings. **Your recordings are never touched.** Your Cloudflare share site stays unless you choose to delete it too.

## Contributing

See [docs/DEVELOPING.md](docs/DEVELOPING.md) for how the code is laid out. Bug reports and pull requests are welcome.

## Licence

[MIT](LICENSE)
