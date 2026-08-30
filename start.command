#!/bin/zsh
set -e

cd "${0:A:h}"

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating Second Hand Smoke's private Python environment…"
  python3 -m venv .venv
fi

if ! .venv/bin/python -c 'import flask, xxhash' 2>/dev/null; then
  echo "Installing required packages…"
  .venv/bin/pip install -r requirements.txt
fi

if ! .venv/bin/python -c 'import numpy' 2>/dev/null; then
  echo "Installing optional waveform-sync support…"
  if ! .venv/bin/pip install 'numpy>=2.0,<3'; then
    echo "Waveform sync is unavailable until NumPy can be installed."
    echo "Verified offload, metadata, timecode sync and proxies still work."
  fi
fi

if ! .venv/bin/python -c 'import PIL' 2>/dev/null; then
  echo "Installing optional dailies-slate support…"
  if ! .venv/bin/pip install 'Pillow>=11,<13'; then
    echo "Phase 6 dailies are unavailable until Pillow can be installed."
    echo "Verified offload and every earlier phase still work."
  fi
fi

if ! command -v ffprobe >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Note: ffmpeg/ffprobe was not found. Copies remain safe, but metadata, proxies, waveform sync and dailies will be unavailable."
  echo "Install ffmpeg before the shoot to enable durations, timecode, proxies, waveform sync and dailies."
fi

echo "Starting Second Hand Smoke · A CnF Works Project…"
SMOKER_PORT="${DIT_PORT:-3838}"
(sleep 1; open "http://127.0.0.1:${SMOKER_PORT}") &
exec .venv/bin/python app.py
