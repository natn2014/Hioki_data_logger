#!/bin/bash
# update_pi.sh — update an already-installed Raspberry Pi to the latest code
# (adds the PATLITE LR6-USB tower light) WITHOUT losing the device's data.
#
# Run as the normal user (not sudo; it asks for sudo when needed):
#   bash update_pi.sh                 # branch MSW-RS485TC (default)
#   bash update_pi.sh MSW             # or another branch
#
# No internet (factory LAN)? Copy the code and Python wheels to the Pi first:
#   git fetch /tmp/hioki.bundle BRANCH:refs/remotes/origin/BRANCH
#   python packages (*.whl) into /tmp/hioki_wheels
# The script then skips GitHub / apt / PyPI and uses what was copied.
#
# Keeps the device's own data files: gui_mode5_config.json (current model,
# limits, language, ...), pending/rejected/failed uploads, model_changes.csv,
# the daily CSV files and logs. A full backup is also kept in ~/hioki_backup_*.
#
# The whole script sits in { ... } so bash reads it completely before running:
# the git pull below may replace this very file.
{
set -e
BRANCH="${1:-MSW-RS485TC}"
APP_DIR="${APP_DIR:-/home/pi/Hioki_data_logger}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="$HOME/hioki_backup_$STAMP"
# Data the device writes itself; the device's copy always wins over git's.
DATA_FILES="gui_mode5_config.json pending_uploads.json rejected_uploads.json
            failed_uploads.json watchdog.log network_status.log network_status.txt
            heartbeat.txt"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "   ${GREEN}[OK]${NC}   $*"; }
warn() { echo -e "   ${YELLOW}[WARN]${NC} $*"; }
die()  { echo -e "   ${RED}[FAIL]${NC} $*"; [ -n "$STARTED" ] && on_error "$BASH_LINENO"; exit 1; }
STARTED=""
WHEELS_DIR="${WHEELS_DIR:-/tmp/hioki_wheels}"

restore_data() {
    if [ -f "$BACKUP/pyvenv.cfg" ]; then
        mkdir -p "$APP_DIR/.venv"
        cp -a "$BACKUP/pyvenv.cfg" "$APP_DIR/.venv/pyvenv.cfg"
    fi
    for f in $DATA_FILES; do
        [ -f "$BACKUP/$f" ] && cp -a "$BACKUP/$f" "$APP_DIR/$f"
    done
    cp -a "$BACKUP"/*.csv "$APP_DIR"/ 2>/dev/null || true   # model_changes, pending_specs, daily CSVs
}

on_error() {
    echo -e "${RED}Update failed at line $1.${NC} Restoring data files and restarting the app..."
    restore_data
    sudo systemctl start hioki-app || true
    sudo systemctl start hioki-watchdog 2>/dev/null || true
    echo "Backup kept in $BACKUP. Send the messages above to the developer."
    exit 1
}

[ "$EUID" -ne 0 ] || die "Run without sudo:  bash update_pi.sh"
[ -d "$APP_DIR/.git" ] || die "$APP_DIR is not a git clone"
cd "$APP_DIR"
APP_USER="$(stat -c %U "$APP_DIR")"

echo ">> 1/7 Stop the app"
sudo systemctl stop hioki-watchdog 2>/dev/null || true   # don't let it restart the app
sudo systemctl stop hioki-app
ok "stopped"

echo ">> 2/7 Back up data files to $BACKUP"
mkdir -p "$BACKUP"
cp -a ./*.json ./*.csv ./*.log ./*.txt "$BACKUP/" 2>/dev/null || true
# Older commits tracked a Windows .venv/pyvenv.cfg: a checkout would replace or
# delete the Pi's own one and break the venv. Keep it and put it back below.
[ -f .venv/pyvenv.cfg ] && cp -a .venv/pyvenv.cfg "$BACKUP/pyvenv.cfg"
ok "$(ls "$BACKUP" | wc -l) files backed up"
trap 'on_error $LINENO' ERR
STARTED=1
ONLINE=0
timeout 8 git ls-remote origin HEAD >/dev/null 2>&1 && ONLINE=1
[ "$ONLINE" = 1 ] && ok "GitHub reachable" || warn "no internet: using code / packages copied to this Pi"

echo ">> 3/7 Get the new code ($BRANCH)"
# Local changes are only the data files (backed up above): put them aside so
# the pull cannot conflict. The stash is kept, in case anything else was edited.
if ! git diff --quiet; then
    git stash push -m "update_pi $STAMP" >/dev/null
    warn "local changes stashed as 'update_pi $STAMP' (see: git stash list)"
fi
# Untracked data files would block the checkout if the new branch tracks them.
for f in $DATA_FILES; do
    if [ -f "$f" ] && ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
        rm -f "$f"
    fi
done
[ "$ONLINE" = 1 ] && git fetch origin
git rev-parse --verify -q "origin/$BRANCH" >/dev/null || die "origin/$BRANCH not found (offline: fetch the bundle first)"
git checkout "$BRANCH"
if ! git merge --ff-only "origin/$BRANCH"; then
    warn "this Pi has its own commits on $BRANCH; they are kept as branch pi-local-$STAMP"
    git branch "pi-local-$STAMP"
    git reset --hard "origin/$BRANCH"
fi
ok "now at $(git log --oneline -1)"

echo ">> 4/7 Put the device's data files back"
restore_data
ok "restored (current model, limits, upload queues, logs, daily CSVs)"

echo ">> 5/7 System packages + Python packages"
if [ "$ONLINE" = 1 ]; then
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends \
        libhidapi-hidraw0 libhidapi-libusb0 libusb-1.0-0 fonts-thai-tlwg >/dev/null
    .venv/bin/pip install -q -r requirements.txt
else
    dpkg -s libhidapi-hidraw0 >/dev/null 2>&1 || warn "libhidapi-hidraw0 missing (apt needs internet)"
    if ls "$WHEELS_DIR"/*.whl >/dev/null 2>&1; then
        .venv/bin/pip install -q --no-index --find-links "$WHEELS_DIR" "$WHEELS_DIR"/*.whl
    fi
fi
if .venv/bin/python -c "import hid" 2>/dev/null; then
    ok "hidapi OK"
else
    warn "hidapi not installed: the tower light stays off (the app works without it)"
fi
PYC="$(mktemp -d)"
PYTHONPYCACHEPREFIX="$PYC" .venv/bin/python -m py_compile ./*.py     || die "the new code does not compile on this Pi's Python"
rm -rf "$PYC"
ok "code compiles on this Pi's Python"

echo ">> 6/7 Tower light USB permission"
sudo install -m 644 deploy/99-patlite-lr6.rules /etc/udev/rules.d/99-patlite-lr6.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
NEED_REBOOT=0
if ! id -nG "$APP_USER" | grep -qw plugdev; then
    sudo usermod -aG plugdev "$APP_USER"
    NEED_REBOOT=1
fi
ok "udev rule installed, $APP_USER in plugdev"
if lsusb | grep -qi "191a:8003"; then
    ok "PATLITE tower found on USB"
else
    warn "PATLITE tower not found on USB (plug it in; the app works without it)"
fi
trap - ERR

echo ">> 7/7 Start the app"
sudo systemctl daemon-reload
sudo systemctl start hioki-app
sudo systemctl start hioki-watchdog 2>/dev/null || true
sleep 3
if systemctl is-active --quiet hioki-app; then
    ok "hioki-app running"
else
    warn "hioki-app not running — check: journalctl -u hioki-app -n 50"
fi

echo ""
echo "Done. Backup: $BACKUP"
if [ "$NEED_REBOOT" = 1 ]; then
    echo -e "${YELLOW}Reboot once so the USB permission applies, then unplug/replug the tower:${NC}"
    echo "    sudo reboot"
else
    echo "If the tower stays dark: unplug and replug its USB cable once."
fi
exit 0
}
