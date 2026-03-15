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
    """
    try:
        result = subprocess.run(
            ["python3", APPLY_CONFIG_PATH],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return False, f"apply_config.py not found at {APPLY_CONFIG_PATH}."
    except subprocess.TimeoutExpired:
        return False, "apply_config.py timed out after 120 seconds."

    if result.returncode != 0:
        # Surface stderr but never echo back the raw payload (may contain keys).
        stderr_snippet = (result.stderr or "").strip()[:400]
        print(
            f"[setup_server] apply_config exited {result.returncode}: {stderr_snippet}",
            file=sys.stderr,
        )
        return False, f"apply_config failed (exit {result.returncode}): {stderr_snippet or 'no error output'}"

    return True, "Setup complete"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/", methods=["GET"])
def index():
    """Serve the setup HTML page."""
    try:
        return render_template("index.html", is_pi=IS_PI)
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
        config_dir = _BASE_DIR / "config"
        configured = (config_dir / ".setup_complete").exists()

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
