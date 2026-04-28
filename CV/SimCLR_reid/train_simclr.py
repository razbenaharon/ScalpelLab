"""
train_simclr.py - SimCLR Contrastive Learning Pipeline for Person Re-ID in the OR
================================================================================

PURPOSE:
    Train an OsNet backbone using SimCLR contrastive learning on burst-captured
    surgical room data. Supports hyperparameter grid search to find optimal config.

PIPELINE:
    1. Load burst dataset (grouped by case for WeightedRandomSampler)
    2. Apply SimCLR augmentations (crop, jitter, erasing)
    3. Train with NT-Xent contrastive loss
    4. Partial freezing: freeze stem + layers 1-2, train layers 3-4
    5. Save best model based on training loss

USAGE:
    python train_simclr.py --dataset_dir <path> --output_dir <path> [--grid_search]

================================================================================
"""

import os
import sys
import re
import json
import argparse
import itertools
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from torchvision import transforms
from PIL import Image
import numpy as np

# ============================================================================
# Try importing torchreid for OsNet; provide fallback instructions
# ============================================================================
try:
    import torchreid
    FeatureExtractor = torchreid.utils.FeatureExtractor
    TORCHREID_AVAILABLE = True
except (ImportError, AttributeError):
    TORCHREID_AVAILABLE = False
    print("[WARNING] torchreid not found. Install with: pip install torchreid")
    print("          Or: pip install git+https://github.com/KaiyangZhou/deep-person-reid.git")


# ============================================================================
# CONFIGURATION & DEFAULTS
# ============================================================================

DEFAULT_CONFIG = {
    # Data
    "image_size": (256, 128),          # H x W (standard ReID size)
    "batch_size": 64,
    "num_workers": 0,              # Use 0 on Windows to avoid forrtl/Intel MKL crash

    # Training
    "epochs": 50,
    "lr": 3e-4,
    "weight_decay": 1e-4,
    "warmup_epochs": 5,

    # SimCLR
    "temperature": 0.07,               # NT-Xent temperature
    "projection_dim": 128,             # Projection head output dim
    "projection_hidden": 512,          # Projection head hidden dim

    # Augmentation
    "color_jitter_strength": 0.5,
    "random_erasing_prob": 0.3,
    "crop_scale_min": 0.7,

    # Architecture
    "backbone": "osnet_ain_x1_0",
    "pretrained_weights": None,        # Path to .pt file or None for ImageNet
    # OsNet AIN structure: conv1, maxpool, conv2, pool2, conv3, pool3, conv4, conv5
    # Freeze: Stem (conv1, maxpool) + Early layers (conv2, pool2, conv3, pool3)
    # Train:  Deep layers (conv4, conv5) — these learn domain-specific features
    "freeze_layers": ["conv1", "maxpool", "conv2", "pool2", "conv3", "pool3"],
}

# Hyperparameter grid for search
HYPERPARAM_GRID = {
    "epochs":               [30, 50],
    "lr":                   [3e-4, 1e-4],
    "temperature":          [0.07, 0.1],
    "batch_size":           [64],
    "color_jitter_strength": [0.5],
    "projection_dim":       [128],
}


# ============================================================================
# DATASET
# ============================================================================

class BurstSimCLRDataset(Dataset):
    """
    Dataset for SimCLR training from burst-captured images.

    Each image filename is expected as: {case_no}_v{video_idx}_{frame_id}.jpg
    The case_no is extracted for per-case balancing via WeightedRandomSampler.

    For each __getitem__ call, returns TWO augmented views of the same image
    (the SimCLR positive pair).
    """

    def __init__(self, root_dir: str, transform=None, image_size=(256, 128)):
        self.root_dir = Path(root_dir)
        self.transform = transform
        self.image_size = image_size

        # Collect all images
        self.image_paths = sorted([
            p for p in self.root_dir.glob("*.jpg")
            if not p.name.startswith(".")
        ])

        if len(self.image_paths) == 0:
            raise ValueError(f"No .jpg images found in {root_dir}")

        # Extract case_no from filename for balancing
        self.case_ids = []
        self.case_to_indices = defaultdict(list)

        for idx, path in enumerate(self.image_paths):
            case_no = self._extract_case(path.name)
            self.case_ids.append(case_no)
            self.case_to_indices[case_no].append(idx)

        print(f"[Dataset] Loaded {len(self.image_paths)} images "
              f"from {len(self.case_to_indices)} cases")

        # Print case distribution for debugging
        for case, indices in sorted(self.case_to_indices.items(),
                                     key=lambda x: len(x[1]), reverse=True)[:10]:
            print(f"  Case {case}: {len(indices)} images")
        if len(self.case_to_indices) > 10:
            print(f"  ... and {len(self.case_to_indices) - 10} more cases")
        if len(self.case_to_indices) == 1:
            print(f"  [WARNING] Only 1 case found! WeightedRandomSampler will have no balancing effect.")
            print(f"  [WARNING] Sample filename: {self.image_paths[0].name}")

    @staticmethod
    def _extract_case(filename: str) -> str:
        """
        Extract case identifier from filename.

        Supports formats:
          - '42_v01_000123.jpg'       -> case '42'
          - 'case42_v01_000123.jpg'   -> case '42'
          - 'C042_v01_000123.jpg'     -> case '042'
          - '42_000123.jpg'           -> case '42'
        """
        # Standard: {case_no}_v{idx}_{frame}.jpg
        match = re.match(r"^(\d+)_v\d+_", filename)
        if match:
            return match.group(1)

        # With 'case' prefix
        match = re.match(r"^[Cc](?:ase)?(\d+)_", filename)
        if match:
            return match.group(1)

        # Fallback: first numeric segment
        match = re.match(r"^(\d+)_", filename)
        if match:
            return match.group(1)

        # Last resort: use first part before underscore
        return filename.split("_")[0]

    def get_sample_weights(self) -> torch.Tensor:
        """
        Compute inverse-frequency weights for WeightedRandomSampler.
        W_i = 1 / N_case(i)  => balanced representation across cases.
        """
        case_counts = {case: len(indices)
                       for case, indices in self.case_to_indices.items()}
        weights = torch.zeros(len(self.image_paths))
        for idx, case in enumerate(self.case_ids):
            weights[idx] = 1.0 / case_counts[case]
        return weights

    def __len__(self):
        return len(self.image_paths)

    def __getitem__(self, idx):
        img_path = self.image_paths[idx]
        img = Image.open(img_path).convert("RGB")

        # Resize to standard ReID size
        img = img.resize((self.image_size[1], self.image_size[0]), Image.BILINEAR)

        if self.transform:
            view1 = self.transform(img)
            view2 = self.transform(img)
        else:
            to_tensor = transforms.ToTensor()
            view1 = to_tensor(img)
            view2 = to_tensor(img)

        return view1, view2, idx


# ============================================================================
# SIMCLR AUGMENTATIONS
# ============================================================================

def get_simclr_transform(image_size=(256, 128),
                         color_jitter_strength=0.5,
                         random_erasing_prob=0.3,
                         crop_scale_min=0.7):
    """
    SimCLR augmentation pipeline optimized for person Re-ID.

    Augmentations act as "invariance teachers":
    - Color Jitter: forces model to ignore clothing shade
    - Random Erasing: forces focus on structural/body features
    - Random Crop: teaches spatial invariance
    """
    color_jitter = transforms.ColorJitter(
        brightness=0.4 * color_jitter_strength,
        contrast=0.4 * color_jitter_strength,
        saturation=0.4 * color_jitter_strength,
        hue=0.1 * color_jitter_strength,
    )

    transform = transforms.Compose([
        transforms.RandomResizedCrop(
            size=image_size,
            scale=(crop_scale_min, 1.0),
            ratio=(0.4, 0.7),   # person aspect ratio
        ),
        transforms.RandomHorizontalFlip(p=0.5),
        transforms.RandomApply([color_jitter], p=0.8),
        transforms.RandomGrayscale(p=0.2),
        transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 2.0)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406],
                             std=[0.229, 0.224, 0.225]),
        transforms.RandomErasing(
            p=random_erasing_prob,
            scale=(0.02, 0.25),
            ratio=(0.3, 3.3),
        ),
    ])
    return transform


# ============================================================================
# NT-Xent LOSS (SimCLR Contrastive Loss)
# ============================================================================

class NTXentLoss(nn.Module):
    """
    Normalized Temperature-scaled Cross Entropy Loss (NT-Xent).

    For a batch of N images producing 2N augmented views:
    - Attraction: pull together the 2 views of the same image
    - Repulsion: push apart views from different images
    """

    def __init__(self, temperature=0.07):
        super().__init__()
        self.temperature = temperature

    def forward(self, z1, z2):
        """
        Args:
            z1, z2: (N, D) normalized projection vectors for view 1 and view 2
        Returns:
            Scalar loss value
        """
        N = z1.size(0)
        z = torch.cat([z1, z2], dim=0)  # (2N, D)

        # Cosine similarity matrix
        sim = F.cosine_similarity(z.unsqueeze(1), z.unsqueeze(0), dim=2)  # (2N, 2N)
        sim = sim / self.temperature

        # Mask out self-similarity (diagonal)
        mask = torch.eye(2 * N, dtype=torch.bool, device=z.device)
        sim.masked_fill_(mask, -1e9)

        # Positive pairs: (i, i+N) and (i+N, i)
        pos_indices = torch.cat([
            torch.arange(N, 2 * N, device=z.device),
            torch.arange(0, N, device=z.device)
        ])  # (2N,)

        # Cross-entropy: treat positive pair as the correct class
        loss = F.cross_entropy(sim, pos_indices)
        return loss


# ============================================================================
# MODEL: OsNet + Projection Head
# ============================================================================

class SimCLRModel(nn.Module):
    """
    SimCLR model = OsNet Backbone + Projection Head.

    The Projection Head absorbs loss-specific distortions,
    keeping the Backbone representations clean for downstream inference.
    """

    def __init__(self, backbone_name="osnet_ain_x1_0",
                 pretrained_weights=None,
                 projection_dim=128,
                 projection_hidden=512,
                 freeze_layers=None):
        super().__init__()

        # --- Build OsNet Backbone ---
        if not TORCHREID_AVAILABLE:
            raise RuntimeError("torchreid is required. Install it first.")

        # Build model through torchreid
        model = torchreid.models.build_model(
            name=backbone_name,
            num_classes=1,  # dummy, we discard classifier
            pretrained=True if pretrained_weights is None else False,
        )

        # Load custom weights if provided (e.g., MSMT17 pretrained)
        if pretrained_weights and Path(pretrained_weights).exists():
            state = torch.load(pretrained_weights, map_location="cpu")
            # Handle different checkpoint formats
            if "state_dict" in state:
                state = state["state_dict"]
            model.load_state_dict(state, strict=False)
            print(f"[Model] Loaded pretrained weights from: {pretrained_weights}")

        # Extract backbone (remove classifier)
        # OsNet structure: conv1, maxpool, layer1, layer2, layer3, layer4, global_avgpool, fc, classifier
        self.backbone = model

        # Determine feature dimension from backbone
        feat_dim = model.feature_dim  # OsNet exposes this

        # --- Projection Head (MLP) ---
        self.projection_head = nn.Sequential(
            nn.Linear(feat_dim, projection_hidden),
            nn.BatchNorm1d(projection_hidden),
            nn.ReLU(inplace=True),
            nn.Linear(projection_hidden, projection_dim),
        )

        # --- Partial Freezing ---
        if freeze_layers:
            self._freeze_layers(freeze_layers)

    def _freeze_layers(self, layer_names):
        """Freeze specified layers to prevent catastrophic forgetting."""
        frozen_params = 0
        total_params = 0

        for name, param in self.backbone.named_parameters():
            total_params += param.numel()
            for layer_name in layer_names:
                if name.startswith(layer_name):
                    param.requires_grad = False
                    frozen_params += param.numel()
                    break

        pct = 100 * frozen_params / total_params if total_params > 0 else 0
        print(f"[Freeze] Frozen {frozen_params:,}/{total_params:,} params ({pct:.1f}%)")
        print(f"[Freeze] Frozen layers: {layer_names}")

    def forward(self, x, return_embedding=False):
        """
        Forward pass.
        Args:
            x: (N, 3, H, W) input images
            return_embedding: if True, return backbone embedding (for inference)
        Returns:
            if return_embedding: (N, feat_dim) backbone features
            else: (N, projection_dim) projected features for contrastive loss
        """
        # OsNet forward - get features before classifier
        # We need to extract intermediate features
        h = self._backbone_forward(x)

        if return_embedding:
            return h

        z = self.projection_head(h)
        z = F.normalize(z, dim=1)  # L2 normalize for cosine similarity
        return z

    def _backbone_forward(self, x):
        """Extract features from backbone (before classifier)."""
        model = self.backbone

        # OsNet uses conv1-conv5 structure, not layer1-layer4
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
        # Pass through fc layer (before classifier)
        x = model.fc(x)
        return x

    def get_backbone_state_dict(self):
        """Return only backbone weights (for inference without projection head)."""
        return {k: v for k, v in self.state_dict().items()
                if not k.startswith("projection_head")}


# ============================================================================
# TRAINING LOOP
# ============================================================================

def train_one_epoch(model, dataloader, criterion, optimizer, device, epoch, total_epochs):
    """Train for one epoch, return average loss."""
    model.train()
    total_loss = 0.0
    num_batches = 0

    for batch_idx, (view1, view2, _) in enumerate(dataloader):
        view1 = view1.to(device)
        view2 = view2.to(device)

        # Forward
        z1 = model(view1)
        z2 = model(view2)

        loss = criterion(z1, z2)

        # Backward
        optimizer.zero_grad()
        loss.backward()

        # Gradient clipping for stability
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)

        optimizer.step()

        total_loss += loss.item()
        num_batches += 1

        if batch_idx % 20 == 0:
            print(f"  Epoch [{epoch+1}/{total_epochs}] "
                  f"Batch [{batch_idx}/{len(dataloader)}] "
                  f"Loss: {loss.item():.4f}")

    return total_loss / max(num_batches, 1)


def get_cosine_scheduler(optimizer, total_epochs, warmup_epochs, last_epoch=-1):
    """Cosine annealing with linear warmup."""
    def lr_lambda(epoch):
        if epoch < warmup_epochs:
            return (epoch + 1) / warmup_epochs
        progress = (epoch - warmup_epochs) / max(1, total_epochs - warmup_epochs)
        return 0.5 * (1 + np.cos(np.pi * progress))

    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda, last_epoch)


# ============================================================================
# SINGLE TRAINING RUN
# ============================================================================

def run_training(config: dict, dataset_dir: str, output_dir: str, run_name: str = "run"):
    """
    Execute a full SimCLR training run with the given config.

    Returns:
        dict with final metrics and model path
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    run_dir = Path(output_dir) / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"TRAINING RUN: {run_name}")
    print(f"{'='*70}")
    print(f"Config: {json.dumps(config, indent=2, default=str)}")
    print(f"Device: {device}")
    print(f"Output: {run_dir}")

    # Save config
    with open(run_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2, default=str)

    # --- Dataset & DataLoader ---
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
    )

    # Weighted sampling for case balancing
    sample_weights = dataset.get_sample_weights()
    sampler = WeightedRandomSampler(
        weights=sample_weights,
        num_samples=len(dataset),
        replacement=True,
    )

    # Determine num_workers safely for the current OS
    num_workers = config["num_workers"]
    if os.name == "nt" and num_workers > 0:
        print(f"[WARNING] Windows detected: forcing num_workers=0 (was {num_workers}) "
              f"to avoid Intel MKL/forrtl crash")
        num_workers = 0

    dataloader = DataLoader(
        dataset,
        batch_size=config["batch_size"],
        sampler=sampler,
        num_workers=num_workers,
        pin_memory=True if torch.cuda.is_available() else False,
        drop_last=True,  # Important for contrastive loss
    )

    # --- Model ---
    model = SimCLRModel(
        backbone_name=config["backbone"],
        pretrained_weights=config.get("pretrained_weights"),
        projection_dim=config["projection_dim"],
        projection_hidden=config["projection_hidden"],
        freeze_layers=config.get("freeze_layers"),
    ).to(device)

    # --- Optimizer & Scheduler ---
    # Only optimize parameters that require gradients
    trainable_params = [p for p in model.parameters() if p.requires_grad]
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

    criterion = NTXentLoss(temperature=config["temperature"])

    # --- Training Loop ---
    best_loss = float("inf")
    loss_history = []

    for epoch in range(config["epochs"]):
        avg_loss = train_one_epoch(
            model, dataloader, criterion, optimizer, device,
            epoch, config["epochs"]
        )

        scheduler.step()
        current_lr = optimizer.param_groups[0]["lr"]
        loss_history.append(avg_loss)

        print(f"  => Epoch {epoch+1}/{config['epochs']} | "
              f"Loss: {avg_loss:.4f} | LR: {current_lr:.6f}")

        # Save best model (backbone only)
        if avg_loss < best_loss:
            best_loss = avg_loss
            torch.save(model.get_backbone_state_dict(),
                       run_dir / "best_backbone.pt")
            torch.save(model.state_dict(),
                       run_dir / "best_full_model.pt")
            print(f"  => New best model saved (loss: {best_loss:.4f})")

        # Periodic checkpoint
        if (epoch + 1) % 10 == 0:
            torch.save({
                "epoch": epoch + 1,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "scheduler_state_dict": scheduler.state_dict(),
                "loss": avg_loss,
                "config": config,
            }, run_dir / f"checkpoint_epoch_{epoch+1}.pt")

    # Save final model
    torch.save(model.get_backbone_state_dict(),
               run_dir / "final_backbone.pt")

    # Save training history
    results = {
        "run_name": run_name,
        "config": config,
        "best_loss": best_loss,
        "final_loss": loss_history[-1] if loss_history else None,
        "loss_history": loss_history,
        "total_epochs": config["epochs"],
        "timestamp": datetime.now().isoformat(),
    }
    with open(run_dir / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n[DONE] {run_name}: Best Loss = {best_loss:.4f}")
    return results


# ============================================================================
# HYPERPARAMETER GRID SEARCH
# ============================================================================

def run_grid_search(base_config: dict, dataset_dir: str, output_dir: str,
                    grid: dict = None):
    """
    Run grid search over hyperparameter combinations.

    Each combination is trained independently and results are compared.
    """
    if grid is None:
        grid = HYPERPARAM_GRID

    # Generate all combinations
    keys = list(grid.keys())
    values = list(grid.values())
    combinations = list(itertools.product(*values))

    print(f"\n{'='*70}")
    print(f"HYPERPARAMETER GRID SEARCH")
    print(f"{'='*70}")
    print(f"Parameters: {keys}")
    print(f"Total combinations: {len(combinations)}")
    print(f"Grid: {json.dumps(grid, indent=2)}")

    all_results = []

    for i, combo in enumerate(combinations):
        # Build config for this run
        config = base_config.copy()
        run_label_parts = []
        for key, val in zip(keys, combo):
            config[key] = val
            run_label_parts.append(f"{key}={val}")

        run_name = f"run_{i+1:03d}_{'_'.join(run_label_parts)}"
        print(f"\n[{i+1}/{len(combinations)}] {run_name}")

        try:
            result = run_training(config, dataset_dir, output_dir, run_name)
            all_results.append(result)
        except Exception as e:
            print(f"[ERROR] Run failed: {e}")
            all_results.append({
                "run_name": run_name,
                "config": config,
                "error": str(e),
            })

    # --- Summary ---
    print(f"\n{'='*70}")
    print(f"GRID SEARCH SUMMARY")
    print(f"{'='*70}")

    successful = [r for r in all_results if "best_loss" in r]
    if successful:
        successful.sort(key=lambda r: r["best_loss"])
        print(f"\nTop 5 configurations (by best loss):")
        for i, r in enumerate(successful[:5]):
            print(f"  {i+1}. Loss={r['best_loss']:.4f} | {r['run_name']}")
            for key in keys:
                print(f"       {key}: {r['config'][key]}")

    # Save full summary
    summary_path = Path(output_dir) / "grid_search_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "grid": grid,
            "total_runs": len(combinations),
            "successful_runs": len(successful),
            "results": all_results,
            "best_run": successful[0] if successful else None,
            "timestamp": datetime.now().isoformat(),
        }, f, indent=2, default=str)

    print(f"\nSummary saved to: {summary_path}")
    return all_results


# ============================================================================
# CLI
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="SimCLR Training for Person Re-ID in the Operating Room"
    )
    parser.add_argument("--dataset_dir", type=str,
                        default="F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned",
                        help="Path to burst dataset directory (from build_dataset.py)")
    parser.add_argument("--output_dir", type=str, default="./simclr_output",
                        help="Output directory for models and results")
    parser.add_argument("--pretrained_weights", type=str, default=None,
                        help="Path to MSMT17 pretrained OsNet weights (.pt)")

    # Single run or grid search
    parser.add_argument("--grid_search", action="store_true",
                        help="Run hyperparameter grid search")

    # Override defaults for single run
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch_size", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--projection_dim", type=int, default=None)
    parser.add_argument("--color_jitter", type=float, default=None)

    return parser.parse_args()


def main():
    args = parse_args()

    # Build config
    config = DEFAULT_CONFIG.copy()
    if args.pretrained_weights:
        config["pretrained_weights"] = args.pretrained_weights
    if args.epochs:
        config["epochs"] = args.epochs
    if args.batch_size:
        config["batch_size"] = args.batch_size
    if args.lr:
        config["lr"] = args.lr
    if args.temperature:
        config["temperature"] = args.temperature
    if args.projection_dim:
        config["projection_dim"] = args.projection_dim
    if args.color_jitter:
        config["color_jitter_strength"] = args.color_jitter

    # Always run grid search
    run_grid_search(config, args.dataset_dir, args.output_dir)


if __name__ == "__main__":
    main()
