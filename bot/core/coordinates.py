"""
bot.core.coordinates
~~~~~~~~~~~~~~~~~~~~
Canonical CoordinateMapper architecture.
Implements rigorous bidirectional transformations across:
  Physical Screen Pixels (DXGI / Win32 Virtual Desktop)
  <-> Window Client Pixels (Win32 Client Area)
  <-> Browser Viewport Pixels
  <-> CSS Pixels (devicePixelRatio scaled)
  <-> Region-Local Normalized Coordinates [0.0, 1.0]
"""

from dataclasses import dataclass
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rect:
    """Represents a rectangle in integer pixel space."""
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom


@dataclass(frozen=True)
class ViewportContext:
    """
    Contextual information about window, viewport, and DPI scaling.
    """
    window_rect: Rect               # Screen physical bounds of window (GetWindowRect)
    client_rect: Rect               # Screen physical bounds of client area (ClientToScreen origin)
    viewport_offset: Tuple[int, int] = (0, 0) # Offset from client area to browser content viewport (e.g. tabs/url bar)
    device_pixel_ratio: float = 1.0 # window.devicePixelRatio
    scroll_offset: Tuple[int, int] = (0, 0)   # window.scrollX, window.scrollY

    @property
    def inner_width(self) -> float:
        """Browser content viewport width in CSS pixels."""
        return max(1.0, (self.client_rect.w - self.viewport_offset[0]) / max(0.1, self.device_pixel_ratio))

    @property
    def inner_height(self) -> float:
        """Browser content viewport height in CSS pixels."""
        return max(1.0, (self.client_rect.h - self.viewport_offset[1]) / max(0.1, self.device_pixel_ratio))


class CoordinateMapper:
    """
    Canonical Bidirectional Coordinate Mapper.
    Guarantees pure conversions without silent rounding drifts.
    """

    @staticmethod
    def screen_to_region_normalized(
        screen_x: int,
        screen_y: int,
        region_rect: Rect
    ) -> Tuple[float, float]:
        """
        Converts Physical Screen Pixels to Region-Local Normalized [0.0, 1.0].
        """
        if region_rect.w <= 0 or region_rect.h <= 0:
            raise ValueError(f"Invalid region dimensions: {region_rect}")
        u = (screen_x - region_rect.x) / float(region_rect.w)
        v = (screen_y - region_rect.y) / float(region_rect.h)
        return (u, v)

    @staticmethod
    def region_normalized_to_screen(
        u: float,
        v: float,
        region_rect: Rect
    ) -> Tuple[int, int]:
        """
        Converts Region-Local Normalized [0.0, 1.0] back to Physical Screen Pixels.
        """
        screen_x = int(round(region_rect.x + u * region_rect.w))
        screen_y = int(round(region_rect.y + v * region_rect.h))
        return (screen_x, screen_y)

    @staticmethod
    def screen_to_window_client(
        screen_x: int,
        screen_y: int,
        client_rect: Rect
    ) -> Tuple[int, int]:
        """
        Converts Physical Screen Pixels to Window Client Area Pixels.
        """
        return (screen_x - client_rect.x, screen_y - client_rect.y)

    @staticmethod
    def window_client_to_screen(
        client_x: int,
        client_y: int,
        client_rect: Rect
    ) -> Tuple[int, int]:
        """
        Converts Window Client Area Pixels to Physical Screen Pixels.
        """
        return (client_rect.x + client_x, client_rect.y + client_y)

    @staticmethod
    def screen_to_css_pixels(
        screen_x: int,
        screen_y: int,
        ctx: ViewportContext
    ) -> Tuple[float, float]:
        """
        Converts Physical Screen Pixels to CSS Viewport Pixels.
        Used for CDP Input.dispatchMouseEvent.
        """
        # Step 1: Screen -> Client Area
        client_x, client_y = CoordinateMapper.screen_to_window_client(screen_x, screen_y, ctx.client_rect)

        # Step 2: Client Area -> Browser Content Viewport
        view_x = client_x - ctx.viewport_offset[0]
        view_y = client_y - ctx.viewport_offset[1]

        # Step 3: Viewport Physical Pixels -> Viewport CSS Pixels via devicePixelRatio
        # Note: CDP Input.dispatchMouseEvent requires coordinates relative to the main frame's
        # visible viewport in CSS pixels (clientX, clientY). Document scroll_offset must NOT shift
        # viewport dispatch coordinates.
        dpr = max(0.1, ctx.device_pixel_ratio)
        css_x = view_x / dpr
        css_y = view_y / dpr

        return (css_x, css_y)

    @staticmethod
    def screen_to_document_css_pixels(
        screen_x: int,
        screen_y: int,
        ctx: ViewportContext
    ) -> Tuple[float, float]:
        """
        Converts Physical Screen Pixels to Document-Relative CSS Pixels (including page scroll).
        """
        css_x, css_y = CoordinateMapper.screen_to_css_pixels(screen_x, screen_y, ctx)
        return (css_x + ctx.scroll_offset[0], css_y + ctx.scroll_offset[1])

    @staticmethod
    def region_normalized_to_css_pixels(
        u: float,
        v: float,
        region_rect: Rect,
        ctx: ViewportContext
    ) -> Tuple[float, float]:
        """
        Direct conversion from Region-Local Normalized [0.0, 1.0] to CSS Viewport Pixels.
        """
        screen_x, screen_y = CoordinateMapper.region_normalized_to_screen(u, v, region_rect)
        return CoordinateMapper.screen_to_css_pixels(screen_x, screen_y, ctx)
