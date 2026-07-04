#!/bin/zsh
# Launch Signal Radar and open it in the browser.
cd "$(dirname "$0")"
.venv/bin/python server.py &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null" EXIT INT TERM
sleep 1.5
open "http://localhost:8765"
wait $SERVER_PID
