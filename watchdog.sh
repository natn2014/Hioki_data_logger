#!/bin/bash
# watchdog.sh — Monitor main.py and kill it if the process hangs.
#
# A hung Python/Qt app on Linux typically enters "D" (uninterruptible sleep)
# when blocked on a network or I/O call that never returns.
# This script kills the process after it stays in D state for
# MAX_D_CHECKS × CHECK_INTERVAL seconds (default: 3 × 10 = 30s).
#
# Usage:
#   chmod +x watchdog.sh
#   ./watchdog.sh            # run in foreground
#   ./watchdog.sh &          # run in background
#   nohup ./watchdog.sh &    # run after logout

APP_SCRIPT="main.py"
CHECK_INTERVAL=10       # seconds between each check
MAX_D_CHECKS=3          # consecutive D-state hits before kill  (3 × 10s = 30s)
LOG="$(dirname "$0")/watchdog.log"

# Heartbeat backstop. The app touches this file (~every few seconds) from its Qt
# event loop. The D-state check above reads the process's MAIN thread state and
# structurally MISSES a hang confined to the serial poll worker thread; a stale
# heartbeat catches "app alive but permanently stalled" that D-state cannot.
# Threshold is deliberately generous so the app's own soft recovery gets first
# chance before we force a restart.
HEARTBEAT_FILE="$(dirname "$0")/heartbeat.txt"
MAX_HEARTBEAT_AGE=90    # seconds without a heartbeat before force-restart

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

log "========================================"
log "Watchdog started. Monitoring: $APP_SCRIPT"
log "Check interval : ${CHECK_INTERVAL}s"
log "Kill threshold : ${MAX_D_CHECKS} consecutive D-state checks"

consecutive_d=0

while true; do
    # Get PID of the running app (first match)
    PID=$(pgrep -f "$APP_SCRIPT" | head -1)

    if [ -z "$PID" ]; then
        # Process not running — just wait
        [ $consecutive_d -ne 0 ] && log "Process gone (was being watched)."
        consecutive_d=0
        sleep "$CHECK_INTERVAL"
        continue
    fi

    # Heartbeat staleness check — catches a stalled app whose main thread still
    # reports a normal (S/R) state, so the D-state check below never fires.
    if [ -f "$HEARTBEAT_FILE" ]; then
        NOW=$(date +%s)
        HB_MTIME=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || echo "$NOW")
        HB_AGE=$((NOW - HB_MTIME))
        if [ "$HB_AGE" -ge "$MAX_HEARTBEAT_AGE" ]; then
            log "STALL DETECTED — heartbeat ${HB_AGE}s old (>= ${MAX_HEARTBEAT_AGE}s) — killing PID $PID"
            kill -9 "$PID"
            consecutive_d=0
            sleep "$CHECK_INTERVAL"
            continue
        fi
    fi

    # Read state from /proc — fast, no extra tools needed
    STATE=$(awk '/^State:/{print $2}' /proc/"$PID"/status 2>/dev/null)

    case "$STATE" in
        D)
            # Uninterruptible sleep — typical sign of a blocked/hung process
            consecutive_d=$((consecutive_d + 1))
            log "PID $PID: D-state (blocked/hung) [$consecutive_d / $MAX_D_CHECKS]"

            if [ "$consecutive_d" -ge "$MAX_D_CHECKS" ]; then
                log "HANG DETECTED — killing PID $PID with SIGKILL"
                kill -9 "$PID"
                consecutive_d=0
                log "Killed."
            fi
            ;;
        Z)
            # Zombie — should not happen normally
            log "PID $PID: zombie state — killing"
            kill -9 "$PID"
            consecutive_d=0
            ;;
        "")
            # Process disappeared between pgrep and /proc read
            consecutive_d=0
            ;;
        *)
            # S (sleeping), R (running), I (idle) — all normal
            consecutive_d=0
            ;;
    esac

    sleep "$CHECK_INTERVAL"
done
