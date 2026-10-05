// Headless Node driver for shan-shui-inf (vendored as shan-shui-inf.html).
//
// {Shan, Shui}* by Lingdong Huang (https://github.com/LingDong-/shan-shui-inf),
// MIT license — see LICENSE in this directory.  Vendored 2026-10-04 from the
// master branch.
//
// The original is a browser one-pager, but its generation core (Prng, Noise,
// PolyTools, Util, and the main generator block ending in chunkloader /
// chunkrender) is DOM-free; only the UI layer touches the document.  This
// driver evaluates just those core <script> blocks with minimal window /
// document shims, seeds the PRNG through the same ?seed= URL path the
// website uses, generates chunks for a fixed window starting at x = 0, and
// writes a standalone SVG.
//
// Usage:
//   node driver.js --seed <string> --width <units> --out <file.svg>
//        [--background <css-color>]
//
// The world height is fixed by the generator (MEM.windy = 800 units);
// choose --width = 800 * aspect for a target aspect ratio.  Same arguments
// => byte-identical output.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function arg(name, def) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : def;
}

const seed = arg("seed", "" + Date.now());
const width = Math.round(parseFloat(arg("width", "3400")));
const outFile = arg("out", "shan_shui.svg");
const background = arg("background", "");

// --- Minimal browser shims -------------------------------------------------
// elemStub absorbs any DOM-element use reachable from the core blocks
// (addEventListener registrations and similar no-ops).
const elemStub = {
  setAttribute() {},
  addEventListener() {},
  appendChild() {},
  removeChild() {},
  scrollTo() {},
  style: {},
  innerHTML: "",
};
global.window = {
  // Prng.hash uses window.btoa; Buffer's binary encoding matches btoa for
  // the JSON strings it receives.
  btoa: (s) => Buffer.from(String(s), "binary").toString("base64"),
  // The seed rides the same ?seed= parsing path the website uses.
  location: { href: "shanshui?seed=" + encodeURIComponent(seed) },
};
global.document = {
  addEventListener() {},
  getElementById: () => elemStub,
  getElementsByTagName: () => [elemStub],
  createElement: () => elemStub,
  body: elemStub,
};
global.self = global; // update() refers to self.*; harmless here
// Keep stdout clean for tooling; forward the generator's chatter to stderr.
console.log = (...a) => process.stderr.write(a.map(String).join(" ") + "\n");

// --- Evaluate the core script blocks --------------------------------------
const html = fs.readFileSync(path.join(__dirname, "shan-shui-inf.html"), "utf8");
const blocks = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(
  (m) => m[1]
);
// Blocks 0..5: PRNG, seed parsing, PerlinNoise, PolyTools, Util, and the
// main generator block (defines MEM, chunkloader, chunkrender).  Everything
// after that is UI and page init, which we do not evaluate.
vm.runInThisContext(blocks.slice(0, 6).join("\n;\n"), {
  filename: "shan-shui-inf.core.js",
});

// --- Generate --------------------------------------------------------------
chunkloader(0, width);
chunkrender(0, width);

const h = MEM.windy;
const svg =
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${h}" ` +
  `width="${width}" height="${h}">` +
  (background
    ? `<rect x="0" y="0" width="${width}" height="${h}" fill="${background}"/>`
    : "") +
  `<g>` +
  MEM.canv +
  `</g></svg>`;

fs.writeFileSync(outFile, svg);
process.stderr.write(
  `shan-shui-inf: seed=${seed} width=${width} -> ${outFile} (${svg.length} bytes)\n`
);
