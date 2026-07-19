#!/usr/bin/env python3
"""
DF-style world map renderer.

Renders a world JSON (from world_gen.py) using a bitmap font PNG.
Multi-pass rendering:
  1. Terrain pass — each biome cell tinted by biome color
  2. Elevation pass — overlays elevation shading (higher = darker)
  3. Site pass — marks cities/settlements with special glyphs

Usage:
  python3 map_renderer.py --world /tmp/world.json --font /tmp/default_font.png --output /tmp/map.png
"""

import json
import sys
import os
import struct
import zlib
from typing import Dict, List, Tuple, Optional

# Import bitmap font utilities
from bitmap_font import (
    GLYPH_SIZE, FONT_GRID_COLS, FONT_GRID_ROWS, FONT_IMAGE_SIZE,
    TERRAIN_GLYPH_MAP, FALLBACK_GLYPH, BIOME_COLORS,
    load_font, get_glyph_pixels, tint_glyph, blend_pixels, write_png
)


# ──────────────────────────────────────────────────────────────────────
# 1.  SITE GLYPHS
# ──────────────────────────────────────────────────────────────────────

SITE_GLYPH_MAP = {
    "city":          0x10,   # city marker  (row 1, col 0)
    "town":          0x11,   # town marker  (row 1, col 1)
    "fortress":      0x12,   # fortress     (row 1, col 2)
    "shrine":        0x13,   # shrine       (row 1, col 3)
    "village":       0x14,   # village      (row 1, col 4)
    "tower":         0x15,   # tower        (row 1, col 5)
    "castle":        0x16,   # castle       (row 1, col 6)
    "ruin":          0x17,   # ruin         (row 1, col 7)
}

SITE_COLORS = {
    "city":     (0xFF, 0xFF, 0xFF),   # white
    "town":     (0xD0, 0xD0, 0xD0),   # light gray
    "fortress": (0xC0, 0x80, 0x40),   # brown
    "shrine":   (0xFF, 0xD0, 0x80),   # gold
    "village":  (0xA0, 0xA0, 0xA0),   # gray
    "tower":    (0x80, 0x80, 0xC0),   # blue-gray
    "castle":   (0xC0, 0xC0, 0x80),   # tan
    "ruin":     (0x60, 0x50, 0x40),   # dark brown
}


# ──────────────────────────────────────────────────────────────────────
# 2.  RENDERER
# ──────────────────────────────────────────────────────────────────────

def render_world_map(
    world: Dict,
    font_pixels: bytes,
    output_path: str,
    has_color: bool = False,
) -> None:
    """Render the world biome grid with multi-pass layering."""

    terrain = world.get("terrain", {})
    biome_grid = terrain.get("biome_grid", [])
    elevation_grid = terrain.get("elevation_grid", [])
    biome_defs = {b["id"]: b for b in world.get("biomes", [])}

    if not biome_grid:
        print("ERROR: No biome_grid in world data")
        return

    height = len(biome_grid)
    width = len(biome_grid[0]) if height > 0 else 0

    if width == 0 or height == 0:
        print("ERROR: Empty biome grid")
        return

    print(f"Rendering map: {width}x{height} tiles")
    print(f"  Each tile: {GLYPH_SIZE}x{GLYPH_SIZE} px")
    print(f"  Output size: {width * GLYPH_SIZE}x{height * GLYPH_SIZE} px")

    # Pre-extract glyphs from font
    glyph_cache = {}  # glyph_idx -> bytes
    def get_glyph(idx: int) -> bytes:
        if idx not in glyph_cache:
            glyph_cache[idx] = get_glyph_pixels(font_pixels, idx)
        return glyph_cache[idx]

    # Normalize elevation to 0..1 range for shading
    flat_elev = []
    for row in elevation_grid:
        flat_elev.append([max(0.0, min(1.0, v)) for v in row])

    # ── Pass 1: Terrain ──
    print("  Pass 1: Terrain...")
    output_w = width * GLYPH_SIZE
    output_h = height * GLYPH_SIZE
    canvas = bytearray(output_w * output_h * 4)

    for y in range(height):
        for x in range(width):
            biome_id = biome_grid[y][x]
            biome = biome_defs.get(biome_id, {})
            biome_name = biome.get("name", "Grassland")

            glyph_idx = TERRAIN_GLYPH_MAP.get(biome_name, FALLBACK_GLYPH)
            color = BIOME_COLORS.get(biome_name, (0x7c, 0x9c, 0x5e))

            glyph = get_glyph(glyph_idx)
            if has_color:
                # Pre-colored font — use glyph pixels as-is
                tile_pixels = glyph
            else:
                tile_pixels = tint_glyph(glyph, color)

            # Place tile on canvas
            dst_y = y * GLYPH_SIZE
            dst_x = x * GLYPH_SIZE
            for py in range(GLYPH_SIZE):
                src_off = py * GLYPH_SIZE * 4
                dst_off = (dst_y + py) * output_w * 4 + dst_x * 4
                canvas[dst_off:dst_off + GLYPH_SIZE * 4] = tile_pixels[src_off:src_off + GLYPH_SIZE * 4]

    # ── Pass 2: Elevation overlay ──
    print("  Pass 2: Elevation shading...")
    for y in range(height):
        for x in range(width):
            elev = flat_elev[y][x] if y < len(flat_elev) and x < len(flat_elev[y]) else 0.5

            # Darken based on elevation: higher = darker
            # Elevation 0.0 = no darkening, 1.0 = heavy darkening
            dark = int(elev * 60)  # 0..60 extra darkness
            overlay = bytearray(GLYPH_SIZE * GLYPH_SIZE * 4)

            dst_y = y * GLYPH_SIZE
            dst_x = x * GLYPH_SIZE

            for py in range(GLYPH_SIZE):
                for px in range(GLYPH_SIZE):
                    ci = (dst_y + py) * output_w * 4 + (dst_x + px) * 4
                    # Read current pixel
                    r, g, b, a = canvas[ci:ci+4]
                    # Apply elevation darkening
                    r2 = max(0, r - dark)
                    g2 = max(0, g - dark)
                    b2 = max(0, b - dark)
                    canvas[ci:ci+4] = (r2, g2, b2, a)

    # ── Pass 3: Site markers ──
    print("  Pass 3: Sites...")
    sites = world.get("sites", [])
    for site in sites:
        sx = site.get("x")
        sy = site.get("y")
        site_type = site.get("site_type", "village")
        if sx is None or sy is None:
            continue
        if sx < 0 or sx >= width or sy < 0 or sy >= height:
            continue

        glyph_idx = SITE_GLYPH_MAP.get(site_type, 0x09)
        color = SITE_COLORS.get(site_type, (0xA0, 0xA0, 0xA0))

        glyph = get_glyph(glyph_idx)
        if has_color:
            # Pre-colored font — use glyph pixels as-is
            tinted = glyph
        else:
            tinted = tint_glyph(glyph, color)

        # Blend onto canvas at site position
        dst_y = sy * GLYPH_SIZE
        dst_x = sx * GLYPH_SIZE

        for py in range(GLYPH_SIZE):
            for px in range(GLYPH_SIZE):
                src_off = py * GLYPH_SIZE * 4 + px * 4
                dst_off = (dst_y + py) * output_w * 4 + (dst_x + px) * 4

                # Get overlay pixel
                or_, og, ob, oa = tinted[src_off:src_off+4]
                br, bg, bb, ba = canvas[dst_off:dst_off+4]

                if oa == 0:
                    continue  # fully transparent — skip
                if ba == 0:
                    canvas[dst_off:dst_off+4] = tinted[src_off:src_off+4]
                    continue

                # Alpha blend
                oa_norm = oa / 255.0
                ba_norm = ba / 255.0
                out_a = oa_norm + ba_norm * (1 - oa_norm)
                if out_a > 0:
                    r = int((or_ * oa_norm + br * ba_norm * (1 - oa_norm)) / out_a) & 0xFF
                    g = int((og * oa_norm + bg * ba_norm * (1 - oa_norm)) / out_a) & 0xFF
                    b = int((ob * oa_norm + bb * ba_norm * (1 - oa_norm)) / out_a) & 0xFF
                    a = int(out_a * 255) & 0xFF
                    canvas[dst_off:dst_off+4] = (r, g, b, a)

    # ── Write output ──
    print(f"  Writing {output_w}x{output_h} PNG...")
    write_png(output_path, output_w, output_h, bytes(canvas))
    print(f"  Done → {output_path}")


# ──────────────────────────────────────────────────────────────────────
# 3.  COMMAND LINE
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Render DF-style world map from JSON + bitmap font")
    parser.add_argument("--world", type=str, required=True, help="Path to world JSON")
    parser.add_argument("--font", type=str, default="/tmp/default_font.png", help="Path to bitmap font PNG")
    parser.add_argument("--output", type=str, default="/tmp/world_map.png", help="Output PNG path")
    parser.add_argument("--generate-font", action="store_true", help="Generate default font if missing")
    parser.add_argument("--has-color", action="store_true",
                        help="Font is pre-colored — skip tint pass, use raw glyph colors")
    args = parser.parse_args()

    # Load font
    if not os.path.exists(args.font):
        if args.generate_font:
            print("Generating default font...")
            from bitmap_font import generate_default_font_file
            generate_default_font_file(args.font)
        else:
            print(f"ERROR: Font not found at {args.font}")
            print("Use --generate-font to create one, or provide an existing font")
            sys.exit(1)

    print(f"Loading font from {args.font}...")
    font_pixels, fw, fh = load_font(args.font, has_color=args.has_color)
    print(f"  Font: {fw}x{fh} px")

    # Load world
    print(f"Loading world from {args.world}...")
    with open(args.world) as f:
        world = json.load(f)
    print(f"  World: {world.get('world_name', '?')} (seed {world.get('seed', '?')})")

    # Render
    render_world_map(world, font_pixels, args.output, has_color=args.has_color)
