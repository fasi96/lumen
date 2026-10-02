# Developing Lumen

How the code is laid out and where things live. For using Lumen, see the [README](../README.md).

## Layout

| Folder | What it is | Installed at (symlink) |
|---|---|---|
| `bin/lumen` | One command for everything: `lumen`, `lumen record`, `lumen share …`, `lumen cam …` (see `lumen help`) | `~/.local/bin/lumen` |
| `bin/gif-record` | The recorder itself: start/stop/set, audio mix, clean-up, hands off to vshare | `~/.local/bin/gif-record` |
| `bin/vshare` | Share-link client: creates the link, uploads, transcripts, titles | `~/.local/bin/vshare` |
| `bin/voice-tune` | Launches the voice tuner | `~/.local/bin/voice-tune` |
| `*.qml`, `manifest.json` | Top-bar widget + popup (Quickshell QML, plugin id `lumen`), at the repo root so `omarchy plugin add` works | `~/.config/omarchy/plugins/lumen` |
| `setup/` | `lumen setup` / `lumen uninstall`, and the three speech-AI install profiles | — |
| `tuner/` | Voice tuner window (python server on :47614 + chromium app) | — |
| `share/` | Cloudflare worker: share page, player, dashboard, R2 + D1, Workers AI | — |
| `share/clean/` | AI clean-up: Whisper transcription, um/pause removal, titles, thumbnails | (inside share) |

Setup links the commands into `~/.local/bin`; the plugin itself is wherever `omarchy plugin add` cloned it.

## Your own setup (not in the repo)

- `share/wrangler.toml` is local-only (your Cloudflare names and database ID); the repo has `share/wrangler.toml.example`.
- Scripts find the rest of Lumen from where they're installed, so the folder can live anywhere.

## User data (not in this folder)

- Settings: `~/.config/gif-record/settings.json`, voice profiles in `~/.config/gif-record/voice.json`
- Share config: `~/.config/vshare/config.json`, share index `~/.local/share/vshare/shares.json`
- Recordings: `~/Videos/`, `~/Videos/GIFs/`, `~/Videos/uncut/`

## Common tasks

```
lumen                              # start / stop (same as gif-record toggle)
lumen set <key> <value>            # change a setting
omarchy restart shell              # reload the bar after editing the QML files
lumen setup cloudflare              # (re)publish your share site
cd share/clean && ./run clean.py IN OUTDIR --fillers --silences
```
