# coding: UTF-8
"""PySide6 UI for Mode 5 (Auto Hold + FETC?)
- Scans available COM ports at 9600 baud
- Uses *IDN? to verify HIOKI; if not HIOKI, disconnects and tries next port
- Enables HOLD:AUTO ON and polls FETC? for stable readings
- Displays live data (left, 60% width) and logs with timestamps (right)
"""

import sys
import time
import json
import os
import csv
import subprocess
import serial
import serial.tools.list_ports
from datetime import datetime
from threading import Thread
from PySide6.QtCore import QTimer, QThread, Signal, Qt, QEvent, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QApplication, QDialog, QMessageBox, QAbstractSpinBox,
    QPushButton, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QDoubleSpinBox,
    QScrollArea, QScroller, QWidget, QStackedWidget
)
from html import escape
from usb_rs import Usb_rs
from insert_resistance2db import (
    insert_to_mssql, fetch_model_spec, upsert_model_spec, check_schema,
    MODEL_MAX_LEN,
)
from ui_UI_Resistance import Ui_Dialog, PointCard, build_qss, set_prop, C as UI_COLORS
from numpad_dialog import NumpadDialog
from keyboard_dialog import KeyboardDialog
from db_upload_manager import DBUploadManager, UploadSignals, SpecQueueManager
from i18n import tr, set_language, get_language, DEFAULT_LANGUAGE
from temp_correction import (
    TempCorrectionTab, correct_resistance, normalize_tc_settings,
    default_tc_settings, TEMP_STALE_S,
)

# RS485/Modbus object-temperature sensor (optional: needs pymodbus + matplotlib).
try:
    import rs485_temperature_monitor as rs485
    _RS485_IMPORT_ERROR = ""
except Exception as _e:  # missing pymodbus/matplotlib must not stop the app
    rs485 = None
    _RS485_IMPORT_ERROR = str(_e)

TEMP_SENSOR_DEFAULTS = {
    "enabled": True,
    "port": rs485.SERIAL_PORT if rs485 else "/dev/ttyAMA0",
    "baud": rs485.BAUDRATE if rs485 else 9600,
    "slave_id": rs485.SLAVE_ID if rs485 else 1,
    "register": rs485.REGISTER_ADDRESS if rs485 else 14,
    "raw_at_4ma": rs485.RAW_AT_4MA if rs485 else 400,
    "raw_at_20ma": rs485.RAW_AT_20MA if rs485 else 2000,
    "temp_at_4ma": rs485.TEMP_AT_4MA if rs485 else 0.0,
    "temp_at_20ma": rs485.TEMP_AT_20MA if rs485 else 200.0,
    "poll_interval": rs485.POLL_INTERVAL_SEC if rs485 else 0.2,
}

BAUD_RATE = 9600
POLL_INTERVAL_MS = 500  # default polling interval for FETC?
CONFIG_FILE = "gui_mode5_config.json"
MAX_VALID_OHMS = 1e12  # ignore readings above this magnitude
CSV_HEADERS = ["Timestamp", "Resistance", "Status", "Model",
               "Point", "Seq", "LowerLimit", "UpperLimit",
               "Date", "Time", "DB_Status"]
MODEL_CHANGE_LOG = "model_changes.csv"  # persistent record of every model switch
SPEC_DECIMALS = 2  # decimal places for spec limits (entry, storage, and display)
# Waiting time (operator setting, numpad; config "waiting_delay_s"). Release
# mode: with HOLD:AUTO ON the meter keeps returning the same held value on every
# FETC? after the probes are lifted, so a value still repeating this long after
# it was recorded is treated as open circuit → WAITING. The recorded PASS/FAIL
# is always readable for this long first.
WAITING_DELAY_DEFAULT_S = 5.0
WAITING_DELAY_MAX_S = 60.0
# NG reconfirm: an NG is not uploaded until the operator re-measures the point
# NG this many more times, or presses Confirm NG (config "ng_reconfirm_max").
NG_RECONFIRM_DEFAULT_MAX = 5
BLINK_MS = 500   # current point card blink period

# Serial hardware-hang recovery. A normal poll cycle is <=2 s (FETC? timeout) +
# 0.5 s interval; if the worker's liveness stamp stops advancing for this long,
# the serial link is wedged and we soft-recover without closing the app.
STALL_LIMIT_S = 8.0
STALL_CHECK_MS = 2000
# Watchdog backstop: main.py touches this on each successful poll; watchdog.sh
# force-restarts if it goes stale (well above STALL_LIMIT_S + reconnect backoff
# so in-app soft recovery always gets first chance).
HEARTBEAT_FILE = "heartbeat.txt"
HEARTBEAT_MIN_INTERVAL_S = 5.0  # throttle disk writes to spare the SD card

# USB status chip: state -> (i18n key, style state).
USB_STATES = {
    "connected":    ("usb_connected",    "ok"),
    "connecting":   ("usb_connecting",   "warn"),
    "disconnected": ("usb_disconnected", "bad"),
}


def show_message(parent, icon, title, text):
    """QMessageBox with a translated OK button (Qt's own is always English)."""
    box = QMessageBox(icon, title, text, QMessageBox.StandardButton.NoButton, parent)
    box.addButton(tr("ok"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def ask_yes_no(parent, title, text):
    """Yes/No question with translated buttons. Returns True for Yes."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text,
                      QMessageBox.StandardButton.NoButton, parent)
    yes = box.addButton(tr("yes"), QMessageBox.ButtonRole.YesRole)
    box.addButton(tr("no"), QMessageBox.ButtonRole.NoRole)
    box.exec()
    return box.clickedButton() is yes


def read_config_language():
    """Language saved in the config file (Thai when missing/unreadable)."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f).get("language", DEFAULT_LANGUAGE)
    except Exception:
        pass
    return DEFAULT_LANGUAGE


def ui_scale():
    """Layout scale vs. the 1280x800 design size (1.0 on the 10.1" panel).

    Uses the whole screen, not availableGeometry(): the kiosk window is full
    screen, and the available area shrinks while the desktop panel or the
    on-screen keyboard (squeekboard) is showing — starting then built the whole
    UI at 2/3 size."""
    screen = QApplication.primaryScreen().geometry()
    return min(screen.width() / 1280.0, screen.height() / 800.0, 1.0)


def enable_touch_scroll(area):
    """Drag-to-scroll with kinetic flick for a scroll area on the touch panel.
    LeftMouseButtonGesture also covers touch, which xcb delivers as mouse."""
    QScroller.grabGesture(area.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture)


# AIM Code 39 Extended escape sequences (see barcodereader.md)
AIM_MAP = {
    '/A': ' ',  '/B': '!',  '/C': '"',  '/D': ',',
    '/E': '%',  '/F': '&',  '/G': "'",  '/H': '(',
    '/I': ')',  '/J': '*',  '/K': '+',  '/L': '/',
    '/M': ':',  '/N': ';',  '/O': '<',  '/P': '=',
    '/Q': '>',  '/R': '?',  '/S': '@',  '/T': '[',
    '/U': '\\', '/V': ']',  '/W': '^',  '/X': '_',
    '/Y': '`',  '/Z': '{',
}


def decode_barcode(raw):
    """Decode AIM Code 39 Extended barcode string to a plain model number.

    Strips the leading check-digit character, converts /X escape pairs to their
    real characters, and stops at /D (field separator).
    """
    if not raw:
        return ''
    s = raw[1:]  # strip check-digit / scanner prefix
    result = ''
    i = 0
    while i < len(s):
        if s[i] == '/' and i + 1 < len(s):
            code = s[i:i + 2].upper()
            if code == '/D':
                break
            if code in AIM_MAP:
                result += AIM_MAP[code]
                i += 2
                continue
        result += s[i]
        i += 1
    return result.strip()


IGNORED_SCAN_PREFIXES = ("https://", "http://", "www.", "ppdb.tisi.go.th")


def is_ignored_scan(text):
    """True for scanned text that must never be used as a model name: URLs
    such as the TISI QR code on the product label ("https://ppdb.tisi.go.th/...")
    or anything longer than the DB Model column accepts."""
    t = (text or "").strip().lower()
    if not t:
        return False
    # Scanners sometimes drop/mangle characters ("ttps:/ ppdb.tisi.go.th..."),
    # so match the URL markers anywhere, not only at the start.
    if (t.startswith(IGNORED_SCAN_PREFIXES) or ":/" in t
            or "tisi.go.th" in t or "ppdb." in t):
        return True
    # e.g. a scan that came through as 35 backticks: it sat at the head of the
    # upload queue and blocked ~2900 readings (2026-10).
    if not any(ch.isalnum() for ch in t):
        return True
    return len(t) > MODEL_MAX_LEN


def _decode_model_raw(raw):
    if '$' in raw:
        first = raw.find('$')
        after = raw[first + 1:]
        second = after.find('$')
        return (after[:second] if second != -1 else after).strip()
    # AIM Code 39: presence of /[A-Z] escape pair signals encoded barcode
    if any(raw[i] == '/' and i + 1 < len(raw) and raw[i + 1].isupper()
           for i in range(len(raw))):
        decoded = decode_barcode(raw)
        if decoded:
            return decoded
    return raw


def decode_model_text(raw):
    """Unified decode for all barcode / manual-entry formats.

    Priority:
    1. Dollar-delimited  — PREFIX$[id]MODEL$SUFFIX  (label-printer format)
    2. AIM Code 39 Extended — /X escape sequences   (USB HID scanner format)
    3. Plain text — returned as-is after strip

    Returns '' for text that is not a model name (a URL / QR link, or longer
    than MODEL_MAX_LEN) so it is ignored instead of being saved as the model —
    otherwise every reading under it is rejected by the DB.

    Shared by the main window and the spec-editor dialog so barcode input is
    cleansed the same way everywhere.
    """
    raw = (raw or "").strip()
    if not raw:
        return ''
    decoded = _decode_model_raw(raw)
    if is_ignored_scan(decoded):
        return ''
    return decoded


class AutoDetectThread(QThread):
    found = Signal(str, str)  # port, idn
    not_found = Signal()

    def __init__(self, exclude_ports=()):
        super().__init__()
        # Never probe these (the RS485 temperature sensor): writing *IDN? onto
        # that bus would corrupt its Modbus traffic. Compared by real path so
        # aliases like /dev/serial0 -> /dev/ttyAMA0 are caught too.
        self.exclude = {os.path.realpath(p) for p in exclude_ports if p}

    def run(self):
        ports = [p for p in serial.tools.list_ports.comports()
                 if os.path.realpath(p.device) not in self.exclude]
        if not ports:
            self.not_found.emit()
            return
        for p in ports:
            try:
                s = serial.Serial(p.device, BAUD_RATE, timeout=1)
                time.sleep(0.2)
                s.write(b"*IDN?\r\n")
                time.sleep(0.2)
                resp = s.read_all().decode(errors="ignore").strip()
                s.close()
                if "HIOKI" in resp.upper() or "RM3544-01" in resp.upper():
                    self.found.emit(p.device, resp)
                    return
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass
                continue
        self.not_found.emit()


class SpecFetchThread(QThread):
    """Fetches a model's point sequence from resistance_spec off the main thread.

    A synchronous DB read would freeze the touch UI for up to the connect
    timeout when the server is unreachable, so spec loading happens here and the
    result is applied on the main thread via a queued signal.
    """
    spec_ready  = Signal(str, list)  # model, points
    spec_failed = Signal(str, str)   # model, error

    def __init__(self, model):
        super().__init__()
        self.model = model

    def run(self):
        try:
            points = fetch_model_spec(self.model)
            self.spec_ready.emit(self.model, points)
        except Exception as e:
            self.spec_failed.emit(self.model, str(e))


class SpecUpsertThread(QThread):
    """Writes a model's point sequence to resistance_spec off the main thread."""
    done   = Signal(str, int)         # model, rows_written
    failed = Signal(str, str, bool)   # model, error, queueable

    def __init__(self, model, points):
        super().__init__()
        self.model = model
        self.points = points

    def run(self):
        try:
            count = upsert_model_spec(self.model, self.points)
            self.done.emit(self.model, count)
        except ValueError as e:
            # Bad data — retrying can never help, so never queue it.
            self.failed.emit(self.model, str(e), False)
        except Exception as e:
            # Connection / query failure — queue it and retry later.
            self.failed.emit(self.model, str(e), True)


class SchemaCheckThread(QThread):
    """Startup guard: probes the DB for the multi-point schema off the main thread."""
    checked = Signal(object)  # result dict from check_schema()

    def run(self):
        self.checked.emit(check_schema())


class ModelSpecDialog(QDialog):
    """Touch-friendly editor for a model's resistance_spec point sequence.

    Returns the entered (model, points) via result_spec() when accepted. Numeric
    limits open the on-screen NumpadDialog on tap, matching the main window.
    """

    def __init__(self, model="", points=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("spec_title"))
        self.setObjectName("SpecDialog")   # scopes the input/button styles
        self.setModal(True)
        self._rows = []          # [{widget, seq, name, lower, upper}, ...]
        self._result = None
        self._scan_fields = []   # text fields that cleanse scanned barcodes
        scale = ui_scale()
        self._px = lambda v: max(1, round(v * scale))
        px = self._px

        outer = QVBoxLayout(self)
        outer.setContentsMargins(px(20), px(16), px(20), px(16))
        outer.setSpacing(px(12))

        outer.addWidget(self._caption(tr("spec_model_caption")))
        self.model_edit = QLineEdit(model)
        self.model_edit.setPlaceholderText(tr("spec_model_placeholder"))
        self.model_edit.setMaxLength(100)
        self._register_scan_field(self.model_edit)
        outer.addWidget(self.model_edit)

        hdr = QHBoxLayout()
        hdr.addWidget(self._caption(tr("spec_points_caption")))
        hdr.addStretch(1)
        self.add_btn = QPushButton(tr("spec_add_point"))
        self.add_btn.setProperty("role", "dlg")
        self.add_btn.clicked.connect(lambda: (self._add_row(), None)[1])
        hdr.addWidget(self.add_btn)
        outer.addLayout(hdr)

        self.rows_container = QWidget()
        self.rows_container.setObjectName("rows_container")
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, px(8), 0)
        self.rows_layout.setSpacing(px(10))
        self.rows_layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.rows_container)
        self.scroll.setMinimumHeight(px(300))
        enable_touch_scroll(self.scroll)
        outer.addWidget(self.scroll, 1)

        self.err_label = QLabel("")
        self.err_label.setWordWrap(True)
        self.err_label.setStyleSheet(
            f"color: {UI_COLORS['fail']}; font-weight: bold; font-size: {px(18)}px;")
        outer.addWidget(self.err_label)

        btns = QHBoxLayout()
        cancel = QPushButton(tr("cancel"))
        cancel.setProperty("role", "dlg")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("spec_save"))
        save.setProperty("role", "primary")
        for b in (cancel, save):
            b.setMinimumSize(px(200), px(64))
        # Not the default button: a barcode scan ends with Enter, and we must not
        # let that trailing Enter submit the dialog — it only cleanses the field.
        save.setAutoDefault(False)
        save.clicked.connect(self._on_save)
        btns.addWidget(cancel)
        btns.addStretch(1)
        btns.addWidget(save)
        outer.addLayout(btns)

        if points:
            for p in points:
                self._add_row(p.get("name", ""), p.get("lower"), p.get("upper"))
        else:
            self._add_row()

        # Most of the 1280x800 screen: rows stay finger-sized, ~5 visible at once.
        self.resize(px(1080), px(700))

    @staticmethod
    def _caption(text):
        lbl = QLabel(text)
        lbl.setProperty("role", "caption")
        return lbl

    def _add_row(self, name="", lower=None, upper=None):
        px = self._px
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(px(10))

        seq_lbl = QLabel(str(len(self._rows) + 1))
        seq_lbl.setFixedWidth(px(36))
        seq_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seq_lbl.setStyleSheet(f"font-size: {px(22)}px; font-weight: bold; color: {UI_COLORS['accent_hi']};")
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("A-B")
        name_edit.setMaxLength(50)
        self._register_scan_field(name_edit)

        lower_spin = QDoubleSpinBox()
        upper_spin = QDoubleSpinBox()
        for sp in (lower_spin, upper_spin):
            sp.setRange(0.0, 9999.0)
            sp.setDecimals(SPEC_DECIMALS)
            sp.setSingleStep(0.01)
            # Tap opens the numpad; spin arrows are too small to hit by finger.
            sp.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            sp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            sp.setSuffix(" Ω")
            sp.setFixedWidth(px(180))
            sp.lineEdit().installEventFilter(self)
        if lower is not None:
            lower_spin.setValue(float(lower))
        if upper is not None:
            upper_spin.setValue(float(upper))

        rm_btn = QPushButton("✕")
        rm_btn.setProperty("role", "danger")
        rm_btn.setFixedSize(px(64), px(56))

        h.addWidget(seq_lbl)
        h.addWidget(name_edit, 1)
        h.addWidget(self._caption(tr("spec_lower")))
        h.addWidget(lower_spin)
        h.addWidget(self._caption(tr("spec_upper")))
        h.addWidget(upper_spin)
        h.addWidget(rm_btn)

        # Insert before the trailing stretch so rows stack top-down.
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row_w)
        rec = {"widget": row_w, "seq": seq_lbl, "name": name_edit,
               "lower": lower_spin, "upper": upper_spin}
        self._rows.append(rec)
        rm_btn.clicked.connect(lambda: self._remove_row(rec))
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(row_w))

    def _remove_row(self, rec):
        if rec["name"] in self._scan_fields:
            self._scan_fields.remove(rec["name"])
        rec["widget"].setParent(None)
        rec["widget"].deleteLater()
        self._rows.remove(rec)
        for i, r in enumerate(self._rows, start=1):
            r["seq"].setText(str(i))

    def _register_scan_field(self, line_edit):
        """Make a text field cleanse scanned barcodes via decode_model_text.

        The USB HID scanner types the AIM Code 39 / dollar-delimited string then
        sends Enter; returnPressed/editingFinished decode it in place. FocusIn
        selects existing text (handled in eventFilter) so a scan overwrites it.
        """
        self._scan_fields.append(line_edit)
        line_edit.installEventFilter(self)
        line_edit.returnPressed.connect(lambda le=line_edit: self._cleanse_field(le))
        line_edit.editingFinished.connect(lambda le=line_edit: self._cleanse_field(le))

    def _cleanse_field(self, line_edit):
        cleaned = decode_model_text(line_edit.text())
        if cleaned != line_edit.text():
            line_edit.setText(cleaned)

    def eventFilter(self, source, event):
        et = event.type()
        if et == QEvent.Type.MouseButtonPress:
            for rec in self._rows:
                if source is rec["lower"].lineEdit():
                    self._numpad(rec["lower"], tr("title_lower_short"))
                    return True
                if source is rec["upper"].lineEdit():
                    self._numpad(rec["upper"], tr("title_upper_short"))
                    return True
            if source in self._scan_fields:
                self._keyboard(source)
                return True
        elif et == QEvent.Type.FocusIn and source in self._scan_fields:
            # Select existing text so a scan (or retype) replaces it cleanly.
            source.selectAll()
        return super().eventFilter(source, event)

    def _keyboard(self, line_edit):
        """Tap a text field → on-screen keyboard (scanner input works too)."""
        title = tr("title_model_name") if line_edit is self.model_edit else tr("title_point_name")
        dlg = KeyboardDialog(
            current_text=line_edit.text(), title=title,
            max_length=line_edit.maxLength(), parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Cleanse here too: the scanner may have typed into the keyboard.
            line_edit.setText(decode_model_text(dlg.get_text()))

    def _numpad(self, spinbox, title):
        dlg = NumpadDialog(
            current_value=spinbox.value(), decimals=spinbox.decimals(), title=title,
            min_val=spinbox.minimum(), max_val=spinbox.maximum(), parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            spinbox.setValue(dlg.get_value())

    def get_validated(self):
        """Return (model, points, error). error is None when valid."""
        model = self.model_edit.text().strip()
        if not model:
            return None, None, tr("err_model_required")
        if not self._rows:
            return None, None, tr("err_add_point")
        points = []
        seen = set()
        for i, rec in enumerate(self._rows, start=1):
            name = rec["name"].text().strip()
            if not name:
                return None, None, tr("err_point_name", i=i)
            if name.lower() in seen:
                return None, None, tr("err_point_dup", i=i, name=name)
            seen.add(name.lower())
            lo = rec["lower"].value()
            up = rec["upper"].value()
            if lo >= up:
                return None, None, tr("err_lower_upper", i=i, name=name)
            points.append({"name": name, "lower": round(lo, SPEC_DECIMALS),
                           "upper": round(up, SPEC_DECIMALS)})
        return model, points, None

    def _on_save(self):
        model, points, err = self.get_validated()
        if err:
            self.err_label.setText("✕ " + err)
            return
        self._result = (model, points)
        self.accept()

    def result_spec(self):
        return self._result


class PollWorkerThread(QThread):
    """Runs all serial I/O (FETC? polling + *IDN? health check) off the main thread."""
    result_ready = Signal(str)   # FETC? response: number string, "Timeout Error", or "Error: ..."
    health_ok = Signal(str)      # *IDN? response when device is healthy
    health_fail = Signal(str)    # error message when *IDN? check fails

    def __init__(self, serial_obj, poll_interval_ms=500, health_check_interval=30):
        super().__init__()
        self.serial_obj = serial_obj
        self.poll_interval = poll_interval_ms / 1000.0
        self.health_check_interval = health_check_interval
        self._running = False
        # Liveness stamp read by the UI thread's stall detector. A wedged
        # SendQueryMsg freezes this thread mid-syscall and stops advancing this
        # timestamp, which is how a silent D-state hang is caught. Plain float
        # read/write is atomic under the GIL, so no lock is needed.
        self.last_cycle_ts = time.time()

    def run(self):
        self._running = True
        last_health_check = time.time()
        try:
            while self._running:
                t_start = time.time()
                self.last_cycle_ts = t_start

                msg = self.serial_obj.SendQueryMsg("FETC?", 2)
                if not self._running:
                    break
                self.result_ready.emit(msg)

                # Periodic *IDN? heartbeat to verify device is still responsive
                if self._running:
                    now = time.time()
                    if now - last_health_check >= self.health_check_interval:
                        last_health_check = now
                        idn = self.serial_obj.SendQueryMsg("*IDN?", 1)
                        if not self._running:
                            break
                        if idn.startswith("Error") or idn == "Timeout Error":
                            self.health_fail.emit(f"Device health check failed: {idn}")
                        else:
                            self.health_ok.emit(idn)

                # Interruptible sleep for the remainder of the poll interval
                elapsed = time.time() - t_start
                remaining = self.poll_interval - elapsed
                if remaining > 0 and self._running:
                    deadline = time.time() + remaining
                    while self._running and time.time() < deadline:
                        time.sleep(0.05)
        except Exception as e:
            if self._running:
                self.health_fail.emit(f"Poll thread crashed unexpectedly: {e}")

    def stop(self):
        self._running = False


class WiFiWorkerThread(QThread):
    wifi_ready = Signal(int)  # percentage 0-100, or -1 if unavailable

    def __init__(self):
        super().__init__()
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            self.wifi_ready.emit(self._get_signal())
            for _ in range(50):  # 5-second interruptible sleep
                if not self._running:
                    break
                time.sleep(0.1)

    def _get_signal(self):
        import re
        # Primary: read /proc/net/wireless (available on all Linux/RPi)
        try:
            with open('/proc/net/wireless', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3 and parts[0].endswith(':'):
                        quality = float(parts[2].rstrip('.'))
                        return min(100, int(quality / 70 * 100))
        except Exception:
            pass
        # Fallback: iwconfig (parses "Link Quality=XX/YY")
        try:
            result = subprocess.run(
                ['iwconfig'],
                capture_output=True, text=True, timeout=3,
            )
            m = re.search(r'Link Quality=(\d+)/(\d+)', result.stdout)
            if m:
                return int(int(m.group(1)) / int(m.group(2)) * 100)
        except Exception:
            pass
        return -1

    def stop(self):
        self._running = False


class NgReconfirmDialog(QDialog):
    """Non-modal NG alarm: the operator re-measures the point (readings keep
    flowing) or presses Confirm NG. Frameless and placed explicitly before it
    is shown, so touch taps land where they look (see keyboard_dialog)."""
    confirm_clicked = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("NgReconfirmDialog")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setModal(False)
        # Don't steal focus: the barcode scanner types into the main window.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scale = ui_scale()
        px = lambda v: max(1, round(v * scale))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(28), px(20), px(28), px(20))
        lay.setSpacing(px(10))
        self.title = QLabel()
        self.title.setObjectName("ng_title")
        self.value = QLabel()
        self.value.setObjectName("ng_value")
        self.count = QLabel()
        self.count.setObjectName("ng_count")
        self.hint = QLabel()
        self.hint.setObjectName("ng_hint")
        self.hint.setWordWrap(True)
        for w in (self.title, self.value, self.count, self.hint):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(w)
        self.btn_confirm = QPushButton()
        self.btn_confirm.setProperty("role", "danger")
        self.btn_confirm.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_confirm.setMinimumHeight(px(72))
        self.btn_confirm.clicked.connect(self.confirm_clicked)
        lay.addWidget(self.btn_confirm)
        self.setFixedWidth(px(620))

    def show_state(self, name, value_text, lo, hi, n, max_n):
        self.title.setText(tr("ng_title", name=name) if name else tr("ng_title_single"))
        self.value.setText(f"{value_text} Ω   ({lo:.{SPEC_DECIMALS}f} – {hi:.{SPEC_DECIMALS}f})")
        self.count.setText(tr("ng_count", n=n, max=max_n))
        self.hint.setText(tr("ng_hint", max=max_n))
        self.btn_confirm.setText(tr("btn_confirm_ng"))
        self.adjustSize()
        if not self.isVisible():
            area = self.parentWidget().window().geometry()
            c = area.center()
            self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)
            self.show()
        self.raise_()


class MainWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.serial_obj = Usb_rs(gui=True)

        # Create signals for thread-safe callbacks
        self.upload_signals = UploadSignals()
        # QueuedConnection ensures slots always run on the main thread even when
        # signals are emitted from a plain threading.Thread (not QThread).
        self.upload_signals.upload_complete.connect(self.on_upload_complete, Qt.ConnectionType.QueuedConnection)
        self.upload_signals.retry_complete.connect(self.on_retry_complete, Qt.ConnectionType.QueuedConnection)
        self.upload_signals.upload_rejected.connect(self.on_upload_rejected, Qt.ConnectionType.QueuedConnection)

        self.upload_signals.spec_flush_complete.connect(
            self.on_spec_flush_complete, Qt.ConnectionType.QueuedConnection)

        self.db_manager = DBUploadManager(parent_signals=self.upload_signals)
        self.spec_queue = SpecQueueManager(parent_signals=self.upload_signals)
        self.connected = False
        self.current_port = None
        self.detect_in_progress = False
        self.previous_numeric = None
        self.previous_raw = None
        self.consecutive_same = 0
        self.lower_limit = 0.0
        self.upper_limit = 1000.0
        self.cleaned_model = ""
        self.last_db_insert_time = None
        # Measure-display behaviour in standby (probes lifted → meter reads
        # over-range/OL). True = keep showing the last reading (default, as
        # before); False = release the display to 0. Persisted in config.
        self.hold_previous = True
        # What the measured panel currently shows, so standby can keep it on
        # screen dimmed (hold mode) against the range it was judged on.
        self._display_value = None
        self._display_range = (None, None)
        self._recorded_at = 0.0   # when the shown value was recorded (Release timer)
        self.waiting_delay_s = WAITING_DELAY_DEFAULT_S   # operator setting (numpad)
        self.ng_reconfirm_max = NG_RECONFIRM_DEFAULT_MAX
        # NG being reconfirmed: {index, point, value, value_text, n, lo, hi,
        # result_text, time_str}, or None. Nothing is uploaded until confirmed.
        self._reconfirm = None
        self.ng_dialog = None
        # Last result per point index {i: (pass, value_text)}, kept across
        # sequence wraps until that point is measured again (cleared on model
        # / spec change).
        self.point_results = {}
        self._blink_on = False

        # Temperature correction: per-model settings (models[m]["tc"]) plus the
        # latest object temperature from the RS485 sensor.
        self.tc = default_tc_settings()
        self.tc_x_is_temp = True          # chart axis pref (global)
        self.temp_sensor_cfg = dict(TEMP_SENSOR_DEFAULTS)
        self.temp_thread = None
        self.last_temp = None
        self.last_temp_ts = 0.0
        self._last_temp_error = ""
        self._temp_sensor_status = "starting"
        self._tc_hist_date = None   # date of the CSV the histogram was loaded from
        self._point_index_base = ""

        # Multi-point spec sequence for the current model. Empty list == the
        # legacy single-range behaviour (self.lower_limit / self.upper_limit).
        self.spec_points = []           # [{seq, name, lower, upper}, ...] by Seq
        self.current_point_index = 0
        self.spec_fetch_thread = None
        self.spec_upsert_thread = None
        self.schema_check_thread = None
        self._pending_spec = None       # (model, points) awaiting DB write result

        self._audio_output = QAudioOutput()
        self._media_player = QMediaPlayer()
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.errorOccurred.connect(
            lambda err, msg: self.log_event(f"Audio error ({err}): {msg}")
        )

        # Barcode scanner input accumulator (USB HID scanner types as keyboard)
        self._barcode_buffer = ""
        self._barcode_timer = QTimer(self)
        self._barcode_timer.setSingleShot(True)
        self._barcode_timer.timeout.connect(self._on_barcode_timer)

        # Connection health and recovery settings
        self.reconnect_delay = 1.0  # Start with 1 second, exponential backoff
        self.max_reconnect_delay = 60.0  # Cap at 60 seconds
        self.consecutive_timeouts = 0
        self.max_consecutive_timeouts = 3  # Trigger reconnect after 3 timeouts

        # Background poll thread — owns all serial I/O after connection
        self.poll_thread = None
        self.wifi_thread = None
        # A wedged poll thread is frozen in D-state and cannot be joined; we must
        # still keep the QThread object alive (dropping the last reference while
        # its OS thread runs makes PySide6 abort). Park orphans here and prune
        # them once the kernel finally frees them.
        self._orphaned_threads = []

        # Non-blocking timers: reconnect scheduling and upload retry only
        self.detect_retry_timer = QTimer(self)
        self.detect_retry_timer.setSingleShot(True)
        self.detect_retry_timer.timeout.connect(self.start_auto_detect)
        self.retry_upload_timer = QTimer(self)
        self.retry_upload_timer.setSingleShot(False)
        self.retry_upload_timer.timeout.connect(self.retry_pending_uploads)

        # Serial-hang detector: watches the poll worker's liveness stamp from the
        # (still-alive) UI thread and soft-recovers a wedged connection.
        self._last_heartbeat_write = 0.0
        self.stall_check_timer = QTimer(self)
        self.stall_check_timer.setSingleShot(False)
        self.stall_check_timer.timeout.connect(self.check_poll_liveness)
        self.stall_check_timer.start(STALL_CHECK_MS)

        self.init_ui()
        self.load_config()
        self.log_event("Application started")
        self.start_temp_sensor()   # before auto-detect, which must skip its port
        self.start_auto_detect()
        self.run_schema_check()

    def init_ui(self):
        scale = ui_scale()
        # Language first so the very first paint is already in it.
        set_language(read_config_language())
        # App-wide so the dialogs and QMessageBoxes get the same touch sizing.
        QApplication.instance().setStyleSheet(build_qss(scale))
        self.ui = Ui_Dialog(scale)
        # Two screens: 0 = QC (MSW layout), 1 = Temperature Correction.
        self.stack = QStackedWidget(self)
        self.page_qc = QWidget()
        self.ui.setupUi(self.page_qc)
        self.stack.addWidget(self.page_qc)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.stack)
        self.setWindowTitle("HIOKI Auto Hold Mode (Mode 5)")
        self._build_tc_page(scale)

        # Limit tiles: tap to edit in single-range mode; locked while a point
        # sequence drives them (see _refresh_limits_ui).
        self.ui.btn_upper.clicked.connect(lambda: self._edit_limit("upper"))
        self.ui.btn_lower.clicked.connect(lambda: self._edit_limit("lower"))

        enable_touch_scroll(self.ui.textBrowser_log)
        enable_touch_scroll(self.ui.point_cards_area)
        self.ui.pushButton_model.clicked.connect(self.on_model_clicked)
        self.ui.btn_lang_th.clicked.connect(lambda: self._on_lang_clicked("th"))
        self.ui.btn_lang_en.clicked.connect(lambda: self._on_lang_clicked("en"))

        # Point-sequence cards: one tappable card per point. Tapping a card jumps
        # straight to that point. point_results ({index: (pass, value_text)})
        # keeps each point's last value + colour until it is measured again.
        self.point_cards_row = self.ui.point_cards_row
        self.point_cards = []

        self.btn_reset_point = self.ui.btn_reset_point
        self.btn_new_model = self.ui.btn_new_model
        self.btn_edit_spec = self.ui.btn_edit_spec
        # Force-uploads pending_uploads.json now, skipping the retry timer/backoff.
        self.btn_upload_now = self.ui.btn_upload_now
        # Toggle: hold the last reading vs. release the display to 0 in standby.
        self.btn_hold_toggle = self.ui.btn_hold_toggle
        self.btn_reset_point.clicked.connect(self.on_reset_point)
        self.btn_new_model.clicked.connect(self.on_new_model_clicked)
        self.btn_edit_spec.clicked.connect(self.on_edit_spec_clicked)
        self.btn_upload_now.clicked.connect(self.on_upload_now_clicked)
        self.btn_hold_toggle.toggled.connect(self.on_hold_toggle)
        self._apply_hold_toggle_ui()   # reflect the (default) hold_previous state
        # ⚙ shows/hides the tool row; waiting time; Temperature Correction screen.
        self.ui.btn_settings.toggled.connect(self.ui.tools_widget.setVisible)
        self.ui.btn_waiting.clicked.connect(self.on_waiting_clicked)
        self.ui.btn_tc.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        self._apply_waiting_ui()
        self._apply_point_to_ui()  # builds cards + sets initial enabled state

        # Current point card blinks (red while an NG is being reconfirmed).
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self._blink_tick)
        self.blink_timer.start(BLINK_MS)
        self.temp_chip_timer = QTimer(self)   # greys the chip once readings stop
        self.temp_chip_timer.timeout.connect(self._update_temp_chip)
        self.temp_chip_timer.start(200)
        self._update_temp_chip()

        # Pass/fail counters: today's readings for the current model (seeded
        # from the daily CSV in _load_counts, then counted live).
        self.counts = {"OK": 0, "NG": 0}
        self._counts_key = None
        self.reading_count = 0

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._tick_clock)
        self.clock_timer.start(1000)
        self._tick_clock()

        # Check if there are pending uploads to retry
        pending_count = self.db_manager.get_pending_count()
        if pending_count > 0:
            self.log_event(f"Found {pending_count} pending uploads to retry")
            self.append_log(tr("log_pending_values", n=pending_count))

        pending_specs = self.spec_queue.pending_count()
        if pending_specs > 0:
            self.log_event(f"Found {pending_specs} pending spec(s) to upload")
            self.append_log(tr("log_pending_specs", n=pending_specs))

        # Initialise status badges and start background WiFi monitor
        self._db_online = None   # unknown until the first DB round-trip
        self._set_usb_status("disconnected")
        self._refresh_db_status()
        self.wifi_thread = WiFiWorkerThread()
        self.wifi_thread.wifi_ready.connect(self._on_wifi_ready)
        self.wifi_thread.start()

    def _tick_clock(self):
        now = datetime.now()
        self.ui.label_clock.setText(now.strftime("%H:%M"))
        if self._counts_key and self._counts_key[1] != now.date():
            self._load_counts()   # midnight: start the new day's counters

    def _set_usb_status(self, state):
        self._usb_state = state
        key, style = USB_STATES.get(state, USB_STATES["disconnected"])
        self.ui.label_usb_status.setText(tr(key))
        set_prop(self.ui.label_usb_status, "state", style)
        if state != "connected":
            self._show_reading("idle", status=tr(
                "status_connecting" if state == "connecting" else "status_disconnected"))

    # ── Language (TH default / EN) ────────────────────────────────────────────

    def _on_lang_clicked(self, code):
        if code != get_language():
            self.apply_language(code)
            self.log_event(f"UI language -> {code}")
            self.save_config()
        else:
            self.ui.retranslate(self.reading_count)   # keep the toggle checked

    def apply_language(self, code):
        """Switch every visible text to `code` without restarting. DATA LOG
        lines already shown stay as written; new lines use the new language."""
        set_language(code)
        QApplication.instance().setStyleSheet(build_qss(ui_scale()))
        self.ui.retranslate(self.reading_count)
        self._set_usb_status(getattr(self, "_usb_state", "disconnected"))
        self._refresh_db_status()
        self._apply_hold_toggle_ui()
        self._apply_waiting_ui()
        self._retranslate_tc_page()
        self._apply_point_to_ui()
        if not self.cleaned_model:
            self.ui.label_model_name.setText(tr("model_placeholder"))
        if self.ui.frame_verdict.property("state") == "idle":
            self.ui.label_verdict_detail.setText(tr("verdict_no_reading_model"))
        if self.connected:
            state = self.ui.label_measure_value.property("state")
            if state == "idle":
                self._show_reading("idle", status=tr("status_ready")
                                   + (self._probe_hint() or tr("status_probe_part")))
            elif state == "standby":
                self._show_waiting()

    def _on_wifi_ready(self, pct):
        if pct < 0:
            self.ui.label_wifi.setText("○ ---")
            set_prop(self.ui.label_wifi, "state", "off")
            return
        bar_chars = "▂▄▆█"
        n = 0 if pct < 20 else 1 if pct < 40 else 2 if pct < 60 else 3 if pct < 80 else 4
        bars = ''.join(bar_chars[i] if i < n else '░' for i in range(4))
        self.ui.label_wifi.setText(f"{bars} {pct}%")
        set_prop(self.ui.label_wifi, "state",
                 "ok" if pct >= 70 else "warn" if pct >= 40 else "bad")

    def _refresh_db_status(self):
        """DB chip + Upload button: reachability and how much is queued."""
        pending = self.db_manager.get_pending_count()
        specs = self.spec_queue.pending_count()
        queued = pending + specs
        if self._db_online is False:
            text, style = (tr("db_offline_n", n=queued) if queued else tr("db_offline")), "bad"
        elif queued:
            text, style = tr("db_queued", n=queued), "warn"
        elif self._db_online:
            text, style = tr("db_online"), "ok"
        else:
            text, style = "◌ ---", "off"
        self.ui.label_db.setText(text)
        set_prop(self.ui.label_db, "state", style)
        self.btn_upload_now.setText(tr("btn_upload_n", n=queued) if queued else tr("btn_upload_now"))
        set_prop(self.btn_upload_now, "attention", bool(queued))

    def _set_db_online(self, online):
        self._db_online = online
        self._refresh_db_status()

    def _set_model_text(self, text):
        self._cancel_reconfirm()
        self.point_results = {}
        self.ui.label_model_name.setText(text if text else tr("model_placeholder"))
        set_prop(self.ui.label_model_name, "empty", not text)
        # A previous model's PASS/FAIL must not linger under the new name.
        self.ui.label_verdict.setText("— — —")
        self.ui.label_verdict_detail.setText(tr("verdict_no_reading_model"))
        set_prop(self.ui.frame_verdict, "state", "idle")
        self._load_counts()

    def _refresh_limits_ui(self, editable):
        """Show self.lower_limit/upper_limit on the tiles (and the gauge)."""
        tag = tr("tag_tap_to_edit") if editable else tr("tag_locked")
        for btn, value_lbl, tag_lbl, v in (
                (self.ui.btn_upper, self.ui.label_upper_value, self.ui.label_upper_tag, self.upper_limit),
                (self.ui.btn_lower, self.ui.label_lower_value, self.ui.label_lower_tag, self.lower_limit)):
            value_lbl.setText(f"{v:.{SPEC_DECIMALS}f}")
            tag_lbl.setText(tag)
            set_prop(btn, "editable", editable)
        self.ui.gauge.set_range(self.lower_limit, self.upper_limit)

    def _edit_limit(self, which):
        # While a point sequence drives the limits they are read-only; skip
        # the numpad so operators can't override the active point's range.
        if self.current_point() is not None:
            self.append_log(tr("log_limits_from_spec"))
            return
        current = self.upper_limit if which == "upper" else self.lower_limit
        dlg = NumpadDialog(
            current_value=current, decimals=SPEC_DECIMALS,
            title=tr("title_upper_limit") if which == "upper" else tr("title_lower_limit"),
            min_val=0.01, max_val=9999.0, parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if which == "upper":
                self.upper_limit = dlg.get_value()
            else:
                self.lower_limit = dlg.get_value()
            self.on_limit_changed()

    def _show_reading(self, state, value=None, status="", lo=None, hi=None):
        """Drive the measured-value panel.

        state: idle | settling | stable | pass | fail | standby. The gauge shows
        the value against lo/hi (default: the active limits) so a recorded
        reading stays drawn against the range it was judged on even after the
        sequence has advanced to the next point.
        """
        lbl = self.ui.label_measure_value
        if value is None:
            lbl.setText("-.---")
        else:
            try:
                lbl.setText(f"{float(value):.3f}")
            except (TypeError, ValueError):
                lbl.setText(str(value))
        set_prop(lbl, "state", state)
        self.ui.label_measure_status.setText(status)
        try:
            num = float(value) if value is not None else None
        except (TypeError, ValueError):
            num = None
        # Blank rather than hide the unit so the number doesn't shift sideways.
        self.ui.label_unit.setText("Ω" if num is not None else "")
        self.ui.gauge.set_range(self.lower_limit if lo is None else lo,
                                self.upper_limit if hi is None else hi)
        self.ui.gauge.set_value(num, state)

    def _show_waiting(self):
        """Open circuit (probes lifted): show WAITING. Hold mode keeps the last
        reading on screen, dimmed; Release mode replaces it with WAITING."""
        status = tr("status_waiting_open") + self._probe_hint()
        if self.hold_previous and self._display_value is not None:
            self._show_reading("standby", self._display_value,
                               status + tr("status_holding"), *self._display_range)
        elif time.time() - self._recorded_at < self.waiting_delay_s:
            return   # keep the just-recorded value readable a moment longer
        else:
            self._show_reading("standby", "WAITING", status)
            if self._reconfirm is None:   # an NG under reconfirm stays on screen
                self.ui.label_verdict.setText("WAITING")
                self.ui.label_verdict_detail.setText(tr("status_waiting_open"))
                set_prop(self.ui.frame_verdict, "state", "idle")

    def _probe_hint(self):
        p = self.current_point()
        return tr("probe_hint", name=p['name']) if p else ""

    def start_auto_detect(self):
        if self.connected or self.detect_in_progress:
            return
        self.detect_in_progress = True
        self.reconnect_delay = min(self.reconnect_delay * 1.5, self.max_reconnect_delay)  # Exponential backoff
        self.log_event(f"Auto-detect starting (reconnect delay: {self.reconnect_delay:.1f}s)")
        self._set_usb_status("connecting")
        self.det_thread = AutoDetectThread(exclude_ports=[self.temp_sensor_cfg["port"]])
        self.det_thread.found.connect(self.on_port_found)
        self.det_thread.not_found.connect(self.on_port_not_found)
        self.det_thread.start()

    def load_config(self):
        """Load model and per-model limits from config.json, falling back to model_changes.csv."""
        config_source = "config"
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    self.cleaned_model = config.get("current_model", "")
                    self.hold_previous = config.get("hold_previous", True)
                    self.tc_x_is_temp = config.get("tc_x_is_temp", True)
                    try:
                        self.waiting_delay_s = min(WAITING_DELAY_MAX_S, max(0.0, float(
                            config.get("waiting_delay_s", WAITING_DELAY_DEFAULT_S))))
                        self.ng_reconfirm_max = max(0, int(
                            config.get("ng_reconfirm_max", NG_RECONFIRM_DEFAULT_MAX)))
                    except (TypeError, ValueError):
                        pass
                    sensor = config.get("temp_sensor", {})
                    if isinstance(sensor, dict):
                        self.temp_sensor_cfg.update(
                            {k: sensor[k] for k in TEMP_SENSOR_DEFAULTS if k in sensor})
                    models = config.get("models", {})
                    if self.cleaned_model and self.cleaned_model in models:
                        self.lower_limit = models[self.cleaned_model].get("lower_limit", 0.0)
                        self.upper_limit = models[self.cleaned_model].get("upper_limit", 1000.0)
                    else:
                        self.lower_limit = 0.0
                        self.upper_limit = 1000.0
            else:
                self.cleaned_model = ""
                self.lower_limit = 0.0
                self.upper_limit = 1000.0
        except Exception as e:
            print(f"Error loading config: {e}")
            self.cleaned_model = ""
            self.lower_limit = 0.0
            self.upper_limit = 1000.0

        # A URL (e.g. a scanned TISI QR code) saved as the model before the
        # scan filter existed — drop it so readings aren't rejected by the DB.
        if is_ignored_scan(self.cleaned_model):
            self.log_event(f"Ignoring invalid saved model '{self.cleaned_model}'")
            self.cleaned_model = ""

        # Fall back to model change log when config has no model (crash / missing file)
        if not self.cleaned_model:
            recovered = self._recover_model_from_log()
            if recovered and not is_ignored_scan(recovered):
                self.cleaned_model = recovered
                config_source = "recovery_log"
                self.log_event(f"Config had no model — recovered '{recovered}' from model_changes.csv")
                self.log_model_change("RESTORE", "", recovered, "recovery_log")
                self.load_model_limits()  # pull saved limits for the recovered model

        # Record every startup with the active model for auditability
        if self.cleaned_model:
            self.log_model_change("STARTUP", "", self.cleaned_model, config_source)

        # Push loaded values into UI
        self._apply_hold_toggle_ui()
        self._apply_waiting_ui()
        self._set_model_text(self.cleaned_model)
        self._refresh_limits_ui(editable=True)

        # Load the point sequence (if any) for the startup model.
        self.load_model_spec()

    def save_config(self):
        """Save current_model and per-model limits to config.json"""
        try:
            # Load existing config to preserve other models
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
            else:
                config = {}

            # Update current model and limits
            config["current_model"] = self.cleaned_model
            config["hold_previous"] = self.hold_previous
            config["language"] = get_language()
            config["tc_x_is_temp"] = self.tc_x_is_temp
            config["waiting_delay_s"] = self.waiting_delay_s
            config["ng_reconfirm_max"] = self.ng_reconfirm_max
            if "models" not in config:
                config["models"] = {}
            if self.cleaned_model:
                # Merge, don't replace: the model entry also holds the cached
                # point sequence ("points") and temperature correction ("tc").
                config["models"].setdefault(self.cleaned_model, {}).update({
                    "lower_limit": round(self.lower_limit, 3),
                    "upper_limit": round(self.upper_limit, 3)
                })

            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")

    def log_model_change(self, action, old_model, new_model, source):
        """Append a model event to model_changes.csv for audit and crash recovery."""
        try:
            log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), MODEL_CHANGE_LOG)
            file_exists = os.path.exists(log_path)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "Action", "Previous_Model", "New_Model", "Source"])
                writer.writerow([timestamp, action, old_model, new_model, source])
        except Exception as e:
            print(f"Model change log write error: {e}")

    def _recover_model_from_log(self):
        """Return the last New_Model from model_changes.csv, or '' if unavailable."""
        try:
            log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), MODEL_CHANGE_LOG)
            if not os.path.exists(log_path):
                return ""
            last_model = ""
            with open(log_path, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("New_Model"):
                        last_model = row["New_Model"]
            return last_model
        except Exception as e:
            print(f"Model recovery log read error: {e}")
            return ""

    def on_limit_changed(self):
        """Persist and show limits after a single-range edit on the numpad."""
        if self.lower_limit >= self.upper_limit:
            self.append_log(tr("log_lower_not_below"))
        self._refresh_limits_ui(editable=True)
        self.save_config()
        self._update_tc_axis()

    def get_daily_csv_path(self, current_time):
        """Return CSV path named YYYYMMDD.csv in application directory."""
        date_name = current_time.strftime("%Y%m%d")
        app_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(app_dir, f"{date_name}.csv")

    def append_csv_row(self, current_time, resistance_value, status, model,
                       point=None, seq=None, lower=None, upper=None,
                       db_status="pending"):
        """Append one measurement row to daily CSV, creating header once."""
        csv_path = self.get_daily_csv_path(current_time)
        file_exists = os.path.exists(csv_path)

        timestamp_value = current_time.strftime("%Y-%m-%d %H:%M:%S")
        date_value = current_time.strftime("%Y-%m-%d")
        time_value = current_time.strftime("%H:%M:%S")

        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(CSV_HEADERS)
            writer.writerow([
                timestamp_value,
                resistance_value,
                status,
                model,
                point,
                seq,
                lower,
                upper,
                date_value,
                time_value,
                db_status
            ])

    def clean_raw_text(self, raw_text):
        """Clean raw text by removing prefix before and including first '$',
        and removing suffix from second '$' onwards.
        Example: FOD11850100163$1SRG14R(BRK)-MM-4FIMXA-A7$15 -> SRG14R(BRK)-MM-4FIMXA-A7
        """
        raw_text = raw_text.strip()

        # Find first '$'
        first_dollar = raw_text.find('$')
        if first_dollar == -1:
            return raw_text  # No '$' found, return as is

        # Remove everything up to and including first '$'
        text_after_first = raw_text[first_dollar + 1:]

        # Find second '$'
        second_dollar = text_after_first.find('$')
        if second_dollar == -1:
            return text_after_first  # No second '$', return everything after first

        # Return text between first and second '$'
        return text_after_first[:second_dollar]

    def _decode_model_text(self, raw):
        """Cleanse barcode / manual-entry text (delegates to decode_model_text)."""
        return decode_model_text(raw)

    def _prompt_model_text(self, preset=""):
        """On-screen keyboard prompt for a model name. Returns (text, ok)."""
        dlg = KeyboardDialog(current_text=preset, title=tr("title_model_name"),
                             max_length=100, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.get_text(), True
        return "", False

    def on_model_clicked(self):
        """Prompt user for model, clean it, show on button, and load its limits."""
        text, ok = self._prompt_model_text(self.cleaned_model)
        if ok:
            old_model = self.cleaned_model
            cleaned = self._decode_model_text(text)
            if not cleaned and text.strip():
                self.append_log(tr("log_model_not_changed", n=MODEL_MAX_LEN))
                return
            self.cleaned_model = cleaned
            self._set_model_text(cleaned)
            self.load_model_limits()
            self.load_model_spec()
            self.save_config()
            self.log_model_change("CHANGE", old_model, cleaned, "manual")

    def load_model_limits(self):
        """Load and apply limits for the current model from config."""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    models = config.get("models", {})
                    if self.cleaned_model and self.cleaned_model in models:
                        self.lower_limit = models[self.cleaned_model].get("lower_limit", 0.0)
                        self.upper_limit = models[self.cleaned_model].get("upper_limit", 1000.0)
                        self._refresh_limits_ui(editable=self.current_point() is None)
                        print(f"DEBUG: Loaded limits for model '{self.cleaned_model}': {self.lower_limit} - {self.upper_limit}")
        except Exception as e:
            print(f"Error loading model limits: {e}")

    # ── Multi-point spec sequence ─────────────────────────────────────────────

    def load_model_spec(self):
        """Load the current model's point sequence: cache first, then DB refresh.

        The cached points are applied immediately so the UI never blocks, then a
        background thread refreshes from resistance_spec (the source of truth).
        Empty result -> legacy single-range behaviour.
        """
        model = self.cleaned_model.strip()
        self._load_model_tc(model)   # per-model temperature-correction settings
        self._load_tc_history()      # today's readings for this model → histogram
        # Apply cached points at once (may be empty -> single-range mode).
        self.spec_points = self._load_cached_points(model) if model else []
        self.current_point_index = 0
        self._apply_point_to_ui()
        if not model:
            return
        # Refresh from DB off the main thread; result applied via signal.
        self.spec_fetch_thread = SpecFetchThread(model)
        self.spec_fetch_thread.spec_ready.connect(
            self.on_spec_ready, Qt.ConnectionType.QueuedConnection)
        self.spec_fetch_thread.spec_failed.connect(
            self.on_spec_failed, Qt.ConnectionType.QueuedConnection)
        self.spec_fetch_thread.start()

    def on_spec_ready(self, model, points):
        """Apply a freshly-fetched spec (main thread). Ignores stale results."""
        if model != self.cleaned_model.strip():
            return  # model changed again before this fetch returned
        if points:
            self._cache_points(model, points)
        self.spec_points = points
        self.current_point_index = 0
        self._apply_point_to_ui()
        self.log_event(f"Spec loaded for '{model}': {len(points)} point(s)")

    def on_spec_failed(self, model, err):
        """DB spec fetch failed — keep the cached sequence already applied."""
        if model != self.cleaned_model.strip():
            return
        self.log_event(f"Spec fetch failed for '{model}', using cache: {err}")

    def _load_cached_points(self, model):
        """Return the cached point sequence for a model from config, or []."""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                pts = config.get("models", {}).get(model, {}).get("points", [])
                return [
                    {"seq": int(p["seq"]), "name": p["name"],
                     "lower": float(p["lower"]), "upper": float(p["upper"])}
                    for p in pts
                ]
        except Exception as e:
            print(f"Error loading cached points: {e}")
        return []

    def _cache_points(self, model, points):
        """Persist a model's point sequence into config for offline use."""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
            else:
                config = {}
            config.setdefault("models", {}).setdefault(model, {})
            config["models"][model]["points"] = points
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error caching points: {e}")

    def current_point(self):
        """Return the active point dict, or None when no sequence is loaded."""
        if self.spec_points and 0 <= self.current_point_index < len(self.spec_points):
            return self.spec_points[self.current_point_index]
        return None

    def advance_point(self):
        """Move to the next point, wrapping back to the first after the last."""
        if self.spec_points:
            self.current_point_index = (self.current_point_index + 1) % len(self.spec_points)
            self._apply_point_to_ui()

    def on_reset_point(self):
        self._cancel_reconfirm()
        if self.spec_points:
            self.current_point_index = 0
            self._apply_point_to_ui()

    def _apply_hold_toggle_ui(self):
        """Sync the toggle button's checked state + label with hold_previous."""
        self.btn_hold_toggle.blockSignals(True)
        self.btn_hold_toggle.setChecked(self.hold_previous)
        self.btn_hold_toggle.blockSignals(False)
        # Label states the *current* mode; checked (highlighted) = hold.
        self.btn_hold_toggle.setText(tr("btn_hold") if self.hold_previous
                                     else tr("btn_release"))

    def on_hold_toggle(self, checked):
        """Toggle whether the measure display holds the last reading or releases
        to 0 while the meter is in standby (probes lifted / over-range)."""
        self.hold_previous = checked
        self._apply_hold_toggle_ui()
        mode = "hold last reading" if checked else "release (show WAITING)"
        self.log_event(f"Measure display mode: {mode}")
        self.append_log(tr("log_display_mode",
                           mode=tr("mode_hold") if checked else tr("mode_release")))
        # In release mode, replace a stale held value with WAITING immediately so
        # the change is visible without waiting for the next standby poll.
        if not checked:
            self._show_waiting()
        self.save_config()

    def on_point_card_tapped(self, index):
        """Operator tapped a point card — jump straight to that point."""
        if not self.spec_points or not (0 <= index < len(self.spec_points)):
            return
        if self._reconfirm is not None and index != self._reconfirm["index"]:
            self._cancel_reconfirm()
        self.current_point_index = index
        self._apply_point_to_ui()
        p = self.spec_points[index]
        self.log_event(f"Point selected by tap: {p['name']} (seq {p['seq']})")
        self.append_log(tr("log_point_selected", name=p['name'],
                           lo=f"{p['lower']:.{SPEC_DECIMALS}f}",
                           hi=f"{p['upper']:.{SPEC_DECIMALS}f}"))

    def _cards_signature(self):
        """Identity of the current card set — rebuild only when this changes."""
        return [(p["name"], p["lower"], p["upper"]) for p in self.spec_points]

    def _rebuild_point_cards(self):
        """Recreate one tappable card per point in the active sequence."""
        while self.point_cards_row.count():
            item = self.point_cards_row.takeAt(0)
            w = item.widget()
            if w is self.ui.label_single_hint:
                w.hide()   # reused, not deleted
            elif w is not None:
                w.setParent(None)
                w.deleteLater()
        self.point_cards = []

        if not self.spec_points:
            # No sequence: the row explains single-range mode instead.
            self.point_cards_row.addWidget(self.ui.label_single_hint)
            self.ui.label_single_hint.show()
        for i, p in enumerate(self.spec_points):
            card = PointCard(
                p["name"],
                f"{p['lower']:.{SPEC_DECIMALS}f} – {p['upper']:.{SPEC_DECIMALS}f} Ω",
                p["seq"],
            )
            # Up to 4 cards share the row; more scroll sideways (drag/flick).
            card.setMinimumWidth(round(190 * self.ui.scale))
            card.setMinimumHeight(round(88 * self.ui.scale))
            card.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            card.clicked.connect(
                lambda _checked=False, idx=i: self.on_point_card_tapped(idx))
            self.point_cards_row.addWidget(card)
            self.point_cards.append(card)
        # A rebuilt card set (spec edited) invalidates old per-point results.
        self.point_results = {}
        # The scroll area would squeeze the row below the cards' minimum width
        # (overlapping them); pin it so extra cards scroll instead.
        row_min = 0
        if self.point_cards:
            row_min = (len(self.point_cards) * self.point_cards[0].minimumWidth()
                       + (len(self.point_cards) - 1) * self.point_cards_row.spacing())
        self.ui.point_cards_area.widget().setMinimumWidth(row_min)
        self._cards_sig = self._cards_signature()

    def _apply_point_to_ui(self):
        """Reflect the active point in the limits, probe header, and point cards.

        When a sequence is active the limit tiles are driven (locked) from the
        current point; otherwise they stay editable (single-range mode).
        Cards are rebuilt only when the point set itself changes, so advancing
        during measurement just re-highlights (no widget churn / flicker).
        """
        point = self.current_point()
        seq_active = point is not None

        if getattr(self, "_cards_sig", None) != self._cards_signature():
            self._rebuild_point_cards()
        for i, card in enumerate(self.point_cards):
            card.set_active(i == self.current_point_index)
            res = self.point_results.get(i)
            card.set_result(*(res if res else (None,)))
        if 0 <= self.current_point_index < len(self.point_cards):
            # Deferred: a freshly rebuilt row has no geometry until layout runs.
            card = self.point_cards[self.current_point_index]
            QTimer.singleShot(0, lambda: card.isVisible()
                              and self.ui.point_cards_area.ensureWidgetVisible(card))

        self.btn_reset_point.setEnabled(seq_active)

        if seq_active:
            self.lower_limit = point["lower"]
            self.upper_limit = point["upper"]
            idx, n = self.current_point_index + 1, len(self.spec_points)
            self.ui.label_probe_caption.setText(tr("cap_probe"))
            self.ui.label_probe_point.setText(point["name"])
            self._point_index_base = tr("point_index", i=idx, n=n)
        else:
            self.ui.label_probe_caption.setText(tr("cap_measured"))
            self.ui.label_probe_point.setText(tr("single_range"))
            self._point_index_base = ""
        self._refresh_tc_tag()
        self._refresh_limits_ui(editable=not seq_active)
        self._update_tc_axis()

    # ── Register / edit a model spec (writes resistance_spec) ─────────────────

    def on_new_model_clicked(self):
        """Open a blank spec editor to register a brand-new model."""
        self._open_spec_dialog(model="", points=None, title_hint="new model")

    def on_edit_spec_clicked(self):
        """Open the spec editor prefilled with the current model."""
        model = self.cleaned_model.strip()
        points = [{"name": p["name"], "lower": p["lower"], "upper": p["upper"]}
                  for p in self.spec_points]
        self._open_spec_dialog(model=model, points=points, title_hint="edit spec")

    def _open_spec_dialog(self, model, points, title_hint):
        """Shared entry for both New Model and Edit Spec."""
        dlg = ModelSpecDialog(model=model, points=points, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.result_spec()
        if not result:
            return
        new_model, new_points = result
        self.log_event(f"Spec dialog accepted ({title_hint}): "
                       f"{new_model} ({len(new_points)} points)")
        self._start_spec_save(new_model, new_points)

    def _start_spec_save(self, model, points):
        """Write a spec to resistance_spec off-thread; queue it if the DB is down."""
        self._pending_spec = (model, points)
        self._set_spec_buttons_enabled(False)
        self.append_log(tr("log_saving_spec", model=model, n=len(points)))
        self.spec_upsert_thread = SpecUpsertThread(model, points)
        self.spec_upsert_thread.done.connect(
            self.on_spec_saved, Qt.ConnectionType.QueuedConnection)
        self.spec_upsert_thread.failed.connect(
            self.on_spec_save_failed, Qt.ConnectionType.QueuedConnection)
        self.spec_upsert_thread.start()

    def _set_spec_buttons_enabled(self, enabled):
        self.btn_edit_spec.setEnabled(enabled)
        self.btn_new_model.setEnabled(enabled)

    def _normalise_points(self, points):
        return [{"seq": i + 1, "name": p["name"],
                 "lower": float(p["lower"]), "upper": float(p["upper"])}
                for i, p in enumerate(points)]

    def _adopt_spec(self, model, norm):
        """Cache the spec locally and apply it (or offer to switch to it).

        Runs whether the DB write succeeded or was queued — the spec is valid
        either way, so the operator can measure with it immediately.
        """
        if norm:
            self._cache_points(model, norm)
        if model == self.cleaned_model.strip():
            self.spec_points = norm
            self.current_point_index = 0
            self._apply_point_to_ui()
            return
        if ask_yes_no(self, tr("msg_spec_saved_title"),
                      tr("msg_spec_saved_body", model=model)):
            old = self.cleaned_model
            self.cleaned_model = model
            self._set_model_text(model)
            self.load_model_limits()
            self.load_model_spec()
            self.save_config()
            self.log_model_change("CHANGE", old, model, "spec_editor")

    def on_spec_saved(self, model, count):
        """resistance_spec write succeeded — cache, apply, then flush any backlog."""
        self._set_spec_buttons_enabled(True)
        pending = self._pending_spec
        self._pending_spec = None
        raw = pending[1] if (pending and pending[0] == model) else []
        norm = self._normalise_points(raw)

        self.log_event(f"Spec saved to DB for '{model}' ({count} points)")
        self._set_db_online(True)
        self.append_log(tr("log_spec_saved", model=model, n=count))
        self._adopt_spec(model, norm)

        # Server just proved reachable — push any specs queued while it was down.
        if self.spec_queue.pending_count() > 0:
            self.spec_queue.flush_async()

    def on_spec_save_failed(self, model, err, queueable):
        """Write failed. Queue it like a reading upload, or reject bad data."""
        self._set_spec_buttons_enabled(True)
        pending = self._pending_spec
        self._pending_spec = None

        if not queueable:
            # Validation error — retrying can never help.
            self.log_event(f"Spec rejected for '{model}': {err}")
            self.append_log(tr("log_spec_rejected", err=err))
            show_message(self, QMessageBox.Icon.Critical, tr("msg_spec_rejected_title"),
                         tr("msg_spec_rejected_body", model=model, err=err))
            return

        raw = pending[1] if (pending and pending[0] == model) else []
        count = self.spec_queue.queue_spec(model, raw)
        self._set_db_online(False)
        self.log_event(f"Spec queued offline for '{model}': {err}")
        self.append_log(tr("log_spec_queued", model=model, n=count))
        # The spec is valid, so use it locally right away.
        self._adopt_spec(model, self._normalise_points(raw))
        show_message(self, QMessageBox.Icon.Information, tr("msg_spec_queued_title"),
                     tr("msg_spec_queued_body", model=model,
                        file=SpecQueueManager.PENDING_FILE))

    def on_spec_flush_complete(self, flushed, remaining):
        """A queued spec batch reached the DB."""
        self.log_event(f"Spec queue flushed: {flushed} model(s), {remaining} remaining")
        self._refresh_db_status()
        if remaining:
            self.append_log(tr("log_spec_queue_partial", done=flushed, left=remaining))
        else:
            self.append_log(tr("log_spec_queue_done", done=flushed))

    # ── Startup schema guard (optional, non-fatal) ────────────────────────────

    def run_schema_check(self):
        """Probe the DB schema off-thread and warn if the migration is missing."""
        self.schema_check_thread = SchemaCheckThread()
        self.schema_check_thread.checked.connect(
            self.on_schema_checked, Qt.ConnectionType.QueuedConnection)
        self.schema_check_thread.start()

    def on_schema_checked(self, result):
        """Log a clear warning when the multi-point schema is out of date.

        A server that's simply unreachable is not treated as a problem — the
        offline queue handles that and a later check will catch a real mismatch.
        """
        self._set_db_online(bool(result.get("reachable")) and not result.get("error"))
        if result.get("error") or not result.get("reachable"):
            self.log_event(f"Schema check skipped (DB not reachable): {result.get('error')}")
            return

        problems = []
        if not result.get("spec_table"):
            problems.append("table 'resistance_spec' is missing")
        missing = result.get("missing_columns") or []
        if missing:
            problems.append("resistance is missing column(s): " + ", ".join(missing))

        if problems:
            msg = ("DB schema out of date — " + "; ".join(problems) +
                   ". Apply migrations/001_multipoint_spec.sql to ENGINEER_DB "
                   "(uploads will fail until then).")
            self.log_event("WARNING: " + msg)
            self.append_log(tr("log_schema_old", problems="; ".join(problems)))
        else:
            self.log_event("DB schema OK (resistance_spec + point columns present)")

    def keyPressEvent(self, event):
        """Intercept USB HID barcode scanner keystrokes.

        The scanner types all characters rapidly then sends Enter.  Printable
        characters are accumulated in _barcode_buffer; Enter (or the 100 ms
        debounce timer) triggers decoding.  When no barcode is in progress the
        Enter key falls through to normal dialog handling.
        """
        key = event.key()
        text = event.text()
        modifiers = event.modifiers()
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_P:
                self.set_judgement_status(True)
                event.accept()
                return
            elif key == Qt.Key.Key_F:
                self.set_judgement_status(False)
                event.accept()
                return
            elif key == Qt.Key.Key_T:
                self.simulate_temperature()
                event.accept()
                return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._barcode_buffer:
                self._barcode_timer.stop()
                self._handle_barcode_input()
                event.accept()
                return
        elif text and text.isprintable():
            self._barcode_buffer += text
            self._barcode_timer.start(100)
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_barcode_timer(self):
        """Fires 100 ms after the last character — handles scanners without Enter."""
        self._handle_barcode_input()

    def _handle_barcode_input(self):
        """Decode accumulated barcode buffer and apply it as the current model."""
        raw = self._barcode_buffer
        self._barcode_buffer = ""
        if len(raw) < 3:
            return
        decoded = self._decode_model_text(raw)
        if not decoded and is_ignored_scan(raw):
            # URL / TISI QR code — not a model label. Keep the current model.
            self.log_event(f"Barcode scan ignored (URL / not a model): '{raw}'")
            self.append_log(tr("log_barcode_ignored"))
            return
        if not decoded:
            self.log_event(f"Barcode scan: decode failed for '{raw}'")
            self.append_log(tr("log_barcode_unknown", raw=raw))
            return
        self.log_event(f"Barcode scan: '{raw}' → '{decoded}'")
        old_model = self.cleaned_model
        self.cleaned_model = decoded
        self._set_model_text(decoded)
        self.load_model_limits()
        self.load_model_spec()
        self.save_config()
        self.log_model_change("CHANGE", old_model, decoded, "barcode")
        self.append_log(tr("log_model_by_barcode", model=decoded))

    def compare_spec(self, value_str, lower=None, upper=None):
        """Compare reading with spec limits. Returns (pass, result_text).

        lower/upper override the active single-range limits so a reading can be
        judged against the current point's range in a multi-point sequence.
        """
        lo = self.lower_limit if lower is None else lower
        hi = self.upper_limit if upper is None else upper
        try:
            value = float(value_str)
            if value < lo:
                return False, "FAIL: Low"
            elif value > hi:
                return False, "FAIL: High"
            else:
                return True, "PASS"
        except ValueError:
            return None, "N/A"

    def on_port_found(self, port, idn):
        self.detect_in_progress = False
        self.detect_retry_timer.stop()
        self.current_port = port
        self.reconnect_delay = 1.0  # Reset backoff on successful detection
        self.log_event(f"HIOKI device found: {port} - {idn}")
        self._set_usb_status("connected")
        # Auto start measurement once device detected
        self.start_mode()

    def on_port_not_found(self):
        self.detect_in_progress = False
        self.log_event("No HIOKI device found, scheduling retry...")
        self._set_usb_status("disconnected")
        # Retry detection with exponential backoff
        retry_ms = int(self.reconnect_delay * 1000)
        self.detect_retry_timer.start(retry_ms)

    def start_mode(self):
        if not self.current_port:
            show_message(self, QMessageBox.Icon.Warning, tr("msg_no_port_title"), tr("msg_no_port_body"))
            return
        if self.connected:
            return
        try:
            self.log_event(f"Attempting to open port: {self.current_port}")
            if not self.serial_obj.open(self.current_port, BAUD_RATE):
                raise RuntimeError(f"Failed to open port: {self.serial_obj.last_error}")
            self.connected = True
            self.log_event(f"Port opened successfully: {self.current_port}")

            # Configure meter (thread not started yet — brief sleeps are safe here)
            self.log_event("Configuring meter...")
            if not self.serial_obj.sendMsg(":INITIATE:CONTINUOUS ON"):
                raise RuntimeError(f"Failed to send INITIATE command: {self.serial_obj.last_error}")
            time.sleep(0.1)
            if not self.serial_obj.sendMsg(":TRIGGER:SOURCE IMM"):
                raise RuntimeError(f"Failed to send TRIGGER command: {self.serial_obj.last_error}")
            time.sleep(0.1)
            if not self.serial_obj.sendMsg("HOLD:AUTO ON"):
                raise RuntimeError(f"Failed to send HOLD command: {self.serial_obj.last_error}")
            time.sleep(0.1)
            # Reset stability state
            self.previous_numeric = None
            self.previous_raw = None
            self.consecutive_same = 0
            self.consecutive_timeouts = 0

            self.append_log(tr("log_auto_hold"))
            self.log_event("Device configured and polling started")
            self._show_reading("idle", status=tr("status_ready")
                               + (self._probe_hint() or tr("status_probe_part")))
            # Start background poll thread — all serial I/O moves off the main thread
            self.poll_thread = PollWorkerThread(
                self.serial_obj, POLL_INTERVAL_MS, health_check_interval=30
            )
            self.poll_thread.result_ready.connect(self.on_fetch_result)
            self.poll_thread.health_ok.connect(
                lambda idn: self.log_event(f"Health check passed: {idn}")
            )
            self.poll_thread.health_fail.connect(self.handle_comm_error)
            self.poll_thread.start()

            self.retry_upload_timer.start(5000)   # Check every 5 s; DBUploadManager gates actual retries via backoff
        except Exception as e:
            self.log_event(f"Connection failed: {e}")
            show_message(self, QMessageBox.Icon.Critical, tr("msg_conn_error_title"), str(e))
            self.connected = False

    def on_fetch_result(self, msg):
        """Process a FETC? result emitted by PollWorkerThread (runs on main thread)."""
        if not self.connected:
            return

        now = datetime.now()
        time_str = now.strftime("%H:%M:%S.%f")[:-3]

        if msg == "Timeout Error":
            self.consecutive_same = 0
            self.consecutive_timeouts += 1
            self.log_event(f"FETC? timeout #{self.consecutive_timeouts}")
            self.append_log(tr("log_error", time=time_str, msg=msg))
            if self.consecutive_timeouts >= self.max_consecutive_timeouts:
                self.log_event(f"Max consecutive timeouts reached ({self.consecutive_timeouts}), triggering reconnect")
                self.handle_comm_error("Multiple timeouts detected")
            return

        if isinstance(msg, str) and msg.startswith("Error"):
            self.consecutive_same = 0
            self.consecutive_timeouts += 1
            self.log_event(f"FETC? error: {msg}")
            self.append_log(tr("log_error", time=time_str, msg=msg))
            self.handle_comm_error(msg)
            return

        # Successful read - reset timeout counter
        self.consecutive_timeouts = 0

        record = False
        stable_eps = 1e-9
        try:
            current_val = float(msg)
            # Over-range == open circuit (probes lifted). Hold the last reading,
            # or show WAITING when hold is toggled off.
            if abs(current_val) > MAX_VALID_OHMS:
                self.consecutive_same = 0
                self.previous_numeric = None
                self.previous_raw = None
                self._show_waiting()
                return
            if self.previous_numeric is None or abs(current_val - self.previous_numeric) >= stable_eps:
                self.previous_numeric = current_val
                self.consecutive_same = 1
            else:
                self.consecutive_same += 1
                if self.consecutive_same == 2:
                    record = True
        except ValueError:
            # fallback raw compare
            if self.previous_raw is None or msg != self.previous_raw:
                self.previous_raw = msg
                self.consecutive_same = 1
            else:
                self.consecutive_same += 1
                if self.consecutive_same == 2:
                    record = True

        # Update measurement display. Colour tells the operator what the number
        # means: amber = settling, white = stable (about to record), green/red =
        # recorded (set below). In Release mode a recorded value the meter is
        # still auto-holding waiting_delay_s later means open circuit → WAITING.
        if (not self.hold_previous and self.consecutive_same >= 3
                and time.time() - self._recorded_at >= self.waiting_delay_s):
            self._show_waiting()
            return
        try:
            value = float(msg)
            self.tc_tab.set_rt(value)            # live Rt for the TC screen / chart
            value = self._display_resistance(value)
            if self.consecutive_same == 1:
                self._display_value, self._display_range = value, (None, None)
                self._show_reading("settling", value, tr("status_settling"))
            elif record:
                self._show_reading("stable", value, tr("status_stable"))
            # consecutive_same >= 3: the meter is auto-holding the reading
            # just recorded — keep its PASS/FAIL colour on screen.
        except ValueError:
            if not self.hold_previous:
                self._show_waiting()

        if record:
            self._record_reading(msg, time_str)

    # ── Recording a stable reading (judge → NG reconfirm or commit) ───────────

    def _record_reading(self, msg, time_str):
        """A reading became stable: judge it against the current point (on Rt₀
        when temperature correction is on). An NG starts / continues the NG
        reconfirm instead of being uploaded; anything else is committed."""
        point = self.current_point()
        if point is not None:
            index = self.current_point_index
            p_name, p_seq = point["name"], point["seq"]
            p_lower, p_upper = point["lower"], point["upper"]
        else:
            index = p_name = p_seq = p_lower = p_upper = None

        try:
            rt_val = float(msg)
        except ValueError:
            rt_val = None
        judge_value = msg
        if rt_val is not None:
            judge_value, tc_note = self._tc_value(rt_val)
            if judge_value is None:
                # TC on but not applicable: a raw Rt stored in the same column
                # would be indistinguishable from a corrected one — don't record.
                self.log_event(f"TC: reading {msg} NOT recorded — {tc_note}")
                self.append_log(tr("log_tc_not_recorded", note=tc_note))
                self.set_judgement_status(None, tc_note)
                return
        pass_result, result_text = (self.compare_spec(judge_value, p_lower, p_upper)
                                    if point is not None else self.compare_spec(judge_value))

        # Ensure model is set; if empty, prompt once at record time
        if not self.cleaned_model.strip():
            text, ok = self._prompt_model_text()
            if ok:
                cleaned_model = self._decode_model_text(text)
                if cleaned_model:
                    self.log_model_change("CHANGE", "", cleaned_model, "prompt")
                    self.cleaned_model = cleaned_model
                    self._set_model_text(cleaned_model)
                    self.load_model_spec()
                else:
                    self.append_log(tr("log_model_not_changed", n=MODEL_MAX_LEN))

        try:
            value = round(float(judge_value), 3)
            value_text = f"{value:.3f}"
        except (TypeError, ValueError):
            value, value_text = msg, str(msg)
        info = {
            "index": index, "p_name": p_name, "p_seq": p_seq,
            "p_lower": p_lower, "p_upper": p_upper,
            "lo": self.lower_limit if p_lower is None else p_lower,
            "hi": self.upper_limit if p_upper is None else p_upper,
            "value": value, "value_text": value_text,
            "result_text": result_text, "time_str": time_str,
        }

        if pass_result is False and self.ng_reconfirm_max > 0:
            self._ng_attempt(info)
            return
        if self._reconfirm is not None:
            # The re-measure passed: the NG is cancelled, nothing NG is sent.
            self._reconfirm = None
            self._close_ng_dialog()
            self.log_event("NG reconfirm: re-measure passed — NG cancelled")
            self.append_log(tr("log_ng_cleared"))
        self._commit_result(info, pass_result)

    def _commit_result(self, info, pass_result, force_upload=False):
        """Final result of a point: CSV + DB upload, counters, verdict, card
        colour, then advance to the next point."""
        p_name, p_seq = info["p_name"], info["p_seq"]
        p_lower, p_upper = info["p_lower"], info["p_upper"]
        value, value_text, time_str = info["value"], info["value_text"], info["time_str"]
        cleaned_model = self.cleaned_model.strip()
        current_time = datetime.now()
        can_insert = force_upload or (self.last_db_insert_time is None or
                                      (current_time - self.last_db_insert_time).total_seconds() >= 5)
        status_for_db = {True: "OK", False: "NG"}.get(pass_result, "N/A")

        try:
            self.append_csv_row(current_time, value, status_for_db,
                                cleaned_model, point=p_name, seq=p_seq,
                                lower=p_lower, upper=p_upper, db_status="pending")
            csv_status = tr("csv_ok")
            # Histogram mirrors today's CSV: new day → start from the new file.
            if self._tc_hist_date != current_time.date():
                self._load_tc_history()
            elif isinstance(value, (int, float)):
                self.tc_tab.add_reading(p_seq, p_name, value)
        except Exception as e:
            csv_status = tr("csv_error", e=e)
            print(f"CSV write error: {e}")

        if cleaned_model and can_insert:
            try:
                self.db_manager.upload_async(cleaned_model, value, status_for_db,
                                             self.on_upload_complete, point=p_name, seq=p_seq,
                                             lower=p_lower, upper=p_upper)
                self.last_db_insert_time = current_time
                db_status = tr("db_uploading")
            except Exception as e:
                db_status = tr("db_error", e=e)
                print(f"Database upload error: {e}")
        elif not cleaned_model:
            db_status = tr("db_no_model")
        else:
            db_status = tr("db_wait")

        self.append_result(time_str, p_name, value_text, pass_result, info["result_text"],
                           f"{csv_status} {db_status}")
        self._count_result(status_for_db, cleaned_model)

        lo, hi = info["lo"], info["hi"]
        where = f"{p_name} · " if p_name else ""
        self.set_judgement_status(
            pass_result,
            f"{where}{value_text} Ω  ({lo:.{SPEC_DECIMALS}f} – {hi:.{SPEC_DECIMALS}f})"
            f"  ·  {time_str[:8]}")

        # The card keeps this point's value + colour until it is measured again.
        if self.spec_points and info["index"] is not None:
            self.point_results[info["index"]] = (pass_result, value_text)
            self.advance_point()   # wraps to point 1; refreshes the cards

        verdict = {True: "PASS", False: "FAIL"}.get(pass_result, "N/A")
        state = {True: "pass", False: "fail"}.get(pass_result, "stable")
        self._display_value = value
        self._display_range = (lo, hi)
        self._recorded_at = time.time()
        self._show_reading(state, value, tr("status_recorded", where=where, verdict=verdict)
                           + (tr("status_next", name=self.current_point()['name'])
                              if self.spec_points else ""),
                           lo, hi)

    # ── NG reconfirm ──────────────────────────────────────────────────────────

    def _ng_attempt(self, info):
        """An NG reading: open / update the reconfirm dialog. The NG is written
        to the CSV as an attempt only; it is uploaded once confirmed."""
        rc = self._reconfirm
        if rc is None or rc["index"] != info["index"]:
            rc = self._reconfirm = dict(info, n=0)
        else:
            n = rc["n"] + 1
            rc.update(info)
            rc["n"] = n
        n, mx = rc["n"], self.ng_reconfirm_max
        p_name = info["p_name"]
        where = f"{p_name} · " if p_name else ""
        try:
            self.append_csv_row(datetime.now(), info["value"], "NG", self.cleaned_model.strip(),
                                point=p_name, seq=info["p_seq"], lower=info["p_lower"],
                                upper=info["p_upper"], db_status=f"reconfirm {n}/{mx}")
        except Exception as e:
            print(f"CSV write error: {e}")
        self.log_event(f"NG reconfirm {n}/{mx}: {where}{info['value_text']} Ω")
        self.append_log(tr("log_ng_reconfirm", where=where, value=info["value_text"], n=n, max=mx))
        if info["index"] is not None:
            self.point_results[info["index"]] = (False, info["value_text"])
            self._apply_point_to_ui()
        lo, hi = info["lo"], info["hi"]
        self.set_judgement_status(
            False, f"{where}{info['value_text']} Ω  ({lo:.{SPEC_DECIMALS}f} – "
                   f"{hi:.{SPEC_DECIMALS}f})  ·  " + tr("ng_count", n=n, max=mx))
        self._display_value = info["value"]
        self._display_range = (lo, hi)
        self._recorded_at = time.time()
        self._show_reading("fail", info["value"],
                           tr("status_reconfirm", name=p_name or "", n=n, max=mx), lo, hi)
        if n >= mx:
            self._confirm_ng()
            return
        if self.ng_dialog is None:
            self.ng_dialog = NgReconfirmDialog(self)
            self.ng_dialog.confirm_clicked.connect(self._confirm_ng)
        self.ng_dialog.show_state(p_name, info["value_text"], lo, hi, n, mx)

    def _confirm_ng(self):
        """Confirm NG (button or the last attempt): upload the last NG value."""
        rc = self._reconfirm
        if rc is None:
            return
        self._reconfirm = None
        self._close_ng_dialog()
        where = f"{rc['p_name']} · " if rc["p_name"] else ""
        self.log_event(f"NG confirmed after {rc['n']} re-measure(s): {where}{rc['value_text']} Ω")
        self.append_log(tr("log_ng_confirmed", where=where, value=rc["value_text"]))
        self._commit_result(rc, False, force_upload=True)

    def _cancel_reconfirm(self):
        """Point / model changed while an NG was pending: drop it (the attempts
        stay in the CSV), nothing is uploaded."""
        if self._reconfirm is None:
            return
        self._reconfirm = None
        self._close_ng_dialog()
        self.log_event("NG reconfirm cancelled (point or model changed)")
        self.append_log(tr("log_ng_discarded"))

    def _close_ng_dialog(self):
        if self.ng_dialog is not None:
            self.ng_dialog.hide()

    def _blink_tick(self):
        self._blink_on = not self._blink_on
        alarm = self._reconfirm is not None
        if 0 <= self.current_point_index < len(self.point_cards):
            self.point_cards[self.current_point_index].set_blink(self._blink_on, alarm)

    # ── Waiting time (operator setting) ───────────────────────────────────────

    def _apply_waiting_ui(self):
        self.ui.btn_waiting.setText(tr("btn_waiting", s=f"{self.waiting_delay_s:g}"))

    def on_waiting_clicked(self):
        dlg = NumpadDialog(current_value=self.waiting_delay_s, decimals=1,
                           title=tr("title_waiting"), min_val=0.0,
                           max_val=WAITING_DELAY_MAX_S, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.waiting_delay_s = float(dlg.get_value())
            self._apply_waiting_ui()
            self.save_config()
            self.log_event(f"Waiting time set to {self.waiting_delay_s:g} s")
            self.append_log(tr("log_waiting_set", s=f"{self.waiting_delay_s:g}"))

    # ── Temperature Correction screen + Object Temperature chip ───────────────

    def _build_tc_page(self, scale):
        px = lambda v: max(1, round(v * scale))
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(px(14), px(14), px(14), px(14))
        lay.setSpacing(px(10))
        head = QHBoxLayout()
        self.btn_tc_back = QPushButton()
        self.btn_tc_back.setProperty("role", "tool")
        self.btn_tc_back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_tc_back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        self.label_tc_title = QLabel()
        self.label_tc_title.setStyleSheet(
            f"color:{UI_COLORS['accent_hi']};font-size:{px(24)}px;font-weight:bold;")
        head.addWidget(self.btn_tc_back)
        head.addSpacing(px(16))
        head.addWidget(self.label_tc_title)
        head.addStretch(1)
        lay.addLayout(head)
        self.tc_tab = TempCorrectionTab(page)
        lay.addWidget(self.tc_tab, 1)
        self.tc_tab.settings_changed.connect(self.on_tc_settings_changed)
        self.tc_tab.axis_changed.connect(self.on_tc_axis_changed)
        self.tc_tab.apply_scale(scale)
        self.stack.addWidget(page)
        self._retranslate_tc_page()

    def _retranslate_tc_page(self):
        self.btn_tc_back.setText(tr("btn_back"))
        self.label_tc_title.setText(tr("tc_title"))

    def _on_temp_sensor_status(self, text):
        self._temp_sensor_status = text
        self._update_temp_chip()

    def _update_temp_chip(self):
        if self._temp_is_fresh():
            text, state = f"{self.last_temp:.1f} °C", "ok"
        else:
            text = "-- °C"
            st = self._temp_sensor_status
            state = "bad" if any(k in st for k in ("fault", "over", "disconnected", "error")) else "off"
        if self.ui.label_temp.text() != text:
            self.ui.label_temp.setText(text)
        set_prop(self.ui.label_temp, "state", state)

    def _refresh_tc_tag(self):
        """Point index (+ TC state when TC is on) in the measured panel header."""
        tag = ""
        if self.tc.get("enabled"):
            tag = (tr("tc_tag_on", t0=f"{self.tc['t0']:.1f}") if self._temp_is_fresh()
                   else tr("tc_tag_notemp"))
        text = "  ·  ".join(x for x in (self._point_index_base, tag) if x)
        if self.ui.label_point_index.text() != text:
            self.ui.label_point_index.setText(text)

    def _load_model_tc(self, model):
        """Load a model's TC settings from config (defaults: OFF, 20 °C, 3930 ppm)."""
        raw = None
        if model:
            try:
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, 'r') as f:
                        raw = json.load(f).get("models", {}).get(model, {}).get("tc")
            except Exception as e:
                print(f"Error loading TC settings: {e}")
        self.tc = normalize_tc_settings(raw)
        if not model:
            self.tc["enabled"] = False   # TC is per model; nothing to apply to
        self.tc_tab.set_model(model)
        self.tc_tab.load_settings(self.tc["enabled"], self.tc["t0"], self.tc["alpha_ppm"])
        self.tc_tab.set_axis_temperature_on_x(self.tc_x_is_temp)
        self._refresh_tc_tag()

    def _save_model_tc(self, model, tc):
        """Persist a model's TC settings (merged, like _cache_points)."""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
            else:
                config = {}
            config.setdefault("models", {}).setdefault(model, {})
            config["models"][model]["tc"] = tc
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving TC settings: {e}")

    def on_tc_settings_changed(self, enabled, t0, alpha_ppm):
        model = self.cleaned_model.strip()
        if not model:
            self.append_log(tr("log_tc_set_model"))
            self.tc_tab.load_settings(False, t0, alpha_ppm)
            return
        self.tc = normalize_tc_settings(
            {"enabled": enabled, "t0": t0, "alpha_ppm": alpha_ppm})
        self._save_model_tc(model, self.tc)
        state = "ON" if self.tc["enabled"] else "OFF"
        self.log_event(f"TC for '{model}': {state}, t0={self.tc['t0']:.1f} °C, "
                       f"alpha={self.tc['alpha_ppm']} ppm/°C")
        self.append_log(tr("log_tc_state", state=state, t0=f"{self.tc['t0']:.1f}",
                           alpha=self.tc['alpha_ppm']))
        self._refresh_tc_tag()

    def on_tc_axis_changed(self, x_is_temp):
        self.tc_x_is_temp = x_is_temp
        self.save_config()

    def _update_tc_axis(self):
        """Fix the TC chart's resistance axis to the model's whole spec band, so
        it doesn't move while the sequence auto-advances point to point."""
        if getattr(self, "tc_tab", None) is None:
            return
        if self.spec_points:
            lo = min(p["lower"] for p in self.spec_points)
            hi = max(p["upper"] for p in self.spec_points)
            spec = [{"seq": p["seq"], "name": p["name"],
                     "lower": p["lower"], "upper": p["upper"]} for p in self.spec_points]
        else:
            lo, hi = self.lower_limit, self.upper_limit
            spec = [{"seq": None, "name": "All readings", "lower": lo, "upper": hi}]
        self.tc_tab.set_spec_range(lo, hi)
        model = self.cleaned_model.strip()
        self.tc_tab.set_hist_spec(spec, f"Today · {model}" if model else "Today")

    def _load_tc_history(self):
        """Fill the TC histogram with today's final results for the model
        (NG re-measure attempts are skipped)."""
        if getattr(self, "tc_tab", None) is None:
            return
        model = self.cleaned_model.strip()
        now = datetime.now()
        self._tc_hist_date = now.date()
        rows = []
        path = self.get_daily_csv_path(now)
        if model and os.path.exists(path):
            try:
                with open(path, newline="", encoding="utf-8") as f:
                    for r in csv.reader(f):
                        if len(r) < 4 or r[0] == "Timestamp" or r[3].strip() != model:
                            continue
                        if len(r) > 10 and r[10].startswith("reconfirm"):
                            continue
                        try:
                            value = float(r[1])
                        except ValueError:
                            continue
                        seq = name = None
                        if len(r) >= len(CSV_HEADERS):
                            name = r[4] or None
                            try:
                                seq = int(r[5]) if r[5] != "" else None
                            except ValueError:
                                seq = None
                        rows.append((seq, name, value))
            except Exception as e:
                self.log_event(f"Histogram: could not read {os.path.basename(path)}: {e}")
        self.tc_tab.set_history(rows)

    def _temp_is_fresh(self):
        return (self.last_temp is not None
                and time.time() - self.last_temp_ts <= TEMP_STALE_S)

    def _tc_value(self, rt):
        """Value to judge/record for a raw reading Rt: (value, note). value is
        None when TC is ON but cannot be applied."""
        if not self.tc["enabled"]:
            return rt, ""
        if not self._temp_is_fresh():
            return None, "no fresh temperature from the sensor"
        t0, alpha = self.tc["t0"], self.tc["alpha_ppm"]
        rt0 = correct_resistance(rt, self.last_temp, t0, alpha)
        if rt0 is None:
            return None, "invalid correction (1 + α(t − t₀) ≤ 0)"
        return rt0, f"Rt {rt:.3f} Ω @ {self.last_temp:.1f} °C → {t0:.1f} °C"

    def _display_resistance(self, rt):
        """What the big measured number shows: Rt₀ when TC applies, else Rt."""
        if self.tc["enabled"] and self._temp_is_fresh():
            rt0 = correct_resistance(rt, self.last_temp, self.tc["t0"], self.tc["alpha_ppm"])
            if rt0 is not None:
                return rt0
        return rt

    # ── RS485 temperature sensor ──────────────────────────────────────────────

    def start_temp_sensor(self):
        cfg = self.temp_sensor_cfg
        if not cfg.get("enabled", True):
            self._on_temp_sensor_status("sensor disabled")
            self.tc_tab.set_sensor_status("disabled in config")
            return
        if rs485 is None:
            self._on_temp_sensor_status("sensor unavailable")
            self.tc_tab.set_sensor_status("unavailable (pymodbus/matplotlib missing)")
            self.log_event(f"Temperature sensor unavailable: {_RS485_IMPORT_ERROR}")
            return
        try:
            self.temp_thread = rs485.ModbusReaderThread(
                port=cfg["port"], baudrate=int(cfg["baud"]),
                slave_id=int(cfg["slave_id"]), register_address=int(cfg["register"]),
                register_count=1, poll_interval=float(cfg["poll_interval"]),
                raw_at_4ma=float(cfg["raw_at_4ma"]), raw_at_20ma=float(cfg["raw_at_20ma"]),
                temp_at_4ma=float(cfg["temp_at_4ma"]), temp_at_20ma=float(cfg["temp_at_20ma"]),
            )
        except Exception as e:
            self.tc_tab.set_sensor_status(f"config error: {e}")
            self.log_event(f"Temperature sensor config error: {e}")
            return
        self.temp_thread.reading.connect(self.on_temp_reading)
        self.temp_thread.status.connect(self.tc_tab.set_sensor_status)
        self.temp_thread.status.connect(self._on_temp_sensor_status)
        self.temp_thread.error.connect(self.on_temp_error)
        self.temp_thread.start()
        self.log_event(f"Temperature sensor started on {cfg['port']} "
                       f"(slave {cfg['slave_id']}, reg {cfg['register']}, "
                       f"4-20 mA = {cfg['temp_at_4ma']}-{cfg['temp_at_20ma']} °C)")

    def stop_temp_sensor(self):
        if self.temp_thread is not None:
            self.temp_thread.stop()
            self.temp_thread.wait(2000)
            self.temp_thread = None

    def on_temp_reading(self, ts, temperature_c):
        self.last_temp = temperature_c
        self.last_temp_ts = ts
        self._last_temp_error = ""
        self.tc_tab.set_temperature(temperature_c, ts)
        self._update_temp_chip()
        self._refresh_tc_tag()

    def simulate_temperature(self):
        """Ctrl+T: type an object temperature by hand (no sensor attached). It
        goes stale after TEMP_STALE_S like a real reading."""
        start = self.last_temp if self.last_temp is not None else 25.0
        dlg = NumpadDialog(current_value=start, decimals=1,
                           title="Simulated object temperature t (°C)",
                           min_val=-40.0, max_val=125.0, parent=self,
                           allow_negative=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            t = dlg.get_value()
            self.on_temp_reading(time.time(), t)
            self.tc_tab.set_sensor_status("SIMULATED (Ctrl+T)")
            self.log_event(f"Simulated temperature: {t:.1f} °C")

    def on_temp_error(self, message):
        # The reader retries every few seconds; only log when the error changes.
        if message != self._last_temp_error:
            self._last_temp_error = message
            self.log_event(f"Temperature sensor: {message}")
        self._refresh_tc_tag()

    # ── Safeguard: the kiosk window never grows past the screen ──────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # A window that grows past the screen never shrinks back on its own
        # (seen once at 1086 px on the 1024 px kiosk) — snap it back. Whole
        # screen, not availableGeometry(): the window covers the desktop panel.
        geo = self.screen().geometry()
        if ((self.width() > geo.width() or self.height() > geo.height())
                and not getattr(self, "_snap_pending", False)):
            self._snap_pending = True
            QTimer.singleShot(0, self._snap_to_screen)

    def _snap_to_screen(self):
        self._snap_pending = False
        geo = self.screen().geometry()
        if self.width() > geo.width() or self.height() > geo.height():
            self.log_event(f"Window grew to {self.width()}x{self.height()} — "
                           f"snapped back to the screen {geo.width()}x{geo.height()}")
            self.setGeometry(geo)

    def on_upload_complete(self, success, error_msg):
        """Callback when async upload completes (runs on main thread)."""
        self._set_db_online(success)
        if success:
            self.log_event("Database upload successful")
            # Server just confirmed reachable — flush any queued records immediately
            pending = self.db_manager.get_pending_count()
            if pending > 0:
                self.log_event(f"Server reachable — flushing {pending} queued record(s)")
                self.append_log(tr("log_server_back", n=pending))
                self.db_manager.retry_pending_uploads()
            # ...and any specs queued while the server was down.
            if self.spec_queue.pending_count() > 0:
                self.spec_queue.flush_async()
        else:
            self.log_event(f"Database upload failed: {error_msg}")
            count, wait, _ = self.db_manager.get_queue_status()
            wait_str = tr("wait_seconds", n=int(wait)) if wait > 1 else tr("wait_soon")
            self.append_log(tr("log_db_unreachable", n=count, wait=wait_str))

    def retry_pending_uploads(self):
        """Timer callback — let DBUploadManager decide whether the backoff window has elapsed."""
        if not self.connected:
            return
        count, wait, reachable = self.db_manager.get_queue_status()
        if count > 0 and self.db_manager.should_retry_now():
            self.log_event(f"Backoff elapsed — attempting batch upload of {count} pending record(s)")
            self.db_manager.retry_pending_uploads()
        # Queued specs ride the same retry window as queued readings.
        if self.db_manager.should_retry_now() and self.spec_queue.pending_count() > 0:
            self.spec_queue.flush_async()

    def on_upload_rejected(self, count, error_msg):
        """The server is up but refused record(s) — parked in failed_uploads.json."""
        self._set_db_online(True)
        self.log_event(f"DB rejected {count} record(s), moved to "
                       f"{DBUploadManager.FAILED_FILE}: {error_msg}")
        self.append_log(tr("log_rejected", n=count, file=DBUploadManager.FAILED_FILE))

    def on_retry_complete(self, success_count, failed_count, remaining_count):
        """Callback when a batch retry finishes (runs on main thread)."""
        self._set_db_online(success_count > 0 or remaining_count == 0)
        if failed_count == 0 and success_count == 0:
            return  # every record was rejected and parked — on_upload_rejected reported it
        if remaining_count == 0 and success_count > 0:
            self.log_event(f"Batch upload complete: {success_count} record(s) sent")
            self.append_log(tr("log_batch_done", n=success_count))
        elif success_count > 0:
            count, wait, _ = self.db_manager.get_queue_status()
            wait_str = tr("wait_seconds", n=int(wait))
            self.log_event(f"Partial batch: {success_count} uploaded, {remaining_count} still pending")
            self.append_log(tr("log_partial", ok=success_count, left=remaining_count, wait=wait_str))
        else:
            count, wait, _ = self.db_manager.get_queue_status()
            wait_str = tr("wait_seconds", n=int(wait))
            self.log_event(f"Batch retry failed — {remaining_count} record(s) still pending")
            self.append_log(tr("log_retry_failed", n=remaining_count, wait=wait_str))

    def on_upload_now_clicked(self):
        """Force an immediate flush of pending_uploads.json — no timer/backoff wait."""
        pending = self.db_manager.get_pending_count()
        specs = self.spec_queue.pending_count()
        if pending == 0 and specs == 0:
            self.log_event("Upload Now: nothing pending")
            self.append_log(tr("log_queue_empty"))
            return
        if self.db_manager.is_uploading:
            self.append_log(tr("log_upload_busy"))
            return
        self.log_event(f"Upload Now: forcing flush of {pending} reading(s), {specs} spec(s)")
        self.append_log(tr("log_uploading_now", n=pending))
        # force=True bypasses the backoff window; the batch runs off-thread and
        # reports back through on_retry_complete.
        self.db_manager.retry_pending_uploads(force=True)
        if specs > 0:
            self.spec_queue.flush_async()

    def _touch_heartbeat(self):
        """Write epoch seconds to the heartbeat file (throttled) so watchdog.sh
        can tell 'app alive but readings frozen' apart from a healthy run."""
        now = time.time()
        if now - self._last_heartbeat_write < HEARTBEAT_MIN_INTERVAL_S:
            return
        self._last_heartbeat_write = now
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(str(int(now)))
        except Exception as e:
            self.log_event(f"Heartbeat write failed: {e}")

    def check_poll_liveness(self):
        """UI-thread stall detector. A wedged serial call freezes the poll worker
        mid-syscall (uninterruptible D-state) so its liveness stamp stops
        advancing; catch that here and soft-recover without closing the app.

        This timer fires every STALL_CHECK_MS as long as the Qt event loop is
        alive, so it also drives the watchdog heartbeat — a true 'app alive'
        signal that stays fresh even while the meter is unplugged and the app is
        merely waiting to reconnect."""
        self._touch_heartbeat()
        self._refresh_db_status()   # queue grows silently as readings are queued
        # Release any parked orphan whose OS thread the kernel has finally freed.
        if self._orphaned_threads:
            self._orphaned_threads = [
                t for t in self._orphaned_threads if t.isRunning()]
        if not self.connected or self.poll_thread is None:
            return
        if not self.poll_thread.isRunning():
            return
        age = time.time() - self.poll_thread.last_cycle_ts
        if age > STALL_LIMIT_S:
            self.handle_poll_stall(age)

    def handle_poll_stall(self, age):
        """The poll worker stopped advancing — treat as a serial hardware hang.
        Orphan the (frozen, unkillable) thread and rebuild on a fresh handle."""
        self.log_event(
            f"Serial hardware hang detected (no poll for {age:.1f}s) — reconnecting")
        self.append_log(tr("log_serial_hang"))
        self._set_usb_status("connecting")

        # Orphan the wedged worker: signal it to stop (it obeys if/when the kernel
        # frees it) and PARK it so PySide6 doesn't abort on a GC'd running QThread.
        # Never wait()/join a frozen thread — that would freeze the UI too.
        wedged = self.poll_thread
        if wedged is not None:
            wedged.stop()
            self._orphaned_threads.append(wedged)
        self.poll_thread = None

        # Abandon the wedged serial handle for a brand-new one. close() on a dead
        # device can itself block in D, so do it fire-and-forget off the UI thread.
        old_serial = self.serial_obj
        Thread(target=lambda: self._safe_close(old_serial), daemon=True).start()
        self.serial_obj = Usb_rs(gui=True)

        # Reset connection state and route through the normal re-detection path.
        self.connected = False
        self.current_port = None
        self.previous_numeric = None
        self.previous_raw = None
        self.consecutive_timeouts = 0
        retry_ms = int(self.reconnect_delay * 1000)
        self.log_event(f"Scheduling reconnection attempt in {self.reconnect_delay:.1f}s")
        self.detect_retry_timer.start(retry_ms)

    @staticmethod
    def _safe_close(serial_obj):
        try:
            serial_obj.close()
        except Exception:
            pass

    def handle_comm_error(self, msg):
        """Recover from serial I/O failures by resetting connection and retrying detection."""
        if not self.connected:
            return

        error_lower = msg.lower() if isinstance(msg, str) else ""
        reconnect_keywords = (
            "input/output error",
            "i/o error",
            "write failed",
            "read failed",
            "device",
            "disconnected",
            "invalid handle",
            "port",
            "no such device",
            "bad file descriptor",
            "broken pipe",
            "permission denied",
            "resource busy",
            "multiple timeouts",
            "health check failed",
            "write timeout",
        )

        trigger_reconnect = any(k in error_lower for k in reconnect_keywords)
        if trigger_reconnect:
            self.log_event(f"Communication error detected: {msg} - Initiating reconnection")
            self._stop_poll_thread()
            self._close_serial_connection()
            self.connected = False
            self.current_port = None
            self.previous_numeric = None
            self.previous_raw = None
            self.consecutive_timeouts = 0
            self._set_usb_status("connecting")
            # Use exponential backoff for retry
            retry_ms = int(self.reconnect_delay * 1000)
            self.log_event(f"Scheduling reconnection attempt in {self.reconnect_delay:.1f}s")
            self.detect_retry_timer.start(retry_ms)
        else:
            self.log_event(f"Non-critical error (no reconnect): {msg}")

    def _stop_poll_thread(self):
        if self.poll_thread is not None:
            self.poll_thread.stop()
            # Park rather than drop: if the thread is mid-syscall, GC'ing a live
            # QThread aborts PySide6. check_poll_liveness prunes it once freed.
            self._orphaned_threads.append(self.poll_thread)
            self.poll_thread = None

    def _close_serial_connection(self):
        try:
            self.log_event("Closing serial connection")
            self.serial_obj.close()
        except Exception as e:
            self.log_event(f"Error during connection close: {e}")

    def stop_mode(self):
        self.log_event("Measurement stopped by user")
        self._stop_poll_thread()
        self.retry_upload_timer.stop()
        self._close_serial_connection()
        self.connected = False
        self._set_usb_status("disconnected")

    def closeEvent(self, event):
        self.log_event("Application closing")
        self.stop_temp_sensor()
        if self.wifi_thread is not None:
            self.wifi_thread.stop()
            self.wifi_thread.wait(3000)
            self.wifi_thread = None
        # Stop poll thread and wait for clean exit before closing the serial port
        if self.poll_thread is not None:
            self.poll_thread.stop()
            self.poll_thread.wait(3000)
            self.poll_thread = None
        if self.spec_fetch_thread is not None:
            self.spec_fetch_thread.wait(3000)
            self.spec_fetch_thread = None
        if self.spec_upsert_thread is not None:
            self.spec_upsert_thread.wait(3000)
            self.spec_upsert_thread = None
        if self.schema_check_thread is not None:
            self.schema_check_thread.wait(3000)
            self.schema_check_thread = None
        self.retry_upload_timer.stop()
        self.detect_retry_timer.stop()
        self._close_serial_connection()
        self.connected = False
        pending_count = self.db_manager.get_pending_count()
        if pending_count > 0:
            self.log_event(f"Application closing with {pending_count} pending uploads (saved for next run)")
        event.accept()

    def _play_sound(self, filename):
        app_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(app_dir, filename)
        if os.path.exists(path):
            self._media_player.stop()
            self._media_player.setSource(QUrl.fromLocalFile(path))
            self._media_player.play()

    def set_judgement_status(self, pass_result, detail=None):
        """Update the PASS/FAIL banner (and play the voice alert)."""
        text, state = {True: ("PASS", "pass"), False: ("FAIL", "fail")}.get(
            pass_result, ("N/A", "na"))
        self.ui.label_verdict.setText(text)
        self.ui.label_verdict_detail.setText(detail or "")
        set_prop(self.ui.frame_verdict, "state", state)
        if pass_result is True:
            self._play_sound("ResistancePass_TH.mp3")
        elif pass_result is False:
            self._play_sound("ResistanceOver_TH.mp3")

    # ── Pass/fail counters (today, current model) ─────────────────────────────

    def _load_counts(self):
        """Seed today's OK/NG counts for the current model from the daily CSV."""
        model = self.cleaned_model.strip()
        today = datetime.now()
        self._counts_key = (model, today.date())
        self.counts = {"OK": 0, "NG": 0}
        path = self.get_daily_csv_path(today)
        if model and os.path.exists(path):
            try:
                with open(path, newline="", encoding="utf-8") as f:
                    for r in csv.reader(f):
                        # Model and Status sit at columns 3 and 2 in every layout.
                        # NG re-measure attempts aren't results; only the final one counts.
                        if len(r) > 10 and r[10].startswith("reconfirm"):
                            continue
                        if len(r) > 3 and r[3].strip() == model and r[2] in self.counts:
                            self.counts[r[2]] += 1
            except Exception as e:
                self.log_event(f"Counter: could not read {os.path.basename(path)}: {e}")
        self._refresh_counts()

    def _count_result(self, status, model):
        if self._counts_key != (model, datetime.now().date()):
            self._load_counts()   # already includes the row just written
        elif status in self.counts:
            self.counts[status] += 1
        self._refresh_counts()

    def _refresh_counts(self):
        ok, ng = self.counts["OK"], self.counts["NG"]
        self.ui.label_count_ok.setText(str(ok))
        self.ui.label_count_ng.setText(str(ng))
        self.ui.label_count_yield.setText(f"{100.0 * ok / (ok + ng):.1f}%" if ok + ng else "—")

    def log_event(self, event_text):
        """Log detailed event to console with timestamp for debugging."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        full_msg = f"[{timestamp}] {event_text}"
        print(full_msg)

    def _log_html(self, html):
        try:
            log = self.ui.textBrowser_log
            bar = log.verticalScrollBar()
            at_bottom = bar.value() >= bar.maximum() - 4
            log.append(html)
            # Don't yank the view while the operator is scrolled back reading.
            if at_bottom:
                bar.setValue(bar.maximum())
        except RuntimeError:
            pass  # Widget already destroyed during shutdown

    def append_log(self, text):
        """Append a system/event line to the data log (warnings in amber)."""
        c = UI_COLORS
        colour = c["warn"] if text.startswith(("!", "⚠")) else c["pass"] if text.startswith("✓") else c["dim"]
        self._log_html(f'<span style="color:{colour}">{escape(text)}</span>')

    def append_result(self, time_str, point, value, pass_result, result_text, note):
        """Append a recorded reading: time · [point] · value · verdict, then
        the CSV/DB outcome in small dim text."""
        c = UI_COLORS
        colour = {True: c["pass"], False: c["fail"]}.get(pass_result, c["na"])
        verdict = {True: "OK", False: "NG"}.get(pass_result, "N/A")
        if pass_result is False and ":" in result_text:
            verdict += " " + result_text.split(":", 1)[1].strip().lower()   # "NG high"
        point_html = f'<span style="color:{c["accent_hi"]}">[{escape(point)}]</span> ' if point else ""
        self.reading_count += 1
        self.ui.label_log_count.setText(tr("log_count", n=self.reading_count))
        self._log_html(
            f'<span style="color:{c["faint"]}">{escape(time_str[:8])}</span> '
            f'{point_html}<b>{escape(str(value))} Ω</b> '
            f'<b style="color:{colour}">{verdict}</b><br>'
            f'<span style="color:{c["faint"]};font-size:small">&nbsp;&nbsp;{escape(note)}</span>')


def main():
    app = QApplication(sys.argv)
    try:
        w = MainWindow()
        w.showFullScreen()
    except Exception as e:
        show_message(None, QMessageBox.Icon.Critical, tr("msg_startup_title"),
                     tr("msg_startup_body", e=e))
        sys.exit(1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
