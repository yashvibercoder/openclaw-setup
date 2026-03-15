#!/usr/bin/env bash
# hotspot_stop.sh — Tear down the OpenClaw-Setup hotspot and reconnect to WiFi
# Stops hostapd and dnsmasq, flushes the static IP from wlan0, then restarts
# dhcpcd so the Pi reconnects to the user's WiFi network whose credentials were
# written to /etc/wpa_supplicant/wpa_supplicant.conf by apply_config.py.
#
# Must be run as root.
# Usage: sudo /opt/openclaw-setup/hotspot_stop.sh
#
# NOTE: set -e is intentionally omitted here so that individual stop commands
#       that fail (e.g. a service that was never started) do not abort the
#       teardown sequence.  set -u and set -o pipefail are still active.

set -uo pipefail

# ── Helpers ──────────────────────────────────────────────────────────────────

log()  { echo "[hotspot-stop] $*"; }
warn() { echo "[hotspot-stop] WARNING: $*" >&2; }
err()  { echo "[hotspot-stop] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

# ── Root check ───────────────────────────────────────────────────────────────

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "This script must be run as root. Try: sudo $0"
fi

# ── Step 1: Stop hostapd ──────────────────────────────────────────────────────

log "Step 1/7 — Stopping hostapd..."

if systemctl stop hostapd 2>/dev/null; then
    log "  Stopped via systemd."
elif pkill -TERM hostapd 2>/dev/null; then
    log "  Sent SIGTERM to hostapd process."
    sleep 1
    pkill -KILL hostapd 2>/dev/null || true
    log "  hostapd killed."
else
    log "  hostapd was not running — nothing to stop."
fi

# ── Step 2: Stop dnsmasq ──────────────────────────────────────────────────────

log "Step 2/7 — Stopping dnsmasq..."

if systemctl stop dnsmasq 2>/dev/null; then
    log "  Stopped via systemd."
elif pkill -TERM dnsmasq 2>/dev/null; then
    log "  Sent SIGTERM to dnsmasq process."
    sleep 1
    pkill -KILL dnsmasq 2>/dev/null || true
    log "  dnsmasq killed."
else
    log "  dnsmasq was not running — nothing to stop."
fi

# ── Step 3: Remove static IP and reset wlan0 ─────────────────────────────────

log "Step 3/7 — Flushing static IP from wlan0..."

ip addr flush dev wlan0 2>/dev/null || warn "  Could not flush wlan0 addresses (interface may be absent)."
ip link set wlan0 down  2>/dev/null || warn "  Could not bring wlan0 down."
ip link set wlan0 up    2>/dev/null || warn "  Could not bring wlan0 up."

log "  wlan0 reset."

# ── Step 4: Kill any lingering dhcpcd for wlan0 ───────────────────────────────

log "Step 4/7 — Removing any stale dhcpcd lease for wlan0..."

# dhcpcd may hold an old state file from before the hotspot; clear it so the
# fresh restart below picks up the new wpa_supplicant credentials cleanly.
if pkill -TERM -f "dhcpcd.*wlan0" 2>/dev/null; then
    log "  Sent SIGTERM to dhcpcd wlan0 process."
    sleep 1
    pkill -KILL -f "dhcpcd.*wlan0" 2>/dev/null || true
else
    log "  No stale dhcpcd wlan0 process found."
fi

# Remove lease file so dhcpcd negotiates a fresh address on the new network
LEASE_FILE="/var/lib/dhcpcd/wlan0.lease"
if [[ -f "${LEASE_FILE}" ]]; then
    rm -f "${LEASE_FILE}"
    log "  Removed stale lease file: ${LEASE_FILE}"
fi

# ── Step 5: Restart dhcpcd to reconnect to user WiFi ─────────────────────────

log "Step 5/7 — Restarting dhcpcd to reconnect to WiFi..."

if systemctl is-active --quiet dhcpcd 2>/dev/null || systemctl is-enabled --quiet dhcpcd 2>/dev/null; then
    # systemd-managed dhcpcd
    if systemctl restart dhcpcd 2>/dev/null; then
        log "  dhcpcd restarted via systemd."
    else
        warn "  systemctl restart dhcpcd failed — trying dhcpcd wlan0 directly."
        dhcpcd wlan0 2>/dev/null || warn "  dhcpcd wlan0 also failed."
    fi
else
    # No systemd unit — fall back to direct invocation
    log "  systemd dhcpcd unit not found — running dhcpcd wlan0 directly."
    dhcpcd wlan0 2>/dev/null || warn "  dhcpcd wlan0 failed; WiFi reconnect may not complete."
fi

# ── Step 6: Wait up to 30 s for WiFi association ─────────────────────────────

log "Step 6/7 — Waiting for WiFi connection (up to 30 s)..."

CONNECTED=false
for i in $(seq 1 30); do
    # iw reports "Connected to <bssid>" when associated
    if iw dev wlan0 link 2>/dev/null | grep -qi "Connected"; then
        CONNECTED=true
        break
    fi
    # Alternatively accept an assigned IP address as proof of connection
    if ip addr show wlan0 2>/dev/null | grep -q "inet "; then
        CONNECTED=true
        break
    fi
    printf "\r[hotspot-stop]   Waiting... %2d/30 s" "${i}"
    sleep 1
done
echo ""   # newline after the progress counter

# ── Step 7: Report result ─────────────────────────────────────────────────────

log "Step 7/7 — Connection check complete."

if "${CONNECTED}"; then
    ASSIGNED_IP=$(ip addr show wlan0 2>/dev/null \
                  | awk '/inet / {print $2}' | head -n1)
    echo ""
    echo "=========================================="
    echo "  Connected to WiFi"
    if [[ -n "${ASSIGNED_IP:-}" ]]; then
        echo "  wlan0 IP address: ${ASSIGNED_IP}"
    fi
    echo "=========================================="
else
    warn "wlan0 did not associate within 30 seconds."
    warn "The OpenClaw agent is still running; WiFi may connect shortly."
    warn "Diagnostics:"
    warn "  iw dev wlan0 link"
    warn "  journalctl -u dhcpcd --no-pager -n 30"
    warn "  cat /etc/wpa_supplicant/wpa_supplicant.conf"
    echo ""
    echo "=========================================="
    echo "  Hotspot stopped. WiFi reconnect pending."
    echo "  (This is not fatal — the Pi agent continues running.)"
    echo "=========================================="
fi

exit 0
