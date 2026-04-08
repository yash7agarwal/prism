"""
tools/visual_diff.py — Visual screenshot comparison

Compares two screenshots and returns differences.
Uses pixelmatch for accurate pixel-level diffing with perceptual threshold.
Falls back to SSIM-approximation via PIL if pixelmatch unavailable.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Threshold above which a diff is considered "significant" (in percent)
_SIGNIFICANCE_THRESHOLD = 5.0

try:
    from pixelmatch.contrib.PIL import pixelmatch as _pixelmatch
    _PIXELMATCH_AVAILABLE = True
    logger.debug("[visual_diff] pixelmatch available — using pixel-accurate diffing")
except ImportError:
    _PIXELMATCH_AVAILABLE = False
    logger.debug("[visual_diff] pixelmatch not available — using PIL fallback")

from PIL import Image, ImageChops, ImageDraw


def resize_to_match(img_a: Image.Image, img_b: Image.Image) -> tuple[Image.Image, Image.Image]:
    """
    Resize img_b to match img_a's dimensions if they differ.
    Returns the (possibly unchanged) pair.
    """
    if img_a.size != img_b.size:
        logger.debug(
            f"[visual_diff] Resizing image from {img_b.size} to {img_a.size}"
        )
        img_b = img_b.resize(img_a.size, Image.LANCZOS)
    return img_a, img_b


def _assessment_from_pct(diff_pct: float) -> str:
    """Map a diff percentage to a human-readable assessment string."""
    if diff_pct == 0.0:
        return "identical"
    elif diff_pct <= 1.0:
        return "minor_differences"
    elif diff_pct <= 10.0:
        return "significant_differences"
    else:
        return "major_differences"


def _compare_with_pixelmatch(
    img_a: Image.Image,
    img_b: Image.Image,
    threshold: float,
    output_diff_path: str | None,
) -> tuple[int, Image.Image | None]:
    """
    Use pixelmatch to compare two PIL images.
    Returns (num_different_pixels, diff_image_or_None).
    """
    img_a = img_a.convert("RGBA")
    img_b = img_b.convert("RGBA")
    img_a, img_b = resize_to_match(img_a, img_b)

    diff_img = Image.new("RGBA", img_a.size)
    mismatch = _pixelmatch(img_a, img_b, diff_img, threshold=threshold, includeAA=False)
    return mismatch, diff_img


def _compare_with_pil(
    img_a: Image.Image,
    img_b: Image.Image,
    output_diff_path: str | None,
) -> tuple[int, Image.Image | None]:
    """
    PIL-based fallback comparison using ImageChops.difference.
    Highlights differing regions in red.
    Returns (approx_different_pixels, diff_image_or_None).
    """
    img_a = img_a.convert("RGB")
    img_b = img_b.convert("RGB")
    img_a, img_b = resize_to_match(img_a, img_b)

    diff = ImageChops.difference(img_a, img_b)

    # Convert to grayscale to count non-zero pixels
    diff_gray = diff.convert("L")
    pixels = list(diff_gray.getdata())
    different = sum(1 for p in pixels if p > 10)  # tolerance for minor noise

    diff_img: Image.Image | None = None
    if output_diff_path is not None:
        # Create a red-highlight diff image
        diff_img = img_a.copy().convert("RGBA")
        width, height = img_a.size
        overlay = Image.new("RGBA", img_a.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        diff_pixels = list(diff_gray.getdata())
        for idx, val in enumerate(diff_pixels):
            if val > 10:
                x = idx % width
                y = idx // width
                draw.point((x, y), fill=(255, 0, 0, 180))

        diff_img = Image.alpha_composite(diff_img, overlay)

    return different, diff_img


def compare_screenshots(
    path_a: str,
    path_b: str,
    output_diff_path: str | None = None,
    threshold: float = 0.1,
) -> dict:
    """
    Compare two screenshots pixel-by-pixel and return a diff result.

    Args:
        path_a:            Path to the baseline screenshot.
        path_b:            Path to the candidate screenshot.
        output_diff_path:  Optional path to save the diff image. If None, no image is saved.
        threshold:         Per-pixel sensitivity (0–1). Lower = stricter. Used by pixelmatch only.

    Returns:
        {
            "diff_percentage":   float,         # % of different pixels
            "different_pixels":  int,
            "total_pixels":      int,
            "diff_image_path":   str | None,    # path if saved, else None
            "is_significant":    bool,           # True if diff_percentage > 5.0
            "assessment":        str,            # "identical" | "minor_differences" | ...
        }
    """
    if not os.path.exists(path_a):
        raise FileNotFoundError(f"Baseline screenshot not found: {path_a}")
    if not os.path.exists(path_b):
        raise FileNotFoundError(f"Candidate screenshot not found: {path_b}")

    img_a = Image.open(path_a)
    img_b = Image.open(path_b)

    total_pixels = img_a.width * img_a.height

    if _PIXELMATCH_AVAILABLE:
        try:
            different, diff_img = _compare_with_pixelmatch(img_a, img_b, threshold, output_diff_path)
        except Exception as e:
            logger.warning(f"[visual_diff] pixelmatch failed ({e}), falling back to PIL")
            different, diff_img = _compare_with_pil(img_a, img_b, output_diff_path)
    else:
        different, diff_img = _compare_with_pil(img_a, img_b, output_diff_path)

    diff_pct = round(different / total_pixels * 100, 4) if total_pixels > 0 else 0.0

    saved_path: str | None = None
    if output_diff_path is not None and diff_img is not None:
        Path(output_diff_path).parent.mkdir(parents=True, exist_ok=True)
        diff_img.convert("RGB").save(output_diff_path)
        saved_path = output_diff_path
        logger.debug(f"[visual_diff] Diff image saved: {output_diff_path}")

    assessment = _assessment_from_pct(diff_pct)
    is_significant = diff_pct > _SIGNIFICANCE_THRESHOLD

    logger.info(
        f"[visual_diff] {os.path.basename(path_a)} vs {os.path.basename(path_b)}: "
        f"{diff_pct}% diff ({assessment})"
    )

    return {
        "diff_percentage": diff_pct,
        "different_pixels": different,
        "total_pixels": total_pixels,
        "diff_image_path": saved_path,
        "is_significant": is_significant,
        "assessment": assessment,
    }


def batch_compare(
    pairs: list[tuple[str, str]],
    output_dir: str,
) -> list[dict]:
    """
    Compare multiple screenshot pairs.

    Args:
        pairs:      List of (baseline_path, candidate_path) tuples.
        output_dir: Directory where diff images will be saved.

    Returns:
        List of compare_screenshots() result dicts, each with an added "pair_index" key.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for idx, (path_a, path_b) in enumerate(pairs):
        # Derive a diff filename from the index and the candidate filename
        candidate_name = Path(path_b).stem
        diff_filename = f"diff_{idx:03d}_{candidate_name}.png"
        diff_path = str(Path(output_dir) / diff_filename)

        try:
            result = compare_screenshots(path_a, path_b, output_diff_path=diff_path)
        except Exception as e:
            logger.error(f"[visual_diff] batch_compare pair {idx} failed: {e}")
            result = {
                "diff_percentage": 0.0,
                "different_pixels": 0,
                "total_pixels": 0,
                "diff_image_path": None,
                "is_significant": False,
                "assessment": "error",
                "error": str(e),
            }

        result["pair_index"] = idx
        results.append(result)

    return results
