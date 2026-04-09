"""投资行动处理服务"""

import logging

from ..models import EventLog
from .constants import (
    MAX_MONTH, month_name, month_of_year,
    INFRA_MAX_LEVEL, INFRA_TYPES,
    calculate_infra_cost, calculate_infra_months,
)
from .ledger import ensure_county_ledgers, ensure_village_ledgers
from .settlement_metrics import MetricsMixin
from .state import load_county_state, save_player_state

logger = logging.getLogger(__name__)


class InvestmentService:
    """投资行动处理"""

    RECLAIM_LAND_GAIN = 800
    RECLAIM_WARNING_THRESHOLD = 0.85
    RECLAIM_HARD_CAP = 1.20
    RECLAIM_HARD_CAP_REASON = "该村继续开垦将超过土地开发上限，无法执行"

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
            "delay_months": 1,  # 1个月后生效（募集训练）
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
        "scholar_lecture": {
            "cost": None,  # 动态：县学升级费用的一半
            "delay_months": 0,  # 当月立即生效
            "requires_village": False,
            "description": "乡贤讲学",
        },
        "open_river_transport": {
            "cost": None,  # 动态：1.5 × 道路修缮基础费用
            "delay_months": 3,
            "requires_village": False,
            "description": "开通河运",
        },
    }

    # 基建 action → county 中对应的 level 字段名
    INFRA_LEVEL_KEYS = {
        "build_irrigation": "irrigation_level",
        "expand_school": "school_level",
        "build_medical": "medical_level",
    }

    # effects_data 中允许的中文/别名 → county 英文字段名映射
    # 兜底处理 LLM 输出非标准键名的情况
    EFFECTS_STAT_ALIASES: dict = {
        '民心': 'morale',
        '治安': 'security',
        '商业': 'commercial',
        '文教': 'education',
        'agriculture': 'agriculture_bonus',  # 农业收成加成（%），结算时乘入秋收产出
    }
    # 四大核心指标（有上下限约束，需通过 MetricsMixin 处理）
    CORE_STATS = frozenset({'morale', 'security', 'commercial', 'education'})

    # -----------------------------------------------------------------------
    # 自创/标准施政选项支持
    # -----------------------------------------------------------------------

    @classmethod
    def _load_custom_policies(cls, game):
        """加载本局可用的自创施政选项（StandardPolicy + 本局 APPROVED 的 ProposedPolicy）。
        返回 list of dict，每项含 action_key / cost_info / delay_months / effects_data 等。
        game=None 时仅加载 StandardPolicy。
        """
        from ..models import StandardPolicy, ProposedPolicy
        result = []

        for sp in StandardPolicy.objects.filter(is_active=True):
            result.append({
                'action': sp.action_key,
                'name': sp.policy_name,
                'cost_type': 'base',          # 乘以 price_index
                'cost_base': sp.cost_base,
                'delay_months': sp.delay_months,
                'requires_village': sp.requires_village,
                'effects_data': sp.effects_data,
                'description': sp.description,
                'source': 'standard',
                'policy_id': sp.id,
            })

        if game is not None:
            game_id = game.id if hasattr(game, 'id') else int(game)
            for pp in ProposedPolicy.objects.filter(
                game=game, status=ProposedPolicy.Status.APPROVED,
            ):
                # Tier 1 始终可用；Tier 2 需要已激活（含全局推广）
                if pp.tier == 2:
                    activated = (
                        game_id in (pp.activated_game_ids or [])
                        or pp.global_promotion
                    )
                    if not activated:
                        # 返回待激活占位（Phase 3: 前端显示状态徽章）
                        result.append({
                            'action': pp.action_key,
                            'name': pp.policy_name,
                            'cost_type': 'fixed',
                            'cost_fixed': pp.cost or 0,
                            'delay_months': pp.delay_months or 0,
                            'requires_village': False,
                            'effects_data': pp.effects_data,
                            'description': pp.effects_data.get('description', ''),
                            'source': 'proposed',
                            'policy_id': pp.id,
                            'is_custom': True,
                            'tier': 2,
                            'code_status': pp.code_status,
                            'pending_activation': True,  # 前端据此展示等待状态
                        })
                        continue

                result.append({
                    'action': pp.action_key,
                    'name': pp.policy_name,
                    'cost_type': 'fixed',         # LLM 已定价，直接使用
                    'cost_fixed': pp.cost or 0,
                    'delay_months': pp.delay_months or 0,
                    'requires_village': False,
                    'effects_data': pp.effects_data,
                    'description': pp.effects_data.get('description', ''),
                    'source': 'proposed',
                    'policy_id': pp.id,
                    'is_custom': True,
                    'is_executed': pp.is_executed,
                    'tier': pp.tier,
                    'code_status': pp.code_status,
                })

        return result

    @classmethod
    def _find_custom_policy(cls, action_key, game=None):
        """按 action_key 找到自创/标准选项定义，找不到返回 None。"""
        for p in cls._load_custom_policies(game):
            if p['action'] == action_key:
                return p
        return None

    @classmethod
    def _get_custom_cost(cls, policy_def, county):
        """计算自创选项实际花费。"""
        if policy_def['cost_type'] == 'base':
            price_index = county.get('price_index', 1.0)
            return round(policy_def['cost_base'] * price_index)
        return policy_def['cost_fixed']  # fixed: LLM 已定好

    @classmethod
    def _apply_custom_effects(cls, county, policy_def, season, game=None):
        """通用效果引擎：处理自创/标准选项的 effects_data。
        返回 (actual_cost, message)。
        """
        effects = policy_def.get('effects_data', {})
        actual_cost = cls._get_custom_cost(policy_def, county)
        policy_name = policy_def['name']
        delay = policy_def.get('delay_months', 0)

        county['treasury'] -= actual_cost

        # immediate 效果：立即 apply delta
        immediate = effects.get('immediate', {})
        if immediate:
            for stat, delta in immediate.items():
                cls._apply_stat_delta(county, stat, delta)

        if delay == 0:
            # 立即完成：apply on_complete
            cls._apply_on_complete(county, policy_def)
            desc = effects.get('description', policy_name)
            msg = f'{policy_name}已完成（花费{actual_cost}两）：{desc}'
        else:
            # 延迟：加入 active_investments，结算时处理
            completion = season + delay
            investment = {
                'action': policy_def['action'],
                'started_season': season,
                'completion_season': completion,
                'description': policy_name,
                'custom_policy_id': policy_def['policy_id'],
                'custom_policy_source': policy_def.get('source', 'proposed'),
            }
            county.setdefault('active_investments', []).append(investment)
            if completion > MAX_MONTH:
                msg = f'{policy_name}已启动（花费{actual_cost}两），但将在任期结束后才完成'
            else:
                msg = f'{policy_name}已启动（花费{actual_cost}两），预计{month_name(completion)}完成'

        return actual_cost, msg

    @classmethod
    def _apply_stat_delta(cls, county, stat, delta):
        """将单个指标 delta 应用到 county，支持中文别名和 agriculture_bonus。"""
        normalized = cls.EFFECTS_STAT_ALIASES.get(stat, stat)
        if normalized == 'agriculture_bonus':
            county['agriculture_bonus'] = round(
                county.get('agriculture_bonus', 0) + delta, 4)
        elif normalized in cls.CORE_STATS:
            MetricsMixin.apply_county_stat_delta(county, normalized, delta)
        elif normalized in county:
            county[normalized] = county[normalized] + delta
        else:
            logger.debug('_apply_stat_delta: unknown stat %r (normalized=%r), skipped', stat, normalized)

    @classmethod
    def _apply_on_complete(cls, county, policy_def):
        """将 on_complete 效果 apply 到 county（结算或即时均调用此方法）。"""
        effects = policy_def.get('effects_data', {})
        on_complete = effects.get('on_complete', {})
        if not on_complete:
            return

        for stat, delta in on_complete.items():
            if stat == 'add_market':
                # 特殊：新增集市
                market_spec = delta if isinstance(delta, dict) else {}
                new_market = {
                    'name': f'{policy_def["name"]}集',
                    'merchants': market_spec.get('merchants', 10),
                    'gmv': 0.0,
                }
                county.setdefault('markets', []).append(new_market)
            else:
                cls._apply_stat_delta(county, stat, delta)

    @classmethod
    def complete_custom_investment(cls, county, investment):
        """结算时调用：完成一条 active_investments 中的自创施政。"""
        from ..models import ProposedPolicy, StandardPolicy
        source = investment.get('custom_policy_source', 'proposed')
        policy_id = investment.get('custom_policy_id')
        if policy_id is None:
            return f'{investment.get("description", "施政")}已完成'

        try:
            if source == 'standard':
                sp = StandardPolicy.objects.get(id=policy_id)
                policy_def = {
                    'action': sp.action_key,
                    'name': sp.policy_name,
                    'effects_data': sp.effects_data,
                }
            else:
                pp = ProposedPolicy.objects.get(id=policy_id)
                policy_def = {
                    'action': pp.action_key,
                    'name': pp.policy_name,
                    'effects_data': pp.effects_data,
                }
                pp.is_executed = True
                pp.save(update_fields=['is_executed'])
        except Exception as e:
            logger.warning('complete_custom_investment: policy not found id=%s source=%s err=%s',
                           policy_id, source, e)
            return f'{investment.get("description", "施政")}已完成'

        cls._apply_on_complete(county, policy_def)
        effects_desc = policy_def['effects_data'].get('description', policy_def['name'])
        return f'{policy_def["name"]}建成：{effects_desc}'

    # -----------------------------------------------------------------------

    @classmethod
    def _get_infra_target_level(cls, county, action):
        """获取基建升级目标等级（当前等级+1）"""
        level_key = cls.INFRA_LEVEL_KEYS.get(action)
        if not level_key:
            return 1
        return county.get(level_key, 0) + 1

    @classmethod
    def _get_reclaim_projection(cls, county, village):
        """Return current/pending/projected reclaim utilization for one village."""
        ensure_village_ledgers(village)
        ceiling = float(village.get("land_ceiling", 0) or 0)
        peasant_land = float(village.get("peasant_ledger", {}).get("farmland", 0) or 0)
        gentry_registered = float(village.get("gentry_ledger", {}).get("registered_farmland", 0) or 0)
        gentry_hidden = float(village.get("gentry_ledger", {}).get("hidden_farmland", 0) or 0)
        cultivated_now = peasant_land + gentry_registered + gentry_hidden

        pending_reclaims = sum(
            1 for inv in county.get("active_investments", [])
            if inv.get("action") == "reclaim_land"
            and inv.get("target_village") == village.get("name")
        )
        pending_gain = pending_reclaims * cls.RECLAIM_LAND_GAIN
        cultivated_with_pending = cultivated_now + pending_gain
        projected_cultivated = cultivated_with_pending + cls.RECLAIM_LAND_GAIN

        current_utilization = cultivated_now / ceiling if ceiling > 0 else 0.0
        projected_utilization = projected_cultivated / ceiling if ceiling > 0 else 0.0
        return {
            "ceiling": ceiling,
            "cultivated_now": cultivated_now,
            "cultivated_with_pending": cultivated_with_pending,
            "projected_cultivated": projected_cultivated,
            "pending_reclaims": pending_reclaims,
            "current_utilization": current_utilization,
            "projected_utilization": projected_utilization,
        }

    @classmethod
    def get_actual_cost(cls, county, action, game=None):
        """获取投资项目的实际花费"""
        infra_type = cls.INFRA_ACTIONS.get(action)
        if infra_type:
            target_level = cls._get_infra_target_level(county, action)
            return calculate_infra_cost(infra_type, target_level, county)
        if action not in cls.INVESTMENT_TYPES:
            # 自创/标准选项
            custom = cls._find_custom_policy(action, game)
            if custom:
                return cls._get_custom_cost(custom, county)
            return 0
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

        if action == "scholar_lecture":
            # 县学升级费用的一半（按下一等级计算；已满级时按最高等级费用）
            school_level = county.get("school_level", 0)
            next_level = min(school_level + 1, INFRA_MAX_LEVEL)
            school_cost = calculate_infra_cost("school", next_level, county)
            return max(1, school_cost // 2)

        if action == "open_river_transport":
            # 道路修缮基础费用 60 × price_index × 1.5
            return round(90 * price_index)

        return round(spec["cost"] * price_index)

    @classmethod
    def get_delay_months(cls, county, action, game=None):
        """获取投资工期"""
        infra_type = cls.INFRA_ACTIONS.get(action)
        if infra_type:
            target_level = cls._get_infra_target_level(county, action)
            return calculate_infra_months(infra_type, target_level)
        if action not in cls.INVESTMENT_TYPES:
            custom = cls._find_custom_policy(action, game)
            if custom:
                return custom.get('delay_months', 0)
            return 0
        spec = cls.INVESTMENT_TYPES[action]
        return spec.get("delay_months", 0)

    @classmethod
    def validate(cls, county, action, target_village=None, season=None, game=None):
        """
        验证投资操作是否合法。
        Returns (is_valid: bool, reason: str). reason 为空字符串表示合法。
        season: 可选，传入时检查月份限制（如开垦荒地不可在七月八月）。
        game: 可选，传入时支持自创/标准选项校验。
        """
        ensure_county_ledgers(county)
        if action not in cls.INVESTMENT_TYPES:
            # 尝试自创/标准选项
            custom = cls._find_custom_policy(action, game)
            if custom is None:
                return False, f"未知的投资类型: {action}"
            # 自创施政默认只能执行一次
            if custom.get('is_executed'):
                return False, f"{custom['name']}已执行过，不可重复累加"
            actual_cost = cls._get_custom_cost(custom, county)
            if county.get("treasury", 0) < actual_cost:
                return False, f"资金不足，需要{actual_cost}两，当前{round(county.get('treasury', 0))}两"
            # 同 action_key 在建检查
            active_actions = [inv["action"] for inv in county.get("active_investments", [])]
            if action in active_actions and custom.get('delay_months', 0) > 0:
                return False, f"{custom['name']}建设中"
            return True, ""

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

        if action == "hire_bailiffs":
            if county.get("bailiff_level", 0) >= 3:
                return False, "衙役已达最高等级(3)"
            if action in active_actions:
                return False, "衙役募集训练中"
        if action == "build_granary" and county.get("has_granary", False):
            return False, "义仓已建成"

        if action == "scholar_lecture":
            last = county.get("last_scholar_lecture_season", -99)
            if season is not None and season - last < 3:
                remaining = 3 - (season - last)
                return False, f"乡贤讲学冷却中，还需{remaining}个月"

        if action == "open_river_transport":
            if county.get("road_repair_count", 0) < 2:
                return False, "开通河运需先修缮道路至少两次"
            if county.get("river_transport_count", 0) >= 2:
                return False, "河运已全线开通（最多2次）"
            active_actions = [inv["action"] for inv in county.get("active_investments", [])]
            if "open_river_transport" in active_actions:
                return False, "河运开凿中，请等待完工"

        if action == "relief":
            if county.get("disaster_this_year") is None:
                return False, "当前无灾害，无需赈灾"
            if county["disaster_this_year"].get("relieved"):
                return False, "已进行过赈灾救济"

        # 村庄目标检查
        if spec["requires_village"]:
            if target_village is None:
                return False, f"{spec['description']}需要指定目标村庄"
            villages = county.get("villages", [])
            village_names = [v["name"] for v in villages]
            if target_village not in village_names:
                return False, f"村庄 '{target_village}' 不存在"
            if action == "reclaim_land":
                for village in villages:
                    if village.get("name") != target_village:
                        continue
                    projection = cls._get_reclaim_projection(county, village)
                    if (
                        projection["ceiling"] > 0
                        and projection["projected_utilization"] > cls.RECLAIM_HARD_CAP
                    ):
                        return False, cls.RECLAIM_HARD_CAP_REASON
                    break
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
    def apply_effects(cls, county, action, season, target_village=None, game=None):
        """Pure-data investment application — no game.save(), no EventLog.
        Shared by player execute() and AI governor paths.
        Returns (actual_cost, message).
        """
        # 自创/标准选项走独立路径
        if action not in cls.INVESTMENT_TYPES:
            custom = cls._find_custom_policy(action, game)
            if custom:
                return cls._apply_custom_effects(county, custom, season, game)
            return 0, f"未知投资类型: {action}"

        spec = cls.INVESTMENT_TYPES[action]
        price_index = county.get("price_index", 1.0)
        actual_cost = cls.get_actual_cost(county, action)

        # 地主出资补贴（G1：宗族/乡绅出资兴建村塾）
        if action == "fund_village_school":
            subsidy = county.pop("landlord_school_subsidy", 0)
            if subsidy:
                actual_cost = max(0, actual_cost - subsidy)

        # Deduct cost
        county["treasury"] -= actual_cost

        # Apply immediate or delayed effects
        if action == "hire_bailiffs":
            # 延迟1个月生效（募集训练），效果递减(8/7/6)
            target_level = county.get("bailiff_level", 0) + 1
            completion = season + 1
            investment = {
                "action": action,
                "started_season": season,
                "completion_season": completion,
                "description": spec["description"],
                "target_bailiff_level": target_level,
            }
            county["active_investments"].append(investment)
            if completion > MAX_MONTH:
                msg = f"增设衙役已启动（花费{actual_cost}两），但将在任期结束后才完成"
            else:
                msg = f"增设衙役已启动（花费{actual_cost}两），预计{month_name(completion)}完成"
            return actual_cost, msg

        if action == "build_granary":
            is_rebuild = bool(county.get("granary_needs_rebuild"))
            county["has_granary"] = True
            county["granary_needs_rebuild"] = False
            if not county.get("granary_rebuild_cost"):
                county["granary_rebuild_cost"] = round(actual_cost)
            actual_morale_gain = MetricsMixin.apply_county_stat_delta(county, "morale", 5)
            msg = (
                f"义仓重建完成，民心+{actual_morale_gain:.1f}，秋季灾害人口损失×0.65"
                if is_rebuild else
                f"义仓建成，民心+{actual_morale_gain:.1f}，秋季灾害人口损失×0.65"
            )
            return actual_cost, msg

        if action == "relief":
            county["disaster_this_year"]["relieved"] = True
            actual_morale_gain = MetricsMixin.apply_county_stat_delta(county, "morale", 8)
            msg = f"赈灾救济已实施，民心+{actual_morale_gain:.1f}，秋季灾害人口损失×0.65"
            return actual_cost, msg

        if action == "scholar_lecture":
            county["education"] = min(100, county.get("education", 0) + 5)
            county["last_scholar_lecture_season"] = season
            msg = f"乡贤讲学举办，文教+5（当前文教：{county['education']}），3个月后方可再次举办"
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
        county = load_county_state(game)

        if game.current_season > MAX_MONTH:
            return False, "游戏已结束，无法投资"

        # Validate
        is_valid, reason = cls.validate(
            county, action, target_village, season=game.current_season, game=game)
        if not is_valid:
            return False, reason

        actual_cost, msg = cls.apply_effects(
            county, action, game.current_season, target_village, game=game)

        if action == 'build_irrigation':
            msg += '。您可以与各村地主协商，请其出资分担费用。'

        save_player_state(game, county)
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
    def get_available_actions(cls, county, season=None, game=None):
        """Return list of investment actions with pre-calculated costs and disable reasons.

        game: 可选，传入时合并 StandardPolicy + 本局 APPROVED ProposedPolicy。
        """
        ensure_county_ledgers(county)
        result = []

        # ── 内置标准选项 ──
        for action, spec in cls.INVESTMENT_TYPES.items():
            actual_cost = cls.get_actual_cost(county, action)

            current_level = None
            max_level = None
            if action in cls.INFRA_LEVEL_KEYS:
                level_key = cls.INFRA_LEVEL_KEYS[action]
                current_level = county.get(level_key, 0)
                max_level = INFRA_MAX_LEVEL
            elif action == "hire_bailiffs":
                current_level = county.get("bailiff_level", 0)
                max_level = 3
            elif action == "open_river_transport":
                current_level = county.get("river_transport_count", 0)
                max_level = 2

            disabled_reason = None
            if spec["requires_village"]:
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
                "is_custom": False,
            }

            if action == "reclaim_land":
                warnings = []
                blocked_villages = []
                for v in county.get("villages", []):
                    projection = cls._get_reclaim_projection(county, v)
                    if projection["ceiling"] <= 0:
                        continue
                    if projection["current_utilization"] > cls.RECLAIM_WARNING_THRESHOLD:
                        warnings.append({"village": v["name"]})
                    if projection["projected_utilization"] > cls.RECLAIM_HARD_CAP:
                        blocked_villages.append(v["name"])
                if warnings:
                    item["village_warnings"] = warnings
                if blocked_villages:
                    item["blocked_villages"] = blocked_villages

            result.append(item)

        # ── 自创/标准数据库选项（StandardPolicy + ProposedPolicy APPROVED）──
        for custom in cls._load_custom_policies(game):
            action = custom['action']
            actual_cost = cls._get_custom_cost(custom, county)

            # Tier 2 待激活：直接作为 disabled 卡片返回，不走 validate
            if custom.get('pending_activation'):
                code_status = custom.get('code_status', 'pending_dev')
                status_labels = {
                    'pending_dev':  '等待系统审批中',
                    'dev_complete': '即将上线',
                }
                disabled_label = status_labels.get(code_status, '暂不可用')
                result.append({
                    "action": action,
                    "name": custom['name'],
                    "cost": actual_cost,
                    "requires_village": False,
                    "disabled_reason": disabled_label,
                    "current_level": None,
                    "max_level": None,
                    "is_custom": True,
                    "custom_source": 'proposed',
                    "description": custom.get('description', ''),
                    "tier": 2,
                    "code_status": code_status,
                    "pending_activation": True,
                })
                continue

            _, reason = cls.validate(county, action, season=season, game=game)
            result.append({
                "action": action,
                "name": custom['name'],
                "cost": actual_cost,
                "requires_village": custom.get('requires_village', False),
                "disabled_reason": reason or None,
                "current_level": None,
                "max_level": None,
                "is_custom": True,
                "is_executed": custom.get('is_executed', False),
                "custom_source": custom.get('source', 'proposed'),
                "description": custom.get('description', ''),
                "tier": custom.get('tier', 1),
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
