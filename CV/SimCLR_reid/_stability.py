"""Early-abort guardrails for unstable SimCLR runs."""

from __future__ import annotations

import math
from typing import Optional


class UnstableRunError(RuntimeError):
    """Raised when a run is unstable enough to skip the remaining budget."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class StabilityGuard:
    def __init__(
        self,
        warmup_epochs: int = 3,
        nan_inf: bool = True,
        explosion_factor: float = 5.0,
        absolute_max: float = 50.0,
        max_oom_retries: int = 3,
    ):
        self.warmup_epochs = int(warmup_epochs)
        self.nan_inf = bool(nan_inf)
        self.explosion_factor = float(explosion_factor)
        self.absolute_max = float(absolute_max)
        self.max_oom_retries = int(max_oom_retries)
        self.loss_at_epoch_0: Optional[float] = None

    def check(
        self,
        epoch: int,
        train_loss: float,
        val_loss: float,
        oom_retries_so_far: int,
    ) -> Optional[str]:
        """Return a reason string if unstable, else None."""
        if epoch >= self.warmup_epochs:
            return None

        if self.nan_inf and (
            not math.isfinite(train_loss) or not math.isfinite(val_loss)
        ):
            return f"nonfinite_loss_epoch_{epoch}"

        if train_loss > self.absolute_max or val_loss > self.absolute_max:
            return "absolute_loss_ceiling_exceeded"

        if epoch == 0:
            self.loss_at_epoch_0 = train_loss
        elif self.loss_at_epoch_0 is not None and self.loss_at_epoch_0 > 0:
            ratio = train_loss / self.loss_at_epoch_0
            if ratio > self.explosion_factor:
                return f"loss_explosion_factor_{ratio:.1f}x"

        if oom_retries_so_far >= self.max_oom_retries:
            return "repeated_oom_during_warmup"

        return None

