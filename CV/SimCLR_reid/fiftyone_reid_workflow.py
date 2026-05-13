"""
FiftyOne workflow for unsupervised SimCLR Re-ID embedding exploration.

This script intentionally does not modify source images, checkpoints, Optuna
databases, or existing simclr_output directories. It stores metadata and
embeddings in a FiftyOne dataset, and writes validation reports under a new
timestamped results directory.
"""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import json
import math
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, UnidentifiedImageError


DEFAULT_DATASET_DIR = Path("F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned")
DEFAULT_FO_DATASET_NAME = "simclr_reid_embeddings"
DEFAULT_BASELINE_WEIGHTS = Path("F:/Projects/ScalpelLab/CV/osnet_ain_x1_0_msmt17.pt")
DEFAULT_FINETUNED_WEIGHTS = Path("CV/SimCLR_reid/simclr_output/trial_0074/best_backbone.pt")
DEFAULT_RESULTS_DIR = Path("CV/SimCLR_reid/fiftyone_validation_results")
DEFAULT_LABELED_EXPORT_DIR = Path("CV/SimCLR_reid/validation_people")

BACKBONE_NAME = "osnet_ain_x1_0"
IMAGE_SIZE = (256, 128)  # H x W, matching train_simclr.py and validate_reid_multi_identity.py
FILENAME_PATTERN = re.compile(r"^(\d+)_v(\d+)_(\d+)\.jpg$", re.IGNORECASE)
VALID_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
SKIP_IDENTITIES = {"", "skip", "skipped", "uncertain"}


@dataclass
class CropRecord:
    path: Path
    filename: str
    case_no: str
    video_idx: int
    frame_id: int
    burst_id: str = ""
    burst_size: int = 0
    burst_is_singleton: bool = False


@dataclass
class EmbeddingRecord:
    sample_id: str
    filepath: str
    filename: str
    case_no: str
    video_idx: int
    frame_id: int
    burst_id: str
    is_validation_video: bool
    baseline: np.ndarray
    finetuned: np.ndarray


def require_fiftyone():
    try:
        import fiftyone as fo
    except ImportError as exc:
        raise RuntimeError(
            "FiftyOne is required for this command. Install it with:\n"
            "  pip install fiftyone\n"
            "For UMAP visualizations, also install:\n"
            "  pip install umap-learn"
        ) from exc
    return fo


def require_torch_stack():
    try:
        import torch
        import torch.nn.functional as F
        from torchvision import transforms
        import torchreid
    except ImportError as exc:
        raise RuntimeError(
            "Embedding extraction requires torch, torchvision, and torchreid. "
            "Install the project requirements before running compute-embeddings."
        ) from exc
    return torch, F, transforms, torchreid


def iter_progress(iterable: Iterable, total: int | None = None, desc: str | None = None):
    try:
        from tqdm import tqdm
    except ImportError:
        return iterable
    return tqdm(iterable, total=total, desc=desc)


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


def parse_crop_filename(path: Path) -> CropRecord | None:
    match = FILENAME_PATTERN.match(path.name)
    if not match:
        return None
    return CropRecord(
        path=path,
        filename=path.name,
        case_no=match.group(1),
        video_idx=int(match.group(2)),
        frame_id=int(match.group(3)),
    )


def discover_crop_records(dataset_dir: Path, case_no: str | None, video_idx: int | None) -> tuple[list[CropRecord], int]:
    if not dataset_dir.exists() or not dataset_dir.is_dir():
        raise FileNotFoundError(f"--dataset_dir does not exist or is not a directory: {dataset_dir}")

    records: list[CropRecord] = []
    skipped = 0
    for path in sorted(dataset_dir.glob("*.jpg"), key=lambda p: p.name.lower()):
        if path.name.startswith("."):
            continue
        record = parse_crop_filename(path)
        if record is None:
            skipped += 1
            continue
        if case_no is not None and record.case_no != str(case_no):
            continue
        if video_idx is not None and record.video_idx != video_idx:
            continue
        records.append(record)
    return records, skipped


def assign_bursts(records: list[CropRecord], gap_threshold: int) -> None:
    grouped: dict[tuple[str, int], list[CropRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.case_no, record.video_idx)].append(record)

    for (case_no, video_idx), group in grouped.items():
        group.sort(key=lambda item: item.frame_id)
        burst_index = 0
        current: list[CropRecord] = []
        previous_frame: int | None = None

        def flush_burst(items: list[CropRecord], index: int) -> None:
            if not items:
                return
            burst_id = f"{case_no}_v{video_idx:02d}_b{index:04d}"
            burst_size = len(items)
            for item in items:
                item.burst_id = burst_id
                item.burst_size = burst_size
                item.burst_is_singleton = burst_size == 1

        for record in group:
            if previous_frame is not None and record.frame_id - previous_frame > gap_threshold:
                flush_burst(current, burst_index)
                burst_index += 1
                current = []
            current.append(record)
            previous_frame = record.frame_id

        flush_burst(current, burst_index)


def select_records(records: list[CropRecord], max_images: int | None, shuffle: bool, seed: int) -> list[CropRecord]:
    selected = list(records)
    if shuffle:
        rng = random.Random(seed)
        rng.shuffle(selected)
    if max_images is not None and max_images > 0:
        selected = selected[:max_images]
    return selected


def summarize_records(records: list[CropRecord]) -> dict[str, int]:
    return {
        "images": len(records),
        "cases": len({record.case_no for record in records}),
        "videos": len({(record.case_no, record.video_idx) for record in records}),
        "bursts": len({record.burst_id for record in records}),
        "validation_video_images": sum(1 for record in records if record.video_idx % 5 == 0),
        "singleton_burst_images": sum(1 for record in records if record.burst_is_singleton),
    }


def build_dataset(args: argparse.Namespace) -> None:
    fo = require_fiftyone()

    dataset_dir = Path(args.dataset_dir)
    records, skipped = discover_crop_records(dataset_dir, args.case_no, args.video_idx)
    assign_bursts(records, args.burst_gap_threshold)
    selected = select_records(records, args.max_images, args.shuffle, args.seed)

    if fo.dataset_exists(args.fo_dataset_name):
        if args.overwrite_dataset:
            print(f"[FiftyOne] Deleting existing dataset: {args.fo_dataset_name}")
            fo.delete_dataset(args.fo_dataset_name)
            dataset = fo.Dataset(args.fo_dataset_name)
        else:
            dataset = fo.load_dataset(args.fo_dataset_name)
            print(f"[FiftyOne] Reusing existing dataset: {args.fo_dataset_name}")
    else:
        dataset = fo.Dataset(args.fo_dataset_name)
        print(f"[FiftyOne] Created dataset: {args.fo_dataset_name}")
    dataset.persistent = True

    existing_by_filepath = {sample.filepath: sample.id for sample in dataset.iter_samples(progress=False)}
    new_samples = []
    updated = 0

    for record in iter_progress(selected, total=len(selected), desc="Building samples"):
        filepath = str(record.path.resolve())
        is_validation_video = record.video_idx % 5 == 0
        fields = {
            "filename": record.filename,
            "case_no": record.case_no,
            "video_idx": record.video_idx,
            "frame_id": record.frame_id,
            "burst_id": record.burst_id,
            "burst_size": record.burst_size,
            "burst_is_singleton": record.burst_is_singleton,
            "source_dataset_dir": str(dataset_dir.resolve()),
            "is_validation_video": is_validation_video,
            "split_hint": "validation_video" if is_validation_video else "train_video",
        }
        sample_id = existing_by_filepath.get(filepath)
        if sample_id is None:
            sample = fo.Sample(filepath=filepath)
            for key, value in fields.items():
                sample[key] = value
            new_samples.append(sample)
        else:
            sample = dataset[sample_id]
            for key, value in fields.items():
                sample[key] = value
            sample.save()
            updated += 1

    if new_samples:
        dataset.add_samples(new_samples)

    summary = summarize_records(selected)
    print("\nDataset build complete")
    print(f"  FiftyOne dataset: {dataset.name}")
    print(f"  Source images matched filters: {len(records):,}")
    print(f"  Filename parse skips: {skipped:,}")
    print(f"  Loaded/updated images: {summary['images']:,}")
    print(f"  New samples: {len(new_samples):,}")
    print(f"  Updated samples: {updated:,}")
    print(f"  Cases: {summary['cases']:,}")
    print(f"  Case/video groups: {summary['videos']:,}")
    print(f"  Bursts represented: {summary['bursts']:,}")
    print(f"  Validation-video images: {summary['validation_video_images']:,}")
    print(f"  Singleton-burst images: {summary['singleton_burst_images']:,}")
    print("  Example parsed samples:")
    for record in selected[:5]:
        print(
            "   "
            f"{record.filename} -> case={record.case_no}, video={record.video_idx:02d}, "
            f"frame={record.frame_id}, burst={record.burst_id}, size={record.burst_size}"
        )


def resolve_device(device_arg: str):
    torch, _, _, _ = require_torch_stack()
    if device_arg == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device_arg == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("--device cuda was requested, but CUDA is not available")
    return torch.device(device_arg)


def eval_transform():
    _, _, transforms, _ = require_torch_stack()
    return transforms.Compose(
        [
            transforms.Resize(IMAGE_SIZE),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )


def load_state_dict_from_path(weights_path: Path) -> dict[str, Any]:
    torch, _, _, _ = require_torch_stack()
    if not weights_path.exists() or not weights_path.is_file():
        raise FileNotFoundError(f"Weights file not found: {weights_path}")

    try:
        state = torch.load(weights_path, map_location="cpu", weights_only=True)
    except TypeError:
        state = torch.load(weights_path, map_location="cpu")
    except Exception:
        state = torch.load(weights_path, map_location="cpu", weights_only=False)

    if isinstance(state, dict) and "model_state_dict" in state:
        state = state["model_state_dict"]
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    if not isinstance(state, dict):
        raise RuntimeError(f"Unsupported checkpoint format: {weights_path}")
    return state


def clean_backbone_state_dict(state: dict[str, Any]) -> dict[str, Any]:
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


def load_osnet_backbone(weights_path: Path, device):
    torch, _, _, torchreid = require_torch_stack()
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


def backbone_forward(model, tensors):
    model_ref = model
    x = model_ref.conv1(tensors)
    x = model_ref.maxpool(x)
    x = model_ref.conv2(x)
    x = model_ref.pool2(x)
    x = model_ref.conv3(x)
    x = model_ref.pool3(x)
    x = model_ref.conv4(x)
    x = model_ref.conv5(x)
    x = model_ref.global_avgpool(x)
    x = x.view(x.size(0), -1)
    return model_ref.fc(x)


def has_embedding(sample, field_name: str) -> bool:
    value = get_sample_field(sample, field_name)
    return value is not None and len(value) > 0


def get_sample_field(sample, field_name: str, default: Any = None) -> Any:
    try:
        return sample.get_field(field_name)
    except Exception:
        try:
            return sample[field_name]
        except Exception:
            return default


def load_image_tensor(path: Path, transform):
    with Image.open(path) as image:
        return transform(image.convert("RGB"))


def write_embedding_failures(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["filepath", "error"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"[Warning] Wrote embedding failure log: {path}")


def compute_embeddings(args: argparse.Namespace) -> None:
    fo = require_fiftyone()
    torch, F, _, _ = require_torch_stack()

    dataset = fo.load_dataset(args.fo_dataset_name)
    try:
        schema = dataset.get_field_schema()
        for field_name in ["baseline_embedding", "finetuned_embedding"]:
            if field_name not in schema:
                dataset.add_sample_field(field_name, fo.VectorField)
    except Exception as exc:
        print(f"[Warning] Could not predeclare embedding vector fields: {exc}")

    candidates = []
    skipped_existing = 0
    for sample in dataset.iter_samples(progress=False):
        needs_baseline = args.force_recompute or not has_embedding(sample, "baseline_embedding")
        needs_finetuned = args.force_recompute or not has_embedding(sample, "finetuned_embedding")
        if not needs_baseline and not needs_finetuned:
            skipped_existing += 1
            continue
        candidates.append((sample.id, sample.filepath, needs_baseline, needs_finetuned))
        if args.limit is not None and len(candidates) >= args.limit:
            break

    if not candidates:
        print("\nEmbedding computation complete")
        print(f"  Dataset: {dataset.name}")
        print("  Candidate samples: 0")
        print(f"  Skipped with existing embeddings: {skipped_existing:,}")
        print("  Nothing to compute. Use --force_recompute to overwrite existing embeddings.")
        return

    device = resolve_device(args.device)
    transform = eval_transform()

    baseline_model = load_osnet_backbone(Path(args.baseline_weights), device)
    finetuned_model = load_osnet_backbone(Path(args.finetuned_weights), device)

    failures: list[dict[str, str]] = []
    computed_samples = 0
    baseline_vectors = 0
    finetuned_vectors = 0
    embedding_dim: int | None = None

    with torch.no_grad():
        for start in iter_progress(range(0, len(candidates), args.batch_size), desc="Embedding batches"):
            batch = candidates[start : start + args.batch_size]
            valid = []
            tensors = []
            for sample_id, filepath, needs_baseline, needs_finetuned in batch:
                try:
                    tensor = load_image_tensor(Path(filepath), transform)
                except (OSError, UnidentifiedImageError, ValueError) as exc:
                    failures.append({"filepath": filepath, "error": str(exc)})
                    continue
                valid.append((sample_id, filepath, needs_baseline, needs_finetuned))
                tensors.append(tensor)

            if not valid:
                continue

            input_tensor = torch.stack(tensors, dim=0).to(device)
            baseline_feats = F.normalize(backbone_forward(baseline_model, input_tensor), dim=1).cpu().numpy()
            finetuned_feats = F.normalize(backbone_forward(finetuned_model, input_tensor), dim=1).cpu().numpy()
            embedding_dim = int(baseline_feats.shape[1])

            for idx, (sample_id, _, needs_baseline, needs_finetuned) in enumerate(valid):
                sample = dataset[sample_id]
                if needs_baseline:
                    sample["baseline_embedding"] = baseline_feats[idx].astype(float).tolist()
                    baseline_vectors += 1
                if needs_finetuned:
                    sample["finetuned_embedding"] = finetuned_feats[idx].astype(float).tolist()
                    finetuned_vectors += 1
                sample.save()
                computed_samples += 1

    failure_log = Path(args.failure_log) if args.failure_log else (
        DEFAULT_RESULTS_DIR / f"embedding_failures_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    write_embedding_failures(failure_log, failures)

    print("\nEmbedding computation complete")
    print(f"  Dataset: {dataset.name}")
    print(f"  Device: {device}")
    print(f"  Candidate samples: {len(candidates):,}")
    print(f"  Samples updated: {computed_samples:,}")
    print(f"  Skipped with existing embeddings: {skipped_existing:,}")
    print(f"  Failed images: {len(failures):,}")
    print(f"  Baseline embeddings written: {baseline_vectors:,}")
    print(f"  Fine-tuned embeddings written: {finetuned_vectors:,}")
    print(f"  Embedding dimension: {embedding_dim}")


def delete_brain_run_if_needed(dataset, brain_key: str, rebuild: bool) -> bool:
    try:
        existing = set(dataset.list_brain_runs())
    except Exception:
        existing = set()
    if brain_key not in existing:
        return True
    if not rebuild:
        print(f"[FiftyOne] Brain key already exists, keeping: {brain_key}")
        return False
    print(f"[FiftyOne] Deleting existing brain key: {brain_key}")
    dataset.delete_brain_run(brain_key)
    return True


def build_indexes(args: argparse.Namespace) -> None:
    fo = require_fiftyone()
    import fiftyone.brain as fob

    dataset = fo.load_dataset(args.fo_dataset_name)
    fields = [
        ("baseline_embedding", "sim_baseline", "viz_baseline"),
        ("finetuned_embedding", "sim_finetuned", "viz_finetuned"),
    ]

    method = args.visualization_method
    if method == "umap" and importlib.util.find_spec("umap") is None:
        if args.no_pca_fallback:
            raise RuntimeError("UMAP requires umap-learn. Install it with: pip install umap-learn")
        print("[Warning] umap-learn is not installed; falling back to PCA visualization.")
        print("          Install UMAP support with: pip install umap-learn")
        method = "pca"

    for embedding_field, similarity_key, visualization_key in fields:
        view = dataset.exists(embedding_field)
        if len(view) == 0:
            print(f"[Warning] No samples have {embedding_field}; skipping")
            continue

        if delete_brain_run_if_needed(dataset, similarity_key, args.rebuild_indexes):
            kwargs = {"embeddings": embedding_field, "brain_key": similarity_key}
            if args.similarity_backend:
                kwargs["backend"] = args.similarity_backend
            print(f"[FiftyOne] Computing similarity index {similarity_key} on {len(view):,} samples")
            fob.compute_similarity(view, **kwargs)

        if delete_brain_run_if_needed(dataset, visualization_key, args.rebuild_indexes):
            print(
                f"[FiftyOne] Computing {method.upper()} visualization "
                f"{visualization_key} on {len(view):,} samples"
            )
            fob.compute_visualization(
                view,
                embeddings=embedding_field,
                method=method,
                brain_key=visualization_key,
                num_dims=2,
            )

    print("\nIndexes and visualizations complete")
    print("Open the app with:")
    print(f"  python CV/SimCLR_reid/fiftyone_reid_workflow.py launch --fo_dataset_name {dataset.name}")
    print("In the App, use brain keys: sim_baseline, sim_finetuned, viz_baseline, viz_finetuned")


def launch_app(args: argparse.Namespace) -> None:
    fo = require_fiftyone()
    dataset = fo.load_dataset(args.fo_dataset_name)
    app_view = dataset

    # Some embedded MongoDB/FiftyOne combinations can have a stale fast
    # estimated count after bulk inserts: `dataset.values("id")` returns
    # samples, but `dataset.count()` returns 0. The App uses the fast count for
    # full datasets, so launch an explicit all-samples view in that case.
    sample_ids = dataset.values("id")
    if dataset.count() == 0 and sample_ids:
        app_view = dataset.select(sample_ids)
        print(
            "[FiftyOne] Dataset fast count is stale at 0; "
            f"launching an explicit all-samples view with {len(sample_ids):,} samples."
        )

    session = fo.launch_app(app_view)
    print(f"[FiftyOne] Launched dataset: {dataset.name}")
    if not args.no_wait:
        session.wait()


def normalize_np(vector: Any) -> np.ndarray | None:
    if vector is None:
        return None
    arr = np.asarray(vector, dtype=np.float32)
    if arr.ndim != 1 or arr.size == 0:
        return None
    norm = np.linalg.norm(arr)
    if not np.isfinite(norm) or norm == 0:
        return None
    return arr / norm


def load_embedding_records(dataset_name: str, validation_only: bool = False) -> list[EmbeddingRecord]:
    fo = require_fiftyone()
    dataset = fo.load_dataset(dataset_name)
    records: list[EmbeddingRecord] = []

    for sample in dataset.iter_samples(progress=False):
        baseline = normalize_np(get_sample_field(sample, "baseline_embedding"))
        finetuned = normalize_np(get_sample_field(sample, "finetuned_embedding"))
        if baseline is None or finetuned is None:
            continue
        is_validation_video = bool(get_sample_field(sample, "is_validation_video"))
        if validation_only and not is_validation_video:
            continue
        records.append(
            EmbeddingRecord(
                sample_id=sample.id,
                filepath=sample.filepath,
                filename=str(get_sample_field(sample, "filename") or Path(sample.filepath).name),
                case_no=str(get_sample_field(sample, "case_no")),
                video_idx=int(get_sample_field(sample, "video_idx")),
                frame_id=int(get_sample_field(sample, "frame_id")),
                burst_id=str(get_sample_field(sample, "burst_id")),
                is_validation_video=is_validation_video,
                baseline=baseline,
                finetuned=finetuned,
            )
        )
    if len(records) < 2:
        raise ValueError("At least two samples with both embeddings are required.")
    return records


def cosine_for_model(a: EmbeddingRecord, b: EmbeddingRecord, model_name: str) -> float:
    emb_a = a.baseline if model_name == "baseline" else a.finetuned
    emb_b = b.baseline if model_name == "baseline" else b.finetuned
    return float(np.dot(emb_a, emb_b))


def stats(values: list[float]) -> dict[str, Any]:
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


def pair_row(a: EmbeddingRecord, b: EmbeddingRecord, pair_type: str) -> dict[str, Any]:
    return {
        "pair_type": pair_type,
        "filepath_1": a.filepath,
        "filepath_2": b.filepath,
        "filename_1": a.filename,
        "filename_2": b.filename,
        "case_no_1": a.case_no,
        "case_no_2": b.case_no,
        "video_idx_1": a.video_idx,
        "video_idx_2": b.video_idx,
        "frame_id_1": a.frame_id,
        "frame_id_2": b.frame_id,
        "burst_id_1": a.burst_id,
        "burst_id_2": b.burst_id,
        "frame_delta": abs(a.frame_id - b.frame_id),
        "baseline_similarity": cosine_for_model(a, b, "baseline"),
        "finetuned_similarity": cosine_for_model(a, b, "finetuned"),
    }


def sample_same_burst_pairs(records: list[EmbeddingRecord], max_pairs: int, rng: random.Random) -> list[tuple[int, int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for idx, record in enumerate(records):
        groups[record.burst_id].append(idx)
    burst_ids = [burst_id for burst_id, items in groups.items() if len(items) >= 2]
    if not burst_ids:
        return []

    pairs = set()
    attempts = 0
    max_attempts = max(max_pairs * 20, 1000)
    while len(pairs) < max_pairs and attempts < max_attempts:
        attempts += 1
        items = groups[rng.choice(burst_ids)]
        i, j = rng.sample(items, 2)
        if i > j:
            i, j = j, i
        pairs.add((i, j))
    return list(pairs)


def sample_different_burst_pairs(records: list[EmbeddingRecord], max_pairs: int, rng: random.Random) -> list[tuple[int, int, str]]:
    by_case_video: dict[tuple[str, int], list[int]] = defaultdict(list)
    by_case: dict[str, list[int]] = defaultdict(list)
    for idx, record in enumerate(records):
        by_case_video[(record.case_no, record.video_idx)].append(idx)
        by_case[record.case_no].append(idx)

    pairs: dict[tuple[int, int], str] = {}
    target_same_video = max_pairs // 2
    attempts = 0
    max_attempts = max(max_pairs * 40, 2000)

    case_video_keys = [key for key, items in by_case_video.items() if len({records[i].burst_id for i in items}) >= 2]
    while len([kind for kind in pairs.values() if kind == "same_case_video_different_burst"]) < target_same_video:
        attempts += 1
        if attempts > max_attempts or not case_video_keys:
            break
        items = by_case_video[rng.choice(case_video_keys)]
        i, j = rng.sample(items, 2)
        if records[i].burst_id == records[j].burst_id:
            continue
        if i > j:
            i, j = j, i
        pairs[(i, j)] = "same_case_video_different_burst"

    all_indices = list(range(len(records)))
    attempts = 0
    while len(pairs) < max_pairs:
        attempts += 1
        if attempts > max_attempts:
            break
        i, j = rng.sample(all_indices, 2)
        if records[i].burst_id == records[j].burst_id:
            continue
        if records[i].case_no == records[j].case_no:
            continue
        if i > j:
            i, j = j, i
        pairs[(i, j)] = "different_case"

    return [(i, j, kind) for (i, j), kind in pairs.items()]


def sample_near_duplicate_buckets(
    records: list[EmbeddingRecord],
    max_pairs_per_bucket: int,
    near_frame_delta: int,
    rng: random.Random,
) -> dict[str, list[tuple[int, int]]]:
    same_burst = sample_same_burst_pairs(records, max_pairs_per_bucket * 4, rng)
    buckets = {
        "same_burst_nearby_frames": [],
        "same_burst_far_frames": [],
        "different_burst_same_video": [],
    }
    for i, j in same_burst:
        frame_delta = abs(records[i].frame_id - records[j].frame_id)
        key = "same_burst_nearby_frames" if frame_delta <= near_frame_delta else "same_burst_far_frames"
        if len(buckets[key]) < max_pairs_per_bucket:
            buckets[key].append((i, j))

    diff = sample_different_burst_pairs(records, max_pairs_per_bucket * 4, rng)
    for i, j, kind in diff:
        if kind == "same_case_video_different_burst" and len(buckets["different_burst_same_video"]) < max_pairs_per_bucket:
            buckets["different_burst_same_video"].append((i, j))
    return buckets


def model_matrix(records: list[EmbeddingRecord], model_name: str) -> np.ndarray:
    values = [record.baseline if model_name == "baseline" else record.finetuned for record in records]
    return np.stack(values, axis=0)


def top_neighbors(
    embeddings: np.ndarray,
    records: list[EmbeddingRecord],
    query_idx: int,
    top_k: int,
    exclude_same_burst: bool = True,
) -> list[tuple[int, float]]:
    scores = embeddings @ embeddings[query_idx]
    scores = scores.astype(np.float64)
    scores[query_idx] = -np.inf
    if exclude_same_burst:
        query_burst = records[query_idx].burst_id
        for idx, record in enumerate(records):
            if record.burst_id == query_burst:
                scores[idx] = -np.inf
    finite = np.isfinite(scores)
    if not finite.any():
        return []
    top_indices = np.argpartition(-scores, min(top_k, finite.sum()) - 1)[:top_k]
    top_indices = sorted(top_indices, key=lambda idx: scores[idx], reverse=True)
    return [(int(idx), float(scores[idx])) for idx in top_indices if np.isfinite(scores[idx])]


def retrieval_summaries(
    records: list[EmbeddingRecord],
    query_indices: list[int],
    top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, list[list[tuple[int, float]]]]]:
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    report_neighbors: dict[str, list[list[tuple[int, float]]]] = {}

    for model_name in ["baseline", "finetuned"]:
        embeddings = model_matrix(records, model_name)
        top1_sims = []
        top5_sims = []
        same_case_top5 = 0
        same_video_top5 = 0
        different_case_top5 = 0
        total_top5 = 0
        per_query_neighbors = []

        for query_idx in query_indices:
            neighbors = top_neighbors(embeddings, records, query_idx, max(top_k, 10), exclude_same_burst=True)
            per_query_neighbors.append(neighbors)
            query = records[query_idx]
            top5 = neighbors[:5]
            if neighbors:
                top1_sims.append(neighbors[0][1])
            top5_sims.extend([score for _, score in top5])
            total_top5 += len(top5)

            same_case_count = sum(1 for idx, _ in top5 if records[idx].case_no == query.case_no)
            same_video_count = sum(
                1
                for idx, _ in top5
                if records[idx].case_no == query.case_no and records[idx].video_idx == query.video_idx
            )
            different_case_count = sum(1 for idx, _ in top5 if records[idx].case_no != query.case_no)
            same_case_top5 += same_case_count
            same_video_top5 += same_video_count
            different_case_top5 += different_case_count

            rows.append(
                {
                    "model": model_name,
                    "query_filepath": query.filepath,
                    "query_filename": query.filename,
                    "query_case_no": query.case_no,
                    "query_video_idx": query.video_idx,
                    "query_frame_id": query.frame_id,
                    "query_burst_id": query.burst_id,
                    "top1_similarity": neighbors[0][1] if neighbors else None,
                    "top1_same_case": bool(neighbors and records[neighbors[0][0]].case_no == query.case_no),
                    "top1_same_video": bool(
                        neighbors
                        and records[neighbors[0][0]].case_no == query.case_no
                        and records[neighbors[0][0]].video_idx == query.video_idx
                    ),
                    "top5_mean_similarity": float(np.mean([score for _, score in top5])) if top5 else None,
                    "top5_same_case_count": same_case_count,
                    "top5_same_video_count": same_video_count,
                    "top5_different_case_count": different_case_count,
                }
            )

        summary[model_name] = {
            "queries": len(query_indices),
            "top1_similarity": stats(top1_sims),
            "top5_similarity": stats(top5_sims),
            "top5_same_case_fraction": None if total_top5 == 0 else same_case_top5 / total_top5,
            "top5_same_video_fraction": None if total_top5 == 0 else same_video_top5 / total_top5,
            "top5_different_case_fraction": None if total_top5 == 0 else different_case_top5 / total_top5,
        }
        report_neighbors[model_name] = per_query_neighbors

    return rows, summary, report_neighbors


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def image_uri(filepath: str) -> str:
    try:
        return Path(filepath).resolve().as_uri()
    except ValueError:
        return filepath


def sample_card(record: EmbeddingRecord, similarity: float | None = None, label: str | None = None) -> str:
    sim_text = "" if similarity is None else f"<div>sim: {similarity:.4f}</div>"
    label_text = "" if label is None else f"<div class='label'>{html.escape(label)}</div>"
    return (
        "<div class='card'>"
        f"<img src='{html.escape(image_uri(record.filepath))}' loading='lazy'>"
        f"{label_text}{sim_text}"
        f"<div>{html.escape(record.filename)}</div>"
        f"<div>case {html.escape(record.case_no)} v{record.video_idx:02d} frame {record.frame_id}</div>"
        f"<div>{html.escape(record.burst_id)}</div>"
        "</div>"
    )


def html_header(title: str) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; color: #222; }}
h1, h2, h3 {{ margin: 0.7em 0 0.35em; }}
.query {{ border-top: 1px solid #ccc; padding-top: 18px; margin-top: 24px; }}
.columns {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px; }}
.card {{ border: 1px solid #ddd; border-radius: 6px; padding: 6px; font-size: 11px; overflow-wrap: anywhere; }}
.card img {{ width: 100%; height: 150px; object-fit: contain; background: #f3f3f3; }}
.query-card {{ max-width: 180px; }}
.label {{ font-weight: bold; }}
.warning {{ background: #fff5d7; border: 1px solid #ead28a; padding: 10px; border-radius: 6px; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
"""


def write_retrieval_report(
    path: Path,
    records: list[EmbeddingRecord],
    query_indices: list[int],
    neighbors_by_model: dict[str, list[list[tuple[int, float]]]],
) -> None:
    parts = [
        html_header("Baseline vs Fine-tuned Cross-burst Retrieval"),
        "<p class='warning'>Same-burst images are excluded from the neighbor gallery. "
        "This is an unsupervised proxy report, not true Re-ID accuracy.</p>",
    ]
    for qpos, query_idx in enumerate(query_indices):
        query = records[query_idx]
        parts.append("<div class='query'>")
        parts.append(f"<h2>Query {qpos + 1}: {html.escape(query.filename)}</h2>")
        parts.append(f"<div class='query-card'>{sample_card(query, label='query')}</div>")
        parts.append("<div class='columns'>")
        for model_name, title in [("baseline", "Baseline top-10"), ("finetuned", "Fine-tuned top-10")]:
            parts.append("<section>")
            parts.append(f"<h3>{title}</h3><div class='grid'>")
            for neighbor_idx, score in neighbors_by_model[model_name][qpos][:10]:
                parts.append(sample_card(records[neighbor_idx], score))
            parts.append("</div></section>")
        parts.append("</div></div>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def collect_hard_negatives(
    records: list[EmbeddingRecord],
    max_items: int,
    query_indices: list[int],
) -> dict[str, list[tuple[int, int, float]]]:
    results: dict[str, list[tuple[int, int, float]]] = {}
    for model_name in ["baseline", "finetuned"]:
        embeddings = model_matrix(records, model_name)
        pairs: dict[tuple[int, int], float] = {}
        for query_idx in query_indices:
            neighbors = top_neighbors(embeddings, records, query_idx, 50, exclude_same_burst=True)
            query = records[query_idx]
            for neighbor_idx, score in neighbors:
                neighbor = records[neighbor_idx]
                is_hard_negative = query.case_no != neighbor.case_no or (
                    query.video_idx == neighbor.video_idx and abs(query.frame_id - neighbor.frame_id) > 600
                )
                if not is_hard_negative:
                    continue
                key = tuple(sorted((query_idx, neighbor_idx)))
                pairs[key] = max(score, pairs.get(key, -math.inf))
        sorted_pairs = sorted(pairs.items(), key=lambda item: item[1], reverse=True)[:max_items]
        results[model_name] = [(i, j, score) for (i, j), score in sorted_pairs]
    return results


def write_hard_negative_report(
    path: Path,
    records: list[EmbeddingRecord],
    hard_negatives: dict[str, list[tuple[int, int, float]]],
) -> None:
    parts = [
        html_header("Hard-negative Retrieval Report"),
        "<p class='warning'>High-similarity different-case or distant-burst pairs. "
        "Use these to look for clothing, mask, camera-angle, or operating-room background confusion.</p>",
    ]
    for model_name, title in [("baseline", "Baseline"), ("finetuned", "Fine-tuned")]:
        parts.append(f"<h2>{title}</h2>")
        for rank, (i, j, score) in enumerate(hard_negatives[model_name], start=1):
            parts.append(f"<h3>Pair {rank} - similarity {score:.4f}</h3>")
            parts.append("<div class='grid'>")
            parts.append(sample_card(records[i], label="query"))
            parts.append(sample_card(records[j], score, label="neighbor"))
            parts.append("</div>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_optional_plots(run_dir: Path, metrics: dict[str, Any]) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    def plot_value(value: Any) -> float:
        return float("nan") if value is None else float(value)

    names = ["same_burst", "different_burst", "proxy_gap"]
    baseline = [
        plot_value(metrics["models"]["baseline"]["same_burst_similarity"]["mean"]),
        plot_value(metrics["models"]["baseline"]["different_burst_similarity"]["mean"]),
        plot_value(metrics["models"]["baseline"]["proxy_separability_gap"]),
    ]
    finetuned = [
        plot_value(metrics["models"]["finetuned"]["same_burst_similarity"]["mean"]),
        plot_value(metrics["models"]["finetuned"]["different_burst_similarity"]["mean"]),
        plot_value(metrics["models"]["finetuned"]["proxy_separability_gap"]),
    ]
    x = np.arange(len(names))
    width = 0.35
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(x - width / 2, baseline, width, label="baseline")
    ax.bar(x + width / 2, finetuned, width, label="fine-tuned")
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel("cosine similarity / gap")
    ax.set_title("Unsupervised embedding proxy metrics")
    ax.legend()
    fig.tight_layout()
    fig.savefig(run_dir / "proxy_metrics.png", dpi=160)
    plt.close(fig)


def interpret_metrics(metrics: dict[str, Any]) -> str:
    base = metrics["models"]["baseline"]
    fine = metrics["models"]["finetuned"]
    base_gap = base["proxy_separability_gap"]
    fine_gap = fine["proxy_separability_gap"]
    base_same = base["same_burst_similarity"]["mean"]
    fine_same = fine["same_burst_similarity"]["mean"]
    base_diff = base["different_burst_similarity"]["mean"]
    fine_diff = fine["different_burst_similarity"]["mean"]

    if None in {base_gap, fine_gap, base_same, fine_same, base_diff, fine_diff}:
        return "Not enough sampled pairs to interpret the proxy metrics."

    gap_improved = fine_gap > base_gap
    same_improved = fine_same > base_same
    diff_increase = fine_diff - base_diff
    if gap_improved and same_improved and diff_increase < 0.08:
        return (
            "Fine-tuned embeddings look better by unsupervised proxy: same-burst similarity "
            "and the separability gap improved without a large different-burst increase. "
            "Confirm visually and with manual identity labels before claiming Re-ID accuracy."
        )
    if gap_improved:
        return (
            "Fine-tuned embeddings improved the proxy gap, but inspect retrieval and leakage reports "
            "to ensure the gain is not mostly case/video context."
        )
    return (
        "Fine-tuned embeddings do not clearly beat baseline by these unsupervised proxies. "
        "Use the visual reports to inspect whether retrieval quality improved in ways the proxy misses."
    )


def validate_unsupervised(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    records = load_embedding_records(args.fo_dataset_name, validation_only=args.validation_only)
    run_dir = make_run_dir(Path(args.output_dir))

    same_pairs = sample_same_burst_pairs(records, args.max_pairs, rng)
    diff_pairs = sample_different_burst_pairs(records, args.max_pairs, rng)
    same_rows = [pair_row(records[i], records[j], "same_burst") for i, j in same_pairs]
    diff_rows = [pair_row(records[i], records[j], kind) for i, j, kind in diff_pairs]

    write_csv(run_dir / "pair_samples_same_burst.csv", same_rows)
    write_csv(run_dir / "pair_samples_different_burst.csv", diff_rows)

    query_count = min(args.max_queries, len(records))
    query_indices = rng.sample(range(len(records)), query_count)
    retrieval_rows, retrieval_summary, report_neighbors = retrieval_summaries(records, query_indices, args.top_k)
    write_csv(run_dir / "retrieval_summary.csv", retrieval_rows)

    report_query_indices = query_indices[: min(args.report_queries, len(query_indices))]
    report_neighbors_trimmed = {
        "baseline": report_neighbors["baseline"][: len(report_query_indices)],
        "finetuned": report_neighbors["finetuned"][: len(report_query_indices)],
    }
    write_retrieval_report(run_dir / "retrieval_report.html", records, report_query_indices, report_neighbors_trimmed)

    hard_query_indices = rng.sample(range(len(records)), min(args.hard_negative_queries, len(records)))
    hard_negatives = collect_hard_negatives(records, args.hard_negative_count, hard_query_indices)
    write_hard_negative_report(run_dir / "hard_negative_report.html", records, hard_negatives)

    near_buckets = sample_near_duplicate_buckets(records, min(args.max_pairs, 5000), args.near_frame_delta, rng)
    near_summary: dict[str, Any] = {}
    for bucket_name, pairs in near_buckets.items():
        near_summary[bucket_name] = {
            "baseline": stats([cosine_for_model(records[i], records[j], "baseline") for i, j in pairs]),
            "finetuned": stats([cosine_for_model(records[i], records[j], "finetuned") for i, j in pairs]),
        }

    metrics: dict[str, Any] = {
        "warning": (
            "This is unsupervised proxy validation, not supervised Re-ID validation. "
            "Do not interpret these metrics as identity accuracy, mAP, or rank-1."
        ),
        "dataset": args.fo_dataset_name,
        "records_with_both_embeddings": len(records),
        "validation_only": args.validation_only,
        "models": {},
        "retrieval": retrieval_summary,
        "near_duplicate_sensitivity": near_summary,
        "collapse_warnings": [],
        "case_video_leakage_notes": [],
    }

    for model_name in ["baseline", "finetuned"]:
        same_values = [row[f"{model_name}_similarity"] for row in same_rows]
        diff_values = [row[f"{model_name}_similarity"] for row in diff_rows]
        same_stats = stats(same_values)
        diff_stats = stats(diff_values)
        gap = None
        if same_stats["mean"] is not None and diff_stats["mean"] is not None:
            gap = same_stats["mean"] - diff_stats["mean"]
        metrics["models"][model_name] = {
            "same_burst_similarity": same_stats,
            "different_burst_similarity": diff_stats,
            "proxy_separability_gap": gap,
            "different_burst_breakdown": {
                kind: stats([row[f"{model_name}_similarity"] for row in diff_rows if row["pair_type"] == kind])
                for kind in sorted({row["pair_type"] for row in diff_rows})
            },
        }

    base = metrics["models"]["baseline"]
    fine = metrics["models"]["finetuned"]
    for model_name, model_metrics in metrics["models"].items():
        same_mean = model_metrics["same_burst_similarity"]["mean"]
        diff_mean = model_metrics["different_burst_similarity"]["mean"]
        gap = model_metrics["proxy_separability_gap"]
        if same_mean is not None and diff_mean is not None and gap is not None:
            if same_mean > 0.90 and diff_mean > 0.75 and gap < 0.10:
                metrics["collapse_warnings"].append(
                    f"{model_name}: both same-burst and different-burst similarities are high with a small gap."
                )

    if (
        base["proxy_separability_gap"] is not None
        and fine["proxy_separability_gap"] is not None
        and fine["different_burst_similarity"]["mean"] is not None
        and base["different_burst_similarity"]["mean"] is not None
        and fine["proxy_separability_gap"] <= base["proxy_separability_gap"]
        and fine["different_burst_similarity"]["mean"] > base["different_burst_similarity"]["mean"]
    ):
        metrics["collapse_warnings"].append(
            "Fine-tuned different-burst similarity increased without improving the proxy gap."
        )

    for model_name, summary in retrieval_summary.items():
        same_case_fraction = summary["top5_same_case_fraction"]
        same_video_fraction = summary["top5_same_video_fraction"]
        if same_case_fraction is not None and same_case_fraction > 0.80:
            metrics["case_video_leakage_notes"].append(
                f"{model_name}: more than 80% of top-5 cross-burst neighbors share case_no."
            )
        if same_video_fraction is not None and same_video_fraction > 0.60:
            metrics["case_video_leakage_notes"].append(
                f"{model_name}: more than 60% of top-5 cross-burst neighbors share the same case/video."
            )

    metrics["interpretation"] = interpret_metrics(metrics)

    with (run_dir / "metrics_summary.json").open("w", encoding="utf-8") as file:
        json.dump(metrics, file, indent=2)
    write_optional_plots(run_dir, metrics)

    print("\nUnsupervised validation complete")
    print(f"  Results: {run_dir}")
    print("  WARNING: These are proxy metrics, not true Re-ID accuracy.")
    for model_name in ["baseline", "finetuned"]:
        model_metrics = metrics["models"][model_name]
        print(f"\n  {model_name}")
        print(f"    same-burst mean:      {model_metrics['same_burst_similarity']['mean']}")
        print(f"    different-burst mean: {model_metrics['different_burst_similarity']['mean']}")
        print(f"    proxy gap:            {model_metrics['proxy_separability_gap']}")
        print(f"    top-1 sim mean:       {retrieval_summary[model_name]['top1_similarity']['mean']}")
        print(f"    top-5 same-case frac: {retrieval_summary[model_name]['top5_same_case_fraction']}")
        print(f"    top-5 same-video frac:{retrieval_summary[model_name]['top5_same_video_fraction']}")
    print(f"\n  Interpretation: {metrics['interpretation']}")


def add_identity_field(args: argparse.Namespace) -> None:
    fo = require_fiftyone()
    dataset = fo.load_dataset(args.fo_dataset_name)
    try:
        schema = dataset.get_field_schema()
        if args.identity_field not in schema:
            dataset.add_sample_field(args.identity_field, fo.StringField)
    except Exception:
        pass

    updated = 0
    for sample in dataset.iter_samples(progress=True):
        current = None
        try:
            current = sample[args.identity_field]
        except Exception:
            pass
        if args.force or current is None:
            sample[args.identity_field] = ""
            sample.save()
            updated += 1
    print(f"[FiftyOne] Identity field ready: {args.identity_field}")
    print(f"  Samples initialized: {updated:,}")


def unique_copy_path(output_dir: Path, identity: str, source: Path) -> Path:
    identity_dir = output_dir / identity
    identity_dir.mkdir(parents=True, exist_ok=True)
    candidate = identity_dir / source.name
    if not candidate.exists():
        return candidate
    stem = source.stem
    suffix = source.suffix
    for idx in range(1, 1000):
        candidate = identity_dir / f"{stem}_{idx:03d}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a non-colliding filename for {source.name} in {identity_dir}")


def export_labeled(args: argparse.Namespace) -> None:
    fo = require_fiftyone()
    dataset = fo.load_dataset(args.fo_dataset_name)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    for sample in dataset.iter_samples(progress=True):
        try:
            identity = str(sample[args.identity_field] or "").strip()
        except Exception:
            identity = ""
        if identity.lower() in SKIP_IDENTITIES:
            continue
        source = Path(sample.filepath)
        if not source.exists():
            print(f"[Warning] Missing source image, skipping: {source}")
            continue
        exported_path = unique_copy_path(output_dir, identity, source)
        shutil.copy2(source, exported_path)
        rows.append(
            {
                "filepath": str(source.resolve()),
                "exported_path": str(exported_path.resolve()),
                "identity": identity,
                "case_no": get_sample_field(sample, "case_no"),
                "video_idx": get_sample_field(sample, "video_idx"),
                "frame_id": get_sample_field(sample, "frame_id"),
                "burst_id": get_sample_field(sample, "burst_id"),
            }
        )

    write_csv(output_dir / "labels.csv", rows)
    summary = {
        "dataset": args.fo_dataset_name,
        "identity_field": args.identity_field,
        "number_of_exported_images": len(rows),
        "number_of_images_per_identity": dict(Counter(row["identity"] for row in rows)),
    }
    with (output_dir / "summary.json").open("w", encoding="utf-8") as file:
        json.dump(summary, file, indent=2)

    print("\nLabeled export complete")
    print(f"  Output: {output_dir}")
    print(f"  Exported images: {len(rows):,}")
    print(f"  Identities: {len(summary['number_of_images_per_identity']):,}")


def add_common_dataset_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fo_dataset_name", default=DEFAULT_FO_DATASET_NAME)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FiftyOne workflow for unsupervised SimCLR Re-ID embedding exploration."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-dataset", help="Load crop paths and parsed metadata into FiftyOne.")
    build.add_argument("--dataset_dir", default=str(DEFAULT_DATASET_DIR))
    add_common_dataset_arg(build)
    build.add_argument("--case_no", default=None)
    build.add_argument("--video_idx", type=int, default=None)
    build.add_argument("--max_images", type=int, default=10000)
    build.add_argument("--shuffle", action="store_true")
    build.add_argument("--seed", type=int, default=42)
    build.add_argument("--burst_gap_threshold", type=int, default=60)
    build.add_argument("--overwrite_dataset", action="store_true")
    build.set_defaults(func=build_dataset)

    embed = subparsers.add_parser("compute-embeddings", help="Compute baseline and fine-tuned embeddings.")
    add_common_dataset_arg(embed)
    embed.add_argument("--baseline_weights", default=str(DEFAULT_BASELINE_WEIGHTS))
    embed.add_argument("--finetuned_weights", default=str(DEFAULT_FINETUNED_WEIGHTS))
    embed.add_argument("--batch_size", type=int, default=128)
    embed.add_argument("--device", default="auto")
    embed.add_argument("--limit", type=int, default=None)
    embed.add_argument("--force_recompute", action="store_true")
    embed.add_argument("--failure_log", default=None)
    embed.set_defaults(func=compute_embeddings)

    indexes = subparsers.add_parser("build-indexes", help="Build FiftyOne similarity indexes and visualizations.")
    add_common_dataset_arg(indexes)
    indexes.add_argument("--rebuild_indexes", action="store_true")
    indexes.add_argument("--similarity_backend", default=None)
    indexes.add_argument("--visualization_method", choices=["umap", "pca", "tsne"], default="umap")
    indexes.add_argument("--no_pca_fallback", action="store_true")
    indexes.set_defaults(func=build_indexes)

    launch = subparsers.add_parser("launch", help="Launch the FiftyOne App.")
    add_common_dataset_arg(launch)
    launch.add_argument("--no_wait", action="store_true")
    launch.set_defaults(func=launch_app)

    validate = subparsers.add_parser("validate-unsupervised", help="Compute unsupervised proxy validation reports.")
    add_common_dataset_arg(validate)
    validate.add_argument("--output_dir", default=str(DEFAULT_RESULTS_DIR))
    validate.add_argument("--max_pairs", type=int, default=50000)
    validate.add_argument("--max_queries", type=int, default=200)
    validate.add_argument("--top_k", type=int, default=5)
    validate.add_argument("--report_queries", type=int, default=20)
    validate.add_argument("--hard_negative_queries", type=int, default=300)
    validate.add_argument("--hard_negative_count", type=int, default=50)
    validate.add_argument("--near_frame_delta", type=int, default=60)
    validate.add_argument("--validation_only", action="store_true")
    validate.add_argument("--seed", type=int, default=42)
    validate.set_defaults(func=validate_unsupervised)

    identity = subparsers.add_parser("add-identity-field", help="Initialize an editable manual identity field.")
    add_common_dataset_arg(identity)
    identity.add_argument("--identity_field", default="identity")
    identity.add_argument("--force", action="store_true")
    identity.set_defaults(func=add_identity_field)

    export = subparsers.add_parser("export-labeled", help="Export manually tagged identities to folders.")
    add_common_dataset_arg(export)
    export.add_argument("--identity_field", default="identity")
    export.add_argument("--output_dir", default=str(DEFAULT_LABELED_EXPORT_DIR))
    export.set_defaults(func=export_labeled)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
