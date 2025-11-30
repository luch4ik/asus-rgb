#!/usr/bin/env python3
"""
Keyboard Lighting Controller - PyQt6 GUI
- Singleton pattern to ensure only one instance runs
- IPC via Unix socket for communication between instances
"""

import os
import sys
import socket
import threading
import logging
import queue
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QComboBox, QGroupBox, QGridLayout,
    QMessageBox, QPlainTextEdit, QDockWidget, QCheckBox, QFrame, QLineEdit
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QSettings, QPointF, QSize
from PyQt6.QtGui import QColor, QPalette, QPainter, QConicalGradient, QRadialGradient, QPen, QDoubleValidator

# --- Constants ---
APP_NAME = "kbdrgb"
ORG_NAME = "kbdrgb"
DEFAULT_DEVICE_PATH = os.environ.get("KBDRGB_HID", "/dev/hidraw1")
WATCHDOG_INTERVAL_SEC = 0.5
WATCHDOG_STALL_THRESHOLD_SEC = 2.0
PID_FILE = Path.home() / ".config" / "kbdrgb" / "app.pid"
SOCKET_FILE = Path.home() / ".config" / "kbdrgb" / "app.sock"

# --- Logging (thread-safe queue -> GUI console) ---
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

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(QueueHandler())

# --- Singleton and IPC Logic ---
def is_another_instance_running():
    """Check if another instance is running using PID file."""
    if PID_FILE.exists():
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # Check if process is still running
            return True
        except (ValueError, ProcessLookupError, FileNotFoundError):
            pass
    return False

def write_pid_file():
    """Write the current PID to the PID file."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def cleanup_pid_file():
    """Clean up the PID file."""
    if PID_FILE.exists():
        try:
            os.remove(PID_FILE)
        except OSError:
            pass

def start_socket_server():
    """Start a Unix socket server for IPC."""
    if SOCKET_FILE.exists():
        os.remove(SOCKET_FILE)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(SOCKET_FILE))
    sock.listen(1)
    return sock

def handle_socket_connection(sock):
    """Handle incoming socket connections."""
    while True:
        try:
            conn, _ = sock.accept()
            data = conn.recv(1024)
            logger.info(f"Received from new instance: {data.decode()}")
            conn.close()
        except Exception as e:
            logger.error(f"Socket error: {e}")
            break

# --- Main Application ---
def main():
    # Check for another instance
    if is_another_instance_running():
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(str(SOCKET_FILE))
            sock.sendall(b"Hello from new instance!")
            sock.close()
            logger.info("Connected to existing instance. Exiting.")
            return
        except (socket.error, FileNotFoundError):
            pass

    write_pid_file()

    sock = start_socket_server()
    threading.Thread(target=handle_socket_connection, args=(sock,), daemon=True).start()

    app = QApplication(sys.argv)

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
    ret = app.exec()

    cleanup_pid_file()
    if SOCKET_FILE.exists():
        os.remove(SOCKET_FILE)
    sys.exit(ret)

class KeyboardLightingWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        # Add your UI code here
        pass

    def closeEvent(self, event):
        event.accept()

if __name__ == "__main__":
    main()
