"""Batch grid layout: arrange flap side images on the jig, apply flip
transforms, and generate ink-saving masks.

Two coordinate frames appear in this module:

- The **upright frame**: flap content as it appears on the display —
  character upright, flap.width across, flap.height tall, spool/pin edge
  at the bottom of a front (top-half) image.  All flap-side images and the
  flap mask polygon are built in this frame.
- The **sheet frame**: the output print image, identical to the physical
  jig on the printer bed viewed from above — long axis along X, mat zero
  point at the lower-right.  Flap pockets sit rotated 90° in the jig, so
  when an upright image is placed on the sheet it is rotated 90° CCW
  (``Image.ROTATE_90``): its top edge faces left and its spool edge faces
  right, toward the zero point.

The 90° CCW placement rotation is the ONLY transform between the two
frames, and it is applied per-flap at paste time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw

from .dimensions import FlapDimensions, JigDimensions, PrintableAreaDimensions, mm_to_px


@dataclass
class FlapSide:
    """One printable side of one flap."""
    image: Image.Image          # RGBA; flap_width × flap_height, or slightly larger when bleed is baked in
    label: str                  # e.g. "EP42"
    slot_index: int             # 0-based index in the custom flap sequence
    side: str                   # "front" or "back"


def map_images_to_flap_sides(
    slot_images: list[tuple[Image.Image, Image.Image]],
    labels: list[str],
) -> tuple[list[FlapSide], list[FlapSide]]:
    """Map sliced (top, bottom) image pairs to front and back FlapSide lists.

    slot_images: list of (top_half, bottom_half) for each custom flap slot.
    labels: list of EP labels, same length as slot_images.

    Mapping rule:
      - Front of slot K = top_half of slot K's image
      - Back of slot K  = bottom_half of slot (K+1)'s image
      - Back of last slot = blank (transparent)

    Returns (fronts, backs) — parallel lists of FlapSide.
    """
    n = len(slot_images)
    fronts: list[FlapSide] = []
    backs: list[FlapSide] = []

    for k in range(n):
        top_half, _ = slot_images[k]
        fronts.append(FlapSide(
            image=top_half,
            label=labels[k],
            slot_index=k,
            side="front",
        ))

        if k + 1 < n:
            _, bottom_half = slot_images[k + 1]
        else:
            # Last slot back is blank
            bottom_half = Image.new('RGBA', top_half.size, (0, 0, 0, 0))

        backs.append(FlapSide(
            image=bottom_half,
            label=labels[k],
            slot_index=k,
            side="back",
        ))

    return fronts, backs


def generate_batch_image(
    flap_sides: list[FlapSide],
    flap: FlapDimensions,
    jig: JigDimensions,
    printable: PrintableAreaDimensions,
    dpi: float,
    spool_at_bottom: bool = True,
    gap_bleed_px: int = 0,
) -> Image.Image:
    """Arrange up to jig.flaps_per_batch flap images on a printable-area-sized canvas.

    The canvas matches the full printable area of the printer bed (sheet
    frame).  The jig insert is positioned at (insert_offset_x,
    insert_offset_y) within that area, and the flap grid is positioned
    within the insert using margins.  Pockets sit rotated 90° in the jig,
    so a pocket's X extent is flap.height and its Y extent is flap.width;
    each upright flap image is rotated 90° CCW as it is pasted.

    Images in *flap_sides* may extend beyond the pocket boundary on flush edges
    (bleed zone encoded in the image size).  The paste position is offset so that
    the physical pocket area aligns with its grid coordinates.

    *spool_at_bottom* — True for front batches (display/outer edge at the
    upright top, spool at the upright bottom); False for back batches
    (upright spool at top).  In the sheet frame this means front sheets
    have their spool edges facing right (matching the jig pockets) and
    their outer-edge bleed extending left of the pocket; back sheets are
    the reverse.

    *gap_bleed_px* — how much of each image's along-X extra size is
    gap-edge bleed (on the spool side), the remainder being outer-edge
    bleed (on the display side).  Blanks and other images without the
    extra rows are unaffected (the per-image amount is capped by what the
    image actually carries).

    Returns an RGBA image at the correct physical size for the given DPI.
    """
    canvas_w = mm_to_px(printable.width, dpi)
    canvas_h = mm_to_px(printable.height, dpi)

    canvas = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))

    # Insert origin within the printable area
    insert_x_px = mm_to_px(printable.insert_offset_x, dpi)
    insert_y_px = mm_to_px(printable.insert_offset_y, dpi)

    flap_w_px = mm_to_px(flap.width, dpi)
    flap_h_px = mm_to_px(flap.height, dpi)
    margin_x_px = mm_to_px(jig.margin_x, dpi)
    margin_y_px = mm_to_px(jig.margin_y, dpi)
    # Pockets are rotated 90°: X extent = flap.height, Y extent = flap.width
    space_x_px = mm_to_px(flap.height + jig.gap_x, dpi)
    space_y_px = mm_to_px(flap.width + jig.gap_y, dpi)
    row_pitch_px = mm_to_px(jig.row_pitch, dpi)

    for i, fs in enumerate(flap_sides[:jig.flaps_per_sheet]):
        col = i % jig.num_x
        grid_row = i // jig.num_x
        insert_idx = grid_row // jig.num_y     # which insert row (0 on the minibed)
        pocket_row = grid_row % jig.num_y      # pocket row within the insert

        pocket_x = insert_x_px + margin_x_px + col * space_x_px
        pocket_y = insert_y_px + insert_idx * row_pitch_px + margin_y_px + pocket_row * space_y_px

        # Rotate the upright image into the sheet frame: top edge → left,
        # spool edge → right.
        img = fs.image.transpose(Image.ROTATE_90)

        # Bleed amounts are encoded in the image dimensions vs the pocket
        # size.  After rotation the pocket occupies flap_h_px along X and
        # flap_w_px along Y within the image.  The along-X extra splits into
        # gap-edge bleed (spool side) and outer-edge bleed (display side).
        extra_x = max(0, img.width - flap_h_px)
        gap_x = min(gap_bleed_px, extra_x)               # gap-edge bleed (spool side)
        outer_x = extra_x - gap_x                        # outer-edge bleed (display side)
        bleed_y_total = max(0, img.height - flap_w_px)   # side bleed, centred on the pocket

        if spool_at_bottom:
            # Front: display edge faces left, spool/gap edge right —
            # outer bleed extends left of the pocket, gap bleed right
            paste_x = pocket_x - outer_x
        else:
            # Back: display edge faces right, spool/gap edge left —
            # gap bleed extends left of the pocket, outer bleed right
            paste_x = pocket_x - gap_x
        # Side bleed is centred; an odd leftover pixel goes below the pocket
        # (the upright frame centres with the leftover on its left edge,
        # which the CCW rotation maps to the sheet bottom).
        paste_y = pocket_y - (bleed_y_total - bleed_y_total // 2)

        canvas.paste(img, (paste_x, paste_y), img)

    return canvas


def draw_registration_marks(
    image: Image.Image,
    dpi: float,
    line_width_mm: float = 1.0,
    arm_length_mm: float = 5.0,
    origin_color: tuple = (0, 255, 0, 255),
    other_color: tuple = (255, 255, 255, 255),
) -> Image.Image:
    """Draw L-shaped corner registration marks at the four image corners.

    Each mark is two perpendicular line segments (horizontal + vertical arm)
    hugging the corner.  Lines have thickness `line_width_mm` and are
    positioned flush with the image edges so they sit fully inside the image
    bounds.  The bottom-right ("origin") corner uses `origin_color` (green by
    default) — this matches the eufyMake Studio mat origin, which sits at
    the lower-right of the imported canvas.  The other three corners use
    `other_color` (white by default).  Useful for visually verifying jig
    alignment after printing.

    Operates on the post-rotation final image.  Returns a new image; does not
    mutate the input.
    """
    img = image.copy()
    draw = ImageDraw.Draw(img)

    lw_px = max(1, int(round(line_width_mm * dpi / 25.4)))
    arm_px = max(lw_px * 2, int(round(arm_length_mm * dpi / 25.4)))
    w, h = img.size

    # corner: (color, anchor_x, anchor_y, dx_sign, dy_sign)
    # anchor is the outer corner pixel; dx/dy point toward the image interior.
    corners = [
        (other_color,  0,     0,     +1, +1),  # top-left
        (other_color,  w - 1, 0,     -1, +1),  # top-right
        (other_color,  0,     h - 1, +1, -1),  # bottom-left
        (origin_color, w - 1, h - 1, -1, -1),  # bottom-right = eufyMake origin
    ]

    for color, ax, ay, dx, dy in corners:
        # Horizontal arm: arm_px long along x, lw_px tall along y
        hx0, hx1 = (ax, ax + dx * arm_px) if dx > 0 else (ax + dx * arm_px, ax)
        hy0, hy1 = (ay, ay + dy * lw_px) if dy > 0 else (ay + dy * lw_px, ay)
        draw.rectangle([hx0, hy0, hx1, hy1], fill=color)
        # Vertical arm: lw_px wide along x, arm_px tall along y
        vx0, vx1 = (ax, ax + dx * lw_px) if dx > 0 else (ax + dx * lw_px, ax)
        vy0, vy1 = (ay, ay + dy * arm_px) if dy > 0 else (ay + dy * arm_px, ay)
        draw.rectangle([vx0, vy0, vx1, vy1], fill=color)

    return img


def reorder_for_jig_flip(
    flap_sides: list[FlapSide],
    jig: 'JigDimensions',
    flip_mode: str,
) -> list[FlapSide]:
    """Reorder flap sides to their post-jig-flip grid positions.

    This places each flap at its physically-correct post-flip position
    while preserving the original content orientation.

    In the sheet frame:
    - "left-right" flip (rotation about the jig's short Y axis) reverses
      pocket positions along X → columns are reversed.
    - "front-back" flip (pancake flip about the long X axis) reverses
      pocket positions along Y → rows are reversed.
    """
    if not flap_sides:
        return flap_sides

    total = jig.num_x * jig.num_y

    # Decide which grid axis to reverse
    reverse_cols = flip_mode == "left-right"
    reverse_rows = flip_mode == "front-back"

    result: list[Optional[FlapSide]] = [None] * total

    for i, fs in enumerate(flap_sides):
        col = i % jig.num_x
        row = i // jig.num_x

        new_row = (jig.num_y - 1 - row) if reverse_rows else row
        new_col = (jig.num_x - 1 - col) if reverse_cols else col

        new_i = new_row * jig.num_x + new_col
        if new_i < total:
            result[new_i] = fs

    # Fill empty slots with blank FlapSide entries
    sample_size = flap_sides[0].image.size
    filled: list[FlapSide] = []
    for entry in result:
        if entry is not None:
            filled.append(entry)
        else:
            blank = Image.new('RGBA', sample_size, (0, 0, 0, 0))
            filled.append(FlapSide(
                image=blank, label="", slot_index=-1, side="back",
            ))

    return filled


def rotate_insert_rows_180(
    image: Image.Image,
    flap: FlapDimensions,
    jig: JigDimensions,
    printable: PrintableAreaDimensions,
    dpi: float,
) -> Image.Image:
    """Rotate each insert-row region 180° in place (about its own centre).

    A front-back (pancake) flip on a multi-row bed flips each INSERT
    individually, not the whole sheet — so the back image for a front-back
    job is the left-right back with every insert-row band rotated 180°
    about its own centre.  On a single-row jig this is equivalent to
    rotating the whole insert region (and, because the insert is centred
    on the canvas, to rotating the whole sheet, which is what the
    single-row path does).

    All flap content (including bleed and labels) lies within the insert
    bounds — bleed extends at most bleed_mm beyond a pocket and the insert
    margins are larger — so band rotation moves everything that must move.

    Returns a new image; does not mutate the input.
    """
    img = image.copy()
    insert_w_mm, insert_h_mm = jig.insert_size(flap)
    x0 = mm_to_px(printable.insert_offset_x, dpi)
    x1 = x0 + mm_to_px(insert_w_mm, dpi)
    insert_y_px = mm_to_px(printable.insert_offset_y, dpi)
    insert_h_px = mm_to_px(insert_h_mm, dpi)
    row_pitch_px = mm_to_px(jig.row_pitch, dpi)

    for insert_idx in range(jig.rows):
        y0 = insert_y_px + insert_idx * row_pitch_px
        box = (x0, y0, x1, y0 + insert_h_px)
        band = img.crop(box)
        img.paste(band.transpose(Image.ROTATE_180), box)

    return img


def _arc_points(cx: float, cy: float, r: float,
                start_deg: float, end_deg: float,
                steps: int = 12) -> list[tuple[int, int]]:
    """Generate points along a circular arc."""
    pts: list[tuple[int, int]] = []
    for i in range(steps + 1):
        angle = math.radians(start_deg + (end_deg - start_deg) * i / steps)
        pts.append((round(cx + r * math.cos(angle)), round(cy + r * math.sin(angle))))
    return pts


def _flap_mask_polygon(
    x: int, y: int, w: int, h: int,
    corner_r: int, notch_d: int, notch_h: int, pin_w: int,
    bleed: int, spool_at_bottom: bool,
) -> list[tuple[int, int]]:
    """Build a mask polygon: flap profile expanded outward by *bleed*.

    Outer edges expand by bleed, convex corner radii grow by bleed,
    and notch voids shrink by bleed on each edge (so the mask extends
    slightly into the notch area, providing bleed coverage along the
    notch boundaries).
    """
    b = bleed
    r = corner_r + b

    # Expanded outer bounds
    xL = x - b
    xR = x + w + b
    yT = y - b
    yB = y + h + b

    # Check if notch is large enough to remain after shrinking by bleed
    skip_notch = (notch_d <= b) or (notch_h <= 2 * b)

    pts: list[tuple[int, int]] = []

    if spool_at_bottom:
        # --- TOP edge: rounded corners (free/display edge) ---
        pts += _arc_points(xL + r, yT + r, r, 180, 270)
        pts.append((xR - r, yT))
        pts += _arc_points(xR - r, yT + r, r, 270, 360)

        if skip_notch:
            # Straight right edge to bottom
            pts.append((xR, yB))
        else:
            # Right side with notch near BOTTOM (spool edge)
            nv_top = y + h - pin_w - notch_h + b   # notch void top (shrunk down)
            nv_bot = y + h - pin_w - b              # notch void bottom (shrunk up)
            ni_r = x + w - notch_d + b              # right notch inner wall (shrunk right)

            pts.append((xR, nv_top))
            pts.append((ni_r, nv_top))
            pts.append((ni_r, nv_bot))
            pts.append((xR, nv_bot))
            pts.append((xR, yB))

        # --- BOTTOM edge: straight (spool edge) ---
        pts.append((xL, yB))

        if skip_notch:
            pass  # Left edge goes straight up to arc
        else:
            # Left side with notch near BOTTOM
            nv_top = y + h - pin_w - notch_h + b
            nv_bot = y + h - pin_w - b
            ni_l = x + notch_d - b                  # left notch inner wall (shrunk left)

            pts.append((xL, nv_bot))
            pts.append((ni_l, nv_bot))
            pts.append((ni_l, nv_top))
            pts.append((xL, nv_top))
    else:
        # --- TOP edge: straight (spool edge) ---
        pts.append((xL, yT))
        pts.append((xR, yT))

        if skip_notch:
            pts.append((xR, yB - r))
        else:
            # Right side with notch near TOP (spool edge)
            nv_top = y + pin_w + b                  # notch void top (shrunk down)
            nv_bot = y + pin_w + notch_h - b        # notch void bottom (shrunk up)
            ni_r = x + w - notch_d + b

            pts.append((xR, nv_top))
            pts.append((ni_r, nv_top))
            pts.append((ni_r, nv_bot))
            pts.append((xR, nv_bot))
            pts.append((xR, yB - r))

        # --- BOTTOM edge: rounded corners (free/display edge) ---
        pts += _arc_points(xR - r, yB - r, r, 0, 90)
        pts.append((xL + r, yB))
        pts += _arc_points(xL + r, yB - r, r, 90, 180)

        if not skip_notch:
            # Left side with notch near TOP
            nv_top = y + pin_w + b
            nv_bot = y + pin_w + notch_h - b
            ni_l = x + notch_d - b

            pts.append((xL, nv_bot))
            pts.append((ni_l, nv_bot))
            pts.append((ni_l, nv_top))
            pts.append((xL, nv_top))

    return pts


def apply_ink_save_mask(
    image: Image.Image,
    flap: FlapDimensions,
    jig: JigDimensions,
    printable: PrintableAreaDimensions,
    dpi: float,
    bleed_mm: float = 1.0,
    spool_at_bottom: bool = True,
) -> Image.Image:
    """Zero out alpha outside the flap pocket areas (expanded by bleed).

    This saves ink by only printing within the flap outlines + margin.
    The mask is drawn on a printable-area-sized canvas at the insert offset.

    The flap outline polygon is built in the upright frame (where the
    verified notch geometry lives), drawn once into a tile, rotated 90° CCW
    into the sheet frame, and pasted at every pocket position.

    *spool_at_bottom* controls which end of each pocket gets the notch (in
    the upright frame): True for front batches (top halves), False for back
    batches (bottom halves).
    """
    mask_w = mm_to_px(printable.width, dpi)
    mask_h = mm_to_px(printable.height, dpi)

    mask = Image.new('L', (mask_w, mask_h), 0)

    insert_x_px = mm_to_px(printable.insert_offset_x, dpi)
    insert_y_px = mm_to_px(printable.insert_offset_y, dpi)

    flap_w_px = mm_to_px(flap.width, dpi)
    flap_h_px = mm_to_px(flap.height, dpi)
    margin_x_px = mm_to_px(jig.margin_x, dpi)
    margin_y_px = mm_to_px(jig.margin_y, dpi)
    # Pockets are rotated 90°: X extent = flap.height, Y extent = flap.width
    space_x_px = mm_to_px(flap.height + jig.gap_x, dpi)
    space_y_px = mm_to_px(flap.width + jig.gap_y, dpi)
    corner_r_px = mm_to_px(flap.corner_radius, dpi)
    notch_d_px = mm_to_px(flap.notch_depth, dpi)
    notch_h_px = mm_to_px(flap.notch_height, dpi)
    pin_w_px = mm_to_px(flap.pin_width, dpi)
    bleed_px = mm_to_px(bleed_mm, dpi)

    # All pockets are identical, so draw the upright flap outline once.
    # The polygon spans [-bleed, size+bleed] inclusive on each axis, i.e.
    # size + 2*bleed + 1 drawn pixels.
    tile = Image.new('L', (flap_w_px + 2 * bleed_px + 1, flap_h_px + 2 * bleed_px + 1), 0)
    tile_draw = ImageDraw.Draw(tile)
    pts = _flap_mask_polygon(
        bleed_px, bleed_px, flap_w_px, flap_h_px,
        corner_r_px, notch_d_px, notch_h_px, pin_w_px,
        bleed_px, spool_at_bottom,
    )
    tile_draw.polygon(pts, fill=255)
    # Rotate into the sheet frame (upright top edge → left, spool → right)
    tile = tile.transpose(Image.ROTATE_90)

    row_pitch_px = mm_to_px(jig.row_pitch, dpi)
    for insert_idx in range(jig.rows):
        for row in range(jig.num_y):
            for col in range(jig.num_x):
                pocket_x = insert_x_px + margin_x_px + col * space_x_px
                pocket_y = insert_y_px + insert_idx * row_pitch_px + margin_y_px + row * space_y_px
                # The tile's one extra inclusive-boundary pixel sits at its top
                # after the CCW rotation, hence the additional -1 on Y.
                mask.paste(tile, (pocket_x - bleed_px, pocket_y - bleed_px - 1), tile)

    # Apply mask: zero alpha outside flap shapes
    result = image.copy()
    r, g, b, a = result.split()
    # Combine existing alpha with the flap mask
    from PIL import ImageChops
    a = ImageChops.multiply(a, mask)
    result = Image.merge('RGBA', (r, g, b, a))
    return result


def generate_sticky_mask(
    flap: FlapDimensions,
    jig: JigDimensions,
    printable: PrintableAreaDimensions,
    dpi: float,
    inset_mm: float,
    color: tuple[int, int, int],
) -> Image.Image:
    """Generate the mat-preparation sticky-mask image.

    The mask is printed ONCE per mat (with the empty insert seated on it,
    so the ink lands on the mat through the pocket openings): cured ink
    permanently de-tackifies the coated areas, so flaps release easily
    during the flip instead of fighting the sticky mat.

    The coat at each pocket is the flap footprint inset inward by
    *inset_mm* on all edges (including an equal clearance around the notch
    cutouts, where the insert material protrudes into the pocket).  This
    leaves a sticky perimeter ring of *inset_mm* that still holds flaps
    against shifting during printing — pressing the flap edges at load
    time engages the ring past the coat's ink-standoff height.  With
    inset_mm = 0 the coat is the full flap footprint (all tack removed;
    pocket walls and the masking-tape flip strip do all the holding).

    The coat shape is invariant to flip mode: it is the physical pocket
    area, and both print passes use the same pockets.

    Returns an RGBA sheet-frame canvas: *color* where the coat prints,
    transparent elsewhere.
    """
    flap_w_px = mm_to_px(flap.width, dpi)
    flap_h_px = mm_to_px(flap.height, dpi)
    inset_px = mm_to_px(inset_mm, dpi)

    if flap_w_px - 2 * inset_px < 2 or flap_h_px - 2 * inset_px < 2:
        raise ValueError(
            f"sticky_mask_inset_mm={inset_mm} leaves no coat area on a "
            f"{flap.width}×{flap.height} mm flap — use a smaller inset")

    corner_r_px = mm_to_px(flap.corner_radius, dpi)
    notch_d_px = mm_to_px(flap.notch_depth, dpi)
    notch_h_px = mm_to_px(flap.notch_height, dpi)
    pin_w_px = mm_to_px(flap.pin_width, dpi)

    # Draw one upright-frame tile (display edge at top, spool/notch edge at
    # bottom): a rounded rectangle inset from the flap footprint, minus the
    # notch cutout regions expanded by the same inset for equal clearance.
    tile = Image.new('L', (flap_w_px, flap_h_px), 0)
    tile_draw = ImageDraw.Draw(tile)
    tile_draw.rounded_rectangle(
        (inset_px, inset_px, flap_w_px - 1 - inset_px, flap_h_px - 1 - inset_px),
        radius=max(0, corner_r_px - inset_px),
        fill=255,
        # Only the display-edge (top) corners of a flap are rounded
        corners=(True, True, False, False),
    )
    notch_top = flap_h_px - pin_w_px - notch_h_px - inset_px
    notch_bot = flap_h_px - pin_w_px + inset_px
    tile_draw.rectangle((0, notch_top, notch_d_px + inset_px, notch_bot), fill=0)
    tile_draw.rectangle((flap_w_px - 1 - notch_d_px - inset_px, notch_top,
                         flap_w_px - 1, notch_bot), fill=0)

    # Rotate into the sheet frame (upright top edge → left, spool → right)
    tile = tile.transpose(Image.ROTATE_90)

    mask_w = mm_to_px(printable.width, dpi)
    mask_h = mm_to_px(printable.height, dpi)
    mask = Image.new('L', (mask_w, mask_h), 0)

    insert_x_px = mm_to_px(printable.insert_offset_x, dpi)
    insert_y_px = mm_to_px(printable.insert_offset_y, dpi)
    margin_x_px = mm_to_px(jig.margin_x, dpi)
    margin_y_px = mm_to_px(jig.margin_y, dpi)
    # Pockets are rotated 90°: X extent = flap.height, Y extent = flap.width
    space_x_px = mm_to_px(flap.height + jig.gap_x, dpi)
    space_y_px = mm_to_px(flap.width + jig.gap_y, dpi)
    row_pitch_px = mm_to_px(jig.row_pitch, dpi)

    for insert_idx in range(jig.rows):
        for row in range(jig.num_y):
            for col in range(jig.num_x):
                pocket_x = insert_x_px + margin_x_px + col * space_x_px
                pocket_y = insert_y_px + insert_idx * row_pitch_px + margin_y_px + row * space_y_px
                mask.paste(tile, (pocket_x, pocket_y), tile)

    # Compose: solid color where the mask is set, transparent elsewhere
    canvas = Image.new('RGBA', (mask_w, mask_h), (0, 0, 0, 0))
    solid = Image.new('RGBA', (mask_w, mask_h), (*color, 255))
    canvas.paste(solid, (0, 0), mask)
    return canvas
