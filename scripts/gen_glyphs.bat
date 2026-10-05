@echo off
:: Generate per-character flap SVGs for each font preset.
:: Each line is independent -- copy, comment out, or add lines as needed.
:: Output: assets/flap_glyphs/<font>/flap_NN.svg  (auto-derived from --font)
:: Fonts available: see 3d/flap_fonts.scad in the splitflap repo.
:: Requires OpenSCAD and a scottbez1/splitflap checkout next to this repo
:: (or pass --splitflap-root PATH).

cd /d "%~dp0\.."
call .venv\Scripts\activate.bat

rem python scripts\generate_epilogue_flap_svgs.py --font Epilogue
python scripts\generate_epilogue_flap_svgs.py --font Roboto
