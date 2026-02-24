"""县域初始化服务"""

import random

from .constants import (
    COUNTY_TYPES, ADMIN_COST_DETAIL, VILLAGE_NAMES, MARKET_NAMES,
    MAX_YIELD_PER_MU, ANNUAL_CONSUMPTION,
    calculate_infra_maint,
)


def _fluctuate(value, pct=0.20):
    """对基准值施加 ±pct 的相对随机波动"""
    return value * random.uniform(1 - pct, 1 + pct)


def _fluctuate_int(value, pct=0.20):
    return int(_fluctuate(value, pct))


def _fluctuate_clamp(value, lo, hi, pct=0.20):
    """波动后 clamp 到 [lo, hi]"""
    return max(lo, min(hi, _fluctuate(value, pct)))


class CountyService:
    """县域初始化"""

    @staticmethod
    def create_initial_county(county_type=None):
        """
        生成初始 county_data JSONB。

        county_type: 'fiscal_core' | 'clan_governance' | 'coastal' | 'disaster_prone' | None
        None 时随机选取。
        """
        if county_type is None:
            county_type = random.choice(list(COUNTY_TYPES.keys()))

        if county_type not in COUNTY_TYPES:
            raise ValueError(f"未知县域类型: {county_type}")

        base = COUNTY_TYPES[county_type]

        # 波动后的基础参数
        total_pop = _fluctuate_int(base["population"])
        total_farmland = _fluctuate_int(base["farmland"])
        gentry_land_ratio = round(_fluctuate_clamp(
            base["gentry_land_ratio"], 0.10, 0.90), 2)

        county = {
            # 县域类型
            "county_type": county_type,
            "county_type_name": base["name"],

            # 县域核心指标
            "morale": round(_fluctuate_clamp(base["morale"], 0, 100)),
            "security": round(_fluctuate_clamp(base["security"], 0, 100)),
            "commercial": round(_fluctuate_clamp(base["commercial"], 0, 100)),
            "education": round(_fluctuate_clamp(base["education"], 0, 100)),
            "treasury": round(_fluctuate(base["treasury"])),
            "tax_rate": 0.12,
            "remit_ratio": base["remit_ratio"],
            "gentry_land_ratio": gentry_land_ratio,
            "has_granary": False,
            "bailiff_level": 0,
            "admin_cost_detail": dict(ADMIN_COST_DETAIL[county_type]),
            "admin_cost": sum(ADMIN_COST_DETAIL[county_type].values()),
            "advisor_level": 1,
            "advisor_questions_used": 0,
            "price_index": base["price_index"],

            # 全局环境 (doc 06 §2)
            "environment": {
                "agriculture_suitability": round(_fluctuate_clamp(
                    base["agriculture_suitability"], 0.3, 1.0), 2),
                "flood_risk": round(_fluctuate_clamp(
                    base["flood_risk"], 0.0, 1.0), 2),
                "border_threat": round(_fluctuate_clamp(
                    base["border_threat"], 0.0, 1.0), 2),
            },

            # 灾害状态
            "disaster_this_year": None,

            # 投资追踪
            "active_investments": [],

            # 商业系统追踪
            "road_repair_count": 0,
            "commercial_tax_rate": 0.03,

            # 年度财政累计（正月重置）
            "fiscal_year": {
                "commercial_tax": 0,
                "commercial_retained": 0,
                "corvee_tax": 0,
                "corvee_retained": 0,
            },
        }

        # 生成村庄
        village_count = base["village_count"]
        names = list(VILLAGE_NAMES[county_type])
        random.shuffle(names)
        village_names = names[:village_count]

        # 分配耕地和人口到各村（带随机权重）
        weights = [random.uniform(0.5, 1.5) for _ in range(village_count)]
        weight_sum = sum(weights)

        villages = []
        for i, vname in enumerate(village_names):
            share = weights[i] / weight_sum
            v_farmland = int(total_farmland * share)
            v_pop = int(total_pop * share)
            # 各村地主占地比围绕县级基准波动
            v_gentry = round(_fluctuate_clamp(
                gentry_land_ratio, 0.05, 0.95, pct=0.15), 2)
            villages.append({
                "name": vname,
                "farmland": v_farmland,
                "population": v_pop,  # 临时值，后续用 ceiling 重算
                "gentry_land_pct": v_gentry,
                "morale": round(_fluctuate_clamp(county["morale"], 0, 100, pct=0.10)),
                "security": round(_fluctuate_clamp(county["security"], 0, 100, pct=0.10)),
                "has_school": False,
            })
        county["villages"] = villages

        # 生成集市
        market_count = base["market_count"]
        mnames = list(MARKET_NAMES[county_type])
        # 如果需要的集市数量超过名称池，补充通用名
        while len(mnames) < market_count:
            mnames.append(f"集市{len(mnames)+1}")
        markets = []
        for j in range(market_count):
            merchants = _fluctuate_int(
                max(5, base["commercial"] // 4), pct=0.20)
            markets.append({
                "name": mnames[j],
                "merchants": merchants,
                "gmv": 0,
            })
        county["markets"] = markets

        # 初始化基建等级 (doc 06a §1.1b)
        # 财赋/宗族型: school=1, irrigation=1, medical=1
        # 沿海/灾荒型: school=1, irrigation=0, medical=0
        if county_type in ("fiscal_core", "clan_governance"):
            county["school_level"] = 1
            county["irrigation_level"] = 1
            county["medical_level"] = 1
        else:
            county["school_level"] = 1
            county["irrigation_level"] = 0
            county["medical_level"] = 0

        # 用 ceiling 模型重算人口（60% of carrying capacity）
        # 注意：需在初始化基建等级之后执行，确保财赋/宗族型按 irrigation=1 计算承载力
        from .settlement import SettlementService
        for v in county["villages"]:
            ceiling = SettlementService._calculate_village_ceiling(v, county)
            v["population"] = int(ceiling * 0.60)
            v["ceiling"] = ceiling

        # 基建维护费用加入 admin_cost_detail
        irr_maint = calculate_infra_maint("irrigation", county["irrigation_level"], county)
        med_maint = calculate_infra_maint("medical", county["medical_level"], county)
        county["admin_cost_detail"]["irrigation_maint"] = irr_maint
        county["admin_cost_detail"]["medical_maint"] = med_maint
        county["admin_cost"] = sum(county["admin_cost_detail"].values())

        # 初始化农民粮食储备（游戏开局正月，距上次九月收获约4个月）
        expected_annual = _compute_initial_peasant_production(county)
        total_pop = sum(v["population"] for v in county["villages"])
        monthly_consumption = total_pop * ANNUAL_CONSUMPTION / 12
        county["peasant_grain_reserve"] = expected_annual - 4 * monthly_consumption

        # 初始化前瞻盈余和集市GMV
        # 使用固定12月视野的年化月均余粮（与 _update_commercial 一致）
        months_to_harvest = 8  # 开局正月，距秋收8个月（仅用于展示）
        annual_consumption = 12 * monthly_consumption
        per_capita_surplus = (
            (county["peasant_grain_reserve"] - annual_consumption)
            / max(total_pop, 1)
        )
        monthly_pcs = per_capita_surplus / 12
        demand_factor = max(0.1, min(2.0, 1 + monthly_pcs / 20))

        for market in county["markets"]:
            market["gmv"] = round(
                market["merchants"] * county["commercial"] * demand_factor, 1)

        county["peasant_surplus"] = {
            "reserve": round(county["peasant_grain_reserve"]),
            "months_to_harvest": months_to_harvest,
            "per_capita_surplus": round(per_capita_surplus, 1),
            "monthly_per_capita_surplus": round(monthly_pcs, 1),
            "demand_factor": round(demand_factor, 2),
            "monthly_consumption": round(monthly_consumption),
        }

        return county


def _compute_initial_peasant_production(county):
    """计算初始年度农民粮食产出（斤），用于初始化粮食储备"""
    env = county.get("environment", {})
    ag_suit = env.get("agriculture_suitability", 0.7)
    irrigation_mult = 1 + county.get("irrigation_level", 0) * 0.15
    tax_rate = county.get("tax_rate", 0.12)

    total = 0
    for v in county["villages"]:
        peasant_land = v["farmland"] * (1 - v.get("gentry_land_pct", 0.3))
        production = peasant_land * MAX_YIELD_PER_MU * ag_suit * irrigation_mult * (1 - tax_rate)
        total += production
    return total
