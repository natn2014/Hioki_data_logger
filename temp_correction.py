# coding: UTF-8
"""Temperature Correction (TC) — the HIOKI meter's TC function done in the app.

Converts a resistance measured at the ambient temperature t into its value at
a standard temperature t0, using the temperature coefficient alpha at t0:

    Rt0 = Rt / (1 + alpha_t0 * (t - t0))        alpha in ppm/°C (x 1e-6)

    Rt   measured resistance (HIOKI FETC?)        t   ambient temp (RS485 sensor)
    Rt0  corrected resistance                     t0  standard temp, -10.0..99.9 °C
    alpha_t0  -9999..9999 ppm/°C (3930 = copper)

TempCorrectionTab is the touch tab that edits the per-model settings, shows
the live conversion, and charts the actual point (t, Rt) against the standard
point (t0, Rt0).
"""
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QSizePolicy,
)

from numpad_dialog import NumpadDialog

try:  # Chart is optional: the tab still works (numbers only) without matplotlib.
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    _HAS_MPL = True
except Exception:  # pragma: no cover - depends on the install
    _HAS_MPL = False

TC_DEFAULT_T0 = 20.0
TC_DEFAULT_ALPHA_PPM = 3930
TEMP_STALE_S = 10.0          # a temperature older than this is not trusted
T0_MIN, T0_MAX = -10.0, 99.9
ALPHA_MIN, ALPHA_MAX = -9999, 9999
# Chart axes are FIXED (only the points/line move per measurement): temperature
# 0-50 °C, resistance from the model's spec. They stretch only to keep an
# out-of-range value visible.
TEMP_AXIS = (0.0, 50.0)

# Chart colours: categorical slots 1 and 2 of the validated dataviz palette on
# the light surface (CVD dE 24.7, contrast >= 3:1). Identity is also carried by
# marker shape and direct labels, never colour alone.
COLOR_STANDARD = "#2a78d6"   # blue   — (t0, Rt0)
COLOR_ACTUAL = "#eb6834"     # orange — (t, Rt)
COLOR_LINE = "#8a8984"       # neutral characteristic line R(T)
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
# Histogram: one colour per seq, in this fixed order (seq 1, 2, ...). Blue and
# orange are taken by the scatter (Standard/Actual), and red/green read as
# fail/pass on a QC screen, so seqs use the other validated categorical slots.
# Ordered so no adjacent pair falls below the CVD floor; seq 6+ folds to grey.
SEQ_COLORS = ["#1baf7a", "#4a3aa7", "#eda100", "#e87ba4", "#008300"]
SEQ_OTHER = "#8a8984"
HIST_BINS = 30


def seq_color(seq):
    """Colour for a seq number (1-based); None (single range) uses slot 1."""
    if seq is None:
        return SEQ_COLORS[0]
    return SEQ_COLORS[seq - 1] if 1 <= seq <= len(SEQ_COLORS) else SEQ_OTHER


def correct_resistance(rt, t, t0, alpha_ppm):
    """Return Rt0 = Rt / (1 + alpha*(t - t0)), or None when it can't be computed."""
    try:
        rt = float(rt)
        t = float(t)
        t0 = float(t0)
        alpha = float(alpha_ppm) * 1e-6
    except (TypeError, ValueError):
        return None
    denom = 1.0 + alpha * (t - t0)
    if denom <= 0:
        return None
    return rt / denom


def spec_axis_range(lower, upper):
    """Fixed resistance-axis range for a spec band: the band plus padding."""
    lo, hi = float(min(lower, upper)), float(max(lower, upper))
    pad = max((hi - lo) * 0.25, hi * 0.05, 1e-3)
    return max(0.0, lo - pad), hi + pad


def _cover(rng, values):
    """Return rng, stretched (with a little margin) only if a value is outside."""
    lo, hi = min([rng[0]] + list(values)), max([rng[1]] + list(values))
    out = lo < rng[0] or hi > rng[1]
    margin = (hi - lo) * 0.05 if out else 0.0
    return (lo - margin if lo < rng[0] else lo, hi + margin if hi > rng[1] else hi)


def default_tc_settings():
    return {"enabled": False, "t0": TC_DEFAULT_T0, "alpha_ppm": TC_DEFAULT_ALPHA_PPM}


def normalize_tc_settings(raw):
    """Coerce a stored/edited settings dict into range; bad values fall back to defaults."""
    tc = default_tc_settings()
    if not isinstance(raw, dict):
        return tc
    tc["enabled"] = bool(raw.get("enabled", False))
    try:
        tc["t0"] = round(min(max(float(raw.get("t0", TC_DEFAULT_T0)), T0_MIN), T0_MAX), 1)
    except (TypeError, ValueError):
        pass
    try:
        tc["alpha_ppm"] = int(round(min(max(float(raw.get("alpha_ppm", TC_DEFAULT_ALPHA_PPM)),
                                            ALPHA_MIN), ALPHA_MAX)))
    except (TypeError, ValueError):
        pass
    return tc


def _card(title, value_font_pt, accent=None):
    """A titled readout card: returns (frame, value_label)."""
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    border = accent or "#c9c8c3"
    frame.setStyleSheet(f"QFrame{{border:2px solid {border};border-radius:8px;}}")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(10, 6, 10, 6)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setStyleSheet(f"border:none;color:{TEXT_SECONDARY};")
    f = QFont()
    f.setPointSize(13)
    t.setFont(f)
    v = QLabel("—")
    v.setStyleSheet(f"border:none;color:{TEXT_PRIMARY};")
    vf = QFont()
    vf.setPointSize(value_font_pt)
    vf.setBold(True)
    v.setFont(vf)
    v.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(t)
    lay.addWidget(v)
    return frame, v


class TempCorrectionTab(QWidget):
    """Per-model TC settings + live conversion + (t, Rt) vs (t0, Rt0) chart."""

    settings_changed = Signal(bool, float, int)   # enabled, t0, alpha_ppm
    axis_changed = Signal(bool)                   # True = temperature on X

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled = False
        self._t0 = TC_DEFAULT_T0
        self._alpha = TC_DEFAULT_ALPHA_PPM
        self._rt = None
        self._t = None
        self._t_ts = 0.0
        self._sensor_status = "waiting…"
        self._x_is_temp = True
        self._r_axis = (0.0, 10.0)     # fixed resistance axis (set per model spec)
        # Histogram: today's judged values for the current model, per seq.
        self._hist = {}                # seq (int|None) -> {"name": str, "values": [float]}
        self._hist_spec = []           # [{seq, name, lower, upper}] for limit lines
        self._hist_title = ""
        self._dirty = True

        self._redraw_timer = QTimer(self)        # coalesce bursts of updates
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.timeout.connect(self._redraw)
        self._age_timer = QTimer(self)           # keep the "x s ago" text honest
        self._age_timer.timeout.connect(self._refresh_readouts)
        self._age_timer.start(1000)

        self._build()
        self._refresh_all()

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        btn_font = QFont()
        btn_font.setPointSize(15)
        btn_font.setBold(True)

        # Row 1: model + switch + the two inputs + reset
        row = QHBoxLayout()
        self.model_label = QLabel("Model: —")
        mf = QFont()
        mf.setPointSize(14)
        mf.setBold(True)
        self.model_label.setFont(mf)
        row.addWidget(self.model_label, 2)

        self.btn_enable = QPushButton()
        self.btn_enable.setCheckable(True)
        self.btn_enable.setFont(btn_font)
        self.btn_enable.setMinimumHeight(56)
        self.btn_enable.toggled.connect(self._on_enable_toggled)
        row.addWidget(self.btn_enable, 2)

        self.btn_t0 = QPushButton()
        self.btn_alpha = QPushButton()
        for b in (self.btn_t0, self.btn_alpha):
            b.setFont(btn_font)
            b.setMinimumHeight(56)
            b.setStyleSheet(
                "QPushButton{background:#ffffff;color:#0b0b0b;border:2px solid #2a78d6;"
                "border-radius:8px;padding:4px 10px;}"
                "QPushButton:pressed{background:#eaf2fc;}")
        self.btn_t0.clicked.connect(self._edit_t0)
        self.btn_alpha.clicked.connect(self._edit_alpha)
        row.addWidget(self.btn_t0, 2)
        row.addWidget(self.btn_alpha, 3)

        self.btn_reset = QPushButton("⟲ Defaults")
        self.btn_reset.setFont(btn_font)
        self.btn_reset.setMinimumHeight(56)
        self.btn_reset.clicked.connect(self._reset_defaults)
        row.addWidget(self.btn_reset, 1)
        root.addLayout(row)

        # Row 2: readouts
        cards = QHBoxLayout()
        f1, self.lbl_rt = _card("Measured  Rt  (Ω)", 26)
        f2, self.lbl_t = _card("Ambient  t  (°C)", 26)
        f3, self.lbl_rt0 = _card("Corrected  Rt₀  (Ω)", 32, accent=COLOR_STANDARD)
        self.lbl_sensor = QLabel("")
        self.lbl_sensor.setStyleSheet(f"border:none;color:{TEXT_SECONDARY};")
        self.lbl_sensor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f2.layout().addWidget(self.lbl_sensor)
        cards.addWidget(f1, 1)
        cards.addWidget(f2, 1)
        cards.addWidget(f3, 1)
        root.addLayout(cards)

        # Row 3: the worked formula, so operators see how Rt0 is produced
        self.lbl_formula = QLabel("")
        ff = QFont("Consolas")
        ff.setStyleHint(QFont.StyleHint.Monospace)
        ff.setPointSize(13)
        self.lbl_formula.setFont(ff)
        self.lbl_formula.setWordWrap(True)
        self.lbl_formula.setStyleSheet(
            "background:#f3f2ef;border-radius:6px;padding:6px 10px;color:#0b0b0b;")
        root.addWidget(self.lbl_formula)

        # Row 4: axis selector + chart
        axis_row = QHBoxLayout()
        axis_lbl = QLabel("Scatter axes:")
        axis_lbl.setFont(btn_font)
        self.btn_axis = QPushButton()
        self.btn_axis.setFont(btn_font)
        self.btn_axis.setMinimumHeight(48)
        self.btn_axis.clicked.connect(self._toggle_axis)
        axis_row.addWidget(axis_lbl)
        axis_row.addWidget(self.btn_axis, 1)
        root.addLayout(axis_row)

        if _HAS_MPL:
            self.figure = Figure(figsize=(9, 3.4), facecolor=SURFACE)
            self.figure.subplots_adjust(left=0.075, right=0.985, top=0.88, bottom=0.17,
                                        wspace=0.28)
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Expanding)
            self.ax = self.figure.add_subplot(1, 2, 1)     # (t, Rt) vs (t0, Rt0)
            self.hax = self.figure.add_subplot(1, 2, 2)    # per-seq histogram
            root.addWidget(self.canvas, 1)
        else:
            self.figure = self.canvas = self.ax = self.hax = None
            na = QLabel("Chart unavailable — matplotlib is not installed.")
            na.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(na, 1)

    # ── public API (called by MainWindow) ─────────────────────────────────────

    def set_model(self, name):
        self.model_label.setText(f"Model: {name}" if name else "Model: — (set a model first)")

    def load_settings(self, enabled, t0, alpha_ppm):
        """Apply stored settings without emitting settings_changed."""
        self._enabled = bool(enabled)
        self._t0 = float(t0)
        self._alpha = int(alpha_ppm)
        self.btn_enable.blockSignals(True)
        self.btn_enable.setChecked(self._enabled)
        self.btn_enable.blockSignals(False)
        self._refresh_all()

    def settings(self):
        return {"enabled": self._enabled, "t0": self._t0, "alpha_ppm": self._alpha}

    def set_rt(self, rt):
        rt = None if rt is None else float(rt)
        if rt == self._rt:
            return
        self._rt = rt
        self._refresh_all()

    def set_temperature(self, t, ts=None):
        self._t = float(t)
        self._t_ts = ts if ts is not None else time.time()
        self._refresh_all()

    def set_sensor_status(self, text):
        self._sensor_status = text
        self._refresh_readouts()

    def set_spec_range(self, lower, upper):
        """Fix the resistance axis to the model's spec band (lowest lower .. highest upper)."""
        rng = spec_axis_range(lower, upper)
        if rng != self._r_axis:
            self._r_axis = rng
            self._refresh_all()

    def set_hist_spec(self, points, title=""):
        """Seq names + limits for the histogram ([{seq, name, lower, upper}])."""
        if list(points) == self._hist_spec and title == self._hist_title:
            return      # called on every point advance — skip needless redraws
        self._hist_spec = list(points)
        self._hist_title = title
        self._refresh_all()

    def set_history(self, rows):
        """Replace the histogram data with [(seq, name, value), ...]."""
        self._hist = {}
        for seq, name, value in rows:
            self._add(seq, name, value)
        self._refresh_all()

    def add_reading(self, seq, name, value):
        """Add one recorded (judged) value to the histogram."""
        if self._add(seq, name, value):
            self._refresh_all()

    def _add(self, seq, name, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return False
        entry = self._hist.setdefault(seq, {"name": name or "", "values": []})
        if name:
            entry["name"] = name
        entry["values"].append(value)
        return True

    def hist_counts(self):
        """{seq: n} — handy for tests and status text."""
        return {k: len(v["values"]) for k, v in self._hist.items()}

    def set_axis_temperature_on_x(self, x_is_temp):
        self._x_is_temp = bool(x_is_temp)
        self._refresh_all()

    def temperature_is_fresh(self):
        return self._t is not None and (time.time() - self._t_ts) <= TEMP_STALE_S

    # ── input handlers ────────────────────────────────────────────────────────

    def _on_enable_toggled(self, checked):
        self._enabled = checked
        self._refresh_all()
        self._emit_settings()

    def _numpad(self, title, value, decimals, lo, hi):
        dlg = NumpadDialog(current_value=value, decimals=decimals, title=title,
                           min_val=lo, max_val=hi, parent=self, allow_negative=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.get_value()
        return None

    def _edit_t0(self):
        v = self._numpad("Standard temperature t₀ (°C)", self._t0, 1, T0_MIN, T0_MAX)
        if v is not None:
            self._t0 = round(float(v), 1)
            self._refresh_all()
            self._emit_settings()

    def _edit_alpha(self):
        v = self._numpad("Temperature coefficient α (ppm/°C)", float(self._alpha), 0,
                         ALPHA_MIN, ALPHA_MAX)
        if v is not None:
            self._alpha = int(round(v))
            self._refresh_all()
            self._emit_settings()

    def _reset_defaults(self):
        self._t0 = TC_DEFAULT_T0
        self._alpha = TC_DEFAULT_ALPHA_PPM
        self._refresh_all()
        self._emit_settings()

    def _toggle_axis(self):
        self._x_is_temp = not self._x_is_temp
        self._refresh_all()
        self.axis_changed.emit(self._x_is_temp)

    def _emit_settings(self):
        self.settings_changed.emit(self._enabled, float(self._t0), int(self._alpha))

    # ── rendering ─────────────────────────────────────────────────────────────

    def _rt0(self):
        if self._rt is None or self._t is None:
            return None
        return correct_resistance(self._rt, self._t, self._t0, self._alpha)

    def _refresh_all(self):
        self._refresh_controls()
        self._refresh_readouts()
        self._dirty = True
        if self.isVisible():
            self._redraw_timer.start(150)

    def _refresh_controls(self):
        if self._enabled:
            self.btn_enable.setText("TC: ON — judging on Rt₀")
            self.btn_enable.setStyleSheet(
                "QPushButton{background:#1baf7a;color:white;border-radius:8px;}")
        else:
            self.btn_enable.setText("TC: OFF — judging on Rt")
            self.btn_enable.setStyleSheet(
                "QPushButton{background:#6b6a66;color:white;border-radius:8px;}")
        self.btn_t0.setText(f"t₀  {self._t0:.1f} °C")
        self.btn_alpha.setText(f"α  {self._alpha} ppm/°C")
        self.btn_axis.setText("X: Temperature  ·  Y: Resistance   (tap to swap)"
                              if self._x_is_temp else
                              "X: Resistance  ·  Y: Temperature   (tap to swap)")

    def _refresh_readouts(self):
        self.lbl_rt.setText("—" if self._rt is None else f"{self._rt:.3f}")
        if self._t is None:
            self.lbl_t.setText("—")
            age_txt = ""
        else:
            self.lbl_t.setText(f"{self._t:.1f}")
            age = max(0, int(time.time() - self._t_ts))
            age_txt = f" · {age} s ago" + ("  ⚠ stale" if age > TEMP_STALE_S else "")
        self.lbl_sensor.setText(f"Sensor: {self._sensor_status}{age_txt}")

        rt0 = self._rt0()
        a = self._alpha * 1e-6
        if self._rt is None or self._t is None:
            self.lbl_rt0.setText("—")
            self.lbl_formula.setText(
                "Rt₀ = Rt / (1 + α·(t − t₀))   — waiting for resistance and temperature…")
        elif rt0 is None:
            self.lbl_rt0.setText("invalid")
            self.lbl_formula.setText(
                f"1 + α·(t − t₀) = 1 + {a:.6f}×({self._t:.1f} − {self._t0:.1f}) ≤ 0  "
                "→ cannot correct; check α and t₀")
        else:
            self.lbl_rt0.setText(f"{rt0:.3f}")
            self.lbl_formula.setText(
                f"Rt₀ = Rt / (1 + α·(t − t₀)) = {self._rt:.3f} / "
                f"(1 + {a:.6f} × ({self._t:.1f} − {self._t0:.1f})) = {rt0:.3f} Ω")

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self._redraw_timer.start(0)

    def _redraw(self):
        if self.ax is None or not self.isVisible():
            return
        self._dirty = False
        self._draw_scatter()
        self._draw_hist()
        self.canvas.draw_idle()

    @staticmethod
    def _style(ax):
        ax.set_facecolor(SURFACE)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
        ax.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)

    def _draw_scatter(self):
        ax = self.ax
        ax.clear()
        self._style(ax)

        t_lbl, r_lbl = "Temperature (°C)", "Resistance (Ω)"
        ax.set_xlabel(t_lbl if self._x_is_temp else r_lbl, color=TEXT_SECONDARY, fontsize=10)
        ax.set_ylabel(r_lbl if self._x_is_temp else t_lbl, color=TEXT_SECONDARY, fontsize=10)

        rt0 = self._rt0()
        if rt0 is None:
            msg = ("Waiting for resistance and temperature…"
                   if self._rt is None or self._t is None
                   else "Cannot correct: 1 + α·(t − t₀) ≤ 0")
            ax.text(0.5, 0.5, msg, transform=ax.transAxes, ha="center", va="center",
                    color=TEXT_SECONDARY, fontsize=10, wrap=True)
            ax.set_xticks([])
            ax.set_yticks([])
            return

        a = self._alpha * 1e-6
        t_dom = _cover(TEMP_AXIS, (self._t, self._t0))
        r_dom = _cover(self._r_axis, (self._rt, rt0))
        temps = list(t_dom)                       # line spans the whole temp axis
        res = [rt0 * (1 + a * (T - self._t0)) for T in temps]

        def xy(temp, r):
            return (temp, r) if self._x_is_temp else (r, temp)

        lx, ly = zip(*(xy(T, R) for T, R in zip(temps, res)))
        ax.plot(lx, ly, linestyle="--", linewidth=1.5, color=COLOR_LINE,
                label=f"R(T) at α = {self._alpha} ppm/°C", zorder=1)

        ax_, ay_ = xy(self._t, self._rt)
        sx_, sy_ = xy(self._t0, rt0)
        ax.plot([ax_], [ay_], marker="o", markersize=12, linestyle="none",
                color=COLOR_ACTUAL, markeredgecolor=SURFACE, markeredgewidth=2,
                label="Actual  (t, Rt)", zorder=3)
        ax.plot([sx_], [sy_], marker="D", markersize=11, linestyle="none",
                color=COLOR_STANDARD, markeredgecolor=SURFACE, markeredgewidth=2,
                label="Standard  (t₀, Rt₀)", zorder=3)

        # Direct labels go in the quadrant the line does NOT pass through:
        # rising line -> upper point labelled up-left, lower point down-right.
        x_dom = t_dom if self._x_is_temp else r_dom
        actual_upper = ay_ >= sy_
        ux, lx = (ax_, sx_) if actual_upper else (sx_, ax_)
        rising = ux >= lx
        for (px, py, text, upper) in (
                (ax_, ay_, f"Actual\n{self._t:.1f} °C · {self._rt:.3f} Ω", actual_upper),
                (sx_, sy_, f"Standard\n{self._t0:.1f} °C · {rt0:.3f} Ω", not actual_upper)):
            hs = (-1 if rising else 1) if upper else (1 if rising else -1)
            frac = (px - x_dom[0]) / ((x_dom[1] - x_dom[0]) or 1.0)
            if hs < 0 and frac < 0.25:
                hs = 1          # too close to the left edge — flip right
            elif hs > 0 and frac > 0.75:
                hs = -1         # too close to the right edge — flip left
            ax.annotate(text, (px, py), xytext=(hs * 10, 10 if upper else -10),
                        textcoords="offset points", ha="right" if hs < 0 else "left",
                        va="bottom" if upper else "top",
                        fontsize=9, color=TEXT_PRIMARY)

        ax.ticklabel_format(useOffset=False, style="plain")
        # Fixed axes; the R(T) line is clipped to them automatically.
        ax.set_xlim(*(t_dom if self._x_is_temp else r_dom))
        ax.set_ylim(*(r_dom if self._x_is_temp else t_dom))
        leg = ax.legend(loc="best", frameon=False, fontsize=8)
        for txt in leg.get_texts():
            txt.set_color(TEXT_PRIMARY)
        ax.set_title("Temperature correction", loc="left", fontsize=10, color=TEXT_SECONDARY)

    def _draw_hist(self):
        """Stacked histogram of today's judged values, one colour per seq.

        Shares the fixed resistance axis with the scatter; dashed lines mark each
        seq's lower/upper limits. Legend carries name, n and mean (visible labels,
        since several seq colours sit below 3:1 contrast on the light surface)."""
        from matplotlib.ticker import MaxNLocator
        ax = self.hax
        ax.clear()
        self._style(ax)
        ax.set_xlabel("Resistance (Ω) — judged value", color=TEXT_SECONDARY, fontsize=10)
        ax.set_ylabel("Count", color=TEXT_SECONDARY, fontsize=10)
        total = sum(len(v["values"]) for v in self._hist.values())
        ax.set_title(f"{self._hist_title}  ·  n = {total}" if self._hist_title else f"n = {total}",
                     loc="left", fontsize=10, color=TEXT_SECONDARY)

        all_vals = [x for v in self._hist.values() for x in v["values"]]
        lo, hi = _cover(self._r_axis, all_vals) if all_vals else self._r_axis
        ax.set_xlim(lo, hi)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.ticklabel_format(axis="x", useOffset=False, style="plain")

        # A model with a sequence: readings without a seq (recorded before the
        # sequence existed) are grey, never mistaken for seq 1.
        has_seq = (any(k is not None for k in self._hist)
                   or any(sp.get("seq") is not None for sp in self._hist_spec))

        def colour(seq):
            if seq is None:
                return SEQ_OTHER if has_seq else SEQ_COLORS[0]
            return seq_color(seq)

        spec_names = {sp.get("seq"): sp.get("name") for sp in self._hist_spec}

        # Spec limits per seq (thin dashed, in the seq colour).
        for sp in self._hist_spec:
            c = colour(sp.get("seq"))
            for v in (sp["lower"], sp["upper"]):
                ax.axvline(v, color=c, linestyle="--", linewidth=1, alpha=0.75, zorder=1)

        if not all_vals:
            ax.text(0.5, 0.5, "No readings today for this model yet",
                    transform=ax.transAxes, ha="center", va="center",
                    color=TEXT_SECONDARY, fontsize=10)
            ax.set_ylim(0, 1)
            return

        width = (hi - lo) / HIST_BINS
        edges = [lo + i * width for i in range(HIST_BINS)]
        bottom = [0] * HIST_BINS
        top = 0
        # Seq order is the entity order: seq 1, 2, ... then None / unknown.
        order = sorted(self._hist, key=lambda k: (k is None, k if k is not None else 0))
        for seq in order:
            vals = self._hist[seq]["values"]
            counts = [0] * HIST_BINS
            for x in vals:
                i = int((x - lo) / width) if width > 0 else 0
                counts[min(max(i, 0), HIST_BINS - 1)] += 1
            name = (spec_names.get(seq) if seq is not None else None) or self._hist[seq]["name"]
            if seq is None:
                label = "no seq (single-range)" if has_seq else (name or "All readings")
            else:
                label = f"{name or 'seq ' + str(seq)} (seq {seq})"
            mean = sum(vals) / len(vals)
            ax.bar(edges, counts, width=width, bottom=bottom, align="edge",
                   color=colour(seq), edgecolor=SURFACE, linewidth=1.5,   # 2px-ish gap
                   label=f"{label} · n={len(vals)} · x̄ {mean:.3f}", zorder=2)
            bottom = [b + c for b, c in zip(bottom, counts)]
            top = max(top, max(bottom))
        ax.set_ylim(0, top * 1.45 + 1)          # headroom for the legend
        leg = ax.legend(loc="upper right", fontsize=8, frameon=True, framealpha=0.9,
                        edgecolor=GRID)
        for txt in leg.get_texts():
            txt.set_color(TEXT_PRIMARY)
