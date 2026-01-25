"""
Cluster Person Crops by Pure Visual Similarity
==============================================

This script organizes a folder of person crops into identity clusters
based solely on visual similarity, ignoring all filename metadata.

Algorithm:
1. Extract feature embeddings using a pre-trained Re-ID model (OSNet)
2. L2 normalize all feature vectors
3. Cluster using DBSCAN with cosine distance
4. Organize results into identity folders

Author: ScalpelLab
"""

import os
import shutil
import argparse
from pathlib import Path
from typing import List, Tuple, Optional
import warnings

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from PIL import Image
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import normalize
from tqdm import tqdm

warnings.filterwarnings('ignore')


# =============================================================================
# Configuration
# =============================================================================

class Config:
    """Configuration parameters for the clustering pipeline."""

    # Input/Output paths
    INPUT_FOLDER = "F:/Room_8_Data/SIMCLR/dataset/simclr_reid_60k"
    OUTPUT_FOLDER = "F:/Room_8_Data/SIMCLR/dataset/clustered_results"

    # Feature extraction
    BATCH_SIZE = 128
    NUM_WORKERS = 4
    IMAGE_SIZE = (256, 128)  # Height x Width (standard Re-ID size)

    # DBSCAN parameters
    # Note: For cosine distance, distance = 1 - cosine_similarity
    # eps=0.2 means cosine_similarity >= 0.8 to be neighbors (strict)
    # eps=0.3 means cosine_similarity >= 0.7 to be neighbors (moderate)
    DBSCAN_EPS = 0.25         # Cosine distance threshold (stricter default)
    DBSCAN_MIN_SAMPLES = 4    # Minimum images to form a cluster

    # Model selection: 'osnet' or 'resnet50'
    MODEL_TYPE = 'osnet'

    # Device
    DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'


# =============================================================================
# Dataset
# =============================================================================

class ImageFolderFlat(Dataset):
    """
    Dataset that loads all images from a flat folder structure.
    Treats each image as an independent data point (ignores filenames).
    """

    VALID_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}

    def __init__(self, folder_path: str, transform=None):
        self.folder_path = Path(folder_path)
        self.transform = transform
        self.image_paths: List[Path] = []
        self.corrupted_images: List[Path] = []

        # Collect all valid image files
        self._scan_folder()

    def _scan_folder(self):
        """Scan folder for all valid image files."""
        print(f"Scanning folder: {self.folder_path}")

        all_files = list(self.folder_path.iterdir())
        for file_path in tqdm(all_files, desc="Scanning images"):
            if file_path.suffix.lower() in self.VALID_EXTENSIONS:
                self.image_paths.append(file_path)

        print(f"Found {len(self.image_paths)} image files")

    def __len__(self) -> int:
        return len(self.image_paths)

    def __getitem__(self, idx: int) -> Tuple[Optional[torch.Tensor], int, str]:
        """
        Returns:
            image: Transformed image tensor (or None if corrupted)
            idx: Original index for tracking
            path: Path to the image file
        """
        img_path = self.image_paths[idx]

        try:
            # Load image
            image = Image.open(img_path).convert('RGB')

            # Apply transforms
            if self.transform:
                image = self.transform(image)

            return image, idx, str(img_path)

        except Exception as e:
            # Return None for corrupted images
            return None, idx, str(img_path)


def collate_fn_skip_none(batch):
    """Custom collate function that skips corrupted images (None values)."""
    # Filter out None images
    valid_items = [(img, idx, path) for img, idx, path in batch if img is not None]
    corrupted = [(idx, path) for img, idx, path in batch if img is None]

    if not valid_items:
        return None, [], [], corrupted

    images = torch.stack([item[0] for item in valid_items])
    indices = [item[1] for item in valid_items]
    paths = [item[2] for item in valid_items]

    return images, indices, paths, corrupted


# =============================================================================
# Models
# =============================================================================

def build_osnet_model(pretrained: bool = True) -> nn.Module:
    """
    Build OSNet-AIN model for Re-ID feature extraction.
    Uses local weights pretrained on MSMT17 dataset for best results.
    Falls back to Market-1501 weights or ImageNet if not available.
    """
    try:
        import torchreid

        # Check for local MSMT17 weights first (best option)
        local_weights_path = "yolo/osnet_ain_x1_0_msmt17.pt"

        if os.path.exists(local_weights_path):
            # Build OSNet-AIN model (Attention-Inspired Network variant)
            model = torchreid.models.build_model(
                name='osnet_ain_x1_0',
                num_classes=1041,  # MSMT17 identities
                pretrained=False,
                loss='softmax'
            )

            # Load local MSMT17 weights
            checkpoint = torch.load(local_weights_path, map_location='cpu', weights_only=False)
            if 'state_dict' in checkpoint:
                state_dict = checkpoint['state_dict']
            else:
                state_dict = checkpoint

            # Remove 'module.' prefix if present (from DataParallel)
            new_state_dict = {}
            for k, v in state_dict.items():
                if k.startswith('module.'):
                    new_state_dict[k[7:]] = v  # Remove 'module.' prefix
                else:
                    new_state_dict[k] = v
            state_dict = new_state_dict

            # Handle potential key mismatches
            model_dict = model.state_dict()
            pretrained_dict = {k: v for k, v in state_dict.items() if k in model_dict and v.shape == model_dict[k].shape}
            model_dict.update(pretrained_dict)
            model.load_state_dict(model_dict, strict=False)
            print(f"  Loaded {len(pretrained_dict)}/{len(model_dict)} weight tensors")

            print(f"Loaded OSNet-AIN-x1.0 with Re-ID weights (MSMT17)")
            model.classifier = nn.Identity()
            return model

        # Fallback: download Market-1501 weights
        print("Local MSMT17 weights not found, downloading Market-1501 weights...")
        reid_weights_url = "https://drive.google.com/uc?id=1vduhq5DpN2q1g4fYEZfPI17MJeh9qyrA"
        reid_weights_path = os.path.join(
            os.path.expanduser("~"),
            ".cache", "torch", "checkpoints", "osnet_x1_0_market.pth"
        )

        if not os.path.exists(reid_weights_path):
            os.makedirs(os.path.dirname(reid_weights_path), exist_ok=True)
            try:
                import gdown
                gdown.download(reid_weights_url, reid_weights_path, quiet=False)
            except ImportError:
                print("gdown not installed, using ImageNet weights instead")
                model = torchreid.models.build_model(
                    name='osnet_x1_0',
                    num_classes=1000,
                    pretrained=True,
                    loss='softmax'
                )
                model.classifier = nn.Identity()
                print("Loaded OSNet-x1.0 model (ImageNet pretrained)")
                return model

        # Build and load Market-1501 model
        model = torchreid.models.build_model(
            name='osnet_x1_0',
            num_classes=751,
            pretrained=False,
            loss='softmax'
        )
        checkpoint = torch.load(reid_weights_path, map_location='cpu', weights_only=False)
        state_dict = checkpoint.get('state_dict', checkpoint)
        model.load_state_dict(state_dict)
        print(f"Loaded OSNet-x1.0 with Re-ID weights (Market-1501)")
        model.classifier = nn.Identity()
        return model

    except ImportError:
        print("torchreid not installed. Install with: pip install torchreid")
        print("Falling back to ResNet50...")
        return build_resnet50_model(pretrained)


def build_resnet50_model(pretrained: bool = True) -> nn.Module:
    """
    Build ResNet50 model for feature extraction (fallback option).
    """
    from torchvision.models import resnet50, ResNet50_Weights

    if pretrained:
        model = resnet50(weights=ResNet50_Weights.IMAGENET1K_V2)
    else:
        model = resnet50(weights=None)

    # Remove the final classification layer
    # Keep everything up to the global average pooling
    model.fc = nn.Identity()

    print("Loaded ResNet50 model (torchvision)")
    return model


def get_model(model_type: str = 'osnet') -> nn.Module:
    """Get the specified model type."""
    if model_type.lower() == 'osnet':
        return build_osnet_model()
    elif model_type.lower() == 'resnet50':
        return build_resnet50_model()
    else:
        raise ValueError(f"Unknown model type: {model_type}")


# =============================================================================
# Feature Extraction
# =============================================================================

def get_transforms(image_size: Tuple[int, int] = (256, 128)) -> transforms.Compose:
    """Get image transforms for Re-ID models."""
    return transforms.Compose([
        transforms.Resize(image_size),
        transforms.ToTensor(),
        transforms.Normalize(
            mean=[0.485, 0.456, 0.406],
            std=[0.229, 0.224, 0.225]
        )
    ])


@torch.no_grad()
def extract_features(
    model: nn.Module,
    dataloader: DataLoader,
    device: str = 'cuda'
) -> Tuple[np.ndarray, List[str], List[str]]:
    """
    Extract feature embeddings for all images in the dataloader.

    Args:
        model: Feature extraction model
        dataloader: DataLoader with images
        device: Device to run inference on

    Returns:
        features: Numpy array of shape (N, D) with L2-normalized features
        valid_paths: List of paths for successfully processed images
        corrupted_paths: List of paths for corrupted images
    """
    model = model.to(device)
    model.eval()

    all_features = []
    valid_paths = []
    corrupted_paths = []

    print(f"\nExtracting features on {device.upper()}...")

    for batch in tqdm(dataloader, desc="Feature extraction"):
        images, indices, paths, corrupted = batch

        # Track corrupted images
        for idx, path in corrupted:
            corrupted_paths.append(path)

        if images is None:
            continue

        # Move to device and extract features
        images = images.to(device)
        features = model(images)

        # Handle different output shapes
        if len(features.shape) > 2:
            features = features.view(features.size(0), -1)

        # Move to CPU and convert to numpy
        features = features.cpu().numpy()

        all_features.append(features)
        valid_paths.extend(paths)

    # Concatenate all features
    if all_features:
        features_array = np.vstack(all_features)

        # L2 normalize features
        features_array = normalize(features_array, norm='l2', axis=1)

        print(f"\nExtracted features shape: {features_array.shape}")
        print(f"Successfully processed: {len(valid_paths)} images")
        print(f"Corrupted/skipped: {len(corrupted_paths)} images")

        return features_array, valid_paths, corrupted_paths
    else:
        raise RuntimeError("No features extracted! Check your dataset.")


# =============================================================================
# Clustering
# =============================================================================

def analyze_distance_distribution(
    features: np.ndarray,
    n_samples: int = 5000
) -> dict:
    """
    Analyze pairwise cosine distance distribution to help tune eps.
    Samples random pairs to avoid O(n^2) computation for large datasets.
    """
    from sklearn.metrics.pairwise import cosine_distances

    n = len(features)
    print(f"\nAnalyzing distance distribution (sampling {n_samples} pairs)...")

    # Sample random pairs
    if n > 1000:
        # For large datasets, sample random pairs
        np.random.seed(42)
        idx1 = np.random.choice(n, size=min(n_samples, n), replace=False)
        idx2 = np.random.choice(n, size=min(n_samples, n), replace=False)

        # Compute distances for sampled pairs
        sample_features = features[idx1[:min(500, n)]]
        distances = cosine_distances(sample_features).flatten()
        # Remove self-distances (diagonal)
        distances = distances[distances > 0.001]
    else:
        # For small datasets, compute all pairwise
        distances = cosine_distances(features).flatten()
        distances = distances[distances > 0.001]

    # Calculate percentiles
    percentiles = [5, 10, 25, 50, 75, 90, 95]
    pct_values = np.percentile(distances, percentiles)

    print("\n" + "=" * 50)
    print("DISTANCE DISTRIBUTION ANALYSIS")
    print("=" * 50)
    print(f"  Min distance:  {distances.min():.4f}")
    print(f"  Max distance:  {distances.max():.4f}")
    print(f"  Mean distance: {distances.mean():.4f}")
    print(f"  Std distance:  {distances.std():.4f}")
    print("\nPercentiles:")
    for p, v in zip(percentiles, pct_values):
        print(f"  {p:3d}%: {v:.4f}")

    print("\n" + "-" * 50)
    print("RECOMMENDED eps VALUES:")
    print("-" * 50)
    print(f"  Strict (few large clusters):   eps = {pct_values[1]:.3f}  (10th percentile)")
    print(f"  Moderate (balanced):           eps = {pct_values[2]:.3f}  (25th percentile)")
    print(f"  Loose (many small clusters):   eps = {pct_values[0]:.3f}  (5th percentile)")
    print("=" * 50)

    return {
        'min': distances.min(),
        'max': distances.max(),
        'mean': distances.mean(),
        'std': distances.std(),
        'percentiles': dict(zip(percentiles, pct_values)),
        'recommended_eps': pct_values[1]  # 10th percentile as default recommendation
    }


def cluster_features(
    features: np.ndarray,
    eps: float = 0.25,
    min_samples: int = 4
) -> np.ndarray:
    """
    Cluster feature vectors using DBSCAN with cosine distance.

    Args:
        features: L2-normalized feature vectors (N, D)
        eps: Maximum distance for samples to be considered neighbors
        min_samples: Minimum samples to form a dense region

    Returns:
        labels: Cluster labels for each sample (-1 = noise)
    """
    print(f"\nClustering {len(features)} samples with DBSCAN...")
    print(f"  eps={eps}, min_samples={min_samples}, metric=cosine")

    # DBSCAN with cosine metric
    # Note: For cosine, distance = 1 - cosine_similarity
    # So eps=0.5 means cosine_similarity >= 0.5
    clustering = DBSCAN(
        eps=eps,
        min_samples=min_samples,
        metric='cosine',
        n_jobs=-1  # Use all CPU cores
    )

    print("Running DBSCAN (this may take a while for large datasets)...")
    labels = clustering.fit_predict(features)

    return labels


def analyze_clusters(labels: np.ndarray) -> dict:
    """Analyze clustering results and return statistics."""
    unique_labels = set(labels)
    n_clusters = len(unique_labels) - (1 if -1 in unique_labels else 0)
    n_noise = np.sum(labels == -1)

    # Get cluster sizes (excluding noise)
    cluster_sizes = []
    for label in unique_labels:
        if label != -1:
            size = np.sum(labels == label)
            cluster_sizes.append((label, size))

    cluster_sizes.sort(key=lambda x: x[1], reverse=True)

    stats = {
        'n_clusters': n_clusters,
        'n_noise': n_noise,
        'n_total': len(labels),
        'cluster_sizes': cluster_sizes,
        'largest_cluster': cluster_sizes[0] if cluster_sizes else None,
        'smallest_cluster': cluster_sizes[-1] if cluster_sizes else None,
    }

    return stats


# =============================================================================
# Output Organization
# =============================================================================

def organize_clusters(
    labels: np.ndarray,
    image_paths: List[str],
    output_folder: str,
    corrupted_paths: List[str] = None
) -> None:
    """
    Organize images into cluster folders.

    Args:
        labels: Cluster labels for each image
        image_paths: Paths to original images (aligned with labels)
        output_folder: Base output folder
        corrupted_paths: Optional list of corrupted image paths
    """
    output_path = Path(output_folder)

    # Clean output folder if exists
    if output_path.exists():
        print(f"\nRemoving existing output folder: {output_path}")
        shutil.rmtree(output_path)

    # Create output structure
    output_path.mkdir(parents=True, exist_ok=True)
    garbage_folder = output_path / "garbage"
    garbage_folder.mkdir(exist_ok=True)

    # Create corrupted folder if needed
    if corrupted_paths:
        corrupted_folder = output_path / "corrupted"
        corrupted_folder.mkdir(exist_ok=True)

    # Get unique cluster labels
    unique_labels = sorted(set(labels))
    cluster_id_map = {}

    # Create folders for valid clusters
    cluster_num = 1
    for label in unique_labels:
        if label != -1:
            folder_name = f"Person_{cluster_num:04d}"
            cluster_folder = output_path / folder_name
            cluster_folder.mkdir(exist_ok=True)
            cluster_id_map[label] = folder_name
            cluster_num += 1

    print(f"\nOrganizing {len(image_paths)} images into {len(cluster_id_map)} clusters...")

    # Copy images to respective folders
    for idx, (label, img_path) in enumerate(tqdm(
        zip(labels, image_paths),
        total=len(labels),
        desc="Copying images"
    )):
        src_path = Path(img_path)

        if label == -1:
            # Noise -> garbage folder
            dst_path = garbage_folder / src_path.name
        else:
            # Valid cluster
            folder_name = cluster_id_map[label]
            dst_path = output_path / folder_name / src_path.name

        # Handle duplicate filenames
        if dst_path.exists():
            stem = dst_path.stem
            suffix = dst_path.suffix
            counter = 1
            while dst_path.exists():
                dst_path = dst_path.parent / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.copy2(src_path, dst_path)

    # Copy corrupted images
    if corrupted_paths:
        print(f"Moving {len(corrupted_paths)} corrupted images...")
        for img_path in tqdm(corrupted_paths, desc="Moving corrupted"):
            src_path = Path(img_path)
            dst_path = output_path / "corrupted" / src_path.name
            if dst_path.exists():
                stem = dst_path.stem
                suffix = dst_path.suffix
                counter = 1
                while dst_path.exists():
                    dst_path = dst_path.parent / f"{stem}_{counter}{suffix}"
                    counter += 1
            try:
                shutil.copy2(src_path, dst_path)
            except Exception:
                pass  # Skip if can't copy corrupted file


def print_report(stats: dict, output_folder: str) -> None:
    """Print clustering report."""
    print("\n" + "=" * 60)
    print("CLUSTERING REPORT")
    print("=" * 60)

    print(f"\nTotal images processed: {stats['n_total']}")
    print(f"Unique clusters (people) found: {stats['n_clusters']}")
    print(f"Noise images (unassigned): {stats['n_noise']} ({100*stats['n_noise']/stats['n_total']:.1f}%)")

    if stats['largest_cluster']:
        label, size = stats['largest_cluster']
        print(f"\nLargest cluster: {size} images")

    if stats['smallest_cluster']:
        label, size = stats['smallest_cluster']
        print(f"Smallest cluster: {size} images")

    if stats['cluster_sizes']:
        sizes = [s for _, s in stats['cluster_sizes']]
        print(f"\nCluster size statistics:")
        print(f"  Mean: {np.mean(sizes):.1f}")
        print(f"  Median: {np.median(sizes):.1f}")
        print(f"  Std: {np.std(sizes):.1f}")

    print(f"\nResults saved to: {output_folder}")
    print("=" * 60)


# =============================================================================
# Main Pipeline
# =============================================================================

def main(args):
    """Main clustering pipeline."""

    # Configuration
    input_folder = args.input or Config.INPUT_FOLDER
    output_folder = args.output or Config.OUTPUT_FOLDER
    batch_size = args.batch_size or Config.BATCH_SIZE
    eps = args.eps or Config.DBSCAN_EPS
    min_samples = args.min_samples or Config.DBSCAN_MIN_SAMPLES
    model_type = args.model or Config.MODEL_TYPE
    device = args.device or Config.DEVICE

    print("=" * 60)
    print("PURE VISUAL CLUSTERING PIPELINE")
    print("=" * 60)
    print(f"\nConfiguration:")
    print(f"  Input folder: {input_folder}")
    print(f"  Output folder: {output_folder}")
    print(f"  Model: {model_type}")
    print(f"  Device: {device}")
    print(f"  Batch size: {batch_size}")
    print(f"  DBSCAN eps: {eps}")
    print(f"  DBSCAN min_samples: {min_samples}")

    # Verify input folder exists
    if not Path(input_folder).exists():
        raise FileNotFoundError(f"Input folder not found: {input_folder}")

    # Step 1: Build dataset and dataloader
    print("\n" + "-" * 40)
    print("STEP 1: Loading Dataset")
    print("-" * 40)

    transform = get_transforms(Config.IMAGE_SIZE)
    dataset = ImageFolderFlat(input_folder, transform=transform)

    if len(dataset) == 0:
        raise RuntimeError("No images found in the input folder!")

    dataloader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=Config.NUM_WORKERS,
        collate_fn=collate_fn_skip_none,
        pin_memory=True if device == 'cuda' else False
    )

    # Step 2: Load model
    print("\n" + "-" * 40)
    print("STEP 2: Loading Model")
    print("-" * 40)

    model = get_model(model_type)

    # Step 3: Extract features
    print("\n" + "-" * 40)
    print("STEP 3: Feature Extraction")
    print("-" * 40)

    features, valid_paths, corrupted_paths = extract_features(
        model, dataloader, device
    )

    # Free GPU memory
    del model
    if device == 'cuda':
        torch.cuda.empty_cache()

    # Step 3.5: Analyze distance distribution (optional)
    if args.analyze or args.auto_eps:
        print("\n" + "-" * 40)
        print("STEP 3.5: Distance Distribution Analysis")
        print("-" * 40)

        dist_stats = analyze_distance_distribution(features)

        if args.analyze:
            # Analysis only mode - exit here
            print("\nAnalysis complete. Use the recommended eps values above.")
            print("Run again without --analyze to perform clustering.")
            return dist_stats

        if args.auto_eps:
            # Use recommended eps
            eps = dist_stats['recommended_eps']
            print(f"\nUsing auto-detected eps: {eps:.4f}")

    # Step 4: Clustering
    print("\n" + "-" * 40)
    print("STEP 4: DBSCAN Clustering")
    print("-" * 40)

    labels = cluster_features(features, eps=eps, min_samples=min_samples)
    stats = analyze_clusters(labels)

    # Step 5: Organize output
    print("\n" + "-" * 40)
    print("STEP 5: Organizing Results")
    print("-" * 40)

    organize_clusters(labels, valid_paths, output_folder, corrupted_paths)

    # Step 6: Report
    print_report(stats, output_folder)

    return stats


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Cluster person crops by pure visual similarity"
    )

    parser.add_argument(
        '--input', '-i',
        type=str,
        default=None,
        help=f"Input folder with images (default: {Config.INPUT_FOLDER})"
    )

    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help=f"Output folder for results (default: {Config.OUTPUT_FOLDER})"
    )

    parser.add_argument(
        '--model', '-m',
        type=str,
        choices=['osnet', 'resnet50'],
        default=None,
        help=f"Model type for feature extraction (default: {Config.MODEL_TYPE})"
    )

    parser.add_argument(
        '--batch-size', '-b',
        type=int,
        default=None,
        help=f"Batch size for inference (default: {Config.BATCH_SIZE})"
    )

    parser.add_argument(
        '--eps', '-e',
        type=float,
        default=None,
        help=f"DBSCAN eps parameter (default: {Config.DBSCAN_EPS})"
    )

    parser.add_argument(
        '--min-samples', '-s',
        type=int,
        default=None,
        help=f"DBSCAN min_samples parameter (default: {Config.DBSCAN_MIN_SAMPLES})"
    )

    parser.add_argument(
        '--device', '-d',
        type=str,
        choices=['cuda', 'cpu'],
        default=None,
        help=f"Device for inference (default: auto-detect)"
    )

    parser.add_argument(
        '--analyze', '-a',
        action='store_true',
        help="Analyze distance distribution to find optimal eps (no clustering)"
    )

    parser.add_argument(
        '--auto-eps',
        action='store_true',
        help="Automatically determine eps from distance distribution (10th percentile)"
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args)
