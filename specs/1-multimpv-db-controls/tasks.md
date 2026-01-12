# Tasks: MultiMPV_Offset Manual Camera Synchronization

**Feature**: 1-multimpv-db-controls
**Total Tasks**: 24
**Status**: Ready for Implementation

## Dependencies

- **Phase 1** (Setup) must be completed first.
- **Phase 2** (Sync Core) blocks Phase 3 and 4.
- **Phase 3** (Database) and **Phase 4** (Playback) can be done in parallel after Phase 2.
- **Phase 5** (Polish) depends on all previous phases.

---

## Phase 1: Architecture & Core Logic

**Goal**: Establish the core architecture by refactoring the monolithic `multiMPV.py` into modular components.

- [ ] T001 [P] Create `models.py` with `Camera`, `Case`, `CameraMetadata` dataclasses `MultiMPV_Offset/models.py`
- [ ] T002 Create `mpv_controller.py` with `MPVController` class for IPC management `MultiMPV_Offset/mpv_controller.py`
- [ ] T003 Refactor `multiMPV.py` to use `MPVController` and new models `MultiMPV_Offset/multiMPV.py`
- [ ] T004 Implement database migration script for `offset_seconds` column `scripts/add_offset_column.py`

## Phase 2: Primary Sync Feature (User Story 1)

**Goal**: Implement the manual synchronization workflow with visual feedback and database persistence.

- [ ] T005 [US1] Create `SyncPanel` class skeleton with main UI layout `MultiMPV_Offset/sync_panel.py`
- [ ] T006 [US1] Implement timestamp polling thread using `MPVController` `MultiMPV_Offset/sync_panel.py`
- [ ] T007 [US1] Implement individual camera control rows with timeline sliders `MultiMPV_Offset/sync_panel.py`
- [ ] T008 [US1] Implement nudge controls (+/- 0.1s, 1.0s) and logic `MultiMPV_Offset/sync_panel.py`
- [ ] T009 [US1] Implement visual sync status indicators (Green/Yellow) `MultiMPV_Offset/sync_panel.py`
- [ ] T010 [US1] Implement "Save to Database" with mandatory confirmation dialog `MultiMPV_Offset/sync_panel.py`
- [ ] T011 [US1] Update `multiMPV.py` to launch `SyncPanel` and apply offsets on load `MultiMPV_Offset/multiMPV.py`

## Phase 3: Database Integration (User Story 2 & 3)

**Goal**: Allow users to browse cases from the database and load them efficiently.

- [ ] T012 [P] [US2] Create `DatabaseBrowser` class with filters and case list `MultiMPV_Offset/db_browser.py`
- [ ] T013 [P] [US2] Implement camera selection dialog within browser `MultiMPV_Offset/db_browser.py`
- [ ] T014 [US2] Implement background thumbnail generation logic `MultiMPV_Offset/db_browser.py`
- [ ] T015 [US3] Integrate `DatabaseBrowser` launch button into `multiMPV.py` `MultiMPV_Offset/multiMPV.py`

## Phase 4: Advanced Playback Controls (User Story 1 - Enhancements)

**Goal**: Provide fine-grained control over video playback.

- [ ] T016 [P] [US1] Implement master timeline scrubber in `SyncPanel` `MultiMPV_Offset/sync_panel.py`
- [ ] T017 [US1] Implement frame-step navigation buttons `MultiMPV_Offset/sync_panel.py`
- [ ] T018 [US1] Implement playback speed control with audio muting `MultiMPV_Offset/sync_panel.py`
- [ ] T019 [P] [US1] Implement split-view mode toggle logic `MultiMPV_Offset/sync_panel.py`

## Phase 5: Polish & Cross-Cutting Concerns

**Goal**: Ensure robustness, add annotations, and verify quality.

- [ ] T020 [US1] Implement annotation marking and export to JSON/Text `MultiMPV_Offset/sync_panel.py`
- [ ] T021 [P] Verify robust error handling for missing files and DB connection `MultiMPV_Offset/multiMPV.py`
- [ ] T022 Create user guide for manual synchronization `MultiMPV_Offset/docs/user-guide.md`
- [ ] T023 Run full integration test of sync workflow `specs/1-multimpv-db-controls/plan/test_sync.py`
- [ ] T024 Final code cleanup and linting `MultiMPV_Offset/`

## Implementation Strategy

1.  **MVP (Phase 1 & 2)**: Focus on getting the sync panel working with hardcoded video paths first, then connect the database save/load.
2.  **Enhancement (Phase 3)**: Add the browser to replace the file dialog.
3.  **Refinement (Phase 4 & 5)**: Add the "nice to have" controls and polish the UI.