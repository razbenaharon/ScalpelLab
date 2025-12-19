# Feature Specification: Core Architecture Refactor

**Feature Branch**: `001-core-refactor`
**Created**: 2025-12-19
**Status**: Draft
**Input**: User description: "Refactor ScalpelLab into a clean, modular 'Core + Interfaces' architecture."

## User Scenarios & Testing

### User Story 1 - Unified CLI Operations (Priority: P1)

As a developer or power user, I want to perform all core data operations via a single `scalpel` command so that I don't have to remember multiple script paths and arguments.

**Why this priority**: Consolidates the "messy" script landscape into a predictable interface, forming the basis for the automation pipeline.

**Independent Test**: Verify `scalpel --help` lists subcommands. Run `scalpel scan` and verify the SQLite database updates exactly as the old script did.

**Acceptance Scenarios**:

1. **Given** a fresh install, **When** I run `scalpel scan`, **Then** the database is populated from the filesystem using the logic from `scripts/update_db.py`.
2. **Given** a set of SEQ files, **When** I run `scalpel export --targets [criteria]`, **Then** the conversion logic triggers (simulated or dry-run) without crashing.
3. **Given** an Excel redaction file, **When** I run `scalpel redact --config [file]`, **Then** the redaction process initializes correctly.

### User Story 2 - Modular Core Library (Priority: P1)

As a developer, I want to import `scalpellab.core` in the Streamlit app so that the UI shares the exact same logic and configuration as the CLI.

**Why this priority**: Eliminates code duplication and "source of truth" conflicts between the app and scripts.

**Independent Test**: Create a small Python script that imports `scalpellab.core.config` and prints a value. Verify it matches `config.py`.

**Acceptance Scenarios**:

1. **Given** the `app/` directory, **When** I replace direct script calls with `from scalpellab.services import scanner`, **Then** the app functions identically but with cleaner code.
2. **Given** a change in `config.py` (e.g., path change), **When** I access it via `scalpellab.core.config`, **Then** the new value is reflected immediately in both CLI and App.

### User Story 3 - Streamlit App Migration (Priority: P2)

As a Data Manager, I want the existing Web UI to remain functional and familiar, but powered by the new backend, so that my workflow is uninterrupted.

**Why this priority**: Ensures the primary user interface remains usable during the refactor.

**Independent Test**: Launch the app with `streamlit run app/main.py` and verify all pages load and display data.

**Acceptance Scenarios**:

1. **Given** the "Database" page, **When** I view the table, **Then** it pulls data via the new `scalpellab.db` layer.
2. **Given** the "Status Summary" page, **When** I click "Refresh", **Then** it triggers the new `scalpellab.services.scanner` logic.

### Edge Cases

- **Missing Config**: What happens if `config.py` is missing or invalid? System should fail fast with a clear error message.
- **Database Lock**: How does the CLI handle a locked database (e.g., App is open)? It should retry or fail gracefully, not corrupt data.
- **Legacy Paths**: What if the filesystem contains files that don't match the regex patterns (e.g., manual renames)? These should be logged as warnings/skipped but not crash the scanner.
- **Partially Migrated App**: What if the user tries to run the old scripts directly? They should ideally print a deprecation warning or forward to the new CLI.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST implement a central `scalpellab` Python package.
- **FR-002**: The `scalpellab` package MUST expose a `config` module that loads settings from the root `config.py`.
- **FR-003**: The system MUST provide a CLI entry point `scalpel` (or `python -m scalpellab.cli`) with subcommands: `scan`, `export`, `redact`.
- **FR-004**: The `scan` logic MUST match the existing `scripts/update_db.py` behavior (recursive scan, regex parsing, DB upsert).
- **FR-005**: The database schema MUST remain compatible with existing SQLite files (or include a migration if absolutely necessary - preference is zero schema change).
- **FR-006**: Video operations (export, redact) MUST continue to support NVIDIA NVENC hardware acceleration arguments as per existing scripts.
- **FR-007**: File operations MUST respect the "Filesystem is Truth" principle—database entries are updated to match disk, not vice-versa (except for metadata like 'comments').

### Key Entities

- **Recording**: Represents a unique session (Date + Case No).
- **FileAsset**: Represents a physical file on disk (SEQ or MP4) with path, size, and status.
- **Camera**: Represents one of the 8 fixed camera angles.
- **Job**: A unit of work for the CLI (e.g., "Convert Case 12 Camera 3").

## Success Criteria

### Measurable Outcomes

- **SC-001**: **Zero Logic Loss**: The `scan` command produces the exact same database state as the legacy `update_db.py` script for a given dataset.
- **SC-002**: **Code Reduction**: The `app/` directory code size is reduced by at least 20% by offloading logic to `src/`.
- **SC-003**: **Developer Efficiency**: New "Hello World" script using `scalpellab` to find a file path takes < 5 lines of code.
- **SC-004**: **Maintainability**: `pylint` score of the new `src/` directory is > 8.0/10.

## Assumptions

- The existing `config.py` format will be preserved or slightly adapted but remains the configuration entry point.
- We are not changing the underlying tools (FFmpeg, MPV, Streamlit) or the database engine (SQLite) in this phase.
- The user has the necessary hardware (GPU) to test the video processing features, or we will mock them for CI.