# Quickstart: Core Architecture Refactor

**Branch**: `001-core-refactor`

## Setup

1.  **Install the package in editable mode**:
    ```bash
    pip install -e .
    ```

2.  **Verify CLI is accessible**:
    ```bash
    scalpel --help
    ```

## CLI Usage

### Scanning Files
Update the database with the latest file status from disk.
```bash
scalpel scan
# Options:
#   --dry-run      Preview changes only
#   --skip-seq     Update MP4s only
#   --fast         Skip duration calculation
```

### Video Conversion
Convert SEQ files to MP4.
```bash
scalpel export --targets "today"
# Options:
#   --targets      Query string or date range
```

### Redaction
Blacken video segments based on Excel times.
```bash
scalpel redact --config times.xlsx
```

## Python API Usage

```python
from scalpellab.core.config import settings
from scalpellab.services.scanner import ScannerService

# Access configuration
print(f"DB Path: {settings.DB_PATH}")

# Run a scan programmatically
scanner = ScannerService()
stats = scanner.scan_all(dry_run=True)
print(f"Found {stats.new_files} new files.")
```
