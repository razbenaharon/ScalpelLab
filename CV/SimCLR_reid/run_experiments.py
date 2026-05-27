"""Resumable orchestration for controlled SimCLR LR sweeps."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from _metrics_db import open_db, upsert_run_meta
from _stability import UnstableRunError
from experiments import (
    ABLATIONS,
    DEFAULT_DATASET_DIR,
    DEFAULT_LR_SWEEP,
    DEFAULT_PRETRAINED_WEIGHTS,
    DEFAULT_SEED,
    FIXED,
    LR_SWEEP,
    OPTIONAL_EXPERIMENTS,
    PROTECTED_PATHS,
)


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
METRICS_DB_NAME = "experiment_metrics.sqlite"
SUMMARY_NAME = "summary.json"
LEGACY_ARTIFACT_NAMES = {
    "metrics.csv",
    "best_checkpoint.pt",
    "best_backbone.pt",
    "last_checkpoint.pt",
    "curves.png",
    SUMMARY_NAME,
    "results.json",
    "downstream.json",
    "final_backbone.pt",
}

TRAIN_DEFAULT_CONFIG = {
    "image_size": (256, 128),
    "batch_size": 512,
    "min_batch_size": 64,
    "auto_reduce_batch_on_oom": True,
    "pin_memory": False,
    "num_workers": 0,
    "burst_gap_threshold": 60,
    "val_video_indices": None,
    "val_split_modulo": 5,
    "val_split_remainder": 0,
    "epochs": 50,
    "lr": 3e-4,
    "weight_decay": 1e-4,
    "warmup_epochs": 5,
    "accumulation_steps": 1,
    "seed": DEFAULT_SEED,
    "deterministic": True,
    "temperature": 0.07,
    "projection_dim": 128,
    "projection_hidden": 512,
    "color_jitter_strength": 0.5,
    "random_erasing_prob": 0.3,
    "crop_scale_min": 0.7,
    "backbone": "osnet_ain_x1_0",
    "pretrained_weights": str(DEFAULT_PRETRAINED_WEIGHTS),
    "freeze_early_layers": True,
    "freeze_layers": ["conv1", "maxpool", "conv2", "pool2"],
    "staged_unfreeze": False,
    "stability_warmup_epochs": 3,
    "stability_nan_inf": True,
    "stability_explosion_factor": 5.0,
    "stability_absolute_max": 50.0,
    "stability_max_oom_retries": 3,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def normalize(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def repo_path(path: Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return normalize(path)
    return normalize(REPO_ROOT / path)


def discover_protected_paths() -> list[Path]:
    protected = [repo_path(path) for path in PROTECTED_PATHS]
    protected.extend(normalize(path) for path in SCRIPT_DIR.rglob("optuna_study.db"))
    protected.extend(normalize(path) for path in REPO_ROOT.rglob("simclr_output") if path.is_dir())
    return sorted(set(protected), key=lambda value: str(value).lower())


def validate_output_root(output_root: Path) -> tuple[Path, list[Path]]:
    output_root = normalize(output_root)
    protected_paths = discover_protected_paths()
    for protected in protected_paths:
        if output_root == protected or is_relative_to(output_root, protected) or is_relative_to(protected, output_root):
            raise ValueError(
                "Refusing protected output_root. "
                f"output_root={output_root} conflicts with protected path={protected}"
            )
    return output_root, protected_paths


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, default=str)


def config_hash(config: dict[str, Any]) -> str:
    encoded = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def read_metrics_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            try:
                rows.append(
                    {
                        "epoch": int(row["epoch"]),
                        "train_loss": float(row["train_loss"]),
                        "val_loss": float(row["val_loss"]),
                        "lr": float(row["lr"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def gpu_info() -> dict[str, Any]:
    try:
        import torch

        info = {
            "cuda_available": bool(torch.cuda.is_available()),
            "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
        }
        if torch.cuda.is_available():
            info["device_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["total_memory_gb"] = round(props.total_memory / (1024**3), 2)
        return info
    except Exception as exc:
        return {"cuda_available": None, "error": str(exc)}


def build_config(base: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    config = TRAIN_DEFAULT_CONFIG.copy()
    config.update(base)
    config["pretrained_weights"] = str(Path(args.pretrained_weights))
    config["epochs"] = int(args.epochs if args.epochs is not None else config["epochs"])
    if args.batch_size is not None:
        config["batch_size"] = int(args.batch_size)
    if args.min_batch_size is not None:
        config["min_batch_size"] = int(args.min_batch_size)
    config["seed"] = int(args.seed if args.seed is not None else config.get("seed", DEFAULT_SEED))
    config["deterministic"] = not args.no_deterministic
    config["auto_reduce_batch_on_oom"] = True
    return config


def selected_experiment_names(args: argparse.Namespace) -> list[str]:
    if args.experiments:
        names = [name.strip() for name in args.experiments.split(",") if name.strip()]
    else:
        names = list(DEFAULT_LR_SWEEP)
    unknown = [name for name in names if name not in LR_SWEEP]
    if unknown:
        raise ValueError(f"Unknown experiment(s): {', '.join(unknown)}")
    optional = [name for name in names if name in OPTIONAL_EXPERIMENTS]
    if optional and not args.include_optional:
        raise ValueError(
            "Optional experiments require --include_optional: "
            + ", ".join(optional)
        )
    return names


def summarize_from_artifacts(
    run_name: str,
    run_dir: Path,
    config: dict[str, Any],
    status: str,
    wall_clock_sec: float | None,
    unstable_reason: str | None = None,
    downstream: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results = read_json(run_dir / "results.json") or {}
    metrics = read_metrics_csv(run_dir / "metrics.csv")
    best_epoch = results.get("best_epoch")
    best_val_loss = results.get("best_val_loss")
    final_train_loss = results.get("final_train_loss")
    final_val_loss = results.get("final_val_loss")
    completed_epochs = results.get("completed_epochs")

    if metrics:
        best_row = min(metrics, key=lambda row: row["val_loss"])
        last_row = metrics[-1]
        best_epoch = best_epoch if best_epoch is not None else best_row["epoch"]
        best_val_loss = best_val_loss if best_val_loss is not None else best_row["val_loss"]
        final_train_loss = final_train_loss if final_train_loss is not None else last_row["train_loss"]
        final_val_loss = final_val_loss if final_val_loss is not None else last_row["val_loss"]
        completed_epochs = completed_epochs if completed_epochs is not None else len(metrics)
    else:
        completed_epochs = completed_epochs if completed_epochs is not None else 0

    existing = read_json(run_dir / SUMMARY_NAME) or {}
    if downstream is None and "downstream" in existing:
        downstream = existing.get("downstream")

    return {
        "run_name": run_name,
        "config_hash": config_hash(config),
        "seed": config.get("seed"),
        "status": status,
        "unstable_reason": unstable_reason,
        "best_epoch": best_epoch,
        "best_val_loss": best_val_loss,
        "final_train_loss": final_train_loss,
        "final_val_loss": final_val_loss,
        "completed_epochs": completed_epochs,
        "downstream": downstream,
        "wall_clock_sec": wall_clock_sec if wall_clock_sec is not None else existing.get("wall_clock_sec"),
        "gpu_info": existing.get("gpu_info") or gpu_info(),
    }


def update_meta_from_summary(conn, run_name: str, config: dict[str, Any], summary: dict[str, Any], started_utc: str | None) -> None:
    fields = {
        "config_json": json.dumps(config, sort_keys=True, default=str),
        "seed": config.get("seed"),
        "status": summary.get("status"),
        "unstable_reason": summary.get("unstable_reason"),
        "finished_utc": utc_now(),
        "completed_epochs": summary.get("completed_epochs"),
        "best_epoch": summary.get("best_epoch"),
        "best_val_loss": summary.get("best_val_loss"),
    }
    if started_utc is not None:
        fields["started_utc"] = started_utc
    upsert_run_meta(conn, run_name, **fields)


def has_downstream_flags(args: argparse.Namespace) -> bool:
    return bool(args.separability_eval or args.multi_identity_eval or args.with_fiftyone)


def validate_downstream_args(args: argparse.Namespace) -> None:
    if args.separability_eval:
        missing = [
            flag
            for flag, value in [
                ("--baseline_weights", args.baseline_weights),
                ("--person_a_dir", args.person_a_dir),
                ("--person_b_dir", args.person_b_dir),
            ]
            if not value
        ]
        if missing:
            raise ValueError("--separability_eval requires " + ", ".join(missing))
    if args.multi_identity_eval and not args.multi_identity_dataset_dir:
        raise ValueError("--multi_identity_eval requires --multi_identity_dataset_dir")


def evaluate_downstream(backbone_path: Path, run_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    downstream = {
        "separability_gap": None,
        "inter_similarity": None,
        "intra_similarity": None,
        "rank1": None,
        "map_score": None,
        "fiftyone_path": None,
    }

    if args.separability_eval:
        try:
            from validate_model import run_comparative_validation

            output_dir = run_dir / "downstream_separability"
            comparison = run_comparative_validation(
                args.baseline_weights,
                str(backbone_path),
                args.person_a_dir,
                args.person_b_dir,
                str(output_dir),
            )
            finetuned = comparison.get("finetuned", {})
            summary = finetuned.get("summary", {})
            inter = finetuned.get("inter_similarity", {})
            downstream["separability_gap"] = summary.get("separability_gap")
            downstream["inter_similarity"] = inter.get("mean")
            downstream["intra_similarity"] = summary.get("avg_intra_similarity")
        except Exception as exc:
            print(f"[WARN] Separability eval failed for {run_dir.name}: {exc}", file=sys.stderr)

    if args.multi_identity_eval:
        try:
            from validate_reid_multi_identity import run_validation

            output_dir = run_dir / "downstream_multi_identity"
            ns = SimpleNamespace(
                validation_dir=args.multi_identity_dataset_dir,
                baseline_weights=args.baseline_weights or args.pretrained_weights,
                finetuned_weights=str(backbone_path),
                output_dir=str(output_dir),
                device=None,
                batch_size=64,
            )
            validation_run_dir = run_validation(ns)
            summary = read_json(Path(validation_run_dir) / "validation_summary.json") or {}
            fine = (summary.get("metrics") or {}).get("fine_tuned", {})
            downstream["rank1"] = fine.get("top1_retrieval_accuracy")
            downstream["separability_gap"] = downstream["separability_gap"] or fine.get("separability_gap")
        except Exception as exc:
            print(f"[WARN] Multi-identity eval failed for {run_dir.name}: {exc}", file=sys.stderr)

    if args.with_fiftyone:
        try:
            import fiftyone_reid_workflow

            fo_name = f"simclr_{run_dir.name}_{config_hash({'path': str(run_dir)})}"
            output_dir = run_dir / "downstream_fiftyone"
            baseline = args.baseline_weights or args.pretrained_weights
            fiftyone_reid_workflow.main(
                [
                    "build-dataset",
                    "--dataset_dir",
                    args.dataset_dir,
                    "--fo_dataset_name",
                    fo_name,
                    "--seed",
                    str(args.seed),
                ]
            )
            fiftyone_reid_workflow.main(
                [
                    "compute-embeddings",
                    "--fo_dataset_name",
                    fo_name,
                    "--baseline_weights",
                    baseline,
                    "--finetuned_weights",
                    str(backbone_path),
                ]
            )
            fiftyone_reid_workflow.main(
                [
                    "validate-unsupervised",
                    "--fo_dataset_name",
                    fo_name,
                    "--output_dir",
                    str(output_dir),
                    "--seed",
                    str(args.seed),
                ]
            )
            downstream["fiftyone_path"] = str(output_dir.resolve())
        except Exception as exc:
            print(f"[WARN] FiftyOne workflow failed for {run_dir.name}: {exc}", file=sys.stderr)

    write_json(run_dir / "downstream.json", downstream)
    return downstream


def prepare_fresh_run_dir(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_ARTIFACT_NAMES:
        path = run_dir / name
        if path.exists() and path.is_file():
            path.unlink()
    for name in ["downstream_separability", "downstream_multi_identity", "downstream_fiftyone"]:
        path = run_dir / name
        if path.exists() and path.is_dir():
            shutil.rmtree(path)


def completed_best_lr(output_root: Path, run_names: list[str]) -> float | None:
    best_lr = None
    best_val = None
    for run_name in run_names:
        summary = read_json(output_root / run_name / SUMMARY_NAME)
        config = read_json(output_root / run_name / "config.json")
        if not summary or not config or summary.get("status") != "completed":
            continue
        val_loss = summary.get("best_val_loss")
        if val_loss is None:
            continue
        if best_val is None or val_loss < best_val:
            best_val = val_loss
            best_lr = config.get("lr")
    return best_lr


def write_skipped(output_root: Path, remaining: list[str], reason: str) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / "skipped.txt").open("w", encoding="utf-8") as file:
        file.write(reason + "\n")
        for run_name in remaining:
            file.write(run_name + "\n")


def run_schedule(
    names: list[str],
    configs: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    output_root: Path,
    conn,
    started_at: float,
) -> None:
    from train_simclr import run_training

    for index, run_name in enumerate(names):
        if args.max_runtime_hours is not None:
            elapsed_hours = (time.monotonic() - started_at) / 3600
            if elapsed_hours > args.max_runtime_hours:
                remaining = names[index:]
                write_skipped(
                    output_root,
                    remaining,
                    f"max_runtime_hours={args.max_runtime_hours} reached before starting {run_name}",
                )
                print(f"[TIME] Runtime cap reached; skipped {len(remaining)} remaining run(s).")
                return

        config = configs[run_name]
        run_dir = output_root / run_name
        summary = read_json(run_dir / SUMMARY_NAME)
        resume_from = None
        previous_wall_sec = 0.0

        if summary and summary.get("status") == "completed":
            if has_downstream_flags(args) and (run_dir / "best_backbone.pt").exists():
                downstream = evaluate_downstream(run_dir / "best_backbone.pt", run_dir, args)
                updated = summarize_from_artifacts(
                    run_name,
                    run_dir,
                    config,
                    "completed",
                    summary.get("wall_clock_sec"),
                    downstream=downstream,
                )
                write_json(run_dir / SUMMARY_NAME, updated)
                update_meta_from_summary(conn, run_name, config, updated, None)
                print(f"[DOWNSTREAM] Updated completed run {run_name}.")
            else:
                print(f"[SKIP] {run_name}: completed")
            continue

        if summary and summary.get("status") == "unstable" and not args.retry_unstable:
            print(f"[SKIP] {run_name}: unstable ({summary.get('unstable_reason')})")
            continue

        if summary and summary.get("status") == "interrupted" and (run_dir / "last_checkpoint.pt").exists():
            resume_from = run_dir / "last_checkpoint.pt"
            previous_wall_sec = float(summary.get("wall_clock_sec") or 0.0)
            print(f"[RESUME] {run_name} from {resume_from}")
        else:
            prepare_fresh_run_dir(run_dir)
            print(f"[START] {run_name}")

        write_json(run_dir / "config.json", config)
        started_utc = utc_now()
        upsert_run_meta(
            conn,
            run_name,
            config_json=json.dumps(config, sort_keys=True, default=str),
            seed=config.get("seed"),
            status="running",
            started_utc=started_utc,
        )

        run_start = time.monotonic()
        try:
            run_training(
                config,
                args.dataset_dir,
                str(output_root),
                run_name=run_name,
                patience=args.patience,
                resume_from=resume_from,
                metrics_conn=conn,
            )
            downstream = None
            if has_downstream_flags(args) and (run_dir / "best_backbone.pt").exists():
                downstream = evaluate_downstream(run_dir / "best_backbone.pt", run_dir, args)
            summary_payload = summarize_from_artifacts(
                run_name,
                run_dir,
                config,
                "completed",
                previous_wall_sec + (time.monotonic() - run_start),
                downstream=downstream,
            )
            if downstream is None:
                summary_payload["downstream"] = None
            write_json(run_dir / SUMMARY_NAME, summary_payload)
            update_meta_from_summary(conn, run_name, config, summary_payload, started_utc)
            print(f"[DONE] {run_name}: best_val={summary_payload.get('best_val_loss')}")
        except UnstableRunError as exc:
            summary_payload = summarize_from_artifacts(
                run_name,
                run_dir,
                config,
                "unstable",
                previous_wall_sec + (time.monotonic() - run_start),
                unstable_reason=exc.reason,
                downstream=None,
            )
            summary_payload["downstream"] = None
            write_json(run_dir / SUMMARY_NAME, summary_payload)
            update_meta_from_summary(conn, run_name, config, summary_payload, started_utc)
            print(f"[UNSTABLE] {run_name}: {exc.reason}")
            continue
        except KeyboardInterrupt:
            summary_payload = summarize_from_artifacts(
                run_name,
                run_dir,
                config,
                "interrupted",
                previous_wall_sec + (time.monotonic() - run_start),
                downstream=None,
            )
            summary_payload["downstream"] = None
            write_json(run_dir / SUMMARY_NAME, summary_payload)
            update_meta_from_summary(conn, run_name, config, summary_payload, started_utc)
            raise
        except Exception:
            summary_payload = summarize_from_artifacts(
                run_name,
                run_dir,
                config,
                "interrupted",
                previous_wall_sec + (time.monotonic() - run_start),
                downstream=None,
            )
            summary_payload["downstream"] = None
            write_json(run_dir / SUMMARY_NAME, summary_payload)
            update_meta_from_summary(conn, run_name, config, summary_payload, started_utc)
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the controlled 50-epoch SimCLR LR sweep.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--experiments", default=None, help="Comma-separated LR experiment names.")
    parser.add_argument("--output_root", default="experiments_output/lr_sweep_v1")
    parser.add_argument("--dry_run", action="store_true")
    parser.add_argument("--include_optional", action="store_true")
    parser.add_argument("--include_ablations", action="store_true")
    parser.add_argument("--dataset_dir", default=str(DEFAULT_DATASET_DIR))
    parser.add_argument("--pretrained_weights", default=str(DEFAULT_PRETRAINED_WEIGHTS))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--no_deterministic",
        action="store_true",
        help="Skip cuDNN deterministic flags. Default deterministic mode may be roughly 10-20% slower.",
    )
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch_size", type=int, default=None, help="Override registry physical batch size.")
    parser.add_argument("--min_batch_size", type=int, default=None, help="Override minimum OOM-retry batch size.")
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--max_runtime_hours", type=float, default=None)
    parser.add_argument("--retry_unstable", action="store_true")
    parser.add_argument("--separability_eval", action="store_true")
    parser.add_argument("--baseline_weights", default=None)
    parser.add_argument("--person_a_dir", default=None)
    parser.add_argument("--person_b_dir", default=None)
    parser.add_argument("--multi_identity_eval", action="store_true")
    parser.add_argument("--multi_identity_dataset_dir", default=None)
    parser.add_argument("--with_fiftyone", action="store_true")
    return parser


def print_dry_run(output_root: Path, protected_paths: list[Path], names: list[str], configs: dict[str, dict[str, Any]], args: argparse.Namespace) -> None:
    print("[DRY RUN] No files will be created or modified.")
    print(f"Output root: {output_root}")
    print("Protected paths checked:")
    for path in protected_paths:
        print(f"  - {path}")
    print("Schedule:")
    for run_name in names:
        print(f"\n[{run_name}]")
        print(json.dumps(configs[run_name], indent=2, default=str))
    if args.include_ablations:
        print("\nAblations will run after the LR sweep using the best completed LR.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_downstream_args(args)
        output_root, protected_paths = validate_output_root(Path(args.output_root))
        names = selected_experiment_names(args)
        configs = {name: build_config(LR_SWEEP[name], args) for name in names}
    except ValueError as exc:
        parser.error(str(exc))

    if args.dry_run:
        print_dry_run(output_root, protected_paths, names, configs, args)
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    conn = open_db(output_root / METRICS_DB_NAME)
    started_at = time.monotonic()
    try:
        run_schedule(names, configs, args, output_root, conn, started_at)
        if args.include_ablations:
            best_lr = completed_best_lr(output_root, names)
            if best_lr is None:
                print("[ABLATION] No completed LR sweep run found; ablations skipped.")
            else:
                ablation_names = list(ABLATIONS)
                ablation_configs = {}
                for ablation_name, overrides in ABLATIONS.items():
                    config = build_config({**FIXED, "lr": best_lr, **overrides}, args)
                    ablation_configs[ablation_name] = config
                run_schedule(ablation_names, ablation_configs, args, output_root, conn, started_at)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
