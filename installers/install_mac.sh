#!/usr/bin/env bash
set -euo pipefail

function print_ok() { echo -e "\033[32m[OK] $1\033[0m"; }
function print_info() { echo -e "\033[36m[INFO] $1\033[0m"; }
function print_error() { echo -e "\033[31m[ERROR] $1\033[0m"; }

print_info "Checking for Homebrew..."
if ! command -v brew &> /dev/null; then
    print_info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null || true)"
else
    print_ok "Homebrew is already installed."
fi

print_info "Checking for Node.js..."
if ! command -v node &> /dev/null || [ "$(node -v | cut -d. -f1 | tr -d 'v')" -lt 22 ]; then
    print_info "Installing Node.js..."
    brew install node
else
    print_ok "Node.js (>= 22) is already installed: $(node --version)"
fi
node --version

print_info "Checking for Python 3..."
if ! command -v python3 &> /dev/null; then
    print_info "Installing Python 3..."
    brew install python3
else
    print_ok "Python 3 is already installed."
fi

print_info "Installing OpenClaw globally..."
npm install -g openclaw
print_ok "OpenClaw installed."

print_info "Installing Flask and requests..."
pip3 install flask requests
print_ok "Flask and requests installed."

print_info "Setting up /opt/openclaw-setup..."
sudo mkdir -p /opt/openclaw-setup
sudo cp -r "$(dirname "$0")"/../* /opt/openclaw-setup/
print_ok "Setup files copied."

print_info "Setting up Node.js compile cache..."
mkdir -p /var/tmp/openclaw-compile-cache
print_ok "Compile cache directory created."

print_info "Installing launchd plists..."
cp /opt/openclaw-setup/services/com.openclaw.setup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.openclaw.setup.plist || true
print_ok "launchd services loaded."

print_info "Setup is starting in your browser!"
sleep 3 && open http://localhost:7070
