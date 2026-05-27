# 2_LR_SWEEP - 50-Epoch Controlled LR Sweep

Best run: `lr_1.5e-3` with best validation NT-Xent `2.689743207968198` at epoch `46`.

| Rank | Run | LR | Best Epoch | Best Val Loss | Final Val Loss | Wall Clock Hours |
|---:|---|---:|---:|---:|---:|---:|
| 1 | `lr_1.5e-3` | 0.0015 | 46 | 2.689743207968198 | 2.7041294162090006 | 14.927 |
| 2 | `lr_8e-4` | 0.0008 | 50 | 2.6933212032684914 | 2.6933212032684914 | 15.175 |
| 3 | `lr_5e-4` | 0.0005 | 45 | 2.694359018252446 | 2.7015892358926625 | 15.151 |
| 4 | `lr_3e-4` | 0.0003 | 45 | 2.7341207595971913 | 2.7387619412862336 | 16.319 |

Findings: `lr_1.5e-3` narrowly beat `lr_8e-4`, so the strongest learning-rate region was roughly `8e-4` to `1.5e-3`. No downstream ReID validation metrics were collected for this sweep; the ranking is based on held-out NT-Xent validation loss only.

Canonical backbones live under `F:\Room_8_Data\SIMCLR\experiments\2_LR_SWEEP\<run>\best_backbone.pt`.
