"""投资行动处理服务"""

from ..models import EventLog
from .constants import (
    MAX_MONTH, month_name,
    INFRA_MAX_LEVEL, INFRA_TYPES,
    calculate_infra_cost, calculate_infra_months,
)


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
    def validate(cls, county, action, target_village=None):
        """
        验证投资操作是否合法。
        Returns (is_valid: bool, reason: str). reason 为空字符串表示合法。
        """
        if action not in cls.INVESTMENT_TYPES:
            return False, f"未知的投资类型: {action}"

        spec = cls.INVESTMENT_TYPES[action]
        actual_cost = cls.get_actual_cost(county, action)

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
    def execute(cls, game, action, target_village=None):
        """
        Execute an investment action.
        Returns (success: bool, message: str).
        """
        county = game.county_data

        if game.current_season > MAX_MONTH:
            return False, "游戏已结束，无法投资"

        # Validate
        is_valid, reason = cls.validate(county, action, target_village)
        if not is_valid:
            return False, reason

        spec = cls.INVESTMENT_TYPES[action]
        price_index = county.get("price_index", 1.0)
        actual_cost = cls.get_actual_cost(county, action)

        # Find village index if needed
        village_idx = None
        if spec["requires_village"] and target_village:
            village_names = [v["name"] for v in county["villages"]]
            village_idx = village_names.index(target_village)

        # Deduct cost
        county["treasury"] -= actual_cost

        # Apply immediate or delayed effects
        if action == "hire_bailiffs":
            county["bailiff_level"] += 1
            county["security"] = min(100, county["security"] + 8)
            admin_increase = round(40 * price_index)
            county["admin_cost"] += admin_increase
            if "admin_cost_detail" in county:
                county["admin_cost_detail"]["bailiff_cost"] += admin_increase
            game.county_data = county
            game.save()
            msg = f"衙役等级提升至{county['bailiff_level']}，治安+8，年行政开支+{admin_increase}两"
            cls._log_investment(game, action, msg, actual_cost, target_village, county["treasury"])
            return True, msg

        if action == "build_granary":
            county["has_granary"] = True
            county["morale"] = min(100, county["morale"] + 5)
            game.county_data = county
            game.save()
            msg = "义仓建成，民心+5，秋季灾害人口损失×0.65"
            cls._log_investment(game, action, msg, actual_cost, target_village, county["treasury"])
            return True, msg

        if action == "relief":
            county["disaster_this_year"]["relieved"] = True
            county["morale"] = min(100, county["morale"] + 8)
            game.county_data = county
            game.save()
            msg = "赈灾救济已实施，民心+8，秋季灾害人口损失×0.65"
            cls._log_investment(game, action, msg, actual_cost, target_village, county["treasury"])
            return True, msg

        # Delayed investments: compute completion month
        current = game.current_season
        if action == "reclaim_land":
            # Completes at next 九月 (harvest): months 9, 21, 33
            harvest_months = [m for m in [9, 21, 33] if m > current]
            if not harvest_months:
                completion = MAX_MONTH + 1  # won't complete in this game
            else:
                completion = harvest_months[0]
        else:
            delay = cls.get_delay_months(county, action)
            completion = current + delay

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

        if completion > MAX_MONTH:
            msg = f"{spec['description']}已启动（花费{actual_cost}两），但将在任期结束后才完成"
        else:
            msg = f"{spec['description']}已启动（花费{actual_cost}两），预计{month_name(completion)}完成"

        if action == 'build_irrigation':
            msg += '。您可以与各村地主协商，请其出资分担费用。'

        cls._log_investment(game, action, msg, actual_cost, target_village, county["treasury"])
        return True, msg

    @classmethod
    def _get_target_village_disabled_reason(cls, county, action):
        """For village-targeted actions, return disable reason or None if any village is eligible."""
        village_names = [v.get("name") for v in county.get("villages", []) if v.get("name")]
        if not village_names:
            return "当前无可用村庄"

        reasons = []
        for village_name in village_names:
            is_valid, reason = cls.validate(county, action, village_name)
            if is_valid:
                return None
            reasons.append(reason)

        # Keep specific reason when all villages fail for the same cause
        unique_reasons = list(dict.fromkeys(reasons))
        if len(unique_reasons) == 1:
            return unique_reasons[0]
        return "暂无可选目标村庄"

    @classmethod
    def get_available_actions(cls, county):
        """Return list of investment actions with pre-calculated costs and disable reasons."""
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
                disabled_reason = cls._get_target_village_disabled_reason(county, action)
            else:
                _, reason = cls.validate(county, action)
                if reason:
                    disabled_reason = reason

            result.append({
                "action": action,
                "name": spec["description"],
                "cost": actual_cost,
                "requires_village": spec["requires_village"],
                "disabled_reason": disabled_reason,
                "current_level": current_level,
                "max_level": max_level,
            })
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
