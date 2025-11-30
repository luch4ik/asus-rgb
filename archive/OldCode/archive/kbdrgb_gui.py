#!/usr/bin/env python3
"""
kbdrgb_gui.py — Fully fledged UI for keyboard RGB control

Features:
- HID detection and device selection (/dev/hidraw*)
- Command test: send raw feature report
- True circular HSV color wheel (hover preview; drag/click apply live)
- Brightness (value) slider synced with wheel
- Presets + user presets
- Animations: static, breathing, rainbow, flash, pulse, wave, spectrum, fade, strobe
- Animation speed control
- Alert: flash red on new Thunderbird mail via org.freedesktop.Notifications
- Debounced HID updates (~120 Hz) for smooth dragging

Note:
- You may need udev permissions for /dev/hidraw* access.
- Set environment KBDRGB_HID to a specific path to preselect the device.
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
import subprocess
from enum import IntEnum
from typing import Optional, Tuple, List

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QComboBox, QGroupBox, QGridLayout,
    QMessageBox, QPlainTextEdit, QDockWidget, QCheckBox, QSizePolicy, QLineEdit,
    QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QObject, QSettings, QPointF, QSize, QRectF
)
from PyQt6.QtGui import (
    QColor, QPalette, QPainter, QPen, QImage, QGuiApplication, QPainterPath,
    QIcon, QPixmap, QAction
)

# -----------------------------------------------------------------------------
# HID Constants
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# Config and logging
# -----------------------------------------------------------------------------

APP_NAME = "kbdrgb"
ORG_NAME = "kbdrgb"
DEFAULT_DEVICE_PATH = os.environ.get("KBDRGB_HID", "/dev/hidraw1")
WATCHDOG_INTERVAL_SEC = 0.5
WATCHDOG_STALL_THRESHOLD_SEC = 2.0

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

# -----------------------------------------------------------------------------
# HID helpers
# -----------------------------------------------------------------------------

def HIDIOCSFEATURE(length):
    """Calculate IOCTL command for HID feature report"""
    return HIDConstants.IOCTL_BASE | (length << 16)

def send_feature_report(dev_path: str, report_id: int, data: List[int]) -> bool:
    """
    Send HID feature report to device
    Returns True on success, False on failure
    """
    if not dev_path or not os.path.exists(dev_path):
        logger.error(f"Device path invalid or doesn't exist: {dev_path}")
        return False

    packet = bytes([report_id]) + bytes(data)
    fd = None
    try:
        fd = os.open(dev_path, os.O_RDWR)
        fcntl.ioctl(fd, HIDIOCSFEATURE(len(packet)), packet)
        return True
    except OSError as e:
        logger.error(f"HID error (id=0x{report_id:02X}): {e}")
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
        time.sleep(0.005)
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

def enumerate_hidraw() -> List[Tuple[str, str]]:
    """
    Returns list of (path, label) for hidraw devices. Label includes vendor/product if detectable.
    """
    devices = []
    base = "/dev"
    for name in sorted(os.listdir(base)):
        if not name.startswith("hidraw"):
            continue
        path = os.path.join(base, name)
        label = name
        try:
            # Try to read uevent to get ID info
            uevent = f"/sys/class/hidraw/{name}/device/uevent"
            if os.path.exists(uevent):
                with open(uevent, "r") as f:
                    txt = f.read()
                # Extract HID_ID and HID_NAME
                hid_id = ""
                hid_name = ""
                for line in txt.splitlines():
                    if line.startswith("HID_ID="):
                        hid_id = line.split("=", 1)[1].strip()
                    elif line.startswith("HID_NAME="):
                        hid_name = line.split("=", 1)[1].strip()
                if hid_name or hid_id:
                    label = f"{name}  {hid_name or ''} {hid_id or ''}".strip()
        except Exception:
            pass
        devices.append((path, label))
    return devices

# Debounced HID sender to keep UI smooth
class LiveSender(QObject):
    def __init__(self, get_dev_path_callable, parent=None):
        super().__init__(parent)
        self._last = (None, None, None, None)
        self._pending = None
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / 120))  # ~120 Hz
        self._timer.timeout.connect(self._flush)
        self._timer.start()
        self._get_dev_path = get_dev_path_callable

    def queue(self, r, g, b, i):
        self._pending = (int(r), int(g), int(b), int(i))

    def _flush(self):
        if self._pending and self._pending != self._last:
            r, g, b, i = self._pending
            dev_path = self._get_dev_path()
            if not dev_path or not os.path.exists(dev_path):
                return
            try:
                disable_autonomous(dev_path)
                set_color(dev_path, r, g, b, i)
                self._last = self._pending
            except Exception as e:
                logger.error(f"Live HID update failed: {e}")

# -----------------------------------------------------------------------------
# Presets and styles
# -----------------------------------------------------------------------------

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
    "Ripple"  # delegated externally
]

# -----------------------------------------------------------------------------
# Color wheel widget
# -----------------------------------------------------------------------------

class ColorWheel(QWidget):
    colorChanged = pyqtSignal(int, int, int)    # click/drag apply (live)
    previewChanged = pyqtSignal(int, int, int)  # hover preview (no HID)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.base_diameter = 250

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(self.base_diameter, self.base_diameter)
        self.setMaximumSize(self.base_diameter, self.base_diameter)

        self.h = 0.0
        self.s = 0.0
        self.v = 1.0

        self.setMouseTracking(True)
        self._wheel_img = None
        self._image_cache = {}  # Cache wheel images at different sizes
        self._regen_image(self.base_diameter)

    def sizeHint(self):
        return QSize(self.base_diameter, self.base_diameter)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return w

    def resizeEvent(self, e):
        side = min(self.width(), self.height())
        if not self._wheel_img or abs(side - self._wheel_img.width()) > 4:
            self._regen_image(side)
        super().resizeEvent(e)

    def _regen_image(self, size):
        size = max(120, int(size))

        # Check cache first - round size to nearest 10 pixels for better cache hits
        cache_size = (size // 10) * 10
        if cache_size in self._image_cache:
            self._wheel_img = self._image_cache[cache_size]
            logger.debug(f"ColorWheel: using cached image at size {cache_size}")
            return

        # Generate new image
        logger.debug(f"ColorWheel: generating new image at size {size}")
        img = QImage(size, size, QImage.Format.Format_RGB32)
        cx, cy = size / 2.0, size / 2.0
        radius = size / 2.0
        for x in range(size):
            for y in range(size):
                dx = x - cx
                dy = y - cy
                dist = math.hypot(dx, dy)
                if dist <= radius:
                    hue_deg = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
                    sat = dist / radius
                    c = QColor.fromHsvF(hue_deg / 360.0, sat, 1.0)
                    img.setPixel(x, y, c.rgb())
                else:
                    img.setPixel(x, y, QColor(53, 53, 53).rgb())

        # Cache the image (limit cache to 5 sizes)
        if len(self._image_cache) >= 5:
            # Remove smallest cached image
            smallest = min(self._image_cache.keys())
            del self._image_cache[smallest]
            logger.debug(f"ColorWheel: evicted cache entry at size {smallest}")

        self._image_cache[cache_size] = img
        self._wheel_img = img

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        x0 = (self.width() - side) / 2
        y0 = (self.height() - side) / 2
        target = QRectF(x0, y0, side, side)
        source = QRectF(0, 0, self._wheel_img.width(), self._wheel_img.height())
        p.drawImage(target, self._wheel_img, source)
        path = QPainterPath()
        path.addEllipse(target)
        p.setClipPath(path)
        cx, cy = target.center().x(), target.center().y()
        radius = target.width() / 2.0
        angle = 2.0 * math.pi * self.h
        rr = self.s * radius
        x = cx + rr * math.cos(angle)
        y = cy + rr * math.sin(angle)
        p.setPen(QPen(Qt.GlobalColor.white, 2))
        p.setBrush(QColor(0, 0, 0, 160))
        p.drawEllipse(QPointF(x, y), 6, 6)

    def mouseMoveEvent(self, e):
        inside, h, s = self._hs_from_event(e)
        if not inside:
            return
        c = QColor.fromHsvF(h, s, self.v)
        self.previewChanged.emit(c.red(), c.green(), c.blue())
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.h, self.s = h, s
            self._emitColor()
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            inside, h, s = self._hs_from_event(e)
            if inside:
                self.h, self.s = h, s
                self._emitColor()
                self.update()

    def mouseDoubleClickEvent(self, e):
        self.mousePressEvent(e)

    def _hs_from_event(self, e):
        side = min(self.width(), self.height())
        x0 = (self.width() - side) / 2
        y0 = (self.height() - side) / 2
        cx = x0 + side / 2.0
        cy = y0 + side / 2.0
        dx = e.position().x() - cx
        dy = e.position().y() - cy
        dist = math.hypot(dx, dy)
        radius = side / 2.0
        if dist > radius:
            return False, self.h, self.s
        angle = math.atan2(dy, dx)
        h = ((math.degrees(angle) + 360.0) % 360.0) / 360.0
        s = dist / radius
        return True, h, s

    def setHSV(self, h: float, s: float, v: float):
        self.h = max(0.0, min(1.0, h))
        self.s = max(0.0, min(1.0, s))
        self.v = max(0.0, min(1.0, v))
        self._emitColor()
        self.update()

    def setRGB(self, r: int, g: int, b: int):
        c = QColor(r, g, b)
        hue = c.hueF()
        self.h = hue if hue >= 0.0 else 0.0
        self.s = c.saturationF()
        self.v = c.valueF()
        self._emitColor()
        self.update()

    def _emitColor(self):
        c = QColor.fromHsvF(self.h, self.s, self.v)
        self.colorChanged.emit(c.red(), c.green(), c.blue())

# -----------------------------------------------------------------------------
# Animations
# -----------------------------------------------------------------------------

def breathing(dev_path, base_color, interval, stop_event):
    logger.info(f"[breathing] start, period={interval}s, base={base_color}")
    r, g, b = base_color
    steps = max(90, int(120 * interval))
    try:
        if not disable_autonomous(dev_path):
            logger.error("[breathing] failed to disable autonomous mode")
            return
        while not stop_event.is_set():
            for k in range(steps):
                if stop_event.is_set():
                    logger.info("[breathing] stop requested")
                    return
                phase = (2 * math.pi) * (k / steps)
                intensity = int(((1 - math.cos(phase)) * 0.5) * 255)
                set_color(dev_path, r, g, b, intensity)
                time.sleep(max(0.002, interval / steps))
    except Exception as e:
        logger.exception(f"[breathing] unexpected error: {e}")
    finally:
        set_color(dev_path, r, g, b, 255)
        logger.info("[breathing] exit")

def rainbow(dev_path, interval, stop_event):
    logger.info(f"[rainbow] start, interval={interval}s")
    steps = 360
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
    finally:
        logger.info("[rainbow] exit")

def flash(dev_path, base_color, interval, stop_event):
    logger.info(f"[flash] start")
    r, g, b = base_color
    try:
        if not disable_autonomous(dev_path):
            return
        while not stop_event.is_set():
            set_color(dev_path, r, g, b, 255)
            time.sleep(interval)
            if stop_event.is_set():
                return
            set_color(dev_path, 0, 0, 0, 0)
            time.sleep(interval)
    except Exception as e:
        logger.exception(f"[flash] error: {e}")
    finally:
        logger.info("[flash] exit")

def pulse(dev_path, base_color, interval, stop_event):
    r, g, b = base_color
    try:
        if not disable_autonomous(dev_path):
            return
        while not stop_event.is_set():
            set_color(dev_path, r, g, b, 255)
            time.sleep(max(0.01, interval/2))
            if stop_event.is_set():
                return
            set_color(dev_path, r, g, b, 64)
            time.sleep(max(0.01, interval/2))
    except Exception as e:
        logger.exception(f"[pulse] error: {e}")

def wave(dev_path, base_color, interval, stop_event):
    r, g, b = base_color
    leds = 20
    try:
        if not disable_autonomous(dev_path):
            return
        while not stop_event.is_set():
            for offset in range(leds):
                if stop_event.is_set():
                    return
                for seg in range(leds):
                    intensity = int((math.sin((seg + offset)/leds * math.pi) ** 2) * 255)
                    set_color(dev_path, r, g, b, intensity, seg*5, seg*5+4)
                time.sleep(interval)
    except Exception as e:
        logger.exception(f"[wave] error: {e}")

def spectrum(dev_path, interval, stop_event):
    leds = 20
    try:
        if not disable_autonomous(dev_path):
            return
        while not stop_event.is_set():
            for offset in range(leds):
                if stop_event.is_set():
                    return
                for seg in range(leds):
                    hue = (seg + offset) / leds
                    c = QColor.fromHsvF(hue, 1.0, 1.0)
                    set_color(dev_path, c.red(), c.green(), c.blue(), 255, seg*5, seg*5+4)
                time.sleep(interval)
    except Exception as e:
        logger.exception(f"[spectrum] error: {e}")

def fade(dev_path, base_color, interval, stop_event, target=(255, 255, 255)):
    r1, g1, b1 = base_color
    r2, g2, b2 = target
    steps = max(60, int(120 * interval))
    try:
        if not disable_autonomous(dev_path):
            return
        while not stop_event.is_set():
            for k in range(steps+1):
                if stop_event.is_set():
                    return
                t = k / steps
                r = int(r1 + (r2 - r1) * t)
                g = int(g1 + (g2 - g1) * t)
                b = int(b1 + (b2 - b1) * t)
                set_color(dev_path, r, g, b, 255)
                time.sleep(interval/steps)
    except Exception as e:
        logger.exception(f"[fade] error: {e}")

def strobe(dev_path, base_color, interval, stop_event):
    r, g, b = base_color
    on_time = max(0.005, interval / 4)
    off_time = on_time
    try:
        if not disable_autonomous(dev_path):
            return
        while not stop_event.is_set():
            set_color(dev_path, r, g, b, 255)
            time.sleep(on_time)
            if stop_event.is_set():
                return
            set_color(dev_path, 0, 0, 0, 0)
            time.sleep(off_time)
    except Exception as e:
        logger.exception(f"[strobe] error: {e}")

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
            return

        # Set initial baseline (20%)
        for seg in range(leds):
            set_color(dev_path, r, g, b, base_intensity, seg*5, seg*5+4)

        ripple_timer = 0
        while not stop_event.is_set():
            # Randomly trigger "keystrokes" (ripples)
            ripple_timer += interval
            if ripple_timer >= random.uniform(0.1, 0.5):
                ripple_timer = 0
                keystroke_led = random.randint(0, leds - 1)
                for i in range(max(0, keystroke_led - 2), min(leds, keystroke_led + 3)):
                    distance = abs(i - keystroke_led)
                    boost = ripple_boost // (distance + 1)
                    led_intensities[i] = min(255, led_intensities[i] + boost)

            # Decay all LEDs back toward baseline
            for seg in range(leds):
                if led_intensities[seg] > base_intensity:
                    led_intensities[seg] = max(base_intensity, led_intensities[seg] - 2)
                set_color(dev_path, r, g, b, led_intensities[seg], seg*5, seg*5+4)

            if stop_event.is_set():
                return

            time.sleep(interval)
    except Exception as e:
        logger.exception(f"[ripple] error: {e}")

class AnimationController(QObject):
    state = pyqtSignal(str)

    def __init__(self, get_dev_path_callable):
        super().__init__()
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._get_dev_path = get_dev_path_callable

    def start(self, style: str, base_color: Tuple[int, int, int], interval: float):
        self.stop()
        self.stop_event.clear()
        s = style.lower()
        dev_path = self._get_dev_path()
        if not dev_path or not os.path.exists(dev_path):
            self.state.emit("device_unavailable")
            return

        if s == "static":
            disable_autonomous(dev_path)
            set_color(dev_path, *base_color, 255)
            self.state.emit("static_set")
            return

        funcs = {
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

        if s in funcs:
            # Non-daemon thread so it continues after GUI closes
            self.thread = threading.Thread(target=funcs[s], daemon=False, name=f"Anim-{s}")
            self.thread.start()
            self.state.emit("animation_started")
        else:
            self.state.emit("unknown_style")

    def stop(self):
        if self.thread and self.thread.is_alive():
            logger.info("Animator stop requested")
            self.stop_event.set()
            start = time.monotonic()
            self.thread.join(timeout=2.0)
            elapsed = time.monotonic() - start
            if self.thread.is_alive():
                logger.warning(f"Animation thread '{self.thread.name if hasattr(self.thread, 'name') else 'unknown'}' did not stop in {elapsed:.2f}s (possible hung thread)")
                self.state.emit("thread_timeout")
            else:
                logger.info(f"Animation thread stopped in {elapsed:.2f}s")
                self.state.emit("thread_stopped")
        self.thread = None

# -----------------------------------------------------------------------------
# Red alert on Thunderbird new mail (org.freedesktop.Notifications)
# -----------------------------------------------------------------------------

class ThunderbirdNotifier(QObject):
    """
    Uses `dbus-monitor --session` to listen for org.freedesktop.Notifications,
    and triggers a callback when a Thunderbird notification appears.
    """
    alert = pyqtSignal()

    def __init__(self, enabled_callable, parent=None):
        super().__init__(parent)
        self._enabled = enabled_callable
        self._proc = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._proc = subprocess.Popen(
                ["dbus-monitor", "--session", "interface='org.freedesktop.Notifications'"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            # Very simple heuristic: look for "string \"Thunderbird\"" and "New Mail" or "mail"
            for line in self._proc.stdout:
                if not self._enabled():
                    continue
                ln = line.strip()
                if "Thunderbird" in ln and ("New Mail" in ln or "mail" in ln.lower()):
                    self.alert.emit()
        except Exception as e:
            logger.error(f"Thunderbird notifier error: {e}")

# -----------------------------------------------------------------------------
# Log console
# -----------------------------------------------------------------------------

class LogConsole(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Logs", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.setWidget(self.view)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.flush)
        self.timer.start(100)

    def flush(self):
        appended = False
        while True:
            try:
                line = log_queue.get_nowait()
            except queue.Empty:
                break
            self.view.appendPlainText(line)
            appended = True
        if appended:
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

# -----------------------------------------------------------------------------
# Main window
# -----------------------------------------------------------------------------

class KeyboardLightingWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.settings = QSettings(ORG_NAME, APP_NAME)

        # Device selection
        self.devices = enumerate_hidraw()
        self.dev_path = self.settings.value("device_path", DEFAULT_DEVICE_PATH, str)
        if not any(p == self.dev_path for p, _ in self.devices):
            # Fallback to first detected
            if self.devices:
                self.dev_path = self.devices[0][0]

        # State
        self.current_color = [
            self.settings.value("color_r", 215, int),
            self.settings.value("color_g", 156, int),
            self.settings.value("color_b", 255, int),
        ]
        self.current_intensity = self.settings.value("intensity", 255, int)
        self.keep_on_exit = self.settings.value("keep_on_exit", True, bool)
        self.tb_alert_enabled = self.settings.value("tb_alert_enabled", False, bool)

        presets_json = self.settings.value("user_presets_json", "[]", str)
        try:
            self.user_presets = json.loads(presets_json)
        except Exception:
            self.user_presets = []

        # Controllers
        self.live_sender = LiveSender(lambda: self.dev_path, self)
        self.animator = AnimationController(lambda: self.dev_path)
        self.animator.state.connect(lambda s: logger.info(f"Animator: {s}"))

        # Red alert manager
        self.alert_active = False
        self.alert_timer = QTimer(self)
        self.alert_timer.timeout.connect(self._alert_tick)
        self.alert_phase = 0
        self.tb_notifier = ThunderbirdNotifier(lambda: self.tb_alert_enabled, self)
        self.tb_notifier.alert.connect(self.trigger_red_alert)

        # Watchdog
        self.last_heartbeat = time.monotonic()
        self.ui_heartbeat_timer = QTimer(self)
        self.ui_heartbeat_timer.timeout.connect(self.on_heartbeat)
        self.ui_heartbeat_timer.start(int(WATCHDOG_INTERVAL_SEC * 1000))
        self.watchdog_stop = threading.Event()
        self.watchdog_thread = threading.Thread(target=self.watchdog_loop, daemon=True)
        self.watchdog_thread.start()

        # Hotplug detection
        self.hotplug_timer = QTimer(self)
        self.hotplug_timer.timeout.connect(self.check_device_hotplug)
        self.hotplug_timer.start(2000)  # Check every 2 seconds

        self.init_ui()
        self.init_system_tray()

        # Initial apply
        if self.dev_path and os.path.exists(self.dev_path):
            try:
                disable_autonomous(self.dev_path)
                r, g, b = self.current_color
                set_color(self.dev_path, r, g, b, self.current_intensity)
            except Exception as e:
                logger.error(f"Startup apply failed: {e}")

    # --- UI setup ---
    def init_ui(self):
        self.setWindowTitle("Keyboard Lighting Controller")
        self.apply_dark_theme()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout()
        central.setLayout(layout)

        # Title + status
        title = QLabel("Keyboard RGB Controller")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold; padding: 8px;")
        layout.addWidget(title)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        self.update_status()

        # Preview label
        self.preview_label = QLabel("RGB Preview")
        self.preview_label.setMinimumHeight(60)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("border: 2px solid #333; font-weight: bold; padding: 10px;")
        layout.addWidget(self.preview_label)

        # --- CENTRAL COLOR WHEEL ---
        self.color_wheel = ColorWheel()
        self.color_wheel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.color_wheel.setRGB(*self.current_color)
        self.color_wheel.previewChanged.connect(self.on_hover_preview)
        self.color_wheel.colorChanged.connect(self.on_wheel_changed)
        self.color_wheel.colorChanged.connect(self.send_live_color)
        layout.addWidget(self.color_wheel, stretch=1)  # give it priority space

        # Brightness slider directly under wheel
        hbox = QHBoxLayout()
        hbox.addWidget(QLabel("Brightness"))
        self.value_slider = QSlider(Qt.Orientation.Horizontal)
        self.value_slider.setRange(0, 255)
        self.value_slider.setValue(self.current_intensity)
        self.value_slider.valueChanged.connect(self.on_value_changed)
        hbox.addWidget(self.value_slider)
        self.value_label = QLabel(str(self.current_intensity))
        hbox.addWidget(self.value_label)
        layout.addLayout(hbox)

        # User presets bar (save button + preset buttons)
        save_bar = QHBoxLayout()
        save_btn = QPushButton("Save preset")
        save_btn.clicked.connect(self.save_current_preset)
        save_bar.addWidget(save_btn)
        self.user_presets_bar = QHBoxLayout()
        self.reload_user_presets_bar()
        save_bar.addLayout(self.user_presets_bar)
        layout.addLayout(save_bar)

        # Other controls flow below
        layout.addWidget(self.build_device_group())
        layout.addWidget(self.build_presets_group())
        layout.addWidget(self.build_style_group())
        layout.addWidget(self.build_speed_group())
        layout.addWidget(self.build_alert_group())

        # Keep on exit checkbox
        self.keep_on_exit_checkbox = QCheckBox("Keep static lighting when app exits")
        self.keep_on_exit_checkbox.setChecked(self.keep_on_exit)
        self.keep_on_exit_checkbox.stateChanged.connect(self.on_keep_on_exit_changed)
        layout.addWidget(self.keep_on_exit_checkbox)

        layout.addLayout(self.build_control_buttons())

        # Add log console
        self.log_console = LogConsole(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_console)

        # Update preview with current color
        self.update_preview(*self.current_color)

    def init_system_tray(self):
        """Initialize system tray icon with quick access menu"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray is not available on this system")
            return

        # Create tray icon
        icon_pixmap = QPixmap(32, 32)
        icon_pixmap.fill(QColor(42, 130, 218))
        self.tray_icon = QSystemTrayIcon(QIcon(icon_pixmap), self)

        # Create tray menu
        tray_menu = QMenu()

        # Quick presets submenu
        presets_menu = tray_menu.addMenu("Quick Presets")
        from_presets = PRESETS.items()
        for name, (r, g, b, i) in list(from_presets)[:8]:  # Limit to first 8
            action = QAction(name, self)
            action.triggered.connect(lambda checked=False, n=name: self.apply_preset(n))
            presets_menu.addAction(action)

        tray_menu.addSeparator()

        # Show/Hide window
        show_action = QAction("Show Window", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)

        hide_action = QAction("Hide Window", self)
        hide_action.triggered.connect(self.hide)
        tray_menu.addAction(hide_action)

        tray_menu.addSeparator()

        # Stop animation
        stop_action = QAction("Stop Animation", self)
        stop_action.triggered.connect(self.stop_animation)
        tray_menu.addAction(stop_action)

        tray_menu.addSeparator()

        # Quit
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()
        logger.info("System tray icon initialized")

    def on_tray_activated(self, reason):
        """Handle tray icon click"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Single click - toggle window visibility
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.activateWindow()

    def apply_dark_theme(self):
        self.setStyleSheet("")
        app = QApplication.instance()
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

    def update_status(self):
        ok = self.dev_path and os.path.exists(self.dev_path)
        status_text = f"● {'Connected' if ok else 'Not Found'} ({self.dev_path or 'none'})"
        status_color = "lime" if ok else "red"
        self.status_label.setText(status_text)
        self.status_label.setStyleSheet(f"color: {status_color}; font-size: 12px;")

    def build_device_group(self):
        group = QGroupBox("Device and command test")
        v = QVBoxLayout()

        # Device selector
        h1 = QHBoxLayout()
        h1.addWidget(QLabel("HID device"))
        self.device_combo = QComboBox()
        for path, label in self.devices:
            self.device_combo.addItem(label, path)
        # Preselect current
        idx = max(0, self.device_combo.findData(self.dev_path))
        self.device_combo.setCurrentIndex(idx)
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        h1.addWidget(self.device_combo)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_devices)
        h1.addWidget(refresh_btn)
        v.addLayout(h1)

        # Command test
        h2 = QHBoxLayout()
        h2.addWidget(QLabel("Report ID"))
        self.test_id = QLineEdit("0x05")
        self.test_id.setMaximumWidth(80)
        h2.addWidget(self.test_id)
        h2.addWidget(QLabel("Data (hex, space-separated)"))
        self.test_data = QLineEdit("01 00 00 64 00 FF 00 00 FF")
        h2.addWidget(self.test_data)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self.on_send_test)
        h2.addWidget(send_btn)
        v.addLayout(h2)

        group.setLayout(v)
        return group

    def build_presets_group(self):
        group = QGroupBox("Quick presets")
        grid = QGridLayout()
        row, col = 0, 0
        for name, (r, g, b, i) in PRESETS.items():
            btn = QPushButton(name)
            btn.setFixedHeight(30)
            btn.setStyleSheet(f"background-color: rgb({r},{g},{b}); color: {'black' if (r+g+b)/3>128 else 'white'};")
            btn.clicked.connect(lambda checked=False, n=name: self.apply_preset(n))
            grid.addWidget(btn, row, col)
            col += 1
            if col >= 4:
                col = 0
                row += 1
        group.setLayout(grid)
        return group


    def build_style_group(self):
        group = QGroupBox("Animation style")
        h = QHBoxLayout()
        self.style_combo = QComboBox()
        self.style_combo.addItems(STYLES)
        self.style_combo.currentIndexChanged.connect(self.on_style_changed)
        h.addWidget(self.style_combo)
        group.setLayout(h)
        return group

    def build_speed_group(self):
        group = QGroupBox("Animation speed")
        h = QHBoxLayout()
        h.addWidget(QLabel("Slow"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(1, 50)
        self.speed_slider.setValue(self.settings.value("speed_slider", 10, int))
        h.addWidget(self.speed_slider)
        h.addWidget(QLabel("Fast"))
        group.setLayout(h)
        return group

    def build_alert_group(self):
        group = QGroupBox("Alerts")
        h = QHBoxLayout()
        self.tb_alert_checkbox = QCheckBox("Flash red on new Thunderbird mail")
        self.tb_alert_checkbox.setChecked(self.tb_alert_enabled)
        self.tb_alert_checkbox.stateChanged.connect(self.on_tb_alert_changed)
        h.addWidget(self.tb_alert_checkbox)
        self.alert_duration_slider = QSlider(Qt.Orientation.Horizontal)
        self.alert_duration_slider.setRange(1, 30)
        self.alert_duration_slider.setValue(self.settings.value("alert_duration", 8, int))
        h.addWidget(QLabel("Duration"))
        h.addWidget(self.alert_duration_slider)
        self.alert_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.alert_speed_slider.setRange(1, 50)
        self.alert_speed_slider.setValue(self.settings.value("alert_speed", 20, int))
        h.addWidget(QLabel("Speed"))
        h.addWidget(self.alert_speed_slider)
        group.setLayout(h)
        return group

    def build_control_buttons(self):
        h = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.apply_lighting)
        h.addWidget(apply_btn)

        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self.stop_animation)
        h.addWidget(stop_btn)

        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(self.close)
        h.addWidget(quit_btn)
        return h

    # --- Callbacks ---

    def on_device_changed(self, idx):
        self.dev_path = self.device_combo.currentData()
        self.settings.setValue("device_path", self.dev_path)
        self.update_status()

    def refresh_devices(self):
        self.devices = enumerate_hidraw()
        self.device_combo.clear()
        for path, label in self.devices:
            self.device_combo.addItem(label, path)
        idx = max(0, self.device_combo.findData(self.dev_path))
        self.device_combo.setCurrentIndex(idx)
        self.update_status()

    def on_send_test(self):
        dev_path = self.dev_path
        if not dev_path or not os.path.exists(dev_path):
            QMessageBox.warning(self, "Device Error", "No HID device selected or not accessible.")
            return

        try:
            # Validate report ID
            rid_text = self.test_id.text().strip()
            if not rid_text:
                raise ValueError("Report ID cannot be empty")

            rid = int(rid_text, 16) if rid_text.startswith("0x") else int(rid_text)
            if rid < 0 or rid > 0xFF:
                raise ValueError(f"Report ID must be between 0x00 and 0xFF, got 0x{rid:02X}")

            # Validate data
            data_hex = self.test_data.text().strip().split()
            if not data_hex:
                raise ValueError("Data cannot be empty")

            data_bytes = []
            for i, x in enumerate(data_hex):
                try:
                    byte_val = int(x, 16)
                    if byte_val < 0 or byte_val > 0xFF:
                        raise ValueError(f"Byte #{i+1} value out of range (0x00-0xFF): {x}")
                    data_bytes.append(byte_val)
                except ValueError as e:
                    raise ValueError(f"Invalid hex byte #{i+1}: '{x}' - {e}")

            # Limit payload size for safety
            if len(data_bytes) > 64:
                reply = QMessageBox.question(
                    self, "Large Payload Warning",
                    f"Payload is {len(data_bytes)} bytes. This is unusually large and may damage the device.\n\nContinue anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            # Send report
            logger.info(f"Sending test report: ID=0x{rid:02X}, Data=[{' '.join(f'{b:02X}' for b in data_bytes)}]")
            if send_feature_report(dev_path, rid, data_bytes):
                QMessageBox.information(self, "Success", f"Feature report sent.\nID: 0x{rid:02X}\nSize: {len(data_bytes)} bytes")
            else:
                QMessageBox.warning(self, "Failed", "Feature report failed to send. Check logs.")

        except ValueError as e:
            QMessageBox.critical(self, "Validation Error", str(e))
        except Exception as e:
            logger.exception(f"Test command error: {e}")
            QMessageBox.critical(self, "Error", f"Failed to send report:\n{str(e)}")

    def on_hover_preview(self, r, g, b):
        self.update_preview(r, g, b)

    def on_wheel_changed(self, r, g, b):
        self.current_color = [r, g, b]
        self.update_preview(r, g, b)
        self.persist_state()

    def send_live_color(self, r, g, b):
        self.live_sender.queue(r, g, b, self.current_intensity)

    def on_style_changed(self, idx):
        self.apply_lighting()

    def on_value_changed(self, value):
        self.current_intensity = value
        self.value_label.setText(str(value))
        self.color_wheel.setHSV(self.color_wheel.h, self.color_wheel.s, value / 255.0)
        r, g, b = self.current_color
        self.live_sender.queue(r, g, b, self.current_intensity)
        self.update_preview(*self.current_color)
        self.persist_state()

    def on_keep_on_exit_changed(self, state):
        self.keep_on_exit = (state == Qt.CheckState.Checked)
        self.persist_state()

    def on_tb_alert_changed(self, state):
        self.tb_alert_enabled = (state == Qt.CheckState.Checked)
        self.settings.setValue("tb_alert_enabled", self.tb_alert_enabled)
        self.settings.sync()

    def update_preview(self, r, g, b):
        text_color = "black" if (r + g + b) / 3 > 128 else "white"
        self.preview_label.setText(f"RGB({r}, {g}, {b})  •  Brightness {self.current_intensity}")
        self.preview_label.setStyleSheet(
            f"border: 2px solid #333; font-weight: bold; color:{text_color};"
            f"background-color: rgb({r},{g},{b});"
        )

    def apply_preset(self, name):
        r, g, b, i = PRESETS[name]
        self.current_color = [r, g, b]
        self.current_intensity = i
        self.value_slider.setValue(i)
        self.color_wheel.setRGB(r, g, b)
        self.update_preview(r, g, b)
        self.persist_state()
        self.apply_lighting()

    def apply_user_preset(self, preset):
        r, g, b, i = preset["r"], preset["g"], preset["b"], preset["i"]
        self.current_color = [r, g, b]
        self.current_intensity = i
        self.value_slider.setValue(i)
        self.color_wheel.setRGB(r, g, b)
        self.update_preview(r, g, b)
        self.persist_state()
        self.apply_lighting()

    def reload_user_presets_bar(self):
        while getattr(self, "user_presets_bar", None) and self.user_presets_bar.count():
            item = self.user_presets_bar.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
        for p in self.user_presets[:16]:
            r, g, b, i = p["r"], p["g"], p["b"], p["i"]
            btn = QPushButton()
            btn.setFixedSize(24, 24)
            btn.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid #555;")
            btn.setToolTip(f"RGB({r},{g},{b}) I({i})")
            btn.clicked.connect(lambda checked=False, preset=p: self.apply_user_preset(preset))
            self.user_presets_bar.addWidget(btn)

    def save_current_preset(self):
        r, g, b = self.current_color
        i = self.current_intensity
        preset = {"r": r, "g": g, "b": b, "i": i}
        self.user_presets = [p for p in self.user_presets if not (
            p.get("r") == r and p.get("g") == g and p.get("b") == b and p.get("i") == i
        )]
        self.user_presets.insert(0, preset)
        self.user_presets = self.user_presets[:16]
        self.settings.setValue("user_presets_json", json.dumps(self.user_presets))
        self.settings.sync()
        self.reload_user_presets_bar()

    def persist_state(self):
        r, g, b = self.current_color
        self.settings.setValue("device_path", self.dev_path)
        self.settings.setValue("color_r", r)
        self.settings.setValue("color_g", g)
        self.settings.setValue("color_b", b)
        self.settings.setValue("intensity", self.current_intensity)
        self.settings.setValue("keep_on_exit", self.keep_on_exit)
        self.settings.setValue("speed_slider", self.speed_slider.value())
        self.settings.setValue("alert_duration", self.alert_duration_slider.value())
        self.settings.setValue("alert_speed", self.alert_speed_slider.value())
        self.settings.sync()

    # Watchdog
    def on_heartbeat(self):
        self.last_heartbeat = time.monotonic()

    def watchdog_loop(self):
        while not self.watchdog_stop.is_set():
            gap = time.monotonic() - self.last_heartbeat
            if gap > WATCHDOG_STALL_THRESHOLD_SEC:
                logger.error(f"UI heartbeat stalled for {gap:.2f}s")
            time.sleep(WATCHDOG_INTERVAL_SEC)

    def check_device_hotplug(self):
        """Check if device was plugged/unplugged"""
        was_available = hasattr(self, 'devices') and any(p == self.dev_path for p, _ in self.devices)
        now_available = self.dev_path and os.path.exists(self.dev_path)

        if was_available != now_available:
            if now_available:
                logger.info(f"Device hotplugged: {self.dev_path}")
                self.update_status()
                # Try to apply current color
                try:
                    disable_autonomous(self.dev_path)
                    r, g, b = self.current_color
                    set_color(self.dev_path, r, g, b, self.current_intensity)
                except Exception as e:
                    logger.error(f"Failed to apply color after hotplug: {e}")
            else:
                logger.warning(f"Device unplugged: {self.dev_path}")
                self.update_status()
                self.animator.stop()

    # Animations
    def apply_lighting(self):
        dev_path = self.dev_path
        if not dev_path or not os.path.exists(dev_path):
            QMessageBox.warning(self, "Device Error",
                                "No HID device selected or not accessible.\n\nCheck udev permissions.")
            return

        if self.alert_active:
            QMessageBox.information(self, "Alert active",
                                    "Red alert is active; stop alert before starting animations.")
            return

        style = self.style_combo.currentText()
        interval = self.speed_slider.value() / 100.0
        base_color = tuple(self.current_color)
        try:
            self.animator.start(style, base_color, interval)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start animation:\n{str(e)}")

    def stop_animation(self):
        self.animator.stop()
        dev_path = self.dev_path
        if dev_path and os.path.exists(dev_path):
            disable_autonomous(dev_path)
            set_color(dev_path, 0, 0, 0, 0)

    # Red alert
    def trigger_red_alert(self):
        if not self.tb_alert_enabled:
            return
        if not self.dev_path or not os.path.exists(self.dev_path):
            return
        # Stop any running animation
        self.animator.stop()
        # Start flashing red
        self.alert_active = True
        self.alert_phase = 0
        duration_sec = self.alert_duration_slider.value()
        speed = self.alert_speed_slider.value()  # higher = faster
        self.alert_timer.setInterval(max(20, int(1000 / (10 + speed))))  # ~ from ~25 Hz downwards
        self.alert_timer.start()
        # Auto-stop after duration
        QTimer.singleShot(duration_sec * 1000, self.stop_red_alert)
        logger.info("Thunderbird new mail: red alert triggered")

    def _alert_tick(self):
        # Simple square-wave flash between full red and off
        self.alert_phase ^= 1
        r, g, b = (255, 0, 0) if self.alert_phase else (0, 0, 0)
        try:
            disable_autonomous(self.dev_path)
            set_color(self.dev_path, r, g, b, 255 if self.alert_phase else 0)
        except Exception as e:
            logger.error(f"Alert tick failed: {e}")

    def stop_red_alert(self):
        if self.alert_active:
            self.alert_timer.stop()
            self.alert_active = False
            # Restore static current color
            try:
                disable_autonomous(self.dev_path)
                r, g, b = self.current_color
                set_color(self.dev_path, r, g, b, self.current_intensity)
            except Exception as e:
                logger.error(f"Red alert stop failed: {e}")

    def closeEvent(self, event):
        try:
            self.persist_state()
            # Stop alert
            self.alert_timer.stop()

            if self.keep_on_exit and self.dev_path and os.path.exists(self.dev_path):
                if not self.animator.thread or not self.animator.thread.is_alive():
                    # Only set static color if no animation is running
                    disable_autonomous(self.dev_path)
                    set_color(self.dev_path, *self.current_color, self.current_intensity)
                    logger.info("Kept static lighting on exit")
                else:
                    logger.info("Animation will continue running after GUI close")
            else:
                # User doesn't want to keep lighting, stop animation
                self.animator.stop()
        finally:
            self.watchdog_stop.set()
            event.accept()

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    window = KeyboardLightingWindow()
    window.resize(900, 1000)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
