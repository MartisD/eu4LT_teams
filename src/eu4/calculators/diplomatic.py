"""
Diplomatic modifier calculators.
Powered by the Unified EU4 Modifier State Engine.
"""
from __future__ import annotations

from eu4.calculators import ModifierResult
from eu4.save_reader import CountryData
from eu4.game_data import GameData

# EU4 base diplomatic relations is 4 (hardcoded in the engine).
_BASE_DIPLOMATIC_RELATIONS = 4.0


def calc_diplomatic_reputation(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute diplomatic reputation."""
    total = country.get_modifier("diplomatic_reputation")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("diplomatic_reputation")]
    return ModifierResult(total=total, breakdown=bd)


def calc_diplomatic_slots(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute total diplomatic relations limit (base 4 + modifiers)."""
    bonus = country.get_modifier("diplomatic_upkeep")
    total = _BASE_DIPLOMATIC_RELATIONS + bonus
    bd = [("base", _BASE_DIPLOMATIC_RELATIONS)] + [
        (e["source"], e["value"]) for e in country.get_breakdown("diplomatic_upkeep")
    ]
    return ModifierResult(total=total, breakdown=bd)


def calc_ae_impact(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute aggressive expansion impact modifier."""
    total = country.get_modifier("ae_impact")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("ae_impact")]
    return ModifierResult(total=total, breakdown=bd)


def calc_improve_relations(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute improve relations modifier."""
    total = country.get_modifier("improve_relation_modifier", "improve_relations")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("improve_relation_modifier", "improve_relations")]
    return ModifierResult(total=total, breakdown=bd)


def calc_max_absolutism(country: CountryData, game_data: GameData) -> ModifierResult:
    """Compute max absolutism bonus."""
    total = country.get_modifier("max_absolutism")
    bd = [(e["source"], e["value"]) for e in country.get_breakdown("max_absolutism")]
    return ModifierResult(total=total, breakdown=bd)
