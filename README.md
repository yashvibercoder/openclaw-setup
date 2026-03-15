# OpenClaw Easy Setup

## What Is This?
OpenClaw Easy Setup is an automated installer that takes a complete beginner from unboxing (like a Raspberry Pi) or downloading on your Mac/Linux/Windows machine to a fully working OpenClaw AI agent in under 5 minutes, with zero terminal usage!

## What You'll Need
- **An LLM API key:** You can use Gemini, OpenAI, Grok, DeepSeek, Anthropic, or even custom OpenAI-compatible models.
- **A Telegram bot token:**
  1. Open Telegram and search for `@BotFather`.
  2. Send `/newbot` and follow the prompts to create a name and username.
  3. Copy the "HTTP API Token" provided by BotFather.
- **The hardware/computer you're installing on.**

## Platform Quickstart

### Raspberry Pi
1. Flash Raspberry Pi OS Lite 64-bit to your SD card or NVMe.
2. Follow the prompt instructions when you connect to it to run the installer script.
3. Connect to the `OpenClaw-Setup` WiFi network on your phone or laptop.
4. Fill in the setup form — your AI agent will be live in seconds!

### macOS
1. Download and unzip the package.
2. Double-click `install_mac.sh` (or run it in Terminal).
3. A browser window will open automatically.

### Linux
1. Download and unzip the package.
2. Run `bash install_linux.sh` in your terminal.
3. A browser window will open automatically.

### Windows
1. Download and unzip the package.
2. Right-click `install_windows.bat` and select **Run as Administrator**.
3. A browser window will open automatically.

## Setup Wizard Fields
- **WiFi SSID + Password (Pi only):** Enter the credentials for your home WiFi network so the Pi can connect to the internet.
- **LLM Provider:** Pick from the available options (Google Gemini is recommended if you want a free tier).
- **API Key:** Paste the API key for your chosen provider. Links are provided in the wizard for where to find it.
- **Telegram Bot Token:** Paste the token you copied from `@BotFather`.

## After Setup
- Your Telegram bot is now live! Send it a message on Telegram to test it.
- The agent runs 24/7 in the background.
- On Raspberry Pi: OpenClaw will start automatically even after reboots.

## Supported LLM Providers

| Provider | Free Tier? | Notes |
|---|---|---|
| Google Gemini | Yes | Recommended for beginners |
| OpenAI / ChatGPT | No | Pay-as-you-go |
| xAI Grok | Limited | X/Twitter ecosystem |
| DeepSeek | Yes | Good free tier |
| Anthropic Claude | No | Pay-as-you-go |
| Custom | — | Bring your own OpenAI-compatible API |

## Troubleshooting
- **Setup WiFi not appearing (Pi):** Wait 60 seconds and try rebooting.
- **Browser doesn't open:** Open your browser and go to `http://localhost:7070` manually.
- **Telegram bot not responding:** Check your token and run `openclaw doctor` in the terminal for diagnostics.
- **Can't connect to home WiFi after setup:** The SSID or password might be incorrect. You may need to retry the setup process.

## Hardware Recommendations (Raspberry Pi)
We highly recommend using a **Raspberry Pi 5 8GB** with an **NVMe SSD** for optimal performance.
> **Warning**: Do NOT use a Raspberry Pi 4 4GB or a standard SD card for primary setups. It's too slow and the SD card will wear out quickly.

## License
MIT
