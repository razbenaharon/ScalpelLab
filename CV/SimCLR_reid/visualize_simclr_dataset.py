"""
visualize_simclr_dataset.py - Visualize Burst-Mode SimCLR Dataset

Analyzes the flat dataset structure with filename format:
    {CaseID}_v{VideoIdx}_{FrameID}.jpg

Creates visualizations showing:
    - Image count per case
    - Burst distribution statistics
    - Dataset quality metrics
"""

import re
from pathlib import Path
from collections import defaultdict, OrderedDict
import matplotlib.pyplot as plt
import numpy as np
import json

# ============================================================================
# CONFIGURATION
# ============================================================================

DATASET_PATH = Path(r"F:\Room_8_Data\SIMCLR\dataset\simclr_burst_v3_cleaned")
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff'}
OUTPUT_PATH = Path(__file__).parent  # Save in same directory as script

# Regex to parse filename: {CaseID}_v{VideoIdx}_{FrameID}.jpg
FILENAME_PATTERN = re.compile(r'^(\d+)_v(\d+)_(\d+)\.jpg$')


# ============================================================================
# DATA ANALYSIS FUNCTIONS
# ============================================================================

def parse_filename(filename: str) -> tuple | None:
    """
    Parse filename to extract case_no, video_idx, and frame_id.

    Args:
        filename: Filename like "42_v01_001234.jpg"

    Returns:
        Tuple of (case_no, video_idx, frame_id) or None if parse fails
    """
    match = FILENAME_PATTERN.match(filename)
    if match:
        case_no = int(match.group(1))
        video_idx = int(match.group(2))
        frame_id = int(match.group(3))
        return (case_no, video_idx, frame_id)
    return None


def analyze_dataset(dataset_path: Path) -> dict:
    """
    Analyze the dataset and collect statistics.

    Returns:
        Dictionary with analysis results:
            - case_counts: images per (case_no, video_idx) pair — unique identity is date+case
            - total_images: total count
            - burst_analysis: burst grouping info
    """
    # Key: (case_no, video_idx) — this is the true unique identity (date+case)
    case_counts = defaultdict(int)
    frame_ids_by_case_video = defaultdict(list)  # for burst analysis

    total_images = 0
    parse_failures = 0

    print(f"Scanning: {dataset_path}")

    for filepath in dataset_path.iterdir():
        if not filepath.is_file():
            continue
        if filepath.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        parsed = parse_filename(filepath.name)
        if parsed:
            case_no, video_idx, frame_id = parsed
            key = (case_no, video_idx)
            case_counts[key] += 1
            frame_ids_by_case_video[key].append(frame_id)
            total_images += 1
        else:
            parse_failures += 1

    # Sort by (case_no, video_idx)
    case_counts = OrderedDict(sorted(case_counts.items()))

    # Analyze bursts (groups of 3 consecutive captures ~20 frames apart)
    burst_stats = analyze_bursts(frame_ids_by_case_video)

    return {
        'case_counts': case_counts,
        'total_images': total_images,
        'parse_failures': parse_failures,
        'num_cases': len(case_counts),
        'num_videos': len(case_counts),
        'burst_stats': burst_stats
    }


def analyze_bursts(frame_ids_by_case_video: dict) -> dict:
    """
    Analyze burst patterns in the dataset.

    A burst is expected to be 3 images with ~20 frame gaps.
    """
    total_bursts = 0
    complete_bursts = 0  # Exactly 3 images
    incomplete_bursts = 0

    for (case_no, video_idx), frame_ids in frame_ids_by_case_video.items():
        if not frame_ids:
            continue

        # Sort frame IDs and group into bursts
        sorted_frames = sorted(frame_ids)

        # Simple heuristic: count how many groups of 3 we have
        # Images in a burst should be ~20-40 frames apart
        # Images between bursts should be 750+ frames apart

        burst_count = 0
        i = 0
        while i < len(sorted_frames):
            # Start of potential burst
            burst_frames = [sorted_frames[i]]
            j = i + 1

            # Collect frames that are close together (part of same burst)
            while j < len(sorted_frames):
                gap = sorted_frames[j] - sorted_frames[j - 1]
                if gap < 100:  # Within burst (expect ~20-40 frame gaps)
                    burst_frames.append(sorted_frames[j])
                    j += 1
                else:
                    break  # Large gap = new burst

            if len(burst_frames) == 3:
                complete_bursts += 1
            else:
                incomplete_bursts += 1

            burst_count += 1
            i = j

        total_bursts += burst_count

    return {
        'total_bursts': total_bursts,
        'complete_bursts': complete_bursts,
        'incomplete_bursts': incomplete_bursts
    }


# ============================================================================
# VISUALIZATION FUNCTIONS
# ============================================================================

def create_case_distribution_chart(analysis: dict, output_path: Path):
    """Create a bar chart showing image counts per case."""
    case_counts = analysis['case_counts']

    if not case_counts:
        print("No data to visualize!")
        return None

    cases = list(case_counts.keys())  # list of (case_no, video_idx) tuples
    counts = list(case_counts.values())
    # Label format: "42_v03" — unique per date+case
    labels = [f"{c}_v{v:02d}" for c, v in cases]

    # Calculate statistics
    total_images = sum(counts)
    avg_images = np.mean(counts)
    max_images = max(counts)
    min_images = min(counts)

    # Create figure with dark style
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(16, 8))

    # Create color gradient based on count values
    norm_counts = np.array(counts) / max(counts)
    colors = plt.colormaps['viridis'](norm_counts)

    # Create bar chart
    x_positions = np.arange(len(cases))
    bars = ax.bar(x_positions, counts, width=0.8, color=colors,
                  edgecolor='white', linewidth=0.3)

    # Add value labels on bars (only if not too many)
    if len(cases) <= 50:
        for bar, count in zip(bars, counts):
            height = bar.get_height()
            ax.annotate(f'{count}',
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 2),
                        textcoords="offset points",
                        ha='center', va='bottom',
                        fontsize=7, fontweight='bold',
                        color='white', rotation=90)

    # Customize x-axis — show ~30 labels max
    step = max(1, len(cases) // 30)
    ax.set_xticks(x_positions[::step])
    ax.set_xticklabels(labels[::step], rotation=45, ha='right', fontsize=8)

    # Add grid
    ax.yaxis.grid(True, linestyle='--', alpha=0.3)
    ax.set_axisbelow(True)

    # Labels and title
    ax.set_xlabel('Case (case_no_vVideoIdx)', fontsize=12, fontweight='bold')
    ax.set_ylabel('Number of Images', fontsize=12, fontweight='bold')
    ax.set_title('SimCLR Burst Dataset - Images per Case\n(simclr_burst_v3_cleaned)',
                 fontsize=14, fontweight='bold', pad=20)

    # Add statistics box
    burst_stats = analysis['burst_stats']
    stats_text = (
        f'Total Images: {total_images:,}\n'
        f'Total Cases: {len(cases)}\n'
        f'Total Videos: {analysis["num_videos"]}\n'
        f'Avg/Case: {avg_images:,.1f}\n'
        f'Max: {max_images:,}\n'
        f'Min: {min_images:,}\n'
        f'───────────────\n'
        f'Bursts: {burst_stats["total_bursts"]:,}\n'
        f'Complete (3): {burst_stats["complete_bursts"]:,}\n'
        f'Incomplete: {burst_stats["incomplete_bursts"]:,}'
    )
    props = dict(boxstyle='round,pad=0.5', facecolor='#2d2d2d',
                 edgecolor='white', alpha=0.9)
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=9,
            verticalalignment='top', bbox=props, family='monospace')

    # Add horizontal line for average
    ax.axhline(y=avg_images, color='#ff6b6b', linestyle='--', linewidth=2,
               label=f'Average ({avg_images:,.1f})')
    ax.legend(loc='upper right')

    # Adjust layout
    plt.tight_layout()

    # Save figure
    output_file = output_path / 'simclr_burst_distribution.png'
    plt.savefig(output_file, dpi=150, bbox_inches='tight', facecolor='#1e1e1e')
    print(f"Saved visualization to: {output_file}")

    plt.show()

    return output_file


def create_histogram(analysis: dict, output_path: Path):
    """Create a histogram showing distribution of images per case."""
    counts = list(analysis['case_counts'].values())

    if not counts:
        return None

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 6))

    # Create histogram
    n, bins, patches = ax.hist(counts, bins=20, color='#4ecdc4',
                                edgecolor='white', linewidth=0.5)

    # Color by height
    max_n = max(n)
    for patch, height in zip(patches, n):
        patch.set_facecolor(plt.colormaps['viridis'](height / max_n))

    ax.set_xlabel('Images per Case', fontsize=12, fontweight='bold')
    ax.set_ylabel('Number of Cases', fontsize=12, fontweight='bold')
    ax.set_title('Distribution of Images per Case',
                 fontsize=14, fontweight='bold')

    ax.yaxis.grid(True, linestyle='--', alpha=0.3)

    # Add statistics
    stats_text = (
        f'Mean: {np.mean(counts):.1f}\n'
        f'Median: {np.median(counts):.1f}\n'
        f'Std: {np.std(counts):.1f}'
    )
    props = dict(boxstyle='round,pad=0.5', facecolor='#2d2d2d',
                 edgecolor='white', alpha=0.9)
    ax.text(0.95, 0.95, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', ha='right', bbox=props, family='monospace')

    plt.tight_layout()

    output_file = output_path / 'simclr_burst_histogram.png'
    plt.savefig(output_file, dpi=150, bbox_inches='tight', facecolor='#1e1e1e')
    print(f"Saved histogram to: {output_file}")

    plt.show()

    return output_file


# ============================================================================
# SUMMARY FUNCTIONS
# ============================================================================

def print_summary(analysis: dict):
    """Print a detailed summary of the dataset."""
    case_counts = analysis['case_counts']
    burst_stats = analysis['burst_stats']

    print("\n" + "=" * 60)
    print("SimCLR BURST DATASET SUMMARY (simclr_burst_v3_cleaned)")
    print("=" * 60)

    # Overall stats
    print(f"\n{'OVERALL STATISTICS':^60}")
    print("-" * 60)
    print(f"  Total Images:      {analysis['total_images']:>10,}")
    print(f"  Total Cases:       {analysis['num_cases']:>10,}")
    print(f"  Total Videos:      {analysis['num_videos']:>10,}")
    print(f"  Parse Failures:    {analysis['parse_failures']:>10,}")

    # Burst stats
    print(f"\n{'BURST STATISTICS':^60}")
    print("-" * 60)
    print(f"  Total Bursts:      {burst_stats['total_bursts']:>10,}")
    print(f"  Complete (3 imgs): {burst_stats['complete_bursts']:>10,}")
    print(f"  Incomplete:        {burst_stats['incomplete_bursts']:>10,}")

    if burst_stats['total_bursts'] > 0:
        complete_pct = (burst_stats['complete_bursts'] / burst_stats['total_bursts']) * 100
        print(f"  Completion Rate:   {complete_pct:>9.1f}%")

    # Per-case breakdown (show top 10 and bottom 10)
    if case_counts:
        counts = list(case_counts.values())
        print(f"\n{'PER-CASE STATISTICS':^60}")
        print("-" * 60)
        print(f"  Average:           {np.mean(counts):>10,.1f}")
        print(f"  Median:            {np.median(counts):>10,.1f}")
        print(f"  Std Dev:           {np.std(counts):>10,.1f}")
        print(f"  Max:               {max(counts):>10,}")
        print(f"  Min:               {min(counts):>10,}")

        # Top 10 cases
        sorted_cases = sorted(case_counts.items(), key=lambda x: x[1], reverse=True)
        print(f"\n{'TOP 10 CASES':^60}")
        print("-" * 60)
        print(f"  {'Case':<15} {'Images':>10}")
        for (case_no, video_idx), count in sorted_cases[:10]:
            print(f"  {f'{case_no}_v{video_idx:02d}':<15} {count:>10,}")

        # Bottom 10 cases
        print(f"\n{'BOTTOM 10 CASES':^60}")
        print("-" * 60)
        print(f"  {'Case':<15} {'Images':>10}")
        for (case_no, video_idx), count in sorted_cases[-10:]:
            print(f"  {f'{case_no}_v{video_idx:02d}':<15} {count:>10,}")

    print("\n" + "=" * 60)


def save_analysis_json(analysis: dict, output_path: Path):
    """Save analysis results to JSON file."""
    # Convert defaultdict and tuple keys for JSON serialization
    counts = list(analysis['case_counts'].values())
    json_data = {
        'total_images': analysis['total_images'],
        'num_cases': analysis['num_cases'],
        'num_videos': analysis['num_videos'],
        'parse_failures': analysis['parse_failures'],
        'burst_stats': analysis['burst_stats'],
        # Keys serialized as "case_no_vVideoIdx" strings
        'case_counts': {f"{c}_v{v:02d}": cnt
                        for (c, v), cnt in analysis['case_counts'].items()},
        'statistics': {
            'mean': float(np.mean(counts)) if counts else 0,
            'median': float(np.median(counts)) if counts else 0,
            'std': float(np.std(counts)) if counts else 0,
        }
    }

    output_file = output_path / 'dataset_analysis.json'
    with open(output_file, 'w') as f:
        json.dump(json_data, f, indent=2)
    print(f"Saved analysis to: {output_file}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    print(f"Analyzing dataset at: {DATASET_PATH}\n")

    if not DATASET_PATH.exists():
        print(f"[ERROR] Dataset path does not exist: {DATASET_PATH}")
        return

    # Analyze dataset
    analysis = analyze_dataset(DATASET_PATH)

    if analysis['total_images'] == 0:
        print("No images found in dataset!")
        return

    # Print summary
    print_summary(analysis)


if __name__ == "__main__":
    main()
