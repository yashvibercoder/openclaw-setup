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

    # Unix/Linux/Mac: try shutil.which first (respects current PATH),
    # then fall back to common npm global bin directories that may be
    # absent from PATH when run inside Docker or a restricted environment.
    found = shutil.which("openclaw")
    if found:
        return found

    # Augment PATH with common npm global bin dirs and retry.
    extra_dirs = [
        "/usr/local/bin",
        "/usr/bin",
        "/opt/homebrew/bin",                           # macOS Homebrew (Apple Silicon)
        "/usr/local/lib/node_modules/.bin",
        os.path.expanduser("~/.npm-global/bin"),
        os.path.expanduser("~/node_modules/.bin"),
        "/root/.npm-global/bin",
    ]
    augmented_path = ":".join(
        [os.environ.get("PATH", "")] + extra_dirs
    )
    found = shutil.which("openclaw", path=augmented_path)
    if found:
        return found

    # Last resort: probe the extra dirs directly.
    for d in extra_dirs:
        p = os.path.join(d, "openclaw")
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    return None


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
        "model":       "anthropic/claude-sonnet-4-6",
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


def _patch_openclaw_json(json_path: Path, updates: dict) -> None:
    """Deep-merge *updates* into the JSON file at *json_path*.

    Creates the file (with an empty dict) if it does not exist.
    Writes atomically via a .tmp sibling to avoid partial writes.
    """
    if json_path.exists():
        try:
            cfg: dict = json.loads(json_path.read_text())
            if not isinstance(cfg, dict):
                cfg = {}
        except Exception:
            cfg = {}
    else:
        cfg = {}

    def _deep_merge(base: dict, patch: dict) -> None:
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                _deep_merge(base[k], v)
            else:
                base[k] = v

    _deep_merge(cfg, updates)

    tmp = str(json_path) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, str(json_path))


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
    """Step 1: Configure OpenClaw via its CLI with direct-file fallbacks.

    Strategy (in priority order):
      a) Use the openclaw CLI to set each value — this is the "official" path
         and preserves any existing config that we should not overwrite.
      b) If a CLI command fails fall back to patching openclaw.json directly
         so setup never silently leaves the agent unconfigured.

    All required sections are written: gateway (port/mode/bind/auth),
    channels.telegram (enabled/botToken/dmPolicy/groupPolicy),
    agents (model/workspace/list), auth profiles, and API key credentials.
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
    model       = config.get("llm_model", "").strip() or info["model"]

    openclaw_dir  = Path.home() / ".openclaw"
    openclaw_dir.mkdir(parents=True, exist_ok=True)
    openclaw_json = openclaw_dir / "openclaw.json"
    workspace_path = str(openclaw_dir / "workspace")

    # Gateway bind mode: Pi setups need LAN access; others use auto (loopback
    # with fallback to all-interfaces), which is safe for initial setup.
    bind_mode = "lan" if IS_PI else "auto"
    gateway_token = "openclaw123"

    # ── 0. Bootstrap openclaw.json if it does not exist yet ────────────────
    # Write a *complete* skeleton covering all required sections so the
    # gateway can start even if the subsequent CLI commands fail entirely.
    # The deep-merge in _patch_openclaw_json will not overwrite values that
    # already exist if this is called on an existing file.
    if not openclaw_json.exists():
        print(
            "[apply_config] openclaw.json missing; writing complete bootstrap config...",
            file=sys.stderr,
        )
        _patch_openclaw_json(openclaw_json, {
            "agents": {
                "defaults": {
                    "model": {"primary": model, "fallbacks": []},
                    "models": {model: {}},
                    "workspace": workspace_path,
                },
                "list": [{"id": "main"}],
            },
            "channels": {
                "telegram": {
                    "enabled": True,
                    "botToken": telegram_token,
                    "dmPolicy": "pairing",
                    "groupPolicy": "open",
                }
            },
            "auth": {
                "profiles": {
                    profile_id: {"provider": provider_id, "mode": "api_key"}
                }
            },
            "gateway": {
                "port": 18789,
                "mode": "local",
                "bind": bind_mode,
                "auth": {"mode": "token", "token": gateway_token},
            },
            "commands": {
                "native": "auto",
                "nativeSkills": "auto",
                "restart": False,
            },
        })

    # ── 1. Configure Telegram channel ──────────────────────────────────────
    # NOTE: 'openclaw channels add --channel telegram' is not supported in
    # this build. Use 'config set' instead — setting botToken auto-enables
    # the channel and sets dmPolicy to "pairing".
    print("[apply_config] Configuring Telegram channel...", file=sys.stderr)
    tg_failed = False
    try:
        tg_result = subprocess.run(
            [exe, "config", "set", "channels.telegram.botToken", telegram_token],
            capture_output=True,
            text=True,
            timeout=15,
        )
        tg_failed = tg_result.returncode != 0
    except subprocess.TimeoutExpired:
        tg_failed = True
    # Set group policy to "open" so group messages are not silently dropped.
    try:
        subprocess.run(
            [exe, "config", "set", "channels.telegram.groupPolicy", "open"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        pass
    if tg_failed:
        print(
            "[apply_config] 'openclaw config set channels.telegram.botToken' failed; "
            "writing token directly to openclaw.json.",
            file=sys.stderr,
        )
        _patch_openclaw_json(
            openclaw_json,
            {
                "channels": {
                    "telegram": {
                        "enabled": True,
                        "botToken": telegram_token,
                        "dmPolicy": "pairing",
                        "groupPolicy": "open",
                    }
                }
            },
        )

    # ── 2. Set the primary model ────────────────────────────────────────────
    print(f"[apply_config] Setting primary model to {model!r}...", file=sys.stderr)
    try:
        model_result = subprocess.run(
            [exe, "config", "set", "agents.defaults.model.primary", model],
            capture_output=True,
            text=True,
            timeout=15,
        )
        model_failed = model_result.returncode != 0
    except subprocess.TimeoutExpired:
        model_failed = True
    if model_failed:
        print(
            "[apply_config] 'openclaw config set' for model failed; patching openclaw.json directly.",
            file=sys.stderr,
        )
        _patch_openclaw_json(
            openclaw_json,
            {"agents": {"defaults": {"model": {"primary": model}}}},
        )

    # ── 3. Set the agent workspace ──────────────────────────────────────────
    print(f"[apply_config] Setting agent workspace to {workspace_path!r}...", file=sys.stderr)
    try:
        ws_result = subprocess.run(
            [exe, "config", "set", "agents.defaults.workspace", workspace_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        ws_failed = ws_result.returncode != 0
    except subprocess.TimeoutExpired:
        ws_failed = True
    if ws_failed:
        _patch_openclaw_json(
            openclaw_json,
            {"agents": {"defaults": {"workspace": workspace_path}}},
        )

    # ── 4. Declare the auth-profile metadata in openclaw.json ──────────────
    # openclaw.json stores the profile type; the key itself lives in
    # auth-profiles.json (written in step 6).
    # NOTE: 'openclaw config set' hangs on dot-paths that contain a colon
    # (e.g. 'auth.profiles.google:default.provider') because the CLI
    # misparses the colon as a host:port separator.  Always write directly.
    print(
        f"[apply_config] Registering auth profile {profile_id!r}...",
        file=sys.stderr,
    )
    _patch_openclaw_json(
        openclaw_json,
        {
            "auth": {
                "profiles": {
                    profile_id: {"provider": provider_id, "mode": "api_key"}
                }
            }
        },
    )

    # ── 5. Configure the gateway ────────────────────────────────────────────
    # Ensure the gateway has a port, mode, bind, and auth section.  Without
    # this the gateway either starts on unexpected defaults or fails to start.
    print("[apply_config] Configuring gateway...", file=sys.stderr)
    gw_cmds = [
        [exe, "config", "set", "gateway.port", "18789"],
        [exe, "config", "set", "gateway.mode", "local"],
        [exe, "config", "set", "gateway.bind", bind_mode],
        [exe, "config", "set", "gateway.auth.mode", "token"],
        [exe, "config", "set", "gateway.auth.token", gateway_token],
    ]
    gw_any_failed = False
    for cmd in gw_cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            failed = r.returncode != 0
        except subprocess.TimeoutExpired:
            failed = True
        if failed:
            gw_any_failed = True
            print(
                f"[apply_config] '{' '.join(cmd[2:])}' failed or timed out; "
                "will patch gateway config directly.",
                file=sys.stderr,
            )
    if gw_any_failed:
        _patch_openclaw_json(
            openclaw_json,
            {
                "gateway": {
                    "port": 18789,
                    "mode": "local",
                    "bind": bind_mode,
                    "auth": {"mode": "token", "token": gateway_token},
                }
            },
        )

    # ── 6. Write the API key to auth-profiles.json ─────────────────────────
    print("[apply_config] Writing API key to auth-profiles.json...", file=sys.stderr)
    _write_auth_profiles(provider_id, profile_id, llm_api_key)

    # ── 7. Custom provider: persist the base URL ────────────────────────────
    if llm_provider == "custom" and llm_base_url:
        print(
            f"[apply_config] Setting custom base URL {llm_base_url!r}...",
            file=sys.stderr,
        )
        try:
            url_result = subprocess.run(
                [exe, "config", "set", "agents.defaults.models.openai/gpt-4o.baseUrl",
                 llm_base_url],
                capture_output=True, text=True, timeout=15,
            )
            url_failed = url_result.returncode != 0
        except subprocess.TimeoutExpired:
            url_failed = True
        if url_failed:
            _patch_openclaw_json(
                openclaw_json,
                {"agents": {"defaults": {"models": {"openai/gpt-4o": {"baseUrl": llm_base_url}}}}},
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


def step_create_required_dirs() -> None:
    """Step 3a: Create critical directories that the gateway requires at startup."""
    required_dirs = [
        Path.home() / ".openclaw" / "workspace",
        Path.home() / ".openclaw" / "agents" / "main" / "agent",
        Path.home() / ".openclaw" / "agents" / "main" / "sessions",
        Path.home() / ".openclaw" / "credentials",
        Path.home() / ".openclaw" / "gateway",
        Path.home() / ".openclaw" / "logs",
        Path.home() / ".openclaw" / "delivery-queue",
        Path.home() / ".openclaw" / "memory",
    ]
    for d in required_dirs:
        d.mkdir(parents=True, exist_ok=True)
        print(f"[apply_config] Ensured directory: {d}", file=sys.stderr)


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

    # ---- Step 1: Configure OpenClaw (model, Telegram, API key, gateway) ----
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

    # ---- Step 3a: Create required directories ----
    try:
        step_create_required_dirs()
    except Exception as exc:
        _fail("create_required_dirs", str(exc))
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
