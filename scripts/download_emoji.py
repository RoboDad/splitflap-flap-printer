#!/usr/bin/env python3
"""Download a Twemoji SVG for use as a flap_printer 'emoji' flap.

The SVG is saved to assets/emoji/ with a human-readable CLDR
short name (e.g. 'waving-hand-medium-skin-tone.svg').  After downloading
you can hand-edit the SVG (adjust colours, stroke weights, etc.) before
committing it to the repo.

Reference the downloaded file in a job config as:

    {"slot": N, "type": "emoji", "name": "waving-hand-medium-skin-tone"}

or let flap_printer resolve it automatically:

    {"slot": N, "type": "emoji", "char": "👋🏽"}

Source: Twemoji (Twitter/X), CC-BY 4.0.
Coverage: Unicode 14.0 / Emoji 14.0 (~3600 emoji).
Emoji newer than Emoji 14.0 are not available in Twemoji.

Usage examples:
    python scripts/download_emoji.py "👋🏽"
    python scripts/download_emoji.py "❤️" --name heart
    python scripts/download_emoji.py "👨‍👩‍👧" --name family
"""

from __future__ import annotations

import argparse
import logging
import sys
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EMOJI_ASSETS_DIR = _REPO_ROOT / 'assets' / 'emoji'

# jsDelivr CDN mirrors the Twemoji GitHub repo assets.
_TWEMOJI_CDN = 'https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/svg/{}.svg'

# U+FE0F — variation selector-16 (emoji presentation).
# Twemoji SVG filenames omit this codepoint.
_VS16 = 0xFE0F

logger = logging.getLogger(__name__)


def codepoints_str(char: str) -> str:
    """Return the Twemoji filename stem for an emoji character/sequence.

    Hex codepoints joined by hyphens, U+FE0F stripped.
    e.g. "👋🏽" → "1f44b-1f3fd"
         "❤️"  → "2764"   (FE0F stripped)
    """
    return '-'.join(f'{ord(c):x}' for c in char if ord(c) != _VS16)


def cldr_name(char: str) -> str | None:
    """Return a hyphenated CLDR short name for the emoji, or None if unknown.

    Requires the 'emoji' package (pip install emoji).
    e.g. "👋🏽" → "waving-hand-medium-skin-tone"
    """
    try:
        import emoji as emoji_lib
    except ImportError:
        logger.warning("'emoji' package not installed; falling back to codepoint name. "
                       "Run: pip install emoji")
        return None
    raw = emoji_lib.demojize(char, language='en')
    # demojize returns ":short_name:" for known emoji, or the char unchanged
    if raw.startswith(':') and raw.endswith(':') and raw.count(':') == 2:
        return raw.strip(':').replace('_', '-')
    return None


def resolve_stem(char: str, override: str | None = None) -> str:
    """Return the filename stem (no extension) to use for this emoji."""
    if override:
        return override
    name = cldr_name(char)
    return name if name else codepoints_str(char)


def download(char: str, stem: str, output_dir: Path) -> Path:
    """Download the Twemoji SVG and return the saved path."""
    cp = codepoints_str(char)
    url = _TWEMOJI_CDN.format(cp)
    dest = output_dir / f'{stem}.svg'
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Fetching %s", url)
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:
        raise RuntimeError(
            f"Download failed: {url}\n"
            f"  {e}\n"
            f"Check that the emoji exists in Twemoji (Unicode 14.0 / Emoji 14.0).\n"
            f"  Codepoints: {cp}"
        ) from e

    return dest


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('char', nargs='?', default=None,
                        help='Emoji character or sequence to download (e.g. "👋🏽")')
    parser.add_argument('--codepoints', default=None,
                        help='Hex codepoint sequence instead of a literal character '
                             '(e.g. --codepoints 1f44b-1f3fd). '
                             'Useful in batch files where emoji characters may be garbled.')
    parser.add_argument('--name', default=None,
                        help='Override the output filename stem '
                             '(default: CLDR short name, e.g. waving-hand-medium-skin-tone)')
    parser.add_argument('--output-dir', type=Path, default=None,
                        help='Output directory (default: assets/emoji/)')
    args = parser.parse_args()

    if args.codepoints:
        # Build char from hex codepoint string, e.g. "1f44b-1f3fd" → "👋🏽"
        try:
            char = ''.join(chr(int(cp, 16)) for cp in args.codepoints.split('-'))
        except ValueError as e:
            logger.error("Invalid --codepoints value %r: %s", args.codepoints, e)
            return 1
    elif args.char:
        char = args.char
    else:
        parser.error("Provide an emoji character or use --codepoints HEX")
        return 1

    output_dir = args.output_dir or _EMOJI_ASSETS_DIR
    stem = resolve_stem(char, args.name)

    try:
        dest = download(char, stem=stem, output_dir=output_dir)
    except RuntimeError as e:
        logger.error("%s", e)
        return 1

    print(f'\nSaved:  {dest.relative_to(_REPO_ROOT)}')
    print(f'\nJob JSON (by name — explicit, edit-safe):')
    print(f'  {{"type": "emoji", "name": "{stem}"}}')
    print(f'\nJob JSON (by char — auto-resolves name at load time):')
    print(f'  {{"type": "emoji", "char": "{char}"}}')
    print(f'\nHand-edit the SVG before committing:')
    print(f'  {dest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
