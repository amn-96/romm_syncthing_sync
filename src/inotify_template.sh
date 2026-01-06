#!/bin/bash
# Photography Organizer Entrypoint
# Replaces the systemd watch_das_dir.sh service
# Monitors DAS Photography/__ORGANIZED/ directory and triggers image sorting

set -e

# Configuration
DIRECTORY="/mnt/TDAS/Photography/__ORGANIZED/"
RUN_PY_SCRIPT="/app/src/pictures_toeditsfolder_g9.py"
DELAY_SECONDS=60
TIMER_PID=""

echo "$(date +"%Y-%m-%d %H:%M:%S"): Photography Organizer initialized - watching $DIRECTORY"

# Function to handle graceful shutdown
cleanup() {
    echo "$(date +"%Y-%m-%d %H:%M:%S"): Shutting down gracefully..."
    if [ -n "$TIMER_PID" ] && kill -0 "$TIMER_PID" 2>/dev/null; then
        kill "$TIMER_PID" 2>/dev/null || true
    fi
    exit 0
}

# Trap SIGTERM and SIGINT for graceful shutdown
trap cleanup SIGTERM SIGINT

# Main loop
while true; do
    # Run inotifywait recursively on the __ORGANIZED photo directory
    inotifywait -q -m -r -e modify,create,delete,moved_to,moved_from "$DIRECTORY" |
    while read -r event; do
        echo "$(date +"%Y-%m-%d %H:%M:%S"): Filesystem Event: $event"

        # If a timer is already running, kill it (reset the countdown)
        if [ -n "$TIMER_PID" ] && kill -0 "$TIMER_PID" 2>/dev/null; then
            kill "$TIMER_PID" 2>/dev/null || true
        fi

        # Start a new timer in the background
        # Parentheses are a SEPARATE SUBSHELL. The "&" at the end runs it in the background rather than sequentially in code
        (
            # Once all events are complete, wait 1 minute for things to wrap up.
            sleep "$DELAY_SECONDS"
            echo "$(date +"%Y-%m-%d %H:%M:%S"): ----------Running $RUN_PY_SCRIPT----------"
            # Run the photo organizer script for immich
            python3 "$RUN_PY_SCRIPT"
            echo "$(date +"%Y-%m-%d %H:%M:%S"): ----------FINISHED----------"
        ) &

        TIMER_PID=$!

    done

done