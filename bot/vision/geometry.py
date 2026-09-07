"""
bot.vision.geometry
~~~~~~~~~~~~~~~~~~~
Geometry & Symbol Topology Verification Engine.
Mandatory Authority Gate: Evaluates candidate shape, contours, and edge topology
completely independent of background color, hue, saturation, or brightness.

Includes TARGET_NOT_GEOMETRICALLY_SEPARABLE guard to reject unviable targets at configuration time.
"""

import cv2
import numpy as np
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


def check_geometry_separability(target_img: np.ndarray) -> Tuple[bool, str]:
    """
    Guard: Validates that a user-provided target image contains sufficient structural/edge information
    to be distinguishable by geometry.
    If the target is a flat solid color, faint gradient, or completely lack distinct edges,
    this guard flags it as TARGET_NOT_GEOMETRICALLY_SEPARABLE.
    """
    if target_img is None or target_img.size == 0:
        return False, "TARGET_NOT_GEOMETRICALLY_SEPARABLE: Empty or invalid image."

    h, w = target_img.shape[:2]
    if h < 8 or w < 8:
        return False, f"TARGET_NOT_GEOMETRICALLY_SEPARABLE: Image dimensions too small ({w}x{h})."

    # Convert to grayscale
    gray = cv2.cvtColor(target_img, cv2.COLOR_BGR2GRAY) if len(target_img.shape) == 3 else target_img

    # Check gradient variance
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    if laplacian_var < 15.0:
        return False, f"TARGET_NOT_GEOMETRICALLY_SEPARABLE: Insufficient edge contrast/sharpness (Laplacian variance={laplacian_var:.2f} < 15.0)."

    # Extract Canny edges
    edges = cv2.Canny(gray, 50, 150)
    edge_pixel_count = cv2.countNonZero(edges)
    total_pixels = h * w
    edge_density = edge_pixel_count / float(total_pixels)

    if edge_density < 0.015:
        return False, f"TARGET_NOT_GEOMETRICALLY_SEPARABLE: Insufficient edge density ({edge_density:.4f} < 0.015)."

    # Check for distinguishable contours
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if len(contours) == 0:
        return False, "TARGET_NOT_GEOMETRICALLY_SEPARABLE: No structural contours detected."

    # Max contour must span a reasonable fraction of target
    max_area = max(cv2.contourArea(c) for c in contours)
    if max_area < 9.0: # At least 3x3 pixels
        return False, f"TARGET_NOT_GEOMETRICALLY_SEPARABLE: Largest contour area too small ({max_area:.1f}px)."

    return True, "SEPARABLE"


class GeometryVerifier:
    """
    Directional Distance Transform & Contour Topology Matcher.
    Computes a normalized geometric similarity score G(C) in [0.0, 1.0].
    """

    def __init__(self, canonical_size: Tuple[int, int] = (64, 64)):
        self.canonical_size = canonical_size

    def extract_edge_map(self, img: np.ndarray) -> np.ndarray:
        """Extracts binary edge map normalized to canonical size."""
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()

        # Canonical resize with aspect-ratio preservation (letterbox)
        resized = self.letterbox_resize(gray, self.canonical_size)

        # Bilateral filter to smooth texture while keeping sharp symbol edges
        filtered = cv2.bilateralFilter(resized, d=5, sigmaColor=50, sigmaSpace=50)

        # Auto-Canny based on median intensity
        v = np.median(filtered)
        lower = int(max(0, (1.0 - 0.33) * v))
        upper = int(min(255, (1.0 + 0.33) * v))
        if lower == upper:
            lower, upper = 50, 150

        edges = cv2.Canny(filtered, lower, upper)

        # Suppress outermost 2 pixels to eliminate rectangular crop boundary clipping artifacts
        edges[0:2, :] = 0
        edges[-2:, :] = 0
        edges[:, 0:2] = 0
        edges[:, -2:] = 0

        return edges

    def letterbox_resize(self, img: np.ndarray, target_size: Tuple[int, int]) -> np.ndarray:
        """Resizes image to target_size (W, H) maintaining aspect ratio with constant padding."""
        target_w, target_h = target_size
        h, w = img.shape[:2]
        scale = min(target_w / float(w), target_h / float(h))
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))

        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

        pad_x = (target_w - new_w) // 2
        pad_y = (target_h - new_h) // 2

        if len(img.shape) == 3:
            canvas = np.zeros((target_h, target_w, img.shape[2]), dtype=img.dtype)
            canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        else:
            canvas = np.zeros((target_h, target_w), dtype=img.dtype)
            canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

        return canvas

    def compute_geometry_score(self, candidate_img: np.ndarray, target_img: np.ndarray) -> float:
        """
        Computes Geometry Similarity Score G(C) in [0.0, 1.0].
        Uses bidirectional Distance Transform Chamfer matching.
        """
        edges_cand = self.extract_edge_map(candidate_img)
        edges_target = self.extract_edge_map(target_img)

        cnt_cand = cv2.countNonZero(edges_cand)
        cnt_target = cv2.countNonZero(edges_target)

        # If both are empty or edge counts drastically differ
        if cnt_cand == 0 or cnt_target == 0:
            return 0.0

        # Ratio of edge quantities check (prevent noisy patch from matching sparse symbol)
        edge_ratio = min(cnt_cand, cnt_target) / float(max(cnt_cand, cnt_target))
        if edge_ratio < 0.25:
            # Major topology mismatch
            return float(edge_ratio * 0.3)

        # Distance transform on inverted edges
        dt_target = cv2.distanceTransform(255 - edges_target, cv2.DIST_L2, 3)
        dt_cand = cv2.distanceTransform(255 - edges_cand, cv2.DIST_L2, 3)

        # Forward Chamfer: Candidate points -> Target DT
        cand_points = np.where(edges_cand > 0)
        forward_dist = np.mean(dt_target[cand_points])

        # Reverse Chamfer: Target points -> Candidate DT
        target_points = np.where(edges_target > 0)
        reverse_dist = np.mean(dt_cand[target_points])

        # Symmetric bidirectional distance
        sym_dist = (forward_dist + reverse_dist) / 2.0

        # Map distance to similarity [0.0, 1.0] using exponential decay
        # At dist = 0, score = 1.0; at dist = 5px, score ~= 0.36
        sigma = 4.0
        score = np.exp(- (sym_dist / sigma))

        return float(np.clip(score * edge_ratio, 0.0, 1.0))
