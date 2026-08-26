"""
Economy modifier calculators.
Powered by the Unified EU4 Modifier State Engine.
"""
from __future__ import annotations

from eu4.calculators import ModifierResult
from eu4.save_reader import CountryData
from eu4.game_data import GameData

# Cumulative production_efficiency from adm technology (EU4 1.37)
_ADM_TECH_PROD_EFF = [
    0.00, 0.00, 0.00, 0.02, 0.02, 0.04, 0.04, 0.04, 0.04, 0.06,
    0.06, 0.06, 0.06, 0.08, 0.08, 0.08, 0.10, 0.10, 0.10, 0.10,
    0.10, 0.12, 0.12, 0.12, 0.12, 0.14, 0.14, 0.14, 0.16, 0.16,
    0.16, 0.16, 0.20
]

# Cumulative trade_efficiency from dip technology (EU4 1.37)
_DIP_TECH_TRADE_EFF = [
    0.00, 0.00, 0.00, 0.02, 0.02, 0.04, 0.04, 0.04, 0.04, 0.04,
    0.06, 0.06, 0.06, 0.08, 0.08, 0.08, 0.10, 0.10, 0.10, 0.10,
    0.12, 0.12, 0.12, 0.12, 0.14, 0.14, 0.14, 0.14, 0.16, 0.16,
    0.18, 0.18, 0.20
]

# Governing capacity from adm technology
_ADM_TECH_GOV_CAP = [
    0, 0, 0, 0, 0, 0, 0, 0, 100, 100,
    100, 100, 200, 200, 200, 200, 200, 250, 250, 250,
    300, 300, 300, 350, 400, 400, 450, 500, 500, 500,
    550, 600, 650
]


def _adm_tech_gov_capacity(adm_tech: int) -> float:
    idx = min(max(adm_tech, 0), len(_ADM_TECH_GOV_CAP) - 1)
    return float(_ADM_TECH_GOV_CAP[idx])


def calc_governing_capacity(country: CountryData, game_data: GameData) -> ModifierResult:
    """
    Compute total governing capacity:
    (Base 200 + Tech + Flat Modifiers) * (1.0 + Percentage Modifiers).
    """
    base = 200.0
    tech_cap = _adm_tech_gov_capacity(country.adm_tech)
    flat_mods = country.get_modifier("governing_capacity")
    pct_mods = country.get_modifier("governing_capacity_modifier")

    bd: list[tuple[str, float]] = [
        ("base", base),
        (f"adm_tech_{country.adm_tech}", tech_cap),
    ]

    for e in country.get_breakdown("governing_capacity"):
        bd.append((e["source"], e["value"]))

    for e in country.get_breakdown("governing_capacity_modifier"):
        bd.append((e["source"] + "(%)", e["value"]))

    base_sum = base + tech_cap + flat_mods
    total = base_sum * (1.0 + pct_mods)
    return ModifierResult(total=total, breakdown=bd)


def calc_goods_produced(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute national goods produced modifier (fraction, e.g. 0.20 = +20%)."""
    keys = (
        "goods_produced_modifier",
        "global_trade_goods_size_modifier",
    )
    total = country.get_modifier(*keys)
    bd = [(e["source"], e["value"]) for e in country.get_breakdown(*keys)]
    return ModifierResult(total=total, breakdown=bd)


def calc_trade_efficiency(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute trade efficiency modifier (includes dip tech base)."""
    tech_idx = min(max(country.dip_tech, 0), len(_DIP_TECH_TRADE_EFF) - 1)
    tech_bonus = _DIP_TECH_TRADE_EFF[tech_idx]

    mod_bonus = country.get_modifier("trade_efficiency")
    bd = [(f"dip_tech_{country.dip_tech}", tech_bonus)] + [
        (e["source"], e["value"]) for e in country.get_breakdown("trade_efficiency")
    ]
    total = tech_bonus + mod_bonus
    return ModifierResult(total=total, breakdown=bd)


def calc_production_efficiency(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute production efficiency (includes adm tech base)."""
    tech_idx = min(max(country.adm_tech, 0), len(_ADM_TECH_PROD_EFF) - 1)
    tech_bonus = _ADM_TECH_PROD_EFF[tech_idx]

    mod_bonus = country.get_modifier("production_efficiency")
    bd = [(f"adm_tech_{country.adm_tech}", tech_bonus)] + [
        (e["source"], e["value"]) for e in country.get_breakdown("production_efficiency")
    ]
    total = tech_bonus + mod_bonus
    return ModifierResult(total=total, breakdown=bd)


def calc_development_cost(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute development cost modifier."""
    keys = ("development_cost", "development_cost_modifier")
    total = country.get_modifier(*keys)
    bd = [(e["source"], e["value"]) for e in country.get_breakdown(*keys)]
    return ModifierResult(total=total, breakdown=bd)


def calc_tax_efficiency(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute national tax modifier."""
    total = country.get_modifier("global_tax_modifier")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("global_tax_modifier")]
    return ModifierResult(total=total, breakdown=bd)


def calc_core_creation_cost(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute core creation cost modifier (CCR)."""
    total = country.get_modifier("core_creation", "core_creation_cost")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("core_creation", "core_creation_cost")]
    return ModifierResult(total=total, breakdown=bd)


def calc_all_power_cost(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute all power cost modifier."""
    total = country.get_modifier("all_power_cost")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("all_power_cost")]
    return ModifierResult(total=total, breakdown=bd)
