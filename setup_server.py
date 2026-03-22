"""
OpenClaw Easy Setup — Flask configuration server.

Runs on port 7070 and provides a web UI for first-time setup:
  - Wi-Fi credentials (Raspberry Pi only)
  - LLM provider / API key
  - Telegram bot token

After the user submits the form the validated payload is forwarded to
apply_config.py via stdin so that file never has to be imported here.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORT = 7070

# Resolve paths relative to this file — works from any working directory.
_BASE_DIR = Path(__file__).resolve().parent
APPLY_CONFIG_PATH = str(_BASE_DIR / "apply_config.py")

KNOWN_PROVIDERS = {"gemini", "openai", "grok", "deepseek", "anthropic", "custom"}

PROVIDER_URLS: dict[str, str | None] = {
    "gemini": "https://generativelanguage.googleapis.com",
    "openai": "https://api.openai.com",
    "grok": "https://api.x.ai",
    "deepseek": "https://api.deepseek.com",
    "anthropic": "https://api.anthropic.com",
    "custom": None,
}

# Detect Raspberry Pi: wlan0 interface exists AND ARM/AArch64 architecture.
IS_PI: bool = (
    os.path.exists("/sys/class/net/wlan0")
    and platform.machine().startswith("aarch")
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(_BASE_DIR / "templates"),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(message: str, status: int = 400) -> tuple:
    """Return a uniform JSON error response."""
    return jsonify({"ok": False, "error": message}), status


def _validate_save_payload(data: dict) -> str | None:
    """
    Validate the fields from POST /save.

    Returns an error string on the first validation failure, or None when
    everything looks good.  API keys / tokens are never logged here.
    """
    provider = data.get("llm_provider", "").strip().lower()
    if provider not in KNOWN_PROVIDERS:
        return f"Unknown LLM provider '{provider}'. Must be one of: {', '.join(sorted(KNOWN_PROVIDERS))}."

    if not data.get("llm_api_key", "").strip():
        return "llm_api_key must not be empty."

    if provider == "custom" and not data.get("llm_base_url", "").strip():
        return "llm_base_url is required when provider is 'custom'."

    if not data.get("telegram_token", "").strip():
        return "telegram_token must not be empty."

    if IS_PI and not data.get("wifi_ssid", "").strip():
        return "wifi_ssid must not be empty on Raspberry Pi."

    return None


def _run_apply_config(payload: dict) -> tuple[bool, str]:
    """
    Invoke apply_config.py as a subprocess, passing *payload* as JSON on stdin.

    Returns (success: bool, message: str).
    The subprocess is invoked with a list — never a shell string — to prevent
    shell-injection attacks.

    apply_config.py writes a JSON result to stdout:
        success → {"ok": true,  "doctor_output": "..."}
        failure → {"ok": false, "error": "...", "step": "..."}
    and mirrors the ok/fail status via its exit code (0 / 1).
    We parse stdout for a human-readable error so the UI can display it.
    """
    try:
        result = subprocess.run(
            [sys.executable, APPLY_CONFIG_PATH],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        return False, f"apply_config.py not found at {APPLY_CONFIG_PATH}."
    except subprocess.TimeoutExpired:
        return False, "apply_config.py timed out after 3 minutes."

    if result.returncode != 0:
        # Try to extract a human-readable error from the JSON written to stdout.
        error_detail = ""
        try:
            out_json = json.loads((result.stdout or "").strip())
            if not out_json.get("ok", True):
                step = out_json.get("step", "")
                msg  = out_json.get("error", "")
                error_detail = f"[{step}] {msg}".strip("[] ") if step else msg
        except Exception:
            pass

        # Fall back to the last non-empty line of stderr (progress messages).
        if not error_detail:
            stderr_lines = [
                ln.strip() for ln in (result.stderr or "").splitlines()
                if ln.strip() and not ln.startswith("[apply_config]")
            ]
            error_detail = stderr_lines[-1] if stderr_lines else ""

        if not error_detail:
            stderr_snippet = (result.stderr or "").strip()[:300]
            error_detail = stderr_snippet or "no error details available"

        print(
            f"[setup_server] apply_config exited {result.returncode}: {error_detail}",
            file=sys.stderr,
        )
        return False, f"Setup failed (step failed): {error_detail}"

    return True, "Setup complete"


# ---------------------------------------------------------------------------
# Gateway helpers
# ---------------------------------------------------------------------------

def _find_openclaw() -> "str | None":
    """Locate the openclaw executable across platforms."""
    IS_WIN = sys.platform == "win32"
    if IS_WIN:
        for name in ("openclaw", "openclaw.cmd", "openclaw.exe"):
            found = shutil.which(name)
            if found:
                return found
        for d in [os.path.expandvars(r"%APPDATA%\npm"), r"C:\Program Files\nodejs"]:
            for ext in (".cmd", ".exe", ""):
                p = os.path.join(d, "openclaw" + ext)
                if os.path.exists(p):
                    return p
        return None
    found = shutil.which("openclaw")
    if found:
        return found
    for d in ["/usr/local/bin", "/usr/bin", os.path.expanduser("~/.npm-global/bin"), "/root/.npm-global/bin"]:
        p = os.path.join(d, "openclaw")
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _is_gateway_running() -> bool:
    """Return True if the openclaw gateway is reachable by probing its HTTP port."""
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:18789/", timeout=3)
        return True
    except Exception:
        pass
    # Gateway returns non-200 but still responds — check if port is open
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 18789), timeout=3)
        s.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _load_existing_config() -> dict:
    """Read current openclaw.json and return a summary dict for the UI."""
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    summary = {"provider": "", "model": "", "bot_token_set": False}
    if not cfg_path.exists():
        return summary
    try:
        cfg = json.loads(cfg_path.read_text())
        summary["model"] = (
            cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
        )
        if summary["model"]:
            parts = summary["model"].split("/", 1)
            summary["provider"] = parts[0] if len(parts) == 2 else ""
        tg = cfg.get("channels", {}).get("telegram", {})
        summary["bot_token_set"] = bool(tg.get("botToken", ""))
        summary["telegram_enabled"] = tg.get("enabled", False)
    except Exception:
        pass
    return summary


@app.route("/", methods=["GET"])
def index():
    """Serve the setup HTML page, or the dashboard if already configured."""
    try:
        configured = (Path.home() / ".openclaw" / ".configured").exists()
        existing = _load_existing_config() if configured else {}
        return render_template("index.html", is_pi=IS_PI, configured=configured, existing=existing)
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        return _error("Could not render setup page. Check that templates/index.html exists.", 500)


@app.route("/save", methods=["POST"])
def save():
    """
    Receive JSON form data, validate it, and hand it off to apply_config.py.
    """
    try:
        data = request.get_json(silent=True)
        if data is None:
            return _error("Request body must be valid JSON with Content-Type: application/json.")

        validation_error = _validate_save_payload(data)
        if validation_error:
            return _error(validation_error)

        # Normalise provider to lowercase before forwarding.
        data["llm_provider"] = data["llm_provider"].strip().lower()

        # Normalise model if provided.
        if data.get("llm_model"):
            data["llm_model"] = data["llm_model"].strip()

        # Resolve base_url for non-custom providers so apply_config doesn't
        # need to know about PROVIDER_URLS.
        if data["llm_provider"] != "custom":
            data["llm_base_url"] = PROVIDER_URLS[data["llm_provider"]]

        ok, message = _run_apply_config(data)
        if not ok:
            return _error(message, 500)

        return jsonify({"ok": True, "message": message})

    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        return _error("Unexpected server error — check server logs.", 500)


@app.route("/status", methods=["GET"])
def status():
    """Return current platform / setup status as JSON."""
    try:
        configured = (Path.home() / ".openclaw" / ".configured").exists()

        return jsonify(
            {
                "ok": True,
                "is_pi": IS_PI,
                "architecture": platform.machine(),
                "python_version": platform.python_version(),
                "configured": configured,
                "apply_config_path": APPLY_CONFIG_PATH,
            }
        )
    except Exception as exc:
        print(traceback.format_exc(), file=sys.stderr)
        return _error(str(exc), 500)


@app.route("/test-telegram", methods=["GET"])
def test_telegram():
    """
    Verify a Telegram bot token by calling the getMe endpoint.

    Query param: token
    """
    try:
        token = request.args.get("token", "").strip()
        if not token:
            return _error("Query parameter 'token' is required.")

        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=10)
        body = resp.json()

        if resp.ok and body.get("ok"):
            username = body.get("result", {}).get("username", "")
            return jsonify({"ok": True, "bot_username": f"@{username}"})

        description = body.get("description", "Unknown error from Telegram API.")
        return jsonify({"ok": False, "error": description})

    except requests.exceptions.ConnectionError:
        return jsonify({"ok": False, "error": "Could not reach api.telegram.org — check network connectivity."})
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Request to Telegram API timed out."})
    except Exception as exc:
        print(traceback.format_exc(), file=sys.stderr)
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/test-llm", methods=["GET"])
def test_llm():
    """
    Basic sanity-check for LLM credentials: verify provider is known and key
    is non-empty.  No network call is made here — a real connectivity test
    would require provider-specific request shapes.

    Query params: provider, key
    """
    try:
        provider = request.args.get("provider", "").strip().lower()
        key = request.args.get("key", "").strip()

        if not provider:
            return jsonify({"ok": False, "error": "Query parameter 'provider' is required."})

        if provider not in KNOWN_PROVIDERS:
            return jsonify(
                {
                    "ok": False,
                    "error": f"Unknown provider '{provider}'. Must be one of: {', '.join(sorted(KNOWN_PROVIDERS))}.",
                }
            )

        if not key:
            return jsonify({"ok": False, "error": "Query parameter 'key' must not be empty."})

        return jsonify({"ok": True})

    except Exception as exc:
        print(traceback.format_exc(), file=sys.stderr)
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/pairing/list", methods=["GET"])
def pairing_list():
    """Return pending Telegram pairing requests."""
    exe = _find_openclaw()
    if not exe:
        return _error("openclaw not found.", 500)
    try:
        result = subprocess.run(
            [exe, "pairing", "list", "telegram", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        # Try JSON output first; fall back to parsing table output
        try:
            data = json.loads(result.stdout.strip())
            return jsonify({"ok": True, "requests": data})
        except Exception:
            pass

        # Parse table output: extract Code and username columns
        requests = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("│") and "│" in line[1:]:
                cols = [c.strip() for c in line.strip("│").split("│")]
                if len(cols) >= 3 and cols[0] and cols[0] not in ("Code", "─"):
                    code = cols[0].strip()
                    if code and not code.startswith("─") and code != "Code":
                        try:
                            meta = json.loads(cols[2]) if cols[2] else {}
                        except Exception:
                            meta = {}
                        requests.append({
                            "code": code,
                            "username": meta.get("username", cols[1].strip()),
                            "firstName": meta.get("firstName", ""),
                        })
        return jsonify({"ok": True, "requests": requests})
    except subprocess.TimeoutExpired:
        return _error("Command timed out.", 500)
    except Exception as exc:
        print(traceback.format_exc(), file=sys.stderr)
        return _error(str(exc), 500)


@app.route("/pairing/approve", methods=["POST"])
def pairing_approve():
    """Approve a Telegram pairing request by code."""
    exe = _find_openclaw()
    if not exe:
        return _error("openclaw not found.", 500)
    try:
        data = request.get_json(silent=True) or {}
        code = data.get("code", "").strip().upper()
        if not code:
            return _error("code is required.")
        result = subprocess.run(
            [exe, "pairing", "approve", "telegram", code],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "Approval failed.").strip()
            return _error(err, 400)
        return jsonify({"ok": True, "message": f"Pairing code {code} approved."})
    except subprocess.TimeoutExpired:
        return _error("Command timed out.", 500)
    except Exception as exc:
        print(traceback.format_exc(), file=sys.stderr)
        return _error(str(exc), 500)


@app.route("/gateway/status", methods=["GET"])
def gateway_status():
    """Return whether the openclaw gateway is currently running."""
    running = _is_gateway_running()
    return jsonify({"ok": True, "running": running})


@app.route("/gateway/start", methods=["POST"])
def gateway_start():
    """Start the openclaw gateway as a detached background process."""
    if _is_gateway_running():
        return jsonify({"ok": True, "message": "Gateway is already running."})

    exe = _find_openclaw()
    if not exe:
        return _error("openclaw executable not found. Make sure it is installed.", 500)

    try:
        env = os.environ.copy()
        env["OPENCLAW_NO_RESPAWN"] = "1"

        if sys.platform == "win32":
            subprocess.Popen(
                [exe, "gateway"],
                env=env,
                creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP),
                close_fds=True,
            )
        else:
            subprocess.Popen(
                [exe, "gateway"],
                env=env,
                close_fds=True,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as exc:
        print(traceback.format_exc(), file=sys.stderr)
        return _error(f"Failed to start gateway: {exc}", 500)

    return jsonify({"ok": True, "message": "Gateway started."})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print(
        f"[setup_server] Starting OpenClaw Easy Setup server on http://0.0.0.0:{PORT}",
        file=sys.stderr,
    )
    print(
        f"[setup_server] Platform: {platform.machine()} | IS_PI={IS_PI}",
        file=sys.stderr,
    )

    try:
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    except OSError as exc:
        # Errno 98 (Linux) / 10048 (Windows) — address already in use.
        if "Address already in use" in str(exc) or exc.errno in (98, 10048):
            print(
                f"\n[setup_server] ERROR: Port {PORT} is already in use.\n"
                "  • Stop the process that is currently using it, or\n"
                "  • Change PORT in setup_server.py to a free port.\n",
                file=sys.stderr,
            )
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
