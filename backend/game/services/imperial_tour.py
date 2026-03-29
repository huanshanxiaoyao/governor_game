"""皇帝巡游费用摊派（财赋核心型县专属）

触发条件（每年三月）：
  - county_type == "fiscal_core"
  - agriculture_suitability > 0.65
  - monthly_per_capita_surplus > 5

摊派金额 = annual_quota["corvee"] × 50%

玩家决策：
  - 缴纳比例：80% / 90% / 100%
  - 摊派方式：
    · 按人头（民心-10）：按全县在册人口比例从各村农民/地主粮食账本扣粮
    · 按土地（民心-6，各村地主好感度-5）：按全县在册土地比例从各村农民/地主粮食账本扣粮

两种方式均不从县库扣银，负担直接落在黎庶与士绅；grain_surplus 允许为负。
换算：1两 = GRAIN_PER_LIANG 斤。县级汇总字段 peasant_grain_reserve 同步扣减。
"""

import logging

from ..models import Agent
from .constants import month_of_year, year_of, GRAIN_PER_LIANG

logger = logging.getLogger('game')

_MAX_MEMORY = 8

# 触发条件阈值
_AG_SUIT_THRESHOLD = 0.65
_MONTHLY_SURPLUS_THRESHOLD = 5.0
_LEVY_RATIO = 0.50

# 摊派方式 → 效果
_APPORTIONMENT_EFFECTS = {
    "per_capita": {"morale_delta": -10, "gentry_affinity_delta": 0},
    "per_land":   {"morale_delta": -6,  "gentry_affinity_delta": -5},
}

_PAYMENT_LABELS = {1.0: "全额（100%）", 0.9: "九成（90%）", 0.8: "八成（80%）"}
_METHOD_LABELS  = {"per_capita": "按人头分摊", "per_land": "按土地分摊"}


class ImperialTourService:
    """皇帝巡游摊派：触发检测 + 玩家决策处理"""

    # ------------------------------------------------------------------ #
    # 1. 触发检测（三月结算时调用）                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def check_and_trigger(cls, county: dict, month: int, report: dict, game=None) -> bool:
        """检查是否满足触发条件；满足时写入 county["imperial_tour_pending"]，返回 True。"""
        if county.get("county_type") != "fiscal_core":
            return False

        moy = month_of_year(month)
        if moy != 3:
            return False

        # 同一年已有未处理摊派，不重复触发
        if county.get("imperial_tour_pending"):
            return False

        env = county.get("environment", {})
        ag_suit = float(env.get("agriculture_suitability", 0))
        if ag_suit <= _AG_SUIT_THRESHOLD:
            return False

        surplus = county.get("peasant_surplus", {})
        monthly_surplus = float(surplus.get("monthly_per_capita_surplus", 0))
        if monthly_surplus <= _MONTHLY_SURPLUS_THRESHOLD:
            return False

        # 计算摊派金额
        corvee_quota = float((county.get("annual_quota") or {}).get("corvee", 0))
        levy_amount = round(corvee_quota * _LEVY_RATIO, 1)

        county["imperial_tour_pending"] = {
            "year": year_of(month),
            "month": month,
            "levy_amount": levy_amount,
            "corvee_quota": corvee_quota,
        }

        report["events"].append(
            f"【皇帝巡游摊派】朝廷令各县分摊圣驾巡游费用，"
            f"本县应缴{levy_amount}两（年度徭役折银{corvee_quota}两的五成）。"
            f"请知县决定缴纳比例与摊派方式后方可推进。"
        )
        return True

    # ------------------------------------------------------------------ #
    # 2. 阻塞检查（AdvanceSeasonView 调用）                               #
    # ------------------------------------------------------------------ #

    @classmethod
    def get_advance_blocker(cls, county: dict):
        """有未处理摊派时返回阻塞提示字符串，否则返回 None。"""
        pending = county.get("imperial_tour_pending")
        if pending:
            return (
                f"皇帝巡游摊派（{pending['levy_amount']}两）尚未处理，"
                f"请在县情总览中决定缴纳方式后方可推进"
            )
        return None

    # ------------------------------------------------------------------ #
    # 3. 决策处理                                                          #
    # ------------------------------------------------------------------ #

    @classmethod
    def resolve(cls, game, county: dict, payment_ratio: float, apportionment_method: str) -> dict:
        """处理玩家的摊派决策，写入效果并清除 pending。

        payment_ratio: 0.8 / 0.9 / 1.0
        apportionment_method: "per_capita" | "per_land"
        返回 result dict（含 success / error）。
        """
        from .state import save_player_state
        from .settlement_metrics import MetricsMixin

        pending = county.get("imperial_tour_pending")
        if not pending:
            return {"error": "当前没有待处理的皇帝巡游摊派"}

        # 参数校验
        valid_ratios = {0.8, 0.9, 1.0}
        if payment_ratio not in valid_ratios:
            return {"error": "缴纳比例无效，须为 0.8、0.9 或 1.0"}
        if apportionment_method not in _APPORTIONMENT_EFFECTS:
            return {"error": "摊派方式无效，须为 per_capita 或 per_land"}

        levy_amount = float(pending["levy_amount"])
        actual_payment = round(levy_amount * payment_ratio, 1)

        # 按人头或按土地从粮食账本扣粮（不扣县库）
        if apportionment_method == "per_capita":
            grain_info = cls._deduct_grain_per_capita(county, actual_payment)
        else:
            grain_info = cls._deduct_grain_per_land(county, actual_payment)

        # 民心变动（作用于各村 → 县级聚合）
        effects = _APPORTIONMENT_EFFECTS[apportionment_method]
        morale_delta = effects["morale_delta"]
        MetricsMixin.apply_county_stat_delta(county, "morale", morale_delta)

        # 地主好感度（按土地分摊时对所有村地主 NPC 各 -5）
        gentry_affinity_delta = effects["gentry_affinity_delta"]
        if gentry_affinity_delta != 0 and game is not None:
            cls._apply_gentry_affinity(game, gentry_affinity_delta)

        # 记录结果（供年度考核读取）
        year = pending["year"]
        record = {
            "year": year,
            "levy_amount": levy_amount,
            "actual_payment": actual_payment,
            "payment_ratio": payment_ratio,
            "apportionment_method": apportionment_method,
            "peasant_grain_total": grain_info["peasant_grain_total"],
            "gentry_grain_total": grain_info["gentry_grain_total"],
        }
        if not county.get("imperial_tour_record"):
            county["imperial_tour_record"] = {}
        county["imperial_tour_record"][str(year)] = record

        # 清除 pending
        county.pop("imperial_tour_pending", None)

        # 写知府记忆
        if game is not None:
            cls._write_prefect_memory(game, record)

        save_player_state(game, county)

        method_label = _METHOD_LABELS[apportionment_method]
        payment_label = _PAYMENT_LABELS[payment_ratio]
        p_grain = grain_info["peasant_grain_total"]
        g_grain = grain_info["gentry_grain_total"]
        return {
            "success": True,
            "actual_payment": actual_payment,
            "morale_delta": morale_delta,
            "gentry_affinity_delta": gentry_affinity_delta,
            "peasant_grain_total": p_grain,
            "gentry_grain_total": g_grain,
            "message": (
                f"皇帝巡游摊派处理完成：缴纳{actual_payment}两（{payment_label}），"
                f"{method_label}，农民扣粮{p_grain}斤、地主扣粮{g_grain}斤，"
                f"民心{morale_delta:+d}"
            ),
        }

    # ------------------------------------------------------------------ #
    # 内部辅助                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _deduct_grain_per_capita(county: dict, actual_payment: float) -> dict:
        """按在册人口比例从各村农民/地主粮食账本扣粮，同步更新 peasant_grain_reserve。

        返回 {"peasant_grain_total": float, "gentry_grain_total": float}。
        兜底（人口为零）：回退扣县库。
        """
        villages = county.get("villages", [])
        total_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", 0) +
            v.get("gentry_ledger", {}).get("registered_population", 0)
            for v in villages
        )
        if total_pop <= 0:
            county["treasury"] = round(float(county.get("treasury", 0)) - actual_payment, 1)
            return {"peasant_grain_total": 0.0, "gentry_grain_total": 0.0}

        total_peasant_grain = 0.0
        total_gentry_grain = 0.0
        for v in villages:
            p_pop = v.get("peasant_ledger", {}).get("registered_population", 0)
            if p_pop > 0:
                p_grain = round(actual_payment * p_pop / total_pop * GRAIN_PER_LIANG, 1)
                pl = v["peasant_ledger"]
                pl["grain_surplus"] = round(pl.get("grain_surplus", 0.0) - p_grain, 1)
                total_peasant_grain += p_grain

            g_pop = v.get("gentry_ledger", {}).get("registered_population", 0)
            if g_pop > 0:
                g_grain = round(actual_payment * g_pop / total_pop * GRAIN_PER_LIANG, 1)
                gl = v["gentry_ledger"]
                gl["grain_surplus"] = round(gl.get("grain_surplus", 0.0) - g_grain, 1)
                total_gentry_grain += g_grain

        county["peasant_grain_reserve"] = round(
            float(county.get("peasant_grain_reserve", 0)) - total_peasant_grain, 1
        )
        return {
            "peasant_grain_total": round(total_peasant_grain, 1),
            "gentry_grain_total": round(total_gentry_grain, 1),
        }

    @staticmethod
    def _deduct_grain_per_land(county: dict, actual_payment: float) -> dict:
        """按在册土地比例从各村农民/地主粮食账本扣粮，同步更新 peasant_grain_reserve。

        农民使用 peasant_ledger.farmland，地主使用 gentry_ledger.registered_farmland。
        返回 {"peasant_grain_total": float, "gentry_grain_total": float}。
        兜底（土地为零）：回退扣县库。
        """
        villages = county.get("villages", [])
        total_land = sum(
            v.get("peasant_ledger", {}).get("farmland", 0) +
            v.get("gentry_ledger", {}).get("registered_farmland", 0)
            for v in villages
        )
        if total_land <= 0:
            county["treasury"] = round(float(county.get("treasury", 0)) - actual_payment, 1)
            return {"peasant_grain_total": 0.0, "gentry_grain_total": 0.0}

        total_peasant_grain = 0.0
        total_gentry_grain = 0.0
        for v in villages:
            p_land = v.get("peasant_ledger", {}).get("farmland", 0)
            if p_land > 0:
                p_grain = round(actual_payment * p_land / total_land * GRAIN_PER_LIANG, 1)
                pl = v["peasant_ledger"]
                pl["grain_surplus"] = round(pl.get("grain_surplus", 0.0) - p_grain, 1)
                total_peasant_grain += p_grain

            g_land = v.get("gentry_ledger", {}).get("registered_farmland", 0)
            if g_land > 0:
                g_grain = round(actual_payment * g_land / total_land * GRAIN_PER_LIANG, 1)
                gl = v["gentry_ledger"]
                gl["grain_surplus"] = round(gl.get("grain_surplus", 0.0) - g_grain, 1)
                total_gentry_grain += g_grain

        county["peasant_grain_reserve"] = round(
            float(county.get("peasant_grain_reserve", 0)) - total_peasant_grain, 1
        )
        return {
            "peasant_grain_total": round(total_peasant_grain, 1),
            "gentry_grain_total": round(total_gentry_grain, 1),
        }

    @staticmethod
    def _apply_gentry_affinity(game, delta: int) -> None:
        """对本游戏所有 GENTRY Agent 的 player_affinity 施加 delta。"""
        for agent in Agent.objects.filter(game=game, role="GENTRY"):
            attrs = agent.attributes
            old = attrs.get("player_affinity", 50)
            attrs["player_affinity"] = max(-99, min(99, old + delta))
            agent.attributes = attrs
            agent.save(update_fields=["attributes"])

    @staticmethod
    def _write_prefect_memory(game, record: dict) -> None:
        """将摊派处置结果写入知府记忆（不抛出异常）。"""
        try:
            prefect = Agent.objects.filter(game=game, role="PREFECT").first()
            if prefect is None:
                return
            method_label = _METHOD_LABELS[record["apportionment_method"]]
            ratio_label = _PAYMENT_LABELS[record["payment_ratio"]]
            p_grain = record.get("peasant_grain_total", 0)
            g_grain = record.get("gentry_grain_total", 0)
            memo = (
                f"第{record['year']}年三月皇帝巡游摊派{record['levy_amount']}两，"
                f"知县{ratio_label}缴纳{record['actual_payment']}两，{method_label}，"
                f"农民扣粮{p_grain}斤、地主扣粮{g_grain}斤。"
            )
            attrs = prefect.attributes
            memory = attrs.get("memory", [])
            memory.append(memo)
            if len(memory) > _MAX_MEMORY:
                memory = memory[-_MAX_MEMORY:]
            attrs["memory"] = memory
            prefect.attributes = attrs
            prefect.save(update_fields=["attributes"])
        except Exception as exc:
            logger.warning("写入知府记忆失败: %s", exc)
