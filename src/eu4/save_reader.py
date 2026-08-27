"""
EU4 save file extractor.

Opens a .eu4 file (zip archive containing 'gamestate'), parses it with the
Clausewitz parser, and returns structured data ready for the calculators.
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Optional

from clausewitz.parser import parse, ClausewitzNode


# ── TODO: approximations that need replacing with accurate calculations ─────────
# TODO [APPROX-1] army_force_limit: currently raw_dev/10 + bonus. Should sum
#   base_manpower of owned provinces + all FL modifiers via game_data.
# TODO [APPROX-2] naval_force_limit: currently total_ship_count + bonus. Should use
#   coastal-province-based formula + modifiers via game_data.
# TODO [APPROX-3] colonial_regions_controlled: always 0. Needs a province→colonial-
#   region mapping (from game data) and full ownership scan per region.
# ─────────────────────────────────────────────────────────────────────────────

# ── ship type classification ──────────────────────────────────────────────────

_HEAVY_SHIP_TYPES: frozenset[str] = frozenset({
    "carrack", "early_carrack", "man_o_war", "galleon",
    "third_rate_ship", "barque_d_aviso", "heavy_ship",
    "second_rate_ship", "first_rate_ship",
})
_LIGHT_SHIP_TYPES: frozenset[str] = frozenset({
    "caravel", "barque", "fluyt", "yacht", "light_ship",
    "frigate",
})
_GALLEY_TYPES: frozenset[str] = frozenset({
    "galley", "war_galley", "galleass",
})
# Everything else (cog, flute, etc.) is a transport


# ── country data structure ────────────────────────────────────────────────────


@dataclass
class AdvisorEntry:
    id: int
    type_int: int  # entity type from save (51 = advisor entity)
    job_type_name: str = ""  # resolved from history (e.g. "commandant")
    skill: int = 1


@dataclass
class ModifierEntry:
    # Named modifier applied to the country (from events/missions)
    name: str
    permanent: bool = False
    expiry_date: str = ""  # "-1.1.1" = permanent


@dataclass
class CountryData:
    tag: str
    player: str = "Unknown"

    # ── direct save values ───────────────────────────────────────────────────
    raw_development: float = 0.0
    starting_development: float = 0.0
    army_tradition: float = 0.0
    navy_tradition: float = 0.0
    legitimacy: float = 100.0
    absolutism: float = 0.0
    horde_unity: float = 0.0
    mercantilism: float = 0.0
    splendor: float = 0.0
    government_rank: int = 1
    max_manpower: float = 0.0
    adm_tech: int = 3
    dip_tech: int = 3
    mil_tech: int = 3
    army_professionalism: float = 0.0
    prestige: float = 0.0
    power_projection: float = 0.0
    stability: float = 0.0
    golden_age: bool = False
    is_defender_of_faith: bool = False
    save_year: int = 1444
    accepted_culture_count: int = 0
    innovativeness: float = 0.0
    overextension: float = 0.0
    war_exhaustion: float = 0.0
    corruption: float = 0.0
    piety: float = 0.0
    patriarch_authority: float = 0.0
    karma: float = 0.0
    harmony: float = 0.0
    militarization: float = 0.0
    mandate: float = 0.0
    devotion: float = 0.0
    republican_tradition: float = 0.0
    meritocracy: float = 0.0
    # pre-computed discipline bonus from estate privileges (e.g. Maratha Military Leadership)
    estate_discipline: float = 0.0
    # list of (privilege_name, land_share_fraction) from all estates
    estate_privileges: list = field(default_factory=list)
    # entity_id → job_type_name extracted from country history (e.g. 58720 → "commandant")
    advisor_job_types: dict = field(default_factory=dict)
    estimated_monthly_income: float = 0.0
    treasury: float = 0.0

    # ── naval ────────────────────────────────────────────────────────────────
    heavy_ships: int = 0    # carrack, galleon, man-o-war, etc.
    trade_ships: int = 0    # caravel, barque, fluyt (light ships)
    galley_ships: int = 0   # galley, war_galley, galleass
    transport_ships: int = 0  # cog, flute, hulk, etc.

    # ── force limits (approximated) ──────────────────────────────────────────
    # These are computed in load_save_full after modifier calculators run
    army_force_limit: float = 0.0   # raw_dev / 10 + army_fl_bonus
    naval_force_limit: float = 0.0  # total_ships + naval_fl_bonus

    # ── subjects ─────────────────────────────────────────────────────────────
    colonial_nations_count: int = 0
    personal_unions_count: int = 0
    # Colonial regions fully controlled (requires province-level scan; set in load_save_full)
    colonial_regions_controlled: int = 0

    # ── territory ────────────────────────────────────────────────────────────
    province_count: int = 0   # directly owned provinces (set in load_save_full)
    # Dev clicks ≈ raw_development - starting_development
    dev_clicks: int = 0

    # ── government ───────────────────────────────────────────────────────────
    used_governing_capacity: float = 0.0

    # ── hegemony ─────────────────────────────────────────────────────────────
    hegemony: str = ""   # "naval", "mil", "economy" or ""

    # ── strings ──────────────────────────────────────────────────────────────
    religion: str = ""
    government_type: str = "monarchy"

    # ── collections ──────────────────────────────────────────────────────────
    # idea_group_name → number of completed ideas (0–7)
    active_idea_groups: dict[str, int] = field(default_factory=dict)
    # active government reforms (from reform_stack)
    government_reforms: list[str] = field(default_factory=list)
    advisors: list[AdvisorEntry] = field(default_factory=list)
    # named modifiers active on the country
    modifiers: list[ModifierEntry] = field(default_factory=list)
    # completed mission names
    completed_missions: list[str] = field(default_factory=list)
    # monarch personalities (e.g. "strict_personality")
    monarch_personalities: list[str] = field(default_factory=list)
    # active policy names
    active_policies: list[str] = field(default_factory=list)
    # (monument_name, tier_int, mods_dict) for all owned monuments with country_modifiers
    monument_modifiers: list = field(default_factory=list)
    # tags of colonial nation subjects (e.g. ["C00", "C01"])
    colonial_nation_tags: list[str] = field(default_factory=list)
    # (estate_type, loyalty_float, territory_float) for active estates
    estates: list[tuple[str, float, float]] = field(default_factory=list)
    # (modifier_name, province_name) for modifiers active on owned provinces
    province_modifiers: list[tuple[str, str]] = field(default_factory=list)

    # ── province aggregates & force limit bases (computed from province scan) ──
    # sum of base_manpower across all owned provinces
    base_manpower_sum: float = 0.0
    # province-level force limit contributions (scaled by autonomy, buildings, trade goods)
    province_lfl: float = 0.0
    province_nfl: float = 0.0
    # subject-level force limit contributions
    subject_lfl: float = 0.0
    subject_nfl: float = 0.0
    large_cn_count: int = 0
    is_subject: bool = False
    naval_doctrine: str = ""
    is_curia_controller: bool = False
    is_hre_emperor: bool = False

    # ── victory card (kept for potential reference) ──────────────────────────
    victory_card_score: float = 0.0
    average_army_drill: float = 0.0
    accepted_cultures: list[str] = field(default_factory=list)

    # ── compiled modifiers & provenance breakdowns (from ModifierEngine) ───────
    compiled_modifiers: dict[str, float] = field(default_factory=dict)
    modifier_breakdowns: dict[str, list] = field(default_factory=dict)
    subjects: list[str] = field(default_factory=list)

    # ── historical ideas (kept for reference) ────────────────────────────────
    historical_idea_groups: list[str] = field(default_factory=list)

    @property
    def reforms(self) -> list[str]:
        return self.government_reforms

    @property
    def ruler_traits(self) -> list[str]:
        return self.monarch_personalities

    @property
    def active_modifiers(self) -> list:
        return self.modifiers

    def get_modifier(self, *keys: str) -> float:
        """Return the aggregated sum of values across the requested modifier keys."""
        val = 0.0
        for k in keys:
            val += self.compiled_modifiers.get(k, 0.0)
        return val

    def get_breakdown(self, *keys: str) -> list[dict[str, Any]]:
        """Return the list of contributing source entries across the requested modifier keys."""
        res: list[dict[str, Any]] = []
        for k in keys:
            res.extend(self.modifier_breakdowns.get(k, []))
        return res


# ── extraction helpers ────────────────────────────────────────────────────────


def _extract_ideas(node: ClausewitzNode) -> dict[str, int]:
    result: dict[str, int] = {}
    ideas_node = node.get_node("active_idea_groups")
    if ideas_node:
        for name, val in ideas_node.items():
            try:
                result[name] = int(val)
            except (ValueError, TypeError):
                pass
    return result


def _extract_advisors(node: ClausewitzNode, job_types: dict[int, Any] | None = None) -> list[AdvisorEntry]:
    entries: list[AdvisorEntry] = []
    for adv_node in node.get_list("advisor"):
        if not isinstance(adv_node, ClausewitzNode):
            continue
        id_node = adv_node.get_node("id")
        entity_id = id_node.get_int("id") if id_node else adv_node.get_int("id")
        info = (job_types or {}).get(entity_id)
        if isinstance(info, tuple):
            job_name = info[0] or ""
            skill = info[1] if len(info) > 1 and info[1] > 0 else adv_node.get_int("skill", 1)
        elif isinstance(info, str):
            job_name = info
            skill = adv_node.get_int("skill", 1)
        else:
            job_name = ""
            skill = adv_node.get_int("skill", 1)

        type_int = adv_node.get_int("type")  # fallback: the entity type int (51)
        entries.append(AdvisorEntry(id=entity_id, type_int=type_int, job_type_name=job_name, skill=skill))
    return entries


def _extract_government(node: ClausewitzNode) -> tuple[str, list[str]]:
    """Returns (government_type, list_of_reform_names)."""
    gov_node = node.get_node("government")
    if not gov_node:
        return "monarchy", []

    gov_type = gov_node.get_str("government", "monarchy")

    reforms: list[str] = []
    reform_stack = gov_node.get_node("reform_stack")
    if reform_stack:
        reforms_node = reform_stack.get_node("reforms")
        if reforms_node:
            # reforms are stored as positional quoted strings
            for r in reforms_node.array:
                if isinstance(r, str) and r:
                    reforms.append(r)

    return gov_type, reforms


def _extract_modifiers(node: ClausewitzNode) -> list[ModifierEntry]:
    """Extract named modifiers from modifier={} blocks."""
    result: list[ModifierEntry] = []
    for mod_node in node.get_list("modifier"):
        if not isinstance(mod_node, ClausewitzNode):
            continue
        name = mod_node.get_str("modifier") or mod_node.get_str("name")
        if not name:
            continue
        permanent = mod_node.get_str("permanent") == "yes"
        expiry = mod_node.get_str("date", "")
        result.append(ModifierEntry(name=name, permanent=permanent, expiry_date=expiry))
    # Also handle explicit permanent_modifier blocks
    for mod_node in node.get_list("permanent_modifier"):
        if not isinstance(mod_node, ClausewitzNode):
            continue
        name = mod_node.get_str("modifier") or mod_node.get_str("name")
        if name:
            result.append(ModifierEntry(name=name, permanent=True, expiry_date="-1.1.1"))
    return result


def _extract_monarch_personalities(node: ClausewitzNode) -> list[str]:
    """Get personality trait names from the current monarch block."""
    monarch_node = node.get_node("monarch")
    if not monarch_node:
        return []
    pers_node = monarch_node.get_node("personalities")
    if not pers_node:
        return []
    # Personalities stored as key=yes pairs
    return [k for k, v in pers_node.items() if v == "yes"]


def _extract_completed_missions(node: ClausewitzNode) -> list[str]:
    missions_node = node.get_node("completed_missions")
    if not missions_node:
        return []
    result: list[str] = []
    for m in missions_node.array:
        if isinstance(m, str) and m:
            result.append(m)
    return result


def _extract_religion(country_node: ClausewitzNode) -> str:
    """Try to get current religion; fall back to history entry."""
    # Direct field (present in many saves)
    r = country_node.get_str("religion")
    if r:
        return r
    # Fall back: last entry in history block
    hist = country_node.get_node("history")
    if hist:
        r = hist.get_str("religion")
        if r:
            return r
    return ""


def _extract_advisor_job_types(country_node: ClausewitzNode) -> dict[int, str]:
    """Scan country history dates to map advisor entity_id → job_type_name."""
    result: dict[int, str] = {}
    hist = country_node.get_node("history")
    if not hist:
        return result
    for _, date_node in hist.items():
        if not isinstance(date_node, ClausewitzNode):
            continue
        for adv_node in date_node.get_list("advisor"):
            if not isinstance(adv_node, ClausewitzNode):
                continue
            id_node = adv_node.get_node("id")
            entity_id = id_node.get_int("id") if id_node else adv_node.get_int("id")
            job_type = adv_node.get_str("type")
            if entity_id and job_type:
                result[entity_id] = job_type
    return result


def _extract_active_policies(country_node: ClausewitzNode) -> list[str]:
    result: list[str] = []
    for pol_node in country_node.get_list("active_policy"):
        if isinstance(pol_node, ClausewitzNode):
            name = pol_node.get_str("policy")
            if name:
                result.append(name)
    return result


def _extract_advisor_job_types_from_provinces(
    root: ClausewitzNode, advisor_ids: set[int]
) -> dict[int, tuple[str, int, str]]:
    """Scan province histories to map advisor entity_id -> (job_type_name, skill, name)."""
    result: dict[int, tuple[str, int, str]] = {}
    provinces = root.get_node("provinces")
    if not provinces:
        return result
    for _, prov_node in provinces.items():
        if not isinstance(prov_node, ClausewitzNode):
            continue
        hist = prov_node.get_node("history")
        if not hist:
            continue
        for date_key in hist.keys():
            for date_node in hist.get_list(date_key):
                if not isinstance(date_node, ClausewitzNode):
                    continue
                for adv_node in date_node.get_list("advisor"):
                    if not isinstance(adv_node, ClausewitzNode):
                        continue
                    id_node = adv_node.get_node("id")
                    entity_id = id_node.get_int("id") if id_node else adv_node.get_int("id")
                    job_type = adv_node.get_str("type")
                    skill = adv_node.get_int("skill", 1)
                    name = adv_node.get_str("name")
                    if entity_id in advisor_ids and (job_type or skill):
                        result[entity_id] = (job_type, skill, name)
    return result


def _extract_estate_discipline(country_node: ClausewitzNode) -> float:
    # placeholder — discipline from estates now computed in calc_discipline via game_data
    return 0.0


def _extract_estate_privileges(country_node: ClausewitzNode) -> list[tuple[str, float]]:
    """Return list of (privilege_name, land_share_fraction) for all active estate privileges."""
    result: list[tuple[str, float]] = []
    estates_node = country_node.get_node("estates")
    estate_list = estates_node.get_list("estate") if estates_node else country_node.get_list("estate")

    for estate_node in estate_list:
        if not isinstance(estate_node, ClausewitzNode):
            continue
        # land share: try field names used across EU4 versions
        land_frac = 0.0
        for key in ("territory", "land_share", "land_ownership"):
            v = estate_node.get_float(key)
            if v > 0:
                land_frac = v / 100.0 if v > 1 else v
                break

        gp = estate_node.get_node("granted_privileges")
        if not gp:
            continue
        for priv in gp.array:
            priv_name = ""
            if isinstance(priv, ClausewitzNode):
                # format: { "privilege_name" date } — name is first array item
                if priv.array:
                    priv_name = str(priv.array[0])
                else:
                    priv_name = priv.get_str("privilege") or priv.get_str("name")
            elif isinstance(priv, str):
                priv_name = priv
            if priv_name:
                result.append((priv_name, land_frac))
    return result


def _extract_starting_development(country_node: ClausewitzNode) -> float:
    # victory_card block (primary location in EU4 saves)
    vc = country_node.get_node("victory_card")
    if vc:
        v = vc.get_float("starting_development")
        if v > 0:
            return v
    # historic_stats_cache (alternative location)
    cache = country_node.get_node("historic_stats_cache")
    if cache:
        v = cache.get_float("starting_development")
        if v > 0:
            return v
    return 0.0


def _extract_dev_clicks(country_node: ClausewitzNode) -> int:
    """Return total development clicks for this country.

    Primary source: country.variables.no_of_dev_clicks (scripted variable
    set by the game engine for every manual development action).
    """
    variables_node = country_node.get_node("variables")
    if variables_node:
        val = variables_node.get_float("no_of_dev_clicks")
        if val > 0:
            return int(round(val))

    # Fallback approximation:
    # TODO [APPROX-4] dev_clicks fallback is raw_dev - starting_dev.
    raw_dev = country_node.get_float("raw_development")
    starting_dev = country_node.get_float("starting_development") or 0.0
    vc = country_node.get_node("victory_card")
    if vc:
        sd = vc.get_float("starting_development")
        if sd > 0:
            starting_dev = sd
    return max(0, int(round(raw_dev - starting_dev)))


def _extract_ships(country_node: ClausewitzNode) -> tuple[int, int, int, int]:
    """Returns (heavy_ships, trade_ships, galley_ships, transport_ships)."""
    heavy = trade = galley = transport = 0
    for navy_node in country_node.get_list("navy"):
        if not isinstance(navy_node, ClausewitzNode):
            continue
        for ship_node in navy_node.get_list("ship"):
            if not isinstance(ship_node, ClausewitzNode):
                continue
            ship_type = ship_node.get_str("type") or ""
            if ship_type in _HEAVY_SHIP_TYPES:
                heavy += 1
            elif ship_type in _LIGHT_SHIP_TYPES:
                trade += 1
            elif ship_type in _GALLEY_TYPES:
                galley += 1
            else:
                transport += 1
    return heavy, trade, galley, transport


_COLONIAL_NATION_RE = re.compile(r"^C\d{2}$")


def _extract_subjects(
    country_node: ClausewitzNode,
    tag: str,
    countries_node: ClausewitzNode | None,
) -> tuple[int, int, list[str]]:
    """Returns (colonial_nations_count, personal_unions_count, colonial_nation_tags)."""
    subjects_node = country_node.get_node("subjects")
    if not subjects_node:
        return 0, 0, []

    subject_tags = [s for s in subjects_node.array if isinstance(s, str)]
    colonial = 0
    personal_union = 0
    colonial_tags: list[str] = []

    for stag in subject_tags:
        # Try to get subject_type from the subject country node
        stype = ""
        if countries_node:
            scn = countries_node.get_node(stag)
            if scn:
                stype = scn.get_str("subject_type") or ""

        if not stype:
            # Heuristic: C00–C99 tags are colonial nations
            if _COLONIAL_NATION_RE.match(stag):
                stype = "colonial_nation"

        if stype == "colonial_nation":
            colonial += 1
            colonial_tags.append(stag)
        elif "union" in stype.lower():
            personal_union += 1

    return colonial, personal_union, colonial_tags


# ── players / country iteration ───────────────────────────────────────────────


def _extract_players_countries(root: ClausewitzNode) -> dict[str, str]:
    """Returns {player_name: country_tag}."""
    pc_node = root.get_node("players_countries")
    if not pc_node:
        return {}
    items = pc_node.array
    result: dict[str, str] = {}
    for i in range(0, len(items) - 1, 2):
        player = str(items[i])
        tag = str(items[i + 1])
        result[player] = tag
    return result


def _extract_estates(country_node: ClausewitzNode) -> list[tuple[str, float, float]]:
    """Extract list of (estate_type, loyalty_float, territory_float)."""
    result: list[tuple[str, float, float]] = []
    estate_list = country_node.get_list("estate")
    for e in estate_list:
        if isinstance(e, ClausewitzNode):
            etype = e.get_str("type")
            loyalty = e.get_float("loyalty")
            territory = e.get_float("territory")
            if etype:
                result.append((etype, loyalty, territory))
    return result


def _extract_army_drill(country_node: ClausewitzNode) -> float:
    total_drill = 0.0
    reg_count = 0
    for army_node in country_node.get_list("army"):
        if isinstance(army_node, ClausewitzNode):
            for reg_node in army_node.get_list("regiment"):
                if isinstance(reg_node, ClausewitzNode):
                    total_drill += reg_node.get_float("drill")
                    reg_count += 1
    return total_drill / reg_count if reg_count > 0 else 0.0


def _extract_countries(
    root: ClausewitzNode,
    tag_to_player: dict[str, str],
    advisor_job_map: dict[int, str] | None = None,
    dof_tags: set[str] | None = None,
    save_year: int = 1444,
) -> list[CountryData]:
    countries_node = root.get_node("countries")
    if not countries_node:
        return []

    result: list[CountryData] = []
    for tag, country_node in countries_node.items():
        if not isinstance(country_node, ClausewitzNode):
            continue
        if len(tag) != 3:
            continue
        # Only process human-played countries
        if tag not in tag_to_player and country_node.get_str("human") != "yes":
            continue

        gov_type, reforms = _extract_government(country_node)
        heavy, trade, galley, transport = _extract_ships(country_node)
        colonial_count, pu_count, colonial_tags = _extract_subjects(country_node, tag, countries_node)
        raw_dev = country_node.get_float("raw_development")
        starting_dev = _extract_starting_development(country_node)
        accepted_cultures_list = [x for x in country_node.get_list("accepted_culture") if isinstance(x, str)]
        avg_drill = _extract_army_drill(country_node)

        cd = CountryData(
            tag=tag,
            player=tag_to_player.get(tag, "Unknown"),
            raw_development=raw_dev,
            starting_development=starting_dev,
            army_tradition=country_node.get_float("army_tradition"),
            navy_tradition=country_node.get_float("navy_tradition"),
            legitimacy=country_node.get_float("legitimacy", 100.0),
            absolutism=country_node.get_float("absolutism"),
            horde_unity=country_node.get_float("horde_unity"),
            mercantilism=country_node.get_float("mercantilism"),
            splendor=country_node.get_float("splendor"),
            government_rank=country_node.get_int("government_rank", 1),
            max_manpower=country_node.get_float("max_manpower"),
            adm_tech=(
                country_node.get_node("technology").get_int("adm_tech", 3)
                if country_node.get_node("technology") else 3
            ),
            dip_tech=(
                country_node.get_node("technology").get_int("dip_tech", 3)
                if country_node.get_node("technology") else 3
            ),
            mil_tech=(
                country_node.get_node("technology").get_int("mil_tech", 3)
                if country_node.get_node("technology") else 3
            ),
            army_professionalism=country_node.get_float("army_professionalism"),
            average_army_drill=avg_drill,
            prestige=country_node.get_float("prestige"),
            power_projection=country_node.get_float("current_power_projection") or country_node.get_float("power_projection"),
            stability=country_node.get_float("stability"),
            golden_age=bool(country_node.get_str("has_golden_age") == "yes" or country_node.get_node("golden_age")),
            is_defender_of_faith=(
                country_node.get_str("defender_of_faith") == "yes"
                or tag in (dof_tags or set())
            ),
            save_year=save_year,
            accepted_culture_count=len(set(accepted_cultures_list)),
            accepted_cultures=accepted_cultures_list,
            innovativeness=country_node.get_float("innovativeness"),
            overextension=country_node.get_float("overextension_percentage") or country_node.get_float("overextension"),
            war_exhaustion=country_node.get_float("war_exhaustion"),
            corruption=country_node.get_float("corruption"),
            piety=country_node.get_float("piety"),
            patriarch_authority=country_node.get_float("patriarch_authority"),
            karma=country_node.get_float("karma"),
            harmony=country_node.get_float("harmony"),
            militarization=(
                country_node.get_float("militarization")
                or (country_node.get_node("government_mechanic").get_float("militarization") if country_node.get_node("government_mechanic") else 0.0)
            ),
            mandate=country_node.get_float("mandate"),
            devotion=country_node.get_float("devotion"),
            republican_tradition=country_node.get_float("republican_tradition", 100.0 if gov_type == "republic" else 0.0),
            meritocracy=country_node.get_float("meritocracy"),
            estate_discipline=_extract_estate_discipline(country_node),
            estate_privileges=_extract_estate_privileges(country_node),
            estates=_extract_estates(country_node),
            advisor_job_types=advisor_job_map or {},
            active_policies=_extract_active_policies(country_node),
            religion=_extract_religion(country_node),
            government_type=gov_type,
            active_idea_groups=_extract_ideas(country_node),
            government_reforms=reforms,
            advisors=_extract_advisors(country_node, advisor_job_map or {}),
            modifiers=_extract_modifiers(country_node),
            completed_missions=_extract_completed_missions(country_node),
            monarch_personalities=_extract_monarch_personalities(country_node),
            # ── new fields ──
            heavy_ships=heavy,
            trade_ships=trade,
            galley_ships=galley,
            transport_ships=transport,
            colonial_nations_count=colonial_count,
            personal_unions_count=pu_count,
            colonial_nation_tags=colonial_tags,
            dev_clicks=_extract_dev_clicks(country_node),
            used_governing_capacity=country_node.get_float("used_governing_capacity"),
            estimated_monthly_income=country_node.get_float("estimated_monthly_income") or 0.0,
            treasury=country_node.get_float("treasury") or 0.0,
        )

        # Victory card
        vc_node = country_node.get_node("victory_card")
        if vc_node:
            cd.victory_card_score = vc_node.get_float("score")

        result.append(cd)

    return result


# ── public entry point ────────────────────────────────────────────────────────


def load_save(save_path: str) -> tuple[dict[str, str], list[CountryData]]:
    """
    Parse an EU4 save file (.eu4 zip archive).

    Returns:
        players_countries: {player_name: country_tag}
        countries: list of CountryData for all human-played countries
    """
    players_countries, countries, _ = load_save_full(save_path)
    return players_countries, countries


def _scan_provinces(
    root: ClausewitzNode, player_tags: set[str], coastal_provinces: set[int] | None = None,
) -> tuple[dict, dict[str, int], dict[str, str], dict[str, float], dict[str, set], dict[str, float], dict[str, float], dict[str, list[tuple[str, str]]], dict[int, dict[str, Any]]]:
    """
    Scan the provinces node once and return:
      most_dev_province      — dict with the highest-dev player province
      tag_province_counts    — {tag: province_count}
      province_owner_map     — {province_id_str: owner_tag} for ALL provinces
      tag_base_manpower      — {tag: sum_of_base_manpower} for ALL tags
      tag_province_sets      — {tag: set_of_province_id_ints} for ALL tags
      tag_province_lfl       — {tag: sum_of_province_land_force_limits} for ALL tags
      tag_province_nfl       — {tag: sum_of_province_naval_force_limits} for ALL tags
      tag_province_modifiers — {tag: list[(modifier_name, province_name)]} for ALL tags
      prov_info_map          — {province_id_int: {name, owner, dev, tg, aut, tc, has_shipyard}}
    """
    provinces_node = root.get_node("provinces")
    best: dict = {"development": 0.0}
    tag_count: dict[str, int] = {}
    province_owner_map: dict[str, str] = {}
    tag_base_manpower: dict[str, float] = {}
    tag_province_sets: dict[str, set] = {}
    tag_province_lfl: dict[str, float] = {}
    tag_province_nfl: dict[str, float] = {}
    tag_province_modifiers: dict[str, list[tuple[str, str]]] = {}
    prov_info_map: dict[int, dict[str, Any]] = {}

    if not provinces_node:
        return best, tag_count, province_owner_map, tag_base_manpower, tag_province_sets, tag_province_lfl, tag_province_nfl, tag_province_modifiers, prov_info_map

    for pid, prov_node in provinces_node.items():
        if not isinstance(prov_node, ClausewitzNode):
            continue
        owner = prov_node.get_str("owner")
        if not owner:
            continue
        int_pid = 0
        try:
            int_pid = abs(int(pid))
        except (ValueError, TypeError):
            pass

        # Track all tags
        tag_count[owner] = tag_count.get(owner, 0) + 1
        province_owner_map[str(pid)] = owner
        if owner not in tag_province_sets:
            tag_province_sets[owner] = set()
        if int_pid:
            province_owner_map[str(int_pid)] = owner
            province_owner_map[f"-{int_pid}"] = owner
            tag_province_sets[owner].add(int_pid)
        tag_base_manpower[owner] = tag_base_manpower.get(owner, 0.0) + prov_node.get_float("base_manpower")

        # Collect province modifiers
        prov_name = prov_node.get_str("name") or str(pid)
        for m in prov_node.get_list("modifier"):
            mname = ""
            if isinstance(m, ClausewitzNode):
                mname = m.get_str("modifier")
            elif isinstance(m, str):
                mname = m
            if mname:
                if owner not in tag_province_modifiers:
                    tag_province_modifiers[owner] = []
                tag_province_modifiers[owner].append((mname, prov_name))

        # Province force limit calculation
        tax = prov_node.get_float("base_tax")
        prod = prov_node.get_float("base_production")
        mp = prov_node.get_float("base_manpower")
        dev = tax + prod + mp

        tg = prov_node.get_str("trade_goods")
        autonomy = prov_node.get_float("local_autonomy")
        buildings_node = prov_node.get_node("buildings")
        b_keys = list(buildings_node.keys()) if buildings_node else []
        tc = prov_node.get_str("active_trade_company")

        # Check territorial core vs full core / trade company (75% minimum autonomy floor in EU4)
        is_territory = bool(tc or prov_node.get_str("territorial_core") or (owner and owner in prov_node.get_list("territorial_core")))
        eff_autonomy = max(autonomy, 75.0) if is_territory else autonomy
        aut_factor = max(0.0, 1.0 - (eff_autonomy / 100.0))

        # Land Force Limit contribution
        lfl = dev * 0.1 * aut_factor
        if tg == "grain":
            lfl += 0.5 * aut_factor
        if "regimental_camp" in b_keys:
            lfl += 1.0
        if "conscription_center" in b_keys:
            lfl += 2.0
        if "externalministry" in b_keys:
            lfl += 4.0
        if "native_fortified_house" in b_keys:
            lfl += 5.0
        tag_province_lfl[owner] = tag_province_lfl.get(owner, 0.0) + lfl

        # Naval Force Limit contribution (coastal provinces only)
        is_coastal = (int_pid in coastal_provinces) if coastal_provinces else (
            "shipyard" in b_keys or "grand_shipyard" in b_keys or "dock" in b_keys or "drydock" in b_keys or prov_node.get_str("port")
        )
        nfl = 0.0
        if is_coastal:
            nfl += dev * 0.1 * aut_factor
            if tg == "naval_supplies":
                nfl += 0.5 * aut_factor
            if "shipyard" in b_keys:
                nfl += 2.0
            if "grand_shipyard" in b_keys:
                nfl += 4.0
            if "navalministry" in b_keys:
                nfl += 7.0

        tag_province_nfl[owner] = tag_province_nfl.get(owner, 0.0) + nfl

        deva = prov_node.get_float("devastation") or 0.0

        prov_info_map[int_pid] = {
            "name": prov_name,
            "owner": owner,
            "dev": round(dev, 1),
            "tg": tg or "",
            "aut": round(autonomy, 1),
            "tc": bool(tc),
            "has_shipyard": "shipyard" in b_keys or "grand_shipyard" in b_keys,
            "devastation": round(deva, 1),
        }

        # Best dev province (player-owned only)
        if owner not in player_tags:
            continue
        if dev > best["development"]:
            best = {
                "id": pid,
                "name": prov_node.get_str("name"),
                "owner": owner,
                "development": dev,
            }
    return (
        best,
        tag_count,
        province_owner_map,
        tag_base_manpower,
        tag_province_sets,
        tag_province_lfl,
        tag_province_nfl,
        tag_province_modifiers,
        prov_info_map,
    )


def _scan_most_dev_province(root: ClausewitzNode, player_tags: set[str]) -> dict:
    most_dev, *_ = _scan_provinces(root, player_tags)
    return most_dev


def _extract_raw_monuments(
    root: ClausewitzNode,
) -> list[tuple[str, int, str | None]]:
    """
    Parse root.great_projects and root.provinces to return a list of
    (monument_name, tier_int, province_id_str_or_None) for every entry.
    """
    result: list[tuple[str, int, str | None]] = []
    seen: set[tuple[str, str | None]] = set()

    # Extract tiers and province locations from top-level great_projects node
    monument_tiers: dict[str, int] = {}
    gp = root.get_node("great_projects")
    if gp:
        for name, node in gp.items():
            if isinstance(node, ClausewitzNode):
                tier = node.get_int("tier", node.get_int("development_tier", 1))
                monument_tiers[name] = tier
                prov = node.get_str("province")
                if prov and (name, prov) not in seen:
                    result.append((name, tier, prov))
                    seen.add((name, prov))

    # Extract monument locations from provinces
    provinces_node = root.get_node("provinces")
    if provinces_node:
        for pid_str, pdata in provinces_node.items():
            if not isinstance(pdata, ClausewitzNode):
                continue
            try:
                int_pid = abs(int(pid_str))
            except (ValueError, TypeError):
                continue

            gp_list = pdata.get_list("great_projects") or []
            for g in gp_list:
                mname = ""
                if isinstance(g, ClausewitzNode):
                    for sub_m in g.array:
                        mname = str(sub_m)
                        tier = monument_tiers.get(mname, 1)
                        if (mname, str(int_pid)) not in seen:
                            result.append((mname, tier, str(int_pid)))
                            seen.add((mname, str(int_pid)))
                elif isinstance(g, str):
                    mname = g
                    tier = monument_tiers.get(mname, 1)
                    if (mname, str(int_pid)) not in seen:
                        result.append((mname, tier, str(int_pid)))
                        seen.add((mname, str(int_pid)))

    return result


def _extract_hegemony_map(root: ClausewitzNode) -> dict[str, str]:
    """Returns {tag: hegemony_type} for any active hegemons.

    hegemony_type ∈ {"naval", "mil", "economy"}
    """
    result: dict[str, str] = {}

    # Check great_powers node
    gp = root.get_node("great_powers")
    if gp:
        for heg_type in ("naval", "mil", "economy"):
            for key_variant in (f"{heg_type}_hegemon", "hegemon"):
                node = gp.get_node(key_variant)
                if node:
                    htag = node.get_str("tag") or node.get_str("country")
                    htype = node.get_str("type") or heg_type
                    if htag:
                        result[htag] = htype
                        break

    # Also check root-level hegemon blocks
    for heg_type in ("naval", "mil", "economy"):
        node = root.get_node(f"{heg_type}_hegemon")
        if node:
            htag = node.get_str("tag") or node.get_str("country")
            if htag:
                result[htag] = heg_type

    # Check individual country nodes for hegemon field
    # (some EU4 versions store it directly on the country)
    countries_node = root.get_node("countries")
    if countries_node:
        for tag, cn in countries_node.items():
            if not isinstance(cn, ClausewitzNode) or len(tag) != 3:
                continue
            hval = cn.get_str("hegemon")
            if hval:
                result[tag] = hval

    return result


def _extract_casualties_from_text(gamestate_text: str) -> dict[int, dict[str, Any]]:
    """Extract total casualties, battle counts, and top battle per province."""
    prov_battles: dict[int, dict[str, Any]] = {}
    pattern = re.compile(r'battle\s*=\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}')
    for m in pattern.finditer(gamestate_text):
        b_str = m.group(1)
        loc_m = re.search(r'location\s*=\s*(\d+)', b_str)
        if not loc_m:
            continue
        pid = int(loc_m.group(1))

        name_m = re.search(r'name\s*=\s*"([^"]*)"', b_str)
        b_name = name_m.group(1) if name_m else f"Battle in {pid}"

        losses = 0
        for loss_m in re.finditer(r'losses\s*=\s*(\d+)', b_str):
            losses += int(loss_m.group(1))

        if losses > 0:
            if pid not in prov_battles:
                prov_battles[pid] = {
                    "casualties": losses,
                    "battles": 1,
                    "top_battle": b_name,
                    "max_loss": losses,
                }
            else:
                prov_battles[pid]["casualties"] += losses
                prov_battles[pid]["battles"] += 1
                if losses > prov_battles[pid]["max_loss"]:
                    prov_battles[pid]["max_loss"] = losses
                    prov_battles[pid]["top_battle"] = b_name

    return prov_battles


def _load_prov_to_area() -> dict[int, str]:
    """Load province-to-area mapping from area.txt."""
    area_paths = [
        os.path.join("eu4_data", "map", "area.txt"),
        os.path.join("eu4_data", "mod", "3783710427", "map", "area.txt"),
        r"C:\Program Files (x86)\Steam\steamapps\common\Europa Universalis IV\map\area.txt",
    ]
    prov_to_area: dict[int, str] = {}
    for path in area_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
                for m in re.finditer(r'([a-zA-Z0-9_]+)\s*=\s*\{([^}]*)\}', txt):
                    aname = m.group(1)
                    clean_body = re.sub(r'#.*', '', m.group(2))
                    for tok in clean_body.split():
                        if tok.isdigit():
                            prov_to_area[int(tok)] = aname
                if prov_to_area:
                    break
            except Exception:
                pass
    return prov_to_area


def _extract_area_prosperity(gamestate_text: str) -> dict[tuple[str, str], float]:
    """Extract prosperity per (area_name, country_tag) from map_area_data in gamestate."""
    p_start = gamestate_text.find("map_area_data{")
    if p_start == -1:
        return {}
    depth = 0
    sub = gamestate_text[p_start + len("map_area_data"):]
    area_text = ""
    for idx, c in enumerate(sub):
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                area_text = sub[:idx + 1]
                break

    pros_map: dict[tuple[str, str], float] = {}
    for m in re.finditer(r'([a-zA-Z0-9_]+)\s*=\s*\{([^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*)\}', area_text):
        aname = m.group(1)
        body = m.group(2)
        p_m = re.search(r'prosperity\s*=\s*([0-9.]+)', body)
        c_m = re.search(r'country\s*=\s*"?([A-Z0-9_]+)"?', body)
        if p_m and c_m:
            pros = float(p_m.group(1))
            tag = c_m.group(1)
            if pros > 0:
                pros_map[(aname, tag)] = pros
    return pros_map


def load_save_full(
    save_path: str, game_data: Any | None = None
) -> tuple[dict[str, str], list[CountryData], dict, dict]:
    """
    Parse an EU4 save file once and return all required data.

    Returns:
        players_countries: {player_name: country_tag}
        countries: list of CountryData for human-played countries
        most_dev_province: dict with province info for highest-dev player province
        extra: dict with province-level data needed for post-game-data processing:
            - province_owner_map: {province_id_str: owner_tag}
            - tag_base_manpower:  {tag: sum_base_manpower}
            - tag_province_sets:  {tag: set[province_id_int]}
            - raw_monuments:      [(name, tier, province_id_str_or_None)]
    """
    save_year = 1444
    with open(save_path, "rb") as f:
        raw_bytes = io.BytesIO(f.read())
    with zipfile.ZipFile(raw_bytes) as z:
        if "meta" in z.namelist():
            with z.open("meta") as m:
                meta_text = m.read().decode("utf-8", errors="ignore")
                m_date = re.search(r"date=(\d+)\.", meta_text)
                if m_date:
                    save_year = int(m_date.group(1))
        with z.open("gamestate") as gs:
            gamestate_text = gs.read().decode("utf-8", errors="ignore")
            if save_year == 1444:
                g_date = re.search(r"\bdate=(\d+)\.", gamestate_text)
                if g_date:
                    save_year = int(g_date.group(1))

    root = parse(gamestate_text)

    players_countries = _extract_players_countries(root)
    tag_to_player = {tag: player for player, tag in players_countries.items()}

    # Build global defender-of-faith set: check religion_instance_data
    dof_tags: set[str] = set()
    rid = root.get_node("religion_instance_data")
    if rid:
        for _rel_name, rel_node in rid.items():
            if isinstance(rel_node, ClausewitzNode):
                dof = rel_node.get_str("defender")
                if dof:
                    dof_tags.add(dof)

    # Build advisor job-type map from province histories (advisors are recorded there)
    all_advisor_ids: set[int] = set()
    countries_node = root.get_node("countries")
    if countries_node:
        for tag in tag_to_player:
            cn = countries_node.get_node(tag)
            if cn:
                for a in cn.get_list("advisor"):
                    if isinstance(a, ClausewitzNode):
                        id_node = a.get_node("id")
                        eid = id_node.get_int("id") if id_node else a.get_int("id")
                        if eid:
                            all_advisor_ids.add(eid)
    advisor_job_map = _extract_advisor_job_types_from_provinces(root, all_advisor_ids)

    countries = _extract_countries(root, tag_to_player, advisor_job_map, dof_tags, save_year=save_year)

    # Province scan — returns extended data
    coastal_provinces = getattr(game_data, "coastal_provinces", None) if game_data else None
    most_dev, tag_province_counts, province_owner_map, tag_base_manpower, tag_province_sets, tag_province_lfl, tag_province_nfl, tag_province_modifiers, prov_info_map = \
        _scan_provinces(root, set(tag_to_player.keys()), coastal_provinces=coastal_provinces)

    # Enrich province data with area and state prosperity
    prov_to_area = _load_prov_to_area()
    area_prosperity = _extract_area_prosperity(gamestate_text)
    for pid, pdata in prov_info_map.items():
        aname = prov_to_area.get(pid, "")
        powner = pdata.get("owner", "")
        pros = area_prosperity.get((aname, powner), 0.0) if aname and powner else 0.0
        pdata["prosperity"] = round(pros, 1)
        if aname:
            pdata["area"] = aname

    # Merge battle casualties into province metadata
    casualties_map = _extract_casualties_from_text(gamestate_text)
    for pid, cdata in casualties_map.items():
        if pid in prov_info_map:
            prov_info_map[pid].update(cdata)
        else:
            prov_info_map[pid] = cdata

    hegemony_map = _extract_hegemony_map(root)
    raw_monuments = _extract_raw_monuments(root)

    # Extract dependency relationships from diplomacy block
    dependency_map: dict[tuple[str, str], str] = {}
    diplo_node = root.get_node("diplomacy")
    if diplo_node:
        for dep in diplo_node.get_list("dependency"):
            if isinstance(dep, ClausewitzNode):
                f_tag = dep.get_str("first")
                s_tag = dep.get_str("second")
                stype = dep.get_str("subject_type")
                if f_tag and s_tag and stype:
                    dependency_map[(f_tag, s_tag)] = stype

    curia_controller = ""
    papacy_node = root.get_node("papacy")
    if papacy_node:
        curia_controller = papacy_node.get_str("controller")

    hre_emperor = ""
    empire_node = root.get_node("empire") or root.get_node("hre")
    if empire_node:
        hre_emperor = empire_node.get_str("emperor")

    tag_names: dict[str, str] = {}
    if countries_node:
        for ctag, cnode in countries_node.items():
            if isinstance(cnode, ClausewitzNode):
                cname = cnode.get_str("name") or cnode.get_str("custom_name")
                if cname:
                    tag_names[ctag] = cname

    for cd in countries:
        cd.is_curia_controller = (cd.tag == curia_controller)
        cd.is_hre_emperor = (cd.tag == hre_emperor)
        cd.province_count = tag_province_counts.get(cd.tag, 0)
        cd.hegemony = hegemony_map.get(cd.tag, "")
        cd.base_manpower_sum = tag_base_manpower.get(cd.tag, 0.0)
        cd.province_lfl = tag_province_lfl.get(cd.tag, 0.0)
        cd.province_nfl = tag_province_nfl.get(cd.tag, 0.0)
        cd.province_modifiers = tag_province_modifiers.get(cd.tag, [])

        # Subjects force limit contribution
        sub_lfl = 0.0
        sub_nfl = 0.0
        large_cn_count = 0
        if countries_node:
            cn = countries_node.get_node(cd.tag)
            if cn:
                cd.naval_doctrine = cn.get_str("naval_doctrine")
                cd.is_subject = bool(cn.get_str("overlord") or cn.get_str("is_subject") == "yes")
                subs = cn.get_node("subjects")
                if subs:
                    cd.subjects = [s for s in subs.array if isinstance(s, str)]
                    for stag in cd.subjects:
                        sub_prov_lfl = tag_province_lfl.get(stag, 0.0)
                        sub_prov_nfl = tag_province_nfl.get(stag, 0.0)
                        stype = dependency_map.get((cd.tag, stag), "")
                        if not stype:
                            scn = countries_node.get_node(stag)
                            stype = scn.get_str("subject_type") if scn else ""
                        if not stype and _COLONIAL_NATION_RE.match(stag):
                            stype = "colony"

                        is_march = "march" in stype.lower()
                        is_cn = _COLONIAL_NATION_RE.match(stag) or "colony" in stype.lower()

                        if is_cn:
                            cn_prov_count = tag_province_counts.get(stag, 0)
                            # CNs transfer 2% of their province naval FL in EU4
                            sub_nfl += 0.02 * sub_prov_nfl
                            if cn_prov_count >= 10:
                                sub_lfl += 5.0
                                large_cn_count += 1
                        elif "tributary" not in stype.lower() and "personal_union" not in stype.lower() and "union" not in stype.lower():
                            # Regular vassal / march / client state / appanage / daimyo
                            pct = 0.20 if is_march else 0.10
                            sub_lfl += 1.0 + pct * (6.0 + sub_prov_lfl)
                            if is_march:
                                sub_nfl += 0.10 * (12.0 + sub_prov_nfl)

        cd.subject_lfl = sub_lfl
        cd.subject_nfl = sub_nfl
        cd.large_cn_count = large_cn_count

    extra = {
        "province_owner_map": province_owner_map,
        "tag_base_manpower": tag_base_manpower,
        "tag_province_sets": tag_province_sets,
        "tag_province_lfl": tag_province_lfl,
        "tag_province_nfl": tag_province_nfl,
        "raw_monuments": raw_monuments,
        "prov_info_map": prov_info_map,
        "tag_names": tag_names,
    }

    return players_countries, countries, most_dev, extra


def enrich_with_historical_ideas(
    countries: list[CountryData],
    eu4_data_path: str = "./eu4_data",
) -> None:
    """Load historical idea group names from eu4_data/countries/ files."""
    tag_to_file: dict[str, str] = {}
    countries_txt = os.path.join(eu4_data_path, "00_countries.txt")
    if not os.path.isfile(countries_txt):
        return
    with open(countries_txt, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"(\w+)\s*=\s*\"([^\"]+)\"", line)
            if m:
                tag_to_file[m.group(1)] = m.group(2)

    for cd in countries:
        fname = tag_to_file.get(cd.tag)
        if not fname:
            continue
        fpath = os.path.join(eu4_data_path, fname)
        if not os.path.isfile(fpath):
            continue
        with open(fpath, encoding="utf-8", errors="ignore") as f:
            contents = f.read()
        m = re.search(r"historical_idea_groups\s*=\s*\{([^}]+)\}", contents)
        if m:
            cd.historical_idea_groups = [
                idea.strip()
                for idea in m.group(1).splitlines()
                if idea.strip()
            ]
