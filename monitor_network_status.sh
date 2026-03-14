#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK_HOST="${1:-172.18.72.16}"
POLL_SECONDS="${POLL_SECONDS:-5}"
STATE_FILE="$APP_DIR/network_status.txt"
LOG_FILE="$APP_DIR/network_status.log"
LOG_TAG="hioki-network"

check_network() {
    if ip route | grep -q '^default'; then
        if ping -n -c 1 -W 1 "$CHECK_HOST" >/dev/null 2>&1; then
            echo "connected"
        else
            echo "disconnected"
        fi
    else
        echo "disconnected"
    fi
}

last_status=""
while true; do
    current_status="$(check_network)"

    if [[ "$current_status" != "$last_status" ]]; then
        timestamp="$(date '+%F %T')"
        echo "$current_status" > "$STATE_FILE"
        echo "$timestamp,$current_status,$CHECK_HOST" >> "$LOG_FILE"
        logger -t "$LOG_TAG" "Network ${current_status} (host=${CHECK_HOST})"
        last_status="$current_status"
    fi

    sleep "$POLL_SECONDS"
done
