# coding: UTF-8
"""
Standalone RS485/Modbus RTU temperature monitor for Pi 5 UART0.

Hardware: RS485 transceiver wired to UART0 (/dev/ttyAMA0, enabled via
`dtoverlay=uart0` in /boot/firmware/config.txt).

The register map below is a PLACEHOLDER — different sensors expose temperature
at different holding registers, with different scale factors. Run this script
with --scan first to read raw register values and figure out which one is
your temperature, then adjust the constants (or pass the matching CLI flags).

    python rs485_temperature_monitor.py --scan --slave-id 1

NOTE on RS485 direction control: this assumes your RS485<->UART adapter does
automatic transceiver direction switching (the common case for cheap MAX485
modules with auto flow-control). If your adapter instead needs a GPIO pin
toggled as DE/RE around each transmission, that is NOT handled here — you'd
need to wire pyserial's RS485 mode (serial.rs485.RS485Settings) into the
Modbus client's underlying serial connection.
"""

import sys
import time
import argparse
import collections

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from pymodbus.client import ModbusSerialClient

# ---------------------------------------------------------------------------
# Config (placeholders - adjust per your sensor's datasheet, or use --scan)
# ---------------------------------------------------------------------------
SERIAL_PORT = "/dev/ttyAMA0"
BAUDRATE = 9600
PARITY = "N"
STOPBITS = 1
BYTESIZE = 8
SLAVE_ID = 1
REGISTER_ADDRESS = 0       # holding register index
REGISTER_COUNT = 1
SCALE = 0.1                # many sensors report temp * 10 (e.g. 253 -> 25.3 C)
POLL_INTERVAL_SEC = 1.0
HISTORY_LENGTH = 120        # points kept on the rolling chart
RECONNECT_BACKOFF_SEC = 3.0


def make_client(port, baudrate):
    return ModbusSerialClient(
        port=port,
        baudrate=baudrate,
        parity=PARITY,
        stopbits=STOPBITS,
        bytesize=BYTESIZE,
        timeout=1,
    )


def scan_registers(port, baudrate, slave_id, count=10):
    """One-shot diagnostic: print raw holding-register values 0..count-1."""
    client = make_client(port, baudrate)
    if not client.connect():
        print(f"Could not open {port} at {baudrate} baud")
        return
    try:
        result = client.read_holding_registers(0, count=count, device_id=slave_id)
        if result.isError():
            print(f"Modbus error reading registers 0-{count - 1}: {result}")
            return
        print(f"Raw holding registers 0-{count - 1} (slave {slave_id}):")
        for addr, value in enumerate(result.registers):
            print(f"  reg[{addr}] = {value}  (x0.1 -> {value * 0.1:.1f}, x0.01 -> {value * 0.01:.2f})")
    except Exception as e:
        print(f"No response reading registers 0-{count - 1} from slave {slave_id}: {e}")
        print("Check wiring, baud rate, and slave ID (RS485 sensors often default to 9600 8N1).")
    finally:
        client.close()


class ModbusReaderThread(QThread):
    reading = Signal(float, float)   # timestamp, temperature_c
    error = Signal(str)
    status = Signal(str)

    def __init__(self, port, baudrate, slave_id, register_address, register_count, scale, poll_interval):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        self.register_address = register_address
        self.register_count = register_count
        self.scale = scale
        self.poll_interval = poll_interval
        self._running = True
        self._client = None

    def stop(self):
        self._running = False

    def run(self):
        self._client = make_client(self.port, self.baudrate)

        while self._running:
            if not self._client.connected and not self._client.connect():
                self.error.emit(f"Cannot open {self.port} at {self.baudrate} baud")
                self.status.emit("disconnected")
                self._sleep(RECONNECT_BACKOFF_SEC)
                continue

            try:
                result = self._client.read_holding_registers(
                    self.register_address, count=self.register_count, device_id=self.slave_id
                )
                if result.isError():
                    self.error.emit(f"Modbus error: {result}")
                    self.status.emit("read error")
                    self._sleep(RECONNECT_BACKOFF_SEC)
                    continue

                raw = result.registers[0]
                temperature_c = raw * self.scale
                self.status.emit("connected")
                self.reading.emit(time.time(), temperature_c)
            except Exception as e:
                self.error.emit(f"Serial/Modbus exception: {e}")
                self.status.emit("disconnected")
                self._client.close()
                self._sleep(RECONNECT_BACKOFF_SEC)
                continue

            self._sleep(self.poll_interval)

        if self._client is not None:
            self._client.close()

    def _sleep(self, seconds):
        # Sleep in small steps so stop() takes effect promptly.
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(0.05)


class MainWindow(QWidget):
    def __init__(self, reader_thread):
        super().__init__()
        self.setWindowTitle("RS485 Temperature Monitor")
        self.resize(700, 500)

        self.timestamps = collections.deque(maxlen=HISTORY_LENGTH)
        self.temperatures = collections.deque(maxlen=HISTORY_LENGTH)
        self.start_time = time.time()

        self.value_label = QLabel("-- °C")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet("font-size: 48px; font-weight: bold;")

        self.status_label = QLabel("Connecting...")
        self.status_label.setAlignment(Qt.AlignCenter)

        self.figure = Figure(figsize=(5, 3))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_xlabel("seconds")
        self.ax.set_ylabel("°C")
        (self.line,) = self.ax.plot([], [])

        layout = QVBoxLayout(self)
        layout.addWidget(self.value_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.canvas)

        self.reader_thread = reader_thread
        self.reader_thread.reading.connect(self.on_reading)
        self.reader_thread.error.connect(self.on_error)
        self.reader_thread.status.connect(self.on_status)
        self.reader_thread.start()

    def on_reading(self, ts, temperature_c):
        self.value_label.setText(f"{temperature_c:.1f} °C")

        self.timestamps.append(ts - self.start_time)
        self.temperatures.append(temperature_c)

        self.line.set_data(list(self.timestamps), list(self.temperatures))
        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw_idle()

    def on_status(self, status):
        self.status_label.setText(f"Status: {status}")

    def on_error(self, message):
        self.status_label.setText(f"Status: {message}")

    def closeEvent(self, event):
        self.reader_thread.stop()
        self.reader_thread.wait(2000)
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="RS485/Modbus RTU temperature monitor")
    parser.add_argument("--port", default=SERIAL_PORT)
    parser.add_argument("--baud", type=int, default=BAUDRATE)
    parser.add_argument("--slave-id", type=int, default=SLAVE_ID)
    parser.add_argument("--register", type=int, default=REGISTER_ADDRESS)
    parser.add_argument("--scale", type=float, default=SCALE)
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL_SEC)
    parser.add_argument("--scan", action="store_true", help="Print raw register values 0-9 and exit")
    args = parser.parse_args()

    if args.scan:
        scan_registers(args.port, args.baud, args.slave_id)
        return

    app = QApplication(sys.argv)
    reader_thread = ModbusReaderThread(
        port=args.port,
        baudrate=args.baud,
        slave_id=args.slave_id,
        register_address=args.register,
        register_count=REGISTER_COUNT,
        scale=args.scale,
        poll_interval=args.poll_interval,
    )
    window = MainWindow(reader_thread)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
