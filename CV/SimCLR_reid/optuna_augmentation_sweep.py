"""
optuna_augmentation_sweep.py - Augmentation-only Optuna sweep for SimCLR ReID.

Heavy artifacts are intentionally written outside the repository by default:

    F:/Room_8_Data/SIMCLR/experiments/3_AUGMENTATION_OPTUNA

The sweep keeps the known-good training recipe fixed and searches only the
augmentation policy that controls background shortcut pressure versus
identity-cue preservation.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:
    import optuna
except ImportError:
    optuna = None

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_OUTPUT_ROOT = Path("F:/Room_8_Data/SIMCLR/experiments/3_AUGMENTATION_OPTUNA")
DEFAULT_DATASET_DIR = Path("F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned")
DEFAULT_BASELINE_WEIGHTS = Path("F:/Projects/ScalpelLab/CV/osnet_ain_x1_0_msmt17.pt")
STUDY_NAME = "simclr_augmentation_sweep"


def load_train_api():
    try:
        from train_simclr import DEFAULT_CONFIG, run_training
    except ImportError as exc:
        raise RuntimeError(
            "Could not import train_simclr.py and its ML dependencies. "
            "Install project dependencies with: pip install -r requirements.txt"
        ) from exc
    return DEFAULT_CONFIG, run_training


class MaximizeWithValLossPruningTrial:
    """
    Adapter for downstream-top1 studies.

    The final objective is maximized, but per-epoch pruning is still based on
    lower NT-Xent validation loss. Reporting -val_loss preserves that ordering
    for Optuna's maximize-direction pruner while the trainer still calls
    report(val_loss, epoch).
    """

    def __init__(self, trial: optuna.Trial):
        self._trial = trial

    def report(self, value: float, step: int) -> None:
        self._trial.report(-float(value), step)

    def should_prune(self) -> bool:
        return self._trial.should_prune()


def normalize(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def is_within(child: Path, parent: Path) -> bool:
    child_text = str(normalize(child)).lower()
    parent_text = str(normalize(parent)).lower()
    return child_text == parent_text or child_text.startswith(parent_text.rstrip("\\/") + "\\")


def validate_external_output_root(output_root: Path) -> Path:
    output_root = normalize(output_root)
    if is_within(output_root, REPO_ROOT):
        raise ValueError(
            "Refusing to write SimCLR checkpoints inside the repository. "
            f"Use an external --output_root; got {output_root}"
        )
    return output_root


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, default=str)


def build_trial_config(trial: optuna.Trial, args: argparse.Namespace, default_config: dict[str, Any]) -> dict[str, Any]:
    config = default_config.copy()

    # Keep model/training fixed so this study measures augmentation policy only.
    config.update(
        {
            "epochs": int(args.epochs),
            "lr": float(args.lr),
            "seed": int(args.seed),
            "deterministic": not args.no_deterministic,
            "batch_size": int(args.batch_size),
            "min_batch_size": int(args.min_batch_size),
            "auto_reduce_batch_on_oom": True,
            "pin_memory": bool(args.pin_memory),
            "accumulation_steps": int(args.accumulation_steps),
            "projection_dim": 256,
            "temperature": 0.07,
            "weight_decay": 1e-4,
            "freeze_early_layers": False,
            "freeze_layers": [],
            "pretrained_weights": args.pretrained_weights,
        }
    )

    # Spatial crop and erasing are the main defenses against memorizing OR
    # equipment, walls, beds, and camera-specific background layout.
    config["crop_scale_min"] = trial.suggest_float("crop_scale_min", 0.40, 0.75)
    config["random_erasing_prob"] = trial.suggest_float("random_erasing_prob", 0.20, 0.70)
    config["random_erasing_scale_max"] = trial.suggest_float("random_erasing_scale_max", 0.20, 0.45)

    # Photometric transforms should break lighting/camera shortcuts without
    # completely deleting clothing/PPE color cues that matter for ReID.
    config["color_jitter_strength"] = trial.suggest_float("color_jitter_strength", 0.15, 0.65)
    config["grayscale_prob"] = trial.suggest_float("grayscale_prob", 0.00, 0.25)
    config["gaussian_blur_prob"] = trial.suggest_float("gaussian_blur_prob", 0.20, 1.00)
    config["solarization_prob"] = trial.suggest_float("solarization_prob", 0.00, 0.15)
    return config


def downstream_top1(run_dir: Path, args: argparse.Namespace) -> float:
    from validate_reid_multi_identity import run_validation

    backbone_path = run_dir / "best_backbone.pt"
    if not backbone_path.exists():
        raise FileNotFoundError(f"Cannot run downstream validation; missing {backbone_path}")

    validation_run_dir = run_validation(
        SimpleNamespace(
            validation_dir=args.validation_dir,
            baseline_weights=args.baseline_weights,
            finetuned_weights=str(backbone_path),
            output_dir=str(run_dir / "downstream_reid"),
            device=args.device,
            batch_size=args.eval_batch_size,
        )
    )
    summary = read_json(Path(validation_run_dir) / "validation_summary.json") or {}
    score = ((summary.get("metrics") or {}).get("fine_tuned") or {}).get("top1_retrieval_accuracy")
    if score is None:
        raise RuntimeError(f"Downstream validation did not produce top1_retrieval_accuracy in {validation_run_dir}")
    return float(score)


def objective(trial: optuna.Trial, args: argparse.Namespace, default_config, run_training_fn) -> float:
    config = build_trial_config(trial, args, default_config)
    run_name = f"trial_{trial.number:04d}"
    run_dir = Path(args.output_root) / run_name

    pruning_trial = trial
    if args.objective_metric == "downstream_top1":
        pruning_trial = MaximizeWithValLossPruningTrial(trial)

    best_val_loss = run_training_fn(
        config,
        args.dataset_dir,
        args.output_root,
        run_name=run_name,
        trial=pruning_trial,
        patience=args.patience,
    )

    trial.set_user_attr("best_val_loss", float(best_val_loss))
    trial.set_user_attr("run_dir", str(run_dir.resolve()))

    if args.objective_metric == "downstream_top1":
        top1 = downstream_top1(run_dir, args)
        trial.set_user_attr("downstream_top1", top1)
        return top1
    return float(best_val_loss)


def make_storage(output_root: Path) -> optuna.storages.RDBStorage:
    storage_url = f"sqlite:///{(output_root / 'optuna_study.db').as_posix()}"
    return optuna.storages.RDBStorage(
        url=storage_url,
        engine_kwargs={"connect_args": {"timeout": 120}},
    )


def trial_to_summary(trial: optuna.trial.FrozenTrial) -> dict[str, Any]:
    return {
        "number": trial.number,
        "state": trial.state.name,
        "value": trial.value,
        "params": dict(trial.params),
        "user_attrs": dict(trial.user_attrs),
        "datetime_start": trial.datetime_start,
        "datetime_complete": trial.datetime_complete,
    }


def write_study_summaries(study: optuna.Study, output_root: Path, args: argparse.Namespace) -> None:
    complete_trials = [trial for trial in study.trials if trial.state == optuna.trial.TrialState.COMPLETE]
    payload = {
        "study_name": study.study_name,
        "objective_metric": args.objective_metric,
        "direction": study.direction.name,
        "created_or_updated": datetime.now().isoformat(timespec="seconds"),
        "output_root": str(output_root.resolve()),
        "n_trials": len(study.trials),
        "n_complete": len(complete_trials),
        "trials": [trial_to_summary(trial) for trial in study.trials],
    }
    if complete_trials:
        payload["best_trial"] = trial_to_summary(study.best_trial)
        write_json(
            output_root / "best_params.json",
            {
                "study_name": study.study_name,
                "objective_metric": args.objective_metric,
                "best_value": study.best_value,
                "best_trial_number": study.best_trial.number,
                "best_params": study.best_params,
                "best_user_attrs": dict(study.best_trial.user_attrs),
            },
        )
    write_json(output_root / "study_summary.json", payload)


def copy_lightweight_report(output_root: Path) -> None:
    repo_report_dir = SCRIPT_DIR / "3_AUGMENTATION_OPTUNA"
    repo_report_dir.mkdir(parents=True, exist_ok=True)
    for name in ["study_summary.json", "best_params.json", "optuna_study.db"]:
        src = output_root / name
        if src.exists():
            shutil.copy2(src, repo_report_dir / name)


def validate_args(args: argparse.Namespace) -> None:
    args.output_root = str(validate_external_output_root(Path(args.output_root)))
    if args.objective_metric == "downstream_top1":
        missing = [
            name
            for name in ["validation_dir", "baseline_weights"]
            if not getattr(args, name)
        ]
        if missing:
            raise ValueError("--objective_metric downstream_top1 requires " + ", ".join(f"--{name}" for name in missing))
        if not Path(args.validation_dir).exists():
            raise FileNotFoundError(f"--validation_dir not found: {args.validation_dir}")
        if not Path(args.baseline_weights).exists():
            raise FileNotFoundError(f"--baseline_weights not found: {args.baseline_weights}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optuna augmentation policy sweep for burst-aware SimCLR ReID.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dataset_dir", default=str(DEFAULT_DATASET_DIR))
    parser.add_argument("--output_root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--study_name", default=STUDY_NAME)
    parser.add_argument("--objective_metric", choices=["val_loss", "downstream_top1"], default="val_loss")
    parser.add_argument("--n_trials", type=int, default=40)
    parser.add_argument("--timeout", type=int, default=604800)
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--lr", type=float, default=1.5e-3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch_size", type=int, default=128)
    parser.add_argument("--min_batch_size", type=int, default=64)
    parser.add_argument("--accumulation_steps", type=int, default=1)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--pin_memory", action="store_true")
    parser.add_argument("--no_deterministic", action="store_true")
    parser.add_argument("--pretrained_weights", default=str(DEFAULT_BASELINE_WEIGHTS))
    parser.add_argument("--validation_dir", default=None)
    parser.add_argument("--baseline_weights", default=str(DEFAULT_BASELINE_WEIGHTS))
    parser.add_argument("--device", default=None)
    parser.add_argument("--eval_batch_size", type=int, default=64)
    parser.add_argument("--copy_repo_report", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        if optuna is None:
            raise RuntimeError("optuna is required. Install project dependencies with: pip install -r requirements.txt")
        default_config, run_training_fn = load_train_api()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    direction = "maximize" if args.objective_metric == "downstream_top1" else "minimize"
    study = optuna.create_study(
        study_name=args.study_name,
        storage=make_storage(output_root),
        direction=direction,
        load_if_exists=True,
        sampler=optuna.samplers.TPESampler(seed=args.seed),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=3, n_warmup_steps=3, interval_steps=1),
    )

    print(f"[Optuna] study={args.study_name} direction={direction}")
    print(f"[Optuna] output_root={output_root.resolve()}")
    study.optimize(
        lambda trial: objective(trial, args, default_config, run_training_fn),
        n_trials=args.n_trials,
        timeout=args.timeout,
        gc_after_trial=True,
        catch=(RuntimeError, OSError, ValueError),
        callbacks=[lambda study, trial: write_study_summaries(study, output_root, args)],
    )
    write_study_summaries(study, output_root, args)
    if args.copy_repo_report:
        copy_lightweight_report(output_root)

    if study.trials:
        print(f"[Optuna] Trials: {len(study.trials)}")
    try:
        print(f"[Optuna] Best trial #{study.best_trial.number}: value={study.best_value:.6f}")
        print(f"[Optuna] Best params: {json.dumps(study.best_params, indent=2)}")
    except ValueError:
        print("[Optuna] No completed trials yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
