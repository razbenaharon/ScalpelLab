# User Guide: MultiMPV Manual Synchronization

## Overview

The Manual Synchronization feature allows you to precise align multiple camera angles for surgical case reviews. It provides a visual interface to see which cameras are out of sync, adjust them individually, and save the corrections to the database for future sessions.

## Quick Start

1.  **Open a Case**:
    *   Launch `multiMPV.py` (or use the shortcut).
    *   Click **"Load from Database"**.
    *   Select a case from the browser list.
    *   Click **"Open Selected Case"** (or double-click).
    *   Ensure cameras are checked and click **"Load Videos"**.

2.  **Check Synchronization**:
    *   The videos will load in a grid.
    *   The **Sync Control Panel** window will open.
    *   Click **"Play All"** to start playback.
    *   Look for visual cues (e.g., a door opening, a hand movement) to spot sync issues.
    *   The Sync Panel shows a list of cameras.
        *   **Green (✓ Synced)**: Camera is aligned with the reference.
        *   **Yellow (⚠️ Out of Sync)**: Camera is ahead or behind.

3.  **Adjust Sync**:
    *   Identify a camera that is out of sync.
    *   **Pause** the video if helpful.
    *   In the Sync Panel, locate the row for that camera.
    *   Use the **Nudge Buttons** to shift the video in time:
        *   `+1.0s` / `+0.1s`: Delays the video (moves it later).
        *   `-1.0s` / `-0.1s`: Advances the video (moves it earlier).
    *   Or use the **Slider** for large adjustments.
    *   The **Offset** value will update (e.g., `-1.5s`).

4.  **Save Changes**:
    *   Once all cameras are synced, click the **"Save All Offsets"** button at the bottom.
    *   Review the changes in the confirmation dialog.
    *   Click **"Yes"** to save to the database.
    *   Next time you load this case, these offsets will be applied automatically.

## Advanced Features

### Timeline & Navigation
*   **Master Timeline**: Drag the slider at the top to scrub all videos simultaneously.
*   **Frame Step**: Use `< Frame` and `Frame >` buttons for precise frame-by-frame analysis.
*   **Speed Control**: Change playback speed (0.25x to 2.0x) from the dropdown. Audio is muted for non-1.0x speeds.

### Annotations
*   **Mark Timestamp**: Click "Mark Timestamp" to save an interesting moment. Enter a note (optional).
*   **Export**: Click "Export Annotations" to save your marked times to a JSON or Text file.

### Troubleshooting

*   **"Missing" in Camera List**: The video file could not be found. Check if the drive is connected.
*   **Sync Panel not controlling videos**: Ensure the `mpv.exe` path is correct in `config.ini`.
*   **Database Error**: Ensure `ScalpelDatabase.sqlite` is in the project folder.

## Keyboard Shortcuts (Global)

*   `Space`: Play/Pause All
*   `Left/Right Arrow`: Seek -/+ 5 seconds
*   `,` / `.`: Frame Step Backward/Forward