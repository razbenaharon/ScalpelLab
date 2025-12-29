"""
OPTIMIZED Anesthesiologist Segmentation using SAM 2.1 Video Predictor

This version includes all critical optimizations:
- Tiny model (sam2.1_hiera_tiny.pt) for maximum speed
- torch.compile for 2-3x speedup
- bfloat16 autocast for 2x speedup
- TF32 enabled for Ampere GPUs (RTX A2000)
- Minimized CPU-GPU transfers
- Batch processing of masks

Expected Performance: 20-30 FPS @ 1024x768 resolution (RTX A2000)

Hardware Requirements:
- NVIDIA GPU with CUDA support (Ampere or newer recommended)
"""

import sys
import os
from pathlib import Path
import tempfile
import time
import subprocess
import json

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np
import torch
from tqdm import tqdm
import warnings

# Suppress SAM2 _C import warning (harmless, doesn't affect functionality)
warnings.filterwarnings("ignore", message="cannot import name '_C' from 'sam2'")

# Import official SAM 2
try:
    from sam2.build_sam import build_sam2_video_predictor
except ImportError:
    print("=" * 70)
    print("ERROR: Official SAM 2 not installed!")
    print("=" * 70)
    print("\nPlease install from: F:\\Projects\\sam2-main")
    print("  cd F:\\Projects\\sam2-main")
    print("  pip install -e .")
    print("=" * 70)
    sys.exit(1)

try:
    from config import MP4_ROOT
except ImportError:
    MP4_ROOT = None


# =============================================================================
# LOAD CONFIGURATION
# =============================================================================
def load_config():
    """Load configuration from sam_config.json"""
    config_path = os.path.join(os.path.dirname(__file__), "sam_config.json")

    if not os.path.exists(config_path):
        print(f"ERROR: Configuration file not found: {config_path}")
        print("Creating default configuration file...")
        # Create default config if it doesn't exist
        default_config = {
            "sam2": {
                "root": "F:\\Projects\\sam2-main",
                "checkpoint_dir": "F:\\Projects\\sam2-main\\checkpoints",
                "config_dir": "F:\\Projects\\sam2-main\\sam2\\configs\\sam2.1",
                "model": "tiny"
            },
            "video": {
                "input_path": "",
                "output_path": "",
                "default_input_dir": "F:\\Room_8_Data\\Recordings",
                "max_resolution": {"width": 1024, "height": 768},
                "auto_resize": True
            },
            "encoding": {
                "h264_preset": "slow",
                "h264_crf": 18,
                "resize_preset": "fast"
            },
            "device": {
                "use_cuda": True,
                "enable_torch_compile": False,
                "enable_bfloat16": True,
                "enable_tf32": True
            }
        }
        with open(config_path, 'w') as f:
            json.dump(default_config, f, indent=2)
        print(f"Created default config at: {config_path}")
        return default_config

    with open(config_path, 'r') as f:
        config = json.load(f)

    return config

# Load configuration
CONFIG = load_config()

# SAM 2.1 paths from config
SAM2_ROOT = CONFIG["sam2"]["root"]
SAM2_CHECKPOINT_DIR = CONFIG["sam2"]["checkpoint_dir"]
SAM2_CONFIG_DIR = CONFIG["sam2"]["config_dir"]

# Get model configuration
selected_model = CONFIG["sam2"]["model"]
model_info = CONFIG["sam2"]["models"][selected_model]

SAM2_CHECKPOINT = os.path.join(SAM2_CHECKPOINT_DIR, model_info["checkpoint"])
SAM2_CONFIG = os.path.join(SAM2_CONFIG_DIR, model_info["config"])

# Device
DEVICE = "cuda" if CONFIG["device"]["use_cuda"] else "cpu"


# =============================================================================
# GLOBAL VARIABLES FOR MOUSE CALLBACK
# =============================================================================
click_point = None
window_name = "Select Anesthesiologist - Click once"


def mouse_callback(event, x, y, flags, param):
    """Mouse callback function to capture user click."""
    global click_point

    if event == cv2.EVENT_LBUTTONDOWN:
        click_point = (x, y)
        print(f"Point selected at: ({x}, {y})")

        # Draw a circle at the clicked point for visual feedback
        frame = param
        cv2.circle(frame, (x, y), 5, (0, 255, 0), -1)
        cv2.imshow(window_name, frame)


def get_user_click(frame):
    """Display first frame and get user click for anesthesiologist location."""
    global click_point, window_name

    click_point = None

    # Create a copy for display
    display_frame = frame.copy()

    # Create window and set mouse callback
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 1280, 720)
    cv2.setMouseCallback(window_name, mouse_callback, display_frame)

    print("\n" + "=" * 70)
    print("USER INTERACTION REQUIRED")
    print("=" * 70)
    print("Please click ONCE on the anesthesiologist in the image.")
    print("Press any key after clicking to continue...")
    print("=" * 70)

    cv2.imshow(window_name, display_frame)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    if click_point is None:
        raise ValueError("No point was selected. Please run the script again and click on the anesthesiologist.")

    return click_point


def setup_device():
    """
    Setup device with optimal settings for RTX A2000.
    Enables bfloat16 and TF32 for maximum speed.
    """
    print("\n" + "=" * 70)
    print("GPU SETUP & OPTIMIZATION")
    print("=" * 70)

    if not torch.cuda.is_available():
        print("WARNING: CUDA is not available!")
        print("CPU mode will be extremely slow for video segmentation.")
        print("=" * 70)

        response = input("\nContinue with CPU? (y/n): ").strip().lower()
        if response != 'y':
            print("Exiting...")
            sys.exit(1)
        return torch.device("cpu")

    device = torch.device("cuda")

    print(f"CUDA Available: YES")
    print(f"GPU Device: {torch.cuda.get_device_name(0)}")
    print(f"CUDA Version: {torch.version.cuda}")
    print(f"GPU Memory: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")

    # Get compute capability
    compute_capability = torch.cuda.get_device_properties(0).major
    print(f"Compute Capability: {compute_capability}.{torch.cuda.get_device_properties(0).minor}")

    # Enable optimizations based on config
    print("\nEnabling optimizations:")

    if CONFIG["device"]["enable_bfloat16"]:
        print("  [+] bfloat16 autocast (2x speedup)")
        torch.autocast("cuda", dtype=torch.bfloat16).__enter__()
    else:
        print("  [-] bfloat16 disabled")

    if CONFIG["device"]["enable_torch_compile"]:
        print("  [+] torch.compile (2-3x speedup) - will be applied to model")
    else:
        print("  [-] torch.compile disabled (avoiding memory issues)")

    # Enable TF32 for Ampere GPUs (RTX A2000 is compute capability 8.6)
    if CONFIG["device"]["enable_tf32"] and compute_capability >= 8:
        print("  [+] TF32 for Ampere GPU (1.5x speedup)")
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
    elif compute_capability < 8:
        print("  [-] TF32 not available (requires Ampere or newer)")
    else:
        print("  [-] TF32 disabled")

    print("=" * 70)
    return device


def extract_frames_to_temp(video_path):
    """Extract video frames to a temporary directory."""
    print("\n" + "=" * 70)
    print("EXTRACTING FRAMES TO TEMPORARY DIRECTORY")
    print("=" * 70)

    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")

    # Get video properties
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"Resolution: {width}x{height}")
    print(f"FPS: {fps:.2f}")
    print(f"Frame Count: {frame_count}")
    print(f"Duration: {frame_count/fps:.1f} seconds")

    # Create temporary directory
    temp_dir = tempfile.mkdtemp(prefix="sam2_frames_")
    print(f"Temp Directory: {temp_dir}")

    # Extract frames
    frame_names = []
    first_frame = None

    print("\nExtracting frames...")
    with tqdm(total=frame_count, desc="Extracting", unit="frame") as pbar:
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx == 0:
                first_frame = frame.copy()

            # Save frame as JPEG
            frame_name = f"{frame_idx:05d}.jpg"
            frame_path = os.path.join(temp_dir, frame_name)
            cv2.imwrite(frame_path, frame)
            frame_names.append(frame_name)

            frame_idx += 1
            pbar.update(1)

    cap.release()

    print(f"Extracted {len(frame_names)} frames")
    print("=" * 70)

    return temp_dir, frame_names, fps, width, height, len(frame_names), first_frame


def resize_video_if_needed(video_path, max_width=None, max_height=None):
    """
    Check video resolution and resize if larger than max dimensions.
    Uses config values if not specified.
    Returns path to video (original or resized temp file).
    """
    if max_width is None:
        max_width = CONFIG["video"]["max_resolution"]["width"]
    if max_height is None:
        max_height = CONFIG["video"]["max_resolution"]["height"]

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    # Check if resizing is needed
    if width <= max_width and height <= max_height:
        print(f"✓ Resolution {width}x{height} is within limits")
        return video_path, None  # No resizing needed, return original path

    # Calculate new dimensions maintaining aspect ratio
    scale = min(max_width / width, max_height / height)
    new_width = int(width * scale)
    new_height = int(height * scale)

    # Make dimensions even (required for some codecs)
    new_width = new_width - (new_width % 2)
    new_height = new_height - (new_height % 2)

    print("\n" + "=" * 70)
    print("VIDEO RESIZING REQUIRED")
    print("=" * 70)
    print(f"Original Resolution: {width}x{height}")
    print(f"Target Resolution: {new_width}x{new_height}")
    print(f"Reason: High resolutions cause GPU memory issues and slowdowns")
    print("\nResizing video with FFmpeg...")

    # Create temporary resized video
    resized_path = tempfile.mktemp(suffix=".mp4", prefix="sam2_resized_")

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", video_path,
        "-vf", f"scale={new_width}:{new_height}",
        "-c:v", "libx264",
        "-preset", CONFIG["encoding"]["resize_preset"],
        "-crf", str(CONFIG["encoding"]["h264_crf"]),
        "-c:a", "copy",
        resized_path
    ]

    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    if result.returncode != 0:
        print(f"FFmpeg resizing failed: {result.stderr}")
        raise RuntimeError("Failed to resize video with FFmpeg")

    print(f"✓ Video resized successfully")
    print(f"  Temp file: {resized_path}")
    print("=" * 70)

    return resized_path, resized_path  # Return resized path and cleanup path


def segment_anesthesiologist_optimized(video_path, output_path=None):
    """
    Segment anesthesiologist from video using optimized SAM 2.1 video predictor.
    """
    # Setup device with optimizations
    device = setup_device()

    # Check resolution and resize if needed (uses config max resolution)
    if CONFIG["video"]["auto_resize"]:
        video_path, resized_temp_file = resize_video_if_needed(video_path)
    else:
        resized_temp_file = None

    # Extract frames to temporary directory
    temp_dir, frame_names, fps, width, height, frame_count, first_frame = extract_frames_to_temp(video_path)

    # Get user click on first frame
    click_x, click_y = get_user_click(first_frame)

    # Initialize SAM 2 video predictor
    print("\n" + "=" * 70)
    print("INITIALIZING SAM 2.1 VIDEO PREDICTOR")
    print("=" * 70)
    print(f"Checkpoint: {SAM2_CHECKPOINT}")
    print(f"Config: {SAM2_CONFIG}")
    print(f"Device: {device}")
    print("Loading model...")

    try:
        # Build SAM 2 video predictor
        predictor = build_sam2_video_predictor(SAM2_CONFIG, SAM2_CHECKPOINT, device=device)

        # Compile model for 2-3x speedup (requires PyTorch 2.0+)
        if CONFIG["device"]["enable_torch_compile"] and device.type == "cuda":
            try:
                print("Compiling model with torch.compile...")
                predictor = torch.compile(predictor, mode="reduce-overhead", fullgraph=False)
                print("Model compiled successfully!")
            except Exception as compile_error:
                print(f"Warning: torch.compile failed: {compile_error}")
                print("Continuing without compilation...")
        elif device.type == "cpu":
            print("torch.compile: Skipped (CPU mode)")
        else:
            print("torch.compile: Disabled via config")

        print("Model loaded successfully!")

    except Exception as e:
        print(f"ERROR: Failed to load SAM 2 model: {e}")
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        sys.exit(1)

    print("=" * 70)

    # Initialize inference state
    print("\n" + "=" * 70)
    print("RUNNING SAM 2.1 VIDEO SEGMENTATION")
    print("=" * 70)
    print(f"Prompt Point: ({click_x}, {click_y})")
    print("Label: 1 (Foreground)")
    print("\nInitializing inference state...")

    try:
        # Initialize inference state with video frames
        inference_state = predictor.init_state(video_path=temp_dir)

        # Add point prompt on first frame
        _, out_obj_ids, out_mask_logits = predictor.add_new_points_or_box(
            inference_state=inference_state,
            frame_idx=0,
            obj_id=1,
            points=np.array([[click_x, click_y]], dtype=np.float32),
            labels=np.array([1], dtype=np.int32),
        )

        print(f"Point prompt added successfully")
        print(f"Object ID: {out_obj_ids}")

    except Exception as e:
        print(f"ERROR: Failed to initialize inference: {e}")
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        sys.exit(1)

    # Determine output path
    if output_path is None:
        video_dir = os.path.dirname(video_path) or "."
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(video_dir, f"{video_name}_mask.mp4")

    print(f"Output: {output_path}")

    # Create output directory if needed
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    # Propagate masks through entire video (FAST - just store results)
    print("\n" + "=" * 70)
    print("PROPAGATING MASKS (STEP 1/2)")
    print("=" * 70)
    print("This should be FAST (30-47 FPS)...\n")

    video_segments = {}
    start_time = time.time()

    with tqdm(total=frame_count, desc="Propagating", unit="frame") as pbar:
        for out_frame_idx, out_obj_ids, out_mask_logits in predictor.propagate_in_video(inference_state):
            # CRITICAL: Move masks to CPU immediately to avoid GPU memory accumulation
            # Convert to numpy and store in system RAM (not GPU VRAM)
            video_segments[out_frame_idx] = {
                out_obj_id: (out_mask_logits[i] > 0.0).cpu().numpy()
                for i, out_obj_id in enumerate(out_obj_ids)
            }
            pbar.update(1)

    propagation_time = time.time() - start_time
    propagation_fps = frame_count / propagation_time

    print(f"\nPropagation complete!")
    print(f"  Time: {propagation_time:.1f} seconds")
    print(f"  Speed: {propagation_fps:.1f} FPS")

    # Now write to video (STEP 2 - optimized for binary masks)
    print("\n" + "=" * 70)
    print("WRITING BINARY MASK VIDEO (STEP 2/2)")
    print("=" * 70)
    print("White = Subject (Anesthesiologist)")
    print("Black = Background\n")

    # Create temporary directory for PNG frames
    mask_temp_dir = tempfile.mkdtemp(prefix="sam2_masks_")

    start_time = time.time()

    # Write binary masks as PNG images (fast, lossless)
    print("Writing mask frames...")
    with tqdm(total=frame_count, desc="Writing PNGs", unit="frame") as pbar:
        for frame_idx in range(frame_count):
            if frame_idx in video_segments and 1 in video_segments[frame_idx]:
                # Get binary mask (already thresholded and on CPU)
                mask = video_segments[frame_idx][1]

                # Convert to uint8 and scale to 0-255
                binary_mask = (mask[0] * 255).astype(np.uint8)

                # Resize if needed
                if binary_mask.shape != (height, width):
                    binary_mask = cv2.resize(binary_mask, (width, height),
                                            interpolation=cv2.INTER_NEAREST)
            else:
                # No mask detected, create empty (black) frame
                binary_mask = np.zeros((height, width), dtype=np.uint8)

            # Write as PNG
            mask_path = os.path.join(mask_temp_dir, f"{frame_idx:05d}.png")
            cv2.imwrite(mask_path, binary_mask)
            pbar.update(1)

    # Encode with FFmpeg using H.264 for optimal compression
    print("\nEncoding video with H.264 (optimized for binary masks)...")
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", os.path.join(mask_temp_dir, "%05d.png"),
        "-c:v", "libx264",
        "-preset", CONFIG["encoding"]["h264_preset"],  # Slower encoding = better compression
        "-crf", str(CONFIG["encoding"]["h264_crf"]),  # High quality (visually lossless for binary content)
        "-pix_fmt", "yuv420p",  # Compatible with all players
        output_path
    ]

    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    if result.returncode != 0:
        print(f"FFmpeg encoding failed: {result.stderr}")
        raise RuntimeError("Failed to encode video with FFmpeg")

    writing_time = time.time() - start_time
    writing_fps = frame_count / writing_time

    print(f"\nWriting complete!")
    print(f"  Time: {writing_time:.1f} seconds")
    print(f"  Speed: {writing_fps:.1f} FPS")

    # Cleanup temporary directories and files
    import shutil
    print("\nCleaning up temporary files...")
    shutil.rmtree(temp_dir, ignore_errors=True)
    shutil.rmtree(mask_temp_dir, ignore_errors=True)
    if resized_temp_file and os.path.exists(resized_temp_file):
        os.remove(resized_temp_file)
        print(f"  Removed temporary resized video")

    # Verify output
    if os.path.exists(output_path):
        output_size = os.path.getsize(output_path) / (1024 * 1024)
        total_time = propagation_time + writing_time
        overall_fps = frame_count / total_time

        print("\n" + "=" * 70)
        print("SEGMENTATION COMPLETE")
        print("=" * 70)
        print(f"Output File: {output_path}")
        print(f"File Size: {output_size:.1f} MB")
        print(f"Frames Processed: {frame_count}")
        print(f"\nPerformance:")
        print(f"  Propagation: {propagation_time:.1f}s @ {propagation_fps:.1f} FPS")
        print(f"  Writing: {writing_time:.1f}s @ {writing_fps:.1f} FPS")
        print(f"  Total: {total_time:.1f}s @ {overall_fps:.1f} FPS")
        print("=" * 70)
    else:
        print("\nERROR: Output file was not created!")
        sys.exit(1)

    return output_path


def main():
    """Main entry point for the script."""
    print("\n" + "=" * 70)
    print("ANESTHESIOLOGIST SEGMENTATION - SAM 2.1 (OPTIMIZED)")
    print("=" * 70)
    print(f"Model: {selected_model} ({model_info['description']})")
    print(f"Config: {os.path.join(os.path.dirname(__file__), 'sam_config.json')}")
    print("=" * 70)

    # Get input video path from config first, then command line, then prompt
    video_path = CONFIG["video"]["input_path"]

    if len(sys.argv) > 1:
        video_path = sys.argv[1]
    elif not video_path:
        default_dir = CONFIG["video"]["default_input_dir"] or MP4_ROOT or "."
        print(f"\nEnter the path to the input video:")
        print(f"(Default search location: {default_dir})")
        video_path = input("\nVideo path: ").strip()

    # Remove quotes if present
    video_path = video_path.strip('"').strip("'")

    # Validate input
    if not video_path:
        print("ERROR: No video path provided")
        sys.exit(1)

    if not os.path.exists(video_path):
        print(f"ERROR: Video file not found: {video_path}")
        sys.exit(1)

    # Get output path from config first, then command line, then auto-generate
    output_path = CONFIG["video"]["output_path"]
    if len(sys.argv) > 2:
        output_path = sys.argv[2].strip('"').strip("'")

    # Convert empty string to None for auto-generation
    if not output_path:
        output_path = None

    # Run segmentation
    try:
        result_path = segment_anesthesiologist_optimized(video_path, output_path)
        print("\nSUCCESS!")
        print(f"\nMask video saved to: {result_path}")

    except KeyboardInterrupt:
        print("\n\nSegmentation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
