"""Generate final reports for SimCLR experiment sweeps."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPORT_COLUMNS = [
    "run_name",
    "seed",
    "lr",
    "temperature",
    "projection_dim",
    "color_jitter_strength",
    "best_val_loss",
    "best_epoch",
    "final_train_loss",
    "final_val_loss",
    "train_val_gap",
    "separability_gap",
    "inter_similarity",
    "rank1",
    "map_score",
    "completed_epochs",
    "status",
    "unstable_reason",
    "wall_clock_sec",
]


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return None


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


def open_readonly_db(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def db_rows(conn: sqlite3.Connection | None) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    meta: dict[str, dict[str, Any]] = {}
    metrics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if conn is None:
        return meta, metrics

    conn.row_factory = sqlite3.Row
    for row in conn.execute("SELECT * FROM run_meta"):
        item = dict(row)
        if item.get("config_json"):
            try:
                item["config"] = json.loads(item["config_json"])
            except json.JSONDecodeError:
                item["config"] = {}
        meta[item["run_name"]] = item

    for row in conn.execute(
        """
        SELECT run_name, epoch, train_loss, val_loss, learning_rate AS lr
        FROM epoch_metrics
        ORDER BY run_name, epoch
        """
    ):
        metrics[row["run_name"]].append(
            {
                "epoch": row["epoch"],
                "train_loss": row["train_loss"],
                "val_loss": row["val_loss"],
                "lr": row["lr"],
            }
        )
    return meta, metrics


def collect_runs(output_root: Path) -> list[dict[str, Any]]:
    conn = open_readonly_db(output_root / "experiment_metrics.sqlite")
    try:
        meta_by_run, metrics_by_run = db_rows(conn)
    finally:
        if conn is not None:
            conn.close()

    run_names = set(meta_by_run) | set(metrics_by_run)
    if output_root.exists():
        for child in output_root.iterdir():
            if child.is_dir() and (
                (child / "summary.json").exists()
                or (child / "metrics.csv").exists()
                or (child / "config.json").exists()
            ):
                run_names.add(child.name)

    runs: list[dict[str, Any]] = []
    for run_name in sorted(run_names):
        run_dir = output_root / run_name
        summary = read_json(run_dir / "summary.json") or {}
        config = (meta_by_run.get(run_name) or {}).get("config") or read_json(run_dir / "config.json") or summary.get("config") or {}
        metrics = metrics_by_run.get(run_name) or read_metrics_csv(run_dir / "metrics.csv")
        meta = meta_by_run.get(run_name, {})
        downstream = summary.get("downstream") or {}

        best_epoch = meta.get("best_epoch") or summary.get("best_epoch")
        best_val_loss = meta.get("best_val_loss") or summary.get("best_val_loss")
        completed_epochs = meta.get("completed_epochs") or summary.get("completed_epochs")
        final_train_loss = summary.get("final_train_loss")
        final_val_loss = summary.get("final_val_loss")

        if metrics:
            finite_val_rows = [row for row in metrics if row.get("val_loss") is not None]
            if finite_val_rows and best_val_loss is None:
                best = min(finite_val_rows, key=lambda row: row["val_loss"])
                best_epoch = best["epoch"]
                best_val_loss = best["val_loss"]
            last = metrics[-1]
            final_train_loss = final_train_loss if final_train_loss is not None else last.get("train_loss")
            final_val_loss = final_val_loss if final_val_loss is not None else last.get("val_loss")
            completed_epochs = completed_epochs if completed_epochs is not None else len(metrics)
        else:
            completed_epochs = completed_epochs if completed_epochs is not None else 0

        status = meta.get("status") or summary.get("status") or "unknown"
        unstable_reason = meta.get("unstable_reason") or summary.get("unstable_reason")
        train_val_gap = None
        if final_train_loss is not None and final_val_loss is not None:
            train_val_gap = final_val_loss - final_train_loss

        runs.append(
            {
                "run_name": run_name,
                "run_dir": run_dir,
                "config": config,
                "metrics": metrics,
                "seed": meta.get("seed") or summary.get("seed") or config.get("seed"),
                "lr": config.get("lr"),
                "temperature": config.get("temperature"),
                "projection_dim": config.get("projection_dim"),
                "color_jitter_strength": config.get("color_jitter_strength"),
                "best_val_loss": best_val_loss,
                "best_epoch": best_epoch,
                "final_train_loss": final_train_loss,
                "final_val_loss": final_val_loss,
                "train_val_gap": train_val_gap,
                "separability_gap": downstream.get("separability_gap"),
                "inter_similarity": downstream.get("inter_similarity"),
                "rank1": downstream.get("rank1"),
                "map_score": downstream.get("map_score"),
                "completed_epochs": completed_epochs,
                "status": status,
                "unstable_reason": unstable_reason,
                "wall_clock_sec": summary.get("wall_clock_sec"),
            }
        )
    return runs


def write_table(output_root: Path, runs: list[dict[str, Any]]) -> None:
    with (output_root / "report_table.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for run in runs:
            writer.writerow({column: "" if run.get(column) is None else run.get(column) for column in REPORT_COLUMNS})


def import_pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def no_data_plot(path: Path, title: str) -> None:
    try:
        plt = import_pyplot()
    except Exception:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=16)
    ax.set_axis_off()
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def color_for_lr(lr: Any, palette: dict[Any, Any], plt) -> Any:
    if lr not in palette:
        cmap = plt.get_cmap("tab10")
        palette[lr] = cmap(len(palette) % 10)
    return palette[lr]


def write_curves_plot(output_root: Path, runs: list[dict[str, Any]]) -> None:
    path = output_root / "report_curves.png"
    curve_runs = [run for run in runs if run["metrics"]]
    if not curve_runs:
        no_data_plot(path, "Train and validation curves")
        return
    try:
        plt = import_pyplot()
    except Exception:
        return
    fig, ax = plt.subplots(figsize=(11, 6))
    palette = {}
    for run in curve_runs:
        metrics = run["metrics"]
        epochs = [row["epoch"] for row in metrics]
        color = color_for_lr(run.get("lr"), palette, plt)
        style = "--" if run.get("status") == "unstable" else "-"
        prefix = "[unstable] " if run.get("status") == "unstable" else ""
        label = f"{prefix}{run['run_name']} val"
        ax.plot(epochs, [row["val_loss"] for row in metrics], linestyle=style, color=color, label=label)
        ax.plot(epochs, [row["train_loss"] for row in metrics], linestyle=":", color=color, alpha=0.55)
    ax.set_xlabel("Epoch")
    ax.set_ylabel("NT-Xent loss")
    ax.set_title("Train and validation curves")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def write_overfitting_plot(output_root: Path, runs: list[dict[str, Any]]) -> None:
    path = output_root / "report_overfitting.png"
    curve_runs = [run for run in runs if run["metrics"]]
    if not curve_runs:
        no_data_plot(path, "Train/validation gap")
        return
    try:
        plt = import_pyplot()
    except Exception:
        return
    fig, ax = plt.subplots(figsize=(11, 5))
    palette = {}
    for run in curve_runs:
        metrics = run["metrics"]
        epochs = [row["epoch"] for row in metrics]
        gaps = [row["val_loss"] - row["train_loss"] for row in metrics]
        color = color_for_lr(run.get("lr"), palette, plt)
        style = "--" if run.get("status") == "unstable" else "-"
        prefix = "[unstable] " if run.get("status") == "unstable" else ""
        ax.plot(epochs, gaps, linestyle=style, color=color, label=f"{prefix}{run['run_name']}")
    ax.axhline(0, color="black", linewidth=1, alpha=0.4)
    ax.set_xlabel("Epoch")
    ax.set_ylabel("Val loss - train loss")
    ax.set_title("Train/validation gap")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def write_lr_scatter(output_root: Path, runs: list[dict[str, Any]]) -> bool:
    path = output_root / "report_lr_vs_val.png"
    completed = [run for run in runs if run.get("status") == "completed" and run.get("best_val_loss") is not None]
    if not completed:
        no_data_plot(path, "LR vs best validation loss")
        return False
    try:
        plt = import_pyplot()
    except Exception:
        return False
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.scatter([run["lr"] for run in completed], [run["best_val_loss"] for run in completed], label="best val loss")
    for run in completed:
        ax.annotate(run["run_name"], (run["lr"], run["best_val_loss"]), fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("Learning rate")
    ax.set_ylabel("Best validation NT-Xent")
    ax.grid(True, alpha=0.25)
    has_separability = any(run.get("separability_gap") is not None for run in completed)
    if has_separability:
        ax2 = ax.twinx()
        sep_runs = [run for run in completed if run.get("separability_gap") is not None]
        ax2.scatter(
            [run["lr"] for run in sep_runs],
            [run["separability_gap"] for run in sep_runs],
            marker="x",
            color="tab:orange",
            label="separability gap",
        )
        ax2.set_ylabel("Separability gap")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return has_separability


def completed_ranked(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            run
            for run in runs
            if run.get("status") == "completed" and run.get("best_val_loss") is not None
        ],
        key=lambda run: run["best_val_loss"],
    )


def answer_lr_questions(runs: list[dict[str, Any]]) -> tuple[str, str]:
    ranked = completed_ranked(runs)
    if not ranked:
        return (
            "Insufficient data: no completed runs are available.",
            "Insufficient data: no completed runs are available.",
        )
    best = ranked[0]
    high_lrs = [run for run in ranked if run.get("lr") is not None and run["lr"] >= 8e-4]
    high_best = min(high_lrs, key=lambda run: run["best_val_loss"]) if high_lrs else None
    high_answer = (
        f"Yes: the best completed run was {best['run_name']} at lr={best['lr']}."
        if high_best and best["run_name"] == high_best["run_name"]
        else f"No: the best completed run was {best['run_name']} at lr={best['lr']}."
    )
    lower = [run for run in ranked if run.get("lr") is not None and run["lr"] < 8e-4]
    if not lower:
        lower_answer = "Insufficient data: no completed lower-LR runs are available."
    else:
        best_lower = min(lower, key=lambda run: run["best_val_loss"])
        lower_answer = (
            f"Yes: {best_lower['run_name']} overtook the higher-LR runs."
            if best_lower["run_name"] == best["run_name"]
            else f"No: the best lower-LR run was {best_lower['run_name']}, behind {best['run_name']}."
        )
    return high_answer, lower_answer


def overfit_answer(runs: list[dict[str, Any]]) -> str:
    candidates = [run for run in runs if run.get("metrics") and run.get("status") == "completed"]
    if not candidates:
        return "Insufficient data: no completed loss curves are available."
    flagged = []
    for run in candidates:
        metrics = run["metrics"]
        if len(metrics) < 4:
            continue
        first_gap = metrics[0]["val_loss"] - metrics[0]["train_loss"]
        final_gap = metrics[-1]["val_loss"] - metrics[-1]["train_loss"]
        train_delta = metrics[-1]["train_loss"] - metrics[max(0, len(metrics) // 2)]["train_loss"]
        val_delta = metrics[-1]["val_loss"] - metrics[max(0, len(metrics) // 2)]["val_loss"]
        if final_gap > first_gap + 0.05 and train_delta < 0 and val_delta > 0:
            flagged.append(run["run_name"])
    if flagged:
        return "Possible overfitting in: " + ", ".join(flagged) + "."
    return "No clear overfitting signal from the completed train/validation curves."


def downstream_answer(runs: list[dict[str, Any]]) -> str:
    with_downstream = [
        run
        for run in runs
        if run.get("separability_gap") is not None or run.get("rank1") is not None or run.get("map_score") is not None
    ]
    if not with_downstream:
        return "Insufficient data: no downstream metrics were collected, so NT-Xent cannot be compared to Re-ID performance."
    ranked_ntxent = completed_ranked(with_downstream)
    if not ranked_ntxent:
        return "Insufficient data: downstream metrics exist, but no completed NT-Xent-ranked runs are available."
    best_ntxent = ranked_ntxent[0]
    best_sep = max(with_downstream, key=lambda run: run.get("separability_gap") if run.get("separability_gap") is not None else float("-inf"))
    if best_sep["run_name"] == best_ntxent["run_name"]:
        return f"Yes on collected separability data: {best_ntxent['run_name']} was best on both NT-Xent and separability gap."
    return f"No on collected separability data: best NT-Xent was {best_ntxent['run_name']}, while best separability was {best_sep['run_name']}."


def fixed_hparams_answer(runs: list[dict[str, Any]]) -> str:
    ranked = completed_ranked(runs)
    if not ranked:
        return "Insufficient data: keep only the fixed experiment controls for now."
    best = ranked[0]
    return (
        f"Fix seed={best.get('seed')}, split policy, NT-Xent training loss, freeze_early_layers=False, "
        f"projection_dim={best.get('projection_dim')}, temperature={best.get('temperature')}, "
        f"color_jitter_strength={best.get('color_jitter_strength')}, and lr={best.get('lr')} for the next controlled pass."
    )


def ablation_answer(runs: list[dict[str, Any]]) -> str:
    completed_names = {run["run_name"] for run in runs if run.get("status") == "completed"}
    expected = {"proj_64", "proj_128", "temp_0.05", "temp_0.10", "jitter_0.35", "jitter_0.60", "staged_unfreeze"}
    missing = sorted(expected - completed_names)
    if not missing:
        return "The planned ablations all have completed results."
    return "Still requires ablation: " + ", ".join(missing) + "."


def stability_summary(runs: list[dict[str, Any]]) -> str:
    counts = Counter(run.get("status", "unknown") for run in runs)
    lines = [
        f"- Completed: {counts.get('completed', 0)}",
        f"- Unstable: {counts.get('unstable', 0)}",
        f"- Interrupted: {counts.get('interrupted', 0)}",
    ]
    unstable = [run for run in runs if run.get("status") == "unstable"]
    for run in unstable:
        lines.append(f"- {run['run_name']}: {run.get('unstable_reason') or 'unknown'}")
    unstable_lrs = [run for run in unstable if run.get("lr") is not None]
    if unstable_lrs:
        high = [run for run in unstable_lrs if run["lr"] >= 1.5e-3]
        if high:
            lines.append("- Recommendation: remove unstable high-LR configs from future sweeps unless there is a driver/config fix to justify --retry_unstable.")
        else:
            lines.append("- Recommendation: retry unstable LR configs once with a lower LR or smaller batch before removing them.")
    return "\n".join(lines)


def write_markdown(output_root: Path, runs: list[dict[str, Any]], has_separability_scatter: bool) -> None:
    high_answer, lower_answer = answer_lr_questions(runs)
    ranked = completed_ranked(runs)
    best_line = "No completed runs."
    if ranked:
        best = ranked[0]
        best_line = f"Best completed run: {best['run_name']} (lr={best.get('lr')}, best_val_loss={best.get('best_val_loss')})."

    lines = [
        "# SimCLR LR Sweep Report",
        "",
        best_line,
        "",
        "## Stability summary",
        stability_summary(runs),
        "",
        "## Questions",
        f"1. Did high LR remain best at 50 epochs? {high_answer}",
        f"2. Did lower LR catch up or overtake? {lower_answer}",
        f"3. Did any configuration overfit? {overfit_answer(runs)}",
        f"4. Did best NT-Xent also mean best Re-ID performance? {downstream_answer(runs)}",
        f"5. Which hyperparameters can now be fixed? {fixed_hparams_answer(runs)}",
        f"6. Which hyperparameters still require ablation? {ablation_answer(runs)}",
        "",
        "## Outputs",
        "- `report_table.csv` contains the run summary table.",
        "- `report_curves.png` overlays train and validation curves; unstable runs are dashed.",
        "- `report_overfitting.png` plots validation-training gap by epoch.",
        "- `report_lr_vs_val.png` plots LR vs best validation loss.",
    ]
    if not has_separability_scatter:
        lines.append("- No non-null `separability_gap` values were found, so the LR-vs-separability scatter was omitted.")
    if not runs:
        lines.extend(["", "No runs were found under this output root."])

    (output_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate SimCLR sweep reports.")
    parser.add_argument("--output_root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = Path(args.output_root).expanduser().resolve(strict=False)
    output_root.mkdir(parents=True, exist_ok=True)
    runs = collect_runs(output_root)
    write_table(output_root, runs)
    write_curves_plot(output_root, runs)
    write_overfitting_plot(output_root, runs)
    has_separability_scatter = write_lr_scatter(output_root, runs)
    write_markdown(output_root, runs, has_separability_scatter)
    print(f"[Report] Wrote report to {output_root / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

