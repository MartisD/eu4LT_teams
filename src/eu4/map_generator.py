"""
High-performance EU4 WebGL2 World Map Generator.
Converts provinces.bmp into an optimized province-ID indexed texture,
generates a dynamic multi-mode color palette texture, and provides
instantaneous GPU-accelerated interactive map rendering.
"""
from __future__ import annotations

import os
import io
import base64
import json
from collections import defaultdict
from typing import Any
import colorsys
from PIL import Image

# Team definitions
TEAM_1_TAGS = {"SPA", "FRA", "HAB", "MUG", "BUR"}
TEAM_2_TAGS = {"MLC", "RUS", "BAH", "QNG"}

# Palette for tournament player nations and their subjects
COUNTRY_COLORS: dict[str, tuple[int, int, int]] = {
    "SPA": (234, 179, 8),     # Spain Gold (#eab308)
    "FRA": (37, 99, 235),     # France Royal Blue (#2563eb)
    "HAB": (252, 252, 254),   # Austria Imperial White (#fcfcfe)
    "MUG": (16, 185, 129),    # Mughals Emerald Green (#10b981)
    "MLC": (162, 28, 175),    # Malacca Purple (#a21caf)
    "RUS": (180, 50, 95),     # Russia Plum / Berry Wine (#b4325f)
    "BAH": (14, 165, 233),    # Bahmanis Sky Cyan (#0ea5e9)
    "QNG": (245, 158, 11),    # Qing Amber Gold (#f59e0b)
    "BUR": (159, 18, 57),     # Burgundy Crimson Wine (#9f1239)
    "C00": (217, 119, 6),     # Colonial Nations / Subjects
    "C01": (217, 119, 6),
    "C02": (217, 119, 6),
    "C03": (217, 119, 6),
    "NAP": (202, 138, 4),
    "TLC": (180, 140, 60),
    "JOL": (180, 140, 60),
    "TMB": (180, 140, 60),
    "PTG": (34, 197, 94),
}

# Historical palette for prominent global AI nations
COMMON_TAG_COLORS: dict[str, tuple[int, int, int]] = {
    "TUR": (22, 163, 74),     # Ottomans Turkish Green (#16a34a)
    "ENG": (220, 38, 38),     # England Red (#dc2626)
    "GBR": (220, 38, 38),     # Great Britain Red (#dc2626)
    "MNG": (220, 38, 38),     # Ming Red
    "POL": (225, 29, 72),     # Poland Soft Crimson (#e11d48)
    "PLC": (225, 29, 72),     # Commonwealth
    "SWE": (37, 99, 235),     # Sweden Blue
    "DAN": (185, 28, 28),     # Denmark Red
    "NOR": (14, 116, 185),    # Norway Blue
    "POR": (34, 197, 94),     # Portugal Green
    "CAS": (234, 179, 8),     # Castille Yellow
    "ARA": (217, 119, 6),     # Aragon Orange
    "MOS": (180, 50, 95),     # Muscovy Plum
    "NOV": (16, 185, 129),    # Novgorod Green
    "VEN": (202, 138, 4),     # Venice Gold
    "GEN": (203, 213, 225),   # Genoa White
    "PAP": (248, 250, 252),   # Papal State White
    "MAM": (245, 158, 11),    # Mamluks Saffron Yellow
    "PER": (5, 150, 105),     # Persia Emerald
    "TIM": (180, 83, 9),      # Timurids Red/Brown
    "VIJ": (234, 88, 12),     # Vijayanagar Orange
    "KIL": (217, 119, 6),     # Kilwa Orange
    "AYU": (59, 130, 246),    # Ayutthaya Blue
    "KOR": (56, 189, 248),    # Korea Sky Blue
    "JAP": (220, 38, 38),     # Japan Red
    "ETH": (234, 179, 8),     # Ethiopia Yellow
    "SON": (217, 119, 6),     # Songhai Orange
    "MOR": (245, 158, 11),    # Morocco Orange
    "TUN": (180, 83, 9),      # Tunis Brown
    "HUN": (185, 28, 28),     # Hungary Red
    "BOH": (234, 88, 12),     # Bohemia Orange
    "BRA": (148, 163, 184),   # Brandenburg Grey
    "PRU": (51, 65, 85),      # Prussia Dark Slate
    "SAX": (100, 116, 139),   # Saxony Slate
    "BAV": (56, 189, 248),    # Bavaria Light Blue
    "SCO": (234, 179, 8),     # Scotland Yellow
    "IRE": (22, 163, 74),     # Ireland Green
}

def get_country_color(tag: str) -> tuple[int, int, int]:
    if not tag:
        return UNCOLONIZED_COLOR
    if tag in COUNTRY_COLORS:
        return COUNTRY_COLORS[tag]
    if tag in COMMON_TAG_COLORS:
        return COMMON_TAG_COLORS[tag]
    if len(tag) == 3:
        h = (ord(tag[0]) * 47 + ord(tag[1]) * 29 + ord(tag[2]) * 13) % 360
        r, g, b = colorsys.hls_to_rgb(h / 360.0, 0.48, 0.55)
        return (int(r * 255), int(g * 255), int(b * 255))
    return (80, 90, 105)

# Styling palette
TEAM_1_COLOR = (37, 99, 235)       # Team 1 Blue (#2563eb)
TEAM_2_COLOR = (239, 68, 68)       # Team 2 Red (#ef4444)

SEA_COLOR = (76, 115, 158)         # Classic EU4 Ocean (#4c739e)
WASTELAND_COLOR = (75, 85, 99)     # Impassable Mountains & Wastelands (#4b5563)
UNCOLONIZED_COLOR = (42, 52, 68)   # Clean Dark Slate (#2a3444)

COASTAL_COLOR = (14, 165, 233)     # Cyan (#0ea5e9)
NAVAL_SUPPLIES_COLOR = (245, 158, 11) # Amber Gold (#f59e0b)
INLAND_COLOR = (35, 45, 60)        # Dark Slate (#232d3c)

def dev_to_heatmap_color(dev: float) -> tuple[int, int, int]:
    """Continuous smooth heatmap interpolation for province development."""
    if dev <= 0:
        return (32, 42, 58)
    keyframes = [
        (1.0,  (24, 45, 82)),    # Deep cool slate blue (Low dev)
        (6.0,  (14, 116, 185)),  # Ocean Blue
        (12.0, (14, 165, 233)),  # Vivid Cyan / Teal (Moderate dev)
        (18.0, (34, 197, 94)),   # Emerald Green
        (26.0, (234, 179, 8)),   # Radiant Gold / Amber (Prosperous)
        (36.0, (249, 115, 22)),  # Hot Orange (Metropolis)
        (48.0, (239, 68, 68)),   # Intense Red (Major Capital)
        (65.0, (244, 63, 94)),   # Neon Magenta / Rose (Mega-city)
    ]
    if dev <= keyframes[0][0]:
        return keyframes[0][1]
    if dev >= keyframes[-1][0]:
        return keyframes[-1][1]
    for i in range(len(keyframes) - 1):
        v0, c0 = keyframes[i]
        v1, c1 = keyframes[i + 1]
        if v0 <= dev <= v1:
            t = (dev - v0) / (v1 - v0)
            t_smooth = t * t * (3.0 - 2.0 * t)
            return (
                int(c0[0] + (c1[0] - c0[0]) * t_smooth),
                int(c0[1] + (c1[1] - c0[1]) * t_smooth),
                int(c0[2] + (c1[2] - c0[2]) * t_smooth),
            )
    return keyframes[-1][1]

def casualty_to_heatmap_color(casualties: int) -> tuple[int, int, int]:
    """Continuous smooth heatmap interpolation for battle casualties."""
    if casualties <= 0:
        return (30, 41, 59)
    keyframes = [
        (1,       (30, 58, 138)),   # Navy blue
        (2500,    (14, 116, 185)),  # Blue
        (7500,    (16, 185, 129)),  # Emerald Green
        (20000,   (234, 179, 8)),   # Gold
        (50000,   (249, 115, 22)),  # Orange
        (100000,  (239, 68, 68)),   # Crimson Red
        (200000,  (225, 29, 72)),   # Rose / Magenta
        (350000,  (217, 70, 239)),  # Violet Bloodbath
    ]
    if casualties <= keyframes[0][0]:
        return keyframes[0][1]
    if casualties >= keyframes[-1][0]:
        return keyframes[-1][1]
    for i in range(len(keyframes) - 1):
        v0, c0 = keyframes[i]
        v1, c1 = keyframes[i + 1]
        if v0 <= casualties <= v1:
            t = (casualties - v0) / (v1 - v0)
            t_smooth = t * t * (3.0 - 2.0 * t)
            return (
                int(c0[0] + (c1[0] - c0[0]) * t_smooth),
                int(c0[1] + (c1[1] - c0[1]) * t_smooth),
                int(c0[2] + (c1[2] - c0[2]) * t_smooth),
            )
    return keyframes[-1][1]


def generate_interactive_map_data(
    bmp_path: str,
    def_path: str,
    sea_provinces: set[int],
    coastal_provinces: set[int],
    prov_data: dict[int, dict[str, Any]],
    tag_to_player: dict[str, str],
    wasteland_provinces: set[int] | None = None,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """
    Renders GPU WebGL2 textures and dynamic palette lookups for the interactive map.
    """
    if wasteland_provinces is None:
        wasteland_provinces = set()

    rgb_to_pid: dict[tuple[int, int, int], int] = {}
    max_pid = 1
    if os.path.isfile(def_path):
        with open(def_path, "r", encoding="latin-1") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(";")
                if len(parts) >= 4:
                    try:
                        pid = int(parts[0])
                        r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
                        rgb_to_pid[(r, g, b)] = pid
                        if pid > max_pid:
                            max_pid = pid
                    except ValueError:
                        pass

    if not os.path.isfile(bmp_path):
        return {}

    im = Image.open(bmp_path).convert("RGB")
    width, height = im.size
    rgb_data = im.tobytes()

    # Generate provinces_id RGBA buffer
    # R: pid & 0xFF
    # G: (pid >> 8) & 0xFF
    # B: 255 if is_sea else 0
    # A: 128 if is_wasteland else 255
    total_pixels = width * height
    id_bytes = bytearray(total_pixels * 4)

    for i in range(total_pixels):
        r = rgb_data[i * 3]
        g = rgb_data[i * 3 + 1]
        b = rgb_data[i * 3 + 2]
        pid = rgb_to_pid.get((r, g, b), 0)
        
        is_sea = pid in sea_provinces
        is_waste = pid in wasteland_provinces

        idx = i * 4
        id_bytes[idx] = pid & 0xFF
        id_bytes[idx + 1] = (pid >> 8) & 0xFF
        id_bytes[idx + 2] = 255 if is_sea else 0
        id_bytes[idx + 3] = 128 if is_waste else 255

    id_img = Image.frombytes("RGBA", (width, height), bytes(id_bytes))

    # Save provinces_id.png to output directory if specified
    if output_dir:
        out_png_path = os.path.join(output_dir, "provinces_id.png")
        try:
            id_img.save(out_png_path, optimize=True)
        except Exception:
            pass

    # Encode to base64 Data URL for zero-CORS direct local loading
    buf = io.BytesIO()
    id_img.save(buf, format="PNG", optimize=True)
    prov_id_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    # Build multi-mode color palette texture (width = 2048, height = 6 * rows_per_mode)
    # Using a 2048-wide grid guarantees 100% compatibility across all mobile & desktop GPUs
    # (many mobile GPUs have MAX_TEXTURE_SIZE = 4096 or 2048)
    pal_w = 2048
    rows_per_mode = (max_pid + pal_w) // pal_w
    pal_h = 6 * rows_per_mode
    pal_bytes = bytearray(pal_w * pal_h * 4)

    client_provinces: dict[int, dict[str, Any]] = {}

    for pid in range(1, max_pid + 1):
        col = pid % pal_w
        row_in_mode = pid // pal_w

        is_sea = pid in sea_provinces
        is_waste = pid in wasteland_provinces

        pinfo = prov_data.get(pid, {})
        owner = pinfo.get("owner", "")
        player = tag_to_player.get(owner, "")
        team = pinfo.get("team", "")

        if not is_sea:
            client_provinces[pid] = {
                "name": pinfo.get("name", f"Province {pid}"),
                "owner": owner,
                "player": player,
                "dev": pinfo.get("dev", 0),
                "coastal": pid in coastal_provinces,
                "tg": pinfo.get("tg", ""),
                "aut": pinfo.get("aut", 0),
                "tc": bool(pinfo.get("tc")),
                "cas": pinfo.get("casualties", 0),
                "bat": pinfo.get("battles", 0),
                "top_b": pinfo.get("top_battle", ""),
            }

        # Calculate colors for all 6 modes
        if is_sea:
            c_players = c_pol = c_teams = c_naval = c_dev = c_cas = SEA_COLOR
        elif is_waste:
            c_players = c_pol = c_teams = c_naval = c_dev = c_cas = WASTELAND_COLOR
        else:
            # 1. Players
            if owner in COUNTRY_COLORS:
                c_players = COUNTRY_COLORS[owner]
            else:
                c_players = UNCOLONIZED_COLOR

            # 2. Political
            c_pol = get_country_color(owner)

            # 3. Teams
            if owner in TEAM_1_TAGS or team == "TEAM_1":
                c_teams = TEAM_1_COLOR
            elif owner in TEAM_2_TAGS or team == "TEAM_2":
                c_teams = TEAM_2_COLOR
            else:
                c_teams = UNCOLONIZED_COLOR

            # 4. Naval
            if pid in coastal_provinces:
                if pinfo.get("tg") == "naval_supplies":
                    c_naval = NAVAL_SUPPLIES_COLOR
                elif pinfo.get("has_shipyard"):
                    c_naval = (56, 189, 248)
                else:
                    c_naval = COASTAL_COLOR
            else:
                c_naval = INLAND_COLOR

            # 5. Dev
            c_dev = dev_to_heatmap_color(pinfo.get("dev", 0))

            # 6. Casualties
            c_cas = casualty_to_heatmap_color(pinfo.get("casualties", 0))

        mode_colors_list = [c_players, c_pol, c_teams, c_naval, c_dev, c_cas]
        for mode_idx, col_rgb in enumerate(mode_colors_list):
            row = mode_idx * rows_per_mode + row_in_mode
            idx = (row * pal_w + col) * 4
            pal_bytes[idx] = col_rgb[0]
            pal_bytes[idx + 1] = col_rgb[1]
            pal_bytes[idx + 2] = col_rgb[2]
            pal_bytes[idx + 3] = 0 if is_sea else (128 if is_waste else 255)

    pal_img = Image.frombytes("RGBA", (pal_w, pal_h), bytes(pal_bytes))
    pal_buf = io.BytesIO()
    pal_img.save(pal_buf, format="PNG")
    palette_b64 = "data:image/png;base64," + base64.b64encode(pal_buf.getvalue()).decode("ascii")

    return {
        "width": width,
        "height": height,
        "palette_width": pal_w,
        "palette_height": pal_h,
        "rows_per_mode": rows_per_mode,
        "provinces_id_b64": prov_id_b64,
        "palette_b64": palette_b64,
        "provinces_json": json.dumps(client_provinces),
        "total_provinces": len(client_provinces),
    }
