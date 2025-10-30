import os
import subprocess
from pathlib import Path

# ========== CONFIG ==========
IN_ROOT = r"F:\Room_8_Data\Sequence_Backup"
OUT_ROOT = r"F:\Room_8_Data\Exports"
CLEXPORT_PATH = r"C:\Program Files\NorPix\BatchProcessor\CLExport.exe"
# =============================

DEBUG = True  # set to False for quiet mode


def debug(msg: str):
    """Print debug messages when DEBUG=True."""
    if DEBUG:
        print(f"[DEBUG] {msg}")


def export_seq(seq_path: Path, out_file: Path, timeout_sec: int = 180) -> bool:
    """Export using MJPEG (AVI), safest CLExport option."""
    out_file = out_file.with_suffix(".avi")  # always use .avi
    out_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [CLEXPORT_PATH, "-i", str(seq_path), "-o", str(out_file), "-f", "mjpeg"]
    debug(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        print(f"⏰ Timeout on {seq_path.name}")
        return False
    except PermissionError:
        print(f"⚠️ Permission denied: {out_file} — trying to remove and retry...")
        try:
            os.remove(out_file)
            result = subprocess.run(cmd, text=True, timeout=timeout_sec)
        except Exception as e:
            print(f"❌ Still failed: {e}")
            return False

    if out_file.exists() and out_file.stat().st_size > 0:
        print(f"✅ Exported → {out_file}")
        return True
    else:
        print(f"❌ Export failed for {seq_path.name}")
        if result.returncode != 0:
            debug(f"Return code: {result.returncode}")
        return False






def main():
    in_root = Path(IN_ROOT)
    out_root = Path(OUT_ROOT)

    if not Path(CLEXPORT_PATH).exists():
        print(f"❌ CLExport.exe not found at:\n{CLEXPORT_PATH}")
        return

    if not in_root.exists():
        print(f"❌ Input folder not found:\n{in_root}")
        return

    seq_files = list(in_root.rglob("*.seq"))
    print(f"🔍 Found {len(seq_files)} .seq files under {in_root}\n")

    to_export = []
    for seq in seq_files:
        rel = seq.relative_to(in_root)
        out_file = out_root / rel.parent / (seq.stem + ".mp4")

        # Export only if no mp4 exists or file is empty
        if not out_file.exists() or out_file.stat().st_size == 0:
            to_export.append((seq, out_file))
        else:
            debug(f"Skipping {seq.name} — MP4 already exists at {out_file}")

    print(f"📦 Need to export {len(to_export)} files.\n")

    for seq, out_file in to_export:
        print(f"▶ Exporting: {seq}")
        export_seq(seq, out_file)

    print("\n✅ Done! All missing MP4s exported.")


if __name__ == "__main__":
    main()
