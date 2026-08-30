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
    "CAS": (234, 179, 8),
    "ARA": (217, 119, 6),
    "FRA": (37, 99, 235),     # France Royal Blue (#2563eb)
    "BUR": (159, 18, 57),     # Burgundy Crimson Wine (#9f1239)
    "HAB": (252, 252, 254),   # Austria Imperial White (#fcfcfe)
    "MUG": (16, 185, 129),    # Mughals Emerald Green (#10b981)
    "TIM": (16, 185, 129),
    "QOM": (16, 185, 129),
    "MLC": (162, 28, 175),    # Malacca Purple (#a21caf)
    "MAY": (162, 28, 175),
    "RUS": (180, 50, 95),     # Russia Plum / Berry Wine (#b4325f)
    "NOV": (180, 50, 95),
    "MOS": (180, 50, 95),
    "BAH": (14, 165, 233),    # Bahmanis Sky Cyan (#0ea5e9)
    "SKE": (14, 165, 233),    # Sikh Empire Sky Cyan (#0ea5e9)
    "DEC": (14, 165, 233),
    "HND": (14, 165, 233),
    "PUN": (14, 165, 233),
    "BHA": (14, 165, 233),
    "QNG": (245, 158, 11),    # Qing Amber Gold (#f59e0b)
    "MCH": (245, 158, 11),
    "MHX": (245, 158, 11),
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

def dev_heatmap_color(t: float) -> tuple[int, int, int]:
    """Development gradient: Red (lowest dev, t=0.0) -> Yellow (medium) -> Green (highest dev, t=1.0)."""
    t = max(0.0, min(1.0, float(t)))
    keyframes = [
        (0.00, (239, 68, 68)),   # Lowest Dev: Crimson Red (#ef4444)
        (0.35, (249, 115, 22)),  # Mid-Low: Orange (#f97316)
        (0.65, (234, 179, 8)),   # Mid-High: Gold / Lime (#eab308)
        (1.00, (34, 197, 94)),   # Highest Dev: Emerald Green (#22c55e)
    ]
    for i in range(len(keyframes) - 1):
        v0, c0 = keyframes[i]
        v1, c1 = keyframes[i + 1]
        if v0 <= t <= v1:
            local_t = (t - v0) / (v1 - v0)
            smooth_t = local_t * local_t * (3.0 - 2.0 * local_t)
            r = int(c0[0] + (c1[0] - c0[0]) * smooth_t)
            g = int(c0[1] + (c1[1] - c0[1]) * smooth_t)
            b = int(c0[2] + (c1[2] - c0[2]) * smooth_t)
            return (r, g, b)
    return keyframes[-1][1]


def casualties_heatmap_color(t: float) -> tuple[int, int, int]:
    """Casualties gradient: Baseline unowned grey (42, 52, 68) -> Team 2 Red (239, 68, 68)."""
    t = max(0.0, min(1.0, float(t)))
    smooth_t = t * t * (3.0 - 2.0 * t)
    r = int(UNCOLONIZED_COLOR[0] + (TEAM_2_COLOR[0] - UNCOLONIZED_COLOR[0]) * smooth_t)
    g = int(UNCOLONIZED_COLOR[1] + (TEAM_2_COLOR[1] - UNCOLONIZED_COLOR[1]) * smooth_t)
    b = int(UNCOLONIZED_COLOR[2] + (TEAM_2_COLOR[2] - UNCOLONIZED_COLOR[2]) * smooth_t)
    return (r, g, b)


def devastation_heatmap_color(t: float) -> tuple[int, int, int]:
    """Devastation gradient: 0% unowned grey (42, 52, 68) -> 100% Team 2 Red (239, 68, 68)."""
    t = max(0.0, min(1.0, float(t)))
    smooth_t = t * t * (3.0 - 2.0 * t)
    r = int(UNCOLONIZED_COLOR[0] + (TEAM_2_COLOR[0] - UNCOLONIZED_COLOR[0]) * smooth_t)
    g = int(UNCOLONIZED_COLOR[1] + (TEAM_2_COLOR[1] - UNCOLONIZED_COLOR[1]) * smooth_t)
    b = int(UNCOLONIZED_COLOR[2] + (TEAM_2_COLOR[2] - UNCOLONIZED_COLOR[2]) * smooth_t)
    return (r, g, b)


def prosperity_color(t: float) -> tuple[int, int, int]:
    """Prosperity color: Lush Emerald Green (#22c55e) with intensity modulation."""
    t = max(0.0, min(1.0, float(t)))
    return (
        int(16 + (34 - 16) * t),
        int(160 + (197 - 160) * t),
        int(80 + (94 - 80) * t),
    )


def generate_interactive_map_data(
    bmp_path: str,
    def_path: str,
    sea_provinces: set[int],
    coastal_provinces: set[int],
    prov_data: dict[int, dict[str, Any]],
    tag_to_player: dict[str, str],
    wasteland_provinces: set[int] | None = None,
    subject_to_overlord: dict[str, str] | None = None,
    team1_tags: set[str] | None = None,
    team2_tags: set[str] | None = None,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """
    Renders GPU WebGL2 textures and dynamic palette lookups for the interactive map.
    """
    if wasteland_provinces is None:
        wasteland_provinces = set()
    if subject_to_overlord is None:
        subject_to_overlord = {}
    if team1_tags is None:
        team1_tags = TEAM_1_TAGS
    if team2_tags is None:
        team2_tags = TEAM_2_TAGS

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
    # B: 0
    # A: 255 (Always 255 to prevent HTML5 canvas premultiplied alpha color corruption)
    total_pixels = width * height
    id_bytes = bytearray(total_pixels * 4)

    for i in range(total_pixels):
        r = rgb_data[i * 3]
        g = rgb_data[i * 3 + 1]
        b = rgb_data[i * 3 + 2]
        pid = rgb_to_pid.get((r, g, b), 0)

        idx = i * 4
        id_bytes[idx] = pid & 0xFF
        id_bytes[idx + 1] = (pid >> 8) & 0xFF
        id_bytes[idx + 2] = 0
        id_bytes[idx + 3] = 255

    id_img = Image.frombytes("RGBA", (width, height), bytes(id_bytes))

    # Save provinces_id.png to output directory if specified
    if output_dir:
        out_png_path = os.path.join(output_dir, "provinces_id.png")
        try:
            id_img.save(out_png_path, optimize=True)
        except Exception:
            pass

    # Encode full and split West/East halves (2816x2048 each) to guarantee 100% full-resolution on all mobile devices
    half_w = width // 2
    west_img = id_img.crop((0, 0, half_w, height))
    east_img = id_img.crop((half_w, 0, width, height))

    buf = io.BytesIO()
    id_img.save(buf, format="PNG", optimize=True)
    prov_id_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    buf_w = io.BytesIO()
    west_img.save(buf_w, format="PNG", optimize=True)
    prov_id_west_b64 = "data:image/png;base64," + base64.b64encode(buf_w.getvalue()).decode("ascii")

    buf_e = io.BytesIO()
    east_img.save(buf_e, format="PNG", optimize=True)
    prov_id_east_b64 = "data:image/png;base64," + base64.b64encode(buf_e.getvalue()).decode("ascii")

    # Dynamic dev and casualty min/max normalization across active land provinces
    valid_devs = [pinfo.get("dev", 0) for pid, pinfo in prov_data.items() if pid not in sea_provinces and pid not in wasteland_provinces and pinfo.get("dev", 0) > 0]
    min_dev = min(valid_devs) if valid_devs else 1.0
    max_dev = max(valid_devs) if valid_devs else 30.0

    valid_cas = [pinfo.get("casualties", 0) for pid, pinfo in prov_data.items() if pid not in sea_provinces and pid not in wasteland_provinces and pinfo.get("casualties", 0) > 0]
    min_cas = min(valid_cas) if valid_cas else 1
    max_cas = max(valid_cas) if valid_cas else 100000

    # Build multi-mode color palette texture (width = 2048, height = 6 * rows_per_mode)
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

        if is_waste:
            client_provinces[pid] = {
                "name": pinfo.get("name", f"Wasteland #{pid}"),
                "waste": True,
            }
        elif not is_sea:
            entry = {"name": pinfo.get("name", f"Province #{pid}")}
            if owner:
                entry["owner"] = owner
            if player:
                entry["player"] = player
            pdev = pinfo.get("dev", 0)
            if pdev:
                entry["dev"] = pdev
            if pid in coastal_provinces:
                entry["coastal"] = 1
            ptg = pinfo.get("tg", "")
            if ptg:
                entry["tg"] = ptg
            paut = pinfo.get("aut", 0)
            if paut:
                entry["aut"] = paut
            if pinfo.get("tc"):
                entry["tc"] = 1
            pcas = pinfo.get("casualties", 0)
            if pcas:
                entry["cas"] = pcas
            pbat = pinfo.get("battles", 0)
            if pbat:
                entry["bat"] = pbat
            pdeva = pinfo.get("devastation", 0.0)
            if pdeva:
                entry["devastation"] = pdeva
            ppros = pinfo.get("prosperity", 0.0)
            if ppros:
                entry["prosperity"] = ppros
            parea = pinfo.get("area", "")
            if parea:
                entry["area"] = parea
            client_provinces[pid] = entry

        # Calculate colors for all 6 modes
        if is_sea:
            c_players = c_pol = c_teams = c_devastation = c_dev = c_cas = SEA_COLOR
        elif is_waste:
            c_players = c_pol = c_teams = c_devastation = c_dev = c_cas = WASTELAND_COLOR
        else:
            # 1. Players (includes subjects with a lighter tint of the overlord)
            if owner in COUNTRY_COLORS:
                c_players = COUNTRY_COLORS[owner]
            elif owner in subject_to_overlord and subject_to_overlord[owner] in COUNTRY_COLORS:
                ol_tag = subject_to_overlord[owner]
                ol_rgb = COUNTRY_COLORS[ol_tag]
                # Lighter pastel tint of the overlord (60% overlord + 40% white)
                c_players = (
                    int(ol_rgb[0] * 0.60 + 255 * 0.40),
                    int(ol_rgb[1] * 0.60 + 255 * 0.40),
                    int(ol_rgb[2] * 0.60 + 255 * 0.40),
                )
            else:
                c_players = UNCOLONIZED_COLOR

            # 2. Political
            c_pol = get_country_color(owner)

            # 3. Teams
            if owner in team1_tags or team == "TEAM_1":
                c_teams = TEAM_1_COLOR
            elif owner in team2_tags or team == "TEAM_2":
                c_teams = TEAM_2_COLOR
            else:
                c_teams = UNCOLONIZED_COLOR

            # 4. Devastation & Prosperity Mode
            p_deva = pinfo.get("devastation", 0.0)
            p_pros = pinfo.get("prosperity", 0.0)
            if p_deva > 0.0:
                t_deva = min(1.0, max(0.0, p_deva / 100.0))
                c_devastation = devastation_heatmap_color(t_deva)
            elif p_pros > 0.0:
                t_pros = min(1.0, max(0.0, p_pros / 100.0))
                c_devastation = prosperity_color(t_pros)
            else:
                c_devastation = (55, 65, 81)  # Neutral #374151 Slate Grey

            # 5. Development Heatmap (Red = Lowest Dev -> Yellow -> Green = Highest Dev)
            pdev = pinfo.get("dev", 0)
            t_dev = (pdev - min_dev) / max(max_dev - min_dev, 1.0)
            c_dev = dev_heatmap_color(t_dev)

            # 6. Casualties Heatmap (Slate Grey = None/Lowest -> Amber -> Deep Crimson = Highest)
            pcas = pinfo.get("casualties", 0)
            if pcas <= 0:
                c_cas = (55, 65, 81)  # Slate Grey
            else:
                t_cas = (pcas - min_cas) / max(max_cas - min_cas, 1)
                c_cas = casualties_heatmap_color(t_cas)

        mode_colors_list = [c_players, c_pol, c_teams, c_devastation, c_dev, c_cas]
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
        "half_width": half_w,
        "palette_width": pal_w,
        "palette_height": pal_h,
        "rows_per_mode": rows_per_mode,
        "provinces_id_b64": prov_id_b64,
        "provinces_id_west_b64": prov_id_west_b64,
        "provinces_id_east_b64": prov_id_east_b64,
        "palette_b64": palette_b64,
        "provinces_json": json.dumps(client_provinces, separators=(',', ':')),
        "total_provinces": len(client_provinces),
        "min_dev": round(min_dev, 1),
        "max_dev": round(max_dev, 1),
        "min_cas": min_cas,
        "max_cas": max_cas,
    }
