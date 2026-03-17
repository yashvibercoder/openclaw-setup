#!/usr/bin/env python3
"""
launch.py — OpenClaw Easy Setup entry point for Windows, macOS, and Linux.

Run this file once. It will:
  1. Install Python dependencies (flask, requests)
  2. Ensure Node.js >= 22 is present, installing if needed
  3. Ensure the OpenClaw CLI is installed globally
  4. Start setup_server.py
  5. Open the setup wizard in your browser
"""

import os
import sys
import subprocess
import time
import urllib.request
import urllib.error
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform flags
# ---------------------------------------------------------------------------
IS_WINDOWS = sys.platform == "win32"
IS_MAC     = sys.platform == "darwin"
IS_LINUX   = sys.platform.startswith("linux")

SCRIPT_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Pretty output helpers
# ---------------------------------------------------------------------------
DIVIDER = "━" * 39


def _banner_open() -> None:
    print(DIVIDER)
    print("  🤖 OpenClaw Setup")
    print("  Getting everything ready for you...")
    print(DIVIDER)


def _banner_ready() -> None:
    print(DIVIDER)
    print("  Browser opening at http://localhost:7070")
    print("  Fill in your API key and Telegram token")
    print("  to complete setup.")
    print()
    print("  Press Ctrl+C here to stop the server.")
    print(DIVIDER)


def _ok(msg: str) -> None:
    print(f"[✓] {msg}")


def _info(msg: str) -> None:
    print(f"[↓] {msg}")


def _fail(msg: str) -> None:
    print(f"[✗] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Step 1 — Python dependencies
# ---------------------------------------------------------------------------

def _ensure_pip() -> None:
    """Ensure pip is available; install it if not."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        capture_output=True,
    )
    if result.returncode == 0:
        return  # pip is already present

    # Try ensurepip (built into Python >= 3.4, but may be disabled on some distros)
    ensurepip = subprocess.run(
        [sys.executable, "-m", "ensurepip", "--upgrade"],
        capture_output=True,
    )
    if ensurepip.returncode == 0:
        return

    # On Debian/Ubuntu/Mint, ensurepip may be stripped — install via apt
    if IS_LINUX:
        _info("pip not found — installing python3-pip via apt-get...")
        try:
            subprocess.run(
                ["sudo", "apt-get", "install", "-y", "python3-pip"],
                check=True,
            )
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    _fail(
        "pip is not available and could not be installed automatically.\n"
        "  On Debian/Ubuntu/Mint: sudo apt-get install python3-pip\n"
        "  On Fedora/RHEL:        sudo dnf install python3-pip\n"
        "  Then re-run this script."
    )
    sys.exit(1)


def _pip_install(*packages: str) -> bool:
    """
    Try to install packages via pip, handling common Linux restrictions.
    Returns True on success.
    Attempt order:
      1. Normal install
      2. --break-system-packages  (PEP 668 / Ubuntu 23.04+ / Mint 22+)
      3. --user install
    """
    base_cmd = [sys.executable, "-m", "pip", "install", "-q", *packages]
    for extra in ([], ["--break-system-packages"], ["--user"]):
        result = subprocess.run(base_cmd + extra, capture_output=True, text=True)
        if result.returncode == 0:
            return True
        # Stop trying --break-system-packages / --user on non-externally-managed errors
        if "externally-managed-environment" not in result.stderr and extra:
            break
    return False


def _install_python_deps() -> None:
    """Install flask and requests; handle pip-absent and externally-managed distros."""
    _ensure_pip()
    if not _pip_install("flask", "requests"):
        _fail(
            "Could not install Python dependencies (flask, requests).\n"
            "  Try manually: pip install flask requests\n"
            "  Or:           pip install --user flask requests"
        )
        sys.exit(1)
    _ok("Python dependencies ready")


# ---------------------------------------------------------------------------
# Step 2 — Node.js
# ---------------------------------------------------------------------------

def _get_node_version() -> "int | None":
    """Return the installed Node.js major version, or None if not found."""
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        # Output is like "v22.3.0"
        raw = result.stdout.strip().lstrip("v")
        major = int(raw.split(".")[0])
        return major
    except (FileNotFoundError, ValueError):
        return None


def _node_version_string() -> str:
    """Return the full node version string for display, e.g. '22.3.0'."""
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip().lstrip("v")
    except Exception:
        return "unknown"


def _refresh_windows_path() -> None:
    """
    Inject known Node/npm locations into PATH for this process.
    Reads both HKLM and HKCU so user-level installs are found too.
    """
    if not IS_WINDOWS:
        return

    extra_dirs = [
        r"C:\Program Files\nodejs",
        os.path.expandvars(r"%ProgramFiles%\nodejs"),
        os.path.expandvars(r"%APPDATA%\npm"),
        os.path.expandvars(r"%ProgramFiles(x86)%\nodejs"),
    ]

    # Also read both HKLM and HKCU PATH from registry
    try:
        import winreg
        for hive, subkey in [
            (winreg.HKEY_LOCAL_MACHINE,
             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment"),
        ]:
            try:
                key = winreg.OpenKey(hive, subkey)
                val, _ = winreg.QueryValueEx(key, "Path")
                winreg.CloseKey(key)
                extra_dirs.extend(val.split(os.pathsep))
            except Exception:
                pass
    except Exception:
        pass

    current = os.environ.get("PATH", "")
    combined = os.pathsep.join(extra_dirs) + os.pathsep + current
    os.environ["PATH"] = combined


def _find_win_exe(name: str) -> "str | None":
    """
    Find an executable on Windows, trying .cmd and .exe variants and
    probing known Node/npm install directories.
    """
    import shutil
    for candidate in (name, name + ".cmd", name + ".exe"):
        found = shutil.which(candidate)
        if found:
            return found
    # Probe common locations directly
    for d in [
        r"C:\Program Files\nodejs",
        os.path.expandvars(r"%ProgramFiles%\nodejs"),
        os.path.expandvars(r"%APPDATA%\npm"),
    ]:
        for ext in (".cmd", ".exe", ""):
            p = os.path.join(d, name + ext)
            if os.path.exists(p):
                return p
    return None


def _install_node_windows() -> None:
    """Install Node.js on Windows via winget."""
    cmd = [
        "winget", "install",
        "--id", "OpenJS.NodeJS.LTS",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
    ]
    _info("Installing Node.js via winget (this may take a minute)...")
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        _fail(
            "winget is not available on this system.\n"
            "  Please install Node.js >= 22 manually from https://nodejs.org\n"
            "  then re-run this script."
        )
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        _fail(
            f"winget exited with code {exc.returncode}.\n"
            "  Please install Node.js >= 22 manually from https://nodejs.org\n"
            "  then re-run this script."
        )
        sys.exit(1)
    _refresh_windows_path()


def _install_node_mac() -> None:
    """Install Node.js on macOS via Homebrew, or fall back to the pkg installer."""
    # Prefer Homebrew
    brew = subprocess.run(["which", "brew"], capture_output=True, text=True)
    if brew.returncode == 0 and brew.stdout.strip():
        _info("Installing Node.js via Homebrew...")
        try:
            subprocess.run(["brew", "install", "node"], check=True)
            return
        except subprocess.CalledProcessError as exc:
            _fail(
                f"brew install node failed (exit {exc.returncode}).\n"
                "  Falling back to direct download..."
            )

    # Fall back: download the macOS .pkg installer and run it silently
    _info("Downloading Node.js macOS installer...")
    pkg_url = "https://nodejs.org/dist/v22.14.0/node-v22.14.0.pkg"
    pkg_path = Path("/tmp/node_installer.pkg")
    try:
        urllib.request.urlretrieve(pkg_url, str(pkg_path))
    except Exception as exc:
        _fail(
            f"Failed to download Node.js installer: {exc}\n"
            "  Please install Node.js >= 22 manually from https://nodejs.org\n"
            "  then re-run this script."
        )
        sys.exit(1)

    _info("Running Node.js installer (may prompt for your password)...")
    try:
        subprocess.run(
            ["sudo", "installer", "-pkg", str(pkg_path), "-target", "/"],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        _fail(
            f"installer exited with code {exc.returncode}.\n"
            "  Please install Node.js >= 22 manually from https://nodejs.org\n"
            "  then re-run this script."
        )
        sys.exit(1)


def _install_node_linux() -> None:
    """Install Node.js on Linux via the NodeSource setup script."""
    _info("Installing Node.js via NodeSource (requires sudo)...")
    # Step 1: run the NodeSource setup script
    curl_cmd = [
        "bash", "-c",
        "curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -",
    ]
    try:
        subprocess.run(curl_cmd, check=True)
    except FileNotFoundError:
        _fail(
            "curl or bash is not available.\n"
            "  Please install Node.js >= 22 manually from https://nodejs.org\n"
            "  then re-run this script."
        )
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        _fail(
            f"NodeSource setup script failed (exit {exc.returncode}).\n"
            "  Please install Node.js >= 22 manually from https://nodejs.org\n"
            "  then re-run this script."
        )
        sys.exit(1)

    # Step 2: install nodejs package
    try:
        subprocess.run(
            ["sudo", "apt-get", "install", "-y", "nodejs"],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        _fail(
            f"apt-get install nodejs failed (exit {exc.returncode}).\n"
            "  Please install Node.js >= 22 manually from https://nodejs.org\n"
            "  then re-run this script."
        )
        sys.exit(1)


def _ensure_node() -> None:
    """Guarantee Node.js >= 22 is present; install if not."""
    # Always refresh PATH on Windows first so npm is findable even if
    # Node was installed in a previous session (not by this script run).
    if IS_WINDOWS:
        _refresh_windows_path()

    major = _get_node_version()

    if major is not None and major >= 22:
        _ok(f"Node.js {_node_version_string()} ready")
        return

    if major is not None:
        _info(
            f"Node.js {_node_version_string()} found but version {major} < 22. "
            "Upgrading..."
        )
    else:
        _info("Node.js not found. Installing...")

    if IS_WINDOWS:
        _install_node_windows()
    elif IS_MAC:
        _install_node_mac()
    elif IS_LINUX:
        _install_node_linux()
    else:
        _fail(
            "Unsupported platform for automatic Node.js installation.\n"
            "  Please install Node.js >= 22 from https://nodejs.org then re-run."
        )
        sys.exit(1)

    # Verify installation succeeded
    major = _get_node_version()
    if major is None or major < 22:
        detected = f"v{_node_version_string()}" if major is not None else "not found"
        _fail(
            f"Node.js installation appears to have failed (detected: {detected}).\n"
            "  Please install Node.js >= 22 manually from https://nodejs.org\n"
            "  then re-run this script."
        )
        sys.exit(1)

    _ok(f"Node.js {_node_version_string()} ready")


# ---------------------------------------------------------------------------
# Step 3 — OpenClaw CLI
# ---------------------------------------------------------------------------

def _openclaw_installed() -> bool:
    """Return True if the openclaw CLI is reachable and exits cleanly."""
    import shutil
    exe = _find_win_exe("openclaw") if IS_WINDOWS else shutil.which("openclaw")
    if not exe:
        return False
    try:
        result = subprocess.run([exe, "--version"], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def _add_npm_global_bin_to_path() -> None:
    """On Windows, ensure the npm global bin dir is on PATH for this process."""
    if not IS_WINDOWS:
        return
    try:
        result = subprocess.run(
            ["npm", "prefix", "-g"],
            capture_output=True,
            text=True,
            check=True,
        )
        npm_prefix = result.stdout.strip()
        npm_bin = npm_prefix + os.sep + "bin"
        os.environ["PATH"] = npm_bin + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        # Non-fatal; openclaw might be on PATH already
        pass


def _ensure_openclaw() -> None:
    """Guarantee the OpenClaw CLI is installed globally."""
    if _openclaw_installed():
        _ok("OpenClaw ready")
        return

    _info("Installing OpenClaw...")
    import shutil
    npm_exe = _find_win_exe("npm") if IS_WINDOWS else shutil.which("npm")
    if not npm_exe:
        _fail(
            "npm was not found. Please open a new terminal and re-run,\n"
            "  or install Node.js >= 22 manually from https://nodejs.org"
        )
        sys.exit(1)
    try:
        subprocess.run([npm_exe, "install", "-g", "openclaw"], check=True)
    except subprocess.CalledProcessError as exc:
        _fail(
            f"npm install -g openclaw failed (exit {exc.returncode}).\n"
            "  Check your internet connection and try again."
        )
        sys.exit(1)

    # On Windows, npm's global bin dir may not be on PATH yet in this process
    _add_npm_global_bin_to_path()

    if not _openclaw_installed():
        _fail(
            "OpenClaw was installed but cannot be found on PATH.\n"
            "  Try opening a new terminal and running: openclaw --version\n"
            "  If that works, re-run this script."
        )
        sys.exit(1)

    _ok("OpenClaw installed")


# ---------------------------------------------------------------------------
# Step 4 — Setup server
# ---------------------------------------------------------------------------

def _start_server() -> subprocess.Popen:
    """Launch setup_server.py and return the Popen handle."""
    server_script = SCRIPT_DIR / "setup_server.py"
    if not server_script.exists():
        _fail(
            f"setup_server.py not found at {server_script}.\n"
            "  Make sure all OpenClaw setup files are in the same directory."
        )
        sys.exit(1)

    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return proc


def _wait_for_server(proc: subprocess.Popen, url: str = "http://localhost:7070") -> None:
    """
    Poll *url* until the server responds or we time out (~10 s).
    Exits the process if the server never comes up.
    """
    for _ in range(20):
        # Check if the process died already
        if proc.poll() is not None:
            break
        try:
            urllib.request.urlopen(url, timeout=1)
            return  # server is up
        except Exception:
            time.sleep(0.5)

    # Server didn't start in time — gather stderr and abort
    try:
        stderr_bytes = proc.stderr.read() if proc.stderr else b""
        stderr_text = stderr_bytes.decode(errors="replace").strip()
    except Exception:
        stderr_text = "(could not read server output)"

    _fail(
        "Setup server failed to start within 10 seconds.\n"
        + (f"  Server output:\n{stderr_text}" if stderr_text else "  No output captured.")
    )
    proc.terminate()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        _banner_open()

        # 1. Python deps
        _install_python_deps()

        # 2. Node.js
        _ensure_node()

        # 3. OpenClaw
        _ensure_openclaw()

        # 4. Start server
        server_proc = _start_server()
        _wait_for_server(server_proc)
        _ok("Setup server running on http://localhost:7070")

        # 5. Open browser
        webbrowser.open("http://localhost:7070")
        _banner_ready()

        # 6. Keep alive
        try:
            server_proc.wait()
        except KeyboardInterrupt:
            print("\n[•] Shutting down setup server...")
            server_proc.terminate()
            server_proc.wait()
            _ok("Done.")

    except SystemExit:
        raise  # propagate intentional exits
    except Exception as exc:  # noqa: BLE001
        _fail(
            f"Unexpected error: {exc}\n"
            "  If this keeps happening, please file an issue at\n"
            "  https://github.com/openclaw/openclaw/issues"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
