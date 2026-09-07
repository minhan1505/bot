"""
tests/test_coordinates.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for Canonical CoordinateMapper across all spaces:
Physical Screen <-> Window Client <-> Browser Viewport <-> CSS <-> Region Normalized.
"""

import pytest
from bot.core.coordinates import CoordinateMapper, Rect, ViewportContext


def test_region_normalized_bidirectional():
    region = Rect(x=100, y=200, w=400, h=300)

    # Point at center of region
    u, v = CoordinateMapper.screen_to_region_normalized(300, 350, region)
    assert u == pytest.approx(0.5, abs=1e-4)
    assert v == pytest.approx(0.5, abs=1e-4)

    # Convert back
    sx, sy = CoordinateMapper.region_normalized_to_screen(u, v, region)
    assert sx == 300
    assert sy == 350


def test_window_client_bidirectional():
    client_rect = Rect(x=50, y=80, w=1280, h=720)

    cx, cy = CoordinateMapper.screen_to_window_client(150, 180, client_rect)
    assert cx == 100
    assert cy == 100

    sx, sy = CoordinateMapper.window_client_to_screen(cx, cy, client_rect)
    assert sx == 150
    assert sy == 180


def test_css_pixels_scaling():
    # Test DPI scaling 125% (dpr = 1.25)
    ctx = ViewportContext(
        window_rect=Rect(0, 0, 1920, 1080),
        client_rect=Rect(0, 50, 1920, 1030),
        viewport_offset=(0, 40), # 40px browser tab/address bar
        device_pixel_ratio=1.25,
        scroll_offset=(0, 0)
    )

    # Screen Y = 90px (50px client offset + 40px viewport offset -> Viewport Y = 0)
    # Screen X = 125px -> Viewport X = 125px -> CSS X = 125 / 1.25 = 100 CSS px
    css_x, css_y = CoordinateMapper.screen_to_css_pixels(125, 90, ctx)
    assert css_x == pytest.approx(100.0, abs=1e-3)
    assert css_y == pytest.approx(0.0, abs=1e-3)
