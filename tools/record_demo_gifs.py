"""Record demo GIFs of the app's functions for the README / user guide.

Drives the real app (main.MainWindow) on the kiosk display with scripted taps,
grabs the whole screen (so pop-up keyboards, numpads and dialogs are included),
draws a finger marker where it taps, adds a caption strip under each frame, and
writes docs/gifs/<scene>.gif.

Nothing touches production data or hardware:
  * the app runs from a scratch copy of the project (config, daily CSV, model
    log and upload queues all land there and are deleted afterwards);
  * the MSSQL module is replaced by an in-memory fake (no DB reads/writes);
  * the HIOKI meter and the RS485 sensor are simulated (no serial port opened),
    so the kiosk service can keep running while recording.

Usage:
    DISPLAY=:0 .venv/bin/python tools/record_demo_gifs.py [scene ...]
Scenes: measure model points settings edit_spec new_model temperature
Each scene runs in its own process with a fresh sandbox.
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "gifs"
FPS = 8
SCALE = 0.75          # 1024x768 -> 768x576 (+ caption strip)
CAPTION_H = 52        # caption strip height before scaling
FINGER_MS = 450       # how long a tap marker stays visible
DEMO_TEMP = 26.0      # simulated object temperature (°C)
OL = "+9.9000E+37"    # meter over-range = probes lifted (standby)

SCENE_TIMEOUT_S = 120  # a stuck scene (unanswered dialog) is aborted, not saved
SCENES = ["measure", "model", "points", "settings", "edit_spec", "new_model", "temperature"]


# ------------------------------------------------------------ data sandbox ---
def make_sandbox() -> Path:
    """Copy the code + config into a scratch dir and run the app from there:
    main.py writes next to __file__ / into the cwd, so every write is contained."""
    tmp = Path(tempfile.mkdtemp(prefix="hioki_demo_"))
    for p in ROOT.glob("*.py"):
        shutil.copy(p, tmp / p.name)
    for name in ("gui_mode5_config.json", "ResistancePass_TH.mp3", "ResistanceOver_TH.mp3"):
        if (ROOT / name).exists():
            shutil.copy(ROOT / name, tmp / name)
    seed_history(tmp)
    return tmp


def seed_history(tmp: Path) -> None:
    """Today's CSV with plausible 500D readings, so the TC histogram has data."""
    import csv
    rnd = random.Random(7)
    now = datetime.now()
    rows = []
    for i in range(36):
        t = now - timedelta(minutes=5 * (36 - i))
        for name, seq, lo, up, mu, sd in (("A-B", 1, 2.9, 3.43, 3.16, 0.07),
                                          ("B-C", 2, 7.86, 9.31, 8.55, 0.18)):
            v = round(rnd.gauss(mu, sd), 3)
            st = "OK" if lo <= v <= up else "NG"
            rows.append([t.strftime("%Y-%m-%d %H:%M:%S"), v, st, "500D", name, seq, lo, up,
                         t.strftime("%Y-%m-%d"), t.strftime("%H:%M:%S"), "uploaded"])
    with open(tmp / f"{now:%Y%m%d}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "Resistance", "Status", "Model", "Point", "Seq",
                    "LowerLimit", "UpperLimit", "Date", "Time", "DB_Status"])
        w.writerows(rows)


def install_fake_db() -> None:
    """Replace insert_resistance2db before main/db_upload_manager import it."""
    fake = types.ModuleType("insert_resistance2db")

    def insert_to_mssql(model, value, status, point=None, seq=None,
                        lower=None, upper=None, timeout=5):
        time.sleep(0.2)
        return True

    def fetch_model_spec(model, timeout=5):
        raise ConnectionError("demo: spec served from local cache")

    def upsert_model_spec(model, points, timeout=5):
        time.sleep(0.4)
        return len(points)

    def check_schema(timeout=5):
        return {"reachable": True, "spec_table": True, "missing_columns": [], "error": None}

    fake.insert_to_mssql = insert_to_mssql
    fake.fetch_model_spec = fetch_model_spec
    fake.upsert_model_spec = upsert_model_spec
    fake.check_schema = check_schema
    sys.modules["insert_resistance2db"] = fake


# ---------------------------------------------------------------- recorder ---
class Recorder:
    def __init__(self, app) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication
        self.app = app
        self.screen = QGuiApplication.primaryScreen()
        self.frames: list = []
        self.taps: list = []              # [(QPointF global, t_end)]
        self.caption = ""
        self.timer = QTimer()
        self.timer.timeout.connect(self._grab)

    def start(self) -> None:
        self.frames = []
        self.timer.start(int(1000 / FPS))

    def stop(self) -> list:
        self.timer.stop()
        return self.frames

    def say(self, text: str, hold: float = 0.0) -> None:
        self.caption = text
        self.pump(hold)

    def _grab(self) -> None:
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
        shot = self.screen.grabWindow(0).toImage().convertToFormat(QImage.Format.Format_RGB888)
        image = QImage(shot.width(), shot.height() + CAPTION_H, QImage.Format.Format_RGB888)
        image.fill(QColor("#1f2933"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(0, 0, shot)
        now = time.monotonic()
        self.taps = [t for t in self.taps if t[1] > now]
        for p, _ in self.taps:
            painter.setPen(QPen(QColor(255, 255, 255, 230), 3))
            painter.setBrush(QColor(255, 170, 40, 120))
            painter.drawEllipse(p, 24, 24)
        if self.caption:
            font = QFont()
            font.setPixelSize(24)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRect(16, shot.height(), shot.width() - 32, CAPTION_H),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             self.caption)
        painter.end()
        image = image.scaled(int(image.width() * SCALE), int(image.height() * SCALE),
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        self.frames.append(image)

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.01)

    def _mark(self, widget, local) -> None:
        from PySide6.QtCore import QPointF
        self.taps.append((QPointF(widget.mapToGlobal(local)),
                          time.monotonic() + FINGER_MS / 1000))

    def tap(self, widget, local=None, after: float = 0.6) -> None:
        """Tap a widget. If it opens a modal dialog the click blocks until the
        dialog closes — register its script with expect_dialog() first."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        local = local if local is not None else widget.rect().center()
        self._mark(widget, local)
        self.pump(0.2)                     # marker visible just before the press
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, local)
        self.pump(after)

    def tap_tab(self, tabs, index: int, after: float = 1.0) -> None:
        bar = tabs.tabBar()
        self.tap(bar, bar.tabRect(index).center(), after)

    def expect_dialog(self, script, settle: float = 0.6) -> None:
        """Run script(dialog) once a new modal dialog is up. Polls from timers,
        which keep firing inside the dialog's own exec() loop."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        before = QApplication.activeModalWidget()

        def poll():
            d = QApplication.activeModalWidget()
            if d is not None and d is not before and d.isVisible():
                self.pump(settle)
                script(d)
                return
            QTimer.singleShot(80, poll)

        QTimer.singleShot(80, poll)


# ----------------------------------------------------------- input helpers ---
def buttons_by_text(dialog) -> dict:
    from PySide6.QtWidgets import QPushButton
    return {b.text(): b for b in dialog.findChildren(QPushButton) if b.isVisible()}


def type_on_keyboard(rec, dlg, text: str) -> None:
    """KeyboardDialog: Clear, then tap each key."""
    keys = buttons_by_text(dlg)
    rec.tap(keys["Clear"], after=0.3)
    for ch in text:
        rec.tap(keys[ch.upper()], after=0.25)
    rec.pump(0.5)
    rec.tap(keys["OK"], after=0.2)


def type_on_numpad(rec, dlg, text: str) -> None:
    keys = buttons_by_text(dlg)
    for ch in text:
        rec.tap(keys[ch], after=0.25)
    rec.pump(0.5)
    rec.tap(keys["OK"], after=0.2)


def answer_yes(rec):
    def script(box):
        from PySide6.QtWidgets import QMessageBox
        rec.tap(box.button(QMessageBox.StandardButton.Yes), after=0.2)
    return script


def keyboard_script(rec, text):
    return lambda dlg: type_on_keyboard(rec, dlg, text)


def numpad_script(rec, text):
    return lambda dlg: type_on_numpad(rec, dlg, text)


def meter(rec, w, ohms: float, gap: float = 0.35) -> None:
    """Probes on a part: the meter repeats a stable value → the app records it."""
    for _ in range(3):
        w.on_fetch_result(f"{ohms:+.4E}")
        rec.pump(gap)


def lift(rec, w, seconds: float = 1.0) -> None:
    """Probes lifted: the meter reports over-range (standby)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        w.on_fetch_result(OL)
        rec.pump(0.3)


def refocus(rec, w) -> None:
    """The spec editor is a framed window: after it closes the desktop panel can
    stay above the full-screen app until it is re-activated."""
    w.showFullScreen()
    w.raise_()
    w.activateWindow()
    w.update()
    rec.pump(0.4)


def open_settings(rec, w) -> None:
    if not w.btn_settings.isChecked():
        rec.tap(w.btn_settings, after=0.8)


def close_settings(rec, w) -> None:
    if w.btn_settings.isChecked():
        rec.tap(w.btn_settings, after=0.8)


# ------------------------------------------------------------------ scenes ---
def scene_measure(rec, w) -> None:
    rec.say("Measure: probe each point in order — the active point card is highlighted", 1.8)
    rec.say("Point A-B: probes on → stable reading is judged and recorded")
    meter(rec, w, 3.30)
    rec.say("PASS → saved to CSV + DB, and the app moves on to point B-C", 2.2)
    lift(rec, w, 1.2)
    rec.say("Point B-C: probes on")
    meter(rec, w, 8.80)
    rec.say("PASS → sequence wraps back to the first point (A-B)", 2.2)
    lift(rec, w, 1.2)
    rec.say("Point A-B again: an out-of-spec part")
    meter(rec, w, 3.72)
    rec.say("FAIL (above the upper limit) — red judgement, logged in Data Log", 2.6)
    lift(rec, w, 1.0)
    rec.say("Object Temperature (top right) is read live from the RS485 sensor", 2.4)


def scene_model(rec, w) -> None:
    rec.say("Change model: tap the Model button", 1.0)
    rec.expect_dialog(keyboard_script(rec, "TESE"))
    rec.say("Type the model name on the on-screen keyboard, then OK")
    rec.tap(w.ui.pushButton_model, after=1.0)
    rec.say("Model TESE loaded — its spec limits are applied", 2.2)
    rec.say("Or simply scan the part barcode with the USB scanner…", 1.2)
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.keyClicks(w, "P$500D$01")
    QTest.keyClick(w, Qt.Key.Key_Return)
    rec.pump(1.0)
    rec.say("Barcode decoded → model 500D with its 2 measurement points", 2.6)


def scene_points(rec, w) -> None:
    rec.say("Point cards: tap a card to jump straight to that point", 1.2)
    rec.tap(w.point_cards[1], after=1.4)
    rec.say("B-C is now active — limits and title follow the selected point", 1.6)
    rec.tap(w.point_cards[0], after=1.2)
    rec.say("Tap ⚙ → ⟲ Reset to first point to restart the sequence", 0.6)
    rec.tap(w.point_cards[1], after=0.8)
    open_settings(rec, w)
    rec.tap(w.btn_reset_point, after=1.4)
    close_settings(rec, w)
    rec.pump(0.8)


def scene_settings(rec, w) -> None:
    # Two readings waiting in the offline queue, so Upload Now has work to do.
    w.db_manager.add_pending_upload("500D", 3.18, "OK", point="A-B", seq=1, lower=2.9, upper=3.43)
    w.db_manager.add_pending_upload("500D", 8.61, "OK", point="B-C", seq=2, lower=7.86, upper=9.31)
    meter(rec, w, 3.25)
    lift(rec, w, 0.6)
    rec.say("Tap ⚙ (bottom right of the judgement) to show the tool buttons", 0.6)
    open_settings(rec, w)
    rec.say("⏸ Hold Reading: the last value stays on screen after the probes lift", 1.4)
    rec.tap(w.btn_hold_toggle, after=0.6)
    rec.say("⤓ Release to 0: the display clears as soon as the probes lift", 1.8)
    rec.tap(w.btn_hold_toggle, after=1.0)
    rec.say("⬆ Upload Now: push readings queued while the DB was offline", 0.6)
    rec.tap(w.btn_upload_now, after=2.0)
    rec.say("Tap ⚙ again to hide the buttons", 0.6)
    close_settings(rec, w)
    rec.pump(1.0)


def scene_edit_spec(rec, w) -> None:
    rec.say("Edit Spec: ⚙ → ✎ Edit Spec opens the current model's points", 0.6)
    open_settings(rec, w)

    def spec_dialog(dlg):
        row = dlg._rows[1]
        rec.say("Tap a limit to change it with the numpad")
        rec.expect_dialog(numpad_script(rec, "9.50"))
        rec.tap(row["upper"].lineEdit(), after=0.8)
        rec.say("B-C upper limit 9.31 → 9.50 Ω — then Save to DB", 1.2)
        save = buttons_by_text(dlg)["Save to DB"]
        rec.tap(save, after=0.2)

    rec.expect_dialog(spec_dialog)
    rec.tap(w.btn_edit_spec, after=0.2)
    refocus(rec, w)
    rec.pump(0.8)
    close_settings(rec, w)
    rec.say("Saved: the B-C card now shows 7.86 – 9.50 Ω", 2.4)


def scene_new_model(rec, w) -> None:
    rec.say("Register a new model: ⚙ → ＋ New Model", 0.6)
    open_settings(rec, w)

    def spec_dialog(dlg):
        rec.say("Model name: tap the field and type (or scan the barcode)")
        rec.expect_dialog(keyboard_script(rec, "750X"))
        rec.tap(dlg.model_edit, after=0.8)
        row = dlg._rows[0]
        rec.say("Point 1: name, lower and upper limit")
        rec.expect_dialog(keyboard_script(rec, "A-B"))
        rec.tap(row["name"], after=0.6)
        rec.expect_dialog(numpad_script(rec, "1.20"))
        rec.tap(row["lower"].lineEdit(), after=0.6)
        rec.expect_dialog(numpad_script(rec, "1.50"))
        rec.tap(row["upper"].lineEdit(), after=0.8)
        rec.say("＋ Add point for each further measuring point (probed in order)")
        rec.tap(dlg.add_btn, after=0.6)
        row = dlg._rows[1]
        rec.expect_dialog(keyboard_script(rec, "B-C"))
        rec.tap(row["name"], after=0.6)
        rec.expect_dialog(numpad_script(rec, "2.40"))
        rec.tap(row["lower"].lineEdit(), after=0.6)
        rec.expect_dialog(numpad_script(rec, "2.80"))
        rec.tap(row["upper"].lineEdit(), after=1.0)
        rec.say("Save to DB → answer Yes to switch to the new model", 0.6)
        rec.expect_dialog(answer_yes(rec), settle=1.4)   # "Switch to this model now?"
        rec.tap(buttons_by_text(dlg)["Save to DB"], after=0.2)

    rec.expect_dialog(spec_dialog)
    rec.tap(w.btn_new_model, after=0.2)
    refocus(rec, w)
    rec.pump(0.8)
    close_settings(rec, w)
    rec.say("Model 750X active with points A-B and B-C", 2.6)


def scene_temperature(rec, w) -> None:
    meter(rec, w, 3.30)
    lift(rec, w, 0.4)
    rec.say("Temperature Correction tab", 0.2)
    rec.tap_tab(w.ui.tabWidget, 1, after=1.6)
    rec.say("Rt is converted to the standard temperature: Rt₀ = Rt / (1 + α·(t − t₀))", 2.4)
    rec.say("Tap t₀ to set the standard temperature")
    rec.expect_dialog(numpad_script(rec, "25"))
    rec.tap(w.tc_tab.btn_t0, after=1.6)
    rec.say("Rt₀ and the chart update immediately", 1.0)
    rec.expect_dialog(numpad_script(rec, "20"))
    rec.tap(w.tc_tab.btn_t0, after=1.2)
    rec.say("TC ON/OFF: judge on corrected Rt₀ or on raw Rt", 0.4)
    rec.tap(w.tc_tab.btn_enable, after=1.4)
    rec.tap(w.tc_tab.btn_enable, after=1.2)
    rec.say("Tap to swap the scatter chart's axes", 0.3)
    rec.tap(w.tc_tab.btn_axis, after=1.6)
    rec.tap(w.tc_tab.btn_axis, after=1.0)
    hist, scatter = w.tc_tab.chart_buttons.buttons()[1], w.tc_tab.chart_buttons.buttons()[0]
    rec.say("Histogram: today's judged values per point, with spec limits", 0.3)
    rec.tap(hist, after=2.8)
    rec.say("Back to the scatter chart, then to the QC Panel", 0.3)
    rec.tap(scatter, after=1.2)
    rec.tap_tab(w.ui.tabWidget, 0, after=1.2)


SCENE_FUNCS = {
    "measure": scene_measure,
    "model": scene_model,
    "points": scene_points,
    "settings": scene_settings,
    "edit_spec": scene_edit_spec,
    "new_model": scene_new_model,
    "temperature": scene_temperature,
}


# ------------------------------------------------------------------ output ---
def save_gif(frames: list, path: Path, hold_last_ms: int = 1500) -> None:
    """Merge identical consecutive frames (static UI stretches) and write."""
    from PIL import Image
    images, durations, last_key = [], [], None
    frame_ms = int(1000 / FPS)
    for qimage in frames:
        key = bytes(qimage.constBits())
        if key == last_key:
            durations[-1] += frame_ms
            continue
        last_key = key
        pil = Image.frombuffer("RGB", (qimage.width(), qimage.height()), key,
                               "raw", "RGB", qimage.bytesPerLine(), 1)
        images.append(pil.quantize(colors=256, method=Image.Quantize.FASTOCTREE))
        durations.append(frame_ms)
    durations[-1] += hold_last_ms
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, save_all=True, append_images=images[1:], duration=durations,
                   loop=0, optimize=True)
    print(f"saved {path.relative_to(ROOT)}: {len(images)} frames, "
          f"{sum(durations) / 1000:.1f}s, {path.stat().st_size / 1e6:.1f} MB", flush=True)


# -------------------------------------------------------------- one scene ---
def run_scene(name: str) -> None:
    sandbox = make_sandbox()
    try:
        os.chdir(sandbox)
        sys.path.insert(0, str(sandbox))
        install_fake_db()
        import main  # the sandbox copy

        def fake_detect(self):
            """Pretend the HIOKI meter was found; readings come from meter()."""
            self.current_port = "demo"
            self.connected = True
            self.consecutive_timeouts = 0
            self._set_usb_status("connected")

        main.MainWindow.start_auto_detect = fake_detect
        main.MainWindow.start_temp_sensor = lambda self: None

        from PySide6.QtCore import QTimer
        app = main.QApplication(sys.argv)
        w = main.MainWindow()
        w.showFullScreen()
        w.raise_()
        w.activateWindow()

        rnd = random.Random(3)

        def feed_temperature():
            w.on_temp_reading(time.time(), DEMO_TEMP + rnd.uniform(-0.15, 0.15))

        w.tc_tab.set_sensor_status("connected")
        feed_temperature()
        temp_timer = QTimer()
        temp_timer.timeout.connect(feed_temperature)
        temp_timer.start(1000)

        def abort():
            print(f"scene '{name}' timed out after {SCENE_TIMEOUT_S}s - GIF not saved",
                  flush=True)
            shutil.rmtree(sandbox, ignore_errors=True)
            os._exit(1)

        QTimer.singleShot(SCENE_TIMEOUT_S * 1000, abort)
        rec = Recorder(app)
        rec.pump(2.5)
        rec.start()
        rec.pump(0.6)
        SCENE_FUNCS[name](rec, w)
        rec.pump(0.4)
        save_gif(rec.stop(), OUT_DIR / f"{name}.gif")
    finally:
        os.chdir(ROOT)
        shutil.rmtree(sandbox, ignore_errors=True)
    os._exit(0)   # skip closeEvent prompts / thread joins of the throwaway window


def main_entry() -> None:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--scene":
        run_scene(args[1])
        return
    wanted = [a for a in args if a in SCENE_FUNCS] or SCENES
    for name in wanted:
        print(f"recording {name}…", flush=True)
        subprocess.run([sys.executable, __file__, "--scene", name], check=False)


if __name__ == "__main__":
    main_entry()
