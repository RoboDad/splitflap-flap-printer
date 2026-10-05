"""Orchestrator: load config, resolve images, slice, layout, and save batch outputs."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

from PIL import Image

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass  # HEIC/HEIF support unavailable; install pillow-heif to enable it

from .config import JobConfig, CustomFlap
from .dimensions import AllDimensions, FlapDimensions, JigDimensions, DisplayDimensions, mm_to_px
from .slicer import slice_display_image, extract_module_column, apply_transforms, fit_to_target, fit_with_notch_mode
from .layout import (map_images_to_flap_sides, generate_batch_image, apply_ink_save_mask,
                     reorder_for_jig_flip, rotate_insert_rows_180, draw_registration_marks,
                     generate_sticky_mask)
from .labels import render_labels
from . import svg_loader

logger = logging.getLogger(__name__)


def _flaps_by_slot(flaps: list[CustomFlap]) -> list[CustomFlap]:
    """Return the custom flap entries ordered by slot (slots are unique)."""
    return sorted(flaps, key=lambda cf: cf.slot)


def _apply_calibration_offset(img: Image.Image, dx_px: int, dy_px: int) -> Image.Image:
    """Translate ``img`` by (dx_px, dy_px) on a fresh transparent canvas of
    the same size.  Content shifted past the image edge is clipped.  Used
    to apply a global mat-calibration offset to the final composited image.
    """
    if dx_px == 0 and dy_px == 0:
        return img
    shifted = Image.new('RGBA', img.size, (0, 0, 0, 0))
    shifted.paste(img, (dx_px, dy_px), img)
    return shifted


def _load_source_image(source: Optional[str], source_path, target_height_px: int) -> Image.Image:
    """Load and validate a source image (raster or SVG).

    ``target_height_px`` is the eventual display-image height in pixels;
    it is used only by the SVG path to pick a high-quality render size.
    Raster images are loaded at their native resolution and resampled by
    the downstream pipeline as usual.
    """
    if source_path is None or not source_path.exists():
        raise FileNotFoundError(f"Source image not found: {source} "
                                f"(resolved to {source_path})")
    if svg_loader.is_svg(source_path):
        return svg_loader.load_svg(source_path, target_height_px)
    img = Image.open(source_path).convert('RGBA')
    return img


def _render_custom_flap_images(
    cf: CustomFlap,
    config: JobConfig,
    module_index: int,
    dims: AllDimensions,
    dpi: float,
    bleed_px: int = 0,
) -> Optional[tuple[Image.Image, Image.Image]]:
    """Render one CustomFlap to (top_half, bottom_half) at the given DPI.

    When *bleed_px* > 0, images are expanded into the bleed zone on flush
    edges so the batch layout can paste them offset by the bleed amount.
    The physical pocket area is centred within the returned images.

    Returns None if the flap does not apply to *module_index* (multi-module
    flaps on the common pass, or with no segment covering the module).
    Returns (blank, blank) for blank or disabled flaps and blank segments.
    """
    flap_w = mm_to_px(dims.flap.width, dpi)
    flap_h = mm_to_px(dims.flap.height, dpi)
    blank = Image.new('RGBA', (flap_w, flap_h), (0, 0, 0, 0))

    if cf.type == "blank" or not cf.enabled:
        return blank, blank

    # Per-flap bleed override: bleed=False suppresses OUTER-edge fit expansion
    # for this image.  Gap-edge bleed (below) is independent of cf.bleed: it
    # needs no expansion or scaling — the rows already exist in the display
    # image's gap strip — so it applies to all flap types unconditionally.
    effective_bleed_px = bleed_px if cf.bleed else 0
    gap_bleed_px = min(bleed_px, mm_to_px(dims.flap.gap, dpi))

    gt = config.global_transforms

    def _pick(*vals):
        """First non-None value (segment override → entry → global chain)."""
        for v in vals:
            if v is not None:
                return v
        return None

    def _apply_offset(img: Image.Image, offset_mm) -> Image.Image:
        """Shift image content by offset_mm within a canvas of the same size."""
        if offset_mm is None or (offset_mm[0] == 0.0 and offset_mm[1] == 0.0):
            return img
        dx_px = round(offset_mm[0] * dpi / 25.4)
        dy_px = round(offset_mm[1] * dpi / 25.4)
        canvas = Image.new('RGBA', img.size, (0, 0, 0, 0))
        canvas.paste(img, (dx_px, dy_px), img)
        return canvas

    global_scale = gt.scale if gt.scale != (1.0, 1.0) else None

    if cf.type == "single":
        target_w = mm_to_px(dims.flap.width, dpi)
        target_h = mm_to_px(dims.flap.display_height, dpi)
        img = _load_source_image(cf.source, cf.source_path, target_h)
        img = apply_transforms(img, scale=_pick(cf.scale, global_scale),
                               crop=_pick(cf.crop, gt.crop_percent))
        fit = _pick(cf.fit_mode, gt.fit_mode)
        notch = _pick(cf.notch_mode, gt.notch_mode)
        notch_inset_px = mm_to_px(dims.flap.notch_depth, dpi)
        img = fit_with_notch_mode(img, target_w, target_h, fit, notch[0], notch[1], notch_inset_px, effective_bleed_px)
        img = _apply_offset(img, cf.offset_mm)
        bleed_y = max(0, (img.height - mm_to_px(dims.flap.display_height, dpi)) // 2)
        return slice_display_image(img, dims.flap, dpi, bleed_y=bleed_y, gap_bleed_px=gap_bleed_px)

    if cf.type == "multi-module":
        if module_index < 0:
            # Common/sentinel pass — multi-module content is module-specific; skip.
            return None
        seg = cf.segment_for_module(module_index)
        if seg is None:
            return None
        if seg.blank:
            return blank, blank
        target_h = mm_to_px(dims.flap.display_height, dpi)
        img = _load_source_image(seg.source, seg.source_path, target_h)
        img = apply_transforms(img, scale=_pick(seg.scale, cf.scale, global_scale),
                               crop=_pick(seg.crop, cf.crop, gt.crop_percent))
        start_mod, end_mod = seg.module_range
        num_span = end_mod - start_mod + 1
        total_width_mm = num_span * dims.display.module_pitch - dims.display.inter_module_gap
        target_w = mm_to_px(total_width_mm, dpi)
        fit = _pick(seg.fit_mode, cf.fit_mode, gt.fit_mode)
        notch = _pick(seg.notch_mode, cf.notch_mode, gt.notch_mode)
        notch_inset_px = mm_to_px(dims.flap.notch_depth, dpi)
        img = fit_to_target(img, target_w, target_h, fit)
        column = extract_module_column(img, module_index, seg.module_range, dims.display, dpi)
        flap_w_px = mm_to_px(dims.flap.width, dpi)
        flap_display_h = mm_to_px(dims.flap.display_height, dpi)
        column = fit_with_notch_mode(column, flap_w_px, flap_display_h, fit, notch[0], notch[1], notch_inset_px, effective_bleed_px)
        column = _apply_offset(column, _pick(seg.offset_mm, cf.offset_mm))
        bleed_y = max(0, (column.height - mm_to_px(dims.flap.display_height, dpi)) // 2)
        return slice_display_image(column, dims.flap, dpi, bleed_y=bleed_y, gap_bleed_px=gap_bleed_px)

    return None  # unknown type


def _resolve_flaps_for_module(
    config: JobConfig,
    module_index: int,
    dims: AllDimensions,
    dpi: float,
    bleed_px: int = 0,
) -> list[tuple[Image.Image, Image.Image, str]]:
    """Resolve and slice all custom flap images for a given module.

    Returns a list of (top_half, bottom_half, label) tuples, one per slot
    that has content for this module, in slot order.

    Multi-module flaps render the segment covering *module_index*.  When
    module_index >= 0 (a real module pass, not the common/sentinel pass)
    and no segment covers the module, a blank is inserted (upfront warning
    already logged by render_job).  On the common pass, multi-module slots
    are omitted entirely.
    """
    flap_w = mm_to_px(dims.flap.width, dpi)
    flap_h = mm_to_px(dims.flap.height, dpi)
    blank = Image.new('RGBA', (flap_w, flap_h), (0, 0, 0, 0))

    results = []

    for cf in _flaps_by_slot(config.custom_flaps):
        pair = _render_custom_flap_images(cf, config, module_index, dims, dpi, bleed_px)
        if pair is not None:
            winning_label = cf.label
        elif cf.type == 'multi-module' and module_index >= 0:
            # No segment covers this module — insert a blank so the slot
            # position is preserved.
            pair = (blank.copy(), blank.copy())
            winning_label = f"{cf.label}-blank"
        else:
            # Multi-module slot on the common pass; omit.
            continue

        results.append((*pair, winning_label))

    return results


def _crop_to_pocket(
    img: Image.Image,
    flap_w: int,
    flap_h: int,
    is_top: bool,
    gap_bleed_px: int = 0,
) -> Image.Image:
    """Crop a (possibly bleed-expanded) half-flap image to the physical pocket area.

    Half images may carry outer bleed (top of top halves, bottom of bottom
    halves) and gap-edge bleed (the opposite side, up to *gap_bleed_px*
    rows).  Returns img unchanged when there is no bleed.
    """
    bx = max(0, (img.width - flap_w) // 2)
    extra_y = max(0, img.height - flap_h)
    if bx == 0 and extra_y == 0:
        return img
    gap = min(gap_bleed_px, extra_y)
    outer = extra_y - gap
    if is_top:
        # Rows: [outer][flap_h][gap] → pocket area starts after the outer bleed
        return img.crop((bx, outer, bx + flap_w, outer + flap_h))
    else:
        # Rows: [gap][flap_h][outer] → pocket area starts after the gap bleed
        return img.crop((bx, gap, bx + flap_w, gap + flap_h))


def _collect_preview_entries(
    config: JobConfig,
    dims: AllDimensions,
    dpi: float,
) -> list[tuple[Image.Image, Image.Image, str]]:
    """Collect (top_half, bottom_half, label) for every displayable slot × module.

    Images are rendered with bleed and then cropped to the physical pocket
    area, so the preview is WYSIWYG.

    Rules:
    - Non-multi-module slots appear once.
    - Multi-module slots enumerate every module that is processed for the
      job (from ``_get_modules_to_process``).  Modules that no segment
      covers are shown as a labelled blank so the user can see the gap.
    """
    flap_w = mm_to_px(dims.flap.width, dpi)
    flap_h = mm_to_px(dims.flap.height, dpi)
    bleed_px = mm_to_px(config.output.bleed_mm, dpi)
    gap_bleed_px = min(bleed_px, mm_to_px(dims.flap.gap, dpi))
    blank = Image.new('RGBA', (flap_w, flap_h), (0, 0, 0, 0))

    # Modules that will be rendered (>= 0 only; -1 sentinel excluded)
    processed_modules = sorted(m for m in _get_modules_to_process(config, None) if m >= 0)

    entries = []

    for cf in _flaps_by_slot(config.custom_flaps):
        if cf.type == 'multi-module':
            for m in processed_modules:
                pair = _render_custom_flap_images(cf, config, m, dims, dpi, bleed_px)
                if pair is not None:
                    winning_label = f"{cf.label} M{m} · #{cf.slot}"
                else:
                    pair = (blank.copy(), blank.copy())
                    winning_label = f"BLANK M{m} · #{cf.slot}"
                top = _crop_to_pocket(pair[0], flap_w, flap_h, is_top=True, gap_bleed_px=gap_bleed_px)
                bottom = _crop_to_pocket(pair[1], flap_w, flap_h, is_top=False, gap_bleed_px=gap_bleed_px)
                entries.append((top, bottom, winning_label))
        else:
            # Render module-agnostic
            pair = _render_custom_flap_images(cf, config, -1, dims, dpi, bleed_px)
            if pair is not None:
                top = _crop_to_pocket(pair[0], flap_w, flap_h, is_top=True, gap_bleed_px=gap_bleed_px)
                bottom = _crop_to_pocket(pair[1], flap_w, flap_h, is_top=False, gap_bleed_px=gap_bleed_px)
                entries.append((top, bottom, f"{cf.label} · #{cf.slot}"))

    return entries


def _get_modules_to_process(config: JobConfig, module_filter: Optional[list[int]]) -> list[int]:
    """Determine which modules need custom flap output."""
    if module_filter is not None:
        return module_filter

    modules = set()
    for cf in config.custom_flaps:
        if cf.type == "single":
            # Single images produce the same output for all modules;
            # output once as "common"
            modules.add(-1)  # sentinel for common/shared
        elif cf.type == "multi-module" and cf.segments:
            # Enumerate per segment (not the overall span) so gaps between
            # segments do not pull in modules nothing covers.
            for seg in cf.segments:
                for m in range(seg.module_range[0], seg.module_range[1] + 1):
                    modules.add(m)

    return sorted(modules)


def _finish_and_save(
    front_img: Image.Image,
    back_img: Image.Image,
    front_path: Path,
    back_path: Path,
    config: JobConfig,
    dims: AllDimensions,
    dpi: float,
    reg_on: bool,
) -> None:
    """Apply sheet-space finishing (registration marks, calibration offset,
    DPI metadata) and save both sides.  Shared by the batch and sheet flows.
    """
    if reg_on:
        lw = config.output.registration_mark_line_width_mm
        front_img = draw_registration_marks(front_img, dpi, line_width_mm=lw)
        back_img = draw_registration_marks(back_img, dpi, line_width_mm=lw)

    # Global calibration offset (applied last so it moves *everything* —
    # flap art, ink-save mask, labels, and registration marks — together).
    # Compensates for systematic printer-vs-jig offsets such as eufyMake
    # Zero-Point calibration error.  Per-jig value wins over output section.
    cal = config.jig.calibration_offset_mm
    if cal is None:
        cal = config.output.calibration_offset_mm
    cal_dx_mm, cal_dy_mm = cal
    if cal_dx_mm != 0.0 or cal_dy_mm != 0.0:
        cal_dx_px = round(cal_dx_mm * dpi / 25.4)
        cal_dy_px = round(cal_dy_mm * dpi / 25.4)
        front_img = _apply_calibration_offset(front_img, cal_dx_px, cal_dy_px)
        back_img = _apply_calibration_offset(back_img, cal_dx_px, cal_dy_px)

    # Effective per-axis DPI from actual pixel count vs intended mm size, so
    # downstream tools read back exact physical dimensions (see session log).
    img_w_px, img_h_px = front_img.size
    eff_dpi_x = img_w_px * 25.4 / dims.printable.width
    eff_dpi_y = img_h_px * 25.4 / dims.printable.height
    dpi_info = (eff_dpi_x, eff_dpi_y)

    front_img.save(str(front_path), dpi=dpi_info)
    back_img.save(str(back_path), dpi=dpi_info)
    logger.info("Wrote %s, %s", front_path, back_path)


def render_sticky_mask(
    config: JobConfig,
    dims: AllDimensions,
    dpi: Optional[int] = None,
    output_dir: Optional[str] = None,
) -> Path:
    """Generate the one-time mat-preparation sticky-mask image.

    Prints the flap-pocket coat (see layout.generate_sticky_mask) on the
    same canvas, at the same calibration, as normal batch output — so the
    standard Zero Point workflow applies.  Registration marks and labels
    are never drawn (everything outside the pocket coats must stay
    ink-free: it would land on the mat or the insert).
    """
    dpi = dpi or config.output.dpi
    out_dir = Path(output_dir or config.output.output_dir)

    img = generate_sticky_mask(
        dims.flap, dims.jig, dims.printable, dpi,
        config.output.sticky_mask_inset_mm,
        config.output.sticky_mask_color,
    )

    # Same calibration handling as _finish_and_save (per-jig value wins)
    cal = config.jig.calibration_offset_mm
    if cal is None:
        cal = config.output.calibration_offset_mm
    cal_dx_mm, cal_dy_mm = cal
    if cal_dx_mm != 0.0 or cal_dy_mm != 0.0:
        cal_dx_px = round(cal_dx_mm * dpi / 25.4)
        cal_dy_px = round(cal_dy_mm * dpi / 25.4)
        img = _apply_calibration_offset(img, cal_dx_px, cal_dy_px)

    # Effective per-axis DPI metadata, as for batch output
    img_w_px, img_h_px = img.size
    dpi_info = (img_w_px * 25.4 / dims.printable.width,
                img_h_px * 25.4 / dims.printable.height)

    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = config.output.format.lower()
    path = out_dir / f"sticky_mask.{fmt}"
    img.save(str(path), dpi=dpi_info)
    logger.info("Wrote %s (inset %.2g mm, color RGB%s)", path,
                config.output.sticky_mask_inset_mm,
                tuple(config.output.sticky_mask_color))
    return path


def _render_sheets(
    config: JobConfig,
    dims: AllDimensions,
    dpi: float,
    out_dir: Path,
    modules: list[int],
    module_filter: Optional[list[int]],
    labels_on: bool,
    mask_on: bool,
    reg_on: bool,
    flip: str,
    bleed_px: int,
) -> list[Path]:
    """Global-packing flow for multi-row jigs (standard flatbed).

    The packed sheet set is the COMPLETE physical print job for the whole
    display, one flap each: every module 0..num_modules-1 contributes its
    full flap sequence (modules covered by multi-module segments get their
    per-module sequence; uncovered modules get the module-agnostic
    singles-only sequence).  Print every sheet exactly once — there is no
    "repeat the common file per module" step like the single-row flow.
    Output goes to the output directory root as sheet_NN_front/back images
    plus a sheets_manifest.txt mapping each pocket to its flap label and
    module.

    Back-side geometry is per INSERT: the operator flips each insert row
    individually, so each row is built with left-right semantics and — for
    front-back jobs — rotated 180° about its own row centre.
    """
    # Modules that have module-specific (multi-module) content.
    covered_modules = {m for m in modules if m >= 0}
    # Physical modules to pack: the whole display, or the --modules filter.
    if module_filter is not None:
        physical_modules = sorted(m for m in module_filter if m >= 0)
    else:
        physical_modules = list(range(config.display.num_modules))

    # Collect (front, back, group_label) pairs, one full sequence per
    # physical module.  Front/back pairing (back of flap K = bottom half of
    # flap K+1) is formed within each module's sequence before packing, so
    # any global order is physically correct.
    entries: list[tuple] = []
    for m in physical_modules:
        if m in covered_modules:
            mod_label = f"module_{m:02d}"
            flap_data = _resolve_flaps_for_module(config, m, dims, dpi, bleed_px)
        else:
            # No module-specific content — this module uses the
            # module-agnostic singles-only sequence.
            mod_label = f"module_{m:02d} (common)"
            flap_data = _resolve_flaps_for_module(config, -1, dims, dpi, bleed_px)
        if not flap_data:
            logger.info("No custom flaps for %s, skipping", mod_label)
            continue
        slot_images = [(top, bottom) for top, bottom, _ in flap_data]
        labels = [label for _, _, label in flap_data]
        fronts, backs = map_images_to_flap_sides(slot_images, labels)
        entries += [(f, b, mod_label) for f, b in zip(fronts, backs)]

    if not entries:
        return []

    per_sheet = dims.jig.flaps_per_sheet
    per_row = dims.jig.flaps_per_batch
    gap_bleed_px = min(bleed_px, mm_to_px(dims.flap.gap, dpi))
    num_sheets = math.ceil(len(entries) / per_sheet)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = config.output.format.lower()
    generated: list[Path] = []
    manifest: list[str] = [
        f"Sheet manifest — jig '{config.active_jig}' ({dims.jig.rows} insert rows × "
        f"{per_row} pockets, flip={flip})",
        "Rows are numbered top-to-bottom in the print image; pockets left-to-right.",
        "Back sheets use the same rows — flip each insert individually.",
        "",
    ]

    for s in range(num_sheets):
        chunk = entries[s * per_sheet:(s + 1) * per_sheet]
        front_batch = [e[0] for e in chunk]
        back_batch = [e[1] for e in chunk]

        # Per-insert back reorder: each insert row is its own flip unit.
        reordered_back = []
        for start in range(0, len(back_batch), per_row):
            reordered_back += reorder_for_jig_flip(
                back_batch[start:start + per_row], dims.jig, "left-right")

        front_img = generate_batch_image(front_batch, dims.flap, dims.jig, dims.printable, dpi,
                                         spool_at_bottom=True, gap_bleed_px=gap_bleed_px)
        back_img = generate_batch_image(reordered_back, dims.flap, dims.jig, dims.printable, dpi,
                                        spool_at_bottom=False, gap_bleed_px=gap_bleed_px)

        if mask_on:
            front_img = apply_ink_save_mask(front_img, dims.flap, dims.jig, dims.printable, dpi,
                                            config.output.bleed_mm, spool_at_bottom=True)
            back_img = apply_ink_save_mask(back_img, dims.flap, dims.jig, dims.printable, dpi,
                                           config.output.bleed_mm, spool_at_bottom=False)

        if labels_on:
            front_img = render_labels(front_img, front_batch, dims.flap, dims.jig, dims.printable, dpi,
                                      config.output.label_font_size_pt)
            back_img = render_labels(back_img, reordered_back, dims.flap, dims.jig, dims.printable, dpi,
                                     config.output.label_font_size_pt)

        # Front-back flip == left-right flip + 180° rotation, applied PER
        # INSERT ROW (each insert is flipped individually on the bed).
        if flip == "front-back":
            back_img = rotate_insert_rows_180(back_img, dims.flap, dims.jig, dims.printable, dpi)

        front_path = out_dir / f"sheet_{s + 1:02d}_front.{fmt}"
        back_path = out_dir / f"sheet_{s + 1:02d}_back.{fmt}"
        _finish_and_save(front_img, back_img, front_path, back_path,
                         config, dims, dpi, reg_on)
        generated.extend([front_path, back_path])

        manifest.append(f"Sheet {s + 1:02d}:")
        for start in range(0, len(chunk), per_row):
            row_idx = start // per_row + 1
            row_desc = ", ".join(
                f"P{p + 1}: {e[0].label} [{e[2]}]"
                for p, e in enumerate(chunk[start:start + per_row]))
            manifest.append(f"  Row {row_idx}: {row_desc}")
        manifest.append("")

    manifest_path = out_dir / "sheets_manifest.txt"
    manifest_path.write_text("\n".join(manifest), encoding='utf-8')
    generated.append(manifest_path)
    logger.info("Packed %d flaps onto %d sheet(s); manifest at %s",
                len(entries), num_sheets, manifest_path)

    return generated


def render_job(
    config: JobConfig,
    dims: AllDimensions,
    dpi: Optional[int] = None,
    output_dir: Optional[str] = None,
    module_filter: Optional[list[int]] = None,
    enable_labels: Optional[bool] = None,
    enable_mask: Optional[bool] = None,
    enable_registration_marks: Optional[bool] = None,
    flip_mode: Optional[str] = None,
) -> list[Path]:
    """Main render pipeline: process all modules and write batch images.

    Returns list of paths to generated files.
    """
    dpi = dpi or config.output.dpi
    out_dir = Path(output_dir or config.output.output_dir)
    labels_on = enable_labels if enable_labels is not None else config.output.labels
    mask_on = enable_mask if enable_mask is not None else config.output.ink_save_mask
    reg_on = enable_registration_marks if enable_registration_marks is not None else config.output.registration_marks
    flip = flip_mode or config.jig.flip_mode

    modules = _get_modules_to_process(config, module_filter)
    generated: list[Path] = []
    bleed_px = mm_to_px(config.output.bleed_mm, dpi)
    gap_bleed_px = min(bleed_px, mm_to_px(dims.flap.gap, dpi))

    # Warn upfront about multi-module slots that have coverage gaps for the
    # modules this job will process.  (Explicit blank segments count as
    # coverage and do not warn.)
    processed_module_set = {m for m in modules if m >= 0}
    for cf in _flaps_by_slot(config.custom_flaps):
        if cf.type != 'multi-module':
            continue
        uncovered = [m for m in sorted(processed_module_set)
                     if cf.segment_for_module(m) is None]
        if uncovered:
            logger.warning(
                "Slot %d ('%s'): modules %s have no coverage — will output blank flap(s)",
                cf.slot, cf.label, uncovered,
            )

    if dims.jig.rows > 1:
        # Multi-row jig (standard flatbed): globally pack all groups' flaps
        # onto shared sheets.
        generated += _render_sheets(config, dims, dpi, out_dir, modules, module_filter,
                                    labels_on, mask_on, reg_on, flip, bleed_px)
        modules = []  # skip the per-group batch flow below

    for mod_idx in modules:
        if mod_idx == -1:
            mod_label = "common"
            # Pass module_index=-1 so multi-module entries are skipped;
            # common/ contains only module-agnostic (single/glyph/blank) content.
            flap_data = _resolve_flaps_for_module(config, -1, dims, dpi, bleed_px)
        else:
            mod_label = f"module_{mod_idx:02d}"
            flap_data = _resolve_flaps_for_module(config, mod_idx, dims, dpi, bleed_px)

        if not flap_data:
            logger.info("No custom flaps for %s, skipping", mod_label)
            continue

        slot_images = [(top, bottom) for top, bottom, _ in flap_data]
        labels = [label for _, _, label in flap_data]

        fronts, backs = map_images_to_flap_sides(slot_images, labels)

        # Batch into groups of jig.flaps_per_batch
        batch_size = dims.jig.flaps_per_batch
        num_batches = math.ceil(len(fronts) / batch_size)

        mod_dir = out_dir / mod_label
        mod_dir.mkdir(parents=True, exist_ok=True)

        for b in range(num_batches):
            start = b * batch_size
            end = min(start + batch_size, len(fronts))

            front_batch = fronts[start:end]
            back_batch = backs[start:end]

            # --- Back-side jig-flip geometry -------------------------------
            # A front-back (pancake) flip is geometrically identical to a
            # left-right flip followed by an in-plane 180° rotation:
            #     reflect_x  ==  rotate180 ∘ reflect_y
            # So the back sheet that registers correctly under a front-back
            # flip is exactly the left-right back sheet rotated 180°.
            # We therefore always BUILD the back using left-right semantics
            # (reorder + spool-at-top), then rotate the finished back image
            # 180° when the job is in front-back mode.  This keeps a single,
            # verified code path for both flip modes and guarantees the flap
            # frame (pins/notches/rounded corners) and content stay aligned
            # after the physical flip.
            back_build_flip = "left-right"

            # Generate front image
            front_img = generate_batch_image(front_batch, dims.flap, dims.jig, dims.printable, dpi,
                                             spool_at_bottom=True, gap_bleed_px=gap_bleed_px)

            # Reorder back-side flaps to their post-jig-flip grid positions.
            reordered_back = reorder_for_jig_flip(back_batch, dims.jig, back_build_flip)
            back_img = generate_batch_image(reordered_back, dims.flap, dims.jig, dims.printable, dpi,
                                            spool_at_bottom=False, gap_bleed_px=gap_bleed_px)

            # Apply ink-saving mask
            if mask_on:
                front_img = apply_ink_save_mask(front_img, dims.flap, dims.jig, dims.printable, dpi,
                                                config.output.bleed_mm, spool_at_bottom=True)
                back_img = apply_ink_save_mask(back_img, dims.flap, dims.jig, dims.printable, dpi,
                                               config.output.bleed_mm, spool_at_bottom=False)

            # Add labels
            if labels_on:
                front_img = render_labels(front_img, front_batch, dims.flap, dims.jig, dims.printable, dpi,
                                          config.output.label_font_size_pt)
                back_img = render_labels(back_img, reordered_back, dims.flap, dims.jig, dims.printable, dpi,
                                         config.output.label_font_size_pt)

            # Front-back flip == left-right flip + 180° in-plane rotation.
            # Rotate the fully-composed (art + mask + labels) back sheet 180°
            # so it registers under a front-back jig flip.  Done before
            # registration marks and calibration offset so those stay in
            # true sheet space.
            if flip == "front-back":
                back_img = back_img.transpose(Image.ROTATE_180)

            # Registration marks + calibration offset + DPI metadata + save
            fmt = config.output.format.lower()
            front_path = mod_dir / f"batch_{b + 1:02d}_front.{fmt}"
            back_path = mod_dir / f"batch_{b + 1:02d}_back.{fmt}"
            _finish_and_save(front_img, back_img, front_path, back_path,
                             config, dims, dpi, reg_on)
            generated.extend([front_path, back_path])

    # Generate preview if enabled (uses a separate lower DPI for screen output)
    if config.preview.enabled:
        from .previewer import generate_preview
        preview_dpi = float(config.preview.dpi)
        logger.info("Generating preview at %g DPI", preview_dpi)
        preview_entries = _collect_preview_entries(config, dims, preview_dpi)
        out_dir.mkdir(parents=True, exist_ok=True)
        preview_path = generate_preview(preview_entries, config, dims, out_dir)
        if preview_path:
            generated.append(preview_path)

    return generated
