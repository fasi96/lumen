# Changelog

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
