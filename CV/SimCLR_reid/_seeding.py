"""Deterministic seeding helpers for SimCLR experiments."""

from __future__ import annotations

import random

import numpy as np
import torch


DEFAULT_SEED = 42


def set_seed(seed: int, deterministic: bool = True) -> None:
    """Seed Python, NumPy, and Torch RNGs for reproducible training runs."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    if deterministic:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

