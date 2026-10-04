#!/bin/sh
# Take a picture of the live viewer with headless Chrome.
#   tools/screenshot.sh out.png [port]
# Start `python -m jetbot_twin serve --no-open` first.
OUT=${1:-viewer.png}
PORT=${2:-8765}
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --enable-gpu --use-angle=metal --hide-scrollbars \
  --window-size=1500,950 --timeout=20000 --user-data-dir="${TMPDIR:-/tmp}/jetbot-twin-shot" \
  --screenshot="$OUT" "http://127.0.0.1:$PORT/?wait=6000" 2>/dev/null
ls -la "$OUT"
