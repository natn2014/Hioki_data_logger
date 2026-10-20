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
    QScrollArea, QScroller, QWidget
)
from html import escape
from usb_rs import Usb_rs
from insert_resistance2db import (
    insert_to_mssql, fetch_model_spec, upsert_model_spec, check_schema
)
from ui_UI_Resistance import Ui_Dialog, PointCard, build_qss, set_prop, C as UI_COLORS
from numpad_dialog import NumpadDialog
from keyboard_dialog import KeyboardDialog
from db_upload_manager import DBUploadManager, UploadSignals, SpecQueueManager

BAUD_RATE = 9600
POLL_INTERVAL_MS = 500  # default polling interval for FETC?
CONFIG_FILE = "gui_mode5_config.json"
MAX_VALID_OHMS = 1e12  # ignore readings above this magnitude
CSV_HEADERS = ["Timestamp", "Resistance", "Status", "Model",
               "Point", "Seq", "LowerLimit", "UpperLimit",
               "Date", "Time", "DB_Status"]
MODEL_CHANGE_LOG = "model_changes.csv"  # persistent record of every model switch
SPEC_DECIMALS = 2  # decimal places for spec limits (entry, storage, and display)
# Release mode: with HOLD:AUTO ON the meter keeps returning the same held value
# on every FETC? after the probes are lifted (its over-range/OL comes much
# later), so a value still repeating this long after it was recorded is treated
# as open circuit → WAITING. A real over-range waits out the same time, so the
# recorded value is always readable for RELEASE_AFTER_S first.
RELEASE_AFTER_S = 3.0

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

# USB status chip: state -> (text, style state).
USB_STATES = {
    "connected":    ("● Connected",    "ok"),
    "connecting":   ("◌ Connecting",   "warn"),
    "disconnected": ("○ Disconnected", "bad"),
}


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


def decode_model_text(raw):
    """Unified decode for all barcode / manual-entry formats.

    Priority:
    1. Dollar-delimited  — PREFIX$[id]MODEL$SUFFIX  (label-printer format)
    2. AIM Code 39 Extended — /X escape sequences   (USB HID scanner format)
    3. Plain text — returned as-is after strip

    Shared by the main window and the spec-editor dialog so barcode input is
    cleansed the same way everywhere.
    """
    raw = raw.strip()
    if not raw:
        return ''
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


class AutoDetectThread(QThread):
    found = Signal(str, str)  # port, idn
    not_found = Signal()

    def run(self):
        ports = list(serial.tools.list_ports.comports())
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
        self.setWindowTitle("Register / Edit Model Spec")
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

        outer.addWidget(self._caption("MODEL NAME  ·  tap to type, or scan the part barcode"))
        self.model_edit = QLineEdit(model)
        self.model_edit.setPlaceholderText("e.g. 750X — or scan the part barcode")
        self.model_edit.setMaxLength(100)
        self._register_scan_field(self.model_edit)
        outer.addWidget(self.model_edit)

        hdr = QHBoxLayout()
        hdr.addWidget(self._caption("MEASUREMENT POINTS  ·  probed in this order  ·  tap a value to edit"))
        hdr.addStretch(1)
        self.add_btn = QPushButton("＋  Add point")
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
        cancel = QPushButton("Cancel")
        cancel.setProperty("role", "dlg")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        save = QPushButton("✓  Save to DB")
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
        h.addWidget(self._caption("LOWER"))
        h.addWidget(lower_spin)
        h.addWidget(self._caption("UPPER"))
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
                    self._numpad(rec["lower"], "Lower Ω")
                    return True
                if source is rec["upper"].lineEdit():
                    self._numpad(rec["upper"], "Upper Ω")
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
        title = "Model name" if line_edit is self.model_edit else "Point name"
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
            return None, None, "model name is required"
        if not self._rows:
            return None, None, "add at least one measurement point"
        points = []
        seen = set()
        for i, rec in enumerate(self._rows, start=1):
            name = rec["name"].text().strip()
            if not name:
                return None, None, f"point {i}: name is required"
            if name.lower() in seen:
                return None, None, f"point {i}: duplicate name '{name}'"
            seen.add(name.lower())
            lo = rec["lower"].value()
            up = rec["upper"].value()
            if lo >= up:
                return None, None, f"point {i} ({name}): lower must be < upper"
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
        self.start_auto_detect()
        self.run_schema_check()

    def init_ui(self):
        scale = ui_scale()
        # App-wide so the dialogs and QMessageBoxes get the same touch sizing.
        QApplication.instance().setStyleSheet(build_qss(scale))
        self.ui = Ui_Dialog(scale)
        self.ui.setupUi(self)
        self.setWindowTitle("HIOKI Auto Hold Mode (Mode 5)")

        # Limit tiles: tap to edit in single-range mode; locked while a point
        # sequence drives them (see _refresh_limits_ui).
        self.ui.btn_upper.clicked.connect(lambda: self._edit_limit("upper"))
        self.ui.btn_lower.clicked.connect(lambda: self._edit_limit("lower"))

        enable_touch_scroll(self.ui.textBrowser_log)
        enable_touch_scroll(self.ui.point_cards_area)
        self.ui.pushButton_model.clicked.connect(self.on_model_clicked)

        # Point-sequence cards: one tappable card per point. Tapping a card jumps
        # straight to that point. cycle_results holds this part's per-point
        # outcome ({index: (pass, value_text)}), cleared when point 1 is recorded.
        self.point_cards_row = self.ui.point_cards_row
        self.point_cards = []
        self.cycle_results = {}

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
        self._apply_point_to_ui()  # builds cards + sets initial enabled state

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
            self.append_log(f"! {pending_count} value(s) waiting to upload")

        pending_specs = self.spec_queue.pending_count()
        if pending_specs > 0:
            self.log_event(f"Found {pending_specs} pending spec(s) to upload")
            self.append_log(f"! {pending_specs} model spec(s) waiting to upload")

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
        text, style = USB_STATES.get(state, USB_STATES["disconnected"])
        self.ui.label_usb_status.setText(text)
        set_prop(self.ui.label_usb_status, "state", style)
        if state != "connected":
            self._show_reading("idle", status={
                "connecting": "connecting to meter…",
            }.get(state, "meter disconnected — check the USB cable"))

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
            text, style = (f"○ Offline · {queued}" if queued else "○ Offline"), "bad"
        elif queued:
            text, style = f"⬆ {queued} queued", "warn"
        elif self._db_online:
            text, style = "● Online", "ok"
        else:
            text, style = "◌ ---", "off"
        self.ui.label_db.setText(text)
        set_prop(self.ui.label_db, "state", style)
        self.btn_upload_now.setText(f"⬆  Upload ({queued})" if queued else "⬆  Upload now")
        set_prop(self.btn_upload_now, "attention", bool(queued))

    def _set_db_online(self, online):
        self._db_online = online
        self._refresh_db_status()

    def _set_model_text(self, text):
        self.ui.label_model_name.setText(text if text else "Tap to set model")
        set_prop(self.ui.label_model_name, "empty", not text)
        # A previous model's PASS/FAIL must not linger under the new name.
        self.ui.label_verdict.setText("— — —")
        self.ui.label_verdict_detail.setText("no reading yet for this model")
        set_prop(self.ui.frame_verdict, "state", "idle")
        self._load_counts()

    def _refresh_limits_ui(self, editable):
        """Show self.lower_limit/upper_limit on the tiles (and the gauge)."""
        tag = "TAP TO EDIT" if editable else "LOCKED"
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
            self.append_log("Limits come from the point spec — use ✎ Edit spec to change them")
            return
        current = self.upper_limit if which == "upper" else self.lower_limit
        dlg = NumpadDialog(
            current_value=current, decimals=SPEC_DECIMALS,
            title="Upper Limit (Ω)" if which == "upper" else "Lower Limit (Ω)",
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
        status = "waiting · open circuit" + self._probe_hint()
        if self.hold_previous and self._display_value is not None:
            self._show_reading("standby", self._display_value,
                               status + " · holding last reading", *self._display_range)
        elif time.time() - self._recorded_at < RELEASE_AFTER_S:
            return   # keep the just-recorded value readable a moment longer
        else:
            self._show_reading("standby", "WAITING", status)

    def _probe_hint(self):
        p = self.current_point()
        return f" · probe {p['name']}" if p else ""

    def start_auto_detect(self):
        if self.connected or self.detect_in_progress:
            return
        self.detect_in_progress = True
        self.reconnect_delay = min(self.reconnect_delay * 1.5, self.max_reconnect_delay)  # Exponential backoff
        self.log_event(f"Auto-detect starting (reconnect delay: {self.reconnect_delay:.1f}s)")
        self._set_usb_status("connecting")
        self.det_thread = AutoDetectThread()
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

        # Fall back to model change log when config has no model (crash / missing file)
        if not self.cleaned_model:
            recovered = self._recover_model_from_log()
            if recovered:
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
            if "models" not in config:
                config["models"] = {}
            if self.cleaned_model:
                config["models"][self.cleaned_model] = {
                    "lower_limit": round(self.lower_limit, 3),
                    "upper_limit": round(self.upper_limit, 3)
                }

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
            self.append_log("! Lower limit is not below the upper limit — every reading will FAIL")
        self._refresh_limits_ui(editable=True)
        self.save_config()

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
        dlg = KeyboardDialog(current_text=preset, title="Model name",
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
        if self.spec_points:
            self.current_point_index = 0
            self._apply_point_to_ui()

    def _apply_hold_toggle_ui(self):
        """Sync the toggle button's checked state + label with hold_previous."""
        self.btn_hold_toggle.blockSignals(True)
        self.btn_hold_toggle.setChecked(self.hold_previous)
        self.btn_hold_toggle.blockSignals(False)
        # Label states the *current* mode; checked (highlighted) = hold.
        self.btn_hold_toggle.setText("◼  Hold reading" if self.hold_previous
                                     else "↧  Release")

    def on_hold_toggle(self, checked):
        """Toggle whether the measure display holds the last reading or releases
        to 0 while the meter is in standby (probes lifted / over-range)."""
        self.hold_previous = checked
        self._apply_hold_toggle_ui()
        mode = "hold last reading" if checked else "release (show WAITING)"
        self.log_event(f"Measure display mode: {mode}")
        self.append_log(f"Display mode → {mode}")
        # In release mode, replace a stale held value with WAITING immediately so
        # the change is visible without waiting for the next standby poll.
        if not checked:
            self._show_waiting()
        self.save_config()

    def on_point_card_tapped(self, index):
        """Operator tapped a point card — jump straight to that point."""
        if not self.spec_points or not (0 <= index < len(self.spec_points)):
            return
        self.current_point_index = index
        self._apply_point_to_ui()
        p = self.spec_points[index]
        self.log_event(f"Point selected by tap: {p['name']} (seq {p['seq']})")
        self.append_log(f"Point → {p['name']} "
                        f"({p['lower']:.{SPEC_DECIMALS}f}–{p['upper']:.{SPEC_DECIMALS}f} Ω)")

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

        self.cycle_results = {}
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
            res = self.cycle_results.get(i)
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
            self.ui.label_probe_caption.setText("PROBE")
            self.ui.label_probe_point.setText(point["name"])
            self.ui.label_point_index.setText(f"point {idx} / {n}")
        else:
            self.ui.label_probe_caption.setText("MEASURED")
            self.ui.label_probe_point.setText("single range")
            self.ui.label_point_index.setText("")
        self._refresh_limits_ui(editable=not seq_active)

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
        self.append_log(f"Saving spec: {model} ({len(points)} points)...")
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
        resp = QMessageBox.question(
            self, "Spec saved",
            f"Spec for '{model}' is ready.\nSwitch to this model now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
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
        self.append_log(f"✓ Spec saved: {model} ({count} points)")
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
            self.append_log(f"! Spec rejected: {err}")
            QMessageBox.critical(
                self, "Spec rejected",
                f"The spec for '{model}' is not valid:\n{err}",
            )
            return

        raw = pending[1] if (pending and pending[0] == model) else []
        count = self.spec_queue.queue_spec(model, raw)
        self._set_db_online(False)
        self.log_event(f"Spec queued offline for '{model}': {err}")
        self.append_log(f"! DB unreachable — spec for {model} queued "
                        f"({count} pending, will upload automatically)")
        # The spec is valid, so use it locally right away.
        self._adopt_spec(model, self._normalise_points(raw))
        QMessageBox.information(
            self, "Spec queued",
            f"The database is unreachable, so the spec for '{model}' was saved "
            f"locally to {SpecQueueManager.PENDING_FILE}.\n\n"
            f"It is active on this device now and will upload automatically "
            f"when the server is back.",
        )

    def on_spec_flush_complete(self, flushed, remaining):
        """A queued spec batch reached the DB."""
        self.log_event(f"Spec queue flushed: {flushed} model(s), {remaining} remaining")
        self._refresh_db_status()
        if remaining:
            self.append_log(f"Spec queue: {flushed} uploaded, {remaining} still pending")
        else:
            self.append_log(f"✓ Spec queue empty — {flushed} model(s) uploaded")

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
            self.append_log("! " + msg)
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
        if not decoded:
            self.log_event(f"Barcode scan: decode failed for '{raw}'")
            self.append_log(f"Barcode: unrecognised format — {raw}")
            return
        self.log_event(f"Barcode scan: '{raw}' → '{decoded}'")
        old_model = self.cleaned_model
        self.cleaned_model = decoded
        self._set_model_text(decoded)
        self.load_model_limits()
        self.load_model_spec()
        self.save_config()
        self.log_model_change("CHANGE", old_model, decoded, "barcode")
        self.append_log(f"Model set via barcode: {decoded}")

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
            QMessageBox.warning(self, "No Port", "No HIOKI port available.")
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

            self.append_log("Auto Hold enabled. Polling FETC?...")
            self.log_event("Device configured and polling started")
            self._show_reading("idle", status="ready" + (self._probe_hint() or " · probe the part"))
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
            QMessageBox.critical(self, "Connection Error", str(e))
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
            self.append_log(f"[{time_str}] Error: {msg}")
            if self.consecutive_timeouts >= self.max_consecutive_timeouts:
                self.log_event(f"Max consecutive timeouts reached ({self.consecutive_timeouts}), triggering reconnect")
                self.handle_comm_error("Multiple timeouts detected")
            return

        if isinstance(msg, str) and msg.startswith("Error"):
            self.consecutive_same = 0
            self.consecutive_timeouts += 1
            self.log_event(f"FETC? error: {msg}")
            self.append_log(f"[{time_str}] Error: {msg}")
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
        # still auto-holding RELEASE_AFTER_S later means open circuit → WAITING.
        if (not self.hold_previous and self.consecutive_same >= 3
                and time.time() - self._recorded_at >= RELEASE_AFTER_S):
            self._show_waiting()
            return
        try:
            value = float(msg)
            if self.consecutive_same == 1:
                self._display_value, self._display_range = value, (None, None)
                self._show_reading("settling", value, "settling…")
            elif record:
                self._show_reading("stable", value, "stable · recording…")
            # consecutive_same >= 3: the meter is auto-holding the reading
            # just recorded — keep its PASS/FAIL colour on screen.
        except ValueError:
            if not self.hold_previous:
                self._show_waiting()

        if record:
            # Judge against the current point's range when a sequence is active,
            # otherwise fall back to the single lower/upper limits.
            point = self.current_point()
            if point is not None:
                p_name, p_seq = point["name"], point["seq"]
                p_lower, p_upper = point["lower"], point["upper"]
                pass_result, result_text = self.compare_spec(msg, p_lower, p_upper)
            else:
                p_name = p_seq = p_lower = p_upper = None
                pass_result, result_text = self.compare_spec(msg)
            cleaned_model = self.cleaned_model.strip()

            current_time = datetime.now()
            can_insert = (self.last_db_insert_time is None or
                         (current_time - self.last_db_insert_time).total_seconds() >= 5)

            print(f"DEBUG: cleaned_model='{cleaned_model}', can_insert={can_insert}, value={msg}")

            # Ensure model is set; if empty, prompt once at record time
            if not cleaned_model:
                text, ok = self._prompt_model_text()
                if ok:
                    cleaned_model = self._decode_model_text(text)
                    self.log_model_change("CHANGE", "", cleaned_model, "prompt")
                    self.cleaned_model = cleaned_model
                    self._set_model_text(cleaned_model)
                    self.load_model_spec()
                    print(f"DEBUG: Model set via prompt: '{cleaned_model}'")

            # Map result to status string for DB
            if pass_result is True:
                status_for_db = "OK"
            elif pass_result is False:
                status_for_db = "NG"
            else:
                status_for_db = "N/A"

            try:
                resistance_value = round(float(msg), 3)
            except ValueError:
                resistance_value = msg

            try:
                self.append_csv_row(current_time, resistance_value, status_for_db,
                                    cleaned_model, point=p_name, seq=p_seq,
                                    lower=p_lower, upper=p_upper, db_status="pending")
                csv_status = "(CSV: ✓)"
            except Exception as e:
                csv_status = f"(CSV Error: {e})"
                print(f"CSV write error: {e}")

            if cleaned_model and can_insert:
                try:
                    print(f"DEBUG: Uploading to DB - Model: {cleaned_model}, Value: {resistance_value}, Status: {status_for_db}")
                    # Use async upload to prevent GUI hang
                    self.db_manager.upload_async(cleaned_model, resistance_value, status_for_db,
                                                 self.on_upload_complete, point=p_name, seq=p_seq,
                                                 lower=p_lower, upper=p_upper)
                    self.last_db_insert_time = current_time
                    db_status = "(DB: Uploading...)"
                except Exception as e:
                    db_status = f"(DB Error: {e})"
                    print(f"Database upload error: {e}")
            elif not cleaned_model:
                db_status = "(DB: No Model)"
                print("DEBUG: No model set - click Model button to enter model name")
            else:
                db_status = "(DB: Wait 10s)"
                print(f"DEBUG: Waiting for 10s interval - last insert was {(current_time - self.last_db_insert_time).total_seconds():.1f}s ago")

            value_text = f"{resistance_value:.3f}" if isinstance(resistance_value, float) else str(msg)
            self.append_result(time_str, p_name, value_text, pass_result, result_text,
                               f"{csv_status} {db_status}")
            self._count_result(status_for_db, cleaned_model)

            # Update judgement indicator
            judged_lo = self.lower_limit if p_lower is None else p_lower
            judged_hi = self.upper_limit if p_upper is None else p_upper
            where = f"{p_name} · " if p_name else ""
            self.set_judgement_status(
                pass_result,
                f"{where}{value_text} Ω  ({judged_lo:.{SPEC_DECIMALS}f} – {judged_hi:.{SPEC_DECIMALS}f})"
                f"  ·  {time_str[:8]}")

            # Per-point mark on the card for this part (a new part starts at point 1).
            if self.spec_points:
                if self.current_point_index == 0:
                    self.cycle_results = {}
                self.cycle_results[self.current_point_index] = (pass_result, value_text)

            # Auto-advance to the next point in the sequence (wraps to point 1).
            if self.spec_points:
                self.advance_point()

            verdict = {True: "PASS", False: "FAIL"}.get(pass_result, "N/A")
            state = {True: "pass", False: "fail"}.get(pass_result, "stable")
            self._display_value = msg
            self._display_range = (judged_lo, judged_hi)
            self._recorded_at = time.time()
            self._show_reading(state, msg, f"recorded {where}{verdict}"
                               + (f"  →  next: {self.current_point()['name']}" if self.spec_points else ""),
                               judged_lo, judged_hi)
        # Removed logging of unstable readings - only log stable data

    def on_upload_complete(self, success, error_msg):
        """Callback when async upload completes (runs on main thread)."""
        self._set_db_online(success)
        if success:
            self.log_event("Database upload successful")
            # Server just confirmed reachable — flush any queued records immediately
            pending = self.db_manager.get_pending_count()
            if pending > 0:
                self.log_event(f"Server reachable — flushing {pending} queued record(s)")
                self.append_log(f"Server back — uploading {pending} queued record(s)...")
                self.db_manager.retry_pending_uploads()
            # ...and any specs queued while the server was down.
            if self.spec_queue.pending_count() > 0:
                self.spec_queue.flush_async()
        else:
            self.log_event(f"Database upload failed: {error_msg}")
            count, wait, _ = self.db_manager.get_queue_status()
            wait_str = f"{int(wait)}s" if wait > 1 else "soon"
            self.append_log(f"! DB unreachable — {count} queued, retry in {wait_str}")

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

    def on_retry_complete(self, success_count, failed_count, remaining_count):
        """Callback when a batch retry finishes (runs on main thread)."""
        self._set_db_online(success_count > 0 or remaining_count == 0)
        if remaining_count == 0 and success_count > 0:
            self.log_event(f"Batch upload complete: {success_count} record(s) sent")
            self.append_log(f"Batch upload done — {success_count} record(s) sent")
        elif success_count > 0:
            count, wait, _ = self.db_manager.get_queue_status()
            wait_str = f"{int(wait)}s"
            self.log_event(f"Partial batch: {success_count} uploaded, {remaining_count} still pending")
            self.append_log(f"Partial upload: {success_count}✓ — {remaining_count} pending, retry in {wait_str}")
        else:
            count, wait, _ = self.db_manager.get_queue_status()
            wait_str = f"{int(wait)}s"
            self.log_event(f"Batch retry failed — {remaining_count} record(s) still pending")
            self.append_log(f"! Retry failed — {remaining_count} queued, next in {wait_str}")

    def on_upload_now_clicked(self):
        """Force an immediate flush of pending_uploads.json — no timer/backoff wait."""
        pending = self.db_manager.get_pending_count()
        specs = self.spec_queue.pending_count()
        if pending == 0 and specs == 0:
            self.log_event("Upload Now: nothing pending")
            self.append_log("Upload Now — queue already empty")
            return
        if self.db_manager.is_uploading:
            self.append_log("Upload Now — an upload is already in progress…")
            return
        self.log_event(f"Upload Now: forcing flush of {pending} reading(s), {specs} spec(s)")
        self.append_log(f"⬆ Uploading {pending} pending record(s) now…")
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
        self.append_log("⚠ Serial hardware hang — reconnecting…")
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
        self.ui.label_log_count.setText(f"{self.reading_count} READINGS")
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
        QMessageBox.critical(None, "Startup Error", f"Failed to initialize application:\n{e}")
        sys.exit(1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
