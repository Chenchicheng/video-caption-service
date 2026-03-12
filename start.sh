#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

pip install -q -r requirements.txt

set -a
source .env
set +a

exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --access-log \
    2>&1 | tee -a "$LOG_DIR/service.log"
