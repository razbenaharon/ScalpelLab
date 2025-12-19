<!--
Sync Impact Report:
- Version Change: 0.0.0 -> 1.0.0 (Initial Ratification)
- Added Principles:
  - I. Data Safety & Integrity
  - II. Configuration-Driven
  - III. Modular Tooling
  - IV. Hardware & Performance
  - V. Reproducibility
- Templates Status:
  - .specify/templates/plan-template.md: ✅ Compatible
  - .specify/templates/spec-template.md: ✅ Compatible
  - .specify/templates/tasks-template.md: ✅ Compatible
- Follow-up: None
-->

# ScalpelLab Constitution

## Core Principles

### I. Data Safety & Integrity
Medical recording data is critical. Database operations (especially deletions) must be explicit, validated, and logged where appropriate. File operations (redaction, conversion) must track status (e.g., via `mp4_status` tables) to prevent data loss, corruption, or redundant processing. "Dry-run" modes SHOULD be implemented for bulk file operations.

### II. Configuration-Driven
All environment-specific paths, device mappings, and constants MUST reside in `config.py` or external configuration files (e.g., `config.ini`). Hardcoding filesystem paths or camera names in source code is strictly prohibited to ensure portability between workstations and environments.

### III. Modular Tooling
Scripts in `scripts/` MUST be independently executable via CLI, adhering to the "Unix philosophy" of doing one thing well. Common logic (e.g., path resolution, status checks) MUST reside in `scripts/helpers/` to avoid code duplication. The Streamlit app (`app/`) acts as a visualization and management layer, utilizing the same underlying data structures and helpers as the CLI tools.

### IV. Hardware & Performance
Video processing tasks (redaction, conversion) MUST prioritize hardware acceleration (e.g., NVIDIA NVENC) when available. Implementations MUST provide graceful fallbacks or clear error messaging if hardware acceleration is unavailable. Large file scanning and database updates MUST be optimized (e.g., "smart updates" that skip unchanged files) to prevent application unresponsiveness.

### V. Reproducibility
The execution environment MUST be strictly defined in `requirements.txt`. Database schema integrity is paramount; schema changes SHOULD be verified using provided tools (`compare_databases.py`) and documented (`ERD.pdf`, `scalpel_dbdiagram.txt`) to ensure the application and scripts remain in sync with the data model.

## Operational Constraints

### Technology Stack
- **Language**: Python 3.7+
- **Interface**: Streamlit for GUI, CLI for batch operations.
- **Database**: SQLite (local filesystem).
- **Video Backend**: FFmpeg (NVENC preferred), MPV for playback.

### Code Style & Structure
- Follow PEP 8 guidelines.
- Use `black` for formatting where possible.
- Imports should be absolute from the project root or relative within packages, avoiding circular dependencies between `app/` and `scripts/`.

## Governance

### Amendments
This constitution dictates the architectural and operational standards of ScalpelLab. Changes to these principles require a formal amendment process:
1.  **Proposal**: Discuss the need for change (e.g., switching database engines, abandoning a UI framework).
2.  **Draft**: Update this file with a new version number.
3.  **Ratification**: Commit the change with a clear message explaining the rationale.

### Compliance
All new features, scripts, and refactors MUST verify compliance with these principles. Code reviews (self or peer) must explicitly check against "Configuration-Driven" and "Data Safety" rules.

**Version**: 1.0.0 | **Ratified**: 2025-12-19 | **Last Amended**: 2025-12-19