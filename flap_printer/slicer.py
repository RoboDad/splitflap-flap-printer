"""Image slicing: split display images into flap top/bottom halves,
extract module columns from multi-module images, and apply transforms.
"""

from __future__ import annotations

from PIL import Image

from .dimensions import FlapDimensions, DisplayDimensions, mm_to_px


def slice_display_image(
    image: Image.Image,
    flap: FlapDimensions,
    dpi: float,
    bleed_y: int = 0,
    gap_bleed_px: int = 0,
) -> tuple[Image.Image, Image.Image]:
    """Split a full-character display image into top and bottom halves.

    The input image represents one full character as a viewer would see it:
    top-half (43mm) + gap (2.4mm) + bottom-half (43mm) = 88.4mm total.
    When *bleed_y* > 0, the image is taller by 2×bleed_y (bleed_y extra rows
    at the outer top and bleed_y extra rows at the outer bottom).  The
    returned halves preserve those outer bleed rows so the physical pocket
    area is centred within each half.

    When *gap_bleed_px* > 0, each half additionally keeps that many rows of
    the adjacent gap-strip content on its gap-side edge (capped at the gap
    height).  This is gap-edge bleed: the true continuation of the artwork
    past the cut line, so a slight print-vs-flap misalignment doesn't
    expose unprinted flap stock at the gap.  Unlike outer-edge bleed it
    requires no fit expansion or scaling — the rows already exist in the
    display image and would otherwise be discarded.

    Returns (top_half, bottom_half) as RGBA images:
      top half rows:    [bleed_y outer][flap_height][gap_bleed]
      bottom half rows: [gap_bleed][flap_height][bleed_y outer]
    The rest of the gap strip is discarded.
    """
    image = image.convert('RGBA')

    top_px = mm_to_px(flap.height, dpi)
    gap_px = mm_to_px(flap.gap, dpi)
    gb = max(0, min(gap_bleed_px, gap_px))
    w = image.width

    # Top half: outer top bleed + pocket + gap-side bleed
    top_half = image.crop((0, 0, w, top_px + bleed_y + gb))
    # Bottom half: gap-side bleed + pocket + outer bottom bleed
    bottom_start = top_px + bleed_y + gap_px
    bottom_half = image.crop((0, bottom_start - gb, w, bottom_start + top_px + bleed_y))

    return top_half, bottom_half


def extract_module_column(
    image: Image.Image,
    module_index: int,
    module_range: tuple[int, int],
    display: DisplayDimensions,
    dpi: float,
) -> Image.Image:
    """Extract a single module's column from a multi-module image.

    The input image spans modules module_range[0] through module_range[1]
    (inclusive), including the inter-module gaps.  module_index is the
    absolute module position to extract.

    Returns the column as an RGBA image with width = flap_width in pixels.
    """
    image = image.convert('RGBA')

    start_module = module_range[0]
    offset_modules = module_index - start_module

    # The composite canvas spans exactly from the left edge of the first flap
    # to the right edge of the last flap:
    #   canvas_width = N * pitch - inter_module_gap
    #                = N * (flap_width + inter_module_gap) - inter_module_gap
    # Within that canvas, flap k starts at k * pitch (no centering offset):
    #   flap 0 → [0, flap_width], flap 1 → [pitch, pitch + flap_width], …
    x_mm = offset_modules * display.module_pitch

    x_px = mm_to_px(x_mm, dpi)
    w_px = mm_to_px(display.module_width, dpi)

    column = image.crop((x_px, 0, x_px + w_px, image.height))
    return column



def apply_transforms(
    image: Image.Image,
    scale: tuple[float, float] | None = None,
    crop: tuple[float, float, float, float] | None = None,
) -> Image.Image:
    """Apply optional scale and crop transforms.

    scale: (sx, sy) — multiply image dimensions by these factors.
    crop: (left%, top%, right%, bottom%) — crop percentages from each edge.
    """
    image = image.convert('RGBA')

    if crop is not None:
        left_pct, top_pct, right_pct, bottom_pct = crop
        w, h = image.size
        left = round(w * left_pct / 100)
        top = round(h * top_pct / 100)
        right = w - round(w * right_pct / 100)
        bottom = h - round(h * bottom_pct / 100)
        image = image.crop((left, top, right, bottom))

    if scale is not None:
        sx, sy = scale
        new_w = max(1, round(image.width * sx))
        new_h = max(1, round(image.height * sy))
        image = image.resize((new_w, new_h), Image.LANCZOS)

    return image


def _flush_edges(
    image_w: int,
    image_h: int,
    target_w: int,
    target_h: int,
    fit_mode: str,
) -> tuple[bool, bool]:
    """Return (flush_x, flush_y): whether the fitted image reaches the target edges.

    flush_x — image fills the full target width (no left/right bars).
    flush_y — image fills the full target height (no top/bottom bars).
    Used to determine which axes need bleed-zone expansion.
    """
    if fit_mode == 'stretch':
        return True, True
    if fit_mode == 'contain':
        return False, False
    if fit_mode == 'fill':
        return True, True
    if fit_mode == 'fit':
        if image_w <= 0 or image_h <= 0 or target_w <= 0 or target_h <= 0:
            return False, False
        img_ar = image_w / image_h
        tgt_ar = target_w / target_h
        # Use a small relative tolerance so that images whose AR matches the
        # target (e.g. 54:88 SVGs, or any image pre-sized to exact flap dims)
        # are treated as filling both axes, not just one.  This matters because
        # a physical print-alignment error could expose any of the four edges.
        _AR_TOL = 1e-3
        ar_match = abs(img_ar - tgt_ar) / max(tgt_ar, 1e-9) < _AR_TOL
        if ar_match:
            return True, True
        # width-constrained: image wider than target ratio → fills x, bars in y
        if img_ar > tgt_ar:
            return True, False
        else:
            return False, True
    return False, False


def fit_to_target(
    image: Image.Image,
    target_w: int,
    target_h: int,
    mode: str = 'fit',
    bleed_px: int = 0,
) -> Image.Image:
    """Resize image to target dimensions using the specified fit mode.

    When *bleed_px* > 0, the target rectangle is expanded outward on axes
    where the image fills to the edge (flush edges), so the returned image
    extends into the bleed zone on those sides.  The physical pocket area
    occupies the centre of the returned image on bleed axes.

    Modes:
      fit     — uniform scale to fit within target; transparent letterbox, centered.
      fill    — uniform scale to fill target; center-crop overflow.
      stretch — non-uniform resize to exact target (may distort).
      contain — no scaling; center on target canvas, clip if larger.
    """
    image = image.convert('RGBA')

    # Expand target on flush axes so image content extends into the bleed zone
    flush_x, flush_y = _flush_edges(image.width, image.height, target_w, target_h, mode)
    bx = bleed_px if flush_x else 0
    by = bleed_px if flush_y else 0
    exp_w = target_w + 2 * bx
    exp_h = target_h + 2 * by

    if mode == 'stretch':
        return image.resize((exp_w, exp_h), Image.LANCZOS)

    if mode == 'fit':
        # Scale uniformly so the image fits entirely within the expanded target
        ratio = min(exp_w / image.width, exp_h / image.height)
        new_w = max(1, round(image.width * ratio))
        new_h = max(1, round(image.height * ratio))
        scaled = image.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new('RGBA', (exp_w, exp_h), (0, 0, 0, 0))
        paste_x = (exp_w - new_w) // 2
        paste_y = (exp_h - new_h) // 2
        canvas.paste(scaled, (paste_x, paste_y), scaled)
        return canvas

    if mode == 'fill':
        # Scale uniformly so the image covers the entire expanded target, then center-crop
        ratio = max(exp_w / image.width, exp_h / image.height)
        new_w = max(1, round(image.width * ratio))
        new_h = max(1, round(image.height * ratio))
        scaled = image.resize((new_w, new_h), Image.LANCZOS)
        crop_x = (new_w - exp_w) // 2
        crop_y = (new_h - exp_h) // 2
        return scaled.crop((crop_x, crop_y, crop_x + exp_w, crop_y + exp_h))

    if mode == 'contain':
        # No scaling — center the image on the expanded target canvas as-is
        canvas = Image.new('RGBA', (exp_w, exp_h), (0, 0, 0, 0))
        paste_x = (exp_w - image.width) // 2
        paste_y = (exp_h - image.height) // 2
        canvas.paste(image, (paste_x, paste_y), image)
        return canvas

    raise ValueError(f"Unknown fit_mode: {mode!r}")


def fit_with_notch_mode(
    image: Image.Image,
    target_w: int,
    target_h: int,
    fit_mode: str,
    notch_left: str,
    notch_right: str,
    notch_inset_px: int,
    bleed_px: int = 0,
) -> Image.Image:
    """Apply fit_mode with independent per-side notch-clearance modifiers.

    notch_left / notch_right — each independently 'none' | 'inset' | 'squeeze':
      'none'    No adjustment on that side.
      'inset'   Fit within the safe content area (target_w minus active side
                insets); paste with the per-side gaps preserved as transparent.
                Aspect ratio is maintained; image may be smaller overall.
      'squeeze' Fit to the full target_w first, then non-uniformly scale to
                the safe content width; paste with per-side gaps transparent.
                Height is preserved; horizontal aspect ratio changes.

    When both sides are 'none', equivalent to fit_to_target(target_w, target_h).
    When sides have different modes, 'squeeze' takes precedence for the
    image-fitting step (fit-to-content-width vs fit-then-squish).

    When *bleed_px* > 0:
    - No horizontal bleed is applied on notch sides (the notch zone is
      physically cut away, making side-edge bleed unnecessary).
    - Vertical bleed is applied on top and bottom when the image fills
      those edges, using the effective content-area dimensions for the
      flush-edge determination.
    """
    left_inset = notch_inset_px if notch_left != 'none' else 0
    right_inset = notch_inset_px if notch_right != 'none' else 0

    if (left_inset == 0 and right_inset == 0) or notch_inset_px <= 0:
        return fit_to_target(image, target_w, target_h, fit_mode, bleed_px)

    content_w = max(1, target_w - left_inset - right_inset)
    paste_x = left_inset

    active_modes = {m for m in (notch_left, notch_right) if m != 'none'}
    effective = 'squeeze' if 'squeeze' in active_modes else 'inset'

    # With any notch: suppress horizontal bleed (notch zone is transparent/cut away).
    # Vertical bleed follows flush-edge determination against the content-area dimensions.
    _, flush_y = _flush_edges(image.width, image.height, content_w, target_h, fit_mode)
    by = bleed_px if flush_y else 0
    exp_h = target_h + 2 * by

    if effective == 'inset':
        # Fit within the content area at the expanded height (bleed_px=0 — pre-expanded)
        fitted = fit_to_target(image, content_w, exp_h, fit_mode)
        canvas = Image.new('RGBA', (target_w, exp_h), (0, 0, 0, 0))
        canvas.paste(fitted, (paste_x, 0), fitted)
        return canvas

    # squeeze: fit to full width first, then squeeze to content width
    fitted = fit_to_target(image, target_w, exp_h, fit_mode)
    squeezed = fitted.resize((content_w, exp_h), Image.LANCZOS)
    canvas = Image.new('RGBA', (target_w, exp_h), (0, 0, 0, 0))
    canvas.paste(squeezed, (paste_x, 0), squeezed)
    return canvas
