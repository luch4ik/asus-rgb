#!/usr/bin/env python3
import os
import sys
import fcntl
import time
import math
import threading
import logging
import queue
import argparse
import json
import signal
from pathlib import Path
from enum import IntEnum
from typing import Optional, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QComboBox, QGroupBox, QGridLayout,
    QMessageBox, QPlainTextEdit, QDockWidget, QCheckBox, QFrame, QLineEdit
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QSettings, QPointF, QSize
from PyQt6.QtGui import QColor, QPalette, QPainter, QConicalGradient, QPen, QDoubleValidator

# --- Config ---
APP_NAME = "kbdrgb"
ORG_NAME = "kbdrgb"
DEFAULT_DEVICE_PATH = os.environ.get("KBDRGB_HID", "/dev/hidraw1")

# --- Logging ---
log_queue = queue.Queue(maxsize=1000)
class QueueHandler(logging.Handler):
    def emit(self, record):
        try: log_queue.put_nowait(self.format(record))
        except queue.Full: pass

logger = logging.getLogger("kbdrgb")
logger.setLevel(logging.INFO)
handler = QueueHandler(); handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
logger.addHandler(handler)

# --- HID Communication ---
def HIDIOCSFEATURE(length): return 0xC0004806 | (length << 16)
def send_feature_report(dev_path, report_id, data):
    if not os.path.exists(dev_path): return False
    logger.info(f"SHELL CMD: hid-feature-report --device {dev_path} --report-id {report_id} --data {' '.join(f'{b:02x}' for b in data)}")
    packet = bytes([report_id]) + bytes(data)
    try:
        with open(dev_path, 'rb+') as fd: fcntl.ioctl(fd, HIDIOCSFEATURE(len(packet)), packet)
        return True
    except (OSError, IOError) as e: logger.error(f"HID Error: {e}"); return False

def disable_autonomous(dev_path): send_feature_report(dev_path, 0x0B, [0x00]); time.sleep(0.01)
def set_color(dev_path, r, g, b, i):
    return send_feature_report(dev_path, 0x05, [0x01, 0, 0, 100, 0, int(r), int(g), int(b), int(i)])

# --- Animation Logic (Reworked for Live Updates) ---
def breathing(dev_path, param_provider, stop_event):
    logger.info("Animation started: Breathing")
    disable_autonomous(dev_path)
    while not stop_event.is_set():
        color, interval, intensity = param_provider()
        r, g, b = color
        t0 = time.monotonic()
        for k in range(180):
            if stop_event.is_set(): break
            # Check for parameter changes mid-cycle for responsiveness
            new_color, new_interval, new_intensity = param_provider()
            if new_interval != interval: interval = new_interval; break
            if new_color != (r,g,b): r,g,b = new_color
            if new_intensity != intensity: intensity = new_intensity
            
            phase = (1 - math.cos(k * 2 * math.pi / 180)) / 2
            set_color(dev_path, r, g, b, int(phase * intensity))
            target = t0 + (k + 1) * (interval / 180)
            time.sleep(max(0.0, target - time.monotonic()))
    logger.info("Animation stopped: Breathing")

class AnimationController(QObject):
    def __init__(self, dev_path):
        super().__init__()
        self.device_path = dev_path
        self.thread = None; self.stop_event = threading.Event(); self.current_style = "Static"
        self._lock = threading.Lock()
        self._params = {'color': (255,0,0), 'interval': 0.5, 'intensity': 255}

    def get_params(self):
        with self._lock: return self._params['color'], self._params['interval'], self._params['intensity']
    
    def update_params(self, **kwargs):
        with self._lock: self._params.update(kwargs)

    def start(self, style, color, interval, intensity):
        self.stop(); self.stop_event.clear()
        self.current_style = style
        self.update_params(color=color, interval=interval, intensity=intensity)
        
        if style.lower() == "static":
            set_color(self.device_path, *color, intensity)
            return
        
        if style.lower() == "breathing":
            self.thread = threading.Thread(target=breathing, args=(self.device_path, self.get_params, self.stop_event), daemon=False)
            self.thread.start()
        else:
             logger.warning(f"Animation '{style}' not implemented for live updates yet.")
             set_color(self.device_path, *color, intensity) # Fallback to static

    def stop(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set(); self.thread.join(timeout=1.0)
        self.current_style = "Static"; disable_autonomous(self.device_path)

# --- UI Components ---
class ColorWheel(QFrame):
    colorChanged = pyqtSignal(QColor)
    def __init__(self, parent=None):
        super().__init__(parent); self.setMinimumSize(250, 250); self.setCursor(Qt.CursorShape.CrossCursor)
        self.h, self.s, self.v = 0.0, 1.0, 1.0
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy, r = self.rect().center().x(), self.rect().center().y(), min(self.width(), self.height()) / 2 - 10
        grad = QConicalGradient(QPointF(cx, cy), 90)
        for i in range(361): grad.setColorAt(i/360., QColor.fromHsvF(i/360., 1.0, self.v))
        p.setBrush(grad); p.drawEllipse(QPointF(cx, cy), r, r)
        angle = 2 * math.pi * self.h + math.pi/2; ix, iy = cx + r * self.s * math.cos(angle), cy - r * self.s * math.sin(angle)
        p.setPen(QPen(Qt.GlobalColor.white, 2)); p.setBrush(self.get_color()); p.drawEllipse(QPointF(ix, iy), 8, 8)
    def mouseMoveEvent(self, e): self._update_pos(e.position())
    def mousePressEvent(self, e): self._update_pos(e.position())
    def _update_pos(self, pos):
        cx, cy, r = self.rect().center().x(), self.rect().center().y(), min(self.width(), self.height())/2-10
        dx, dy = pos.x()-cx, pos.y()-cy; dist = math.hypot(dx, dy)
        if dist > r: return
        self.h = (math.degrees(math.atan2(-dy, dx)) - 90) % 360 / 360.; self.s = dist / r
        self.update(); self.colorChanged.emit(self.get_color())
    def get_color(self): return QColor.fromHsvF(self.h, self.s, self.v)
    def set_color(self, c: QColor):
        h, s, v = c.hueF(), c.saturationF(), c.valueF()
        if h >= 0: self.h = h
        self.s, self.v = s, v; self.update()

class LogConsole(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Logs", parent); self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.view = QPlainTextEdit(); self.view.setReadOnly(True); self.setWidget(self.view)
        self.timer = QTimer(self); self.timer.timeout.connect(self.flush); self.timer.start(100)
    def flush(self):
        while not log_queue.empty(): self.view.appendPlainText(log_queue.get_nowait())

class MainAppWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.device_path = self.settings.value("device_path", DEFAULT_DEVICE_PATH, str)
        self.animator = AnimationController(self.device_path)
        self._build_ui(); self.load_settings()

    def _build_ui(self):
        self.setWindowTitle("ASUS HID RGB Control"); self.setGeometry(100, 100, 720, 820); self.apply_dark_theme()
        central = QWidget(); self.setCentralWidget(central); layout = QVBoxLayout(central)
        title = QLabel("⌨️ ASUS HID RGB Control"); title.setStyleSheet("font-size: 24px; font-weight: bold;"); title.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(title)
        
        wheel_group = QGroupBox("Color & Brightness"); wheel_layout = QVBoxLayout(wheel_group)
        self.wheel = ColorWheel(); self.wheel.colorChanged.connect(self.live_update_color)
        wheel_layout.addWidget(self.wheel)
        slider_layout = QHBoxLayout(); slider_layout.addWidget(QLabel("Brightness"))
        self.slider = QSlider(Qt.Orientation.Horizontal); self.slider.setRange(0, 255)
        self.slider_label = QLabel(); slider_layout.addWidget(self.slider); slider_layout.addWidget(self.slider_label)
        self.slider.valueChanged.connect(self.slider_label.setNum)
        self.slider.valueChanged.connect(self.live_update_intensity)
        wheel_layout.addLayout(slider_layout); layout.addWidget(wheel_group)

        anim_group = QGroupBox("Animation"); anim_layout = QVBoxLayout(anim_group)
        self.style_combo = QComboBox(); self.style_combo.addItems(["Static", "Breathing"]); self.style_combo.activated.connect(self.apply_animation_style)
        anim_layout.addWidget(self.style_combo)
        speed_layout = QHBoxLayout(); speed_layout.addWidget(QLabel("Slow")); self.speed_slider = QSlider(Qt.Orientation.Horizontal); self.speed_slider.setRange(0, 100); speed_layout.addWidget(self.speed_slider); speed_layout.addWidget(QLabel("Fast"))
        self.speed_input = QLineEdit(); self.speed_input.setValidator(QDoubleValidator(0.01, 30.0, 2)); speed_layout.addWidget(self.speed_input)
        self.speed_slider.valueChanged.connect(self.live_update_interval_from_slider)
        self.speed_input.editingFinished.connect(self.live_update_interval_from_input)
        anim_layout.addLayout(speed_layout); layout.addWidget(anim_group)

        btn_layout = QHBoxLayout()
        save_exit_btn = QPushButton("💾 Save and Exit"); save_exit_btn.setMinimumHeight(40); save_exit_btn.setStyleSheet("background-color: #4CAF50;"); save_exit_btn.clicked.connect(self.save_and_exit)
        stop_btn = QPushButton("■ Stop Animation & Power Off"); stop_btn.setMinimumHeight(40); stop_btn.setStyleSheet("background-color: #f44336;"); stop_btn.clicked.connect(self.stop_and_off)
        btn_layout.addWidget(save_exit_btn); btn_layout.addWidget(stop_btn); layout.addLayout(btn_layout)

        self.log_console = LogConsole(self); self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.log_console)
        self.log_console.setVisible(True); self.menuBar().addMenu("View").addAction(self.log_console.toggleViewAction())
    
    def load_settings(self):
        c = QColor(*[self.settings.value(f"color_{k}", v, int) for k,v in zip("rgb",[0,0,255])]); i = self.settings.value("intensity", 255, int); interval = self.settings.value("speed_interval", 0.5, float)
        self.wheel.blockSignals(True); self.slider.blockSignals(True); self.speed_slider.blockSignals(True); self.speed_input.blockSignals(True)
        self.slider.setValue(i); self.wheel.set_color(c); self.speed_input.setText(f"{interval:.2f}"); self.speed_slider.setValue(self._interval_to_slider(interval))
        self.wheel.blockSignals(False); self.slider.blockSignals(False); self.speed_slider.blockSignals(False); self.speed_input.blockSignals(False)
        self.animator.start("Static", [c.red(),c.green(),c.blue()], interval, i) # Start in static mode

    def live_update_color(self, color: QColor):
        if self.animator.current_style.lower() == "static":
            set_color(self.device_path, color.red(), color.green(), color.blue(), self.slider.value())
        else: self.animator.update_params(color=(color.red(), color.green(), color.blue()))

    def live_update_intensity(self, value):
        c = self.wheel.get_color()
        if self.animator.current_style.lower() == "static":
            set_color(self.device_path, c.red(), c.green(), c.blue(), value)
        else: self.animator.update_params(intensity=value)
            
    def live_update_interval_from_slider(self, value):
        interval = self._slider_to_interval(value)
        self.speed_input.blockSignals(True); self.speed_input.setText(f"{interval:.2f}"); self.speed_input.blockSignals(False)
        if self.animator.current_style.lower() != "static": self.apply_animation_style()

    def live_update_interval_from_input(self):
        interval = float(self.speed_input.text())
        self.speed_slider.blockSignals(True); self.speed_slider.setValue(self._interval_to_slider(interval)); self.speed_slider.blockSignals(False)
        if self.animator.current_style.lower() != "static": self.apply_animation_style()

    def apply_animation_style(self):
        c = self.wheel.get_color()
        self.animator.start(self.style_combo.currentText(), (c.red(), c.green(), c.blue()), float(self.speed_input.text()), self.slider.value())

    def save_and_exit(self):
        self.animator.stop(); c = self.wheel.get_color(); i = self.slider.value()
        set_color(self.device_path, c.red(), c.green(), c.blue(), i)
        self.settings.setValue("color_r", c.red()); self.settings.setValue("color_g", c.green()); self.settings.setValue("color_b", c.blue())
        self.settings.setValue("intensity", i); self.settings.setValue("speed_interval", float(self.speed_input.text()))
        logger.info("Settings saved."); self.close()

    def stop_and_off(self): self.animator.stop(); set_color(self.device_path, 0, 0, 0, 0)
    def _slider_to_interval(self, v): return max(0.01, min(30.0, 10 ** (1.0 - 0.03 * v)))
    def _interval_to_slider(self, i): return int(round((1.0 - math.log10(max(0.01, min(30.0, i)))) / 0.03))
    def closeEvent(self, e): self.animator.stop(); super().closeEvent(e)
    def apply_dark_theme(self):
        p = QPalette(); p.setColor(QPalette.ColorRole.Window, QColor(53,53,53)); p.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        p.setColor(QPalette.ColorRole.Base, QColor(25,25,25)); p.setColor(QPalette.ColorRole.AlternateBase, QColor(53,53,53))
        p.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white); p.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
        p.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white); p.setColor(QPalette.ColorRole.Button, QColor(53,53,53))
        p.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white); p.setColor(QPalette.ColorRole.Highlight, QColor(42,130,218))
        p.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black); QApplication.instance().setPalette(p)

def main():
    parser = argparse.ArgumentParser(description="Control ASUS Keyboard RGB")
    parser.add_argument("--mode", type=str, help="Set the animation mode")
    parser.add_argument("--color", type=str, help="Set the color in hex format (e.g., ff0000)")
    parser.add_argument("--brightness", type=int, help="Set the brightness (0-255)")
    parser.add_argument("--speed", type=float, help="Set the animation speed")
    args = parser.parse_args()

    if args.mode:
        handle_cli(args)
    else:
        app = QApplication(sys.argv); app.setStyle("Fusion")
        win = MainAppWindow(); win.show(); sys.exit(app.exec())

def handle_cli(args):
    settings = QSettings(ORG_NAME, APP_NAME)
    device_path = settings.value("device_path", DEFAULT_DEVICE_PATH, str)
    animator = AnimationController(device_path)

    color = (255, 0, 0)
    if args.color:
        hex_color = args.color.lstrip('#')
        color = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    brightness = args.brightness if args.brightness is not None else 255
    speed = args.speed if args.speed is not None else 0.5

    animator.start(args.mode, color, speed, brightness)
    # The script should exit after setting the mode.
    # For animations, a separate mechanism would be needed to keep it running.
    # For now, we assume that for CLI, we set the mode and exit.
    # Breathing animation will stop because the main thread exits.
    # This is a limitation we'll address if needed.
    time.sleep(0.1) # Give it a moment to send the command
    sys.exit(0)

if __name__ == "__main__":
    main()