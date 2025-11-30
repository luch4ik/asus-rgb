#!/usr/bin/env python3
"""
Simple Keyboard RGB Controller with Color Rectangle Picker
- Simple color rectangle (no complex wheel)
- Persistent settings
- Animations continue after GUI close
- Background daemon support
"""

import os
import sys
import fcntl
import time
import math
import threading
import logging
import queue
import json
from enum import IntEnum
from typing import Optional, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QComboBox, QGroupBox, QGridLayout,
    QMessageBox, QPlainTextEdit, QDockWidget, QCheckBox, QColorDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QSettings
from PyQt6.QtGui import QColor, QPalette

# --- HID Constants ---
class HIDReport(IntEnum):
    SET_COLOR = 0x05
    DISABLE_AUTONOMOUS = 0x0B

class HIDConstants:
    IOCTL_BASE = 0xC0004806
    DEFAULT_LED_START = 0
    DEFAULT_LED_END = 100
    MAX_INTENSITY = 255

# --- Config ---
APP_NAME = "kbdrgb"
ORG_NAME = "kbdrgb"
DEFAULT_DEVICE_PATH = os.environ.get("KBDRGB_HID", "/dev/hidraw1")

# --- Logging ---
log_queue = queue.Queue(maxsize=10000)

class QueueHandler(logging.Handler):
    def emit(self, record):
        formatted = self.format(record)
        try:
            log_queue.put_nowait(formatted)
        except queue.Full:
            try:
                log_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                log_queue.put_nowait(formatted)
            except queue.Full:
                pass

logger = logging.getLogger("kbdrgb")
logger.setLevel(logging.DEBUG)
handler = QueueHandler()
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
handler.setFormatter(formatter)
logger.addHandler(handler)

# --- HID helpers ---
def HIDIOCSFEATURE(length):
    return HIDConstants.IOCTL_BASE | (length << 16)

def send_feature_report(dev_path: str, report_id: int, data: list) -> bool:
    if not dev_path or not os.path.exists(dev_path):
        logger.error(f"Device path invalid: {dev_path}")
        return False
    fd = None
    try:
        fd = os.open(dev_path, os.O_RDWR)
        packet = bytes([report_id]) + bytes(data)
        fcntl.ioctl(fd, HIDIOCSFEATURE(len(packet)), packet)
        return True
    except OSError as e:
        logger.error(f"HID error: {e}")
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

def disable_autonomous(dev_path: str) -> bool:
    if send_feature_report(dev_path, HIDReport.DISABLE_AUTONOMOUS, [0x00]):
        time.sleep(0.01)
        return True
    return False

def set_color(dev_path: str, r: int, g: int, b: int, i: int,
              start_id: int = None, end_id: int = None) -> bool:
    if start_id is None:
        start_id = HIDConstants.DEFAULT_LED_START
    if end_id is None:
        end_id = HIDConstants.DEFAULT_LED_END

    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    i = max(0, min(HIDConstants.MAX_INTENSITY, int(i)))

    payload = [
        0x01,
        start_id & 0xFF, (start_id >> 8) & 0xFF,
        end_id & 0xFF, (end_id >> 8) & 0xFF,
        r, g, b, i
    ]
    return send_feature_report(dev_path, HIDReport.SET_COLOR, payload)

# --- Presets & Styles ---
PRESETS = {
    "Red": (255, 0, 0, 255),
    "Green": (0, 255, 0, 255),
    "Blue": (0, 0, 255, 255),
    "White": (255, 255, 255, 255),
    "Cyan": (0, 255, 255, 255),
    "Yellow": (255, 255, 0, 255),
    "Orange": (255, 128, 0, 255),
    "Pink": (255, 105, 180, 255),
    "Off": (0, 0, 0, 0),
}

STYLES = ["Static", "Breathing", "Rainbow", "Flash", "Pulse", "Wave", "Spectrum", "Fade", "Strobe", "Ripple"]

# --- Animations (same as before) ---
def breathing(dev_path, base_color, interval, stop_event):
    logger.info(f"[breathing] start")
    r, g, b = base_color
    steps = max(90, int(120 * interval))
    try:
        if not disable_autonomous(dev_path):
            return
        while not stop_event.is_set():
            for k in range(steps):
                if stop_event.is_set():
                    return
                phase = (2 * math.pi) * (k / steps)
                intensity = int(((1 - math.cos(phase)) * 0.5) * 255)
                set_color(dev_path, r, g, b, intensity)
                time.sleep(max(0.002, interval / steps))
    except Exception as e:
        logger.exception(f"[breathing] error: {e}")

def rainbow(dev_path, interval, stop_event):
    logger.info(f"[rainbow] start")
    steps = 180
    try:
        if not disable_autonomous(dev_path):
            return
        while not stop_event.is_set():
            for k in range(steps):
                if stop_event.is_set():
                    return
                hue = k / steps
                c = QColor.fromHsvF(hue, 1.0, 1.0)
                set_color(dev_path, c.red(), c.green(), c.blue(), 255)
                time.sleep(interval)
    except Exception as e:
        logger.exception(f"[rainbow] error: {e}")

def ripple(dev_path, base_color, interval, stop_event):
    logger.info(f"[ripple] start")
    r, g, b = base_color
    leds = 20
    base_intensity = int(0.20 * 255)
    ripple_boost = int(0.05 * 255)
    import random
    led_intensities = [base_intensity] * leds

    try:
        if not disable_autonomous(dev_path):
            return
        for seg in range(leds):
            set_color(dev_path, r, g, b, base_intensity, seg*5, seg*5+4)

        ripple_timer = 0
        while not stop_event.is_set():
            ripple_timer += interval
            if ripple_timer >= random.uniform(0.1, 0.5):
                ripple_timer = 0
                keystroke_led = random.randint(0, leds - 1)
                for i in range(max(0, keystroke_led - 2), min(leds, keystroke_led + 3)):
                    distance = abs(i - keystroke_led)
                    boost = ripple_boost // (distance + 1)
                    led_intensities[i] = min(255, led_intensities[i] + boost)

            for seg in range(leds):
                if led_intensities[seg] > base_intensity:
                    led_intensities[seg] = max(base_intensity, led_intensities[seg] - 2)
                set_color(dev_path, r, g, b, led_intensities[seg], seg*5, seg*5+4)

            if stop_event.is_set():
                return
            time.sleep(interval)
    except Exception as e:
        logger.exception(f"[ripple] error: {e}")

# --- Animation Controller ---
class AnimationController(QObject):
    thread_state = pyqtSignal(str)

    def __init__(self, get_device_path_callable):
        super().__init__()
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._get_device_path = get_device_path_callable

    def start(self, style: str, base_color: Tuple[int, int, int], interval: float):
        self.stop()
        self.stop_event.clear()

        style_lower = style.lower()
        dev_path = self._get_device_path()

        if not dev_path or not os.path.exists(dev_path):
            logger.error(f"Device not available: {dev_path}")
            return

        if style_lower == "static":
            disable_autonomous(dev_path)
            set_color(dev_path, *base_color, 255)
            return

        style_funcs = {
            "breathing": lambda: breathing(dev_path, base_color, interval, self.stop_event),
            "rainbow": lambda: rainbow(dev_path, interval, self.stop_event),
            "ripple": lambda: ripple(dev_path, base_color, interval, self.stop_event),
        }

        if style_lower in style_funcs:
            self.thread = threading.Thread(target=style_funcs[style_lower], daemon=False, name=f"Anim-{style_lower}")
            self.thread.start()
            logger.info(f"Started animation: {style_lower}")
        else:
            logger.warning(f"Unknown style: {style_lower}")

    def stop(self):
        if self.thread and self.thread.is_alive():
            logger.info("Stopping animation...")
            self.stop_event.set()
            self.thread.join(timeout=2.0)
        self.thread = None

# --- Log Console ---
class LogConsole(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Logs", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.setWidget(self.view)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.flush_logs)
        self.timer.start(100)

    def flush_logs(self):
        while True:
            try:
                line = log_queue.get_nowait()
                self.view.appendPlainText(line)
            except queue.Empty:
                break

# --- Main Window ---
class SimpleRGBController(QMainWindow):
    def __init__(self):
        super().__init__()

        # Settings
        self.settings = QSettings(ORG_NAME, APP_NAME)
        logger.info(f"Settings file: {self.settings.fileName()}")

        # Device
        self.device_path = self.settings.value("device_path", DEFAULT_DEVICE_PATH, str)
        self.device_available = os.path.exists(self.device_path)

        # State
        self.current_color = [
            self.settings.value("color_r", 255, int),
            self.settings.value("color_g", 128, int),
            self.settings.value("color_b", 0, int),
        ]
        self.current_intensity = self.settings.value("intensity", 255, int)

        logger.info(f"Loaded: RGB({self.current_color[0]},{self.current_color[1]},{self.current_color[2]}) I={self.current_intensity}")

        # Animator
        self.animator = AnimationController(lambda: self.device_path)

        self.init_ui()

        # Apply saved color
        if self.device_available:
            disable_autonomous(self.device_path)
            set_color(self.device_path, *self.current_color, self.current_intensity)

    def init_ui(self):
        self.setWindowTitle("Keyboard RGB Controller - Simple")
        self.setGeometry(100, 100, 600, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout()
        central.setLayout(layout)

        # Title
        title = QLabel("Keyboard RGB Controller")
        title.setStyleSheet("font-size: 20px; font-weight: bold; padding: 10px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Status
        status = "● Connected" if self.device_available else "● Not Found"
        self.status_label = QLabel(f"{status} ({self.device_path})")
        self.status_label.setStyleSheet(f"color: {'lime' if self.device_available else 'red'}; padding: 5px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Color Preview
        preview_group = QGroupBox("Current Color")
        preview_layout = QVBoxLayout()
        self.color_preview = QLabel()
        self.color_preview.setMinimumHeight(80)
        self.color_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_preview()
        preview_layout.addWidget(self.color_preview)
        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)

        # Color Picker Button
        color_btn_layout = QHBoxLayout()
        pick_color_btn = QPushButton("Pick Color")
        pick_color_btn.setMinimumHeight(50)
        pick_color_btn.clicked.connect(self.pick_color)
        color_btn_layout.addWidget(pick_color_btn)
        layout.addLayout(color_btn_layout)

        # Brightness
        brightness_group = QGroupBox("Brightness")
        brightness_layout = QHBoxLayout()
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 255)
        self.brightness_slider.setValue(self.current_intensity)
        self.brightness_slider.valueChanged.connect(self.on_brightness_changed)
        brightness_layout.addWidget(QLabel("0"))
        brightness_layout.addWidget(self.brightness_slider)
        brightness_layout.addWidget(QLabel("255"))
        self.brightness_value = QLabel(str(self.current_intensity))
        self.brightness_value.setMinimumWidth(40)
        brightness_layout.addWidget(self.brightness_value)
        brightness_group.setLayout(brightness_layout)
        layout.addWidget(brightness_group)

        # Quick Presets
        presets_group = QGroupBox("Quick Presets")
        presets_grid = QGridLayout()
        row, col = 0, 0
        for name, (r, g, b, i) in PRESETS.items():
            btn = QPushButton(name)
            btn.setStyleSheet(f"background-color: rgb({r},{g},{b}); color: {'white' if (r+g+b)/3 < 128 else 'black'}; padding: 8px;")
            btn.clicked.connect(lambda checked=False, n=name: self.apply_preset(n))
            presets_grid.addWidget(btn, row, col)
            col += 1
            if col >= 3:
                col, row = 0, row + 1
        presets_group.setLayout(presets_grid)
        layout.addWidget(presets_group)

        # Animation Style
        style_group = QGroupBox("Animation")
        style_layout = QHBoxLayout()
        self.style_combo = QComboBox()
        self.style_combo.addItems(STYLES)
        style_layout.addWidget(QLabel("Style:"))
        style_layout.addWidget(self.style_combo)
        style_group.setLayout(style_layout)
        layout.addWidget(style_group)

        # Speed
        speed_group = QGroupBox("Animation Speed")
        speed_layout = QHBoxLayout()
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(1, 50)
        self.speed_slider.setValue(self.settings.value("speed_slider", 10, int))
        speed_layout.addWidget(QLabel("Slow"))
        speed_layout.addWidget(self.speed_slider)
        speed_layout.addWidget(QLabel("Fast"))
        speed_group.setLayout(speed_layout)
        layout.addWidget(speed_group)

        # Buttons
        btn_layout = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.setMinimumHeight(50)
        apply_btn.setStyleSheet("background-color: #4CAF50; color: white; font-size: 16px; font-weight: bold;")
        apply_btn.clicked.connect(self.apply_lighting)
        btn_layout.addWidget(apply_btn)

        stop_btn = QPushButton("Stop")
        stop_btn.setMinimumHeight(50)
        stop_btn.setStyleSheet("background-color: #f44336; color: white; font-size: 16px; font-weight: bold;")
        stop_btn.clicked.connect(self.stop_animation)
        btn_layout.addWidget(stop_btn)
        layout.addLayout(btn_layout)

        # Log Console
        self.log_console = LogConsole(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_console)

        self.apply_dark_theme()

    def apply_dark_theme(self):
        app = QApplication.instance()
        app.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        app.setPalette(palette)

    def pick_color(self):
        color = QColorDialog.getColor(QColor(*self.current_color), self, "Pick a Color")
        if color.isValid():
            self.current_color = [color.red(), color.green(), color.blue()]
            self.update_preview()
            self.persist_state()
            # Apply immediately
            if self.device_available:
                disable_autonomous(self.device_path)
                set_color(self.device_path, *self.current_color, self.current_intensity)

    def on_brightness_changed(self, value):
        self.current_intensity = value
        self.brightness_value.setText(str(value))
        self.update_preview()
        self.persist_state()
        # Apply immediately
        if self.device_available:
            disable_autonomous(self.device_path)
            set_color(self.device_path, *self.current_color, self.current_intensity)

    def update_preview(self):
        r, g, b = self.current_color
        factor = self.current_intensity / 255
        dr, dg, db = int(r * factor), int(g * factor), int(b * factor)
        color = QColor(dr, dg, db)
        palette = self.color_preview.palette()
        palette.setColor(QPalette.ColorRole.Window, color)
        self.color_preview.setAutoFillBackground(True)
        self.color_preview.setPalette(palette)
        text_color = "white" if (r + g + b) / 3 < 128 else "black"
        self.color_preview.setText(f"RGB({r}, {g}, {b})\\nBrightness: {self.current_intensity}")
        self.color_preview.setStyleSheet(f"color: {text_color}; font-size: 14px; font-weight: bold; border: 2px solid #333;")

    def apply_preset(self, name):
        r, g, b, i = PRESETS[name]
        self.current_color = [r, g, b]
        self.current_intensity = i
        self.brightness_slider.setValue(i)
        self.update_preview()
        self.persist_state()
        if self.device_available:
            disable_autonomous(self.device_path)
            set_color(self.device_path, r, g, b, i)

    def apply_lighting(self):
        if not self.device_available:
            QMessageBox.warning(self, "Error", "Device not found")
            return
        style = self.style_combo.currentText()
        interval = self.speed_slider.value() / 100
        self.animator.start(style, tuple(self.current_color), interval)

    def stop_animation(self):
        self.animator.stop()
        if self.device_available:
            disable_autonomous(self.device_path)
            set_color(self.device_path, 0, 0, 0, 0)

    def persist_state(self):
        r, g, b = self.current_color
        self.settings.setValue("color_r", r)
        self.settings.setValue("color_g", g)
        self.settings.setValue("color_b", b)
        self.settings.setValue("intensity", self.current_intensity)
        self.settings.setValue("device_path", self.device_path)
        self.settings.setValue("speed_slider", self.speed_slider.value())
        self.settings.sync()
        logger.debug(f"Settings saved: RGB({r},{g},{b}) I={self.current_intensity}")

    def closeEvent(self, event):
        logger.info("Closing GUI - animations will continue in background")
        self.persist_state()
        # DON'T stop animations - let them continue
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = SimpleRGBController()
    window.show()
    logger.info("Application started")
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
