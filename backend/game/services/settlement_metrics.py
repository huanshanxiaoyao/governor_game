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
from .clan import get_county_security_delta


class MetricsMixin:
    """民心、治安、商业、粮食生产等月度指标更新"""

    @staticmethod
    def _clamp_public_stat(value):
        return max(0.0, min(100.0, float(value)))

    @staticmethod
    def _zone_multiplier(value):
        """分区衰减乘数：低分区减缓，高分区加速。
        困境区(0-35): ×0.4 | 正常区(35-65): ×1.0 | 高分区(65-100): ×1.5
        """
        if value >= 65:
            return 1.5
        if value >= 35:
            return 1.0
        return 0.4

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
            "monthly_per_capita_surplus": round(per_capita_surplus / max(months_to_harvest, 1), 1),
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
        """Apply a stat delta to all villages, then aggregate to county (Model A).
        village_delta: if provided, villages get this value; otherwise they get delta.
        """
        old = float(county.get(field, 50.0))
        effective_delta = float(village_delta) if village_delta is not None else float(delta)

        for village in county.get("villages", []):
            village[field] = cls._clamp_public_stat(
                float(village.get(field, 50.0)) + effective_delta
            )
        cls._sync_county_from_villages(county, field)

        return round(float(county.get(field, 50.0)) - old, 1)

    @classmethod
    def refresh_metric_report_lines(cls, county, report):
        """Rewrite metric summary lines against final county values after all late-stage effects.

        Also stores metric_deltas in report for downstream consumers (e.g. rumor generation).
        """
        metric_bases = report.pop("_metric_bases", None) or {}
        if not metric_bases:
            return

        # 存储各指标本月变化量，供流言板等下游使用
        metric_deltas = {}
        for field in ("morale", "security", "commercial", "education"):
            if field in metric_bases:
                metric_deltas[field] = round(
                    float(county.get(field, 50.0)) - float(metric_bases[field]), 1)
        report["metric_deltas"] = metric_deltas

        labels = {
            "morale": "民心变化",
            "security": "治安变化",
        }
        events = report.get("events", [])

        for field, label in labels.items():
            if field not in metric_bases:
                continue
            actual_change = metric_deltas.get(field, 0)
            current = float(county.get(field, 50.0))
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
        """月度民心结算（Model A：直接更新各村，县级为聚合值）。"""
        old_county = float(county.get("morale", 50.0))
        report.setdefault("_metric_bases", {})["morale"] = old_county

        # 基础衰减：-1.0/月（分区乘数调整）
        delta = -1.0 * cls._zone_multiplier(old_county)

        # 文教贡献：文教>40时加成，连续渐变 (edu-40)/60（文教100→+1.0）
        if county["education"] > 40:
            delta += (county["education"] - 40) / 60

        # 治安联动：连续渐变 (security-50)/40（治安75→+0.625，治安30→-0.5）
        delta += (county["security"] - 50) / 40

        # 重税惩罚
        if county["tax_rate"] >= 0.15:
            delta -= 1

        # 保甲 L2 连坐：平时监视有怨 -0.25/月；灾年共御外患反升 +0.15/月
        if county.get("baojia_level", 0) >= 2:
            if county.get("disaster_this_year"):
                delta += 0.15
            else:
                delta -= 0.25

        # 直接更新各村，县级聚合
        for v in county["villages"]:
            v["morale"] = max(0.0, min(100.0, float(v.get("morale", 50.0)) + delta))
        cls._sync_county_from_villages(county, "morale")

        actual_change = county["morale"] - old_county
        if actual_change != 0:
            report["events"].append(
                f"民心变化: {'+' if actual_change > 0 else ''}"
                f"{actual_change:.1f} (当前: {county['morale']:.1f})")

    @classmethod
    def _update_security(cls, county, report):
        """月度治安结算（Model A：直接更新各村，县级为聚合值）。"""
        old_county = float(county.get("security", 50.0))
        report.setdefault("_metric_bases", {})["security"] = old_county

        # 基础衰减：-1.2/月（分区乘数调整）
        delta = -1.2 * cls._zone_multiplier(old_county)

        # 衙役加成（不受分区乘数影响）
        delta += county["bailiff_level"] * 0.67

        # 保甲加成：L1 +0.35/月，L2 +0.80/月
        baojia_security = (0.0, 0.35, 0.80)[min(county.get("baojia_level", 0), 2)]
        delta += baojia_security

        # 民心联动（使用本月民心结算后的县级聚合值）
        if county["morale"] > 60:
            delta += 0.33
        elif county["morale"] < 30:
            delta -= 0.67

        # 宗族关系影响治安（按实力加权，各宗族 power 越大影响越显著）
        # 衙役等级越高，宗族负向拉扯越弱；满级衙役应能实质压制乡间失序。
        # 保甲 L2 连坐制度：负向宗族扰动再减半（邻里互保抑制聚众闹事）。
        clan_delta = get_county_security_delta(county)
        if clan_delta != 0.0:
            if clan_delta < 0:
                bailiff_mitigation = max(0.25, 1.0 - county.get("bailiff_level", 0) * 0.25)
                if county.get("baojia_level", 0) >= 2:
                    bailiff_mitigation *= 0.5
                clan_delta = round(clan_delta * bailiff_mitigation, 2)
            delta += clan_delta

        # 直接更新各村，县级聚合
        for v in county["villages"]:
            v["security"] = max(0.0, min(100.0, float(v.get("security", 50.0)) + delta))
        cls._sync_county_from_villages(county, "security")

        actual_change = county["security"] - old_county
        if actual_change != 0:
            report["events"].append(
                f"治安变化: {'+' if actual_change > 0 else ''}"
                f"{actual_change:.1f} (当前: {county['security']:.1f})"
                + (f"（含宗族修正{clan_delta:+.1f}）" if clan_delta != 0.0 else "")
            )

    @classmethod
    def _update_education(cls, county, report):
        """月度文教结算：自然衰减 + 学校等级减免 + 村塾减免 + 分区乘数。
        文教为县级独立指标（无村级分布），直接更新 county["education"]。
        零衰减需 L3县学+3村塾 或 L2县学+6村塾。
        """
        old_edu = float(county.get("education", 0.0))
        report.setdefault("_metric_bases", {})["education"] = old_edu

        # 基础衰减：-0.6/月；县学每级减免0.15，每个村塾减免0.05
        school_level = county.get("school_level", 0)
        village_school_count = sum(
            1 for v in county.get("villages", []) if v.get("has_school")
        )
        school_reduction = school_level * 0.15 + village_school_count * 0.05
        net_decay = max(0.0, 0.6 - school_reduction) * cls._zone_multiplier(old_edu)

        county["education"] = round(max(0.0, min(100.0, old_edu - net_decay)), 1)

        actual_change = round(county["education"] - old_edu, 1)
        if actual_change != 0:
            report["events"].append(
                f"文教变化: {'+' if actual_change > 0 else ''}"
                f"{actual_change:.1f} (当前: {county['education']:.1f})")

    @staticmethod
    def _sync_county_from_villages(county, field):
        """县级指标 = 各村按人口加权平均（Model A：县为纯聚合，无独立轨道）"""
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
        county[field] = max(0, min(100, round(weighted_avg, 1)))

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
    def _update_commercial(cls, county, month, report, prefecture_ctx=None, game=None):
        """月度商业更新：粮食消耗→扣后余粮→消费信心指数→GMV→商税
        prefecture_ctx: optional dict with road_level for inter-county commerce bonus.
        消费信心基于扣除本月消耗后的余粮，确保展示与计算口径一致。
        """
        ensure_county_ledgers(county)
        report.setdefault("_metric_bases", {})["commercial"] = float(county.get("commercial", 0.0))
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

        # 商业繁荣→集市自发扩张
        commercial = county.get("commercial", 0)
        existing_names = {m["name"] for m in county.get("markets", [])}

        def _unique_market_name(candidates):
            for n in candidates:
                if n not in existing_names:
                    return n
            return f"集市{len(county['markets']) + 1}"

        def _log_market_created(game, market_name, desc_text):
            """将新集市事件写入 EventLog 供流言板读取（非关键，失败静默）。"""
            try:
                from .eventlog import log_game_event
                log_game_event(
                    game,
                    event_type='auto_market_created',
                    category='ECONOMY',
                    description=desc_text,
                    data={'market_name': market_name},
                )
            except Exception:
                pass

        # 条件A：集市不足2个且商业>45，补足至2个（每月最多+1，自然触发）
        if len(county["markets"]) < 2 and commercial > 45:
            name = _unique_market_name(["草市", "墟市", "小集", "新市"])
            new_market = {"name": name, "merchants": 8, "gmv": 0.0}
            county["markets"].append(new_market)
            existing_names.add(name)
            desc = f"商业回暖，草市自发形成：{name}（初始商贩{new_market['merchants']}人）"
            report["events"].append(desc)
            _log_market_created(game, name, desc)

        # 条件B/C：商业≥60/80时各额外触发一次（独立于条件A的计数）
        if commercial >= 80:
            target_auto = 2
        elif commercial >= 60:
            target_auto = 1
        else:
            target_auto = 0
        auto_market_count = county.get("auto_market_count", 0)
        if target_auto > auto_market_count:
            name = _unique_market_name(["新兴集", "通商集", "盛贸集"])
            new_market = {"name": name, "merchants": 10, "gmv": 0.0}
            county["markets"].append(new_market)
            existing_names.add(name)
            county["auto_market_count"] = auto_market_count + 1
            desc = f"商业繁荣，新集市自发形成：{name}（初始商贩{new_market['merchants']}人）"
            report["events"].append(desc)
            _log_market_created(game, name, desc)

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
            f"征收徭役折银: {corvee_total:.1f}两（在册应役人口{liable_pop}人 × 人均{CORVEE_PER_CAPITA}两），"
            f"留存{retained:.1f}两，"
            f"村民售粮{round(grain_deduction)}斤折银缴纳")
