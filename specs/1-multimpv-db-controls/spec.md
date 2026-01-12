# Feature Specification: MultiMPV_Offset Manual Camera Synchronization with Database Persistence

**Feature ID**: 1-multimpv-db-controls
**Status**: Approved - Prioritized - Ready for Planning
**Created**: 2026-01-12
**Last Updated**: 2026-01-12 (Prioritization Update)
**Primary Goal**: Easy manual camera synchronization with visual feedback and database persistence (with mandatory user confirmation)

---

## Overview

### Problem Statement

Medical professionals using the MultiMPV_Offset synchronized video player face critical challenges when reviewing multi-camera surgical recordings:

1. **Camera synchronization is difficult and time-consuming**: Different cameras start recording at slightly different times, causing videos to be out of sync (0.5-3 seconds variance). Currently, users cannot easily identify which cameras are misaligned or adjust individual video timing.
2. **No visual sync feedback**: Users must rely on watching content to detect sync issues rather than having clear visual indicators showing which videos are ahead/behind.
3. **Individual camera control is limited**: Current interface only supports "all videos" commands; no easy way to pause/seek/adjust one specific camera independently.
4. **Sync adjustments are lost**: Manual timing adjustments must be redone every session because there's no way to save sync offsets permanently.
5. **Secondary inefficiencies**: Manual file navigation is time-consuming; file browsers don't show case metadata; loading multiple cameras requires clicking through dialogs.

**Primary Pain Point**: Users spend 5-10 minutes per case manually trying to sync cameras by pausing/seeking, with no clear visual feedback on which cameras need adjustment or by how much.

### Proposed Solution

Upgrade MultiMPV_Offset with an advanced control panel focused on **easy manual synchronization** with visual feedback and database persistence:

1. **Individual camera control panel**: Dedicated controls for each video with pause, seek, and nudge buttons; large visual display showing current timestamp and sync offset for each camera
2. **Visual sync indicators**: Real-time sync status display showing which cameras are ahead/behind relative to reference video (e.g., "Camera 2: +1.2s, Camera 5: -0.8s")
3. **Easy nudge controls**: One-click buttons to adjust individual cameras by ±0.1s and ±1s with immediate visual feedback
4. **Persistent sync storage**: "Save All Syncs to Database" button (with confirmation dialog) stores offsets in `mp4_status.offset_seconds` for future sessions
5. **Supporting features**: Database-driven case browser for faster loading, timeline scrubbing, playback speed control, split-view comparison mode

---

## User Scenarios & Testing

### Primary User Personas

1. **Medical Reviewer**: Anesthesiology attending reviewing resident performance on surgical cases
2. **Researcher**: Analyzing specific case types or time ranges for quality improvement studies
3. **Educator**: Preparing teaching materials using specific case examples

### Core User Flows

#### Scenario 1: Manual Camera Synchronization (PRIMARY WORKFLOW)

**Actor**: Medical Reviewer
**Precondition**: 6 videos loaded (Cart_Center, Cart_Left, Cart_Right, Monitor, Room_Left, Room_Right); videos are currently out of sync
**Flow**:
1. User clicks "Play All" - notices Cart_Left video shows surgeon entering room 1.5 seconds before other cameras
2. Control panel displays sync status for each camera:
   ```
   Cart_Center:   00:15:32.1  [Offset: 0.0s]    ✓ Reference
   Cart_Left:     00:15:33.6  [Offset: 0.0s]    ⚠️ +1.5s ahead
   Cart_Right:    00:15:31.8  [Offset: 0.0s]    ⚠️ -0.3s behind
   Monitor:       00:15:32.0  [Offset: 0.0s]    ✓ Synced
   Room_Left:     00:15:33.2  [Offset: 0.0s]    ⚠️ +1.1s ahead
   Room_Right:    00:15:31.5  [Offset: 0.0s]    ⚠️ -0.6s behind
   ```
3. User clicks "Cart_Left" row to select it (row highlights)
4. User clicks "Nudge -1s" button twice (Cart_Left offset now shows: -2.0s)
5. User fine-tunes by clicking "Nudge +0.1s" five times (Cart_Left offset now: -1.5s)
6. Cart_Left sync indicator turns green: `Cart_Left: 00:15:32.1 [Offset: -1.5s] ✓ Synced`
7. User repeats for Cart_Right (nudge +0.3s), Room_Left (nudge -1.1s), Room_Right (nudge +0.6s)
8. All cameras now show green ✓ Synced status
9. User clicks "Save All Syncs to Database" button
10. System displays confirmation dialog:
    ```
    Save sync offsets to database?

    This will update mp4_status.offset_seconds for 4 cameras:
    - Cart_Left: -1.5s
    - Cart_Right: +0.3s
    - Room_Left: -1.1s
    - Room_Right: +0.6s

    [Cancel] [Save to Database]
    ```
11. User clicks "Save to Database"
12. System shows success message: "✓ Sync offsets saved successfully"

**Expected Outcome**: All cameras play perfectly synchronized; sync offsets are permanently stored in database for future sessions; user can easily see which cameras need adjustment and by how much

#### Scenario 2: Opening a Recent Case from Database

**Actor**: Medical Reviewer
**Precondition**: MultiMPV_Offset is launched, database contains cases with saved sync offsets
**Flow**:
1. User clicks "Open from Database" button
2. System displays database browser showing most recent 20 cases
3. User clicks on case "2023-05-15 / Case 3" (6 cameras available)
4. System displays camera selection with all 6 cameras checked by default
5. User clicks "Load Videos"
6. System loads videos and applies saved sync offsets from database automatically
7. Control panel displays:
   ```
   Cart_Left:  [Offset: -1.5s] ✓ Synced (from database)
   Cart_Right: [Offset: +0.3s] ✓ Synced (from database)
   ...
   ```

**Expected Outcome**: Videos load with previously saved sync offsets applied automatically; no re-syncing needed

#### Scenario 3: Searching for Specific Provider Cases

**Actor**: Researcher
**Precondition**: Database contains 200+ cases over 6 months
**Flow**:
1. User opens database browser
2. User enters "Dr. Smith" in anesthesiologist filter field
3. User selects date range "2023-03-01" to "2023-04-30"
4. System filters and displays 15 matching cases
5. User sorts by duration (ascending) to find shorter cases
6. User selects case with 00:18:32 duration and loads videos

**Expected Outcome**: Videos load successfully, control panel shows filtered case metadata

---

## Functional Requirements

### PRIMARY FEATURE: Individual Camera Synchronization Control

**REQ-1.1: Visual Sync Status Display**
- Control panel shall display dedicated sync status panel showing all loaded cameras in a table/list format
- Each camera row shall display: Camera Name, Current Timestamp, Sync Offset Value, Sync Status Indicator
- Sync status indicator shall show:
  - ✓ Green checkmark with "Synced" text when camera is within ±0.3s of reference camera
  - ⚠️ Yellow warning with "+X.Xs ahead" or "-X.Xs behind" when camera is outside sync tolerance
  - Reference camera marked with "Reference" label (default: first loaded camera or user-selected)
- Current timestamp shall update in real-time during playback (at least 10 Hz refresh rate)
- Sync offset value shall display with ±X.X second precision (one decimal place)
- User shall click any camera row to select it for adjustment (row highlights)

**REQ-1.2: Individual Camera Nudge Controls**
- Control panel shall provide prominent nudge buttons adjacent to camera list:
  - "Nudge -1s" button (large, easy to click)
  - "Nudge -0.1s" button
  - "Nudge +0.1s" button
  - "Nudge +1s" button (large, easy to click)
- Nudge buttons shall only affect the currently selected camera (highlighted row)
- Clicking nudge button shall immediately:
  - Update the sync offset value displayed for that camera
  - Apply the offset to the video playback position
  - Recalculate and update the sync status indicator
- Nudge actions shall be cumulative (clicking "-1s" twice applies -2.0s total offset)
- System shall display current offset value prominently next to nudge buttons (e.g., "Current Offset: -1.5s")

**REQ-1.3: Individual Camera Playback Controls**
- Each camera row shall include individual control buttons:
  - Pause/Play button (toggle for this camera only)
  - "Seek -5s" button (rewind this camera 5 seconds)
  - "Seek +5s" button (advance this camera 5 seconds)
- Individual controls shall not affect other cameras
- Individual pause shall visually indicate camera is paused (e.g., row background color change, pause icon)
- User shall unpause individual camera by clicking Play button or using "Play All" global control

**REQ-1.4: Reference Camera Selection**
- User shall designate any loaded camera as the "reference" camera via right-click menu or dropdown
- All sync indicators shall calculate relative to the reference camera timestamp
- Changing reference camera shall immediately recalculate all sync status indicators
- Reference camera always shows sync offset of 0.0s and "Reference" status

**REQ-1.5: Sync Offset Loading from Database**
- On video load, system shall query `mp4_status.offset_seconds` column for each camera
- System shall apply saved offsets automatically to video playback start positions
- Control panel shall display "(from database)" label next to cameras with pre-existing offsets
- If offset_seconds is NULL or 0.0, system shall treat as no saved offset

**REQ-1.6: Sync Offset Persistence to Database (WITH MANDATORY CONFIRMATION)**
- Control panel shall display large, prominent "Save All Syncs to Database" button
- Button shall be disabled (grayed out) if no offsets have been modified since last save
- Clicking button shall display confirmation dialog with:
  - Title: "Save sync offsets to database?"
  - Message: "This will update mp4_status.offset_seconds for [N] cameras:"
  - List of cameras and their offset values (only cameras with non-zero offsets)
  - Two buttons: "Cancel" (default/ESC key) and "Save to Database" (requires explicit click)
- User MUST explicitly click "Save to Database" to proceed - no auto-save, no implicit save
- On "Cancel", dialog closes with no database changes
- On "Save to Database":
  - System executes UPDATE query for each modified camera's offset_seconds value
  - System displays success message: "✓ Sync offsets saved successfully for [N] cameras"
  - Button returns to disabled state until next modification
- If database write fails, system shall display error dialog with specific error message and option to retry

### Database Integration Requirements (Supporting Feature)

**REQ-2.1: Database Query Interface**
- System shall read case metadata from `recording_details` table
- System shall read video file paths from `mp4_status` table
- System shall join tables to associate videos with case information
- System shall handle missing or null database values gracefully
- System shall validate file paths exist before attempting to load

**REQ-2.2: Case Browser Display**
- System shall display cases in sortable table format with thumbnail preview column
- System shall extract thumbnail preview from Monitor camera at timestamp specified by `mp4_status.offset_seconds` column (defaults to 00:00:01 if null)
- Thumbnail generation shall occur in background to avoid blocking UI (display placeholder during extraction)
- System shall show columns: Thumbnail Preview, Recording Date, Case Number, Room, Anesthesiologist Name, Camera Count, Duration
- System shall support sorting by any column (ascending/descending)
- System shall display most recent 20 cases by default (pagination for more)
- System shall show visual indicator for cases with missing video files

**REQ-2.3: Case Filtering**
- System shall support filtering by:
  - Date range (start date, end date)
  - Anesthesiologist name (text search, partial match)
  - Room number (dropdown selection)
  - Case number (numeric range)
  - Camera availability (minimum camera count)
- System shall apply filters in real-time as user types/selects
- System shall show result count after filtering

**REQ-2.4: Camera Selection**
- After case selection, system shall display available cameras for that case
- System shall show camera name, duration, and file size for each camera
- System shall allow multi-select via checkboxes (default: all cameras selected)
- System shall indicate if video file is missing or corrupted
- System shall support loading 1-9 cameras (maximum grid size)

### Advanced Playback Controls (Supporting Feature)

**REQ-3.1: Timeline Scrubber**
- Control panel shall display horizontal timeline spanning full video duration
- Timeline shall show current playback position as draggable marker
- User shall drag marker to any position; all videos seek to that timestamp
- Timeline shall display tick marks every 60 seconds with timestamp labels
- Timeline shall show marked timestamps as vertical lines with annotation tooltips

**REQ-3.2: Frame-Level Navigation**
- Control panel shall provide "Frame Forward" button (advances 1 frame = 1/30 second)
- Control panel shall provide "Frame Backward" button (rewinds 1 frame)
- Control panel shall provide "Skip Forward 5s" button
- Control panel shall provide "Skip Backward 5s" button
- Frame navigation shall apply to all synchronized videos

**REQ-3.3: Playback Speed Control**
- Control panel shall provide speed selector with options: 0.25x, 0.5x, 0.75x, 1.0x, 1.25x, 1.5x, 2.0x
- Speed changes shall apply to all synchronized videos
- Audio shall be disabled when playback speed ≠ 1.0x (avoids pitch distortion)
- Current speed shall be displayed prominently on control panel

**REQ-3.4: Timestamp Annotation**
- User shall click "Mark Timestamp" button to save current playback position
- System shall prompt for annotation text (optional, 200 character limit)
- Marked timestamps shall appear on timeline scrubber as vertical lines
- User shall hover over marked timestamp to view annotation tooltip
- User shall right-click marked timestamp to edit or delete annotation

**REQ-3.5: Enhanced Export Functionality**
- Export shall include case metadata (date, case number, anesthesiologist)
- Export shall include each marked timestamp with annotation text
- Export shall save to user-specified location (default: `exports/<case_id>_timestamps.txt`)
- Export format shall support both plain text and JSON
- Export shall include sync offset values for each camera

**REQ-3.6: Split-View Comparison Mode**
- Control panel shall provide "Split-View Mode" toggle button
- When split-view enabled, user shall select videos to display in enlarged side-by-side layout via checkboxes
- System shall resize selected videos to fill screen space evenly (e.g., 2 videos = 50% width each, 3 videos = 33% each)
- System shall minimize non-selected videos to thumbnail strip at bottom (80x60 pixel thumbnails)
- User shall click thumbnail to add/remove video from split-view selection
- System shall maintain synchronization across all videos (including minimized thumbnails)

**REQ-3.7: Control Panel Layout**
- Control panel shall remain "always-on-top" above video windows
- Control panel shall be resizable (minimum 600x400 pixels)
- Control panel shall display case metadata prominently at top (case date, number, provider)
- Control panel shall group controls logically with PRIMARY FEATURE PROMINENT:
  - **Synchronization Panel** (top, largest section): Camera list with sync status, nudge controls, Save to DB button
  - Playback Controls (play/pause/speed for all videos)
  - Navigation Controls (timeline scrubber, frame buttons, seek buttons)
  - Annotation Tools (mark timestamp, export)
  - View Mode (split-view toggle, grid layout options)
- Control panel shall display current timestamp and total duration

### Data Validation Requirements

**REQ-4.1: Database Connectivity**
- System shall verify database file exists at expected path (from config or environment)
- System shall display clear error if database unreachable
- System shall provide "Browse for Database" fallback option
- System shall validate database schema matches expected tables/columns

**REQ-4.2: File Path Validation**
- System shall check each video file path from database exists before loading
- System shall display warning icon for missing files in camera selection dialog
- System shall allow user to proceed with available videos only (skip missing)
- System shall log missing file paths for troubleshooting

**REQ-4.3: Video Compatibility**
- System shall verify video format is supported by MPV before loading
- System shall display error message for unsupported formats
- System shall gracefully handle corrupted video files (skip and continue)

---

## Success Criteria

### PRIMARY: Manual Sync Workflow Efficiency
- **Sync time reduced by 80%**: Syncing 6 cameras completes in <2 minutes (vs. 5-10 minutes with current manual seeking method)
- **Visual sync identification**: Users identify which cameras are out of sync within 5 seconds of playback start (using visual indicators)
- **Nudge control efficiency**: Users adjust individual camera sync using nudge buttons without seeking manually 100% of the time
- **Sync accuracy achieved**: 95%+ of sync adjustments result in all cameras within ±0.3 seconds (green checkmark status)
- **Database save adoption**: 80%+ of sessions where sync adjustments are made result in user clicking "Save to Database"
- **Zero accidental saves**: 0% of database writes occur without explicit user confirmation dialog approval
- **Sync persistence value**: Cameras load with pre-saved offsets applied automatically, eliminating re-sync work in 90%+ of repeat case views

### Usability - Sync Control Panel
- **Sync status visibility**: 100% of users can identify which camera is out of sync and by how much without external documentation
- **Individual camera control adoption**: 70%+ of users use individual camera pause/seek controls during sync workflow
- **Reference camera switching**: 40%+ of users change the reference camera at least once per session for better sync comparison
- **Real-time feedback satisfaction**: Sync status indicators update within 100ms of nudge button click, providing immediate visual confirmation

### Technical Performance - Sync Features
- **Sync status update rate**: Camera timestamps and sync indicators refresh at minimum 10 Hz (10 times per second) during playback
- **Nudge control responsiveness**: Clicking nudge button applies offset and updates display within 100ms
- **Database write speed**: Saving sync offsets to database completes within 2 seconds for 9 cameras
- **Offset loading speed**: Saved offsets load from database and apply to video start positions within 500ms of video initialization
- **Sync calculation accuracy**: Sync offset values displayed match actual video position differences within ±0.05 seconds

### Supporting Features Efficiency
- **Case loading time reduced by 70%**: Opening 6-camera case from database completes in <10 seconds (vs. 30+ seconds with file dialogs)
- **User clicks reduced by 80%**: Opening case requires 3 clicks (launch → select case → load) vs. 15+ clicks with file browsers
- **Thumbnail preview effectiveness**: 70%+ of users identify target case using thumbnail preview without reading metadata

### Data Integrity
- **100% path validation**: Zero attempts to load non-existent video files
- **Export accuracy**: Exported timestamps match actual video positions within ±0.1 seconds
- **Sync offset persistence**: Manual sync adjustments maintain accuracy throughout session (no drift)
- **Database confirmation requirement**: 100% of database writes show confirmation dialog; 0% of writes occur without user approval

---

## Acceptance Criteria

**PRIMARY: Manual Camera Synchronization Must:**
1. Display sync status panel with table/list showing all loaded cameras
2. Show for each camera: Name, Current Timestamp (updated 10x per second), Sync Offset, Status Indicator (✓ green / ⚠️ yellow)
3. Calculate sync status indicators relative to designated reference camera
4. Display "Synced" (green ✓) when camera within ±0.3s of reference, "+X.Xs ahead" or "-X.Xs behind" (yellow ⚠️) otherwise
5. Allow user to select any camera row by clicking (row highlights)
6. Provide prominent nudge buttons: "Nudge -1s", "Nudge -0.1s", "Nudge +0.1s", "Nudge +1s"
7. Apply nudge adjustments only to selected camera, not all videos
8. Update sync offset value and status indicator within 100ms of nudge button click
9. Cumulative nudge actions (clicking "-1s" twice = -2.0s total offset)
10. Display current offset value prominently (e.g., "Current Offset: -1.5s")

**Individual Camera Control Must:**
11. Provide individual Pause/Play button for each camera in sync status panel
12. Provide individual "Seek -5s" and "Seek +5s" buttons for each camera
13. Individual pause/seek actions affect only that camera, not others
14. Visually indicate when individual camera is paused (row background change, pause icon)
15. Allow user to designate any camera as "reference" via right-click menu or dropdown
16. Recalculate all sync indicators immediately when reference camera changes

**Sync Persistence to Database Must:**
17. Load existing sync offsets from mp4_status.offset_seconds on video initialization
18. Apply loaded offsets to video start positions automatically
19. Display "(from database)" label for cameras with pre-existing offsets
20. Provide large, prominent "Save All Syncs to Database" button
21. Disable "Save" button (gray out) when no modifications made since last save
22. **MANDATORY CONFIRMATION**: Display dialog before any database write with:
    - Title: "Save sync offsets to database?"
    - Message: "This will update mp4_status.offset_seconds for [N] cameras:"
    - List of cameras and offset values
    - Two buttons: "Cancel" (default/ESC) and "Save to Database" (requires explicit click)
23. User MUST explicitly click "Save to Database" - no auto-save, no implicit writes
24. On "Cancel", close dialog with no database changes
25. On "Save to Database", execute UPDATE queries and show success message: "✓ Sync offsets saved successfully for [N] cameras"
26. Return "Save" button to disabled state after successful save
27. Display error dialog with retry option if database write fails

**Database Browser Must (Supporting Feature):**
28. Display cases from ScalpelDatabase.sqlite without errors
29. Show accurate metadata (dates, providers, durations match database values)
30. Display thumbnail preview extracted from Monitor camera at offset_seconds timestamp
31. Generate thumbnails in background without blocking UI (show placeholder during load)
32. Support filtering by date range, provider, and room with <2 second response time
33. Handle edge cases: empty database, missing tables, null values, missing offset_seconds column
34. Provide clear error messages when database unavailable

**Camera Selection Must (Supporting Feature):**
35. List all cameras from mp4_status table for selected case
36. Indicate missing files with warning icon and gray text
37. Allow loading 1-9 cameras in any combination
38. Arrange windows in optimal grid layout automatically
39. Validate file existence before MPV launch

**Advanced Playback Controls Must (Supporting Feature):**
40. Scrub timeline to any timestamp with <200ms video response time
41. Advance/rewind by single frames accurately (verified frame count)
42. Change playback speed to 7 supported values (0.25x - 2.0x)
43. Mark timestamps with optional annotations (save and display on timeline)
44. Export timestamps with case metadata and annotations to text/JSON
45. Remain always-on-top and responsive during video playback

**Split-View Mode Must (Supporting Feature):**
46. Toggle between grid view and split-view via control panel button
47. Allow user to select multiple videos for enlarged display via checkboxes
48. Resize selected videos to fill screen evenly (proportional width allocation)
49. Minimize non-selected videos to thumbnail strip (80x60 pixels at bottom)
50. Maintain synchronization across all videos (including thumbnails)

**System Reliability Must:**
51. Handle database connection failures gracefully (show error dialog, offer file browser fallback)
52. Validate all file paths before attempting video load
53. Continue operation when 1-2 cameras fail to load (show warning, load remaining)
54. Maintain sync accuracy for sessions >60 minutes
55. Handle missing offset_seconds column gracefully (default to 0.0 for sync, 1.0 for thumbnails)

---

## Key Entities

### Case
- **Attributes**: Recording Date, Case Number, Room, Anesthesiologist Name, Camera Count
- **Relationships**: Has many Camera Videos
- **Business Rules**: Case Number must be unique per Recording Date and Room

### Camera Video
- **Attributes**: Camera Name, File Path, Duration, File Size, Sync Offset
- **Relationships**: Belongs to one Case
- **Business Rules**: File Path must be valid filesystem path; Duration must be positive

### Marked Timestamp
- **Attributes**: Timestamp (HH:MM:SS.mmm), Annotation Text (optional), Creation Time
- **Relationships**: Belongs to one Case, associated with Playback Session
- **Business Rules**: Timestamp must be within video duration range; Annotation limited to 200 characters

### Playback Session
- **Attributes**: Session Start Time, Loaded Case, Active Cameras, Sync Offsets, Marked Timestamps
- **Relationships**: Has one Case, has many Camera Videos, has many Marked Timestamps
- **Business Rules**: Session must have 1-9 active cameras; Sync offsets persist per session

---

## Assumptions

1. **Database Structure with offset_seconds**: The `mp4_status` table includes (or will include) an `offset_seconds` column (REAL type) for storing sync offsets and thumbnail timestamps
2. **Database Structure Stability**: The `recording_details` and `mp4_status` table schemas will not change significantly during development beyond offset_seconds additions
3. **File System Access**: Users running MultiMPV_Offset have read access to video file paths stored in database
4. **MPV Installation**: MPV executable path is configured correctly in config.ini
5. **Video Synchronization Tolerance**: Medical reviewers accept ±0.5 second sync variance as clinically acceptable
6. **Single User Session**: Control panel and video windows operate in single-instance mode (one case open at a time)
7. **Network Paths Supported**: Database may reference network paths (UNC paths like `\\server\recordings\...`) which are accessible
8. **Frame Rate Consistency**: All cameras for a given case record at same frame rate (typically 30 FPS)
9. **Control Panel Platform**: Initial implementation may use Tkinter (Windows) with future consideration for GTK/GIO cross-platform support
10. **Database Write Permissions**: System requires read access for browsing; write access for saving sync offsets (user will be prompted for confirmation)
11. **Video Format Consistency**: All videos are H.264/H.265 MP4 files playable by MPV without transcoding
12. **Thumbnail Extraction Capability**: MPV or FFmpeg is available for extracting frame images at specified timestamps

---

## Clarified Decisions

### Decision 1: Thumbnail Preview Implementation
**Resolution**: Database browser will include thumbnail previews extracted at the timestamp specified by the `offset_seconds` column in the `mp4_status` table. This provides visual case identification while using existing database infrastructure.

### Decision 2: Sync Offset Persistence Strategy
**Resolution**: Sync offset adjustments will be stored in the `mp4_status.offset_seconds` column to persist across sessions. System will prompt user for confirmation before modifying database schema or values, ensuring user awareness of database changes.

### Decision 3: Split-View Comparison Mode
**Resolution**: Control panel will support split-view mode allowing users to select multiple videos from the case for detailed side-by-side comparison. All available videos (where camera exists in database) can be included in split-view layout.

---

## Dependencies

### Technical Dependencies
- **Python 3.10+**: Required for subprocess management and Tkinter
- **SQLite3**: Database driver (included in Python standard library)
- **MPV Media Player**: External executable, version 0.34+ recommended
- **Tkinter**: GUI framework (may be replaced with GTK/GIO in future)
- **Python Libraries**: `sqlite3`, `subprocess`, `os`, `pathlib`, `datetime`, `json` (all standard library)

### Data Dependencies
- **ScalpelDatabase.sqlite**: Must exist and contain populated `recording_details` and `mp4_status` tables
- **Video Files**: Physical video files must exist at paths specified in `mp4_status.path` column
- **Config.ini**: Must specify correct MPV executable path

### System Dependencies
- **Operating System**: Windows 10+ (initial release), future Linux support via GTK
- **File System Access**: User must have read permissions for video storage directories
- **Screen Resolution**: Minimum 1920x1080 for optimal 9-camera grid layout

---

## Out of Scope

1. **Video Editing**: Trimming, clipping, or modifying video files
2. **Real-Time Recording**: Starting/stopping new camera captures
3. **Database Schema Modifications**: Altering existing database table structures
4. **User Authentication**: Access control or user login system
5. **Cloud Storage Integration**: Opening videos from S3, Azure Blob, or other cloud services
6. **Multi-Session Management**: Opening multiple cases simultaneously in separate windows
7. **Video Transcoding**: Converting video formats or resolutions
8. **Audio Synchronization**: Advanced audio waveform matching for sync (relies on manual nudging)
9. **Collaborative Annotations**: Sharing marked timestamps with other users
10. **Mobile/Web Interface**: Control panel is desktop-only
11. **Automated Quality Checks**: Detecting video corruption, frame drops, or encoding issues
12. **Export to Video Formats**: Combining cameras into single video file (use existing FFmpeg scripts)
13. **Database Creation Tools**: Populating database from scratch (use existing `2_4_update_db.py` script)

---

## Non-Functional Requirements

### Performance
- Database queries complete within 2 seconds for 1000+ case databases
- Video loading completes within 10 seconds for 9-camera cases
- Control panel UI remains responsive (<100ms button click response) during video playback
- Timeline scrubbing updates all videos within 200ms

### Reliability
- System handles missing video files without crashing
- Database connection failures display clear error dialogs
- Control panel maintains state if MPV process crashes
- Marked timestamps persist in memory throughout session (recovered after control panel restart)

### Usability
- Control panel uses standard keyboard shortcuts (Space = play/pause, Left/Right arrows = seek)
- Error messages include actionable next steps ("Browse for database" button)
- Case browser columns are sortable by clicking headers
- Control panel tooltips explain each button function

### Maintainability
- Database query logic isolated in separate module for easy schema updates
- Control panel UI components use modular design for future GTK migration
- Configuration values (database path, export directory) externalized to config.ini
- Code comments explain IPC command formats and sync calculation logic

---

## Security & Privacy Considerations

### PHI Protection
- Database contains Protected Health Information (PHI) - implement appropriate access controls at system level
- Exported timestamp files should avoid including patient identifiers (use case number only, not patient names)
- File paths displayed in UI should not reveal patient-identifying directory names
- Consider encrypting exported annotation files if they contain clinical notes

### Data Validation
- Sanitize database query inputs to prevent SQL injection (use parameterized queries)
- Validate file paths to prevent directory traversal attacks (reject paths with `..` sequences)
- Limit annotation text length to prevent buffer overflow (200 character maximum)

### Audit Logging
- Log database access events (which cases were opened, by which user, when) for compliance auditing
- Log exported timestamp files (when created, for which case) for chain of custody

---

## Future Enhancements

*(Not part of current scope, but noted for roadmap planning)*

1. **GTK/GIO Control Panel**: Replace Tkinter with cross-platform GTK for Linux deployment
2. **Waveform Audio Sync**: Auto-detect sync offsets by matching audio waveforms across cameras
3. **Thumbnail Timeline**: Display frame thumbnails along timeline for visual seeking
4. **Cloud Database Support**: Connect to PostgreSQL/MySQL for centralized case databases
5. **Collaborative Annotations**: Share marked timestamps with team members via network sync
6. **Video Quality Metrics**: Display frame drop count, bitrate, and resolution per camera
7. **Custom Grid Layouts**: User-defined window positions (e.g., picture-in-picture for specific camera)
8. **Playback Presets**: Save/load control panel settings (speed, sync offsets) as named profiles
9. **Export to DICOM**: Convert marked timestamps to DICOM SR (Structured Report) for medical records
10. **Mobile Companion App**: Remote control for play/pause/seek via smartphone

---

## References

### Existing System Documentation
- **MultiMPV_Offset Implementation**: `F:\Projects\ScalpelLab_Raz\MultiMPV_Offset\multiMPV.py`
- **Database Schema**: ScalpelDatabase.sqlite (tables: recording_details, mp4_status, anesthesiology)
- **Configuration File**: `F:\Projects\ScalpelLab_Raz\MultiMPV_Offset\config.ini`

### External Dependencies
- **MPV Manual**: https://mpv.io/manual/stable/
- **MPV IPC Protocol**: https://mpv.io/manual/stable/#json-ipc
- **SQLite Documentation**: https://www.sqlite.org/docs.html

---

**Document Status**: ✅ Approved and Validated
**Next Steps**: Proceed to `/speckit.plan` phase for technical implementation planning. All requirements clarified, 37 acceptance criteria defined, quality validation complete.
