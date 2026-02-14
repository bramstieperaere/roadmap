#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Defaults ---
PORT=8081
CONFIG=""

# --- Parse arguments ---
usage() {
    echo "Usage: $0 [--port PORT] [--config PATH]"
    echo ""
    echo "Options:"
    echo "  --port PORT      Port to listen on (default: 8081)"
    echo "  --config PATH    Path to config.yaml (default: auto-created next to app/)"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)   PORT="$2"; shift 2 ;;
        --config) CONFIG="$2"; shift 2 ;;
        --help|-h) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

# --- Check Python ---
PYTHON=""
for cmd in python3.13 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -eq 3 ] && [ "$minor" -ge 13 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.13+ is required but not found."
    echo "Please install Python 3.13 or later and ensure it is on your PATH."
    exit 1
fi

echo "Using Python: $($PYTHON --version)"

# --- Create venv if needed ---
VENV_DIR="${SCRIPT_DIR}/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

# --- Install dependencies from vendored wheels ---
VENV_PYTHON="${VENV_DIR}/bin/python"
STAMP="${VENV_DIR}/.vendor-installed"

if [ ! -f "$STAMP" ]; then
    echo "Installing dependencies from vendored packages..."
    "$VENV_PYTHON" -m pip install --no-index --find-links "${SCRIPT_DIR}/vendor" \
        -r "${SCRIPT_DIR}/requirements.txt" --quiet
    touch "$STAMP"
    echo "Dependencies installed."
else
    echo "Dependencies already installed."
fi

# --- Set config path ---
if [ -n "$CONFIG" ]; then
    export ROADMAP_CONFIG_PATH="$CONFIG"
fi

# --- Start the server ---
echo ""
echo "Starting Roadmap on port ${PORT}..."
echo "Open http://localhost:${PORT} in your browser."
echo ""

"$VENV_PYTHON" -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --app-dir "$SCRIPT_DIR"
