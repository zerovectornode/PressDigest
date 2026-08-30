"""Page rasterization.

Two persisted-vs-on-demand tiers (see config/default.yaml render section):
  - vision image (~1.25MP): persisted per page in the bronze layer, for
    Phase 2's vision pass.
  - high-res (300 DPI) and the line_no debug overlay: generated on demand
    for a specific page via the CLI, never persisted for all 18 pages by
    default (18 x 300 DPI renders would be hundreds of MB, opened rarely).

Line bbox coordinates are (x0, top, x1, bottom) in PDF points, top-down
(distance from the top of the page) - the same convention pdfplumber uses
for its own rendering, so scaling to pixels is a plain multiply by
(dpi / 72) with no axis flip.
"""
from __future__ import annotations

import math

from PIL import ImageDraw, ImageFont

from hindu_extract.models import Line


def compute_vision_dpi(width_pt: float, height_pt: float, target_megapixels: float) -> float:
    area_pt2 = width_pt * height_pt
    target_px = target_megapixels * 1_000_000
    return math.sqrt(target_px * 72 * 72 / area_pt2)


def render_vision_image(page, target_megapixels: float):
    dpi = compute_vision_dpi(page.width, page.height, target_megapixels)
    return page.to_image(resolution=dpi, antialias=True).original


def render_hires_image(page, dpi: float):
    return page.to_image(resolution=dpi, antialias=True).original


def render_debug_overlay(page, lines: list[Line], dpi: float):
    """High-res render with each line's bbox and line_no drawn on top."""
    image = render_hires_image(page, dpi).convert("RGB")
    draw = ImageDraw.Draw(image)
    scale = dpi / 72.0
    try:
        font = ImageFont.truetype("arial.ttf", 10)
    except OSError:
        font = ImageFont.load_default()

    for line in lines:
        x0, top, x1, bottom = line.bbox
        px = (x0 * scale, top * scale, x1 * scale, bottom * scale)
        color = "red" if line.flags.size_outlier else "blue"
        draw.rectangle(px, outline=color, width=1)
        draw.text((px[0], max(0, px[1] - 11)), f"L{line.line_no:04d}", fill=color, font=font)

    return image
