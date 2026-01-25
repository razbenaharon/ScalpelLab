"""
simclr_config_reid.py - Optimal configuration for Person Re-ID in StrongSORT

Dataset: 546 hours, 123 cases, General_3 camera
Goal: Learn discriminative person representations for tracking
GPU: RTX A2000 12GB

This config is optimized for:
1. Maximum inter-person discrimination (for Re-ID)
2. Temporal diversity (same person, different poses)
3. Quality over quantity (sharp, clear crops)
4. Efficient processing on 12GB GPU
"""

# ============================================================================
# TARGET DATASET SIZE - OPTIMIZED FOR RE-ID
# ============================================================================

# For Re-ID, we want:
# - Many unique identities (people)
# - Good samples per identity
# - High temporal diversity per identity

TARGET_TOTAL_IMAGES = 60000      # Large dataset for robust Re-ID
MIN_IMAGES_PER_CLASS = 150       # Each person needs good representation
MAX_IMAGES_PER_CLASS = 800       # Prevent single person dominance

# Expected outcome with 123 cases:
# - ~50-100 unique track IDs (people)
# - ~600-1200 images per person
# - Excellent coverage

# ============================================================================
# QUALITY THRESHOLDS - HIGH QUALITY FOR RE-ID
# ============================================================================

# Re-ID needs VERY clear person crops
CONFIDENCE_THRESHOLD = 0.92      # High confidence (good detections)
MIN_AREA_PIXELS = 9000           # Larger crops (better features)
BLUR_THRESHOLD = 110             # Sharp images (critical for Re-ID)

# Brightness - surgical lighting can vary
BRIGHTNESS_MIN = 25              # Accept slightly darker (surgical context)
BRIGHTNESS_MAX = 230             # Accept slightly brighter (surgical lights)

# Aspect ratio - strict for person shape
ASPECT_RATIO_MIN = 1.3           # Height/width - clearly person-shaped
ASPECT_RATIO_MAX = 3.3           # Not too thin (filters equipment)

# ============================================================================
# TEMPORAL DIVERSITY - CRITICAL FOR RE-ID!
# ============================================================================

# For Re-ID, we want the SAME person in DIFFERENT poses/angles
# This helps the model learn identity, not just pose

MIN_FRAME_GAP = 60               # 2 seconds at 30fps
                                 # Same person, different pose
                                 # Critical for invariant features!

MOVEMENT_THRESHOLD_PX = 70       # Significant movement required
                                 # Ensures pose diversity

# ============================================================================
# PROCESSING OPTIMIZATION - A2000 12GB GPU
# ============================================================================

FRAME_SKIP = 10                  # Process every 10th frame
                                 # With 546 hours, still ~200K frames
                                 # Balances thoroughness with speed
                                 # Estimated: 24-36 hours processing

# Batch processing for YOLO (A2000 optimization)
YOLO_BATCH_SIZE = 1              # Conservative for 12GB
                                 # Can try 2-4 if stable

# ============================================================================
# DIVERSITY ACROSS VIDEOS
# ============================================================================

MAX_SAMPLES_PER_TRACK_PER_VIDEO = 80   # Allow more per video
                                        # With 123 cases, still diverse
                                        # Each person can appear in multiple cases

# ============================================================================
# CROP SETTINGS - OPTIMIZED FOR RE-ID
# ============================================================================

PADDING_PIXELS = 30              # More context around person
                                 # Helps with partial occlusions
                                 # Important for surgical setting

# ============================================================================
# RE-ID SPECIFIC FEATURES
# ============================================================================

# Add sharpness weighting
USE_SHARPNESS_SCORING = True     # Prefer sharper crops
SHARPNESS_WEIGHT = 0.3           # In quality score

# Add size preference (larger = better for Re-ID)
PREFER_LARGER_CROPS = True       # Prefer larger person detections
SIZE_WEIGHT = 0.2                # In quality score

# ============================================================================
# MULTI-STAGE PROCESSING (RECOMMENDED)
# ============================================================================

# Stage 1: Sample 20 cases to verify config
STAGE_1_CASES = 20
STAGE_1_TARGET = 10000

# Stage 2: Full processing if Stage 1 looks good
STAGE_2_CASES = 123
STAGE_2_TARGET = 60000

# ============================================================================
# EXPECTED OUTCOMES
# ============================================================================

"""
With this configuration, you should get:

Dataset Statistics:
- Total images: ~60,000
- Unique persons: 50-100
- Images per person: 600-1200
- Imbalance ratio: <5x (good balance)
- Temporal diversity: Very high (2 sec gaps)

Quality Metrics:
- High confidence detections (>0.92)
- Sharp images (blur >110)
- Good size crops (>9000 px)
- Clear person shapes (aspect 1.3-3.3)

Processing Time:
- ~24-36 hours on A2000 12GB
- ~0.5-1.5 hours per case
- Can pause/resume between cases

SimCLR Training (Next Step):
- Batch size: 128-192 (on A2000)
- Epochs: 200-300
- Projection dim: 128
- Temperature: 0.5

StrongSORT Integration:
- Extract 128-d embeddings
- Replace BoT/OSNet with SimCLR encoder
- Fine-tune on your specific videos
- Expected boost in Re-ID accuracy: 15-30%
"""

# ============================================================================
# VALIDATION STRATEGY
# ============================================================================

# Before full run, validate on subset
VALIDATION_CASES = [1, 2, 3, 4, 5]  # First 5 cases
VALIDATION_TARGET = 5000             # Should get ~5K images

# Check these metrics before full run:
# ✓ Imbalance ratio < 10x
# ✓ Min samples per person > 100
# ✓ Blur scores look good (>110)
# ✓ Crops look visually correct

# ============================================================================
# PERSON CLASS ONLY
# ============================================================================

PERSON_CLASS_ID = 0

# Model path - YOLO26 for faster inference
# YOLO26 options:
# - yolo26n-pose.pt: Nano (fastest, 43% faster than YOLO11n on CPU)
# - yolo26s-pose.pt: Small
# - yolo26m-pose.pt: Medium (RECOMMENDED - best balance)
# - yolo26l-pose.pt: Large (highest accuracy)
MODEL_PATH = r"F:\YOLO_Models\yolo26m-pose.pt"

# Output directory
OUTPUT_DIR_NAME = "simclr_reid_60k"

if __name__ == "__main__":
    print("=" * 80)
    print("SimCLR Config for Person Re-ID in StrongSORT")
    print("=" * 80)
    print(f"\nDataset Specs:")
    print(f"  Videos: 546 hours, 123 cases")
    print(f"  Target: {TARGET_TOTAL_IMAGES:,} images")
    print(f"  Per person: {MIN_IMAGES_PER_CLASS}-{MAX_IMAGES_PER_CLASS} images")
    print(f"\nQuality Settings:")
    print(f"  Confidence: {CONFIDENCE_THRESHOLD}")
    print(f"  Min area: {MIN_AREA_PIXELS:,} px")
    print(f"  Blur threshold: {BLUR_THRESHOLD}")
    print(f"  Brightness: [{BRIGHTNESS_MIN}, {BRIGHTNESS_MAX}]")
    print(f"\nTemporal Diversity:")
    print(f"  Frame gap: {MIN_FRAME_GAP} frames ({MIN_FRAME_GAP/30:.1f} sec)")
    print(f"  Movement: {MOVEMENT_THRESHOLD_PX} px")
    print(f"\nProcessing:")
    print(f"  Frame skip: {FRAME_SKIP} (every {FRAME_SKIP/30:.1f} sec)")
    print(f"  Est. time: 24-36 hours on A2000")
    print(f"  Output: ~60K images (~10-15GB)")
    print("=" * 80)
