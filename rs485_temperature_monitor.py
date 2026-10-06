# coding: UTF-8
"""
Standalone RS485/Modbus RTU temperature monitor for Pi 5 UART0.

Signal chain:
    Optex SA-80T-2A (4-20 mA = 0-200 C) -> GLK 4-20mA->RS485 converter
    (Modbus RTU) -> RS485 port -> Pi 5 UART0 (/dev/ttyAMA0, enabled via
    `dtoverlay=uart0` in /boot/firmware/config.txt).

GLK converter defaults: slave 1, 9600 8N1, FC03, loop current in holding
register 0x000E as signed int16, 0.01 mA per count (622 -> 6.22 mA -> 27.8 C).
Register 0 is NOT the temperature (it reads ~6222, i.e. "622 C" at x0.1).
Confirm on a new install with --scan.

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
# Config (GLK converter + SA-80T-2A; confirm with --scan)
# ---------------------------------------------------------------------------
SERIAL_PORT = "/dev/ttyAMA0"
BAUDRATE = 9600
PARITY = "N"
STOPBITS = 1
BYTESIZE = 8
SLAVE_ID = 1
REGISTER_ADDRESS = 14      # 0x000E = loop current (Process Value)
REGISTER_COUNT = 1
RAW_AT_4MA = 400           # 4.00 mA
RAW_AT_20MA = 2000         # 20.00 mA
TEMP_AT_4MA = 0.0          # SA-80T-2A range 0-200 C
TEMP_AT_20MA = 200.0
# Loop-current limits (NAMUR NE43-style): below ~3.8 mA means wire break /
# sensor fault, above ~20.5 mA means the target is over range.
UNDER_MA = 3.8
OVER_MA = 20.5
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


def raw_to_ma(raw, raw_at_4ma=RAW_AT_4MA, raw_at_20ma=RAW_AT_20MA):
    """Convert a raw register value to loop current (mA)."""
    if raw > 32767:
        raw -= 65536   # register is signed int16
    return 4.0 + (raw - raw_at_4ma) * 16.0 / (raw_at_20ma - raw_at_4ma)


def ma_to_celsius(ma, temp_at_4ma=TEMP_AT_4MA, temp_at_20ma=TEMP_AT_20MA):
    return temp_at_4ma + (ma - 4.0) * (temp_at_20ma - temp_at_4ma) / 16.0


def scan_registers(port, baudrate, slave_id, count=16):
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
            print(f"  reg[{addr:2d}] (0x{addr:04X}) = {value}")
        if REGISTER_ADDRESS < count:
            raw = result.registers[REGISTER_ADDRESS]
            ma = raw_to_ma(raw)
            print(f"\nPV reg[{REGISTER_ADDRESS}] = {raw} -> {ma:.2f} mA -> {ma_to_celsius(ma):.1f} C")
    except Exception as e:
        print(f"No response reading registers 0-{count - 1} from slave {slave_id}: {e}")
        print("Check wiring, baud rate, and slave ID (RS485 sensors often default to 9600 8N1).")
    finally:
        client.close()


class ModbusReaderThread(QThread):
    reading = Signal(float, float)   # timestamp, temperature_c
    error = Signal(str)
    status = Signal(str)

    def __init__(self, port, baudrate, slave_id, register_address, register_count, poll_interval,
                 raw_at_4ma=RAW_AT_4MA, raw_at_20ma=RAW_AT_20MA,
                 temp_at_4ma=TEMP_AT_4MA, temp_at_20ma=TEMP_AT_20MA):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        self.register_address = register_address
        self.register_count = register_count
        if raw_at_20ma == raw_at_4ma:
            raise ValueError("raw_at_4ma and raw_at_20ma must differ")
        self.raw_at_4ma = raw_at_4ma
        self.raw_at_20ma = raw_at_20ma
        self.temp_at_4ma = temp_at_4ma
        self.temp_at_20ma = temp_at_20ma
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

                ma = raw_to_ma(result.registers[0], self.raw_at_4ma, self.raw_at_20ma)
                # Out-of-range loop current is a fault, not a temperature: emit
                # no reading so the last value goes stale and TC stops applying.
                if ma < UNDER_MA:
                    self.error.emit(f"loop current {ma:.2f} mA - wire break / sensor fault")
                    self.status.emit("sensor fault (under range)")
                elif ma > OVER_MA:
                    self.error.emit(f"loop current {ma:.2f} mA - over range")
                    self.status.emit("over range")
                else:
                    self.status.emit("connected")
                    self.reading.emit(time.time(),
                                      ma_to_celsius(ma, self.temp_at_4ma, self.temp_at_20ma))
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
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL_SEC)
    parser.add_argument("--scan", action="store_true", help="Print raw register values 0-15 and exit")
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
        poll_interval=args.poll_interval,
    )
    window = MainWindow(reader_thread)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
