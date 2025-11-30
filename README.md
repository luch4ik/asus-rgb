# ASUS Keyboard RGB Controller (Service Edition)

A robust, service-based RGB controller for ASUS keyboards on Linux. This version uses a client-server architecture to ensure animations persist seamlessly in the background without zombie processes or stability issues.

## Structure

*   **`kbdrgb_service/daemon.py`**: The background service. Owns the device and runs animations.
*   **`kbdrgb_service/gui.py`**: The graphical interface. Sends commands to the daemon.
*   **`kbdrgb_service/main.py`**: The smart launcher. Starts the daemon if needed and opens the GUI.
*   **`kbdrgb_service/shared.py`**: Shared logic for HID communication and animations.

## Installation

1.  **Dependencies:**
    ```bash
    pip install PyQt6
    ```

2.  **Permissions:**
    Install the udev rule to allow non-root access to the keyboard:
    ```bash
    sudo cp udev/99-kbdrgb.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    ```

## Usage

**Run the Launcher:**
This is the recommended way. It automatically handles the background service.
```bash
./kbdrgb_service/main.py
```

**Manual Daemon Control:**
```bash
./kbdrgb_service/daemon.py
```

## Systemd Service (Optional)

To have the daemon start automatically on boot:

1.  Edit `kbdrgb_service/kbdrgb.service` and verify the path to `daemon.py` is correct for your system.
2.  Install the service:
    ```bash
    mkdir -p ~/.config/systemd/user/
    cp kbdrgb_service/kbdrgb.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now kbdrgb
    ```