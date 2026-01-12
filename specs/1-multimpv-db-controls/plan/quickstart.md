# Developer Quickstart: Manual Camera Synchronization Feature

**Feature**: 1-multimpv-db-controls
**Target Branch**: `1-multimpv-db-controls`
**Last Updated**: 2026-01-12

---

## Overview

This guide helps developers set up their environment and understand the codebase structure for implementing the manual camera synchronization feature in MultiMPV_Offset.

---

## Prerequisites

### Required Software

| Tool | Version | Purpose |
|------|---------|---------|
| **Python** | 3.10+ | Application runtime |
| **MPV** | 0.34+ | Video player (external process) |
| **SQLite** | 3.x | Database (Python stdlib, no installation) |
| **Git** | Any | Version control |

### Required Python Packages

```bash
# Install dependencies
pip install pillow==10.2.0  # For thumbnail generation
```

**Note**: All other dependencies are Python standard library (`tkinter`, `sqlite3`, `subprocess`, `threading`, `os`).

---

## Repository Setup

### 1. Clone and Branch

```bash
# Ensure you're on the feature branch
git checkout 1-multimpv-db-controls

# Verify current branch
git branch
# Should show: * 1-multimpv-db-controls
```

### 2. Directory Structure

```
F:\Projects\ScalpelLab_Raz\
├── MultiMPV_Offset/           # Main application directory
│   ├── multiMPV.py             # ✏️ Existing main file (will refactor)
│   ├── sync_panel.py           # 🆕 NEW: Sync control UI (to create)
│   ├── db_browser.py           # 🆕 NEW: Database case browser (to create)
│   ├── mpv_controller.py       # 🆕 NEW: IPC abstraction layer (to create)
│   ├── config.py               # Configuration management
│   ├── config.ini              # User settings
│   ├── input/                  # MPV input configs
│   │   ├── input2.conf
│   │   └── ...input9.conf
│   └── scripts/
│       └── cycle-commands.lua  # Existing MPV script
├── ScalpelDatabase.sqlite      # Database file
├── specs/                      # Feature specifications
│   └── 1-multimpv-db-controls/
│       ├── spec.md             # Feature requirements
│       ├── plan/
│       │   ├── impl-plan.md    # Implementation plan
│       │   ├── research.md     # Technical decisions
│       │   ├── data-model.md   # Entity definitions
│       │   ├── contracts/
│       │   │   └── mpv-ipc.md  # IPC API reference
│       │   └── quickstart.md   # This file
│       └── checklists/
│           └── requirements.md # Quality validation
└── scripts/
    ├── add_offset_column.py    # 🆕 NEW: DB migration script (to create)
    └── 2_4_update_db.py        # Existing DB update script
```

### 3. Database Setup

#### Check if offset_seconds column exists

```bash
# Open database in SQLite CLI
sqlite3 ScalpelDatabase.sqlite

# Check mp4_status schema
.schema mp4_status
```

**Expected Output** (should include):
```sql
CREATE TABLE mp4_status (
    ...
    offset_seconds REAL DEFAULT 0.0,  -- This line may be missing
    ...
);
```

#### If column is missing, run migration

```bash
# Create migration script (see scripts/add_offset_column.py)
python scripts/add_offset_column.py
```

**Migration Script** (`scripts/add_offset_column.py`):
```python
import sqlite3
import os

DB_PATH = "ScalpelDatabase.sqlite"

def migrate_add_offset_column():
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

---

## Configuration

### MPV Installation

1. **Download MPV** from https://mpv.io/installation/
2. **Extract** to `C:\Users\user\Downloads\MPV\` (or custom location)
3. **Update config.ini**:

```ini
[MPV]
mpv_executable = C:/Users/user/Downloads/MPV/mpv.exe
video_scale = 1280:720
force_original_aspect_ratio = decrease

[Database]
database_path = F:/Projects/ScalpelLab_Raz/ScalpelDatabase.sqlite
```

### Verify MPV Installation

```bash
# Test MPV from command line
"C:\Users\user\Downloads\MPV\mpv.exe" --version

# Expected output:
# mpv 0.34.0 Copyright © 2000-2022 mpv/MPlayer/mplayer2 projects
# ...
```

---

## Running Existing System

### Launch MultiMPV_Offset (Current Version)

```bash
cd F:\Projects\ScalpelLab_Raz\MultiMPV_Offset
python run_viewer.py
```

**Expected Behavior**:
1. Tkinter window opens with "Browse for folder" or "Load playlist" buttons
2. User selects directory with video files
3. MPV instances launch in grid layout
4. Basic control panel appears (play/pause/seek)

**Current Capabilities**:
- ✅ Load videos from file dialog or playlist
- ✅ Play/pause all videos
- ✅ Seek forward/backward ±10 seconds
- ✅ Export timestamps to text file
- ❌ NO sync status display
- ❌ NO individual camera controls
- ❌ NO database integration
- ❌ NO sync offset persistence

---

## Development Workflow

### Phase 1: Refactor Existing Code (Task 1.1)

**Goal**: Extract MPV process management into separate module for reusability.

**Files to Create/Modify**:
- 🆕 `mpv_controller.py` (new)
- ✏️ `multiMPV.py` (refactor to use MPVController)

**Steps**:

1. **Create MPVController class**:

```python
# db_offset_viewer/mpv_controller.py

import subprocess
import os
import time

class MPVController:
    """Manages MPV processes and IPC communication"""

    def __init__(self, mpv_executable_path):
        self.mpv_exe = mpv_executable_path
        self.processes = []  # List of active MPV processes

    def launch_video(self, video_path, pipe_name, geometry, start_offset=0.0):
        """Launch MPV instance with IPC enabled"""
        start_arg = f"--start=+{start_offset}" if start_offset != 0 else ""

        mpv_args = [
            self.mpv_exe,
            video_path,
            f"--input-ipc-server={pipe_name}",
            f"--geometry={geometry}",
            start_arg,
            "--keep-open=yes",
            "--force-window=yes",
            "--osc=yes"
        ]

        # Remove empty args
        mpv_args = [arg for arg in mpv_args if arg]

        process = subprocess.Popen(mpv_args)
        self.processes.append(process)

        # Wait for IPC pipe to be ready
        time.sleep(0.2)

        return process

    def send_command(self, pipe_name, command):
        """Send command to MPV via IPC (fire-and-forget)"""
        try:
            fd = os.open(pipe_name, os.O_RDWR)
            os.write(fd, (command + '\n').encode('utf-8'))
            os.close(fd)
            return True
        except Exception as e:
            print(f"IPC command error: {e}")
            return False

    def query_property(self, pipe_name, property_name):
        """Query MPV property via IPC (blocking)"""
        try:
            fd = os.open(pipe_name, os.O_RDWR)
            command = f"get_property {property_name}\n"
            os.write(fd, command.encode('utf-8'))

            # Read response
            response = os.read(fd, 1024).decode('utf-8').strip()
            os.close(fd)

            # Parse: "ANS_property_name=value"
            if response.startswith(f"ANS_{property_name}="):
                value = response.split('=', 1)[1]
                return value
            else:
                return None
        except Exception as e:
            print(f"IPC query error: {e}")
            return None

    def close_all(self):
        """Terminate all MPV processes"""
        for process in self.processes:
            if process.poll() is None:  # Still running
                process.terminate()
        self.processes.clear()
```

2. **Refactor multiMPV.py to use MPVController**:

```python
# db_offset_viewer/run_viewer.py (refactored)

from mpv_controller import MPVController
import tkinter as tk
# ... other imports ...

def main():
    # Initialize controller
    mpv_exe = load_mpv_path_from_config()
    controller = MPVController(mpv_exe)

    # Load videos
    videos = select_videos_from_dialog()

    # Launch MPV instances
    cameras = []
    for idx, video_path in enumerate(videos):
        pipe_name = f"\\\\.\\pipe\\mpv_socket_{idx}_{int(time.time())}"
        geometry = calculate_geometry(idx, len(videos))

        process = controller.launch_video(video_path, pipe_name, geometry)

        camera = Camera(
            name=f"Camera_{idx}",
            file_path=video_path,
            case_id=("unknown", 0),
            mpv_process=process,
            ipc_pipe_path=pipe_name,
        )
        cameras.append(camera)

    # Create control panel
    root = tk.Tk()
    panel = ControlPanel(root, cameras, controller)
    root.mainloop()

    # Cleanup
    controller.close_all()
```

**Testing**:
- ✅ Verify existing file dialog workflow still works
- ✅ Verify videos launch in grid
- ✅ Verify play/pause/seek commands work via MPVController

---

### Phase 2: Implement Sync Panel UI (Task 1.2 - 1.4)

**Goal**: Create sync status display with timestamp polling and nudge controls.

**Files to Create**:
- 🆕 `sync_panel.py` (new, ~600 lines)

**Key Components**:

1. **SyncPanel class** (main UI container)
2. **Timestamp polling thread** (10 Hz loop)
3. **Camera list display** (Treeview widget)
4. **Nudge control buttons** (±1s, ±0.1s)
5. **Sync status calculation** (delta from reference camera)

**Development Steps**:

1. Create basic UI layout (camera list + nudge buttons)
2. Implement timestamp polling thread
3. Add sync status calculation logic
4. Connect nudge buttons to IPC commands
5. Test with 3-4 videos to verify sync indicators

**Visual Layout**:
```
┌─────────────────────────────────────────┐
│ Case: 2023-05-15 / Case 3  [Save to DB]│
├───────────────────────┬─────────────────┤
│ Camera List           │ Nudge Controls  │
│ ☑ Cart_Center (Ref)   │                 │
│   00:15:32.1 [+0.0s]  │ Selected:       │
│   ✓ Synced            │ Cart_Left       │
│ ☑ Cart_Left           │                 │
│   00:15:33.6 [-1.5s]  │ Offset: -1.5s   │
│   +1.5s ahead ⚠️      │                 │
│ ☑ Monitor             │ [Nudge -1s]     │
│   00:15:32.0 [+0.0s]  │ [Nudge -0.1s]   │
│   ✓ Synced            │ [Nudge +0.1s]   │
│ ...                   │ [Nudge +1s]     │
└───────────────────────┴─────────────────┘
```

**Testing Checklist**:
- [ ] Camera list displays all loaded videos
- [ ] Timestamps update at 10 Hz (visible smooth counting)
- [ ] Sync status colors: green ✓ when within ±0.3s, yellow ⚠️ when outside
- [ ] Selecting camera highlights row
- [ ] Nudge buttons apply offset immediately (<100ms)
- [ ] Video seeks when nudge clicked (visible in MPV window)
- [ ] Cumulative offsets work (clicking -1s twice = -2.0s)

---

### Phase 3: Database Persistence (Task 1.5)

**Goal**: Add "Save to DB" button with MANDATORY confirmation dialog.

**Key Requirements**:
- ✅ Confirmation dialog MUST appear before ANY database write
- ✅ User MUST click "Save to Database" (ESC key = Cancel)
- ✅ Dialog lists all modified cameras and offset values
- ✅ Success message after save
- ✅ Error dialog with retry on failure

**Testing Checklist**:
- [ ] "Save to DB" button disabled when no changes
- [ ] "Save to DB" button enabled when offset modified
- [ ] Clicking "Save to DB" shows confirmation dialog
- [ ] Dialog lists correct cameras and offsets
- [ ] Clicking "Cancel" closes dialog with NO database changes
- [ ] Clicking "Yes" triggers UPDATE query and shows success
- [ ] After save, offsets persist (reopen case, verify offsets applied)

---

### Phase 4: Database Integration (Task 2.1 - 2.4)

**Goal**: Implement case browser for loading videos from database.

**Files to Create**:
- 🆕 `db_browser.py` (new, ~750 lines)

**Key Features**:
- Case list with thumbnail previews
- Filtering by date range, provider, room
- Camera selection dialog
- Thumbnail generation (background threads)

**Development Steps**:

1. Create database query functions (load cases, load cameras for case)
2. Build case browser UI (Treeview with filter widgets)
3. Add camera selection dialog
4. Implement thumbnail generation (using FFmpeg or MPV frame extraction)
5. Integrate with main application ("Open from Database" button)

**Testing Checklist**:
- [ ] Case list loads from database without errors
- [ ] Filtering updates list in real-time
- [ ] Clicking case opens camera selection dialog
- [ ] Selecting cameras launches videos with saved offsets
- [ ] Thumbnails generate in background (no UI freeze)

---

## Debugging Tips

### MPV IPC Issues

**Problem**: `FileNotFoundError` when trying to open named pipe

**Solutions**:
1. Verify MPV launched with `--input-ipc-server` flag
2. Check pipe name format: `\\.\pipe\mpv_socket_0_1234567890`
3. Wait 200ms after launching MPV before attempting IPC

**Debug Command**:
```python
import time
process = controller.launch_video(...)
time.sleep(0.5)  # Longer wait for debugging
```

---

### Database Query Issues

**Problem**: `sqlite3.OperationalError: no such column: offset_seconds`

**Solutions**:
1. Run migration script: `python scripts/add_offset_column.py`
2. Verify column exists: `sqlite3 ScalpelDatabase.sqlite ".schema mp4_status"`
3. Use `COALESCE(offset_seconds, 0.0)` in queries for graceful degradation

---

### Timestamp Polling Performance

**Problem**: GUI freezes when polling timestamps

**Solutions**:
1. Ensure polling runs in daemon thread (not main thread)
2. Use `root.after(0, callback)` to update UI (thread-safe)
3. Reduce polling rate if needed (change to 5 Hz / 200ms)

**Debug Logging**:
```python
def _poll_loop(self):
    while self.running:
        start = time.time()

        # Polling logic...

        elapsed = time.time() - start
        print(f"Poll iteration took {elapsed*1000:.1f}ms")  # Should be <50ms

        time.sleep(0.1)
```

---

## Testing Strategy

### Unit Tests (Recommended)

Create `tests/` directory with:

- `test_mpv_controller.py`: Test IPC commands, process management
- `test_sync_logic.py`: Test sync delta calculation, status classification
- `test_database.py`: Test queries, migrations

**Example Test**:
```python
# tests/test_sync_logic.py

import unittest
from sync_panel import calculate_sync_status

class TestSyncLogic(unittest.TestCase):
    def test_sync_status_synced(self):
        ref_timestamp = 100.0
        camera_timestamp = 100.2
        offset = 0.0

        delta = camera_timestamp - ref_timestamp + offset
        status = "synced" if abs(delta) <= 0.3 else "out_of_sync"

        self.assertEqual(status, "synced")  # 0.2s within tolerance

    def test_sync_status_out_of_sync(self):
        ref_timestamp = 100.0
        camera_timestamp = 101.5
        offset = 0.0

        delta = camera_timestamp - ref_timestamp + offset
        status = "synced" if abs(delta) <= 0.3 else "out_of_sync"

        self.assertEqual(status, "out_of_sync")  # 1.5s outside tolerance
```

---

### Manual Integration Testing

**Test Scenario**: End-to-end manual sync workflow

1. Open database browser
2. Select case with 6 cameras
3. Load all 6 cameras
4. Play videos
5. Identify camera that's out of sync (yellow ⚠️)
6. Select camera, click nudge buttons until green ✓
7. Repeat for all cameras
8. Click "Save to DB"
9. Verify confirmation dialog appears
10. Click "Save"
11. Close application
12. Reopen same case
13. Verify offsets applied (all cameras green ✓ immediately)

**Expected Duration**: ~3 minutes (vs. 10 minutes manually seeking)

---

## Common Pitfalls

### 1. Forgetting Thread-Safety

**Problem**: Calling Tkinter widgets from background thread causes crash

**Solution**: Always use `root.after(0, callback)` to schedule UI updates on main thread

❌ **Wrong**:
```python
def _poll_loop(self):
    while running:
        timestamp = query_timestamp(...)
        self.label.config(text=f"{timestamp:.1f}s")  # CRASH: Not thread-safe
```

✅ **Correct**:
```python
def _poll_loop(self):
    while running:
        timestamp = query_timestamp(...)
        self.root.after(0, lambda: self.label.config(text=f"{timestamp:.1f}s"))
```

---

### 2. Hardcoding File Paths

**Problem**: Paths like `C:\Users\user\...` don't work on other machines

**Solution**: Use config.ini and `os.path.expanduser()`

❌ **Wrong**:
```python
mpv_exe = "C:/Users/user/Downloads/MPV/mpv.exe"
```

✅ **Correct**:
```python
import configparser
config = configparser.ConfigParser()
config.read("config.ini")
mpv_exe = config.get("MPV", "mpv_executable")
```

---

### 3. Skipping Confirmation Dialog

**Problem**: Saving directly to database violates critical requirement (AC 22-27)

**Solution**: ALWAYS show confirmation dialog before database writes

❌ **Wrong**:
```python
def save_offsets():
    # Direct save - FORBIDDEN
    conn.execute("UPDATE mp4_status SET offset_seconds = ?", ...)
    conn.commit()
```

✅ **Correct**:
```python
def save_offsets():
    result = messagebox.askyesno("Save to database?", ...)
    if not result:
        return  # User cancelled - NO database changes
    # Proceed with save...
```

---

## Resources

### Documentation

- **Feature Spec**: `specs/1-multimpv-db-controls/spec.md`
- **Implementation Plan**: `specs/1-multimpv-db-controls/plan/impl-plan.md`
- **Data Model**: `specs/1-multimpv-db-controls/plan/data-model.md`
- **IPC API Reference**: `specs/1-multimpv-db-controls/plan/contracts/mpv-ipc.md`

### External References

- **MPV Manual**: https://mpv.io/manual/stable/
- **MPV IPC Protocol**: https://mpv.io/manual/stable/#json-ipc
- **Tkinter Documentation**: https://docs.python.org/3/library/tkinter.html
- **SQLite Documentation**: https://www.sqlite.org/docs.html

---

## Getting Help

### Questions?

1. **Check documentation first**: Review spec.md and impl-plan.md
2. **Search existing code**: Look for similar patterns in multiMPV.py
3. **Test incrementally**: Don't implement everything at once
4. **Use print debugging**: Add logging to IPC commands and polling loops

---

## Next Steps

1. ✅ Complete Phase 0: Environment setup (this guide)
2. 🔄 Begin Phase 1: Refactor multiMPV.py (Task 1.1)
3. ⏳ Implement sync panel UI (Task 1.2 - 1.4)
4. ⏳ Add database persistence (Task 1.5)
5. ⏳ Build database browser (Task 2.1 - 2.4)
6. ⏳ Add advanced playback controls (Task 3.1 - 3.3)
7. ⏳ Implement annotations (Task 4.1 - 4.2)
8. ⏳ Testing and documentation

**Estimated Timeline**: 12 days @ 8 hours/day (see impl-plan.md Phase Timeline)

---

**Quickstart Version**: 1.0
**Last Updated**: 2026-01-12
**Ready to Code!** 🚀
