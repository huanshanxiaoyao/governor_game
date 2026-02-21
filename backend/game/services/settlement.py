"""季度结算引擎"""

import random

from ..models import Agent, EventLog, NegotiationSession
from .constants import (
    MAX_YIELD_PER_MU,
    ANNUAL_CONSUMPTION,
    BASE_GROWTH_RATE,
    GROWTH_RATE_CLAMP,
    calculate_medical_cost,
    CORVEE_PER_CAPITA,
    GENTRY_POP_RATIO_COEFF,
)


class SettlementService:
    """季度结算引擎"""

    @classmethod
    def settle_county(cls, county, season, report):
        """
        纯county_data级物理结算 — 邻县和玩家共用。
        不涉及 game/Agent/EventLog/NegotiationSession/Promise 等数据库操作。
        """
        # 1. [Spring] Environment drift
        if season in (1, 5, 9):
            cls._drift_environment(county, report)

        # 2. Check & apply completed investments (data-only, no game)
        cls._apply_completed_investments(county, season, report)

        # 3. [Summer] Disaster check (data-only)
        if season in (2, 6, 10):
            cls._disaster_check_data(county, report)

        # 4. Morale change
        cls._update_morale(county, report)

        # 5. Security change
        cls._update_security(county, report)

        # 6. [Autumn] Agricultural output + tax
        if season in (3, 7, 11):
            cls._autumn_settlement(county, report)

        # 7. [Winter] Annual snapshot + clear disaster
        if season in (4, 8, 12):
            cls._winter_settlement(county, season, report)

    @classmethod
    def advance_season(cls, game):
        """
        Advance the game by one season. Returns a settlement report dict.
        """
        if game.current_season > 12:
            return {"error": "游戏已结束"}

        county = game.county_data
        season = game.current_season
        report = {"season": season, "events": []}

        # 0. Reset per-season counters
        county["advisor_questions_used"] = 0

        # 1. [Spring] Environment drift
        if season in (1, 5, 9):
            cls._drift_environment(county, report)

        # 2. Check & apply completed investments (with Agent updates)
        cls._apply_completed_investments(county, season, report, game=game)

        # 3. [Summer] Disaster check (with EventLog)
        if season in (2, 6, 10):
            cls._summer_disaster_check(game, county, report)

        # 4. (Population growth now handled annually at autumn)

        # 5. Morale change (doc 06 §4.5)
        cls._update_morale(county, report)

        # 6. Security change (doc 06 §4.5)
        cls._update_security(county, report)

        # 6b. Annexation event check (per-village gentry)
        cls._check_annexation_events(game, county, report)

        # 7. [Autumn] Agricultural output + tax (seasons 3, 7, 11)
        if season in (3, 7, 11):
            cls._autumn_settlement(county, report)

        # 8. [Winter] Annual snapshot + clear disaster (seasons 4, 8, 12)
        if season in (4, 8, 12):
            cls._winter_settlement(county, season, report)

        # 8b. Check promises
        from ..promise_service import PromiseService
        try:
            promise_events = PromiseService.check_promises(game)
            report['events'].extend(promise_events)
        except Exception as e:
            import logging
            logging.getLogger('game').warning("Promise check failed (non-fatal): %s", e)

        # 9. Advance season counter
        game.current_season = season + 1
        report["next_season"] = game.current_season

        # 10. Game end check
        if game.current_season > 12:
            report["game_over"] = True
            report["summary"] = cls._generate_summary(game, county)
        else:
            report["game_over"] = False

        game.county_data = county
        game.save()

        # Log settlement summary
        log_data = {'events': report.get('events', [])}
        if report.get('autumn'):
            log_data['autumn'] = report['autumn']
        if report.get('winter_snapshot'):
            log_data['winter_snapshot'] = report['winter_snapshot']
        EventLog.objects.create(
            game=game,
            season=season,
            event_type='season_settlement',
            category='SETTLEMENT',
            description=f"第{season}季度结算",
            data=log_data,
        )

        return report

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
        action = inv["action"]

        if action == "reclaim_land":
            village_name = inv["target_village"]
            for v in county["villages"]:
                if v["name"] == village_name:
                    old_farmland = v["farmland"]
                    old_pct = v.get("gentry_land_pct", 0.3)
                    gentry_land = old_farmland * old_pct
                    v["farmland"] += 800
                    v["gentry_land_pct"] = round(gentry_land / v["farmland"], 4)
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
                                f"第{game.current_season}季度，知县大人下令开垦荒地，"
                                f"{village_name}百姓新增耕地，感激不已")
                            if len(memory) > 20:
                                memory = memory[-20:]
                            attrs['memory'] = memory
                            villager.attributes = attrs
                            villager.save(update_fields=['attributes'])
                    break

        elif action == "build_irrigation":
            county["irrigation_level"] = min(2, county["irrigation_level"] + 1)
            report["events"].append(
                f"水利工程完工，水利等级提升至{county['irrigation_level']}")

        elif action == "expand_school":
            county["education"] = min(100, county["education"] + 10)
            report["events"].append("县学扩建完成，文教+10")

        elif action == "fund_village_school":
            village_name = inv["target_village"]
            for v in county["villages"]:
                if v["name"] == village_name:
                    v["has_school"] = True
                    v["morale"] = min(100, v["morale"] + 5)
                    report["events"].append(f"{village_name}村塾建成，民心+5")
                    break

        elif action == "repair_roads":
            county["commercial"] = min(100, county["commercial"] + 8)
            report["events"].append("道路修缮完成，商业+8")

    @classmethod
    def _drift_environment(cls, county, report):
        """Spring: drift environment variables (doc 06 §2.1-2.2)."""
        env = county["environment"]

        env["agriculture_suitability"] = max(0.3, min(1.0,
            env["agriculture_suitability"] + random.uniform(-0.1, 0.1)))
        env["flood_risk"] = max(0.0, min(1.0,
            env["flood_risk"] + random.uniform(-0.1, 0.1)))
        env["border_threat"] = max(0.0, min(1.0,
            env["border_threat"] + random.uniform(-0.05, 0.05)))

        # Narrative hints
        if env["agriculture_suitability"] >= 0.8:
            report["events"].append("今春风调雨顺，老农皆言是个好年景")
        elif env["agriculture_suitability"] <= 0.4:
            report["events"].append("开春以来旱象初现，不少田地未能按时播种")

        if env["flood_risk"] >= 0.7:
            report["events"].append("入夏以来雨水偏多，堤坝需多加留意")

        if env["border_threat"] >= 0.5:
            report["events"].append("北方边报频传，朝中气氛紧张")

    @classmethod
    def _disaster_check_data(cls, county, report):
        """Summer disaster check — pure data, no EventLog creation."""
        env = county["environment"]
        medical_level = county.get("medical_level", 0)
        medical_mult = 0.85 ** medical_level

        disaster_table = [
            (
                "flood",
                max(0.02 if env["flood_risk"] > 0 else 0,
                    env["flood_risk"] * 0.3 * (1 - county["irrigation_level"] * 0.5)),
                (0.4, 0.7),
                -10,
            ),
            ("drought", 0.15 * (1 - env["agriculture_suitability"]), (0.3, 0.6), -8),
            ("locust", 0.08, (0.2, 0.4), -5),
            ("plague", 0.05 * medical_mult, (0.05, 0.15), -15),
        ]

        DISASTER_NAMES = {"flood": "洪灾", "drought": "旱灾", "locust": "蝗灾", "plague": "疫病"}

        for dtype, prob, sev_range, morale_hit in disaster_table:
            if random.random() < prob:
                severity = random.uniform(sev_range[0], sev_range[1])
                if dtype == "plague":
                    severity *= medical_mult

                county["disaster_this_year"] = {
                    "type": dtype,
                    "severity": round(severity, 3),
                    "relieved": False,
                }
                county["morale"] = max(0, county["morale"] + morale_hit)

                if dtype == "plague":
                    village = random.choice(county["villages"])
                    pop_loss = int(village["population"] * severity)
                    village["population"] = max(0, village["population"] - pop_loss)
                    report["events"].append(
                        f"疫病突袭！{village['name']}染疫，"
                        f"人口减少{pop_loss}人，民心-{abs(morale_hit)}"
                        f"{'（医疗减损）' if medical_level > 0 else ''}")
                else:
                    narrative = {
                        "flood": f"夏季洪水泛滥，预计秋收损失{severity:.0%}，民心-{abs(morale_hit)}",
                        "drought": f"旱灾肆虐，田地干裂，预计秋收损失{severity:.0%}，民心-{abs(morale_hit)}",
                        "locust": f"蝗灾来袭，遮天蔽日，预计秋收损失{severity:.0%}，民心-{abs(morale_hit)}",
                    }
                    report["events"].append(narrative[dtype])
                break

    @classmethod
    def _summer_disaster_check(cls, game, county, report):
        """Summer: roll for disasters (doc 06 §3)."""
        env = county["environment"]
        medical_level = county.get("medical_level", 0)
        # Medical multiplier: 0.85 per level (applies to plague prob & severity)
        medical_mult = 0.85 ** medical_level

        # Disaster candidates: (type, probability, severity_range, morale_hit)
        disaster_table = [
            (
                "flood",
                max(0.02 if env["flood_risk"] > 0 else 0,
                    env["flood_risk"] * 0.3 * (1 - county["irrigation_level"] * 0.5)),
                (0.4, 0.7),
                -10,
            ),
            (
                "drought",
                0.15 * (1 - env["agriculture_suitability"]),
                (0.3, 0.6),
                -8,
            ),
            (
                "locust",
                0.08,
                (0.2, 0.4),
                -5,
            ),
            (
                "plague",
                0.05 * medical_mult,  # reduced probability by medical level
                (0.05, 0.15),  # population loss fraction (severity also reduced)
                -15,
            ),
        ]

        for dtype, prob, sev_range, morale_hit in disaster_table:
            if random.random() < prob:
                severity = random.uniform(sev_range[0], sev_range[1])

                # Plague severity reduced by medical level
                if dtype == "plague":
                    severity *= medical_mult

                county["disaster_this_year"] = {
                    "type": dtype,
                    "severity": round(severity, 3),
                    "relieved": False,
                }

                # Apply immediate morale hit
                county["morale"] = max(0, county["morale"] + morale_hit)

                # Plague: reduce population in a random village
                if dtype == "plague":
                    village = random.choice(county["villages"])
                    pop_loss = int(village["population"] * severity)
                    village["population"] = max(0, village["population"] - pop_loss)
                    report["events"].append(
                        f"疫病突袭！{village['name']}染疫，"
                        f"人口减少{pop_loss}人，民心-{abs(morale_hit)}"
                        f"{'（医疗减损）' if medical_level > 0 else ''}")
                else:
                    narrative = {
                        "flood": f"夏季洪水泛滥，预计秋收损失{severity:.0%}，民心-{abs(morale_hit)}",
                        "drought": f"旱灾肆虐，田地干裂，预计秋收损失{severity:.0%}，民心-{abs(morale_hit)}",
                        "locust": f"蝗灾来袭，遮天蔽日，预计秋收损失{severity:.0%}，民心-{abs(morale_hit)}",
                    }
                    report["events"].append(narrative[dtype])

                # EventLog
                EventLog.objects.create(
                    game=game,
                    season=game.current_season,
                    event_type=f"disaster_{dtype}",
                    category='DISASTER',
                    description=report["events"][-1],
                    data={
                        'disaster_type': dtype,
                        'severity': round(severity, 3),
                    },
                )

                break  # only one disaster per year

    @staticmethod
    def _calculate_village_ceiling(village, county):
        """Calculate population ceiling (carrying capacity) for a village."""
        env = county.get("environment", {})
        ag_suit = env.get("agriculture_suitability", 0.7)
        irrigation = county.get("irrigation_level", 0)
        tax_rate = county.get("tax_rate", 0.12)
        gentry_pct = village.get("gentry_land_pct", 0.3)

        effective_farmland = village["farmland"] * (1 - gentry_pct)
        irrigation_bonus = 1 + min(0.20, irrigation * 0.05)
        ceiling = (effective_farmland * ag_suit * MAX_YIELD_PER_MU
                   * irrigation_bonus * (1 - tax_rate) / ANNUAL_CONSUMPTION)
        return int(ceiling)

    @staticmethod
    def _capacity_modifier(pop, ceiling):
        """Logistic capacity modifier for population growth."""
        if ceiling <= 0:
            return -0.5
        ratio = (ceiling - pop) / ceiling
        if ratio > 0:
            return ratio ** 0.5  # diminishing boost as capacity fills
        else:
            return ratio * 2.0  # aggressive decline when overcrowded

    @classmethod
    def _annual_population_update(cls, county, report):
        """Annual population growth — called once per year at autumn."""
        medical_level = county.get("medical_level", 0)
        total_pop_before = sum(v["population"] for v in county["villages"])
        village_details = []

        for v in county["villages"]:
            pop = v["population"]
            ceiling = cls._calculate_village_ceiling(v, county)
            v["ceiling"] = ceiling

            # Morale modifier: ×1.01 per point above 50, ×0.99 per point below
            morale_mult = 1.01 ** (v.get("morale", 50) - 50)

            # Medical modifier: ×1.05 per level
            medical_mult = 1.05 ** medical_level

            # Capacity modifier
            cap_mod = cls._capacity_modifier(pop, ceiling)

            # Combined growth rate, clamped
            growth_rate = BASE_GROWTH_RATE * morale_mult * medical_mult * cap_mod
            growth_rate = max(-GROWTH_RATE_CLAMP, min(GROWTH_RATE_CLAMP, growth_rate))
            delta_growth = int(pop * growth_rate)

            # Separate additive factors
            inflow = 0
            if county["commercial"] > 50:
                share = pop / max(total_pop_before, 1)
                inflow = int(20 * share)

            outflow = 0
            if county["security"] < 20:
                outflow = int(pop * 0.03)

            new_pop = max(0, pop + delta_growth + inflow - outflow)
            change = new_pop - pop
            v["population"] = new_pop

            village_details.append({
                "name": v["name"],
                "pop_before": pop,
                "ceiling": ceiling,
                "growth_rate": round(growth_rate * 100, 2),
                "delta_growth": delta_growth,
                "inflow": inflow,
                "outflow": outflow,
                "pop_after": new_pop,
            })

        total_pop_after = sum(v["population"] for v in county["villages"])
        total_change = total_pop_after - total_pop_before

        report["population_update"] = {
            "villages": village_details,
            "total_before": total_pop_before,
            "total_after": total_pop_after,
            "total_change": total_change,
        }
        report["events"].append(
            f"年度人口变化: {'+' if total_change >= 0 else ''}{total_change} "
            f"(总人口: {total_pop_after})")

    @classmethod
    def _update_morale(cls, county, report):
        """Calculate morale change per doc 06 §4.5, with county↔village sync."""
        old = county["morale"]

        # Base decay: -1
        delta = -1

        # Education contribution: education / 20
        delta += county["education"] / 20

        # Heavy tax penalty: if tax_rate > 0.15, penalty
        if county["tax_rate"] > 0.15:
            delta -= 3

        county["morale"] = max(0, min(100, county["morale"] + delta))
        county_delta = county["morale"] - old

        # County → Village propagation: 县级变化的50%传导到各村
        if county_delta != 0:
            for v in county["villages"]:
                v["morale"] = max(0, min(100, v["morale"] + county_delta * 0.5))

        # Village → County aggregation: 按人口权重加权平均，与当前县级民心混合
        cls._sync_county_from_villages(county, "morale")

        actual_change = county["morale"] - old
        if actual_change != 0:
            report["events"].append(
                f"民心变化: {'+' if actual_change > 0 else ''}"
                f"{actual_change:.1f} (当前: {county['morale']:.1f})")

    @classmethod
    def _update_security(cls, county, report):
        """Calculate security change per doc 06 §4.5, with county↔village sync."""
        old = county["security"]

        # Base decay: -1
        delta = -1

        # Bailiff bonus: bailiff_level * 2
        delta += county["bailiff_level"] * 2

        # Morale linkage
        if county["morale"] > 60:
            delta += 1
        elif county["morale"] < 30:
            delta -= 2

        county["security"] = max(0, min(100, county["security"] + delta))
        county_delta = county["security"] - old

        # County → Village propagation
        if county_delta != 0:
            for v in county["villages"]:
                v["security"] = max(0, min(100, v["security"] + county_delta * 0.5))

        # Village → County aggregation
        cls._sync_county_from_villages(county, "security")

        actual_change = county["security"] - old
        if actual_change != 0:
            report["events"].append(
                f"治安变化: {'+' if actual_change > 0 else ''}"
                f"{actual_change:.1f} (当前: {county['security']:.1f})")

    @staticmethod
    def _sync_county_from_villages(county, field):
        """按人口权重将各村指标汇聚到县级，与当前县值混合(70%村均/30%县值)"""
        villages = county["villages"]
        total_pop = sum(v.get("population", 0) for v in villages)
        if total_pop <= 0:
            return
        weighted_sum = sum(
            v.get(field, 50) * v.get("population", 0) for v in villages)
        weighted_avg = weighted_sum / total_pop
        county[field] = max(0, min(100,
            round(0.7 * weighted_avg + 0.3 * county[field], 1)))

    @classmethod
    def _autumn_settlement(cls, county, report):
        """Autumn: annual population update, agricultural output and tax revenue."""
        # Annual population update (once per year at autumn)
        cls._annual_population_update(county, report)

        env = county["environment"]
        suitability = env["agriculture_suitability"]
        irrigation_bonus = county["irrigation_level"] * 0.15  # 0/0.15/0.3

        # Agricultural output per village
        # base_yield 0.5 两/亩 (was 2.0, reduced because farmland ×4)
        base_yield = 0.5  # 两/亩
        total_agri_output = 0
        for v in county["villages"]:
            output = v["farmland"] * base_yield * suitability * (1 + irrigation_bonus)
            total_agri_output += output

        # Disaster damage (non-plague disasters reduce output)
        disaster = county.get("disaster_this_year")
        disaster_damage = 0
        if disaster and disaster["type"] != "plague":
            damage_factor = disaster["severity"]
            if county["has_granary"]:
                damage_factor *= 0.5
            if disaster.get("relieved"):
                damage_factor *= 0.5
            disaster_damage = total_agri_output * damage_factor
            total_agri_output *= (1 - damage_factor)
            report["events"].append(
                f"灾害影响秋收: 产出损失{round(disaster_damage)}两"
                f"{'（义仓减损）' if county['has_granary'] else ''}"
                f"{'（赈灾减损）' if disaster.get('relieved') else ''}")

        # Agricultural tax (doc 06a §4.1)
        morale_factor = county["morale"] / 100
        collection_efficiency = 0.7 + 0.3 * morale_factor  # ranges 0.7-1.0
        agri_tax = total_agri_output * county["tax_rate"] * collection_efficiency

        # Corvée tax (doc 06a §4.2) — 地主免役，仅对平民征收
        total_pop = sum(v["population"] for v in county["villages"])
        gentry_ratio = county.get("gentry_land_ratio", 0.35)
        gentry_pop_ratio = gentry_ratio * GENTRY_POP_RATIO_COEFF
        liable_pop = total_pop * (1 - gentry_pop_ratio)
        corvee_tax = liable_pop * CORVEE_PER_CAPITA

        # Commercial tax (doc 06a §4.3)
        commercial_tax = 0
        for m in county["markets"]:
            commercial_tax += m["merchants"] * 5 * m["trade_index"] / 50

        total_tax = agri_tax + corvee_tax + commercial_tax

        # Central remittance (doc 06a §4.4) — 上缴比例按县域类型
        remit_ratio = county.get("remit_ratio", 0.65)
        remit = total_tax * remit_ratio
        retained = total_tax - remit

        # Annual admin cost (deducted once per year at autumn)
        admin = county["admin_cost"]

        # Annual medical cost (per-capita, scaled by price index)
        medical_level = county.get("medical_level", 0)
        medical_cost = calculate_medical_cost(medical_level, total_pop, county.get("price_index", 1.0))

        # Net change to treasury
        net = retained - admin - medical_cost
        county["treasury"] += net

        report["autumn"] = {
            "total_agri_output": round(total_agri_output, 1),
            "agri_tax": round(agri_tax, 1),
            "corvee_tax": round(corvee_tax, 1),
            "commercial_tax": round(commercial_tax, 1),
            "total_tax": round(total_tax, 1),
            "remit_ratio": remit_ratio,
            "remit_to_central": round(remit, 1),
            "admin_cost": admin,
            "medical_cost": medical_cost,
            "net_treasury_change": round(net, 1),
            "treasury_after": round(county["treasury"], 1),
        }
        report["events"].append(
            f"秋季结算: 农业产出{round(total_agri_output)}两, "
            f"农业税{round(agri_tax)}两, 徭役折银{round(corvee_tax)}两, "
            f"商业税{round(commercial_tax)}两, "
            f"上缴{round(remit)}两({remit_ratio:.0%}), "
            f"行政开支{admin}两"
            f"{f', 医疗开支{medical_cost}两' if medical_cost > 0 else ''}"
            f", 县库净变化{round(net)}两")

    @classmethod
    def _winter_settlement(cls, county, season, report):
        """Winter: annual snapshot + clear disaster."""
        county["disaster_this_year"] = None

        total_pop = sum(v["population"] for v in county["villages"])
        total_farmland = sum(v["farmland"] for v in county["villages"])
        report["winter_snapshot"] = {
            "year": (season - 1) // 4 + 1,
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

    @classmethod
    def _generate_summary(cls, game, county):
        """Generate end-game summary stats."""
        total_pop = sum(v["population"] for v in county["villages"])
        total_farmland = sum(v["farmland"] for v in county["villages"])
        return {
            "final_season": 12,
            "total_population": total_pop,
            "total_farmland": total_farmland,
            "treasury": round(county["treasury"], 1),
            "morale": round(county["morale"], 1),
            "security": round(county["security"], 1),
            "commercial": round(county["commercial"], 1),
            "education": round(county["education"], 1),
            "irrigation_level": county["irrigation_level"],
            "has_granary": county["has_granary"],
            "bailiff_level": county["bailiff_level"],
            "medical_level": county.get("medical_level", 0),
            "villages": [
                {
                    "name": v["name"],
                    "population": v["population"],
                    "farmland": v["farmland"],
                    "has_school": v["has_school"],
                }
                for v in county["villages"]
            ],
        }

    @classmethod
    def _check_annexation_events(cls, game, county, report):
        """Check if any village gentry triggers a land annexation event."""
        # Skip if active negotiation already exists
        if NegotiationSession.objects.filter(game=game, status='active').exists():
            return

        has_disaster = county.get('disaster_this_year') is not None

        for v in county['villages']:
            # Probability formula
            prob = 0.08
            if v['morale'] < 40:
                prob += 0.10
            if v['morale'] < 25:
                prob += 0.15
            if v.get('gentry_land_pct', 0) > 0.35:
                prob += 0.05
            if has_disaster:
                prob += 0.10
            if v['morale'] > 60:
                prob -= 0.05
            prob = max(0.0, min(0.5, prob))

            if random.random() >= prob:
                continue

            # Find matching gentry agent by village_name
            village_name = v['name']
            gentry = Agent.objects.filter(
                game=game,
                role='GENTRY',
                attributes__village_name=village_name,
            ).first()
            if gentry is None:
                continue

            # Determine proposed increase
            proposed_increase = round(random.uniform(0.03, 0.08), 2)

            from ..negotiation_service import NegotiationService
            context_data = {
                'village_name': village_name,
                'current_pct': v.get('gentry_land_pct', 0.3),
                'proposed_pct_increase': proposed_increase,
                'morale_at_trigger': v['morale'],
            }
            session, err = NegotiationService.start_negotiation(
                game, gentry, 'ANNEXATION', context_data,
            )
            if err:
                break

            # Append notification to pending_events
            notification = {
                'type': 'ANNEXATION',
                'message': (
                    f'{village_name}的地主{gentry.name}趁民心低迷，'
                    f'正大肆收购村民田地！请前往与其交涉。'
                ),
                'negotiation_id': session.id,
                'village_name': village_name,
                'agent_name': gentry.name,
            }
            if not isinstance(game.pending_events, list):
                game.pending_events = []
            game.pending_events.append(notification)

            report['events'].append(
                f'【地主兼并】{village_name}的{gentry.name}趁机收购村民田地，'
                f'需与其谈判交涉'
            )

            EventLog.objects.create(
                game=game,
                season=game.current_season,
                event_type='annexation_trigger',
                category='ANNEXATION',
                description=(
                    f'{village_name}的地主{gentry.name}趁民心低迷，'
                    f'大肆收购村民田地'
                ),
                data={
                    'village_name': village_name,
                    'agent_name': gentry.name,
                    'proposed_increase': proposed_increase,
                },
            )

            # Only one annexation per season advance
            break

    @classmethod
    def get_summary(cls, game):
        """Get end-game summary for a completed game."""
        if game.current_season <= 12:
            return None
        return cls._generate_summary(game, game.county_data)
