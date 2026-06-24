#!/bin/bash
# setup_services.sh — Install and enable hioki-app + hioki-watchdog as systemd services.
# Run once after copying files to the Raspberry Pi:
#   chmod +x setup_services.sh
#   sudo ./setup_services.sh

set -e  # stop on any error

# ── Configuration ────────────────────────────────────────────────────────────
APP_USER="${SUDO_USER:-pi}"                         # the user who ran sudo
APP_DIR="/home/$APP_USER/Hioki_data_logger"
APP_UID=$(id -u "$APP_USER" 2>/dev/null || echo "1000")
SERVICE_DIR="/etc/systemd/system"
VENV_DIR="$APP_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_PIP="$VENV_DIR/bin/pip"
# ─────────────────────────────────────────────────────────────────────────────

# Colour helpers (safe — falls back to plain text if terminal doesn't support it)
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "   ${GREEN}[OK]${NC}   $*"; }
fail() { echo -e "   ${RED}[FAIL]${NC} $*"; }
warn() { echo -e "   ${YELLOW}[WARN]${NC} $*"; }

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo:  sudo ./setup_services.sh"
    exit 1
fi

echo ""
echo "=== Hioki Service Setup ==="
echo "User      : $APP_USER  (UID $APP_UID)"
echo "App dir   : $APP_DIR"
echo "Venv      : $VENV_DIR"
echo ""

# ── Check app directory and main entry point ─────────────────────────────────
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
echo ">> System Python : $SYS_PYTHON  ($(${SYS_PYTHON} --version 2>&1))"

# ── Install system-level dependencies via apt ────────────────────────────────
echo ""
echo ">> Installing system packages via apt"
apt-get update -qq
apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    apt-transport-https \
    python3-serial \
    python3-venv \
    python3-pip \
    unixodbc \
    unixodbc-dev \
    libglib2.0-0 \
    libdbus-1-3 \
    libxcb-cursor-dev \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-alsa \
    gstreamer1.0-libav
echo ">> System packages installed."

# ── Install Microsoft ODBC Driver 18 for SQL Server ──────────────────────────
echo ""
echo ">> Installing Microsoft ODBC Driver 18 for SQL Server"

ARCH=$(dpkg --print-architecture 2>/dev/null || echo "unknown")
if [ "$ARCH" != "arm64" ] && [ "$ARCH" != "amd64" ]; then
    warn "Architecture '$ARCH' is not supported by Microsoft ODBC Driver 18."
    warn "Database uploads will fail at runtime. Consider using a 64-bit OS image."
else
    # Detect Debian codename from /etc/os-release
    DEBIAN_CODENAME=""
    if [ -f /etc/os-release ]; then
        DEBIAN_CODENAME=$(. /etc/os-release && echo "${VERSION_CODENAME:-}")
    fi
    if [ -z "$DEBIAN_CODENAME" ]; then
        DEBIAN_CODENAME="bookworm"
        warn "Could not detect Debian codename — defaulting to 'bookworm'"
    fi

    case "$DEBIAN_CODENAME" in
        bookworm) DEBIAN_VER=12 ;;
        bullseye) DEBIAN_VER=11 ;;
        buster)   DEBIAN_VER=10 ;;
        *)
            DEBIAN_VER=12
            warn "Unknown codename '$DEBIAN_CODENAME' — defaulting to Debian 12"
            ;;
    esac
    echo ">> Detected: Debian $DEBIAN_VER ($DEBIAN_CODENAME) on $ARCH"

    MS_KEYRING="/usr/share/keyrings/microsoft-prod.gpg"
    MS_REPO="/etc/apt/sources.list.d/mssql-release.list"

    if [ ! -f "$MS_REPO" ]; then
        echo ">> Adding Microsoft apt repository"
        curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
            | gpg --dearmor -o "$MS_KEYRING"
        echo "deb [arch=$ARCH signed-by=$MS_KEYRING] https://packages.microsoft.com/debian/$DEBIAN_VER/prod $DEBIAN_CODENAME main" \
            > "$MS_REPO"
        apt-get update -qq
    else
        echo ">> Microsoft apt repository already configured"
    fi

    if dpkg -l msodbcsql18 2>/dev/null | grep -q "^ii"; then
        echo ">> msodbcsql18 already installed"
    else
        echo ">> Installing msodbcsql18 (EULA auto-accepted)"
        ACCEPT_EULA=Y apt-get install -y msodbcsql18
    fi
    ok "ODBC Driver 18 for SQL Server installed"
fi

# ── Create virtual environment (with access to system site-packages) ─────────
# --system-site-packages lets the venv reuse apt-installed python3-serial
# instead of rebuilding it from source via pip.
if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo ">> Creating virtual environment at $VENV_DIR"
    sudo -u "$APP_USER" "$SYS_PYTHON" -m venv --system-site-packages "$VENV_DIR"
else
    echo ""
    echo ">> Virtual environment already exists at $VENV_DIR"
fi

# Confirm the venv Python exists before proceeding
if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: Expected venv Python not found at $VENV_PYTHON"
    echo "       Try deleting $VENV_DIR and re-running this script."
    exit 1
fi
echo ">> Venv Python   : $VENV_PYTHON  ($("$VENV_PYTHON" --version 2>&1))"

# ── Install / upgrade Python packages ────────────────────────────────────────
echo ""
echo ">> Upgrading pip"
sudo -u "$APP_USER" "$VENV_PIP" install --upgrade pip --quiet

if [ -f "$APP_DIR/requirements.txt" ]; then
    echo ">> Installing packages from $APP_DIR/requirements.txt"
    sudo -u "$APP_USER" "$VENV_PIP" install -r "$APP_DIR/requirements.txt"
else
    echo ">> No requirements.txt found — installing default packages"
    sudo -u "$APP_USER" "$VENV_PIP" install \
        PySide6 \
        pyodbc \
        pyserial \
        watchdog \
        requests
fi

# ── Verify every required module imports without error ────────────────────────
echo ""
echo ">> Verifying Python module imports..."
PASS=0
FAIL=0

check_import() {
    local module="$1"
    local label="${2:-$1}"
    if sudo -u "$APP_USER" "$VENV_PYTHON" -c "import $module" 2>/dev/null; then
        ok "$label"
        PASS=$((PASS + 1))
    else
        fail "$label  ← import failed"
        FAIL=$((FAIL + 1))
    fi
}

check_import "PySide6.QtWidgets"       "PySide6.QtWidgets"
check_import "PySide6.QtCore"          "PySide6.QtCore"
check_import "PySide6.QtGui"           "PySide6.QtGui"
check_import "PySide6.QtMultimedia"    "PySide6.QtMultimedia  (audio playback)"
check_import "serial.tools.list_ports" "pyserial  (serial.tools.list_ports)"
check_import "pyodbc"                  "pyodbc"
check_import "watchdog"                "watchdog"
check_import "requests"                "requests"
check_import "csv"                     "csv  (stdlib)"
check_import "json"                    "json  (stdlib)"

# Verify the ODBC driver is registered with unixODBC (not just installed)
ODBC_DRIVER="ODBC Driver 18 for SQL Server"
if sudo -u "$APP_USER" "$VENV_PYTHON" -c \
    "import pyodbc; assert '$ODBC_DRIVER' in pyodbc.drivers(), 'not registered'" \
    2>/dev/null; then
    ok "$ODBC_DRIVER  (registered in unixODBC)"
    PASS=$((PASS + 1))
else
    AVAILABLE=$(sudo -u "$APP_USER" "$VENV_PYTHON" -c \
        "import pyodbc; print(', '.join(pyodbc.drivers()) or 'none')" 2>/dev/null || echo "pyodbc unavailable")
    fail "$ODBC_DRIVER  (not registered — available drivers: $AVAILABLE)"
    FAIL=$((FAIL + 1))
fi

# Verify required audio files are present
for audio_file in "ResistancePass_TH.mp3" "ResistanceOver_TH.mp3"; do
    if [ -f "$APP_DIR/$audio_file" ]; then
        ok "$audio_file  (found)"
        PASS=$((PASS + 1))
    else
        fail "$audio_file  ← file missing from $APP_DIR"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
if [ "$FAIL" -gt 0 ]; then
    echo -e "${RED}ERROR: $FAIL module(s) failed to import.${NC}"
    echo "       Fix the failures above, then re-run this script."
    exit 1
fi
echo -e "${GREEN}>> All $PASS modules imported successfully.${NC}"

# ── Make scripts executable ───────────────────────────────────────────────────
chmod +x "$APP_DIR/watchdog.sh"

# ── Write hioki-app.service ──────────────────────────────────────────────────
echo ""
echo ">> Writing $SERVICE_DIR/hioki-app.service"
cat > "$SERVICE_DIR/hioki-app.service" <<EOF
[Unit]
Description=Hioki Data Logger
After=network.target display-manager.service
Wants=display-manager.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
# Wait until the X11 socket exists (up to 30 s) before launching the GUI
ExecStartPre=/bin/bash -c 'for i in \$(seq 1 30); do [ -S /tmp/.X11-unix/X0 ] && exit 0; sleep 1; done; exit 1'
ExecStart=$VENV_PYTHON $APP_DIR/main.py
Restart=always
RestartSec=5
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/$APP_USER/.Xauthority
Environment=XDG_RUNTIME_DIR=/run/user/$APP_UID
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$APP_UID/bus
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
echo ""
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
