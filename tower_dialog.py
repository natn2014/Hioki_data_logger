"""Touch settings dialog for the PATLITE LR6-USB tower light.

Every change is applied to the TowerController at once (so a pattern can be
heard straight away); the caller saves the settings when the dialog closes
and the tower returns to the real current state.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from i18n import tr
from numpad_dialog import NumpadDialog
from tower_light import OK_BEEP_MS_RANGE
from ui_UI_Resistance import C, set_prop

# (i18n key, buzzer value) — values match patlite_lr6.BUZZER_*
NG_PATTERNS = [("tower_pat_off", 0x0), ("tower_pat_cont", 0x1), ("tower_pat_1", 0x2),
               ("tower_pat_2", 0x3), ("tower_pat_3", 0x4), ("tower_pat_4", 0x5)]

STATUS_STATE = {"tower_st_connected": "ok", "tower_st_retrying": "warn",
                "tower_st_no_access": "bad", "tower_st_no_driver": "bad",
                "tower_st_off": "off"}


class TowerLightDialog(QDialog):

    def __init__(self, tower, scale=1.0, parent=None):
        super().__init__(parent)
        self.tower = tower
        self.setObjectName("TowerDialog")
        self.setWindowTitle(tr("tower_title"))
        self.setModal(True)
        px = lambda v: max(1, round(v * scale))
        self._px = px

        outer = QVBoxLayout(self)
        outer.setContentsMargins(px(24), px(18), px(24), px(18))
        outer.setSpacing(px(12))

        title = QLabel(tr("tower_title"))
        title.setStyleSheet(f"color:{C['accent_hi']};font-size:{px(24)}px;font-weight:bold;")
        outer.addWidget(title)

        legend = QLabel(tr("tower_legend"))
        legend.setWordWrap(True)
        legend.setStyleSheet(f"color:{C['dim']};font-size:{px(16)}px;")
        outer.addWidget(legend)

        self.status = QLabel()
        self.status.setProperty("role", "chip")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # On/off toggles
        row = QHBoxLayout()
        row.setSpacing(px(12))
        self.btn_enabled = self._toggle(row)
        self.btn_buzzer = self._toggle(row)
        self.btn_enabled.clicked.connect(lambda on: self._change(enabled=on))
        self.btn_buzzer.clicked.connect(lambda on: self._change(buzzer_enabled=on))
        outer.addLayout(row)

        # NG buzzer pattern (tap = set + play one cycle)
        outer.addWidget(self._caption(tr("tower_ng_caption")))
        grid = QGridLayout()
        grid.setSpacing(px(10))
        self.pattern_btns = {}
        for i, (key, value) in enumerate(NG_PATTERNS):
            b = self._button(tr(key), checkable=True)
            b.clicked.connect(lambda _=False, v=value: self._pick_pattern(v))
            grid.addWidget(b, i // 3, i % 3)
            self.pattern_btns[value] = b
        outer.addLayout(grid)

        # OK beep
        outer.addWidget(self._caption(tr("tower_ok_caption")))
        row = QHBoxLayout()
        row.setSpacing(px(12))
        self.btn_ok_beep = self._toggle(row)
        self.btn_ok_beep.clicked.connect(lambda on: self._change(ok_beep_enabled=on))
        self.btn_ok_ms = self._button("")
        self.btn_ok_ms.clicked.connect(self._edit_ok_ms)
        row.addWidget(self.btn_ok_ms)
        outer.addLayout(row)

        # Tests
        outer.addWidget(self._caption(tr("tower_test_caption")))
        row = QHBoxLayout()
        row.setSpacing(px(12))
        for state in ("PASS", "NG", "WAITING"):
            b = self._button(tr("tower_test", state=state))
            b.clicked.connect(lambda _=False, s=state: self.tower.show_test(s))
            row.addWidget(b)
        outer.addLayout(row)

        outer.addStretch(1)
        close = self._button(tr("tower_close"), role="primary")
        close.setMinimumSize(px(200), px(64))
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        outer.addLayout(row)

        tower.status_changed.connect(self._refresh)
        self._refresh()
        self.resize(px(820), px(700))

    def done(self, result):
        try:
            self.tower.status_changed.disconnect(self._refresh)
        except (RuntimeError, TypeError):
            pass
        super().done(result)

    # ── widgets ───────────────────────────────────────────────────────────────

    def _caption(self, text):
        lbl = QLabel(text)
        lbl.setProperty("role", "caption")
        return lbl

    def _button(self, text, checkable=False, role="dlg"):
        b = QPushButton(text)
        b.setProperty("role", role)
        b.setCheckable(checkable)
        b.setAutoDefault(False)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setMinimumHeight(self._px(64))
        return b

    def _toggle(self, row):
        b = self._button("", checkable=True)
        row.addWidget(b)
        return b

    # ── actions ───────────────────────────────────────────────────────────────

    def _change(self, **kw):
        s = dict(self.tower.settings)
        s.update(kw)
        self.tower.set_settings(s)
        self._refresh()

    def _pick_pattern(self, value):
        self._change(ng_pattern=value)
        self.tower.preview_ng_pattern()

    def _edit_ok_ms(self):
        lo, hi = OK_BEEP_MS_RANGE
        dlg = NumpadDialog(current_value=self.tower.settings["ok_beep_ms"], decimals=0,
                           title=tr("tower_ok_ms_title"), min_val=lo, max_val=hi, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._change(ok_beep_ms=int(round(float(dlg.get_value()))))
            self.tower.test_ok_beep()

    def _refresh(self, *_):
        s = self.tower.settings
        on_off = lambda on: tr("tower_on") if on else tr("tower_off")
        self.status.setText(tr(self.tower.status))
        set_prop(self.status, "state", STATUS_STATE.get(self.tower.status, "off"))
        self.btn_enabled.setChecked(s["enabled"])
        self.btn_enabled.setText(tr("tower_enabled", v=on_off(s["enabled"])))
        self.btn_buzzer.setChecked(s["buzzer_enabled"])
        self.btn_buzzer.setText(tr("tower_buzzer", v=on_off(s["buzzer_enabled"])))
        self.btn_ok_beep.setChecked(s["ok_beep_enabled"])
        self.btn_ok_beep.setText(tr("tower_ok_beep", v=on_off(s["ok_beep_enabled"])))
        self.btn_ok_ms.setText(tr("tower_ok_ms", ms=s["ok_beep_ms"]))
        for value, b in self.pattern_btns.items():
            b.setChecked(value == s["ng_pattern"])
        # Sound settings mean nothing with the buzzer muted.
        for b in (*self.pattern_btns.values(), self.btn_ok_beep, self.btn_ok_ms):
            b.setEnabled(s["buzzer_enabled"])
