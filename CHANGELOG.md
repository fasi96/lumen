# Changelog

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
