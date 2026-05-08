# MPV_Multiviewer/ — Tkinter + libmpv synchronized multi-camera viewer

Standalone tool for loading multiple camera angles of one case, scrubbing them
in sync, and writing per-camera offset corrections back to the DB. Independent
of the NiceGUI dashboard — runs from its own entry point.

```bash
python MPV_Multiviewer/run_viewer.py
```

## Layout

```
MPV_Multiviewer/
├── run_viewer.py            # Tkinter entry; main multiMPV class wiring everything
├── config.ini               # persisted UI/MPV settings (auto-created on first run)
├── lib/
│   ├── mpv_controller.py    # libmpv IPC controller — play/seek/pause across N players
│   ├── models.py            # Camera, CameraMetadata dataclasses
│   ├── sync_panel.py        # offset-adjustment side panel
│   └── db_browser.py        # case picker that reads from ScalpelDatabase.sqlite
└── docs/user-guide.md       # user-facing operating instructions
```

## Key facts

- **File extension**: `.mmpv` is a project-file format that bundles a set of
  videos and per-video offsets. The viewer also accepts plain `.mp4`, `.mkv`,
  `.avi`, and `.txt` (a list of paths).
- **DB integration**: opens `ScalpelDatabase.sqlite` directly to populate the
  case browser and to write `mp4_status.sync_offset_ms` when the user saves
  corrections. Path resolution differs from the NiceGUI app — check
  `lib/db_browser.py` before assuming behavior.
- **External dep**: requires `mpv.exe` on PATH (or in a configured location).
  This is not a Python package — installation is OS-level (https://mpv.io).
- The tool uses **Tkinter**, not NiceGUI. Don't import from `app/` here, and
  don't pull `nicegui`/Quasar idioms in.

## Pitfalls

- Sync correctness depends on accurate per-frame timestamps. If a recording
  has corrupt IDX timestamps (see `seq_enriched.time_drift_ms` outliers
  documented in the root `CLAUDE.md`), the resulting MP4 may have wrong
  duration and the offsets recorded here will compensate for that — be
  careful not to "fix" a recording by changing only the offset when the
  underlying MP4 is the problem.
- `config.ini` is rewritten on first run to strip the `first_run` marker. If
  you delete it during development, the app reinitializes defaults silently.
