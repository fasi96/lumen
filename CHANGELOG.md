# Changelog

## 1.3.2

Two privacy fixes from the marketplace review, and a licence file.

- **The private choices in setup are private, as they say.** Setup offered "On my NVIDIA card — fast and private" but saved the *Automatic* setting, which sends the soundtrack to your Cloudflare's AI when the card is busy or out of memory (only if you have a share site). Without an NVIDIA card, "On this computer — private" was read as the NVIDIA choice: it saved *Automatic* too, and installed NVIDIA's libraries for nothing. Both now save *This computer*: the card, or the processor when the card is busy or missing, never Cloudflare. If you made either choice before 1.3.2, Lumen moves you to *This computer* at your next recording, once, and tells you. Pick *Automatic* in the popup's Settings if you want the Cloudflare fallback, and titles written by Cloudflare's AI, back.
- **Switching "Remove ums" on always asks where the speech AI runs.** With share links set up first, the switch used to come on straight away and use your Cloudflare's AI without asking. A clean-up with nothing chosen now stays on this computer.
- **An empty name stays empty.** Setup says that leaving your name empty shows the ✦ mark, but the link-preview card then showed your account's full name or login. The card now shows only a name you saved in setup, like the share page; with none, the ✦ mark and the video's length. A card made before 1.3.2 keeps what it has until you redraw it: `lumen share polish ID`.
- Setup says where the name is shown, and offers an empty name again if that's what you saved.
- Run by hand, the clean-up and title tools (`share/clean/clean.py`, `meta.py`) stay on this computer unless you pass `--engine auto` or `cloud`. Lumen itself always tells them where to run, so this changes nothing in the app.
- The Apache License 2.0 text for the two bundled MediaPipe models is in `cam/models/LICENSE-Apache-2.0.txt`.

## 1.3.1

- **Share site: transcription counts by minutes of audio**, not against a device key's 600 Workers AI calls a day: 240 minutes a day per key (`DEVICE_AUDIO_MIN_PER_DAY`), a little over what Cloudflare's free allowance covers. Live captions (Lumen Notes for Chrome) send a sentence every few seconds and would have hit the call cap within the hour. Titles and summaries keep the call cap. `/api/info` shows a device its allowance and what it used today. The site updates its database by itself (schema 4).

## 1.3.0

- **Lumen for Chrome can share to your share site:** `lumen share connect-code --copy` makes a one-time code with its own device key (it can only share and manage the videos it made; revoke it on the dashboard under Devices).
- **Share page on phones:** edge-to-edge video, controls that fit one row, working fullscreen; it also waits for a transcript that's still being written.
- **Security review fixes** (share site, recorder, setup):
  - The share page and its link previews only ever treat a recording's details (size, codec, the clean-up badge, the transcript) as data, never as page markup; transcripts must be JSON and have a size limit.
  - A device key has a daily allowance of Workers AI calls and uploads, can't upload once the site is past the free 10 GB, and request bodies are size-limited.
  - Video passwords: PBKDF2 instead of one SHA-256 (old ones are upgraded on their next unlock), 10 wrong tries lock the form for 15 minutes, the unlock cookie no longer contains the hash, and a locked video's page doesn't show its title.
  - Dashboard: sessions end after 30 days on the server, there's a Sign out button, and the session cookie only changes things from the dashboard's own pages.
  - Errors don't show internal details; video ids are unbiased random text.
  - The share link and the card's title go to `wl-copy` and the card renderer on stdin, never on a command line (other users can read those); `vshare setup` takes the key only on stdin.
  - vshare and the clean-up tools refuse redirects (the key never follows one), insist on https, read replies with a size limit, and accept only well-formed share ids from the site.
  - Python started from setup and the bubble's control script doesn't import modules from the folder you're in; no empty entry on the loader path; `npm ci --ignore-scripts`.
  - The clean-up writes private files with fresh names and decodes at most 4 hours of audio; the setup test uses a new private folder.
  - The developer-only camera scripts have hash locks; the bundled models' NOTICE cites versioned download links.
  - **The face bubble gets its own environment** (`~/.local/share/lumen/cam-venv`), installed from hash-locked lists that include the tools building pycairo and pygobject from source, so every file is checked. If you set the bubble up before 1.3.0 it keeps working offline from the old one; `lumen setup bubble` moves it over.

## 1.2.2

- Share links: setup pins your Cloudflare account in the share site's settings, so an update can never publish into a different account; if wrangler is signed in to the wrong one, it says so and how to switch.
- Uninstall also tidies the recorder's empty folders.
- README: the talking bubble.

## 1.2.1

- Setup's share-site check no longer writes curl's reply to a file (the marketplace scanner read `curl … >/dev/null` followed by `npm ci … >/dev/null` as a download being run). Same behaviour.
- Uninstall and add-on instructions work without the optional terminal commands.

## 1.2.0

- **Sound presets one click away:** a Sound menu right under the mic (Natural, Podcast, Crisp, Noisy room, Raw), or *Fine-tune by ear…* for the tuner. Each mic keeps its own.
- **Noise removal works on every machine:** it used to need an RNNoise model file Lumen never installed; without it, ffmpeg's own denoiser now does the job at the same strength.
- README: the little things (presets, multilingual ums, the share page's previews and cut marks, link preview cards), and that every recording stays an MP4 on your computer, shared or not; new marketplace preview.

## 1.1.0

Simpler to start, and ready for the Omarchy plugin marketplace.

- **New plugin id: `io.github.fasi96.lumen`**, shown as "Lumen Recorder" (another plugin in the catalogue is already called Lumen). If you installed 1.0.0: run `lumen uninstall` (or `omarchy plugin remove lumen`), then `omarchy plugin add https://github.com/fasi96/lumen --enable`. Your settings and recordings stay.

- **Record right away.** No setup screen: recording uses only what Omarchy ships.
- **Add-ons install when you switch them on**: remove ums, face bubble and share links each install only what they need, after asking. `lumen setup` is a checklist.
- **The face bubble only shows when it's useful**: while the popup is open and while you record. × on the bubble turns it off; no bubble without a webcam.
- **Uninstall removes exactly what setup added** (a checksummed record), keeps anything you've edited, moves crash leftovers to `~/Videos`, and still works after the plugin is removed.
- **Privacy and secrets:** Lumen's files are private (0700/0600), the share site's key never appears on a command line, the dashboard signs in with a session cookie, and the mic tuner's local server needs a per-run token.
- **Pinned downloads:** Python packages with hashes, the speech model at a fixed revision, `npm ci` for the share site's tools.
- Fixed: MP4 saving failed on a fresh machine with a tuned mic (missing noise model).
- **Your share site needs updating** for the new dashboard sign-in: `lumen setup site`.

## 1.0.0 (unreleased)

The first public release.

- Record an area, a window or a whole screen as MP4 (or GIF), with your mic and, optionally, desktop sound.
- Ums and long pauses removed automatically; the speech AI runs on an NVIDIA card, your own Cloudflare account, or your processor. The uncut recording is kept.
- Share links on your own Cloudflare account, copied the moment you stop: player, clickable transcript, chapters, password, expiry, views, dashboard, AI titles and summaries.
- Face bubble (beta): your webcam in a bubble that follows your outline; size, shape, outline and framing from its toolbar.
- `lumen setup`: installs what's missing, connects Cloudflare, picks where the AI runs, adds the Shift+Alt+Print shortcut, checks the mic. `lumen uninstall` removes it all except your recordings.
- Backup recorder (wf-recorder) when the graphics card isn't supported, and a clear message when recording can't start.
