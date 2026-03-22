# OpenClaw Setup — Changes & Improvements

## Docker Setup

### New Files Added
- `Dockerfile` — Builds the container: Node.js 22 + Python 3 + git + flask/requests + openclaw CLI
- `docker-compose.yml` — Runs the container with port 7070 exposed and a named volume for config persistence
- `.dockerignore` — Excludes shell scripts, git files, and platform installers from the image

### docker-compose.yml
- `restart: no` — Container does NOT auto-restart when Docker starts. Start manually with `docker compose up -d`

---

## Bug Fixes

### apply_config.py
- **Gateway auth** — Changed `gateway.auth.mode` from `none` to `token` with default token `openclaw123`.
  Previously the gateway refused to start with: `Refusing to bind gateway to auto without auth`
- **Gateway self-respawn** — Set `commands.restart` to `False` to stop the gateway relaunching itself after being killed
- **Model selection** — Now uses `config.get("llm_model")` from the submitted form instead of the hardcoded default,
  allowing the user to choose a specific model during setup

### setup_server.py
- **Configured flag path** — Fixed mismatch: was checking `config/.setup_complete` but `apply_config.py` writes
  `~/.openclaw/.configured`. Now both use `~/.openclaw/.configured`
- **Gateway status check** — Replaced slow CLI-based check (`openclaw gateway status` + systemd probe, often timing out)
  with a direct socket probe to port 18789. Fast and accurate
- **`llm_model` passthrough** — Normalises and forwards the selected model to `apply_config.py`

---

## New Features

### Portal: Dashboard (second screen)
When `~/.openclaw/.configured` exists, opening the portal now shows a dashboard instead of the blank setup form:
- Current model and provider
- Telegram bot status (configured / active)
- Gateway status badge (Running / Stopped) — checked via port 18789 probe
- **▶ Start Gateway** button — launches `openclaw gateway` as a detached background process, polls until confirmed
- **Reconfigure** button — returns to the setup form

### Portal: Gateway Start Button
Added to both the success screen and the dashboard:
- Calls `POST /gateway/start` — starts `openclaw gateway` with `OPENCLAW_NO_RESPAWN=1`
- Polls `/gateway/status` every second until running, then updates badge to ● Running
- Button disables once gateway is confirmed running

### Portal: Telegram Pairing
Added pairing approval UI to both the success screen and the dashboard:
- `GET /pairing/list` — fetches pending pairing requests from `openclaw pairing list telegram`
- `POST /pairing/approve` — runs `openclaw pairing approve telegram <code>`
- Pending requests shown as cards with an **Approve** button per request
- Manual code input field for entering codes received via Telegram
- Section auto-hides after all requests are approved (re-checks list after each approval)

### Portal: Model Selection
Added a **Model** dropdown to the setup form (below the provider grid):
- Dropdown updates automatically when provider is switched
- Available models per provider:

| Provider  | Models |
|-----------|--------|
| Gemini    | 2.0 Flash Lite (Free), 1.5 Flash (Free), 1.5 Pro, 2.5 Pro, 2.0 Flash |
| OpenAI    | GPT-4o Mini, GPT-4o, GPT-4 Turbo, O1 Mini, O1 |
| Grok      | Grok 2 Mini, Grok 2, Grok 3 |
| DeepSeek  | DeepSeek Chat (V3), DeepSeek Reasoner (R1) |
| Anthropic | Haiku 4.5, Sonnet 4.6, Opus 4.6, Claude 3.5 Sonnet |
| Custom    | Default (gpt-4o compatible) |

### New API Endpoints (setup_server.py)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/gateway/status` | GET | Returns `{ running: true/false }` via socket probe |
| `/gateway/start` | POST | Starts openclaw gateway as detached process |
| `/pairing/list` | GET | Returns pending Telegram pairing requests |
| `/pairing/approve` | POST | Approves a pairing request by code |

---

## How to Run

```bash
# Start
cd "Pi projects/openclaw-setup"
docker compose up -d

# Rebuild after changes
docker compose up --build -d

# Start gateway (inside container)
docker exec openclaw-setup sh -c "OPENCLAW_NO_RESPAWN=1 openclaw gateway > /tmp/gateway.log 2>&1 &"

# Check gateway log
docker exec openclaw-setup sh -c "cat /tmp/gateway.log"

# Stop everything
docker compose down
```

## Setup Portal
Open `http://localhost:7070` in your browser.

- **First run** — fill in provider, model, API key, Telegram bot token → Save & Start Agent
- **After setup** — dashboard shows gateway status and pairing requests
- **Reconfigure** — click Reconfigure on the dashboard to change model/API key/token
