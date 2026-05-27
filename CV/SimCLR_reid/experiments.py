"""Experiment registries for controlled SimCLR ReID sweeps."""

from __future__ import annotations

from pathlib import Path

DEFAULT_SEED = 42
DEFAULT_DATASET_DIR = Path("F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned")
DEFAULT_PRETRAINED_WEIGHTS = Path("F:/Projects/ScalpelLab/CV/osnet_ain_x1_0_msmt17.pt")

FREEZE_STEM_LAYERS = ["conv1", "maxpool", "conv2", "pool2"]

FIXED = {
    "freeze_early_layers": False,
    "freeze_layers": [],
    "projection_dim": 256,
    "temperature": 0.07,
    "color_jitter_strength": 0.48,
    "warmup_epochs": 5,
    "weight_decay": 1e-4,
    "epochs": 50,
    "batch_size": 512,
    "min_batch_size": 64,
    "seed": DEFAULT_SEED,
}

LR_SWEEP = {
    "lr_3e-4": {**FIXED, "lr": 3e-4},
    "lr_5e-4": {**FIXED, "lr": 5e-4},
    "lr_8e-4": {**FIXED, "lr": 8e-4},
    "lr_1.5e-3": {**FIXED, "lr": 1.5e-3},
    "lr_3e-3": {**FIXED, "lr": 3e-3},
    "lr_10": {**FIXED, "lr": 10.0},
}

DEFAULT_LR_SWEEP = ("lr_3e-4", "lr_5e-4", "lr_8e-4", "lr_1.5e-3")
OPTIONAL_EXPERIMENTS = {"lr_3e-3", "lr_10"}

ABLATIONS = {
    "proj_64": {"projection_dim": 64},
    "proj_128": {"projection_dim": 128},
    "temp_0.05": {"temperature": 0.05},
    "temp_0.10": {"temperature": 0.10},
    "jitter_0.35": {"color_jitter_strength": 0.35},
    "jitter_0.60": {"color_jitter_strength": 0.60},
    "staged_unfreeze": {
        "staged_unfreeze": True,
        "freeze_early_layers": True,
        "freeze_layers": FREEZE_STEM_LAYERS,
    },
}

PROTECTED_PATHS = [
    Path("CV/SimCLR_reid/simclr_output"),
    Path("CV/SimCLR_reid/test_output_dry_run"),
]
