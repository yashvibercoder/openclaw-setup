#!/usr/bin/env bash
# Double-click this file in macOS Finder to start OpenClaw Setup.
# chmod +x openclaw-setup.command

cd "$(dirname "$0")"

if ! command -v python3 &>/dev/null; then
    echo "Python 3 is required. Opening download page..."
    open "https://www.python.org/downloads/"
    read -rp "Press Enter after installing Python, then run this file again."
    exit 1
fi

python3 launch.py || { echo; read -rp "Setup failed. Press Enter to close."; exit 1; }
