# ── Base: Node.js 22 LTS on Debian slim ──────────────────────────────────────
FROM node:22-slim

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    git \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
RUN pip3 install --break-system-packages flask requests

# ── OpenClaw CLI ──────────────────────────────────────────────────────────────
RUN npm install -g openclaw

# ── App files ─────────────────────────────────────────────────────────────────
WORKDIR /app
COPY . .

# ── Expose setup wizard port ──────────────────────────────────────────────────
EXPOSE 7070

# ── Start the setup server ────────────────────────────────────────────────────
CMD ["python3", "setup_server.py"]
