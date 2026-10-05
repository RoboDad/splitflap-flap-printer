"""SVG input support: rasterize SVG source files into PIL images.

Uses resvg-py (a Rust-backed SVG renderer) so there are no native system
library dependencies on Windows.

Vector input flows through the same downstream pipeline as raster input
(``apply_transforms`` → ``fit_to_target`` → ``slice_display_image``).  We
rasterize at a configurable supersample factor so the final fit/slice
downsample preserves edge quality.
"""

from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# Raster cache for LARGE SVG sources (e.g. generated shan-shui landscapes,
# 10-25 MB of brush strokes that take minutes to rasterize at print DPI).
# The pipeline re-loads a multi-module source once per module and once per
# preview pass, and re-renders re-load everything, so rasterization results
# are cached two ways:
#   - on disk, keyed by source CONTENT hash + render height, as the exact
#     PNG bytes resvg produced (no re-encode; byte-identical pixels).
#     Lives under generated/_raster_cache/, which is gitignored
#     and safe to delete at any time.
#   - in memory (same key), so one run decodes each cached PNG only once.
# Small SVGs (glyphs, emoji) rasterize in milliseconds and skip the cache.
_RASTER_CACHE_DIR = Path(__file__).resolve().parent.parent / 'generated' / '_raster_cache'
_RASTER_CACHE_MIN_SVG_BYTES = 512 * 1024
_mem_cache: dict[tuple[str, int], Image.Image] = {}

# resvg-py is required only when SVG inputs are used.  Import lazily so a
# missing dependency only matters for jobs that actually reference .svg
# files.
_resvg = None


def _get_resvg():
    global _resvg
    if _resvg is None:
        try:
            import resvg_py  # type: ignore
        except ImportError as e:
            raise ImportError(
                "SVG input requires resvg-py.  Install with: pip install resvg-py"
            ) from e
        _resvg = resvg_py
    return _resvg


def is_svg(path: str | Path) -> bool:
    """Return True if path has a .svg extension (case-insensitive)."""
    return str(path).lower().endswith('.svg')


def load_svg(svg_path: str | Path, target_height_px: int, supersample: float = 2.0) -> Image.Image:
    """Rasterize an SVG file to a PIL RGBA Image.

    The SVG is rendered at ``target_height_px * supersample`` pixels of
    height, preserving the aspect ratio implied by the SVG's viewBox or
    width/height attributes.  Downstream ``fit_to_target`` will then
    downsample to the exact target size, which yields better edge quality
    than rendering at 1×.

    A non-zero ``dpi`` is passed to resvg so SVG width/height attributes
    expressed in real-world units (``mm``, ``in``) are interpreted
    correctly.
    """
    svg_path = Path(svg_path)
    if not svg_path.exists():
        raise FileNotFoundError(f"SVG source not found: {svg_path}")

    render_h = max(1, int(round(target_height_px * supersample)))

    # Large sources go through the raster cache (see module docstring).
    # Keyed by content hash, so editing or regenerating the source
    # invalidates automatically.
    cache_key = None
    cache_file = None
    if svg_path.stat().st_size >= _RASTER_CACHE_MIN_SVG_BYTES:
        content_hash = hashlib.sha256(svg_path.read_bytes()).hexdigest()[:16]
        cache_key = (content_hash, render_h)
        cached = _mem_cache.get(cache_key)
        if cached is not None:
            return cached
        cache_file = _RASTER_CACHE_DIR / f"{content_hash}_h{render_h}.png"
        if cache_file.exists():
            logger.info("Raster cache hit for %s (h=%d): %s",
                        svg_path.name, render_h, cache_file.name)
            img = Image.open(cache_file).convert('RGBA')
            _mem_cache[cache_key] = img
            return img
        logger.info("Rasterizing %s at height %d (large SVG — result will be "
                    "cached)...", svg_path.name, render_h)

    resvg = _get_resvg()
    png_bytes = resvg.svg_to_bytes(
        svg_path=str(svg_path),
        height=render_h,
        dpi=96,  # any non-zero value enables mm/in unit handling
    )
    img = Image.open(io.BytesIO(png_bytes)).convert('RGBA')

    if cache_file is not None:
        _RASTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix('.png.tmp')
        tmp.write_bytes(png_bytes)
        tmp.replace(cache_file)
        _mem_cache[cache_key] = img
        logger.info("Cached raster: %s", cache_file.name)

    logger.debug("Loaded SVG %s at %dx%d (supersample=%.1f)",
                 svg_path.name, img.width, img.height, supersample)
    return img
