# MPV IPC API Contract

**Protocol**: Plain-text named pipe (Windows) / Unix socket (future Linux support)
**IPC Mode**: Command-based (plain text)
**Version**: MPV 0.34+

---

## Overview

This document defines the IPC (Inter-Process Communication) contract between the MultiMPV_Offset application and MPV video player instances. Each MPV process exposes an IPC server via named pipe, allowing the application to send commands and query properties.

---

## Connection Specification

### Named Pipe Path (Windows)

**Format**: `\\.\pipe\mpv_socket_{instance_id}_{timestamp}`

**Example**: `\\.\pipe\mpv_socket_0_1704643200`

**Launch Command**:
```bash
mpv.exe video.mp4 --input-ipc-server=\\.\pipe\mpv_socket_0_1704643200 --keep-open=yes
```

### Communication Protocol

**Request Format**: Plain-text command ending with newline (`\n`)

**Response Format**: Plain-text response (format varies by command type)

**Encoding**: UTF-8

**Connection Method**:
```python
import os

# Open named pipe for read/write
fd = os.open(pipe_path, os.O_RDWR)

# Send command
command = "pause\n"
os.write(fd, command.encode('utf-8'))

# Read response (if expected)
response = os.read(fd, 1024).decode('utf-8').strip()

# Close connection
os.close(fd)
```

---

## Command Reference

### 1. Playback Control Commands

#### 1.1 Pause Playback

**Command**: `pause`

**Description**: Pause video playback (toggle pause state)

**Request**:
```
pause\n
```

**Response**: None (fire-and-forget)

**Example**:
```python
send_mpv_command(pipe, "pause")
```

**Use Case**: Individual camera pause button in sync panel

---

#### 1.2 Set Pause State (Explicit)

**Command**: `set pause <yes|no>`

**Description**: Explicitly set pause state (no toggle)

**Request**:
```
set pause yes\n   # Pause playback
set pause no\n    # Resume playback
```

**Response**: None (fire-and-forget)

**Example**:
```python
send_mpv_command(pipe, "set pause yes")  # Pause
send_mpv_command(pipe, "set pause no")   # Play
```

**Use Case**: "Play All" / "Pause All" global controls

---

#### 1.3 Seek (Relative)

**Command**: `seek <seconds> relative+exact`

**Description**: Seek forward (+) or backward (-) by specified seconds with frame-accurate positioning

**Request**:
```
seek -1.0 relative+exact\n   # Rewind 1 second
seek +0.1 relative+exact\n   # Advance 0.1 second
```

**Response**: None (fire-and-forget)

**Flags**:
- `relative`: Seek from current position (not absolute timestamp)
- `exact`: Frame-accurate seek (no keyframe shortcuts)

**Example**:
```python
# Nudge -1s button
send_mpv_command(pipe, "seek -1.0 relative+exact")

# Nudge +0.1s button
send_mpv_command(pipe, "seek +0.1 relative+exact")
```

**Use Case**: Nudge controls for manual sync adjustment

---

#### 1.4 Seek (Absolute)

**Command**: `seek <seconds> absolute`

**Description**: Jump to specific timestamp in video

**Request**:
```
seek 120.5 absolute\n   # Jump to 2:00.5
```

**Response**: None (fire-and-forget)

**Example**:
```python
# Timeline scrubber dragged to 2:00.5
send_mpv_command(pipe, "seek 120.5 absolute")
```

**Use Case**: Timeline scrubber (drag to specific time)

---

#### 1.5 Frame Step (Forward)

**Command**: `frame-step`

**Description**: Advance playback by exactly 1 frame

**Request**:
```
frame-step\n
```

**Response**: None (fire-and-forget)

**Example**:
```python
send_mpv_command(pipe, "frame-step")
```

**Use Case**: "Frame Forward" button in advanced controls

---

#### 1.6 Frame Step (Backward)

**Command**: `frame-back-step`

**Description**: Rewind playback by exactly 1 frame

**Request**:
```
frame-back-step\n
```

**Response**: None (fire-and-forget)

**Example**:
```python
send_mpv_command(pipe, "frame-back-step")
```

**Use Case**: "Frame Backward" button in advanced controls

---

### 2. Property Query Commands

#### 2.1 Get Current Timestamp

**Command**: `get_property time-pos`

**Description**: Query current playback position in seconds

**Request**:
```
get_property time-pos\n
```

**Response Format**: `ANS_time-pos=<float>`

**Response Example**:
```
ANS_time-pos=123.456
```

**Parsing**:
```python
response = send_and_read(pipe, "get_property time-pos")
if response.startswith("ANS_time-pos="):
    timestamp = float(response.split('=')[1])  # 123.456
```

**Example**:
```python
timestamp = query_mpv_property(pipe, "time-pos")
# Returns: 123.456 (float seconds)
```

**Use Case**: Timestamp polling loop (10 Hz) for sync status display

---

#### 2.2 Get Pause State

**Command**: `get_property pause`

**Description**: Query whether playback is paused

**Request**:
```
get_property pause\n
```

**Response Format**: `ANS_pause=<yes|no>`

**Response Example**:
```
ANS_pause=yes   # Paused
ANS_pause=no    # Playing
```

**Parsing**:
```python
response = send_and_read(pipe, "get_property pause")
is_paused = response.split('=')[1] == "yes"
```

**Use Case**: Sync individual pause state with UI display

---

#### 2.3 Get Video Duration

**Command**: `get_property duration`

**Description**: Query total video duration in seconds

**Request**:
```
get_property duration\n
```

**Response Format**: `ANS_duration=<float>`

**Response Example**:
```
ANS_duration=2535.123
```

**Parsing**:
```python
response = send_and_read(pipe, "get_property duration")
duration = float(response.split('=')[1])  # 2535.123
```

**Use Case**: Calculate timeline scrubber range, display total duration

---

### 3. Playback Speed Control

#### 3.1 Set Playback Speed

**Command**: `set speed <float>`

**Description**: Change playback speed multiplier

**Request**:
```
set speed 0.5\n    # Half speed (slow motion)
set speed 1.0\n    # Normal speed
set speed 2.0\n    # Double speed
```

**Response**: None (fire-and-forget)

**Valid Range**: 0.01 to 100.0 (practical range: 0.25 to 2.0)

**Example**:
```python
# Speed dropdown changed to 0.5x
send_mpv_command(pipe, "set speed 0.5")
```

**Use Case**: Playback speed control (REQ-3.3)

---

#### 3.2 Mute Audio

**Command**: `set ao-volume <0-100>`

**Description**: Set audio output volume (0 = mute)

**Request**:
```
set ao-volume 0\n     # Mute audio
set ao-volume 100\n   # Full volume
```

**Response**: None (fire-and-forget)

**Example**:
```python
# Mute audio when speed ≠ 1.0x (avoid pitch distortion)
if speed != 1.0:
    send_mpv_command(pipe, "set ao-volume 0")
else:
    send_mpv_command(pipe, "set ao-volume 100")
```

**Use Case**: REQ-3.3 - Audio disabled when playback speed ≠ 1.0x

---

### 4. Window Management

#### 4.1 Set Window Geometry

**Command**: `set geometry <WxH+X+Y>`

**Description**: Resize and reposition MPV window

**Request**:
```
set geometry 640x480+100+200\n
# Width=640, Height=480, X=100, Y=200 (top-left corner)
```

**Response**: None (fire-and-forget)

**Example**:
```python
# Split-view mode: Resize selected videos
send_mpv_command(pipe, "set geometry 960x1080+0+0")  # Left half
send_mpv_command(pipe, "set geometry 960x1080+960+0")  # Right half
```

**Use Case**: REQ-3.6 - Split-view mode window resizing

---

#### 4.2 Toggle Fullscreen

**Command**: `cycle fullscreen`

**Description**: Toggle fullscreen mode

**Request**:
```
cycle fullscreen\n
```

**Response**: None (fire-and-forget)

**Use Case**: Optional fullscreen toggle for individual camera

---

### 5. On-Screen Display (OSD)

#### 5.1 Show On-Screen Text

**Command**: `show-text "<text>" <duration_ms>`

**Description**: Display text overlay on video (e.g., confirmation messages)

**Request**:
```
show-text "Offset applied: +1.5s" 2000\n
# Display text for 2000ms (2 seconds)
```

**Response**: None (fire-and-forget)

**Example**:
```python
# Show feedback when nudge applied
send_mpv_command(pipe, 'show-text "Offset: +1.5s" 1500')
```

**Use Case**: Visual feedback for nudge button clicks

---

## Launch-Time Options

### Start-Time Offset

**Flag**: `--start=<seconds>`

**Description**: Begin playback at specified timestamp (apply saved sync offset)

**Format**:
```bash
mpv.exe video.mp4 --start=+5.5   # Start 5.5 seconds into video
mpv.exe video.mp4 --start=+0.0   # Start at beginning (default)
```

**Use Case**: Apply saved `offset_seconds` from database on video load

**Example**:
```python
# Load camera with saved offset
offset = load_offset_from_db(camera_name)
start_arg = f"--start=+{offset}" if offset != 0 else ""

mpv_args = [
    mpv_exe,
    video_path,
    f"--input-ipc-server={pipe_path}",
    start_arg,  # Apply offset at launch
    "--keep-open=yes"
]
subprocess.Popen(mpv_args)
```

---

## Error Handling

### Connection Failures

**Symptoms**:
- `os.open()` raises `FileNotFoundError`: Named pipe doesn't exist (MPV not running or IPC not enabled)
- `os.open()` raises `PermissionError`: Pipe exists but access denied
- `os.write()` raises `BrokenPipeError`: MPV process terminated

**Mitigation**:
```python
def send_mpv_command_safe(pipe_path, command):
    try:
        fd = os.open(pipe_path, os.O_RDWR)
        os.write(fd, (command + '\n').encode('utf-8'))
        os.close(fd)
        return True
    except FileNotFoundError:
        print(f"Error: MPV pipe not found: {pipe_path}")
        return False
    except BrokenPipeError:
        print(f"Error: MPV process terminated")
        return False
    except Exception as e:
        print(f"IPC error: {e}")
        return False
```

---

### Query Timeouts

**Symptoms**:
- `os.read()` blocks indefinitely (MPV not responding)

**Mitigation**:
```python
import select

def query_mpv_property_timeout(pipe_path, property_name, timeout_ms=500):
    try:
        fd = os.open(pipe_path, os.O_RDWR)
        command = f"get_property {property_name}\n"
        os.write(fd, command.encode('utf-8'))

        # Wait for response with timeout
        ready = select.select([fd], [], [], timeout_ms / 1000.0)
        if ready[0]:
            response = os.read(fd, 1024).decode('utf-8').strip()
            os.close(fd)
            return response.split('=')[1] if '=' in response else None
        else:
            os.close(fd)
            print(f"Timeout querying {property_name}")
            return None
    except Exception as e:
        print(f"Query error: {e}")
        return None
```

---

## Usage Examples

### Complete Workflow: Nudge Camera by +1s

```python
# 1. User clicks "Nudge +1s" button for Cart_Left camera
camera = get_selected_camera()  # Cart_Left

# 2. Update offset in memory
camera.offset_seconds += 1.0
camera.offset_modified = True

# 3. Send IPC command to apply offset
send_mpv_command(camera.ipc_pipe_path, "seek +1.0 relative+exact")

# 4. Show visual feedback (optional)
send_mpv_command(camera.ipc_pipe_path, 'show-text "Offset: +1.0s" 1500')

# 5. Update UI
update_sync_status_display()
enable_save_button()
```

### Complete Workflow: Timestamp Polling

```python
import threading
import time

def poll_timestamps_loop(cameras):
    while running:
        for camera in cameras:
            # Query current timestamp via IPC
            response = send_and_read(camera.ipc_pipe_path, "get_property time-pos")

            if response and response.startswith("ANS_time-pos="):
                timestamp = float(response.split('=')[1])
                camera.current_timestamp = timestamp

                # Calculate sync delta
                ref_timestamp = reference_camera.current_timestamp
                delta = timestamp - ref_timestamp + camera.offset_seconds
                camera.sync_delta = delta

                # Update sync status
                if abs(delta) <= 0.3:
                    camera.sync_status = "synced"
                    camera.sync_status_text = "✓ Synced"
                else:
                    camera.sync_status = "out_of_sync"
                    camera.sync_status_text = f"+{delta:.1f}s ahead" if delta > 0 else f"{delta:.1f}s behind"

        # Update UI (thread-safe)
        root.after(0, update_camera_list_display)

        time.sleep(0.1)  # 10 Hz polling

# Start polling thread
poll_thread = threading.Thread(target=poll_timestamps_loop, args=(cameras,), daemon=True)
poll_thread.start()
```

---

## API Summary Table

| Command | Type | Response | Use Case |
|---------|------|----------|----------|
| `pause` | Control | None | Toggle pause |
| `set pause <yes\|no>` | Control | None | Explicit pause/play |
| `seek <s> relative+exact` | Control | None | Nudge controls |
| `seek <s> absolute` | Control | None | Timeline scrubber |
| `frame-step` | Control | None | Frame forward button |
| `frame-back-step` | Control | None | Frame backward button |
| `get_property time-pos` | Query | `ANS_time-pos=<float>` | Timestamp polling |
| `get_property pause` | Query | `ANS_pause=<yes\|no>` | Pause state |
| `get_property duration` | Query | `ANS_duration=<float>` | Total duration |
| `set speed <float>` | Control | None | Speed control |
| `set ao-volume <0-100>` | Control | None | Audio mute |
| `set geometry <WxH+X+Y>` | Control | None | Split-view resize |
| `show-text "<text>" <ms>` | Display | None | Visual feedback |

---

## References

- **MPV Manual**: https://mpv.io/manual/stable/
- **MPV IPC Protocol**: https://mpv.io/manual/stable/#json-ipc
- **MPV Command List**: https://mpv.io/manual/stable/#list-of-input-commands

---

**Contract Version**: 1.0
**Last Updated**: 2026-01-12
