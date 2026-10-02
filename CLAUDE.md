# Lumen: notes for Claude sessions

Read docs/DEVELOPING.md for what lives where (README.md is for users).

## Before you edit

- Run `git log --oneline -10` and `git status` first. Another session (or another open
  copy of the same conversation) may have changed things since you last looked.
- One live session works on Lumen at a time. If you find uncommitted changes you didn't
  make, ask before touching those files.
- Commit each finished change with a clear message, and push, so the next session starts
  from it.

## Checking your work

- Bar panel/icon (`*.qml` at the repo root): run `omarchy restart shell`, then
  `omarchy-shell lumen open` and screenshot with grim.
- Camera bubble (`cam/`): `lumen-cam` / `lumen-cam stop` (never `pkill -f lumen_cam.py` —
  it matches your own shell). `uv run sim.py` replays 18 scenes; `lumen-cam --demo` shows
  splits and merges live; `LUMEN_PROFILE=1 lumen-cam` prints CPU per stage.
- Recorder (`bin/gif-record`): settings in ~/.config/gif-record/settings.json, log in
  ~/.cache/gif-record/log.
