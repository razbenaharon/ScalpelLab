# 3_AUGMENTATION_OPTUNA - 15-Epoch Augmentation Optuna Sweep

Best trial: `trial_0019` with best validation NT-Xent `2.77376687343304` at epoch `15`.

| Rank | Trial | Best Val Loss | Best Epoch | Final Val Loss | Crop Min | Erase Prob | Erase Max | Jitter | Gray | Blur | Solarize |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `trial_0019` | 2.77376687343304 | 15 | 2.77376687343304 | 0.7400678891689445 | 0.2012792283530576 | 0.2009689670978414 | 0.44287315713019293 | 0.07742519492917913 | 0.9144499586960764 | 0.12266982149356377 |
| 2 | `trial_0033` | 2.783912014961243 | 15 | 2.783912014961243 | 0.7424224630239804 | 0.20185074599413957 | 0.32639400343598096 | 0.26634143803668603 | 0.16821876157168242 | 0.4616065211703595 | 0.001081966270590434 |
| 3 | `trial_0031` | 2.803761560183305 | 15 | 2.803761560183305 | 0.7499218311989673 | 0.23367254179678665 | 0.3048898462644657 | 0.28118924588651883 | 0.16459598365593983 | 0.2002096898437707 | 0.00330065054446696 |
| 4 | `trial_0032` | 2.8091937945439267 | 15 | 2.8091937945439267 | 0.7413706107310618 | 0.23171792003354713 | 0.3030072452123942 | 0.2742271810359628 | 0.168140286480308 | 0.4862083487227354 | 0.007989814950613607 |
| 5 | `trial_0020` | 2.8291640969423146 | 14 | 2.844241903378413 | 0.7456256246066187 | 0.272903019354219 | 0.3076130207259027 | 0.3188622254879676 | 0.0780242380286119 | 0.916587035127982 | 0.1255004564984294 |

Findings: the best completed configuration used a high minimum crop scale,
very light random erasing, moderate color jitter, low grayscale probability,
heavy Gaussian blur, and modest solarization. The top cluster suggests that
preserving the person crop while perturbing camera/lighting shortcuts mattered
more than aggressive erasing in this 15-epoch setup.

This run improved the earlier 15-epoch Optuna baseline (`2.8193627119064333`)
but did not beat the 50-epoch LR sweep best (`2.689743207968198`). No
downstream ReID validation metrics were collected; rankings here are based on
held-out NT-Xent validation loss only.

Canonical backbone: `F:\Room_8_Data\SIMCLR\experiments\3_AUGMENTATION_OPTUNA\trial_0019\best_backbone.pt`.
