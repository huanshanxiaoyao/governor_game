"""县域指标系统：民心、治安、商业、粮食生产"""

from .constants import (
    MAX_YIELD_PER_MU,
    ANNUAL_CONSUMPTION,
    IRRIGATION_DAMAGE_REDUCTION,
    COMMERCIAL_TAX_RETENTION,
    CC_SENSITIVITY,
    CORVEE_PER_CAPITA,
    GRAIN_PER_LIANG,
    ROAD_COMMERCE_BONUS_PER_LEVEL,
    month_of_year,
)
from .ledger import ensure_county_ledgers, refresh_village_grain_ledgers


class MetricsMixin:
    """民心、治安、商业、粮食生产等月度指标更新"""

    @staticmethod
    def _clamp_public_stat(value):
        return max(0.0, min(100.0, float(value)))

    @staticmethod
    def _months_to_harvest(month, pre_harvest=False):
        """Distance to next autumn harvest from the current state."""
        moy = month_of_year(month)
        if pre_harvest and moy == 9:
            return 1
        return (9 - moy) % 12 or 12

    @classmethod
    def _estimate_consumption_profile(
        cls,
        total_pop,
        reserve,
        months_to_harvest,
        halve_consumption=False,
        monthly_loan_repayment=0.0,
    ):
        """Estimate this month's peasant grain consumption profile.

        消费信心指数 consumer_confidence = per_capita_surplus / months_to_harvest
        （斤/人/月，超出维生水平的月均余粮）。
        surplus 计算扣除未来各月的借粮还款，使信心指数正确反映实际可支配余粮。
        紧急状态（仓空或限粮令）直接压至地板 -CC_SENSITIVITY/2，使两项乘数均降至0.5。
        正常状态下统一公式：multiplier = clamp(1 + cc / CC_SENSITIVITY, 0.5, 2.0)
        """
        base_monthly_consumption = max(0.0, total_pop * ANNUAL_CONSUMPTION / 12)
        if total_pop <= 0:
            return {
                "baseline_monthly_consumption": 0.0,
                "monthly_consumption": 0.0,
                "consumption_multiplier": 0.0,
                "consumer_confidence": 0.0,
            }

        # 月度总流出 = 基础消耗 + 邻县借粮还款
        # reserve 已由 prepare_month 扣除本月还款，故还款仅计入剩余 (N-1) 个月
        total_monthly_outflow = base_monthly_consumption + float(monthly_loan_repayment)
        pre_per_capita_surplus = (
            (reserve
             - months_to_harvest * base_monthly_consumption
             - max(0, months_to_harvest - 1) * float(monthly_loan_repayment))
            / max(total_pop, 1)
        )

        # 紧急状态：仓空或限粮令，直接压到地板（multiplier → 0.5）
        if reserve < 0 or halve_consumption:
            consumer_confidence = -CC_SENSITIVITY / 2.0
        else:
            consumer_confidence = pre_per_capita_surplus / max(months_to_harvest, 1)

        consumption_multiplier = max(0.5, min(2.0, 1.0 + consumer_confidence / CC_SENSITIVITY))
        monthly_consumption = base_monthly_consumption * consumption_multiplier

        return {
            "baseline_monthly_consumption": base_monthly_consumption,
            "monthly_consumption": monthly_consumption,
            "consumption_multiplier": consumption_multiplier,
            "consumer_confidence": consumer_confidence,
        }

    @classmethod
    def refresh_peasant_surplus_snapshot(
        cls,
        county,
        month,
        *,
        monthly_consumption=None,
        consumption_multiplier=None,
        pre_harvest=False,
    ):
        """Sync peasant surplus display fields to the current reserve."""
        ensure_county_ledgers(county)
        total_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county.get("villages", [])
        )
        months_to_harvest = cls._months_to_harvest(month, pre_harvest=pre_harvest)
        reserve = float(county.get("peasant_grain_reserve", 0.0))

        emergency = county.get("emergency") or {}
        halve = bool(emergency.get("halve_consumption_this_month"))

        # 活跃借粮每月还款总额（纳入余粮预期计算）
        active_loans = emergency.get("neighbor_loans") or []
        monthly_loan_repayment = sum(
            float(l.get("installment_grain", 0.0))
            for l in active_loans
            if l.get("status") == "ACTIVE"
        )

        profile = cls._estimate_consumption_profile(
            total_pop,
            reserve,
            months_to_harvest,
            halve_consumption=halve,
            monthly_loan_repayment=monthly_loan_repayment,
        )
        base_monthly_consumption = profile["baseline_monthly_consumption"]
        if monthly_consumption is None:
            monthly_consumption = profile["monthly_consumption"]
        if consumption_multiplier is None:
            consumption_multiplier = profile["consumption_multiplier"]

        # 扣除消耗和还款后的余粮（反映到秋收实际剩余）
        total_monthly_outflow = base_monthly_consumption + monthly_loan_repayment
        surplus_total = reserve - months_to_harvest * total_monthly_outflow
        per_capita_surplus = surplus_total / max(total_pop, 1)

        # 消费信心指数（展示用，基于快照时刻余粮，已含还款压力）
        if reserve < 0 or halve:
            consumer_confidence = -CC_SENSITIVITY / 2.0
        else:
            consumer_confidence = per_capita_surplus / max(months_to_harvest, 1)
        confidence_index = max(0.5, min(2.0, 1.0 + consumer_confidence / CC_SENSITIVITY))

        county["peasant_surplus"] = {
            "reserve": round(reserve),
            "months_to_harvest": months_to_harvest,
            "per_capita_surplus": round(per_capita_surplus, 1),
            "consumer_confidence": round(consumer_confidence, 1),
            "confidence_index": round(confidence_index, 2),
            "monthly_consumption": round(monthly_consumption),
            "baseline_monthly_consumption": round(base_monthly_consumption),
            "consumption_multiplier": round(consumption_multiplier, 2),
            "monthly_loan_repayment": round(monthly_loan_repayment, 1),
        }
        return county["peasant_surplus"]

    @classmethod
    def apply_county_stat_delta(cls, county, field, delta, *, village_delta=None):
        """Apply a county public-stat delta and keep village values moving in the same direction."""
        old = float(county.get(field, 50.0))
        county[field] = cls._clamp_public_stat(old + float(delta))
        actual_county_delta = county[field] - old

        if village_delta is None:
            village_delta = actual_county_delta

        if village_delta:
            for village in county.get("villages", []):
                village[field] = cls._clamp_public_stat(
                    float(village.get(field, 50.0)) + float(village_delta)
                )

        return round(float(county.get(field, 50.0)) - old, 1)

    @classmethod
    def refresh_metric_report_lines(cls, county, report):
        """Rewrite metric summary lines against final county values after all late-stage effects."""
        metric_bases = report.pop("_metric_bases", None) or {}
        if not metric_bases:
            return

        labels = {
            "morale": "民心变化",
            "security": "治安变化",
        }
        events = report.get("events", [])

        for field, label in labels.items():
            if field not in metric_bases:
                continue
            current = float(county.get(field, 50.0))
            actual_change = round(current - float(metric_bases[field]), 1)
            line = (
                f"{label}: {'+' if actual_change > 0 else ''}"
                f"{actual_change:.1f} (当前: {current:.1f})"
            )
            replaced = False
            for idx, event in enumerate(events):
                if isinstance(event, str) and event.startswith(f"{label}:"):
                    events[idx] = line
                    replaced = True
                    break
            if not replaced:
                events.append(line)

    @classmethod
    def _update_morale(cls, county, report):
        """Calculate morale change per doc 06 §4.5, with county↔village sync.
        Monthly tick — deltas scaled to ~1/3 of old seasonal values.
        """
        old = county["morale"]
        report.setdefault("_metric_bases", {})["morale"] = float(old)

        # Base decay: -0.33/month (was -1/season, same -4/year)
        delta = -0.33

        # Education contribution: education/60 per month (was /20 per season)
        delta += county["education"] / 60

        # Security linkage (monthly): high security boosts morale, low security erodes it
        if county["security"] > 60:
            delta += 0.5
        elif county["security"] < 30:
            delta -= 0.5

        # Heavy tax penalty: -1/month at maximum rate (was -3/season)
        if county["tax_rate"] >= 0.15:
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
        report.setdefault("_metric_bases", {})["security"] = float(old)

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
        ensure_county_ledgers(county)
        villages = county["villages"]
        total_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in villages
        )
        if total_pop <= 0:
            return
        weighted_sum = sum(
            v.get(field, 50)
            * v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in villages
        )
        weighted_avg = weighted_sum / total_pop
        county[field] = max(0, min(100,
            round(0.7 * weighted_avg + 0.3 * county[field], 1)))

    @classmethod
    def _compute_peasant_production(cls, county, include_disaster=False):
        """年度农民粮食产出（斤），扣税后。用于粮食储备计算。"""
        ensure_county_ledgers(county)
        env = county.get("environment", {})
        ag_suit = env.get("agriculture_suitability", 0.7)
        irrigation_mult = 1 + county.get("irrigation_level", 0) * 0.15
        tax_rate = county.get("tax_rate", 0.12)

        total = 0
        for v in county["villages"]:
            peasant_land = v.get("peasant_ledger", {}).get("farmland", 0)
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
    def _refresh_village_ledger_metrics(cls, county, monthly_consumption, month=None):
        """Refresh village-level grain ledgers from current county state."""
        refresh_village_grain_ledgers(
            county,
            monthly_consumption=monthly_consumption,
            current_season=month,
        )

    @classmethod
    def _update_commercial(cls, county, month, report, prefecture_ctx=None):
        """月度商业更新：粮食消耗→扣后余粮→消费信心指数→GMV→商税
        prefecture_ctx: optional dict with road_level for inter-county commerce bonus.
        消费信心基于扣除本月消耗后的余粮，确保展示与计算口径一致。
        """
        ensure_county_ledgers(county)
        total_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county["villages"]
        )
        months_to_harvest = cls._months_to_harvest(month)

        reserve_before = county.get("peasant_grain_reserve", 0)
        emergency = county.get("emergency") or {}
        halve = bool(emergency.get("halve_consumption_this_month"))

        # 活跃借粮每月还款（本月已在 prepare_month 扣除，此处用于预期余粮计算）
        active_loans = emergency.get("neighbor_loans") or []
        monthly_loan_repayment = sum(
            float(l.get("installment_grain", 0.0))
            for l in active_loans
            if l.get("status") == "ACTIVE"
        )

        consumption_profile = cls._estimate_consumption_profile(
            total_pop,
            reserve_before,
            months_to_harvest,
            halve_consumption=halve,
            monthly_loan_repayment=monthly_loan_repayment,
        )
        base_monthly_consumption = consumption_profile["baseline_monthly_consumption"]
        monthly_consumption = consumption_profile["monthly_consumption"]
        consumption_multiplier = consumption_profile["consumption_multiplier"]

        # 1. 先扣粮食消耗
        county["peasant_grain_reserve"] = reserve_before - monthly_consumption

        # 2. 基于扣后余粮计算消费信心指数（含未来还款压力）
        post_reserve = county["peasant_grain_reserve"]
        total_monthly_outflow = base_monthly_consumption + monthly_loan_repayment
        # post_reserve 已扣本月消耗，loan 已在 prepare_month 扣除，故剩余 (N-1) 个月
        post_surplus_total = post_reserve - max(0, months_to_harvest - 1) * total_monthly_outflow
        post_per_capita_surplus = post_surplus_total / max(total_pop, 1)
        if post_reserve < 0 or halve:
            post_cc = -CC_SENSITIVITY / 2.0
        else:
            post_cc = post_per_capita_surplus / max(months_to_harvest, 1)
        demand_factor = max(0.5, min(2.0, 1.0 + post_cc / CC_SENSITIVITY))
        cls.refresh_peasant_surplus_snapshot(
            county,
            month,
            monthly_consumption=monthly_consumption,
            consumption_multiplier=consumption_multiplier,
        )

        # 3. 即时计算各集市 GMV（跨县驿道提升贸易量）
        road_mult = 1.0 + (prefecture_ctx or {}).get("road_level", 0) * ROAD_COMMERCE_BONUS_PER_LEVEL
        for market in county["markets"]:
            market["gmv"] = round(
                market["merchants"] * county["commercial"] * demand_factor * road_mult, 1)

        # 4. 月度商业税征收（地方固定留存60%，独立于 remit_ratio）
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

        # 5. 扣后余粮已通过 snapshot helper 同步到 peasant_surplus
        cls._refresh_village_ledger_metrics(county, monthly_consumption, month=month)

        if total_gmv >= 1:
            report["events"].append(
                f"集市月贸易额: {total_gmv:.0f}两 "
                f"(消费信心: {demand_factor:.2f}, 月均余粮: {post_cc:.1f}斤/人)")

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
            "agri_tax": 0,
            "agri_remitted": 0,
        }
        report["events"].append("新年伊始，财政年度重置")

    @classmethod
    def _collect_corvee(cls, county, report):
        """年度徭役征收（五月全额）"""
        ensure_county_ledgers(county)
        # 徭役折银仅基于在册村民（地主账本人口不纳入应役人口）
        liable_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county["villages"]
        )
        corvee_total = liable_pop * CORVEE_PER_CAPITA

        remit_ratio = county.get("remit_ratio", 0.65)
        retained = corvee_total * (1 - remit_ratio)
        county["treasury"] += retained

        # 累计到 fiscal_year
        fy = county.get("fiscal_year", {})
        fy["corvee_tax"] = fy.get("corvee_tax", 0) + corvee_total
        fy["corvee_retained"] = fy.get("corvee_retained", 0) + retained
        county["fiscal_year"] = fy

        # 村民无非粮食收入，徭役折银须卖粮换银缴纳，从粮食储备中扣减等值粮食
        grain_deduction = corvee_total * GRAIN_PER_LIANG
        county["peasant_grain_reserve"] = county.get("peasant_grain_reserve", 0) - grain_deduction

        report["events"].append(
            f"征收徭役折银: {corvee_total:.1f}两（年度，五月），"
            f"留存{retained:.1f}两，"
            f"村民售粮{round(grain_deduction)}斤折银缴纳")
