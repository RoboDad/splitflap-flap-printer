# splitflap-flap-printer — Claude Code Project Context

## What this repo is

A standalone Python tool (plus OpenSCAD jig) that generates print-ready
images for UV printing custom flaps for scottbez1/splitflap displays on a
eufyMake E1 flatbed printer.  It is NOT a fork of splitflap; it only
consumes the standard flap geometry.

## Repo layout

| Path | Purpose |
|---|---|
| `flap_printer/` | The Python package (`python -m flap_printer`) |
| `cad/` | `flap_printer_jigs.scad` (laser-cut jig), auto-generated `flap_printer_params.scad`, vendored `vendor/flap_profile.scad` |
| `assets/` | Committed inputs: `flap_glyphs/<Font>/`, `emoji/`, `photos/`; `local/` is gitignored for your own images |
| `generators/` | Vendored external generators (`shan-shui-inf`, Node.js) |
| `examples/` | Job JSON files + `test_images/`; render output lands beside the job file |
| `scripts/` | Asset generators (`download_emoji.py`, `generate_epilogue_flap_svgs.py`) and runner scripts |
| `scad/` | OpenSCAD wrapper used only by the glyph generator (needs a splitflap checkout) |
| `docs/` | Images used by the README |

## Python environment

Run everything from the repo root.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m flap_printer examples/example_basic_job.json --dry-run
python -m flap_printer examples/example_job.json
python -m flap_printer examples/example_job.json --jig standard
```

## Orientation convention (critical rule)

There is exactly ONE shared frame — **printer orientation** — used by the
OpenSCAD jig model (`cad/flap_printer_jigs.scad`), the generated params
file, the job JSON `jig` section, and the output print images:

- The jig/mat long axis runs along **X** (370 mm), matching how the jig
  physically sits on the eufyMake E1 flatbed, viewed from above.
- The mat zero-point corner (diagonal corner cut) is at the **bottom-right**
  (= lower-right of the imported image in eufyMake Studio Zero Point mode;
  the green registration mark).
- Flap pockets sit **rotated 90°** in the jig (single row along X, spool/notch
  edge facing right, toward the zero point), so a pocket's X extent is
  `flap_height` (43) and its Y extent is `flap_width` (54).

Flap *content* (images, slicing, preview) is composed in the **upright frame**
(character upright).  The only transform between frames is a single 90° CCW
rotation (`Image.ROTATE_90`) applied per-flap at batch-layout time in
`layout.py`: upright top edge → sheet left, upright spool edge → sheet right.
The terms "portrait"/"landscape" are obsolete — do not reintroduce them.

## Flip geometry (critical rule)

A **front-back** flip is geometrically equivalent to a **left-right** flip
**+ in-plane 180° rotation**:

```
reflect_x == rotate180 ∘ reflect_y
```

In `renderer.py`, the back sheet is *always* built with left-right semantics.
When `flip == "front-back"`, the finished back image is `Image.ROTATE_180`
**before** registration marks and calibration offset are applied (those stay
in sheet space).

- Front sheet: `spool_at_bottom=True` (upright frame; spool edge faces right on the sheet)
- Left-right back: `spool_at_bottom=False` (spool edge faces left on the sheet)
- Front-back back: left-right back rotated 180° (spool ends up on the correct side)

`spool_at_bottom` always refers to the upright content frame, not the sheet.

Ink-save mask notches are drawn symmetrically on both sides — if the notch
looks wrong on a back sheet, the real cause is spool orientation, not the
mask geometry.

## README update rule

Any change that adds or modifies a **user-facing feature** MUST include a
`README.md` update in the same commit:

- New config key → add row to the relevant table
- New flap type → add bullet under **Custom Flap Types**
- New mode constant → add a dedicated `## X Modes` section
- Pipeline change → update the numbered list under **Image Pipeline**
- New CLI option → update the usage block

## Verification discipline

Rendering changes are verified by byte-comparing outputs before/after
(`git stash` → render → unstash → render → compare) and by inspecting
`preview.png`.  Never claim a render is unchanged without the comparison.

## Privacy

This repo is public.  Never commit personal photos or absolute paths that
include a user name; personal images go in `assets/local/` (gitignored) or a
separate private repo.
