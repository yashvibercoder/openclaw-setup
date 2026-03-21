#!/usr/bin/env bash
# =============================================================================
# OpenClaw Easy Setup — Raspberry Pi Installer
# =============================================================================
# Target:  Raspberry Pi OS Lite 64-bit (Pi 5 8GB primary, Pi 4 8GB secondary)
# Usage:   sudo bash install_pi.sh
# Log:     /var/log/openclaw-install.log
# =============================================================================

set -euo pipefail

# =============================================================================
# CONSTANTS & GLOBALS
# =============================================================================
readonly LOG_FILE="/var/log/openclaw-install.log"
readonly INSTALL_START_TIME=$(date +%s)
readonly REQUIRED_NODE_MAJOR=22

PI_MODEL=""
BOOT_DEVICE=""

# ANSI colour codes
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly RESET='\033[0m'

# =============================================================================
# LOGGING HELPERS
# =============================================================================
# All output goes to both stdout (with colour) and the log file (plain text).
# We open the log file once here and tee every subsequent write into it.

# Ensure log directory exists before exec redirect
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

# Redirect all further stdout + stderr through tee into the log file.
# We use file descriptor 3 for a plain-text copy (strip ANSI before writing).
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
    echo -e "${GREEN}[OK]${RESET}   $*"
}

warn() {
    echo -e "${YELLOW}[WARN]${RESET} $*"
}

error() {
    echo -e "${RED}[ERROR]${RESET} $*" >&2
}

section() {
    echo ""
    echo -e "${CYAN}${BOLD}━━━  $* ━━━${RESET}"
}

# =============================================================================
# STEP 1 — PRE-FLIGHT CHECKS
# =============================================================================
step_1_preflight() {
    section "Step 1/15 — Pre-flight checks"

    # Root check
    if [ "$EUID" -ne 0 ]; then
        error "This installer must be run as root."
        error "Please re-run with: sudo bash $0"
        exit 1
    fi
    log "Running as root — OK"

    # Detect Pi model
    if [ -f /proc/device-tree/model ]; then
        PI_MODEL=$(tr -d '\0' < /proc/device-tree/model)
    else
        PI_MODEL="Unknown (non-Pi or virtualised environment)"
        warn "Could not read /proc/device-tree/model — hardware detection skipped."
    fi
    log "Detected hardware: ${PI_MODEL}"

    # Detect boot device
    local root_device
    root_device=$(findmnt -n -o SOURCE / 2>/dev/null || echo "unknown")

    if echo "$root_device" | grep -qE '^/dev/mmcblk'; then
        BOOT_DEVICE="SD card (${root_device})"
        echo ""
        echo -e "${YELLOW}${BOLD}  ⚠️  WARNING: Running from SD card.${RESET}"
        echo -e "${YELLOW}      For best performance, boot from NVMe SSD or USB drive.${RESET}"
        echo -e "${YELLOW}      SD cards wear out quickly under OpenClaw's constant read/write activity.${RESET}"
        echo ""
    elif echo "$root_device" | grep -qE '^/dev/(nvme|sda)'; then
        BOOT_DEVICE="SSD/USB (${root_device})"
        log "Boot device: ${BOOT_DEVICE}"
    else
        BOOT_DEVICE="Unknown (${root_device})"
        warn "Could not determine boot device type: ${root_device}"
    fi

    log "Boot device stored: ${BOOT_DEVICE}"
}

# =============================================================================
# STEP 2 — SYSTEM UPDATE
# =============================================================================
step_2_update() {
    section "Step 2/15 — System update"

    log "Updating package lists..."
    apt-get update -y

    log "Upgrading installed packages..."
    apt-get upgrade -y

    log "System update complete."
}

# =============================================================================
# STEP 3 — INSTALL SYSTEM DEPENDENCIES
# =============================================================================
step_3_dependencies() {
    section "Step 3/15 — Install system dependencies"

    apt-get install -y \
        curl \
        git \
        build-essential \
        hostapd \
        dnsmasq \
        python3 \
        python3-pip \
        wireless-tools \
        iw \
        net-tools \
        dhcpcd5

    log "System dependencies installed."
}

# =============================================================================
# STEP 4 — INSTALL NODE.JS >= 22 VIA NODESOURCE
# =============================================================================
step_4_nodejs() {
    section "Step 4/15 — Install Node.js >= ${REQUIRED_NODE_MAJOR} (NodeSource)"

    log "Fetching NodeSource setup script for Node.js 24.x..."
    curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -

    log "Installing Node.js..."
    apt-get install -y nodejs

    # Verify version
    local node_version_raw
    node_version_raw=$(node --version 2>/dev/null || echo "")

    if [ -z "$node_version_raw" ]; then
        error "node command not found after installation. Cannot continue."
        exit 1
    fi

    # Strip leading 'v' and extract major version number
    local node_major
    node_major=$(echo "$node_version_raw" | sed 's/^v//' | cut -d. -f1)

    if [ "$node_major" -lt "$REQUIRED_NODE_MAJOR" ]; then
        error "Node.js version ${node_version_raw} is below required v${REQUIRED_NODE_MAJOR}."
        error "Please check your NodeSource repository configuration."
        exit 1
    fi

    log "Node.js ${node_version_raw} installed and verified (>= v${REQUIRED_NODE_MAJOR})."
    log "npm: $(npm --version)"
}

# =============================================================================
# STEP 5 — INSTALL OPENCLAW GLOBALLY
# =============================================================================
step_5_openclaw() {
    section "Step 5/15 — Install OpenClaw globally"

    log "Running: npm install -g openclaw"
    npm install -g openclaw

    local oc_version
    oc_version=$(openclaw --version 2>/dev/null || echo "")
    if [ -z "$oc_version" ]; then
        warn "openclaw command not found or returned no version — installation may have issues."
        warn "Continuing anyway; the package may not expose a --version flag yet."
    else
        log "OpenClaw installed: ${oc_version}"
    fi
}

# =============================================================================
# STEP 6 — INSTALL FLASK + REQUESTS
# =============================================================================
step_6_python_deps() {
    section "Step 6/15 — Install Python dependencies (Flask, requests)"

    # Pi OS Bookworm (Debian 12) and later mark the system Python as
    # "externally managed" (PEP 668), which blocks plain pip3 install.
    # Try the most permissive flag first, then fall back gracefully.
    if pip3 install flask requests 2>/dev/null; then
        log "Flask and requests installed (standard pip)."
    elif pip3 install --break-system-packages flask requests 2>/dev/null; then
        log "Flask and requests installed (--break-system-packages)."
    elif pip3 install --user flask requests 2>/dev/null; then
        log "Flask and requests installed (--user)."
    else
        error "Could not install Flask and requests via pip3."
        error "Try manually: sudo pip3 install --break-system-packages flask requests"
        exit 1
    fi
}

# =============================================================================
# STEP 7 — COPY SETUP FILES TO /opt/openclaw-setup
# =============================================================================
step_7_copy_files() {
    section "Step 7/15 — Copy setup files to /opt/openclaw-setup"

    # Resolve absolute paths robustly — works regardless of where caller cd'd
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local repo_root
    repo_root="$(dirname "$script_dir")"

    log "Script directory: ${script_dir}"
    log "Repository root:  ${repo_root}"

    mkdir -p /opt/openclaw-setup

    # rsync is preferred if available (preserves structure, avoids partial copies)
    if command -v rsync &>/dev/null; then
        rsync -a --delete "${repo_root}/" /opt/openclaw-setup/
    else
        cp -r "${repo_root}/." /opt/openclaw-setup/
    fi

    # Make all shell scripts executable
    find /opt/openclaw-setup -maxdepth 1 -name '*.sh' -exec chmod +x {} \;
    find /opt/openclaw-setup/installers -name '*.sh' -exec chmod +x {} \; 2>/dev/null || true

    log "Files copied to /opt/openclaw-setup and permissions set."
}

# =============================================================================
# STEP 8 — PERFORMANCE OPTIMISATION 1: NODE.JS COMPILE CACHE
# =============================================================================
step_8_compile_cache() {
    section "Step 8/15 — Performance: Node.js compile cache"

    mkdir -p /var/tmp/openclaw-compile-cache

    # System-wide environment variables
    if ! grep -q "NODE_COMPILE_CACHE" /etc/environment 2>/dev/null; then
        echo 'NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache' >> /etc/environment
        echo 'OPENCLAW_NO_RESPAWN=1' >> /etc/environment
        log "Added NODE_COMPILE_CACHE and OPENCLAW_NO_RESPAWN to /etc/environment."
    else
        log "NODE_COMPILE_CACHE already present in /etc/environment — skipped."
    fi

    # Per-user .bashrc
    local real_user="${SUDO_USER:-pi}"
    local user_home
    user_home=$(getent passwd "$real_user" | cut -d: -f6 2>/dev/null || echo "/home/${real_user}")

    if [ -d "$user_home" ]; then
        if ! grep -q "NODE_COMPILE_CACHE" "${user_home}/.bashrc" 2>/dev/null; then
            {
                echo 'export NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache'
                echo 'export OPENCLAW_NO_RESPAWN=1'
            } >> "${user_home}/.bashrc"
            log "Added compile cache exports to ${user_home}/.bashrc."
        else
            log "NODE_COMPILE_CACHE already present in ${user_home}/.bashrc — skipped."
        fi
    else
        warn "Home directory '${user_home}' not found for user '${real_user}' — skipping .bashrc update."
    fi
}

# =============================================================================
# STEP 9 — PERFORMANCE OPTIMISATION 2: SWAP FILE (2 GB)
# =============================================================================
step_9_swap() {
    section "Step 9/15 — Performance: Swap file (2 GB)"

    # swapon --show prints a header line plus one line per swap area.
    # wc -l == 1 means only the header — no active swap.
    if [ "$(swapon --show | wc -l)" -le 1 ]; then
        log "No swap detected — creating 2 GB swapfile at /swapfile..."

        # Remove any broken leftover file
        if [ -f /swapfile ]; then
            swapoff /swapfile 2>/dev/null || true
            rm -f /swapfile
        fi

        fallocate -l 2G /swapfile
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile

        if ! grep -q '/swapfile' /etc/fstab; then
            echo '/swapfile none swap sw 0 0' >> /etc/fstab
            log "Added /swapfile entry to /etc/fstab."
        fi

        if ! grep -q 'vm.swappiness' /etc/sysctl.conf 2>/dev/null; then
            echo 'vm.swappiness=10' >> /etc/sysctl.conf
            log "Set vm.swappiness=10 in /etc/sysctl.conf."
        fi

        sysctl -p
        log "Swap file created and activated."
    else
        log "Swap already active ($(swapon --show | tail -n +2 | awk '{print $3}' | tr '\n' ' ')) — skipped."
    fi
}

# =============================================================================
# STEP 10 — PERFORMANCE OPTIMISATION 3: NODE.JS HEAP SIZE
# =============================================================================
step_10_heap() {
    section "Step 10/15 — Performance: Node.js heap size"

    local node_heap
    if echo "$PI_MODEL" | grep -q "Raspberry Pi 5"; then
        node_heap="4096"
        log "Pi 5 detected — setting Node.js max heap to ${node_heap} MB."
    else
        node_heap="2048"
        log "Pi 4 (or other) detected — setting Node.js max heap to ${node_heap} MB."
    fi

    local node_opts="--max-old-space-size=${node_heap}"

    if ! grep -q "NODE_OPTIONS" /etc/environment 2>/dev/null; then
        echo "NODE_OPTIONS=${node_opts}" >> /etc/environment
        log "Added NODE_OPTIONS=${node_opts} to /etc/environment."
    else
        log "NODE_OPTIONS already present in /etc/environment — skipped."
    fi
}

# =============================================================================
# STEP 11 — PERFORMANCE OPTIMISATION 4: DISABLE WIFI POWER MANAGEMENT
# =============================================================================
step_11_wifi_power() {
    section "Step 11/15 — Performance: Disable WiFi power management"

    if ! grep -q 'wireless-power' /etc/network/interfaces 2>/dev/null; then
        echo 'wireless-power off' >> /etc/network/interfaces
        log "Added 'wireless-power off' to /etc/network/interfaces."
    else
        log "wireless-power already configured — skipped."
    fi

    # Apply immediately; suppress errors if wlan0 doesn't exist yet
    if iwconfig wlan0 power off 2>/dev/null; then
        log "WiFi power management disabled on wlan0 immediately."
    else
        warn "Could not apply iwconfig change now (wlan0 may not be up) — will take effect on next boot."
    fi
}

# =============================================================================
# STEP 12 — PERFORMANCE OPTIMISATION 5: WEEKLY REBOOT CRON
# =============================================================================
step_12_weekly_reboot() {
    section "Step 12/15 — Performance: Weekly reboot cron (memory leak prevention)"

    local cron_line="0 4 * * 0 /sbin/reboot"

    # Atomically replace root's crontab: remove any existing reboot entry, add the new one
    (crontab -l 2>/dev/null | grep -v '/sbin/reboot'; echo "$cron_line") | crontab -

    log "Weekly reboot scheduled: ${cron_line}"
}

# =============================================================================
# STEP 13 — INSTALL SYSTEMD SERVICES
# =============================================================================
step_13_systemd_services() {
    section "Step 13/15 — Install systemd services"

    local services_dir="/opt/openclaw-setup/services"

    # Copy service unit files — warn but don't abort if a source file is missing
    for svc in openclaw-setup.service openclaw-gateway.service openclaw-tui.service; do
        local src="${services_dir}/${svc}"
        local dst="/etc/systemd/system/${svc}"
        if [ -f "$src" ]; then
            cp "$src" "$dst"
            log "Installed ${svc}."
        else
            warn "Service file not found: ${src} — skipping (will be deployed later)."
        fi
    done

    # Create a systemd drop-in for the gateway service to inject env vars & restart policy
    mkdir -p /etc/systemd/system/openclaw-gateway.service.d/

    cat > /etc/systemd/system/openclaw-gateway.service.d/perf.conf << 'EOF'
[Service]
Environment=OPENCLAW_NO_RESPAWN=1
Environment=NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
Environment=NODE_OPTIONS=--max-old-space-size=4096
Restart=always
RestartSec=2
TimeoutStartSec=90
EOF

    log "Created gateway performance drop-in: /etc/systemd/system/openclaw-gateway.service.d/perf.conf"

    systemctl daemon-reload
    log "systemd daemon reloaded."

    # Enable only the setup service; gateway + TUI are enabled by apply_config.py post-setup
    if systemctl cat openclaw-setup.service &>/dev/null; then
        systemctl enable openclaw-setup
        log "openclaw-setup service enabled."
    else
        warn "openclaw-setup.service unit not found — enable it manually after deploying service files."
    fi

    for svc in openclaw-gateway openclaw-tui; do
        systemctl disable "$svc" 2>/dev/null || true
        log "${svc} service disabled (will be enabled after wizard completes)."
    done
}

# =============================================================================
# STEP 14 — FIRST-BOOT SYSTEMD SERVICE
# =============================================================================
step_14_first_boot() {
    section "Step 14/15 — Install first-boot systemd service"

    cat > /etc/systemd/system/openclaw-first-boot.service << 'EOF'
[Unit]
Description=OpenClaw First Boot Setup
After=network.target
ConditionPathExists=!/home/pi/.openclaw/.configured

[Service]
Type=oneshot
ExecStart=/opt/openclaw-setup/first_boot.sh
RemainAfterExit=yes
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable openclaw-first-boot
    log "openclaw-first-boot service installed and enabled."
}

# =============================================================================
# STEP 15 — FINAL VERIFICATION & SUMMARY
# =============================================================================
step_15_summary() {
    section "Step 15/15 — Final verification"

    local node_ver npm_ver python_ver oc_ver flask_ver

    node_ver=$(node --version 2>/dev/null || echo "NOT FOUND")
    npm_ver=$(npm --version 2>/dev/null || echo "NOT FOUND")
    python_ver=$(python3 --version 2>/dev/null || echo "NOT FOUND")
    oc_ver=$(openclaw --version 2>/dev/null || echo "installed")

    if python3 -c "import flask" 2>/dev/null; then
        flask_ver="installed"
    else
        flask_ver="NOT FOUND — check pip3 install flask"
    fi

    echo ""
    echo -e "${GREEN}✅ Node.js:  ${node_ver}${RESET}"
    echo -e "${GREEN}✅ npm:      ${npm_ver}${RESET}"
    echo -e "${GREEN}✅ Python:   ${python_ver}${RESET}"
    echo -e "${GREEN}✅ Flask:    ${flask_ver}${RESET}"
    echo -e "${GREEN}✅ OpenClaw: ${oc_ver}${RESET}"
    echo -e "${GREEN}✅ Systemd services: configured${RESET}"
    echo -e "${GREEN}✅ Performance optimisations: applied${RESET}"
    echo -e "${GREEN}✅ Boot device: ${BOOT_DEVICE}${RESET}"

    # Print total elapsed time
    local end_time elapsed minutes seconds
    end_time=$(date +%s)
    elapsed=$(( end_time - INSTALL_START_TIME ))
    minutes=$(( elapsed / 60 ))
    seconds=$(( elapsed % 60 ))

    echo ""
    echo -e "${CYAN}Total install time: ${minutes}m ${seconds}s${RESET}"
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${BOLD}  OpenClaw installation complete!${RESET}"
    echo ""
    echo -e "  Reboot your Pi to start the setup wizard:"
    echo -e "    ${CYAN}sudo reboot${RESET}"
    echo ""
    echo -e "  After reboot, connect to WiFi: ${BOLD}OpenClaw-Setup${RESET}"
    echo -e "  Then open a browser on your phone or laptop."
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
    echo -e "${CYAN}Full install log saved to: ${LOG_FILE}${RESET}"
}

# =============================================================================
# MAIN — RUN ALL STEPS IN ORDER
# =============================================================================
main() {
    echo ""
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${BOLD}${CYAN}     OpenClaw Easy Setup — Raspberry Pi Installer${RESET}"
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "  Started: $(date)"
    echo -e "  Log file: ${LOG_FILE}"
    echo ""

    step_1_preflight
    step_2_update
    step_3_dependencies
    step_4_nodejs
    step_5_openclaw
    step_6_python_deps
    step_7_copy_files
    step_8_compile_cache
    step_9_swap
    step_10_heap
    step_11_wifi_power
    step_12_weekly_reboot
    step_13_systemd_services
    step_14_first_boot
    step_15_summary
}

main "$@"
