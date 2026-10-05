"""Generate test source images with flap-shaped outlines for visual verification.

Each image represents one full display character: top_half + gap + bottom_half.

Physical flap orientation in the display:
  - TOP half: spool/notch at BOTTOM (center of display), rounded corners at TOP
  - BOTTOM half: spool/notch at TOP (center of display), rounded corners at BOTTOM

Usage:
    python scripts/generate_test_images.py      # writes examples/test_images/
"""

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Physical dimensions (mm) — match defaults in dimensions.py
FLAP_W = 54.0
FLAP_H = 43.0
GAP = 2.0
CORNER_R = 3.1
NOTCH_DEPTH = 3.2
NOTCH_HEIGHT = 15.0
PIN_WIDTH = 1.4
DPI = 360

DISPLAY_H = FLAP_H * 2 + GAP  # 88mm


def mm_to_px(mm: float) -> int:
    return round(mm * DPI / 25.4)


def _arc_points(cx: float, cy: float, r: float,
                start_deg: float, end_deg: float, steps: int = 16) -> list[tuple[int, int]]:
    """Generate points along a circular arc."""
    pts = []
    for i in range(steps + 1):
        angle = math.radians(start_deg + (end_deg - start_deg) * i / steps)
        pts.append((round(cx + r * math.cos(angle)), round(cy + r * math.sin(angle))))
    return pts


def _flap_polygon(x: int, y: int, w: int, h: int,
                  corner_r: int, notch_d: int, notch_h: int, pin_w: int,
                  spool_at_bottom: bool) -> list[tuple[int, int]]:
    """Build a closed polygon for a flap shape.

    spool_at_bottom=True:  rounded corners at TOP, notch/spool at BOTTOM  (top half flap)
    spool_at_bottom=False: notch/spool at TOP, rounded corners at BOTTOM  (bottom half flap)
    """
    r = corner_r
    pts: list[tuple[int, int]] = []

    if spool_at_bottom:
        # --- TOP edge: rounded corners (free/display edge) ---
        # Top-left arc (center at x+r, y+r)
        pts += _arc_points(x + r, y + r, r, 180, 270)
        # Top straight edge
        pts.append((x + w - r, y))
        # Top-right arc (center at x+w-r, y+r)
        pts += _arc_points(x + w - r, y + r, r, 270, 360)

        # --- Right side with notch near BOTTOM (spool edge) ---
        notch_bot = y + h
        notch_top = notch_bot - pin_w
        notch_inner_top = notch_top - notch_h
        pts.append((x + w, notch_inner_top))
        pts.append((x + w - notch_d, notch_inner_top))
        pts.append((x + w - notch_d, notch_top))
        pts.append((x + w, notch_top))

        # --- BOTTOM edge: straight (spool edge) ---
        pts.append((x + w, y + h))
        pts.append((x, y + h))

        # --- Left side with notch near BOTTOM ---
        pts.append((x, notch_top))
        pts.append((x + notch_d, notch_top))
        pts.append((x + notch_d, notch_inner_top))
        pts.append((x, notch_inner_top))
    else:
        # --- TOP edge: straight (spool edge) ---
        pts.append((x, y))
        pts.append((x + w, y))

        # --- Right side with notch near TOP (spool edge) ---
        notch_top = y + pin_w
        notch_bot = notch_top + notch_h
        pts.append((x + w, notch_top))
        pts.append((x + w - notch_d, notch_top))
        pts.append((x + w - notch_d, notch_bot))
        pts.append((x + w, notch_bot))

        # --- Right side continues to bottom-right arc ---
        pts.append((x + w, y + h - r))
        # Bottom-right arc (center at x+w-r, y+h-r)
        pts += _arc_points(x + w - r, y + h - r, r, 0, 90)

        # --- BOTTOM edge: rounded corners (free/display edge) ---
        pts.append((x + r, y + h))
        # Bottom-left arc (center at x+r, y+h-r)
        pts += _arc_points(x + r, y + h - r, r, 90, 180)

        # --- Left side with notch near TOP ---
        pts.append((x, notch_bot))
        pts.append((x + notch_d, notch_bot))
        pts.append((x + notch_d, notch_top))
        pts.append((x, notch_top))

    return pts


def draw_flap(draw: ImageDraw.ImageDraw,
              x: int, y: int, w: int, h: int,
              corner_r: int, notch_d: int, notch_h: int, pin_w: int,
              fill_color: tuple, outline_color: tuple,
              spool_at_bottom: bool, outline_width: int = 3):
    """Draw a filled flap with outline."""
    pts = _flap_polygon(x, y, w, h, corner_r, notch_d, notch_h, pin_w, spool_at_bottom)
    draw.polygon(pts, fill=fill_color, outline=outline_color, width=outline_width)


# 14 distinct hues
COLORS = [
    (220, 50, 50),    # red
    (50, 180, 50),    # green
    (50, 50, 220),    # blue
    (200, 200, 30),   # yellow
    (200, 50, 200),   # magenta
    (50, 200, 200),   # cyan
    (255, 140, 0),    # orange
    (100, 60, 180),   # purple
    (180, 120, 60),   # brown
    (50, 150, 100),   # teal
    (220, 100, 150),  # pink
    (100, 160, 220),  # sky blue
    (160, 180, 50),   # olive
    (80, 200, 160),   # seafoam
]


def generate_image(index: int, out_dir: Path):
    """Generate one test source image."""
    w_px = mm_to_px(FLAP_W)
    h_px = mm_to_px(DISPLAY_H)
    flap_h_px = mm_to_px(FLAP_H)
    gap_px = mm_to_px(GAP)
    corner_r_px = mm_to_px(CORNER_R)
    notch_d_px = mm_to_px(NOTCH_DEPTH)
    notch_h_px = mm_to_px(NOTCH_HEIGHT)
    pin_w_px = mm_to_px(PIN_WIDTH)

    color = COLORS[index % len(COLORS)]
    light = tuple(min(255, c + 80) for c in color)

    img = Image.new('RGBA', (w_px, h_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Top half: rounded corners at TOP, spool/notch at BOTTOM
    draw_flap(draw, 0, 0, w_px, flap_h_px,
              corner_r_px, notch_d_px, notch_h_px, pin_w_px,
              fill_color=color + (200,), outline_color=color + (255,),
              spool_at_bottom=True, outline_width=3)

    # Bottom half: spool/notch at TOP, rounded corners at BOTTOM
    bot_y = flap_h_px + gap_px
    draw_flap(draw, 0, bot_y, w_px, flap_h_px,
              corner_r_px, notch_d_px, notch_h_px, pin_w_px,
              fill_color=light + (200,), outline_color=light + (255,),
              spool_at_bottom=False, outline_width=3)

    # Labels
    try:
        font = ImageFont.truetype("arial.ttf", max(12, w_px // 10))
    except (OSError, IOError):
        font = ImageFont.load_default()

    num = f"{index + 1:02d}"
    text_x = notch_d_px + 8

    # Top half: "TOP nn" near center, arrow near rounded (top) edge
    draw.text((text_x, 8), "▲", fill=color + (180,), font=font)
    draw.text((text_x, flap_h_px // 3),
              f"TOP {num}", fill=color + (220,), font=font)

    # Bottom half: "BOT nn" near center, arrow near rounded (bottom) edge
    draw.text((text_x, bot_y + flap_h_px // 3),
              f"BOT {num}", fill=light + (220,), font=font)
    draw.text((text_x, bot_y + flap_h_px - font.size - 8),
              "▼", fill=light + (180,), font=font)

    # Gap zone divider lines
    draw.line([(0, flap_h_px), (w_px, flap_h_px)], fill=(150, 150, 150, 100), width=1)
    draw.line([(0, flap_h_px + gap_px), (w_px, flap_h_px + gap_px)],
              fill=(150, 150, 150, 100), width=1)

    out_path = out_dir / f"sample_{num}.png"
    img.save(str(out_path), dpi=(DPI, DPI))
    print(f"  {out_path}")


def main():
    out_dir = Path(__file__).resolve().parent.parent / "examples" / "test_images"
    out_dir.mkdir(exist_ok=True)

    print(f"Generating 14 test images ({mm_to_px(FLAP_W)}×{mm_to_px(DISPLAY_H)} px) to {out_dir}/")
    for i in range(14):
        generate_image(i, out_dir)
    print("Done.")


if __name__ == "__main__":
    main()
