"""
validate_model.py - Comparative Validation for SimCLR Re-ID Model
================================================================================

PURPOSE:
    Validate that the fine-tuned model has acquired true discriminative capabilities
    through a rigorous A/B comparison against the baseline (pre-trained) model.

METHODOLOGY (Section 4: Metric Separability):
    1. Baseline Model: OsNet with MSMT17 weights (zero OR exposure)
    2. Fine-Tuned Model: OsNet after SimCLR training on OR data
    3. Test Data: Hold-out images of 2 distinct staff members from an
       excluded surgical case (wearing identical scrubs)

METRICS:
    - Intra-identity similarity (same person, different images)
    - Inter-identity similarity (different persons)
    - Separability gap = intra_sim - inter_sim

EXPECTED RESULTS:
    Baseline:   High inter-similarity (>0.9) → Color Bias (failure)
    Fine-Tuned: Low inter-similarity  (<0.6) → Morphological Separation (success)

USAGE:
    python validate_model.py \
        --baseline_weights <path_to_msmt17.pt> \
        --finetuned_weights <path_to_finetuned_backbone.pt> \
        --person_a_dir <path_to_person_a_images> \
        --person_b_dir <path_to_person_b_images> \
        [--output_dir ./validation_results]

================================================================================
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from itertools import combinations

import torch
import torch.nn.functional as F
import numpy as np
from PIL import Image
from torchvision import transforms

try:
    import torchreid
    TORCHREID_AVAILABLE = True
except ImportError:
    TORCHREID_AVAILABLE = False
    print("[WARNING] torchreid not found.")

# Optional: matplotlib for visualization
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("[INFO] matplotlib not available, skipping plots.")


# ============================================================================
# CONFIGURATION
# ============================================================================

IMAGE_SIZE = (256, 128)  # H x W
BACKBONE_NAME = "osnet_ain_x1_0"

# Standard normalization (ImageNet)
EVAL_TRANSFORM = transforms.Compose([
    transforms.Resize(IMAGE_SIZE),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std=[0.229, 0.224, 0.225]),
])


# ============================================================================
# MODEL LOADING
# ============================================================================

def load_osnet_backbone(weights_path: str = None, device: str = "cpu"):
    """
    Load OsNet backbone for feature extraction.

    Args:
        weights_path: Path to .pt weights. None = ImageNet default.
        device: 'cuda' or 'cpu'
    Returns:
        model in eval mode
    """
    if not TORCHREID_AVAILABLE:
        raise RuntimeError("torchreid required")

    model = torchreid.models.build_model(
        name=BACKBONE_NAME,
        num_classes=1,
        pretrained=True if weights_path is None else False,
    )

    if weights_path and Path(weights_path).exists():
        state = torch.load(weights_path, map_location="cpu")
        if "state_dict" in state:
            state = state["state_dict"]

        # Filter out projection head keys and classifier keys
        backbone_state = {
            k: v for k, v in state.items()
            if not k.startswith("projection_head") and not k.startswith("classifier")
        }
        # Also handle 'backbone.' prefix from SimCLR model
        cleaned_state = {}
        for k, v in backbone_state.items():
            if k.startswith("backbone."):
                cleaned_state[k[len("backbone."):]] = v
            else:
                cleaned_state[k] = v

        model.load_state_dict(cleaned_state, strict=False)
        print(f"[Model] Loaded weights from: {weights_path}")
    else:
        print(f"[Model] Using default pretrained weights")

    model = model.to(device)
    model.eval()
    return model


def extract_features(model, image_path: str, device: str = "cpu") -> torch.Tensor:
    """
    Extract backbone embedding vector for a single image.

    Returns:
        (1, D) normalized feature tensor
    """
    img = Image.open(image_path).convert("RGB")
    tensor = EVAL_TRANSFORM(img).unsqueeze(0).to(device)

    with torch.no_grad():
        # Forward through backbone layers (same as SimCLRModel._backbone_forward)
        x = model.conv1(tensor)
        x = model.maxpool(x)
        x = model.layer1(x)
        x = model.layer2(x)
        x = model.layer3(x)
        x = model.layer4(x)
        x = model.global_avgpool(x)
        feat = x.view(x.size(0), -1)

    # L2 normalize
    feat = F.normalize(feat, dim=1)
    return feat


def extract_features_batch(model, image_dir: str, device: str = "cpu"):
    """
    Extract features for all images in a directory.

    Returns:
        features: (N, D) tensor
        paths: list of image paths
    """
    image_dir = Path(image_dir)
    paths = sorted([
        p for p in image_dir.glob("*")
        if p.suffix.lower() in [".jpg", ".jpeg", ".png", ".bmp"]
    ])

    if len(paths) == 0:
        raise ValueError(f"No images found in {image_dir}")

    features = []
    for p in paths:
        feat = extract_features(model, str(p), device)
        features.append(feat)

    features = torch.cat(features, dim=0)  # (N, D)
    print(f"  Extracted {len(paths)} embeddings from {image_dir.name}")
    return features, paths


# ============================================================================
# SIMILARITY ANALYSIS
# ============================================================================

def cosine_similarity_matrix(feat_a: torch.Tensor, feat_b: torch.Tensor) -> torch.Tensor:
    """
    Compute pairwise cosine similarity between two sets of features.

    Args:
        feat_a: (Na, D) features of person A
        feat_b: (Nb, D) features of person B
    Returns:
        (Na, Nb) similarity matrix
    """
    return F.cosine_similarity(
        feat_a.unsqueeze(1),  # (Na, 1, D)
        feat_b.unsqueeze(0),  # (1, Nb, D)
        dim=2
    )


def compute_intra_similarity(features: torch.Tensor) -> dict:
    """
    Compute within-identity similarity (same person, different images).

    Returns statistics: mean, std, min, max
    """
    N = features.size(0)
    if N < 2:
        return {"mean": 1.0, "std": 0.0, "min": 1.0, "max": 1.0, "n_pairs": 0}

    sim_matrix = F.cosine_similarity(
        features.unsqueeze(1), features.unsqueeze(0), dim=2
    )

    # Extract upper triangle (exclude self-similarity diagonal)
    mask = torch.triu(torch.ones(N, N, dtype=torch.bool), diagonal=1)
    sims = sim_matrix[mask]

    return {
        "mean": sims.mean().item(),
        "std": sims.std().item(),
        "min": sims.min().item(),
        "max": sims.max().item(),
        "n_pairs": sims.numel(),
    }


def compute_inter_similarity(feat_a: torch.Tensor, feat_b: torch.Tensor) -> dict:
    """
    Compute between-identity similarity (different persons).
    """
    sim_matrix = cosine_similarity_matrix(feat_a, feat_b)
    sims = sim_matrix.flatten()

    return {
        "mean": sims.mean().item(),
        "std": sims.std().item(),
        "min": sims.min().item(),
        "max": sims.max().item(),
        "n_pairs": sims.numel(),
    }


# ============================================================================
# FULL VALIDATION PIPELINE
# ============================================================================

def validate_model(model, model_name: str,
                   person_a_dir: str, person_b_dir: str,
                   device: str = "cpu") -> dict:
    """
    Run full validation for a single model.

    Returns dict with intra/inter similarity stats and separability metrics.
    """
    print(f"\n--- Validating: {model_name} ---")

    # Extract features
    feat_a, paths_a = extract_features_batch(model, person_a_dir, device)
    feat_b, paths_b = extract_features_batch(model, person_b_dir, device)

    # Intra-identity (how similar is person A to themselves?)
    intra_a = compute_intra_similarity(feat_a)
    intra_b = compute_intra_similarity(feat_b)

    # Inter-identity (how similar is person A to person B?)
    inter = compute_inter_similarity(feat_a, feat_b)

    # Separability metrics
    avg_intra = (intra_a["mean"] + intra_b["mean"]) / 2
    separability_gap = avg_intra - inter["mean"]

    results = {
        "model_name": model_name,
        "person_a": {
            "n_images": len(paths_a),
            "intra_similarity": intra_a,
        },
        "person_b": {
            "n_images": len(paths_b),
            "intra_similarity": intra_b,
        },
        "inter_similarity": inter,
        "summary": {
            "avg_intra_similarity": avg_intra,
            "inter_similarity": inter["mean"],
            "separability_gap": separability_gap,
            "interpretation": (
                "SUCCESS: Morphological Separation" if inter["mean"] < 0.6
                else "WARNING: Possible Color Bias" if inter["mean"] < 0.8
                else "FAILURE: Color Bias (no discrimination)"
            ),
        },
    }

    # Print results
    print(f"  Intra-A similarity: {intra_a['mean']:.4f} ± {intra_a['std']:.4f}")
    print(f"  Intra-B similarity: {intra_b['mean']:.4f} ± {intra_b['std']:.4f}")
    print(f"  Inter similarity:   {inter['mean']:.4f} ± {inter['std']:.4f}")
    print(f"  Separability gap:   {separability_gap:.4f}")
    print(f"  => {results['summary']['interpretation']}")

    return results


def run_comparative_validation(baseline_weights: str,
                               finetuned_weights: str,
                               person_a_dir: str,
                               person_b_dir: str,
                               output_dir: str = "./validation_results"):
    """
    Run the full A/B comparative validation experiment.

    Compares baseline (MSMT17) vs fine-tuned (SimCLR) model on hold-out data.
    """
    device = "cuda" if torch.cuda.is_available() else "cpu"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("COMPARATIVE VALIDATION EXPERIMENT")
    print("=" * 70)
    print(f"Baseline weights:   {baseline_weights}")
    print(f"Fine-tuned weights: {finetuned_weights}")
    print(f"Person A images:    {person_a_dir}")
    print(f"Person B images:    {person_b_dir}")
    print(f"Device:             {device}")

    # --- Model A: Baseline (Pre-trained) ---
    print("\n[1/2] Loading BASELINE model...")
    baseline_model = load_osnet_backbone(baseline_weights, device)
    baseline_results = validate_model(
        baseline_model, "Baseline (MSMT17)",
        person_a_dir, person_b_dir, device
    )
    del baseline_model
    torch.cuda.empty_cache()

    # --- Model B: Fine-Tuned ---
    print("\n[2/2] Loading FINE-TUNED model...")
    finetuned_model = load_osnet_backbone(finetuned_weights, device)
    finetuned_results = validate_model(
        finetuned_model, "Fine-Tuned (SimCLR)",
        person_a_dir, person_b_dir, device
    )
    del finetuned_model
    torch.cuda.empty_cache()

    # --- Comparative Analysis ---
    delta_inter = (baseline_results["inter_similarity"]["mean"]
                   - finetuned_results["inter_similarity"]["mean"])
    delta_gap = (finetuned_results["summary"]["separability_gap"]
                 - baseline_results["summary"]["separability_gap"])

    comparison = {
        "experiment": "Comparative Validation - Metric Separability",
        "timestamp": datetime.now().isoformat(),
        "baseline": baseline_results,
        "finetuned": finetuned_results,
        "comparative": {
            "delta_inter_similarity": delta_inter,
            "delta_separability_gap": delta_gap,
            "improvement_pct": (delta_inter / max(baseline_results["inter_similarity"]["mean"], 1e-6)) * 100,
            "conclusion": (
                "STRONG DOMAIN ADAPTATION: The fine-tuned model shows significant "
                "morphological separation capability vs. the baseline."
                if delta_inter > 0.2
                else "MODERATE ADAPTATION: Some improvement in discrimination."
                if delta_inter > 0.05
                else "INSUFFICIENT ADAPTATION: Minimal improvement over baseline."
            ),
        },
    }

    # Print comparative summary
    print("\n" + "=" * 70)
    print("COMPARATIVE RESULTS")
    print("=" * 70)
    print(f"\n{'Metric':<30} {'Baseline':>12} {'Fine-Tuned':>12} {'Delta':>12}")
    print("-" * 66)
    print(f"{'Inter-ID Similarity':<30} "
          f"{baseline_results['inter_similarity']['mean']:>12.4f} "
          f"{finetuned_results['inter_similarity']['mean']:>12.4f} "
          f"{-delta_inter:>+12.4f}")
    print(f"{'Avg Intra-ID Similarity':<30} "
          f"{baseline_results['summary']['avg_intra_similarity']:>12.4f} "
          f"{finetuned_results['summary']['avg_intra_similarity']:>12.4f} "
          f"{'':>12}")
    print(f"{'Separability Gap':<30} "
          f"{baseline_results['summary']['separability_gap']:>12.4f} "
          f"{finetuned_results['summary']['separability_gap']:>12.4f} "
          f"{delta_gap:>+12.4f}")
    print("-" * 66)
    print(f"\n=> {comparison['comparative']['conclusion']}")

    # Save results
    results_path = output_dir / "validation_results.json"
    with open(results_path, "w") as f:
        json.dump(comparison, f, indent=2, default=str)
    print(f"\nResults saved to: {results_path}")

    # --- Visualization ---
    if MATPLOTLIB_AVAILABLE:
        plot_validation_results(baseline_results, finetuned_results, output_dir)

    return comparison


# ============================================================================
# VISUALIZATION
# ============================================================================

def plot_validation_results(baseline: dict, finetuned: dict, output_dir: Path):
    """Generate comparison plots for the validation experiment."""
    output_dir = Path(output_dir)

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle("Comparative Validation: Baseline vs Fine-Tuned",
                 fontsize=14, fontweight="bold")

    # --- Plot 1: Bar chart of inter-identity similarity ---
    ax = axes[0]
    models = ["Baseline\n(MSMT17)", "Fine-Tuned\n(SimCLR)"]
    inter_vals = [
        baseline["inter_similarity"]["mean"],
        finetuned["inter_similarity"]["mean"],
    ]
    inter_stds = [
        baseline["inter_similarity"]["std"],
        finetuned["inter_similarity"]["std"],
    ]
    colors = ["#e74c3c", "#2ecc71"]
    bars = ax.bar(models, inter_vals, yerr=inter_stds, capsize=5,
                  color=colors, alpha=0.8, edgecolor="black")
    ax.axhline(y=0.6, color="orange", linestyle="--", alpha=0.7, label="Success threshold")
    ax.axhline(y=0.9, color="red", linestyle="--", alpha=0.7, label="Failure threshold")
    ax.set_ylabel("Cosine Similarity")
    ax.set_title("Inter-Identity Similarity\n(Lower = Better)")
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=8)

    # --- Plot 2: Separability gap comparison ---
    ax = axes[1]
    gaps = [
        baseline["summary"]["separability_gap"],
        finetuned["summary"]["separability_gap"],
    ]
    bars = ax.bar(models, gaps, color=colors, alpha=0.8, edgecolor="black")
    ax.set_ylabel("Separability Gap")
    ax.set_title("Separability Gap\n(Intra - Inter, Higher = Better)")
    for bar, val in zip(bars, gaps):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01,
                f"{val:.3f}", ha="center", va="bottom", fontsize=10)

    # --- Plot 3: Full breakdown ---
    ax = axes[2]
    x = np.arange(2)
    width = 0.25

    intra_a_vals = [
        baseline["person_a"]["intra_similarity"]["mean"],
        finetuned["person_a"]["intra_similarity"]["mean"],
    ]
    intra_b_vals = [
        baseline["person_b"]["intra_similarity"]["mean"],
        finetuned["person_b"]["intra_similarity"]["mean"],
    ]
    inter_vals_plot = [
        baseline["inter_similarity"]["mean"],
        finetuned["inter_similarity"]["mean"],
    ]

    ax.bar(x - width, intra_a_vals, width, label="Intra-A", color="#3498db", alpha=0.8)
    ax.bar(x, intra_b_vals, width, label="Intra-B", color="#9b59b6", alpha=0.8)
    ax.bar(x + width, inter_vals_plot, width, label="Inter (A↔B)", color="#e67e22", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(["Baseline", "Fine-Tuned"])
    ax.set_ylabel("Cosine Similarity")
    ax.set_title("Full Similarity Breakdown")
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=8)

    plt.tight_layout()
    plot_path = output_dir / "validation_comparison.png"
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[Plot] Saved to: {plot_path}")


# ============================================================================
# CLI
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Comparative Validation for SimCLR Re-ID Model"
    )
    parser.add_argument("--baseline_weights", type=str, required=True,
                        help="Path to baseline (MSMT17) OsNet weights")
    parser.add_argument("--finetuned_weights", type=str, required=True,
                        help="Path to fine-tuned backbone weights (from train_simclr.py)")
    parser.add_argument("--person_a_dir", type=str, required=True,
                        help="Directory with hold-out images of Person A")
    parser.add_argument("--person_b_dir", type=str, required=True,
                        help="Directory with hold-out images of Person B")
    parser.add_argument("--output_dir", type=str, default="./validation_results",
                        help="Output directory for results and plots")
    return parser.parse_args()


def main():
    args = parse_args()

    run_comparative_validation(
        baseline_weights=args.baseline_weights,
        finetuned_weights=args.finetuned_weights,
        person_a_dir=args.person_a_dir,
        person_b_dir=args.person_b_dir,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
