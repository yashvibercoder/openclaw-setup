#!/usr/bin/env bash
# Run this to start OpenClaw Setup on Linux.

echo "Starting OpenClaw Setup..."
cd "$(dirname "$0")"

if ! command -v python3 &>/dev/null; then
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y python3 python3-pip
    else
        echo "Please install Python 3 and run this script again."
        exit 1
    fi
fi

python3 launch.py
