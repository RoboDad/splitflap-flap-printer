/*
   Copyright 2026 Philip Van Valkenberg

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.

   Renders a single display face (one full character as seen on the
   display) into a 2D SVG via OpenSCAD's projection-renderer pipeline.

   Top half = front of flap[flap_index]
   Bottom half = back of flap[flap_index - 1], rotated 180 degrees and
                 translated below the top.

   No flap outlines are drawn; the SVG contains only the letter geometry,
   which is what the flap_printer pipeline rasterizes onto pre-cut flaps.

   Driven by scripts/generate_epilogue_flap_svgs.py via Scott's
   3d/scripts/projection_renderer.py.

   The 3d/... includes below resolve against a checkout of
   scottbez1/splitflap through OPENSCADPATH, which the driver script sets
   to --splitflap-root (default: a sibling "splitflap" directory).
*/

include<3d/flap_dimensions.scad>;
use<3d/flap.scad>;
use<3d/flap_characters.scad>;
use<3d/flap_fonts.scad>;
use<3d/projection_renderer.scad>;
use<3d/splitflap.scad>;

// ---- Parameters (overridden via openscad -D) ----
flap_index = 0;            // Which character index from character_list to render
bleed = 0;                 // Letter bleed in mm

// Projection-renderer plumbing (set by the renderer)
render_index = -1;
render_etch = false;
kerf_width = 0;
render_fill = false;

flap_gap = get_flap_gap();
letter_color = [0, 0, 0];
flap_color = [1, 1, 1];

// Echo physical dimensions so the Python driver can set the output SVG's
// viewBox to the exact display-face extents (54mm x 88.4mm typical).  The
// extract_values() helper in 3d/scripts/openscad.py parses these.
echo(epilogue_flap_width = flap_width);
echo(epilogue_flap_height = flap_height);
echo(epilogue_flap_gap = flap_gap);
echo(epilogue_flap_pin_width = flap_pin_width);

// The font_generator quirk: render_fill gate is checked via fill_text() in
// font_generator.scad; here we always want to render letter geometry, so we
// call flap_with_letters() directly with flap=false (no flap outline).

projection_renderer(render_index = render_index, render_etch = render_etch, kerf_width = kerf_width, panel_height = 0, panel_horizontal = 0, panel_vertical = 0) {
    // Top half: front letter of flap[flap_index]
    flap_with_letters(flap_color, letter_color, flap_index, flap_gap,
                      flap=false, front_letter=true, back_letter=false,
                      bleed=bleed, print_3d=false);

    // Bottom half: back letter of flap[flap_index - 1], positioned below
    // the top flap and rotated 180 degrees so the letter reads upright
    // (matches MODE_FULL_FONT layout in font_generator.scad).
    translate([0, -flap_pin_width - flap_gap, 0]) {
        rotate([180, 0, 0]) {
            flap_with_letters(flap_color, letter_color, flap_index - 1, flap_gap,
                              flap=false, front_letter=false, back_letter=true,
                              bleed=bleed, print_3d=false);
        }
    }
}
