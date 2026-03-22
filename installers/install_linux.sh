#!/usr/bin/env bash
set -euo pipefail

function print_ok() { echo -e "\e[32m[OK] $1\e[0m"; }
function print_info() { echo -e "\e[36m[INFO] $1\e[0m"; }
function print_error() { echo -e "\e[31m[ERROR] $1\e[0m"; }

print_info "Updating apt package lists..."
sudo apt update

print_info "Checking Node.js..."
if ! command -v node &> /dev/null || [ "$(node -v | cut -d. -f1 | tr -d 'v')" -lt 22 ]; then
    print_info "Installing Node.js 22.x via NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt install -y nodejs
else
    print_ok "Node.js (>= 22) is already installed: $(node --version)"
fi
node --version

print_info "Checking Python 3 and pip..."
if ! command -v python3 &> /dev/null || ! command -v pip3 &> /dev/null; then
    print_info "Installing Python 3 and pip..."
    sudo apt install -y python3 python3-pip
else
    print_ok "Python 3 and pip are already installed."
fi

print_info "Installing OpenClaw globally..."
if sudo npm install -g openclaw; then
    print_ok "OpenClaw installed."
else
    print_error "npm install -g openclaw failed. Check your internet connection and try again."
    exit 1
fi

# Verify openclaw is reachable after install
if ! command -v openclaw &>/dev/null; then
    print_error "openclaw not found on PATH after install. Try: hash -r && openclaw --version"
    exit 1
fi
print_ok "openclaw is on PATH: $(command -v openclaw)"

print_info "Installing Flask and requests..."
# Ubuntu 23.04+, Debian 12+, and Mint 22+ mark Python as externally managed
# (PEP 668). Try progressively more permissive install modes.
if pip3 install flask requests 2>/dev/null; then
    print_ok "Flask and requests installed (standard pip)."
elif pip3 install --break-system-packages flask requests 2>/dev/null; then
    print_ok "Flask and requests installed (--break-system-packages)."
elif pip3 install --user flask requests 2>/dev/null; then
    print_ok "Flask and requests installed (--user)."
else
    print_error "Could not install Flask and requests. Try: sudo pip3 install --break-system-packages flask requests"
    exit 1
fi

print_info "Setting up /opt/openclaw-setup..."
# Resolve the repo root from this script's real location so the copy is
# correct regardless of the caller's working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
sudo mkdir -p /opt/openclaw-setup
sudo rsync -a --delete "${REPO_ROOT}/" /opt/openclaw-setup/ 2>/dev/null || \
    sudo cp -r "${REPO_ROOT}/." /opt/openclaw-setup/
sudo find /opt/openclaw-setup -maxdepth 1 -name '*.sh' -exec chmod +x {} \;
sudo find /opt/openclaw-setup/installers -name '*.sh' -exec chmod +x {} \; 2>/dev/null || true
print_ok "Setup files copied."

print_info "Setting up Node.js compile cache..."
sudo mkdir -p /var/tmp/openclaw-compile-cache
if ! grep -q 'NODE_COMPILE_CACHE' ~/.bashrc; then
    echo 'export NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache' >> ~/.bashrc
fi
if ! grep -q 'OPENCLAW_NO_RESPAWN' ~/.bashrc; then
    echo 'export OPENCLAW_NO_RESPAWN=1' >> ~/.bashrc
fi
print_ok "Compile cache configured."

print_info "Installing and enabling systemd services..."
if [ -f /opt/openclaw-setup/services/openclaw-setup.service ]; then
    sudo cp /opt/openclaw-setup/services/openclaw-setup.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable openclaw-setup
    sudo systemctl start openclaw-setup
    print_ok "systemd services started."
else
    print_error "openclaw-setup.service not found."
fi

print_info "Setup wizard is opening in your browser!"
sleep 3
xdg-open http://localhost:7070 2>/dev/null || echo "Open http://localhost:7070 in your browser"
