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
import serial
import serial.tools.list_ports
from datetime import datetime
from PySide2.QtCore import QTimer, QThread, Signal
from PySide2.QtWidgets import (
    QApplication, QDialog, QMessageBox, QInputDialog, QAbstractSpinBox
)
from PySide2.QtGui import QPalette, QColor
from PySide2.QtCore import QStringListModel
from usb_rs import Usb_rs
from insert_resistance2db import insert_to_mssql
from ui_UI_Resistance import Ui_Dialog

BAUD_RATE = 9600
POLL_INTERVAL_MS = 500  # default polling interval for FETC?
CONFIG_FILE = "gui_mode5_config.json"
MAX_VALID_OHMS = 1e12  # ignore readings above this magnitude
CSV_HEADERS = ["Timestamp", "Resistance", "Status", "Model", "Date", "Time"]


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


class MainWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.serial_obj = Usb_rs(gui=True)
        self.connected = False
        self.current_port = None
        self.detect_in_progress = False
        self.previous_numeric = None
        self.previous_raw = None
        self.consecutive_same = 0
        self.lower_limit = 0.0
        self.upper_limit = 1000.0
        self.cleaned_model = ""
        self.last_db_insert_time = None  # Track last DB insertion time
        
        # Connection health and recovery settings
        self.reconnect_delay = 1.0  # Start with 1 second, exponential backoff
        self.max_reconnect_delay = 60.0  # Cap at 60 seconds
        self.consecutive_timeouts = 0
        self.max_consecutive_timeouts = 3  # Trigger reconnect after 3 timeouts
        self.last_health_check = None
        self.health_check_interval = 30  # Check connection every 30 seconds
        
        # Timers
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll_fetch)
        self.detect_retry_timer = QTimer(self)
        self.detect_retry_timer.setSingleShot(True)
        self.detect_retry_timer.timeout.connect(self.start_auto_detect)
        self.health_check_timer = QTimer(self)  # Periodic connection health check
        self.health_check_timer.timeout.connect(self.check_connection_health)
        
        self.init_ui()
        self.load_config()
        self.log_event("Application started")
        self.start_auto_detect()

    def init_ui(self):
        self.ui = Ui_Dialog()
        self.ui.setupUi(self)
        self.setWindowTitle("HIOKI Auto Hold Mode (Mode 5)")

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

        # Measurement display is read-only
        self.ui.doubleSpinBox_Measure.setReadOnly(True)
        self.ui.doubleSpinBox_Measure.setButtonSymbols(QAbstractSpinBox.NoButtons)

        # Logger model for list view
        self.log_model = QStringListModel()
        self.ui.listView_logger.setModel(self.log_model)

        # Button for model input
        self.ui.pushButton_model.clicked.connect(self.on_model_clicked)

        # Judgement button used as status indicator
        self.ui.pushButton_Judgement.setEnabled(False)

    def start_auto_detect(self):
        if self.connected or self.detect_in_progress:
            return
        self.detect_in_progress = True
        self.reconnect_delay = min(self.reconnect_delay * 1.5, self.max_reconnect_delay)  # Exponential backoff
        self.log_event(f"Auto-detect starting (reconnect delay: {self.reconnect_delay:.1f}s)")
        self.set_status(f"Scanning ports (retry in {self.reconnect_delay:.0f}s)...", "orange")
        self.det_thread = AutoDetectThread()
        self.det_thread.found.connect(self.on_port_found)
        self.det_thread.not_found.connect(self.on_port_not_found)
        self.det_thread.start()

    def load_config(self):
        """Load model and per-model limits from config.json"""
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

    def append_csv_row(self, current_time, resistance_value, status, model):
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
                time_value
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

    def on_model_clicked(self):
        """Prompt user for model, clean it, show on button, and load its limits."""
        text, ok = QInputDialog.getText(self, "Model", "Enter model text:", text=self.cleaned_model)
        if ok:
            cleaned = self.clean_raw_text(text)
            self.cleaned_model = cleaned
            self.ui.pushButton_model.setText(cleaned if cleaned else "Model")
            self.load_model_limits()
            self.save_config()

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
        self.set_status(f"Found HIOKI on {port} ({idn})", "green")
        # Auto start measurement once device detected
        self.start_mode()

    def on_port_not_found(self):
        self.detect_in_progress = False
        self.log_event("No HIOKI device found, scheduling retry...")
        self.set_status("No HIOKI device found", "red")
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
            
            # Configure meter
            self.log_event("Configuring meter...")
            self.serial_obj.sendMsg(":INITIATE:CONTINUOUS ON")
            time.sleep(0.1)
            self.serial_obj.sendMsg(":TRIGGER:SOURCE IMM")
            time.sleep(0.1)
            self.serial_obj.sendMsg("HOLD:AUTO ON")
            time.sleep(0.1)
            # Reset stability state
            self.previous_numeric = None
            self.previous_raw = None
            self.consecutive_same = 0
            self.consecutive_timeouts = 0
            self.log_model.setStringList([])
            self.last_health_check = time.time()
            
            self.append_log("Auto Hold enabled. Polling FETC?...")
            self.log_event("Device configured and polling started")
            self.set_status(f"Connected on {self.current_port} (polling)", "green")
            self.timer.start(POLL_INTERVAL_MS)
            self.health_check_timer.start(5000)  # Health check every 5 seconds
        except Exception as e:
            self.log_event(f"Connection failed: {e}")
            QMessageBox.critical(self, "Connection Error", str(e))
            self.set_status("Connection failed", "red")
            self.connected = False

    def poll_fetch(self):
        if not self.connected:
            return
        try:
            msg = self.serial_obj.SendQueryMsg("FETC?", 2)
        except Exception as e:
            self.log_event(f"FETC? query exception: {e}")
            self.append_log(f"Error: {e}")
            self.handle_comm_error(str(e))
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
                    cleaned_model = self.clean_raw_text(text).strip()
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
                self.append_csv_row(current_time, resistance_value, status_for_db, cleaned_model)
                csv_status = "(CSV: ✓)"
            except Exception as e:
                csv_status = f"(CSV Error: {e})"
                print(f"CSV write error: {e}")

            if cleaned_model and can_insert:
                try:
                    print(f"DEBUG: Inserting to DB - Model: {cleaned_model}, Value: {resistance_value}, Status: {status_for_db}")
                    insert_to_mssql(cleaned_model, resistance_value, status_for_db)
                    self.last_db_insert_time = current_time
                    db_status = "(DB: ✓)"
                except Exception as e:
                    db_status = f"(DB Error: {e})"
                    print(f"Database insertion error: {e}")
            elif not cleaned_model:
                db_status = "(DB: No Model)"
                print("DEBUG: No model set - click Model button to enter model name")
            else:
                db_status = "(DB: Wait 5s)"
                print(f"DEBUG: Waiting for 5s interval - last insert was {(current_time - self.last_db_insert_time).total_seconds():.1f}s ago")
            
            log_line = f"{time_str}  {msg}  {result_text}  {csv_status}  {db_status}"
            self.append_log(log_line)
            
            # Update judgement indicator
            if pass_result is True:
                self.ui.pushButton_Judgement.setText("PASS")
                self.ui.pushButton_Judgement.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
            elif pass_result is False:
                self.ui.pushButton_Judgement.setText("FAIL")
                self.ui.pushButton_Judgement.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
            else:
                self.ui.pushButton_Judgement.setText("N/A")
                self.ui.pushButton_Judgement.setStyleSheet("background-color: #9e9e9e; color: white; font-weight: bold;")
        # Removed logging of unstable readings - only log stable data

    def check_connection_health(self):
        """Periodically verify device is still connected by checking port state and sending heartbeat."""
        if not self.connected:
            return
        try:
            # Check if port is still open
            if not self.serial_obj.is_port_open():
                self.log_event("Health check: Port is no longer open")
                self.handle_comm_error("Port no longer open")
                return
            
            # Optional: Send periodic *IDN? to verify device is responsive (every 30 seconds)
            current_time = time.time()
            if self.last_health_check is None or (current_time - self.last_health_check) >= self.health_check_interval:
                self.log_event("Health check: Sending *IDN? to verify device")
                response = self.serial_obj.SendQueryMsg("*IDN?", 1)
                self.last_health_check = current_time
                if response.startswith("Error") or response == "Timeout Error":
                    self.log_event(f"Health check failed: {response}")
                    self.handle_comm_error(f"Device health check failed: {response}")
                else:
                    self.log_event(f"Health check passed: {response}")
        except Exception as e:
            self.log_event(f"Health check exception: {e}")
            self.handle_comm_error(str(e))

    def handle_comm_error(self, msg):
        """Recover from serial I/O failures by resetting connection and retrying detection."""
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
            self.timer.stop()
            self.health_check_timer.stop()
            self._close_serial_connection()
            self.connected = False
            self.current_port = None
            self.previous_numeric = None
            self.previous_raw = None
            self.consecutive_timeouts = 0
            self.set_status("Connection lost, retrying...", "orange")
            # Use exponential backoff for retry
            retry_ms = int(self.reconnect_delay * 1000)
            self.log_event(f"Scheduling reconnection attempt in {self.reconnect_delay:.1f}s")
            self.detect_retry_timer.start(retry_ms)
        else:
            self.log_event(f"Non-critical error (no reconnect): {msg}")

    def _close_serial_connection(self):
        if self.connected:
            try:
                self.log_event("Closing serial connection")
                self.serial_obj.close()
            except Exception as e:
                self.log_event(f"Error during connection close: {e}")

    def stop_mode(self):
        self.log_event("Measurement stopped by user")
        self.timer.stop()
        self.health_check_timer.stop()
        self._close_serial_connection()
        self.connected = False
        self.set_status("Stopped", "red")

    def closeEvent(self, event):
        self.log_event("Application closing")
        self.timer.stop()
        self.health_check_timer.stop()
        self.detect_retry_timer.stop()
        self._close_serial_connection()
        self.connected = False
        event.accept()

    def set_status(self, text, color):
        color_map = {
            "green": "#4CAF50",
            "red": "#f44336",
            "orange": "#FF9800"
        }
        label = self.ui.label_ConnectionStatus
        label.setText(f"Status: {text}")
        palette = label.palette()
        palette.setColor(QPalette.WindowText, QColor(color_map.get(color, 'black')))
        label.setPalette(palette)
        label.setStyleSheet("font-weight: bold;")

    def log_event(self, event_text):
        """Log detailed event to console with timestamp for debugging."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        full_msg = f"[{timestamp}] {event_text}"
        print(full_msg)
    
    def append_log(self, text):
        """Append a line to the list view logger."""
        items = self.log_model.stringList()
        items.append(text)
        if len(items) > 500:
            items = items[-500:]
        self.log_model.setStringList(items)
        self.ui.listView_logger.scrollToBottom()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.showFullScreen()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
