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
    QApplication, QDialog, QMessageBox, QInputDialog, QAbstractSpinBox
)
from PySide6.QtGui import QFont
from usb_rs import Usb_rs
from insert_resistance2db import insert_to_mssql
from ui_UI_Resistance import Ui_Dialog
from numpad_dialog import NumpadDialog
from db_upload_manager import DBUploadManager, UploadSignals

BAUD_RATE = 9600
POLL_INTERVAL_MS = 500  # default polling interval for FETC?
CONFIG_FILE = "gui_mode5_config.json"
MAX_VALID_OHMS = 1e12  # ignore readings above this magnitude
CSV_HEADERS = ["Timestamp", "Resistance", "Status", "Model", "Date", "Time", "DB_Status"]
MODEL_CHANGE_LOG = "model_changes.csv"  # persistent record of every model switch

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
                if "HIOKI" in resp.upper():
                    self.found.emit(p.device, resp)
                    return
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass
                continue
        self.not_found.emit()


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
        try:
            result = subprocess.run(
                ['netsh', 'wlan', 'show', 'interfaces'],
                capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.splitlines():
                s = line.strip()
                if s.startswith('Signal') and ':' in s:
                    return int(s.split(':', 1)[1].strip().replace('%', ''))
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
            if source is self.ui.doubleSpinBox_UpperLimit.lineEdit():
                self._show_numpad_for_spinbox(self.ui.doubleSpinBox_UpperLimit, "Upper Limit")
                return True
            elif source is self.ui.doubleSpinBox_lowerLimit.lineEdit():
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

    def append_csv_row(self, current_time, resistance_value, status, model, db_status="pending"):
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
        """Unified decode for all barcode / manual-entry formats.

        Priority:
        1. Dollar-delimited  — PREFIX$[id]MODEL$SUFFIX  (label-printer format)
        2. AIM Code 39 Extended — /X escape sequences   (USB HID scanner format)
        3. Plain text — returned as-is after strip
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

    def on_model_clicked(self):
        """Prompt user for model, clean it, show on button, and load its limits."""
        text, ok = QInputDialog.getText(self, "Model", "Enter model text:", text=self.cleaned_model)
        if ok:
            old_model = self.cleaned_model
            cleaned = self._decode_model_text(text)
            self.cleaned_model = cleaned
            self.ui.pushButton_model.setText(cleaned if cleaned else "Model")
            self.load_model_limits()
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
        self.save_config()
        self.log_model_change("CHANGE", old_model, decoded, "barcode")
        self.append_log(f"Model set via barcode: {decoded}")

    def compare_spec(self, value_str):
        """Compare reading with spec limits. Returns (pass, result_text)"""
        try:
            value = float(value_str)
            if value < self.lower_limit:
                return False, "FAIL: Low"
            elif value > self.upper_limit:
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
                self.append_csv_row(current_time, resistance_value, status_for_db, cleaned_model, "pending")
                csv_status = "(CSV: ✓)"
            except Exception as e:
                csv_status = f"(CSV Error: {e})"
                print(f"CSV write error: {e}")

            if cleaned_model and can_insert:
                try:
                    print(f"DEBUG: Uploading to DB - Model: {cleaned_model}, Value: {resistance_value}, Status: {status_for_db}")
                    # Use async upload to prevent GUI hang
                    self.db_manager.upload_async(cleaned_model, resistance_value, status_for_db, self.on_upload_complete)
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

            log_line = f"{time_str}  {msg}  {result_text}  {csv_status}  {db_status}"
            self.append_log(log_line)

            # Update judgement indicator
            self.set_judgement_status(pass_result)
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
