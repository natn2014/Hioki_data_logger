# -*- coding: utf-8 -*-
"""Full-screen-friendly on-screen QWERTY keyboard for touch panels.

Companion to NumpadDialog: the numpad handles numeric limits, this handles the
text fields (model name, point name). The display is a real QLineEdit so a USB
barcode scanner can type into it too — its trailing Enter accepts the dialog.
"""
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


def _screen_scale():
    screen = QApplication.primaryScreen().availableGeometry()
    return min(screen.width() / 1280.0, screen.height() / 800.0, 1.0)


# Letter/symbol rows. Digits and symbols never change with Shift; only letters do.
_ROWS = [
    list("1234567890"),
    list("QWERTYUIOP"),
    list("ASDFGHJKL-"),
    list("ZXCVBNM()_"),
]
_SYMBOLS = [".", "/", "+", "#", "&"]


class KeyboardDialog(QDialog):
    def __init__(self, current_text="", title="Enter Text",
                 max_length=100, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self._scale = _screen_scale()
        self._upper = True          # model names are conventionally uppercase
        self._letter_buttons = []   # relabelled when Shift toggles
        self._max_length = max_length
        self._setup_ui(title, current_text)

    # ── sizing helpers (mirror NumpadDialog) ──────────────────────────────────

    def _px(self, v):
        return max(1, round(v * self._scale))

    def _sf(self, pt, bold=False):
        f = QFont()
        f.setPointSize(max(8, round(pt * self._scale)))
        if bold:
            f.setBold(True)
        return f

    def _make_key(self, label, kind="key"):
        btn = QPushButton(label)
        btn.setFont(self._sf(18, bold=True))
        btn.setMinimumSize(self._px(64), self._px(64))
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep focus in the display
        palette = {
            "key":    ("#2980b9", "#3498db"),
            "action": ("#2c3e50", "#34495e"),
            "warn":   ("#c0392b", "#e74c3c"),
        }[kind]
        btn.setStyleSheet(
            f"QPushButton{{background:{palette[0]};color:white;border-radius:8px;}}"
            f"QPushButton:pressed{{background:{palette[1]};}}"
        )
        return btn

    # ── layout ────────────────────────────────────────────────────────────────

    def _setup_ui(self, title, current_text):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self._px(14), self._px(14), self._px(14), self._px(14))
        outer.setSpacing(self._px(10))

        title_lbl = QLabel(title)
        title_lbl.setFont(self._sf(16, bold=True))
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet("color: #ddd;")
        outer.addWidget(title_lbl)

        self.display = QLineEdit(current_text)
        self.display.setFont(self._sf(28, bold=True))
        self.display.setMinimumHeight(self._px(72))
        self.display.setMaxLength(self._max_length)
        self.display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display.setStyleSheet(
            "background-color:#111; color:#00ff88; padding:8px 16px; "
            "border-radius:6px; border:2px solid #444;"
        )
        # A scanner ends its burst with Enter — treat that as OK.
        self.display.returnPressed.connect(self.accept)
        outer.addWidget(self.display)

        for row in _ROWS:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(self._px(6))
            for ch in row:
                btn = self._make_key(ch)
                if ch.isalpha():
                    self._letter_buttons.append(btn)
                btn.clicked.connect(lambda _checked=False, c=ch: self._insert_key(c))
                row_layout.addWidget(btn)
            outer.addLayout(row_layout)

        # Bottom row: Shift · symbols · Space · Backspace · Clear
        bottom = QHBoxLayout()
        bottom.setSpacing(self._px(6))

        self.shift_btn = self._make_key("⇧ abc", kind="action")
        self.shift_btn.clicked.connect(self._toggle_case)
        bottom.addWidget(self.shift_btn, 2)

        for sym in _SYMBOLS:
            b = self._make_key(sym)
            b.clicked.connect(lambda _checked=False, c=sym: self._insert_key(c))
            bottom.addWidget(b, 1)

        space = self._make_key("Space", kind="action")
        space.clicked.connect(lambda: self._insert_key(" "))
        bottom.addWidget(space, 4)

        back = self._make_key("⌫ Back", kind="warn")
        back.clicked.connect(self._backspace)
        bottom.addWidget(back, 2)

        clear = self._make_key("Clear", kind="warn")
        clear.clicked.connect(self.display.clear)
        bottom.addWidget(clear, 2)

        outer.addLayout(bottom)

        act = QHBoxLayout()
        act.setSpacing(self._px(8))
        cancel_btn = QPushButton("Cancel")
        ok_btn = QPushButton("OK")
        act_font = self._sf(20, bold=True)
        for b in (cancel_btn, ok_btn):
            b.setFont(act_font)
            b.setMinimumHeight(self._px(66))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel_btn.setStyleSheet(
            "QPushButton{background:#555;color:white;border-radius:8px;}"
            "QPushButton:pressed{background:#666;}"
        )
        ok_btn.setStyleSheet(
            "QPushButton{background:#27ae60;color:white;border-radius:8px;}"
            "QPushButton:pressed{background:#2ecc71;}"
        )
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)
        act.addWidget(cancel_btn)
        act.addWidget(ok_btn)
        outer.addLayout(act)

        self.setStyleSheet("background-color: #1a1a2e;")
        self.setMinimumWidth(self._px(880))

        self.display.setFocus()
        self.display.selectAll()  # first keypress replaces the pre-loaded text

    # ── key handling ──────────────────────────────────────────────────────────

    def _insert_key(self, ch):
        if ch.isalpha() and not self._upper:
            ch = ch.lower()
        self.display.insert(ch)
        self.display.setFocus()

    def _backspace(self):
        if self.display.hasSelectedText():
            self.display.del_()
        else:
            self.display.backspace()
        self.display.setFocus()

    def _toggle_case(self):
        self._upper = not self._upper
        self.shift_btn.setText("⇧ ABC" if not self._upper else "⇧ abc")
        for btn in self._letter_buttons:
            btn.setText(btn.text().upper() if self._upper else btn.text().lower())
        self.display.setFocus()

    def get_text(self):
        return self.display.text().strip()
