"""
tests/test_geometry.py
~~~~~~~~~~~~~~~~~~~~~~
Unit tests for Geometry Verification and TARGET_NOT_GEOMETRICALLY_SEPARABLE guard.
"""

import pytest
import numpy as np
import cv2
from bot.vision.geometry import check_geometry_separability, GeometryVerifier


def make_glyph_image(symbol: str, bg_color=(200, 50, 50), fg_color=(255, 255, 255)) -> np.ndarray:
    """Generates synthetic UI button with symbol ('X', 'O', 'CHECK')."""
    img = np.zeros((48, 48, 3), dtype=np.uint8)
    img[:] = bg_color # Background
    if symbol == "CHECK":
        # Draw checkmark
        cv2.line(img, (12, 24), (20, 36), fg_color, 3)
        cv2.line(img, (20, 36), (36, 12), fg_color, 3)
    elif symbol == "CROSS":
        # Draw X
        cv2.line(img, (12, 12), (36, 36), fg_color, 3)
        cv2.line(img, (12, 36), (36, 12), fg_color, 3)
    elif symbol == "CIRCLE":
        # Draw circle
        cv2.circle(img, (24, 24), 14, fg_color, 3)
    return img


def test_geometry_separability_guard_rejects_flat_targets():
    # 1. Solid color image -> Must FAIL
    solid_img = np.full((50, 50, 3), 128, dtype=np.uint8)
    ok, reason = check_geometry_separability(solid_img)
    assert not ok
    assert "TARGET_NOT_GEOMETRICALLY_SEPARABLE" in reason

    # 2. Very faint gradient -> Must FAIL
    gradient = np.tile(np.linspace(100, 105, 50, dtype=np.uint8), (50, 1))
    grad_img = cv2.cvtColor(gradient, cv2.COLOR_GRAY2BGR)
    ok, reason = check_geometry_separability(grad_img)
    assert not ok
    assert "TARGET_NOT_GEOMETRICALLY_SEPARABLE" in reason


def test_geometry_separability_guard_accepts_valid_symbol():
    # Valid glyph with sharp edges
    glyph_img = make_glyph_image("CHECK")
    ok, reason = check_geometry_separability(glyph_img)
    assert ok
    assert reason == "SEPARABLE"


def test_geometry_verifier_distinguishes_symbols_regardless_of_color():
    verifier = GeometryVerifier(canonical_size=(64, 64))

    img_check_blue = make_glyph_image("CHECK", bg_color=(200, 50, 50))
    img_check_green = make_glyph_image("CHECK", bg_color=(50, 200, 50)) # Same symbol, totally different color
    img_cross_blue = make_glyph_image("CROSS", bg_color=(200, 50, 50))  # Same color, different symbol

    # 1. Same symbol, different background color -> Score should be HIGH (> 0.70)
    score_same_sym = verifier.compute_geometry_score(img_check_blue, img_check_green)
    assert score_same_sym > 0.70, f"Expected high geometry score for same symbol with different color, got {score_same_sym}"

    # 2. Different symbol, identical background color -> Score should be LOW (< 0.40)
    score_diff_sym = verifier.compute_geometry_score(img_check_blue, img_cross_blue)
    assert score_diff_sym < 0.40, f"Expected low geometry score for different symbols with identical color, got {score_diff_sym}"

    # Verify separation margin
    assert score_same_sym > score_diff_sym + 0.30
