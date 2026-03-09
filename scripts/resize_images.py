"""Resize all images in a directory to 256x128 using multiprocessing."""

import os
import sys
from multiprocessing import Pool, cpu_count
from pathlib import Path

from PIL import Image

TARGET_SIZE = (256, 128)


def resize_one(img_path_str: str):
    img = Image.open(img_path_str)
    img_resized = img.resize(TARGET_SIZE, Image.LANCZOS)
    img_resized.save(img_path_str)


def resize_images(directory: str):
    dir_path = Path(directory)
    if not dir_path.is_dir():
        print(f"Error: '{directory}' is not a valid directory.")
        sys.exit(1)

    extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
    images = [str(f) for f in dir_path.iterdir() if f.suffix.lower() in extensions]

    if not images:
        print("No images found.")
        return

    workers = min(cpu_count(), 16)
    print(f"Resizing {len(images)} images to {TARGET_SIZE[0]}x{TARGET_SIZE[1]} with {workers} workers...")

    with Pool(workers) as pool:
        for i, _ in enumerate(pool.imap_unordered(resize_one, images, chunksize=64), 1):
            if i % 5000 == 0 or i == len(images):
                print(f"  {i}/{len(images)}")

    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <directory>")
        sys.exit(1)
    resize_images(sys.argv[1])
