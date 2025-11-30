#!/usr/bin/env python3
"""
Integrated Keyboard RGB Controller
- Simple color rectangle picker (QColorDialog)
- Communicates with background daemon for true persistence
- Settings saved and animations continue independently
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
from pathlib import Path
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
DAEMON_STATE_FILE = Path.home() / ".config" / "kbdrgb" / "daemon_state.json"
DAEMON_PID_FILE = Path.home() / ".config" / "kbdrgb" / "daemon.pid"

# Ensure config directory exists
DAEMON_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

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

# --- Daemon Integration ---
def is_daemon_running() -> bool:
    """Check if daemon is running"""
    if not DAEMON_PID_FILE.exists():
        return False
    try:
        with open(DAEMON_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        # Check if process exists
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, OSError):
        return False

def write_daemon_state(style: str, color: Tuple[int, int, int], intensity: int,
                       interval: float, device_path: str):
    """Write state for daemon to pick up"""
    state = {
        "style": style,
        "color": list(color),
        "intensity": intensity,
        "interval": interval,
        "device_path": device_path
    }
    try:
        with open(DAEMON_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        logger.info(f"Wrote daemon state: {style} RGB{color}")
    except Exception as e:
        logger.error(f"Failed to write daemon state: {e}")

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

STYLES = ["Static", "Breathing", "Rainbow", "Ripple"]

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
class IntegratedRGBController(QMainWindow):
    def __init__(self):
        super().__init__()

        # Settings
        self.settings = QSettings(ORG_NAME, APP_NAME)
        logger.info(f"Settings file: {self.settings.fileName()}")

        # Device
        self.device_path = self.settings.value("device_path", DEFAULT_DEVICE_PATH, str)
        self.device_available = os.path.exists(self.device_path)

        # Daemon status
        self.daemon_mode = is_daemon_running()

        # State
        self.current_color = [
            self.settings.value("color_r", 255, int),
            self.settings.value("color_g", 128, int),
            self.settings.value("color_b", 0, int),
        ]
        self.current_intensity = self.settings.value("intensity", 255, int)
        self.use_daemon = self.settings.value("use_daemon", True, bool)

        logger.info(f"Loaded: RGB({self.current_color[0]},{self.current_color[1]},{self.current_color[2]}) I={self.current_intensity}")
        logger.info(f"Daemon mode: {self.daemon_mode}")

        self.init_ui()

        # Apply saved color if not using daemon
        if not self.use_daemon and self.device_available:
            disable_autonomous(self.device_path)
            set_color(self.device_path, *self.current_color, self.current_intensity)

    def init_ui(self):
        self.setWindowTitle("Keyboard RGB Controller - Integrated")
        self.setGeometry(100, 100, 650, 750)

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
        daemon_status = " | Daemon: Running" if self.daemon_mode else " | Daemon: Stopped"
        self.status_label = QLabel(f"{status} ({self.device_path}){daemon_status}")
        self.status_label.setStyleSheet(f"color: {'lime' if self.device_available else 'red'}; padding: 5px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Daemon Control
        daemon_group = QGroupBox("Persistence Mode")
        daemon_layout = QVBoxLayout()

        self.use_daemon_checkbox = QCheckBox("Use background daemon (animations persist after close)")
        self.use_daemon_checkbox.setChecked(self.use_daemon)
        self.use_daemon_checkbox.stateChanged.connect(self.on_daemon_mode_changed)
        daemon_layout.addWidget(self.use_daemon_checkbox)

        daemon_info = QLabel("When enabled, animations continue running even after closing this window")
        daemon_info.setStyleSheet("color: gray; font-size: 11px;")
        daemon_layout.addWidget(daemon_info)

        daemon_group.setLayout(daemon_layout)
        layout.addWidget(daemon_group)

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
        pick_color_btn.setStyleSheet("font-size: 14px;")
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

        stop_btn = QPushButton("Stop/Off")
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

    def on_daemon_mode_changed(self, state):
        self.use_daemon = bool(state)
        self.settings.setValue("use_daemon", self.use_daemon)
        logger.info(f"Daemon mode: {'enabled' if self.use_daemon else 'disabled'}")

    def pick_color(self):
        color = QColorDialog.getColor(QColor(*self.current_color), self, "Pick a Color")
        if color.isValid():
            self.current_color = [color.red(), color.green(), color.blue()]
            self.update_preview()
            self.persist_state()
            # Apply immediately if not using daemon
            if not self.use_daemon and self.device_available:
                disable_autonomous(self.device_path)
                set_color(self.device_path, *self.current_color, self.current_intensity)

    def on_brightness_changed(self, value):
        self.current_intensity = value
        self.brightness_value.setText(str(value))
        self.update_preview()
        self.persist_state()
        # Apply immediately if not using daemon
        if not self.use_daemon and self.device_available:
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
        self.color_preview.setText(f"RGB({r}, {g}, {b})\nBrightness: {self.current_intensity}")
        self.color_preview.setStyleSheet(f"color: {text_color}; font-size: 14px; font-weight: bold; border: 2px solid #333;")

    def apply_preset(self, name):
        r, g, b, i = PRESETS[name]
        self.current_color = [r, g, b]
        self.current_intensity = i
        self.brightness_slider.setValue(i)
        self.update_preview()
        self.persist_state()

        if self.use_daemon:
            write_daemon_state("static", (r, g, b), i, 0.1, self.device_path)
        elif self.device_available:
            disable_autonomous(self.device_path)
            set_color(self.device_path, r, g, b, i)

    def apply_lighting(self):
        if not self.device_available:
            QMessageBox.warning(self, "Error", "Device not found")
            return

        style = self.style_combo.currentText()
        interval = self.speed_slider.value() / 100

        if self.use_daemon:
            # Write state for daemon to pick up
            write_daemon_state(style, tuple(self.current_color), self.current_intensity, interval, self.device_path)
            logger.info(f"Sent {style} animation to daemon")
        else:
            # Direct application (won't persist after close)
            logger.warning("Daemon mode disabled - animation will stop when GUI closes")
            if style.lower() == "static":
                disable_autonomous(self.device_path)
                set_color(self.device_path, *self.current_color, self.current_intensity)
            else:
                QMessageBox.information(self, "Info",
                    "Enable daemon mode for persistent animations.\n\n"
                    "Otherwise, animations will stop when you close the GUI.")

    def stop_animation(self):
        if self.use_daemon:
            # Send "off" state to daemon
            write_daemon_state("static", (0, 0, 0), 0, 0.1, self.device_path)
            logger.info("Sent stop command to daemon")
        elif self.device_available:
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
        logger.info("Closing GUI...")
        self.persist_state()

        if self.use_daemon:
            logger.info("Daemon mode enabled - animations will continue")
        else:
            logger.info("Daemon mode disabled - animations will stop")

        event.accept()

def main():
    app = QApplication(sys.argv)
    window = IntegratedRGBController()
    window.show()
    logger.info("Application started")

    # Check daemon status and inform user
    if is_daemon_running():
        logger.info("Daemon is running - animations will persist")
    else:
        logger.warning("Daemon is NOT running - start it with: python3 kbdrgb_daemon.py")

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
