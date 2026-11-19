"""PATLITE LR6-USB tower light for the PASS / NG / WAITING result.

    PASS     green steady   + one short beep
    NG       red steady     + the selected buzzer pattern, repeating
    WAITING  yellow blink   in step with the current point card, silent

Adapted from natn2014/UniversalV1 app/tower.py. The tower is optional: every
hardware error drops the connection instead of raising, so a missing or
yanked tower never stops measuring. A watchdog reconnects it when it comes
back and shows the current state again.
"""
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

try:
    from patlite_lr6 import (TowerLight, LED_OFF, LED_ON, BUZZER_OFF,
                             BUZZER_ON, BUZZER_PATTERN_NAMES)
except Exception:                       # driver file missing or broken
    TowerLight = None
    LED_OFF, LED_ON = 0x0, 0x1
    BUZZER_OFF, BUZZER_ON = 0x0, 0x1
    BUZZER_PATTERN_NAMES = [("Off", 0x0), ("Continuous", 0x1), ("Pattern 1", 0x2),
                            ("Pattern 2", 0x3), ("Pattern 3", 0x4), ("Pattern 4", 0x5)]

try:
    import hid
except Exception:                       # hidapi not installed
    hid = None

PATLITE_VID, PATLITE_PID = 0x191A, 0x8003
WATCHDOG_MS = 2000                      # cable check / reconnect interval

STATES = ("PASS", "NG", "WAITING")

TOWER_DEFAULTS = {
    "enabled": True,                    # use the tower at all
    "buzzer_enabled": True,             # master mute for NG alarm and OK beep
    "ng_pattern": BUZZER_ON,            # 0 off, 1 continuous, 2-5 pattern 1-4
    "ok_beep_enabled": True,
    "ok_beep_ms": 50,
}
OK_BEEP_MS_RANGE = (10, 500)


def normalize_tower_settings(raw):
    """Defaults merged with a saved "tower" config block; bad values ignored."""
    s = dict(TOWER_DEFAULTS)
    if not isinstance(raw, dict):
        return s
    for key in ("enabled", "buzzer_enabled", "ok_beep_enabled"):
        if key in raw:
            s[key] = bool(raw[key])
    try:
        if int(raw.get("ng_pattern", s["ng_pattern"])) in range(0, 6):
            s["ng_pattern"] = int(raw.get("ng_pattern", s["ng_pattern"]))
        lo, hi = OK_BEEP_MS_RANGE
        s["ok_beep_ms"] = min(hi, max(lo, int(raw.get("ok_beep_ms", s["ok_beep_ms"]))))
    except (TypeError, ValueError):
        pass
    return s


def device_present() -> Optional[bool]:
    """True/False if the tower is on the USB bus; None if that can't be
    checked (no hidapi), in which case only write errors detect a pull."""
    if hid is None:
        return None
    try:
        return bool(hid.enumerate(PATLITE_VID, PATLITE_PID))
    except Exception:
        return None


class TowerController(QObject):
    """Drives the tower from the app's PASS / NG / WAITING state.

    status_changed carries an i18n key (tower_st_*) for the settings dialog;
    message carries an i18n key for one DATA LOG line."""

    status_changed = Signal(str)
    message = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.settings = dict(TOWER_DEFAULTS)
        self._tower = None
        self._connected = False
        self._last_state: Optional[str] = None
        # Latest state asked for, even while disconnected: re-applied on reconnect.
        self._wanted_state: Optional[str] = None
        self._ok_beep_token = 0
        self._blink_phase = True        # yellow on/off, driven by blink()
        self.status = "tower_st_off"
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_MS)
        self._watchdog.timeout.connect(self._check_connection)

    # ── status ────────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected and self._tower is not None

    @property
    def wanted_state(self) -> Optional[str]:
        return self._wanted_state

    def _set_status(self, key: str) -> None:
        if key != self.status:      # the watchdog retries every 2 s
            self.status = key
            self.status_changed.emit(key)

    def _status_not_connected(self) -> None:
        """Says why: unplugged, or plugged in but not allowed to open it."""
        self._set_status("tower_st_no_access" if device_present() else "tower_st_retrying")

    # ── settings ──────────────────────────────────────────────────────────────

    def set_settings(self, settings: dict) -> None:
        """Apply new settings: start / stop the tower and re-sync the buzzer."""
        self.settings = normalize_tower_settings(settings)
        running = self._watchdog.isActive() or self.connected
        if self.settings["enabled"] and not running:
            self.connect_device()
        elif not self.settings["enabled"] and running:
            self.shutdown()
        elif not self.settings["enabled"]:
            self._set_status("tower_st_off")
        else:
            self._sync_buzzer_now()

    def _ng_buzzer(self, state: Optional[str]) -> int:
        if self.settings["buzzer_enabled"] and state == "NG":
            return self.settings["ng_pattern"]
        return BUZZER_OFF

    # ── connection ────────────────────────────────────────────────────────────

    def connect_device(self) -> None:
        """Connect now and keep reconnecting (watchdog) until shutdown()."""
        if not self.settings["enabled"]:
            self._set_status("tower_st_off")
            return
        if TowerLight is None or hid is None:
            self._set_status("tower_st_no_driver")
            return
        self._watchdog.start()
        if self._try_open():
            self._reapply()
        else:
            self._status_not_connected()

    def _try_open(self) -> bool:
        if self.connected:
            return True
        if device_present() is False:
            return False    # don't open (and log) a device that isn't plugged in
        try:
            self._tower = TowerLight(backend="auto")
            self._tower.open()
        except Exception:
            self._tower = None
            self._connected = False
            return False
        self._connected = True
        self._set_status("tower_st_connected")
        return True

    def _check_connection(self) -> None:
        if self.connected:
            if device_present() is False:
                self._disconnected()
            return
        if not self._try_open():
            self._status_not_connected()
            return
        self.message.emit("log_tower_reconnected")
        self._reapply()

    def _reapply(self) -> None:
        """Show the current state again, not the tower's power-on state."""
        self._last_state = None
        if self._wanted_state is not None:
            self.update_state(self._wanted_state)

    def shutdown(self) -> None:
        """All lights off, buzzer stopped, watchdog stopped."""
        self._watchdog.stop()
        if self._tower is not None:
            try:
                self._tower.reset()
            except Exception:
                pass
            try:
                self._tower.close()
            except Exception:
                pass
        self._tower = None
        self._connected = False
        self._last_state = None
        self._set_status("tower_st_off")

    def _disconnected(self) -> None:
        """Drop the dead handle; the watchdog reconnects when it's back."""
        was_connected = self._connected
        if self._tower is not None:
            try:
                self._tower.close()
            except Exception:
                pass
        self._tower = None
        self._connected = False
        self._last_state = None
        self._set_status("tower_st_retrying")
        if was_connected:
            self.message.emit("log_tower_disconnected")

    # ── state ─────────────────────────────────────────────────────────────────

    def update_state(self, state: str) -> None:
        """PASS / NG / WAITING. Repeats of the shown state are ignored."""
        if state not in STATES:
            return
        self._wanted_state = state
        if not self.connected or state == self._last_state:
            return
        try:
            # Buzzer first: set_tower() re-sends the driver's remembered buzzer
            # value, so it must already be right or a stale one chirps briefly.
            self._tower.set_buzzer(self._ng_buzzer(state))
            if state == "PASS":
                self._tower.set_tower(green=LED_ON)
            elif state == "NG":
                self._tower.set_tower(red=LED_ON)
            else:
                self._tower.set_tower(yellow=LED_ON if self._blink_phase else LED_OFF)
            self._last_state = state
            # Beep after set_tower(), which re-sends the (off) buzzer value and
            # would otherwise cancel the tone.
            if (state == "PASS" and self.settings["buzzer_enabled"]
                    and self.settings["ok_beep_enabled"]):
                self._play_ok_beep()
        except Exception:
            self._disconnected()

    def blink(self, on: bool) -> None:
        """One blink step from the app's point-card timer: while WAITING the
        yellow light turns on / off together with the current point card."""
        self._blink_phase = bool(on)
        if not self.connected or self._last_state != "WAITING":
            return
        try:
            self._tower.set_tower(yellow=LED_ON if on else LED_OFF)
        except Exception:
            self._disconnected()

    def show_test(self, state: str) -> None:
        """Show a state for testing without changing the wanted state. An NG
        test plays one cycle of the pattern; restore() brings back the real one."""
        if not self.connected or state not in STATES:
            return
        real = self._wanted_state
        self._last_state = None
        self.update_state(state)
        self._wanted_state = real
        if state == "NG" and self.settings["buzzer_enabled"]:
            try:
                self._tower.set_buzzer(self.settings["ng_pattern"], limit=1)
            except Exception:
                self._disconnected()

    def restore(self) -> None:
        """Back to the real current state (after tests in the settings dialog)."""
        if self.connected:
            self._reapply()

    # ── buzzer ────────────────────────────────────────────────────────────────

    def preview_ng_pattern(self) -> None:
        """One cycle of the NG pattern; the tower stops it by itself."""
        if not self.connected or self.settings["ng_pattern"] == BUZZER_OFF:
            return
        try:
            self._tower.set_buzzer(self.settings["ng_pattern"], limit=1)
        except Exception:
            self._disconnected()

    def test_ok_beep(self) -> None:
        self._play_ok_beep()

    def _play_ok_beep(self) -> None:
        """Short confirmation beep: the built-in patterns are all too long, so
        switch the tone on and turn it off again after ok_beep_ms."""
        if not self.connected:
            return
        self._ok_beep_token += 1
        token = self._ok_beep_token
        try:
            self._tower.set_buzzer(BUZZER_ON)
        except Exception:
            self._disconnected()
            return
        QTimer.singleShot(self.settings["ok_beep_ms"], lambda: self._end_ok_beep(token))

    def _end_ok_beep(self, token: int) -> None:
        if token != self._ok_beep_token or not self.connected:
            return      # a newer beep owns the buzzer now
        try:
            # Whatever the state wants now: silent, or the alarm if it turned NG.
            self._tower.set_buzzer(self._ng_buzzer(self._last_state))
        except Exception:
            self._disconnected()

    def _sync_buzzer_now(self) -> None:
        """Re-apply the buzzer at once after a settings change (an active NG
        alarm follows the new pattern / mute immediately)."""
        if not self.connected:
            return
        try:
            self._tower.set_buzzer(self._ng_buzzer(self._last_state))
        except Exception:
            self._disconnected()
