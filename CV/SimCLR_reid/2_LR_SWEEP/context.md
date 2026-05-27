# 2_LR_SWEEP - 50-Epoch Controlled LR Sweep

This folder is the lightweight repo record for the controlled SimCLR learning
rate sweep completed on May 27, 2026. Heavy model files live outside the repo
under:

`F:\Room_8_Data\SIMCLR\experiments\2_LR_SWEEP`

## Purpose

Compare fixed learning rates while holding the rest of the SimCLR/OSNet setup
constant. Each run trained for 50 epochs with the same burst-aware split and
the same OSNet AIN pretrained weights.

## Canonical Models

Each run keeps only its canonical best backbone externally:

- `F:\Room_8_Data\SIMCLR\experiments\2_LR_SWEEP\lr_3e-4\best_backbone.pt`
- `F:\Room_8_Data\SIMCLR\experiments\2_LR_SWEEP\lr_5e-4\best_backbone.pt`
- `F:\Room_8_Data\SIMCLR\experiments\2_LR_SWEEP\lr_8e-4\best_backbone.pt`
- `F:\Room_8_Data\SIMCLR\experiments\2_LR_SWEEP\lr_1.5e-3\best_backbone.pt`

The repo copy intentionally excludes `best_checkpoint.pt`, `last_checkpoint.pt`,
`final_backbone.pt`, and all other `.pt` files.

## Outcome

Best contrastive run: `lr_1.5e-3`.

- Best validation NT-Xent: `2.689743207968198`
- Best epoch: `46`
- Final train loss: `0.08825734452496606`
- Final validation loss: `2.7041294162090006`
- Completed epochs: `50`

The `lr_1.5e-3` run narrowly beat `lr_8e-4`, so the useful learning-rate region
appears to be around `8e-4` to `1.5e-3`. No downstream ReID validation metrics
were collected for this sweep, so the ranking is based only on held-out NT-Xent
validation loss.

## Lightweight Files

- `report.md`: short narrative summary and ranked table.
- `report_table.csv`: ranked sweep table.
- `experiment_metrics.sqlite`: lightweight epoch metric DB.
- Per-run subfolders: `metrics.csv`, `config.json`, `summary.json`,
  `results.json`, and `curves.png`.

No `.pt` files should be stored in this repo folder or in
`experiments_output\lr_sweep_v1`.
