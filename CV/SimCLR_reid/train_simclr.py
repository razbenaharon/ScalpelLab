"""
train_simclr.py - Burst-aware SimCLR training for OSNet person Re-ID.

Default mode launches a resumable Optuna study backed by SQLite. Use
--single_run for one-off training.
"""

import argparse
import csv
import gc
import json
import math
import random
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image, ImageFile, UnidentifiedImageError
from torch.utils.data import DataLoader, Dataset, Subset, WeightedRandomSampler
from torchvision import transforms

ImageFile.LOAD_TRUNCATED_IMAGES = True

try:
    import optuna
except ImportError:
    optuna = None

from _metrics_db import upsert_epoch
from _seeding import DEFAULT_SEED, set_seed
from _stability import StabilityGuard, UnstableRunError


# ============================================================================
# Try importing torchreid for OSNet; provide fallback instructions
# ============================================================================
try:
    import torchreid

    TORCHREID_AVAILABLE = True
except (ImportError, AttributeError):
    TORCHREID_AVAILABLE = False
    print("[WARNING] torchreid not found. Install with:")
    print("          pip install torchreid")


# ============================================================================
# CONFIGURATION & DEFAULTS
# ============================================================================

DEFAULT_CONFIG = {
    # Data
    "image_size": (256, 128),  # H x W (standard ReID size)
    "batch_size": 512,        # physical batch; override with --max_physical_batch
    "min_batch_size": 64,
    "auto_reduce_batch_on_oom": True,
    "pin_memory": False,      # opt-in on Windows; safer off after CUDA pressure/OOM
    "num_workers": 0,         # intentionally 0 for week-long Windows stability
    "burst_gap_threshold": 60,
    "val_video_indices": None,
    "val_split_modulo": 5,
    "val_split_remainder": 0,

    # Training
    "epochs": 50,
    "lr": 3e-4,
    "weight_decay": 1e-4,
    "warmup_epochs": 5,
    "accumulation_steps": 1,
    "seed": DEFAULT_SEED,
    "deterministic": True,

    # SimCLR
    "temperature": 0.07,
    "projection_dim": 128,
    "projection_hidden": 512,

    # Augmentation
    "color_jitter_strength": 0.5,
    "random_erasing_prob": 0.3,
    "crop_scale_min": 0.7,

    # Architecture
    "backbone": "osnet_ain_x1_0",
    "pretrained_weights": "F:/Projects/ScalpelLab/CV/osnet_ain_x1_0_msmt17.pt",
    # OSNet AIN structure: conv1, maxpool, conv2, pool2, conv3, pool3, conv4, conv5.
    "freeze_early_layers": True,
    "freeze_layers": ["conv1", "maxpool", "conv2", "pool2"],
    "staged_unfreeze": False,

    # Early-abort stability guard
    "stability_warmup_epochs": 3,
    "stability_nan_inf": True,
    "stability_explosion_factor": 5.0,
    "stability_absolute_max": 50.0,
    "stability_max_oom_retries": 3,
}


# ============================================================================
# DATASET
# ============================================================================

def build_bursts(image_paths, gap_threshold=60):
    """Group images into bursts and drop singleton bursts."""
    pattern = re.compile(r"^(\d+)_v(\d+)_(\d+)\.jpg$", re.IGNORECASE)
    grouped = defaultdict(list)
    skipped = 0

    for path in image_paths:
        match = pattern.match(path.name)
        if not match:
            skipped += 1
            continue
        case_no = match.group(1)
        video_idx = int(match.group(2))
        frame_id = int(match.group(3))
        grouped[(case_no, video_idx)].append((frame_id, path))

    bursts = []
    burst_cases = []
    burst_video_indices = []
    singletons = 0

    for (case_no, video_idx), items in grouped.items():
        if not items:
            continue
        items.sort(key=lambda item: item[0])
        current = [items[0][1]]
        previous_frame = items[0][0]

        for frame_id, path in items[1:]:
            if frame_id - previous_frame > gap_threshold:
                if len(current) >= 2:
                    bursts.append(current)
                    burst_cases.append(case_no)
                    burst_video_indices.append(video_idx)
                else:
                    singletons += 1
                current = []
            current.append(path)
            previous_frame = frame_id

        if len(current) >= 2:
            bursts.append(current)
            burst_cases.append(case_no)
            burst_video_indices.append(video_idx)
        else:
            singletons += 1

    if skipped:
        print(f"[Dataset] Skipped {skipped} files not matching {{case}}_v{{idx}}_{{frame}}.jpg")
    if singletons:
        print(f"[Dataset] Dropped {singletons} singleton bursts (cannot form a positive pair)")

    return bursts, burst_cases, burst_video_indices


class BurstSimCLRDataset(Dataset):
    """Return two distinct frames from the same burst, augmented independently."""

    def __init__(self, root_dir, transform=None, image_size=(256, 128), burst_gap_threshold=60):
        self.root_dir = Path(root_dir)
        self.transform = transform
        self.image_size = image_size
        self._warned_bad_paths = set()

        all_paths = sorted(
            path for path in self.root_dir.glob("*.jpg")
            if not path.name.startswith(".")
        )
        if not all_paths:
            raise ValueError(f"No .jpg images found in {root_dir}")

        self.bursts, self.burst_cases, self.burst_video_indices = build_bursts(all_paths, burst_gap_threshold)
        if not self.bursts:
            raise ValueError(
                "No multi-image bursts found. Lower --burst_gap_threshold or check filename format."
            )
        if len(self.bursts) < 2:
            raise ValueError("At least two multi-image bursts are required for stable BatchNorm training.")

        sizes = [len(burst) for burst in self.bursts]
        total = sum(sizes)
        print(
            f"[Dataset] {len(self.bursts)} bursts, {total} images, "
            f"{len(set(self.burst_cases))} cases | "
            f"burst size min={min(sizes)} max={max(sizes)} mean={total / len(sizes):.2f}"
        )

        top_cases = Counter(self.burst_cases).most_common(10)
        for case_no, count in top_cases:
            print(f"  Case {case_no}: {count} bursts")
        if len(set(self.burst_cases)) > 10:
            print(f"  ... and {len(set(self.burst_cases)) - 10} more cases")

        self._case_burst_counts = Counter(self.burst_cases)

    def get_sample_weights(self, indices=None):
        if indices is None:
            indices = range(len(self.bursts))
        selected_cases = [self.burst_cases[idx] for idx in indices]
        case_counts = Counter(selected_cases)
        return torch.tensor(
            [1.0 / case_counts[case_no] for case_no in selected_cases],
            dtype=torch.float32,
        )

    def __len__(self):
        return len(self.bursts)

    def _load_rgb(self, path):
        try:
            with Image.open(path) as image:
                image = image.convert("RGB")
                return image.resize((self.image_size[1], self.image_size[0]), Image.BILINEAR)
        except (OSError, UnidentifiedImageError) as exc:
            if path not in self._warned_bad_paths:
                self._warned_bad_paths.add(path)
                print(f"[Dataset] Skipping unreadable image {path}: {exc}")
            return None

    def _blank_image(self):
        return Image.new("RGB", (self.image_size[1], self.image_size[0]), color=(0, 0, 0))

    def __getitem__(self, idx):
        burst = self.bursts[idx]
        order = list(range(len(burst)))
        random.shuffle(order)

        images = []
        for image_idx in order:
            image = self._load_rgb(burst[image_idx])
            if image is not None:
                images.append(image)
            if len(images) == 2:
                break

        if len(images) < 2:
            if burst[0] not in self._warned_bad_paths:
                print(f"[Dataset] Burst {idx} has fewer than two readable images; using a blank fallback.")
            while len(images) < 2:
                images.append(self._blank_image())

        if self.transform is not None:
            view1 = self.transform(images[0])
            view2 = self.transform(images[1])
        else:
            to_tensor = transforms.ToTensor()
            view1 = to_tensor(images[0])
            view2 = to_tensor(images[1])

        return view1, view2, idx


def parse_video_indices(value):
    """Parse comma-separated video IDs such as '00,05,10' into ints."""
    if value is None or value == "":
        return None
    indices = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        indices.append(int(part))
    return sorted(set(indices))


def build_video_idx_split(dataset, val_video_indices=None, val_split_modulo=5, val_split_remainder=0):
    """Split burst indices by disjoint video_idx values."""
    available_videos = sorted(set(dataset.burst_video_indices))
    if val_video_indices is None:
        val_videos = [vid for vid in available_videos if vid % val_split_modulo == val_split_remainder]
    else:
        val_videos = sorted(set(val_video_indices))

    unknown_videos = sorted(set(val_videos) - set(available_videos))
    if unknown_videos:
        print(f"[Split] Ignoring validation video_idx values not present in dataset: {unknown_videos}")
    val_video_set = set(val_videos) & set(available_videos)
    train_video_set = set(available_videos) - val_video_set

    if not val_video_set:
        raise ValueError("Validation split is empty. Adjust --val_video_indices or split modulo/remainder.")
    if not train_video_set:
        raise ValueError("Training split is empty. Adjust --val_video_indices or split modulo/remainder.")
    if train_video_set & val_video_set:
        raise AssertionError("Train/validation video_idx leakage detected.")

    train_indices = []
    val_indices = []
    for idx, video_idx in enumerate(dataset.burst_video_indices):
        if video_idx in val_video_set:
            val_indices.append(idx)
        else:
            train_indices.append(idx)

    if not train_indices or not val_indices:
        raise ValueError("Train/validation split produced an empty burst subset.")

    train_images = sum(len(dataset.bursts[idx]) for idx in train_indices)
    val_images = sum(len(dataset.bursts[idx]) for idx in val_indices)
    total_bursts = len(train_indices) + len(val_indices)
    total_images = train_images + val_images
    split_info = {
        "train_video_indices": sorted(train_video_set),
        "val_video_indices": sorted(val_video_set),
        "train_bursts": len(train_indices),
        "val_bursts": len(val_indices),
        "train_images": train_images,
        "val_images": val_images,
        "val_burst_fraction": len(val_indices) / total_bursts,
        "val_image_fraction": val_images / total_images,
    }

    print(
        "[Split] "
        f"train_videos={len(train_video_set)} val_videos={len(val_video_set)} | "
        f"train_bursts={len(train_indices):,} val_bursts={len(val_indices):,} "
        f"({split_info['val_burst_fraction']:.2%} val) | "
        f"train_images={train_images:,} val_images={val_images:,} "
        f"({split_info['val_image_fraction']:.2%} val)"
    )
    print(f"[Split] validation video_idx: {','.join(f'{vid:02d}' for vid in sorted(val_video_set))}")

    return train_indices, val_indices, split_info


# ============================================================================
# SIMCLR AUGMENTATIONS
# ============================================================================

def get_simclr_transform(
    image_size=(256, 128),
    color_jitter_strength=0.5,
    random_erasing_prob=0.3,
    crop_scale_min=0.7,
):
    """SimCLR augmentation pipeline tuned for person ReID crops."""
    color_jitter = transforms.ColorJitter(
        brightness=0.4 * color_jitter_strength,
        contrast=0.4 * color_jitter_strength,
        saturation=0.4 * color_jitter_strength,
        hue=0.1 * color_jitter_strength,
    )

    return transforms.Compose([
        transforms.RandomResizedCrop(
            size=image_size,
            scale=(crop_scale_min, 1.0),
            ratio=(0.4, 0.7),
        ),
        transforms.RandomHorizontalFlip(p=0.5),
        transforms.RandomApply([color_jitter], p=0.8),
        transforms.RandomGrayscale(p=0.2),
        transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 2.0)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        transforms.RandomErasing(
            p=random_erasing_prob,
            scale=(0.02, 0.25),
            ratio=(0.3, 3.3),
        ),
    ])


# ============================================================================
# NT-Xent LOSS
# ============================================================================

class NTXentLoss(nn.Module):
    """Normalized temperature-scaled cross entropy loss."""

    def __init__(self, temperature=0.07):
        super().__init__()
        self.temperature = temperature

    def forward(self, z1, z2):
        batch_size = z1.size(0)
        z = torch.cat([z1, z2], dim=0)
        similarities = F.cosine_similarity(z.unsqueeze(1), z.unsqueeze(0), dim=2)
        similarities = similarities / self.temperature

        mask = torch.eye(2 * batch_size, dtype=torch.bool, device=z.device)
        similarities.masked_fill_(mask, -1e9)

        positive_indices = torch.cat([
            torch.arange(batch_size, 2 * batch_size, device=z.device),
            torch.arange(0, batch_size, device=z.device),
        ])
        return F.cross_entropy(similarities, positive_indices)


# ============================================================================
# MODEL: OSNet + Projection Head
# ============================================================================

class SimCLRModel(nn.Module):
    """SimCLR model = OSNet backbone + projection head."""

    def __init__(
        self,
        backbone_name="osnet_ain_x1_0",
        pretrained_weights=None,
        projection_dim=128,
        projection_hidden=512,
        freeze_layers=None,
        allow_downloads=False,
    ):
        super().__init__()
        if not TORCHREID_AVAILABLE:
            raise RuntimeError("torchreid is required. Install it first.")

        weights_path = Path(pretrained_weights) if pretrained_weights else None
        if weights_path is not None and not weights_path.exists():
            raise FileNotFoundError(
                "Pretrained weights were requested but not found. "
                f"Offline training will not download them automatically: {weights_path}"
            )
        use_torchreid_pretrained = allow_downloads and weights_path is None

        model = torchreid.models.build_model(
            name=backbone_name,
            num_classes=1,
            pretrained=use_torchreid_pretrained,
        )

        if weights_path is not None:
            state = torch.load(weights_path, map_location="cpu")
            if isinstance(state, dict) and "state_dict" in state:
                state = state["state_dict"]
            model.load_state_dict(state, strict=False)
            print(f"[Model] Loaded pretrained weights from: {weights_path}")
        elif use_torchreid_pretrained:
            print("[Model] Loading torchreid-managed pretrained weights; this may require network access.")
        else:
            print("[Model] No pretrained weights requested; using random OSNet initialization.")

        self.backbone = model
        feat_dim = model.feature_dim

        self.projection_head = nn.Sequential(
            nn.Linear(feat_dim, projection_hidden),
            nn.BatchNorm1d(projection_hidden),
            nn.ReLU(inplace=True),
            nn.Linear(projection_hidden, projection_dim),
        )

        if freeze_layers:
            self._freeze_layers(freeze_layers)

    def _freeze_layers(self, layer_names):
        frozen_params = 0
        total_params = 0

        for name, param in self.backbone.named_parameters():
            total_params += param.numel()
            for layer_name in layer_names:
                if name.startswith(layer_name):
                    param.requires_grad = False
                    frozen_params += param.numel()
                    break

        pct = 100 * frozen_params / total_params if total_params else 0
        print(f"[Freeze] Frozen {frozen_params:,}/{total_params:,} params ({pct:.1f}%)")
        print(f"[Freeze] Frozen layers: {layer_names}")

    def forward(self, x, return_embedding=False):
        embedding = self._backbone_forward(x)
        if return_embedding:
            return embedding

        projected = self.projection_head(embedding)
        return F.normalize(projected, dim=1)

    def _backbone_forward(self, x):
        model = self.backbone
        x = model.conv1(x)
        x = model.maxpool(x)
        x = model.conv2(x)
        x = model.pool2(x)
        x = model.conv3(x)
        x = model.pool3(x)
        x = model.conv4(x)
        x = model.conv5(x)
        x = model.global_avgpool(x)
        x = x.view(x.size(0), -1)
        return model.fc(x)

    def get_backbone_state_dict(self):
        return {
            key: value
            for key, value in self.state_dict().items()
            if not key.startswith("projection_head")
        }


# ============================================================================
# TRAINING LOOP
# ============================================================================

def train_one_epoch(
    model,
    dataloader,
    criterion,
    optimizer,
    scaler,
    device,
    epoch,
    total_epochs,
    accumulation_steps=1,
    grad_clip=1.0,
    log_every=20,
):
    """Train for one epoch, return average loss."""
    model.train()
    total_loss = 0.0
    num_micro_batches = 0
    optimizer_steps = 0
    skipped_batches = 0
    optimizer.zero_grad(set_to_none=True)

    for batch_idx, (view1, view2, _) in enumerate(dataloader):
        view1 = view1.to(device, non_blocking=True)
        view2 = view2.to(device, non_blocking=True)

        autocast_device = "cuda" if device.type == "cuda" else "cpu"
        with torch.amp.autocast(device_type=autocast_device, enabled=scaler.is_enabled()):
            z1 = model(view1)
            z2 = model(view2)
            raw_loss = criterion(z1, z2)

        if not torch.isfinite(raw_loss.detach()):
            skipped_batches += 1
            optimizer.zero_grad(set_to_none=True)
            print(f"  [WARNING] Non-finite loss at batch {batch_idx}; skipped batch.")
            continue

        loss = raw_loss / accumulation_steps
        scaler.scale(loss).backward()

        is_step = ((batch_idx + 1) % accumulation_steps == 0) or (batch_idx + 1 == len(dataloader))
        if is_step:
            if grad_clip is not None:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
            old_scale = scaler.get_scale()
            scaler.step(optimizer)
            scaler.update()
            new_scale = scaler.get_scale()
            if new_scale >= old_scale:
                optimizer_steps += 1
            optimizer.zero_grad(set_to_none=True)

        total_loss += raw_loss.item()
        num_micro_batches += 1

        if batch_idx % log_every == 0:
            print(
                f"  Epoch [{epoch + 1}/{total_epochs}] "
                f"Batch [{batch_idx}/{len(dataloader)}] "
                f"Loss: {raw_loss.item():.4f}"
            )

    if num_micro_batches == 0:
        raise RuntimeError(f"All batches were skipped in epoch {epoch + 1}.")
    if skipped_batches:
        print(f"  [WARNING] Skipped {skipped_batches} non-finite batches in epoch {epoch + 1}.")

    return total_loss / num_micro_batches, optimizer_steps


def validate_one_epoch(model, dataloader, criterion, device, epoch, total_epochs, amp_enabled=True):
    """Evaluate one epoch on held-out bursts and return average validation loss."""
    model.eval()
    total_loss = 0.0
    num_batches = 0
    skipped_batches = 0
    autocast_device = "cuda" if device.type == "cuda" else "cpu"

    with torch.no_grad():
        for batch_idx, (view1, view2, _) in enumerate(dataloader):
            view1 = view1.to(device, non_blocking=True)
            view2 = view2.to(device, non_blocking=True)

            with torch.amp.autocast(device_type=autocast_device, enabled=amp_enabled):
                z1 = model(view1)
                z2 = model(view2)
                loss = criterion(z1, z2)

            if not torch.isfinite(loss.detach()):
                skipped_batches += 1
                print(f"  [WARNING] Non-finite validation loss at batch {batch_idx}; skipped batch.")
                continue

            total_loss += loss.item()
            num_batches += 1

    if num_batches == 0:
        raise RuntimeError(f"All validation batches were skipped in epoch {epoch + 1}.")
    if skipped_batches:
        print(f"  [WARNING] Skipped {skipped_batches} non-finite validation batches in epoch {epoch + 1}.")

    avg_loss = total_loss / num_batches
    print(f"  => Validation [{epoch + 1}/{total_epochs}] Loss: {avg_loss:.4f}")
    return avg_loss


def get_cosine_scheduler(optimizer, total_epochs, warmup_epochs, last_epoch=-1):
    """Cosine annealing with linear warmup."""
    warmup_epochs = max(1, warmup_epochs)

    def lr_lambda(epoch):
        if epoch < warmup_epochs:
            return (epoch + 1) / warmup_epochs
        progress = (epoch - warmup_epochs) / max(1, total_epochs - warmup_epochs)
        return 0.5 * (1 + np.cos(np.pi * progress))

    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda, last_epoch)


# ============================================================================
# SINGLE TRAINING RUN
# ============================================================================

def _reduced_batch_size(batch_size, min_batch_size):
    """Reduce batch size with 32-sample alignment for CUDA OOM recovery."""
    if batch_size <= min_batch_size:
        return None
    reduced = int(batch_size * 0.75)
    reduced = max(min_batch_size, (reduced // 32) * 32)
    if reduced >= batch_size:
        reduced = batch_size - 32
    return max(min_batch_size, reduced)


CUDA_OOM_MARKERS = (
    "out of memory",
    "cuda error: out of memory",
    "cublas_status_alloc_failed",
    "cuda out of memory",
)


def _is_cuda_oom_error(exc):
    """Return True for both explicit and asynchronous CUDA OOM exceptions."""
    if isinstance(exc, torch.OutOfMemoryError):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in CUDA_OOM_MARKERS)


def _clear_cuda_after_error():
    """Best-effort cleanup after CUDA memory pressure."""
    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
        except Exception as cleanup_exc:
            print(f"[CUDA] empty_cache failed during cleanup: {cleanup_exc}")
        try:
            torch.cuda.ipc_collect()
        except Exception as cleanup_exc:
            print(f"[CUDA] ipc_collect failed during cleanup: {cleanup_exc}")
    gc.collect()


METRICS_HEADER = ["epoch", "train_loss", "val_loss", "lr"]


def _ensure_metrics_csv(metrics_path, fresh=False):
    metrics_path = Path(metrics_path)
    if fresh or not metrics_path.exists():
        with metrics_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(METRICS_HEADER)


def _append_metrics_csv(metrics_path, epoch, train_loss, val_loss, lr):
    with Path(metrics_path).open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([epoch, train_loss, val_loss, lr])


def _load_metric_histories(metrics_path):
    train_loss_history = []
    val_loss_history = []
    lr_history = []
    metrics_path = Path(metrics_path)
    if not metrics_path.exists():
        return train_loss_history, val_loss_history, lr_history

    with metrics_path.open("r", newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            try:
                train_loss_history.append(float(row["train_loss"]))
                val_loss_history.append(float(row["val_loss"]))
                lr_history.append(float(row["lr"]))
            except (KeyError, TypeError, ValueError):
                continue
    return train_loss_history, val_loss_history, lr_history


def _capture_rng_state(train_generator=None):
    rng_state = {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
    }
    if train_generator is not None:
        rng_state["train_generator"] = train_generator.get_state()
    return rng_state


def _restore_rng_state(rng_state, train_generator=None):
    if not rng_state:
        return
    if "python" in rng_state:
        random.setstate(rng_state["python"])
    if "numpy" in rng_state:
        np.random.set_state(rng_state["numpy"])
    if "torch" in rng_state:
        torch.set_rng_state(rng_state["torch"])
    if torch.cuda.is_available() and rng_state.get("cuda") is not None:
        torch.cuda.set_rng_state_all(rng_state["cuda"])
    if train_generator is not None and rng_state.get("train_generator") is not None:
        train_generator.set_state(rng_state["train_generator"])


def _torch_load_checkpoint(path, map_location):
    try:
        return torch.load(path, map_location=map_location, weights_only=False)
    except TypeError:
        return torch.load(path, map_location=map_location)


def _write_curves(run_dir, train_loss_history, val_loss_history, lr_history):
    if not train_loss_history and not val_loss_history:
        return
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        print(f"[Plot] Could not write curves.png: {exc}")
        return

    epochs = list(range(1, max(len(train_loss_history), len(val_loss_history)) + 1))
    fig, ax_loss = plt.subplots(figsize=(9, 5))
    if train_loss_history:
        ax_loss.plot(epochs[: len(train_loss_history)], train_loss_history, label="train_loss")
    if val_loss_history:
        ax_loss.plot(epochs[: len(val_loss_history)], val_loss_history, label="val_loss")
    ax_loss.set_xlabel("Epoch")
    ax_loss.set_ylabel("NT-Xent loss")
    ax_loss.grid(True, alpha=0.25)

    ax_lr = ax_loss.twinx()
    if lr_history:
        ax_lr.plot(epochs[: len(lr_history)], lr_history, color="tab:green", alpha=0.5, label="lr")
    ax_lr.set_ylabel("Learning rate")

    handles, labels = ax_loss.get_legend_handles_labels()
    lr_handles, lr_labels = ax_lr.get_legend_handles_labels()
    ax_loss.legend(handles + lr_handles, labels + lr_labels, loc="best")
    fig.tight_layout()
    fig.savefig(Path(run_dir) / "curves.png", dpi=160)
    plt.close(fig)


def _set_backbone_layers_requires_grad(model, layer_names, requires_grad):
    changed = 0
    layer_names = list(layer_names or [])
    if not layer_names:
        return changed
    for name, param in model.backbone.named_parameters():
        if any(name.startswith(layer_name) for layer_name in layer_names):
            param.requires_grad = requires_grad
            changed += param.numel()
    state = "trainable" if requires_grad else "frozen"
    print(f"[StagedUnfreeze] Marked {changed:,} stem params as {state}: {layer_names}")
    return changed


def _save_training_checkpoint(
    checkpoint_path,
    model,
    optimizer,
    scheduler,
    scaler,
    epoch,
    config,
    best_val_loss,
    best_epoch,
    train_loss,
    val_loss,
    train_loss_history,
    val_loss_history,
    lr_history,
    patience_counter,
    no_step_epochs,
    stopped_early,
    train_generator,
):
    torch.save(
        {
            "epoch": epoch,
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict(),
            "scaler_state_dict": scaler.state_dict(),
            "loss": best_val_loss,
            "best_val_loss": best_val_loss,
            "best_epoch": best_epoch,
            "train_loss": train_loss,
            "val_loss": val_loss,
            "config": config,
            "train_loss_history": train_loss_history,
            "val_loss_history": val_loss_history,
            "lr_history": lr_history,
            "patience_counter": patience_counter,
            "no_step_epochs": no_step_epochs,
            "stopped_early": stopped_early,
            "rng_state": _capture_rng_state(train_generator),
        },
        checkpoint_path,
    )


def run_training(
    config,
    dataset_dir,
    output_dir,
    run_name="run",
    trial=None,
    patience=10,
    resume_from=None,
    metrics_conn=None,
):
    """Execute training, automatically retrying with smaller batches after CUDA OOM."""
    current_batch_size = int(config["batch_size"])
    min_batch_size = int(config.get("min_batch_size", 128))
    auto_reduce = bool(config.get("auto_reduce_batch_on_oom", True))
    attempted_batches = []

    while True:
        attempt_config = config.copy()
        attempt_config["batch_size"] = current_batch_size
        attempt_config["attempted_batch_sizes"] = attempted_batches + [current_batch_size]
        attempt_config["oom_retries_so_far"] = len(attempted_batches)

        try:
            return _run_training_once(
                attempt_config,
                dataset_dir,
                output_dir,
                run_name,
                trial,
                patience,
                resume_from=resume_from,
                metrics_conn=metrics_conn,
                oom_retries_so_far=len(attempted_batches),
            )
        except (torch.OutOfMemoryError, RuntimeError) as exc:
            if isinstance(exc, UnstableRunError):
                raise
            if not _is_cuda_oom_error(exc):
                raise

            attempted_batches.append(current_batch_size)
            _clear_cuda_after_error()

            max_oom_retries = int(config.get("stability_max_oom_retries", 3))
            if len(attempted_batches) >= max_oom_retries:
                raise UnstableRunError("repeated_oom_during_warmup") from exc

            next_batch_size = _reduced_batch_size(current_batch_size, min_batch_size)
            if not auto_reduce or next_batch_size is None:
                print(
                    f"[OOM] CUDA out of memory at batch_size={current_batch_size}. "
                    "Automatic batch reduction is disabled or already at the minimum."
                )
                raise

            print(
                f"[OOM] CUDA out of memory at batch_size={current_batch_size}: {exc}\n"
                f"[OOM] Clearing CUDA cache and retrying {run_name} from scratch "
                f"with batch_size={next_batch_size}."
            )
            time.sleep(2)
            current_batch_size = next_batch_size


def _run_training_once(
    config,
    dataset_dir,
    output_dir,
    run_name="run",
    trial=None,
    patience=10,
    resume_from=None,
    metrics_conn=None,
    oom_retries_so_far=0,
):
    """Execute a SimCLR training run and return the best validation loss."""
    config = config.copy()
    config["seed"] = int(config.get("seed", DEFAULT_SEED))
    config["deterministic"] = bool(config.get("deterministic", True))
    set_seed(config["seed"], deterministic=config["deterministic"])
    run_start_monotonic = time.monotonic()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    run_dir = Path(output_dir) / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / "metrics.csv"
    fresh_metrics = resume_from is None
    _ensure_metrics_csv(metrics_path, fresh=fresh_metrics)

    print(f"\n{'=' * 70}")
    print(f"TRAINING RUN: {run_name}")
    print(f"{'=' * 70}")
    print(f"Config: {json.dumps(config, indent=2, default=str)}")
    print(f"Device: {device}")
    print(f"Output: {run_dir}")

    if config["batch_size"] < 16:
        print("[WARNING] Physical batch < 16; projection-head BatchNorm statistics may be noisy.")
    if config["accumulation_steps"] > 1:
        effective_batch = config["batch_size"] * config["accumulation_steps"]
        print(f"[Train] Gradient accumulation: physical={config['batch_size']} effective={effective_batch}")
        print("[Train] NT-Xent negatives still come only from the physical mini-batch.")

    train_generator = torch.Generator()
    train_generator.manual_seed(config["seed"])

    transform = get_simclr_transform(
        image_size=config["image_size"],
        color_jitter_strength=config["color_jitter_strength"],
        random_erasing_prob=config["random_erasing_prob"],
        crop_scale_min=config["crop_scale_min"],
    )
    dataset = BurstSimCLRDataset(
        root_dir=dataset_dir,
        transform=transform,
        image_size=config["image_size"],
        burst_gap_threshold=config["burst_gap_threshold"],
    )

    train_indices, val_indices, split_info = build_video_idx_split(
        dataset,
        val_video_indices=config.get("val_video_indices"),
        val_split_modulo=config.get("val_split_modulo", 5),
        val_split_remainder=config.get("val_split_remainder", 0),
    )
    config.update(split_info)

    with open(run_dir / "config.json", "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2, default=str)

    train_dataset = Subset(dataset, train_indices)
    val_dataset = Subset(dataset, val_indices)

    sample_weights = dataset.get_sample_weights(train_indices)
    sampler = WeightedRandomSampler(
        weights=sample_weights,
        num_samples=len(train_dataset),
        replacement=True,
        generator=train_generator,
    )

    train_batch_size = min(config["batch_size"], len(train_dataset))
    val_batch_size = min(config["batch_size"], len(val_dataset))
    train_drop_last = len(train_dataset) >= config["batch_size"]
    pin_memory = bool(config.get("pin_memory", False)) and device.type == "cuda"
    train_loader = DataLoader(
        train_dataset,
        batch_size=train_batch_size,
        sampler=sampler,
        num_workers=0,
        pin_memory=pin_memory,
        drop_last=train_drop_last,
        persistent_workers=False,
        generator=train_generator,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=val_batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=pin_memory,
        drop_last=False,
        persistent_workers=False,
    )
    print(
        f"[DataLoader] train_batches={len(train_loader)} train_batch_size={train_batch_size} "
        f"drop_last={train_drop_last} | val_batches={len(val_loader)} "
        f"val_batch_size={val_batch_size} pin_memory={pin_memory}"
    )

    model = SimCLRModel(
        backbone_name=config["backbone"],
        pretrained_weights=config.get("pretrained_weights"),
        projection_dim=config["projection_dim"],
        projection_hidden=config["projection_hidden"],
        freeze_layers=config.get("freeze_layers"),
        allow_downloads=False,
    ).to(device)

    staged_unfreeze = bool(config.get("staged_unfreeze", False))
    trainable_params = list(model.parameters()) if staged_unfreeze else [p for p in model.parameters() if p.requires_grad]
    print(f"[Optimizer] Trainable parameters: {sum(p.numel() for p in trainable_params):,}")

    optimizer = torch.optim.AdamW(
        trainable_params,
        lr=config["lr"],
        weight_decay=config["weight_decay"],
    )
    scheduler = get_cosine_scheduler(
        optimizer,
        total_epochs=config["epochs"],
        warmup_epochs=config["warmup_epochs"],
    )
    criterion = NTXentLoss(temperature=config["temperature"]).to(device)
    scaler = torch.amp.GradScaler(
        "cuda",
        enabled=device.type == "cuda",
        init_scale=1024.0,
        growth_interval=1000,
    )

    best_val_loss = float("inf")
    best_epoch = None
    patience_counter = 0
    no_step_epochs = 0
    stopped_early = False
    train_loss_history = []
    val_loss_history = []
    lr_history = []
    start_epoch = 0

    if resume_from is not None:
        resume_path = Path(resume_from)
        if not resume_path.exists():
            raise FileNotFoundError(f"Resume checkpoint not found: {resume_path}")
        checkpoint = _torch_load_checkpoint(resume_path, map_location=device)
        model.load_state_dict(checkpoint["model_state_dict"])
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        scheduler.load_state_dict(checkpoint["scheduler_state_dict"])
        scaler.load_state_dict(checkpoint["scaler_state_dict"])
        start_epoch = int(checkpoint.get("epoch", 0))
        best_val_loss = float(checkpoint.get("best_val_loss", checkpoint.get("loss", best_val_loss)))
        best_epoch = checkpoint.get("best_epoch", best_epoch)
        train_loss_history = list(checkpoint.get("train_loss_history", []))
        val_loss_history = list(checkpoint.get("val_loss_history", []))
        lr_history = list(checkpoint.get("lr_history", []))
        if not train_loss_history or not val_loss_history:
            train_loss_history, val_loss_history, lr_history = _load_metric_histories(metrics_path)
        patience_counter = int(checkpoint.get("patience_counter", patience_counter))
        no_step_epochs = int(checkpoint.get("no_step_epochs", no_step_epochs))
        stopped_early = bool(checkpoint.get("stopped_early", stopped_early))
        _restore_rng_state(checkpoint.get("rng_state"), train_generator)
        print(f"[Resume] Loaded {resume_path}; continuing at epoch {start_epoch + 1}.")

    if staged_unfreeze and start_epoch >= int(config.get("warmup_epochs", 0)):
        _set_backbone_layers_requires_grad(model, config.get("freeze_layers"), True)

    guard = StabilityGuard(
        warmup_epochs=config.get("stability_warmup_epochs", 3),
        nan_inf=config.get("stability_nan_inf", True),
        explosion_factor=config.get("stability_explosion_factor", 5.0),
        absolute_max=config.get("stability_absolute_max", 50.0),
        max_oom_retries=config.get("stability_max_oom_retries", 3),
    )
    if train_loss_history:
        guard.loss_at_epoch_0 = train_loss_history[0]

    for epoch in range(start_epoch, config["epochs"]):
        if staged_unfreeze and epoch == int(config.get("warmup_epochs", 0)):
            _set_backbone_layers_requires_grad(model, config.get("freeze_layers"), True)

        try:
            train_loss, optimizer_steps = train_one_epoch(
                model,
                train_loader,
                criterion,
                optimizer,
                scaler,
                device,
                epoch,
                config["epochs"],
                accumulation_steps=max(1, config["accumulation_steps"]),
            )
        except RuntimeError as exc:
            if epoch < guard.warmup_epochs and "All batches were skipped" in str(exc):
                raise UnstableRunError(f"nonfinite_loss_epoch_{epoch}") from exc
            raise
        if not math.isfinite(train_loss) and epoch >= guard.warmup_epochs:
            raise RuntimeError(f"Non-finite average training loss at epoch {epoch + 1}: {train_loss}")

        if optimizer_steps > 0:
            no_step_epochs = 0
            scheduler.step()
        else:
            no_step_epochs += 1
            print(f"  [WARNING] No optimizer steps completed in epoch {epoch + 1}; LR schedule not advanced.")
            if scaler.is_enabled() and no_step_epochs >= 1:
                print("  [WARNING] AMP appears unstable for this run; disabling AMP and continuing in FP32.")
                scaler = torch.amp.GradScaler("cuda", enabled=False)

        try:
            val_loss = validate_one_epoch(
                model,
                val_loader,
                criterion,
                device,
                epoch,
                config["epochs"],
                amp_enabled=scaler.is_enabled(),
            )
        except RuntimeError as exc:
            if epoch < guard.warmup_epochs and "All validation batches were skipped" in str(exc):
                raise UnstableRunError(f"nonfinite_loss_epoch_{epoch}") from exc
            raise
        if not math.isfinite(val_loss) and epoch >= guard.warmup_epochs:
            raise RuntimeError(f"Non-finite average validation loss at epoch {epoch + 1}: {val_loss}")

        train_loss_history.append(train_loss)
        val_loss_history.append(val_loss)
        current_lr = optimizer.param_groups[0]["lr"]
        lr_history.append(current_lr)
        print(
            f"  => Epoch {epoch + 1}/{config['epochs']} | "
            f"Train Loss: {train_loss:.4f} | Val Loss: {val_loss:.4f} | LR: {current_lr:.6f}"
        )

        _append_metrics_csv(metrics_path, epoch + 1, train_loss, val_loss, current_lr)
        if metrics_conn is not None:
            upsert_epoch(
                metrics_conn,
                run_name,
                epoch + 1,
                train_loss,
                val_loss,
                current_lr,
                config.get("seed"),
                config.get("batch_size"),
                "running",
                time.monotonic() - run_start_monotonic,
            )

        if val_loss < best_val_loss - 1e-6:
            best_val_loss = val_loss
            best_epoch = epoch + 1
            patience_counter = 0
            _save_training_checkpoint(
                run_dir / "best_checkpoint.pt",
                model,
                optimizer,
                scheduler,
                scaler,
                epoch + 1,
                config,
                best_val_loss,
                best_epoch,
                train_loss,
                val_loss,
                train_loss_history,
                val_loss_history,
                lr_history,
                patience_counter,
                no_step_epochs,
                stopped_early,
                train_generator,
            )
            torch.save(model.get_backbone_state_dict(), run_dir / "best_backbone.pt")
            print(f"  => New best validation loss ({best_val_loss:.4f}) - checkpoint saved")
        else:
            patience_counter += 1

        _save_training_checkpoint(
            run_dir / "last_checkpoint.pt",
            model,
            optimizer,
            scheduler,
            scaler,
            epoch + 1,
            config,
            best_val_loss,
            best_epoch,
            train_loss,
            val_loss,
            train_loss_history,
            val_loss_history,
            lr_history,
            patience_counter,
            no_step_epochs,
            stopped_early,
            train_generator,
        )
        _write_curves(run_dir, train_loss_history, val_loss_history, lr_history)

        unstable_reason = guard.check(epoch, train_loss, val_loss, oom_retries_so_far)
        if unstable_reason is not None:
            if metrics_conn is not None:
                upsert_epoch(
                    metrics_conn,
                    run_name,
                    epoch + 1,
                    train_loss,
                    val_loss,
                    current_lr,
                    config.get("seed"),
                    config.get("batch_size"),
                    "unstable",
                    time.monotonic() - run_start_monotonic,
                )
            raise UnstableRunError(unstable_reason)

        if trial is not None:
            trial.report(val_loss, epoch)
            if trial.should_prune():
                if optuna is None:
                    raise RuntimeError("Optuna is required for pruning.")
                raise optuna.TrialPruned()

        if patience_counter >= patience:
            stopped_early = True
            print(f"[EarlyStop] No improvement for {patience} epochs at epoch {epoch + 1}")
            break

    torch.save(model.get_backbone_state_dict(), run_dir / "final_backbone.pt")

    results = {
        "run_name": run_name,
        "config": config,
        "seed": config.get("seed"),
        "best_loss": best_val_loss,
        "best_val_loss": best_val_loss,
        "best_epoch": best_epoch,
        "final_loss": val_loss_history[-1] if val_loss_history else None,
        "final_val_loss": val_loss_history[-1] if val_loss_history else None,
        "final_train_loss": train_loss_history[-1] if train_loss_history else None,
        "loss_history": val_loss_history,
        "train_loss_history": train_loss_history,
        "val_loss_history": val_loss_history,
        "lr_history": lr_history,
        "train_video_indices": split_info["train_video_indices"],
        "val_video_indices": split_info["val_video_indices"],
        "train_bursts": split_info["train_bursts"],
        "val_bursts": split_info["val_bursts"],
        "train_images": split_info["train_images"],
        "val_images": split_info["val_images"],
        "total_epochs": config["epochs"],
        "completed_epochs": len(val_loss_history),
        "stopped_early": stopped_early,
        "timestamp": datetime.now().isoformat(),
    }
    with open(run_dir / "results.json", "w", encoding="utf-8") as file:
        json.dump(results, file, indent=2, default=str)

    print(f"\n[DONE] {run_name}: Best Val Loss = {best_val_loss:.4f}")
    return best_val_loss


# ============================================================================
# OPTUNA STUDY
# ============================================================================

def objective(trial, args, base_config):
    cfg = base_config.copy()
    cfg["lr"] = trial.suggest_float("lr", 1e-5, 1e-3, log=True)
    cfg["temperature"] = trial.suggest_float("temperature", 0.05, 0.2)
    cfg["projection_dim"] = trial.suggest_categorical("projection_dim", [64, 128, 256])
    cfg["color_jitter_strength"] = trial.suggest_float("color_jitter_strength", 0.3, 0.7)
    cfg["freeze_early_layers"] = trial.suggest_categorical("freeze_early_layers", [True, False])
    cfg["freeze_layers"] = ["conv1", "maxpool", "conv2", "pool2"] if cfg["freeze_early_layers"] else []
    cfg["batch_size"] = args.max_physical_batch
    cfg["accumulation_steps"] = args.accumulation_steps
    cfg["epochs"] = args.epochs
    cfg["burst_gap_threshold"] = args.burst_gap_threshold
    cfg["val_video_indices"] = parse_video_indices(args.val_video_indices)
    cfg["val_split_modulo"] = args.val_split_modulo
    cfg["val_split_remainder"] = args.val_split_remainder

    run_name = f"trial_{trial.number:04d}"
    try:
        return run_training(
            cfg,
            args.dataset_dir,
            args.output_dir,
            run_name=run_name,
            trial=trial,
            patience=args.patience,
        )
    finally:
        _clear_cuda_after_error()


def _storage_url(args):
    if args.storage:
        return args.storage
    return f"sqlite:///{(Path(args.output_dir) / 'optuna_study.db').as_posix()}"


def _make_storage(storage_url):
    if storage_url.startswith("sqlite:///"):
        db_path = Path(storage_url.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return optuna.storages.RDBStorage(
            url=storage_url,
            engine_kwargs={"connect_args": {"timeout": 120}},
        )
    return storage_url


def _finished_trial_count(study):
    return sum(1 for trial in study.trials if trial.state.is_finished())


def run_optuna_study(args, base_config):
    if optuna is None:
        raise RuntimeError("Optuna is required. Install with: pip install optuna>=3.6")

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    storage_url = _storage_url(args)
    storage = _make_storage(storage_url)
    study = optuna.create_study(
        study_name=args.study_name,
        storage=storage,
        direction="minimize",
        load_if_exists=True,
        pruner=optuna.pruners.MedianPruner(n_startup_trials=3, n_warmup_steps=5, interval_steps=1),
        sampler=optuna.samplers.TPESampler(seed=42),
    )

    print(f"[Optuna] study={args.study_name} storage={storage_url}")
    deadline = time.monotonic() + args.timeout if args.timeout else None
    consecutive_driver_errors = 0

    while True:
        if args.n_trials is not None and _finished_trial_count(study) >= args.n_trials:
            break
        if deadline is not None and time.monotonic() >= deadline:
            print("[Optuna] Timeout reached; study can resume with the same command.")
            break

        remaining_timeout = None
        if deadline is not None:
            remaining_timeout = max(1, int(deadline - time.monotonic()))

        try:
            study.optimize(
                lambda trial: objective(trial, args, base_config),
                n_trials=1,
                timeout=remaining_timeout,
                gc_after_trial=True,
                catch=(RuntimeError, OSError, ValueError),
            )
            consecutive_driver_errors = 0
        except KeyboardInterrupt:
            print("[Optuna] Interrupted by user; study is safely resumable.")
            break
        except Exception as exc:
            consecutive_driver_errors += 1
            wait_s = min(300, 10 * consecutive_driver_errors)
            print(f"[Optuna] Driver/storage error #{consecutive_driver_errors}: {exc}")
            if consecutive_driver_errors >= 5:
                print("[Optuna] Pausing after repeated driver errors. Re-run the same command to resume.")
                break
            print(f"[Optuna] Sleeping {wait_s}s, then reloading the study.")
            time.sleep(wait_s)
            study = optuna.load_study(study_name=args.study_name, storage=storage)

    try:
        print(f"\nBest trial: #{study.best_trial.number}  loss={study.best_value:.4f}")
        print(f"Params: {study.best_params}")
    except ValueError:
        print("[Optuna] No completed trial yet.")
    return study


# ============================================================================
# CLI
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Burst-aware SimCLR training for person ReID in the operating room."
    )
    parser.add_argument(
        "--dataset_dir",
        type=str,
        default="F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned",
        help="Path to burst dataset directory from build_dataset.py.",
    )
    parser.add_argument("--output_dir", type=str, default="./simclr_output")
    parser.add_argument("--pretrained_weights", type=str, default=DEFAULT_CONFIG["pretrained_weights"])
    parser.add_argument(
        "--no_pretrained_weights",
        action="store_true",
        help="Use random OSNet initialization and do not load or download pretrained weights.",
    )

    # Mode
    parser.add_argument("--single_run", action="store_true", help="Run one training job instead of Optuna.")

    # Hardware
    parser.add_argument("--max_physical_batch", type=int, default=DEFAULT_CONFIG["batch_size"])
    parser.add_argument("--accumulation_steps", type=int, default=DEFAULT_CONFIG["accumulation_steps"])
    parser.add_argument("--min_physical_batch", type=int, default=DEFAULT_CONFIG["min_batch_size"])
    parser.add_argument(
        "--pin_memory",
        action="store_true",
        default=DEFAULT_CONFIG["pin_memory"],
        help="Enable pinned CPU memory for DataLoader transfers. Faster sometimes, less resilient after OOM.",
    )
    parser.add_argument(
        "--disable_auto_batch_reduction",
        action="store_true",
        help="Disable automatic CUDA-OOM retry with a smaller physical batch.",
    )

    # Training
    parser.add_argument("--epochs", type=int, default=DEFAULT_CONFIG["epochs"])
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--seed", type=int, default=DEFAULT_CONFIG["seed"])
    parser.add_argument(
        "--no_deterministic",
        action="store_true",
        help=(
            "Skip cuDNN deterministic flags. Deterministic mode is the default "
            "and may be roughly 10-20% slower."
        ),
    )
    parser.add_argument("--burst_gap_threshold", type=int, default=DEFAULT_CONFIG["burst_gap_threshold"])
    parser.add_argument(
        "--val_video_indices",
        type=str,
        default=None,
        help="Comma-separated validation video_idx values. Overrides --val_split_modulo/remainder.",
    )
    parser.add_argument("--val_split_modulo", type=int, default=DEFAULT_CONFIG["val_split_modulo"])
    parser.add_argument("--val_split_remainder", type=int, default=DEFAULT_CONFIG["val_split_remainder"])

    # Single-run overrides
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--projection_dim", type=int, default=None)
    parser.add_argument("--color_jitter", type=float, default=None)
    parser.add_argument(
        "--freeze_early_layers",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_CONFIG["freeze_early_layers"],
        help="For single runs, freeze conv1/maxpool/conv2/pool2. Use --no-freeze_early_layers to train them.",
    )

    # Optuna
    parser.add_argument("--n_trials", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=604800, help="Study timeout in seconds.")
    parser.add_argument("--storage", type=str, default=None)
    parser.add_argument("--study_name", type=str, default="simclr_reid")

    return parser.parse_args()


def main():
    args = parse_args()
    config = DEFAULT_CONFIG.copy()
    config["pretrained_weights"] = None if args.no_pretrained_weights else (args.pretrained_weights or config["pretrained_weights"])
    config["batch_size"] = args.max_physical_batch
    config["min_batch_size"] = args.min_physical_batch
    config["auto_reduce_batch_on_oom"] = not args.disable_auto_batch_reduction
    config["pin_memory"] = args.pin_memory
    config["accumulation_steps"] = args.accumulation_steps
    config["epochs"] = args.epochs
    config["seed"] = args.seed
    config["deterministic"] = not args.no_deterministic
    config["burst_gap_threshold"] = args.burst_gap_threshold
    config["val_video_indices"] = parse_video_indices(args.val_video_indices)
    config["val_split_modulo"] = args.val_split_modulo
    config["val_split_remainder"] = args.val_split_remainder
    config["freeze_early_layers"] = args.freeze_early_layers
    config["freeze_layers"] = ["conv1", "maxpool", "conv2", "pool2"] if args.freeze_early_layers else []

    if args.single_run:
        for key, value in [
            ("lr", args.lr),
            ("temperature", args.temperature),
            ("projection_dim", args.projection_dim),
            ("color_jitter_strength", args.color_jitter),
        ]:
            if value is not None:
                config[key] = value
        run_training(
            config,
            args.dataset_dir,
            args.output_dir,
            run_name="single_run",
            trial=None,
            patience=args.patience,
        )
    else:
        run_optuna_study(args, config)


if __name__ == "__main__":
    main()
