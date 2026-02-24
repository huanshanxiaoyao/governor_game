"""县域指标系统：民心、治安、商业、粮食生产"""

from .constants import (
    MAX_YIELD_PER_MU,
    ANNUAL_CONSUMPTION,
    IRRIGATION_DAMAGE_REDUCTION,
    COMMERCIAL_TAX_RETENTION,
    EXCESS_CONSUMPTION_THRESHOLD,
    CORVEE_PER_CAPITA,
    GENTRY_POP_RATIO_COEFF,
    month_of_year,
)


class MetricsMixin:
    """民心、治安、商业、粮食生产等月度指标更新"""

    @classmethod
    def _update_morale(cls, county, report):
        """Calculate morale change per doc 06 §4.5, with county↔village sync.
        Monthly tick — deltas scaled to ~1/3 of old seasonal values.
        """
        old = county["morale"]

        # Base decay: -0.33/month (was -1/season, same -4/year)
        delta = -0.33

        # Education contribution: education/60 per month (was /20 per season)
        delta += county["education"] / 60

        # Security linkage (monthly): high security boosts morale, low security erodes it
        if county["security"] > 60:
            delta += 0.5
        elif county["security"] < 30:
            delta -= 0.5

        # Heavy tax penalty: -1/month (was -3/season)
        if county["tax_rate"] > 0.15:
            delta -= 1

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
        """Calculate security change per doc 06 §4.5, with county↔village sync.
        Monthly tick — deltas scaled to ~1/3 of old seasonal values.
        """
        old = county["security"]

        # Base decay: -0.33/month (was -1/season)
        delta = -0.33

        # Bailiff bonus: level*0.67/month (was level*2/season)
        delta += county["bailiff_level"] * 0.67

        # Morale linkage: +0.33/-0.67 per month (was +1/-2 per season)
        if county["morale"] > 60:
            delta += 0.33
        elif county["morale"] < 30:
            delta -= 0.67

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
    def _compute_peasant_production(cls, county, include_disaster=False):
        """年度农民粮食产出（斤），扣税后。用于粮食储备计算。"""
        env = county.get("environment", {})
        ag_suit = env.get("agriculture_suitability", 0.7)
        irrigation_mult = 1 + county.get("irrigation_level", 0) * 0.15
        tax_rate = county.get("tax_rate", 0.12)

        total = 0
        for v in county["villages"]:
            peasant_land = v["farmland"] * (1 - v.get("gentry_land_pct", 0.3))
            production = peasant_land * MAX_YIELD_PER_MU * ag_suit * irrigation_mult * (1 - tax_rate)
            total += production

        if include_disaster:
            disaster = county.get("disaster_this_year")
            if disaster and disaster["type"] != "plague":
                damage = disaster["severity"]
                # 水利减损（仅洪灾和旱灾）
                if disaster["type"] in ("flood", "drought"):
                    irr_level = county.get("irrigation_level", 0)
                    damage *= (1 - IRRIGATION_DAMAGE_REDUCTION[min(irr_level, 3)])
                total *= (1 - damage)

        return total

    @classmethod
    def _update_commercial(cls, county, month, report):
        """月度商业更新：盈余→需求系数→GMV→商税→粮食消耗"""
        total_pop = sum(v["population"] for v in county["villages"])
        base_monthly_consumption = total_pop * ANNUAL_CONSUMPTION / 12

        # 年化人均月余粮（固定12月视野，单次计算）
        annual_consumption = 12 * base_monthly_consumption
        per_capita_surplus = (
            (county.get("peasant_grain_reserve", 0) - annual_consumption)
            / max(total_pop, 1)
        )
        monthly_pcs = per_capita_surplus / 12

        # 需求系数：clamp(1 + 月均余粮/20, 0.1, 2.0)
        demand_factor = max(0.1, min(2.0, 1 + monthly_pcs / 20))

        # 过度消费机制：当月均余粮 > 阈值时，消耗按二次方增加
        monthly_consumption = base_monthly_consumption
        if monthly_pcs > EXCESS_CONSUMPTION_THRESHOLD:
            ratio = monthly_pcs / EXCESS_CONSUMPTION_THRESHOLD
            excess_mult = 1 + ratio * ratio * 0.1
            monthly_consumption = base_monthly_consumption * excess_mult

        # 扣除粮食消耗
        county["peasant_grain_reserve"] = county.get("peasant_grain_reserve", 0) - monthly_consumption

        # 4. 即时计算各集市 GMV
        for market in county["markets"]:
            market["gmv"] = round(
                market["merchants"] * county["commercial"] * demand_factor, 1)

        # 5. 月度商业税征收（地方固定留存60%，独立于 remit_ratio）
        commercial_tax_rate = county.get("commercial_tax_rate", 0.03)
        total_gmv = sum(m["gmv"] for m in county["markets"])
        monthly_commercial_tax = total_gmv * commercial_tax_rate

        commercial_retained = monthly_commercial_tax * COMMERCIAL_TAX_RETENTION
        county["treasury"] += commercial_retained

        # 累计到 fiscal_year
        fy = county.get("fiscal_year", {})
        fy["commercial_tax"] = fy.get("commercial_tax", 0) + monthly_commercial_tax
        fy["commercial_retained"] = fy.get("commercial_retained", 0) + commercial_retained
        county["fiscal_year"] = fy

        # 6. 存储盈余信息供前端展示
        moy = month_of_year(month)
        months_to_harvest = (9 - moy) % 12 or 12
        county["peasant_surplus"] = {
            "reserve": round(county["peasant_grain_reserve"]),
            "months_to_harvest": months_to_harvest,
            "per_capita_surplus": round(per_capita_surplus, 1),
            "monthly_per_capita_surplus": round(monthly_pcs, 1),
            "demand_factor": round(demand_factor, 2),
            "monthly_consumption": round(monthly_consumption),
        }

        if total_gmv >= 1:
            report["events"].append(
                f"集市月贸易额: {total_gmv:.0f}两 "
                f"(需求系数: {demand_factor:.2f}, 月均余粮: {monthly_pcs:.1f}斤)")

        if monthly_commercial_tax >= 0.5:
            report["events"].append(
                f"月度商税: {monthly_commercial_tax:.1f}两 "
                f"(税率{commercial_tax_rate:.1%}), "
                f"留存{commercial_retained:.1f}两")

    @staticmethod
    def _reset_fiscal_year(county, report):
        """正月：重置年度财政累计"""
        county["fiscal_year"] = {
            "commercial_tax": 0,
            "commercial_retained": 0,
            "corvee_tax": 0,
            "corvee_retained": 0,
        }
        report["events"].append("新年伊始，财政年度重置")

    @classmethod
    def _collect_corvee(cls, county, report):
        """半年度徭役征收（正月、五月各一半）"""
        total_pop = sum(v["population"] for v in county["villages"])
        gentry_ratio = county.get("gentry_land_ratio", 0.35)
        gentry_pop_ratio = gentry_ratio * GENTRY_POP_RATIO_COEFF
        liable_pop = total_pop * (1 - gentry_pop_ratio)
        half_corvee = liable_pop * CORVEE_PER_CAPITA / 2

        remit_ratio = county.get("remit_ratio", 0.65)
        retained = half_corvee * (1 - remit_ratio)
        county["treasury"] += retained

        # 累计到 fiscal_year
        fy = county.get("fiscal_year", {})
        fy["corvee_tax"] = fy.get("corvee_tax", 0) + half_corvee
        fy["corvee_retained"] = fy.get("corvee_retained", 0) + retained
        county["fiscal_year"] = fy

        report["events"].append(
            f"征收徭役折银: {half_corvee:.1f}两（半年度），"
            f"留存{retained:.1f}两")
