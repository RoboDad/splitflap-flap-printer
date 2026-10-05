#!/usr/bin/env python3
"""Generate per-character Epilogue flap SVGs for use with flap_printer.

For each character in Scott Bezek's standard 52-character flap list, this
script invokes OpenSCAD on scad/epilogue_flap_single.scad via Scott's
projection_renderer pipeline (3d/scripts/ in the upstream splitflap repo)
to produce a single SVG that depicts that character's full display face
(top half + bottom half, no flap outline).

Requirements (not needed to USE flap_printer — the generated SVGs are
committed under assets/flap_glyphs/):
  * OpenSCAD on PATH
  * a checkout of https://github.com/scottbez1/splitflap — by default a
    sibling directory named "splitflap"; override with --splitflap-root or
    the SPLITFLAP_ROOT environment variable
  * pip install -r <splitflap>/3d/scripts/requirements.txt  (svg.path 6.x)

Outputs are written to assets/flap_glyphs/<font>/ as flap_NN.svg (0-padded
indices) along with index.json mapping indices to characters.  The default
output directory is auto-computed from --font; override with --output-dir.

Run once per font change; the resulting SVGs are committed and used by
the ``"glyph"`` (and backward-compat ``"epilogue"``) flap types in job
configs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_splitflap_root(argv: list[str]) -> Path:
    """Locate the upstream splitflap checkout (sibling dir by default).

    Parsed ahead of argparse because the renderer imports below need it on
    sys.path before anything else happens.
    """
    root = None
    for i, a in enumerate(argv):
        if a == '--splitflap-root' and i + 1 < len(argv):
            root = argv[i + 1]
        elif a.startswith('--splitflap-root='):
            root = a.split('=', 1)[1]
    root = Path(root or os.environ.get('SPLITFLAP_ROOT') or (_REPO_ROOT.parent / 'splitflap'))
    root = root.resolve()
    if not (root / '3d' / 'scripts' / 'projection_renderer.py').is_file():
        sys.exit(f"error: splitflap checkout not found at {root} "
                 f"(expected 3d/scripts/projection_renderer.py). Clone "
                 f"https://github.com/scottbez1/splitflap next to this repo, or pass "
                 f"--splitflap-root / set SPLITFLAP_ROOT.")
    return root


_SPLITFLAP_ROOT = _resolve_splitflap_root(sys.argv[1:])
sys.path.insert(0, str(_SPLITFLAP_ROOT / '3d' / 'scripts'))
# The SCAD wrapper's include<3d/...> lines resolve through OPENSCADPATH.
os.environ['OPENSCADPATH'] = os.pathsep.join(
    p for p in (str(_SPLITFLAP_ROOT), os.environ.get('OPENSCADPATH', '')) if p)

from projection_renderer import Renderer  # noqa: E402
from svg_processor import SvgProcessor  # noqa: E402

# Must stay in sync with the upstream 3d/flap_characters.scad.  We re-state
# it here so we can build the index.json without parsing SCAD.
CHARACTER_LIST = " ABCDEFGHIJKLMNOPQRSTUVWXYZg0123456789r.?-$'#yp,!@&w"

# Friendly names for non-alphanumeric characters, used in optional debug
# logging only (filenames stay numeric for unambiguity).
_CHAR_NAMES = {
    ' ': 'space', '.': 'dot', '?': 'qmark', '-': 'dash',
    '$': 'dollar', "'": 'apos', '#': 'hash', ',': 'comma',
    '!': 'bang', '@': 'at', '&': 'amp',
}

logger = logging.getLogger(__name__)


def _postprocess_svg(svg_path: Path, outputs: dict, fill_color: str) -> None:
    """Rewrite the SVG so it matches the flap display face exactly.

    OpenSCAD's projection export sets the viewBox to the bounding box of
    the rendered geometry (i.e. the letter outlines), which is too small
    and offset wrong for use as a "display face" SVG.  We post-process to:

      1. Override viewBox + width/height so the SVG's coordinate system
         exactly covers (flap_width) x (2*flap_height + flap_gap) mm,
         with (0, 0) at the top-left of the top flap face.
      2. Apply etch styling, then override fill to *fill_color* — the
         letters need to render as solid ink in the colour the printed
         flap material requires (typically white on dark flap stock).
      3. Remove redundant duplicate line segments before fill conversion.
    """
    flap_w = float(outputs['epilogue_flap_width'])
    flap_h = float(outputs['epilogue_flap_height'])
    flap_gap = float(outputs['epilogue_flap_gap'])
    flap_pin = float(outputs['epilogue_flap_pin_width'])

    # SCAD origin: top-flap content occupies SCAD-y in [-pin/2, h - pin/2].
    # SVG y is inverted in OpenSCAD's projection export, so SCAD y=h-pin/2
    # becomes SVG y = -(h - pin/2).  ViewBox starts there and spans the
    # full display face height (2h + gap).
    vb_x = 0.0
    vb_y = -(flap_h - flap_pin / 2)
    vb_w = flap_w
    vb_h = 2 * flap_h + flap_gap

    processor = SvgProcessor(str(svg_path))
    processor.remove_redundant_lines()
    processor.apply_laser_etch_style()
    # Override the fill colour set by apply_laser_etch_style() (which
    # hard-codes #000000) to whatever the caller asked for.
    for path in processor.svg_node.getElementsByTagName('path'):
        path.setAttribute('fill', fill_color)
    # Override SVG root attributes directly.
    svg_node = processor.svg_node
    svg_node.setAttribute('width', f'{vb_w}mm')
    svg_node.setAttribute('height', f'{vb_h}mm')
    svg_node.setAttribute('viewBox', f'{vb_x} {vb_y} {vb_w} {vb_h}')
    processor.write(str(svg_path))


def render_single(scad_path: Path, flap_index: int, bleed: float, font_preset: str,
                  fill_color: str) -> Path:
    """Render one character's SVG via OpenSCAD; return path to display-face SVG.

    Special-case: the space character (index 0) has no letter geometry, so
    OpenSCAD's projection produces nothing.  We emit an empty SVG with the
    correct viewBox in that case.
    """
    with tempfile.TemporaryDirectory(prefix='epilogue_flap_') as tmp:
        renderer = Renderer(
            input_file=str(scad_path),
            output_folder=tmp,
            extra_variables={
                'flap_index': flap_index,
                'bleed': bleed,
                'font_preset': font_preset,
            },
        )
        renderer.clean()
        try:
            svg_path, outputs = renderer.render_svgs(panelize_quantity=1)
            _postprocess_svg(Path(svg_path), outputs, fill_color)
        except AttributeError:
            # No geometry produced (e.g. the space character).  Build an
            # empty SVG using the SCAD-echoed dims from a dry-run extract.
            outputs = renderer._get_extracted_outputs()
            svg_path = str(Path(tmp) / 'combined.svg')
            _write_empty_svg(Path(svg_path), outputs)

        out_tmp = Path(tmp).parent / f'_epilogue_{flap_index:02d}.svg'
        shutil.copy(svg_path, out_tmp)
        return out_tmp


def _write_empty_svg(svg_path: Path, outputs: dict) -> None:
    flap_w = float(outputs['epilogue_flap_width'])
    flap_h = float(outputs['epilogue_flap_height'])
    flap_gap = float(outputs['epilogue_flap_gap'])
    flap_pin = float(outputs['epilogue_flap_pin_width'])
    vb_y = -(flap_h - flap_pin / 2)
    vb_h = 2 * flap_h + flap_gap
    svg_path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{flap_w}mm" '
        f'height="{vb_h}mm" viewBox="0 {vb_y} {flap_w} {vb_h}" version="1.1"/>\n',
        encoding='utf-8',
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=None,
                        help='Where to write flap_NN.svg files. '
                             'Defaults to assets/flap_glyphs/<font>.')
    parser.add_argument('--splitflap-root', type=Path, default=_SPLITFLAP_ROOT,
                        help='Path to a scottbez1/splitflap checkout '
                             '(default: $SPLITFLAP_ROOT or ../splitflap)')
    parser.add_argument('--font', default='Epilogue',
                        help='Font preset name (see 3d/flap_fonts.scad in the splitflap repo)')
    parser.add_argument('--bleed', type=float, default=0.0,
                        help='Letter bleed (mm); usually 0 here, since the '
                             'flap_printer applies its own bleed at raster time')
    parser.add_argument('--fill-color', default='#ffffff',
                        help='SVG fill colour for the letter geometry. Defaults '
                             'to white (#ffffff) since printed flaps are '
                             'typically dark stock with white ink. Use '
                             '#000000 for the previous black-on-transparent '
                             'behaviour.')
    parser.add_argument('--only', type=int, default=None,
                        help='Render only this index (for debugging)')
    args = parser.parse_args()
    if args.output_dir is None:
        args.output_dir = _REPO_ROOT / 'assets' / 'flap_glyphs' / args.font

    scad_path = _REPO_ROOT / 'scad' / 'epilogue_flap_single.scad'
    if not scad_path.exists():
        logger.error("SCAD wrapper not found: %s", scad_path)
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    indices = [args.only] if args.only is not None else range(len(CHARACTER_LIST))
    index_map: dict[int, str] = {}

    for i in indices:
        char = CHARACTER_LIST[i]
        friendly = _CHAR_NAMES.get(char, char)
        logger.info("Rendering flap %02d  (char %r / %s)", i, char, friendly)

        produced = render_single(scad_path, flap_index=i, bleed=args.bleed,
                                 font_preset=args.font,
                                 fill_color=args.fill_color)
        dest = args.output_dir / f'flap_{i:02d}.svg'
        shutil.move(str(produced), str(dest))
        index_map[i] = char
        logger.info("  -> %s", dest)

    # Always include the full mapping in index.json (even on --only runs)
    full_map = {i: c for i, c in enumerate(CHARACTER_LIST)}
    index_path = args.output_dir / 'index.json'
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump({
            'font': args.font,
            'character_list': CHARACTER_LIST,
            'index_to_char': full_map,
        }, f, indent=2)
    logger.info("Wrote %s", index_path)

    return 0


if __name__ == '__main__':
    sys.exit(main())
