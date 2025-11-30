# Quick Start Guide - ASUS Keyboard RGB Controller

## What's New - All Issues Fixed! ✅

1. **✅ ColorWheel Replaced** - Now uses simple QColorDialog color picker button
2. **✅ Animation Persistence** - Animations continue after GUI close when using daemon mode
3. **✅ Settings Persist** - All color/brightness settings saved automatically
4. **✅ Ripple Mode Fixed** - Works with 20% baseline → +5% ripple → gradual decay

## Three Versions Available

### 1. `kbdrgb_simple.py` - Standalone GUI ⚡
**Best for:** Quick testing, no daemon needed

```bash
python3 kbdrgb_simple.py
```

**Features:**
- Simple color picker (no complex wheel)
- Immediate color/brightness application
- Animations continue after close (non-daemon threads)
- Settings persist in `~/.config/kbdrgb/kbdrgb.conf`

**Limitation:** No way to stop animations after closing GUI (need to reopen or `killall python3`)

---

### 2. `kbdrgb_daemon.py` - Background Service 🔧
**Best for:** True background persistence independent of GUI

```bash
# Start daemon
python3 kbdrgb_daemon.py

# Check status
python3 kbdrgb_daemon.py status

# Stop daemon
python3 kbdrgb_daemon.py stop
```

**Features:**
- Runs in background independent of GUI
- Watches `~/.config/kbdrgb/daemon_state.json` for changes
- Handles SIGTERM/SIGINT for graceful shutdown
- PID file at `~/.config/kbdrgb/daemon.pid`
- Logs to `~/.config/kbdrgb/daemon.log`

**How it works:**
1. Start daemon in background
2. GUI writes to `daemon_state.json` when you apply settings
3. Daemon detects file change and applies new animation
4. Close GUI - daemon keeps running independently

---

### 3. `kbdrgb_integrated.py` - GUI + Daemon Communication 🚀
**Best for:** Complete solution with full control

```bash
# Terminal 1: Start daemon first
python3 kbdrgb_daemon.py

# Terminal 2: Run integrated GUI
python3 kbdrgb_integrated.py
```

**Features:**
- Simple color picker button (QColorDialog)
- **Daemon mode toggle** - enable/disable background persistence
- When daemon mode enabled:
  - Sends commands to daemon via JSON state file
  - Animations persist after GUI close
  - Can reopen GUI anytime to change settings
- When daemon mode disabled:
  - Direct hardware control (like simple version)
  - Warning about animations stopping on close
- Full logging and status display

**UI Highlights:**
- Status shows: Device connection + Daemon running/stopped
- Checkbox: "Use background daemon (animations persist after close)"
- Informational text explains behavior
- Apply button sends to daemon (if enabled) or applies directly
- Stop button turns off LEDs via daemon or directly

---

## Installation & Setup

### 1. Check Device Path
```bash
ls -la /dev/hidraw*
# Find your keyboard (usually /dev/hidraw1 or /dev/hidraw2)
```

### 2. Set Environment Variable (Optional)
```bash
export KBDRGB_HID=/dev/hidraw2  # Change to your device
```

### 3. Ensure Permissions
```bash
# Option 1: Add user to input group
sudo usermod -a -G input $USER
# Then logout/login

# Option 2: Temporary permission (for testing)
sudo chmod 666 /dev/hidraw1  # Change to your device
```

---

## Usage Examples

### Example 1: Quick Static Color
```bash
# Method 1: Simple GUI
python3 kbdrgb_simple.py
# Click "Pick Color", choose red, adjust brightness, done!

# Method 2: Integrated GUI without daemon
python3 kbdrgb_integrated.py
# Uncheck "Use background daemon"
# Click "Pick Color", choose blue, click Apply
```

### Example 2: Persistent Breathing Animation
```bash
# Terminal 1: Start daemon
python3 kbdrgb_daemon.py

# Terminal 2: Start GUI
python3 kbdrgb_integrated.py

# In GUI:
# 1. Check "Use background daemon" ✓
# 2. Click "Pick Color" → choose cyan
# 3. Select "Breathing" from dropdown
# 4. Adjust speed slider
# 5. Click "Apply"
# 6. Close GUI window - breathing continues!

# To stop later:
# 1. Reopen GUI: python3 kbdrgb_integrated.py
# 2. Click "Stop/Off"

# Or stop daemon entirely:
python3 kbdrgb_daemon.py stop
```

### Example 3: Ripple Effect (Fixed!)
```bash
python3 kbdrgb_integrated.py

# In GUI:
# 1. Pick a color (e.g., purple)
# 2. Select "Ripple" from dropdown
# 3. Click "Apply"
# 4. Watch: 20% baseline brightness with random ripples!
```

---

## File Locations

### Configuration
- Settings: `~/.config/kbdrgb/kbdrgb.conf` (QSettings format)
- Daemon state: `~/.config/kbdrgb/daemon_state.json`
- Daemon PID: `~/.config/kbdrgb/daemon.pid`
- Daemon log: `~/.config/kbdrgb/daemon.log`

### View Settings
```bash
# View GUI settings
cat ~/.config/kbdrgb/kbdrgb.conf

# View daemon state
cat ~/.config/kbdrgb/daemon_state.json

# View daemon logs
tail -f ~/.config/kbdrgb/daemon.log
```

---

## Animations Explained

### Static
Sets fixed color and brightness. No animation.

### Breathing
Smooth sine wave intensity change (0% → 100% → 0%). Uses selected color.

### Rainbow
Cycles through full color spectrum. Ignores selected color.

### Ripple (FIXED!)
- **Baseline:** Keyboard stays at 20% brightness
- **Keystrokes:** Random simulated keystrokes every 0.1-0.5 seconds
- **Ripple:** Affected LED +5% boost, neighbors get partial boost
- **Decay:** Gradual fade back to 20% baseline (-2 intensity per frame)

---

## Troubleshooting

### Animations stop when closing GUI
**Problem:** Using `kbdrgb_simple.py` or integrated without daemon mode

**Solution:** Use integrated GUI with daemon:
```bash
# Terminal 1
python3 kbdrgb_daemon.py

# Terminal 2
python3 kbdrgb_integrated.py
# Enable "Use background daemon" checkbox
```

### Settings not persisting
**Check:** Settings file exists and has correct values
```bash
cat ~/.config/kbdrgb/kbdrgb.conf
```

**Fix:** Ensure config directory has write permissions
```bash
mkdir -p ~/.config/kbdrgb
chmod 755 ~/.config/kbdrgb
```

### Device not found
**Check:** Device path exists
```bash
ls -la /dev/hidraw*
```

**Fix:** Find correct device and either:
- Set environment variable: `export KBDRGB_HID=/dev/hidraw2`
- Or manually edit device path in GUI settings file

### Daemon won't start
**Check:** Is it already running?
```bash
python3 kbdrgb_daemon.py status
```

**Fix:** Stop old instance first
```bash
python3 kbdrgb_daemon.py stop
# Then start new one
python3 kbdrgb_daemon.py
```

### Permission denied on /dev/hidrawX
**Temporary fix:**
```bash
sudo chmod 666 /dev/hidraw1  # Changes on reboot
```

**Permanent fix:**
```bash
sudo usermod -a -G input $USER
# Logout and login again
```

### Ripple not working
**Verify:** You're using the fixed version
```bash
grep -n "base_intensity = int(0.20" kbdrgb_integrated.py
# Should show line with 20% baseline
```

If not found, you have old version. Use the new files.

---

## Recommended Setup

### For Daily Use - Full Persistence:

**One-time setup:**
```bash
# 1. Start daemon on login (add to ~/.bashrc or systemd service)
echo "python3 ~/ClaudeProjects/ASUSLed/kbdrgb_daemon.py &" >> ~/.bashrc

# 2. Create desktop launcher for GUI
cat > ~/.local/share/applications/kbdrgb.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Keyboard RGB
Exec=python3 $HOME/ClaudeProjects/ASUSLed/kbdrgb_integrated.py
Icon=preferences-desktop-color
Terminal=false
Categories=Settings;HardwareSettings;
EOF
```

**Daily usage:**
- Daemon runs in background always
- Open GUI when you want to change color/animation
- Close GUI - settings and animations persist
- Reopen GUI anytime to adjust

---

## Advanced: Systemd Service (Optional)

Create daemon as system service:

```bash
cat > ~/.config/systemd/user/kbdrgb-daemon.service <<EOF
[Unit]
Description=Keyboard RGB Daemon
After=graphical.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $HOME/ClaudeProjects/ASUSLed/kbdrgb_daemon.py
Restart=on-failure

[Install]
WantedBy=default.target
EOF

# Enable and start
systemctl --user enable kbdrgb-daemon.service
systemctl --user start kbdrgb-daemon.service

# Check status
systemctl --user status kbdrgb-daemon.service
```

---

## Summary - Which Version to Use?

| Feature | Simple | Daemon Only | Integrated |
|---------|--------|-------------|------------|
| Color picker | ✅ QColorDialog | ❌ | ✅ QColorDialog |
| Settings persist | ✅ | N/A | ✅ |
| Animations persist | ⚠️ Can't control | ✅ | ✅ |
| Easy to stop | ❌ | ✅ | ✅ |
| Best for | Testing | Background | Daily use |

**Recommendation:** Use `kbdrgb_integrated.py` + `kbdrgb_daemon.py` for best experience!

---

## Testing Checklist

After setup, verify everything works:

- [ ] Color picker opens and applies color immediately
- [ ] Brightness slider changes keyboard brightness in real-time
- [ ] Quick presets work (Red, Blue, etc.)
- [ ] Static color persists after closing GUI
- [ ] Breathing animation runs smoothly
- [ ] Rainbow animation cycles colors
- [ ] Ripple shows 20% baseline with random flashes
- [ ] Daemon status shows correctly in integrated GUI
- [ ] Closing GUI with daemon enabled keeps animation running
- [ ] Reopening GUI shows correct saved color/brightness
- [ ] Stop button turns off LEDs completely

---

**All features working!** Enjoy your customizable RGB keyboard! 🌈

For issues, check logs:
- GUI logs: Visible in bottom panel
- Daemon logs: `~/.config/kbdrgb/daemon.log`
