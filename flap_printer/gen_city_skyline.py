"""Procedural city-skyline generator ("city-skyline") for the generated flap type.

Stylized graphic skylines of effectively infinite width — think residential
New York: varied building archetypes (slab towers, art-deco setbacks, glass
towers, walk-ups with fire escapes and rooftop water towers, spired
landmarks), rooftop clutter (antennas with beacons, AC units, chimneys),
and occasional rooftop silhouettes (humans, cats, dogs).

Three times of day (params["time_of_day"]):
  "night" (default) — near-black sky with a warm city-glow gradient at the
      horizon, star field, full moon with craters and halo, lit windows,
      red antenna beacons, and an occasional UFO.
  "dusk" — layered sunset gradient, setting sun half-hidden behind the far
      skyline, dark streak clouds with glowing undersides, early stars.
  "day" — blue sky gradient, sun with halo, puffy flat-bottomed cumulus
      clouds, jet contrails with tiny planes, unlit/reflective windows.

Supported params (all optional):
  time_of_day : "night" | "dusk" | "day"        (default "night")
  density     : 0.0..1.0 skyline fullness        (default 0.92; lower = gaps)
  moon        : bool                             (default True, night only)
  ufo         : bool                             (default seeded ~1-in-3, night)
  height      : world height in SVG units        (default 800)

Deterministic: the same (seed, aspect, params) always produces the same SVG.
Rendered via the standard resvg pipeline; no SVG filters are used.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _n(v: float) -> str:
    """Compact numeric formatting for SVG coordinates."""
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


def _rect(x, y, w, h, fill, opacity=None, rx=None):
    o = f' opacity="{opacity}"' if opacity is not None else ""
    r = f' rx="{_n(rx)}"' if rx else ""
    return (f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}"'
            f' fill="{fill}"{o}{r}/>')


def _circle(cx, cy, r, fill, opacity=None):
    o = f' opacity="{opacity}"' if opacity is not None else ""
    return f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" fill="{fill}"{o}/>'


def _poly(pts, fill, opacity=None):
    o = f' opacity="{opacity}"' if opacity is not None else ""
    p = " ".join(f"{_n(x)},{_n(y)}" for x, y in pts)
    return f'<polygon points="{p}" fill="{fill}"{o}/>'


def _line(x1, y1, x2, y2, stroke, width, opacity=None, cap="butt"):
    o = f' opacity="{opacity}"' if opacity is not None else ""
    return (f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}"'
            f' stroke="{stroke}" stroke-width="{_n(width)}" stroke-linecap="{cap}"{o}/>')


class _Envelope:
    """Smooth 1-D height envelope (seeded value noise, cosine interpolation)."""

    def __init__(self, rng: random.Random, width: float, step: float = 280.0):
        self.step = step
        n = int(width / step) + 3
        self.vals = [rng.random() for _ in range(n)]

    def at(self, x: float) -> float:
        t = x / self.step
        i = max(0, min(len(self.vals) - 2, int(t)))
        f = t - i
        f = 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, f)))
        return self.vals[i] * (1 - f) + self.vals[i + 1] * f


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

PALETTES = {
    "night": {
        "sky": [(0.0, "#04050e"), (0.55, "#0a0d20"), (0.82, "#171633"), (1.0, "#3a2737")],
        "glow": "#c07a44",
        "far": "#151b31", "mid": "#0c1022", "fg": "#07090f",
        "far_dot": "#4e4a68",
        "lit": ["#ffd98c", "#ffcf7a", "#ffe3a8", "#f5b964"], "tv": "#9fd4ff",
        "lit_p": 0.34, "unlit": None,
        "glass_strip": None,
        "beacon": "#ff5a4e", "detail": "#10141f",
        "escape": "#03040a", "figure": "#020309",
        "moon": "#f2eedd", "crater": "#d8d3bd", "star": "#ffffff",
    },
    "dusk": {
        "sky": [(0.0, "#1b1038"), (0.38, "#45215c"), (0.62, "#8e3b5c"),
                (0.82, "#d96b4a"), (1.0, "#f7b45c")],
        "glow": None,
        "far": "#593057", "mid": "#2b1838", "fg": "#140b1a",
        "far_dot": "#8a5a72",
        "lit": ["#ffc97e", "#ffb45e", "#ffdca0"], "tv": "#aee0ff",
        "lit_p": 0.5, "unlit": None,
        "glass_strip": None,
        "beacon": "#ff5a4e", "detail": "#1d1226",
        "escape": "#0a0510", "figure": "#0a0510",
        "star": "#fff3dd",
        "sun": "#ffd99a", "sun_halo": "#ff9a5c",
        "cloud": "#2e1b3e", "cloud_edge": "#f09a5a",
    },
    "day": {
        "sky": [(0.0, "#3d7bd0"), (0.6, "#7fb2e8"), (1.0, "#d2e7f6")],
        "glow": None,
        "far": "#b4c7dc", "mid": "#8096b0", "fg": "#4a5a72",
        "far_dot": None,
        "lit": None, "tv": None,
        "lit_p": 0.0, "unlit": "#323f58",
        "glass_strip": "#7fa6cc",
        "beacon": "#8e2f2f", "detail": "#3a485e",
        "escape": "#2b3850", "figure": "#1c2638",
        "sun": "#fff3c4", "sun_halo": "#ffe9a0",
        "cloud": "#ffffff", "cloud_shade": "#ccdcec",
        "contrail": "#ffffff", "plane": "#2c3a52",
    },
}

# ---------------------------------------------------------------------------
# Rooftop silhouettes (unit paths: 1.0 = figure height, y down, feet at y=1)
# ---------------------------------------------------------------------------

# Standing person, slight contrapposto, hands in pockets.
_HUMAN = ('<circle cx=".18" cy=".10" r=".088" fill="{f}"/>'
          '<path fill="{f}" d="M .085,.19 L .275,.19 L .315,.50 L .265,.52 '
          'L .255,.40 L .26,.60 L .245,.98 L .19,.98 L .185,.64 L .165,.64 '
          'L .15,.98 L .095,.98 L .095,.60 L .105,.40 L .09,.52 L .04,.50 Z"/>')

# Sitting cat facing left: ears, arched back, haunch, upright curled tail.
_CAT = ('<path fill="{f}" d="M .07,1 L .07,.66 Q .05,.46 .17,.40 L .155,.26 '
        'L .21,.10 L .255,.26 L .33,.26 L .375,.10 L .42,.27 Q .49,.36 .44,.47 '
        'Q .58,.56 .63,.74 Q .655,.84 .65,1 Z"/>'
        '<path fill="{f}" d="M .63,.97 Q .83,.92 .83,.60 Q .83,.44 .72,.42 '
        'Q .78,.54 .745,.70 Q .71,.88 .58,.90 L .58,.97 Z"/>')

# Standing dog facing left: muzzle, floppy ear, level back, tail up.
_DOG = ('<path fill="{f}" d="M .02,.38 L .14,.30 L .17,.18 L .23,.28 L .30,.30 '
        'L .62,.32 L .72,.22 L .78,.10 L .83,.14 L .76,.30 L .73,.40 L .70,.62 '
        'L .72,.98 L .65,.98 L .62,.66 L .36,.66 L .34,.98 L .27,.98 L .26,.62 '
        'L .18,.54 L .10,.52 L .02,.46 Z"/>'
        '<path fill="{f}" d="M .13,.30 L .19,.33 L .18,.46 L .12,.42 Z"/>')

_FIGURES = [("cat", _CAT, 0.9), ("human", _HUMAN, 1.0), ("dog", _DOG, 0.55)]


def _figure_svg(kind_tpl: str, x: float, y_base: float, h: float, fill: str,
                flip: bool) -> str:
    """Place a unit figure path with feet at (x, y_base), height h."""
    sx = -h if flip else h
    tx = x + (h * 0.5 if flip else -h * 0.25)
    return (f'<g transform="translate({_n(tx)},{_n(y_base - h)}) '
            f'scale({_n(sx)},{_n(h)})">{kind_tpl.format(f=fill)}</g>')


# ---------------------------------------------------------------------------
# Sky
# ---------------------------------------------------------------------------


def _sky(rng, W, H, tod, pal, opts):
    out = []
    stops = "".join(f'<stop offset="{_n(o * 100)}%" stop-color="{c}"/>'
                    for o, c in pal["sky"])
    out.append(f'<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
               f'{stops}</linearGradient>'
               f'<radialGradient id="halo"><stop offset="0%" stop-color="#ffffff" '
               f'stop-opacity="0.9"/><stop offset="100%" stop-color="#ffffff" '
               f'stop-opacity="0"/></radialGradient></defs>')
    out.append(_rect(0, 0, W, H, "url(#sky)"))

    if tod == "night":
        # City-glow pools low on the horizon, behind everything
        if opts["glow"]:
            out.append('<defs><radialGradient id="glow"><stop offset="0%" '
                       f'stop-color="{pal["glow"]}" stop-opacity="0.32"/>'
                       '<stop offset="100%" stop-color="#000000" stop-opacity="0"/>'
                       '</radialGradient></defs>')
            for _ in range(2 + int(W / 1200)):
                gx = rng.uniform(0.05, 0.95) * W
                grx = rng.uniform(380, 750)
                out.append(f'<ellipse cx="{_n(gx)}" cy="{_n(H * 1.02)}" '
                           f'rx="{_n(grx)}" ry="{_n(grx * 0.5)}" fill="url(#glow)"/>')
        # Stars: density falls toward the glow at the horizon
        n_stars = int(W * H / 14000)
        for _ in range(n_stars):
            sx, sy = rng.uniform(0, W), rng.uniform(0, H * 0.78) ** 1.15
            if sy > H * 0.75:
                continue
            fade = 1.0 - sy / (H * 0.85)
            r = rng.choice((0.7, 0.9, 1.1, 1.4, 1.4, 1.9))
            op = round(min(1.0, rng.uniform(0.25, 1.0) * (0.35 + fade)), 2)
            out.append(_circle(sx, sy, r, pal["star"], op))
            if r > 1.6 and rng.random() < 0.5:  # sparkle cross on big stars
                out.append(_line(sx - 4, sy, sx + 4, sy, pal["star"], 0.6, op * 0.6))
                out.append(_line(sx, sy - 4, sx, sy + 4, pal["star"], 0.6, op * 0.6))
        # Moon with craters and halo
        if opts["moon"]:
            mx = rng.uniform(0.12, 0.88) * W
            my = rng.uniform(0.10, 0.30) * H
            mr = rng.uniform(36, 52)
            out.append(_circle(mx, my, mr * 3.2, "url(#halo)", 0.16))
            out.append(_circle(mx, my, mr, pal["moon"]))
            for _ in range(rng.randint(3, 5)):
                a, d = rng.uniform(0, 6.28), rng.uniform(0.15, 0.62) * mr
                out.append(_circle(mx + d * math.cos(a), my + d * math.sin(a),
                                   rng.uniform(0.1, 0.22) * mr, pal["crater"], 0.8))
            opts["moon_pos"] = (mx, my, mr)
        # Occasional UFO
        if opts["ufo"]:
            ux = rng.uniform(0.1, 0.9) * W
            uy = rng.uniform(0.08, 0.30) * H
            if opts.get("moon_pos"):
                mxx, myy, mrr = opts["moon_pos"]
                if abs(ux - mxx) < mrr * 4:
                    ux = (mxx + W * 0.3) % W
            if rng.random() < 0.55:  # abduction beam: luminous teal-green glow
                out.append('<defs><linearGradient id="beam" x1="0" y1="0" '
                           'x2="0" y2="1">'
                           '<stop offset="0%" stop-color="#d2ffe6" stop-opacity="0.85"/>'
                           '<stop offset="35%" stop-color="#7df2c2" stop-opacity="0.45"/>'
                           '<stop offset="100%" stop-color="#3fd4a0" stop-opacity="0"/>'
                           '</linearGradient></defs>')
                out.append(_poly([(ux - 10, uy + 5), (ux + 10, uy + 5),
                                  (ux + 34, uy + 120), (ux - 34, uy + 120)],
                                 "url(#beam)"))
                out.append(_poly([(ux - 4, uy + 5), (ux + 4, uy + 5),
                                  (ux + 13, uy + 115), (ux - 13, uy + 115)],
                                 "url(#beam)"))
            out.append(f'<ellipse cx="{_n(ux)}" cy="{_n(uy - 7)}" rx="11" ry="9" '
                       f'fill="#2c3a55"/>')
            out.append(f'<ellipse cx="{_n(ux)}" cy="{_n(uy)}" rx="27" ry="8" '
                       f'fill="#1a2238"/>')
            for i in (-16, 0, 16):
                out.append(_circle(ux + i, uy + 2.5, 2.1, "#aef2b0", 0.95))

    elif tod == "day":
        # Sun with halo
        sx = rng.uniform(0.12, 0.88) * W
        sy = rng.uniform(0.10, 0.28) * H
        sr = rng.uniform(26, 36)
        out.append(_circle(sx, sy, sr * 3.4, "url(#halo)", 0.35))
        out.append(_circle(sx, sy, sr, pal["sun"]))
        # Contrails (long tapered streaks with a tiny plane at the head)
        for _ in range(rng.randint(1, 2)):
            cx0 = rng.uniform(0.05, 0.6) * W
            cy0 = rng.uniform(0.08, 0.35) * H
            length = rng.uniform(260, 620)
            ang = math.radians(rng.uniform(-14, 14))
            hx = cx0 + length * math.cos(ang)
            hy = cy0 - length * math.sin(ang)
            nx, ny = -math.sin(ang), -math.cos(ang)
            out.append(_poly([(cx0 + nx * 0.4, cy0 + ny * 0.4),
                              (cx0 - nx * 0.4, cy0 - ny * 0.4),
                              (hx - nx * 3.2, hy - ny * 3.2),
                              (hx + nx * 3.2, hy + ny * 3.2)],
                             pal["contrail"], 0.4))
            pd = 9
            out.append(_poly([(hx + pd * math.cos(ang), hy - pd * math.sin(ang)),
                              (hx - pd * 0.5 * math.cos(ang) + nx * 4,
                               hy + pd * 0.5 * math.sin(ang) + ny * 4),
                              (hx - pd * 0.5 * math.cos(ang) - nx * 4,
                               hy + pd * 0.5 * math.sin(ang) - ny * 4)],
                             pal["plane"]))
        # Puffy flat-bottomed cumulus
        for _ in range(2 + int(W / 900)):
            cx = rng.uniform(0, W)
            cy = rng.uniform(0.10, 0.42) * H
            s = rng.uniform(28, 64)
            op = rng.uniform(0.85, 0.97)
            lobes = [(0, 0, 1.0), (0.85, -0.12, 0.74), (-0.85, -0.08, 0.7),
                     (0.38, -0.5, 0.62), (-0.34, -0.46, 0.58)]
            for lx, ly, lr in lobes[:rng.randint(3, 5)]:
                out.append(_circle(cx + lx * s, cy + ly * s, lr * s,
                                   pal["cloud"], op))
            out.append(_rect(cx - 1.3 * s, cy - 0.18 * s, 2.6 * s, 0.5 * s,
                             pal["cloud"], op, rx=0.25 * s))
            out.append(_rect(cx - 0.95 * s, cy + 0.23 * s, 1.9 * s, 0.09 * s,
                             pal["cloud_shade"], 0.35, rx=0.045 * s))

    else:  # dusk
        # Setting sun, low — the far skyline will partly occlude it
        sx = rng.uniform(0.2, 0.8) * W
        sy = H * rng.uniform(0.62, 0.72)
        sr = rng.uniform(52, 76)
        out.append('<defs><radialGradient id="dhalo"><stop offset="0%" '
                   f'stop-color="{pal["sun_halo"]}" stop-opacity="0.5"/>'
                   '<stop offset="100%" stop-color="#000000" stop-opacity="0"/>'
                   '</radialGradient></defs>')
        out.append(_circle(sx, sy, sr * 3.0, "url(#dhalo)"))
        out.append(_circle(sx, sy, sr, pal["sun"]))
        opts["sun_x"] = sx
        # A few early stars, high up only
        for _ in range(int(W / 60)):
            stx, sty = rng.uniform(0, W), rng.uniform(0, 0.22) * H
            out.append(_circle(stx, sty, rng.uniform(0.6, 1.1), pal["star"],
                               round(rng.uniform(0.15, 0.5), 2)))
        # Dramatic streak clouds: layered horizontal wisps with a warm
        # under-edge tucked beneath only the widest lobe.
        for _ in range(3 + int(W / 1000)):
            cx = rng.uniform(0, W)
            cy = rng.uniform(0.12, 0.55) * H
            crx = rng.uniform(260, 620)
            cry = crx * rng.uniform(0.028, 0.05)
            for _li in range(rng.randint(3, 4)):
                lx = cx + rng.uniform(-0.7, 0.7) * crx
                ly = cy + rng.uniform(-0.9, 0.9) * cry
                lrx = crx * rng.uniform(0.45, 0.85)
                lry = cry * rng.uniform(0.55, 1.0)
                out.append(f'<ellipse cx="{_n(lx)}" cy="{_n(ly)}" rx="{_n(lrx)}" '
                           f'ry="{_n(lry)}" fill="{pal["cloud"]}" opacity="0.82"/>')
            out.append(f'<ellipse cx="{_n(cx)}" cy="{_n(cy + cry * 1.05)}" '
                       f'rx="{_n(crx * 0.55)}" ry="{_n(max(1.4, cry * 0.16))}" '
                       f'fill="{pal["cloud_edge"]}" opacity="0.7"/>')
    return out


# ---------------------------------------------------------------------------
# Building details
# ---------------------------------------------------------------------------


def _windows(rng, out, x, y, w, h, H, pal, pitch=(15, 19), size=(7.5, 9.5)):
    """Window grid.  Night/dusk: emit only lit windows; day: emit all unlit."""
    px, py = pitch
    ww, wh = size
    mx = max(5.0, (w % px) / 2 + 5)
    cols = int((w - 2 * mx + (px - ww)) // px)
    if cols < 1:
        return
    gx = x + (w - (cols * px - (px - ww))) / 2
    rows = int((min(y + h, H) - y - 8) // py)
    lit_p = pal["lit_p"]
    for r in range(rows):
        wy = y + 8 + r * py
        for c in range(cols):
            wx = gx + c * px
            if pal["unlit"]:
                out.append(_rect(wx, wy, ww, wh, pal["unlit"]))
            elif rng.random() < lit_p:
                col = pal["tv"] if (pal["tv"] and rng.random() < 0.018) \
                    else rng.choice(pal["lit"])
                out.append(_rect(wx, wy, ww, wh, col))


def _antenna(rng, out, x, y, pal, night):
    ah = rng.uniform(26, 70)
    out.append(_line(x, y, x, y - ah, pal["detail"], 2.2))
    for f in (0.35, 0.6):
        if rng.random() < 0.7:
            bw = 7 * (1 - f) + 3
            out.append(_line(x - bw, y - ah * f, x + bw, y - ah * f,
                             pal["detail"], 1.6))
    if night:
        out.append(_circle(x, y - ah, 4.5, pal["beacon"], 0.25))
    out.append(_circle(x, y - ah, 2.0, pal["beacon"]))


def _water_tower(rng, out, x, y, pal):
    """NYC rooftop water tower: legs, barrel, conical cap, finial."""
    s = rng.uniform(0.8, 1.15)
    bw, bh, lh = 30 * s, 26 * s, 14 * s
    f = pal["detail"]
    out.append(_line(x - bw * 0.32, y, x - bw * 0.42, y - lh, f, 2.4))
    out.append(_line(x + bw * 0.32, y, x + bw * 0.42, y - lh, f, 2.4))
    out.append(_line(x - bw * 0.36, y - lh * 0.4, x + bw * 0.36, y - lh * 0.4, f, 1.6))
    out.append(_rect(x - bw / 2, y - lh - bh, bw, bh, f))
    out.append(_poly([(x - bw * 0.56, y - lh - bh), (x + bw * 0.56, y - lh - bh),
                      (x, y - lh - bh - 13 * s)], f))
    out.append(_line(x, y - lh - bh - 13 * s, x, y - lh - bh - 19 * s, f, 1.6))


def _fire_escape(rng, out, x, y, w, h, H, pal):
    """Zigzag fire escape over the facade (silhouettes against lit windows)."""
    span = min(w * 0.46, 52.0)
    ex = x + w / 2 - span / 2 + rng.uniform(-w * 0.14, w * 0.14)
    col = pal["escape"]
    floor = 34.0
    bottom = min(y + h, H) - 12
    n = int((bottom - (y + 14)) // floor)
    if n < 2:
        return
    left = True
    for i in range(n):
        fy = y + 16 + i * floor
        out.append(_line(ex - 2, fy, ex + span + 2, fy, col, 2.4))        # platform
        out.append(_line(ex - 2, fy, ex - 2, fy - 7, col, 1.4))           # railing
        out.append(_line(ex + span + 2, fy, ex + span + 2, fy - 7, col, 1.4))
        out.append(_line(ex + span * 0.5, fy, ex + span * 0.5, fy - 7, col, 1.2))
        if i < n - 1:                                                     # stair
            x1, x2 = (ex + 3, ex + span - 3) if left else (ex + span - 3, ex + 3)
            out.append(_line(x1, fy, x2, fy + floor, col, 2.2))
            left = not left
    out.append(_line(ex + span * 0.5, y + 16 + (n - 1) * floor,
                     ex + span * 0.5, bottom, col, 1.8))                   # drop ladder


# ---------------------------------------------------------------------------
# Building archetypes (each draws from roofline y down past the bottom edge).
#
# Each returns (peak, decks) describing the REAL roof surfaces (not the
# bounding box), so decorations and figures stand on actual geometry:
#   peak  — (x0, x1, y) of the topmost flat surface (antenna spot), or None
#   decks — list of (x0, x1, y) flat regions (water towers / AC / figures):
#           full-width rooftops, or the setback ledges flanking a crown.
# ---------------------------------------------------------------------------


def _bldg_slab(rng, out, x, w, top, H, pal, fill, windows, night):
    out.append(_rect(x, top, w, H - top + 2, fill))
    out.append(_rect(x - 1.5, top, w + 3, 3.5, fill))  # parapet lip
    if windows:
        _windows(rng, out, x, top + 4, w, H - top, H, pal)
    return (x + w * 0.12, x + w * 0.88, top), [(x + w * 0.06, x + w * 0.94, top)]


def _bldg_stepped(rng, out, x, w, top, H, pal, fill, windows, night):
    """Art-deco setbacks: stacked narrowing tiers + crown."""
    tiers = rng.randint(2, 3)
    tw, ty = w, min(top + (H - top) * rng.uniform(0.28, 0.42), H - 60)
    base_roof = ty
    out.append(_rect(x, ty, w, H - ty + 2, fill))
    if windows:
        _windows(rng, out, x, ty + 4, w, H - ty, H, pal)
    cx = x + w / 2
    tw1 = None
    for t in range(tiers):
        tw *= rng.uniform(0.6, 0.74)
        if tw1 is None:
            tw1 = tw  # widest (lowest) tier — bounds the base-roof ledges
        nty = top + (ty - top) * (tiers - 1 - t) / tiers
        out.append(_rect(cx - tw / 2, nty, tw, ty - nty + 4, fill))
        if windows and tw > 40:
            _windows(rng, out, cx - tw / 2, nty + 4, tw, ty - nty, H, pal,
                     pitch=(13, 17), size=(6, 8))
        ty = nty
    out.append(_rect(cx - tw * 0.22, top - 7, tw * 0.44, 9, fill))  # crown block
    out.append(_line(cx, top - 7, cx, top - 7 - rng.uniform(16, 34), fill, 2.4))
    # Antennas only on the crown block; figures on the setback ledges
    peak = (cx - tw * 0.18, cx + tw * 0.18, top - 7)
    decks = [(x + 4, cx - tw1 / 2 - 4, base_roof),
             (cx + tw1 / 2 + 4, x + w - 4, base_roof)]
    return peak, decks


def _bldg_glass(rng, out, x, w, top, H, pal, fill, windows, night):
    """Modern tower: full-height window strips."""
    out.append(_rect(x, top, w, H - top + 2, fill))
    strip_fill = pal["glass_strip"]
    n = max(2, int(w // 16))
    sw = w / n * 0.52
    for i in range(n):
        sx = x + w * (i + 0.24) / n
        if strip_fill:  # day: reflective strips
            out.append(_rect(sx, top + 6, sw, H - top - 8, strip_fill, 0.8))
        elif rng.random() < 0.6:  # night/dusk: some strips fully lit, floorwise
            fy = top + 6
            while fy < H - 10:
                fh = 13
                if rng.random() < pal["lit_p"] + 0.1:
                    out.append(_rect(sx, fy, sw, fh - 3.5, rng.choice(pal["lit"])))
                fy += fh
    out.append(_rect(x - 1.5, top, w + 3, 3, fill))
    return (x + w * 0.15, x + w * 0.85, top), [(x + w * 0.08, x + w * 0.92, top)]


def _bldg_residential(rng, out, x, w, top, H, pal, fill, windows, night):
    """NYC walk-up: cornice, regular windows, fire escape, rooftop clutter."""
    out.append(_rect(x, top, w, H - top + 2, fill))
    out.append(_rect(x - 3, top - 5, w + 6, 7, fill))          # cornice
    out.append(_rect(x - 1.5, top - 8, w + 3, 3, fill))        # cornice cap
    if windows:
        _windows(rng, out, x, top + 10, w, H - top, H, pal,
                 pitch=(19, 26), size=(9.5, 13))
    if rng.random() < 0.8:
        _fire_escape(rng, out, x, top + 2, w, H - top, H, pal)
    if rng.random() < 0.3:                                      # chimney
        chx = x + rng.uniform(0.15, 0.85) * w
        out.append(_rect(chx, top - 16, 8, 16, fill))
    roof = top - 8
    return (x + w * 0.1, x + w * 0.9, roof), [(x + 3, x + w - 3, roof)]


def _bldg_spire(rng, out, x, w, top, H, pal, fill, windows, night):
    """Landmark tower with tapered crown and needle."""
    body_top = top + 30
    out.append(_rect(x, body_top, w, H - body_top + 2, fill))
    if windows:
        _windows(rng, out, x, body_top + 4, w, H - body_top, H, pal,
                 pitch=(13, 17), size=(6, 8))
    cx = x + w / 2
    cw = w * 0.72
    for i in range(4):  # tapering crown tiers
        cw *= 0.72
        out.append(_rect(cx - cw / 2, top + 30 - (i + 1) * 9, cw, 10, fill))
    out.append(_poly([(cx - cw * 0.4, top - 6), (cx + cw * 0.4, top - 6),
                      (cx, top - 22)], fill))
    out.append(_line(cx, top - 20, cx, top - 46, fill, 2.2))
    # The needle IS the antenna — no peak spot; figures use the ledges
    # flanking the crown on the body roof (widest crown tier ≈ 0.52 w).
    decks = [(x + 3, cx - w * 0.27, body_top),
             (cx + w * 0.27, x + w - 3, body_top)]
    return None, decks


_ARCHETYPES = [
    (_bldg_slab, 0.28, (70, 150)),
    (_bldg_stepped, 0.20, (90, 170)),
    (_bldg_glass, 0.15, (60, 120)),
    (_bldg_residential, 0.27, (150, 260)),
    (_bldg_spire, 0.10, (80, 130)),
]


# ---------------------------------------------------------------------------
# Skyline layers
# ---------------------------------------------------------------------------


def _layer(rng, W, H, pal, height_at, scale, fill, windows, density, night, detail):
    """Generate one depth layer of buildings; returns svg + rooftop list."""
    out = []
    rooftops = []  # (x_center, roof_y, width) candidates for figures
    x = -rng.uniform(20, 120)
    while x < W + 40:
        r, acc = rng.random(), 0.0
        func, wrange = _ARCHETYPES[0][0], _ARCHETYPES[0][2]
        for f, wgt, wr in _ARCHETYPES:
            acc += wgt
            if r <= acc:
                func, wrange = f, wr
                break
        w = rng.uniform(*wrange)
        hmax = H * (0.20 + 0.68 * height_at(x + w / 2)) * scale
        if func is _bldg_residential:
            hmax = min(hmax, H * 0.42 * scale + 40)
        elif detail and rng.random() < 0.06:
            hmax *= 1.22    # occasional supertall for skyline drama
        top = H - max(55.0, hmax * rng.uniform(0.82, 1.05))
        peak, decks = func(rng, out, x, w, top, H, pal, fill, windows, night)
        decks = [d for d in decks if d[1] - d[0] >= 16]
        if detail:
            if peak and rng.random() < 0.4:
                _antenna(rng, out, rng.uniform(peak[0], peak[1]), peak[2],
                         pal, night)
            wide = [d for d in decks if d[1] - d[0] >= 46]
            if func is _bldg_residential and wide and rng.random() < 0.55:
                d = rng.choice(wide)
                _water_tower(rng, out, rng.uniform(d[0] + 21, d[1] - 21),
                             d[2] - 1, pal)
            if decks and rng.random() < 0.35:  # AC units / roof boxes
                for _ in range(rng.randint(1, 3)):
                    d = rng.choice(decks)
                    out.append(_rect(rng.uniform(d[0], d[1] - 11),
                                     d[2] - rng.uniform(5, 9), 11, 9, fill))
            rooftops += [d for d in decks if d[1] - d[0] >= 28]
        if rng.random() > density:
            x += w + rng.uniform(30, 140)   # skyline gap
        else:
            x += w + rng.uniform(-6, 14)    # packed streetwall
    return out, rooftops


def _place_figures(rng, rooftops, pal):
    """Rooftop silhouettes: guarantee at least one, keep them occasional."""
    out = []
    if not rooftops:
        return out
    placed = 0
    want = 1 + (1 if rng.random() < 0.5 else 0) + (1 if rng.random() < 0.2 else 0)
    candidates = rng.sample(rooftops, min(len(rooftops), want * 3))
    for x0, x1, roof_y in candidates:
        if placed >= want:
            break
        kind, tpl, rel = _FIGURES[min(int(rng.random() * 3), 2)]
        h = 22 * rel * rng.uniform(0.9, 1.15)
        fx = rng.uniform(x0 + 9, x1 - 9)
        out.append(_figure_svg(tpl, fx, roof_y - 1, h, pal["figure"],
                               flip=rng.random() < 0.5))
        placed += 1
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate(seed: str, aspect: float, params: dict, out_path: Path) -> None:
    H = int(params.get("height", 800))
    W = max(1, round(H * aspect))
    tod = str(params.get("time_of_day", "night")).lower()
    if tod not in PALETTES:
        raise ValueError(f"city-skyline: unknown time_of_day {tod!r} "
                         f"(expected one of {sorted(PALETTES)})")
    pal = PALETTES[tod]
    density = float(params.get("density", 0.92))

    rng = random.Random(f"city-skyline:{seed}")
    night = tod == "night"
    opts = {
        "moon": bool(params.get("moon", True)) and night,
        "ufo": bool(params.get("ufo", rng.random() < 0.33)) and night,
        "glow": night,
    }

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'width="{W}" height="{H}">']
    svg += _sky(rng, W, H, tod, pal, opts)

    env = _Envelope(rng, W)
    sun_x = opts.get("sun_x")  # dusk: carve a skyline valley so the sunset shows

    def height_at(x: float) -> float:
        e = env.at(x) ** 1.25
        if sun_x is not None:
            e *= 1.0 - 0.62 * math.exp(-((x - sun_x) / 240.0) ** 2)
        return e

    far, _ = _layer(random.Random(f"far:{seed}"), W, H, pal, height_at, 0.52,
                    pal["far"], False, min(0.98, density + 0.05), night, False)
    svg += far
    # Sparse lit specks on the far layer at night/dusk
    if pal["far_dot"]:
        drng = random.Random(f"fardots:{seed}")
        for _ in range(int(W / 14)):
            dx, dy = drng.uniform(0, W), H - drng.uniform(20, H * 0.30)
            svg.append(_rect(dx, dy, 2.4, 3.2, pal["far_dot"],
                             round(drng.uniform(0.3, 0.8), 2)))
    mid, _ = _layer(random.Random(f"mid:{seed}"), W, H, pal, height_at, 0.74,
                    pal["mid"], True, density, night, False)
    svg += mid
    fg, rooftops = _layer(random.Random(f"fg:{seed}"), W, H, pal, height_at, 1.0,
                          pal["fg"], True, density, night, True)
    svg += fg
    svg += _place_figures(random.Random(f"fig:{seed}"), rooftops, pal)
    svg.append("</svg>")

    out_path.write_text("".join(svg), encoding="utf-8")
