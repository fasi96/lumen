#!/bin/bash
# lumen setup: get Lumen ready on this computer. Safe to run again: whatever is
# already set up correctly is kept, and it only asks about what's missing or what
# you choose to change.
#
#   lumen setup              the whole guided setup
#   lumen setup check        just report what's set up
#   lumen setup ai PROFILE   install the speech AI: cloud | local | nvidia
#   lumen setup cloudflare   connect (or reconnect) your Cloudflare account
#   lumen uninstall          remove what setup added (asks before anything you'd miss)
#
# Where things go:
#   ~/.local/share/lumen/venv      speech-AI tools      ~/.local/share/lumen/worker   the share site's code
#   ~/.config/lumen/setup.json     what setup chose     ~/.config/gif-record, ~/.config/vshare: settings
set -uo pipefail

LUMEN_HOME="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/lumen"
VENV="${LUMEN_VENV:-$DATA/venv}"
WORKER="$DATA/worker"
STATE="$HOME/.config/lumen/setup.json"
REC_CONF="$HOME/.config/gif-record/settings.json"
SHARE_CONF="$HOME/.config/vshare/config.json"
BIN="$HOME/.local/bin"
MODEL="large-v3-turbo"
# What Lumen needs, as "how to tell it's there = the Arch package that provides it".
# Checked by what works, not by package name, so tools installed another way (mise, pipx…) count.
NEEDS=(
  "command -v gpu-screen-recorder=gpu-screen-recorder" "command -v wf-recorder=wf-recorder" "command -v ffmpeg=ffmpeg" "command -v jq=jq"
  "command -v wl-copy=wl-clipboard" "command -v uv=uv" "command -v node=nodejs" "command -v npm=npm"
  "command -v pactl=libpulse" "command -v slurp=slurp" "command -v v4l2-ctl=v4l-utils"
  "test -e /usr/lib/libgtk4-layer-shell.so=gtk4-layer-shell" "python3 -c 'import gi'=python-gobject"
)

# ---------- small helpers

accent() { gum style --foreground 105 "$@"; }
say() { gum style --margin "0 2" "$@"; }
title() { echo; gum style --bold --foreground 105 --margin "0 2" "✦ $*"; }
ok() { gum style --margin "0 2" --foreground 114 "✓ $*"; }
warn() { gum style --margin "0 2" --foreground 214 "! $*"; }
fail() { gum style --margin "0 2" --foreground 203 "✗ $*"; }
state_get() { jq -r --arg k "$1" '.[$k] // empty' "$STATE" 2>/dev/null; }
state_set() {
  mkdir -p "$(dirname "$STATE")"
  [[ -f $STATE ]] || echo '{}' >"$STATE"
  jq --arg k "$1" --arg v "$2" '.[$k] = $v' "$STATE" >"$STATE.tmp" && mv "$STATE.tmp" "$STATE"
}
has_nvidia() { nvidia-smi -L >/dev/null 2>&1; }
cf_url() { jq -r '.cloudflare.url // empty' "$SHARE_CONF" 2>/dev/null; }
cf_token() { jq -r '.cloudflare.token // empty' "$SHARE_CONF" 2>/dev/null; }
cf_answers() {
  local url token
  url=$(cf_url) token=$(cf_token)
  [[ -n $url && -n $token ]] || return 1
  curl -fsS -m 10 -A lumen-setup -H "Authorization: Bearer $token" "$url/api/videos" >/dev/null 2>&1
}
venv_profile() {
  local v=$VENV
  # An older install kept its environment inside the plugin folder; the tools still use it.
  [[ ! -x $v/bin/python && -x $LUMEN_HOME/share/clean/.venv/bin/python ]] && v=$LUMEN_HOME/share/clean/.venv
  [[ -x $v/bin/python ]] || { echo none; return; }
  local VENV=$v
  if [[ -d $(echo "$VENV"/lib/python3*/site-packages/nvidia) ]]; then echo nvidia
  elif "$VENV/bin/python" -c "import faster_whisper" 2>/dev/null; then echo local
  else echo cloud; fi
}
model_ready() {
  "$LUMEN_HOME/share/clean/run" - "$MODEL" <<'PY' 2>/dev/null
import sys
from faster_whisper.utils import download_model
download_model(sys.argv[1], local_files_only=True)
PY
}

# ---------- 1. this computer

check_machine() {
  title "Checking this computer"
  if has_nvidia; then ok "NVIDIA graphics: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
  else say "No NVIDIA card: speech AI will use your Cloudflare or the processor"; fi
  local cams mics
  cams=$(ls /dev/video* 2>/dev/null | wc -l)
  mics=$(pactl list short sources 2>/dev/null | grep -vc monitor)
  ((cams)) && ok "Webcam found" || say "No webcam right now (the face bubble can be turned on later)"
  ((mics)) && ok "$mics microphone(s)" || warn "No microphone found"

  local missing=()
  for n in "${NEEDS[@]}"; do eval "${n%=*}" >/dev/null 2>&1 || missing+=("${n##*=}"); done
  if ((${#missing[@]})); then
    say "Lumen needs: ${missing[*]}"
    gum confirm "Install them now? (asks for your password once)" || { fail "Can't continue without them"; exit 1; }
    sudo pacman -S --needed --noconfirm "${missing[@]}" || { fail "Installing packages failed"; exit 1; }
  fi
  ok "Everything Lumen needs is installed"

  # Commands on your PATH, pointing into this install.
  mkdir -p "$BIN"
  for c in lumen gif-record vshare lumen-cam voice-tune; do
    [[ $(readlink -f "$BIN/$c" 2>/dev/null) == "$LUMEN_HOME/bin/$c" ]] && continue
    [[ -e $BIN/$c && ! -L $BIN/$c ]] && mv "$BIN/$c" "$BIN/$c.before-lumen"
    ln -sfn "$LUMEN_HOME/bin/$c" "$BIN/$c"
  done
  ok "Commands ready: lumen, gif-record, vshare, lumen-cam, voice-tune"
}

# ---------- 2. where videos go

ask_storage() {
  title "Where should your videos go?"
  if cf_answers; then
    ok "Connected to your Cloudflare: $(cf_url)"
    state_set storage cloudflare
    gum confirm --default=false "Connect a different Cloudflare account instead?" || return 0
  fi
  local pick
  pick=$(gum choose --header "  Share links need somewhere to host the video." \
    "My Cloudflare — share links; free up to 10 GB, setup creates it all in your account" \
    "Just this computer — no account, no share links") || exit 1
  if [[ $pick == My* ]]; then
    connect_cloudflare && state_set storage cloudflare
  else
    state_set storage local
    gif-record set share off >/dev/null
    ok "Videos stay on this computer (in ~/Videos)"
  fi
}

connect_cloudflare() {
  title "Connecting your Cloudflare"
  say "Two things Cloudflare asks for once, on a new account:" \
      "  • R2 storage has to be switched on, which needs a payment method on file." \
      "    It stays free up to 10 GB, and watching videos is always free." \
      "  • A free workers.dev subdomain for your share links (you pick the name)."
  gum confirm "Open Cloudflare's R2 page to check it's switched on?" && xdg-open "https://dash.cloudflare.com/?to=/:account/r2/overview" >/dev/null 2>&1
  gum confirm "Ready to continue?" || return 1

  # The share site's code gets built outside the plugin folder.
  mkdir -p "$WORKER"
  rsync -a --delete --exclude node_modules --exclude .wrangler --exclude wrangler.toml --exclude .dev.vars \
    "$LUMEN_HOME/share/" "$WORKER/" || return 1
  (cd "$WORKER" && npm install --silent --no-audit --no-fund) >/dev/null 2>&1 || { fail "Couldn't install the share site's tools (npm)"; return 1; }
  local wr=(npx --prefix "$WORKER" wrangler)

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
    mkdir -p "$HOME/.config/lumen" && (umask 077 && printf '%s\n' "$CLOUDFLARE_API_TOKEN" >"$HOME/.config/lumen/cloudflare-token")
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
  gum spin --title "Creating storage ($bucket)…" -- "${wr[@]}" r2 bucket create "$bucket" >/tmp/lumen-setup.log 2>&1 \
    || { fail "Creating R2 storage failed — is R2 switched on? (see /tmp/lumen-setup.log)"; return 1; }
  gum spin --title "Creating the database ($db)…" -- "${wr[@]}" d1 create "$db" >/tmp/lumen-setup.log 2>&1 \
    || { fail "Creating the database failed (see /tmp/lumen-setup.log)"; return 1; }
  dbid=$(grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' /tmp/lumen-setup.log | head -1)
  sed -e "s/^name = .*/name = \"$name\"/" -e "s/^bucket_name = .*/bucket_name = \"$bucket\"/" \
      -e "s/^database_name = .*/database_name = \"$db\"/" -e "s/^database_id = .*/database_id = \"$dbid\"/" \
      -e "s/^OWNER_NAME = .*/OWNER_NAME = \"$owner\"/" wrangler.toml.example >wrangler.toml
  gum spin --title "Setting up the database…" -- "${wr[@]}" d1 execute "$db" --remote --file schema.sql >>/tmp/lumen-setup.log 2>&1 \
    || { fail "Setting up the database failed (see /tmp/lumen-setup.log)"; return 1; }
  gum spin --title "Publishing your share site…" -- "${wr[@]}" deploy >/tmp/lumen-setup.log 2>&1 \
    || { fail "Publishing failed — a new account may need a workers.dev subdomain first (see /tmp/lumen-setup.log)"; return 1; }
  local url
  url=$(grep -oE 'https://[a-z0-9.-]+\.workers\.dev' /tmp/lumen-setup.log | head -1)
  admin=$(openssl rand -hex 24)
  printf '%s' "$admin" | "${wr[@]}" secret put ADMIN_TOKEN >/dev/null 2>&1 || { fail "Couldn't store the share site's key"; return 1; }
  # Video passwords are hashed with a salt of this install's own (never change it later).
  openssl rand -hex 16 | tr -d '\n' | "${wr[@]}" secret put PASSWORD_SALT >/dev/null 2>&1 || { fail "Couldn't store the share site's password salt"; return 1; }
  vshare setup cloudflare "$url" "$admin" >/dev/null || { fail "Couldn't save the share settings"; return 1; }
  state_set worker "$name"
  sleep 3
  cf_answers && ok "Your share site is live: $url" || warn "Published $url, but it isn't answering yet — try 'lumen setup check' in a minute"
}

# ---------- 3. where the AI runs

ask_ai() {
  title "Where should the speech AI run?"
  say "It listens to your recording to find the ums and long pauses to cut."
  local opts=()
  if has_nvidia; then opts+=("Automatic — your NVIDIA card (fast, private)")
  else opts+=("Automatic — your Cloudflare's AI, this computer if that's unreachable"); fi
  opts+=("This computer only — your audio never leaves it$(has_nvidia || echo '; slower without NVIDIA')")
  [[ $(state_get storage) == cloudflare ]] && opts+=("Cloudflare only — smallest install; if it's unreachable, recordings stay uncut")
  opts+=("Off — keep every word")
  local pick profile
  pick=$(gum choose "${opts[@]}") || exit 1
  case $pick in
  Automatic*) gif-record set ai auto >/dev/null; gif-record set clean on >/dev/null; profile=local ;;
  This*) gif-record set ai local >/dev/null; gif-record set clean on >/dev/null; profile=local ;;
  Cloudflare*) gif-record set ai cloud >/dev/null; gif-record set clean on >/dev/null; profile=cloud ;;
  Off*) gif-record set clean off >/dev/null; profile=cloud ;;
  esac
  [[ $profile == local ]] && has_nvidia && profile=nvidia
  install_ai "$profile"
}

install_ai() {
  local want=$1 have=none
  # Only the environment in its proper place counts; an older in-place one gets replaced.
  if [[ -x $VENV/bin/python ]]; then
    have=$(LUMEN_HOME=/nonexistent venv_profile)
  fi
  title "Installing the speech AI ($want)"
  if [[ $have == "$want" ]]; then
    ok "Already installed"
  else
    case $want in
    cloud) say "About 80 MB." ;;
    local) say "About 400 MB, plus a 1.6 GB speech model (once)." ;;
    nvidia) say "About 2.6 GB (NVIDIA's libraries), plus a 1.6 GB speech model (once)." ;;
    esac
    [[ -x $VENV/bin/python ]] || uv venv -q --python 3.12 "$VENV" || { fail "Couldn't create the Python environment"; return 1; }
    gum spin --title "Installing…" -- uv pip install -q --python "$VENV/bin/python" -r "$LUMEN_HOME/setup/requirements-$want.txt" \
      || { fail "Installing the speech AI failed"; return 1; }
    ok "Installed"
  fi
  state_set ai_profile "$want"
  if [[ $want != cloud ]] && ! model_ready; then
    say "Downloading the speech model ($MODEL, 1.6 GB):"
    # Quiet the Hugging Face "unauthenticated requests" warning; a token isn't needed for this public model.
    HF_HUB_VERBOSITY=error PYTHONWARNINGS=ignore \
      "$VENV/bin/python" -c "from faster_whisper.utils import download_model; download_model('$MODEL')" \
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
  mkdir -p "$(dirname "$SHARE_CONF")"
  [[ -f $SHARE_CONF ]] || echo '{}' >"$SHARE_CONF"
  jq --arg n "$name" '.owner = $n' "$SHARE_CONF" >"$SHARE_CONF.tmp" && mv "$SHARE_CONF.tmp" "$SHARE_CONF" && chmod 600 "$SHARE_CONF"
  ok "${name:-(no name, the ✦ mark)}"
}

ask_camera() {
  title "Face bubble (beta)"
  say "Your webcam in a bubble on screen, recorded with everything else."
  if gum confirm "Turn the face bubble on?"; then
    gum spin --title "Getting the camera AI ready (about 0.5 GB, once)…" -- \
      uv sync -q --script "$LUMEN_HOME/cam/lumen_cam.py" || { fail "Couldn't install the camera AI"; return 1; }
    gif-record set camera on >/dev/null
    ok "On — hover it to change size, shape and outline"
  else
    gif-record set camera off >/dev/null
    ok "Off (turn it on any time in the popup)"
  fi
}

# The keyboard shortcut: Shift+Alt+Print starts and stops a recording. Added to the
# user's own Hyprland bindings (marked, so uninstall can take it out again), and
# only if nothing is bound to those keys already.
SHORTCUT_FILE="$HOME/.config/hypr/bindings.lua"
add_shortcut() {
  [[ -f $SHORTCUT_FILE ]] || return 0
  grep -q "lumen-shortcut" "$SHORTCUT_FILE" && return 0
  if grep -qiE '^[^-]*"SHIFT \+ ALT \+ PRINT"' "$SHORTCUT_FILE"; then
    say "Shift+Alt+Print is already used, so no shortcut was added (click the ✦ instead)"
    return 0
  fi
  printf '\n-- lumen-shortcut (added by lumen setup; lumen uninstall removes it)\no.bind("SHIFT + ALT + PRINT", "Lumen: start / stop recording", "%s")\n' "$BIN/lumen" >>"$SHORTCUT_FILE"
  ok "Shortcut: Shift+Alt+Print starts and stops a recording"
}

# What Lumen is for: MP4s with your voice. Only fills in choices not made yet,
# so running setup again never undoes what someone picked in the popup.
recording_defaults() {
  [[ -n $(jq -r '.format // empty' "$REC_CONF" 2>/dev/null) ]] || gif-record set format mp4 >/dev/null
  [[ -n $(jq -r '.mp4Fps // empty' "$REC_CONF" 2>/dev/null) ]] || gif-record set mp4Fps 30 >/dev/null
  if [[ -z $(jq -r '.audio // empty' "$REC_CONF" 2>/dev/null) ]]; then
    if pactl list short sources 2>/dev/null | grep -vq monitor; then gif-record set audio mic >/dev/null
    else gif-record set audio none >/dev/null; fi
  fi
}

# ---------- 5. tests

run_checks() {
  title "Checking it all works"
  local lvl
  if pactl list short sources 2>/dev/null | grep -vq monitor; then
    say "Say something for 3 seconds…"
    lvl=$(timeout 4 ffmpeg -v error -f pulse -i default -t 3 -af volumedetect -f null - 2>&1 | grep -oE 'max_volume: -?[0-9.]+' | grep -oE -- '-?[0-9.]+$')
    if [[ -n $lvl ]] && awk "BEGIN{exit !($lvl > -40)}"; then ok "Mic hears you (peak $lvl dB)"
    else warn "The mic sounded very quiet${lvl:+ ($lvl dB)} — check the input in your sound settings"; fi
  fi
  if [[ $(jq -r '.camera // "off"' "$REC_CONF" 2>/dev/null) == on ]]; then
    if timeout 6 ffmpeg -v error -f v4l2 -i /dev/video0 -frames:v 1 -f null - 2>/dev/null; then ok "Camera works"
    else warn "Couldn't read the camera (in use by another app, or unplugged?)"; fi
  fi
  if [[ $(state_get storage) == cloudflare ]]; then
    cf_answers && ok "Share site answers: $(cf_url)" || warn "Share site isn't answering: run 'lumen setup cloudflare'"
  fi
  if [[ $(jq -r '.clean // "off"' "$REC_CONF" 2>/dev/null) == on ]]; then
    "$LUMEN_HOME/share/clean/run" -c "import numpy" 2>/dev/null && ok "Speech AI ready ($(venv_profile))" || warn "Speech AI isn't installed: run 'lumen setup'"
  fi
}

# ---------- commands

check() {
  title "Lumen on this computer"
  say "Installed at:  $LUMEN_HOME" \
      "Commands:      $([[ $(readlink -f "$BIN/lumen") == "$LUMEN_HOME/bin/lumen" ]] && echo linked || echo 'not linked (run lumen setup)')" \
      "Speech AI:     $(venv_profile)$( [[ $(venv_profile) =~ local|nvidia ]] && { model_ready && echo ', model ready' || echo ', model not downloaded'; })" \
      "AI runs on:    $(jq -r '.ai // "auto"' "$REC_CONF" 2>/dev/null)   · remove ums: $(jq -r '.clean // "off"' "$REC_CONF" 2>/dev/null)" \
      "Videos go to:  $(cf_answers && echo "your Cloudflare ($(cf_url))" || echo 'this computer')" \
      "Face bubble:   $(jq -r '.camera // "off"' "$REC_CONF" 2>/dev/null)" \
      "Setup done:    $(state_get done || echo no)"
}

full() {
  gum style --border rounded --border-foreground 105 --padding "1 3" --margin "1 2" \
    "$(accent '✦ Lumen')" "Record your screen, your face in a bubble," "ums removed, a share link the moment you stop." "" "Setup takes about 3 minutes."
  check_machine
  ask_storage
  ask_ai
  ask_name
  ask_camera
  add_shortcut
  recording_defaults
  run_checks
  state_set done yes
  echo
  gum style --margin "0 2" --bold "All set. Click the ✦ in the bar to record."
}

uninstall() {
  title "Remove Lumen"
  say "This removes the speech AI, the share site's local copy, the commands and the bar plugin." \
      "Your recordings in ~/Videos are never touched."
  gum confirm "Continue?" || exit 0
  lumen-cam stop 2>/dev/null
  for c in lumen gif-record vshare lumen-cam voice-tune; do
    [[ $(readlink -f "$BIN/$c" 2>/dev/null) == "$LUMEN_HOME/bin/$c" ]] && rm -f "$BIN/$c"
    [[ -e $BIN/$c.before-lumen ]] && mv "$BIN/$c.before-lumen" "$BIN/$c"
  done
  ok "Commands removed"
  if [[ -f $SHORTCUT_FILE ]] && grep -q "lumen-shortcut" "$SHORTCUT_FILE"; then
    # Drop the marker, the bind line after it, and the blank line setup put before it.
    awk '{ l[NR] = $0 } END { for (i = 1; i <= NR; i++) {
      if (l[i] == "" && l[i + 1] ~ /-- lumen-shortcut/) continue
      if (l[i] ~ /-- lumen-shortcut/) { i++; continue }
      print l[i] } }' "$SHORTCUT_FILE" >"$SHORTCUT_FILE.tmp" && mv "$SHORTCUT_FILE.tmp" "$SHORTCUT_FILE" && ok "Keyboard shortcut removed"
  fi
  if [[ -n $(state_get worker) ]] && gum confirm --default=false "Also delete your share site from Cloudflare? Existing share links will stop working."; then
    local name
    name=$(state_get worker)
    [[ $(gum input --header "  Type $name to confirm") == "$name" ]] &&
      (cd "$WORKER" && npx --prefix "$WORKER" wrangler delete --force >/dev/null 2>&1) && ok "Share site deleted (its storage and database stay in your Cloudflare dashboard)"
  fi
  rm -rf "$DATA"
  # The speech model downloads into the shared Hugging Face cache; remove only Lumen's model there.
  rm -rf "$HOME/.cache/huggingface/hub/"models--*--faster-whisper-"$MODEL"
  ok "Speech AI, its model and local files removed"
  if gum confirm --default=false "Also remove your Lumen settings (mic tuning, bubble, share keys)?"; then
    rm -rf "$HOME/.config/lumen" "$HOME/.config/gif-record" "$HOME/.config/vshare" "$HOME/.cache/gif-record"
    ok "Settings removed"
  fi
  # Already confirmed above; without --yes, omarchy refuses when it can't ask again.
  omarchy plugin remove lumen --yes >/dev/null 2>&1 && ok "Bar plugin removed" || say "Remove the bar plugin with: omarchy plugin remove lumen"
}

case ${1:-full} in
full) full ;;
check) check ;;
ai) [[ ${2:-} =~ ^(cloud|local|nvidia)$ ]] || { echo "usage: lumen setup ai cloud|local|nvidia" >&2; exit 1; }; install_ai "$2" ;;
cloudflare) connect_cloudflare ;;
uninstall) uninstall ;;
*) sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 1 ;;
esac
