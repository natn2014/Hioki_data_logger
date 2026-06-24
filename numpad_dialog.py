# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


def _screen_scale():
    screen = QApplication.primaryScreen().availableGeometry()
    return min(screen.width() / 1280.0, screen.height() / 800.0, 1.0)


class NumpadDialog(QDialog):
    def __init__(self, current_value=0.0, decimals=3, title="Enter Value",
                 min_val=None, max_val=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.decimals = decimals
        self.min_val = min_val
        self.max_val = max_val
        self._value_str = f"{current_value:.{decimals}f}"
        self._is_fresh = True  # first keypress clears the pre-loaded value
        self._scale = _screen_scale()
        self._setup_ui(title)

    def _px(self, v):
        return max(1, round(v * self._scale))

    def _sf(self, pt, bold=False):
        f = QFont()
        f.setPointSize(max(8, round(pt * self._scale)))
        if bold:
            f.setBold(True)
        return f

    def _setup_ui(self, title):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self._px(14), self._px(14), self._px(14), self._px(14))
        outer.setSpacing(self._px(10))

        title_lbl = QLabel(title)
        title_lbl.setFont(self._sf(16, bold=True))
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet("color: #ddd;")
        outer.addWidget(title_lbl)

        self.display = QLabel(self._value_str)
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.display.setFont(self._sf(32, bold=True))
        self.display.setMinimumHeight(self._px(72))
        self.display.setStyleSheet(
            "background-color: #111; color: #00ff88; padding: 8px 16px; "
            "border-radius: 6px; border: 2px solid #444;"
        )
        outer.addWidget(self.display)

        grid = QGridLayout()
        grid.setSpacing(self._px(8))

        buttons = [
            ('7', 0, 0), ('8', 0, 1), ('9', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('1', 2, 0), ('2', 2, 1), ('3', 2, 2),
            ('.', 3, 0), ('0', 3, 1), ('<', 3, 2),
        ]
        btn_font = self._sf(24, bold=True)
        for lbl, row, col in buttons:
            btn = QPushButton(lbl)
            btn.setFont(btn_font)
            btn.setMinimumSize(self._px(100), self._px(80))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            if lbl == '<':
                btn.setStyleSheet(
                    "QPushButton{background:#c0392b;color:white;border-radius:8px;}"
                    "QPushButton:pressed{background:#e74c3c;}"
                )
            elif lbl == '.':
                btn.setStyleSheet(
                    "QPushButton{background:#2c3e50;color:white;border-radius:8px;}"
                    "QPushButton:pressed{background:#34495e;}"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton{background:#2980b9;color:white;border-radius:8px;}"
                    "QPushButton:pressed{background:#3498db;}"
                )
            btn.clicked.connect(lambda checked, l=lbl: self._on_key(l))
            grid.addWidget(btn, row, col)

        outer.addLayout(grid)

        act = QHBoxLayout()
        act.setSpacing(self._px(8))
        cancel_btn = QPushButton("Cancel")
        ok_btn = QPushButton("OK")
        act_font = self._sf(20, bold=True)
        for b in (cancel_btn, ok_btn):
            b.setFont(act_font)
            b.setMinimumHeight(self._px(70))
        cancel_btn.setStyleSheet(
            "QPushButton{background:#555;color:white;border-radius:8px;}"
            "QPushButton:pressed{background:#666;}"
        )
        ok_btn.setStyleSheet(
            "QPushButton{background:#27ae60;color:white;border-radius:8px;}"
            "QPushButton:pressed{background:#2ecc71;}"
        )
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self._on_ok)
        act.addWidget(cancel_btn)
        act.addWidget(ok_btn)
        outer.addLayout(act)

        self.setStyleSheet("background-color: #1a1a2e;")
        self.setMinimumWidth(self._px(360))

    def _on_key(self, key):
        if self._is_fresh:
            self._value_str = ''
            self._is_fresh = False

        if key == '<':
            self._value_str = self._value_str[:-1]
        elif key == '.':
            if '.' not in self._value_str:
                if not self._value_str:
                    self._value_str = '0.'
                else:
                    self._value_str += '.'
        else:
            if '.' in self._value_str:
                after_dot = len(self._value_str) - self._value_str.index('.') - 1
                if after_dot >= self.decimals:
                    return
            self._value_str += key

        self.display.setText(self._value_str if self._value_str else '0')

    def _on_ok(self):
        try:
            val = float(self._value_str)
        except ValueError:
            self._value_str = ''
            self.display.setText('0')
            return
        if self.min_val is not None and val < self.min_val:
            self._value_str = f"{self.min_val:.{self.decimals}f}"
            self.display.setText(self._value_str)
            return
        if self.max_val is not None and val > self.max_val:
            self._value_str = f"{self.max_val:.{self.decimals}f}"
            self.display.setText(self._value_str)
            return
        self.accept()

    def get_value(self):
        try:
            return round(float(self._value_str), self.decimals)
        except ValueError:
            return 0.0
