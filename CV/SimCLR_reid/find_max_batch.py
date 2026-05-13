"""Probe the largest physical SimCLR batch this GPU can run."""

import argparse
import gc
import math
from pathlib import Path

import torch

from train_simclr import DEFAULT_CONFIG, NTXentLoss, SimCLRModel


def can_fit(batch_size, image_size, device, weights_path):
    """Return (fits, peak_allocated_gb, peak_reserved_gb) for a CUDA stress test."""
    torch.cuda.empty_cache()
    gc.collect()

    try:
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()

        model = SimCLRModel(
            backbone_name=DEFAULT_CONFIG["backbone"],
            pretrained_weights=weights_path,
            projection_dim=DEFAULT_CONFIG["projection_dim"],
            projection_hidden=DEFAULT_CONFIG["projection_hidden"],
            freeze_layers=DEFAULT_CONFIG["freeze_layers"],
            allow_downloads=False,
        ).to(device)
        criterion = NTXentLoss(temperature=DEFAULT_CONFIG["temperature"]).to(device)
        scaler = torch.amp.GradScaler(
            "cuda",
            enabled=device.type == "cuda",
            init_scale=1024.0,
            growth_interval=1000,
        )
        optimizer = torch.optim.AdamW(
            [p for p in model.parameters() if p.requires_grad],
            lr=1e-4,
        )

        height, width = image_size
        x1 = torch.randn(batch_size, 3, height, width, device=device)
        x2 = torch.randn(batch_size, 3, height, width, device=device)

        for _ in range(3):
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast(device_type="cuda", enabled=device.type == "cuda"):
                z1 = model(x1)
                z2 = model(x2)
                loss = criterion(z1, z2)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

        torch.cuda.synchronize()
        peak_allocated = torch.cuda.max_memory_allocated() / 1024**3
        peak_reserved = torch.cuda.max_memory_reserved() / 1024**3

        del model, criterion, optimizer, scaler, x1, x2, z1, z2, loss
        torch.cuda.empty_cache()
        gc.collect()
        torch.cuda.reset_peak_memory_stats()
        return True, peak_allocated, peak_reserved

    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        gc.collect()
        return False, None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pretrained_weights",
        default=r"F:\Projects\ScalpelLab\CV\osnet_ain_x1_0_msmt17.pt",
    )
    parser.add_argument(
        "--no_pretrained_weights",
        action="store_true",
        help="Probe with random OSNet initialization and do not load or download pretrained weights.",
    )
    parser.add_argument("--image_size", nargs=2, type=int, default=[256, 128])
    parser.add_argument("--target_effective_batch", type=int, default=128)
    parser.add_argument(
        "--headroom_fraction",
        type=float,
        default=0.90,
        help=(
            "Recommend the largest OK batch whose peak allocated and peak reserved "
            "CUDA memory stay under this fraction of dedicated VRAM."
        ),
    )
    parser.add_argument(
        "--allow_shared_gpu_memory",
        action="store_true",
        help=(
            "Allow recommendations above dedicated VRAM on Windows/WDDM. "
            "The recommended batch is reduced by --batch_headroom_fraction from the largest OK batch."
        ),
    )
    parser.add_argument(
        "--batch_headroom_fraction",
        type=float,
        default=0.90,
        help="When --allow_shared_gpu_memory is set, recommend this fraction of the largest OK batch.",
    )
    parser.add_argument(
        "--round_to",
        type=int,
        default=32,
        help="Round the recommended batch down to this multiple.",
    )
    parser.add_argument(
        "--candidates",
        nargs="+",
        type=int,
        default=[128, 256, 384, 512, 640, 704, 768, 896, 1024, 1088, 1152, 1216, 1248, 1280],
    )
    args = parser.parse_args()
    if args.no_pretrained_weights:
        args.pretrained_weights = None
    elif args.pretrained_weights and not Path(args.pretrained_weights).exists():
        raise FileNotFoundError(
            "Pretrained weights were requested but not found. "
            f"Offline probing will not download them automatically: {args.pretrained_weights}"
        )

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the max-batch probe.")

    device = torch.device("cuda")
    props = torch.cuda.get_device_properties(0)
    total_gb = props.total_memory / 1024**3
    safe_peak_gb = total_gb * args.headroom_fraction
    print(f"GPU: {torch.cuda.get_device_name(0)} ({total_gb:.2f} GB dedicated VRAM)")
    if args.allow_shared_gpu_memory:
        print(
            "Recommendation target: shared GPU memory allowed; "
            f"recommend {args.batch_headroom_fraction:.0%} of the largest OK batch"
        )
    else:
        print(
            f"Recommendation target: peak allocated and reserved <= "
            f"{safe_peak_gb:.2f} GB ({args.headroom_fraction:.0%})"
        )

    last_ok = None
    last_peak = None
    last_reserved = None
    recommended = None
    recommended_peak = None
    recommended_reserved = None
    for batch_size in args.candidates:
        ok, peak, reserved = can_fit(batch_size, args.image_size, device, args.pretrained_weights)
        if ok:
            dedicated_pct = 100 * peak / total_gb
            status = "SAFE" if peak <= safe_peak_gb and reserved <= safe_peak_gb else "TIGHT"
            print(
                f"  bs={batch_size:4d}  OK/{status:<5} "
                f"peak_alloc={peak:.2f} GB ({dedicated_pct:.0f}%) "
                f"peak_reserved={reserved:.2f} GB"
            )
            last_ok, last_peak, last_reserved = batch_size, peak, reserved
            if peak <= safe_peak_gb and reserved <= safe_peak_gb:
                recommended = batch_size
                recommended_peak = peak
                recommended_reserved = reserved
        else:
            print(f"  bs={batch_size:4d}  OOM")
            break

    if last_ok is None:
        print("FAILED: even the smallest candidate batch OOMs.")
        return

    if args.allow_shared_gpu_memory:
        recommended = max(1, int(last_ok * args.batch_headroom_fraction))
        if args.round_to > 1:
            recommended = max(args.round_to, (recommended // args.round_to) * args.round_to)
        recommended_peak = last_peak
        recommended_reserved = last_reserved
    elif recommended is None:
        recommended = max(1, int(last_ok * 0.9))
        if args.round_to > 1:
            recommended = max(args.round_to, (recommended // args.round_to) * args.round_to)
        recommended_peak = last_peak
        recommended_reserved = last_reserved
        print(
            "\n[WARNING] No tested batch kept both allocated and reserved memory under the requested VRAM headroom; "
            "falling back to a 10% batch-size reduction from the largest OK candidate."
        )
    if last_peak and last_peak > total_gb and not args.allow_shared_gpu_memory:
        print(
            "\n[WARNING] The largest OK batch exceeded dedicated VRAM as reported by PyTorch. "
            "That can spill into shared GPU memory on Windows and is not recommended for unattended training."
        )
    elif last_peak and last_peak > total_gb:
        print("\n[Shared GPU memory] Largest OK batch intentionally exceeded dedicated VRAM.")

    accumulation_steps = math.ceil(args.target_effective_batch / recommended)
    print(f"\nMax tested OK physical batch: {last_ok} (peak_alloc {last_peak:.2f} GB)")
    print(
        f"Recommended physical batch: {recommended} "
        f"(largest OK peak_alloc {recommended_peak:.2f} GB, peak_reserved {recommended_reserved:.2f} GB)"
    )
    print(f"For target effective batch={args.target_effective_batch}:")
    print(f"  accumulation_steps = ceil({args.target_effective_batch}/{recommended}) = {accumulation_steps}")
    print(f"  effective batch    = {recommended * accumulation_steps}")
    print(f"\nUse: --max_physical_batch {recommended} --accumulation_steps {accumulation_steps}")


if __name__ == "__main__":
    main()
