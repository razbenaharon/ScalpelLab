# Tests

End-to-end tests for the ScalpelLab pipeline (SEQ → DB → MP4) and the dashboard
launcher pages. Run with `pytest -q tests/` from the repo root.

## What runs without setup

These tests run on a clean clone — no env vars, no sample data:

- `test_progress_parser.py` — unit tests for the `PROGRESS::JSON` parser the
  dashboard uses to drive the live counter strip.
- `test_pages_smoke.py` — module-import smoke for `app/pages/nuk_export.py`,
  `app/pages/update_db.py`, and `app/pages/seq_to_mp4.py`. Catches any
  syntax/import regression that would break the dashboard at startup.

```bash
pytest -q tests/test_progress_parser.py tests/test_pages_smoke.py
```

## What needs a sample directory

`test_pipeline_e2e.py` drives the three real scripts as subprocesses against
a small real-file SEQ sample. To enable it, copy **one case-worth of files**
from a non-production location into a scratch folder, then point an env var
at it:

```powershell
# Example: copy a single small case (one date / one case / a couple of cameras)
$sample = "C:\scalpel_test_sample"
$src = "F:\Room_8_Data\Sequence_Backup\DATA_25-01-15\Case3"  # CHANGE this
robocopy $src "$sample\DATA_25-01-15\Case3" /E /R:0 /W:0

# Tell the tests where to find it
$env:SCALPELLAB_TEST_SAMPLE_DIR = $sample

pytest -q tests/
```

Layout the env var should point at:

```
$SCALPELLAB_TEST_SAMPLE_DIR/
  DATA_YY-MM-DD/
    CaseN/
      CameraName_A/
        *.seq
        *.seq.idx
        *.metadata
      CameraName_B/
        ...
```

If the env var is unset or empty, the E2E test calls `pytest.skip(...)` — it
won't fail.

## Safety

- The test harness **never** touches `F:\Room_8_Data\` or
  `ScalpelDatabase.sqlite`. Every fixture writes to a `tmp_path_factory` dir.
- Env-var overrides (`SCALPEL_DB`, `SCALPEL_SEQ_ROOT`, `SCALPEL_MP4_ROOT`) are
  set per-subprocess in `conftest.py::env_overrides`, so even a script
  misreading `config.py` is redirected to the temp tree.
- **Do not** point `SCALPELLAB_TEST_SAMPLE_DIR` at the production
  `F:\Room_8_Data\Sequence_Backup` directly. Always copy a slice to a scratch
  folder first — `1_seq_curation.py` reads but does not modify its source, but
  defense in depth still matters.

## External tools

The full conversion test additionally requires `ffmpeg`, `ffprobe`, and
`mkvmerge` on `PATH`, plus an NVIDIA NVENC-capable GPU. Without those:

- The dry-run step still runs (it only needs `ffprobe` for resolution
  detection).
- The real-encode step calls `pytest.skip(...)` with the underlying error tail.

## How long does it take?

- Parser + smoke tests: ~1 s total.
- E2E without encoder: ~10–30 s depending on sample size.
- E2E with encoder: dominated by FFmpeg; a single-minute, 2-camera sample
  encodes in 1–3 min on a modern NVENC card.

If a test takes longer than expected, the subprocess timeout in
`tests/test_pipeline_e2e.py` is 30 min — adjust if you're working with a
larger sample.
