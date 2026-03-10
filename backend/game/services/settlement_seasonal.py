"""季节性结算：秋收、年终、投资完成"""

import random

from ..models import Agent
from .constants import (
    MAX_MONTH,
    INFRA_MAX_LEVEL,
    IRRIGATION_DAMAGE_REDUCTION,
    GRANARY_POP_LOSS_MULTIPLIER,
    RELIEF_POP_LOSS_MULTIPLIER,
    PREF_GRANARY_POP_LOSS_MULT,
    CORVEE_PER_CAPITA,
    QUOTA_BASE_COLLECTION_EFFICIENCY,
    STIPEND_BY_BACKGROUND,
    calculate_infra_maint,
    month_of_year,
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
        任期末（season == MAX_MONTH）时，静默丢弃所有永远无法完成的基建
        （completion_season > MAX_MONTH），不产生任何效果。
        """
        remaining = []
        for inv in county["active_investments"]:
            if inv["completion_season"] <= season:
                cls._apply_investment_effect(county, inv, report, game=game)
            elif inv["completion_season"] > MAX_MONTH and season >= MAX_MONTH:
                # 任期内无法完成，任期末月静默清除
                pass
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
    def _set_annual_quota(cls, county, month, report):
        """正月：知府按在册土地和人口下达本年度上缴配额。
        配额按"标准年"估算：base_yield=0.5两/亩，含水利加成，不含农业适宜度和灾害。
        """
        ensure_county_ledgers(county)
        total_land = sum(v["farmland"] for v in county["villages"])
        total_peasant_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county["villages"]
        )
        tax_rate = county.get("tax_rate", 0.12)
        remit_ratio = county.get("remit_ratio", 0.65)
        irrigation_bonus = county.get("irrigation_level", 0) * 0.15

        # 农业税配额：标准年产出 × 税率 × 标准征收效率 × 上缴比例
        std_agri_output = total_land * 0.5 * (1 + irrigation_bonus)
        agri_quota = round(
            std_agri_output * tax_rate * QUOTA_BASE_COLLECTION_EFFICIENCY * remit_ratio, 1)

        # 徭役折银配额：在册村民 × 人均折银 × 上缴比例
        corvee_quota = round(total_peasant_pop * CORVEE_PER_CAPITA * remit_ratio, 1)

        county["annual_quota"] = {
            "agricultural": agri_quota,
            "corvee": corvee_quota,
            "total": round(agri_quota + corvee_quota, 1),
            "year": year_of(month),
        }
        # 清除上年的减免申请标记
        county.pop("relief_application_submitted", None)
        county.pop("relief_application", None)
        county.pop("autumn_tax_assessment", None)

        report["events"].append(
            f"知府下达本年配额：农业税{agri_quota}两、徭役折银{corvee_quota}两，"
            f"合计{agri_quota + corvee_quota}两（按在册土地人口估算，不含灾害因素）"
        )

    @staticmethod
    def _credit_annual_stipend(game, report):
        """正月：知县年度养廉银入账（合法薪俸，计入家产）。仅玩家路径。"""
        try:
            player = game.player
        except Exception:
            return
        stipend = STIPEND_BY_BACKGROUND.get(player.background, 15)
        player.personal_wealth = round((player.personal_wealth or 0) + stipend, 1)
        player.save(update_fields=['personal_wealth'])
        report['events'].append(
            f"知县年度养廉银{stipend}两入账，当前家产{player.personal_wealth}两"
        )

    @classmethod
    def _update_quota_completion(cls, county, agri_remitted, report):
        """Update quota completion once agri remittance is finalized."""
        fy = county.get("fiscal_year", {})
        annual_quota = county.get("annual_quota", {})
        if not annual_quota:
            return

        quota_total = annual_quota.get("total", 0)
        ytd_corvee = fy.get("corvee_tax", 0)
        ytd_corvee_retained = fy.get("corvee_retained", 0)
        corvee_remitted = ytd_corvee - ytd_corvee_retained
        total_remitted_to_prefecture = agri_remitted + corvee_remitted
        completion_rate = round(
            total_remitted_to_prefecture / max(quota_total, 1) * 100, 1)

        county["quota_completion"] = {
            "quota_total": quota_total,
            "actual_remitted": round(total_remitted_to_prefecture, 1),
            "completion_rate": completion_rate,
            "year": annual_quota.get("year"),
        }
        report["events"].append(
            f"年度配额完成：配额{quota_total}两，"
            f"实缴{round(total_remitted_to_prefecture)}两，"
            f"完成率{completion_rate}%"
        )

    @classmethod
    def _process_october_agri_payment(cls, county, month, report, game=None):
        """十月：执行九月核定的农业税上缴，并在此时结转灾害减免结果。"""
        if month_of_year(month) != 10:
            return

        assessment = county.get("autumn_tax_assessment") or {}
        if not assessment:
            return
        if assessment.get("status") == "PAID":
            return
        if assessment.get("year") != year_of(month):
            return

        agri_tax = float(assessment.get("agri_tax", 0.0))
        agri_remit_due = float(assessment.get("agri_remit_due", 0.0))
        relief_result = {}
        if hasattr(cls, "_review_disaster_relief_application"):
            relief_result = cls._review_disaster_relief_application(
                game=game,
                county=county,
                month=month,
                report=report,
                agri_remit_due=agri_remit_due,
            ) or {}

        relief_deduction = 0.0
        if relief_result.get("approved"):
            relief_deduction = min(
                agri_remit_due, float(relief_result.get("approved_amount", 0.0)))

        agri_remit_final = max(0.0, agri_remit_due - relief_deduction)
        agri_retained_final = max(0.0, agri_tax - agri_remit_final)
        county["treasury"] += agri_retained_final

        fy = county.get("fiscal_year", {})
        fy["agri_tax"] = round(agri_tax, 1)
        fy["agri_remitted"] = round(agri_remit_final, 1)
        county["fiscal_year"] = fy

        ytd_corvee = fy.get("corvee_tax", 0)
        ytd_corvee_retained = fy.get("corvee_retained", 0)
        ytd_commercial = fy.get("commercial_tax", 0)
        ytd_commercial_retained = fy.get("commercial_retained", 0)
        total_tax = agri_tax + ytd_corvee + ytd_commercial
        total_remit = agri_remit_final + (ytd_corvee - ytd_corvee_retained) + (
            ytd_commercial - ytd_commercial_retained)

        cls._update_quota_completion(county, agri_remit_final, report)

        assessment.update({
            "status": "PAID",
            "paid_season": month,
            "agri_remit_final": round(agri_remit_final, 1),
            "agri_retained_final": round(agri_retained_final, 1),
            "relief_deduction": round(relief_deduction, 1),
        })
        county["autumn_tax_assessment"] = assessment

        relief_note = ""
        if relief_deduction > 0:
            relief_note = f"，核减上缴{round(relief_deduction)}两"

        report["autumn_payment"] = {
            "agri_tax": round(agri_tax, 1),
            "agri_remit_due": round(agri_remit_due, 1),
            "relief_deduction": round(relief_deduction, 1),
            "agri_remit_final": round(agri_remit_final, 1),
            "agri_retained_final": round(agri_retained_final, 1),
            "corvee_tax_ytd": round(ytd_corvee, 1),
            "corvee_retained_ytd": round(ytd_corvee_retained, 1),
            "commercial_tax_ytd": round(ytd_commercial, 1),
            "commercial_retained_ytd": round(ytd_commercial_retained, 1),
            "total_tax": round(total_tax, 1),
            "remit_to_central": round(total_remit, 1),
            "net_treasury_change": round(agri_retained_final, 1),
            "treasury_after": round(county["treasury"], 1),
            "relief_result": relief_result,
        }
        report["events"].append(
            f"十月农业税上缴完成：应缴{round(agri_remit_due)}两，实缴{round(agri_remit_final)}两"
            f"{relief_note}，县库入账{round(agri_retained_final)}两"
        )

    @classmethod
    def _autumn_settlement(cls, county, report, peer_counties=None, prefecture_ctx=None):
        """Autumn: annual population update, agricultural output and agri tax only.
        Corvée and commercial tax already collected during the year via fiscal_year.
        prefecture_ctx: optional dict with granary bool for prefecture-level pop-loss reduction.
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
            granary_active = bool(county.get("has_granary", False))
            total_pop_loss = 0
            for v in county["villages"]:
                ensure_village_ledgers(v)
                loss_rate = random.uniform(0.02, disaster["severity"] / 5)
                base_pop = v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
                pop_loss = int(base_pop * loss_rate)
                if granary_active:
                    pop_loss = int(pop_loss * GRANARY_POP_LOSS_MULTIPLIER)
                if disaster.get("relieved"):
                    pop_loss = int(pop_loss * RELIEF_POP_LOSS_MULTIPLIER)
                # 府级义仓：跨县粮食调拨进一步减少人口损失
                if prefecture_ctx and prefecture_ctx.get("granary"):
                    pop_loss = int(pop_loss * PREF_GRANARY_POP_LOSS_MULT)
                new_pop = max(0, base_pop - pop_loss)
                v["peasant_ledger"]["registered_population"] = new_pop
                v["population"] = new_pop
                total_pop_loss += pop_loss
            pref_granary_active = bool((prefecture_ctx or {}).get("granary"))
            report["events"].append(
                f"灾害持续影响: 全县人口减少{total_pop_loss}人"
                f"{'（义仓减损）' if granary_active else ''}"
                f"{'（赈灾减损）' if disaster.get('relieved') else ''}"
                f"{'（府仓调拨）' if pref_granary_active else ''}")
            # 赈灾额外民心加成
            if disaster.get("relieved"):
                county["morale"] = min(100, county["morale"] + 2)
                report["events"].append("赈灾安民: 民心+2")

            if granary_active:
                county["has_granary"] = False
                county["granary_needs_rebuild"] = True
                county["granary_last_used_season"] = report.get("season")
                if not county.get("granary_rebuild_cost"):
                    county["granary_rebuild_cost"] = round(70 * county.get("price_index", 1.0))
                report["events"].append(
                    f"义仓本次赈济后已耗尽，需重建（预算{round(county['granary_rebuild_cost'])}两）"
                )

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
        agri_remit_due = agri_tax * remit_ratio
        agri_retained_due = agri_tax - agri_remit_due

        # Annual admin cost (deducted once per year at autumn, includes infra maintenance)
        admin = county["admin_cost"]

        # 九月仅核定农业税并扣除年度行政成本，实际税款在十月结转
        net = -admin
        county["treasury"] -= admin

        # YTD figures from fiscal_year (corvée + commercial collected during the year)
        fy = county.get("fiscal_year", {})
        ytd_corvee = fy.get("corvee_tax", 0)
        ytd_corvee_retained = fy.get("corvee_retained", 0)
        ytd_commercial = fy.get("commercial_tax", 0)
        ytd_commercial_retained = fy.get("commercial_retained", 0)

        # 秋季将农业税数据写入 fiscal_year（十月上缴后再写 agri_remitted）
        fy["agri_tax"] = round(agri_tax, 1)
        fy["agri_remitted"] = 0.0
        county["fiscal_year"] = fy

        assessment_year = None
        if report.get("season"):
            assessment_year = year_of(report.get("season"))
        else:
            assessment_year = (county.get("annual_quota") or {}).get("year")

        county["autumn_tax_assessment"] = {
            "year": assessment_year,
            "agri_tax": round(agri_tax, 1),
            "agri_remit_due": round(agri_remit_due, 1),
            "agri_retained_due": round(agri_retained_due, 1),
            "status": "PENDING_PAYMENT",
            "payment_month_of_year": 10,
        }

        # Annual totals for reporting
        total_tax = agri_tax + ytd_corvee + ytd_commercial
        total_remit = (ytd_corvee - ytd_corvee_retained) + (ytd_commercial - ytd_commercial_retained)

        report["autumn"] = {
            "total_agri_output": round(total_agri_output, 1),
            "agri_tax": round(agri_tax, 1),
            "agri_remit_due": round(agri_remit_due, 1),
            "agri_retained_due": round(agri_retained_due, 1),
            "agri_remit": round(agri_remit_due, 1),
            "agri_retained": round(agri_retained_due, 1),
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
            "payment_pending": True,
            "payment_month_of_year": 10,
        }
        report["events"].append(
            f"秋季结算: 农业产出{round(total_agri_output)}两, "
            f"农业税核定{round(agri_tax)}两(应上缴{round(agri_remit_due)}两, "
            f"预计留存{round(agri_retained_due)}两, 十月执行), "
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
