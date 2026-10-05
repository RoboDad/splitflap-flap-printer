"""Dimension dataclasses for flap, jig, and display geometry.

Values are resolved using a two-tier chain:
  1. OpenSCAD subprocess (parses tagged echo output from an optional --scad-dims file)
  2. Job-config dimension overrides (JSON fallbacks)
  3. Hardcoded defaults (last resort)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .scad_parser import run_openscad

if TYPE_CHECKING:
    from .config import JigConfig

logger = logging.getLogger(__name__)


def mm_to_px(mm: float, dpi: float) -> int:
    """Convert millimetres to pixels at the given DPI."""
    return round(mm / 25.4 * dpi)


def px_to_mm(px: int, dpi: float) -> float:
    """Convert pixels to millimetres at the given DPI."""
    return px * 25.4 / dpi


@dataclass(frozen=True)
class FlapDimensions:
    width: float = 54.0           # mm
    height: float = 43.0          # mm
    gap: float = 2.0              # mm — vertical gap between stacked flaps
    corner_radius: float = 3.1    # mm
    notch_height: float = 15.0    # mm
    notch_depth: float = 3.2      # mm
    pin_width: float = 1.4        # mm

    @property
    def display_height(self) -> float:
        """Full visible height of one character: top-half + gap + bottom-half."""
        return self.height * 2 + self.gap


@dataclass(frozen=True)
class JigDimensions:
    """Jig pocket grid in printer orientation (long axis along X).

    Flap pockets sit rotated 90° in the jig (spool edge facing the mat zero
    point at the right), so a pocket's X extent is flap.height and its Y
    extent is flap.width.
    """
    num_x: int = 6
    num_y: int = 1
    gap_x: float = 6.0            # mm between flap pockets horizontally
    gap_y: float = 6.0            # mm between flap pockets vertically
    margin_x: float = 6.0         # mm margin around grid
    margin_y: float = 6.0         # mm margin around grid
    # Insert rows: the standard flatbed stacks several identical inserts
    # along Y at row_pitch intervals; each insert is flipped individually
    # for the back pass.  The minibed is a single row.
    rows: int = 1
    row_pitch: float = 0.0        # mm, first-insert-origin to next-insert-origin (unused when rows == 1)

    @property
    def flaps_per_batch(self) -> int:
        """Flaps in ONE insert (one physical flip unit)."""
        return self.num_x * self.num_y

    @property
    def flaps_per_sheet(self) -> int:
        """Flaps in one print run (all insert rows)."""
        return self.flaps_per_batch * self.rows

    def insert_size(self, flap: FlapDimensions) -> tuple[float, float]:
        """Return (width_mm, height_mm) of the jig insert in printer orientation.

        Pockets are rotated 90°: X extent = flap.height, Y extent = flap.width.
        """
        space_x = flap.height + self.gap_x
        space_y = flap.width + self.gap_y
        w = space_x * self.num_x - self.gap_x + 2 * self.margin_x
        h = space_y * self.num_y - self.gap_y + 2 * self.margin_y
        return (w, h)


@dataclass(frozen=True)
class PrintableAreaDimensions:
    """Printable area of the printer bed, and the insert's position within it.

    Coordinates are in printer orientation: the long axis runs along X and
    the mat zero point is at the image's lower-right corner.  The four
    fields below (width/height/insert_offset_x/insert_offset_y) define the
    canvas the renderer draws onto and where the jig insert sits on it.
    By default these come from the jig printable-area + insert geometry, but
    `output.canvas_size_mm` in the job config can replace them with the
    eufyMake Studio mat-canvas dimensions (using absolute mat coordinates for
    the insert).  The `*_origin_*` fields below are the raw absolute mat
    coordinates; they enable that override in cli.py.
    """
    width: float = 333.0          # mm — canvas width  (defaults to flatbed_printable_size_x)
    height: float = 88.0          # mm — canvas height (defaults to flatbed_printable_size_y)
    insert_offset_x: float = 16.5 # mm — insert X within canvas
    insert_offset_y: float = 11.0 # mm — insert Y within canvas
    # Raw absolute mat coordinates; used when canvas is overridden
    printable_origin_x: float = 4.0
    printable_origin_y: float = 5.0
    insert_origin_x: float = 20.5
    insert_origin_y: float = 16.0


@dataclass(frozen=True)
class DisplayDimensions:
    module_pitch: float = 64.0    # mm centre-to-centre
    inter_module_gap: float = 10.0  # mm gap between adjacent flap edges
    module_width: float = 54.0    # mm (= flap_width)


@dataclass
class AllDimensions:
    flap: FlapDimensions = field(default_factory=FlapDimensions)
    jig: JigDimensions = field(default_factory=JigDimensions)
    printable: PrintableAreaDimensions = field(default_factory=PrintableAreaDimensions)
    display: DisplayDimensions = field(default_factory=DisplayDimensions)

    @staticmethod
    def from_scad(
        mods_path: Optional[str | Path] = None,
        openscad_path: Optional[str] = None,
        overrides: Optional[dict[str, float]] = None,
        jig_config: Optional['JigConfig'] = None,
    ) -> AllDimensions:
        """Load dimensions with two-tier fallback: OpenSCAD → overrides → hardcoded.

        Args:
            mods_path: Optional OpenSCAD file that echoes FLAP_PRINTER:<key>=<value>
                       lines for the flap dimensions (None = skip OpenSCAD).
            openscad_path: Path to the OpenSCAD executable, or None to auto-detect.
            overrides: Optional dict of dimension fallbacks from the job config
                       (DimensionOverrides.as_dict()).  Values here fill in any
                       gaps left when OpenSCAD is unavailable.
            jig_config: When provided, jig grid + printable area dims are taken
                        directly from the job JSON (not from SCAD echoes).  Flap
                        physical dims are still resolved via SCAD.
        """
        # Tier 1: OpenSCAD echo extraction
        scad_vals: dict[str, float] = {}
        if mods_path and Path(mods_path).exists():
            result, from_cache = run_openscad(mods_path, openscad_path=openscad_path)
            if result:
                scad_vals = result
                if from_cache:
                    logger.info("Got %d dimensions from cache", len(scad_vals))
                else:
                    logger.info("Got %d dimensions from OpenSCAD", len(scad_vals))
            else:
                logger.info("OpenSCAD unavailable or failed, using fallbacks")

        # Tier 2: merge overrides underneath (scad wins)
        ov = overrides or {}

        def _get(key: str, default: float) -> float:
            """Resolve: scad_vals → overrides → hardcoded default."""
            if key in scad_vals:
                return scad_vals[key]
            if key in ov:
                return ov[key]
            return default

        flap = FlapDimensions(
            width=_get('flap_width', 54.0),
            height=_get('flap_height', 43.0),
            gap=_get('flap_gap', 2.0),
            corner_radius=_get('flap_corner_radius', 3.1),
            notch_height=_get('flap_notch_height', 15.0),
            notch_depth=_get('flap_notch_depth', 3.2),
            pin_width=_get('flap_pin_width', 1.4),
        )

        if jig_config is not None:
            # Jig config present — take all jig + printable dims directly from job JSON.
            jig = JigDimensions(
                num_x=jig_config.num_flaps_x,
                num_y=jig_config.num_flaps_y,
                gap_x=jig_config.gap_x_mm,
                gap_y=jig_config.gap_y_mm,
                margin_x=jig_config.margin_x_mm,
                margin_y=jig_config.margin_y_mm,
                rows=jig_config.insert_rows,
                row_pitch=jig_config.insert_pitch_y_mm or 0.0,
            )
            printable = PrintableAreaDimensions(
                width=jig_config.printable_size_x_mm,
                height=jig_config.printable_size_y_mm,
                insert_offset_x=jig_config.insert_origin_x_mm - jig_config.printable_origin_x_mm,
                insert_offset_y=jig_config.insert_origin_y_mm - jig_config.printable_origin_y_mm,
                printable_origin_x=jig_config.printable_origin_x_mm,
                printable_origin_y=jig_config.printable_origin_y_mm,
                insert_origin_x=jig_config.insert_origin_x_mm,
                insert_origin_y=jig_config.insert_origin_y_mm,
            )
        else:
            # Legacy: resolve jig + printable dims from SCAD echoes / overrides / defaults.
            jig = JigDimensions(
                num_x=int(_get('flatbed_flap_jig_num_flaps_x', 6)),
                num_y=int(_get('flatbed_flap_jig_num_flaps_y', 1)),
                gap_x=_get('flatbed_flap_jig_gap_x', 6.0),
                gap_y=_get('flatbed_flap_jig_gap_y', 6.0),
                margin_x=_get('flatbed_flap_jig_margin_x', 6.0),
                margin_y=_get('flatbed_flap_jig_margin_y', 6.0),
            )
            insert_w, insert_h = jig.insert_size(flap)
            printable_w = _get('flatbed_printable_size_x', 333.0)
            printable_h = _get('flatbed_printable_size_y', 88.0)
            printable_origin_x = _get('flatbed_printable_origin_x', 4.0)
            printable_origin_y = _get('flatbed_printable_origin_y', 5.0)
            insert_offset_x = (printable_w - insert_w) / 2.0
            insert_offset_y = (printable_h - insert_h) / 2.0
            insert_origin_x = insert_offset_x + printable_origin_x
            insert_origin_y = insert_offset_y + printable_origin_y
            printable = PrintableAreaDimensions(
                width=printable_w,
                height=printable_h,
                insert_offset_x=insert_offset_x,
                insert_offset_y=insert_offset_y,
                printable_origin_x=printable_origin_x,
                printable_origin_y=printable_origin_y,
                insert_origin_x=insert_origin_x,
                insert_origin_y=insert_origin_y,
            )

        display = DisplayDimensions(
            module_width=flap.width,
        )

        return AllDimensions(flap=flap, jig=jig, printable=printable, display=display)
