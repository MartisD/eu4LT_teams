"""
Game data loader.

Reads EU4 game files from disk and builds modifier lookup tables used by the
calculators.  Call load_game_data() once at startup; pass the returned
GameData instance to every calculator.

Required game files (paths relative to eu4_install_dir / mod_dir):
  common/ideas/*.txt
  common/government_reforms/*.txt
  common/advisortypes/00_advisortypes.txt
  common/event_modifiers/*.txt
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Optional

from clausewitz.parser import parse, ClausewitzNode


@dataclass
class TriggeredModifier:
    name: str
    potential: dict  # parsed scalar conditions from potential={} block
    trigger: dict   # parsed scalar conditions from trigger={} block
    modifiers: dict[str, float]


# ── data classes ─────────────────────────────────────────────────────────────


@dataclass
class IdeaGroup:
    name: str
    # traditions = bonuses at slot 0 (before any idea is picked)
    traditions: dict[str, float] = field(default_factory=dict)
    # ideas[0..6] = bonuses unlocked as each idea is picked
    ideas: list[dict[str, float]] = field(default_factory=list)
    # bonus = ambition bonus after all 7 ideas are picked
    bonus: dict[str, float] = field(default_factory=dict)


@dataclass
class GameData:
    # idea_group_name → IdeaGroup
    idea_groups: dict[str, IdeaGroup] = field(default_factory=dict)
    # reform_name → {modifier_key: value}
    government_reforms: dict[str, dict[str, float]] = field(default_factory=dict)
    # advisor_type_int → advisor_type_name  (index = position in advisortypes file)
    advisor_type_names: dict[int, str] = field(default_factory=dict)
    # advisor_type_name → {modifier_key: value}
    advisor_modifiers: dict[str, dict[str, float]] = field(default_factory=dict)
    # named modifier → {modifier_key: value}  (from event_modifiers + mission_modifiers)
    event_modifiers: dict[str, dict[str, float]] = field(default_factory=dict)
    # policy_name → {modifier_key: value}
    policies: dict[str, dict[str, float]] = field(default_factory=dict)
    # estate_privilege_name → {modifier_key: value}  (from benefits / modifier / country_modifier)
    estate_privileges: dict[str, dict[str, float]] = field(default_factory=dict)
    # estate_privilege_name → {modifier_key: scale_value}  (scale by land_share/100)
    estate_privilege_land_modifiers: dict[str, dict[str, float]] = field(default_factory=dict)
    # estate_name → {modifier_key: value} when loyalty >= 60%
    estate_happy_modifiers: dict[str, dict[str, float]] = field(default_factory=dict)
    # estate_name → {modifier_key: value} when loyalty < 30%
    estate_angry_modifiers: dict[str, dict[str, float]] = field(default_factory=dict)
    # government_mechanic_name or power_name → {modifier_key: max_value_at_100}
    government_mechanics: dict[str, dict[str, float]] = field(default_factory=dict)
    # religion_name → {modifier_key: value}  (from religion country = {} block)
    religion_country_modifiers: dict[str, dict[str, float]] = field(default_factory=dict)
    # static_modifier_name → {modifier_key: value}  (prestige, defender_of_faith, etc.)
    static_modifiers: dict[str, dict[str, float]] = field(default_factory=dict)
    # triggered modifiers with conditions to evaluate at runtime
    triggered_modifiers: dict[str, TriggeredModifier] = field(default_factory=dict)
    # idea_name → (group_name, slot_index) for has_idea condition evaluation
    idea_name_to_slot: dict[str, tuple[str, int]] = field(default_factory=dict)
    # great_project_name → {tier_int: {modifier_key: value}}  (country_modifiers per tier)
    great_projects: dict[str, dict[int, dict[str, float]]] = field(default_factory=dict)
    # colonial_region_name → set of province IDs belonging to that region
    colonial_region_provinces: dict[str, set] = field(default_factory=dict)
    # government_rank_int → {modifier_key: value}
    government_rank_modifiers: dict[int, dict[str, float]] = field(default_factory=dict)
    # ruler_personality_name → {modifier_key: value}
    ruler_personalities: dict[str, dict[str, float]] = field(default_factory=dict)
    # set of coastal province IDs computed from map BMP and definitions
    coastal_provinces: set[int] = field(default_factory=set)
    # tech_type ('adm'/'dip'/'mil') → list of year per tech level (index 0 = tech 1)
    tech_years: dict[str, list[int]] = field(default_factory=dict)

    def get_advisor_type_name(self, type_int: int) -> Optional[str]:
        return self.advisor_type_names.get(type_int)

    def get_modifier_effects(self, modifier_name: str) -> dict[str, float]:
        """Look up the effects of a named modifier (from events/missions)."""
        return self.event_modifiers.get(modifier_name, {})

    def compute_idea_modifiers(
        self, group_name: str, completed_levels: int
    ) -> dict[str, float]:
        """Sum all modifiers unlocked by completing `completed_levels` ideas (0–7)."""
        group = self.idea_groups.get(group_name)
        if group is None:
            return {}
        result: dict[str, float] = {}

        # Traditions active as soon as the first idea in the group is taken
        if completed_levels > 0:
            for k, v in group.traditions.items():
                result[k] = result.get(k, 0.0) + v

        for i in range(min(completed_levels, len(group.ideas))):
            for k, v in group.ideas[i].items():
                result[k] = result.get(k, 0.0) + v

        if completed_levels >= 7:
            for k, v in group.bonus.items():
                result[k] = result.get(k, 0.0) + v

        return result


def _parse_modifier_block(node: ClausewitzNode) -> dict[str, float]:
    """Extract numeric modifier key-value pairs from a block node."""
    result: dict[str, float] = {}
    for key, val in node.items():
        if isinstance(val, str):
            try:
                result[key] = float(val)
            except ValueError:
                pass
    return result


def _parse_conditions(node: ClausewitzNode) -> dict[str, Any]:
    """Extract simple scalar conditions and NOT-blocks from a condition node."""
    result: dict[str, Any] = {}
    for key, val in node.items():
        if isinstance(val, ClausewitzNode):
            result[key] = _parse_conditions(val)  # handles NOT={}, OR={}, etc.
        elif isinstance(val, str):
            result[key] = val
    return result


def _load_ideas_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for group_name, group_node in root.items():
        if not isinstance(group_node, ClausewitzNode):
            continue

        ig = IdeaGroup(name=group_name)

        for slot_key, slot_val in group_node.items():
            if not isinstance(slot_val, ClausewitzNode):
                continue
            mods = _parse_modifier_block(slot_val)
            if slot_key == "start":
                ig.traditions = mods
            elif slot_key == "bonus":
                ig.bonus = mods
            elif slot_key not in ("trigger", "free", "ai_will_do", "category", "important"):
                slot_idx = len(ig.ideas)
                ig.ideas.append(mods)
                # record idea name → (group, slot) for has_idea evaluation
                data.idea_name_to_slot[slot_key] = (group_name, slot_idx)

        data.idea_groups[group_name] = ig


def _load_reforms_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for reform_name, reform_node in root.items():
        if not isinstance(reform_node, ClausewitzNode):
            continue
        mods_node = reform_node.get_node("modifiers") or reform_node.get_node("modifier")
        if mods_node:
            data.government_reforms[reform_name] = _parse_modifier_block(mods_node)
        else:
            # Some reforms have modifiers inline
            inline = _parse_modifier_block(reform_node)
            if inline:
                data.government_reforms[reform_name] = inline


def _load_advisortypes_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    # Advisor types are ordered; 0-based index = advisor.type integer in save.
    idx = 0
    for type_name, type_node in root.items():
        if not isinstance(type_node, ClausewitzNode):
            continue
        data.advisor_type_names[idx] = type_name
        # Root-level numeric values are the base modifiers (e.g. discipline = 0.05)
        mods = _parse_modifier_block(type_node)
        # Also merge any nested modifier={} sub-block
        mods_node = type_node.get_node("modifier")
        if mods_node:
            mods.update(_parse_modifier_block(mods_node))
        if mods:
            data.advisor_modifiers[type_name] = mods
        idx += 1


def _load_event_modifiers_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for mod_name, mod_node in root.items():
        if not isinstance(mod_node, ClausewitzNode):
            continue
        mods = _parse_modifier_block(mod_node)
        if mods:
            data.event_modifiers[mod_name] = mods


def _load_policies_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for pol_name, pol_node in root.items():
        if not isinstance(pol_node, ClausewitzNode):
            continue
        mods = _parse_modifier_block(pol_node)
        if mods:
            data.policies[pol_name] = mods


def _load_estate_privileges_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for priv_name, priv_node in root.items():
        if not isinstance(priv_node, ClausewitzNode):
            continue

        # Standard flat and percentage modifiers
        all_mods: dict[str, float] = {}
        for block_key in ("benefits", "penalties", "country_modifier", "modifier"):
            bnode = priv_node.get_node(block_key)
            if bnode:
                all_mods.update(_parse_modifier_block(bnode))
        if all_mods:
            data.estate_privileges[priv_name] = all_mods

        land_mod_node = priv_node.get_node("modifier_by_land_ownership")
        if land_mod_node:
            mods = _parse_modifier_block(land_mod_node)
            if mods:
                data.estate_privilege_land_modifiers[priv_name] = mods


def _load_estates_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for estate_name, estate_node in root.items():
        if not isinstance(estate_node, ClausewitzNode):
            continue
        happy_node = estate_node.get_node("country_modifier_happy")
        if happy_node:
            mods = _parse_modifier_block(happy_node)
            if mods:
                data.estate_happy_modifiers.setdefault(estate_name, {}).update(mods)
        angry_node = estate_node.get_node("country_modifier_angry")
        if angry_node:
            mods = _parse_modifier_block(angry_node)
            if mods:
                data.estate_angry_modifiers.setdefault(estate_name, {}).update(mods)


def _load_government_mechanics_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for mech_name, mech_node in root.items():
        if not isinstance(mech_node, ClausewitzNode):
            continue
        powers_node = mech_node.get_node("powers")
        if not powers_node:
            continue
        for power_name, power_node in powers_node.items():
            if not isinstance(power_node, ClausewitzNode):
                continue
            scaled_mods: dict[str, float] = {}
            for sm in power_node.get_list("scaled_modifier"):
                if isinstance(sm, ClausewitzNode):
                    mod_node = sm.get_node("modifier")
                    if mod_node:
                        scaled_mods.update(_parse_modifier_block(mod_node))
            if scaled_mods:
                data.government_mechanics[power_name] = scaled_mods
                data.government_mechanics[mech_name] = scaled_mods


def _load_religions_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    # Religion files have: group = { religion_name = { country = { modifier = value } } }
    for _group_name, group_node in root.items():
        if not isinstance(group_node, ClausewitzNode):
            continue
        for rel_name, rel_node in group_node.items():
            if not isinstance(rel_node, ClausewitzNode):
                continue
            country_block = rel_node.get_node("country")
            if country_block:
                mods = _parse_modifier_block(country_block)
                if mods:
                    data.religion_country_modifiers[rel_name] = mods


def _load_static_modifiers_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for mod_name, mod_node in root.items():
        if not isinstance(mod_node, ClausewitzNode):
            continue
        mods = _parse_modifier_block(mod_node)
        if mods:
            data.static_modifiers[mod_name] = mods


def _load_triggered_modifiers_file(path: str, data: GameData) -> None:
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for mod_name, mod_node in root.items():
        if not isinstance(mod_node, ClausewitzNode):
            continue
        mods = _parse_modifier_block(mod_node)
        if not mods:
            continue
        potential_node = mod_node.get_node("potential")
        trigger_node = mod_node.get_node("trigger")
        data.triggered_modifiers[mod_name] = TriggeredModifier(
            name=mod_name,
            potential=_parse_conditions(potential_node) if potential_node else {},
            trigger=_parse_conditions(trigger_node) if trigger_node else {},
            modifiers=mods,
        )


def _load_great_projects_file(path: str, data: GameData) -> None:
    """Load great project / monument files, extracting country_modifiers and modifier per tier."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for project_name, project_node in root.items():
        if not isinstance(project_node, ClausewitzNode):
            continue
        tier_mods: dict[int, dict[str, float]] = {}
        for i in range(4):  # EU4 monuments have tier 0–3
            tier_node = project_node.get_node(f"tier_{i}")
            if not tier_node:
                continue
            mods: dict[str, float] = {}
            cm_node = tier_node.get_node("country_modifiers")
            if cm_node:
                mods.update(_parse_modifier_block(cm_node))
            m_node = tier_node.get_node("modifier")
            if m_node:
                mods.update(_parse_modifier_block(m_node))
            if mods:
                tier_mods[i] = mods
        if tier_mods:
            data.great_projects[project_name] = tier_mods


def _load_colonial_regions_file(path: str, data: GameData) -> None:
    """Load colonial regions, mapping region name → set of province IDs."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for region_name, region_node in root.items():
        if not isinstance(region_node, ClausewitzNode):
            continue
        provinces_node = region_node.get_node("provinces")
        if not provinces_node:
            continue
        province_ids: set[int] = set()
        for pid in provinces_node.array:
            try:
                province_ids.add(int(pid))
            except (ValueError, TypeError):
                pass
        if province_ids:
            data.colonial_region_provinces[region_name] = province_ids


def _load_government_ranks_file(path: str, data: GameData) -> None:
    """Load government ranks, mapping rank int → modifier dict."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for rank_key, rank_node in root.items():
        if not isinstance(rank_node, ClausewitzNode):
            continue
        try:
            rank_int = int(rank_key)
        except ValueError:
            continue
        mods = _parse_modifier_block(rank_node)
        if mods:
            data.government_rank_modifiers[rank_int] = mods


def _load_ruler_personalities_file(path: str, data: GameData) -> None:
    """Load ruler personalities, mapping personality name → modifier dict."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        root = parse(f.read())

    for pers_name, pers_node in root.items():
        if not isinstance(pers_node, ClausewitzNode):
            continue
        mods_node = pers_node.get_node("modifier")
        if mods_node:
            mods = _parse_modifier_block(mods_node)
        else:
            mods = _parse_modifier_block(pers_node)
        if mods:
            data.ruler_personalities[pers_name] = mods


def _load_tech_years_file(path: str, tech_type: str, data: GameData) -> None:
    """Parse common/technologies/{adm,dip,mil}.txt and extract years per tech level."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        # Find every 'year = XXXX' inside a technology = { ... } block
        import re
        # Quick regex: grab 'year = NNNN' inside each technology block
        blocks = re.split(r'\btechnology\s*=\s*\{', content)
        years = []
        for block in blocks[1:]:  # skip text before first 'technology = {'
            m = re.search(r'\byear\s*=\s*(\d+)', block)
            years.append(int(m.group(1)) if m else 0)
        if years:
            data.tech_years[tech_type] = years
    except Exception:
        pass


def _load_directory(dir_path: str, loader_fn, data: GameData) -> None:
    if not os.path.isdir(dir_path):
        return
    for fname in sorted(os.listdir(dir_path)):
        if fname.endswith(".txt"):
            loader_fn(os.path.join(dir_path, fname), data)


# ── public entry point ────────────────────────────────────────────────────────


def load_game_data(base_path: str, mod_paths: list[str] | None = None) -> GameData:
    """
    Load EU4 game data from base_path and optional mod_paths.
    Mod files overwrite base game entries with the same key.

    Folder structure expected under each path:
        common/ideas/*.txt
        common/government_reforms/*.txt
        common/advisortypes/*.txt
        common/event_modifiers/*.txt
        common/great_projects/*.txt
        common/colonial_regions/*.txt
        common/government_ranks/*.txt
        common/ruler_personalities/*.txt
    """
    data = GameData()

    expanded_paths = [base_path]
    for mp in (mod_paths or []):
        if not os.path.exists(mp):
            continue
        expanded_paths.append(mp)
        if os.path.isdir(mp):
            for entry in os.listdir(mp):
                sub = os.path.join(mp, entry)
                if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "common")):
                    expanded_paths.append(sub)

    for root_path in expanded_paths:
        _load_directory(os.path.join(root_path, "common", "ideas"), _load_ideas_file, data)
        _load_directory(
            os.path.join(root_path, "common", "government_reforms"),
            _load_reforms_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "advisortypes"),
            _load_advisortypes_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "event_modifiers"),
            _load_event_modifiers_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "policies"),
            _load_policies_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "estate_privileges"),
            _load_estate_privileges_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "estates"),
            _load_estates_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "government_mechanics"),
            _load_government_mechanics_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "religions"),
            _load_religions_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "static_modifiers"),
            _load_static_modifiers_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "triggered_modifiers"),
            _load_triggered_modifiers_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "great_projects"),
            _load_great_projects_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "colonial_regions"),
            _load_colonial_regions_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "government_ranks"),
            _load_government_ranks_file,
            data,
        )
        _load_directory(
            os.path.join(root_path, "common", "ruler_personalities"),
            _load_ruler_personalities_file,
            data,
        )

    _load_coastal_provinces(base_path, mod_paths, data)

    # Load technology years (mod overrides vanilla if present, so prefer last matching path)
    for tech_type in ('adm', 'dip', 'mil'):
        loaded = False
        for root_path in reversed(expanded_paths):  # mod last = highest priority
            tech_path = os.path.join(root_path, "common", "technologies", f"{tech_type}.txt")
            if os.path.isfile(tech_path):
                _load_tech_years_file(tech_path, tech_type, data)
                loaded = True
                break
        if not loaded:
            # fallback: vanilla
            tech_path = os.path.join(base_path, "common", "technologies", f"{tech_type}.txt")
            _load_tech_years_file(tech_path, tech_type, data)

    return data


def _load_coastal_provinces(base_path: str, mod_paths: list[str] | None, data: GameData) -> None:
    """Compute all coastal province IDs by analyzing map definitions and provinces.bmp."""
    steam_fallback = r"C:\Program Files (x86)\Steam\steamapps\common\Europa Universalis IV"
    all_roots = (mod_paths or []) + [base_path, steam_fallback]
    def_path = None
    for r in all_roots:
        p = os.path.join(r, "map", "definition.csv")
        if os.path.isfile(p):
            def_path = p
            break
    if not def_path:
        fallback = r"C:\Program Files (x86)\Steam\steamapps\common\Europa Universalis IV\map\definition.csv"
        if os.path.isfile(fallback):
            def_path = fallback
    if not def_path:
        return

    rgb_to_pid: dict[tuple[int, int, int], int] = {}
    with open(def_path, "r", encoding="latin-1") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            if len(parts) >= 4:
                try:
                    pid = int(parts[0])
                    r = int(parts[1])
                    g = int(parts[2])
                    b = int(parts[3])
                    rgb_to_pid[(r, g, b)] = pid
                except ValueError:
                    pass

    def_map_path = None
    for r in all_roots:
        p = os.path.join(r, "map", "default.map")
        if os.path.isfile(p):
            def_map_path = p
            break
    if not def_map_path:
        return

    sea_provinces: set[int] = set()
    with open(def_map_path, "r", encoding="utf-8", errors="ignore") as f:
        map_text = f.read()
    sea_starts_m = re.search(r"sea_starts\s*=\s*\{([^}]*)\}", map_text)
    if sea_starts_m:
        for token in sea_starts_m.group(1).split():
            if token.isdigit():
                sea_provinces.add(int(token))

    data.sea_provinces = sea_provinces

    bmp_path = None
    for r in all_roots:
        p = os.path.join(r, "map", "provinces.bmp")
        if os.path.isfile(p):
            bmp_path = p
            break
    if not bmp_path:
        return

    try:
        with open(bmp_path, "rb") as f:
            header = f.read(14)
            magic, size, res1, res2, offset = struct.unpack("<2sIHHI", header)
            f.seek(14)
            hdr_size, width, height, planes, bpp = struct.unpack("<IiiHH", f.read(16))
            f.seek(offset)
            raw_data = f.read()

        row_size = ((width * 3 + 3) // 4) * 4
        grid = []
        for y in range(height):
            row_offset = y * row_size
            row = []
            for x in range(width):
                px_offset = row_offset + x * 3
                b = raw_data[px_offset]
                g = raw_data[px_offset + 1]
                r = raw_data[px_offset + 2]
                row.append(rgb_to_pid.get((r, g, b), 0))
            grid.append(row)

        coastal: set[int] = set()
        for y in range(height):
            for x in range(width):
                pid = grid[y][x]
                if pid == 0 or pid in sea_provinces:
                    continue
                is_coast = False
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < width and 0 <= ny < height:
                            if grid[ny][nx] in sea_provinces:
                                is_coast = True
                                break
                    if is_coast:
                        break
                if is_coast:
                    coastal.add(pid)
        data.coastal_provinces = coastal
    except Exception:
        pass

