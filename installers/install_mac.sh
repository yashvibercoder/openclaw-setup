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
if npm install -g openclaw; then
    print_ok "OpenClaw installed."
else
    print_error "npm install -g openclaw failed. Check your internet connection and try again."
    exit 1
fi

# Verify openclaw is reachable (it may not expose --version, so just confirm the binary exists)
if ! command -v openclaw &>/dev/null; then
    print_error "openclaw binary not found on PATH after install. Try opening a new terminal."
    exit 1
fi
print_ok "openclaw is on PATH: $(command -v openclaw)"

print_info "Installing Flask and requests..."
# macOS Homebrew Python and system Python 3.12+ are externally managed (PEP 668).
if pip3 install flask requests 2>/dev/null; then
    print_ok "Flask and requests installed (standard pip)."
elif pip3 install --break-system-packages flask requests 2>/dev/null; then
    print_ok "Flask and requests installed (--break-system-packages)."
elif pip3 install --user flask requests 2>/dev/null; then
    print_ok "Flask and requests installed (--user)."
else
    print_error "Could not install Flask and requests. Try: pip3 install --break-system-packages flask requests"
    exit 1
fi

print_info "Setting up /opt/openclaw-setup..."
# Resolve the repo root relative to this script's real location, not the
# caller's working directory, so the copy works regardless of how it is invoked.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
sudo mkdir -p /opt/openclaw-setup
sudo rsync -a --delete "${REPO_ROOT}/" /opt/openclaw-setup/ 2>/dev/null || \
    sudo cp -r "${REPO_ROOT}/." /opt/openclaw-setup/
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
