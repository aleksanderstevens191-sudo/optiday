#!/usr/bin/env bash
# OptiDay installer for macOS. Safe to run again to update.
#   bash ~/Downloads/OptiDay-v0.4.1/install.sh            install / update
#   bash ~/Downloads/OptiDay-v0.4.1/install.sh --no-ai    skip installing Ollama + the local model
#   bash ~/Downloads/OptiDay-v0.4.1/install.sh --force    install this folder even if it's older
set -euo pipefail

# Works no matter which folder you run it from.
cd "$(dirname "$0")"
SRC="$(pwd)"
# Files downloaded from the internet are "quarantined" by macOS; clear that for this folder
# so nothing inside gets blocked later.
xattr -dr com.apple.quarantine "$SRC" 2>/dev/null || true
APP_SUPPORT="$HOME/Library/Application Support/OptiDay"
VENV="$APP_SUPPORT/venv"
WITH_AI=1
FORCE=0
for arg in ${1+"$@"}; do
  case "$arg" in
    --no-ai) WITH_AI=0 ;;
    --force) FORCE=1 ;;
  esac
done

bold() { printf "\n\033[1m%s\033[0m\n" "$*"; }
info() { printf "   %s\n" "$*"; }

[[ "$(uname)" == "Darwin" ]] || { echo "OptiDay records on macOS only."; exit 1; }

# ------------------------------------------------------------------ Right folder?
# Running an old download (say OptiDay-v0.2) would quietly replace a newer OptiDay.
# Stop and print the exact line to paste instead.
version_of() {  # version = "0.4.0" in a folder's pyproject.toml
  sed -n 's/^version = "\(.*\)"/\1/p' "$1/pyproject.toml" 2>/dev/null | head -1
}
newer() {  # true when version $1 is newer than $2
  local IFS=.; local -a a=($1) b=($2); local i
  for i in 0 1 2; do
    (( 10#${a[i]:-0} > 10#${b[i]:-0} )) && return 0
    (( 10#${a[i]:-0} < 10#${b[i]:-0} )) && return 1
  done
  return 1
}
THIS_VERSION="$(version_of "$SRC")"
INSTALLED_VERSION=""
[[ -x "$VENV/bin/python" ]] && \
  INSTALLED_VERSION="$("$VENV/bin/python" -c 'import optiday; print(optiday.__version__)' 2>/dev/null || true)"
NEWEST_DIR=""; NEWEST_VERSION="$THIS_VERSION"
for d in "$HOME"/Downloads/OptiDay-v*/; do
  [[ -f "$d/install.sh" ]] || continue
  v="$(version_of "$d")"
  [[ -n "$v" ]] && newer "$v" "$NEWEST_VERSION" && { NEWEST_DIR="${d%/}"; NEWEST_VERSION="$v"; }
done
printf "\n\033[1mOptiDay %s installer\033[0m  (folder: %s)\n" "${THIS_VERSION:-?}" "$SRC"
[[ -n "$INSTALLED_VERSION" ]] && info "Currently installed: OptiDay $INSTALLED_VERSION"
if [[ $FORCE -eq 0 && -n "$THIS_VERSION" ]]; then
  if [[ -n "$NEWEST_DIR" ]]; then
    printf "\n   \033[1;33mThis is an older OptiDay folder (%s).\033[0m A newer one is in Downloads (%s).\n" \
      "$THIS_VERSION" "$NEWEST_VERSION"
    info "Nothing was changed. Paste this line instead:"
    printf "\n      bash \"%s/install.sh\"\n\n" "$NEWEST_DIR"
    exit 1
  fi
  if [[ -n "$INSTALLED_VERSION" ]] && newer "$INSTALLED_VERSION" "$THIS_VERSION"; then
    printf "\n   \033[1;33mYou already have OptiDay %s, which is newer than this folder (%s).\033[0m\n" \
      "$INSTALLED_VERSION" "$THIS_VERSION"
    info "Nothing was changed. Run the installer from the newest OptiDay folder you downloaded,"
    info "or add --force to this line if you really want to go back to $THIS_VERSION."
    exit 1
  fi
fi

# ------------------------------------------------------------------ Python
bold "1/5  Finding Python 3.11+"
find_python() {
  local c
  for c in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
           /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
           /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
           "$(command -v python3 2>/dev/null || true)"; do
    [[ -n "$c" && -x "$c" ]] || continue
    # Apple's /usr/bin/python3 is too old and can pop up a "install developer tools" dialog
    [[ "$c" == /usr/bin/python3 ]] && continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}
PY="$(find_python || true)"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [[ -x "$b" ]] && ! command -v brew >/dev/null 2>&1 && eval "$("$b" shellenv)"
done
if [[ -z "$PY" ]]; then
  if command -v brew >/dev/null 2>&1; then
    info "Installing Python 3.12 with Homebrew…"
    brew install python@3.12
    PY="$(brew --prefix)/bin/python3.12"
  else
    info "Installing Python 3.12 with uv (no admin password needed)…"
    command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    uv python install 3.12
    PY="$(uv python find 3.12)"
  fi
fi
info "Using $PY"

# ------------------------------------------------------------------ App
bold "2/5  Installing OptiDay"
mkdir -p "$APP_SUPPORT"
chmod 700 "$APP_SUPPORT"
[[ -x "$VENV/bin/python" ]] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade "$SRC"
"$VENV/bin/python" -m pip install --quiet --force-reinstall --no-deps "$SRC"
mkdir -p "$HOME/.local/bin"
ln -sf "$VENV/bin/optiday" "$HOME/.local/bin/optiday"
FIRST_RUN=0
[[ -f "$APP_SUPPORT/config.toml" ]] || FIRST_RUN=1
"$VENV/bin/python" -c 'from optiday.config import load_config; load_config()'
info "Installed. Command line: ~/.local/bin/optiday"

# ------------------------------------------------------------------ Local AI
if [[ $WITH_AI -eq 1 ]]; then
  bold "3/5  Setting up the private AI model (Ollama)"
  RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
  if   (( RAM_GB >= 32 )); then MODEL="qwen2.5:14b"
  elif (( RAM_GB >= 16 )); then MODEL="qwen2.5:7b"
  else                          MODEL="qwen2.5:3b"
  fi
  info "This Mac has ${RAM_GB} GB of memory → using $MODEL"

  OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"
  [[ -z "$OLLAMA_BIN" && -x /Applications/Ollama.app/Contents/Resources/ollama ]] && \
    OLLAMA_BIN=/Applications/Ollama.app/Contents/Resources/ollama
  if [[ -z "$OLLAMA_BIN" ]]; then
    info "Downloading Ollama…"
    TMP="$(mktemp -d)"
    if curl -fsSL -o "$TMP/Ollama.zip" https://ollama.com/download/Ollama-darwin.zip; then
      ditto -x -k "$TMP/Ollama.zip" /Applications
    elif curl -fsSL -o "$TMP/Ollama.dmg" https://ollama.com/download/Ollama.dmg; then
      MNT="$(hdiutil attach -nobrowse -quiet "$TMP/Ollama.dmg" | tail -1 | awk -F'\t' '{print $NF}')"
      cp -R "$MNT/Ollama.app" /Applications/
      hdiutil detach -quiet "$MNT" || true
    fi
    rm -rf "$TMP"
    [[ -x /Applications/Ollama.app/Contents/Resources/ollama ]] && \
      OLLAMA_BIN=/Applications/Ollama.app/Contents/Resources/ollama
  fi
  if [[ -n "$OLLAMA_BIN" ]]; then
    curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || open -a Ollama 2>/dev/null || \
      ("$OLLAMA_BIN" serve >/dev/null 2>&1 &)
    for _ in $(seq 1 30); do curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
    info "Downloading $MODEL (a few GB, one time)…"
    if "$OLLAMA_BIN" pull "$MODEL"; then
      if [[ $FIRST_RUN -eq 1 ]]; then
        "$VENV/bin/python" -c "from optiday.config import set_config_value; set_config_value('ai','model','$MODEL')"
      fi
    else
      info "Couldn't download the model now. OptiDay still works with built-in rules; run 'ollama pull $MODEL' later."
    fi
  else
    info "Couldn't install Ollama automatically. Get it from https://ollama.com/download, then run: ollama pull $MODEL"
  fi
else
  bold "3/5  Skipping AI setup (--no-ai). Reports will use built-in rules."
  "$VENV/bin/python" -c "from optiday.config import set_config_value; set_config_value('ai','provider','none')"
fi

# ------------------------------------------------------------------ Login item
bold "4/5  Starting OptiDay (and at every login)"
"$VENV/bin/python" -m optiday install-agent
sleep 3

# ------------------------------------------------------------------ Permissions
bold "5/5  One thing only you can do: allow screen access"
cat <<'MSG'
   macOS needs your OK before any app can see window titles and on-screen text.

   1. System Settings is opening at Privacy & Security → Screen & System Audio Recording.
   2. Turn ON the entry named "Python" or "python3…" (OptiDay's engine). If it isn't listed,
      click +, press Cmd+Shift+G, paste the path printed below, and add it.
   3. Then click the ◉ in your menu bar → "Restart OptiDay".
   4. The first time OptiDay reads a browser tab, macOS asks "python wants to control
      Chrome/Safari/Arc". Click OK so OptiDay can see which site and video you're on.

MSG
info "Python path to allow: $(readlink -f "$VENV/bin/python" 2>/dev/null || "$VENV/bin/python" -c 'import os,sys;print(os.path.realpath(sys.executable))')"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" || true

# ------------------------------------------------------------------ Old download folders
# Installers in OptiDay-v0.3 and earlier don't have the version check above, so running one of
# them again would put the old version back. Offer to delete them (your data isn't in them).
OLD_DIRS=(); OLD_COUNT=0
for d in "$HOME"/Downloads/OptiDay-v*/; do
  d="${d%/}"
  [[ "$d" != "$SRC" && -f "$d/install.sh" ]] || continue
  grep -q '^name = "optiday"' "$d/pyproject.toml" 2>/dev/null || continue
  v="$(version_of "$d")"
  [[ -n "$v" && -n "$THIS_VERSION" ]] && newer "$THIS_VERSION" "$v" && { OLD_DIRS[OLD_COUNT]="$d"; OLD_COUNT=$((OLD_COUNT + 1)); }
done
if [[ $OLD_COUNT -gt 0 && -t 0 ]]; then
  bold "Old OptiDay folders in Downloads"
  for d in "${OLD_DIRS[@]}"; do info "$(basename "$d")"; done
  info "Running one of these by mistake would put an older OptiDay back. Your history and"
  info "settings are not stored in them."
  printf "   Delete these old folders now? [y/N] "
  read -r answer || answer=""
  if [[ "$answer" =~ ^[Yy] ]]; then
    for d in "${OLD_DIRS[@]}"; do rm -rf "$d" && info "Deleted $(basename "$d")"; done
  else
    info "Kept them. Always use: bash \"$SRC/install.sh\""
  fi
fi

bold "Done. OptiDay ${THIS_VERSION} is running (look for ◉ in your menu bar)."
info "Battery: full detail when plugged in, battery saver when unplugged. The menu shows"
info "OptiDay's own CPU use so you can check it stays tiny."
info "• Live tracker for a side monitor: menu bar ◉ → Live view  (or: ~/.local/bin/optiday live)"
info "• Your end-of-day report opens automatically at 6:00pm (change it in Settings)."
info "• Menu bar ◉ → Open today's report, any time."
info "• Want to see a sample report right now?  ~/.local/bin/optiday demo --open"
info "• Check everything is OK:                  ~/.local/bin/optiday doctor"
