# Tasks: Core Architecture Refactor

**Input**: Design documents from `specs/001-core-refactor/`
**Prerequisites**: plan.md, spec.md, data-model.md

## Phase 1: Setup & Core Structure (P1)

**Purpose**: Establish the package layout and configuration system.

- [ ] T001 Create `src/scalpellab/core` directory structure
- [ ] T002 Implement `src/scalpellab/core/config.py` (Load from `config.py`)
- [ ] T003 Implement `src/scalpellab/core/types.py` (Data classes for FileAsset, Recording)
- [ ] T004 Create `setup.py` or `pyproject.toml` for package installation

## Phase 2: Database Layer (P1)

**Purpose**: Abstract SQLite access.

- [ ] T005 Implement `src/scalpellab/db/repository.py` (Port SQL queries from `app/utils.py`)
- [ ] T006 Add unit tests for DB repository

## Phase 3: Services Logic (P1)

**Purpose**: Port business logic from scripts to library.

- [ ] T007 Implement `src/scalpellab/services/scanner.py` (Port `scripts/update_db.py`)
- [ ] T008 Implement `src/scalpellab/services/conversion.py` (Port `scripts/seq_to_mp4_convert.py`)
- [ ] T009 Implement `src/scalpellab/services/redaction.py` (Port `scripts/batch_blacken.py`)
- [ ] T010 Add integration tests for Scanner service (mock filesystem)

## Phase 4: CLI Implementation (P1)

**Purpose**: Create the unified command-line interface.

- [ ] T011 Implement `src/scalpellab/cli/main.py` (Entry point)
- [ ] T012 Implement `scalpel scan` command
- [ ] T013 Implement `scalpel export` command
- [ ] T014 Implement `scalpel redact` command
- [ ] T015 Verify CLI against `scripts/` behavior

## Phase 5: App Refactor (P2)

**Purpose**: Switch Streamlit app to use the new library.

- [ ] T016 Refactor `app/utils.py` to wrap `scalpellab.db`
- [ ] T017 Update `app/pages/1_Database.py` to use new services
- [ ] T018 Update `app/pages/2_Status_Summary.py` to use new services
- [ ] T019 Verify App functionality

## Phase 6: Cleanup (P3)

**Purpose**: Remove legacy scripts and finalized documentation.

- [ ] T020 Remove old scripts in `scripts/` (or move to `legacy/`)
- [ ] T021 Update `README.md` with new CLI instructions
