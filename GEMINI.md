# ASUS Keyboard RGB Controller

This project provides a Python-based GUI tool to control the RGB lighting on ASUS keyboards via the HID interface on Linux.

## Project Overview

*   **Primary Language:** Python 3
*   **GUI Framework:** PyQt6
*   **Platform:** Linux (depends on `/dev/hidraw`)
*   **Key Features:**
    *   Color picker (HSV wheel + Value slider).
    *   Animations (Static, Breathing, Rainbow, Ripple, etc.).
    *   Persistence (animations continue after window close via system tray or background threads).
    *   Configuration auto-saving.

## Key Files

*   **`kbdrgb.py`**: The **main application**. Run this file to start the GUI. It handles the UI, HID communication, and animation threads.
*   **`config.py`**: Helper module for managing user configuration and presets (JSON-based).
*   **`udev/99-kbdrgb.rules`**: Udev rules file to grant non-root users access to the HID device.
*   **`copilot_rgb.py`**: A variant/experiment implementing a singleton pattern with socket IPC (appears less feature-complete than `kbdrgb.py`).
*   **`archive/`**: Contains older or alternative implementations (`kbdrgb_daemon.py`, `kbdrgb_integrated.py`, etc.). `kbdrgb.py` is the recommended version.

## Setup & Installation

1.  **Dependencies:**
    Ensure Python 3 is installed. Install the required PyQt6 library:
    ```bash
    pip install PyQt6
    ```

2.  **Device Permissions (udev):**
    To control the keyboard without `sudo`, install the udev rule:
    ```bash
    sudo cp udev/99-kbdrgb.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    ```
    Alternatively, add your user to the `input` group:
    ```bash
    sudo usermod -a -G input $USER
    ```
    *(You may need to log out and back in).*

3.  **Identify Device:**
    Check `/dev/hidraw*` to find your keyboard. It is usually `/dev/hidraw1` or `/dev/hidraw2`.

## Usage

**Run the application:**
```bash
python3 kbdrgb.py
```

**Specify a custom device path:**
If the app doesn't find your keyboard automatically, set the `KBDRGB_HID` environment variable:
```bash
export KBDRGB_HID=/dev/hidraw2
python3 kbdrgb.py
```

**Headless/Daemon Mode:**
While `kbdrgb.py` is a GUI, older scripts in `archive/` (`kbdrgb_daemon.py`) provided headless daemon functionality. The current `kbdrgb.py` supports background operation by minimizing/closing to the system tray.

## Development Notes

*   **Style:** The project uses a dark theme (`QPalette`) for the GUI.
*   **Threading:** Animations run in separate threads to avoid blocking the UI.
*   **Persistence:** Settings are stored in `~/.config/kbdrgb/`.
*   **Logging:** Logs are displayed in the GUI console and useful for debugging HID issues.
