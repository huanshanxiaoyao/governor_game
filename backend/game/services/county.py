"""县域初始化服务"""

import random

from .constants import COUNTY_TYPES, VILLAGE_NAMES, MARKET_NAMES


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
            "irrigation_level": 0,
            "has_granary": False,
            "bailiff_level": 0,
            "admin_cost": base["admin_cost"],
            "medical_level": 0,
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
                "trade_index": round(_fluctuate_clamp(
                    base["commercial"], 5, 100, pct=0.15)),
            })
        county["markets"] = markets

        # 用 ceiling 模型重算人口（60% of carrying capacity）
        from .settlement import SettlementService
        for v in county["villages"]:
            ceiling = SettlementService._calculate_village_ceiling(v, county)
            v["population"] = int(ceiling * 0.60)
            v["ceiling"] = ceiling

        return county
