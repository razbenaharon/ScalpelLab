"""
Script to visualize the SIMCLR ReID dataset distribution by date.
Counts images in each date directory and creates a bar chart visualization.
"""

import os
from pathlib import Path
from collections import OrderedDict
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import numpy as np

# Configuration
DATASET_PATH = Path(r"F:\Room_8_Data\SIMCLR\dataset\simclr_reid_60k_v2")
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff'}
OUTPUT_PATH = Path(__file__).parent  # Save in same directory as script


def count_images_per_date(dataset_path: Path) -> dict:
    """
    Count the number of images in each date directory.

    Args:
        dataset_path: Path to the dataset root directory

    Returns:
        Dictionary mapping date strings to image counts
    """
    date_counts = {}

    for item in sorted(dataset_path.iterdir()):
        if item.is_dir():
            # Try to parse as date to filter out non-date directories
            try:
                datetime.strptime(item.name, "%Y-%m-%d")
            except ValueError:
                continue

            # Count images in this date directory
            image_count = sum(
                1 for f in item.iterdir()
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
            )
            date_counts[item.name] = image_count

    return OrderedDict(sorted(date_counts.items()))


def create_visualization(date_counts: dict, output_path: Path):
    """
    Create a bar chart visualization of image counts per date.

    Args:
        date_counts: Dictionary mapping date strings to image counts
        output_path: Path to save the output figure
    """
    # Parse dates and get counts
    dates = [datetime.strptime(d, "%Y-%m-%d") for d in date_counts.keys()]
    counts = list(date_counts.values())

    # Calculate statistics
    total_images = sum(counts)
    avg_images = np.mean(counts)
    max_images = max(counts)
    min_images = min(counts)

    # Create figure with dark style
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(14, 8))

    # Create color gradient based on count values
    norm_counts = np.array(counts) / max(counts)
    colors = plt.cm.viridis(norm_counts)

    # Create bar chart
    bars = ax.bar(dates, counts, width=0.8, color=colors, edgecolor='white', linewidth=0.5)

    # Add value labels on bars
    for bar, count in zip(bars, counts):
        height = bar.get_height()
        ax.annotate(f'{count:,}',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom',
                    fontsize=9, fontweight='bold',
                    color='white')

    # Customize x-axis
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    plt.xticks(rotation=45, ha='right')

    # Add grid
    ax.yaxis.grid(True, linestyle='--', alpha=0.3)
    ax.set_axisbelow(True)

    # Labels and title
    ax.set_xlabel('Date', fontsize=12, fontweight='bold')
    ax.set_ylabel('Number of Images', fontsize=12, fontweight='bold')
    ax.set_title('SIMCLR ReID Dataset Distribution by Date\n(simclr_reid_60k_v2)',
                 fontsize=14, fontweight='bold', pad=20)

    # Add statistics box
    stats_text = (
        f'Total Images: {total_images:,}\n'
        f'Total Dates: {len(counts)}\n'
        f'Average: {avg_images:,.0f}\n'
        f'Max: {max_images:,}\n'
        f'Min: {min_images:,}'
    )
    props = dict(boxstyle='round,pad=0.5', facecolor='#2d2d2d', edgecolor='white', alpha=0.9)
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', bbox=props, family='monospace')

    # Add horizontal line for average
    ax.axhline(y=avg_images, color='#ff6b6b', linestyle='--', linewidth=2,
               label=f'Average ({avg_images:,.0f})')
    ax.legend(loc='upper right')

    # Adjust layout
    plt.tight_layout()

    # Save figure
    output_file = output_path / 'simclr_dataset_distribution.png'
    plt.savefig(output_file, dpi=150, bbox_inches='tight', facecolor='#1e1e1e')
    print(f"Saved visualization to: {output_file}")

    # Also show the plot
    plt.show()

    return output_file


def print_summary(date_counts: dict):
    """Print a summary table of the dataset."""
    print("\n" + "=" * 50)
    print("SIMCLR ReID Dataset Summary (simclr_reid_60k_v2)")
    print("=" * 50)
    print(f"{'Date':<15} {'Images':>10}")
    print("-" * 50)

    for date, count in date_counts.items():
        print(f"{date:<15} {count:>10,}")

    print("-" * 50)
    total = sum(date_counts.values())
    print(f"{'TOTAL':<15} {total:>10,}")
    print(f"{'AVERAGE':<15} {total/len(date_counts):>10,.0f}")
    print("=" * 50 + "\n")


def main():
    print(f"Scanning dataset at: {DATASET_PATH}")

    # Count images per date
    date_counts = count_images_per_date(DATASET_PATH)

    if not date_counts:
        print("No date directories found!")
        return

    # Print summary
    print_summary(date_counts)

    # # Create visualization
    # output_file = create_visualization(date_counts, OUTPUT_PATH)
    #
    # print(f"\nDone! Visualization saved to: {output_file}")


if __name__ == "__main__":
    main()
