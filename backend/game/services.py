"""游戏核心业务逻辑"""
import copy
import random

from .models import EventLog


class CountyService:
    """县域初始化"""

    @staticmethod
    def create_initial_county():
        """生成MVP中等难度县的初始 county_data JSONB"""
        return {
            # 县域核心指标
            "morale": 50,           # 民心
            "security": 55,         # 治安
            "commercial": 35,       # 商业指数
            "education": 25,        # 文教指数
            "treasury": 400,        # 县库现金（两）
            "tax_rate": 0.12,       # 当前税率 (12% base)
            "irrigation_level": 0,  # 水利等级 (0/1/2)
            "has_granary": False,   # 义仓
            "bailiff_level": 0,     # 衙役等级 (0/1/2/3)
            "admin_cost": 80,       # 年度行政开支

            # 村庄 (6个 MVP)
            "villages": [
                {"name": "李家村", "population": 800, "farmland": 1600,
                 "gentry_land_pct": 0.35, "morale": 50, "security": 55, "has_school": False},
                {"name": "张家村", "population": 650, "farmland": 1300,
                 "gentry_land_pct": 0.30, "morale": 52, "security": 58, "has_school": False},
                {"name": "王家村", "population": 900, "farmland": 1800,
                 "gentry_land_pct": 0.40, "morale": 48, "security": 50, "has_school": False},
                {"name": "陈家村", "population": 550, "farmland": 1100,
                 "gentry_land_pct": 0.25, "morale": 55, "security": 60, "has_school": False},
                {"name": "赵家村", "population": 700, "farmland": 1400,
                 "gentry_land_pct": 0.38, "morale": 45, "security": 52, "has_school": False},
                {"name": "刘家村", "population": 400, "farmland": 800,
                 "gentry_land_pct": 0.20, "morale": 53, "security": 57, "has_school": False},
            ],
            # Total: pop ~4000, farmland ~8000, avg gentry ~31%

            # 集市 (2个 MVP)
            "markets": [
                {"name": "东关集", "merchants": 15, "trade_index": 35},
                {"name": "西街市", "merchants": 10, "trade_index": 30},
            ],

            # 全局环境 (doc 06 §2)
            "environment": {
                "agriculture_suitability": 0.7,  # 农业适宜度
                "flood_risk": 0.4,               # 水患风险度
                "border_threat": 0.2,            # 边患风险度
            },

            # 灾害状态
            "disaster_this_year": None,  # or {"type": "flood", "severity": 0.5, "relieved": False}

            # 投资追踪
            "active_investments": [],  # 进行中的项目
        }


class InvestmentService:
    """投资行动处理"""

    # 投资类型定义: cost, delay_seasons, requires_target_village
    INVESTMENT_TYPES = {
        "reclaim_land": {
            "cost": 50,
            "delay_seasons": None,  # completes at next autumn
            "requires_village": True,
            "description": "开垦荒地",
        },
        "build_irrigation": {
            "cost": 100,
            "delay_seasons": 8,  # 2 years = 8 seasons
            "requires_village": False,
            "description": "修建水利",
        },
        "expand_school": {
            "cost": 80,
            "delay_seasons": 8,  # 2 years
            "requires_village": False,
            "description": "扩建县学",
        },
        "fund_village_school": {
            "cost": 30,
            "delay_seasons": 4,  # 1 year
            "requires_village": True,
            "description": "资助村塾",
        },
        "hire_bailiffs": {
            "cost": 40,
            "delay_seasons": 0,  # immediate
            "requires_village": False,
            "description": "增设衙役",
        },
        "repair_roads": {
            "cost": 60,
            "delay_seasons": 1,  # 1 season
            "requires_village": False,
            "description": "修缮道路",
        },
        "build_granary": {
            "cost": 70,
            "delay_seasons": 0,  # immediate
            "requires_village": False,
            "description": "开设义仓",
        },
        "relief": {
            "cost": 80,
            "delay_seasons": 0,  # immediate
            "requires_village": False,
            "description": "赈灾救济",
        },
    }

    @classmethod
    def execute(cls, game, action, target_village=None):
        """
        Execute an investment action.
        Returns (success: bool, message: str).
        """
        if action not in cls.INVESTMENT_TYPES:
            return False, f"未知的投资类型: {action}"

        spec = cls.INVESTMENT_TYPES[action]
        county = game.county_data

        if game.current_season > 12:
            return False, "游戏已结束，无法投资"

        # Validate treasury
        if county["treasury"] < spec["cost"]:
            return False, f"县库资金不足，需要{spec['cost']}两，当前{county['treasury']}两"

        # Validate target village if required
        village_idx = None
        if spec["requires_village"]:
            if target_village is None:
                return False, f"{spec['description']}需要指定目标村庄"
            village_names = [v["name"] for v in county["villages"]]
            if target_village not in village_names:
                return False, f"村庄 '{target_village}' 不存在"
            village_idx = village_names.index(target_village)

        # Action-specific validation
        if action == "hire_bailiffs" and county["bailiff_level"] >= 3:
            return False, "衙役已达最高等级(3)"
        if action == "build_irrigation" and county["irrigation_level"] >= 2:
            return False, "水利已达最高等级(2)"
        if action == "build_granary" and county["has_granary"]:
            return False, "义仓已建成"
        if action == "relief":
            if county.get("disaster_this_year") is None:
                return False, "当前无灾害，无需赈灾"
            if county["disaster_this_year"].get("relieved"):
                return False, "已进行过赈灾救济"
        if action == "fund_village_school":
            if county["villages"][village_idx]["has_school"]:
                return False, f"{target_village}已有村塾"

        # Deduct cost
        county["treasury"] -= spec["cost"]

        # Apply immediate or delayed effects
        if action == "hire_bailiffs":
            county["bailiff_level"] += 1
            county["security"] = min(100, county["security"] + 8)
            county["admin_cost"] += 40
            game.county_data = county
            game.save()
            return True, f"衙役等级提升至{county['bailiff_level']}，治安+8，年行政开支+40两"

        if action == "build_granary":
            county["has_granary"] = True
            county["morale"] = min(100, county["morale"] + 5)
            game.county_data = county
            game.save()
            return True, "义仓建成，民心+5，灾害时损失减半"

        if action == "relief":
            county["disaster_this_year"]["relieved"] = True
            county["morale"] = min(100, county["morale"] + 8)
            game.county_data = county
            game.save()
            return True, "赈灾救济已实施，民心+8，秋季灾害损失减半"

        # Delayed investments: compute completion season
        current = game.current_season
        if action == "reclaim_land":
            # Completes at next autumn: seasons 3, 7, 11
            autumn_seasons = [s for s in [3, 7, 11] if s > current]
            if not autumn_seasons:
                completion = 13  # won't complete in this game
            else:
                completion = autumn_seasons[0]
        else:
            completion = current + spec["delay_seasons"]

        investment = {
            "action": action,
            "started_season": current,
            "completion_season": completion,
            "description": spec["description"],
        }
        if village_idx is not None:
            investment["target_village"] = target_village

        county["active_investments"].append(investment)
        game.county_data = county
        game.save()

        if completion > 12:
            return True, f"{spec['description']}已启动（花费{spec['cost']}两），但将在游戏结束后才完成"
        return True, f"{spec['description']}已启动（花费{spec['cost']}两），预计第{completion}季度完成"


class SettlementService:
    """季度结算引擎"""

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

        # 1. [Spring] Environment drift
        if season in (1, 5, 9):
            cls._drift_environment(county, report)

        # 2. Check & apply completed investments
        cls._apply_completed_investments(county, season, report)

        # 3. [Summer] Disaster check
        if season in (2, 6, 10):
            cls._summer_disaster_check(game, county, report)

        # 4. Population change (doc 06 §4.6)
        cls._update_population(county, report)

        # 5. Morale change (doc 06 §4.5)
        cls._update_morale(county, report)

        # 6. Security change (doc 06 §4.5)
        cls._update_security(county, report)

        # 7. [Autumn] Agricultural output + tax (seasons 3, 7, 11)
        if season in (3, 7, 11):
            cls._autumn_settlement(county, report)

        # 8. [Winter] Annual snapshot + clear disaster (seasons 4, 8, 12)
        if season in (4, 8, 12):
            cls._winter_settlement(county, season, report)

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

        return report

    @classmethod
    def _apply_completed_investments(cls, county, season, report):
        """Apply effects of investments that complete this season."""
        remaining = []
        for inv in county["active_investments"]:
            if inv["completion_season"] <= season:
                cls._apply_investment_effect(county, inv, report)
            else:
                remaining.append(inv)
        county["active_investments"] = remaining

    @classmethod
    def _apply_investment_effect(cls, county, inv, report):
        """Apply the effect of a single completed investment."""
        action = inv["action"]

        if action == "reclaim_land":
            village_name = inv["target_village"]
            for v in county["villages"]:
                if v["name"] == village_name:
                    v["farmland"] += 200
                    report["events"].append(f"{village_name}开垦完成，耕地+200亩")
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
    def _summer_disaster_check(cls, game, county, report):
        """Summer: roll for disasters (doc 06 §3)."""
        env = county["environment"]

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
                0.05,
                (0.05, 0.15),  # population loss fraction
                -15,
            ),
        ]

        for dtype, prob, sev_range, morale_hit in disaster_table:
            if random.random() < prob:
                severity = random.uniform(sev_range[0], sev_range[1])

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
                        f"人口减少{pop_loss}人，民心-{abs(morale_hit)}")
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
                    choice="",
                )

                break  # only one disaster per year

    @classmethod
    def _update_population(cls, county, report):
        """Calculate population change per doc 06 §4.6."""
        total_pop_before = sum(v["population"] for v in county["villages"])

        for v in county["villages"]:
            pop = v["population"]
            # Natural growth: 0.5% per season
            growth = int(pop * 0.005)

            # Inflow: if commercial > 50, +10~30 distributed across villages
            inflow = 0
            if county["commercial"] > 50:
                # Proportional share of 20 people total inflow
                share = pop / max(total_pop_before, 1)
                inflow = int(20 * share)

            # Outflow: if morale < 30, 2% loss; if security < 20, extra 3%
            outflow = 0
            if county["morale"] < 30:
                outflow += int(pop * 0.02)
            if county["security"] < 20:
                outflow += int(pop * 0.03)

            v["population"] = max(0, pop + growth + inflow - outflow)

        total_pop_after = sum(v["population"] for v in county["villages"])
        change = total_pop_after - total_pop_before
        if change != 0:
            report["events"].append(
                f"人口变化: {'+' if change > 0 else ''}{change} "
                f"(总人口: {total_pop_after})")

    @classmethod
    def _update_morale(cls, county, report):
        """Calculate morale change per doc 06 §4.5."""
        old = county["morale"]

        # Base decay: -1
        delta = -1

        # Education contribution: education / 20
        delta += county["education"] / 20

        # Heavy tax penalty: if tax_rate > 0.15, penalty
        if county["tax_rate"] > 0.15:
            delta -= 3

        county["morale"] = max(0, min(100, county["morale"] + delta))

        actual_change = county["morale"] - old
        if actual_change != 0:
            report["events"].append(
                f"民心变化: {'+' if actual_change > 0 else ''}"
                f"{actual_change:.1f} (当前: {county['morale']:.1f})")

    @classmethod
    def _update_security(cls, county, report):
        """Calculate security change per doc 06 §4.5."""
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

        actual_change = county["security"] - old
        if actual_change != 0:
            report["events"].append(
                f"治安变化: {'+' if actual_change > 0 else ''}"
                f"{actual_change:.1f} (当前: {county['security']:.1f})")

    @classmethod
    def _autumn_settlement(cls, county, report):
        """Autumn: calculate agricultural output and tax revenue (doc 06 §4.1-4.2)."""
        env = county["environment"]
        suitability = env["agriculture_suitability"]
        irrigation_bonus = county["irrigation_level"] * 0.15  # 0/0.15/0.3

        # Agricultural output per village (doc 06 §4.1)
        # village_output = farmland * base_yield * suitability * (1 + irrigation_bonus)
        base_yield = 2  # 两/亩
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

        # Agricultural tax (doc 06 §4.2)
        # collection_efficiency based on morale (0.7 ~ 1.0)
        morale_factor = county["morale"] / 100
        collection_efficiency = 0.7 + 0.3 * morale_factor  # ranges 0.7-1.0
        agri_tax = total_agri_output * county["tax_rate"] * collection_efficiency

        # Commercial tax (doc 06 §4.2)
        # per market: merchants * 5 * trade_index/50
        commercial_tax = 0
        for m in county["markets"]:
            commercial_tax += m["merchants"] * 5 * m["trade_index"] / 50

        total_tax = agri_tax + commercial_tax

        # Central remittance: 65% of total tax
        remit = total_tax * 0.65
        retained = total_tax - remit

        # Annual admin cost (deducted once per year at autumn)
        admin = county["admin_cost"]

        # Net change to treasury
        net = retained - admin
        county["treasury"] += net

        report["autumn"] = {
            "total_agri_output": round(total_agri_output, 1),
            "agri_tax": round(agri_tax, 1),
            "commercial_tax": round(commercial_tax, 1),
            "total_tax": round(total_tax, 1),
            "remit_to_central": round(remit, 1),
            "admin_cost": admin,
            "net_treasury_change": round(net, 1),
            "treasury_after": round(county["treasury"], 1),
        }
        report["events"].append(
            f"秋季结算: 农业产出{round(total_agri_output)}两, "
            f"税收{round(total_tax)}两, 上缴{round(remit)}两, "
            f"行政开支{admin}两, 县库净变化{round(net)}两")

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
    def get_summary(cls, game):
        """Get end-game summary for a completed game."""
        if game.current_season <= 12:
            return None
        return cls._generate_summary(game, game.county_data)
