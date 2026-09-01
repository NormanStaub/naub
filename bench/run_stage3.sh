#!/bin/bash
# Bench runner: deploy the Stage 3 test and log everything to a timestamped file.
set -e
PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
[ -z "$PORT" ] && { echo "No board found - is it plugged in?"; exit 1; }
[ -d /Volumes/CIRCUITPY ] || { echo "CIRCUITPY not mounted - replug the board."; exit 1; }
LOG="$HOME/naub-module-test/stage3-$(date +%Y%m%d-%H%M%S).log"
cp "$(dirname "$0")/stage3_test.py" /Volumes/CIRCUITPY/code.py && sync
echo "Deployed. Port: $PORT   Log: $LOG"
echo "Follow the prompts. ENTER skips a step. Ctrl-A then K quits screen."
sleep 2
exec script -q "$LOG" screen "$PORT" 115200
