#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
DESKTOP="$ROOT/desktop"
VENV="$BACKEND/.venv"
PYTHON="${PYTHON:-python3}"
MODE="${1:-desktop}"

log() { printf '[ForgeAgent] %s\n' "$*"; }
fail() { printf '[ForgeAgent] %s\n' "$*" >&2; exit 1; }

check_python() {
  command -v "$PYTHON" >/dev/null 2>&1 || fail "Python 3.11+ is required; set PYTHON=/path/to/python3 if it is not on PATH."
  "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || fail "Python 3.11+ is required (found: $("$PYTHON" --version 2>&1))."
}

ensure_backend() {
  check_python
  if [ ! -x "$VENV/bin/python" ]; then
    log "Creating backend/.venv..."
    rm -rf "$VENV"
    "$PYTHON" -m venv "$VENV" \
      || fail "Could not create a virtual environment. On Debian/Ubuntu install python3-venv."
  fi
  if ! "$VENV/bin/python" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
    log "Installing backend dependencies..."
    "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
    "$VENV/bin/python" -m pip install -r "$BACKEND/requirements.txt"
  fi
  if [ ! -f "$BACKEND/.env" ] && [ -f "$BACKEND/.env.example" ]; then
    cp "$BACKEND/.env.example" "$BACKEND/.env"
    log "Created backend/.env from .env.example; configure the model in the app or edit the file."
  fi
}

ensure_node() {
  command -v node >/dev/null 2>&1 || fail "Node.js 20+ and npm are required for the desktop/web UI."
  local major
  major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" -ge 20 ] || fail "Node.js 20+ is required (found: $(node --version))."
  command -v npm >/dev/null 2>&1 || fail "npm is required."
}

ensure_desktop() {
  ensure_node
  if [ ! -x "$DESKTOP/node_modules/.bin/vite" ]; then
    log "Installing desktop dependencies..."
    (cd "$DESKTOP" && npm ci --no-audit --no-fund)
  fi
}

run_host() {
  cd "$BACKEND"
  exec "$VENV/bin/python" -m forge_agent.server "$@"
}

case "$MODE" in
  desktop)
    ensure_backend
    ensure_desktop
    log "Starting Host and Electron..."
    (cd "$BACKEND" && exec "$VENV/bin/python" -m forge_agent.server) &
    HOST_PID=$!
    trap 'kill "$HOST_PID" 2>/dev/null || true' EXIT INT TERM
    cd "$DESKTOP"
    npm run electron:dev
    ;;
  serve)
    ensure_backend
    shift || true
    run_host "$@"
    ;;
  web)
    ensure_backend
    ensure_desktop
    log "Building the web UI..."
    (cd "$DESKTOP" && npm run build)
    export FORGEAGENT_WEB_DIR="${FORGEAGENT_WEB_DIR:-$DESKTOP/dist}"
    shift || true
    run_host "$@"
    ;;
  *)
    fail "Usage: $0 [desktop|serve|web]"
    ;;
esac
