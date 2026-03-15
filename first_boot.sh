#!/usr/bin/env bash
# first_boot.sh — OpenClaw first-boot initialisation
# Invoked on every boot by the openclaw-first-boot.service systemd unit.
#
# Behaviour:
#   • If ~/.openclaw/.configured exists  → device is already set up; exit 0.
#   • Otherwise                          → start the WiFi hotspot and the
#                                          Flask setup server so the user can
#                                          configure the device via a captive-
#                                          portal browser page.
#
# All output is appended to /var/log/openclaw-first-boot.log in addition to
# the journal (systemd captures stdout/stderr automatically; we also tee to
# the log file so it persists across reboots and is easy to inspect).
#
# Must be run as root (systemd service runs as root).

set -euo pipefail

# ── Log redirection ───────────────────────────────────────────────────────────
# Redirect all stdout and stderr to the log file AND to the original stdout so
# journald still captures the output.

LOG_FILE="/var/log/openclaw-first-boot.log"
mkdir -p "$(dirname "${LOG_FILE}")"
exec > >(tee -a "${LOG_FILE}") 2>&1

# ── Helpers ───────────────────────────────────────────────────────────────────

log()  { echo "[first-boot] $(date '+%Y-%m-%d %H:%M:%S') $*"; }
err()  { echo "[first-boot] $(date '+%Y-%m-%d %H:%M:%S') ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "This script must be run as root. Try: sudo $0"
fi

# ── Resolve the real (non-root) user ─────────────────────────────────────────
# When launched by systemd the environment may be minimal; SUDO_USER is set
# when the admin calls `sudo` manually, and logname(1) resolves the login name
# from utmp.  Fall back to 'pi' — the default Raspberry Pi OS user — if both
# fail (which is expected during a fully automated first boot).

REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
OPENCLAW_HOME="/home/${REAL_USER}/.openclaw"
CONFIGURED_FLAG="${OPENCLAW_HOME}/.configured"

log "Resolved real user  : ${REAL_USER}"
log "OpenClaw home       : ${OPENCLAW_HOME}"
log "Configured flag     : ${CONFIGURED_FLAG}"

# ── Paths ─────────────────────────────────────────────────────────────────────

HOTSPOT_START_SCRIPT="/opt/openclaw-setup/hotspot_start.sh"
SETUP_SERVER_SCRIPT="/opt/openclaw-setup/setup_server.py"
SETUP_PID_FILE="/tmp/openclaw-setup.pid"

# ── Step 1: Check configuration state ────────────────────────────────────────

log "Checking configuration state..."

if [[ -f "${CONFIGURED_FLAG}" ]]; then
    log "OpenClaw already configured. Setup complete."
    echo ""
    echo "=========================================="
    echo "  OpenClaw is configured. Nothing to do."
    echo "=========================================="
    exit 0
fi

# ── Not yet configured — run the setup wizard ────────────────────────────────

log "First boot detected — starting setup wizard..."

# ── Step 2: Validate required scripts exist ───────────────────────────────────

if [[ ! -x "${HOTSPOT_START_SCRIPT}" ]]; then
    die "Hotspot start script not found or not executable: ${HOTSPOT_START_SCRIPT}
Please reinstall OpenClaw setup package."
fi

if [[ ! -f "${SETUP_SERVER_SCRIPT}" ]]; then
    die "Setup server script not found: ${SETUP_SERVER_SCRIPT}
Please reinstall OpenClaw setup package."
fi

# ── Step 3: Start the hotspot ─────────────────────────────────────────────────

log "Starting WiFi hotspot..."

if ! "${HOTSPOT_START_SCRIPT}"; then
    die "hotspot_start.sh failed (exit $?). Check ${LOG_FILE} and run:
    sudo ${HOTSPOT_START_SCRIPT}
for verbose output."
fi

log "Hotspot is up."

# ── Step 4: Start the Flask setup server ─────────────────────────────────────

log "Starting Flask setup server..."

# Kill any leftover setup server from a previous (incomplete) boot
if [[ -f "${SETUP_PID_FILE}" ]]; then
    OLD_PID=$(cat "${SETUP_PID_FILE}" 2>/dev/null || true)
    if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
        log "  Killing stale setup server (PID ${OLD_PID})..."
        kill -TERM "${OLD_PID}" 2>/dev/null || true
        sleep 1
        kill -KILL "${OLD_PID}" 2>/dev/null || true
    fi
    rm -f "${SETUP_PID_FILE}"
fi

# Launch the server as a background process owned by the real user so that
# files it creates (e.g. wpa_supplicant.conf) are written with the correct
# permissions.  We still run the overall script as root so that the hotspot
# commands (ip, hostapd, etc.) have the required privileges.
sudo -u "${REAL_USER}" python3 "${SETUP_SERVER_SCRIPT}" &
SETUP_PID=$!

# Confirm the process started
sleep 1
if ! kill -0 "${SETUP_PID}" 2>/dev/null; then
    die "Setup server exited immediately (PID ${SETUP_PID}).  Check:
    sudo python3 ${SETUP_SERVER_SCRIPT}
for verbose output."
fi

echo "${SETUP_PID}" > "${SETUP_PID_FILE}"
log "Setup server started (PID ${SETUP_PID}, PID file: ${SETUP_PID_FILE})."

# ── Step 5: Print user instructions ──────────────────────────────────────────

log "Setup wizard is running."
echo ""
echo "=========================================="
echo "  Connect to 'OpenClaw-Setup' WiFi on your phone or laptop"
echo "  Then open a browser — the setup page will appear automatically"
echo ""
echo "  Setup server PID : ${SETUP_PID}"
echo "  PID file         : ${SETUP_PID_FILE}"
echo "  Log file         : ${LOG_FILE}"
echo "=========================================="

exit 0
