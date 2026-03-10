"""投资行动处理服务"""

from ..models import EventLog
from .constants import (
    MAX_MONTH, month_name, month_of_year,
    INFRA_MAX_LEVEL, INFRA_TYPES,
    calculate_infra_cost, calculate_infra_months,
)
from .ledger import ensure_county_ledgers, ensure_village_ledgers


class InvestmentService:
    """投资行动处理"""

    # 基建类投资（费用/工期动态计算）
    INFRA_ACTIONS = {"build_irrigation": "irrigation", "expand_school": "school", "build_medical": "medical"}

    # 投资类型定义: cost, delay_months, requires_target_village
    INVESTMENT_TYPES = {
        "reclaim_land": {
            "cost": 50,
            "delay_months": None,  # completes at next 九月 (harvest)
            "requires_village": True,
            "description": "开垦荒地",
        },
        "build_irrigation": {
            "cost": None,  # 动态计算
            "delay_months": None,  # 动态计算
            "requires_village": False,
            "description": "修建水利",
        },
        "expand_school": {
            "cost": None,  # 动态计算
            "delay_months": None,  # 动态计算
            "requires_village": False,
            "description": "扩建县学",
        },
        "build_medical": {
            "cost": None,  # 动态计算
            "delay_months": None,  # 动态计算
            "requires_village": False,
            "description": "建设医疗",
        },
        "fund_village_school": {
            "cost": 30,
            "delay_months": 4,  # 小规模修缮+聘塾师
            "requires_village": True,
            "description": "资助村塾",
        },
        "hire_bailiffs": {
            "cost": 40,
            "delay_months": 0,  # immediate
            "requires_village": False,
            "description": "增设衙役",
        },
        "repair_roads": {
            "cost": 60,
            "delay_months": 2,  # 征发民夫修路
            "requires_village": False,
            "description": "修缮道路",
        },
        "build_granary": {
            "cost": 70,
            "delay_months": 0,  # immediate
            "requires_village": False,
            "description": "开设义仓",
        },
        "relief": {
            "cost": 80,
            "delay_months": 0,  # immediate
            "requires_village": False,
            "description": "赈灾救济",
        },
    }

    # 基建 action → county 中对应的 level 字段名
    INFRA_LEVEL_KEYS = {
        "build_irrigation": "irrigation_level",
        "expand_school": "school_level",
        "build_medical": "medical_level",
    }

    @classmethod
    def _get_infra_target_level(cls, county, action):
        """获取基建升级目标等级（当前等级+1）"""
        level_key = cls.INFRA_LEVEL_KEYS.get(action)
        if not level_key:
            return 1
        return county.get(level_key, 0) + 1

    @classmethod
    def get_actual_cost(cls, county, action):
        """获取投资项目的实际花费"""
        infra_type = cls.INFRA_ACTIONS.get(action)
        if infra_type:
            target_level = cls._get_infra_target_level(county, action)
            return calculate_infra_cost(infra_type, target_level, county)
        spec = cls.INVESTMENT_TYPES[action]
        price_index = county.get("price_index", 1.0)

        if action == "build_granary":
            # 灾后重建默认沿用首次建仓成本（不随当前物价再波动）
            rebuild_cost = county.get("granary_rebuild_cost")
            try:
                if rebuild_cost is not None:
                    parsed = float(rebuild_cost)
                    if parsed > 0:
                        return round(parsed)
            except (TypeError, ValueError):
                pass

        if action == "relief":
            # 赈灾成本随灾害强度与物价浮动
            disaster = county.get("disaster_this_year") or {}
            try:
                severity = float(disaster.get("severity", 0.0))
            except (TypeError, ValueError):
                severity = 0.0
            severity = max(0.0, min(1.0, severity))
            dynamic_multiplier = 0.8 + severity * 0.8
            return round(spec["cost"] * price_index * dynamic_multiplier)

        return round(spec["cost"] * price_index)

    @classmethod
    def get_delay_months(cls, county, action):
        """获取投资工期"""
        infra_type = cls.INFRA_ACTIONS.get(action)
        if infra_type:
            target_level = cls._get_infra_target_level(county, action)
            return calculate_infra_months(infra_type, target_level)
        spec = cls.INVESTMENT_TYPES[action]
        return spec.get("delay_months", 0)

    @classmethod
    def validate(cls, county, action, target_village=None, season=None):
        """
        验证投资操作是否合法。
        Returns (is_valid: bool, reason: str). reason 为空字符串表示合法。
        season: 可选，传入时检查月份限制（如开垦荒地不可在七月八月）。
        """
        ensure_county_ledgers(county)
        if action not in cls.INVESTMENT_TYPES:
            return False, f"未知的投资类型: {action}"

        spec = cls.INVESTMENT_TYPES[action]
        actual_cost = cls.get_actual_cost(county, action)

        # 月份限制：开垦荒地不可在七月八月（农忙时节）
        if action == "reclaim_land" and season is not None:
            moy = month_of_year(season)
            if moy in (7, 8):
                return False, "七月八月农忙时节，不宜开垦荒地"

        # 资金检查
        if county.get("treasury", 0) < actual_cost:
            return False, f"资金不足，需要{actual_cost}两，当前{round(county.get('treasury', 0))}两"

        # 同类在建检查（基建类不可同时建设同类）
        active_actions = [inv["action"] for inv in county.get("active_investments", [])]
        if action in cls.INFRA_ACTIONS and action in active_actions:
            return False, f"{cls.INVESTMENT_TYPES[action]['description']}建设中"

        # 基建等级上限检查
        if action in cls.INFRA_LEVEL_KEYS:
            level_key = cls.INFRA_LEVEL_KEYS[action]
            current_level = county.get(level_key, 0)
            if current_level >= INFRA_MAX_LEVEL:
                return False, f"已达最高等级({INFRA_MAX_LEVEL})"

        if action == "hire_bailiffs" and county.get("bailiff_level", 0) >= 3:
            return False, "衙役已达最高等级(3)"
        if action == "build_granary" and county.get("has_granary", False):
            return False, "义仓已建成"
        if action == "relief":
            if county.get("disaster_this_year") is None:
                return False, "当前无灾害，无需赈灾"
            if county["disaster_this_year"].get("relieved"):
                return False, "已进行过赈灾救济"

        # 村庄目标检查
        if spec["requires_village"]:
            if target_village is None:
                return False, f"{spec['description']}需要指定目标村庄"
            village_names = [v["name"] for v in county.get("villages", [])]
            if target_village not in village_names:
                return False, f"村庄 '{target_village}' 不存在"
            if action == "fund_village_school":
                for v in county.get("villages", []):
                    if v["name"] == target_village and v.get("has_school"):
                        return False, f"{target_village}已有村塾"
                for inv in county.get("active_investments", []):
                    if (
                        inv.get("action") == "fund_village_school"
                        and inv.get("target_village") == target_village
                    ):
                        return False, f"{target_village}村塾建设中"

        return True, ""

    @classmethod
    def apply_effects(cls, county, action, season, target_village=None):
        """Pure-data investment application — no game.save(), no EventLog.
        Shared by player execute() and AI governor paths.
        Returns (actual_cost, message).
        """
        spec = cls.INVESTMENT_TYPES[action]
        price_index = county.get("price_index", 1.0)
        actual_cost = cls.get_actual_cost(county, action)

        # Deduct cost
        county["treasury"] -= actual_cost

        # Apply immediate or delayed effects
        if action == "hire_bailiffs":
            county["bailiff_level"] += 1
            county["security"] = min(100, county["security"] + 8)
            village_security_bonus = 5
            for village in county.get("villages", []):
                village["security"] = max(
                    0, min(100, village.get("security", 50) + village_security_bonus)
                )
            admin_increase = round(40 * price_index)
            county["admin_cost"] += admin_increase
            if "admin_cost_detail" in county:
                county["admin_cost_detail"]["bailiff_cost"] += admin_increase
            msg = (
                f"衙役等级提升至{county['bailiff_level']}，县治安+8、"
                f"各村治安+{village_security_bonus}，年行政开支+{admin_increase}两"
            )
            return actual_cost, msg

        if action == "build_granary":
            is_rebuild = bool(county.get("granary_needs_rebuild"))
            county["has_granary"] = True
            county["granary_needs_rebuild"] = False
            if not county.get("granary_rebuild_cost"):
                county["granary_rebuild_cost"] = round(actual_cost)
            county["morale"] = min(100, county["morale"] + 5)
            msg = (
                "义仓重建完成，民心+5，秋季灾害人口损失×0.65"
                if is_rebuild else
                "义仓建成，民心+5，秋季灾害人口损失×0.65"
            )
            return actual_cost, msg

        if action == "relief":
            county["disaster_this_year"]["relieved"] = True
            county["morale"] = min(100, county["morale"] + 8)
            msg = "赈灾救济已实施，民心+8，秋季灾害人口损失×0.65"
            return actual_cost, msg

        # Delayed investments: compute completion month
        if action == "reclaim_land":
            completion = season + 2
        else:
            delay = cls.get_delay_months(county, action)
            completion = season + delay

        investment = {
            "action": action,
            "started_season": season,
            "completion_season": completion,
            "description": spec["description"],
        }
        if spec["requires_village"] and target_village:
            investment["target_village"] = target_village

        county["active_investments"].append(investment)

        if completion > MAX_MONTH:
            msg = f"{spec['description']}已启动（花费{actual_cost}两），但将在任期结束后才完成"
        else:
            msg = f"{spec['description']}已启动（花费{actual_cost}两），预计{month_name(completion)}完成"

        return actual_cost, msg

    @classmethod
    def execute(cls, game, action, target_village=None):
        """
        Execute an investment action (player path).
        Returns (success: bool, message: str).
        """
        county = game.county_data

        if game.current_season > MAX_MONTH:
            return False, "游戏已结束，无法投资"

        # Validate
        is_valid, reason = cls.validate(county, action, target_village, season=game.current_season)
        if not is_valid:
            return False, reason

        actual_cost, msg = cls.apply_effects(
            county, action, game.current_season, target_village)

        if action == 'build_irrigation':
            msg += '。您可以与各村地主协商，请其出资分担费用。'

        game.county_data = county
        game.save()
        cls._log_investment(game, action, msg, actual_cost, target_village, county["treasury"])
        return True, msg

    @classmethod
    def _get_target_village_disabled_reason(cls, county, action, season=None):
        """For village-targeted actions, return disable reason or None if any village is eligible."""
        village_names = [v.get("name") for v in county.get("villages", []) if v.get("name")]
        if not village_names:
            return "当前无可用村庄"

        reasons = []
        for village_name in village_names:
            is_valid, reason = cls.validate(county, action, village_name, season=season)
            if is_valid:
                return None
            reasons.append(reason)

        # Keep specific reason when all villages fail for the same cause
        unique_reasons = list(dict.fromkeys(reasons))
        if len(unique_reasons) == 1:
            return unique_reasons[0]
        return "暂无可选目标村庄"

    @classmethod
    def get_available_actions(cls, county, season=None):
        """Return list of investment actions with pre-calculated costs and disable reasons."""
        ensure_county_ledgers(county)
        result = []
        for action, spec in cls.INVESTMENT_TYPES.items():
            actual_cost = cls.get_actual_cost(county, action)

            # Current/max level for infra actions
            current_level = None
            max_level = None
            if action in cls.INFRA_LEVEL_KEYS:
                level_key = cls.INFRA_LEVEL_KEYS[action]
                current_level = county.get(level_key, 0)
                max_level = INFRA_MAX_LEVEL
            elif action == "hire_bailiffs":
                current_level = county.get("bailiff_level", 0)
                max_level = 3

            disabled_reason = None
            if spec["requires_village"]:
                # For village-targeted actions, only disable when no valid village can be selected.
                disabled_reason = cls._get_target_village_disabled_reason(county, action, season=season)
            else:
                _, reason = cls.validate(county, action, season=season)
                if reason:
                    disabled_reason = reason

            item = {
                "action": action,
                "name": spec["description"],
                "cost": actual_cost,
                "requires_village": spec["requires_village"],
                "disabled_reason": disabled_reason,
                "current_level": current_level,
                "max_level": max_level,
            }

            # 过度开发预警 (doc 06a §2.5): reclaim_land 时标记高利用率村庄
            if action == "reclaim_land":
                warnings = []
                for v in county.get("villages", []):
                    ensure_village_ledgers(v)
                    ceiling = v.get("land_ceiling", 0)
                    if ceiling <= 0:
                        continue
                    peasant_land = v.get("peasant_ledger", {}).get("farmland", 0)
                    gentry_registered = v.get("gentry_ledger", {}).get("registered_farmland", 0)
                    gentry_hidden = v.get("gentry_ledger", {}).get("hidden_farmland", 0)
                    cultivated = peasant_land + gentry_registered + gentry_hidden
                    utilization = cultivated / ceiling
                    if utilization > 0.85:
                        warnings.append({
                            "village": v["name"],
                            "utilization": round(utilization * 100, 1),
                        })
                if warnings:
                    item["village_warnings"] = warnings

            result.append(item)
        return result

    @classmethod
    def _log_investment(cls, game, action, msg, cost, target_village, treasury_after):
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type=f'investment_{action}',
            category='INVESTMENT',
            description=msg,
            data={
                'action': action,
                'cost': cost,
                'target_village': target_village,
                'treasury_after': round(treasury_after, 1),
            },
        )
