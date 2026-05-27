# SimCLR ReID

Research scripts for building and training a SimCLR-style person re-identification
model from operating-room video crops. The workflow uses YOLO to extract person
crops from `General_3` MP4 recordings, trains an OSNet backbone with burst-level
contrastive learning, and compares the fine-tuned model against a baseline ReID
model.

## Pipeline

1. Build a burst-crop dataset from surgical room videos.
2. Inspect or resize the generated crops if needed.
3. Probe the largest safe physical GPU batch.
4. Train OSNet with burst-aware SimCLR, AMP, gradient accumulation, and Optuna.
5. Validate the fine-tuned backbone on hold-out images of two known people.

## Files

- `build_dataset.py`: queries the ScalpelLab SQLite database for `General_3`
  videos, runs YOLO person detection, and saves burst-mode person crops.
- `find_max_batch.py`: probes GPU memory and recommends `--max_physical_batch`
  plus `--accumulation_steps` for a target effective batch.
- `train_simclr.py`: trains an OSNet backbone with burst-level SimCLR.
- `validate_model.py`: compares baseline and fine-tuned OSNet embeddings using
  intra-identity, inter-identity, and separability-gap metrics.
- `visualize_simclr_dataset.py`: analyzes the generated flat image dataset and
  prints per-case and burst statistics.
- `resize_images.py`: resizes every image in a directory to `256x128`.
- `inspect_osnet.py`: prints OSNet layer names, parameter counts, and feature
  shapes.

## Artifact Layout

Heavy SimCLR artifacts are stored outside the repo under
`F:\Room_8_Data\SIMCLR`. The repo keeps only lightweight context, reports,
metrics, plots, and DB summaries.

- External heavyweight root: `F:\Room_8_Data\SIMCLR\experiments`
- `CV/SimCLR_reid/1_OPTUNA_15_EPOCH`: lightweight record for the 15-epoch Optuna search.
- `CV/SimCLR_reid/2_LR_SWEEP`: lightweight record for the 50-epoch LR sweep.

Canonical backbone paths:

- `1_OPTUNA_15_EPOCH`: `F:\Room_8_Data\SIMCLR\experiments\1_OPTUNA_15_EPOCH\trial_0074_best_backbone.pt`
- `2_LR_SWEEP`: `F:\Room_8_Data\SIMCLR\experiments\2_LR_SWEEP\<run>\best_backbone.pt`

Do not commit `.pt` files or full optimizer checkpoints from SimCLR
experiments. Keep only report/context files, CSV/JSON summaries, plots, and
small SQLite metric databases in the repo.

## Dependencies

Install the project dependencies from the repository root:

```bash
pip install -r requirements.txt
```

For GPU training, install a CUDA-compatible PyTorch build for your machine.
`requirements.txt` installs the PyPI `torchreid` package, which exposes the
OSNet AIN backbones used here without compiling optional native extensions.

Training is offline-safe at runtime. The scripts never ask `torchreid` to
download weights by default. Keep the default local weights file available at
`F:/Projects/ScalpelLab/CV/osnet_ain_x1_0_msmt17.pt`, pass another local file
with `--pretrained_weights`, or pass `--no_pretrained_weights` to train from
random OSNet initialization.

## Dataset Format

The training dataset is a flat directory of crop images:

```text
simclr_burst_v3_cleaned/
├── 12_v00_000120.jpg
├── 12_v00_000140.jpg
├── 12_v00_000160.jpg
└── ...
```

Filename format:

```text
{case_no}_v{video_idx}_{frame_id}.jpg
```

`train_simclr.py` groups images by `(case_no, video_idx)` and splits them into
bursts whenever frame gaps exceed `--burst_gap_threshold` (default `60`). Each
training sample returns two distinct frames from the same burst, with independent
SimCLR augmentations. Case-balanced sampling is applied at the burst level.

## Build The Dataset

Edit these constants in `build_dataset.py` before running:

- `MODEL_PATH`: YOLO model path, currently `F:\YOLO_Models\yolo26m-pose.pt`.
- `OUTPUT_ROOT`: crop output directory, currently
  `F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned`.
- `CONFIDENCE_THRESHOLD`, `BURST_SIZE`, `BURST_FRAME_GAP`, `COOLDOWN_FRAMES`,
  and `PADDING_PIXELS` if you want different capture behavior.

Then run:

```bash
python CV/SimCLR_reid/build_dataset.py
```

The script reads `DB_PATH` and `MP4_ROOT` from the repository-level `config.py`.
It saves progress to `progress.json` in the output directory and writes a final
`dataset_stats.json`.

## Inspect The Dataset

`visualize_simclr_dataset.py` currently uses a hard-coded dataset path:

```python
DATASET_PATH = Path(r"F:\Room_8_Data\SIMCLR\dataset\simclr_burst_v3_cleaned")
```

After adjusting that path if needed, run:

```bash
python CV/SimCLR_reid/visualize_simclr_dataset.py
```

To resize a dataset directory in place:

```bash
python CV/SimCLR_reid/resize_images.py F:\Room_8_Data\SIMCLR\dataset\simclr_burst_v3_cleaned
```

## Probe Batch Size

Run the CUDA memory probe from the repository root:

```bash
python CV/SimCLR_reid/find_max_batch.py --allow_shared_gpu_memory
```

The final line prints a stress-test recommendation. Full training with the
validation loader is heavier, so use a conservative starting batch for week-long
runs and let the script automatically back off after CUDA OOM.

```bash
Use: --max_physical_batch 512 --min_physical_batch 64 --accumulation_steps 1
```

Gradient accumulation improves optimizer stability and the effective batch seen
by the optimizer. The NT-Xent negative pool still comes only from the physical
mini-batch.

## Train

Default mode starts a resumable Optuna study:

```bash
python CV/SimCLR_reid/train_simclr.py ^
  --dataset_dir F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned ^
  --output_dir CV/SimCLR_reid/simclr_output ^
  --max_physical_batch 512 ^
  --min_physical_batch 64 ^
  --accumulation_steps 1 ^
  --epochs 50 ^
  --n_trials 50 ^
  --timeout 604800
```

Optuna minimizes held-out validation loss over:

- `lr`: log-uniform `1e-5` to `1e-3`
- `temperature`: `0.05` to `0.2`
- `projection_dim`: `64`, `128`, or `256`
- `color_jitter_strength`: `0.3` to `0.7`
- `freeze_early_layers`: freeze or train OSNet `conv1/maxpool/conv2/pool2`

The study uses SQLite storage at `{output_dir}/optuna_study.db` by default,
`MedianPruner(n_warmup_steps=5)`, and `load_if_exists=True` so the same command
can resume after a crash, pause, or machine restart.

By default, validation uses a strict date/team split by `video_idx`: every
`video_idx` where `video_idx % 5 == 0` is held out, giving approximately an
80/20 train/validation split by burst volume with no video overlap.

Run one fixed configuration instead:

```bash
python CV/SimCLR_reid/train_simclr.py ^
  --single_run ^
  --dataset_dir F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned ^
  --output_dir CV/SimCLR_reid/simclr_output ^
  --max_physical_batch 512 ^
  --min_physical_batch 64 ^
  --accumulation_steps 1 ^
  --epochs 50 ^
  --lr 0.0003 ^
  --temperature 0.07 ^
  --projection_dim 128 ^
  --color_jitter 0.5
```

Useful training flags:

- `--single_run`: disable Optuna and train one fixed configuration.
- `--max_physical_batch`: physical mini-batch size that must fit in VRAM.
- `--accumulation_steps`: optimizer accumulation steps.
- `--min_physical_batch`: lowest batch size automatic CUDA-OOM recovery may use,
  default `128`.
- `--disable_auto_batch_reduction`: fail immediately on CUDA OOM instead of
  clearing CUDA memory and retrying the run with a smaller physical batch.
- `--pin_memory`: opt into pinned CPU memory for DataLoader transfers. It is
  off by default because Windows CUDA OOM recovery is more resilient without it.
- `--burst_gap_threshold`: frame gap used to split bursts, default `60`.
- `--val_video_indices`: explicit comma-separated validation `video_idx` values.
- `--val_split_modulo` / `--val_split_remainder`: default validation rule,
  `video_idx % 5 == 0`, used when `--val_video_indices` is not provided.
- `--patience`: early stop patience, default `10`.
- `--freeze_early_layers` / `--no-freeze_early_layers`: single-run control for
  freezing OSNet `conv1/maxpool/conv2/pool2`; Optuna chooses this per trial.
- `--n_trials`: target number of Optuna trials, default `50`.
- `--timeout`: Optuna timeout in seconds, default `604800` (one week).
- `--storage`: explicit Optuna storage URL.
- `--study_name`: Optuna study name, default `simclr_reid`.
- `--pretrained_weights`: OSNet weights path, default
  `F:/Projects/ScalpelLab/CV/osnet_ain_x1_0_msmt17.pt`.
- `--no_pretrained_weights`: train from random OSNet initialization without
  loading or downloading weights.

Each run directory may contain:

- `config.json`
- `best_checkpoint.pt`
- `best_backbone.pt`
- `final_backbone.pt`
- `results.json`

`best_checkpoint.pt` includes model, optimizer, AMP scaler, scheduler, epoch,
loss, and config state.

For long-running experiments, copy only lightweight summaries back into the repo
experiment folder and move the selected `best_backbone.pt` to
`F:\Room_8_Data\SIMCLR\experiments\<experiment_name>`.

## Validate

Prepare two directories of hold-out images, one for each person:

```text
validation_people/
├── person_a/
│   ├── a_001.jpg
│   └── ...
└── person_b/
    ├── b_001.jpg
    └── ...
```

Then run:

```bash
python CV/SimCLR_reid/validate_model.py ^
  --baseline_weights path\to\baseline_osnet.pt ^
  --finetuned_weights CV/SimCLR_reid/simclr_output\trial_0000\best_backbone.pt ^
  --person_a_dir path\to\validation_people\person_a ^
  --person_b_dir path\to\validation_people\person_b ^
  --output_dir CV/SimCLR_reid/validation_results
```

The validation script reports:

- mean same-person similarity for person A and person B
- mean different-person similarity
- separability gap, defined as `intra_similarity - inter_similarity`

Lower inter-identity similarity and a larger separability gap indicate better
identity separation.

## Useful Commands

```bash
python CV/SimCLR_reid/inspect_osnet.py
python CV/SimCLR_reid/build_dataset.py
python CV/SimCLR_reid/visualize_simclr_dataset.py
python CV/SimCLR_reid/resize_images.py <dataset_dir>
python CV/SimCLR_reid/find_max_batch.py
python CV/SimCLR_reid/train_simclr.py --dataset_dir <dataset_dir> --output_dir <output_dir>
python CV/SimCLR_reid/validate_model.py --baseline_weights <baseline.pt> --finetuned_weights <best_backbone.pt> --person_a_dir <person_a_dir> --person_b_dir <person_b_dir>
```

## Notes

- The scripts are Windows-oriented and several defaults use `F:` drive paths.
- `build_dataset.py` only pulls `General_3` / `General 3` videos from
  `mp4_status`.
- CUDA is used automatically when PyTorch detects it.
- Training intentionally uses `num_workers=0` to reduce Windows multiprocessing,
  MKL, file-handle, and long-run data-loader failure modes.
- Generated datasets and model checkpoints can be large; keep them outside git
  unless intentionally archiving a small example.
