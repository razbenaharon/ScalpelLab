# Implementation Plan: MultiMPV_Offset Manual Camera Synchronization

**Feature ID**: 1-multimpv-db-controls
**Plan Created**: 2026-01-12
**Implementation Priority**: PRIMARY - Manual Camera Sync → Supporting - Database Integration → Supporting - Advanced Controls
**Target Files**: `MultiMPV_Offset/multiMPV.py`, `MultiMPV_Offset/sync_panel.py` (new), `MultiMPV_Offset/db_browser.py` (new)

---

## Executive Summary

This plan outlines the technical implementation for upgrading MultiMPV_Offset with **manual camera synchronization** as the PRIMARY feature. The existing system (`MultiMPV_Offset/multiMPV.py`) uses Windows named pipes for IPC with MPV processes and a basic Tkinter control panel. We will:

1. **Refactor sync control** into dedicated `sync_panel.py` module with real-time status display and nudge controls
2. **Add database integration** in `db_browser.py` for querying cases and persisting sync offsets
3. **Enhance existing control panel** with timeline scrubber, frame navigation, and split-view mode
4. **Maintain backward compatibility** with existing video loading methods (file dialog, playlist files)

**Key Technical Constraint**: User MUST explicitly approve database writes via confirmation dialog (REQ-1.6, AC 22-27).

---

## Phase 0: Research & Technical Decisions

### Technology Stack (Existing + New)

| Component | Technology | Rationale |
|-----------|------------|-----------|
| **GUI Framework** | Tkinter (Python stdlib) | Already in use; lightweight; sufficient for desktop tool; avoids new dependencies |
| **Video Player** | MPV (external process) | Already in use; robust; hardware-accelerated; IPC via named pipes |
| **IPC Method** | Windows Named Pipes (`\\\\.\\pipe\\mpv_socket_{id}`) | Already in use; low-latency command/query interface |
| **Database** | SQLite3 (Python stdlib) | Already in use for ScalpelDatabase.sqlite; zero-config; ACID compliance |
| **Database Driver** | `sqlite3` (Python stdlib) | Built-in; no external dependencies |
| **Threading** | `threading.Thread` (Python stdlib) | For async timestamp polling and thumbnail generation |
| **Timestamp Format** | `HH:MM:SS.mmm` strings + float seconds | MPV native format; human-readable; precise to milliseconds |

### Key Technical Decisions

#### Decision 1: Sync Status Calculation Method

**Decision**: Poll all MPV instances for `time-pos` property at 10 Hz (100ms intervals) using named pipe IPC

**Rationale**:
- MPV provides accurate `time-pos` property via IPC (seconds as float, precision to 0.001s)
- 10 Hz refresh rate provides smooth visual feedback without excessive CPU usage
- Existing system already uses named pipes for commands; extends naturally to queries
- Alternative (MPV property-change events) requires JSON IPC mode not currently implemented

**Implementation**:
```python
def poll_timestamps():
    while running:
        for camera in cameras:
            send_ipc_command(camera.pipe, '{"command": ["get_property", "time-pos"]}')
            response = read_ipc_response(camera.pipe)
            camera.current_timestamp = parse_json(response)["data"]
        time.sleep(0.1)  # 10 Hz
```

**Alternatives Considered**:
- **Event-driven property observation**: Requires switching to JSON IPC protocol; more complex; deferred to future
- **Manual timestamp tracking**: Inaccurate due to seek operations, pause events, frame drops; rejected

---

#### Decision 2: Sync Offset Storage Strategy

**Decision**: Store offsets in `mp4_status.offset_seconds` column (REAL type); apply as initial seek on video load

**Rationale**:
- `offset_seconds` column already exists (confirmed from problem statement: "into table mp4_status off_set seconds col")
- Simple implementation: `mpv video.mp4 --start=+{offset}` applies offset at launch
- Persistent across sessions; no external files to manage
- Confirmation dialog prevents accidental writes (REQ-1.6 requirement)

**Database Schema** (assumed existing):
```sql
CREATE TABLE mp4_status (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    camera_name TEXT NOT NULL,
    path TEXT NOT NULL,
    file_size INTEGER,
    duration REAL,
    offset_seconds REAL DEFAULT 0.0,  -- Sync offset in seconds
    PRIMARY KEY (recording_date, case_no, camera_name)
);
```

**Update Query** (with user confirmation):
```sql
UPDATE mp4_status
SET offset_seconds = ?
WHERE recording_date = ? AND case_no = ? AND camera_name = ?
```

**Alternatives Considered**:
- **Separate sync_offsets table**: More normalized but adds complexity; rejected for simplicity
- **JSON file per case**: Loses centralized database benefits; harder to query; rejected
- **In-memory only**: Loses persistence; rejected per user requirements

---

#### Decision 3: Sync Status Indicator Logic

**Decision**: Calculate sync delta relative to reference camera; classify as "Synced" (green ✓) if within ±0.3s, otherwise "Out of Sync" (yellow ⚠️)

**Rationale**:
- ±0.3s tolerance accounts for frame time at 30 FPS (0.033s) plus small network/IPC latency
- Visual color coding provides instant feedback (no need to read numbers)
- Reference camera concept simplifies mental model (all others relative to one source)
- User can change reference camera if needed (e.g., if reference has bad footage)

**Calculation**:
```python
reference_timestamp = cameras[reference_index].current_timestamp
for camera in cameras:
    delta = camera.current_timestamp - reference_timestamp + camera.offset
    camera.sync_delta = delta
    camera.status = "synced" if abs(delta) <= 0.3 else "out_of_sync"
    camera.status_text = f"+{delta:.1f}s ahead" if delta > 0.3 else f"{delta:.1f}s behind" if delta < -0.3 else "✓ Synced"
```

**Alternatives Considered**:
- **Absolute timestamp matching**: Confusing when videos have different start times; rejected
- **Tighter tolerance (±0.1s)**: Too strict for manual adjustment workflow; would frustrate users; rejected
- **Looser tolerance (±0.5s)**: Acceptable for some use cases but user specified ±0.3s in success criteria

---

#### Decision 4: GUI Layout for Sync Panel

**Decision**: Tkinter Frame-based layout with camera list (Listbox or Treeview) on left, nudge controls on right, global controls at bottom

**Rationale**:
- Tkinter Listbox provides clickable row selection with built-in highlight
- Separates camera-specific controls (left) from global controls (bottom) for visual clarity
- Always-on-top window (`root.wm_attributes('-topmost', 1)`) ensures visibility over MPV windows
- Resizable design accommodates 1-9 cameras without scrolling (minimum 600x400px per REQ-3.7)

**Layout Mockup**:
```
┌─────────────────────────────────────────────────────────────┐
│ Case: 2023-05-15 / Case 3 / Dr. Smith        [Save to DB]  │
├───────────────────────────────────┬─────────────────────────┤
│ Camera List                       │ Nudge Controls          │
│ ☑ Cart_Center  [Reference]        │                         │
│   00:15:32.1  [+0.0s] ✓ Synced    │   Selected: Cart_Left   │
│                                    │                         │
│ ☑ Cart_Left   [+1.5s ahead] ⚠️     │   Offset: -1.5s         │
│   00:15:33.6  [-1.5s applied]     │                         │
│   [⏸] [◀5s] [5s▶]                │   [Nudge -1s]           │
│                                    │   [Nudge -0.1s]         │
│ ☑ Monitor     [✓ Synced]           │   [Nudge +0.1s]         │
│   00:15:32.0  [+0.0s]             │   [Nudge +1s]           │
│   [⏵] [◀5s] [5s▶]                │                         │
│ ...                                │                         │
├───────────────────────────────────┴─────────────────────────┤
│ Global: [⏵ Play All] [⏸ Pause All] Speed: [1.0x ▼]        │
│ Timeline: [━━━━━●─────────────] 00:15:32 / 00:42:15       │
└─────────────────────────────────────────────────────────────┘
```

**Key UI Elements**:
- **Camera List**: Each row shows camera name, timestamp, offset, status icon
- **Individual Controls**: Per-camera pause/play and seek buttons inline with camera row
- **Nudge Controls**: Large buttons on right side (applies to selected camera only)
- **Global Controls**: Bottom section for play all, pause all, timeline scrubber
- **Save Button**: Top-right corner, large and prominent, disabled when no changes

**Alternatives Considered**:
- **Tabbed interface**: Hides camera list when on other tabs; rejected for always-visible sync status
- **Separate window for nudge controls**: Too many windows; rejected for single-window design
- **Horizontal camera strip**: Doesn't scale to 9 cameras; rejected for vertical list

---

### Unknowns Resolved

| Unknown | Resolution |
|---------|------------|
| How to query MPV timestamp? | Use IPC named pipe with command: `{"command": ["get_property", "time-pos"]}` |
| How often to poll timestamps? | 10 Hz (100ms intervals) balances responsiveness with CPU usage |
| Where to store sync offsets in DB? | `mp4_status.offset_seconds` column (REAL type), confirmed existing from user |
| How to apply offsets on video load? | MPV `--start=+{offset}` flag shifts playback start position |
| How to prevent accidental DB writes? | Confirmation dialog with explicit "Save to Database" button click required |
| What sync tolerance to use? | ±0.3s per REQ-1.1 and success criteria |
| How to handle missing offset_seconds column? | Graceful degradation: treat NULL as 0.0, provide migration script if column doesn't exist |

---

## Phase 1: Architecture & Data Model

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Tkinter GUI                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐ │
│  │ sync_panel.py    │  │ db_browser.py    │  │ multiMPV.py  │ │
│  │ - Camera list    │  │ - Case browser   │  │ - Main window│ │
│  │ - Nudge controls │  │ - Filter UI      │  │ - Video grid │ │
│  │ - Status display │  │ - Thumbnail gen  │  │ - Timeline   │ │
│  └────────┬─────────┘  └────────┬─────────┘  └──────┬───────┘ │
│           │                     │                    │          │
│           └─────────────────────┴────────────────────┘          │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
           ┌───────────────────────┴───────────────────────┐
           │                                               │
     ┌─────▼─────┐                                  ┌──────▼──────┐
     │ IPC Layer │                                  │  DB Layer   │
     │ (named    │                                  │ (sqlite3)   │
     │  pipes)   │                                  │             │
     └─────┬─────┘                                  └──────┬──────┘
           │                                               │
    ┌──────▼──────────────────────┐              ┌────────▼────────┐
    │   MPV Instances (1-9)       │              │ ScalpelDatabase │
    │   - Video playback          │              │   .sqlite       │
    │   - IPC server enabled      │              │ - recording_    │
    │   - Individual windows      │              │   details       │
    └─────────────────────────────┘              │ - mp4_status    │
                                                  └─────────────────┘
```

### Module Structure

**File Organization**:
```
MultiMPV_Offset/
├── multiMPV.py              # Main entry point (refactored)
├── sync_panel.py            # NEW: Manual sync UI and logic
├── db_browser.py            # NEW: Database case browser
├── mpv_controller.py        # NEW: IPC abstraction layer
├── config.py                # Configuration management
├── config.ini               # User settings
├── input/                   # Existing input configs
│   ├── input2.conf
│   └── ...input9.conf
└── scripts/
    └── cycle-commands.lua   # Existing MPV script
```

**Import Dependencies**:
```python
# run_viewer.py
from sync_panel import SyncPanel
from db_browser import DatabaseBrowser
from mpv_controller import MPVController

# sync_panel.py
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time

# db_browser.py
import sqlite3
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk  # For thumbnail display

# mpv_controller.py
import subprocess
import os
import json
```

---

### Data Model

#### Entity: Camera

**Purpose**: Represents a single video camera/angle in the multi-camera setup

**Attributes**:
```python
@dataclass
class Camera:
    # Identity
    name: str              # e.g., "Cart_Center", "Monitor", "Room_Left"
    file_path: str         # Absolute path to video file
    case_id: tuple         # (recording_date, case_no) for DB lookups

    # Playback State
    mpv_process: subprocess.Popen   # MPV subprocess handle
    ipc_pipe_path: str              # Named pipe path for IPC
    current_timestamp: float        # Current playback position (seconds)
    is_paused: bool                 # Individual pause state

    # Sync State
    offset_seconds: float           # Applied sync offset (from DB or user adjustment)
    offset_modified: bool           # True if offset changed since last DB save
    sync_delta: float               # Calculated delta from reference camera
    sync_status: str                # "synced" | "out_of_sync"
    sync_status_text: str           # Display text: "✓ Synced" | "+1.5s ahead"

    # UI State
    is_selected: bool               # Currently selected in sync panel list
    is_reference: bool              # Designated as reference camera
```

**Relationships**:
- Belongs to one Case (via case_id foreign key)
- References one MPV process (one-to-one)
- References one IPC pipe (one-to-one)

**Business Rules**:
- `offset_seconds` range: -300.0 to +300.0 (±5 minutes max offset)
- `current_timestamp` must be ≥ 0.0 (no negative timestamps)
- Only one camera can have `is_reference = True` per session
- `offset_modified` resets to False after successful DB save

**State Transitions**:
```
offset_modified:
  False → True: When user clicks nudge button
  True → False: When "Save to DB" succeeds OR when loading from DB

sync_status:
  synced ↔ out_of_sync: Recalculated every 100ms based on sync_delta

is_paused:
  False → True: User clicks individual pause button
  True → False: User clicks individual play button OR global "Play All"
```

---

#### Entity: Case

**Purpose**: Represents a surgical case with multiple camera recordings

**Attributes**:
```python
@dataclass
class Case:
    # Identity
    recording_date: str    # ISO format: "2023-05-15"
    case_no: int           # Case number for that date
    room: str              # Operating room identifier

    # Metadata
    anesthesiologist_name: str
    camera_count: int
    total_duration: float  # Longest camera duration (seconds)

    # Relationships
    cameras: List[CameraMetadata]  # Available camera files
```

**CameraMetadata** (nested):
```python
@dataclass
class CameraMetadata:
    camera_name: str       # e.g., "Cart_Center"
    file_path: str         # Absolute path from mp4_status.path
    duration: float        # Video duration (seconds)
    file_size: int         # File size (bytes)
    offset_seconds: float  # Saved sync offset (or 0.0)
    file_exists: bool      # Path validation result
```

**Business Rules**:
- `(recording_date, case_no, room)` forms unique identifier
- `camera_count` must match `len(cameras)`
- All camera files must be .mp4, .mkv, or .avi format
- Maximum 9 cameras supported per case (per REQ-2.4)

---

#### Entity: SyncAdjustment (in-memory only, not persisted until user confirms)

**Purpose**: Tracks pending sync offset changes before database write

**Attributes**:
```python
@dataclass
class SyncAdjustment:
    camera_name: str
    case_id: tuple         # (recording_date, case_no)
    old_offset: float      # Original value from DB
    new_offset: float      # User-adjusted value
    timestamp: datetime    # When adjustment was made
```

**Business Rules**:
- Cleared after successful DB save
- Discarded if user cancels confirmation dialog
- Used to populate confirmation dialog list

---

### Database Schema (Existing + Assumptions)

**Assumed Existing Tables**:

```sql
-- Cases table
CREATE TABLE IF NOT EXISTS recording_details (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    room TEXT,
    anesthesiologist_key TEXT,
    PRIMARY KEY (recording_date, case_no)
);

-- Camera files table
CREATE TABLE IF NOT EXISTS mp4_status (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    camera_name TEXT NOT NULL,
    path TEXT NOT NULL,
    file_size INTEGER,
    duration REAL,
    offset_seconds REAL DEFAULT 0.0,  -- SYNC OFFSET STORAGE
    PRIMARY KEY (recording_date, case_no, camera_name),
    FOREIGN KEY (recording_date, case_no) REFERENCES recording_details(recording_date, case_no)
);

-- Provider lookup table
CREATE TABLE IF NOT EXISTS anesthesiology (
    key TEXT PRIMARY KEY,
    first_name TEXT,
    last_name TEXT
);
```

**Migration Required** (if offset_seconds doesn't exist):

```sql
-- Add offset_seconds column if missing
ALTER TABLE mp4_status ADD COLUMN offset_seconds REAL DEFAULT 0.0;
```

**Query: Load Case with Cameras**:
```sql
SELECT
    rd.recording_date,
    rd.case_no,
    rd.room,
    a.first_name || ' ' || a.last_name AS anesthesiologist_name,
    COUNT(ms.camera_name) AS camera_count,
    MAX(ms.duration) AS total_duration
FROM recording_details rd
LEFT JOIN anesthesiology a ON rd.anesthesiologist_key = a.key
LEFT JOIN mp4_status ms ON rd.recording_date = ms.recording_date AND rd.case_no = ms.case_no
WHERE ms.path IS NOT NULL
GROUP BY rd.recording_date, rd.case_no
ORDER BY rd.recording_date DESC
LIMIT 20;
```

**Query: Load Camera Files for Case**:
```sql
SELECT
    camera_name,
    path,
    duration,
    file_size,
    COALESCE(offset_seconds, 0.0) AS offset_seconds
FROM mp4_status
WHERE recording_date = ? AND case_no = ?
ORDER BY camera_name;
```

**Update: Save Sync Offsets**:
```sql
UPDATE mp4_status
SET offset_seconds = ?
WHERE recording_date = ? AND case_no = ? AND camera_name = ?;
```

---

## Phase 2: Implementation Plan (Feature-by-Feature)

### Priority 1: PRIMARY FEATURE - Manual Camera Synchronization

#### Task 1.1: Refactor existing multiMPV.py for modularity

**Estimated Effort**: 4 hours

**Changes**:
- Extract MPV process management into `mpv_controller.py`
- Create `MPVController` class with methods: `launch_video()`, `send_command()`, `query_property()`, `close()`
- Replace inline subprocess calls with MPVController API
- Maintain backward compatibility with existing file loading workflow

**Files Modified**:
- `multiMPV.py` (refactored)
- `mpv_controller.py` (new, ~200 lines)

**Testing**:
- Verify existing file dialog/playlist loading still works
- Verify play/pause/seek commands still work via MPVController

---

#### Task 1.2: Implement timestamp polling thread

**Estimated Effort**: 3 hours

**Implementation**:
```python
# In sync_panel.py
class SyncPanel:
    def __init__(self, cameras, mpv_controller):
        self.cameras = cameras
        self.controller = mpv_controller
        self.running = False
        self.poll_thread = None

    def start_polling(self):
        self.running = True
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.poll_thread.start()

    def _poll_loop(self):
        while self.running:
            for camera in self.cameras:
                try:
                    timestamp = self.controller.query_property(camera.ipc_pipe_path, "time-pos")
                    camera.current_timestamp = float(timestamp)
                except Exception as e:
                    print(f"Error polling {camera.name}: {e}")

            # Update UI (must use tk.after for thread-safe GUI update)
            self.root.after(0, self._update_camera_list_display)

            time.sleep(0.1)  # 10 Hz

    def stop_polling(self):
        self.running = False
        if self.poll_thread:
            self.poll_thread.join(timeout=1.0)
```

**Files Modified**:
- `sync_panel.py` (new, ~100 lines)

**Testing**:
- Verify timestamps update at 10 Hz (visible in UI)
- Verify no race conditions or GUI freezing
- Verify thread cleanup on window close

---

#### Task 1.3: Build sync status display UI

**Estimated Effort**: 6 hours

**Implementation**:
```python
# In sync_panel.py
def _build_camera_list_ui(self):
    # Camera list (left side)
    list_frame = ttk.Frame(self.master)
    list_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

    # Treeview for camera list (columns: Name, Timestamp, Offset, Status)
    columns = ("name", "timestamp", "offset", "status")
    self.camera_tree = ttk.Treeview(list_frame, columns=columns, show="tree headings", selectmode="browse")
    self.camera_tree.heading("name", text="Camera")
    self.camera_tree.heading("timestamp", text="Timestamp")
    self.camera_tree.heading("offset", text="Offset")
    self.camera_tree.heading("status", text="Status")
    self.camera_tree.pack(fill="both", expand=True)

    # Bind selection event
    self.camera_tree.bind("<<TreeviewSelect>>", self._on_camera_selected)

    # Individual control buttons per camera (inline)
    for camera in self.cameras:
        btn_frame = ttk.Frame(list_frame)
        ttk.Button(btn_frame, text="⏸" if not camera.is_paused else "⏵",
                   command=lambda c=camera: self._toggle_individual_pause(c)).pack(side="left")
        ttk.Button(btn_frame, text="◀5s",
                   command=lambda c=camera: self._individual_seek(c, -5)).pack(side="left")
        ttk.Button(btn_frame, text="5s▶",
                   command=lambda c=camera: self._individual_seek(c, +5)).pack(side="left")
        btn_frame.pack()

def _update_camera_list_display(self):
    # Called from poll thread via tk.after()
    for idx, camera in enumerate(self.cameras):
        # Calculate sync delta relative to reference
        ref_timestamp = self.reference_camera.current_timestamp
        delta = camera.current_timestamp - ref_timestamp + camera.offset_seconds
        camera.sync_delta = delta

        # Determine status
        if abs(delta) <= 0.3:
            status_icon = "✓"
            status_color = "green"
            status_text = "Synced"
        else:
            status_icon = "⚠️"
            status_color = "yellow"
            status_text = f"+{delta:.1f}s ahead" if delta > 0 else f"{delta:.1f}s behind"

        # Update Treeview item
        item_id = f"camera_{idx}"
        self.camera_tree.item(item_id, values=(
            camera.name,
            f"{camera.current_timestamp:.1f}s",
            f"{camera.offset_seconds:+.1f}s",
            f"{status_icon} {status_text}"
        ))

        # Color code by status
        self.camera_tree.tag_configure(f"status_{idx}", foreground=status_color)
```

**Files Modified**:
- `sync_panel.py` (~200 lines added)

**Testing**:
- Verify timestamps update in real-time
- Verify sync status colors (green/yellow) display correctly
- Verify reference camera shows "Reference" label
- Verify selected camera row highlights

---

#### Task 1.4: Implement nudge controls

**Estimated Effort**: 4 hours

**Implementation**:
```python
# In sync_panel.py
def _build_nudge_controls_ui(self):
    nudge_frame = ttk.LabelFrame(self.master, text="Nudge Controls")
    nudge_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)

    # Selected camera display
    self.selected_label = ttk.Label(nudge_frame, text="Selected: None", font=("Arial", 12, "bold"))
    self.selected_label.pack(pady=5)

    # Current offset display
    self.offset_label = ttk.Label(nudge_frame, text="Offset: 0.0s", font=("Arial", 14))
    self.offset_label.pack(pady=5)

    # Nudge buttons
    btn_frame = ttk.Frame(nudge_frame)
    ttk.Button(btn_frame, text="Nudge -1s", width=12,
               command=lambda: self._nudge_selected_camera(-1.0)).pack(pady=2)
    ttk.Button(btn_frame, text="Nudge -0.1s", width=12,
               command=lambda: self._nudge_selected_camera(-0.1)).pack(pady=2)
    ttk.Button(btn_frame, text="Nudge +0.1s", width=12,
               command=lambda: self._nudge_selected_camera(+0.1)).pack(pady=2)
    ttk.Button(btn_frame, text="Nudge +1s", width=12,
               command=lambda: self._nudge_selected_camera(+1.0)).pack(pady=2)
    btn_frame.pack(pady=10)

def _nudge_selected_camera(self, delta):
    if not self.selected_camera:
        messagebox.showwarning("No Selection", "Please select a camera first")
        return

    # Apply offset
    self.selected_camera.offset_seconds += delta
    self.selected_camera.offset_modified = True

    # Apply to MPV playback via seek command
    self.controller.send_command(
        self.selected_camera.ipc_pipe_path,
        f"seek {delta} relative+exact"
    )

    # Update UI (offset label and camera list)
    self.offset_label.config(text=f"Offset: {self.selected_camera.offset_seconds:+.1f}s")
    self._update_camera_list_display()

    # Enable "Save to DB" button
    self.save_button.config(state="normal")
```

**Files Modified**:
- `sync_panel.py` (~100 lines added)

**Testing**:
- Verify nudge buttons only affect selected camera
- Verify offset updates immediately in UI
- Verify video seek happens within 100ms
- Verify cumulative offsets (clicking -1s twice = -2.0s)
- Verify "Save to DB" button enables when offset modified

---

#### Task 1.5: Implement database save with mandatory confirmation

**Estimated Effort**: 3 hours

**Implementation**:
```python
# In sync_panel.py
def _build_save_button_ui(self):
    save_frame = ttk.Frame(self.master)
    save_frame.grid(row=0, column=2, sticky="ne", padx=5, pady=5)

    self.save_button = ttk.Button(
        save_frame,
        text="Save All Syncs to Database",
        command=self._save_offsets_to_database,
        state="disabled",  # Initially disabled
        width=25
    )
    self.save_button.pack()

def _save_offsets_to_database(self):
    # Collect modified cameras
    modified_cameras = [c for c in self.cameras if c.offset_modified]

    if not modified_cameras:
        messagebox.showinfo("No Changes", "No sync offsets have been modified")
        return

    # Build confirmation message
    camera_list = "\n".join([
        f"  - {c.name}: {c.offset_seconds:+.1f}s"
        for c in modified_cameras
    ])

    message = (
        f"This will update mp4_status.offset_seconds for {len(modified_cameras)} cameras:\n\n"
        f"{camera_list}\n\n"
        "Do you want to proceed?"
    )

    # Show confirmation dialog (MANDATORY per REQ-1.6)
    result = messagebox.askyesno(
        "Save sync offsets to database?",
        message,
        icon="question",
        default="no"  # ESC key maps to "No"
    )

    if not result:  # User clicked "No" or closed dialog
        return  # No database changes

    # User explicitly clicked "Yes" - proceed with save
    try:
        import sqlite3
        db_path = self.config.get("database_path", "ScalpelDatabase.sqlite")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for camera in modified_cameras:
            cursor.execute(
                "UPDATE mp4_status SET offset_seconds = ? WHERE recording_date = ? AND case_no = ? AND camera_name = ?",
                (camera.offset_seconds, camera.case_id[0], camera.case_id[1], camera.name)
            )

        conn.commit()
        conn.close()

        # Mark cameras as no longer modified
        for camera in modified_cameras:
            camera.offset_modified = False

        # Disable save button
        self.save_button.config(state="disabled")

        # Show success message
        messagebox.showinfo("Success", f"✓ Sync offsets saved successfully for {len(modified_cameras)} cameras")

    except Exception as e:
        # Show error dialog with retry option
        retry = messagebox.askretrycancel(
            "Database Error",
            f"Failed to save sync offsets:\n{str(e)}\n\nRetry?"
        )
        if retry:
            self._save_offsets_to_database()  # Recursive retry
```

**Files Modified**:
- `sync_panel.py` (~80 lines added)

**Testing**:
- Verify confirmation dialog always appears before DB write
- Verify "Cancel" / ESC / dialog close prevents DB write
- Verify "Yes" click triggers UPDATE query
- Verify success message appears after save
- Verify offset_modified flags reset after save
- Verify "Save to DB" button disables after save
- Verify error dialog with retry option on DB failures

---

### Priority 2: SUPPORTING FEATURE - Database Integration

#### Task 2.1: Implement database case browser UI

**Estimated Effort**: 8 hours

**Implementation**:
- Tkinter Toplevel window with Treeview for case list
- Columns: Thumbnail, Recording Date, Case Number, Room, Anesthesiologist, Camera Count, Duration
- Filter widgets: Date range (Entry + DatePicker), Provider search (Entry), Room dropdown (Combobox)
- Load button triggers camera selection dialog

**Files Modified**:
- `db_browser.py` (new, ~400 lines)

**Testing**:
- Verify case list loads from database
- Verify sorting by clicking column headers
- Verify filtering updates list in real-time
- Verify double-click on case opens camera selection dialog

---

#### Task 2.2: Implement camera selection dialog

**Estimated Effort**: 4 hours

**Implementation**:
- Tkinter Toplevel window with Checkbutton list
- Each row: Checkbox, Camera name, Duration, File size
- "Load Videos" button (disabled if no cameras selected)
- File path validation (warning icon for missing files)

**Files Modified**:
- `db_browser.py` (~150 lines added)

**Testing**:
- Verify all cameras checked by default
- Verify unchecking/rechecking cameras
- Verify warning icons for missing files
- Verify "Load Videos" launches MPV instances

---

#### Task 2.3: Implement thumbnail generation

**Estimated Effort**: 6 hours

**Implementation**:
- Background thread extracts frame at offset_seconds timestamp using FFmpeg or MPV
- Cache thumbnails in temp directory to avoid re-extraction
- Display placeholder image during generation
- Update Treeview cell with ImageTk.PhotoImage when ready

**Files Modified**:
- `db_browser.py` (~200 lines added)

**Dependencies**:
- `Pillow` (PIL) for image handling
- FFmpeg or MPV for frame extraction

**Testing**:
- Verify thumbnails generate without blocking UI
- Verify correct timestamp used (offset_seconds or default 1s)
- Verify thumbnails cached and reused on re-open
- Verify placeholder displays during generation

---

#### Task 2.4: Integrate database browser with main application

**Estimated Effort**: 2 hours

**Implementation**:
- Add "Open from Database" button to main window
- Button launches DatabaseBrowser dialog
- DatabaseBrowser returns selected cameras (list of Camera objects)
- Main window launches MPV instances and opens SyncPanel

**Files Modified**:
- `multiMPV.py` (~50 lines added)

**Testing**:
- Verify "Open from Database" button launches browser
- Verify selected cameras load into MPV grid
- Verify sync panel opens with cameras populated
- Verify saved offsets from DB applied to video start positions

---

### Priority 3: SUPPORTING FEATURE - Advanced Playback Controls

#### Task 3.1: Implement timeline scrubber

**Estimated Effort**: 5 hours

**Implementation**:
- Tkinter Scale widget (horizontal slider)
- Maps slider position (0-100) to video duration (0 - max_duration)
- Dragging slider issues seek command to all MPV instances
- Current playback position marker updates from polling thread

**Files Modified**:
- `sync_panel.py` or `multiMPV.py` (~100 lines)

**Testing**:
- Verify dragging slider seeks all videos simultaneously
- Verify playback position marker tracks during play
- Verify seek completes within 200ms

---

#### Task 3.2: Implement frame navigation and speed control

**Estimated Effort**: 3 hours

**Implementation**:
- Frame forward/backward buttons: `send_command("frame-step")` / `send_command("frame-back-step")`
- Speed control: Combobox with options [0.25x, 0.5x, 0.75x, 1.0x, 1.25x, 1.5x, 2.0x]
- Speed change: `send_command("set speed {value}")`
- Audio mute when speed ≠ 1.0x: `send_command("set ao-volume 0")`

**Files Modified**:
- `sync_panel.py` or `multiMPV.py` (~80 lines)

**Testing**:
- Verify frame buttons advance/rewind exactly 1 frame
- Verify speed changes apply to all videos
- Verify audio mutes when speed ≠ 1.0x

---

#### Task 3.3: Implement split-view mode

**Estimated Effort**: 8 hours

**Implementation**:
- Toggle button switches between grid and split-view layout
- Split-view: Checkbuttons to select videos for enlarged display
- Selected videos resize via MPV IPC: `send_command("set geometry WxH+X+Y")`
- Non-selected videos minimize to thumbnail strip

**Files Modified**:
- `sync_panel.py` or `multiMPV.py` (~250 lines)

**Testing**:
- Verify toggling between grid and split-view
- Verify selecting 2-3 videos enlarges them evenly
- Verify thumbnails clickable to add/remove from split-view
- Verify sync maintained across all videos (including thumbnails)

---

### Priority 4: SUPPORTING FEATURE - Annotations & Export

#### Task 4.1: Implement timestamp marking

**Estimated Effort**: 4 hours

**Implementation**:
- "Mark Timestamp" button opens input dialog for annotation text
- Stores marked timestamp as tuple: (timestamp, annotation_text, datetime)
- Displays markers on timeline scrubber as vertical lines

**Files Modified**:
- `sync_panel.py` or `multiMPV.py` (~100 lines)

**Testing**:
- Verify marking timestamp saves current playback position
- Verify annotation text optional (can be blank)
- Verify markers display on timeline

---

#### Task 4.2: Implement export functionality

**Estimated Effort**: 3 hours

**Implementation**:
- "Export" button opens file dialog to select save location
- Export format: Plain text and JSON
- Includes case metadata, marked timestamps, sync offsets

**Files Modified**:
- `sync_panel.py` or `multiMPV.py` (~80 lines)

**Testing**:
- Verify export saves to user-selected location
- Verify both text and JSON formats generate correctly
- Verify exported data includes case metadata and offsets

---

## Phase 3: Testing Strategy

### Unit Tests

**Test Suite 1: mpv_controller.py**
- Test `launch_video()` creates subprocess with correct args
- Test `send_command()` writes to named pipe successfully
- Test `query_property()` parses IPC response correctly
- Test error handling for closed pipes, missing MPV executable

**Test Suite 2: sync_panel.py sync logic**
- Test sync delta calculation (reference camera, offsets applied)
- Test sync status classification (±0.3s tolerance)
- Test nudge offset accumulation
- Test offset_modified flag behavior

**Test Suite 3: db_browser.py database queries**
- Test case list query returns correct data
- Test filtering by date range, provider, room
- Test camera selection query for specific case
- Test UPDATE query for saving offsets

---

### Integration Tests

**Test Scenario 1: Load videos from database and sync manually**
- Open database browser → select case → load 6 cameras
- Verify videos launch in grid layout
- Verify sync panel displays 6 cameras with real-time timestamps
- Select camera with +1.5s ahead status → click "Nudge -1s" twice → click "Nudge +0.1s" 5 times
- Verify offset updates to -1.5s, status changes to "✓ Synced"
- Click "Save to DB" → verify confirmation dialog → click "Save" → verify success message
- Close application → reopen same case → verify -1.5s offset applied at video start

**Test Scenario 2: Individual camera controls**
- Load 3 cameras
- Click individual pause button on Camera 2 → verify Camera 2 pauses, others continue playing
- Click individual "Seek -5s" on Camera 2 → verify Camera 2 rewinds 5s, others unaffected
- Click "Play All" → verify Camera 2 resumes playback

**Test Scenario 3: Database write cancellation**
- Make sync adjustments to 3 cameras
- Click "Save to DB" → confirmation dialog appears
- Click "Cancel" → verify dialog closes with no DB changes
- Reopen same case → verify offsets not saved (still at original values)

---

### Acceptance Testing (maps to AC 1-55)

**AC 1-10: Manual Camera Synchronization**
- ✅ AC 1: Sync status panel displays all loaded cameras
- ✅ AC 2: Each camera row shows name, timestamp, offset, status indicator
- ✅ AC 3: Sync indicators calculated relative to reference camera
- ✅ AC 4: "Synced" (green ✓) within ±0.3s, else "Out of Sync" (yellow ⚠️)
- ✅ AC 5: User can select camera row by clicking
- ✅ AC 6: Nudge buttons visible: "-1s", "-0.1s", "+0.1s", "+1s"
- ✅ AC 7: Nudge applies only to selected camera
- ✅ AC 8: Offset and status update within 100ms of nudge click
- ✅ AC 9: Nudge actions cumulative (two -1s clicks = -2.0s)
- ✅ AC 10: Current offset displayed prominently

**AC 22-27: Sync Persistence with Mandatory Confirmation**
- ✅ AC 22: Confirmation dialog displays before ANY DB write
- ✅ AC 23: User MUST explicitly click "Save to Database" to proceed
- ✅ AC 24: "Cancel" / ESC closes dialog with no DB changes
- ✅ AC 25: "Save" triggers UPDATE query and shows success message
- ✅ AC 26: "Save" button disabled after successful save
- ✅ AC 27: Error dialog with retry option on DB write failure

*(Full acceptance test coverage for AC 1-55 documented in separate test plan)*

---

## Phase 4: Deployment & Documentation

### Deployment Steps

1. **Database Migration** (if offset_seconds column missing):
   ```bash
   sqlite3 ScalpelDatabase.sqlite "ALTER TABLE mp4_status ADD COLUMN offset_seconds REAL DEFAULT 0.0;"
   ```

2. **Dependency Installation**:
   ```bash
   pip install pillow  # For thumbnail generation
   ```

3. **Configuration Update**:
   - Add `database_path` to `config.ini`:
     ```ini
     [Database]
     database_path = F:\Projects\ScalpelLab_Raz\ScalpelDatabase.sqlite
     ```

4. **File Deployment**:
   - Copy new files: `sync_panel.py`, `db_browser.py`, `mpv_controller.py`
   - Replace existing `multiMPV.py` with refactored version

5. **Testing Verification**:
   - Run integration test suite
   - Run acceptance test for AC 1-27 (PRIMARY feature)

---

### Documentation Deliverables

1. **quickstart.md**: Developer setup guide (see separate file)
2. **user-guide.md**: End-user instructions for manual sync workflow
3. **api-contracts/**: IPC command reference (see separate files)
4. **CHANGELOG.md**: Version history and breaking changes

---

## Risk Assessment

### High-Risk Areas

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Accidental database writes** | CRITICAL: Data corruption | MANDATORY confirmation dialog (AC 22-27); extensive testing; disabled save button |
| **IPC named pipe failures** | HIGH: Videos can't be controlled | Error handling with retry logic; fallback to file dialog loading |
| **Timestamp polling lag** | MEDIUM: Sync status inaccurate | 10 Hz refresh rate; async polling thread; UI update optimization |
| **Missing offset_seconds column** | MEDIUM: Feature won't work | Graceful degradation; migration script; clear error messaging |

### Technical Debt

- **Windows-only IPC**: Named pipes are Windows-specific; future Linux support requires refactoring to Unix sockets or JSON IPC
- **Tkinter GUI scalability**: Tkinter adequate for desktop tool but limits future mobile/web deployment
- **Thumbnail caching**: No cleanup mechanism; temp directory may accumulate files over time

---

## Success Criteria (from spec)

### PRIMARY Success Metrics

- [ ] **Sync time reduced by 80%**: <2 minutes for 6 cameras (vs. 5-10 min baseline)
- [ ] **Visual sync identification**: <5 seconds to identify out-of-sync cameras
- [ ] **Nudge control adoption**: 100% (users never manually seek for sync)
- [ ] **Database save adoption**: 80%+ sessions with adjustments result in DB save
- [ ] **Zero accidental saves**: 0% DB writes without confirmation dialog approval
- [ ] **Sync persistence value**: 90%+ repeat case views load with pre-saved offsets

---

## Timeline Estimate

| Phase | Tasks | Estimated Hours | Priority |
|-------|-------|-----------------|----------|
| **Phase 0: Research** | *(Complete)* | *(8h)* | ✅ DONE |
| **Phase 1: PRIMARY Sync Feature** | Task 1.1 - 1.5 | 20h | 🔴 CRITICAL |
| **Phase 2: Database Integration** | Task 2.1 - 2.4 | 20h | 🟡 HIGH |
| **Phase 3: Playback Controls** | Task 3.1 - 3.3 | 16h | 🟢 MEDIUM |
| **Phase 4: Annotations** | Task 4.1 - 4.2 | 7h | 🔵 LOW |
| **Phase 5: Testing** | Unit + Integration + Acceptance | 15h | 🔴 CRITICAL |
| **Phase 6: Documentation** | User guide, API docs, deployment | 8h | 🟡 HIGH |
| **Total** | | **94 hours** (~12 days @ 8h/day) | |

---

## Next Steps

1. **Immediate**: Begin Task 1.1 (refactor multiMPV.py into MPVController)
2. **Week 1**: Complete PRIMARY sync feature (Task 1.1 - 1.5) and unit tests
3. **Week 2**: Implement database integration (Task 2.1 - 2.4) and integration tests
4. **Week 3**: Add advanced playback controls (Task 3.1 - 3.3) and annotations (Task 4.1 - 4.2)
5. **Week 4**: Acceptance testing, documentation, deployment

---

**Plan Status**: ✅ COMPLETE - Ready for Implementation
**Next Command**: `/speckit.tasks` (break down into GitHub issues/tasks)
