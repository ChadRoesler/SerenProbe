#!/usr/bin/env python3
"""
POI Local Map Renderer — zoomed-in DF-style area map using a bitmap font.

For a given POI (city, fortress, village, tower, shrine), renders a local map
showing terrain, elevation, and procedural building layouts — walls, keeps,
temples, towers, houses — using glyphs from a bitmap font.

Usage:
  python3 poi_map_renderer.py --world /tmp/world.json --poi-id 2 \\
      --font /tmp/default_font.png --output /tmp/city_map.png
"""

import json
import sys
import os
import random
from typing import Dict, List, Tuple, Optional

from bitmap_font import (
    GLYPH_SIZE, FONT_GRID_COLS, FONT_GRID_ROWS, FONT_IMAGE_SIZE,
    load_font, get_glyph_pixels, tint_glyph, blend_pixels, write_png,
    BIOME_COLORS, TERRAIN_GLYPH_MAP, FALLBACK_GLYPH,
)


# ──────────────────────────────────────────────────────────────────────
# 1.  BUILDING DEFINITIONS
# ──────────────────────────────────────────────────────────────────────

# Each building type uses a glyph index from the font, a base color,
# and a size range (min_w, max_w, min_h, max_h) in tiles.
# The same glyph can be tinted different colors for different materials.

BUILDING_TYPES = {
    # ── Building exteriors (Row 2: 0x20-0x2F) ──
    "wall_stone": {
        "glyph": 0x20,        # stone wall / keep
        "color": (0x70, 0x70, 0x70),   # gray stone
    },
    "wall_wood": {
        "glyph": 0x21,        # wood wall
        "color": (0x5A, 0x3A, 0x2A),   # dark brown wood
    },
    "keep": {
        "glyph": 0x22,        # keep / fortress core
        "color": (0x90, 0x80, 0x60),   # tan stone
    },
    "tower": {
        "glyph": 0x23,        # tower structure
        "color": (0x80, 0x70, 0x60),   # gray-brown
    },
    "house": {
        "glyph": 0x24,        # house / building
        "color": (0x6A, 0x4A, 0x30),   # brown wood
    },
    "temple": {
        "glyph": 0x25,        # temple / shrine
        "color": (0xC0, 0xA0, 0x60),   # golden
    },
    "gate": {
        "glyph": 0x26,        # gate / entrance
        "color": (0x50, 0x40, 0x30),   # dark wood
    },
    "bridge": {
        "glyph": 0x27,        # bridge
        "color": (0x60, 0x50, 0x40),   # dark stone
    },

    # ── Roads (Row 1: 0x18) ──
    "road": {
        "glyph": 0x18,        # road / path
        "color": (0x80, 0x70, 0x50),   # dirt brown
    },

    # ── Furniture / interiors (Row 3: 0x30-0x3F) ──
    "door": {
        "glyph": 0x30,        # door
        "color": (0x50, 0x3A, 0x28),   # dark wood
    },
    "chest": {
        "glyph": 0x31,        # chest
        "color": (0x60, 0x45, 0x30),   # brown wood
    },
    "barrel": {
        "glyph": 0x32,        # barrel
        "color": (0x6A, 0x50, 0x38),   # medium brown
    },
    "bed": {
        "glyph": 0x33,        # bed
        "color": (0x70, 0x60, 0x50),   # tan
    },
    "table": {
        "glyph": 0x34,        # table
        "color": (0x60, 0x50, 0x40),   # brown
    },
    "chair": {
        "glyph": 0x35,        # chair
        "color": (0x55, 0x45, 0x35),   # dark brown
    },
    "stairs_up": {
        "glyph": 0x36,        # stairs up
        "color": (0x80, 0x70, 0x60),   # gray
    },
    "stairs_down": {
        "glyph": 0x37,        # stairs down
        "color": (0x60, 0x60, 0x70),   # dark gray
    },
    "well": {
        "glyph": 0x38,        # well
        "color": (0x40, 0x60, 0xA0),   # blue stone
    },
    "fountain": {
        "glyph": 0x39,        # fountain
        "color": (0x50, 0x80, 0xB0),   # bright blue
    },
    "pillar": {
        "glyph": 0x3A,        # pillar / column
        "color": (0x80, 0x80, 0x80),   # gray
    },
    "statue": {
        "glyph": 0x3B,        # statue
        "color": (0xC0, 0xC0, 0xC0),   # white stone
    },
    "altar": {
        "glyph": 0x3C,        # altar
        "color": (0xD0, 0xB0, 0x80),   # gold stone
    },

    # ── POI / dungeon features (Row 4: 0x40-0x4F) ──
    "market": {
        "glyph": 0x40,        # market square
        "color": (0xA0, 0x90, 0x70),   # light stone
    },
    "shrine": {
        "glyph": 0x41,        # shrine / gold
        "color": (0xE0, 0xC0, 0x80),   # bright gold
    },
    "anvil": {
        "glyph": 0x42,        # anvil
        "color": (0x50, 0x50, 0x60),   # dark metal
    },
    "forge": {
        "glyph": 0x43,        # forge
        "color": (0xD0, 0x60, 0x30),   # red hot
    },
    "bookshelf": {
        "glyph": 0x44,        # bookshelf
        "color": (0x60, 0x50, 0x40),   # brown
    },
    "cabinet": {
        "glyph": 0x45,        # cabinet
        "color": (0x55, 0x45, 0x35),   # dark brown
    },
    "weapon_rack": {
        "glyph": 0x46,        # weapon rack
        "color": (0x70, 0x60, 0x50),   # tan
    },
    "armor_stand": {
        "glyph": 0x47,        # armor stand
        "color": (0x80, 0x70, 0x60),   # gray
    },
    "throne": {
        "glyph": 0x48,        # throne
        "color": (0xC0, 0x90, 0x50),   # golden
    },
    "cage": {
        "glyph": 0x49,        # cage
        "color": (0x60, 0x60, 0x70),   # iron
    },
    "coffin": {
        "glyph": 0x4A,        # coffin
        "color": (0x50, 0x40, 0x30),   # dark wood
    },
    "grave": {
        "glyph": 0x4B,        # grave / tombstone
        "color": (0x70, 0x70, 0x70),   # gray stone
    },
    "workbench": {
        "glyph": 0x4C,        # workbench
        "color": (0x60, 0x50, 0x40),   # brown
    },
    "loom": {
        "glyph": 0x4D,        # loom
        "color": (0x70, 0x60, 0x50),   # tan
    },
    "millstone": {
        "glyph": 0x4E,        # millstone
        "color": (0x80, 0x80, 0x70),   # light gray
    },
    "cauldron": {
        "glyph": 0x4F,        # cauldron / pot
        "color": (0x60, 0x50, 0x40),   # dark iron
    },
}


# ──────────────────────────────────────────────────────────────────────
# 2.  LOCAL AREA EXTRACTION
# ──────────────────────────────────────────────────────────────────────

def extract_local_area(
    world: Dict,
    center_x: int,
    center_y: int,
    radius: int = 24,
) -> Tuple[List[List[int]], List[List[float]], int, int]:
    """
    Extract a (2*radius+1) x (2*radius+1) chunk of the biome/elevation grid
    centered on (center_x, center_y). Returns (biome_chunk, elev_chunk, offset_x, offset_y).
    """
    terrain = world.get("terrain", {})
    biome_grid = terrain.get("biome_grid", [])
    elevation_grid = terrain.get("elevation_grid", [])

    if not biome_grid:
        raise ValueError("No biome_grid in world data")

    world_h = len(biome_grid)
    world_w = len(biome_grid[0]) if world_h > 0 else 0

    size = 2 * radius + 1
    biome_chunk = [[0] * size for _ in range(size)]
    elev_chunk = [[0.5] * size for _ in range(size)]

    offset_x = center_x - radius
    offset_y = center_y - radius

    for dy in range(size):
        wy = offset_y + dy
        for dx in range(size):
            wx = offset_x + dx
            if 0 <= wy < world_h and 0 <= wx < world_w:
                biome_chunk[dy][dx] = biome_grid[wy][wx]
                elev_chunk[dy][dx] = elevation_grid[wy][wx] if wy < len(elevation_grid) and wx < len(elevation_grid[wy]) else 0.5

    return biome_chunk, elev_chunk, offset_x, offset_y


# ──────────────────────────────────────────────────────────────────────
# 3.  SITE LAYOUT GENERATOR
# ──────────────────────────────────────────────────────────────────────

def generate_site_layout(
    site_type: str,
    population: int,
    is_capital: bool,
    local_w: int,
    local_h: int,
    rng: random.Random,
) -> List[List[Optional[str]]]:
    """
    Generate a procedural building layout for the local map area.
    Returns a 2D grid where each cell is either None (terrain) or a building type name.
    """
    layout = [[None] * local_w for _ in range(local_h)]
    cx = local_w // 2
    cy = local_h // 2

    if site_type == "city":
        _layout_city(layout, cx, cy, population, is_capital, local_w, local_h, rng)
    elif site_type == "fortress":
        _layout_fortress(layout, cx, cy, population, local_w, local_h, rng)
    elif site_type == "village":
        _layout_village(layout, cx, cy, population, local_w, local_h, rng)
    elif site_type == "tower":
        _layout_tower(layout, cx, cy, local_w, local_h, rng)
    elif site_type == "shrine":
        _layout_shrine(layout, cx, cy, local_w, local_h, rng)
    else:
        # Default: small cluster
        _layout_village(layout, cx, cy, 20, local_w, local_h, rng)

    return layout


def _layout_city(
    layout: List[List[Optional[str]]],
    cx: int, cy: int,
    population: int,
    is_capital: bool,
    w: int, h: int,
    rng: random.Random,
) -> None:
    """Layout a city with outer wall, keep, temple, market, houses."""
    # City wall — rectangle with a gate on the south side
    wall_size = min(w, h) // 2 - 2
    wall_size = max(wall_size, 6)

    # Outer wall
    x0 = cx - wall_size
    x1 = cx + wall_size
    y0 = cy - wall_size
    y1 = cy + wall_size

    for x in range(x0, x1 + 1):
        if 0 <= x < w:
            if 0 <= y0 < h: layout[y0][x] = "wall_stone"
            if 0 <= y1 < h: layout[y1][x] = "wall_stone"
    for y in range(y0, y1 + 1):
        if 0 <= y < h:
            if 0 <= x0 < w: layout[y][x0] = "wall_stone"
            if 0 <= x1 < w: layout[y][x1] = "wall_stone"

    # Gate on south wall
    gate_y = y1
    gate_x = cx
    if 0 <= gate_y < h and 0 <= gate_x < w:
        layout[gate_y][gate_x] = "gate"

    # Keep at center
    keep_size = 3 if is_capital else 2
    for dy in range(-keep_size, keep_size + 1):
        for dx in range(-keep_size, keep_size + 1):
            if abs(dx) <= keep_size and abs(dy) <= keep_size:
                nx = cx + dx
                ny = cy + dy
                if 0 <= nx < w and 0 <= ny < h:
                    if dx == 0 and dy == 0:
                        layout[ny][nx] = "keep"
                    elif abs(dx) <= keep_size and abs(dy) <= keep_size:
                        layout[ny][nx] = "wall_stone"

    # Temple offset from center
    tx = cx + 3
    ty = cy - 3
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            nx = tx + dx
            ny = ty + dy
            if 0 <= nx < w and 0 <= ny < h:
                if abs(dx) <= 2 and abs(dy) <= 2:
                    layout[ny][nx] = "temple"

    # Market square near gate
    mx = cx
    my = gate_y - 2
    for dy in range(-1, 2):
        for dx in range(-3, 4):
            nx = mx + dx
            ny = my + dy
            if 0 <= nx < w and 0 <= ny < h:
                layout[ny][nx] = "market"

    # Houses fill remaining interior space
    house_count = population // 5 + rng.randint(5, 15)
    for _ in range(house_count):
        hx = cx + rng.randint(-wall_size + 1, wall_size - 1)
        hy = cy + rng.randint(-wall_size + 1, wall_size - 1)
        if 0 <= hx < w and 0 <= hy < h and layout[hy][hx] is None:
            layout[hy][hx] = "house"

    # Roads — main road from gate to keep
    for y in range(gate_y, cy, -1):
        if 0 <= y < h and 0 <= cx < w and layout[y][cx] is None:
            layout[y][cx] = "road"


def _layout_fortress(
    layout: List[List[Optional[str]]],
    cx: int, cy: int,
    population: int,
    w: int, h: int,
    rng: random.Random,
) -> None:
    """Layout a fortress with thick walls, central keep, corner towers."""
    wall_size = min(w, h) // 3
    wall_size = max(wall_size, 5)

    x0 = cx - wall_size
    x1 = cx + wall_size
    y0 = cy - wall_size
    y1 = cy + wall_size

    # Double-thick outer wall
    for x in range(x0, x1 + 1):
        for layer in range(2):
            yy = y0 + layer if layer == 0 else y1 - layer
            if 0 <= yy < h and 0 <= x < w:
                layout[yy][x] = "wall_stone"
    for y in range(y0, y1 + 1):
        for layer in range(2):
            xx = x0 + layer if layer == 0 else x1 - layer
            if 0 <= y < h and 0 <= xx < w:
                layout[y][xx] = "wall_stone"

    # Corner towers
    for (tx, ty) in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                nx = tx + dx
                ny = ty + dy
                if 0 <= nx < w and 0 <= ny < h:
                    layout[ny][nx] = "tower"

    # Gate on south wall
    gate_y = y1
    gate_x = cx
    if 0 <= gate_y < h and 0 <= gate_x < w:
        layout[gate_y][gate_x] = "gate"

    # Central keep
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            nx = cx + dx
            ny = cy + dy
            if 0 <= nx < w and 0 <= ny < h:
                if abs(dx) <= 3 and abs(dy) <= 3:
                    layout[ny][nx] = "keep"

    # Barracks and support buildings
    for _ in range(population // 3):
        bx = cx + rng.randint(-wall_size + 2, wall_size - 2)
        by = cy + rng.randint(-wall_size + 2, wall_size - 2)
        if 0 <= bx < w and 0 <= by < h and layout[by][bx] is None:
            layout[by][bx] = "house"


def _layout_village(
    layout: List[List[Optional[str]]],
    cx: int, cy: int,
    population: int,
    w: int, h: int,
    rng: random.Random,
) -> None:
    """Layout a village — clustered houses around a central well/green."""
    cluster_r = 6

    # Central well or shrine
    if 0 <= cy < h and 0 <= cx < w:
        layout[cy][cx] = "well"

    # Houses clustered around center
    house_count = max(3, population // 3)
    for _ in range(house_count):
        angle = rng.uniform(0, 6.28)
        dist = rng.uniform(2, cluster_r)
        hx = cx + int(dist * rng.choice([-1, 1]))
        hy = cy + int(dist * rng.choice([-1, 1]))
        if 0 <= hx < w and 0 <= hy < h and layout[hy][hx] is None:
            layout[hy][hx] = "house"

    # Dirt roads from center outward
    for angle in range(0, 360, 45):
        rad = angle * 3.14159 / 180
        for d in range(1, cluster_r):
            rx = cx + int(d * rng.choice([-1, 1]))
            ry = cy + int(d * rng.choice([-1, 1]))
            if 0 <= rx < w and 0 <= ry < h and layout[ry][rx] is None:
                layout[ry][rx] = "road"


def _layout_tower(
    layout: List[List[Optional[str]]],
    cx: int, cy: int,
    w: int, h: int,
    rng: random.Random,
) -> None:
    """Layout a wizard's tower with surrounding wall."""
    # Central tower
    for dy in range(-3, 4):
        for dx in range(-2, 3):
            nx = cx + dx
            ny = cy + dy
            if 0 <= nx < w and 0 <= ny < h:
                if abs(dx) <= 2 and abs(dy) <= 3:
                    layout[ny][nx] = "tower"

    # Small wall around tower
    wall_r = 5
    for x in range(cx - wall_r, cx + wall_r + 1):
        if 0 <= x < w:
            yy1 = cy - wall_r
            yy2 = cy + wall_r
            if 0 <= yy1 < h: layout[yy1][x] = "wall_stone"
            if 0 <= yy2 < h: layout[yy2][x] = "wall_stone"
    for y in range(cy - wall_r, cy + wall_r + 1):
        if 0 <= y < h:
            xx1 = cx - wall_r
            xx2 = cx + wall_r
            if 0 <= xx1 < w: layout[y][xx1] = "wall_stone"
            if 0 <= xx2 < w: layout[y][xx2] = "wall_stone"

    # Gate
    gate_y = cy + wall_r
    if 0 <= gate_y < h and 0 <= cx < w:
        layout[gate_y][cx] = "gate"

    # Outbuildings
    for _ in range(4):
        bx = cx + rng.randint(-wall_r + 2, wall_r - 2)
        by = cy + rng.randint(-wall_r + 2, wall_r - 2)
        if 0 <= bx < w and 0 <= by < h and layout[by][bx] is None:
            layout[by][bx] = "house"


def _layout_shrine(
    layout: List[List[Optional[str]]],
    cx: int, cy: int,
    w: int, h: int,
    rng: random.Random,
) -> None:
    """Layout a shrine/temple complex."""
    # Main shrine building
    for dy in range(-4, 5):
        for dx in range(-3, 4):
            nx = cx + dx
            ny = cy + dy
            if 0 <= nx < w and 0 <= ny < h:
                if abs(dx) <= 3 and abs(dy) <= 4:
                    layout[ny][nx] = "shrine"

    # Outer wall
    wall_r = 7
    for x in range(cx - wall_r, cx + wall_r + 1):
        if 0 <= x < w:
            yy1 = cy - wall_r
            yy2 = cy + wall_r
            if 0 <= yy1 < h: layout[yy1][x] = "wall_stone"
            if 0 <= yy2 < h: layout[yy2][x] = "wall_stone"
    for y in range(cy - wall_r, cy + wall_r + 1):
        if 0 <= y < h:
            xx1 = cx - wall_r
            xx2 = cx + wall_r
            if 0 <= xx1 < w: layout[y][xx1] = "wall_stone"
            if 0 <= xx2 < w: layout[y][xx2] = "wall_stone"

    # Gate
    gate_y = cy + wall_r
    if 0 <= gate_y < h and 0 <= cx < w:
        layout[gate_y][cx] = "gate"

    # Auxiliary buildings (priest quarters, etc.)
    for _ in range(6):
        bx = cx + rng.randint(-wall_r + 2, wall_r - 2)
        by = cy + rng.randint(-wall_r + 2, wall_r - 2)
        if 0 <= bx < w and 0 <= by < h and layout[by][bx] is None:
            layout[by][bx] = "house"


# ──────────────────────────────────────────────────────────────────────
# 4.  RENDERER
# ──────────────────────────────────────────────────────────────────────

def render_poi_local_map(
    world: Dict,
    poi_id: int,
    font_pixels: bytes,
    output_path: str,
    radius: int = 24,
    zoom: int = 1,
    has_color: bool = False,
) -> None:
    """Render a zoomed-in local map around a POI with building layouts."""

    # Find the POI
    sites = world.get("sites", [])
    poi = None
    for s in sites:
        if s["id"] == poi_id:
            poi = s
            break
    if poi is None:
        print(f"ERROR: POI id {poi_id} not found in sites")
        return

    px = poi.get("x", 0)
    py = poi.get("y", 0)
    site_type = poi.get("site_type", "village")
    population = poi.get("population", 50)
    is_capital = poi.get("is_capital", False)
    name = poi.get("name", "Unknown")

    print(f"POI: {name} ({site_type}, id={poi_id})")
    print(f"  Location: ({px}, {py})")
    print(f"  Population: {population}")
    print(f"  Capital: {is_capital}")

    # Extract local terrain
    biome_chunk, elev_chunk, offset_x, offset_y = extract_local_area(
        world, px, py, radius
    )

    local_h = len(biome_chunk)
    local_w = len(biome_chunk[0])

    # Build biome lookup
    biome_defs = {b["id"]: b for b in world.get("biomes", [])}

    # Generate building layout
    rng = random.Random(poi_id * 31337 + hash(name))
    layout = generate_site_layout(site_type, population, is_capital, local_w, local_h, rng)

    # ── Pre-extract glyphs from font ──
    glyph_cache = {}
    def get_glyph(idx: int) -> bytes:
        if idx not in glyph_cache:
            glyph_cache[idx] = get_glyph_pixels(font_pixels, idx)
        return glyph_cache[idx]

    # ── Render ──
    tile_size = GLYPH_SIZE * zoom
    output_w = local_w * tile_size
    output_h = local_h * tile_size
    canvas = bytearray(output_w * output_h * 4)

    print(f"  Local map: {local_w}x{local_h} tiles → {output_w}x{output_h} px")
    print("  Rendering terrain...")

    # Pass 1: Terrain
    for y in range(local_h):
        for x in range(local_w):
            biome_id = biome_chunk[y][x]
            biome = biome_defs.get(biome_id, {})
            biome_name = biome.get("name", "Grassland")

            glyph_idx = TERRAIN_GLYPH_MAP.get(biome_name, FALLBACK_GLYPH)
            color = BIOME_COLORS.get(biome_name, (0x7c, 0x9c, 0x5e))

            glyph = get_glyph(glyph_idx)
            if has_color:
                tile_pixels = glyph
            else:
                tile_pixels = tint_glyph(glyph, color)

            # Place on canvas (with zoom)
            dst_y = y * tile_size
            dst_x = x * tile_size
            if zoom == 1:
                for py in range(GLYPH_SIZE):
                    src_off = py * GLYPH_SIZE * 4
                    dst_off = (dst_y + py) * output_w * 4 + dst_x * 4
                    canvas[dst_off:dst_off + GLYPH_SIZE * 4] = tile_pixels[src_off:src_off + GLYPH_SIZE * 4]
            else:
                # Simple nearest-neighbor zoom
                for py in range(GLYPH_SIZE):
                    for pz in range(zoom):
                        src_row = py * GLYPH_SIZE * 4
                        for px2 in range(GLYPH_SIZE):
                            c = tile_pixels[src_row + px2 * 4: src_row + px2 * 4 + 4]
                            for pzx in range(zoom):
                                ci = (dst_y + py * zoom + pz) * output_w * 4 + (dst_x + px2 * zoom + pzx) * 4
                                canvas[ci:ci+4] = c

    # Pass 2: Elevation shading
    print("  Elevation shading...")
    for y in range(local_h):
        for x in range(local_w):
            elev = elev_chunk[y][x]
            dark = int(elev * 60)

            dst_y = y * tile_size
            dst_x = x * tile_size
            for py in range(tile_size):
                for px in range(tile_size):
                    ci = (dst_y + py) * output_w * 4 + (dst_x + px) * 4
                    r, g, b, a = canvas[ci:ci+4]
                    canvas[ci:ci+4] = (max(0, r - dark), max(0, g - dark), max(0, b - dark), a)

    # Pass 3: Buildings
    print("  Placing buildings...")
    for y in range(local_h):
        for x in range(local_w):
            btype = layout[y][x]
            if btype is None:
                continue

            info = BUILDING_TYPES.get(btype)
            if info is None:
                continue

            glyph = get_glyph(info["glyph"])
            if has_color:
                tinted = glyph
            else:
                tinted = tint_glyph(glyph, info["color"])

            dst_y = y * tile_size
            dst_x = x * tile_size
            if zoom == 1:
                for py in range(GLYPH_SIZE):
                    for px2 in range(GLYPH_SIZE):
                        src_off = py * GLYPH_SIZE * 4 + px2 * 4
                        dst_off = (dst_y + py) * output_w * 4 + (dst_x + px2) * 4

                        or_, og, ob, oa = tinted[src_off:src_off+4]
                        br, bg, bb, ba = canvas[dst_off:dst_off+4]

                        if oa == 0:
                            continue
                        if ba == 0:
                            canvas[dst_off:dst_off+4] = tinted[src_off:src_off+4]
                            continue

                        oa_norm = oa / 255.0
                        ba_norm = ba / 255.0
                        out_a = oa_norm + ba_norm * (1 - oa_norm)
                        if out_a > 0:
                            r = int((or_ * oa_norm + br * ba_norm * (1 - oa_norm)) / out_a) & 0xFF
                            g = int((og * oa_norm + bg * ba_norm * (1 - oa_norm)) / out_a) & 0xFF
                            b = int((ob * oa_norm + bb * ba_norm * (1 - oa_norm)) / out_a) & 0xFF
                            a = int(out_a * 255) & 0xFF
                            canvas[dst_off:dst_off+4] = (r, g, b, a)
            else:
                # Zoomed: just place without per-pixel blending for speed
                for py in range(GLYPH_SIZE):
                    for pz in range(zoom):
                        src_row = py * GLYPH_SIZE * 4
                        for px2 in range(GLYPH_SIZE):
                            c = tinted[src_row + px2 * 4: src_row + px2 * 4 + 4]
                            for pzx in range(zoom):
                                ci = (dst_y + py * zoom + pz) * output_w * 4 + (dst_x + px2 * zoom + pzx) * 4
                                # Simple overwrite for zoomed
                                if c[3] > 0:
                                    canvas[ci:ci+4] = c

    # ── Write output ──
    print(f"  Writing {output_w}x{output_h} PNG...")
    write_png(output_path, output_w, output_h, bytes(canvas))
    print(f"  Done → {output_path}")


# ──────────────────────────────────────────────────────────────────────
# 5.  COMMAND LINE
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Render a zoomed-in local map around a POI using bitmap font")
    parser.add_argument("--world", type=str, required=True, help="Path to world JSON")
    parser.add_argument("--poi-id", type=int, required=True, help="POI (site) ID to zoom into")
    parser.add_argument("--font", type=str, default="/tmp/default_font.png", help="Path to bitmap font PNG")
    parser.add_argument("--output", type=str, default="/tmp/poi_map.png", help="Output PNG path")
    parser.add_argument("--radius", type=int, default=24, help="Local map radius in tiles (default 24 = 49x49)")
    parser.add_argument("--zoom", type=int, default=1, help="Zoom factor (2 = 2x larger tiles)")
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
    render_poi_local_map(world, args.poi_id, font_pixels, args.output,
                         radius=args.radius, zoom=args.zoom, has_color=args.has_color)
