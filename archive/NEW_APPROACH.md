# New Approach: Detached Background Worker Process

## The "Out of the Box" Solution

Instead of trying to keep threads or processes alive, we **spawn a completely independent background worker** that has NO relationship to the GUI after launch.

## How It Works

### Two-File Architecture

1. **`kbdrgb_launcher.py`** - GUI application
   - Simple interface for color/animation selection
   - Spawns background worker when you click "Apply"
   - **Can close completely** - worker keeps running
   - Saves state for auto-restore on next launch

2. **`kbdrgb_worker.py`** - Background worker
   - Minimal script that ONLY runs animations
   - Completely independent process (detached session)
   - Writes PID to `~/.config/kbdrgb/worker.pid`
   - Runs until killed or system reboot

### Process Flow

```
User clicks "Apply Breathing"
         ↓
GUI spawns detached worker:
  subprocess.Popen(..., start_new_session=True)
         ↓
Worker process starts in new session
  - No stdin, stdout, stderr (all redirected to /dev/null)
  - Detached from parent (survives parent exit)
  - Writes PID to file
         ↓
GUI saves state to animation_state.json
         ↓
User closes GUI window
  - GUI exits completely ✅
  - Worker keeps running ✅
  - Animation continues ✅
         ↓
User reboots computer
  - Worker stops (normal)
         ↓
User launches GUI again
  - Reads animation_state.json
  - Auto-restarts last animation
  - Animation restored ✅
```

## Why This is Different

### Previous Approaches
- ❌ **Threads**: Die when process exits
- ❌ **System Tray**: Process must stay alive
- ❌ **Daemon Service**: Complex, requires manual start

### New Approach
- ✅ **Detached Process**: Completely independent
- ✅ **Auto-Restore**: Saves state, restores on launch
- ✅ **Simple**: Two files, no daemon management
- ✅ **True Persistence**: GUI can exit, animation continues

## Usage

### Start Animation

```bash
# Run the launcher GUI
python3 kbdrgb_launcher.py

# 1. Pick a color
# 2. Select animation (Breathing, Rainbow, Ripple)
# 3. Click "Apply"
# 4. Message: "Animation started in background!"
# 5. Close the window - animation keeps running!
```

### Check if Worker Running

```bash
# Check PID file
cat ~/.config/kbdrgb/worker.pid

# Verify process is running
ps aux | grep kbdrgb_worker
```

### Stop Animation

```bash
# Method 1: Open GUI and click "Off" button
python3 kbdrgb_launcher.py
# Click "Off"

# Method 2: Kill manually
pkill -f kbdrgb_worker
```

### Auto-Restore on Launch

When you launch the GUI again:
- Reads `~/.config/kbdrgb/animation_state.json`
- If worker not running, automatically restarts last animation
- Your keyboard lights come back exactly as you left them!

## State Files

### Worker PID File
```
~/.config/kbdrgb/worker.pid
```
Contains process ID of running worker

### Animation State File
```json
~/.config/kbdrgb/animation_state.json
{
  "device": "/dev/hidraw1",
  "style": "breathing",
  "color": [255, 128, 0],
  "speed": 0.1,
  "intensity": 255
}
```

### QSettings (GUI preferences)
```
~/.config/kbdrgb/kbdrgb.conf
```
Stores last used color, brightness, etc.

## Process Management

### Spawning Worker

```python
subprocess.Popen(
    [sys.executable, "kbdrgb_worker.py", device, style, r, g, b, speed, intensity],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,  # THIS IS THE KEY!
    cwd=str(Path.home())
)
```

**`start_new_session=True`** makes the process:
- Detach from parent terminal
- Create new process group
- Survive parent process exit
- Immune to parent signals (SIGHUP, etc.)

### Killing Worker

```python
def kill_worker():
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)  # Graceful shutdown
        PID_FILE.unlink()
```

Worker catches SIGTERM and cleans up PID file:
```python
def cleanup(sig, frame):
    PID_FILE.unlink(missing_ok=True)
    sys.exit(0)

signal.signal(signal.SIGTERM, cleanup)
```

## Advantages

### 1. True Independence
- Worker has NO connection to GUI after spawn
- GUI can crash, be killed, or exit normally
- Worker unaffected

### 2. Auto-Restore
- State saved to JSON file
- Next launch reads state and restores
- Seamless experience across reboots

### 3. Simple to Understand
- GUI: Spawn worker, exit
- Worker: Run animation until killed
- No complex IPC, no threading issues

### 4. Reliable
- No race conditions
- No thread synchronization
- Each component has one job

## Comparison

| Feature | System Tray | Daemon Service | Detached Worker |
|---------|-------------|----------------|-----------------|
| GUI stays running | ✅ Yes | ❌ No | ❌ No |
| True independence | ❌ No | ✅ Yes | ✅ Yes |
| Auto-restore | ❌ No | ⚠️ Complex | ✅ Yes |
| Complexity | Medium | High | Low |
| User experience | OK | Complex | Excellent |

## Testing

### Test 1: Basic Animation
```bash
python3 kbdrgb_launcher.py
# Pick orange
# Select "Breathing"
# Click "Apply"
# Close window
# Result: Breathing continues ✅
```

### Test 2: Kill GUI Forcefully
```bash
python3 kbdrgb_launcher.py
# Start rainbow animation
# In another terminal: killall python3
# Result: Rainbow continues ✅
```

### Test 3: Auto-Restore
```bash
python3 kbdrgb_launcher.py
# Start ripple animation
# Close window normally
# Kill worker: pkill -f kbdrgb_worker
# Launch GUI again: python3 kbdrgb_launcher.py
# Result: Ripple auto-restarts ✅
```

### Test 4: Static Color
```bash
python3 kbdrgb_launcher.py
# Pick red
# Select "Static"
# Click "Apply"
# Result: Color set, worker exits (static doesn't need loop)
# Close GUI
# Result: Red stays (hardware keeps it) ✅
```

## Limitations

### Reboot Required for Persistence
- Worker stops on system reboot (normal behavior)
- Auto-restore on next GUI launch
- Solution: Add GUI to autostart

### One Animation at a Time
- Starting new animation kills previous worker
- Hardware limitation (one LED state at a time)
- Expected behavior

### No Live Status Updates
- GUI doesn't know if worker crashed
- Check PID file manually if unsure
- Could add heartbeat mechanism (future enhancement)

## Auto-Start on Boot (Optional)

### Make Animation Truly Persistent

Create autostart desktop file:
```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/kbdrgb.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Keyboard RGB
Exec=python3 /home/$USER/ClaudeProjects/ASUSLed/kbdrgb_launcher.py
Hidden=true
X-GNOME-Autostart-enabled=true
EOF
```

Now:
1. Set your animation
2. Close GUI
3. Reboot computer
4. GUI auto-starts hidden
5. Animation auto-restores
6. Keyboard lights up with your animation!

## Troubleshooting

### Animation doesn't persist
Check if worker is running:
```bash
ps aux | grep kbdrgb_worker
cat ~/.config/kbdrgb/worker.pid
```

If not running, check state file:
```bash
cat ~/.config/kbdrgb/animation_state.json
```

### Worker keeps crashing
Check if device path is correct:
```bash
ls -la /dev/hidraw*
export KBDRGB_HID=/dev/hidraw2  # Adjust as needed
```

### Animation doesn't restore on launch
Check auto-restore setting:
```bash
# In GUI, this is enabled by default
# State file should exist:
ls -la ~/.config/kbdrgb/animation_state.json
```

## Summary

**The "out of the box" solution:**
- Spawn completely detached worker process
- Worker runs independently (survives GUI exit)
- State saved for auto-restore
- Simple, reliable, effective

**Files:**
- `kbdrgb_launcher.py` - GUI
- `kbdrgb_worker.py` - Background worker

**Command:**
```bash
python3 kbdrgb_launcher.py
```

**Result:** Animations truly persist! 🎉
