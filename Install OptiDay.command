#!/bin/bash
# Double-click me to install or update OptiDay.
# If macOS says it "can't be opened", go to System Settings → Privacy & Security, scroll down
# and click "Open Anyway". Or open Terminal and paste:  bash ~/Downloads/OptiDay-v0.4.1/install.sh
cd "$(dirname "$0")" || exit 1
bash ./install.sh
echo
read -r -n 1 -s -p "Press any key to close this window…"
