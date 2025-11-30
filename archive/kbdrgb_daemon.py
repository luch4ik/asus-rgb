#!/usr/bin/env python3
"""
Keyboard RGB Daemon - Background Service
Keeps animations running independently of GUI
Communicates via socket or shared settings file
"""

import os
import sys
import fcntl
import time
import math
import threading
import logging
import json
import signal
from enum import IntEnum
from pathlib import Path
from typing import Optional, Tuple

# --- HID Constants ---
class HIDReport(IntEnum):
    SET_COLOR = 0x05
    DISABLE_AUTONOMOUS = 0x0B

class HIDConstants:
    IOCTL_BASE = 0xC0004806
    DEFAULT_LED_START = 0
    DEFAULT_LED_END = 100
    MAX_INTENSITY = 255

DEFAULT_DEVICE_PATH = os.environ.get("KBDRGB_HID", "/dev/hidraw1")
DAEMON_STATE_FILE = Path.home() / ".config" / "kbdrgb" / "daemon_state.json"
PID_FILE = Path.home() / ".config" / "kbdrgb" / "daemon.pid"

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(Path.home() / ".config" / "kbdrgb" / "daemon.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("kbdrgb_daemon")

# --- HID helpers ---
def HIDIOCSFEATURE(length):
    return HIDConstants.IOCTL_BASE | (length << 16)

def send_feature_report(dev_path: str, report_id: int, data: list) -> bool:
    if not dev_path or not os.path.exists(dev_path):
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

# --- Animations ---
def breathing(dev_path, base_color, interval, stop_event):
    r, g, b = base_color
    steps = max(90, int(120 * interval))
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

def rainbow(dev_path, interval, stop_event):
    steps = 180
    if not disable_autonomous(dev_path):
        return
    while not stop_event.is_set():
        for k in range(steps):
            if stop_event.is_set():
                return
            hue = k / steps
            # Simple RGB calculation without QColor
            phase_g = 2 * math.pi / 3
            phase_b = 4 * math.pi / 3
            r = int(255 * (math.sin(2 * math.pi * hue) * 0.5 + 0.5))
            g = int(255 * (math.sin(2 * math.pi * hue + phase_g) * 0.5 + 0.5))
            b = int(255 * (math.sin(2 * math.pi * hue + phase_b) * 0.5 + 0.5))
            set_color(dev_path, r, g, b, 255)
            time.sleep(interval)

def ripple(dev_path, base_color, interval, stop_event):
    r, g, b = base_color
    leds = 20
    base_intensity = int(0.20 * 255)
    ripple_boost = int(0.05 * 255)
    import random
    led_intensities = [base_intensity] * leds

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

# --- Daemon State Manager ---
class DaemonState:
    def __init__(self):
        self.state_file = DAEMON_STATE_FILE
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.stop_event = threading.Event()
        self.animation_thread = None
        self.current_animation = None
        self.watch_timer = None

    def load_state(self):
        """Load desired state from file"""
        if not self.state_file.exists():
            return None
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return None

    def save_state(self, state):
        """Save current state to file"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def start_animation(self, style, color, intensity, interval, device_path):
        """Start animation in background thread"""
        self.stop_animation()

        logger.info(f"Starting {style} animation: RGB{color} @ {intensity} intensity")

        if style.lower() == "static":
            disable_autonomous(device_path)
            set_color(device_path, *color, intensity)
            return

        self.stop_event.clear()

        animations = {
            "breathing": lambda: breathing(device_path, color, interval, self.stop_event),
            "rainbow": lambda: rainbow(device_path, interval, self.stop_event),
            "ripple": lambda: ripple(device_path, color, interval, self.stop_event),
        }

        if style.lower() in animations:
            self.animation_thread = threading.Thread(
                target=animations[style.lower()],
                daemon=False,
                name=f"Daemon-{style}"
            )
            self.animation_thread.start()
            self.current_animation = style
        else:
            logger.warning(f"Unknown animation: {style}")

    def stop_animation(self):
        """Stop current animation"""
        if self.animation_thread and self.animation_thread.is_alive():
            logger.info(f"Stopping {self.current_animation}")
            self.stop_event.set()
            self.animation_thread.join(timeout=2.0)
        self.animation_thread = None
        self.current_animation = None

    def watch_state_file(self):
        """Watch state file for changes and react"""
        last_mtime = 0
        while not self.stop_event.is_set():
            try:
                if self.state_file.exists():
                    mtime = self.state_file.stat().st_mtime
                    if mtime > last_mtime:
                        last_mtime = mtime
                        state = self.load_state()
                        if state:
                            self.apply_state(state)
            except Exception as e:
                logger.error(f"Watch error: {e}")
            time.sleep(1)

    def apply_state(self, state):
        """Apply state from config"""
        style = state.get("style", "static")
        color = tuple(state.get("color", [255, 255, 255]))
        intensity = state.get("intensity", 255)
        interval = state.get("interval", 0.1)
        device_path = state.get("device_path", DEFAULT_DEVICE_PATH)

        self.start_animation(style, color, intensity, interval, device_path)

# --- Main Daemon ---
class RGBDaemon:
    def __init__(self):
        self.state_manager = DaemonState()
        self.running = True

    def write_pid(self):
        """Write PID file"""
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))

    def remove_pid(self):
        """Remove PID file"""
        if PID_FILE.exists():
            PID_FILE.unlink()

    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        self.state_manager.stop_event.set()
        self.state_manager.stop_animation()

    def run(self):
        """Main daemon loop"""
        logger.info("RGB Daemon starting...")

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

        self.write_pid()

        try:
            # Load initial state
            state = self.state_manager.load_state()
            if state:
                self.state_manager.apply_state(state)

            # Start watching for state changes
            watch_thread = threading.Thread(
                target=self.state_manager.watch_state_file,
                daemon=False
            )
            watch_thread.start()

            logger.info("RGB Daemon running (PID: {})".format(os.getpid()))
            logger.info(f"State file: {DAEMON_STATE_FILE}")
            logger.info("Send SIGTERM or SIGINT to stop")

            # Keep daemon alive
            while self.running:
                time.sleep(1)

            logger.info("RGB Daemon shutting down...")
            self.state_manager.stop_animation()
            watch_thread.join(timeout=2)

        finally:
            self.remove_pid()
            logger.info("RGB Daemon stopped")

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        # Stop daemon
        if PID_FILE.exists():
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"Sent SIGTERM to daemon (PID {pid})")
            except ProcessLookupError:
                print(f"Daemon not running (PID {pid} not found)")
                PID_FILE.unlink()
        else:
            print("Daemon not running (no PID file)")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        # Check status
        if PID_FILE.exists():
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)  # Check if process exists
                print(f"Daemon is running (PID {pid})")
                if DAEMON_STATE_FILE.exists():
                    with open(DAEMON_STATE_FILE, 'r') as f:
                        state = json.load(f)
                    print(f"Current state: {state}")
            except ProcessLookupError:
                print(f"Daemon not running (stale PID file)")
                PID_FILE.unlink()
        else:
            print("Daemon not running")
        return

    # Start daemon
    daemon = RGBDaemon()
    daemon.run()

if __name__ == "__main__":
    main()
