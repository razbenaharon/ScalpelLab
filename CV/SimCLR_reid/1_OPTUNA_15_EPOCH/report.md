# 1_OPTUNA_15_EPOCH - 15-Epoch Optuna SimCLR Summary

Best trial: `trial_0074`.

| Metric | Value |
|---|---:|
| Best validation NT-Xent | 2.8193627119064333 |
| Best epoch | 14 |
| Completed epochs | 15 |
| Final train loss | 0.20426675496229613 |
| Final validation loss | 2.821744306270893 |
| Learning rate | 0.0008 |
| Temperature | 0.07 |
| Projection dimension | 256 |
| Color jitter strength | 0.48 |

Canonical backbone: `F:\Room_8_Data\SIMCLR\experiments\1_OPTUNA_15_EPOCH\trial_0074_best_backbone.pt`.

Findings: the best 15-epoch Optuna run was `trial_0074`, with validation loss still near its best at the end of training. This experiment is useful as the earlier hyperparameter search baseline; later `2_LR_SWEEP` improved the best validation NT-Xent loss using longer 50-epoch controlled runs.
