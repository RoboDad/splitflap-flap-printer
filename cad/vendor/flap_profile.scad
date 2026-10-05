/*
   Vendored geometry helpers from scottbez1/splitflap (3d/flap.scad,
   3d/shapes.scad, 3d/global_constants.scad), trimmed to what the flap
   printer jig needs.  Unmodified except for this header and the removal of
   unrelated code.

   Copyright 2015-2021 Scott Bezek and the splitflap contributors

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

   This file is meant to be include<>d (not use<>d) AFTER
   flap_printer_params.scad, so flap_2d() sees the flap_* dimension
   variables written there.
*/

// "Epsilon" - a small error tolerance value, used when designing 3d parts to avoid exact complanar faces that cause OpenSCAD rendering artifacts
// See https://3dprinting.stackexchange.com/a/9795
eps=0.01;

// 2D flap outline, drawn upright: flap_width along X, spool/notch edge at
// the bottom (y = -flap_pin_width/2), rounded corners at the top.
module flap_2d(cut_tabs = true) {
    translate([0, -flap_pin_width/2, 0])
    difference() {
        union() {
            square([flap_width, flap_height - flap_corner_radius]);

            // rounded corners
            hull() {
                translate([flap_corner_radius, flap_height - flap_corner_radius])
                    circle(r=flap_corner_radius, $fn=40);
                translate([flap_width - flap_corner_radius, flap_height - flap_corner_radius])
                    circle(r=flap_corner_radius, $fn=40);
            }
        }
        // spool tabs
        if(cut_tabs) {
            translate([-eps, flap_pin_width])
                square([eps + flap_notch_depth, flap_notch_height]);
            translate([flap_width - flap_notch_depth, flap_pin_width])
                square([eps + flap_notch_depth, flap_notch_height]);
        }
    }
}

module rounded_square(size, center=false, r=0.0, corners=[0,1,2,3], $fn=$fn) {
    translation_matrix = [
        [-1, -1],
        [-1,  1],
        [ 1,  1],
        [ 1, -1],
    ];

    module add_corner(index, center, r) {
        // this is tiny because the geometry is 'hull'ed and we need to get out
        // of the way of the rounds at larger radii
        square_size = 0.001;

        function circle_offset(axis) =
            center[axis] * translation_matrix[index][axis];

        function square_offset(axis) =
            center[axis] * translation_matrix[index][axis] + r * translation_matrix[index][axis] + (translation_matrix[index][axis] >= 1 ? -square_size : 0);

        if(len(search(index, corners)) > 0)
            translate([circle_offset(0), circle_offset(1)])
                circle(r=r, $fn=$fn);
        else {
            translate([square_offset(0), square_offset(1)])
                square(square_size);
        }
    }

    width  = size[0] == undef ? size : size[0];  // unpack vector if present
    height = size[1] == undef ? size : size[1];

    if(r <= 0.0) {
        square([width, height], center=center);
    } else {
        radius = min(min(r, width/2), height/2);  // radius cannot be larger than rectangle
        center_x = center ? 0 : width/2;
        center_y = center ? 0 : height/2;

        translate([center_x, center_y]) {
            hull() {
                x =  width/2 - radius;
                y = height/2 - radius;
                for(i = [0:3])
                    add_corner(i, [x, y], radius);
            }
        }
    }
}
