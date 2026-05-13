"""
validate_reid_multi_identity.py - Multi-person Re-ID validation for SimCLR OSNet.

The script compares a baseline OSNet backbone against a SimCLR fine-tuned OSNet
backbone on a manually labeled validation_people directory:

validation_people/
  person_01/
  person_02/
  person_03/
  ...

Every visible subdirectory except "skipped" is treated as one identity. The
script extracts L2-normalized embeddings, computes cosine similarity metrics,
nearest-neighbor retrieval accuracy, per-identity summaries, and confusion
matrices. Results are saved into a timestamped child directory so old validation
runs are not overwritten.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, UnidentifiedImageError
from torchvision import transforms

try:
    import torchreid

    TORCHREID_AVAILABLE = True
except ImportError:
    TORCHREID_AVAILABLE = False

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


BACKBONE_NAME = "osnet_ain_x1_0"
IMAGE_SIZE = (256, 128)  # H x W, same Re-ID crop size as train_simclr.py
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
IGNORED_DIRS = {"skipped", "__pycache__"}

EVAL_TRANSFORM = transforms.Compose(
    [
        transforms.Resize(IMAGE_SIZE),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ]
)


@dataclass(frozen=True)
class ImageRecord:
    path: Path
    identity: str


def resolve_device(device_arg: str | None) -> torch.device:
    if device_arg:
        if device_arg == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("--device cuda was requested, but CUDA is not available.")
        return torch.device(device_arg)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def make_run_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = output_dir / f"run_{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = output_dir / f"run_{stamp}_{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def discover_validation_images(validation_dir: Path) -> tuple[list[str], list[ImageRecord]]:
    if not validation_dir.exists() or not validation_dir.is_dir():
        raise FileNotFoundError(f"--validation_dir does not exist or is not a directory: {validation_dir}")

    identities: list[str] = []
    records: list[ImageRecord] = []
    for identity_dir in sorted(validation_dir.iterdir(), key=lambda p: p.name.lower()):
        if not identity_dir.is_dir():
            continue
        if identity_dir.name.startswith(".") or identity_dir.name in IGNORED_DIRS:
            continue
        image_paths = [
            path
            for path in sorted(identity_dir.iterdir(), key=lambda p: p.name.lower())
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        if not image_paths:
            print(f"[Warning] Ignoring identity folder with no images: {identity_dir}")
            continue
        identities.append(identity_dir.name)
        records.extend(ImageRecord(path=path, identity=identity_dir.name) for path in image_paths)

    if len(identities) < 2:
        raise ValueError("Validation requires at least two identity folders with readable image files.")
    if len(records) < 2:
        raise ValueError("Validation requires at least two images total.")
    return identities, records


def filter_readable_images(records: list[ImageRecord]) -> list[ImageRecord]:
    readable: list[ImageRecord] = []
    for record in records:
        try:
            with Image.open(record.path) as image:
                image.convert("RGB")
            readable.append(record)
        except (OSError, UnidentifiedImageError) as exc:
            print(f"[Warning] Skipping unreadable image {record.path}: {exc}")
    if len(readable) < 2:
        raise ValueError("Fewer than two readable validation images remain after filtering.")
    readable_identities = sorted({record.identity for record in readable})
    if len(readable_identities) < 2:
        raise ValueError("Fewer than two identities remain after filtering unreadable images.")
    return readable


def load_state_dict_from_path(weights_path: Path) -> dict:
    if not weights_path.exists() or not weights_path.is_file():
        raise FileNotFoundError(f"Weights file not found: {weights_path}")

    try:
        state = torch.load(weights_path, map_location="cpu", weights_only=True)
    except TypeError:
        state = torch.load(weights_path, map_location="cpu")
    except Exception:
        # Local experiment checkpoints may contain optimizer/scaler objects that
        # PyTorch refuses in weights_only mode. This path is user-provided local
        # data, so fall back to full loading with a clear, narrow purpose.
        state = torch.load(weights_path, map_location="cpu", weights_only=False)

    if isinstance(state, dict) and "model_state_dict" in state:
        state = state["model_state_dict"]
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    if not isinstance(state, dict):
        raise RuntimeError(f"Unsupported checkpoint format: {weights_path}")
    return state


def clean_backbone_state_dict(state: dict) -> dict:
    cleaned = {}
    for key, value in state.items():
        if key.startswith("module."):
            key = key[len("module.") :]
        if key.startswith("projection_head"):
            continue
        if key.startswith("classifier"):
            continue
        if key.startswith("backbone."):
            key = key[len("backbone.") :]
        if key.startswith("module."):
            key = key[len("module.") :]
        if key.startswith("classifier"):
            continue
        cleaned[key] = value
    return cleaned


def load_osnet_backbone(weights_path: Path, device: torch.device):
    if not TORCHREID_AVAILABLE:
        raise RuntimeError("torchreid is required. Install it with pip install torchreid.")

    model = torchreid.models.build_model(name=BACKBONE_NAME, num_classes=1, pretrained=False)
    state = clean_backbone_state_dict(load_state_dict_from_path(weights_path))
    incompatible = model.load_state_dict(state, strict=False)

    missing = [key for key in incompatible.missing_keys if not key.startswith("classifier")]
    unexpected = [key for key in incompatible.unexpected_keys if not key.startswith("classifier")]
    if missing:
        print(f"[Warning] Missing non-classifier keys while loading {weights_path}: {missing[:8]}")
    if unexpected:
        print(f"[Warning] Unexpected keys while loading {weights_path}: {unexpected[:8]}")

    model.to(device)
    model.eval()
    print(f"[Model] Loaded {weights_path}")
    return model


def backbone_forward(model, x: torch.Tensor) -> torch.Tensor:
    """Match SimCLRModel._backbone_forward from train_simclr.py."""
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


def load_image_tensor(path: Path) -> torch.Tensor:
    with Image.open(path) as image:
        image = image.convert("RGB")
        return EVAL_TRANSFORM(image)


def extract_embeddings(model, records: list[ImageRecord], device: torch.device, batch_size: int) -> torch.Tensor:
    embeddings: list[torch.Tensor] = []
    with torch.no_grad():
        for start in range(0, len(records), batch_size):
            batch_records = records[start : start + batch_size]
            tensors = torch.stack([load_image_tensor(record.path) for record in batch_records], dim=0).to(device)
            feats = backbone_forward(model, tensors)
            feats = F.normalize(feats, dim=1)
            embeddings.append(feats.cpu())
    return torch.cat(embeddings, dim=0)


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "std": None, "min": None, "max": None}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "std": float(arr.std(ddof=0)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def topk_indices(similarity_matrix: np.ndarray, row_idx: int, k: int) -> list[int]:
    scores = similarity_matrix[row_idx].copy()
    scores[row_idx] = -np.inf
    k = min(k, len(scores) - 1)
    if k <= 0:
        return []
    # Stable deterministic order for ties: lexsort by negative score, then index.
    order = np.lexsort((np.arange(len(scores)), -scores))
    return [int(idx) for idx in order[:k] if math.isfinite(scores[idx])]


def compute_model_metrics(
    model_name: str,
    similarity_matrix: np.ndarray,
    records: list[ImageRecord],
    identities: list[str],
) -> dict:
    labels = [record.identity for record in records]
    positive: list[float] = []
    negative: list[float] = []
    per_identity_positive: dict[str, list[float]] = defaultdict(list)

    for i, j in combinations(range(len(records)), 2):
        value = float(similarity_matrix[i, j])
        if labels[i] == labels[j]:
            positive.append(value)
            per_identity_positive[labels[i]].append(value)
        else:
            negative.append(value)

    top1_correct = 0
    top5_correct = 0
    per_identity_queries: Counter = Counter()
    per_identity_top1_correct: Counter = Counter()
    per_identity_top5_correct: Counter = Counter()
    confusion: dict[str, Counter] = {identity: Counter() for identity in identities}

    for idx, label in enumerate(labels):
        neighbors = topk_indices(similarity_matrix, idx, 5)
        if not neighbors:
            continue
        per_identity_queries[label] += 1
        pred_label = labels[neighbors[0]]
        confusion[label][pred_label] += 1
        if pred_label == label:
            top1_correct += 1
            per_identity_top1_correct[label] += 1
        if any(labels[neighbor] == label for neighbor in neighbors):
            top5_correct += 1
            per_identity_top5_correct[label] += 1

    n_queries = sum(per_identity_queries.values())
    positive_stats = stats(positive)
    negative_stats = stats(negative)
    mean_pos = positive_stats["mean"]
    mean_neg = negative_stats["mean"]
    gap = None if mean_pos is None or mean_neg is None else float(mean_pos - mean_neg)

    per_identity = {}
    image_counts = Counter(labels)
    for identity in identities:
        query_count = per_identity_queries[identity]
        wrong_counts = Counter(confusion[identity])
        wrong_counts.pop(identity, None)
        per_identity[identity] = {
            "n_images": int(image_counts[identity]),
            "mean_positive_similarity": stats(per_identity_positive[identity])["mean"],
            "top1_accuracy": None if query_count == 0 else float(per_identity_top1_correct[identity] / query_count),
            "top5_accuracy": None if query_count == 0 else float(per_identity_top5_correct[identity] / query_count),
            "most_confused_with": [
                {"identity": pred, "count": int(count)}
                for pred, count in wrong_counts.most_common()
            ],
        }

    return {
        "model_name": model_name,
        "positive_similarity": positive_stats,
        "negative_similarity": negative_stats,
        "separability_gap": gap,
        "top1_retrieval_accuracy": None if n_queries == 0 else float(top1_correct / n_queries),
        "top5_retrieval_accuracy": None if n_queries == 0 else float(top5_correct / n_queries),
        "n_queries": int(n_queries),
        "per_identity": per_identity,
        "confusion": {
            identity: {pred: int(count) for pred, count in confusion[identity].items()}
            for identity in identities
        },
        "positive_values": positive,
        "negative_values": negative,
    }


def metric_delta(finetuned_value, baseline_value):
    if finetuned_value is None or baseline_value is None:
        return None
    return float(finetuned_value - baseline_value)


def compare_metrics(baseline: dict, finetuned: dict) -> dict:
    deltas = {
        "mean_positive_similarity": metric_delta(
            finetuned["positive_similarity"]["mean"], baseline["positive_similarity"]["mean"]
        ),
        "mean_negative_similarity": metric_delta(
            finetuned["negative_similarity"]["mean"], baseline["negative_similarity"]["mean"]
        ),
        "separability_gap": metric_delta(finetuned["separability_gap"], baseline["separability_gap"]),
        "top1_retrieval_accuracy": metric_delta(
            finetuned["top1_retrieval_accuracy"], baseline["top1_retrieval_accuracy"]
        ),
        "top5_retrieval_accuracy": metric_delta(
            finetuned["top5_retrieval_accuracy"], baseline["top5_retrieval_accuracy"]
        ),
    }

    gap_up = (deltas["separability_gap"] or 0.0) > 0
    top1_up = (deltas["top1_retrieval_accuracy"] or 0.0) >= 0
    neg_delta = deltas["mean_negative_similarity"]
    pos_delta = deltas["mean_positive_similarity"]
    collapse_warning = (
        neg_delta is not None
        and pos_delta is not None
        and neg_delta > 0.03
        and neg_delta >= pos_delta
    )

    if gap_up and top1_up and not collapse_warning:
        conclusion = "Fine-tuning improved identity separation on this validation set."
    elif collapse_warning:
        conclusion = (
            "Fine-tuning raised negative similarity substantially; embeddings may be less discriminative "
            "or partially collapsed despite any positive-similarity gains."
        )
    else:
        conclusion = "Fine-tuning did not clearly improve this validation set."

    return {
        "deltas": deltas,
        "fine_tuning_improved": bool(gap_up and top1_up and not collapse_warning),
        "collapse_warning": bool(collapse_warning),
        "conclusion": conclusion,
    }


def write_pairwise_csv(
    path: Path,
    records: list[ImageRecord],
    baseline_sim: np.ndarray,
    finetuned_sim: np.ndarray,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "image_1",
                "image_2",
                "identity_1",
                "identity_2",
                "pair_type",
                "baseline_similarity",
                "finetuned_similarity",
            ],
        )
        writer.writeheader()
        for i, j in combinations(range(len(records)), 2):
            pair_type = "positive" if records[i].identity == records[j].identity else "negative"
            writer.writerow(
                {
                    "image_1": str(records[i].path),
                    "image_2": str(records[j].path),
                    "identity_1": records[i].identity,
                    "identity_2": records[j].identity,
                    "pair_type": pair_type,
                    "baseline_similarity": f"{float(baseline_sim[i, j]):.8f}",
                    "finetuned_similarity": f"{float(finetuned_sim[i, j]):.8f}",
                }
            )


def write_per_identity_csv(path: Path, identities: list[str], baseline: dict, finetuned: dict) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "identity",
                "n_images",
                "baseline_mean_positive_similarity",
                "finetuned_mean_positive_similarity",
                "baseline_top1_accuracy",
                "finetuned_top1_accuracy",
                "baseline_top5_accuracy",
                "finetuned_top5_accuracy",
                "baseline_most_confused_with",
                "finetuned_most_confused_with",
            ],
        )
        writer.writeheader()
        for identity in identities:
            base_row = baseline["per_identity"][identity]
            fine_row = finetuned["per_identity"][identity]
            writer.writerow(
                {
                    "identity": identity,
                    "n_images": base_row["n_images"],
                    "baseline_mean_positive_similarity": base_row["mean_positive_similarity"],
                    "finetuned_mean_positive_similarity": fine_row["mean_positive_similarity"],
                    "baseline_top1_accuracy": base_row["top1_accuracy"],
                    "finetuned_top1_accuracy": fine_row["top1_accuracy"],
                    "baseline_top5_accuracy": base_row["top5_accuracy"],
                    "finetuned_top5_accuracy": fine_row["top5_accuracy"],
                    "baseline_most_confused_with": json.dumps(base_row["most_confused_with"]),
                    "finetuned_most_confused_with": json.dumps(fine_row["most_confused_with"]),
                }
            )


def write_confusion_csv(path: Path, identities: list[str], confusion: dict[str, dict[str, int]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["true_identity", *identities])
        for true_identity in identities:
            row = [true_identity]
            for pred_identity in identities:
                row.append(confusion.get(true_identity, {}).get(pred_identity, 0))
            writer.writerow(row)


def json_safe_metrics(metrics: dict) -> dict:
    return {
        key: value
        for key, value in metrics.items()
        if key not in {"positive_values", "negative_values"}
    }


def plot_results(run_dir: Path, baseline: dict, finetuned: dict) -> None:
    if not MATPLOTLIB_AVAILABLE:
        print("[Info] matplotlib is not available; skipping plots.")
        return

    for metrics, name in [(baseline, "baseline"), (finetuned, "finetuned")]:
        plt.figure(figsize=(8, 5))
        plt.hist(metrics["positive_values"], bins=40, alpha=0.65, label="positive", color="#2ca02c")
        plt.hist(metrics["negative_values"], bins=40, alpha=0.65, label="negative", color="#d62728")
        plt.xlabel("Cosine similarity")
        plt.ylabel("Pair count")
        plt.title(f"{metrics['model_name']} positive vs negative similarities")
        plt.legend()
        plt.tight_layout()
        out = run_dir / f"similarity_histogram_{name}.png"
        plt.savefig(out, dpi=150)
        plt.close()

    plt.figure(figsize=(7, 5))
    names = ["baseline", "fine-tuned"]
    gaps = [baseline["separability_gap"], finetuned["separability_gap"]]
    top1 = [baseline["top1_retrieval_accuracy"], finetuned["top1_retrieval_accuracy"]]
    x = np.arange(len(names))
    width = 0.35
    plt.bar(x - width / 2, gaps, width, label="separability gap")
    plt.bar(x + width / 2, top1, width, label="top-1 accuracy")
    plt.xticks(x, names)
    plt.ylabel("Score")
    plt.title("Baseline vs fine-tuned Re-ID validation")
    plt.legend()
    plt.tight_layout()
    out = run_dir / "comparison_bars.png"
    plt.savefig(out, dpi=150)
    plt.close()
    print(f"[Plots] Saved plots in {run_dir}")


def print_comparison(baseline: dict, finetuned: dict, comparison: dict) -> None:
    print("\n" + "=" * 78)
    print("Baseline vs Fine-Tuned Multi-Identity Re-ID Validation")
    print("=" * 78)
    print(f"{'Metric':<34} {'Baseline':>14} {'Fine-tuned':>14} {'Delta':>14}")
    print("-" * 78)

    rows = [
        (
            "mean positive similarity",
            baseline["positive_similarity"]["mean"],
            finetuned["positive_similarity"]["mean"],
            comparison["deltas"]["mean_positive_similarity"],
        ),
        (
            "mean negative similarity",
            baseline["negative_similarity"]["mean"],
            finetuned["negative_similarity"]["mean"],
            comparison["deltas"]["mean_negative_similarity"],
        ),
        (
            "separability gap",
            baseline["separability_gap"],
            finetuned["separability_gap"],
            comparison["deltas"]["separability_gap"],
        ),
        (
            "top-1 retrieval accuracy",
            baseline["top1_retrieval_accuracy"],
            finetuned["top1_retrieval_accuracy"],
            comparison["deltas"]["top1_retrieval_accuracy"],
        ),
        (
            "top-5 retrieval accuracy",
            baseline["top5_retrieval_accuracy"],
            finetuned["top5_retrieval_accuracy"],
            comparison["deltas"]["top5_retrieval_accuracy"],
        ),
    ]

    for label, base, fine, delta in rows:
        print(f"{label:<34} {format_metric(base):>14} {format_metric(fine):>14} {format_metric(delta, signed=True):>14}")

    print("-" * 78)
    print(comparison["conclusion"])
    if comparison["collapse_warning"]:
        print("Interpretation warning: positive similarity and negative similarity both rose; check for reduced discrimination.")


def format_metric(value, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    prefix = "+" if signed and value >= 0 else ""
    return f"{prefix}{value:.4f}"


def run_validation(args: argparse.Namespace) -> Path:
    validation_dir = Path(args.validation_dir)
    baseline_weights = Path(args.baseline_weights)
    finetuned_weights = Path(args.finetuned_weights)
    output_dir = Path(args.output_dir)
    device = resolve_device(args.device)

    identities, records = discover_validation_images(validation_dir)
    records = filter_readable_images(records)
    identities = sorted({record.identity for record in records})

    run_dir = make_run_dir(output_dir)
    print("=" * 78)
    print("Multi-Identity Re-ID Validation")
    print("=" * 78)
    print(f"Validation dir:    {validation_dir.resolve()}")
    print(f"Identities:        {len(identities)}")
    print(f"Images:            {len(records)}")
    print(f"Device:            {device}")
    print(f"Output run dir:    {run_dir.resolve()}")

    print("\n[1/2] Baseline embeddings")
    baseline_model = load_osnet_backbone(baseline_weights, device)
    baseline_embeddings = extract_embeddings(baseline_model, records, device, args.batch_size)
    del baseline_model
    if device.type == "cuda":
        torch.cuda.empty_cache()

    print("\n[2/2] Fine-tuned embeddings")
    finetuned_model = load_osnet_backbone(finetuned_weights, device)
    finetuned_embeddings = extract_embeddings(finetuned_model, records, device, args.batch_size)
    del finetuned_model
    if device.type == "cuda":
        torch.cuda.empty_cache()

    baseline_sim = (baseline_embeddings @ baseline_embeddings.T).numpy()
    finetuned_sim = (finetuned_embeddings @ finetuned_embeddings.T).numpy()

    baseline_metrics = compute_model_metrics("baseline", baseline_sim, records, identities)
    finetuned_metrics = compute_model_metrics("fine_tuned", finetuned_sim, records, identities)
    comparison = compare_metrics(baseline_metrics, finetuned_metrics)

    write_pairwise_csv(run_dir / "pairwise_results.csv", records, baseline_sim, finetuned_sim)
    write_per_identity_csv(run_dir / "per_identity_summary.csv", identities, baseline_metrics, finetuned_metrics)
    write_confusion_csv(run_dir / "confusion_matrix_baseline.csv", identities, baseline_metrics["confusion"])
    write_confusion_csv(run_dir / "confusion_matrix_finetuned.csv", identities, finetuned_metrics["confusion"])

    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "validation_dir": str(validation_dir.resolve()),
        "baseline_weights": str(baseline_weights.resolve()),
        "finetuned_weights": str(finetuned_weights.resolve()),
        "output_dir": str(run_dir.resolve()),
        "number_of_identities": len(identities),
        "number_of_images": len(records),
        "identities": identities,
        "images": [{"path": str(record.path), "identity": record.identity} for record in records],
        "metrics": {
            "baseline": json_safe_metrics(baseline_metrics),
            "fine_tuned": json_safe_metrics(finetuned_metrics),
        },
        "comparison": comparison,
        "interpretation": {
            "good_fine_tuning": (
                "Higher positive similarity, lower or stable negative similarity, larger separability gap, "
                "and higher top-1/top-5 retrieval accuracy."
            ),
            "collapse_warning": (
                "If both positive and negative similarities increase substantially, embeddings may have become "
                "less discriminative even when positives look better."
            ),
        },
    }
    with (run_dir / "validation_summary.json").open("w", encoding="utf-8") as file:
        json.dump(summary, file, indent=2)

    plot_results(run_dir, baseline_metrics, finetuned_metrics)
    print_comparison(baseline_metrics, finetuned_metrics, comparison)
    print(f"\n[Done] Results saved to: {run_dir.resolve()}")
    return run_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare baseline vs SimCLR fine-tuned OSNet embeddings on a multi-identity validation set.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--validation_dir", required=True, help="Directory containing person_* identity folders.")
    parser.add_argument(
        "--baseline_weights",
        default="F:/Projects/ScalpelLab/CV/osnet_ain_x1_0_msmt17.pt",
        help="Baseline OSNet weights.",
    )
    parser.add_argument(
        "--finetuned_weights",
        default="CV/SimCLR_reid/simclr_output/trial_0074/best_backbone.pt",
        help="Fine-tuned SimCLR backbone weights.",
    )
    parser.add_argument(
        "--output_dir",
        default="CV/SimCLR_reid/validation_results",
        help="Parent output directory. A timestamped run folder is created inside it.",
    )
    parser.add_argument("--device", default=None, help="Optional device override, e.g. cuda or cpu.")
    parser.add_argument("--batch_size", type=int, default=64, help="Embedding extraction batch size.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_validation(args)


if __name__ == "__main__":
    main()
