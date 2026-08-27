"""
Military modifier calculators.
Powered by the Unified EU4 Modifier State Engine.
"""
from __future__ import annotations

from eu4.calculators import ModifierResult
from eu4.save_reader import CountryData
from eu4.game_data import GameData


def _base_morale(mil_tech: int) -> float:
    """Base land morale per military tech level (from common/technologies/mil.txt)."""
    tech_morale = {
        1: 2.0, 2: 2.0, 3: 2.0, 4: 2.5, 5: 3.0, 6: 3.0, 7: 3.0, 8: 3.0,
        9: 3.0, 10: 3.0, 11: 3.0, 12: 3.0, 13: 3.0, 14: 3.0, 15: 3.0,
        16: 4.0, 17: 4.0, 18: 4.0, 19: 4.0, 20: 4.0, 21: 4.0, 22: 4.0,
        23: 4.0, 24: 4.0, 25: 4.0, 26: 4.0, 27: 5.0, 28: 5.0, 29: 5.0,
        30: 5.0, 31: 6.0, 32: 6.0, 33: 6.0
    }
    return tech_morale.get(mil_tech, 3.0 if mil_tech < 16 else (4.0 if mil_tech < 27 else 5.0))


def _base_naval_morale(dip_tech: int) -> float:
    """Base naval morale per diplomatic tech level (from common/technologies/dip.txt)."""
    tech_naval = {
        1: 2.0, 2: 2.0, 3: 2.0, 4: 2.1, 5: 2.1, 6: 2.1, 7: 2.1, 8: 2.4,
        9: 2.6, 10: 2.6, 11: 2.6, 12: 2.6, 13: 3.0, 14: 3.0, 15: 3.0,
        16: 3.0, 17: 3.0, 18: 3.0, 19: 3.5, 20: 3.5, 21: 3.5, 22: 3.5,
        23: 4.0, 24: 4.0, 25: 4.0, 26: 4.5, 27: 4.5, 28: 5.0, 29: 5.0,
        30: 5.5, 31: 5.5, 32: 6.0, 33: 6.0
    }
    return tech_naval.get(dip_tech, 2.0 if dip_tech < 4 else (2.6 if dip_tech < 13 else 3.0))


def calc_discipline(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute total army discipline (multiplier where 1.0 = 100%)."""
    base = 1.0
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("discipline")]
    total = base + country.get_modifier("discipline")
    return ModifierResult(total=total, breakdown=bd)


def calc_morale_armies(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute total land morale (absolute value e.g. 5.20)."""
    base = _base_morale(country.mil_tech)
    pct_mods = country.get_modifier("morale_armies", "land_morale")

    bd: list[tuple[str, float]] = [("base_mil_tech", base)]
    for e in country.get_breakdown("morale_armies", "land_morale"):
        bd.append((e["source"], e["value"]))

    total = base * (1.0 + pct_mods)
    return ModifierResult(total=total, breakdown=bd)


def calc_naval_morale(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute total naval morale."""
    base = _base_naval_morale(country.dip_tech)
    pct_mods = country.get_modifier("naval_morale")

    bd: list[tuple[str, float]] = [("base_dip_tech", base)]
    for e in country.get_breakdown("naval_morale"):
        bd.append((e["source"], e["value"]))

    total = base * (1.0 + pct_mods)
    return ModifierResult(total=total, breakdown=bd)


def calc_infantry_combat_ability(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute infantry combat ability (fraction, e.g. 0.20 = +20%)."""
    total = country.get_modifier("infantry_power")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("infantry_power")]
    return ModifierResult(total=total, breakdown=bd)


def calc_cavalry_combat_ability(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute cavalry combat ability."""
    total = country.get_modifier("cavalry_power")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("cavalry_power")]
    return ModifierResult(total=total, breakdown=bd)


def calc_artillery_combat_ability(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute artillery combat ability."""
    total = country.get_modifier("artillery_power")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("artillery_power")]
    return ModifierResult(total=total, breakdown=bd)


def calc_galley_combat_ability(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute galley combat ability."""
    keys = ("galley_power", "galley_combat_ability")
    total = country.get_modifier(*keys)
    bd = [(e["source"], e["value"]) for e in country.get_breakdown(*keys)]
    return ModifierResult(total=total, breakdown=bd)


def calc_heavy_ship_combat_ability(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute heavy ship combat ability."""
    keys = ("heavy_ship_power", "heavy_ship_combat_ability")
    total = country.get_modifier(*keys)
    bd = [(e["source"], e["value"]) for e in country.get_breakdown(*keys)]
    return ModifierResult(total=total, breakdown=bd)


def calc_light_ship_combat_ability(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute light (trade) ship combat ability."""
    keys = ("light_ship_power", "light_ship_combat_ability")
    total = country.get_modifier(*keys)
    bd = [(e["source"], e["value"]) for e in country.get_breakdown(*keys)]
    return ModifierResult(total=total, breakdown=bd)


def calc_siege_ability(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute siege ability."""
    total = country.get_modifier("siege_ability")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("siege_ability")]
    return ModifierResult(total=total, breakdown=bd)


def calc_manpower_recovery_speed(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute manpower recovery speed bonus."""
    total = country.get_modifier("manpower_recovery_speed")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("manpower_recovery_speed")]
    return ModifierResult(total=total, breakdown=bd)


def calc_army_force_limit(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute exact army force limit: (Base + Provinces + Subjects + Flat Modifiers) * (1.0 + Pct Modifiers)."""
    base_nation = 6.0
    prov_fl = country.province_lfl
    sub_fl = country.subject_lfl
    flat_mods = country.get_modifier("land_forcelimit")
    base_fl = base_nation + prov_fl + sub_fl + flat_mods

    pct_mod = country.get_modifier("land_forcelimit_modifier")

    bd: list[tuple[str, float]] = [
        ("base_nation", base_nation),
        (f"provinces({country.province_count})", prov_fl),
    ]
    if sub_fl > 0:
        bd.append(("subjects", sub_fl))
    for e in country.get_breakdown("land_forcelimit"):
        src = e.get("desc") or e["source"]
        bd.append((src if "(flat)" in src else f"{src}(flat)", e["value"]))
    for e in country.get_breakdown("land_forcelimit_modifier"):
        src = e.get("desc") or e["source"]
        bd.append((src if "(mod%)" in src else f"{src}(mod%)", e["value"]))

    total = base_fl * (1.0 + pct_mod)
    return ModifierResult(total=total, breakdown=bd)


def calc_naval_force_limit(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute exact naval force limit: (Base + Provinces + Subjects + Flat Modifiers) * (1.0 + Pct Modifiers)."""
    base_nation = 12.0
    prov_fl = country.province_nfl
    sub_fl = country.subject_nfl
    flat_mods = country.get_modifier("naval_forcelimit")
    base_fl = base_nation + prov_fl + sub_fl + flat_mods

    pct_mod = country.get_modifier("naval_forcelimit_modifier", "global_naval_forcelimit_modifier")

    bd: list[tuple[str, float]] = [
        ("base_nation", base_nation),
        (f"provinces({country.province_count})", prov_fl),
    ]
    if sub_fl > 0:
        bd.append(("subjects", sub_fl))
    for e in country.get_breakdown("naval_forcelimit"):
        src = e.get("desc") or e["source"]
        bd.append((src if "(flat)" in src else f"{src}(flat)", e["value"]))
    for e in country.get_breakdown("naval_forcelimit_modifier", "global_naval_forcelimit_modifier"):
        src = e.get("desc") or e["source"]
        bd.append((src if "(mod%)" in src else f"{src}(mod%)", e["value"]))

    total = base_fl * (1.0 + pct_mod)
    return ModifierResult(total=total, breakdown=bd)


def calc_fire_damage(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute land fire damage dealt modifier (e.g. from Army Professionalism, ideas)."""
    total = country.get_modifier("fire_damage", "land_fire_damage")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("fire_damage", "land_fire_damage")]
    return ModifierResult(total=total, breakdown=bd)


def calc_shock_damage(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute land shock damage dealt modifier (e.g. from Army Professionalism, ideas)."""
    total = country.get_modifier("shock_damage", "land_shock_damage")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("shock_damage", "land_shock_damage")]
    return ModifierResult(total=total, breakdown=bd)

