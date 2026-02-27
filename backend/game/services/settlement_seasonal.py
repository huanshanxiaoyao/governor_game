"""季节性结算：秋收、年终、投资完成"""

import random

from ..models import Agent
from .constants import (
    INFRA_MAX_LEVEL,
    IRRIGATION_DAMAGE_REDUCTION,
    GRANARY_POP_LOSS_MULTIPLIER,
    RELIEF_POP_LOSS_MULTIPLIER,
    calculate_infra_maint,
    month_name,
    year_of,
)
from .ledger import (
    ensure_county_ledgers,
    ensure_village_ledgers,
    sync_county_gentry_land_ratio,
    sync_legacy_from_ledgers,
)


class SeasonalMixin:
    """秋收结算、年终结算、投资完成效果"""

    @classmethod
    def _apply_completed_investments(cls, county, season, report, game=None):
        """Apply effects of investments that complete this season.
        When game is provided, also updates Agent models (player path).
        """
        remaining = []
        for inv in county["active_investments"]:
            if inv["completion_season"] <= season:
                cls._apply_investment_effect(county, inv, report, game=game)
            else:
                remaining.append(inv)
        county["active_investments"] = remaining

    @classmethod
    def _apply_investment_effect(cls, county, inv, report, game=None):
        """Apply the effect of a single completed investment.
        When game is provided, also updates Agent affinity/memory for reclaim_land.
        """
        ensure_county_ledgers(county)
        action = inv["action"]

        if action == "reclaim_land":
            village_name = inv["target_village"]
            for v in county["villages"]:
                if v["name"] == village_name:
                    ensure_village_ledgers(v)
                    old_pct = v.get("gentry_land_pct", 0.3)
                    peasant = v["peasant_ledger"]
                    peasant["farmland"] = max(0, int(peasant.get("farmland", 0)) + 800)
                    # legacy fields同步，地主在册地不变，因此占比下降
                    sync_legacy_from_ledgers(v)
                    v["morale"] = min(100, v["morale"] + 5)
                    report["events"].append(
                        f"{village_name}开垦完成，耕地+800亩，民心+5，"
                        f"地主占比{old_pct:.0%}→{v['gentry_land_pct']:.0%}")
                    # Agent updates only when game is provided (player path)
                    if game is not None:
                        villager = Agent.objects.filter(
                            game=game,
                            role='VILLAGER',
                            attributes__village_name=village_name,
                        ).first()
                        if villager:
                            attrs = villager.attributes
                            attrs['player_affinity'] = min(
                                99, attrs.get('player_affinity', 50) + 5)
                            memory = attrs.get('memory', [])
                            memory.append(
                                f"{month_name(game.current_season)}，知县大人下令开垦荒地，"
                                f"{village_name}百姓新增耕地，感激不已")
                            if len(memory) > 20:
                                memory = memory[-20:]
                            attrs['memory'] = memory
                            villager.attributes = attrs
                            villager.save(update_fields=['attributes'])
                    break
            sync_county_gentry_land_ratio(county)

        elif action == "build_irrigation":
            county["irrigation_level"] = min(INFRA_MAX_LEVEL, county.get("irrigation_level", 0) + 1)
            new_maint = calculate_infra_maint("irrigation", county["irrigation_level"], county)
            county["admin_cost_detail"]["irrigation_maint"] = new_maint
            county["admin_cost"] = sum(county["admin_cost_detail"].values())
            report["events"].append(
                f"水利工程完工，水利等级提升至{county['irrigation_level']}，"
                f"年维护费{new_maint}两")

        elif action == "expand_school":
            county["school_level"] = min(INFRA_MAX_LEVEL, county.get("school_level", 1) + 1)
            county["education"] = min(100, county["education"] + 10)
            report["events"].append(
                f"县学扩建完成，文教+10，县学等级{county['school_level']}")

        elif action == "build_medical":
            county["medical_level"] = min(INFRA_MAX_LEVEL, county.get("medical_level", 0) + 1)
            new_maint = calculate_infra_maint("medical", county["medical_level"], county)
            county["admin_cost_detail"]["medical_maint"] = new_maint
            county["admin_cost"] = sum(county["admin_cost_detail"].values())
            report["events"].append(
                f"医疗设施建成，医疗等级提升至{county['medical_level']}，"
                f"年维护费{new_maint}两")

        elif action == "fund_village_school":
            village_name = inv["target_village"]
            for v in county["villages"]:
                if v["name"] == village_name:
                    v["has_school"] = True
                    v["morale"] = min(100, v["morale"] + 5)
                    school_increase = round(10 * county.get("price_index", 1.0))
                    county["admin_cost"] += school_increase
                    if "admin_cost_detail" in county:
                        county["admin_cost_detail"]["school_cost"] += school_increase
                    report["events"].append(
                        f"{village_name}村塾建成，民心+5，年运营费+{school_increase}两")
                    break

        elif action == "repair_roads":
            repair_count = county.get("road_repair_count", 0)
            bonus = max(0, 8 - repair_count)
            county["commercial"] = min(100, county["commercial"] + bonus)
            county["road_repair_count"] = repair_count + 1
            report["events"].append(f"道路修缮完成，商业+{bonus}"
                                    f"{'（边际递减）' if bonus < 8 else ''}")

    @classmethod
    def _autumn_settlement(cls, county, report, peer_counties=None):
        """Autumn: annual population update, agricultural output and agri tax only.
        Corvée and commercial tax already collected during the year via fiscal_year.
        """
        ensure_county_ledgers(county)
        # Annual population update (once per year at autumn)
        cls._annual_population_update(county, report, peer_counties=peer_counties)

        # 秋收：农民粮食储备增加
        harvest_production = cls._compute_peasant_production(county, include_disaster=True)
        county["peasant_grain_reserve"] = county.get("peasant_grain_reserve", 0) + harvest_production

        # 年度商户调整（商户数量变化缓慢）
        for market in county["markets"]:
            if county["commercial"] >= 60 and market["merchants"] < 30:
                market["merchants"] += 1
            elif county["commercial"] <= 25 and market["merchants"] > 2:
                market["merchants"] -= 1

        env = county["environment"]
        suitability = env["agriculture_suitability"]
        irr_level = county.get("irrigation_level", 0)
        irrigation_bonus = irr_level * 0.15  # 0/0.15/0.30/0.45

        # Agricultural output per village
        base_yield = 0.5  # 两/亩
        total_agri_output = 0
        for v in county["villages"]:
            output = v["farmland"] * base_yield * suitability * (1 + irrigation_bonus)
            total_agri_output += output

        # Disaster damage (non-plague disasters reduce output; all disasters cause pop loss)
        disaster = county.get("disaster_this_year")
        disaster_damage = 0
        if disaster and disaster["type"] != "plague":
            damage_factor = disaster["severity"]
            # 水利减损（仅洪灾和旱灾）
            if disaster["type"] in ("flood", "drought"):
                damage_factor *= (1 - IRRIGATION_DAMAGE_REDUCTION[min(irr_level, 3)])
            disaster_damage = total_agri_output * damage_factor
            total_agri_output *= (1 - damage_factor)
            report["events"].append(
                f"灾害影响秋收: 产出损失{round(disaster_damage)}两"
                f"{'（水利减损）' if irr_level > 0 and disaster['type'] in ('flood', 'drought') else ''}")

        # 灾害持续效果：所有灾害类型的秋季人口损失
        if disaster:
            total_pop_loss = 0
            for v in county["villages"]:
                ensure_village_ledgers(v)
                loss_rate = random.uniform(0.02, disaster["severity"] / 5)
                base_pop = v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
                pop_loss = int(base_pop * loss_rate)
                if county["has_granary"]:
                    pop_loss = int(pop_loss * GRANARY_POP_LOSS_MULTIPLIER)
                if disaster.get("relieved"):
                    pop_loss = int(pop_loss * RELIEF_POP_LOSS_MULTIPLIER)
                new_pop = max(0, base_pop - pop_loss)
                v["peasant_ledger"]["registered_population"] = new_pop
                v["population"] = new_pop
                total_pop_loss += pop_loss
            report["events"].append(
                f"灾害持续影响: 全县人口减少{total_pop_loss}人"
                f"{'（义仓减损）' if county['has_granary'] else ''}"
                f"{'（赈灾减损）' if disaster.get('relieved') else ''}")
            # 赈灾额外民心加成
            if disaster.get("relieved"):
                county["morale"] = min(100, county["morale"] + 2)
                report["events"].append("赈灾安民: 民心+2")

        # Agricultural tax (doc 06a §4.1) — only agri tax computed at autumn
        total_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county["villages"]
        )
        morale_factor = county["morale"] / 100
        collection_efficiency = 0.7 + 0.3 * morale_factor  # ranges 0.7-1.0
        agri_tax = total_agri_output * county["tax_rate"] * collection_efficiency

        # Remittance on agri tax only
        remit_ratio = county.get("remit_ratio", 0.65)
        agri_remit = agri_tax * remit_ratio
        agri_retained = agri_tax - agri_remit

        # Annual admin cost (deducted once per year at autumn, includes infra maintenance)
        admin = county["admin_cost"]

        # Net change to treasury from autumn settlement
        net = agri_retained - admin
        county["treasury"] += net

        # YTD figures from fiscal_year (corvée + commercial collected during the year)
        fy = county.get("fiscal_year", {})
        ytd_corvee = fy.get("corvee_tax", 0)
        ytd_corvee_retained = fy.get("corvee_retained", 0)
        ytd_commercial = fy.get("commercial_tax", 0)
        ytd_commercial_retained = fy.get("commercial_retained", 0)

        # Annual totals for reporting
        total_tax = agri_tax + ytd_corvee + ytd_commercial
        total_remit = agri_remit + (ytd_corvee - ytd_corvee_retained) + (ytd_commercial - ytd_commercial_retained)

        report["autumn"] = {
            "total_agri_output": round(total_agri_output, 1),
            "agri_tax": round(agri_tax, 1),
            "agri_remit": round(agri_remit, 1),
            "agri_retained": round(agri_retained, 1),
            "corvee_tax_ytd": round(ytd_corvee, 1),
            "corvee_retained_ytd": round(ytd_corvee_retained, 1),
            "commercial_tax_ytd": round(ytd_commercial, 1),
            "commercial_retained_ytd": round(ytd_commercial_retained, 1),
            "total_tax": round(total_tax, 1),
            "remit_ratio": remit_ratio,
            "remit_to_central": round(total_remit, 1),
            "admin_cost": admin,
            "net_treasury_change": round(net, 1),
            "treasury_after": round(county["treasury"], 1),
        }
        report["events"].append(
            f"秋季结算: 农业产出{round(total_agri_output)}两, "
            f"农业税{round(agri_tax)}两(留存{round(agri_retained)}两), "
            f"年度徭役{round(ytd_corvee)}两(留存{round(ytd_corvee_retained)}两), "
            f"年度商税{round(ytd_commercial)}两(留存{round(ytd_commercial_retained)}两), "
            f"总税收{round(total_tax)}两, 总上缴{round(total_remit)}两, "
            f"行政开支{admin}两(含基建维护), 县库净变化{round(net)}两")

    @classmethod
    def _winter_settlement(cls, county, month, report):
        """Winter (腊月): annual snapshot + clear disaster."""
        ensure_county_ledgers(county)
        county["disaster_this_year"] = None

        total_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county["villages"]
        )
        total_farmland = sum(v["farmland"] for v in county["villages"])
        report["winter_snapshot"] = {
            "year": year_of(month),
            "total_population": total_pop,
            "total_farmland": total_farmland,
            "treasury": round(county["treasury"], 1),
            "morale": round(county["morale"], 1),
            "security": round(county["security"], 1),
            "commercial": round(county["commercial"], 1),
            "education": round(county["education"], 1),
        }
        report["events"].append(
            f"冬季年终总结: 第{report['winter_snapshot']['year']}年完")
