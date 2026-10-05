"""Contact-sheet preview generator.

Produces a grid image showing all rendered flap slots as they would appear
on the physical splitflap display: two flap halves (top + bottom) separated
by the real gap, with the physical flap shape (rounded outer corners +
spool-pin notch cutouts on the inner sides) rendered in the configured
flap colour.  Artwork is clipped to the flap outline and alpha-composited
on top so transparent regions show the flap colour.
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .config import JobConfig
from .dimensions import AllDimensions, mm_to_px

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shape drawing
# ---------------------------------------------------------------------------

def _draw_flap_half(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    corner_r: int,
    notch_h: int,
    notch_d: int,
    flap_color: tuple,
    bg_color: tuple,
    notch_at_bottom: bool,
) -> None:
    """Draw one physical flap half shape onto *draw* at cell position (x, y).

    The outer corners on the *far* side from the spool are rounded;
    the inner corners (nearest the spool / gap) are square to match the
    physical geometry.  Spool-pin notch cutouts are drawn in *bg_color*
    on the appropriate side.

    notch_at_bottom=True  → top flap half  (rounded top, notch at bottom)
    notch_at_bottom=False → bottom flap half (notch at top, rounded bottom)
    """
    if notch_at_bottom:
        corners = (True, True, False, False)   # top-left, top-right rounded
    else:
        corners = (False, False, True, True)   # bottom-right, bottom-left rounded

    draw.rounded_rectangle(
        (x, y, x + w - 1, y + h - 1),
        radius=corner_r,
        fill=flap_color,
        corners=corners,
    )

    # Notch cutouts — overdraw with bg_color
    if notch_at_bottom:
        # Left notch at bottom edge
        draw.rectangle((x, y + h - notch_h, x + notch_d - 1, y + h - 1), fill=bg_color)
        # Right notch at bottom edge
        draw.rectangle((x + w - notch_d, y + h - notch_h, x + w - 1, y + h - 1), fill=bg_color)
    else:
        # Left notch at top edge
        draw.rectangle((x, y, x + notch_d - 1, y + notch_h - 1), fill=bg_color)
        # Right notch at top edge
        draw.rectangle((x + w - notch_d, y, x + w - 1, y + notch_h - 1), fill=bg_color)


def _make_flap_mask(
    w: int,
    h: int,
    corner_r: int,
    notch_h: int,
    notch_d: int,
    notch_at_bottom: bool,
) -> Image.Image:
    """Return a grayscale mask (L mode) for one flap half.

    White (255) = inside the flap outline; black (0) = outside.
    Used to clip artwork so nothing bleeds beyond the physical flap shape.
    """
    mask = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask)

    if notch_at_bottom:
        corners = (True, True, False, False)
    else:
        corners = (False, False, True, True)

    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=corner_r, fill=255, corners=corners)

    # Cut out notch areas
    if notch_at_bottom:
        draw.rectangle((0, h - notch_h, notch_d - 1, h - 1), fill=0)
        draw.rectangle((w - notch_d, h - notch_h, w - 1, h - 1), fill=0)
    else:
        draw.rectangle((0, 0, notch_d - 1, notch_h - 1), fill=0)
        draw.rectangle((w - notch_d, 0, w - 1, notch_h - 1), fill=0)

    return mask


# ---------------------------------------------------------------------------
# Cell rendering
# ---------------------------------------------------------------------------

def _load_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a proportional font at *size_px*, falling back to the PIL default."""
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size_px)
        except (OSError, IOError):
            pass
    return ImageFont.load_default()


def _render_preview_cell(
    top_half: Image.Image,
    bottom_half: Image.Image,
    label: str,
    dims: AllDimensions,
    preview_dpi: float,
    flap_color: tuple,
    bg_color: tuple,
    label_color: tuple,
    label_font_size_pt: int,
) -> Image.Image:
    """Render one preview cell: top flap + gap + bottom flap + label below.

    top_half and bottom_half may be at any resolution; they are scaled to
    the preview cell size with LANCZOS resampling.
    """
    flap_w = mm_to_px(dims.flap.width, preview_dpi)
    flap_h = mm_to_px(dims.flap.height, preview_dpi)
    gap_h = max(1, mm_to_px(dims.flap.gap, preview_dpi))
    corner_r = max(1, mm_to_px(dims.flap.corner_radius, preview_dpi))
    notch_h = max(1, mm_to_px(dims.flap.notch_height, preview_dpi))
    notch_d = max(1, mm_to_px(dims.flap.notch_depth, preview_dpi))

    font_size_px = max(8, round(label_font_size_pt * preview_dpi / 72.0))
    label_pad_px = max(2, mm_to_px(1.5, preview_dpi))
    label_area_h = font_size_px + 2 * label_pad_px

    display_h = flap_h * 2 + gap_h
    cell_h = display_h + label_area_h

    bg_rgba = (*bg_color, 255)
    flap_rgba = (*flap_color, 255)

    cell = Image.new('RGBA', (flap_w, cell_h), bg_rgba)
    draw = ImageDraw.Draw(cell)

    # Flap shapes
    _draw_flap_half(draw, 0, 0, flap_w, flap_h,
                    corner_r, notch_h, notch_d, flap_rgba, bg_rgba,
                    notch_at_bottom=True)
    _draw_flap_half(draw, 0, flap_h + gap_h, flap_w, flap_h,
                    corner_r, notch_h, notch_d, flap_rgba, bg_rgba,
                    notch_at_bottom=False)

    # Artwork composited over the flap shapes, clipped to flap outline
    mask_top = _make_flap_mask(flap_w, flap_h, corner_r, notch_h, notch_d, notch_at_bottom=True)
    mask_bot = _make_flap_mask(flap_w, flap_h, corner_r, notch_h, notch_d, notch_at_bottom=False)

    top_r = top_half.convert('RGBA').resize((flap_w, flap_h), Image.LANCZOS)
    bottom_r = bottom_half.convert('RGBA').resize((flap_w, flap_h), Image.LANCZOS)

    # Intersect artwork alpha with the flap mask so pixels outside the outline
    # are fully transparent before compositing onto the cell.
    top_r.putalpha(ImageChops.multiply(top_r.getchannel('A'), mask_top))
    bottom_r.putalpha(ImageChops.multiply(bottom_r.getchannel('A'), mask_bot))

    cell.alpha_composite(top_r, dest=(0, 0))
    cell.alpha_composite(bottom_r, dest=(0, flap_h + gap_h))

    # Label centred below the flap rendering
    font = _load_font(font_size_px)
    label_y = display_h + label_pad_px
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_x = max(0, (flap_w - text_w) // 2)
    draw.text((text_x, label_y), label, fill=(*label_color, 255), font=font)

    return cell


# ---------------------------------------------------------------------------
# Grid assembly
# ---------------------------------------------------------------------------

def generate_preview(
    preview_entries: list[tuple[Image.Image, Image.Image, str]],
    config: JobConfig,
    dims: AllDimensions,
    out_dir: Path,
) -> Path | None:
    """Assemble a contact-sheet preview grid and save it.

    *preview_entries* is a list of (top_half, bottom_half, label) tuples at
    any resolution; they are scaled to preview DPI inside each cell.

    Returns the path of the saved file, or None if the entry list is empty.
    """
    if not preview_entries:
        logger.warning("preview: no entries to render")
        return None

    pv = config.preview
    preview_dpi = float(pv.dpi)
    border_px = max(2, mm_to_px(pv.cell_padding_mm, preview_dpi))
    gap_mm = pv.inter_cell_gap_mm if pv.inter_cell_gap_mm is not None else pv.cell_padding_mm
    gap_px = max(0, mm_to_px(gap_mm, preview_dpi))

    flap_color = tuple(int(c) for c in pv.flap_color)
    bg_color = tuple(int(c) for c in pv.background_color)
    label_color = tuple(int(c) for c in pv.label_color)

    cells = [
        _render_preview_cell(top, bottom, label, dims, preview_dpi,
                             flap_color, bg_color, label_color,
                             pv.label_font_size_pt)
        for top, bottom, label in preview_entries
    ]

    n = len(cells)
    cols = max(1, min(pv.columns, n))
    rows = math.ceil(n / cols)
    cell_w, cell_h = cells[0].size

    canvas_w = cols * cell_w + (cols - 1) * gap_px + 2 * border_px
    canvas_h = rows * cell_h + (rows - 1) * gap_px + 2 * border_px
    canvas = Image.new('RGB', (canvas_w, canvas_h), bg_color)

    for i, cell in enumerate(cells):
        row_i = i // cols
        col_i = i % cols
        px = border_px + col_i * (cell_w + gap_px)
        py = border_px + row_i * (cell_h + gap_px)
        canvas.paste(cell.convert('RGB'), (px, py), cell)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / pv.filename

    # The preview is the one output users keep open in an image viewer
    # between renders, and Windows Photos (among others) intermittently
    # holds a mapping on the displayed file — overwriting it then fails
    # with a transient OSError (observed as [Errno 22] Invalid argument).
    # Write to a temp file and swap it in with retries; if the target
    # stays locked, fall back to a numbered name and warn instead of
    # failing the whole render (the batch images are already written).
    tmp_path = out_path.with_suffix(out_path.suffix + '.tmp')
    canvas.save(str(tmp_path), format='PNG', dpi=(preview_dpi, preview_dpi))
    for attempt in range(4):
        try:
            tmp_path.replace(out_path)
            break
        except OSError:
            time.sleep(0.4 * (attempt + 1))
    else:
        fallback = out_path.with_stem(out_path.stem + '_new')
        try:
            tmp_path.replace(fallback)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            logger.warning("Preview could not be written (%s is held open by "
                           "another program — close the image viewer and "
                           "re-run).  Batch images were written normally.",
                           out_path)
            return None
        logger.warning("%s is held open by another program (image viewer?) — "
                       "preview written to %s instead.", out_path.name, fallback)
        out_path = fallback

    logger.info("Wrote preview: %s (%d×%d px, %d cells)", out_path, canvas_w, canvas_h, n)
    return out_path
