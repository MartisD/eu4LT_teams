#!/usr/bin/env python3
"""
EU4 tournament score calculator (Team Matchup System).

Usage:
  python src/score_calculator.py <save.eu4>
  python src/score_calculator.py <save.eu4> <eu4_install_dir>
  python src/score_calculator.py <save.eu4> <eu4_install_dir> <mod_dir>
"""
from __future__ import annotations

import os
import sys
import re
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))

from jinja2 import Environment, FileSystemLoader

from eu4.save_reader import CountryData, enrich_with_historical_ideas, load_save_full
from eu4.game_data import GameData, load_game_data
from eu4.map_generator import generate_interactive_map_data
from eu4.modifier_engine import ModifierEngine
from eu4.calculators.military import (
    calc_artillery_combat_ability,
    calc_army_force_limit,
    calc_cavalry_combat_ability,
    calc_discipline,
    calc_fire_damage,
    calc_galley_combat_ability,
    calc_heavy_ship_combat_ability,
    calc_infantry_combat_ability,
    calc_manpower_recovery_speed,
    calc_morale_armies,
    calc_naval_force_limit,
    calc_naval_morale,
    calc_shock_damage,
    calc_siege_ability,
)
from eu4.calculators.economy import (
    calc_all_power_cost,
    calc_core_creation_cost,
    calc_development_cost,
    calc_goods_produced,
    calc_governing_capacity,
    calc_production_efficiency,
    calc_tax_efficiency,
    calc_trade_efficiency,
)
from eu4.calculators.diplomatic import (
    calc_ae_impact,
    calc_diplomatic_reputation,
    calc_diplomatic_slots,
    calc_improve_relations,
    calc_max_absolutism,
)

# ── Role and Team Configuration ───────────────────────────────────────────────

TEAM_1_NAME = "METAFILU KOMANDA"
TEAM_2_NAME = "METASVYDO KOMANDA"

TEAM_1_ROLES = {
    "SPA": "NAVAL",
    "FRA": "QUANTITY",
    "HAB": "QUALITY",
    "MUG": "BLOB",
}

TEAM_2_ROLES = {
    "MLC": "NAVAL",
    "RUS": "QUANTITY",
    "BAH": "QUALITY",
    "QNG": "BLOB",
}

TAG_TO_TEAM_ROLE = {}
for tag, role in TEAM_1_ROLES.items():
    TAG_TO_TEAM_ROLE[tag] = (TEAM_1_NAME, role)
for tag, role in TEAM_2_ROLES.items():
    TAG_TO_TEAM_ROLE[tag] = (TEAM_2_NAME, role)


# ── Metric Definitions per Role ───────────────────────────────────────────────
# (metric_key, display_name, points, format_type, is_hegemon_rule, is_approx)
# format_type: 'int', 'float1', 'float2', 'pct1', 'pct2', 'bool'

ROLE_METRICS_SPEC = {
    "NAVAL": [
        ("naval_force_limit", "Naval Force Limit", 2, "float1", False, False),
        ("heavy_ships", "Heavy Ships count", 1, "int", False, False),
        ("trade_ships", "Trade ships count", 1, "int", False, False),
        ("naval_morale", "Naval Morale", 1, "float3", False, False),
        ("colonial_nations_count", "Colonial nations count", 3, "int", False, False),
        ("galley_ca", "Galley combat ability", 1, "pct1", False, False),
        ("colonial_regions_controlled", "Fully controlled colonial regions", 2, "int", False, False),
        ("is_naval_hegemon", "Naval hegemon", 5, "bool", True, False),
    ],
    "QUANTITY": [
        ("max_manpower", "Max manpower", 2, "manpower", False, False),
        ("army_force_limit", "Land Force limit", 1, "float1", False, False),
        ("production_efficiency", "Production efficiency", 1, "pct1", False, False),
        ("goods_produced", "Goods produced modifier", 2, "pct1", False, False),
        ("is_mil_hegemon", "Mil hegemon", 5, "bool", True, False),
        ("diplo_capacity", "Diplo capacity", 1, "int", False, False),
    ],
    "QUALITY": [
        ("morale_armies", "Land Morale", 2, "float3", False, False),
        ("discipline", "Discipline", 2, "discipline", False, False),
        ("infantry_ca", "ICA (Infantry CA)", 1, "pct1", False, False),
        ("cavalry_ca", "CCA (Cavalry CA)", 1, "pct1", False, False),
        ("artillery_ca", "ACA (Artillery CA)", 2, "pct1", False, False),
        ("trade_efficiency", "Trade efficiency", 2, "pct1", False, False),
    ],
    "BLOB": [
        ("province_count", "Province count", 2, "int", False, False),
        ("development", "Dev amount controlled", 2, "int", False, False),
        ("dev_clicks", "Dev clicks count", 2, "int", False, False),
        ("diplo_reputation", "Diplo rep", 1, "float1", False, False),
        ("personal_unions_count", "Personal unions count", 1, "int", False, False),
        ("governing_capacity", "Gov cap (available)", 1, "float1", False, False),
        ("is_economy_hegemon", "Economy hegemon", 5, "bool", True, False),
    ],
}


_COMPUTED_STATS: list[tuple[str, object]] = [
    ("discipline",              calc_discipline),
    ("morale_armies",           calc_morale_armies),
    ("army_force_limit",        calc_army_force_limit),
    ("naval_force_limit",       calc_naval_force_limit),
    ("manpower_recovery",       calc_manpower_recovery_speed),
    ("infantry_ca",             calc_infantry_combat_ability),
    ("cavalry_ca",              calc_cavalry_combat_ability),
    ("artillery_ca",            calc_artillery_combat_ability),
    ("fire_damage",             calc_fire_damage),
    ("shock_damage",            calc_shock_damage),
    ("galley_ca",               calc_galley_combat_ability),
    ("heavy_ship_ca",           calc_heavy_ship_combat_ability),
    ("siege_ability",           calc_siege_ability),
    ("naval_morale",            calc_naval_morale),
    ("production_efficiency",   calc_production_efficiency),
    ("goods_produced",          calc_goods_produced),
    ("trade_efficiency",        calc_trade_efficiency),
    ("tax_efficiency",          calc_tax_efficiency),
    ("dev_cost",                calc_development_cost),
    ("core_creation_cost",      calc_core_creation_cost),
    ("all_power_cost",          calc_all_power_cost),
    ("diplo_reputation",        calc_diplomatic_reputation),
    ("diplo_slots_bonus",       calc_diplomatic_slots),
    ("ae_impact",               calc_ae_impact),
    ("improve_relations",       calc_improve_relations),
    ("max_absolutism",          calc_max_absolutism),
    ("governing_capacity",      calc_governing_capacity),
]


def _format_val(val: Any, fmt: str) -> str:
    if val is None:
        return "N/A"
    if fmt == "int":
        return f"{int(round(val)):,}"
    elif fmt == "float1":
        return f"{float(val):.1f}"
    elif fmt == "float2":
        return f"{float(val):.2f}"
    elif fmt == "float3":
        return f"{float(val):.3f}"
    elif fmt == "pct1":
        pct = float(val) * 100.0
        return f"+{pct:.1f}%" if pct > 0 else f"{pct:.1f}%"
    elif fmt == "discipline":
        pct = float(val) * 100.0
        return f"{pct:.2f}%"
    elif fmt == "manpower":
        # Max manpower is stored in thousands in save (e.g. 237.784 -> 237,784)
        mp = float(val) * 1000.0
        return f"{int(round(mp)):,}"
    elif fmt == "bool":
        return "Claimed" if val else "No"
    return str(val)


def _process_country_data(cd: CountryData, game_data: GameData | None, most_dev_tag: str) -> dict:
    team_role = TAG_TO_TEAM_ROLE.get(cd.tag)
    team = team_role[0] if team_role else "Other"
    role = team_role[1] if team_role else "NONE"

    d: dict = {
        "tag":                    cd.tag,
        "player":                 cd.player,
        "team":                   team,
        "role":                   role,
        "development":            cd.raw_development,
        "starting_development":   cd.starting_development,
        "army_tradition":         cd.army_tradition,
        "navy_tradition":         cd.navy_tradition,
        "max_manpower":           cd.max_manpower,
        "absolutism":             cd.absolutism,
        "army_professionalism":   cd.army_professionalism,
        "religion":               cd.religion,
        "active_idea_groups":     cd.active_idea_groups,
        "historical_idea_groups": cd.historical_idea_groups,
        "government_type":        cd.government_type,
        "government_reforms":     cd.government_reforms,
        # New tournament fields:
        "heavy_ships":            cd.heavy_ships,
        "trade_ships":            cd.trade_ships,
        "galley_ships":           cd.galley_ships,
        "transport_ships":        cd.transport_ships,
        "total_ships":            cd.heavy_ships + cd.trade_ships + cd.galley_ships + cd.transport_ships,
        "colonial_nations_count": cd.colonial_nations_count,
        "personal_unions_count":  cd.personal_unions_count,
        "colonial_regions_controlled": cd.colonial_regions_controlled,
        "province_count":         cd.province_count,
        "dev_clicks":             cd.dev_clicks,
        "used_governing_capacity": cd.used_governing_capacity,
        "hegemony":               cd.hegemony,
        "is_naval_hegemon":       cd.hegemony.lower() == "naval",
        "is_mil_hegemon":         cd.hegemony.lower() in ("mil", "military"),
        "is_economy_hegemon":     cd.hegemony.lower() in ("economy", "economic"),
        "active_policies":        cd.active_policies,
        "estate_privileges":      cd.estate_privileges,
        "estates":                [{"name": e[0].replace("estate_", "").replace("_", " ").title(), "loyalty": e[1], "territory": e[2]} for e in cd.estates],
        "monarch_personalities":  cd.monarch_personalities,
        "adm_tech":               cd.adm_tech,
        "dip_tech":               cd.dip_tech,
        "mil_tech":               cd.mil_tech,
        "legitimacy":             cd.legitimacy,
        "prestige":               cd.prestige,
        "stability":              cd.stability,
        "power_projection":       cd.power_projection,
        "mercantilism":           cd.mercantilism,
        "innovativeness":         cd.innovativeness,
        "war_exhaustion":         cd.war_exhaustion,
        "corruption":             cd.corruption,
        "piety":                  cd.piety,
        "patriarch_authority":    cd.patriarch_authority,
        "karma":                  cd.karma,
        "harmony":                cd.harmony,
        "militarization":         cd.militarization,
        "mandate":                cd.mandate,
        "devotion":               cd.devotion,
        "republican_tradition":   cd.republican_tradition,
        "meritocracy":            cd.meritocracy,
        "absolutism":             cd.absolutism,
        "army_professionalism":   cd.army_professionalism,
        "is_defender_of_faith":   cd.is_defender_of_faith,
        "total_income":           cd.estimated_monthly_income,
        "treasury":               cd.treasury,
        "advisors":               [{"name": adv.job_type_name or (game_data.get_advisor_type_name(adv.type_int) if game_data else str(adv.type_int)), "skill": adv.skill} for adv in cd.advisors],
    }

    # Modifier calculations
    for stat_name, fn in _COMPUTED_STATS:
        if game_data:
            result = fn(cd, game_data)
            d[stat_name] = result.total
            d[f"{stat_name}_breakdown"] = result.breakdown
        else:
            d[stat_name] = None
            d[f"{stat_name}_breakdown"] = []

    # If game_data was unavailable, fall back to dev/10 approximation.
    if not game_data:
        d["army_force_limit"] = cd.raw_development / 10.0
        d["army_force_limit_breakdown"] = [("approx_raw_dev/10", cd.raw_development / 10.0)]
        d["naval_force_limit"] = float(d["total_ships"])
        d["naval_force_limit_breakdown"] = [("approx_ships", float(d["total_ships"]))]

    # Diplo capacity — calc_diplomatic_slots now returns the full total (base 4 + bonuses)
    diplo_bonus = d.get("diplo_slots_bonus") or 4
    d["diplo_capacity"] = int(round(diplo_bonus))

    return d


def _format_breakdown_items(stat_key: str, cd_dict: dict | None) -> list[tuple[str, str]]:
    if not cd_dict:
        return []

    bk_key = f"{stat_key}_breakdown"
    if stat_key == "diplo_capacity":
        bk_key = "diplo_slots_bonus_breakdown"

    raw_bk = cd_dict.get(bk_key)
    if raw_bk:
        res = []
        for item in raw_bk:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                lbl, val = item[0], item[1]
                if isinstance(val, (int, float)):
                    if stat_key in ("discipline", "infantry_ca", "cavalry_ca", "artillery_ca", "galley_ca", "heavy_ship_ca", "production_efficiency", "goods_produced", "trade_efficiency", "fire_damage", "shock_damage", "siege_ability", "dev_cost", "core_creation_cost", "all_power_cost"):
                        val_str = f"+{val*100:.1f}%" if val > 0 else f"{val*100:.1f}%"
                    elif stat_key in ("morale_armies", "naval_morale"):
                        if "base" in str(lbl).lower():
                            val_str = f"{val:.2f}"
                        else:
                            val_str = f"+{val*100:.1f}%" if val > 0 else f"{val*100:.1f}%"
                    elif stat_key in ("army_force_limit", "naval_force_limit", "governing_capacity"):
                        if "mod%" in str(lbl) or "(%)" in str(lbl):
                            val_str = f"+{val*100:.1f}%"
                        else:
                            val_str = f"+{val:.1f}" if val > 0 else f"{val:.1f}"
                    elif stat_key in ("diplo_reputation", "diplo_capacity"):
                        val_str = f"+{val:.1f}" if val > 0 else f"{val:.1f}"
                    else:
                        val_str = f"{val}"
                else:
                    val_str = str(val)
                res.append((str(lbl), val_str))
        if res:
            return res

    if stat_key == "max_manpower":
        mp = (cd_dict.get("max_manpower") or 0) * 1000
        rec = cd_dict.get("manpower_recovery") or 0
        return [("Base Manpower Pool", f"{int(mp):,}"), ("Recovery Speed", f"+{rec*100:.1f}%")]
    elif stat_key == "development":
        return [("Controlled Dev", f"{int(cd_dict.get('development', 0)):,}"), ("Starting Dev", f"{int(cd_dict.get('starting_development', 0)):,}")]
    elif stat_key == "province_count":
        return [("Directly Owned", f"{cd_dict.get('province_count', 0)} provinces")]
    elif stat_key == "dev_clicks":
        return [("Development Clicks", f"{cd_dict.get('dev_clicks', 0)} clicks")]
    elif stat_key == "heavy_ships":
        ca = cd_dict.get("heavy_ship_ca") or 0
        return [("Heavy Ships", f"{cd_dict.get('heavy_ships', 0)}"), ("Heavy Combat Ability", f"+{ca*100:.1f}%")]
    elif stat_key == "trade_ships":
        return [("Light / Trade Ships", f"{cd_dict.get('trade_ships', 0)} ships")]
    elif stat_key == "colonial_nations_count":
        tags = cd_dict.get("colonial_nation_tags", [])
        return [("Colonial Subjects", ", ".join(tags) if tags else "None")]
    elif stat_key == "personal_unions_count":
        return [("Personal Unions", f"{cd_dict.get('personal_unions_count', 0)} PUs")]
    elif stat_key == "colonial_regions_controlled":
        return [("Controlled Regions", f"{cd_dict.get('colonial_regions_controlled', 0)} / 12")]
    elif stat_key in ("is_naval_hegemon", "is_mil_hegemon", "is_economy_hegemon"):
        heg = cd_dict.get("hegemony", "None")
        return [("Hegemony Status", heg.capitalize())]

    return []


def _compute_matchups(country_map: dict[str, dict]) -> tuple[dict, dict, list[dict]]:
    """
    Compute role matchups between Team 1 and Team 2.
    Returns (team1_summary, team2_summary, matchups_list).
    """
    roles = ["NAVAL", "QUANTITY", "QUALITY", "BLOB"]
    role_to_t1 = {"NAVAL": "SPA", "QUANTITY": "FRA", "QUALITY": "HAB", "BLOB": "MUG"}
    role_to_t2 = {"NAVAL": "MLC", "QUANTITY": "RUS", "QUALITY": "BAH", "BLOB": "QNG"}

    team1_total = 0
    team2_total = 0
    matchups = []

    for role in roles:
        t1_tag = role_to_t1[role]
        t2_tag = role_to_t2[role]
        c1 = country_map.get(t1_tag)
        c2 = country_map.get(t2_tag)

        role_t1_score = 0
        role_t2_score = 0
        metrics_comparison = []

        for key, name, pts, fmt, is_hegemon, is_approx in ROLE_METRICS_SPEC[role]:
            val1 = c1.get(key) if c1 else None
            val2 = c2.get(key) if c2 else None

            val1_disp = _format_val(val1, fmt)
            val2_disp = _format_val(val2, fmt)

            winner = 0  # 0 = tie/none, 1 = T1, 2 = T2

            if is_hegemon:
                # Hegemon rule: 5p if claimed
                if val1 and not val2:
                    winner = 1
                    role_t1_score += pts
                elif val2 and not val1:
                    winner = 2
                    role_t2_score += pts
                elif val1 and val2:
                    # Both claimed (rare) -> tie
                    winner = 0
                else:
                    winner = 0
            else:
                # Standard comparison
                if val1 is not None and val2 is not None:
                    # Treat small float differences as tie
                    if isinstance(val1, float) or isinstance(val2, float):
                        diff = float(val1) - float(val2)
                        if diff > 1e-6:
                            winner = 1
                            role_t1_score += pts
                        elif diff < -1e-6:
                            winner = 2
                            role_t2_score += pts
                    else:
                        if val1 > val2:
                            winner = 1
                            role_t1_score += pts
                        elif val2 > val1:
                            winner = 2
                            role_t2_score += pts
                elif val1 is not None:
                    winner = 1
                    role_t1_score += pts
                elif val2 is not None:
                    winner = 2
                    role_t2_score += pts

            metrics_comparison.append({
                "key": key,
                "name": name,
                "points": pts,
                "val1": val1,
                "val2": val2,
                "val1_disp": val1_disp,
                "val2_disp": val2_disp,
                "winner": winner,
                "is_approx": is_approx,
                "breakdown1": _format_breakdown_items(key, c1),
                "breakdown2": _format_breakdown_items(key, c2),
            })

        team1_total += role_t1_score
        team2_total += role_t2_score

        if c1:
            c1["score"] = role_t1_score
        if c2:
            c2["score"] = role_t2_score

        matchups.append({
            "role": role,
            "t1_country": c1,
            "t2_country": c2,
            "t1_score": role_t1_score,
            "t2_score": role_t2_score,
            "metrics": metrics_comparison,
        })

    for c in country_map.values():
        c.setdefault("score", 0)

    team1_summary = {
        "name": TEAM_1_NAME,
        "score": team1_total,
        "countries": [country_map.get(tag) for tag in ["SPA", "FRA", "HAB", "MUG"] if tag in country_map],
    }
    team2_summary = {
        "name": TEAM_2_NAME,
        "score": team2_total,
        "countries": [country_map.get(tag) for tag in ["MLC", "RUS", "BAH", "QNG"] if tag in country_map],
    }

    return team1_summary, team2_summary, matchups


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python src/score_calculator.py <save.eu4> [eu4_dir] [mod_dir]")
        return

    save_path = sys.argv[1]
    eu4_dir   = sys.argv[2] if len(sys.argv) > 2 else None
    mod_dir   = sys.argv[3] if len(sys.argv) > 3 else None

    game_data: GameData | None = None
    if eu4_dir:
        print("Loading game data...")
        # mod_dir may be a folder containing numbered mod subdirectories (e.g. eu4_data/mod/3783710427)
        # Expand it: if it has no common/ but contains subdirs, use each subdir as a mod path
        mod_paths: list[str] = []
        if mod_dir:
            if os.path.isdir(os.path.join(mod_dir, "common")):
                mod_paths = [mod_dir]
            else:
                for sub in sorted(os.listdir(mod_dir)):
                    sub_path = os.path.join(mod_dir, sub)
                    if os.path.isdir(sub_path) and os.path.isdir(os.path.join(sub_path, "common")):
                        mod_paths.append(sub_path)
        game_data = load_game_data(eu4_dir, mod_paths)
        print(
            f"  {len(game_data.idea_groups)} idea groups, "
            f"{len(game_data.government_reforms)} reforms, "
            f"{len(game_data.advisor_modifiers)} advisor types, "
            f"{len(game_data.great_projects)} monuments, "
            f"{len(game_data.colonial_region_provinces)} colonial regions, "
            f"{len(game_data.coastal_provinces)} coastal provinces loaded"
        )

    print("Parsing save file...")
    players_countries, countries, most_dev, extra = load_save_full(save_path, game_data=game_data)
    print(f"  {len(players_countries)} players, {len(countries)} countries extracted")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    eu4_data_path = os.path.join(os.path.dirname(script_dir), "eu4_data")
    enrich_with_historical_ideas(countries, eu4_data_path)

    if game_data:

        # ── Post-load: attach monument modifiers to countries ──────────────────
        province_owner_map = extra["province_owner_map"]
        tag_to_cd = {cd.tag: cd for cd in countries}
        # Build subject -> overlord mapping
        subject_to_overlord: dict[str, str] = {}
        for cd in countries:
            for cn_tag in cd.colonial_nation_tags:
                subject_to_overlord[cn_tag] = cd.tag

        for mon_name, tier, province_id in extra["raw_monuments"]:
            if province_id is None:
                continue
            owner_tag = province_owner_map.get(str(province_id)) or province_owner_map.get(f"-{province_id}")
            if not owner_tag:
                continue
            tier_mods = game_data.great_projects.get(mon_name, {}).get(tier, {})
            if not tier_mods:
                continue

            # If directly owned by a player country
            if owner_tag in tag_to_cd:
                tag_to_cd[owner_tag].monument_modifiers.append((mon_name, tier, tier_mods))
            # If owned by a player subject, attach overlord modifiers (e.g. fuerte_del_morro)
            elif owner_tag in subject_to_overlord:
                ol_tag = subject_to_overlord[owner_tag]
                ol_mods = {}
                for k, v in tier_mods.items():
                    if k.startswith("overlord_"):
                        ol_mods[k.replace("overlord_", "")] = v
                if ol_mods and ol_tag in tag_to_cd:
                    tag_to_cd[ol_tag].monument_modifiers.append((f"{mon_name}(subject:{owner_tag})", tier, ol_mods))

        # ── Post-load: compute fully controlled colonial regions per country ──────────
        tag_province_sets = extra["tag_province_sets"]
        for cd in countries:
            all_controlled_pids = set(tag_province_sets.get(cd.tag, set()))
            for cn_tag in cd.colonial_nation_tags:
                all_controlled_pids.update(tag_province_sets.get(cn_tag, set()))
            # Fully controlled: every province of the colonial region must belong to the player or its subjects/colonies
            fully_controlled = sum(
                1 for region_pids in game_data.colonial_region_provinces.values()
                if region_pids and region_pids.issubset(all_controlled_pids)
            )
            cd.colonial_regions_controlled = fully_controlled

        # ── Unified Modifier Engine: evaluate all country active modifiers ──────────
        raw_monuments = extra.get("raw_monuments", [])
        province_owner_map = extra.get("province_owner_map", {})
        dof_tags_set = {cd.tag for cd in countries if cd.is_defender_of_faith}
        for cd in countries:
            ModifierEngine.evaluate_country(
                country=cd,
                game_data=game_data,
                raw_monuments=raw_monuments,
                dof_tags=dof_tags_set,
                province_owner_map=province_owner_map,
            )
    else:
        print("  No EU4 install dir given - modifier stats will be N/A")

    most_dev_tag = most_dev.get("owner", "")
    processed_countries = [_process_country_data(cd, game_data, most_dev_tag) for cd in countries]
    country_map = {c["tag"]: c for c in processed_countries}

    # Compute team matchups and scoreboard
    team1_summary, team2_summary, matchups = _compute_matchups(country_map)

    # Sort countries: Team 1 first (by role), Team 2 second (by role), then others
    role_order = {"NAVAL": 0, "QUANTITY": 1, "QUALITY": 2, "BLOB": 3, "NONE": 4}
    def _country_sort_key(c: dict) -> tuple[int, int, str]:
        t_order = 0 if c["team"] == TEAM_1_NAME else (1 if c["team"] == TEAM_2_NAME else 2)
        r_order = role_order.get(c["role"], 99)
        return (t_order, r_order, c["tag"])

    sorted_countries = sorted(processed_countries, key=_country_sort_key)

    # Generate interactive map data if map assets exist
    map_data: dict[str, Any] | None = None
    if game_data:
        all_roots = (mod_paths or []) + [eu4_dir]
        bmp_path = None
        def_path = None
        def_map_path = None
        for r in all_roots:
            p = os.path.join(r, "map", "provinces.bmp")
            if os.path.isfile(p):
                bmp_path = p
                break
        for r in all_roots:
            p = os.path.join(r, "map", "definition.csv")
            if os.path.isfile(p):
                def_path = p
                break
        if not def_path:
            fallback = r"C:\Program Files (x86)\Steam\steamapps\common\Europa Universalis IV\map\definition.csv"
            if os.path.isfile(fallback):
                def_path = fallback

        sea_provinces: set[int] = set()
        for r in all_roots:
            p = os.path.join(r, "map", "default.map")
            if os.path.isfile(p):
                def_map_path = p
                break
        if def_map_path:
            with open(def_map_path, "r", encoding="utf-8", errors="ignore") as f:
                map_text = f.read()
            sea_starts_m = re.search(r"sea_starts\s*=\s*\{([^}]*)\}", map_text)
            if sea_starts_m:
                for token in sea_starts_m.group(1).split():
                    if token.isdigit():
                        sea_provinces.add(int(token))
            lakes_m = re.search(r"lakes\s*=\s*\{([^}]*)\}", map_text)
            if lakes_m:
                for token in lakes_m.group(1).split():
                    if token.isdigit():
                        sea_provinces.add(int(token))

        wasteland_provinces: set[int] = set()
        for r in all_roots:
            p = os.path.join(r, "map", "climate.txt")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    c_text = f.read()
                m = re.search(r"impassable\s*=\s*\{([^}]*)\}", c_text)
                if m:
                    for token in m.group(1).split():
                        if token.isdigit():
                            wasteland_provinces.add(int(token))

        if bmp_path and def_path:
            print("Generating GPU WebGL2 interactive map assets (5632x2048)...")
            tag_to_player = {cd.tag: cd.player for cd in countries}
            prov_info_map = extra.get("prov_info_map", {})
            output_dir = os.path.dirname(script_dir)
            map_data = generate_interactive_map_data(
                bmp_path=bmp_path,
                def_path=def_path,
                sea_provinces=sea_provinces,
                coastal_provinces=game_data.coastal_provinces,
                prov_data=prov_info_map,
                tag_to_player=tag_to_player,
                wasteland_provinces=wasteland_provinces,
                output_dir=output_dir,
            )
            if map_data:
                print(f"  Interactive map ready with {map_data.get('total_provinces', 0)} active provinces")

    env = Environment(
        loader=FileSystemLoader(os.path.join(script_dir, "templates"))
    )
    template = env.get_template("report_template.html")
    html = template.render(
        team1=team1_summary,
        team2=team2_summary,
        matchups=matchups,
        countries=sorted_countries,
        most_dev_province=most_dev,
        game_data_loaded=game_data is not None,
        map_data=map_data,
    )

    output_path = os.path.join(os.path.dirname(script_dir), "index.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report written -> {output_path}")


if __name__ == "__main__":
    main()
