#!/bin/bash
# setup_services.sh — Install and enable hioki-app + hioki-watchdog as systemd services.
# Run once after copying files to the Raspberry Pi:
#   chmod +x setup_services.sh
#   sudo ./setup_services.sh

set -e  # stop on any error

# ── Configuration ────────────────────────────────────────────────────────────
APP_USER="${SUDO_USER:-pi}"                         # the user who ran sudo
APP_DIR="/home/$APP_USER/Hioki_data_logger"
SERVICE_DIR="/etc/systemd/system"
VENV_DIR="$APP_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_PIP="$VENV_DIR/bin/pip"
# ─────────────────────────────────────────────────────────────────────────────

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo:  sudo ./setup_services.sh"
    exit 1
fi

echo ""
echo "=== Hioki Service Setup ==="
echo "User      : $APP_USER"
echo "App dir   : $APP_DIR"
echo "Venv      : $VENV_DIR"
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

SYS_PYTHON="$(which python3 2>/dev/null || true)"
if [ -z "$SYS_PYTHON" ]; then
    echo "ERROR: python3 not found. Install it with: sudo apt install python3"
    exit 1
fi

# ── Create virtual environment if it doesn't exist ───────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo ">> Creating virtual environment at $VENV_DIR"
    sudo -u "$APP_USER" "$SYS_PYTHON" -m venv "$VENV_DIR"
else
    echo ">> Virtual environment already exists at $VENV_DIR"
fi

# ── Install / upgrade dependencies ───────────────────────────────────────────
echo ">> Upgrading pip"
sudo -u "$APP_USER" "$VENV_PIP" install --upgrade pip --quiet

if [ -f "$APP_DIR/requirements.txt" ]; then
    echo ">> Installing packages from requirements.txt"
    sudo -u "$APP_USER" "$VENV_PIP" install -r "$APP_DIR/requirements.txt"
else
    echo ">> No requirements.txt found — installing default packages"
    sudo -u "$APP_USER" "$VENV_PIP" install \
        PySide2 \
        pyodbc \
        pyserial \
        watchdog \
        requests
fi
echo ">> Python packages installed."

PYTHON="$VENV_PYTHON"

# Make scripts executable
chmod +x "$APP_DIR/watchdog.sh"

# ── Write hioki-app.service ──────────────────────────────────────────────────
echo ">> Writing $SERVICE_DIR/hioki-app.service"
cat > "$SERVICE_DIR/hioki-app.service" <<EOF
[Unit]
Description=Hioki Data Logger
After=network.target graphical.target
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
Environment=PYTHONUNBUFFERED=1
Environment=QT_QPA_PLATFORM=xcb

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
WantedBy=multi-user.target
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
