#!/usr/bin/env python3
"""
Keyboard Lighting Controller - PyQt6 GUI
- Live color wheel (HSV) with instant HID updates on drag
- Brightness (value) slider synced with wheel
- Quick presets and user presets (JSON via QSettings)
- Instant apply on style/preset changes
- Smooth breathing; ripple delegated to daemon
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
    QMessageBox, QPlainTextEdit, QDockWidget, QCheckBox, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QSettings, QPointF, QSize
from PyQt6.QtGui import QColor, QPalette, QPainter, QConicalGradient, QRadialGradient, QPen

# --- HID Constants ---
class HIDReport(IntEnum):
    """HID Report IDs for keyboard RGB control"""
    SET_COLOR = 0x05
    DISABLE_AUTONOMOUS = 0x0B

class HIDConstants:
    """HID-related constants"""
    IOCTL_BASE = 0xC0004806
    DEFAULT_LED_START = 0
    DEFAULT_LED_END = 100
    MAX_INTENSITY = 255

# --- Config ---
APP_NAME = "kbdrgb"
ORG_NAME = "kbdrgb"
DEFAULT_DEVICE_PATH = os.environ.get("KBDRGB_HID", "/dev/hidraw1")
WATCHDOG_INTERVAL_SEC = 0.5
WATCHDOG_STALL_THRESHOLD_SEC = 2.0

# --- Logging (thread-safe queue -> GUI console) ---
log_queue = queue.Queue(maxsize=10000)

class QueueHandler(logging.Handler):
    def emit(self, record):
        formatted = self.format(record)
        try:
            log_queue.put_nowait(formatted)
        except queue.Full:
            # Drop oldest message and try again
            try:
                log_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                log_queue.put_nowait(formatted)
            except queue.Full:
                # If still full, silently drop the message
                pass

logger = logging.getLogger("kbdrgb")
logger.setLevel(logging.DEBUG)
handler = QueueHandler()
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
handler.setFormatter(formatter)
logger.addHandler(handler)

# --- HID helpers ---
def HIDIOCSFEATURE(length):
    """Calculate IOCTL command for HID feature report"""
    return HIDConstants.IOCTL_BASE | (length << 16)

def send_feature_report(dev_path: str, report_id: int, data: list) -> bool:
    """
    Send HID feature report to device
    Returns True on success, False on failure
    """
    if not dev_path or not os.path.exists(dev_path):
        logger.error(f"Device path invalid or doesn't exist: {dev_path}")
        return False

    full_packet = bytes([report_id]) + bytes(data)
    start = time.monotonic()
    fd = None
    try:
        fd = os.open(dev_path, os.O_RDWR)
        fcntl.ioctl(fd, HIDIOCSFEATURE(len(full_packet)), full_packet)
        elapsed = (time.monotonic() - start) * 1000
        logger.debug(f"HID report sent (id=0x{report_id:02X}, len={len(full_packet)}), {elapsed:.1f} ms")
        return True
    except OSError as e:
        elapsed = (time.monotonic() - start) * 1000
        logger.error(f"HID Error on id=0x{report_id:02X} after {elapsed:.1f} ms: {e}")
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

def disable_autonomous(dev_path: str) -> bool:
    """Disable autonomous lighting mode"""
    if send_feature_report(dev_path, HIDReport.DISABLE_AUTONOMOUS, [0x00]):
        time.sleep(0.01)
        return True
    return False

def set_color(dev_path: str, r: int, g: int, b: int, i: int,
              start_id: int = None, end_id: int = None) -> bool:
    """Set LED color for a range of LEDs"""
    if start_id is None:
        start_id = HIDConstants.DEFAULT_LED_START
    if end_id is None:
        end_id = HIDConstants.DEFAULT_LED_END

    # Clamp values
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
    "Purple": (128, 0, 128, 255),
    "Cyan": (0, 255, 255, 255),
    "Yellow": (255, 255, 0, 255),
    "Orange": (255, 128, 0, 255),
    "Pink": (255, 105, 180, 255),
    "Night": (255, 128, 0, 64),
    "Off": (0, 0, 0, 0),
}

STYLES = [
    "Static",
    "Breathing",
    "Rainbow",
    "Flash",
    "Pulse",
    "Wave",
    "Spectrum",
    "Fade",
    "Strobe",
    "Ripple"  # delegated to daemon
]

# --- ColorWheel (live HSV wheel) ---
class ColorWheel(QFrame):
    colorChanged = pyqtSignal(int, int, int)  # r,g,b

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self.setMaximumSize(400, 400)
        self.setSizePolicy(QWidget().sizePolicy())  # Normal size policy
        self.h = 0.0       # hue 0..1
        self.s = 0.0       # saturation 0..1
        self.v = 1.0       # value 0..1
        self.setCursor(Qt.CursorShape.CrossCursor)

    def sizeHint(self):
        return QSize(250, 250)

    def heightForWidth(self, w):
        return w  # Keep it square

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        cx, cy = rect.center().x(), rect.center().y()
        radius = min(rect.width(), rect.height()) // 2

        # Hue ring
        hue_grad = QConicalGradient(QPointF(cx, cy), 0.0)
        for i in range(361):
            hue = i / 360.0
            hue_grad.setColorAt(hue, QColor.fromHsvF(hue, 1.0, 1.0))
        painter.setBrush(hue_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(rect)

        # Neutral ticks
        ticks = 12
        for i in range(ticks):
            angle = 2 * math.pi * i / ticks
            x1 = cx + (radius - 12) * math.cos(angle)
            y1 = cy - (radius - 12) * math.sin(angle)
            x2 = cx + radius * math.cos(angle)
            y2 = cy - radius * math.sin(angle)
            painter.setPen(QPen(QColor(120, 120, 120, 180), 2))
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Accent ticks up to current hue
        current_angle = 2 * math.pi * self.h
        accent = QColor(42, 130, 218)
        for i in range(ticks):
            angle = 2 * math.pi * i / ticks
            if angle <= current_angle:
                x1 = cx + (radius - 12) * math.cos(angle)
                y1 = cy - (radius - 12) * math.sin(angle)
                x2 = cx + radius * math.cos(angle)
                y2 = cy - radius * math.sin(angle)
                painter.setPen(QPen(accent, 2))
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Saturation/value disk (inner)
        inner_margin = int(radius * 0.5)
        inner_rect = rect.adjusted(inner_margin, inner_margin, -inner_margin, -inner_margin)
        sat_grad = QRadialGradient(QPointF(cx, cy), radius - inner_margin)
        sat_grad.setColorAt(0.0, QColor.fromHsvF(self.h, 0.0, self.v))
        sat_grad.setColorAt(1.0, QColor.fromHsvF(self.h, 1.0, self.v))
        painter.setBrush(sat_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(inner_rect)

        # Smooth end-of-dial arc
        end_angle_deg = -self.h * 360
        painter.setPen(QPen(accent, 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(rect, int(end_angle_deg * 16), 20 * 16)

        # Selection marker
        angle = 2 * math.pi * self.h
        rr = (radius - inner_margin) * self.s
        x = cx + rr * math.cos(angle)
        y = cy - rr * math.sin(angle)
        painter.setPen(QColor(255, 255, 255))
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawEllipse(int(x) - 5, int(y) - 5, 10, 10)

    def mousePressEvent(self, e):
        self._updateFromMouse(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._updateFromMouse(e)

    def _updateFromMouse(self, e):
        rect = self.rect()
        cx, cy = rect.center().x(), rect.center().y()
        dx = e.position().x() - cx
        dy = cy - e.position().y()
        angle = math.atan2(dy, dx)
        self.h = ((angle / (2 * math.pi)) % 1.0)
        radius = min(rect.width(), rect.height()) // 2
        inner_margin = int(radius * 0.5)
        max_r = radius - inner_margin
        dist = min(math.hypot(dx, dy), max_r)
        self.s = max(0.0, min(1.0, dist / max_r))
        self._emitColor()
        self.update()

    def setHSV(self, h, s, v):
        self.h = max(0.0, min(1.0, h))
        self.s = max(0.0, min(1.0, s))
        self.v = max(0.0, min(1.0, v))
        self._emitColor()
        self.update()

    def setRGB(self, r, g, b):
        c = QColor(r, g, b)
        self.h = c.hueF() if c.hueF() >= 0.0 else 0.0
        self.s = c.saturationF()
        self.v = c.valueF()
        self._emitColor()
        self.update()

    def _emitColor(self):
        c = QColor.fromHsvF(self.h, self.s, self.v)
        self.colorChanged.emit(c.red(), c.green(), c.blue())

# --- Animations ---
def breathing(dev_path, base_color, interval, stop_event):
    logger.info(f"[breathing] start, period={interval}s, base={base_color}")
    r, g, b = base_color
    updates_per_cycle = max(90, int(120 * interval))

    try:
        if not disable_autonomous(dev_path):
            logger.error("[breathing] failed to disable autonomous mode")
            return

        t0 = time.monotonic()
        while not stop_event.is_set():
            for k in range(updates_per_cycle):
                if stop_event.is_set():
                    logger.info("[breathing] stop requested")
                    return
                phase = (2 * math.pi) * (k / updates_per_cycle)
                intensity = int(((1 - math.cos(phase)) * 0.5) * 255)
                if not set_color(dev_path, r, g, b, intensity):
                    logger.error("[breathing] HID command failed")
                    return
                target = t0 + (k + 1) * (interval / updates_per_cycle)
                sleep = max(0.0, target - time.monotonic())
                time.sleep(sleep)
            t0 = time.monotonic()
    except Exception as e:
        logger.exception(f"[breathing] unexpected error: {e}")
    finally:
        logger.info("[breathing] exit")

def rainbow(dev_path, interval, stop_event):
    logger.info(f"[rainbow] start, interval={interval}s")
    steps = 180
    try:
        if not disable_autonomous(dev_path):
            logger.error("[rainbow] failed to disable autonomous mode")
            return
        while not stop_event.is_set():
            for k in range(steps):
                if stop_event.is_set():
                    logger.info("[rainbow] stop requested")
                    return
                hue = k / steps
                phase_g = 2 * math.pi / 3
                phase_b = 4 * math.pi / 3
                r = int(255 * (math.sin(2 * math.pi * hue) * 0.5 + 0.5))
                g = int(255 * (math.sin(2 * math.pi * hue + phase_g) * 0.5 + 0.5))
                b = int(255 * (math.sin(2 * math.pi * hue + phase_b) * 0.5 + 0.5))
                set_color(dev_path, r, g, b, 255)
                time.sleep(interval)
    except Exception as e:
        logger.exception(f"[rainbow] unexpected error: {e}")
    finally:
        logger.info("[rainbow] exit")

def flash(dev_path, base_color, interval, stop_event):
    logger.info(f"[flash] start, interval={interval}s, base={base_color}")
    r, g, b = base_color
    try:
        if not disable_autonomous(dev_path):
            logger.error("[flash] failed to disable autonomous mode")
            return
        while not stop_event.is_set():
            set_color(dev_path, r, g, b, 255)
            time.sleep(interval)
            if stop_event.is_set():
                logger.info("[flash] stop requested")
                return
            set_color(dev_path, 0, 0, 0, 0)
            time.sleep(interval)
    except Exception as e:
        logger.exception(f"[flash] unexpected error: {e}")
    finally:
        logger.info("[flash] exit")

def pulse(dev_path, base_color, interval, stop_event):
    logger.info(f"[pulse] start, interval={interval}s, base={base_color}")
    r, g, b = base_color
    try:
        if not disable_autonomous(dev_path):
            logger.error("[pulse] failed to disable autonomous mode")
            return
        while not stop_event.is_set():
            set_color(dev_path, r, g, b, 255)
            time.sleep(max(0.01, interval/2))
            if stop_event.is_set():
                logger.info("[pulse] stop requested")
                return
            set_color(dev_path, r, g, b, 64)
            time.sleep(max(0.01, interval/2))
    except Exception as e:
        logger.exception(f"[pulse] unexpected error: {e}")
    finally:
        logger.info("[pulse] exit")

def wave(dev_path, base_color, interval, stop_event):
    logger.info(f"[wave] start, interval={interval}s, base={base_color}")
    r, g, b = base_color
    leds = 20
    try:
        if not disable_autonomous(dev_path):
            logger.error("[wave] failed to disable autonomous mode")
            return
        while not stop_event.is_set():
            for offset in range(leds):
                if stop_event.is_set():
                    logger.info("[wave] stop requested")
                    return
                for seg in range(leds):
                    intensity = int((math.sin((seg+offset)/leds * math.pi) ** 2) * 255)
                    set_color(dev_path, r, g, b, intensity, seg*5, seg*5+4)
                time.sleep(interval)
    except Exception as e:
        logger.exception(f"[wave] unexpected error: {e}")
    finally:
        logger.info("[wave] exit")

def spectrum(dev_path, interval, stop_event):
    logger.info(f"[spectrum] start, interval={interval}s")
    leds = 20
    try:
        if not disable_autonomous(dev_path):
            logger.error("[spectrum] failed to disable autonomous mode")
            return
        while not stop_event.is_set():
            for offset in range(leds):
                if stop_event.is_set():
                    logger.info("[spectrum] stop requested")
                    return
                for seg in range(leds):
                    hue = (seg + offset) / leds
                    phase_g = 2 * math.pi / 3
                    phase_b = 4 * math.pi / 3
                    r = int(255 * (math.sin(2 * math.pi * hue) * 0.5 + 0.5))
                    g = int(255 * (math.sin(2 * math.pi * hue + phase_g) * 0.5 + 0.5))
                    b = int(255 * (math.sin(2 * math.pi * hue + phase_b) * 0.5 + 0.5))
                    set_color(dev_path, r, g, b, 255, seg*5, seg*5+4)
                time.sleep(interval)
    except Exception as e:
        logger.exception(f"[spectrum] unexpected error: {e}")
    finally:
        logger.info("[spectrum] exit")

def fade(dev_path, base_color, interval, stop_event, target=(255, 255, 255)):
    logger.info(f"[fade] start, interval={interval}s, base={base_color}, target={target}")
    r1, g1, b1 = base_color
    r2, g2, b2 = target
    steps = max(60, int(120 * interval))
    try:
        if not disable_autonomous(dev_path):
            logger.error("[fade] failed to disable autonomous mode")
            return
        while not stop_event.is_set():
            for k in range(steps + 1):
                if stop_event.is_set():
                    logger.info("[fade] stop requested")
                    return
                t = k / steps
                r = int(r1 + (r2 - r1) * t)
                g = int(g1 + (g2 - g1) * t)
                b = int(b1 + (b2 - b1) * t)
                set_color(dev_path, r, g, b, 255)
                time.sleep(interval / steps)
    except Exception as e:
        logger.exception(f"[fade] unexpected error: {e}")
    finally:
        logger.info("[fade] exit")

def strobe(dev_path, base_color, interval, stop_event):
    logger.info(f"[strobe] start, interval={interval}s, base={base_color}")
    r, g, b = base_color
    on_time = max(0.005, interval / 4)
    off_time = on_time
    try:
        if not disable_autonomous(dev_path):
            logger.error("[strobe] failed to disable autonomous mode")
            return
        while not stop_event.is_set():
            set_color(dev_path, r, g, b, 255)
            time.sleep(on_time)
            if stop_event.is_set():
                logger.info("[strobe] stop requested")
                return
            set_color(dev_path, 0, 0, 0, 0)
            time.sleep(off_time)
    except Exception as e:
        logger.exception(f"[strobe] unexpected error: {e}")
    finally:
        logger.info("[strobe] exit")

def ripple(dev_path, base_color, interval, stop_event):
    """
    Ripple mode: keyboard stays at 20% brightness, random ripples simulate keystrokes
    Each ripple: 20% -> spike up 5% -> gradually fade back to 20%
    """
    logger.info(f"[ripple] start, base={base_color}, simulating keystroke ripples")
    r, g, b = base_color
    leds = 20
    base_intensity = int(0.20 * 255)  # 20% baseline
    ripple_boost = int(0.05 * 255)     # 5% boost per ripple

    import random

    # Track intensity per LED segment
    led_intensities = [base_intensity] * leds

    try:
        if not disable_autonomous(dev_path):
            logger.error("[ripple] failed to disable autonomous mode")
            return

        # Set initial baseline (20%)
        for seg in range(leds):
            set_color(dev_path, r, g, b, base_intensity, seg*5, seg*5+4)

        ripple_timer = 0
        while not stop_event.is_set():
            # Randomly trigger "keystrokes" (ripples)
            ripple_timer += interval
            if ripple_timer >= random.uniform(0.1, 0.5):  # Random keystroke frequency
                ripple_timer = 0
                # Pick random LED to "keystroke"
                keystroke_led = random.randint(0, leds - 1)
                # Boost that LED and neighbors
                for i in range(max(0, keystroke_led - 2), min(leds, keystroke_led + 3)):
                    distance = abs(i - keystroke_led)
                    boost = ripple_boost // (distance + 1)
                    led_intensities[i] = min(255, led_intensities[i] + boost)

            # Decay all LEDs back toward baseline
            for seg in range(leds):
                if led_intensities[seg] > base_intensity:
                    # Gradual decay
                    led_intensities[seg] = max(base_intensity, led_intensities[seg] - 2)
                set_color(dev_path, r, g, b, led_intensities[seg], seg*5, seg*5+4)

            if stop_event.is_set():
                logger.info("[ripple] stop requested")
                return

            time.sleep(interval)
    except Exception as e:
        logger.exception(f"[ripple] unexpected error: {e}")
    finally:
        logger.info("[ripple] exit")

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
            self.thread_state.emit("device_unavailable")
            return

        logger.info(f"Animator start: style={style_lower}, base={base_color}, interval={interval}s")

        if style_lower == "static":
            disable_autonomous(dev_path)
            set_color(dev_path, *base_color, 255)
            self.thread_state.emit("static_applied")
            return

        style_funcs = {
            "breathing": lambda: breathing(dev_path, base_color, interval, self.stop_event),
            "rainbow":   lambda: rainbow(dev_path, interval, self.stop_event),
            "flash":     lambda: flash(dev_path, base_color, interval, self.stop_event),
            "pulse":     lambda: pulse(dev_path, base_color, interval, self.stop_event),
            "wave":      lambda: wave(dev_path, base_color, interval, self.stop_event),
            "spectrum":  lambda: spectrum(dev_path, interval, self.stop_event),
            "fade":      lambda: fade(dev_path, base_color, interval, self.stop_event),
            "strobe":    lambda: strobe(dev_path, base_color, interval, self.stop_event),
            "ripple":    lambda: ripple(dev_path, base_color, interval, self.stop_event),
        }

        if style_lower in style_funcs:
            # Non-daemon thread so it continues after GUI closes
            self.thread = threading.Thread(target=style_funcs[style_lower], daemon=False, name=f"Anim-{style_lower}")
            self.thread.start()
            self.thread_state.emit("thread_started")
        else:
            logger.warning(f"Unknown style: {style_lower}")

    def stop(self):
        if self.thread and self.thread.is_alive():
            logger.info("Animator stop requested")
            self.stop_event.set()
            start = time.monotonic()
            self.thread.join(timeout=2.0)
            elapsed = time.monotonic() - start
            if self.thread.is_alive():
                logger.warning(f"Animation thread '{self.thread.name}' did not stop in {elapsed:.2f}s (possible hung thread)")
                self.thread_state.emit("thread_timeout")
            else:
                logger.info(f"Animation thread stopped in {elapsed:.2f}s")
                self.thread_state.emit("thread_stopped")
        self.thread = None

# --- Log console widget ---
class LogConsole(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Logs", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.setWidget(self.view)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.flush_logs)
        self.timer.start(100)

    def flush_logs(self):
        appended = False
        while True:
            try:
                line = log_queue.get_nowait()
            except queue.Empty:
                break
            self.view.appendPlainText(line)
            appended = True
        if appended:
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

# --- Main Window ---
class KeyboardLightingWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Settings
        self.settings = QSettings(ORG_NAME, APP_NAME)
        logger.info(f"Settings file: {self.settings.fileName()}")

        # Device path
        self.device_path = self.settings.value("device_path", DEFAULT_DEVICE_PATH, str)

        self.animator = AnimationController(lambda: self.device_path)
        self.animator.thread_state.connect(self.on_thread_state)

        # State
        self.current_color = [
            self.settings.value("color_r", 0, int),
            self.settings.value("color_g", 0, int),
            self.settings.value("color_b", 255, int),
        ]
        self.current_intensity = self.settings.value("intensity", 255, int)
        self.keep_on_exit = self.settings.value("keep_on_exit", False, bool)

        logger.info(f"Loaded settings: RGB({self.current_color[0]},{self.current_color[1]},{self.current_color[2]}) I={self.current_intensity} keep_on_exit={self.keep_on_exit}")

        presets_json = self.settings.value("user_presets_json", "[]", str)
        try:
            self.user_presets = json.loads(presets_json)
        except Exception:
            self.user_presets = []

        # Device availability
        self.device_available = os.path.exists(self.device_path)
        if self.device_available:
            logger.info(f"Device found: {self.device_path}")
        else:
            logger.warning(f"Device not found: {self.device_path}")

        # Watchdog
        self.last_heartbeat = time.monotonic()
        self.ui_heartbeat_timer = QTimer(self)
        self.ui_heartbeat_timer.timeout.connect(self.on_ui_heartbeat)
        self.ui_heartbeat_timer.start(int(WATCHDOG_INTERVAL_SEC * 1000))
        self.watchdog_stop = threading.Event()
        self.watchdog_thread = threading.Thread(target=self.watchdog_loop, daemon=True)
        self.watchdog_thread.start()

        self.init_ui()

        # Reapply static lighting at startup
        if self.device_available:
            try:
                disable_autonomous(self.device_path)
                r, g, b = self.current_color
                set_color(self.device_path, r, g, b, self.current_intensity)
                logger.info("Reapplied last saved static lighting on startup")
            except Exception as e:
                logger.exception(f"Failed to reapply lighting on startup: {e}")

    def init_ui(self):
        self.setWindowTitle("Keyboard Lighting Controller")
        self.setGeometry(100, 100, 720, 820)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout()
        central.setLayout(main_layout)

        title = QLabel("⌨️ Keyboard RGB Controller")
        title.setStyleSheet("font-size: 24px; font-weight: bold; padding: 10px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title)

        status_text = "● Connected" if self.device_available else "● Not Found"
        status_color = "green" if self.device_available else "red"
        self.status_label = QLabel(status_text)
        self.status_label.setStyleSheet(f"color: {status_color}; font-size: 12px; padding: 5px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.status_label)

        # Preview
        self.create_preview(main_layout)

        # Quick presets
        self.create_presets(main_layout)

        # Color wheel + Value slider + user presets bar
        self.create_color_wheel(main_layout)

        # Style selector (instant apply)
        self.create_style_selector(main_layout)

        # Speed control
        self.create_speed_control(main_layout)

        # Keep-on-exit
        self.keep_on_exit_checkbox = QCheckBox("Keep static lighting when app exits")
        self.keep_on_exit_checkbox.setChecked(self.keep_on_exit)
        self.keep_on_exit_checkbox.stateChanged.connect(self.on_keep_on_exit_changed)
        main_layout.addWidget(self.keep_on_exit_checkbox)

        # Control buttons
        self.create_control_buttons(main_layout)

        # Logs
        self.log_console = LogConsole(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_console)

        self.update_preview()

    def create_preview(self, layout):
        group = QGroupBox("Color preview")
        group_layout = QVBoxLayout()

        self.preview_label = QLabel()
        self.preview_label.setMinimumHeight(100)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("font-size: 14px; font-weight: bold; border: 2px solid #333;")

        group_layout.addWidget(self.preview_label)
        group.setLayout(group_layout)
        layout.addWidget(group)

    def create_presets(self, layout):
        group = QGroupBox("Quick presets")
        grid = QGridLayout()

        row, col = 0, 0
        for name, (r, g, b, i) in PRESETS.items():
            btn = QPushButton()
            btn.setFixedSize(30, 30)
            btn.setToolTip(name)
            bg_color = f"rgb({r}, {g}, {b})"
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg_color};
                    border: 1px solid #555;
                    border-radius: 4px;
                }}
                QPushButton:hover {{ border: 2px solid #fff; }}
            """)
            btn.clicked.connect(lambda checked=False, n=name: self.apply_preset(n))
            grid.addWidget(btn, row, col)
            col += 1
            if col >= 8:
                col, row = 0, row + 1

        group.setLayout(grid)
        layout.addWidget(group)

    def create_color_wheel(self, layout):
        group = QGroupBox("Color wheel and value")
        group_layout = QVBoxLayout()

        # Wheel (live) - centered
        wheel_container = QHBoxLayout()
        wheel_container.addStretch()
        self.color_wheel = ColorWheel()
        self.color_wheel.setRGB(*self.current_color)
        self.color_wheel.colorChanged.connect(self.on_wheel_changed)   # updates preview + state
        self.color_wheel.colorChanged.connect(self.send_live_color)     # instant HID update
        wheel_container.addWidget(self.color_wheel)
        wheel_container.addStretch()
        group_layout.addLayout(wheel_container)

        # Value (brightness) slider
        v_layout = QHBoxLayout()
        v_label = QLabel("Value")
        v_layout.addWidget(v_label)

        self.value_slider = QSlider(Qt.Orientation.Horizontal)
        self.value_slider.setMinimum(0)
        self.value_slider.setMaximum(255)
        self.value_slider.setValue(self.current_intensity)
        self.value_slider.valueChanged.connect(self.on_value_changed)
        v_layout.addWidget(self.value_slider)

        self.value_label = QLabel(str(self.current_intensity))
        self.value_label.setMinimumWidth(40)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        v_layout.addWidget(self.value_label)
        group_layout.addLayout(v_layout)

        # User preset bar
        bar_layout = QHBoxLayout()
        save_btn = QPushButton("Save preset")
        save_btn.clicked.connect(self.save_current_preset)
        bar_layout.addWidget(save_btn)

        self.preset_bar = QHBoxLayout()
        self.reload_user_presets_bar()
        group_layout.addLayout(bar_layout)
        group_layout.addLayout(self.preset_bar)

        group.setLayout(group_layout)
        layout.addWidget(group)

    def create_style_selector(self, layout):
        group = QGroupBox("Animation style")
        group_layout = QHBoxLayout()

        self.style_combo = QComboBox()
        self.style_combo.addItems(STYLES)
        self.style_combo.setMinimumHeight(35)
        self.style_combo.currentIndexChanged.connect(self.on_style_changed)  # instant apply
        group_layout.addWidget(self.style_combo)

        group.setLayout(group_layout)
        layout.addWidget(group)

    def create_speed_control(self, layout):
        group = QGroupBox("Animation speed")
        group_layout = QHBoxLayout()

        slow_label = QLabel("Slow")
        group_layout.addWidget(slow_label)

        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setMinimum(1)
        self.speed_slider.setMaximum(50)
        self.speed_slider.setValue(self.settings.value("speed_slider", 10, int))
        group_layout.addWidget(self.speed_slider)

        fast_label = QLabel("Fast")
        group_layout.addWidget(fast_label)

        group.setLayout(group_layout)
        layout.addWidget(group)

    def create_control_buttons(self, layout):
        btn_layout = QHBoxLayout()

        apply_btn = QPushButton("✓ Apply")
        apply_btn.setMinimumHeight(50)
        apply_btn.setStyleSheet("""
            QPushButton { background-color: #4CAF50; color: white; font-size: 16px; font-weight: bold; border-radius: 8px; }
            QPushButton:hover { background-color: #45a049; }
        """)
        apply_btn.clicked.connect(self.apply_lighting)
        btn_layout.addWidget(apply_btn)

        stop_btn = QPushButton("■ Stop")
        stop_btn.setMinimumHeight(50)
        stop_btn.setStyleSheet("""
            QPushButton { background-color: #f44336; color: white; font-size: 16px; font-weight: bold; border-radius: 8px; }
            QPushButton:hover { background-color: #da190b; }
        """)
        stop_btn.clicked.connect(self.stop_animation)
        btn_layout.addWidget(stop_btn)

        force_btn = QPushButton("⛔ Force Quit")
        force_btn.setMinimumHeight(50)
        force_btn.setStyleSheet("""
            QPushButton { background-color: #9c27b0; color: white; font-size: 16px; font-weight: bold; border-radius: 8px; }
            QPushButton:hover { background-color: #7b1fa2; }
        """)
        force_btn.clicked.connect(self.force_quit)
        btn_layout.addWidget(force_btn)

        layout.addLayout(btn_layout)

    # --- Callbacks ---
    def send_live_color(self, r, g, b):
        if not self.device_available:
            return
        disable_autonomous(self.device_path)
        set_color(self.device_path, r, g, b, self.current_intensity)

    def on_thread_state(self, state: str):
        logger.debug(f"Animator state: {state}")

    def on_style_changed(self, idx):
        self.apply_lighting()

    def on_wheel_changed(self, r, g, b):
        self.current_color = [r, g, b]
        self.update_preview()
        self.persist_state()

    def on_value_changed(self, value):
        self.current_intensity = value
        self.value_label.setText(str(value))
        # Sync wheel's V (for visual)
        self.color_wheel.setHSV(self.color_wheel.h, self.color_wheel.s, value / 255.0)
        self.update_preview()
        self.persist_state()
        # Reapply live color
        if self.device_available:
            r, g, b = self.current_color
            disable_autonomous(self.device_path)
            set_color(self.device_path, r, g, b, self.current_intensity)

    def on_keep_on_exit_changed(self, state):
        self.keep_on_exit = (state == Qt.CheckState.Checked)
        self.persist_state()

    def update_preview(self):
        r, g, b = self.current_color
        intensity_factor = self.current_intensity / 255
        dr = int(r * intensity_factor)
        dg = int(g * intensity_factor)
        db = int(b * intensity_factor)
        color = QColor(dr, dg, db)
        palette = self.preview_label.palette()
        palette.setColor(QPalette.ColorRole.Window, color)
        self.preview_label.setAutoFillBackground(True)
        self.preview_label.setPalette(palette)

        text_color = "white" if (r + g + b) / 3 < 128 else "black"
        self.preview_label.setText(f"RGB({r}, {g}, {b})\nBrightness: {self.current_intensity}")
        self.preview_label.setStyleSheet(f"""
            color: {text_color};
            font-size: 14px;
            font-weight: bold;
            border: 2px solid #333;
        """)

    def apply_preset(self, name):
        r, g, b, i = PRESETS[name]
        logger.info(f"Preset applied: {name} -> RGB({r},{g},{b}) I({i})")
        self.current_color = [r, g, b]
        self.current_intensity = i
        self.color_wheel.setRGB(r, g, b)
        self.value_slider.setValue(i)
        self.update_preview()
        self.persist_state()
        self.apply_lighting()

    def apply_lighting(self):
        if not self.device_available:
            QMessageBox.warning(self, "Device Error",
                                f"Device not found at {self.device_path}\n\nInstall udev rule and replug your keyboard.")
            logger.warning("Apply aborted: device not available")
            return
        try:
            style = self.style_combo.currentText()
            interval = self.speed_slider.value() / 100
            base_color = tuple(self.current_color)
            logger.info(f"Apply: style={style}, base={base_color}, interval={interval}s, intensity={self.current_intensity}")
            self.settings.setValue("speed_slider", self.speed_slider.value())
            self.settings.sync()
            self.animator.start(style, base_color, interval)
        except Exception as e:
            logger.exception(f"Failed to apply lighting: {e}")
            QMessageBox.critical(self, "Error", f"Failed to apply lighting:\n{str(e)}")

    def stop_animation(self):
        logger.info("Stop animation requested")
        self.animator.stop()
        if self.device_available:
            disable_autonomous(self.device_path)
            set_color(self.device_path, 0, 0, 0, 0)

    def force_quit(self):
        logger.critical("Force quit invoked by user")
        os._exit(1)

    # --- User presets (JSON) ---
    def save_current_preset(self):
        r, g, b = self.current_color
        i = self.current_intensity
        preset = {"r": r, "g": g, "b": b, "i": i}
        # Deduplicate
        self.user_presets = [p for p in self.user_presets if not (
            p.get("r") == r and p.get("g") == g and p.get("b") == b and p.get("i") == i)]
        self.user_presets.insert(0, preset)
        self.user_presets = self.user_presets[:16]
        self.settings.setValue("user_presets_json", json.dumps(self.user_presets))
        self.settings.sync()
        self.reload_user_presets_bar()
        logger.info(f"Saved user preset RGB({r},{g},{b}) I({i})")

    def delete_user_preset(self, preset):
        self.user_presets = [p for p in self.user_presets if p != preset]
        self.settings.setValue("user_presets_json", json.dumps(self.user_presets))
        self.settings.sync()
        self.reload_user_presets_bar()
        logger.info("Deleted user preset")

    def reload_user_presets_bar(self):
        # Clear existing widgets
        while self.preset_bar.count():
            item = self.preset_bar.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()

        for p in self.user_presets:
            r, g, b, i = p["r"], p["g"], p["b"], p["i"]
            btn = QPushButton()
            btn.setFixedSize(24, 24)
            btn.setToolTip(f"RGB({r},{g},{b}) I({i})")
            btn.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid #555; border-radius: 4px;")
            btn.clicked.connect(lambda checked=False, preset=p: self.apply_user_preset(preset))
            # Right-click delete
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, preset=p: self.delete_user_preset(preset))
            self.preset_bar.addWidget(btn)

    def apply_user_preset(self, preset):
        r, g, b, i = preset["r"], preset["g"], preset["b"], preset["i"]
        self.current_color = [r, g, b]
        self.current_intensity = i
        self.value_slider.setValue(i)
        self.color_wheel.setRGB(r, g, b)
        self.update_preview()
        self.persist_state()
        self.apply_lighting()

    # --- Persistence ---
    def persist_state(self):
        r, g, b = self.current_color
        self.settings.setValue("device_path", self.device_path)
        self.settings.setValue("color_r", r)
        self.settings.setValue("color_g", g)
        self.settings.setValue("color_b", b)
        self.settings.setValue("intensity", self.current_intensity)
        self.settings.setValue("keep_on_exit", self.keep_on_exit)
        self.settings.sync()
        logger.debug(f"Settings saved: RGB({r},{g},{b}) I={self.current_intensity} device={self.device_path}")

    # --- Watchdog ---
    def on_ui_heartbeat(self):
        self.last_heartbeat = time.monotonic()

    def watchdog_loop(self):
        logger.info("Watchdog thread started")
        while not self.watchdog_stop.is_set():
            now = time.monotonic()
            gap = now - self.last_heartbeat
            if gap > WATCHDOG_STALL_THRESHOLD_SEC:
                logger.error(f"UI heartbeat stalled for {gap:.2f}s (event loop may be blocked)")
            time.sleep(WATCHDOG_INTERVAL_SEC)

    def closeEvent(self, event):
        logger.info("Window closing...")
        try:
            self.persist_state()
            if self.keep_on_exit and self.device_available:
                if not self.animator.thread or not self.animator.thread.is_alive():
                    # Only set static color if no animation is running
                    disable_autonomous(self.device_path)
                    r, g, b = self.current_color
                    set_color(self.device_path, r, g, b, self.current_intensity)
                    logger.info("Kept static lighting on exit")
                else:
                    logger.info("Animation will continue running after GUI close")
            else:
                # User doesn't want to keep lighting, stop animation
                self.animator.stop()
        finally:
            self.watchdog_stop.set()
            event.accept()

# --- Main ---
def main():
    app = QApplication(sys.argv)

    # Dark theme
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
    app.setPalette(palette)

    window = KeyboardLightingWindow()
    window.show()

    logger.info("Application started")
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
