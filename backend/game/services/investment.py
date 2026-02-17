"""投资行动处理服务"""

from ..models import EventLog


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
            msg = f"衙役等级提升至{county['bailiff_level']}，治安+8，年行政开支+40两"
            cls._log_investment(game, action, msg, spec["cost"], target_village, county["treasury"])
            return True, msg

        if action == "build_granary":
            county["has_granary"] = True
            county["morale"] = min(100, county["morale"] + 5)
            game.county_data = county
            game.save()
            msg = "义仓建成，民心+5，灾害时损失减半"
            cls._log_investment(game, action, msg, spec["cost"], target_village, county["treasury"])
            return True, msg

        if action == "relief":
            county["disaster_this_year"]["relieved"] = True
            county["morale"] = min(100, county["morale"] + 8)
            game.county_data = county
            game.save()
            msg = "赈灾救济已实施，民心+8，秋季灾害损失减半"
            cls._log_investment(game, action, msg, spec["cost"], target_village, county["treasury"])
            return True, msg

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
            msg = f"{spec['description']}已启动（花费{spec['cost']}两），但将在游戏结束后才完成"
        else:
            msg = f"{spec['description']}已启动（花费{spec['cost']}两），预计第{completion}季度完成"

        if action == 'build_irrigation':
            msg += '。您可以与各村地主协商，请其出资分担费用。'

        cls._log_investment(game, action, msg, spec["cost"], target_village, county["treasury"])
        return True, msg

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
