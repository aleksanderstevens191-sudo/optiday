#!/usr/bin/env bash
# Removes OptiDay. Asks before deleting your recorded data.
set -euo pipefail
APP_SUPPORT="$HOME/Library/Application Support/OptiDay"
VENV="$APP_SUPPORT/venv"

if [[ -x "$VENV/bin/python" ]]; then
  "$VENV/bin/python" -m optiday uninstall-agent || true
fi
rm -f "$HOME/.local/bin/optiday"
rm -rf "$VENV"
echo "OptiDay is uninstalled and no longer starts at login."

read -r -p "Also delete ALL recorded history and reports? This can't be undone. [y/N] " ans
if [[ "${ans:-N}" =~ ^[Yy]$ ]]; then
  rm -rf "$APP_SUPPORT"
  security delete-generic-password -s OptiDay -a database-key >/dev/null 2>&1 || true
  echo "All OptiDay data deleted."
else
  echo "Your data is still in: $APP_SUPPORT"
fi
