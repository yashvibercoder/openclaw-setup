"""
apply_config.py — OpenClaw Easy Setup configuration applier.

Called by setup_server.py after successful form submission.
Reads config JSON from stdin, writes all config files, installs services.

Exit codes:
    0 — success
    1 — failure (JSON error details printed to stdout)
"""

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

IS_PI_OR_LINUX: bool = sys.platform.startswith("linux")
IS_MAC: bool = sys.platform == "darwin"
IS_WINDOWS: bool = sys.platform == "win32"
IS_PI: bool = IS_PI_OR_LINUX and os.path.exists("/sys/class/net/wlan0")


def _find_openclaw() -> "str | None":
    """Resolve the openclaw executable path across platforms."""
    import shutil
    if IS_WINDOWS:
        for name in ("openclaw", "openclaw.cmd", "openclaw.exe"):
            found = shutil.which(name)
            if found:
                return found
        # Probe npm global bin directly
        for d in [
            os.path.expandvars(r"%APPDATA%\npm"),
            r"C:\Program Files\nodejs",
        ]:
            for ext in (".cmd", ".exe", ""):
                p = os.path.join(d, "openclaw" + ext)
                if os.path.exists(p):
                    return p
        return None
    return shutil.which("openclaw")


# ---------------------------------------------------------------------------
# Provider mapping
# Maps the web UI provider name to openclaw's internal identifiers.
# ---------------------------------------------------------------------------

PROVIDER_INFO: dict[str, dict] = {
    "gemini": {
        "provider_id": "google",
        "profile_id":  "google:default",
        "model":       "google/gemini-2.0-flash",
    },
    "openai": {
        "provider_id": "openai",
        "profile_id":  "openai:default",
        "model":       "openai/gpt-4o",
    },
    "grok": {
        "provider_id": "xai",
        "profile_id":  "xai:default",
        "model":       "xai/grok-2",
    },
    "deepseek": {
        "provider_id": "deepseek",
        "profile_id":  "deepseek:default",
        "model":       "deepseek/deepseek-chat",
    },
    "anthropic": {
        "provider_id": "anthropic",
        "profile_id":  "anthropic:default",
        "model":       "anthropic/claude-3-5-sonnet-20241022",
    },
    # custom: treat as an OpenAI-compatible endpoint
    "custom": {
        "provider_id": "openai",
        "profile_id":  "openai:custom",
        "model":       "openai/gpt-4o",
    },
}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _fail(step: str, message: str) -> None:
    """Print a failure JSON payload to stdout and exit with code 1.

    API keys and tokens are intentionally never included in error messages.
    """
    payload = {"ok": False, "error": message, "step": step}
    print(json.dumps(payload), flush=True)
    sys.exit(1)


def _success(doctor_output: str) -> None:
    """Print a success JSON payload to stdout and exit with code 0."""
    payload = {"ok": True, "doctor_output": doctor_output}
    print(json.dumps(payload), flush=True)
    sys.exit(0)


def run_script(path: str) -> None:
    """Run a shell script by path using subprocess (no shell=True)."""
    subprocess.run(["/bin/bash", path], check=True)


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


def _write_auth_profiles(provider_id: str, profile_id: str, api_key: str) -> None:
    """Write the API key to auth-profiles.json for the main agent.

    OpenClaw stores actual credentials in
    ~/.openclaw/agents/main/agent/auth-profiles.json — separate from the
    main openclaw.json which only holds profile metadata.
    """
    agents_dir = Path.home() / ".openclaw" / "agents" / "main" / "agent"
    agents_dir.mkdir(parents=True, exist_ok=True)
    auth_file = agents_dir / "auth-profiles.json"

    # Load existing data, or start fresh.
    if auth_file.exists():
        try:
            existing: dict = json.loads(auth_file.read_text())
        except Exception:
            existing = {"version": 1, "profiles": {}}
    else:
        existing = {"version": 1, "profiles": {}}

    if "profiles" not in existing:
        existing["profiles"] = {}
    if "lastGood" not in existing:
        existing["lastGood"] = {}

    existing["profiles"][profile_id] = {
        "type": "api_key",
        "provider": provider_id,
        "key": api_key,
    }
    existing["lastGood"][provider_id] = profile_id

    # Write atomically with restrictive permissions.
    tmp_path = str(auth_file) + ".tmp"
    tmp_fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(tmp_fd, "w") as fh:
        json.dump(existing, fh, indent=2)
        fh.write("\n")
    os.replace(tmp_path, str(auth_file))
    try:
        os.chmod(str(auth_file), 0o600)
    except NotImplementedError:
        pass


def step_write_openclaw_config(config: dict) -> None:
    """Step 1: Configure OpenClaw via its CLI and direct file writes.

    Previous versions of this script incorrectly wrote to
    ~/.openclaw/config.json with the wrong schema.  The real config file is
    ~/.openclaw/openclaw.json and credentials live in
    ~/.openclaw/agents/main/agent/auth-profiles.json.
    """
    exe = _find_openclaw()
    if not exe:
        raise RuntimeError(
            "openclaw executable not found. "
            "Make sure OpenClaw was installed successfully and is on PATH."
        )

    llm_provider   = config["llm_provider"]
    llm_api_key    = config["llm_api_key"]
    llm_base_url   = config.get("llm_base_url") or ""
    telegram_token = config["telegram_token"]

    info = PROVIDER_INFO.get(llm_provider)
    if not info:
        raise RuntimeError(f"Unknown LLM provider: {llm_provider!r}")

    provider_id = info["provider_id"]
    profile_id  = info["profile_id"]
    model       = info["model"]

    # ── 1. Bootstrap openclaw.json if it does not exist yet ────────────────
    openclaw_json = Path.home() / ".openclaw" / "openclaw.json"
    if not openclaw_json.exists():
        print("[apply_config] Initialising openclaw config...", file=sys.stderr)
        subprocess.run(
            [
                exe, "setup", "--non-interactive",
                "--workspace", str(Path.home() / ".openclaw" / "workspace"),
            ],
            check=False,  # best-effort; the file may be created mid-run
        )

    # ── 2. Configure Telegram via the channels CLI ─────────────────────────
    print("[apply_config] Configuring Telegram channel...", file=sys.stderr)
    subprocess.run(
        [exe, "channels", "add", "--channel", "telegram", "--token", telegram_token],
        check=True,
    )

    # ── 3. Set the primary model ────────────────────────────────────────────
    print(f"[apply_config] Setting primary model to {model!r}...", file=sys.stderr)
    subprocess.run(
        [exe, "config", "set", "agents.defaults.model.primary", model],
        check=True,
    )

    # ── 4. Declare the auth-profile metadata in openclaw.json ──────────────
    #   openclaw.json only stores the profile type; the key itself lives in
    #   auth-profiles.json (written in step 5).
    print(
        f"[apply_config] Registering auth profile {profile_id!r}...",
        file=sys.stderr,
    )
    subprocess.run(
        [exe, "config", "set", f"auth.profiles.{profile_id}.provider", provider_id],
        check=True,
    )
    subprocess.run(
        [exe, "config", "set", f"auth.profiles.{profile_id}.mode", "api_key"],
        check=True,
    )

    # ── 5. Write the API key to auth-profiles.json ─────────────────────────
    print("[apply_config] Writing API key to auth-profiles.json...", file=sys.stderr)
    _write_auth_profiles(provider_id, profile_id, llm_api_key)

    # ── 6. Custom provider: persist the base URL in the agent model config ─
    if llm_provider == "custom" and llm_base_url:
        print(
            f"[apply_config] Setting custom base URL {llm_base_url!r}...",
            file=sys.stderr,
        )
        # Store the custom base URL so the gateway knows where to send requests.
        subprocess.run(
            [exe, "config", "set", "agents.defaults.models.openai/gpt-4o.baseUrl",
             llm_base_url],
            check=False,  # best-effort; key path may differ across versions
        )


def step_configure_wifi(config: dict) -> None:
    """Step 2: Write WiFi credentials using a platform-appropriate method."""
    wifi_ssid: str = config.get("wifi_ssid", "").strip()
    wifi_password: str = config.get("wifi_password", "")

    if IS_PI_OR_LINUX:
        if not wifi_ssid:
            # Nothing to configure — skip silently.
            return

        wpa_conf = (
            "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
            "update_config=1\n"
            "country=GB\n"
            "\n"
            "network={\n"
            f'    ssid="{wifi_ssid}"\n'
            f'    psk="{wifi_password}"\n'
            "}\n"
        )

        # Write via sudo tee so we do not need to run the whole process as root.
        proc = subprocess.run(
            ["sudo", "tee", "/etc/wpa_supplicant/wpa_supplicant.conf"],
            input=wpa_conf,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"sudo tee wpa_supplicant.conf failed (exit {proc.returncode})"
            )

    elif IS_MAC:
        if not wifi_ssid:
            return

        subprocess.run(
            [
                "networksetup",
                "-setairportnetwork",
                "en0",
                wifi_ssid,
                wifi_password,
            ],
            check=True,
        )

    elif IS_WINDOWS:
        if not wifi_ssid:
            return

        _write_wifi_profile_windows(wifi_ssid, wifi_password)


def _write_wifi_profile_windows(ssid: str, password: str) -> None:
    """Generate a WPA2 WiFi profile XML and add it via netsh on Windows."""
    # Escape XML special characters in SSID and password.
    def _xml_escape(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    safe_ssid = _xml_escape(ssid)
    safe_password = _xml_escape(password)

    profile_xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{safe_ssid}</name>
    <SSIDConfig>
        <SSID>
            <name>{safe_ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{safe_password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>
"""

    # Write to a secure temporary file; delete after netsh consumes it.
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xml", prefix="openclaw_wifi_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(profile_xml)

        subprocess.run(
            ["netsh", "wlan", "add", "profile", f'filename="{tmp_path}"'],
            check=True,
        )

        # Attempt to connect immediately.
        subprocess.run(
            ["netsh", "wlan", "connect", f'name="{ssid}"'],
            check=False,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def step_create_flag_file() -> None:
    """Step 3: Create ~/.openclaw/.configured to mark setup completion."""
    flag_path = os.path.expanduser("~/.openclaw/.configured")
    open(flag_path, "w").close()


def step_pi_reconnect_wifi(wifi_ssid: str) -> None:
    """Step 4 (Pi only): Stop hotspot and wait for WiFi association."""
    if not (IS_PI and wifi_ssid):
        return

    hotspot_script = "/opt/openclaw-setup/hotspot_stop.sh"
    if os.path.exists(hotspot_script):
        run_script(hotspot_script)

    # Wait up to 30 seconds for the interface to associate.
    for _ in range(30):
        result = subprocess.run(
            ["iwconfig", "wlan0"],
            capture_output=True,
            text=True,
        )
        if "ESSID" in result.stdout and wifi_ssid in result.stdout:
            break
        time.sleep(1)


def step_install_daemon() -> None:
    """Step 5: Install OpenClaw as a system daemon service (best-effort).

    On Windows and desktop Linux there is no systemd/launchd managed by
    openclaw onboard — we skip silently and rely on step_restart_gateway
    to launch the process directly. Only attempt on Pi and macOS where the
    daemon install is known to work.
    """
    if IS_WINDOWS:
        print("[apply_config] Skipping daemon install on Windows.", file=sys.stderr)
        return
    if IS_PI_OR_LINUX and not IS_PI:
        # Desktop Linux (e.g. Mint, Ubuntu) — onboard --install-daemon
        # requires root and is Pi-oriented; skip it and let the gateway
        # start directly instead.
        print("[apply_config] Desktop Linux — skipping daemon install.", file=sys.stderr)
        return
    exe = _find_openclaw()
    if not exe:
        print("[apply_config] openclaw not found — skipping daemon install.", file=sys.stderr)
        return
    try:
        subprocess.run([exe, "onboard", "--install-daemon"], check=True)
    except Exception as exc:
        # Best-effort: log but do not abort setup — gateway will start directly.
        print(f"[apply_config] daemon install skipped: {exc}", file=sys.stderr)


def step_run_doctor() -> str:
    """Step 6: Run openclaw doctor and return its output."""
    exe = _find_openclaw()
    if not exe:
        return "(openclaw not found — skipping doctor)"
    try:
        result = subprocess.run(
            [exe, "doctor"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout
        if result.stderr:
            output += result.stderr
        return output.strip()
    except Exception as exc:
        return f"(openclaw doctor skipped: {exc})"


def step_restart_gateway() -> None:
    """Step 7: Start or restart the OpenClaw gateway (best-effort).

    On Pi the daemon was installed by step_install_daemon so we try
    'gateway restart' first, then fall back to a direct detached launch.
    On Windows and desktop Linux we launch directly as a detached process
    so the gateway survives after this script exits.
    """
    exe = _find_openclaw()
    if not exe:
        print("[apply_config] openclaw not found — skipping gateway start.", file=sys.stderr)
        return

    if IS_WINDOWS:
        try:
            subprocess.Popen(
                [exe, "gateway"],
                creationflags=(
                    subprocess.DETACHED_PROCESS |
                    subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                close_fds=True,
            )
            print("[apply_config] OpenClaw gateway started in background.", file=sys.stderr)
        except Exception as exc:
            print(f"[apply_config] Could not start gateway: {exc}", file=sys.stderr)
    else:
        # Try a graceful restart first (works when daemon is already running).
        result = subprocess.run([exe, "gateway", "restart"], check=False)
        if result.returncode != 0:
            # Daemon not running or not installed — launch directly.
            # start_new_session=True fully detaches from this process group so
            # the gateway is not killed when apply_config.py (or the setup
            # server) exits or the terminal is closed.
            try:
                subprocess.Popen(
                    [exe, "gateway"],
                    close_fds=True,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print("[apply_config] OpenClaw gateway launched in background.", file=sys.stderr)
            except Exception as exc:
                print(f"[apply_config] Could not start gateway: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Read and parse the JSON config from stdin.
    try:
        raw = sys.stdin.read()
        config: dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail("parse_input", f"Invalid JSON from stdin: {exc}")
        return  # unreachable; satisfies type checkers

    # Validate required keys are present (without echoing their values).
    required_keys = [
        "llm_provider",
        "llm_api_key",
        "llm_base_url",
        "telegram_token",
    ]
    for key in required_keys:
        if key not in config:
            _fail("validate_input", f"Missing required field: {key}")
            return

    # wifi_ssid / wifi_password are optional (only required on Pi, validated
    # in setup_server.py before this script is called).
    config.setdefault("wifi_ssid", "")
    config.setdefault("wifi_password", "")

    # ---- Step 1: Configure OpenClaw (model, Telegram, API key) ----
    try:
        step_write_openclaw_config(config)
    except Exception as exc:
        _fail("write_openclaw_config", str(exc))
        return

    # ---- Step 2: Configure WiFi ----
    try:
        step_configure_wifi(config)
    except Exception as exc:
        _fail("configure_wifi", str(exc))
        return

    # ---- Step 3: Create flag file ----
    try:
        step_create_flag_file()
    except Exception as exc:
        _fail("create_flag_file", str(exc))
        return

    # ---- Step 4: Pi — stop hotspot and reconnect WiFi ----
    try:
        step_pi_reconnect_wifi(config.get("wifi_ssid", "").strip())
    except Exception as exc:
        _fail("pi_reconnect_wifi", str(exc))
        return

    # ---- Step 5: Install daemon ----
    try:
        step_install_daemon()
    except Exception as exc:
        _fail("install_daemon", str(exc))
        return

    # ---- Step 6: Run doctor ----
    doctor_output = ""
    try:
        doctor_output = step_run_doctor()
    except subprocess.TimeoutExpired:
        _fail("run_doctor", "openclaw doctor timed out after 30 seconds")
        return
    except Exception as exc:
        _fail("run_doctor", str(exc))
        return

    # ---- Step 7: Restart gateway ----
    try:
        step_restart_gateway()
    except Exception as exc:
        # Gateway restart is best-effort; log to stderr but do not fail.
        print(f"Warning: gateway restart failed: {exc}", file=sys.stderr)

    _success(doctor_output)


if __name__ == "__main__":
    main()
