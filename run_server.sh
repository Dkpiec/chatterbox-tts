#!/bin/bash
# Chatterbox TTS container entrypoint: venv install (once) + HTTP server.
set -e
exec > "$0.boot.log" 2>&1   # boot diagnostics visible from host via bind mount (same-path mount)
echo "=== boot start $(date -u) ==="
BASE=$(cd "$(dirname "$0")" && pwd)
echo "BASE=$BASE"
VENV=$BASE/venv
MARKER=$BASE/.installed
export HF_HOME=$BASE/hf_cache
mkdir -p "$HF_HOME"

# runtime libs torch needs (slim image lacks them)
if ! ldconfig -p | grep -q libgomp.so.1; then
  apt-get update -qq && apt-get install -y -qq libgomp1 > /dev/null || true
fi

if [ ! -f "$MARKER" ]; then
  echo "first boot: installing chatterbox-tts (torch CPU) into venv..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu "chatterbox-tts==0.1.7"
  touch "$MARKER"
  echo "install done"
fi

exec "$VENV/bin/python" "$BASE/server.py"
