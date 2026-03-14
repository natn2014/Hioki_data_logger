#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "Please run with sudo: sudo ./setup_pi_autostart.sh"
    exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-pi}"
CHECK_HOST="${CHECK_HOST:-172.18.72.16}"

chmod +x "$APP_DIR/run_hioki_app.sh"
chmod +x "$APP_DIR/monitor_network_status.sh"

cat > /etc/systemd/system/hioki-app.service <<EOF
[Unit]
Description=HIOKI Logger App
After=graphical.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/${APP_USER}/.Xauthority
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/run_hioki_app.sh
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF

cat > /etc/systemd/system/hioki-network-status.service <<EOF
[Unit]
Description=HIOKI Network Status Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=POLL_SECONDS=5
ExecStart=${APP_DIR}/monitor_network_status.sh ${CHECK_HOST}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hioki-network-status.service
systemctl enable hioki-app.service
systemctl restart hioki-network-status.service
systemctl restart hioki-app.service

echo "Setup complete."
echo "Service status:"
systemctl --no-pager --full status hioki-network-status.service | head -n 20 || true
systemctl --no-pager --full status hioki-app.service | head -n 20 || true

echo "Network state file: ${APP_DIR}/network_status.txt"
echo "Network log file:   ${APP_DIR}/network_status.log"
