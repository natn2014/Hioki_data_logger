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
from PySide6.QtCore import QTimer, QThread, Signal, Qt, QStringListModel, QEvent, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QApplication, QDialog, QMessageBox, QInputDialog, QAbstractSpinBox,
    QPushButton, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QDoubleSpinBox,
    QScrollArea, QWidget
)
from PySide6.QtGui import QFont
from usb_rs import Usb_rs
from insert_resistance2db import (
    insert_to_mssql, fetch_model_spec, upsert_model_spec, check_schema
)
from ui_UI_Resistance import Ui_Dialog
from numpad_dialog import NumpadDialog
from db_upload_manager import DBUploadManager, UploadSignals

BAUD_RATE = 9600
POLL_INTERVAL_MS = 500  # default polling interval for FETC?
CONFIG_FILE = "gui_mode5_config.json"
MAX_VALID_OHMS = 1e12  # ignore readings above this magnitude
CSV_HEADERS = ["Timestamp", "Resistance", "Status", "Model",
               "Point", "Seq", "LowerLimit", "UpperLimit",
               "Date", "Time", "DB_Status"]
MODEL_CHANGE_LOG = "model_changes.csv"  # persistent record of every model switch

# Tappable point card: normal / active (checked) / pressed states. Sized for
# fingertips on the touch panel.
POINT_CARD_QSS = """
QPushButton {
    background-color: #eceff1;
    border: 2px solid #b0bec5;
    border-radius: 10px;
    padding: 6px 4px;
    color: #37474f;
    font-size: 15px;
}
QPushButton:checked {
    background-color: #e0f7fa;
    border: 3px solid #00acc1;
    color: #006064;
    font-weight: bold;
}
QPushButton:pressed { background-color: #b2ebf2; }
"""

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
    done   = Signal(str, int)   # model, rows_written
    failed = Signal(str, str)   # model, error

    def __init__(self, model, points):
        super().__init__()
        self.model = model
        self.points = points

    def run(self):
        try:
            count = upsert_model_spec(self.model, self.points)
            self.done.emit(self.model, count)
        except Exception as e:
            self.failed.emit(self.model, str(e))


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
        self.setModal(True)
        self._rows = []          # [{widget, seq, name, lower, upper}, ...]
        self._result = None
        self._scan_fields = []   # text fields that cleanse scanned barcodes

        outer = QVBoxLayout(self)

        outer.addWidget(QLabel("Model name  (type or scan barcode)"))
        self.model_edit = QLineEdit(model)
        self.model_edit.setPlaceholderText("e.g. 750X — or scan the part barcode")
        self.model_edit.setMaxLength(100)
        self._register_scan_field(self.model_edit)
        outer.addWidget(self.model_edit)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Measurement points (probed in this order)"))
        hdr.addStretch(1)
        self.add_btn = QPushButton("＋ Add point")
        self.add_btn.clicked.connect(lambda: (self._add_row(), None)[1])
        hdr.addWidget(self.add_btn)
        outer.addLayout(hdr)

        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.rows_container)
        scroll.setMinimumHeight(230)
        outer.addWidget(scroll, 1)

        self.err_label = QLabel("")
        self.err_label.setWordWrap(True)
        self.err_label.setStyleSheet("color: #f44336; font-weight: bold;")
        outer.addWidget(self.err_label)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save to DB")
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

        self.resize(580, 500)

    def _add_row(self, name="", lower=None, upper=None):
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)

        seq_lbl = QLabel(str(len(self._rows) + 1))
        seq_lbl.setFixedWidth(24)
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("A-B")
        name_edit.setMaxLength(50)
        self._register_scan_field(name_edit)

        lower_spin = QDoubleSpinBox()
        upper_spin = QDoubleSpinBox()
        for sp in (lower_spin, upper_spin):
            sp.setRange(0.0, 9999.0)
            sp.setDecimals(3)
            sp.setSingleStep(0.1)
            sp.lineEdit().installEventFilter(self)  # tap opens numpad
        if lower is not None:
            lower_spin.setValue(float(lower))
        if upper is not None:
            upper_spin.setValue(float(upper))

        rm_btn = QPushButton("✕")
        rm_btn.setFixedWidth(40)

        h.addWidget(seq_lbl)
        h.addWidget(name_edit, 1)
        h.addWidget(QLabel("L"))
        h.addWidget(lower_spin)
        h.addWidget(QLabel("U"))
        h.addWidget(upper_spin)
        h.addWidget(rm_btn)

        # Insert before the trailing stretch so rows stack top-down.
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row_w)
        rec = {"widget": row_w, "seq": seq_lbl, "name": name_edit,
               "lower": lower_spin, "upper": upper_spin}
        self._rows.append(rec)
        rm_btn.clicked.connect(lambda: self._remove_row(rec))

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
        elif et == QEvent.Type.FocusIn and source in self._scan_fields:
            # Select existing text so a scan (or retype) replaces it cleanly.
            source.selectAll()
        return super().eventFilter(source, event)

    def _numpad(self, spinbox, title):
        dlg = NumpadDialog(
            current_value=spinbox.value(), decimals=3, title=title,
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
            points.append({"name": name, "lower": round(lo, 3), "upper": round(up, 3)})
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

    def run(self):
        self._running = True
        last_health_check = time.time()
        try:
            while self._running:
                t_start = time.time()

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

        self.db_manager = DBUploadManager(parent_signals=self.upload_signals)
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

        # Non-blocking timers: reconnect scheduling and upload retry only
        self.detect_retry_timer = QTimer(self)
        self.detect_retry_timer.setSingleShot(True)
        self.detect_retry_timer.timeout.connect(self.start_auto_detect)
        self.retry_upload_timer = QTimer(self)
        self.retry_upload_timer.setSingleShot(False)
        self.retry_upload_timer.timeout.connect(self.retry_pending_uploads)

        self.init_ui()
        self.load_config()
        self.log_event("Application started")
        self.start_auto_detect()
        self.run_schema_check()

    def init_ui(self):
        self.ui = Ui_Dialog()
        self.ui.setupUi(self)
        self.setWindowTitle("HIOKI Auto Hold Mode (Mode 5)")
        self._apply_ui_scale()

        # Configure spinboxes for limits
        self.ui.doubleSpinBox_lowerLimit.setRange(0.01, 9999.0)
        self.ui.doubleSpinBox_lowerLimit.setDecimals(3)
        self.ui.doubleSpinBox_lowerLimit.setSingleStep(0.01)
        self.ui.doubleSpinBox_lowerLimit.setValue(self.lower_limit)
        self.ui.doubleSpinBox_lowerLimit.valueChanged.connect(self.on_limit_changed)

        self.ui.doubleSpinBox_UpperLimit.setRange(0.01, 9999.0)
        self.ui.doubleSpinBox_UpperLimit.setDecimals(3)
        self.ui.doubleSpinBox_UpperLimit.setSingleStep(0.01)
        self.ui.doubleSpinBox_UpperLimit.setValue(self.upper_limit)
        self.ui.doubleSpinBox_UpperLimit.valueChanged.connect(self.on_limit_changed)

        # Open numpad on click for limit spinboxes
        self.ui.doubleSpinBox_UpperLimit.lineEdit().installEventFilter(self)
        self.ui.doubleSpinBox_lowerLimit.lineEdit().installEventFilter(self)

        # Measurement display is read-only
        self.ui.doubleSpinBox_Measure.setReadOnly(True)
        self.ui.doubleSpinBox_Measure.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        # Logger model for list view
        self.log_model = QStringListModel()
        self.ui.listView_logger.setModel(self.log_model)

        # Button for model input
        self.ui.pushButton_model.clicked.connect(self.on_model_clicked)

        # Judgement button used as status indicator
        self.ui.pushButton_Judgement.setEnabled(False)

        # Point-sequence cards: one tappable card per point, sitting between the
        # measured value and the judgement. Tapping a card jumps straight to that
        # point — this replaces the old Prev/Next stepping buttons.
        self.point_cards_row = QHBoxLayout()
        self.point_cards = []
        self.ui.verticalLayout.insertLayout(2, self.point_cards_row)

        # Remaining controls (added programmatically so the generated UI file
        # stays untouched). Reset needs an active sequence; Edit Spec never does.
        self.point_button_row = QHBoxLayout()
        self.btn_reset_point = QPushButton("⟲ Reset to first point")
        self.btn_edit_spec = QPushButton("⚙ Edit Spec")
        for b in (self.btn_reset_point, self.btn_edit_spec):
            b.setMinimumHeight(48)
            self.point_button_row.addWidget(b)
        self.ui.groupBox_Judge.layout().addLayout(self.point_button_row)
        self.btn_reset_point.clicked.connect(self.on_reset_point)
        self.btn_edit_spec.clicked.connect(self.on_edit_spec_clicked)
        self._apply_point_to_ui()  # builds cards + sets initial enabled state

        # Check if there are pending uploads to retry
        pending_count = self.db_manager.get_pending_count()
        if pending_count > 0:
            self.log_event(f"Found {pending_count} pending uploads to retry")
            self.append_log(f"! {pending_count} value(s) waiting to upload")

        # Initialise status badges and start background WiFi monitor
        self._set_usb_status("disconnected")
        self.wifi_thread = WiFiWorkerThread()
        self.wifi_thread.wifi_ready.connect(self._on_wifi_ready)
        self.wifi_thread.start()

    def _apply_ui_scale(self):
        screen = QApplication.primaryScreen().availableGeometry()
        scale = min(screen.width() / 1280.0, screen.height() / 800.0, 1.0)

        def sf(pt, bold=False):
            f = QFont()
            f.setPointSize(max(8, round(pt * scale)))
            if bold:
                f.setBold(True)
                f.setWeight(QFont.Weight.Bold)
            return f

        def px(v):
            return max(1, round(v * scale))

        ui = self.ui
        ui.groupBox_status.setFont(sf(12))
        ui.label_usb_status.setFont(sf(13, bold=True))
        ui.label_wifi.setFont(sf(13, bold=True))
        ui.pushButton_model.setFont(sf(25))
        ui.pushButton_model.setMinimumHeight(px(80))

        ui.groupBox_Resistance.setFont(sf(12))
        ui.groupBox_MeasureValue.setFont(sf(24, bold=True))
        ui.doubleSpinBox_Measure.setFont(sf(72, bold=True))

        ui.groupBox_UpperLimit.setFont(sf(18))
        ui.doubleSpinBox_UpperLimit.setFont(sf(30))
        ui.groupBox_LowerLimit.setFont(sf(18))
        ui.doubleSpinBox_lowerLimit.setFont(sf(30))

        ui.groupBox_Judge.setFont(sf(12))
        ui.pushButton_Judgement.setFont(sf(48))

        ui.groupBox.setFont(sf(12))

    def _set_usb_status(self, state):
        props = {
            "connected":    ("● USB  Connected",    "#4CAF50"),
            "connecting":   ("◌ USB  Connecting",   "#FF9800"),
            "disconnected": ("○ USB  Disconnected", "#f44336"),
        }
        text, color = props.get(state, ("○ USB  Disconnected", "#f44336"))
        self.ui.label_usb_status.setText(text)
        self.ui.label_usb_status.setStyleSheet(
            f"color: {color}; font-weight: bold; "
            f"border: 2px solid {color}; border-radius: 6px; padding: 4px 10px;"
        )

    def _on_wifi_ready(self, pct):
        if pct < 0:
            self.ui.label_wifi.setText("WiFi  ---")
            self.ui.label_wifi.setStyleSheet("color: #888; font-weight: bold;")
            return
        bar_chars = "▂▄▆█"
        n = 0 if pct < 20 else 1 if pct < 40 else 2 if pct < 60 else 3 if pct < 80 else 4
        bars = ''.join(bar_chars[i] if i < n else '░' for i in range(4))
        color = "#4CAF50" if pct >= 70 else "#FF9800" if pct >= 40 else "#f44336"
        self.ui.label_wifi.setText(f"WiFi {bars} {pct}%")
        self.ui.label_wifi.setStyleSheet(f"color: {color}; font-weight: bold;")

    def eventFilter(self, source, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            # While a point sequence drives the limits they are read-only; skip
            # the numpad so operators can't override the active point's range.
            if source is self.ui.doubleSpinBox_UpperLimit.lineEdit():
                if not self.ui.doubleSpinBox_UpperLimit.isReadOnly():
                    self._show_numpad_for_spinbox(self.ui.doubleSpinBox_UpperLimit, "Upper Limit")
                return True
            elif source is self.ui.doubleSpinBox_lowerLimit.lineEdit():
                if not self.ui.doubleSpinBox_lowerLimit.isReadOnly():
                    self._show_numpad_for_spinbox(self.ui.doubleSpinBox_lowerLimit, "Lower Limit")
                return True
        return super().eventFilter(source, event)

    def _show_numpad_for_spinbox(self, spinbox, label):
        dlg = NumpadDialog(
            current_value=spinbox.value(),
            decimals=3,
            title=label,
            min_val=spinbox.minimum(),
            max_val=spinbox.maximum(),
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            spinbox.setValue(dlg.get_value())

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
        self.ui.pushButton_model.setText(self.cleaned_model if self.cleaned_model else "Model")
        self.ui.doubleSpinBox_lowerLimit.blockSignals(True)
        self.ui.doubleSpinBox_UpperLimit.blockSignals(True)
        self.ui.doubleSpinBox_lowerLimit.setValue(self.lower_limit)
        self.ui.doubleSpinBox_UpperLimit.setValue(self.upper_limit)
        self.ui.doubleSpinBox_lowerLimit.blockSignals(False)
        self.ui.doubleSpinBox_UpperLimit.blockSignals(False)

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
        """Update limits when spinbox values change"""
        self.lower_limit = self.ui.doubleSpinBox_lowerLimit.value()
        self.upper_limit = self.ui.doubleSpinBox_UpperLimit.value()
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

    def on_model_clicked(self):
        """Prompt user for model, clean it, show on button, and load its limits."""
        text, ok = QInputDialog.getText(self, "Model", "Enter model text:", text=self.cleaned_model)
        if ok:
            old_model = self.cleaned_model
            cleaned = self._decode_model_text(text)
            self.cleaned_model = cleaned
            self.ui.pushButton_model.setText(cleaned if cleaned else "Model")
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
                        self.ui.doubleSpinBox_lowerLimit.blockSignals(True)
                        self.ui.doubleSpinBox_UpperLimit.blockSignals(True)
                        self.ui.doubleSpinBox_lowerLimit.setValue(self.lower_limit)
                        self.ui.doubleSpinBox_UpperLimit.setValue(self.upper_limit)
                        self.ui.doubleSpinBox_lowerLimit.blockSignals(False)
                        self.ui.doubleSpinBox_UpperLimit.blockSignals(False)
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

    def on_point_card_tapped(self, index):
        """Operator tapped a point card — jump straight to that point."""
        if not self.spec_points or not (0 <= index < len(self.spec_points)):
            return
        self.current_point_index = index
        self._apply_point_to_ui()
        p = self.spec_points[index]
        self.log_event(f"Point selected by tap: {p['name']} (seq {p['seq']})")
        self.append_log(f"Point → {p['name']} ({p['lower']:g}–{p['upper']:g} Ω)")

    def _cards_signature(self):
        """Identity of the current card set — rebuild only when this changes."""
        return [(p["name"], p["lower"], p["upper"]) for p in self.spec_points]

    def _rebuild_point_cards(self):
        """Recreate one tappable card per point in the active sequence."""
        while self.point_cards_row.count():
            item = self.point_cards_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.point_cards = []

        for i, p in enumerate(self.spec_points):
            card = QPushButton(
                f"{p['name']}\n{p['lower']:g} – {p['upper']:g} Ω\nseq {p['seq']}"
            )
            card.setCheckable(True)
            card.setMinimumHeight(76)
            card.setStyleSheet(POINT_CARD_QSS)
            card.clicked.connect(
                lambda _checked=False, idx=i: self.on_point_card_tapped(idx))
            self.point_cards_row.addWidget(card)
            self.point_cards.append(card)
        self._cards_sig = self._cards_signature()

    def _apply_point_to_ui(self):
        """Reflect the active point in the limits, group title, and point cards.

        When a sequence is active the limit spinboxes are driven read-only from
        the current point; otherwise they stay editable (single-range mode).
        Cards are rebuilt only when the point set itself changes, so advancing
        during measurement just re-highlights (no widget churn / flicker).
        """
        point = self.current_point()
        seq_active = point is not None

        if getattr(self, "_cards_sig", None) != self._cards_signature():
            self._rebuild_point_cards()
        for i, card in enumerate(self.point_cards):
            card.setChecked(i == self.current_point_index)

        self.ui.doubleSpinBox_lowerLimit.setReadOnly(seq_active)
        self.ui.doubleSpinBox_UpperLimit.setReadOnly(seq_active)
        self.btn_reset_point.setEnabled(seq_active)

        if seq_active:
            self.lower_limit = point["lower"]
            self.upper_limit = point["upper"]
            self.ui.doubleSpinBox_lowerLimit.blockSignals(True)
            self.ui.doubleSpinBox_UpperLimit.blockSignals(True)
            self.ui.doubleSpinBox_lowerLimit.setValue(point["lower"])
            self.ui.doubleSpinBox_UpperLimit.setValue(point["upper"])
            self.ui.doubleSpinBox_lowerLimit.blockSignals(False)
            self.ui.doubleSpinBox_UpperLimit.blockSignals(False)
            idx, n = self.current_point_index + 1, len(self.spec_points)
            self.ui.groupBox_MeasureValue.setTitle(
                f"Measured — {point['name']} "
                f"({point['lower']:g}–{point['upper']:g}Ω)  {idx}/{n}"
            )
        else:
            self.ui.groupBox_MeasureValue.setTitle("Measured")

    # ── Register / edit a model spec (writes resistance_spec) ─────────────────

    def on_edit_spec_clicked(self):
        """Open the spec editor prefilled with the current model, then save."""
        model = self.cleaned_model.strip()
        points = [{"name": p["name"], "lower": p["lower"], "upper": p["upper"]}
                  for p in self.spec_points]
        dlg = ModelSpecDialog(model=model, points=points, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.result_spec()
        if not result:
            return
        new_model, new_points = result

        self._pending_spec = (new_model, new_points)
        self.btn_edit_spec.setEnabled(False)
        self.append_log(f"Saving spec: {new_model} ({len(new_points)} points)...")
        self.spec_upsert_thread = SpecUpsertThread(new_model, new_points)
        self.spec_upsert_thread.done.connect(
            self.on_spec_saved, Qt.ConnectionType.QueuedConnection)
        self.spec_upsert_thread.failed.connect(
            self.on_spec_save_failed, Qt.ConnectionType.QueuedConnection)
        self.spec_upsert_thread.start()

    def on_spec_saved(self, model, count):
        """resistance_spec write succeeded — cache, then apply/offer switch."""
        self.btn_edit_spec.setEnabled(True)
        pending = self._pending_spec
        self._pending_spec = None
        raw = pending[1] if (pending and pending[0] == model) else []
        norm = [{"seq": i + 1, "name": p["name"],
                 "lower": float(p["lower"]), "upper": float(p["upper"])}
                for i, p in enumerate(raw)]
        if norm:
            self._cache_points(model, norm)
        self.log_event(f"Spec saved to DB for '{model}' ({count} points)")
        self.append_log(f"✓ Spec saved: {model} ({count} points)")

        if model == self.cleaned_model.strip():
            # Refresh the active model's sequence in place.
            self.spec_points = norm
            self.current_point_index = 0
            self._apply_point_to_ui()
            return

        resp = QMessageBox.question(
            self, "Spec saved",
            f"Saved spec for '{model}'.\nSwitch to this model now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            old = self.cleaned_model
            self.cleaned_model = model
            self.ui.pushButton_model.setText(model)
            self.load_model_limits()
            self.load_model_spec()
            self.save_config()
            self.log_model_change("CHANGE", old, model, "spec_editor")

    def on_spec_save_failed(self, model, err):
        """resistance_spec write failed — surface the error, keep the button live."""
        self.btn_edit_spec.setEnabled(True)
        self._pending_spec = None
        self.log_event(f"Spec save failed for '{model}': {err}")
        self.append_log(f"! Spec save failed: {err}")
        QMessageBox.critical(
            self, "Spec save failed",
            f"Could not write resistance_spec for '{model}':\n{err}",
        )

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
        self.ui.pushButton_model.setText(decoded)
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
            self.log_model.setStringList([])

            self.append_log("Auto Hold enabled. Polling FETC?...")
            self.log_event("Device configured and polling started")
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
            # Ignore unrealistically high values
            if abs(current_val) > MAX_VALID_OHMS:
                self.consecutive_same = 0
                self.previous_numeric = None
                self.previous_raw = None
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

        # Update measurement display
        try:
            self.ui.doubleSpinBox_Measure.setValue(float(msg))
        except ValueError:
            pass

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
                text, ok = QInputDialog.getText(self, "Model", "Enter model text:")
                if ok:
                    cleaned_model = self._decode_model_text(text)
                    self.log_model_change("CHANGE", "", cleaned_model, "prompt")
                    self.cleaned_model = cleaned_model
                    self.ui.pushButton_model.setText(cleaned_model if cleaned_model else "Model")
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

            point_tag = f"[{p_name}] " if p_name else ""
            log_line = f"{time_str}  {point_tag}{msg}  {result_text}  {csv_status}  {db_status}"
            self.append_log(log_line)

            # Update judgement indicator
            self.set_judgement_status(pass_result)

            # Auto-advance to the next point in the sequence (wraps to point 1).
            if self.spec_points:
                self.advance_point()
        # Removed logging of unstable readings - only log stable data

    def on_upload_complete(self, success, error_msg):
        """Callback when async upload completes (runs on main thread)."""
        if success:
            self.log_event("Database upload successful")
            # Server just confirmed reachable — flush any queued records immediately
            pending = self.db_manager.get_pending_count()
            if pending > 0:
                self.log_event(f"Server reachable — flushing {pending} queued record(s)")
                self.append_log(f"Server back — uploading {pending} queued record(s)...")
                self.db_manager.retry_pending_uploads()
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

    def on_retry_complete(self, success_count, failed_count, remaining_count):
        """Callback when a batch retry finishes (runs on main thread)."""
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

    def set_judgement_status(self, pass_result):
        """Update judgement indicator button based on pass/fail result."""
        judgement_map = {
            True: ("PASS", "#4CAF50"),      # Green
            False: ("FAIL", "#f44336"),     # Red
            None: ("N/A", "#9e9e9e")        # Gray
        }
        text, color = judgement_map.get(pass_result, ("N/A", "#9e9e9e"))
        self.ui.pushButton_Judgement.setText(text)
        self.ui.pushButton_Judgement.setStyleSheet(f"background-color: {color}; color: white; font-weight: bold;")
        if pass_result is True:
            self._play_sound("ResistancePass_TH.mp3")
        elif pass_result is False:
            self._play_sound("ResistanceOver_TH.mp3")

    def log_event(self, event_text):
        """Log detailed event to console with timestamp for debugging."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        full_msg = f"[{timestamp}] {event_text}"
        print(full_msg)

    def append_log(self, text):
        """Append a line to the list view logger."""
        try:
            items = self.log_model.stringList()
            items.append(text)
            if len(items) > 500:
                items = items[-500:]
            self.log_model.setStringList(items)
            self.ui.listView_logger.scrollToBottom()
        except RuntimeError:
            pass  # Widget already destroyed during shutdown


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
