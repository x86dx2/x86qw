#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const assets = path.join(root, "dist/installer/assets");
const requireFromSite = createRequire(path.join(root, "site/package.json"));
const sharp = requireFromSite("sharp");
const svg = await fs.readFile(path.join(assets, "x86qw.svg"));
const sizes = [16, 24, 32, 48, 64, 128, 256, 512];
const pngs = new Map();

for (const size of sizes) {
  const png = await sharp(svg, { density: 384 })
    .resize(size, size, { fit: "fill", kernel: sharp.kernel.lanczos3 })
    .png({ compressionLevel: 9, adaptiveFiltering: false, palette: false })
    .toBuffer();
  pngs.set(size, png);
  if ([16, 32, 48, 64, 128, 256, 512].includes(size)) {
    await fs.writeFile(path.join(assets, `x86qw-${size}.png`), png);
  }
}

const icoSizes = [16, 24, 32, 48, 64, 128, 256];
const icoHeader = Buffer.alloc(6 + icoSizes.length * 16);
icoHeader.writeUInt16LE(0, 0);
icoHeader.writeUInt16LE(1, 2);
icoHeader.writeUInt16LE(icoSizes.length, 4);
let icoOffset = icoHeader.length;
for (const [index, size] of icoSizes.entries()) {
  const entry = 6 + index * 16;
  const png = pngs.get(size);
  icoHeader.writeUInt8(size === 256 ? 0 : size, entry);
  icoHeader.writeUInt8(size === 256 ? 0 : size, entry + 1);
  icoHeader.writeUInt8(0, entry + 2);
  icoHeader.writeUInt8(0, entry + 3);
  icoHeader.writeUInt16LE(1, entry + 4);
  icoHeader.writeUInt16LE(32, entry + 6);
  icoHeader.writeUInt32LE(png.length, entry + 8);
  icoHeader.writeUInt32LE(icoOffset, entry + 12);
  icoOffset += png.length;
}
await fs.writeFile(
  path.join(assets, "x86qw.ico"),
  Buffer.concat([icoHeader, ...icoSizes.map((size) => pngs.get(size))]),
);

const icnsTypes = new Map([
  [16, "icp4"],
  [32, "icp5"],
  [64, "icp6"],
  [128, "ic07"],
  [256, "ic08"],
  [512, "ic09"],
]);
const icnsChunks = [];
for (const [size, type] of icnsTypes) {
  const png = pngs.get(size);
  const header = Buffer.alloc(8);
  header.write(type, 0, 4, "ascii");
  header.writeUInt32BE(8 + png.length, 4);
  icnsChunks.push(header, png);
}
const icnsLength = 8 + icnsChunks.reduce((total, chunk) => total + chunk.length, 0);
const icnsHeader = Buffer.alloc(8);
icnsHeader.write("icns", 0, 4, "ascii");
icnsHeader.writeUInt32BE(icnsLength, 4);
await fs.writeFile(
  path.join(assets, "x86qw.icns"),
  Buffer.concat([icnsHeader, ...icnsChunks]),
);
