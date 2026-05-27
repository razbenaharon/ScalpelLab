# CV/ — Computer-vision experiments (independent of the main pipeline)

Research code for pose estimation, multi-object tracking, and ReID on
operating-room footage. Reads MP4s produced by `scripts/3_seq_to_mp4_convert.py`
but **does not** read or write the main SQLite DB. Treat as a separate
sub-project.

## Layout

```
CV/
├── yolo/              # YOLOv8 pose + tracking + overlay scripts
└── SimCLR_reid/       # SimCLR / OSNet ReID experiments
```

## yolo/ — pose estimation + multi-object tracking

```
yolo/
├── 1_pose_anesthesiologist.py             # baseline pose pass
├── 1_pose_anesthesiologist_BotSort.py     # + BoT-SORT tracker
├── 1_pose_anesthesiologist_StrongSort.py  # + StrongSORT tracker
├── debug_pose_anesthesiologist_StrongSort.py
├── 2_inspect_parquet.py                   # inspect saved keypoint parquet
├── 3_process_tracks.py                    # post-process tracks
├── calibrate.py                           # camera calibration
├── live_visualize_overlay.py              # live overlay viewer
├── visualize_overlay.py                   # batch overlay generator
├── diagnose_tracking.py
└── osnet_ain_x1_0_msmt17.pt               # OSNet ReID weights checkpoint
```

Conventions:
- Pose data is serialized to **Parquet** via `pyarrow` (fast columnar reads
  for downstream track processing).
- `ultralytics` (YOLOv8) is the pose backbone; tracker variants are swapped
  out by the numbered prefix in the filename.
- The `osnet_ain_x1_0_msmt17.pt` checkpoint is committed to the repo and
  used as the appearance feature for StrongSORT.

## SimCLR_reid/ — appearance ReID training

```
SimCLR_reid/
├── simclr_reid.md                  # setup notes (read first)
├── build_dataset.py                # crop + organize per-track image dataset
├── resize_images.py                # preprocessing
├── train_simclr.py                 # contrastive training loop
├── validate_model.py               # evaluation on held-out tracks
├── inspect_osnet.py                # poke at OSNet feature distributions
├── visualize_simclr_dataset.py     # sanity-check dataset crops
├── 1_OPTUNA_15_EPOCH/              # lightweight 15-epoch Optuna summary
└── 2_LR_SWEEP/                     # lightweight 50-epoch LR sweep summary
```

Has its own `simclr_reid.md` — prefer that for run instructions.

## Pitfalls

- These scripts assume CUDA is available. CPU paths exist but are slow.
- Don't import anything from `app/`, `scripts/`, or `MPV_Multiviewer/`.
  Keeping CV self-contained protects the main pipeline from heavy ML deps
  when running in production.
- Model weights are large; `simclr_output/` and similar artifact dirs should
  not be committed (check `.gitignore` before adding new artifact directories).
- SimCLR backbones and checkpoints live under `F:\Room_8_Data\SIMCLR`; repo
  experiment folders keep only summaries, metrics, plots, and context.
