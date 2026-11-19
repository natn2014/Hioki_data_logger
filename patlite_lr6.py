"""PATLITE LR6-USB signal tower driver (hidapi / pyusb).

Vendored unchanged from natn2014/UniversalV1 (branch imageprocessing), which
vendors it from https://github.com/natn2014/TowerLightUSB.
"""
from __future__ import annotations

import struct
import sys

VENDOR_ID = 0x191A
DEVICE_ID = 0x8003

COMMAND_VERSION = 0x00
COMMAND_ID = 0x00

ENDPOINT_ADDRESS = 1
SEND_TIMEOUT = 1000

LED_COLOR_RED = 0
LED_COLOR_YELLOW = 1
LED_COLOR_GREEN = 2
LED_COLOR_BLUE = 3
LED_COLOR_WHITE = 4

LED_OFF = 0x0
LED_ON = 0x1
LED_PATTERN1 = 0x2
LED_PATTERN2 = 0x3
LED_PATTERN3 = 0x4
LED_PATTERN4 = 0x5
LED_KEEP = 0xF

BUZZER_OFF = 0x0
BUZZER_ON = 0x1
BUZZER_PATTERN1 = 0x2
BUZZER_PATTERN2 = 0x3
BUZZER_PATTERN3 = 0x4
BUZZER_PATTERN4 = 0x5
BUZZER_KEEP = 0xF

BUZZER_PITCH_OFF = 0x0
BUZZER_PITCH_DFLT_A = 0xE
BUZZER_PITCH_DFLT_B = 0xF

LED_PATTERN_NAMES = [
    ("Off", LED_OFF),
    ("On (steady)", LED_ON),
    ("Pattern 1", LED_PATTERN1),
    ("Pattern 2", LED_PATTERN2),
    ("Pattern 3", LED_PATTERN3),
    ("Pattern 4", LED_PATTERN4),
]

BUZZER_PATTERN_NAMES = [
    ("Off", BUZZER_OFF),
    ("Continuous", BUZZER_ON),
    ("Pattern 1", BUZZER_PATTERN1),
    ("Pattern 2", BUZZER_PATTERN2),
    ("Pattern 3", BUZZER_PATTERN3),
    ("Pattern 4", BUZZER_PATTERN4),
]


class TowerLightError(Exception):
    """Raised when the tower light cannot be opened or written to."""


def build_command(red=LED_KEEP, yellow=LED_KEEP, green=LED_KEEP,
                  blue=LED_KEEP, white=LED_KEEP,
                  buzzer=BUZZER_KEEP, buzzer_limit=0,
                  pitch_a=BUZZER_PITCH_DFLT_A, pitch_b=BUZZER_PITCH_DFLT_B) -> bytes:
    """Pack one 8-byte control command. Nibbles left at LED_KEEP are untouched."""
    return struct.pack(
        'BBBBBBBx',
        COMMAND_VERSION,
        COMMAND_ID,
        (buzzer_limit << 4) | buzzer,
        (pitch_a << 4) | pitch_b,
        (red << 4) | yellow,
        (green << 4) | blue,
        white << 4,
    )


class _HidBackend:
    """Transport via the hidapi package - uses the stock Windows HID driver."""

    name = 'hidapi'

    def __init__(self):
        import hid
        self._hid = hid
        self._dev = None

    def open(self):
        hid = self._hid
        if hasattr(hid, 'device'):
            dev = hid.device()
            dev.open(VENDOR_ID, DEVICE_ID)
        else:
            dev = hid.Device(vid=VENDOR_ID, pid=DEVICE_ID)
        self._dev = dev

    def write(self, data: bytes):
        self._dev.write(b'\x00' + data)

    def close(self):
        if self._dev is not None:
            try:
                self._dev.close()
            finally:
                self._dev = None


class _UsbBackend:
    """Transport via pyusb, matching PATLITE's official sample."""

    name = 'pyusb'

    def __init__(self):
        import usb.core
        self._usb = usb.core
        self._dev = None

    def open(self):
        dev = self._usb.find(idVendor=VENDOR_ID, idProduct=DEVICE_ID)
        if dev is None:
            raise TowerLightError('device not found')
        if sys.platform == 'linux' and dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
        dev.set_configuration()
        self._dev = dev

    def write(self, data: bytes):
        try:
            written = self._dev.write(ENDPOINT_ADDRESS, data, SEND_TIMEOUT)
            if sys.platform == 'win32':
                written -= 1
            if written != len(data):
                raise TowerLightError('short write: %d of %d bytes' % (written, len(data)))
        finally:
            self._dev.reset()

    def close(self):
        self._dev = None


class TowerLight:
    """
    Controls one LR6-USB signal tower.

    Tracks the current pattern of every tier so the UI can toggle a single
    colour without disturbing the others, and re-sends the full state on each
    command (more predictable than relying on the device's KEEP nibble).
    """

    def __init__(self, backend: str = 'auto'):
        self._backend_pref = backend
        self._backend = None
        self.state = {
            LED_COLOR_RED: LED_OFF,
            LED_COLOR_YELLOW: LED_OFF,
            LED_COLOR_GREEN: LED_OFF,
            LED_COLOR_BLUE: LED_OFF,
            LED_COLOR_WHITE: LED_OFF,
        }
        self.buzzer = BUZZER_OFF

    @property
    def is_open(self) -> bool:
        return self._backend is not None

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend else 'none'

    def open(self):
        """Open the tower, trying each available backend in turn."""
        if self._backend is not None:
            return

        candidates = []
        if self._backend_pref in ('auto', 'hidapi'):
            candidates.append(_HidBackend)
        if self._backend_pref in ('auto', 'pyusb'):
            candidates.append(_UsbBackend)

        errors = []
        for factory in candidates:
            try:
                backend = factory()
            except ImportError as exc:
                errors.append('%s: package not installed (%s)' % (factory.name, exc))
                continue
            try:
                backend.open()
            except Exception as exc:
                errors.append('%s: %s' % (factory.name, exc))
                continue
            self._backend = backend
            return

        raise TowerLightError(
            'Could not open LR6-USB (VID 0x%04X / PID 0x%04X).\n  ' % (VENDOR_ID, DEVICE_ID)
            + '\n  '.join(errors)
        )

    def close(self):
        if self._backend is not None:
            try:
                self._backend.close()
            finally:
                self._backend = None

    def _send(self, data: bytes):
        if self._backend is None:
            raise TowerLightError('not connected')
        self._backend.write(data)

    def apply(self):
        """Push the tracked LED and buzzer state to the device."""
        self._send(build_command(
            red=self.state[LED_COLOR_RED],
            yellow=self.state[LED_COLOR_YELLOW],
            green=self.state[LED_COLOR_GREEN],
            blue=self.state[LED_COLOR_BLUE],
            white=self.state[LED_COLOR_WHITE],
            buzzer=self.buzzer,
            buzzer_limit=0,
        ))

    def set_light(self, color: int, pattern: int):
        """Set one tier's pattern, leaving the others as they are."""
        if color not in self.state:
            raise ValueError('out of range color: %r' % color)
        self.state[color] = pattern
        self.apply()

    def toggle(self, color: int, on_pattern: int = LED_ON) -> int:
        """Flip one tier between off and on_pattern. Returns the new pattern."""
        new = LED_OFF if self.state[color] != LED_OFF else on_pattern
        self.set_light(color, new)
        return new

    def set_tower(self, red=LED_OFF, yellow=LED_OFF, green=LED_OFF,
                  blue=LED_OFF, white=LED_OFF):
        """Set every tier at once."""
        self.state.update({
            LED_COLOR_RED: red,
            LED_COLOR_YELLOW: yellow,
            LED_COLOR_GREEN: green,
            LED_COLOR_BLUE: blue,
            LED_COLOR_WHITE: white,
        })
        self.apply()

    def set_buzzer(self, pattern: int, limit: int = 0):
        """Set the buzzer pattern. limit 0 = continuous, 1-15 = repeat count."""
        self.buzzer = pattern
        self._send(build_command(
            red=self.state[LED_COLOR_RED],
            yellow=self.state[LED_COLOR_YELLOW],
            green=self.state[LED_COLOR_GREEN],
            blue=self.state[LED_COLOR_BLUE],
            white=self.state[LED_COLOR_WHITE],
            buzzer=pattern,
            buzzer_limit=limit,
        ))

    def reset(self):
        """All LEDs off, buzzer stopped."""
        for key in self.state:
            self.state[key] = LED_OFF
        self.buzzer = BUZZER_OFF
        self._send(build_command(
            red=LED_OFF, yellow=LED_OFF, green=LED_OFF,
            blue=LED_OFF, white=LED_OFF,
            buzzer=BUZZER_OFF, buzzer_limit=0,
            pitch_a=BUZZER_PITCH_OFF, pitch_b=BUZZER_PITCH_OFF,
        ))

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
