#!/bin/bash
# lumen setup: the optional add-ons. Recording itself needs no setup: everything it
# uses ships with Omarchy. Each add-on installs only what it needs, the first time
# you switch it on in the popup (or pick it here). Safe to run again.
#
#   lumen setup              pick add-ons from a checklist
#   lumen setup ums          remove ums & pauses: the speech AI (asks where it runs)
#   lumen setup bubble       the face bubble's camera AI
#   lumen setup share        share links on your own Cloudflare
#   lumen setup shortcut     Shift+Alt+Print starts and stops a recording
#   lumen setup commands     lumen, gif-record, vshare… in ~/.local/bin for the terminal
#   lumen setup recorder     the backup recorder (wf-recorder) for unsupported graphics
#   lumen setup status       what's ready, as JSON (the popup reads this)
#   lumen setup check        the same, for people
#   lumen setup ai PROFILE   install the speech AI directly: cloud | local | nvidia
#   lumen setup cloudflare   connect (or reconnect) your Cloudflare account
#   lumen setup site         update your share site to this version of Lumen (keeps your videos)
#   lumen uninstall          remove what setup added (asks before anything you'd miss)
#
# Where things go:
#   ~/.local/share/lumen/venv      speech-AI tools      ~/.local/share/lumen/worker   the share site's code
#   ~/.config/lumen/setup.json     what setup chose     ~/.config/gif-record, ~/.config/vshare: settings
set -uo pipefail
umask 077   # everything setup creates (environments, keys, settings) is private

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# Normally this runs from the plugin folder. A copy also lives in ~/.local/share/lumen/uninstaller,
# so `lumen uninstall` still works after `omarchy plugin remove`; that copy finds the plugin
# folder (if it's still there) from the install record.
if [[ -f $HERE/../manifest.json ]]; then
  LUMEN_HOME="$(cd "$HERE/.." && pwd)"
else
  LUMEN_HOME=$(jq -r '.facts.home // empty' "$HOME/.config/lumen/installed.json" 2>/dev/null)
fi
RECORD=(python3 "$HERE/record.py")
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/lumen"
VENV="${LUMEN_VENV:-$DATA/venv}"
WORKER="$DATA/worker"
STATE="$HOME/.config/lumen/setup.json"
REC_CONF="$HOME/.config/gif-record/settings.json"
SHARE_CONF="$HOME/.config/vshare/config.json"
BIN="$HOME/.local/bin"
MODEL="large-v3-turbo"
MODEL_REVISION="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"   # same pin as share/clean/clean.py
MODEL_CACHE="$HOME/.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-$MODEL"
GR="$LUMEN_HOME/bin/gif-record"
# What each part needs, as "how to tell it's there = the Arch package that provides it".
# Checked by what works, not by package name, so tools installed another way (mise, pipx…) count.
# Plain recording needs only what Omarchy ships (gpu-screen-recorder, ffmpeg, jq, slurp,
# wl-clipboard, pactl), so it has no entry here.
NEEDS_AI=("command -v uv=uv")
NEEDS_BUBBLE=("command -v uv=uv" "test -e /usr/lib/libgtk4-layer-shell.so=gtk4-layer-shell" "python3 -c 'import gi'=python-gobject")
NEEDS_SHARE=("command -v uv=uv" "command -v node=nodejs" "command -v npm=npm")
NEEDS_RECORDER=("command -v wf-recorder=wf-recorder")

# ---------- small helpers

accent() { gum style --foreground 105 "$@"; }
say() { gum style --margin "0 2" "$@"; }
title() { echo; gum style --bold --foreground 105 --margin "0 2" "✦ $*"; }
ok() { gum style --margin "0 2" --foreground 114 "✓ $*"; }
warn() { gum style --margin "0 2" --foreground 214 "! $*"; }
fail() { gum style --margin "0 2" --foreground 203 "✗ $*"; }
state_get() { jq -r --arg k "$1" '.[$k] // empty' "$STATE" 2>/dev/null; }
# json_set FILE KEY VALUE — private atomic update (a fresh 0600 temp next to it, then rename).
json_set() {
  local f=$1 tmp
  mkdir -p "$(dirname "$f")"
  [[ -f $f ]] || echo '{}' >"$f"
  tmp=$(mktemp "$f.XXXXXX") || return 1
  jq --arg k "$2" --arg v "$3" '.[$k] = $v' "$f" >"$tmp" && mv "$tmp" "$f" || { rm -f "$tmp"; return 1; }
}
state_set() { json_set "$STATE" "$1" "$2"; }
has_nvidia() { nvidia-smi -L >/dev/null 2>&1; }
cf_url() { jq -r '.cloudflare.url // empty' "$SHARE_CONF" 2>/dev/null; }
cf_token() { jq -r '.cloudflare.token // empty' "$SHARE_CONF" 2>/dev/null; }
cf_answers() {
  local url token
  url=$(cf_url) token=$(cf_token)
  [[ -n $url && -n $token ]] || return 1
  # The key goes to curl through its config on stdin, never on its command line.
  printf 'header = "Authorization: Bearer %s"\n' "$token" | curl -fsS -m 10 -A lumen-setup -K - "$url/api/videos" >/dev/null 2>&1
}
venv_profile() {
  local v=$VENV
  # An older install kept its environment inside the plugin folder; the tools still use it.
  [[ ! -x $v/bin/python && -x $LUMEN_HOME/share/clean/.venv/bin/python ]] && v=$LUMEN_HOME/share/clean/.venv
  [[ -x $v/bin/python ]] || { echo none; return; }
  local VENV=$v
  if [[ -d $(echo "$VENV"/lib/python3*/site-packages/nvidia) ]]; then echo nvidia
  elif [[ -d $(echo "$VENV"/lib/python3*/site-packages/faster_whisper) ]]; then echo local
  else echo cloud; fi
}
model_ready() {
  "$LUMEN_HOME/share/clean/run" - "$MODEL" "$MODEL_REVISION" <<'PY' 2>/dev/null
import sys
from faster_whisper.utils import download_model
download_model(sys.argv[1], revision=sys.argv[2], local_files_only=True)
PY
}

# ---------- packages and commands

# need_pkgs NEEDS_ARRAY_NAME "what for" — install whatever is missing, after asking.
need_pkgs() {
  local -n list=$1
  local missing=() n
  for n in "${list[@]}"; do eval "${n%=*}" >/dev/null 2>&1 || missing+=("${n##*=}"); done
  ((${#missing[@]})) || return 0
  say "$2 needs: ${missing[*]} (from the Arch repositories)"
  gum confirm "Install them now? (asks for your password)" || { fail "Not installed"; return 1; }
  sudo pacman -S --needed --noconfirm "${missing[@]}" || { fail "Installing packages failed"; return 1; }
  ok "Installed ${missing[*]}"
}

# Commands on your PATH for the terminal (the popup and the shortcut don't need them).
link_commands() {
  # Never replaces a command you already have under the same name (it's skipped and named).
  for c in lumen gif-record vshare lumen-cam voice-tune; do
    "${RECORD[@]}" link "$c" "$LUMEN_HOME/bin/$c" | sed 's/^/  /'
  done
  state_set commands yes
  ok "Terminal commands ready (lumen help lists them)"
}

# A copy of the uninstaller outside the plugin folder, so removing the plugin first
# doesn't strand what setup added. Refreshed by every setup step.
keep_uninstaller() {
  local u="$DATA/uninstaller"
  mkdir -p "$u"
  install -m 700 "$LUMEN_HOME/setup/setup.sh" "$u/setup.sh"
  install -m 600 "$LUMEN_HOME/setup/record.py" "$u/record.py"
  "${RECORD[@]}" set home "$LUMEN_HOME"
}

# ---------- share links

setup_share() {
  title "Share links on your own Cloudflare"
  if cf_answers; then
    ok "Already connected: $(cf_url)"
    gum confirm --default=false "Connect a different Cloudflare account instead?" || { "$GR" set share cloudflare >/dev/null; return 0; }
  fi
  say "Lumen creates a small share site in your Cloudflare account. Free up to 10 GB of videos;" \
      "Lumen's author never sees your account or your videos."
  need_pkgs NEEDS_SHARE "Share links" || return 1
  install_ai cloud quiet || return 1       # share pages' preview images need numpy + pillow
  ask_name
  connect_cloudflare || return 1
  state_set storage cloudflare
  "$GR" set share cloudflare >/dev/null
  ok "Share links are on: the link is copied the moment you stop"
}

connect_cloudflare() {
  title "Connecting your Cloudflare"
  say "Two things Cloudflare asks for once, on a new account:" \
      "  • R2 storage has to be switched on, which needs a payment method on file." \
      "    It stays free up to 10 GB, and watching videos is always free." \
      "  • A free workers.dev subdomain for your share links (you pick the name)."
  gum confirm "Open Cloudflare's R2 page to check it's switched on?" && xdg-open "https://dash.cloudflare.com/?to=/:account/r2/overview" >/dev/null 2>&1
  gum confirm "Ready to continue?" || return 1

  # The share site's code gets built outside the plugin folder, with the exact tool versions
  # in its lockfile (npm ci), and wrangler is run from there (no registry fallback).
  "${RECORD[@]}" own-dir "$WORKER"
  rsync -a --delete --exclude node_modules --exclude .wrangler --exclude wrangler.toml --exclude .dev.vars --exclude .lumen-owned \
    "$LUMEN_HOME/share/" "$WORKER/" || return 1
  (cd "$WORKER" && npm ci --silent --no-audit --no-fund) >/dev/null 2>&1 || { fail "Couldn't install the share site's tools (npm)"; return 1; }
  local wr=("$WORKER/node_modules/.bin/wrangler")
  # Wrangler's output (the database id, the site's address) stays in a private file.
  local log
  log=$(mktemp -p "${XDG_RUNTIME_DIR:-$DATA}" lumen-setup.XXXXXX) || return 1

  local how=""
  if [[ -n ${CLOUDFLARE_API_TOKEN:-} ]]; then
    ok "Using the Cloudflare API token already set in CLOUDFLARE_API_TOKEN"
  else
    how=$(gum choose --header "  How do you want to sign in to Cloudflare?" \
      "In the browser (recommended)" "Paste an API token") || return 1
  fi
  if [[ -z $how ]]; then
    :
  elif [[ $how == Paste* ]]; then
    say "Create one at dash.cloudflare.com → My Profile → API Tokens → Create Token →" \
        "'Edit Cloudflare Workers' template, plus: D1 Edit, Workers R2 Storage Edit, Workers AI Read."
    gum confirm "Open that page?" && xdg-open "https://dash.cloudflare.com/profile/api-tokens" >/dev/null 2>&1
    export CLOUDFLARE_API_TOKEN
    CLOUDFLARE_API_TOKEN=$(gum input --password --header "  Paste the token") || return 1
  else
    (cd "$WORKER" && "${wr[@]}" login) || { fail "Browser sign-in didn't finish — run 'lumen setup cloudflare' and choose the API token"; return 1; }
  fi
  local account=${CLOUDFLARE_ACCOUNT_ID:-}
  [[ -n $account ]] || account=$(cd "$WORKER" && "${wr[@]}" whoami 2>/dev/null | grep -oE '[0-9a-f]{32}' | head -1)
  [[ -n $account ]] || { fail "Couldn't read your Cloudflare account"; return 1; }
  export CLOUDFLARE_ACCOUNT_ID=$account
  ok "Signed in (account ${account:0:6}…)"

  # Names with a short random tail, so they never collide with anything you have.
  local tail name bucket db dbid admin owner
  tail=$(openssl rand -hex 3)
  name="lumen-share-$tail" bucket="lumen-videos-$tail" db="lumen-$tail"
  owner=$(jq -r '.owner // empty' "$SHARE_CONF" 2>/dev/null)
  cd "$WORKER" || return 1
  gum spin --title "Creating storage ($bucket)…" -- "${wr[@]}" r2 bucket create "$bucket" >"$log" 2>&1 \
    || { fail "Creating R2 storage failed — is R2 switched on? (see "$log")"; return 1; }
  gum spin --title "Creating the database ($db)…" -- "${wr[@]}" d1 create "$db" >"$log" 2>&1 \
    || { fail "Creating the database failed (see "$log")"; return 1; }
  dbid=$(grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' "$log" | head -1)
  sed -e "s/^name = .*/name = \"$name\"/" -e "s/^bucket_name = .*/bucket_name = \"$bucket\"/" \
      -e "s/^database_name = .*/database_name = \"$db\"/" -e "s/^database_id = .*/database_id = \"$dbid\"/" \
      wrangler.toml.example >wrangler.toml
  # The name goes in as a TOML string, escaped (quotes, backslashes, sed's own & and /).
  python3 - "$owner" <<'PY'
import json, re, sys
t = open("wrangler.toml").read()
open("wrangler.toml", "w").write(re.sub(r'(?m)^OWNER_NAME = .*$', lambda m: "OWNER_NAME = " + json.dumps(sys.argv[1]), t))
PY
  gum spin --title "Setting up the database…" -- "${wr[@]}" d1 execute "$db" --remote --file schema.sql >>"$log" 2>&1 \
    || { fail "Setting up the database failed (see "$log")"; return 1; }
  gum spin --title "Publishing your share site…" -- "${wr[@]}" deploy >"$log" 2>&1 \
    || { fail "Publishing failed — a new account may need a workers.dev subdomain first (see "$log")"; return 1; }
  local url
  url=$(grep -oE 'https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev' "$log" | head -1)
  [[ $url =~ ^https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev$ ]] || { fail "Couldn't read your share site's address from wrangler"; return 1; }
  admin=$(openssl rand -hex 24)
  printf '%s' "$admin" | "${wr[@]}" secret put ADMIN_TOKEN >/dev/null 2>&1 || { fail "Couldn't store the share site's key"; return 1; }
  # Video passwords are hashed with a salt of this install's own (never change it later).
  openssl rand -hex 16 | tr -d '\n' | "${wr[@]}" secret put PASSWORD_SALT >/dev/null 2>&1 || { fail "Couldn't store the share site's password salt"; return 1; }
  printf '%s\n' "$admin" | "$LUMEN_HOME/bin/vshare" setup cloudflare "$url" - >/dev/null || { fail "Couldn't save the share settings"; return 1; }
  state_set worker "$name"
  sleep 3
  cf_answers && ok "Your share site is live: $url" || warn "Published $url, but it isn't answering yet — try 'lumen setup check' in a minute"
  rm -f "$log"
}

# Push this version's share-site code to the site you already have. Videos, links,
# passwords and keys stay; only the code changes.
update_site() {
  title "Update your share site"
  local dir
  if [[ -f $WORKER/wrangler.toml ]]; then
    dir=$WORKER
    rsync -a --exclude node_modules --exclude .wrangler --exclude wrangler.toml --exclude .dev.vars --exclude .lumen-owned \
      "$LUMEN_HOME/share/" "$WORKER/" || return 1
  elif [[ -f $LUMEN_HOME/share/wrangler.toml ]]; then
    dir=$LUMEN_HOME/share      # a site set up by hand from the plugin folder
  else
    fail "No share site set up on this computer (lumen setup share)"; return 1
  fi
  need_pkgs NEEDS_SHARE "Updating the share site" || return 1
  (cd "$dir" && npm ci --silent --no-audit --no-fund) >/dev/null 2>&1 || { fail "Couldn't install the share site's tools (npm)"; return 1; }
  local log
  log=$(mktemp -p "${XDG_RUNTIME_DIR:-$DATA}" lumen-setup.XXXXXX) || return 1
  gum spin --title "Publishing…" -- bash -c 'cd "$1" && node_modules/.bin/wrangler deploy' _ "$dir" >"$log" 2>&1 \
    || { fail "Publishing failed (see $log)"; return 1; }
  rm -f "$log"
  cf_answers && ok "Your share site is up to date: $(cf_url)" || warn "Published, but it isn't answering yet — try 'lumen setup check' in a minute"
}

# ---------- remove ums & pauses (the speech AI)

setup_ums() {
  title "Remove ums & long pauses"
  say "When you stop, a speech AI listens for ums and long pauses and cuts them. The uncut copy is kept."
  local opts=()
  if has_nvidia; then opts+=("On my NVIDIA card — fast and private (about 4 GB once)")
  else opts+=("On this computer — private, a bit slow without NVIDIA (about 2 GB once)"); fi
  cf_answers && opts+=("On my Cloudflare — smallest install (80 MB)")
  local pick profile ai
  pick=$(gum choose --header "  Where should it run?" "${opts[@]}") || return 1
  case $pick in
  *NVIDIA*) profile=nvidia ai=auto ;;
  *Cloudflare*) profile=cloud ai=cloud ;;
  *) profile=local ai=local ;;
  esac
  need_pkgs NEEDS_AI "The speech AI" || return 1
  install_ai "$profile" || return 1
  "$GR" set ai "$ai" >/dev/null
  "$GR" set clean on >/dev/null
  state_set ums "$profile"
  ok "On: ums and long pauses are cut when you stop"
}

install_ai() {
  local want=$1 quiet=${2:-} have=none
  # Only the environment in its proper place counts; an older in-place one gets replaced.
  if [[ -x $VENV/bin/python ]]; then
    have=$(LUMEN_HOME=/nonexistent venv_profile)
  fi
  # A bigger install already covers a smaller one (nvidia ⊃ local ⊃ cloud).
  case $want:$have in cloud:local | cloud:nvidia | local:nvidia) want=$have ;; esac
  [[ -z $quiet ]] && title "Installing the speech AI ($want)"
  if [[ $have == "$want" ]]; then
    [[ -z $quiet ]] && ok "Already installed"
  else
    case $want in
    cloud) say "About 80 MB." ;;
    local) say "About 400 MB, plus a 1.6 GB speech model (once)." ;;
    nvidia) say "About 2.6 GB (NVIDIA's libraries), plus a 1.6 GB speech model (once)." ;;
    esac
    # Python 3.12: uv fetches its own private copy if this computer doesn't have one.
    [[ -x $VENV/bin/python ]] || uv venv -q --python 3.12 "$VENV" || { fail "Couldn't create the Python environment"; return 1; }
    "${RECORD[@]}" own-dir "$VENV"
    # Exact versions, each checked against its recorded hash.
    # uv's own progress bars, not a spinner: on a slow connection this takes a while.
    uv pip install --require-hashes --python "$VENV/bin/python" -r "$LUMEN_HOME/setup/requirements-$want.lock" \
      || { fail "Installing the speech AI failed — run 'lumen setup ums' to try again"; return 1; }
    ok "Installed"
  fi
  state_set ai_profile "$want"
  if [[ $want != cloud ]] && ! model_ready; then
    say "Downloading the speech model ($MODEL, 1.6 GB):"
    # Quiet the Hugging Face "unauthenticated requests" warning; a token isn't needed for this public model.
    [[ -d $MODEL_CACHE ]] || "${RECORD[@]}" set model_downloaded_by_lumen yes
    HF_HUB_VERBOSITY=error PYTHONWARNINGS=ignore \
      "$VENV/bin/python" -c "from faster_whisper.utils import download_model; download_model('$MODEL', revision='$MODEL_REVISION')" \
      || { fail "The download stopped — run 'lumen setup ai $want' to try again"; return 1; }
    ok "Speech model ready"
  fi
  local legacy=$LUMEN_HOME/share/clean/.venv
  if [[ -d $legacy ]] && "$LUMEN_HOME/share/clean/run" -c "import numpy" 2>/dev/null; then
    say "An older copy of the AI tools is still inside the plugin folder ($(du -sh "$legacy" | cut -f1)); the new one replaces it."
    gum confirm "Delete the old copy?" && rm -rf "$legacy" && ok "Old copy deleted"
  fi
}

# ---------- 4. name and face bubble

ask_name() {
  title "Your name on share pages"
  local current def name
  current=$(jq -r '.owner // empty' "$SHARE_CONF" 2>/dev/null)
  def=${current:-$(getent passwd "$USER" | cut -d: -f5 | cut -d, -f1)}
  name=$(gum input --value "$def" --header "  Leave it empty to show the ✦ mark instead") || exit 1
  json_set "$SHARE_CONF" owner "$name"
  ok "${name:-(no name, the ✦ mark)}"
}

setup_bubble() {
  title "Face bubble (beta)"
  say "Your webcam in a bubble that follows your outline, recorded with everything else." \
      "It shows while the popup is open and while you record, then gets out of the way."
  ls /dev/video* >/dev/null 2>&1 || warn "No webcam is plugged in right now; it'll work once one is."
  need_pkgs NEEDS_BUBBLE "The face bubble" || return 1
  say "Getting the camera AI ready (about 0.5 GB, once):"
  uv sync --locked --script "$LUMEN_HOME/cam/lumen_cam.py" || { fail "Couldn't install the camera AI — run 'lumen setup bubble' to try again"; return 1; }
  state_set bubble yes
  "$GR" set camera on >/dev/null
  ok "On: hover the bubble to change its size, shape and outline"
}

setup_recorder() {
  title "Backup recorder"
  say "For graphics the main recorder can't use (for example in a virtual machine)." \
      "It records your mic, but not desktop sound."
  need_pkgs NEEDS_RECORDER "The backup recorder" && ok "Ready"
}

# The keyboard shortcut: Shift+Alt+Print starts and stops a recording. It goes into the
# user's own Hyprland bindings as one fenced block, recorded with its checksum, so
# uninstall takes out exactly that block (and leaves it if the user has edited it).
# Added only when asked, and only if nothing is bound to those keys already.
SHORTCUT_FILE="$HOME/.config/hypr/bindings.lua"
legacy_shortcut() { grep -q -- "-- lumen-shortcut (added by lumen setup" "$SHORTCUT_FILE" 2>/dev/null; }
add_shortcut() {
  [[ -f $SHORTCUT_FILE ]] || { warn "No ~/.config/hypr/bindings.lua to add it to"; return 0; }
  if "${RECORD[@]}" has-block shortcut || legacy_shortcut; then
    state_set shortcut yes; ok "Shortcut already added: Shift+Alt+Print"; return 0
  fi
  if hyprctl binds -j 2>/dev/null | jq -e 'any(.[]; .modmask == 9 and (.key | ascii_downcase) == "print")' >/dev/null; then
    say "Shift+Alt+Print is already used, so no shortcut was added (click the ✦ instead)"
    return 0
  fi
  "${RECORD[@]}" add-block "$SHORTCUT_FILE" shortcut "$(printf '%s\n%s\n%s' \
    "-- >>> lumen shortcut (added by lumen setup; lumen uninstall removes it) >>>" \
    "o.bind(\"SHIFT + ALT + PRINT\", \"Lumen: start / stop recording\", \"$LUMEN_HOME/bin/lumen\")" \
    "-- <<< lumen shortcut <<<")" || { fail "Couldn't add the shortcut"; return 1; }
  state_set shortcut yes
  ok "Shortcut: Shift+Alt+Print starts and stops a recording"
}

# What Lumen is for: MP4s with your voice. Only fills in choices not made yet,
# so running setup again never undoes what someone picked in the popup.
recording_defaults() {
  [[ -n $(jq -r '.format // empty' "$REC_CONF" 2>/dev/null) ]] || "$GR" set format mp4 >/dev/null
  [[ -n $(jq -r '.mp4Fps // empty' "$REC_CONF" 2>/dev/null) ]] || "$GR" set mp4Fps 30 >/dev/null
  if [[ -z $(jq -r '.audio // empty' "$REC_CONF" 2>/dev/null) ]]; then
    if pactl list short sources 2>/dev/null | grep -vq monitor; then "$GR" set audio mic >/dev/null
    else "$GR" set audio none >/dev/null; fi
  fi
}

# ---------- what's ready

# Fast and offline: the popup runs this every time it opens.
ready_ums() { [[ $(venv_profile) != none ]] && { [[ $(venv_profile) != cloud ]] || [[ -n $(cf_url) ]]; }; }
ready_bubble() { command -v uv >/dev/null && { [[ $(state_get bubble) == yes ]] || [[ $(jq -r '.camera // "off"' "$REC_CONF" 2>/dev/null) == on ]]; }; }
ready_share() { [[ -n $(cf_url) && -n $(cf_token) ]] && [[ $(venv_profile) != none ]]; }
ready_shortcut() { "${RECORD[@]}" has-block shortcut || legacy_shortcut; }
status() {
  local u=false b=false sh=false k=false
  ready_ums && u=true; ready_bubble && b=true; ready_share && sh=true; ready_shortcut && k=true
  printf '{"ums":%s,"bubble":%s,"share":%s,"shortcut":%s,"webcam":%s,"recorder":%s,"nvidia":%s}\n' "$u" "$b" "$sh" "$k" \
    "$(compgen -G '/dev/video*' >/dev/null && echo true || echo false)" \
    "$(command -v wf-recorder >/dev/null && echo true || echo false)" "$(has_nvidia && echo true || echo false)"
}

check() {
  title "Lumen on this computer"
  local y="✓" n="·"
  say "Installed at:    $LUMEN_HOME" \
      "Recording:       $y ready (nothing to install)" \
      "Remove ums:      $(ready_ums && echo "$y speech AI: $(venv_profile)$( [[ $(venv_profile) =~ local|nvidia ]] && { model_ready && echo ', model ready' || echo ', model not downloaded'; })" || echo "$n not installed  (lumen setup ums)")" \
      "Face bubble:     $(ready_bubble && echo "$y ready" || echo "$n not installed  (lumen setup bubble)")" \
      "Share links:     $(cf_answers && echo "$y your Cloudflare ($(cf_url))" || { [[ -n $(cf_url) ]] && echo "! $(cf_url) isn't answering" || echo "$n not connected  (lumen setup share)"; })" \
      "Shortcut:        $(ready_shortcut && echo "$y Shift+Alt+Print" || echo "$n not added  (lumen setup shortcut)")" \
      "Terminal cmds:   $([[ $(readlink -f "$BIN/lumen") == "$LUMEN_HOME/bin/lumen" ]] && echo "$y linked" || echo "$n not linked  (lumen setup commands)")"
}

# The checklist: everything is optional; recording works without any of it.
full() {
  gum style --border rounded --border-foreground 105 --padding "1 3" --margin "1 2" \
    "$(accent '✦ Lumen')" "Recording already works: click the ✦ in the bar." "" "Pick any add-ons you want (space to pick, enter to go)."
  local ums="Remove ums & pauses — a speech AI cuts them when you stop ($(has_nvidia && echo '~4 GB' || echo '~2 GB'))"
  local bub="Face bubble (beta) — your webcam in a bubble that follows you (~0.5 GB)"
  local shr="Share links — a link on your clipboard when you stop; uses your own free Cloudflare"
  local key="Keyboard shortcut — Shift+Alt+Print starts and stops a recording"
  local cmd="Terminal commands — lumen · gif-record · vshare in ~/.local/bin"
  local sel=()
  ready_ums && sel+=("$ums"); ready_bubble && sel+=("$bub"); ready_share && sel+=("$shr")
  ready_shortcut && sel+=("$key"); [[ $(readlink -f "$BIN/lumen") == "$LUMEN_HOME/bin/lumen" ]] && sel+=("$cmd")
  local picks
  picks=$(gum choose --no-limit --height 8 --selected "$(IFS=,; echo "${sel[*]}")" "$ums" "$bub" "$shr" "$key" "$cmd") || exit 0
  [[ $picks == *"Remove ums"* ]] && { ready_ums && ok "Remove ums: already installed" || setup_ums; }
  [[ $picks == *"Face bubble"* ]] && { ready_bubble && ok "Face bubble: already installed" || setup_bubble; }
  [[ $picks == *"Share links"* ]] && { ready_share && cf_answers && ok "Share links: already connected" || setup_share; }
  [[ $picks == *"Keyboard shortcut"* ]] && add_shortcut
  [[ $picks == *"Terminal commands"* ]] && link_commands
  recording_defaults
  state_set done yes
  keep_uninstaller
  echo
  gum style --margin "0 2" --bold "All set. Click the ✦ in the bar to record."
}

uninstall() {
  title "Remove Lumen"
  say "This removes what Lumen's setup added, and only that: the speech AI, the share site's" \
      "local copy, the shortcut, the terminal commands and the bar plugin." \
      "Your recordings in ~/Videos are never touched."
  gum confirm "Continue?" || exit 0
  local gr="$LUMEN_HOME/bin/gif-record" cam="$LUMEN_HOME/bin/lumen-cam"
  [[ -x $gr ]] && "$gr" cancel 2>/dev/null
  [[ -x $cam ]] && "$cam" stop 2>/dev/null

  # Terminal commands: only the links Lumen made. (1.0.0 made them without a record:
  # those still pointing into the plugin go too, and its .before-lumen backups come back.)
  "${RECORD[@]}" unlink | sed 's/^/  /'
  for c in lumen gif-record vshare lumen-cam voice-tune; do
    [[ -n $LUMEN_HOME && -L $BIN/$c && $(readlink "$BIN/$c") == "$LUMEN_HOME/bin/$c" ]] && rm -f "$BIN/$c"
    [[ -e $BIN/$c.before-lumen && ! -e $BIN/$c ]] && mv "$BIN/$c.before-lumen" "$BIN/$c" && say "Restored your own $BIN/$c"
  done
  ok "Terminal commands removed"

  # The shortcut: exactly the block setup added, if it's unchanged.
  "${RECORD[@]}" remove-blocks | sed 's/^/  /'
  legacy_shortcut && "${RECORD[@]}" remove-legacy-shortcut "$SHORTCUT_FILE" | sed 's/^/  /'

  local keep_site=1
  if [[ -n $(state_get worker) ]] && gum confirm --default=false "Also delete your share site from Cloudflare? Existing share links will stop working."; then
    local name
    name=$(state_get worker)
    if [[ $(gum input --header "  Type $name to confirm") == "$name" && -x $WORKER/node_modules/.bin/wrangler ]] &&
      (cd "$WORKER" && node_modules/.bin/wrangler delete --force >/dev/null 2>&1); then
      keep_site=0
      ok "Share site deleted (its storage and database stay in your Cloudflare dashboard)"
    fi
  fi

  # The speech model, only if Lumen downloaded it. Removed through Hugging Face's own cache
  # API (newer versions keep the big file in a shared store outside the model's folder),
  # before the environment that has that API goes.
  if [[ $("${RECORD[@]}" get model_downloaded_by_lumen) == yes && -d $MODEL_CACHE ]] &&
    gum confirm "Delete the speech model too (1.6 GB)?"; then
    if [[ -x $VENV/bin/python ]] && "$VENV/bin/python" - "$MODEL" <<'PY' 2>/dev/null; then
import sys
from huggingface_hub import scan_cache_dir
info = scan_cache_dir()
revs = [rev.commit_hash for r in info.repos if r.repo_id == "mobiuslabsgmbh/faster-whisper-" + sys.argv[1] for rev in r.revisions]
info.delete_revisions(*revs).execute()
PY
      ok "Speech model deleted"
    else
      rm -rf "$MODEL_CACHE" && ok "Speech model deleted"
    fi
  fi

  # Folders setup created, recognised by the mark it left inside them.
  if "${RECORD[@]}" owned-dir "$VENV" || [[ -f $VENV/pyvenv.cfg && $VENV == "$DATA/venv" ]]; then
    rm -rf "$VENV" && ok "Speech AI removed"
  fi
  if ((keep_site)) && [[ -f $WORKER/wrangler.toml ]]; then
    say "Kept $WORKER: it's how your share site is managed (delete it with 'wrangler delete' there)."
  elif "${RECORD[@]}" owned-dir "$WORKER" || [[ -f $WORKER/wrangler.toml.example && $WORKER == "$DATA/worker" ]]; then
    rm -rf "$WORKER"
  fi
  # Recordings a crash left in the working folder are moved to ~/Videos, never deleted.
  local leftovers=() f
  for f in "$HOME"/.cache/gif-record/{rec,mixed}-*.mp4; do [[ -f $f ]] && leftovers+=("$f"); done
  if ((${#leftovers[@]})); then
    mkdir -p "$HOME/Videos/Lumen leftovers" && mv -n "${leftovers[@]}" "$HOME/Videos/Lumen leftovers/" &&
      say "Moved ${#leftovers[@]} unfinished recording(s) to ~/Videos/Lumen leftovers"
  fi
  # Lumen's own working files (named, not whole folders), then the folders if now empty.
  rm -rf "$HOME"/.cache/gif-record/{state.json,log,recorder.err,cancel,thumb.png} "$HOME"/.cache/gif-record/clean-* \
    "$HOME/.cache/vshare/state.json" "$HOME/.cache/voice-tuner/samples" "$HOME/.cache/voice-tuner/chromium"
  rmdir "$HOME/.cache/gif-record" "$HOME/.cache/vshare" "$HOME/.cache/voice-tuner" 2>/dev/null

  if gum confirm --default=false "Also remove your Lumen settings (mic tuning, bubble, the share site's key)?"; then
    rm -f "$HOME"/.config/gif-record/{settings.json,voice.json} "$HOME/.config/vshare/config.json" "$HOME/.config/lumen/setup.json"
    rmdir "$HOME/.config/gif-record" "$HOME/.config/vshare" 2>/dev/null
    ok "Settings removed"
  fi
  if [[ -d $HOME/.local/share/vshare ]] &&
    gum confirm --default=false "Also remove the list of videos you've shared and their transcripts?"; then
    rm -rf "$HOME/.local/share/vshare/transcripts" "$HOME/.local/share/vshare/shares.json"
    rmdir "$HOME/.local/share/vshare" 2>/dev/null
    ok "Share list removed (the videos stay on your Cloudflare)"
  fi

  say "Not touched (shared with other tools): uv's Python and package cache (uv cache clean)," \
      "and wrangler's Cloudflare sign-in (wrangler logout)."
  if [[ -n $LUMEN_HOME && -d $LUMEN_HOME ]]; then
    # The plugin's id comes from its manifest (1.0.0 was plain "lumen").
    local pid
    pid=$(jq -r '.id // empty' "$LUMEN_HOME/manifest.json" 2>/dev/null)
    pid=${pid:-$(basename "$LUMEN_HOME")}
    # Already confirmed above; without --yes, omarchy refuses when it can't ask again.
    # An install updated in place from 1.0.0 keeps its old folder name, which Omarchy then wants instead.
    if omarchy plugin remove "$pid" --yes >/dev/null 2>&1 || omarchy plugin remove "$(basename "$LUMEN_HOME")" --yes >/dev/null 2>&1; then
      ok "Bar plugin removed"
    else
      say "Remove the bar plugin with: omarchy plugin remove $pid"
    fi
  fi
  rm -f "$HOME/.config/lumen/installed.json"
  rmdir "$HOME/.config/lumen" 2>/dev/null
  rm -rf "$DATA/uninstaller"
  rmdir "$DATA" 2>/dev/null
  return 0      # leftover folders above are only tidied if empty; that's not a failure
}

# Single steps end by marking setup as seen, so the popup never shows a first-run screen again.
step() { "$@"; local r=$?; recording_defaults; state_set done yes; keep_uninstaller; return $r; }

case ${1:-full} in
full) full ;;
ums) step setup_ums ;;
bubble) step setup_bubble ;;
share) step setup_share ;;
shortcut) step add_shortcut ;;
commands) step link_commands ;;
recorder) step setup_recorder ;;
status) status ;;
check) check ;;
ai) [[ ${2:-} =~ ^(cloud|local|nvidia)$ ]] || { echo "usage: lumen setup ai cloud|local|nvidia" >&2; exit 1; }; step install_ai "$2" ;;
cloudflare) step connect_cloudflare ;;
site) step update_site ;;
uninstall) uninstall ;;
*) sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 1 ;;
esac
