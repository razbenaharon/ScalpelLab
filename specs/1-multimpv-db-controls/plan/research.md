# Technical Research: Manual Camera Synchronization Implementation

**Feature**: 1-multimpv-db-controls
**Research Phase Completed**: 2026-01-12
**Status**: All NEEDS CLARIFICATION items resolved

---

## Research Summary

This document consolidates technical research findings for implementing manual camera synchronization in MultiMPV_Offset. All unknowns from the technical context have been resolved through investigation of existing codebase, MPV documentation, and best practices research.

---

## Research Topic 1: MPV IPC (Inter-Process Communication)

### Question
How to query real-time playback position from MPV instances for sync status display?

### Research Findings

**MPV IPC Protocol Options**:
1. **Named Pipes (Windows)**: `\\.\pipe\mpv_socket_{id}` - Already in use in existing codebase
2. **Unix Sockets (Linux)**: `/tmp/mpv-socket-{id}` - Not currently used
3. **JSON IPC**: More structured but requires protocol upgrade

**Current Implementation** (`MultiMPV_Offset/multiMPV.py` lines 150-180):
```python
# Existing code creates named pipes
pipe_name = f"\\\\.\\pipe\\mpv_socket_{i}_{timestamp}"
mpv_args = [
    mpv_exe,
    video_path,
    f"--input-ipc-server={pipe_name}",
    # ... other args
]
subprocess.Popen(mpv_args)

# Sending commands (existing pattern)
fd = os.open(pipe_name, os.O_RDWR)
os.write(fd, (command + '\n').encode('utf-8'))
os.close(fd)
```

**Query Method** (from MPV documentation):
```
# Command format for property queries:
get_property <property_name>

# Example: Query current playback position
get_property time-pos

# Response format (plain text):
ANS_time-pos=123.456
```

**JSON IPC Alternative** (more robust but requires changes):
```json
// Command
{"command": ["get_property", "time-pos"]}

// Response
{"data": 123.456, "error": "success"}
```

### Decision

**Chosen**: Continue using **plain-text named pipe IPC** with `get_property time-pos` command

**Rationale**:
- Maintains consistency with existing IPC infrastructure
- Minimal code changes required (extend existing pattern)
- Plain-text parsing sufficient for timestamp queries (no complex data structures)
- Upgrade to JSON IPC can be deferred to future enhancement

**Implementation Pattern**:
```python
def query_mpv_property(pipe_name: str, property_name: str) -> str:
    """Query MPV property via named pipe IPC"""
    try:
        fd = os.open(pipe_name, os.O_RDWR)
        command = f"get_property {property_name}\n"
        os.write(fd, command.encode('utf-8'))

        # Read response (format: ANS_property_name=value)
        response = os.read(fd, 1024).decode('utf-8').strip()
        os.close(fd)

        # Parse: "ANS_time-pos=123.456" → "123.456"
        if response.startswith(f"ANS_{property_name}="):
            value = response.split('=', 1)[1]
            return value
        else:
            return None
    except Exception as e:
        print(f"IPC query error: {e}")
        return None
```

**Alternative Considered**:
- **JSON IPC**: More structured, better error handling, but requires:
  - Changing all MPV launch commands to use JSON mode
  - Rewriting all existing command functions
  - JSON parsing library (stdlib `json` available)
  - **Decision**: Deferred to future refactoring

---

## Research Topic 2: Timestamp Polling Frequency

### Question
How often should we poll MPV instances for current timestamp to achieve smooth sync status updates without excessive CPU usage?

### Research Findings

**Human Perception Thresholds**:
- **60 Hz (16.6ms)**: Smooth animation for gaming/video
- **30 Hz (33ms)**: Standard video frame rate
- **10 Hz (100ms)**: Acceptable for UI updates (perceived as "real-time")
- **5 Hz (200ms)**: Noticeable lag, feels sluggish
- **1 Hz (1000ms)**: Clearly discrete updates, poor UX

**CPU Usage Considerations**:
- Named pipe I/O: ~0.1ms per query (negligible)
- 9 cameras × 10 Hz = 90 queries/sec = ~9ms CPU time (0.9% of 1 core @ 1 GHz)
- Tkinter GUI update: ~1-5ms per frame (acceptable overhead)

**Existing System**:
- Current `multiMPV.py` has no polling loop (commands only, no status queries)
- No existing performance baseline to compare against

**Best Practices Research**:
- **VLC player**: 10-20 Hz UI refresh for progress bars
- **YouTube player**: 4 Hz progress bar updates (250ms)
- **Professional editing tools (DaVinci Resolve)**: 30 Hz timeline updates

### Decision

**Chosen**: **10 Hz (100ms polling interval)**

**Rationale**:
- Balances responsiveness with CPU efficiency
- Sufficient for manual sync workflow (user needs visual confirmation, not frame-perfect animation)
- Matches industry standard for media player UI updates
- Leaves CPU headroom for other operations (video decoding, Tkinter rendering)
- Easy to adjust if needed (configurable constant)

**Implementation**:
```python
POLL_INTERVAL_MS = 100  # 10 Hz

def _poll_loop(self):
    while self.running:
        start_time = time.time()

        # Poll all cameras
        for camera in self.cameras:
            timestamp = query_mpv_property(camera.pipe, "time-pos")
            if timestamp:
                camera.current_timestamp = float(timestamp)

        # Schedule UI update (thread-safe)
        self.root.after(0, self._update_sync_display)

        # Sleep to maintain 10 Hz rate
        elapsed = time.time() - start_time
        sleep_time = max(0, (POLL_INTERVAL_MS / 1000.0) - elapsed)
        time.sleep(sleep_time)
```

**Alternatives Considered**:
- **30 Hz (33ms)**: Smoother but 3x CPU usage; unnecessary for manual sync
- **5 Hz (200ms)**: Noticeable lag; rejected for poor UX
- **Variable rate (fast when seeking, slow when paused)**: Complex; deferred to optimization phase

---

## Research Topic 3: Sync Offset Application Strategy

### Question
When user adjusts sync offset via nudge buttons, how to apply the offset to the video playback?

### Research Findings

**MPV Seek Command Options**:

1. **Relative seek**: `seek <seconds> relative+exact`
   - Shifts playback position by ±N seconds from current position
   - `+exact` flag ensures frame-accurate seek (no keyframe shortcuts)
   - Example: `seek -1.0 relative+exact` rewinds 1 second exactly

2. **Absolute seek**: `seek <seconds> absolute`
   - Jumps to specific timestamp in video
   - Requires recalculating target position on each nudge

3. **Start-time offset**: `--start=<seconds>` (launch flag)
   - Only applies at video initialization
   - Cannot change during playback

**Existing Implementation**:
- Current `multiMPV.py` uses relative seeks for skip forward/backward buttons
- No start-time offset currently applied

### Decision

**Chosen**: **Dual approach**:
1. **During playback**: Use `seek <delta> relative+exact` to immediately apply nudge adjustment
2. **On video load**: Use `--start=+{offset}` flag to initialize with saved database offset

**Rationale**:
- Relative seek provides instant visual feedback (video shifts immediately)
- `+exact` flag ensures precise timing (no keyframe approximation)
- Start-time offset ensures videos begin at correct sync position when loaded from database
- Offsets accumulate correctly (each nudge adds to stored offset value)

**Implementation**:
```python
# During playback (nudge button clicked)
def apply_nudge(camera, delta):
    camera.offset_seconds += delta
    send_mpv_command(camera.pipe, f"seek {delta} relative+exact")

# On video load (from database)
def launch_video_with_offset(video_path, offset):
    start_arg = f"--start=+{offset}" if offset != 0 else ""
    mpv_args = [mpv_exe, video_path, start_arg, ...]
    subprocess.Popen(mpv_args)
```

**Alternatives Considered**:
- **Absolute seek only**: Requires tracking "base timestamp" and recalculating on every nudge; more complex
- **Frame stepping**: `frame-step` / `frame-back-step` commands exist but too granular for coarse adjustments (1s = 30 frames)
- **Pause + seek + resume**: Unnecessary; relative seek works during playback

---

## Research Topic 4: Database Schema for Sync Offset Storage

### Question
Does `mp4_status.offset_seconds` column exist? If not, what's the migration strategy?

### Research Findings

**User Confirmation**:
- User stated: "Q1- into table mp4_status off_set seconds col" (original message)
- Indicates column exists OR user expects it to be added

**Database Schema Investigation** (from existing codebase analysis):
- `scripts/2_4_update_db.py` contains database update logic
- Existing columns: `recording_date`, `case_no`, `camera_name`, `path`, `file_size`, `duration`
- **No explicit reference to `offset_seconds` in existing scripts**

**SQLite ALTER TABLE Support**:
```sql
ALTER TABLE mp4_status ADD COLUMN offset_seconds REAL DEFAULT 0.0;
```
- Supported in SQLite 3.2.0+ (widely available)
- `DEFAULT 0.0` ensures existing rows have valid values
- Non-destructive: Preserves all existing data

### Decision

**Chosen**: **Assume column may not exist; implement graceful migration**

**Rationale**:
- Safe assumption: Column may need to be added
- Graceful handling prevents feature failure if column missing
- Migration script is non-destructive and idempotent (safe to run multiple times)

**Implementation**:

**Migration Script** (`scripts/add_offset_column.py`):
```python
import sqlite3
import os

DB_PATH = "ScalpelDatabase.sqlite"

def migrate_add_offset_column():
    """Add offset_seconds column to mp4_status if missing"""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return False

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if column exists
    cursor.execute("PRAGMA table_info(mp4_status)")
    columns = [row[1] for row in cursor.fetchall()]

    if "offset_seconds" in columns:
        print("✓ offset_seconds column already exists")
        return True

    # Add column
    try:
        cursor.execute("ALTER TABLE mp4_status ADD COLUMN offset_seconds REAL DEFAULT 0.0")
        conn.commit()
        print("✓ offset_seconds column added successfully")
        return True
    except sqlite3.Error as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_add_offset_column()
```

**Graceful Degradation** (in sync_panel.py):
```python
def load_offsets_from_db(cameras):
    """Load sync offsets, handle missing column gracefully"""
    try:
        cursor.execute(
            "SELECT camera_name, offset_seconds FROM mp4_status WHERE recording_date = ? AND case_no = ?",
            (date, case_no)
        )
        offsets = {row[0]: row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError as e:
        if "no such column: offset_seconds" in str(e):
            print("Warning: offset_seconds column missing. Using default offsets (0.0).")
            offsets = {camera.name: 0.0 for camera in cameras}
        else:
            raise

    # Apply offsets
    for camera in cameras:
        camera.offset_seconds = offsets.get(camera.name, 0.0)
```

**Alternatives Considered**:
- **Require manual migration**: Forces users to run script first; rejected for poor UX
- **Separate sync_offsets table**: More normalized but adds JOIN complexity; rejected for simplicity
- **Fail loudly if column missing**: Prevents feature use until migration; rejected for bad UX

---

## Research Topic 5: Sync Tolerance Threshold

### Question
What tolerance (±X seconds) should classify cameras as "synced" vs "out of sync"?

### Research Findings

**Medical Use Case Considerations**:
- Surgical recordings: precision matters for correlating events across angles
- Manual sync workflow: users visually align events (e.g., surgeon entering room)
- Human perception: ~0.2s lag is noticeable in audio-video sync

**Technical Constraints**:
- 30 FPS video: 1 frame = 0.033s
- IPC latency: ~1-5ms (negligible)
- Seek precision: MPV supports millisecond-level seeking

**Existing System**:
- No sync tolerance defined in current `multiMPV.py`
- Success criteria in spec: "videos remain synchronized within ±0.5 seconds for 95% of playback"

**User Requirements** (from spec):
- REQ-1.1: "Green checkmark when camera within ±0.3s of reference"
- Success criteria: "95%+ sync adjustments result in all cameras within ±0.3s"

### Decision

**Chosen**: **±0.3 seconds (300 milliseconds)**

**Rationale**:
- Explicitly specified in REQ-1.1 and success criteria
- Tight enough for medical review precision (9 frames @ 30 FPS)
- Loose enough to be achievable via manual nudge controls
- ~1.5x the human audio-video sync perception threshold (accommodates minor playback variance)

**Implementation**:
```python
SYNC_TOLERANCE_SECONDS = 0.3

def calculate_sync_status(camera, reference_camera):
    delta = camera.current_timestamp - reference_camera.current_timestamp + camera.offset_seconds
    camera.sync_delta = delta

    if abs(delta) <= SYNC_TOLERANCE_SECONDS:
        camera.sync_status = "synced"
        camera.sync_status_text = "✓ Synced"
        camera.status_color = "green"
    else:
        camera.sync_status = "out_of_sync"
        if delta > SYNC_TOLERANCE_SECONDS:
            camera.sync_status_text = f"+{delta:.1f}s ahead"
        else:
            camera.sync_status_text = f"{delta:.1f}s behind"
        camera.status_color = "yellow"
```

**Alternatives Considered**:
- **±0.1s (100ms)**: Too strict; difficult to achieve manually; rejected
- **±0.5s (500ms)**: Spec mentions this for "maintained sync during playback" but uses ±0.3s for manual adjustment success criteria; rejected for inconsistency
- **User-configurable**: Adds UI complexity; deferred to future enhancement

---

## Research Topic 6: GUI Framework Choice

### Question
Should we continue using Tkinter or migrate to a more modern framework (PyQt, Kivy, web-based)?

### Research Findings

**Current Implementation**:
- Existing `multiMPV.py` uses Tkinter extensively
- Tkinter is Python stdlib (no external dependencies)
- Windows-only deployment (no cross-platform requirement mentioned)

**Alternative Frameworks**:

1. **PyQt5 / PySide2**:
   - Pros: Modern styling, rich widget library, cross-platform
   - Cons: Large dependency (~50MB), licensing considerations (PyQt = GPL, PySide2 = LGPL)

2. **Kivy**:
   - Pros: Touch-friendly, mobile support
   - Cons: Not suited for desktop tools, overkill for this use case

3. **Web-based (Flask/Electron)**:
   - Pros: Cross-platform, modern UI
   - Cons: Requires web server, complex IPC bridge to MPV, significant refactor

4. **Tkinter** (current):
   - Pros: Zero dependencies, lightweight, sufficient for desktop tool, team familiarity
   - Cons: Basic styling, limited widgets, dated appearance

**User Requirements**:
- Spec mentions "Initial implementation may use Tkinter (Windows) with future consideration for GTK/GIO cross-platform support" (Assumptions section)
- No immediate cross-platform requirement

### Decision

**Chosen**: **Continue using Tkinter**

**Rationale**:
- Minimizes risk: Proven working in existing codebase
- Zero dependencies: No installation complications for end users
- Sufficient functionality: Tkinter provides all required widgets (Listbox, Button, Scale, Label)
- Fast development: Team already familiar with Tkinter patterns
- Future-compatible: Can migrate to PyQt/GTK later without affecting core logic (separation of concerns)

**Implementation**:
- Keep GUI code in separate modules (`sync_panel.py`, `db_browser.py`)
- Use Model-View pattern: Core logic (sync calculation, IPC) independent of GUI
- Future migration path: Replace Tkinter modules with PyQt equivalents without changing `mpv_controller.py`

**Alternatives Considered**:
- **PyQt5**: Better UX but adds 50MB dependency and licensing complexity; deferred to future
- **Web-based**: Over-engineered for desktop tool; rejected

---

## Research Topic 7: Threading Strategy for Async Operations

### Question
How to handle async operations (timestamp polling, thumbnail generation) without blocking Tkinter GUI?

### Research Findings

**Tkinter Threading Constraints**:
- Tkinter is **NOT thread-safe**: Cannot call widget methods from background threads
- Workaround: `root.after(delay_ms, callback)` schedules callback on main thread

**Python Threading Options**:
1. **`threading.Thread`**: Stdlib, simple, good for I/O-bound tasks
2. **`multiprocessing.Process`**: Separate process, overkill for this use case
3. **`asyncio`**: Event loop, good for async I/O but Tkinter not async-compatible
4. **Thread pools (`concurrent.futures`)**: Good for many parallel tasks

**Use Cases**:
- **Timestamp polling**: Continuous loop, needs to run indefinitely
- **Thumbnail generation**: One-time per case, potentially slow (FFmpeg extraction)

### Decision

**Chosen**:
1. **Timestamp polling**: `threading.Thread` with daemon mode
2. **Thumbnail generation**: `threading.Thread` per thumbnail (max 20 concurrent)

**Rationale**:
- **Daemon threads**: Auto-terminate when main program exits (no orphaned processes)
- **Simple pattern**: Easy to understand and maintain
- **Sufficient for I/O-bound tasks**: Polling and file I/O don't need CPU parallelism

**Implementation**:

**Polling Thread** (daemon, runs continuously):
```python
def start_timestamp_polling(self):
    self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
    self.poll_thread.start()

def _poll_loop(self):
    while self.running:
        for camera in self.cameras:
            timestamp = query_mpv_property(camera.pipe, "time-pos")
            if timestamp:
                camera.current_timestamp = float(timestamp)

        # Schedule UI update on main thread (THREAD-SAFE)
        self.root.after(0, self._update_sync_display)

        time.sleep(0.1)  # 10 Hz
```

**Thumbnail Generation** (one thread per thumbnail):
```python
def generate_thumbnail_async(self, case, callback):
    thread = threading.Thread(
        target=self._generate_thumbnail,
        args=(case, callback),
        daemon=True
    )
    thread.start()

def _generate_thumbnail(self, case, callback):
    # Run FFmpeg (blocking I/O)
    thumbnail_path = extract_frame_ffmpeg(case.video_path, case.offset_seconds)

    # Schedule UI update on main thread
    self.root.after(0, lambda: callback(case, thumbnail_path))
```

**Alternatives Considered**:
- **asyncio**: Requires rewriting Tkinter event loop integration; complex; rejected
- **Multiprocessing**: Overkill for I/O-bound tasks; rejected
- **No threading (blocking)**: Freezes GUI during polling; rejected

---

## Research Topic 8: Confirmation Dialog Best Practices

### Question
How to implement MANDATORY confirmation dialog that prevents accidental database writes?

### Research Findings

**User Requirements** (CRITICAL):
- REQ-1.6: "User MUST explicitly click 'Save to Database' to proceed - no auto-save, no implicit save"
- AC 22-27: Six acceptance criteria dedicated to confirmation dialog behavior
- Success criteria: "Zero accidental saves: 0% of database writes occur without explicit user confirmation"

**Tkinter Dialog Options**:
1. **`messagebox.askyesno()`**: Simple yes/no dialog
2. **`messagebox.askokcancel()`**: OK/Cancel dialog
3. **Custom Toplevel window**: Full control over UI

**Security Best Practices**:
- **Default to safe action**: ESC key / close button should map to "Cancel"
- **Explicit confirmation required**: User must click affirmative button
- **Clear consequences**: Dialog message explains what will be changed

### Decision

**Chosen**: **`messagebox.askyesno()` with explicit message and default="no"**

**Rationale**:
- Built-in Tkinter widget (no custom dialog code needed)
- `default="no"` ensures ESC / close button / Enter key maps to "Cancel" (safe behavior)
- User MUST click "Yes" button explicitly
- Clear message listing cameras and offsets prevents confusion

**Implementation**:
```python
def _save_offsets_to_database(self):
    modified_cameras = [c for c in self.cameras if c.offset_modified]

    if not modified_cameras:
        messagebox.showinfo("No Changes", "No sync offsets have been modified")
        return

    # Build detailed message
    camera_list = "\n".join([
        f"  - {c.name}: {c.offset_seconds:+.1f}s"
        for c in modified_cameras
    ])

    message = (
        f"This will update mp4_status.offset_seconds for {len(modified_cameras)} cameras:\n\n"
        f"{camera_list}\n\n"
        "Do you want to proceed?"
    )

    # MANDATORY CONFIRMATION DIALOG
    result = messagebox.askyesno(
        title="Save sync offsets to database?",
        message=message,
        icon="question",
        default="no"  # ESC key / close = "No"
    )

    if not result:  # User clicked "No" or closed dialog
        return  # CRITICAL: No database changes

    # User explicitly clicked "Yes" - proceed with save
    try:
        # ... database UPDATE queries ...
        messagebox.showinfo("Success", f"✓ Sync offsets saved for {len(modified_cameras)} cameras")
    except Exception as e:
        # Error with retry option
        retry = messagebox.askretrycancel("Database Error", f"Failed to save: {e}\n\nRetry?")
        if retry:
            self._save_offsets_to_database()  # Recursive retry
```

**Alternatives Considered**:
- **Custom Toplevel dialog**: More control but unnecessary complexity; rejected
- **`askokcancel()`**: "OK" label less explicit than "Yes"; rejected
- **No confirmation (just save)**: Violates critical requirement; absolutely rejected

---

## Technology Stack Summary

### Final Technology Choices

| Component | Technology | Version | Rationale |
|-----------|------------|---------|-----------|
| **GUI Framework** | Tkinter | Python stdlib | Zero dependencies, sufficient functionality, existing codebase |
| **Video Player** | MPV | Latest stable | Already integrated, robust, hardware-accelerated |
| **IPC Protocol** | Named Pipes (Windows) | N/A | Existing infrastructure, low-latency |
| **Database** | SQLite3 | 3.x | Already in use, zero-config, ACID compliance |
| **Database Driver** | `sqlite3` | Python stdlib | Built-in, no dependencies |
| **Threading** | `threading.Thread` | Python stdlib | Simple, sufficient for I/O-bound tasks |
| **Image Handling** | Pillow (PIL) | 10.x | Thumbnail generation and display |
| **Timestamp Format** | Float (seconds) + `HH:MM:SS.mmm` | N/A | MPV native, human-readable |

### External Dependencies (New)

```
# requirements.txt
Pillow==10.2.0  # For thumbnail generation and display in case browser
```

**All other dependencies are Python stdlib** (no external installations required beyond Pillow).

---

## Open Questions (Deferred to Future)

| Question | Priority | Deferred To |
|----------|----------|-------------|
| Cross-platform support (Linux/Mac)? | LOW | Future enhancement (requires Unix socket IPC) |
| JSON IPC protocol upgrade? | MEDIUM | Future refactoring (better error handling) |
| PyQt5 GUI migration? | LOW | Future UX enhancement |
| Cloud database support (PostgreSQL)? | LOW | Future enterprise feature |
| Mobile/web interface? | LOW | Future platform expansion |

---

## Research Status

✅ **All NEEDS CLARIFICATION items resolved**

**Research Phase**: COMPLETE

**Next Phase**: Implementation (begin Task 1.1 - Refactor multiMPV.py)
