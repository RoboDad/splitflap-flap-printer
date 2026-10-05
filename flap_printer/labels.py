"""Render EP labels in the margin areas between flap pockets."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from .dimensions import FlapDimensions, JigDimensions, PrintableAreaDimensions, mm_to_px
from .layout import FlapSide


def render_labels(
    image: Image.Image,
    flap_sides: list[FlapSide],
    flap: FlapDimensions,
    jig: JigDimensions,
    printable: PrintableAreaDimensions,
    dpi: float,
    font_size_pt: int = 6,
) -> Image.Image:
    """Draw EP labels in the margin/gap areas of a batch image.

    The batch image is in the sheet frame (flaps rotated 90°), so the sheet
    is temporarily rotated into the upright frame, labels are drawn with
    upright text next to each flap, and the sheet is rotated back.  On the
    printed sheet the labels therefore read in the same direction as the
    flap content.
    """
    # Rotate the sheet into the upright frame for text drawing
    work = image.transpose(Image.ROTATE_270)

    draw = ImageDraw.Draw(work)

    # Font: try to use a reasonable default
    font_size_px = mm_to_px(font_size_pt * 0.3528, dpi)  # pt → mm → px
    font_size_px = max(8, font_size_px)
    try:
        font = ImageFont.truetype("arial.ttf", font_size_px)
    except (OSError, IOError):
        font = ImageFont.load_default()

    # Pocket grid in sheet-frame pixels (pockets are rotated 90°:
    # X extent = flap.height, Y extent = flap.width)
    insert_x_px = mm_to_px(printable.insert_offset_x, dpi)
    insert_y_px = mm_to_px(printable.insert_offset_y, dpi)
    margin_x_px = mm_to_px(jig.margin_x, dpi)
    margin_y_px = mm_to_px(jig.margin_y, dpi)
    space_x_px = mm_to_px(flap.height + jig.gap_x, dpi)
    space_y_px = mm_to_px(flap.width + jig.gap_y, dpi)
    row_pitch_px = mm_to_px(jig.row_pitch, dpi)
    flap_w_px = mm_to_px(flap.width, dpi)
    flap_h_px = mm_to_px(flap.height, dpi)
    sheet_h_px = image.height  # = work.width

    label_color = (200, 200, 200, 255)  # light grey, visible on transparent bg

    for i, fs in enumerate(flap_sides[:jig.flaps_per_sheet]):
        if not fs.label:
            continue

        col = i % jig.num_x
        grid_row = i // jig.num_x
        insert_idx = grid_row // jig.num_y
        pocket_row = grid_row % jig.num_y

        # Pocket top-left in sheet-frame pixels
        pocket_x_s = insert_x_px + margin_x_px + col * space_x_px
        pocket_y_s = (insert_y_px + insert_idx * row_pitch_px
                      + margin_y_px + pocket_row * space_y_px)

        # Map into the upright work frame (work = ROTATE_270(sheet)):
        # the pocket appears upright with its top-left at (work_x, work_y).
        work_x = sheet_h_px - pocket_y_s - flap_w_px
        work_y = pocket_x_s

        # Label in the gap below the upright flap
        label_x = work_x + 2
        label_y = work_y + flap_h_px + 1

        draw.text((label_x, label_y), fs.label, fill=label_color, font=font)

        # Also draw side indicator
        side_text = "F" if fs.side == "front" else "B"
        bbox = draw.textbbox((0, 0), fs.label, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text((label_x + text_w + 4, label_y), side_text,
                  fill=(150, 150, 255, 255) if fs.side == "front" else (255, 150, 150, 255),
                  font=font)

    # Rotate back into the sheet frame
    return work.transpose(Image.ROTATE_90)
