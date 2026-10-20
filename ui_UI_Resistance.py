# -*- coding: utf-8 -*-
"""Main-window layout for the HIOKI data logger kiosk.

Designed for a 10.1" 1280x800 capacitive touch panel (~150 PPI, so 1 mm ≈ 6 px):
primary touch targets are >= 64 px (~11 mm), secondary >= 56 px, with >= 12 px
gaps. Smaller screens shrink everything by build_qss(scale) / Ui_Dialog(scale).

Hand-maintained (UI_Resistance.ui is no longer the source). Visual language
follows HIOKI_UI_mockup.html: dark panels, monospace numbers, state colours.

  ┌ USB ─┬ WiFi ┬ DB ──┬ MODEL ─────────────────────────┬ clock ┐
  ├ Measured — point, live value, tolerance gauge ┬ Upper ┤ DATA   │
  │                                               ├ Lower ┤ LOG    │
  ├ point cards (tap to jump) ────────────────────────────┤        │
  ├ PASS / FAIL verdict ──────────────────────────────────┤        │
  └ tool buttons ─────────────────────────────────────────┴────────┘
"""

from PySide6.QtCore import QCoreApplication, QMetaObject, QRectF, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)

# Palette shared with the mockup, the dialogs and the gauge painter.
C = {
    "bg": "#0b1015", "panel": "#121a21", "panel2": "#18232c", "panel3": "#1e2c37",
    "edge": "#26363f", "edge_soft": "#1c2831",
    "ink": "#e7eff3", "dim": "#8ba1ad", "faint": "#5f7481",
    "accent": "#19b7d4", "accent_hi": "#46d2ec",
    "pass": "#2ecc71", "pass_deep": "#14472c", "pass_edge": "#1f7a48",
    "fail": "#ff5d5d", "fail_deep": "#4f1d1f", "fail_edge": "#a33a3a",
    "warn": "#f5b13d", "na": "#74858f",
}
MONO = "DejaVu Sans Mono"
SANS = "Piboto, Roboto, DejaVu Sans, sans-serif"


def build_qss(scale=1.0):
    """Application-wide stylesheet. Dynamic properties ("state", "active",
    "result") select the per-state colours; call repolish() after changing one."""
    def px(v):
        return max(1, round(v * scale))
    c = C
    return f"""
* {{ font-family: {SANS}; }}
QDialog, QMessageBox {{ background: {c['bg']}; color: {c['ink']}; font-size: {px(17)}px; }}
QLabel {{ color: {c['ink']}; background: transparent; }}
QToolTip {{ background: {c['panel3']}; color: {c['ink']}; border: 1px solid {c['edge']}; }}

/* ── panels ─────────────────────────────────────────────────────────── */
QFrame[panel="true"] {{
    background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: {px(12)}px;
}}
QLabel[role="caption"] {{
    color: {c['faint']}; font-family: {MONO}; font-size: {px(12)}px; letter-spacing: 1px;
}}
QLabel[role="tag"] {{ color: {c['accent']}; font-family: {MONO}; font-size: {px(12)}px; }}

/* ── status chips (top bar) ─────────────────────────────────────────── */
QLabel[role="chip"] {{ font-family: {MONO}; font-size: {px(16)}px; font-weight: bold; }}
QLabel[role="chip"][state="ok"]   {{ color: {c['pass']}; }}
QLabel[role="chip"][state="warn"] {{ color: {c['warn']}; }}
QLabel[role="chip"][state="bad"]  {{ color: {c['fail']}; }}
QLabel[role="chip"][state="off"]  {{ color: {c['na']}; }}
QLabel#label_clock {{ font-family: {MONO}; font-size: {px(26)}px; font-weight: bold; color: {c['ink']}; }}

/* ── model card ─────────────────────────────────────────────────────── */
QPushButton#pushButton_model {{
    background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: {px(12)}px;
    text-align: left;
}}
QPushButton#pushButton_model:pressed {{ background: {c['panel3']}; border-color: {c['accent']}; }}
QLabel#label_model_name {{ font-family: {MONO}; font-weight: bold; color: {c['ink']}; }}
QLabel#label_model_name[empty="true"] {{ color: {c['warn']}; }}

/* ── measured value ─────────────────────────────────────────────────── */
QLabel#label_probe_point {{ font-family: {MONO}; font-size: {px(30)}px; font-weight: bold; color: {c['accent_hi']}; }}
QLabel#label_point_index {{ font-family: {MONO}; font-size: {px(17)}px; color: {c['dim']}; }}
QLabel#label_measure_value {{ font-family: {MONO}; font-weight: bold; }}
QLabel#label_measure_value[state="idle"]     {{ color: {c['faint']}; }}
QLabel#label_measure_value[state="settling"] {{ color: {c['warn']}; }}
QLabel#label_measure_value[state="stable"]   {{ color: {c['ink']}; }}
QLabel#label_measure_value[state="pass"]     {{ color: {c['pass']}; }}
QLabel#label_measure_value[state="fail"]     {{ color: {c['fail']}; }}
QLabel#label_measure_value[state="standby"]  {{ color: {c['dim']}; }}
QLabel#label_unit {{ font-family: {MONO}; font-size: {px(44)}px; color: {c['dim']}; }}
QLabel#label_measure_status {{ font-family: {MONO}; font-size: {px(16)}px; color: {c['dim']}; }}

/* ── limit tiles ────────────────────────────────────────────────────── */
QPushButton[role="limit"] {{
    background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: {px(12)}px;
}}
QPushButton[role="limit"]:pressed {{ background: {c['panel3']}; }}
QPushButton[role="limit"][editable="true"] {{ border: 2px dashed {c['accent']}; }}
QLabel[role="limit_value"] {{ font-family: {MONO}; font-size: {px(46)}px; font-weight: bold; color: {c['ink']}; }}

/* ── point cards ────────────────────────────────────────────────────── */
QScrollArea#point_cards_area, QWidget#point_cards_host {{ background: transparent; border: none; }}
QPushButton[role="card"] {{
    background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: {px(10)}px;
}}
QPushButton[role="card"][active="true"] {{
    background: #12323b; border: {px(3)}px solid {c['accent']};
}}
QPushButton[role="card"]:pressed {{ background: {c['panel3']}; }}
QLabel[role="card_name"]  {{ font-family: {MONO}; font-size: {px(22)}px; font-weight: bold; color: {c['ink']}; }}
QLabel[role="card_range"] {{ font-family: {MONO}; font-size: {px(15)}px; color: {c['dim']}; }}
QLabel[role="card_mark"]  {{ font-family: {MONO}; font-size: {px(15)}px; font-weight: bold; color: {c['faint']}; }}
QLabel[role="card_mark"][result="pass"] {{ color: {c['pass']}; }}
QLabel[role="card_mark"][result="fail"] {{ color: {c['fail']}; }}
QLabel#label_single_hint {{ color: {c['dim']}; font-size: {px(16)}px; }}

/* ── verdict ────────────────────────────────────────────────────────── */
QFrame#frame_verdict {{ border-radius: {px(14)}px; border: 2px solid {c['edge']}; background: {c['panel2']}; }}
QFrame#frame_verdict[state="pass"] {{ background: {c['pass_deep']}; border-color: {c['pass_edge']}; }}
QFrame#frame_verdict[state="fail"] {{ background: {c['fail_deep']}; border-color: {c['fail_edge']}; }}
QLabel#label_verdict {{ font-family: {MONO}; font-size: {px(76)}px; font-weight: bold; letter-spacing: {px(8)}px; color: {c['faint']}; }}
QFrame#frame_verdict[state="pass"] QLabel {{ color: {c['pass']}; }}
QFrame#frame_verdict[state="fail"] QLabel {{ color: {c['fail']}; }}
QFrame#frame_verdict[state="na"] QLabel   {{ color: {c['na']}; }}
QLabel#label_verdict_detail {{ font-family: {MONO}; font-size: {px(18)}px; color: {c['dim']}; }}

/* ── buttons ──────────────────────────────────────────────────────────
   Only buttons with a role (plus message-box buttons) get the base look, so
   the keyboard / numpad keys keep their own fonts and sizes. */
QPushButton[role="tool"], QPushButton[role="dlg"], QPushButton[role="primary"],
QPushButton[role="danger"], QMessageBox QPushButton {{
    background: {c['panel2']}; color: {c['ink']}; border: 1px solid {c['edge']};
    border-radius: {px(10)}px; font-size: {px(17)}px; font-weight: bold;
    min-height: {px(56)}px; min-width: {px(110)}px; padding: 0 {px(14)}px;
}}
QPushButton[role="tool"]:pressed, QPushButton[role="dlg"]:pressed,
QMessageBox QPushButton:pressed {{ background: {c['panel3']}; border-color: {c['accent']}; }}
QPushButton[role="tool"]:disabled, QPushButton[role="dlg"]:disabled {{ color: {c['faint']}; background: {c['panel']}; border-color: {c['edge_soft']}; }}
QPushButton[role="tool"] {{ min-height: {px(64)}px; }}
QPushButton[role="tool"][attention="true"] {{ border: 2px solid {c['warn']}; color: {c['warn']}; }}
QPushButton#btn_hold_toggle:checked {{ background: #17394a; border: 2px solid {c['accent']}; color: {c['accent_hi']}; }}
QPushButton[role="primary"] {{ background: {c['pass_edge']}; border-color: {c['pass']}; }}
QPushButton[role="primary"]:pressed {{ background: {c['pass']}; }}
QPushButton[role="danger"] {{ background: {c['fail_deep']}; border-color: {c['fail_edge']}; color: {c['fail']}; min-width: {px(56)}px; padding: 0; }}

/* ── data log ───────────────────────────────────────────────────────── */
QTextBrowser#textBrowser_log {{
    background: {c['panel']}; border: none; color: {c['ink']};
    font-family: {MONO}; font-size: {px(14)}px;
}}
QLabel[role="count"] {{ font-family: {MONO}; font-size: {px(26)}px; font-weight: bold; }}
QLabel[role="count"][state="ok"]  {{ color: {c['pass']}; }}
QLabel[role="count"][state="bad"] {{ color: {c['fail']}; }}
QLabel[role="count"][state="off"] {{ color: {c['ink']}; }}

/* ── inputs (spec dialog) ───────────────────────────────────────────── */
#SpecDialog QLineEdit, #SpecDialog QDoubleSpinBox {{
    background: {c['panel2']}; color: {c['ink']}; border: 1px solid {c['edge']};
    border-radius: {px(8)}px; padding: 0 {px(10)}px; min-height: {px(56)}px;
    font-family: {MONO}; font-size: {px(20)}px;
    selection-background-color: {c['accent']};
}}
#SpecDialog QLineEdit:focus, #SpecDialog QDoubleSpinBox:focus {{ border: 2px solid {c['accent']}; }}
QScrollArea {{ background: transparent; border: none; }}
#SpecDialog QWidget#qt_scrollarea_viewport, QWidget#rows_container {{ background: transparent; }}
QScrollBar:vertical {{ background: {c['panel']}; width: {px(14)}px; margin: 0; border-radius: {px(7)}px; }}
QScrollBar::handle:vertical {{ background: {c['edge']}; min-height: {px(40)}px; border-radius: {px(7)}px; }}
QScrollBar:horizontal {{ background: {c['panel']}; height: {px(10)}px; margin: 0; border-radius: {px(5)}px; }}
QScrollBar::handle:horizontal {{ background: {c['edge']}; min-width: {px(40)}px; border-radius: {px(5)}px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""


def repolish(widget):
    """Re-apply the stylesheet after a dynamic property change (children too,
    since rules like `#frame_verdict[state=pass] QLabel` depend on the parent)."""
    for w in [widget] + widget.findChildren(QWidget):
        w.style().unpolish(w)
        w.style().polish(w)


def set_prop(widget, name, value):
    """Set a style property and repolish only when it actually changed."""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


def _caption(text, parent=None):
    lbl = QLabel(text, parent)
    lbl.setProperty("role", "caption")
    return lbl


def _panel(parent=None):
    f = QFrame(parent)
    f.setProperty("panel", True)
    return f


class FitLabel(QLabel):
    """Single-line label drawn as large as fits: shrinks from max_px down to
    min_px, then elides. Used for the measured value and the model name, whose
    length varies (0.012 vs 1234.567, or a 40-char barcode model)."""

    def __init__(self, text="", max_px=150, min_px=14, parent=None):
        super().__init__(text, parent)
        self._max_px, self._min_px = max_px, min_px
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(1)

    def setText(self, text):
        self._full = text
        self.setToolTip(text)
        self._fit()

    def text(self):
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        avail_w = max(10, self.width() - 4)
        avail_h = max(10, self.height())
        f = QFont(self.font())
        size = min(self._max_px, int(avail_h * 0.9))
        while size > self._min_px:
            f.setPixelSize(size)
            if QFontMetrics(f).horizontalAdvance(self._full) <= avail_w:
                break
            size -= max(1, size // 12)
        f.setPixelSize(max(self._min_px, size))
        self.setFont(f)
        shown = QFontMetrics(f).elidedText(self._full, Qt.TextElideMode.ElideMiddle, avail_w)
        super().setText(shown)


class ToleranceGauge(QWidget):
    """Horizontal band: the pass window [lower, upper] in green with a margin of
    out-of-range on each side, and a marker at the current value. Lets the
    operator see *how close* a reading is to a limit, not only PASS/FAIL."""

    def __init__(self, scale=1.0, parent=None, decimals=2):
        super().__init__(parent)
        self._scale = scale
        self._decimals = decimals
        self._lo = self._hi = None
        self._value = None
        self._state = "idle"
        self.setMinimumHeight(round(54 * scale))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_range(self, lo, hi):
        self._lo, self._hi = lo, hi
        self.update()

    def set_value(self, value, state="stable"):
        self._value, self._state = value, state
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._scale
        w, h = self.width(), self.height()
        track_y, track_h = round(8 * s), round(16 * s)
        pad = round(12 * s)
        track = QRectF(pad, track_y, w - 2 * pad, track_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["panel3"]))
        p.drawRoundedRect(track, track_h / 2, track_h / 2)
        if self._lo is None or self._hi is None or self._hi <= self._lo:
            return
        span = self._hi - self._lo
        vmin, vmax = self._lo - span * 0.5, self._hi + span * 0.5

        def x_of(v):
            v = min(max(v, vmin), vmax)
            return track.left() + (v - vmin) / (vmax - vmin) * track.width()

        band = QRectF(QPointF(x_of(self._lo), track.top()), QPointF(x_of(self._hi), track.bottom()))
        p.setBrush(QColor(C["pass_edge"]))
        p.drawRect(band)

        f = QFont(MONO)
        f.setPixelSize(round(14 * s))
        p.setFont(f)
        p.setPen(QColor(C["dim"]))
        label_y = track.bottom() + round(20 * s)
        for v, align in ((self._lo, Qt.AlignmentFlag.AlignRight), (self._hi, Qt.AlignmentFlag.AlignLeft)):
            x = x_of(v)
            text = f"{v:.{self._decimals}f}"
            tw = QFontMetrics(f).horizontalAdvance(text)
            tx = x - tw - round(4 * s) if align == Qt.AlignmentFlag.AlignRight else x + round(4 * s)
            p.drawText(QPointF(tx, label_y), text)

        if self._value is None or self._state in ("idle", "standby"):
            return
        colour = {"pass": C["pass"], "fail": C["fail"], "settling": C["warn"]}.get(
            self._state, C["ink"])
        if self._state == "stable":
            colour = C["pass"] if self._lo <= self._value <= self._hi else C["fail"]
        x = x_of(self._value)
        tri = round(10 * s)
        p.setPen(QPen(QColor(C["bg"]), max(1, round(2 * s))))
        p.setBrush(QColor(colour))
        p.drawPolygon(QPolygonF([QPointF(x, track.bottom() + 1),
                                 QPointF(x - tri, track.bottom() + tri + 4),
                                 QPointF(x + tri, track.bottom() + tri + 4)]))
        p.drawRect(QRectF(x - 2 * s, track.top() - 3 * s, 4 * s, track_h + 6 * s))


class PointCard(QPushButton):
    """Tappable measurement-point card: name, range, and this cycle's result."""

    def __init__(self, name, range_text, seq, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setProperty("active", False)
        self.setCheckable(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 12, 8)
        lay.setSpacing(2)
        top = QHBoxLayout()
        self.name_label = QLabel(name)
        self.name_label.setProperty("role", "card_name")
        self.mark_label = QLabel(f"seq {seq}")
        self.mark_label.setProperty("role", "card_mark")
        self.mark_label.setProperty("result", "")
        self.mark_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.name_label, 1)
        top.addWidget(self.mark_label)
        lay.addLayout(top)
        self.range_label = QLabel(range_text)
        self.range_label.setProperty("role", "card_range")
        lay.addWidget(self.range_label)
        self._seq = seq
        for lbl in (self.name_label, self.mark_label, self.range_label):
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_active(self, active):
        self.setChecked(active)
        set_prop(self, "active", bool(active))

    def set_result(self, result, value_text=""):
        """result: True / False / None (not yet measured this cycle)."""
        if result is None:
            self.mark_label.setText(f"seq {self._seq}")
            set_prop(self.mark_label, "result", "")
        else:
            self.mark_label.setText(("✓ " if result else "✗ ") + value_text)
            set_prop(self.mark_label, "result", "pass" if result else "fail")


class Ui_Dialog(object):
    def __init__(self, scale=1.0):
        self.scale = scale

    def px(self, v):
        return max(1, round(v * self.scale))

    def _chip(self, parent, caption, obj_name):
        frame = _panel(parent)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(self.px(14), self.px(8), self.px(14), self.px(8))
        lay.setSpacing(self.px(2))
        lay.addWidget(_caption(caption))
        value = QLabel("---", frame)
        value.setObjectName(obj_name)
        value.setProperty("role", "chip")
        value.setProperty("state", "off")
        lay.addWidget(value)
        return frame, value

    def _limit_tile(self, parent, caption):
        btn = QPushButton(parent)
        btn.setProperty("role", "limit")
        btn.setProperty("editable", False)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(btn)
        lay.setContentsMargins(self.px(16), self.px(10), self.px(16), self.px(10))
        lay.setSpacing(self.px(2))
        head = QHBoxLayout()
        cap = _caption(caption)
        tag = QLabel("")
        tag.setProperty("role", "tag")
        head.addWidget(cap)
        head.addStretch(1)
        head.addWidget(tag)
        lay.addLayout(head)
        value = QLabel("0.00")
        value.setProperty("role", "limit_value")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(value, 1)
        for w in (cap, tag, value):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return btn, tag, value

    def setupUi(self, Dialog):
        if not Dialog.objectName():
            Dialog.setObjectName(u"MainWindow")
        px = self.px
        gap = px(12)
        root = QVBoxLayout(Dialog)
        root.setContentsMargins(px(14), px(14), px(14), px(14))
        root.setSpacing(gap)
        self.rootLayout = root

        # ── Top bar: status chips · model · clock ────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(gap)
        self.frame_usb, self.label_usb_status = self._chip(Dialog, "USB · METER", "label_usb_status")
        self.frame_wifi, self.label_wifi = self._chip(Dialog, "WI-FI · UPLINK", "label_wifi")
        self.frame_db, self.label_db = self._chip(Dialog, "DATABASE", "label_db")
        for f, w in ((self.frame_usb, 190), (self.frame_wifi, 180), (self.frame_db, 200)):
            f.setFixedWidth(px(w))
            top.addWidget(f)

        self.pushButton_model = QPushButton(Dialog)
        self.pushButton_model.setObjectName(u"pushButton_model")
        self.pushButton_model.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        model_lay = QHBoxLayout(self.pushButton_model)
        model_lay.setContentsMargins(px(18), px(4), px(18), px(4))
        model_lay.setSpacing(px(14))
        model_cap = QVBoxLayout()
        model_cap.addWidget(_caption("MODEL"))
        self.label_model_hint = _caption("tap or scan ▸")
        model_cap.addWidget(self.label_model_hint)
        model_lay.addLayout(model_cap)
        self.label_model_name = FitLabel("Model", max_px=px(40), min_px=px(14))
        self.label_model_name.setObjectName(u"label_model_name")
        self.label_model_name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        model_lay.addWidget(self.label_model_name, 1)
        for w in self.pushButton_model.findChildren(QWidget):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top.addWidget(self.pushButton_model, 1)

        self.frame_clock = _panel(Dialog)
        clock_lay = QVBoxLayout(self.frame_clock)
        clock_lay.setContentsMargins(px(14), px(4), px(14), px(4))
        self.label_clock = QLabel("--:--")
        self.label_clock.setObjectName(u"label_clock")
        self.label_clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clock_lay.addWidget(self.label_clock)
        self.frame_clock.setFixedWidth(px(120))
        top.addWidget(self.frame_clock)

        top_w = QWidget(Dialog)
        top_w.setLayout(top)
        top.setContentsMargins(0, 0, 0, 0)
        top_w.setFixedHeight(px(80))
        root.addWidget(top_w)

        # ── Body: measurement column | data log ──────────────────────────────
        body = QHBoxLayout()
        body.setSpacing(gap)
        root.addLayout(body, 1)
        left = QVBoxLayout()
        left.setSpacing(gap)
        body.addLayout(left, 1)
        self.verticalLayout = left

        # Measured panel + limit tiles
        meas_row = QHBoxLayout()
        meas_row.setSpacing(gap)
        left.addLayout(meas_row, 1)

        self.frame_measure = _panel(Dialog)
        m = QVBoxLayout(self.frame_measure)
        m.setContentsMargins(px(20), px(12), px(20), px(10))
        m.setSpacing(px(2))
        head = QHBoxLayout()
        self.label_probe_caption = _caption("PROBE")
        head.addWidget(self.label_probe_caption, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addSpacing(px(8))
        self.label_probe_point = QLabel("—")
        self.label_probe_point.setObjectName(u"label_probe_point")
        head.addWidget(self.label_probe_point)
        head.addStretch(1)
        self.label_point_index = QLabel("")
        self.label_point_index.setObjectName(u"label_point_index")
        head.addWidget(self.label_point_index)
        m.addLayout(head)

        value_row = QHBoxLayout()
        value_row.setSpacing(px(8))
        self.label_measure_value = FitLabel("-.---", max_px=px(150), min_px=px(40))
        self.label_measure_value.setObjectName(u"label_measure_value")
        self.label_measure_value.setProperty("state", "idle")
        self.label_measure_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_row.addWidget(self.label_measure_value, 1)
        self.label_unit = QLabel("Ω")
        self.label_unit.setObjectName(u"label_unit")
        self.label_unit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.label_unit.setContentsMargins(0, 0, 0, px(24))
        value_row.addWidget(self.label_unit)
        m.addLayout(value_row, 1)

        self.gauge = ToleranceGauge(self.scale, self.frame_measure)
        m.addWidget(self.gauge)
        self.label_measure_status = QLabel("waiting for meter…")
        self.label_measure_status.setObjectName(u"label_measure_status")
        self.label_measure_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m.addWidget(self.label_measure_status)
        meas_row.addWidget(self.frame_measure, 1)

        limits = QVBoxLayout()
        limits.setSpacing(gap)
        self.btn_upper, self.label_upper_tag, self.label_upper_value = self._limit_tile(Dialog, "UPPER LIMIT")
        self.btn_lower, self.label_lower_tag, self.label_lower_value = self._limit_tile(Dialog, "LOWER LIMIT")
        limits.addWidget(self.btn_upper)
        limits.addWidget(self.btn_lower)
        limits_w = QWidget(Dialog)
        limits_w.setLayout(limits)
        limits.setContentsMargins(0, 0, 0, 0)
        limits_w.setFixedWidth(px(240))
        meas_row.addWidget(limits_w)

        # Point cards (horizontally scrollable when a model has many points)
        self.point_cards_area = QScrollArea(Dialog)
        self.point_cards_area.setObjectName(u"point_cards_area")
        self.point_cards_area.setWidgetResizable(True)
        self.point_cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.point_cards_area.setFixedHeight(px(100))
        host = QWidget()
        host.setObjectName(u"point_cards_host")
        self.point_cards_row = QHBoxLayout(host)
        self.point_cards_row.setContentsMargins(0, 0, 0, 0)
        self.point_cards_row.setSpacing(gap)
        self.point_cards_area.setWidget(host)
        self.label_single_hint = QLabel(
            "Single-range mode — no point sequence for this model.  "
            "Tap a limit to edit it, or ✎ Edit spec to add points.")
        self.label_single_hint.setObjectName(u"label_single_hint")
        self.label_single_hint.setWordWrap(True)
        self.label_single_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.point_cards_area)

        # Verdict banner
        self.frame_verdict = QFrame(Dialog)
        self.frame_verdict.setObjectName(u"frame_verdict")
        self.frame_verdict.setProperty("state", "idle")
        self.frame_verdict.setFixedHeight(px(132))
        v = QVBoxLayout(self.frame_verdict)
        v.setContentsMargins(px(16), px(4), px(16), px(8))
        v.setSpacing(0)
        self.label_verdict = QLabel("— — —")
        self.label_verdict.setObjectName(u"label_verdict")
        self.label_verdict.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.label_verdict, 1)
        self.label_verdict_detail = QLabel("no reading yet")
        self.label_verdict_detail.setObjectName(u"label_verdict_detail")
        self.label_verdict_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.label_verdict_detail)
        left.addWidget(self.frame_verdict)

        # Tool buttons
        self.tools_row = QHBoxLayout()
        self.tools_row.setSpacing(gap)
        self.btn_reset_point = QPushButton("⟲  First point")
        self.btn_new_model = QPushButton("＋  New model")
        self.btn_edit_spec = QPushButton("✎  Edit spec")
        self.btn_upload_now = QPushButton("⬆  Upload now")
        self.btn_hold_toggle = QPushButton("Hold reading")
        self.btn_hold_toggle.setObjectName(u"btn_hold_toggle")
        self.btn_hold_toggle.setCheckable(True)
        for b in (self.btn_reset_point, self.btn_new_model, self.btn_edit_spec,
                  self.btn_upload_now, self.btn_hold_toggle):
            b.setProperty("role", "tool")
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.tools_row.addWidget(b)
        left.addLayout(self.tools_row)

        # ── Data log column ──────────────────────────────────────────────────
        self.frame_log = _panel(Dialog)
        self.frame_log.setFixedWidth(px(360))
        lg = QVBoxLayout(self.frame_log)
        lg.setContentsMargins(px(14), px(12), px(8), px(10))
        lg.setSpacing(px(8))
        counts = QHBoxLayout()
        counts.setSpacing(px(10))
        self.label_count_ok, self.label_count_ng, self.label_count_yield = (QLabel("0"), QLabel("0"), QLabel("—"))
        for cap, lbl, state in (("OK", self.label_count_ok, "ok"), ("NG", self.label_count_ng, "bad"),
                                ("YIELD", self.label_count_yield, "off")):
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(_caption(cap))
            lbl.setProperty("role", "count")
            lbl.setProperty("state", state)
            col.addWidget(lbl)
            counts.addLayout(col, 1)
        lg.addLayout(counts)
        self.label_counts_scope = _caption("TODAY · THIS MODEL")
        lg.addWidget(self.label_counts_scope)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{C['edge']};")
        lg.addWidget(sep)
        log_head = QHBoxLayout()
        log_head.addWidget(_caption("DATA LOG"))
        log_head.addStretch(1)
        self.label_log_count = _caption("0 READINGS")
        log_head.addWidget(self.label_log_count)
        lg.addLayout(log_head)
        self.textBrowser_log = QTextBrowser(self.frame_log)
        self.textBrowser_log.setObjectName(u"textBrowser_log")
        self.textBrowser_log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.textBrowser_log.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.textBrowser_log.document().setMaximumBlockCount(500)
        lg.addWidget(self.textBrowser_log, 1)
        body.addWidget(self.frame_log)

        self.retranslateUi(Dialog)
        QMetaObject.connectSlotsByName(Dialog)
    # setupUi

    def retranslateUi(self, Dialog):
        Dialog.setWindowTitle(QCoreApplication.translate("Dialog", u"HIOKI Data Logger", None))
    # retranslateUi
