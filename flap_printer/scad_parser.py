"""Extract dimension variables from OpenSCAD via subprocess or cache.

Primary method: run OpenSCAD on a user-supplied dimensions file (the
``--scad-dims`` option) and parse the FLAP_PRINTER-tagged echo lines from
stderr, e.g. ``echo(str("FLAP_PRINTER:flap_width=", flap_width));``.

Fallback: mtime-based JSON cache next to the .scad file, or caller-
supplied defaults from the job config.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TAG_PREFIX = "FLAP_PRINTER:"
_CACHE_FILENAME = ".flap_printer_dims.json"

# Match ECHO: "FLAP_PRINTER:key=value"
_ECHO_RE = re.compile(
    r'ECHO:\s*"' + re.escape(_TAG_PREFIX) + r'(\w+)=([^"]+)"'
)


def _find_openscad(openscad_path: Optional[str] = None) -> Optional[str]:
    """Locate the OpenSCAD executable."""
    if openscad_path:
        if os.path.isfile(openscad_path):
            return openscad_path
        # Maybe just a name — let shutil.which resolve it
        found = shutil.which(openscad_path)
        if found:
            return found
        return None

    # Try common locations
    found = shutil.which("openscad")
    if found:
        return found

    # Windows default install paths — nightly before stable so newer build is preferred
    for prog_dir in [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]:
        if prog_dir:
            for subdir in ["OpenSCAD (Nightly)", "OpenSCAD"]:
                candidate = os.path.join(prog_dir, subdir, "openscad.exe")
                if os.path.isfile(candidate):
                    return candidate

    return None


def _parse_echo_output(stderr_text: str) -> dict[str, float]:
    """Parse FLAP_PRINTER-tagged echo lines from OpenSCAD stderr."""
    result: dict[str, float] = {}
    for match in _ECHO_RE.finditer(stderr_text):
        key = match.group(1)
        val_str = match.group(2).strip()
        try:
            result[key] = float(val_str)
        except ValueError:
            logger.warning("Could not parse FLAP_PRINTER value: %s=%s", key, val_str)
    return result


def _cache_path(scad_path: Path) -> Path:
    """Return the cache file path for a given .scad file."""
    return scad_path.parent / _CACHE_FILENAME


def _read_cache(scad_path: Path) -> Optional[dict[str, float]]:
    """Read cached dimensions if the cache is newer than the .scad file."""
    cache = _cache_path(scad_path)
    if not cache.exists():
        return None

    scad_mtime = scad_path.stat().st_mtime
    cache_mtime = cache.stat().st_mtime
    if cache_mtime < scad_mtime:
        logger.debug("Cache is stale (scad modified after cache)")
        return None

    try:
        with open(cache, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.debug("Loaded %d cached dimensions from %s", len(data), cache)
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read cache %s: %s", cache, e)
        return None


def _write_cache(scad_path: Path, values: dict[str, float]) -> None:
    """Write dimensions to the JSON cache file."""
    cache = _cache_path(scad_path)
    try:
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(values, f, indent=2)
        logger.debug("Wrote %d dimensions to cache %s", len(values), cache)
    except OSError as e:
        logger.warning("Failed to write cache %s: %s", cache, e)


def run_openscad(
    scad_path: str | Path,
    openscad_path: Optional[str] = None,
    use_cache: bool = True,
) -> tuple[Optional[dict[str, float]], bool]:
    """Run OpenSCAD on the given .scad file and extract FLAP_PRINTER echo values.

    Returns a tuple of (values, from_cache) where *values* is a dict of
    variable names to float values (or None on failure) and *from_cache*
    indicates whether the result came from the JSON cache.

    Uses an mtime-based JSON cache to avoid re-running OpenSCAD when the
    .scad file hasn't changed.
    """
    scad_path = Path(scad_path)
    if not scad_path.exists():
        logger.warning("SCAD file not found: %s", scad_path)
        return None, False

    # Check cache first
    if use_cache:
        cached = _read_cache(scad_path)
        if cached is not None:
            return cached, True

    # Find OpenSCAD
    exe = _find_openscad(openscad_path)
    if exe is None:
        logger.info("OpenSCAD not found — will use fallback defaults")
        return None, False

    # Run OpenSCAD — use a temp .csg file as output (fastest format, avoids
    # the "no valid suffix" error that NUL triggers on Windows)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.csg')
    os.close(tmp_fd)
    cmd = [exe, "-o", tmp_path, str(scad_path)]
    logger.debug("Running: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(scad_path.parent),  # so relative use<>/include<> paths resolve
        )
    except FileNotFoundError:
        logger.warning("OpenSCAD executable not found at: %s", exe)
        return None, False
    except subprocess.TimeoutExpired:
        logger.warning("OpenSCAD timed out after 60s")
        return None, False
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if proc.returncode != 0:
        logger.warning("OpenSCAD exited with code %d", proc.returncode)
        logger.debug("OpenSCAD stderr:\n%s", proc.stderr[:2000])

    # Parse both stderr and stdout (some OpenSCAD versions differ)
    values = _parse_echo_output(proc.stderr + proc.stdout)

    if not values:
        logger.warning("No FLAP_PRINTER echo values found in OpenSCAD output")
        return None, False

    logger.info("Extracted %d dimensions from OpenSCAD", len(values))

    # Write cache
    if use_cache:
        _write_cache(scad_path, values)

    return values, False
