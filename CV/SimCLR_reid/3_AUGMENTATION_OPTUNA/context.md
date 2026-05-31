# 3_AUGMENTATION_OPTUNA - 15-Epoch Augmentation Optuna Sweep

This folder is the lightweight repo record for the augmentation-only SimCLR
Optuna sweep completed on May 31, 2026. Heavy model files live outside the repo
under:

`F:\Room_8_Data\SIMCLR\experiments\3_AUGMENTATION_OPTUNA`

## Purpose

Search the SimCLR augmentation policy while keeping the known-good training
recipe fixed. The sweep varied crop strength, random erasing, color jitter,
grayscale, Gaussian blur, and solarization while holding LR, projection size,
temperature, seed, batch size, and the burst-aware validation split constant.

Fixed training controls:

- `epochs`: `15`
- `lr`: `0.0015`
- `temperature`: `0.07`
- `projection_dim`: `256`
- `batch_size`: `128`
- `seed`: `42`
- `freeze_early_layers`: `false`
- validation split: `video_idx % 5 == 0`

## Canonical Model

Use this backbone for any follow-up evaluation from this experiment:

`F:\Room_8_Data\SIMCLR\experiments\3_AUGMENTATION_OPTUNA\trial_0019\best_backbone.pt`

The repo copy intentionally excludes `best_backbone.pt`,
`best_checkpoint.pt`, `last_checkpoint.pt`, `final_backbone.pt`, and all other
`.pt` files.

## Outcome

- Best trial: `trial_0019`
- Best validation NT-Xent: `2.77376687343304`
- Best epoch: `15`
- Completed epochs for the best trial: `15`
- Final train loss for the best trial: `0.16558387272980107`
- Final validation loss for the best trial: `2.77376687343304`

Trial state counts from the packaged Optuna DB:

- Complete: `15`
- Pruned: `18`
- Failed: `2`

The top trials converged around aggressive cropping (`crop_scale_min` near
`0.74`) plus low random erasing probability. No downstream ReID validation
metrics were collected for this sweep, so the ranking is based only on held-out
NT-Xent validation loss.

## Lightweight Files

- `report.md`: short narrative summary and ranked table.
- `report_table.csv`: completed trials ranked by best validation loss.
- `trial_summary.csv`: all Optuna trials exported from `optuna_study.db`.
- `study_summary.json`: JSON summary written by the training script.
- `best_params.json`: best Optuna parameters written by the training script.
- `optuna_study.db`: lightweight Optuna DB for provenance and re-querying.
- Per-trial `config.json`, `metrics.csv`, `results.json`, and `curves.png`
  files remain in the external heavyweight folder.

Note: `study_summary.json` was last written before the final failed
`trial_0034`; `optuna_study.db` and `trial_summary.csv` are the authoritative
records for the final trial count.

No `.pt` files or `trial_*` folders should be stored in this repo folder.
