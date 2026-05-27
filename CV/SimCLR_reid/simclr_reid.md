# SimCLR ReID

Research scripts for building and training a SimCLR-style person re-identification
model from operating-room video crops. The workflow uses YOLO to extract person
crops from `General_3` MP4 recordings, trains an OSNet backbone with contrastive
learning, and compares the fine-tuned model against a baseline ReID model.

## Pipeline

1. Build a burst-crop dataset from surgical room videos.
2. Inspect or resize the generated crops if needed.
3. Train OSNet with SimCLR augmentations and NT-Xent loss.
4. Validate the fine-tuned backbone on hold-out images of two known people.

## Files

- `build_dataset.py`: queries the ScalpelLab SQLite database for `General_3`
  videos, runs YOLO person detection, and saves burst-mode person crops.
- `train_simclr.py`: trains an OSNet backbone with SimCLR contrastive learning.
- `validate_model.py`: compares baseline and fine-tuned OSNet embeddings using
  intra-identity, inter-identity, and separability-gap metrics.
- `visualize_simclr_dataset.py`: analyzes the generated flat image dataset and
  prints per-case and burst statistics.
- `resize_images.py`: resizes every image in a directory to `256x128`.
- `inspect_osnet.py`: prints OSNet layer names, parameter counts, and feature
  shapes.

## Artifact Layout

Heavy SimCLR artifacts are stored outside the repo under
`F:\Room_8_Data\SIMCLR`. Keep repo experiment folders lightweight: context,
reports, metrics, plots, JSON summaries, and small SQLite DBs only.

- `F:\Room_8_Data\SIMCLR\experiments\1_OPTUNA_15_EPOCH`: canonical
  heavyweight folder for `CV/SimCLR_reid/1_OPTUNA_15_EPOCH`.
- `F:\Room_8_Data\SIMCLR\experiments\2_LR_SWEEP`: canonical heavyweight
  folder for `CV/SimCLR_reid/2_LR_SWEEP`.
- `CV/SimCLR_reid/1_OPTUNA_15_EPOCH`: lightweight report for the 15-epoch Optuna search.
- `CV/SimCLR_reid/2_LR_SWEEP`: lightweight report for the 50-epoch LR sweep.

Canonical backbones:

- `F:\Room_8_Data\SIMCLR\experiments\1_OPTUNA_15_EPOCH\trial_0074_best_backbone.pt`
- `F:\Room_8_Data\SIMCLR\experiments\2_LR_SWEEP\<run>\best_backbone.pt`

Do not store SimCLR `.pt` files in the repo experiment folders.

## Dependencies

Install the project dependencies from the repository root:

```bash
pip install -r requirements.txt
```

The SimCLR/ReID scripts also require:

```bash
pip install torchvision matplotlib torchreid
```

If `torchreid` is unavailable from PyPI in your environment, install the source
package instead:

```bash
pip install git+https://github.com/KaiyangZhou/deep-person-reid.git
```

For GPU training, install a CUDA-compatible PyTorch build for your machine.

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

`train_simclr.py` extracts the case identifier from the filename and uses inverse
case-frequency sampling to balance cases during training.

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

## Train

Run training from the repository root:

```bash
python CV/SimCLR_reid/train_simclr.py ^
  --dataset_dir F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned ^
  --output_dir CV/SimCLR_reid/simclr_output ^
  --epochs 50 ^
  --batch_size 64 ^
  --lr 0.0003 ^
  --temperature 0.07
```

Optional pretrained OSNet weights:

```bash
python CV/SimCLR_reid/train_simclr.py ^
  --dataset_dir F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned ^
  --output_dir CV/SimCLR_reid/simclr_output ^
  --pretrained_weights path\to\msmt17_weights.pt
```

Outputs are written under the selected output directory. Each run may contain:

- `config.json`
- `best_backbone.pt`
- `best_full_model.pt`
- `checkpoint_epoch_*.pt`
- `final_backbone.pt`
- `results.json`
- `grid_search_summary.json`

Note: the current `main()` in `train_simclr.py` always calls `run_grid_search`,
even when `--grid_search` is not passed.

After a run, move the selected `best_backbone.pt` into
`F:\Room_8_Data\SIMCLR\experiments\<experiment_name>` and keep only lightweight
reports and metric files under `CV/SimCLR_reid/<experiment_name>`.

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
  --finetuned_weights CV/SimCLR_reid/simclr_output\run_001\best_backbone.pt ^
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
python CV/SimCLR_reid/train_simclr.py --dataset_dir <dataset_dir> --output_dir <output_dir>
python CV/SimCLR_reid/validate_model.py --baseline_weights <baseline.pt> --finetuned_weights <best_backbone.pt> --person_a_dir <person_a_dir> --person_b_dir <person_b_dir>
```

## Notes

- The scripts are Windows-oriented and several defaults use `F:` drive paths.
- `build_dataset.py` only pulls `General_3` / `General 3` videos from
  `mp4_status`.
- CUDA is used automatically when PyTorch detects it.
- `num_workers` is forced to `0` on Windows during training to avoid known
  multiprocessing/MKL instability.
- Generated datasets and model checkpoints can be large; keep them outside git
  unless intentionally archiving a small example.
