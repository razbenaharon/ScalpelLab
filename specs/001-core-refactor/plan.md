# Implementation Plan: Core Architecture Refactor

**Branch**: `001-core-refactor` | **Date**: 2025-12-19 | **Spec**: [specs/001-core-refactor/spec.md](specs/001-core-refactor/spec.md)
**Input**: Feature specification from `specs/001-core-refactor/spec.md`

## Summary

Refactor the existing ScalpelLab codebase into a cohesive `Core + Interfaces` architecture. The core business logic (scanning, video conversion, redaction) will be moved to a dedicated `scalpellab` library. The existing Streamlit app and a new unified CLI will consume this library, ensuring consistent logic and configuration across all interfaces.

## Technical Context

**Language/Version**: Python 3.7+ (constrained by existing environment)
**Primary Dependencies**: `streamlit` (UI), `pandas` (Data), `PyMuPDF` (PDF), `pillow` (Images), `openpyxl` (Excel), `sqlite3` (Stdlib).
**Storage**: SQLite (filesystem-based, preserving existing schema).
**Testing**: `pytest` for unit and integration testing.
**Target Platform**: Windows 10/11 (Local Desktop).
**Project Type**: Python Package + Streamlit Web App.
**Performance Goals**: Scanning speed matches or exceeds current script performance; Video operations utilize GPU (NVENC).
**Constraints**: Must run in the existing environment without requiring admin install privileges.
**Scale/Scope**: ~3000 LOC refactor.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Data Safety & Integrity**: Compliant. The plan centers on "Filesystem as Truth" and preserves the `mp4_status` tracking logic to prevent data loss.
- **II. Configuration-Driven**: Compliant. A core `config` module will standardize access to settings, replacing ad-hoc imports.
- **III. Modular Tooling**: Compliant. The primary goal is to decouple logic from scripts, enabling the `scalpellab` library to be the reusable core.
- **IV. Hardware & Performance**: Compliant. GPU-acceleration arguments will be preserved in the new CLI wrappers.
- **V. Reproducibility**: Compliant. The directory structure will be standardized, and `requirements.txt` will be updated to reflect the package structure.

## Project Structure

### Documentation (this feature)

```text
specs/001-core-refactor/
├── plan.md              # This file
├── research.md          # (Skipped - Domain well understood)
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
src/
└── scalpellab/              # CORE LIBRARY
    ├── __init__.py
    ├── core/
    │   ├── __init__.py
    │   ├── config.py        # Settings loader
    │   └── types.py         # Type definitions
    ├── db/
    │   ├── __init__.py
    │   ├── models.py        # Data classes
    │   └── repository.py    # SQLite interactions
    ├── services/
    │   ├── __init__.py
    │   ├── scanner.py       # File scanning logic
    │   ├── conversion.py    # Video conversion logic
    │   └── redaction.py     # Redaction logic
    └── cli/
        ├── __init__.py
        └── main.py          # Click/Argparse entry point

app/                         # UI LAYER (Streamlit)
    ├── __init__.py
    ├── main.py              # App entry point
    └── pages/               # UI Pages

tests/
    ├── unit/
    └── integration/
```

**Structure Decision**: A standard `src/` layout is chosen to clearly separate the importable library (`scalpellab`) from the application entry points (`app/` and `cli/`). This prevents the common "script import" issues found in the current flat structure.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `src/` Layout | To solve import path issues | Flat layout causes circular imports and `sys.path` hacks (current state). |
| Service Layer | To decouple Logic from UI | Putting logic in Views makes it untestable and unshareable (current state). |
