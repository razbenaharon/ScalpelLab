# 1_OPTUNA_15_EPOCH - 15-Epoch Optuna SimCLR Search

This folder is the lightweight repo record for the first full SimCLR ReID
experiment. Heavy model files live outside the repo under:

`F:\Room_8_Data\SIMCLR\experiments\1_OPTUNA_15_EPOCH`

The original mixed output folder was archived, without deleting data, at:

`F:\Room_8_Data\SIMCLR\_archive_unrelated\simclr_output_15_epoches`

## Purpose

Run an early Optuna search over SimCLR/OSNet hyperparameters using 15 training
epochs per completed trial. This experiment established a useful learning-rate
and augmentation region before the later controlled 50-epoch LR sweep.

## Canonical Model

Use this backbone for any follow-up evaluation from this experiment:

`F:\Room_8_Data\SIMCLR\experiments\1_OPTUNA_15_EPOCH\trial_0074_best_backbone.pt`

Only the canonical `best_backbone.pt` was kept in the external experiment
folder. Full optimizer checkpoints and non-canonical backbones remain only in
the archived legacy output.

## Outcome

- Best trial: `trial_0074`
- Best validation NT-Xent: `2.8193627119064333`
- Best epoch: `14`
- Completed epochs: `15`
- Final train loss: `0.20426675496229613`
- Final validation loss: `2.821744306270893`

## Lightweight Files

- `report.md`: short narrative summary.
- `report_table.csv`: one-row canonical result table.
- `trial_summary.csv`: Optuna trial ranking exported from `optuna_study.db`.
- `config.json`: selected best-trial config.
- `results.json`: selected best-trial results.
- `optuna_study.db`: lightweight Optuna DB for provenance and re-querying.

No `.pt` files should be stored in this repo folder.
