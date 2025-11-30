# ASUS Keyboard RGB Controller

Simple RGB controller for ASUS keyboards with HID interface.

## Features

✅ **Simple Color Picker** - Clean QColorDialog button (no complex widgets)
✅ **Animations Persist** - Runs in system tray, animations continue when window closed
✅ **Auto-Save Settings** - Color, brightness, and animation preferences persist
✅ **4 Animations** - Static, Breathing, Rainbow, Ripple (20% baseline + 5% boost)
✅ **Quick Presets** - One-click color selection

## Quick Start

```bash
# Make executable
chmod +x kbdrgb.py

# Run (adjust device path if needed)
python3 kbdrgb.py

# Or set custom device
export KBDRGB_HID=/dev/hidraw2
python3 kbdrgb.py
```

## How It Works

1. **Open GUI** - Click "Pick Color" to choose your color
2. **Set Animation** - Select Static, Breathing, Rainbow, or Ripple
3. **Click Apply** - Animation starts immediately
4. **Close Window** - App minimizes to system tray, animations continue!
5. **Reopen** - Click tray icon to reopen window and adjust settings
6. **Quit** - Right-click tray icon → "Quit" to stop everything

## Animations Explained

**Static** - Fixed color, no animation
**Breathing** - Smooth sine wave intensity (0% → 100% → 0%)
**Rainbow** - Full color spectrum cycle
**Ripple** - 20% baseline brightness with random +5% ripple flashes that decay back to baseline

## Device Setup

### Find Your Device

```bash
ls -la /dev/hidraw*
# Look for your keyboard (usually /dev/hidraw1 or /dev/hidraw2)
```

### Fix Permissions

```bash
# Temporary (resets on reboot)
sudo chmod 666 /dev/hidraw1

# Permanent - add user to input group
sudo usermod -a -G input $USER
# Then logout/login
```

### Create udev Rule (Optional)

```bash
sudo tee /etc/udev/rules.d/99-asus-keyboard.rules <<EOF
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", MODE="0666"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```

## Configuration

Settings auto-save to: `~/.config/kbdrgb/kbdrgb.conf`

Saved settings:
- Color (RGB values)
- Brightness (0-255)
- Last animation style
- Animation speed
- Device path

## Troubleshooting

### "Device not found" error

Check device path exists:
```bash
ls -la /dev/hidraw*
```

If your device is `/dev/hidraw2`, set environment variable:
```bash
export KBDRGB_HID=/dev/hidraw2
python3 kbdrgb.py
```

### Permission denied

```bash
sudo chmod 666 /dev/hidraw1  # Change to your device
```

Or add yourself to input group (permanent):
```bash
sudo usermod -a -G input $USER
# Logout and login again
```

### Animations stop when I close window

This is fixed in the new version! The app minimizes to system tray instead of closing. Animations keep running in the background.

To verify:
1. Start an animation (e.g., Breathing)
2. Close the window (click X)
3. Look for the app icon in your system tray
4. Animation should still be running!

To actually quit:
- Right-click tray icon → "Quit (Stop Animations)"

### Static color works but animations don't

Make sure you're using the new `kbdrgb.py` file (not the old versions in the archive folder).

The new version keeps the process running in the background via system tray, which allows animations to continue.

### No system tray icon

Some desktop environments hide tray icons by default.

**GNOME:** Install extension like "AppIndicator Support"
```bash
# On Fedora/RHEL
sudo dnf install gnome-shell-extension-appindicator

# On Ubuntu/Debian
sudo apt install gnome-shell-extension-appindicator
```

**KDE:** Tray icons work by default

**Other:** If your DE doesn't support tray icons, animations will still persist when you close the window. To quit, use:
```bash
killall python3
# Or
pkill -f kbdrgb.py
```

## Auto-Start on Login (Optional)

### Method 1: Desktop Autostart

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/kbdrgb.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Keyboard RGB
Exec=python3 $HOME/ClaudeProjects/ASUSLed/kbdrgb.py
Icon=preferences-desktop-color
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
```

### Method 2: Add to Shell RC

```bash
echo "python3 ~/ClaudeProjects/ASUSLed/kbdrgb.py &" >> ~/.bashrc
```

## Development

### File Structure

```
.
├── kbdrgb.py          # Main application (USE THIS)
├── config.py          # Preset configurations (optional)
├── README.md          # This file
├── QUICKSTART.md      # Detailed usage guide
├── udev/              # udev rules examples
└── archive/           # Old versions (deprecated)
```

### Requirements

- Python 3.6+
- PyQt6
- Linux with HID support

Install dependencies:
```bash
pip install PyQt6
```

### Architecture

- **HID Communication:** Direct `ioctl` calls to `/dev/hidraw` device
- **Animation Threads:** Non-daemon threads that persist with main process
- **System Tray:** Keeps process alive when window is closed
- **Settings:** QSettings (stores in `~/.config/kbdrgb/kbdrgb.conf`)

### Report IDs

- `0x0B` (DISABLE_AUTONOMOUS) - Disables firmware animations
- `0x05` (SET_COLOR) - Sets RGB color for LED range

## Tips

- **Instant Preview:** Brightness slider updates preview in real-time
- **Quick Colors:** Use preset buttons for instant color changes
- **Speed Control:** Adjust animation speed with slider (slow → fast)
- **Persistent:** Settings save automatically, restore on next launch

## License

MIT License - Feel free to modify and distribute

## Credits

Built with Python and PyQt6 for ASUS keyboard RGB control.

## Archive

Old versions moved to `archive/` folder:
- `kbdrgbd.py` - Original basic version
- `kbdrgb_gui.py` - Advanced version with extras
- `kbdrgb_simple.py` - Simplified version (no persistence)
- `kbdrgb_daemon.py` - Separate daemon process
- `kbdrgb_integrated.py` - GUI + daemon communication

**Use `kbdrgb.py` for best experience!**
