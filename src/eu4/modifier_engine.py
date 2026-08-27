"""
Unified EU4 Modifier State Engine.
Evaluates all active game entities for a nation into an aggregated in-memory modifier store
with complete provenance breakdown tracking.
"""
from __future__ import annotations

from typing import Any
from clausewitz.parser import ClausewitzNode
from eu4.game_data import GameData
from eu4.save_reader import CountryData


class ModifierEngine:
    @staticmethod
    def evaluate_country(
        country: CountryData,
        game_data: GameData,
        raw_monuments: list[tuple[str, int, str | None]] | None = None,
        dof_tags: set[str] | None = None,
        province_owner_map: dict[str, str] | None = None,
        tag_names: dict[str, str] | None = None,
    ) -> None:
        """
        Populate country.compiled_modifiers and country.modifier_breakdowns by evaluating all active sources.
        """
        mods: dict[str, float] = {}
        breakdowns: dict[str, list[dict[str, Any]]] = {}

        def add_mod(m_key: str, val: float, source: str, desc: str = ""):
            if abs(val) < 1e-6:
                return
            mods[m_key] = mods.get(m_key, 0.0) + val
            if m_key not in breakdowns:
                breakdowns[m_key] = []
            breakdowns[m_key].append({
                "source": source,
                "desc": desc or source,
                "value": val,
            })

        def merge_dict(d: dict[str, float], source: str, scale: float = 1.0, desc: str = ""):
            for k, v in d.items():
                add_mod(k, v * scale, source, desc or source)

        def apply_static_modifier(
            mod_name: str,
            scale: float,
            source_label: str,
            fallback_mods: dict[str, float] | None = None,
            category: str | None = None
        ) -> bool:
            sm = game_data.static_modifiers.get(mod_name)
            if not sm:
                sm = fallback_mods or {}
            if not sm or abs(scale) < 1e-6:
                return False
            cat = category or mod_name
            for k, v in sm.items():
                add_mod(k, v * scale, cat, source_label)
            return True

        # 1. Idea Groups & Traditions & Ambitions
        for group_name, completed_levels in country.active_idea_groups.items():
            ig = game_data.idea_groups.get(group_name)
            if not ig:
                continue
            # Traditions are active for active idea groups
            merge_dict(ig.traditions, f"idea:{group_name}(tradition)")
            # Individual unlocked ideas
            for idx in range(min(completed_levels, len(ig.ideas))):
                idea_dict = dict(ig.ideas[idx])
                merge_dict(idea_dict, f"idea:{group_name}[{idx+1}]")
            # Ambition / Bonus (all 7 completed)
            if completed_levels >= 7 and ig.bonus:
                merge_dict(ig.bonus, f"idea:{group_name}(ambition)")

            # Mod triggered modifiers for idea groups
            if group_name == "humanist_ideas" and completed_levels >= 3:
                acc_count = getattr(country, "accepted_culture_count", 0) or len(getattr(country, "accepted_cultures", []))
                if acc_count >= 7:
                    add_mod("land_morale", 0.075, "humanist_bonus", "Humanist Ideas Bonus (7+ Accepted Cultures)")

            if group_name == "kolonialimperium0":
                cn_count = len(getattr(country, "colonial_nation_tags", []))
                if cn_count > 0:
                    if completed_levels >= 3:
                        kolo4_bonus = min(0.25, 0.05 * cn_count)
                        add_mod("global_manpower_modifier", kolo4_bonus, f"kolo4_{cn_count}_bonus", f"kolo4_{cn_count}_bonus")
                        add_mod("global_sailors_modifier", kolo4_bonus, f"kolo4_{cn_count}_bonus", f"kolo4_{cn_count}_bonus")
                    if completed_levels >= 4:
                        add_mod("global_tariffs", 0.05 * cn_count, f"kolo3_{cn_count}_bonus", f"kolo3_{cn_count}_bonus")
                        add_mod("inflation_reduction", -0.15 * cn_count, f"kolo3_{cn_count}_bonus", f"kolo3_{cn_count}_bonus")
                    if completed_levels >= 5:
                        kolo6_bonus = min(0.25, 0.05 * cn_count)
                        add_mod("naval_forcelimit_modifier", kolo6_bonus, f"kolo6_{cn_count}_bonus", f"kolo6_{cn_count}_bonus")

        # 2. Active Policies
        for pol in country.active_policies:
            p_mods = game_data.policies.get(pol)
            if p_mods:
                merge_dict(p_mods, f"policy:{pol}")

        # 3. Government Reforms
        for ref in country.government_reforms:
            r_mods = game_data.government_reforms.get(ref)
            if r_mods:
                merge_dict(r_mods, f"reform:{ref}")

        # 4. Estate Privileges
        for priv_item in country.estate_privileges:
            if isinstance(priv_item, (tuple, list)):
                priv = priv_item[0]
                land_share = priv_item[1] if len(priv_item) > 1 else 0.30
            else:
                priv = str(priv_item)
                land_share = 0.30

            p_mods = game_data.estate_privileges.get(priv)
            if p_mods:
                merge_dict(p_mods, f"privilege:{priv}")
            p_land = game_data.estate_privilege_land_modifiers.get(priv)
            if p_land:
                merge_dict(p_land, f"privilege:{priv}(land)", scale=land_share)

        # 5. Active Event & Static Modifiers from Save
        for m in country.modifiers:
            mname = m.name if hasattr(m, "name") else (m.get("modifier") if isinstance(m, dict) else str(m))
            if not mname:
                continue
            emods = game_data.event_modifiers.get(mname) or game_data.static_modifiers.get(mname)
            if emods:
                merge_dict(emods, f"modifier:{mname}")

        # 6. Advisors
        for adv in country.advisors:
            tname = adv.job_type_name or game_data.get_advisor_type_name(adv.type_int)
            if tname:
                amods = game_data.advisor_modifiers.get(tname)
                if amods:
                    merge_dict(amods, f"advisor:{tname}(L{adv.skill})")

        # 7. Ruler Personalities
        for trait in country.monarch_personalities:
            tmods = game_data.ruler_personalities.get(trait)
            if tmods:
                merge_dict(tmods, f"trait:{trait}")

        # 8. Monuments (Great Projects)
        def is_monument_active(proj_name: str, owner_tag: str) -> bool:
            trig = game_data.great_project_triggers.get(proj_name)
            if not trig:
                return True

            def check_cond(node: ClausewitzNode) -> bool:
                for k, v in node.items():
                    if k == "OR":
                        if isinstance(v, ClausewitzNode):
                            or_passed = False
                            for or_k, or_v in v.items():
                                if or_k in ("owned_by", "tag"):
                                    tags = [or_v] if isinstance(or_v, str) else (list(or_v.array) if isinstance(or_v, ClausewitzNode) else [str(or_v)])
                                    if owner_tag in tags or country.tag in tags:
                                        or_passed = True
                                        break
                                elif or_k == "religion":
                                    rels = [or_v] if isinstance(or_v, str) else (list(or_v.array) if isinstance(or_v, ClausewitzNode) else [str(or_v)])
                                    if country.religion in rels:
                                        or_passed = True
                                        break
                                elif or_k == "culture":
                                    culs = [or_v] if isinstance(or_v, str) else (list(or_v.array) if isinstance(or_v, ClausewitzNode) else [str(or_v)])
                                    acc = getattr(country, "accepted_cultures", [])
                                    if any(c in acc or c == getattr(country, "primary_culture", "") for c in culs):
                                        or_passed = True
                                        break
                                elif or_k == "culture_group":
                                    or_passed = True
                                    break
                                elif isinstance(or_v, ClausewitzNode):
                                    if check_cond(or_v):
                                        or_passed = True
                                        break
                            if not or_passed:
                                return False
                    elif k == "AND":
                        if isinstance(v, ClausewitzNode):
                            if not check_cond(v):
                                return False
                    elif k in ("owned_by", "tag"):
                        tags = [v] if isinstance(v, str) else (list(v.array) if isinstance(v, ClausewitzNode) else [str(v)])
                        if owner_tag not in tags and country.tag not in tags:
                            return False
                    elif k == "owner":
                        if isinstance(v, ClausewitzNode):
                            for ok, ov in v.items():
                                if ok in ("tag", "owned_by"):
                                    tags = [ov] if isinstance(ov, str) else (list(ov.array) if isinstance(ov, ClausewitzNode) else [str(ov)])
                                    if owner_tag not in tags and country.tag not in tags:
                                        return False
                                elif ok == "government":
                                    if country.government_type != str(ov):
                                        return False
                                elif ok == "has_reform":
                                    if str(ov) not in getattr(country, "government_reforms", []):
                                        return False
                                elif ok == "is_emperor":
                                    if str(ov) == "yes" and not getattr(country, "is_hre_emperor", False):
                                        return False
                                elif ok == "is_curia_controller":
                                    if str(ov) == "yes" and not getattr(country, "is_curia_controller", False):
                                        return False
                                elif ok == "OR" and isinstance(ov, ClausewitzNode):
                                    sub_or = False
                                    for sk, sv in ov.items():
                                        if sk in ("tag", "owned_by") and (country.tag == str(sv) or owner_tag == str(sv)):
                                            sub_or = True
                                        elif sk == "is_emperor" and str(sv) == "yes" and getattr(country, "is_hre_emperor", False):
                                            sub_or = True
                                        elif sk == "is_curia_controller" and str(sv) == "yes" and getattr(country, "is_curia_controller", False):
                                            sub_or = True
                                        elif sk == "culture_group":
                                            sub_or = True
                                        elif sk == "primary_culture":
                                            if str(sv) == getattr(country, "primary_culture", "") or str(sv) in getattr(country, "accepted_cultures", []):
                                                sub_or = True
                                        elif sk == "dynasty":
                                            sub_or = True
                                    if not sub_or:
                                        return False
                    elif k == "religion":
                        if country.religion != str(v):
                            return False
                    elif k == "custom_trigger_tooltip":
                        if isinstance(v, ClausewitzNode) and not check_cond(v):
                            return False
                return True

            return check_cond(trig)

        if raw_monuments and province_owner_map:
            for proj_name, tier, prov_id in raw_monuments:
                if tier <= 0 or not prov_id:
                    continue
                owner = province_owner_map.get(str(prov_id))
                tier_dict = game_data.great_projects.get(proj_name, {})
                t_mods = tier_dict.get(tier)
                if not t_mods:
                    continue

                if owner == country.tag:
                    if is_monument_active(proj_name, owner):
                        merge_dict(t_mods, f"monument:{proj_name}(T{tier})")
                elif any(s == owner for s in getattr(country, "subjects", [])):
                    if is_monument_active(proj_name, owner):
                        overlord_mods = {
                            k.replace("overlord_", ""): v
                            for k, v in t_mods.items()
                            if k.startswith("overlord_")
                        }
                        if overlord_mods:
                            sub_name = tag_names.get(owner, owner) if tag_names else owner
                            merge_dict(overlord_mods, f"monument:{proj_name}(subject:{owner})(T{tier})", desc=f"{sub_name}")

        # 8b. Active Province Modifiers on Owned Provinces (Country-wide modifiers only)
        COUNTRY_WIDE_KEYS = {
            "governing_capacity", "governing_capacity_modifier",
            "discipline", "land_forcelimit", "naval_forcelimit",
            "land_forcelimit_modifier", "naval_forcelimit_modifier",
            "morale_armies", "land_morale", "naval_morale",
            "merchants", "diplomats", "colonists", "missionaries",
            "diplomatic_reputation", "global_trade_power",
            "global_unrest", "all_power_cost", "technology_cost",
            "idea_cost", "development_cost", "development_cost_modifier",
            "goods_produced_modifier", "global_trade_goods_size_modifier",
            "galley_power", "galley_combat_ability", "heavy_ship_power", "infantry_power", "cavalry_power", "artillery_power",
            "global_tax_modifier", "global_manpower_modifier", "global_sailors_modifier",
            "production_efficiency", "trade_efficiency", "manpower_recovery_speed",
            "sailors_recovery_speed", "ae_impact", "improve_relation_modifier",
            "administrative_efficiency", "core_creation", "core_creation_cost",
            "siege_ability", "defensiveness", "fire_damage", "shock_damage",
            "yearly_corruption", "max_absolutism", "yearly_absolutism",
        }
        for mname, prov_name in getattr(country, "province_modifiers", []):
            pmods = game_data.event_modifiers.get(mname) or game_data.static_modifiers.get(mname)
            if pmods:
                filtered_pmods = {
                    k: v for k, v in pmods.items()
                    if k in COUNTRY_WIDE_KEYS and not k.startswith("local_") and not k.startswith("province_")
                }
                if filtered_pmods:
                    merge_dict(filtered_pmods, f"prov_mod:{mname}({prov_name})")

        # 9. Religion
        if country.religion:
            rel_mods = game_data.religion_country_modifiers.get(country.religion)
            if rel_mods:
                merge_dict(rel_mods, f"religion:{country.religion}")

        # 10. Government Rank
        if country.government_rank in game_data.government_rank_modifiers:
            gr_mods = game_data.government_rank_modifiers[country.government_rank]
            merge_dict(gr_mods, f"gov_rank:{country.government_rank}")
        elif country.government_rank >= 3:
            add_mod("governing_capacity", 400.0, "gov_rank:3")
            add_mod("morale_armies", 0.05, "gov_rank:3")
        elif country.government_rank >= 2:
            add_mod("governing_capacity", 200.0, "gov_rank:2")

        # 10b. Naval Doctrine
        if getattr(country, "naval_doctrine", ""):
            doc_mods = game_data.naval_doctrines.get(country.naval_doctrine)
            if doc_mods:
                merge_dict(doc_mods, f"naval_doctrine:{country.naval_doctrine}", desc=f"Naval Doctrine ({country.naval_doctrine.replace('_', ' ').title()})")

        # 11. Golden Age
        if country.golden_age:
            apply_static_modifier("golden_age", 1.0, "Golden Age", fallback_mods={"all_power_cost": -0.10, "morale_armies": 0.10, "naval_morale": 0.10, "goods_produced_modifier": 0.10, "max_absolutism": 5.0})

        # 12. Defender of the Faith
        if dof_tags and country.tag in dof_tags:
            apply_static_modifier("defender_of_faith", 1.0, "Defender of the Faith", fallback_mods={"morale_armies": 0.05, "naval_morale": 0.05, "manpower_recovery_speed": 0.20, "war_exhaustion": -0.03})

        # 12b. Curia Controller
        if getattr(country, "is_curia_controller", False):
            apply_static_modifier("curia_controller", 1.0, "Curia Controller", fallback_mods={"diplomatic_reputation": 1.0, "diplomats": 1.0, "advisor_cost": -0.20, "ae_impact": -0.20, "technology_cost": -0.05, "prestige": 1.0})

        # 12c. HRE Emperor
        if getattr(country, "is_hre_emperor", False):
            apply_static_modifier("emperor", 1.0, "Holy Roman Emperor", fallback_mods={"diplomatic_upkeep": 1.0, "prestige": 1.0, "advisor_pool": 1.0})

        # 13. Hegemony
        if country.hegemony:
            h_type = country.hegemony.lower()
            if "military" in h_type:
                apply_static_modifier("military_hegemon", 1.0, "Military Hegemon", fallback_mods={"war_exhaustion": -0.10, "movement_speed": 0.10, "siege_ability": 0.20})
            elif "naval" in h_type:
                apply_static_modifier("naval_hegemon", 1.0, "Naval Hegemon", fallback_mods={"naval_morale": 0.20, "trade_steering": 0.50})
            elif "economic" in h_type:
                apply_static_modifier("economic_hegemon", 1.0, "Economic Hegemon", fallback_mods={"governing_capacity_modifier": 0.20, "goods_produced_modifier": 0.20})

        # 14. Dynamic Static Modifiers
        # Army Tradition (0 to 100)
        at = min(100.0, max(0.0, country.army_tradition))
        if at > 0:
            apply_static_modifier("army_tradition", at / 100.0, f"Army Tradition ({at:.1f})", fallback_mods={"morale_armies": 0.25, "manpower_recovery_speed": 0.10, "siege_ability": 0.05})

        # Navy Tradition (0 to 100)
        nt = min(100.0, max(0.0, country.navy_tradition))
        if nt > 0:
            apply_static_modifier("navy_tradition", nt / 100.0, f"Navy Tradition ({nt:.1f})", fallback_mods={"naval_morale": 0.25, "trade_steering": 1.00, "privateer_efficiency": 0.25, "blockade_efficiency": 1.00})

        # Prestige (-100 to +100)
        prest = country.prestige
        if prest > 0:
            apply_static_modifier("prestige", prest / 100.0, f"Prestige ({prest:.1f})", fallback_mods={"morale_armies": 0.10, "naval_morale": 0.10, "global_trade_power": 0.15, "ae_impact": -0.10, "improve_relations": 0.50})
        elif prest < 0:
            if not apply_static_modifier("negative_prestige", abs(prest) / 100.0, f"Negative Prestige ({prest:.1f})"):
                apply_static_modifier("prestige", prest / 100.0, f"Prestige ({prest:.1f})", fallback_mods={"morale_armies": 0.10, "naval_morale": 0.10, "global_trade_power": 0.15, "ae_impact": -0.10, "improve_relations": 0.50})

        # Power Projection (0 to 100)
        pp = min(100.0, max(0.0, country.power_projection))
        if pp > 0:
            apply_static_modifier("power_projection", pp / 100.0, f"Power Projection ({pp:.1f})", fallback_mods={"morale_armies": 0.10, "defensiveness": 0.10, "global_trade_power": 0.10})

        # 15. Estate Loyalty Effects (Happy >=60%, Neutral 30-59%, Angry <30%) from game files
        for etype, loyalty, _territory in getattr(country, "estates", []):
            ename = etype.replace("estate_", "").replace("_", " ").title()
            if loyalty >= 60.0:
                happy_mods = game_data.estate_happy_modifiers.get(etype)
                if happy_mods:
                    merge_dict(happy_mods, f"estate_loyalty:{etype}", desc=f"{ename} Loyal (>=60%)")
                else:
                    if etype in ("estate_burghers", "estate_vaisyas"):
                        add_mod("trade_efficiency", 0.20, f"estate_loyalty:{etype}", f"{ename} Loyal (>=60%)")
                        add_mod("development_cost", -0.10, f"estate_loyalty:{etype}", f"{ename} Loyal")
                    elif etype in ("estate_nobles", "estate_nobility", "estate_maratha", "estate_rajput", "estate_nomadic_tribes"):
                        add_mod("manpower_recovery_speed", 0.20, f"estate_loyalty:{etype}", f"{ename} Loyal (>=60%)")
                        add_mod("land_maintenance_modifier", -0.10, f"estate_loyalty:{etype}", f"{ename} Loyal")
                    elif etype in ("estate_church", "estate_brahmins"):
                        add_mod("global_tax_modifier", 0.20, f"estate_loyalty:{etype}", f"{ename} Loyal (>=60%)")
                        add_mod("stability_cost_modifier", -0.10, f"estate_loyalty:{etype}", f"{ename} Loyal")
            elif loyalty >= 30.0:
                neutral_mods = game_data.estate_neutral_modifiers.get(etype)
                if neutral_mods:
                    # Neutral estate loyalty gives country_modifier_neutral scaled by 0.75 (+15% trade efficiency for burghers)
                    merge_dict(neutral_mods, f"estate_loyalty:{etype}", scale=0.75, desc=f"{ename} Neutral (30-59%)")
                else:
                    if etype in ("estate_burghers", "estate_vaisyas"):
                        add_mod("trade_efficiency", 0.15, f"estate_loyalty:{etype}", f"{ename} Neutral (30-59%)")
                    elif etype in ("estate_nobles", "estate_nobility", "estate_maratha", "estate_rajput", "estate_nomadic_tribes"):
                        add_mod("manpower_recovery_speed", 0.15, f"estate_loyalty:{etype}", f"{ename} Neutral (30-59%)")
                    elif etype in ("estate_church", "estate_brahmins"):
                        add_mod("global_tax_modifier", 0.15, f"estate_loyalty:{etype}", f"{ename} Neutral (30-59%)")
            elif loyalty < 30.0:
                angry_mods = game_data.estate_angry_modifiers.get(etype)
                if angry_mods:
                    merge_dict(angry_mods, f"estate_disloyal:{etype}", desc=f"{ename} Disloyal (<30%)")
                else:
                    if etype in ("estate_burghers", "estate_vaisyas"):
                        add_mod("trade_efficiency", -0.10, f"estate_disloyal:{etype}", f"{ename} Disloyal (<30%)")
                        add_mod("development_cost", 0.10, f"estate_disloyal:{etype}", f"{ename} Disloyal (<30%)")
                    elif etype in ("estate_nobles", "estate_nobility", "estate_maratha", "estate_rajput"):
                        add_mod("manpower_recovery_speed", -0.10, f"estate_disloyal:{etype}", f"{ename} Disloyal (<30%)")
                    elif etype in ("estate_church", "estate_brahmins"):
                        add_mod("global_tax_modifier", -0.10, f"estate_disloyal:{etype}", f"{ename} Disloyal (<30%)")

        # 16. Stability (-3 to +3)
        stab = country.stability
        if stab > 0:
            apply_static_modifier("positive_stability", stab, f"Stability (+{int(stab)})", fallback_mods={"global_tax_modifier": 0.05, "global_unrest": -1.0, "global_missionary_strength": 0.005}, category="stability")
        elif stab < 0:
            apply_static_modifier("negative_stability", abs(stab), f"Negative Stability ({int(stab)})", fallback_mods={"global_tax_modifier": 0.05, "global_unrest": -2.0}, category="stability")

        # 16b. Legitimacy / Devotion / Meritocracy / Republican Tradition
        # In EU4, Legitimacy/Devotion/Meritocracy/RT are dynamic static modifiers.
        # Legitimacy has baseline 50 (range 0 to 100): 100 grants +1.0 diplo rep, -2 unrest, +10 max absolutism.
        # In static_modifiers.txt, legitimacy defines values at max 2.0 (e.g. diplo rep 2.0), so scale is (legitimacy - 50.0) / 100.0.
        if country.government_type == "monarchy" or country.legitimacy > 0:
            leg_scale = (country.legitimacy - 50.0) / 100.0
            apply_static_modifier("legitimacy", leg_scale, f"Legitimacy ({country.legitimacy:.1f})", fallback_mods={"diplomatic_reputation": 2.0, "global_unrest": -4.0, "tolerance_own": 2.0, "max_absolutism": 20.0}, category="legitimacy")
        elif country.government_type == "theocracy" or country.devotion > 0:
            dev_scale = (country.devotion - 50.0) / 100.0
            apply_static_modifier("devotion", dev_scale, f"Devotion ({country.devotion:.1f})", fallback_mods={"diplomatic_reputation": 2.0, "prestige": 2.0, "global_tax_modifier": 0.50}, category="devotion")
        elif country.meritocracy > 0:
            mer_scale = (country.meritocracy - 50.0) / 100.0
            apply_static_modifier("meritocracy", mer_scale, f"Meritocracy ({country.meritocracy:.1f})", fallback_mods={"global_spy_defence": 1.0, "advisor_cost": -0.50}, category="meritocracy")
        elif country.government_type == "republic" or country.republican_tradition > 0:
            rt_scale = (country.republican_tradition - 50.0) / 100.0
            apply_static_modifier("republican_tradition", rt_scale, f"Republican Tradition ({country.republican_tradition:.1f})", fallback_mods={"global_unrest": -2.0, "reform_progress_growth": 1.0}, category="republican_tradition")

        # 17. Mercantilism (0 to 100)
        merc = max(0.0, country.mercantilism)
        if merc > 0:
            if not apply_static_modifier("mercantilism_modifier", merc / 100.0, f"Mercantilism ({merc:.1f}%)", category="mercantilism"):
                apply_static_modifier("mercantilism", merc / 100.0, f"Mercantilism ({merc:.1f}%)", fallback_mods={"global_prov_trade_power_modifier": 2.0, "embargo_efficiency": 0.50, "trade_steering": 0.25, "trade_efficiency": 0.10}, category="mercantilism")

        # 18. Army Professionalism (0.0 to 1.0)
        prof = min(1.0, max(0.0, country.army_professionalism))
        if prof > 0:
            if not apply_static_modifier("army_professionalism", prof, f"Army Professionalism ({prof*100:.1f}%)", category="army_professionalism"):
                apply_static_modifier("army_professionalism_modifier", prof, f"Army Professionalism ({prof*100:.1f}%)", fallback_mods={"discipline": 0.05, "siege_ability": 0.20, "fire_damage": 0.10, "shock_damage": 0.10, "drill_decay_modifier": -0.50}, category="army_professionalism")

        # 19. Absolutism (0 to 100+)
        abs_val = max(0.0, country.absolutism)
        if abs_val > 0:
            apply_static_modifier("absolutism", abs_val / 100.0, f"Absolutism ({abs_val:.1f})", fallback_mods={"discipline": 0.05, "administrative_efficiency": 0.30, "core_decay_on_your_own": -0.50})

        # 20. Innovativeness (0 to 100)
        inno = min(100.0, max(0.0, country.innovativeness))
        if inno > 0:
            apply_static_modifier("innovativeness", inno / 100.0, f"Innovativeness ({inno:.1f})", fallback_mods={"all_power_cost": -0.10, "army_tradition_decay": -0.01, "navy_tradition_decay": -0.01})

        # 21. Overextension
        oe = max(0.0, country.overextension)
        if oe > 0:
            apply_static_modifier("over_extension", oe, f"Overextension ({oe*100:.1f}%)", fallback_mods={"global_foreign_trade_power": -1.0, "stability_cost_modifier": 0.50, "mercenary_cost": 0.50, "diplomatic_reputation": -2.0, "improve_relation_modifier": -0.50, "global_unrest": 5.0, "yearly_corruption": 0.50}, category="overextension")

        # 22. War Exhaustion (0 to 20)
        we = max(0.0, country.war_exhaustion)
        if we > 0:
            apply_static_modifier("war_exhaustion", we, f"War Exhaustion ({we:.2f})", fallback_mods={"global_unrest": 1.0, "core_creation": 0.03, "manpower_recovery_speed": -0.01, "sailors_recovery_speed": -0.01, "siege_ability": -0.01, "global_trade_goods_size_modifier": -0.02})

        # 23. Corruption (0 to 100)
        corr = max(0.0, country.corruption)
        if corr > 0:
            c_mods = game_data.static_modifiers.get("corruption")
            c_scale = corr / 100.0 if c_mods and c_mods.get("all_power_cost", 0) >= 0.5 else corr
            apply_static_modifier("corruption", c_scale, f"Corruption ({corr:.1f})", fallback_mods={"all_power_cost": 0.01, "global_unrest": -0.20})

        # 24. Muslim Piety (Legalism vs Mysticism)
        if country.religion in ("sunni", "shiite", "ibadi"):
            piety = country.piety
            if piety > 0:
                if not apply_static_modifier("piety_positive", piety, f"Legalism ({piety*100:.1f}%)", category="legalism"):
                    apply_static_modifier("legalism", piety, f"Legalism ({piety*100:.1f}%)", fallback_mods={"global_tax_modifier": 0.20, "manpower_recovery_speed": 0.20, "technology_cost": -0.10}, category="legalism")
            elif piety < 0:
                myst = abs(piety)
                if not apply_static_modifier("piety_negative", myst, f"Mysticism ({myst*100:.1f}%)", category="mysticism"):
                    apply_static_modifier("mysticism", myst, f"Mysticism ({myst*100:.1f}%)", fallback_mods={"morale_armies": 0.10, "global_missionary_strength": 0.03, "idea_cost": -0.10}, category="mysticism")

        # 25. Patriarch Authority (Orthodox)
        if country.religion == "orthodox":
            pa = max(0.0, country.patriarch_authority)
            if pa > 0:
                apply_static_modifier("patriarch_authority", pa / 100.0, f"Patriarch Authority ({pa*100:.1f}%)", fallback_mods={"global_missionary_strength": 0.02, "manpower_in_true_faith_provinces": 0.33})

        # 26. Militarization (Prussia / Militaristic government)
        milit = max(0.0, country.militarization)
        if milit > 0:
            m_mods = game_data.government_mechanics.get("militarized_society") or game_data.government_mechanics.get("prussian_militarized_society_1") or game_data.static_modifiers.get("militarized_society")
            if m_mods:
                merge_dict(m_mods, "militarization", scale=milit / 100.0, desc=f"Militarization ({milit:.1f}%)")
            else:
                add_mod("discipline", 0.10 * (milit / 100.0), "militarization", f"Militarization ({milit:.1f}%)")
                add_mod("manpower_recovery_speed", 0.20 * (milit / 100.0), "militarization", f"Militarization ({milit:.1f}%)")
                add_mod("land_maintenance_modifier", -0.20 * (milit / 100.0), "militarization", f"Militarization ({milit:.1f}%)")

        # 27. Buddhist Karma
        if country.religion in ("buddhism", "vajrayana", "mahayana"):
            k = country.karma
            if -25 <= k <= 25:
                apply_static_modifier("karma_just_right", 1.0, f"Balanced Karma ({k:.1f})", fallback_mods={"diplomatic_reputation": 2.0, "discipline": 0.05}, category="karma")
            elif k > 25:
                apply_static_modifier("karma_too_high", 1.0, f"High Karma ({k:.1f})", fallback_mods={"diplomatic_reputation": 1.0}, category="karma")
            elif k < -25:
                apply_static_modifier("karma_too_low", 1.0, f"Low Karma ({k:.1f})", fallback_mods={"discipline": 0.025}, category="karma")

        # 28. Horde Unity
        if country.government_type == "nomad" or "nomad" in getattr(country, "government_reforms", []):
            hu = max(0.0, country.horde_unity)
            if hu > 0:
                apply_static_modifier("horde_unity", hu / 100.0, f"Horde Unity ({hu:.1f})", fallback_mods={"discipline": 0.05, "morale_armies": 0.10})

        # 29. Ahead of Time Technology (years loaded from common/technologies/*.txt)
        # EU4 applies the ahead_of_time bonus when the NEXT tech's year is still
        # in the future — meaning you're at the frontier where researching further
        # would cost ahead-of-time penalties.
        s_year = country.save_year or 1444

        adm_tech_years = game_data.tech_years.get("adm", [])
        dip_tech_years = game_data.tech_years.get("dip", [])
        mil_tech_years = game_data.tech_years.get("mil", [])

        # ADM ahead-of-time: next adm tech level's year > save_year
        if country.adm_tech >= 1 and country.adm_tech < len(adm_tech_years):
            next_adm_yr = adm_tech_years[country.adm_tech]  # index = current_tech (0-based next)
            if next_adm_yr > s_year:
                if not apply_static_modifier("ahead_of_time_administrative", 1.0, f"Ahead in ADM Tech ({country.adm_tech}→{country.adm_tech+1}, yr {next_adm_yr})", category="ahead_of_time:adm"):
                    add_mod("production_efficiency", 0.20, "ahead_of_time:adm", f"Ahead in ADM Tech ({country.adm_tech})")
                    add_mod("yearly_corruption", -0.05, "ahead_of_time:adm", "Ahead in ADM Tech")

        # DIP ahead-of-time: next dip tech level's year > save_year
        if country.dip_tech >= 1 and country.dip_tech < len(dip_tech_years):
            next_dip_yr = dip_tech_years[country.dip_tech]  # index = current_tech (0-based next)
            if next_dip_yr > s_year:
                if not apply_static_modifier("ahead_of_time_diplomatic", 1.0, f"Ahead in DIP Tech ({country.dip_tech}→{country.dip_tech+1}, yr {next_dip_yr})", category="ahead_of_time:dip"):
                    add_mod("trade_efficiency", 0.20, "ahead_of_time:dip", f"Ahead in DIP Tech ({country.dip_tech})")
                    add_mod("yearly_corruption", -0.05, "ahead_of_time:dip", "Ahead in DIP Tech")

        # MIL ahead-of-time: next mil tech level's year > save_year
        if country.mil_tech >= 1 and country.mil_tech < len(mil_tech_years):
            next_mil_yr = mil_tech_years[country.mil_tech]  # index = current_tech (0-based next)
            if next_mil_yr > s_year:
                if not apply_static_modifier("ahead_of_time_military", 1.0, f"Ahead in MIL Tech ({country.mil_tech}→{country.mil_tech+1}, yr {next_mil_yr})", category="ahead_of_time:mil"):
                    add_mod("yearly_corruption", -0.05, "ahead_of_time:mil", "Ahead in MIL Tech")

        # 30. Large Colonial Nations bonus (+5 land FL, +1 merchant per large CN)
        if country.large_cn_count > 0:
            lcn_mods = dict(game_data.static_modifiers.get("large_colonial_nation", {}))
            lcn_mods.pop("naval_forcelimit", None)
            lcn_mods.pop("naval_forcelimit_modifier", None)
            if not lcn_mods:
                lcn_mods = {"merchants": 1.0, "global_trade_power": 0.02, "land_forcelimit": 5.0}
            for k, v in lcn_mods.items():
                add_mod(k, v * country.large_cn_count, "large_colonial_nations", f"{country.large_cn_count} Large CNs")

        # 31. Army Professionalism (0 to 100%)
        prof = getattr(country, "army_professionalism", 0.0)
        if prof > 0:
            prof_scale = prof / 100.0 if prof > 1.0 else prof
            apply_static_modifier("high_army_professionalism", prof_scale, f"Army Professionalism ({prof_scale*100:.1f}%)", fallback_mods={"discipline": 0.05, "fire_damage": 0.10, "shock_damage": 0.10, "siege_ability": 0.20})

        # 32. Army Drill (Average Regiment Drill)
        drill = getattr(country, "average_army_drill", 0.0)
        if drill > 0:
            drill_scale = drill / 100.0
            apply_static_modifier("regiment_drill_modifier", drill_scale, f"Army Drill ({drill:.1f}%)", fallback_mods={"fire_damage": 0.15, "shock_damage": 0.15, "fire_damage_received": -0.25, "shock_damage_received": -0.25, "movement_speed": 0.25})

        # Save into country compiled state
        country.compiled_modifiers = mods
        country.modifier_breakdowns = breakdowns


