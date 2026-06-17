#!/bin/bash
# setup_services.sh — Install and enable hioki-app + hioki-watchdog as systemd services.
# Run once after copying files to the Raspberry Pi:
#   chmod +x setup_services.sh
#   sudo ./setup_services.sh

set -e  # stop on any error

# ── Configuration ────────────────────────────────────────────────────────────
APP_USER="${SUDO_USER:-pi}"                         # the user who ran sudo
APP_DIR="/home/$APP_USER/Hioki_data_logger"
PYTHON="$APP_DIR/.venv/bin/python3"
SERVICE_DIR="/etc/systemd/system"
# ─────────────────────────────────────────────────────────────────────────────

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo:  sudo ./setup_services.sh"
    exit 1
fi

echo ""
echo "=== Hioki Service Setup ==="
echo "User      : $APP_USER"
echo "App dir   : $APP_DIR"
echo "Python    : $PYTHON"
echo ""

# ── Check app directory exists ───────────────────────────────────────────────
if [ ! -d "$APP_DIR" ]; then
    echo "ERROR: App directory not found: $APP_DIR"
    echo "       Copy your project files there first, then re-run this script."
    exit 1
fi

if [ ! -f "$APP_DIR/main.py" ]; then
    echo "ERROR: main.py not found in $APP_DIR"
    exit 1
fi

if [ ! -f "$PYTHON" ]; then
    echo "WARNING: .venv python not found at $PYTHON"
    echo "         Falling back to system python3"
    PYTHON=$(which python3)
fi

# Make scripts executable
chmod +x "$APP_DIR/watchdog.sh"

# ── Write hioki-app.service ──────────────────────────────────────────────────
echo ">> Writing $SERVICE_DIR/hioki-app.service"
cat > "$SERVICE_DIR/hioki-app.service" <<EOF
[Unit]
Description=Hioki Data Logger
After=network.target graphical-session.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON $APP_DIR/main.py
Restart=always
RestartSec=3
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/$APP_USER/.Xauthority

[Install]
WantedBy=graphical.target
EOF

# ── Write hioki-watchdog.service ─────────────────────────────────────────────
echo ">> Writing $SERVICE_DIR/hioki-watchdog.service"
cat > "$SERVICE_DIR/hioki-watchdog.service" <<EOF
[Unit]
Description=Hioki Data Logger Watchdog
After=hioki-app.service
Requires=hioki-app.service

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=/bin/bash $APP_DIR/watchdog.sh
Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
EOF

# ── Register with systemd ─────────────────────────────────────────────────────
echo ">> Reloading systemd daemon"
systemctl daemon-reload

echo ">> Enabling services (auto-start on boot)"
systemctl enable hioki-app.service
systemctl enable hioki-watchdog.service

echo ">> Starting services now"
systemctl restart hioki-app.service
sleep 2
systemctl restart hioki-watchdog.service

# ── Show status ───────────────────────────────────────────────────────────────
echo ""
echo "=== Status ==="
systemctl status hioki-app.service      --no-pager -l
echo ""
systemctl status hioki-watchdog.service --no-pager -l

echo ""
echo "=== Done ==="
echo "Both services are running and will auto-start on every boot."
echo ""
echo "Useful commands:"
echo "  sudo systemctl status  hioki-app.service"
echo "  sudo systemctl restart hioki-app.service"
echo "  sudo systemctl stop    hioki-app.service"
echo "  tail -f $APP_DIR/watchdog.log"
echo "  journalctl -u hioki-app.service -f"
