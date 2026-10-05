"""Procedural image generators for the ``"generated"`` custom-flap type.

A generated flap resolves to a cached image file at config-load time and
then lowers to ordinary ``single`` / ``multi-module`` semantics, per the
"adding new flap types" contract in the README — the renderer never knows
generators exist.

Generators live under ``generators/<name>/`` (vendored sources + a driver)
or, for pure-Python ones, in this package.  Output is cached under
``generated/<name>/`` keyed by the generation arguments, so a given
(generator, seed, geometry) pair runs its external tool once and is
reproducible forever after; the cache directory is gitignored (copy a
keeper into ``assets/local/`` to pin it).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_GENERATORS_DIR = Path(__file__).resolve().parent.parent / 'generators'
_CACHE_DIR = Path(__file__).resolve().parent.parent / 'generated'

# Canonical generator names (aliases map to these in run_generator)
_ALIASES = {
    'shan-shui-infinite': 'shan-shui-inf',
    'shanshui': 'shan-shui-inf',
    'skyline': 'city-skyline',
    'city': 'city-skyline',
}


def _safe(token: str) -> str:
    """Sanitize a token for use in a cache filename."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(token))


def _require_node() -> str:
    node = shutil.which('node')
    if node is None:
        raise RuntimeError(
            "The 'generated' flap type requires Node.js ('node' not found on "
            "PATH). Install from https://nodejs.org and retry.")
    return node


def _gen_shan_shui_inf(seed: str, aspect: float, params: dict, out_path: Path) -> None:
    """{Shan, Shui}* by Lingdong Huang — procedural Chinese landscape.

    The vendored generator has a fixed world height of 800 units; the
    window width is chosen from the target aspect ratio.  Supported
    params: ``background`` (CSS color for a paper-style backing rect;
    default none = transparent, which prints as white art on dark stock).
    """
    node = _require_node()
    width = max(1, round(800 * aspect))
    driver = _GENERATORS_DIR / 'shan-shui-inf' / 'driver.js'
    cmd = [node, str(driver), '--seed', str(seed), '--width', str(width),
           '--out', str(out_path)]
    background = params.get('background')
    if background:
        cmd += ['--background', str(background)]
    logger.info("Running generator shan-shui-inf (seed=%s, width=%d)...", seed, width)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(
            f"shan-shui-inf generator failed (exit {result.returncode}):\n"
            f"{result.stderr[-2000:]}")


def _gen_city_skyline(seed: str, aspect: float, params: dict, out_path: Path) -> None:
    """Stylized procedural city skyline (first-party Python generator —
    see gen_city_skyline.py for the full option list)."""
    from .gen_city_skyline import generate
    logger.info("Running generator city-skyline (seed=%s, time_of_day=%s)...",
                seed, params.get('time_of_day', 'night'))
    generate(seed, aspect, params, out_path)


# name -> (builder, source files hashed into the cache key so that editing
# a generator automatically invalidates its cached outputs)
_PKG_DIR = Path(__file__).resolve().parent
GENERATORS: dict[str, tuple[Callable[[str, float, dict, Path], None], list[Path]]] = {
    'shan-shui-inf': (_gen_shan_shui_inf,
                      [_GENERATORS_DIR / 'shan-shui-inf' / 'driver.js',
                       _GENERATORS_DIR / 'shan-shui-inf' / 'shan-shui-inf.html']),
    'city-skyline': (_gen_city_skyline,
                     [_PKG_DIR / 'gen_city_skyline.py']),
}


def _source_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.read_bytes())
    return h.hexdigest()[:8]


def run_generator(name: str, seed: str, aspect: float, params: dict) -> Path:
    """Generate (or reuse from cache) an image and return its path.

    Cache key = generator + seed + aspect-derived geometry + params, so the
    same job config always maps to the same file and the external tool only
    runs when that exact combination has never been generated before.
    """
    canonical = _ALIASES.get(name, name)
    if canonical not in GENERATORS:
        known = sorted(set(GENERATORS) | set(_ALIASES))
        raise ValueError(f"Unknown generator {name!r} (known: {', '.join(known)})")

    builder, src_files = GENERATORS[canonical]
    width = max(1, round(800 * aspect))
    parts = [f"s{_safe(seed)}", f"w{width}"]
    if params:
        # All generator params participate in the cache key (stable JSON hash)
        pjson = json.dumps(params, sort_keys=True, separators=(',', ':'))
        parts.append("p" + hashlib.sha256(pjson.encode()).hexdigest()[:8])
    # The generator's own source participates too, so editing a generator
    # invalidates its cached outputs automatically (old files become
    # harmless orphans in the gitignored cache dir).
    parts.append("g" + _source_hash(src_files))
    out_dir = _CACHE_DIR / canonical
    out_path = out_dir / ("_".join(parts) + ".svg")

    if out_path.exists():
        logger.info("Generator cache hit: %s", out_path)
        return out_path

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix('.svg.tmp')
    try:
        builder(str(seed), aspect, params, tmp_path)
        tmp_path.replace(out_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    logger.info("Generated %s", out_path)
    return out_path
