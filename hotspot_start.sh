#!/usr/bin/env bash
# hotspot_start.sh — Start the OpenClaw-Setup WiFi access point
# Configures wlan0 as a captive-portal hotspot so a user can connect
# and run the setup wizard through the browser.
#
# Must be run as root.
# Usage: sudo /opt/openclaw-setup/hotspot_start.sh

set -euo pipefail

# ── Helpers ──────────────────────────────────────────────────────────────────

log()  { echo "[hotspot] $*"; }
err()  { echo "[hotspot] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

# ── Root check ───────────────────────────────────────────────────────────────

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "This script must be run as root. Try: sudo $0"
fi

# ── Dependency check ─────────────────────────────────────────────────────────

if ! command -v hostapd &>/dev/null; then
    die "hostapd is not installed. Install it with:
    sudo apt-get update && sudo apt-get install -y hostapd dnsmasq"
fi

if ! command -v dnsmasq &>/dev/null; then
    die "dnsmasq is not installed. Install it with:
    sudo apt-get update && sudo apt-get install -y dnsmasq"
fi

# ── Step 1: Kill any existing hotspot processes ───────────────────────────────

log "Step 1/8 — Stopping any existing hostapd / dnsmasq processes..."

# Gracefully stop via systemd first, then fall back to pkill
systemctl stop hostapd  2>/dev/null && log "  hostapd stopped via systemd"  || true
systemctl stop dnsmasq  2>/dev/null && log "  dnsmasq stopped via systemd"  || true

# Belt-and-suspenders: kill any stray processes
if pkill -TERM hostapd 2>/dev/null; then
    log "  Sent SIGTERM to hostapd"
    sleep 1
    pkill -KILL hostapd 2>/dev/null || true
fi
if pkill -TERM dnsmasq 2>/dev/null; then
    log "  Sent SIGTERM to dnsmasq"
    sleep 1
    pkill -KILL dnsmasq 2>/dev/null || true
fi

log "  Done."

# ── Step 2: Configure wlan0 with static IP ────────────────────────────────────

log "Step 2/8 — Assigning static IP 192.168.4.1 to wlan0..."

ip link set wlan0 down
ip addr flush dev wlan0
ip addr add 192.168.4.1/24 dev wlan0
ip link set wlan0 up

log "  wlan0 → 192.168.4.1/24"

# ── Step 3: Write hostapd configuration ──────────────────────────────────────

log "Step 3/8 — Writing /etc/hostapd/hostapd.conf..."

mkdir -p /etc/hostapd

cat > /etc/hostapd/hostapd.conf <<'EOF'
interface=wlan0
driver=nl80211
ssid=OpenClaw-Setup
hw_mode=g
channel=6
ieee80211n=1
wmm_enabled=1
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
EOF

chmod 600 /etc/hostapd/hostapd.conf
log "  Written."

# ── Step 4: Write dnsmasq configuration ──────────────────────────────────────

log "Step 4/8 — Writing /etc/dnsmasq.conf..."

# Back up any pre-existing dnsmasq config the first time
if [[ -f /etc/dnsmasq.conf ]] && [[ ! -f /etc/dnsmasq.conf.openclaw-backup ]]; then
    cp /etc/dnsmasq.conf /etc/dnsmasq.conf.openclaw-backup
    log "  Backed up original dnsmasq.conf → dnsmasq.conf.openclaw-backup"
fi

cat > /etc/dnsmasq.conf <<'EOF'
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/#/192.168.4.1
no-resolv
dhcp-option=3,192.168.4.1
dhcp-option=6,192.168.4.1
log-dhcp
EOF

chmod 644 /etc/dnsmasq.conf
log "  Written."

# ── Step 5: Start hostapd ─────────────────────────────────────────────────────

log "Step 5/8 — Starting hostapd..."

# Run in background daemon mode; -B forks and exits 0 on success
if ! hostapd -B /etc/hostapd/hostapd.conf; then
    die "hostapd failed to start. Check /etc/hostapd/hostapd.conf or run:
    sudo hostapd /etc/hostapd/hostapd.conf
to see verbose output."
fi

log "  hostapd launched."

# ── Step 6: Start dnsmasq ─────────────────────────────────────────────────────

log "Step 6/8 — Starting dnsmasq..."

if ! systemctl start dnsmasq; then
    die "dnsmasq failed to start via systemd. Check: journalctl -u dnsmasq --no-pager"
fi

log "  dnsmasq started."

# ── Step 7: Verify hostapd is still running ───────────────────────────────────

log "Step 7/8 — Verifying hostapd is running..."

# Give hostapd a moment to settle
sleep 2

if ! pgrep -x hostapd &>/dev/null; then
    die "hostapd started but is no longer running. Possible causes:
  - Another process is already using wlan0
  - The wireless driver does not support AP mode
  - The nl80211 driver is unavailable
  Run 'sudo hostapd /etc/hostapd/hostapd.conf' for verbose diagnostics."
fi

log "  hostapd is running (PID $(pgrep -x hostapd))."

# ── Step 8: Done ─────────────────────────────────────────────────────────────

log "Step 8/8 — Hotspot active."
echo ""
echo "=========================================="
echo "  Hotspot 'OpenClaw-Setup' is active on 192.168.4.1"
echo "  DHCP range : 192.168.4.2 – 192.168.4.20"
echo "  Connect your phone or laptop to 'OpenClaw-Setup'"
echo "  then open any browser — the setup page will appear."
echo "=========================================="
