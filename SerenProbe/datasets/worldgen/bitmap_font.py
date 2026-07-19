#!/usr/bin/env python3
"""
Bitmap Font Generator & Renderer for DF-style world maps.

Font format:
  - A PNG image with glyphs in a 16x16 grid (rows x columns)
  - Each glyph is 16x16 pixels → font image is 256x256 total
  - Grayscale or RGBA — white = full tint, black = black, alpha = transparency

Glyph-to-terrain mapping:
  - Each biome/feature type maps to an ASCII glyph index
  - The renderer tints the glyph by a base color using the grayscale as a mask
  - Multiple render passes layer terrain, then elevation, then sites on top
"""

import struct
import zlib
import json
import sys
import os
from typing import List, Tuple, Optional, Dict

# ──────────────────────────────────────────────────────────────────────
# 1.  BITMAP FONT FORMAT
# ──────────────────────────────────────────────────────────────────────

GLYPH_SIZE = 16          # pixels per glyph side
FONT_GRID_COLS = 16       # glyphs per row in font image
FONT_GRID_ROWS = 16       # rows of glyphs
FONT_IMAGE_SIZE = GLYPH_SIZE * FONT_GRID_COLS  # 256 px

# ── Default glyph-to-ASCII mapping for terrain ──
# These map biome/feature types to glyph indices (ASCII codes 1-255)
# We skip 0 (null) and use printable-ish codes

TERRAIN_GLYPH_MAP = {
    # Row 0 (0x00-0x0F):  Terrain biomes
    "Ocean":        0x00,  # smooth water
    "Deep Ocean":   0x01,  # deep water
    "Shallows":     0x02,  # shallow waves
    "Beach":        0x03,  # sand
    "Grassland":    0x04,  # light shade — open grass
    "Forest":       0x05,  # medium shade — trees
    "Dense Forest": 0x06,  # dark shade — deep woods
    "Taiga":        0x07,  # pine forest
    "Tundra":       0x08,  # ice
    "Desert":       0x09,  # sand dunes
    "Badlands":     0x0A,  # eroded rock
    "Savanna":      0x0B,  # dry grass
    "Swamp":        0x0C,  # murky water
    "Mountain":     0x0D,  # rocky peaks
    "High Mountain":0x0E,  # snow peaks
    "Volcanic":     0x0F,  # fire mountain
}

# Fallback glyph for unknown biomes
FALLBACK_GLYPH = 0x20  # space (row 2, col 0 — reserved for unknown)

# ── Glyph legend: what each glyph represents semantically ──
# Format:  "KEY" — description (key = TERRAIN_GLYPH_MAP biome name or BUILDING_TYPES type name)
GLYPH_LEGEND = {
    # ── Row 0 (0x00-0x0F): Terrain biomes ──
    0x00: '"Ocean" — Ocean water',
    0x01: '"Deep Ocean" — Deep ocean',
    0x02: '"Shallows" — Shallows / coast',
    0x03: '"Beach" — Beach / sand',
    0x04: '"Grassland" — Grassland',
    0x05: '"Forest" — Forest',
    0x06: '"Dense Forest" — Dense forest',
    0x07: '"Taiga" — Taiga / pine',
    0x08: '"Tundra" — Tundra / ice',
    0x09: '"Desert" — Desert / dunes',
    0x0A: '"Badlands" — Badlands / rock',
    0x0B: '"Savanna" — Savanna / dry grass',
    0x0C: '"Swamp" — Swamp / marsh',
    0x0D: '"Mountain" — Mountain',
    0x0E: '"High Mountain" — High mountain / snow',
    0x0F: '"Volcanic" — Volcanic / fire',

    # ── Row 1 (0x10-0x1F): Site markers + roads ──
    0x10: '"city" — City marker',
    0x11: '"town" — Town marker',
    0x12: '"fortress" — Fortress marker',
    0x13: '"shrine" — Shrine marker',
    0x14: '"village" — Village marker',
    0x15: '"tower" — Tower marker',
    0x16: '"castle" — Castle marker',
    0x17: '"ruin" — Ruin marker',
    0x18: '"road" — Road / path',
    # 0x19-0x1F: reserved

    # ── Row 2 (0x20-0x2F): Building exteriors ──
    0x20: '"wall_stone" — Stone wall / keep',
    0x21: '"wall_wood" — Wood wall',
    0x22: '"keep" — Keep / fortress core',
    0x23: '"tower" — Tower structure',
    0x24: '"house" — House / building',
    0x25: '"temple" — Temple / shrine',
    0x26: '"gate" — Gate / entrance',
    0x27: '"bridge" — Bridge',
    # 0x28-0x2F: reserved

    # ── Row 3 (0x30-0x3F): Building interiors — furniture ──
    0x30: '"door" — Door',
    0x31: '"chest" — Chest',
    0x32: '"barrel" — Barrel',
    0x33: '"bed" — Bed',
    0x34: '"table" — Table',
    0x35: '"chair" — Chair',
    0x36: '"stairs_up" — Stairs up',
    0x37: '"stairs_down" — Stairs down',
    0x38: '"well" — Well',
    0x39: '"fountain" — Fountain',
    0x3A: '"pillar" — Pillar / column',
    0x3B: '"statue" — Statue',
    0x3C: '"altar" — Altar',
    0x3D: '"trap" — Trap / spike',
    0x3E: '"grate" — Grate / bars',
    0x3F: '"brazier" — Brazier / fire',

    # ── Row 4 (0x40-0x4F): POI / dungeon features ──
    0x40: '"market" — Market square',
    0x41: '"shrine" — Shrine / gold',
    0x42: '"anvil" — Anvil',
    0x43: '"forge" — Forge',
    0x44: '"bookshelf" — Bookshelf',
    0x45: '"cabinet" — Cabinet',
    0x46: '"weapon_rack" — Weapon rack',
    0x47: '"armor_stand" — Armor stand',
    0x48: '"throne" — Throne',
    0x49: '"cage" — Cage',
    0x4A: '"coffin" — Coffin',
    0x4B: '"grave" — Grave / tombstone',
    0x4C: '"workbench" — Workbench',
    0x4D: '"loom" — Loom',
    0x4E: '"millstone" — Millstone',
    0x4F: '"cauldron" — Cauldron / pot',

    # ── Rows 5-15 (0x50-0xFF): Reserved for future ──
}

# ── Biome → base color mapping (same as world_gen) ──
BIOME_COLORS = {
    "Ocean":        (0x1a, 0x3a, 0x5c),
    "Deep Ocean":   (0x0d, 0x2b, 0x45),
    "Shallows":     (0x3b, 0x7b, 0xaa),
    "Beach":        (0xd4, 0xc4, 0xa8),
    "Grassland":    (0x7c, 0x9c, 0x5e),
    "Forest":       (0x4a, 0x7a, 0x3a),
    "Dense Forest": (0x2d, 0x5a, 0x1e),
    "Taiga":        (0x5a, 0x7a, 0x5a),
    "Tundra":       (0xb0, 0xb8, 0xc0),
    "Desert":       (0xc8, 0xb4, 0x5a),
    "Badlands":     (0xa0, 0x70, 0x50),
    "Savanna":      (0x8c, 0xa8, 0x5a),
    "Swamp":        (0x6a, 0x5a, 0x3a),
    "Mountain":     (0x8a, 0x8a, 0x8a),
    "High Mountain":(0xc0, 0xc0, 0xc0),
    "Volcanic":     (0x5a, 0x2a, 0x1a),
}


# ──────────────────────────────────────────────────────────────────────
# 2.  DEFAULT FONT GENERATOR
# ──────────────────────────────────────────────────────────────────────

def generate_default_font() -> bytes:
    """
    Generate a 256x256 RGBA bitmap font with 256 distinct glyphs.
    Each glyph uses its index as a seed for a deterministic unique pattern.
    Patterns include noise fields, gradients, dots, waves, rings, and
    terrain-inspired shapes (grass tufts, tree silhouettes, rock piles, water ripples).
    """
    size = FONT_IMAGE_SIZE  # 256
    pixels = bytearray(size * size * 4)  # RGBA

    for gy in range(FONT_GRID_ROWS):
        for gx in range(FONT_GRID_COLS):
            char_code = gy * FONT_GRID_COLS + gx
            base_x = gx * GLYPH_SIZE
            base_y = gy * GLYPH_SIZE

            cx = GLYPH_SIZE // 2
            cy = GLYPH_SIZE // 2

            # Use the char_code to seed a deterministic hash for this glyph
            # We mix it to get several variation parameters
            h1 = (char_code * 137 + 251) & 0xFF
            h2 = (char_code * 251 + 137) & 0xFF
            h3 = (char_code * 73 + 199) & 0xFF
            h4 = (char_code * 199 + 73) & 0xFF

            # Pattern type derived from char_code (0..15 for more variety)
            pattern_type = (char_code * 31 + 13) % 16

            # Density / scale parameters
            density = 0.3 + (h1 / 255.0) * 0.5   # 0.3..0.8
            roughness = (h2 / 255.0) * 0.5        # 0..0.5
            asymmetry = (h3 / 255.0) - 0.5        # -0.5..0.5

            for py in range(GLYPH_SIZE):
                for px in range(GLYPH_SIZE):
                    idx = (base_y + py) * size * 4 + (base_x + px) * 4

                    dx = px - cx
                    dy = py - cy
                    dist = (dx * dx + dy * dy) ** 0.5
                    max_dist = cx
                    norm = dist / max_dist  # 0..1

                    # Start with base value
                    val = 0

                    # ── Pattern types 0..15 ──
                    if pattern_type == 0:
                        # Filled circle with variable radius
                        r = 0.3 + (h1 / 255.0) * 0.5
                        val = int(255 * (1.0 - norm / r)) if norm < r else 0

                    elif pattern_type == 1:
                        # Ring with variable thickness
                        thick = 0.1 + (h2 / 255.0) * 0.3
                        ring = abs(norm - 0.5) / thick
                        val = int(255 * (1.0 - ring)) if ring < 1.0 else 0

                    elif pattern_type == 2:
                        # Diamond with variable size
                        diamond = (abs(dx) + abs(dy)) / (GLYPH_SIZE // 2)
                        r = 0.3 + (h3 / 255.0) * 0.5
                        val = int(255 * (1.0 - diamond / r)) if diamond < r else 0

                    elif pattern_type == 3:
                        # Cross / plus sign
                        arm_w = 2 + (h4 % 5)
                        in_arm = abs(dx) < arm_w or abs(dy) < arm_w
                        val = int(255 * (0.5 + density * 0.5)) if in_arm else 0

                    elif pattern_type == 4:
                        # Checkerboard with variable cell size
                        cell_sz = 1 + (h1 % 4)
                        cell = ((px // cell_sz) + (py // cell_sz)) % 2
                        val = int(255 * (0.3 + density * 0.5)) if cell else int(255 * 0.15)

                    elif pattern_type == 5:
                        # Vertical stripes with variable width
                        stripe_w = 1 + (h2 % 5)
                        stripe = (px % (stripe_w * 2) < stripe_w)
                        val = int(255 * (0.4 + density * 0.4)) if stripe else int(255 * 0.1)

                    elif pattern_type == 6:
                        # Horizontal stripes
                        stripe_w = 1 + (h3 % 5)
                        stripe = (py % (stripe_w * 2) < stripe_w)
                        val = int(255 * (0.4 + density * 0.4)) if stripe else int(255 * 0.1)

                    elif pattern_type == 7:
                        # Corner / quadrant fill
                        quadrant = (dx > 0 and dy > 0) or (dx < 0 and dy < 0)
                        val = int(255 * (0.5 + density * 0.3)) if quadrant else 0

                    elif pattern_type == 8:
                        # Concentric rings
                        rings = 2 + (h4 % 4)
                        ring_idx = int(norm * rings) % 2
                        val = int(255 * (0.6 + density * 0.3)) if ring_idx else 0

                    elif pattern_type == 9:
                        # Radial spokes (sunburst)
                        spokes = 4 + (h1 % 5) * 2
                        angle = (dx / max_dist + 1) * 0.5 if max_dist > 0 else 0
                        spoke = int(angle * spokes) % 2
                        val = int(255 * (0.5 + density * 0.3)) if spoke and norm < 0.9 else 0

                    elif pattern_type == 10:
                        # Dots / spots scattered randomly
                        seed = (px * 17 + py * 31 + char_code * 7) & 0xFF
                        dot = seed > (255 * (1.0 - density))
                        val = int(255 * (0.6 + roughness * 0.4)) if dot else 0

                    elif pattern_type == 11:
                        # Wavy lines (water-like)
                        freq = 1 + (h1 % 4)
                        phase = (h2 / 255.0) * 3.14
                        wave = (px * freq * 0.5 + py * freq * 0.3 + phase)
                        wval = (wave % 3.14) / 3.14
                        val = int(255 * (0.3 + wval * 0.5))

                    elif pattern_type == 12:
                        # Gradient ring (soft glow)
                        glow = max(0.0, 1.0 - norm * 2) * (0.5 + density * 0.5)
                        val = int(255 * glow)

                    elif pattern_type == 13:
                        # Triangular / mountain shape
                        tri = (dx * dx + dy * (dy + cx)) / (max_dist * max_dist)
                        tri = max(0.0, 1.0 - tri * 3)
                        val = int(255 * tri)

                    elif pattern_type == 14:
                        # Plaid / tartan pattern
                        step = 1 + (h3 % 4)
                        band_x = (px // step) % 2
                        band_y = (py // step) % 2
                        val = int(255 * (0.4 + density * 0.4)) if band_x or band_y else int(255 * 0.1)

                    elif pattern_type == 15:
                        # Cellular / Voronoi-inspired random cells
                        seed_x = ((px * 7 + py * 13 + char_code * 3) % 256)
                        seed_y = ((px * 11 + py * 5 + char_code * 7) % 256)
                        cell = (seed_x + seed_y) % 2
                        val = int(255 * (0.5 + density * 0.3)) if cell else int(255 * 0.1)

                    # Apply roughness as noise overlay
                    noise_seed = (px * 13 + py * 17 + char_code * 31 + pattern_type * 7) % 256
                    noise = (noise_seed - 128) // 6  # -21..+21 variation
                    val = max(0, min(255, val + noise))

                    # Set pixel — grayscale with full alpha
                    pixels[idx + 0] = val
                    pixels[idx + 1] = val
                    pixels[idx + 2] = val
                    pixels[idx + 3] = 255

    return bytes(pixels)


def write_png(filepath: str, width: int, height: int, pixel_data: bytes) -> None:
    """Write a minimal PNG file from raw RGBA pixel data."""
    # PNG signature
    sig = b'\x89PNG\r\n\x1a\n'

    def make_chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk = chunk_type + data
        crc = zlib.crc32(chunk_type + data) & 0xffffffff
        return struct.pack('>I', len(data)) + chunk + struct.pack('>I', crc)

    # IHDR chunk
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    ihdr = make_chunk(b'IHDR', ihdr_data)

    # IDAT chunk — raw pixel data with filters
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter byte: none
        row_start = y * stride
        raw.extend(pixel_data[row_start:row_start + stride])

    compress = zlib.compress(raw)
    idat = make_chunk(b'IDAT', compress)

    # IEND chunk
    iend = make_chunk(b'IEND', b'')

    with open(filepath, 'wb') as f:
        f.write(sig)
        f.write(ihdr)
        f.write(idat)
        f.write(iend)


def generate_default_font_file(filepath: str = "/tmp/default_font.png") -> str:
    """Generate and save the default bitmap font PNG."""
    pixels = generate_default_font()
    write_png(filepath, FONT_IMAGE_SIZE, FONT_IMAGE_SIZE, pixels)
    print(f"Default font saved to {filepath}")
    print(f"  Size: {FONT_IMAGE_SIZE}x{FONT_IMAGE_SIZE} px")
    print(f"  Glyphs: {FONT_GRID_COLS}x{FONT_GRID_ROWS} = {FONT_GRID_COLS * FONT_GRID_ROWS}")
    print(f"  Each glyph: {GLYPH_SIZE}x{GLYPH_SIZE} px")
    return filepath


# ──────────────────────────────────────────────────────────────────────
# 3.  FONT LOADER
# ──────────────────────────────────────────────────────────────────────

def load_font(filepath: str, has_color: bool = False) -> Tuple[bytes, int, int]:
    """
    Load a bitmap font PNG. Returns (pixel_data, width, height).
    Expects a 256x256 RGBA PNG.

    If has_color is True, the font is treated as pre-colored: no alpha fix,
    no grayscale conversion. The raw RGBA pixels are returned as-is.
    """
    # Decode via Pillow so ALL PNG scanline filters (None/Sub/Up/Average/Paeth) are
    # reversed correctly, and palette / RGB / grayscale / 16-bit / interlaced PNGs are
    # normalized to 8-bit RGBA. The old hand-rolled reader assumed every scanline used
    # filter 0, so it only decoded PNGs written by this module's own write_png() and
    # produced garbage for anything an editor (PixlPunkt, GIMP, Photoshop) saved.
    from PIL import Image

    img = Image.open(filepath).convert("RGBA")
    width, height = img.size
    pixels = bytearray(img.tobytes())  # row-major RGBA, no filter bytes

    if width != FONT_IMAGE_SIZE or height != FONT_IMAGE_SIZE:
        print(f"Warning: font image is {width}x{height}, expected {FONT_IMAGE_SIZE}x{FONT_IMAGE_SIZE}")

    if has_color:
        # Pre-colored font: use raw pixels as-is, no alpha fix, no grayscale conversion
        return (bytes(pixels), width, height)

    # Some image editors (Windows) save RGBA PNGs with alpha=0 but valid grayscale data.
    # Detect this: if >50% of alpha bytes are 0 but the corresponding gray bytes are non-zero,
    # treat the image as opaque by setting alpha to 255 everywhere.
    alpha_sum = sum(pixels[3::4])
    total_pixels = width * height
    if alpha_sum < total_pixels * 64:  # average alpha < 64 → likely alpha channel was wiped
        print(f"  Note: font alpha channel appears flat (avg {alpha_sum // total_pixels}). Forcing opaque.")
        for i in range(len(pixels)):
            if i % 4 == 3:
                pixels[i] = 255

    # Windows image editors may also save PNGs where R≠G≠B for what should be grayscale.
    # Force all pixels to true grayscale by averaging R/G/B so the tint function
    # (which reads R as the gray value) works consistently.
    # Do this unconditionally — the font is designed as grayscale.
    for i in range(len(pixels)):
        if i % 4 < 3:  # R, G, B channels
            off = i - (i % 4)
            r = pixels[off]
            g = pixels[off + 1]
            b = pixels[off + 2]
            gray = (r + g + b) // 3
            pixels[off] = gray
            pixels[off + 1] = gray
            pixels[off + 2] = gray

    return (bytes(pixels), width, height)


# ──────────────────────────────────────────────────────────────────────
# 4.  GLYPH EXTRACTION & TINTING
# ──────────────────────────────────────────────────────────────────────

def get_glyph_pixels(font_pixels: bytes, glyph_idx: int) -> bytes:
    """
    Extract a single glyph's RGBA pixels from the font image.
    Glyph index runs 0..255, arranged in a 16x16 grid.
    """
    size = int((len(font_pixels) // 4) ** 0.5)  # font image side length
    gx = glyph_idx % FONT_GRID_COLS
    gy = glyph_idx // FONT_GRID_COLS

    ox = gx * GLYPH_SIZE
    oy = gy * GLYPH_SIZE

    glyph_pixels = bytearray(GLYPH_SIZE * GLYPH_SIZE * 4)
    for py in range(GLYPH_SIZE):
        src_start = (oy + py) * size * 4 + ox * 4
        dst_start = py * GLYPH_SIZE * 4
        glyph_pixels[dst_start:dst_start + GLYPH_SIZE * 4] = \
            font_pixels[src_start:src_start + GLYPH_SIZE * 4]

    return bytes(glyph_pixels)


def tint_glyph(glyph_pixels: bytes, color: Tuple[int, int, int], alpha_mult: float = 1.0, has_color: bool = False) -> bytes:
    """
    Tint a grayscale glyph by a base color.
    White pixels → pure color, black pixels → black.
    Alpha is preserved and multiplied by alpha_mult.

    If has_color is True, the glyph is already colored — returns it unchanged.

    glyph_pixels: raw RGBA bytes for one glyph (GLYPH_SIZE*GLYPH_SIZE*4 bytes)
    color: (R, G, B) tuple
    alpha_mult: multiplier for the glyph's alpha channel

    Returns tinted RGBA bytes.
    """
    if has_color:
        return glyph_pixels

    n = len(glyph_pixels) // 4
    cr, cg, cb = color
    result = bytearray(len(glyph_pixels))

    for i in range(n):
        off = i * 4
        gray = glyph_pixels[off + 0]  # R channel in grayscale font
        alpha = glyph_pixels[off + 3]

        # Tint: gray determines how much of the color shows
        # gray=255 → full color, gray=0 → black
        factor = gray / 255.0
        inv = 1.0 - factor

        result[off + 0] = int(cr * factor + 0 * inv) & 0xFF
        result[off + 1] = int(cg * factor + 0 * inv) & 0xFF
        result[off + 2] = int(cb * factor + 0 * inv) & 0xFF
        result[off + 3] = int(alpha * alpha_mult) & 0xFF

    return bytes(result)


def blend_pixels(base: bytes, overlay: bytes) -> bytes:
    """
    Alpha-blend overlay pixels onto base pixels.
    Both are RGBA byte arrays of the same size.
    """
    n = len(base) // 4
    result = bytearray(len(base))

    for i in range(n):
        bo = i * 4
        # Base
        br, bg, bb, ba = base[bo:bo+4]
        # Overlay
        or_, og, ob, oa = overlay[bo:bo+4]

        if oa == 0:
            # Overlay fully transparent — keep base
            result[bo:bo+4] = base[bo:bo+4]
        elif ba == 0:
            # Base fully transparent — use overlay
            result[bo:bo+4] = overlay[bo:bo+4]
        else:
            # Standard alpha blending
            oa_norm = oa / 255.0
            ba_norm = ba / 255.0
            out_a = oa_norm + ba_norm * (1 - oa_norm)
            if out_a > 0:
                r = int((or_ * oa_norm + br * ba_norm * (1 - oa_norm)) / out_a) & 0xFF
                g = int((og * oa_norm + bg * ba_norm * (1 - oa_norm)) / out_a) & 0xFF
                b = int((ob * oa_norm + bb * ba_norm * (1 - oa_norm)) / out_a) & 0xFF
                a = int(out_a * 255) & 0xFF
                result[bo:bo+4] = (r, g, b, a)
            else:
                result[bo:bo+4] = (0, 0, 0, 0)

    return bytes(result)


# ──────────────────────────────────────────────────────────────────────
# 5.  REFERENCE SHEET GENERATOR
# ──────────────────────────────────────────────────────────────────────

def _generate_reference_sheet(font_pixels: bytes, font_w: int, font_h: int, output_path: str,
                               legend: Optional[Dict[int, str]] = None) -> None:
    """
    Generate a reference image showing all 256 glyphs with their index numbers
    and a readable semantic label showing what each glyph tile represents.
    Each glyph is rendered at 2x size (32x32) with info below it.
    The output is a single PNG showing the full grid.
    """
    ref_glyph_size = GLYPH_SIZE * 2  # 32 px per glyph for readability
    ref_cols = FONT_GRID_COLS
    ref_rows = FONT_GRID_ROWS
    label_height = 48  # px for index + semantic label below each glyph (taller for readable text)

    img_w = ref_cols * ref_glyph_size
    img_h = ref_rows * (ref_glyph_size + label_height)

    canvas = bytearray(img_w * img_h * 4)
    # Fill background dark
    for i in range(len(canvas)):
        canvas[i] = 0x20 if i % 4 != 3 else 0xFF  # dark gray background

    # ── Pre-define a simple 5×7 bitmap font for readable labels ──
    # Each character is 5 wide × 7 tall, 1px gap
    LABEL_DIGIT_W = 5
    LABEL_DIGIT_H = 7
    LABEL_DIGIT_GAP = 1
    # A minimal readable 5×7 font for A-Z, 0-9, common punctuation
    BITMAP_FONT = {
        'A': [[0,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[1,1,1,1,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1]],
        'B': [[1,1,1,1,0],[1,0,0,0,1],[1,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,1,1,1,0]],
        'C': [[0,1,1,1,0],[1,0,0,0,1],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,1],[0,1,1,1,0]],
        'D': [[1,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,1,1,1,0]],
        'E': [[1,1,1,1,1],[1,0,0,0,0],[1,1,1,1,0],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0],[1,1,1,1,1]],
        'F': [[1,1,1,1,1],[1,0,0,0,0],[1,1,1,1,0],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0]],
        'G': [[0,1,1,1,0],[1,0,0,0,1],[1,0,0,0,0],[1,0,0,1,1],[1,0,0,0,1],[1,0,0,0,1],[0,1,1,1,0]],
        'H': [[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,1,1,1,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1]],
        'I': [[1,1,1,1,1],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[1,1,1,1,1]],
        'J': [[0,0,0,0,1],[0,0,0,0,1],[0,0,0,0,1],[0,0,0,0,1],[0,0,0,0,1],[1,0,0,0,1],[0,1,1,1,0]],
        'K': [[1,0,0,0,1],[1,0,0,1,0],[1,0,1,0,0],[1,1,0,0,0],[1,0,1,0,0],[1,0,0,1,0],[1,0,0,0,1]],
        'L': [[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0],[1,1,1,1,1]],
        'M': [[1,0,0,0,1],[1,1,0,1,1],[1,0,1,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1]],
        'N': [[1,0,0,0,1],[1,1,0,0,1],[1,0,1,0,1],[1,0,0,1,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1]],
        'O': [[0,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[0,1,1,1,0]],
        'P': [[1,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[1,1,1,1,0],[1,0,0,0,0],[1,0,0,0,0],[1,0,0,0,0]],
        'Q': [[0,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,1,1],[1,0,0,0,1],[0,1,1,1,1]],
        'R': [[1,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[1,1,1,1,0],[1,0,1,0,0],[1,0,0,1,0],[1,0,0,0,1]],
        'S': [[0,1,1,1,0],[1,0,0,0,1],[0,1,1,1,0],[0,0,0,0,1],[0,0,0,0,1],[1,0,0,0,1],[0,1,1,1,0]],
        'T': [[1,1,1,1,1],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0]],
        'U': [[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[0,1,1,1,0]],
        'V': [[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[0,1,0,1,0],[0,0,1,0,0]],
        'W': [[1,0,0,0,1],[1,0,0,0,1],[1,0,0,0,1],[1,0,1,0,1],[1,0,1,0,1],[1,0,1,0,1],[0,1,0,1,0]],
        'X': [[1,0,0,0,1],[1,0,0,0,1],[0,1,0,1,0],[0,0,1,0,0],[0,1,0,1,0],[1,0,0,0,1],[1,0,0,0,1]],
        'Y': [[1,0,0,0,1],[1,0,0,0,1],[0,1,0,1,0],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0]],
        'Z': [[1,1,1,1,1],[0,0,0,1,0],[0,0,1,0,0],[0,1,0,0,0],[1,0,0,0,0],[1,0,0,0,0],[1,1,1,1,1]],
        # Numbers
        '0': [[0,1,1,1,0],[1,0,0,0,1],[1,0,0,1,1],[1,0,1,0,1],[1,1,0,0,1],[1,0,0,0,1],[0,1,1,1,0]],
        '1': [[0,0,1,0,0],[0,1,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[0,0,1,0,0],[1,1,1,1,1]],
        '2': [[0,1,1,1,0],[1,0,0,0,1],[0,0,0,1,0],[0,0,1,0,0],[0,1,0,0,0],[1,0,0,0,0],[1,1,1,1,1]],
        '3': [[0,1,1,1,0],[1,0,0,0,1],[0,0,0,1,0],[0,1,1,1,0],[0,0,0,1,0],[1,0,0,0,1],[0,1,1,1,0]],
        '4': [[0,0,0,0,0],[0,0,0,0,0],[1,0,0,0,1],[1,0,0,0,1],[1,1,1,1,1],[0,0,0,0,1],[0,0,0,0,1]],
        '5': [[1,1,1,1,1],[1,0,0,0,0],[1,1,1,1,0],[0,0,0,0,1],[0,0,0,0,1],[1,0,0,0,1],[0,1,1,1,0]],
        '6': [[0,1,1,1,0],[1,0,0,0,0],[1,0,0,0,0],[1,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[0,1,1,1,0]],
        '7': [[1,1,1,1,1],[0,0,0,0,1],[0,0,0,1,0],[0,0,1,0,0],[0,1,0,0,0],[0,1,0,0,0],[0,1,0,0,0]],
        '8': [[0,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[0,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[0,1,1,1,0]],
        '9': [[0,1,1,1,0],[1,0,0,0,1],[1,0,0,0,1],[0,1,1,1,1],[0,0,0,0,1],[0,0,0,0,1],[0,1,1,1,0]],
        # Punctuation
        ' ': [[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0]],
        ',': [[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,1,0,0,0],[1,0,0,0,0]],
        '.': [[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,1,1,0,0]],
        '-': [[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[1,1,1,1,1],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0]],
        '/': [[0,0,0,0,1],[0,0,0,1,0],[0,0,1,0,0],[0,1,0,0,0],[1,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0]],
        "'": [[0,0,1,0,0],[0,0,1,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0]],
        '"': [[0,1,0,1,0],[0,1,0,1,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0],[0,0,0,0,0]],
    }
    # Lowercase letters reuse uppercase
    for uc in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        lc = uc.lower()
        BITMAP_FONT[lc] = BITMAP_FONT[uc]

    def draw_label_text(canvas, text: str, x: int, y: int, color_rgb, img_w):
        """Draw text using the 5×7 bitmap font at position (x,y)."""
        cr, cg, cb = color_rgb
        for ci, ch in enumerate(text):
            glyph = BITMAP_FONT.get(ch, BITMAP_FONT.get(' ', [[0]*5]*7))
            if not glyph:
                continue
            for dy in range(LABEL_DIGIT_H):
                for dx in range(LABEL_DIGIT_W):
                    if dy < len(glyph) and dx < len(glyph[dy]) and glyph[dy][dx]:
                        px = x + ci * (LABEL_DIGIT_W + LABEL_DIGIT_GAP) + dx
                        py = y + dy
                        if 0 <= px < img_w and 0 <= py < len(canvas) // (img_w * 4):
                            off = py * img_w * 4 + px * 4
                            canvas[off + 0] = cr
                            canvas[off + 1] = cg
                            canvas[off + 2] = cb
                            canvas[off + 3] = 0xFF

    for gy in range(ref_rows):
        for gx in range(ref_cols):
            idx = gy * ref_cols + gx
            glyph = get_glyph_pixels(font_pixels, idx)

            base_x = gx * ref_glyph_size
            base_y = gy * (ref_glyph_size + label_height)

            # Draw glyph at 2x
            for py in range(GLYPH_SIZE):
                for px in range(GLYPH_SIZE):
                    src_off = py * GLYPH_SIZE * 4 + px * 4
                    gray = glyph[src_off + 0]
                    alpha = glyph[src_off + 3]

                    # Scale up: each source pixel becomes 2x2
                    for dy in range(2):
                        for dx in range(2):
                            ci = (base_y + py * 2 + dy) * img_w * 4 + (base_x + px * 2 + dx) * 4
                            canvas[ci + 0] = gray
                            canvas[ci + 1] = gray
                            canvas[ci + 2] = gray
                            canvas[ci + 3] = alpha

            # Draw index label below glyph
            label_str = str(idx)
            digit_w = 3
            digit_h = 5
            digit_gap = 1
            total_w = len(label_str) * (digit_w + digit_gap)
            label_x = base_x + (ref_glyph_size - total_w) // 2
            label_y = base_y + ref_glyph_size + 2

            for ci, ch in enumerate(label_str):
                digit = _render_digit(ch)
                for dy in range(digit_h):
                    for dx in range(digit_w):
                        if digit[dy][dx]:
                            li = (label_y + dy) * img_w * 4 + (label_x + ci * (digit_w + digit_gap) + dx) * 4
                            canvas[li + 0] = 0xFF
                            canvas[li + 1] = 0xFF
                            canvas[li + 2] = 0xFF
                            canvas[li + 3] = 0xFF

            # Draw semantic label in readable 5×7 text below the index
            if legend and idx in legend:
                sem_label = legend[idx]
                # Parse the label to extract the key name (before the ' — ' or first part)
                # Format is usually: '"Key" — Description'
                # Extract just the key name for a compact tile-type label
                key_name = sem_label
                if ' — ' in sem_label:
                    quote_part = sem_label.split(' — ')[0].strip('"').strip("'")
                    key_name = quote_part
                elif ' - ' in sem_label:
                    quote_part = sem_label.split(' - ')[0].strip('"').strip("'")
                    key_name = quote_part

                # Multi-line wrapping: each 5×7 char is 6px wide (5+1 gap),
                # column is 32px wide → max 5 chars per line with 1px margin
                max_chars_per_line = 5
                # Split into lines of max_chars_per_line each
                lines = []
                while len(key_name) > 0:
                    lines.append(key_name[:max_chars_per_line])
                    key_name = key_name[max_chars_per_line:]

                line_h = LABEL_DIGIT_H + 2  # 7px font + 2px spacing = 9px per line
                total_label_h = len(lines) * line_h
                label_y = label_y + digit_h + 4
                # Center the block vertically in available space
                # (label_height - digit_h - 4 - 2) = ~37px, enough for up to 4 lines
                label_y = label_y + (37 - total_label_h) // 2

                # Draw background bar tall enough for all lines
                bar_h = total_label_h + 2
                bar_w = ref_glyph_size - 4
                for by in range(bar_h):
                    for bx in range(bar_w):
                        bi = (label_y - 1 + by) * img_w * 4 + (base_x + 2 + bx) * 4
                        canvas[bi + 0] = 0x10
                        canvas[bi + 1] = 0x10
                        canvas[bi + 2] = 0x10
                        canvas[bi + 3] = 0xC0

                for li, line in enumerate(lines):
                    line_x = base_x + (ref_glyph_size - (len(line) * (LABEL_DIGIT_W + LABEL_DIGIT_GAP))) // 2
                    line_y = label_y + li * line_h
                    draw_label_text(canvas, line, line_x, line_y, (0xC0, 0xFF, 0xC0), img_w)

    write_png(output_path, img_w, img_h, bytes(canvas))


def _render_digit(ch: str) -> List[List[bool]]:
    """Simple 3x5 bitmap digit renderer."""
    # Each digit is a 3x5 bool grid
    digits = {
        '0': [[1,1,1],[1,0,1],[1,0,1],[1,0,1],[1,1,1]],
        '1': [[0,1,0],[0,1,0],[0,1,0],[0,1,0],[0,1,0]],
        '2': [[1,1,1],[0,0,1],[1,1,1],[1,0,0],[1,1,1]],
        '3': [[1,1,1],[0,0,1],[1,1,1],[0,0,1],[1,1,1]],
        '4': [[1,0,1],[1,0,1],[1,1,1],[0,0,1],[0,0,1]],
        '5': [[1,1,1],[1,0,0],[1,1,1],[0,0,1],[1,1,1]],
        '6': [[1,1,1],[1,0,0],[1,1,1],[1,0,1],[1,1,1]],
        '7': [[1,1,1],[0,0,1],[0,0,1],[0,0,1],[0,0,1]],
        '8': [[1,1,1],[1,0,1],[1,1,1],[1,0,1],[1,1,1]],
        '9': [[1,1,1],[1,0,1],[1,1,1],[0,0,1],[1,1,1]],
    }
    return digits.get(ch, [[0,0,0],[0,0,0],[0,0,0],[0,0,0],[0,0,0]])


# ──────────────────────────────────────────────────────────────────────
# 6.  COMMAND LINE
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bitmap font tools for DF-style world maps")
    parser.add_argument("--generate-font", action="store_true", help="Generate default bitmap font PNG")
    parser.add_argument("--font-output", type=str, default="/tmp/default_font.png", help="Output path for generated font")
    parser.add_argument("--reference", type=str, default=None, help="Path to a font PNG to generate reference sheet from")
    parser.add_argument("--reference-output", type=str, default=None, help="Output path for reference sheet (default: font path with _reference.png)")
    parser.add_argument("--info", type=str, default=None, help="Path to a font PNG to inspect")
    args = parser.parse_args()

    if args.generate_font:
        generate_default_font_file(args.font_output)
    elif args.reference:
        pixels, w, h = load_font(args.reference)
        out_path = args.reference_output or args.reference.replace('.png', '_reference.png')
        _generate_reference_sheet(pixels, w, h, out_path, legend=GLYPH_LEGEND)
        print(f"Reference sheet saved to {out_path}")
    elif args.info:
        pixels, w, h = load_font(args.info)
        print(f"Font: {w}x{h} px, {len(pixels)} bytes")
        print(f"  Glyph grid: {FONT_GRID_COLS}x{FONT_GRID_ROWS}")
        print(f"  Glyph size: {GLYPH_SIZE}x{GLYPH_SIZE}")
        for idx in range(4):
            g = get_glyph_pixels(pixels, idx)
            print(f"  Glyph {idx}: min={min(g[::4])} max={max(g[::4])} alpha={g[3]}")
    else:
        parser.print_help()
