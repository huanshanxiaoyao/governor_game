"""知府游戏服务 — 府域初始化、月度结算、汇报生成"""

import copy
import logging
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Optional

from django.db import connection

from ..models import AdminUnit, Agent, EventLog, GameState, JudicialCaseInstance, NeighborPrecompute
from .constants import (
    COUNTY_TYPES,
    ARCHETYPE_COUNTY_TYPE_WEIGHTS,
    GOVERNOR_STYLES,
    GOVERNOR_SURNAMES,
    GOVERNOR_GIVEN_NAMES,
    NEIGHBOR_COUNTY_NAMES,
    derive_governor_style,
    generate_governor_profile,
    month_of_year,
    month_name,
    year_of,
    CORVEE_PER_CAPITA,
    QUOTA_BASE_COLLECTION_EFFICIENCY,
)
from .county import CountyService
from .settlement import SettlementService
from .ai_governor import AIGovernorService
from .emergency import EmergencyService
from .magistrate_service import MagistrateService
from .annual_review import AnnualReviewService
from .judicial_caseflow import JudicialCaseflowService
from llm.client import LLM_DEFAULT_TIMEOUT

logger = logging.getLogger('game')

# ===== 汇报月份 =====
REPORT_MONTHS = {2, 5, 8, 11}

# ===== 指标档位映射 =====
TIER_THRESHOLDS = [
    (0,  12,  "极差"),
    (13, 24,  "差"),
    (25, 37,  "稍差"),
    (38, 49,  "勉强"),
    (50, 62,  "及格"),
    (63, 74,  "稍好"),
    (75, 87,  "良好"),
    (88, 99,  "优秀"),
]

DISASTER_TYPE_LABELS = {
    "flood": "洪灾",
    "drought": "旱灾",
    "locust": "蝗灾",
    "plague": "疫病",
}


def score_to_tier(score: float) -> str:
    """将 0–99 数值转换为 8 档状况描述"""
    s = max(0, min(99, int(score)))
    for lo, hi, label in TIER_THRESHOLDS:
        if lo <= s <= hi:
            return label
    return "及格"


def _clamp_meter(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamp relationship/reputation meters to a stable range."""
    return max(lo, min(hi, float(value)))


# ===== 府域类型定义 =====
PREFECTURE_TYPES = {
    "fiscal_heavy": {
        "name": "财赋重府",
        "description": "高额省级定额，财税压力与地方发展空间的博弈",
        "county_mix": ["fiscal_core", "fiscal_core", "clan_governance", "fiscal_core", "clan_governance"],
        "quota_difficulty": 0.80,   # 省里盯得紧，压力最重
        "prefecture_names": ["苏州府", "松江府", "常州府", "嘉兴府", "湖州府"],
    },
    "frontier_heavy": {
        "name": "边防要府",
        "description": "军事压力突出，民生资源有限",
        "county_mix": ["disaster_prone", "coastal", "disaster_prone", "coastal", "clan_governance"],
        "quota_difficulty": 0.72,   # 边疆有减免，但军费另有摊派
        "prefecture_names": ["大同府", "宣府", "保定府", "永平府", "延绥镇"],
    },
    "balanced_inland": {
        "name": "均衡内陆",
        "description": "各类型混合，核心挑战为均衡发展与突出重点",
        "county_mix": ["fiscal_core", "clan_governance", "disaster_prone", "coastal", "clan_governance"],
        "quota_difficulty": 0.75,   # 标准压力
        "prefecture_names": ["南昌府", "长沙府", "武昌府", "成都府", "西安府"],
    },
    "remote_poor": {
        "name": "贫困边远",
        "description": "资源极度匮乏，生存压力为主",
        "county_mix": ["coastal", "disaster_prone", "clan_governance", "disaster_prone", "coastal"],
        "quota_difficulty": 0.68,   # 省里预期低，但实收也难
        "prefecture_names": ["柳州府", "廉州府", "琼州府", "贵阳府", "思州府"],
    },
}

# 府衙年度固定行政开支（两）
PREFECTURE_ANNUAL_ADMIN_COST = {
    "salary": 200,      # 知府俸禄（含养廉）
    "tongpan": 100,     # 通判俸禄
    "tuiguan": 80,      # 推官俸禄
    "staff": 60,        # 幕僚束脩
    "clerks": 80,       # 府衙书吏
    "misc": 80,         # 衙署杂费
}
PREFECTURE_ANNUAL_ADMIN_TOTAL = sum(PREFECTURE_ANNUAL_ADMIN_COST.values())  # 600两

GRANARY_MAX_STOCK = 20000.0   # 义仓最大容量（斤）
GRANARY_INIT_STOCK = 10000.0  # 义仓建成时初始库存（斤）

# ===== 府级投资规格 =====
PREFECTURE_INVESTMENT_SPECS = {
    "school": {
        "label": "府学",
        "field": "school_level",
        "max_level": 3,
        "costs":     [300, 500, 800],   # 建设费用（对应等级 1/2/3）
        "durations": [4,   6,   10],    # 建设工期（月）
    },
    "road": {
        "label": "交通基建",
        "field": "road_level",
        "max_level": 2,
        "costs":     [400, 800],
        "durations": [6,   12],
    },
    "granary": {
        "label": "府级义仓",
        "field": "granary",
        "max_level": 1,
        "costs":     [500],
        "durations": [0],               # 即时完工
    },
    "river": {
        "label": "水利基建",
        "field": "river_work_level",
        "max_level": 2,
        "costs":     [600, 1000],
        "durations": [12,  18],
    },
}

# ===== 府试常量 =====
# school_level 0/1/2/3 对应能力值噪声幅度（分值偏移）
EXAM_NOISE_BY_SCHOOL = [20, 10, 4, 0]
EXAM_TOP_N = 100   # 每届府试录取名额


class _SubordinateAdapter:
    """让 AdminUnit 以 NeighborCounty 接口被 AIGovernorService 使用"""

    def __init__(self, unit: AdminUnit):
        self._unit = unit

    # AIGovernorService 需要的属性
    @property
    def id(self):
        return f"sub_{self._unit.id}"

    @property
    def county_data(self):
        return self._unit.unit_data

    @county_data.setter
    def county_data(self, value):
        self._unit.unit_data = value

    @property
    def county_name(self):
        return self._unit.unit_data.get('county_name', '')

    @property
    def governor_name(self):
        return self._unit.unit_data.get('governor_profile', {}).get('name', '')

    @property
    def governor_style(self):
        return self._unit.unit_data.get('governor_profile', {}).get('style', 'baoshou')

    @property
    def governor_bio(self):
        return self._unit.unit_data.get('governor_profile', {}).get('bio', '')

    @property
    def last_reasoning(self):
        return self._unit.unit_data.get('_last_reasoning', '')

    @last_reasoning.setter
    def last_reasoning(self, value):
        self._unit.unit_data['_last_reasoning'] = value

    def save(self, update_fields=None):
        """Propagate saves back to AdminUnit"""
        self._unit.save(update_fields=['unit_data'])


class PrefectureService:
    """知府游戏的核心服务：初始化、月度结算、汇报生成"""

    # ==================== 初始化 ====================

    @classmethod
    def create_prefecture_game(cls, game, prefecture_type: str = None):
        """
        初始化知府游戏：
        - 创建 AdminUnit(PREFECTURE) 作为 player_unit
        - 创建 5–6 个 AdminUnit(COUNTY) 作为下辖县，含 AI 知县
        - 设置 game.player_role = 'PREFECT'
        """
        if prefecture_type is None:
            prefecture_type = random.choice(list(PREFECTURE_TYPES.keys()))

        ptype = PREFECTURE_TYPES[prefecture_type]
        prefecture_name = random.choice(ptype["prefecture_names"])

        # ── 府域基础数据 ──
        county_mix = ptype["county_mix"]

        prefecture_data = {
            "prefecture_name": prefecture_name,
            "prefecture_type": prefecture_type,
            "prefecture_type_name": ptype["name"],
            "treasury": 800,
            "judicial_prestige": 50,        # 司法声望（0-100）
            "inspector_favor": 50,          # 按察使观感（0-100）
            "annual_quota": 0,           # 在县初始化后动态计算，见下方
            "quota_assignments": {},         # {unit_id: amount}
            "inspection_used": {"tongpan": 0, "tuiguan": 0},  # 年度核查次数
            "school_level": 0,               # 府学等级 0–3
            "road_level": 0,                 # 交通基建等级 0–2
            "granary": False,                # 府级义仓
            "river_work_level": 0,           # 水利基建等级 0–2
            "year_end_review_pending": False,
            "exam_pending": False,
            "pending_events": [],
            # 基础建设
            "construction_queue": [],    # [{project, label, level, months_remaining, started_season}]
            # 才池与府试
            "talent_pool": [],           # 在 _init_talent_pool 中填充
            "exam_results": [],          # 最近3次府试记录
            "total_disciples": 0,        # 累计录取门生人数
        }

        # ── 创建府级 AdminUnit ──
        prefecture_unit = AdminUnit.objects.create(
            game=game,
            unit_type='PREFECTURE',
            unit_data=prefecture_data,
            is_player_controlled=True,
        )

        # ── 创建下辖县 AdminUnit ──
        subordinates = cls._create_subordinate_counties(
            game=game,
            parent=prefecture_unit,
            county_mix=county_mix,
            prefecture_name=prefecture_name,
        )

        # ── 动态计算省级定额（依据各县实际土地人口，§5.2公式）──
        annual_quota, per_county_quotas = cls._compute_annual_quota(
            subordinates, ptype["quota_difficulty"]
        )
        prefecture_unit.unit_data['annual_quota'] = annual_quota

        # ── 写入初始配额建议（按县能力分配，而非均摊）──
        default_quota = {str(uid): q for uid, q in per_county_quotas.items()}
        prefecture_unit.unit_data['quota_assignments'] = default_quota

        # ── 初始化才池 ──
        cls._init_talent_pool(prefecture_unit.unit_data, subordinates)

        prefecture_unit.save(update_fields=['unit_data'])

        # ── 更新 GameState ──
        game.player_role = 'PREFECT'
        game.player_unit = prefecture_unit
        game.save(update_fields=['player_role', 'player_unit'])

        return game

    @classmethod
    def _compute_annual_quota(cls, subordinates: list, difficulty: float) -> tuple:
        """
        依据各下辖县实际在册土地和人口，使用 §5.2 公式计算省级定额。
        与知县游戏的 _set_annual_quota 保持完全一致的公式。

        返回: (total_quota, {unit_id: county_quota})
        """
        per_county = {}
        for unit in subordinates:
            cd = unit.unit_data
            total_land = sum(v["farmland"] for v in cd.get("villages", []))
            total_peasant_pop = sum(
                v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
                for v in cd.get("villages", [])
            )
            tax_rate = cd.get("tax_rate", 0.12)
            remit_ratio = cd.get("remit_ratio", 0.65)
            irrigation_bonus = cd.get("irrigation_level", 0) * 0.15

            # 农业税配额（标准年，不含农业适宜度和灾害波动）
            agri_quota = (
                total_land * 0.5 * (1 + irrigation_bonus)
                * tax_rate * QUOTA_BASE_COLLECTION_EFFICIENCY * remit_ratio
            )
            # 徭役折银配额
            corvee_quota = total_peasant_pop * CORVEE_PER_CAPITA * remit_ratio

            per_county[unit.id] = round((agri_quota + corvee_quota) * difficulty)

        total_quota = sum(per_county.values())
        return total_quota, per_county

    @classmethod
    def _create_subordinate_counties(cls, game, parent, county_mix, prefecture_name):
        """生成下辖各县的 AdminUnit，含 AI 知县 profile 和 LLM 生成简介"""
        used_names = set()

        def _pick_name():
            for _ in range(100):
                n = random.choice(list(GOVERNOR_SURNAMES)) + random.choice(list(GOVERNOR_GIVEN_NAMES))
                if n not in used_names:
                    used_names.add(n)
                    return n
            return "某知县"

        # ── 分配施政类型：保证2贪酷 ──
        archetypes = cls._assign_archetypes(county_mix)

        specs = []
        for i, c_type in enumerate(county_mix):
            archetype = archetypes[i]
            # 先从 archetype 生成属性，再推导风格
            profile = generate_governor_profile(archetype)
            style_key = derive_governor_style(profile)
            names_pool = list(NEIGHBOR_COUNTY_NAMES.get(c_type, ["下辖县"]))
            county_name = names_pool[i % len(names_pool)]
            specs.append({
                'c_type': c_type,
                'archetype': archetype,
                'style_key': style_key,
                'profile': profile,
                'county_name': county_name,
                'governor_name': _pick_name(),
            })

        # ── 并行生成 LLM 人物简介 ──
        bios = cls._generate_bios_parallel(specs)

        units = []
        for i, spec in enumerate(specs):
            bio = bios[i] or f"{spec['governor_name']}，{spec['county_name']}知县。"
            county_data = CountyService.create_initial_county(county_type=spec['c_type'])
            EmergencyService.ensure_state(county_data)
            county_data['governor_profile'] = {
                **spec['profile'],
                'name': spec['governor_name'],
                'style': spec['style_key'],
                'archetype': spec['archetype'],
                'bio': bio,
            }
            county_data['county_name'] = spec['county_name']
            county_data['initial_villages'] = copy.deepcopy(county_data.get('villages', []))
            county_data['initial_snapshot'] = {
                k: county_data.get(k, 0)
                for k in ('treasury', 'morale', 'security', 'commercial', 'education')
            }
            county_data['subordinate_reports'] = []   # 历史汇报列表（最多保留8条）
            # 下属对知府好感度（0-100）：影响服从度和汇报诚实度
            _affinity_by_archetype = {
                'VIRTUOUS': random.randint(60, 75),
                'MIDDLING': random.randint(45, 60),
                'CORRUPT':  random.randint(25, 40),
            }
            county_data['prefect_affinity'] = _affinity_by_archetype.get(spec['archetype'], 50)

            unit = AdminUnit.objects.create(
                game=game,
                unit_type='COUNTY',
                unit_data=county_data,
                is_player_controlled=False,
                parent=parent,
            )
            units.append(unit)

        return units

    @staticmethod
    def _assign_archetypes(county_mix):
        archetypes = ['CORRUPT', 'CORRUPT']
        for c_type in county_mix[2:]:
            weights = ARCHETYPE_COUNTY_TYPE_WEIGHTS.get(c_type, [0.40, 0.60, 0.0])
            w_v, w_m = weights[0], weights[1]
            total = w_v + w_m or 1
            archetypes.append(
                random.choices(['VIRTUOUS', 'MIDDLING'], weights=[w_v / total, w_m / total], k=1)[0]
            )
        random.shuffle(archetypes)
        return archetypes

    @staticmethod
    def _generate_bios_parallel(specs):
        bios = [''] * len(specs)

        def _gen(spec):
            return MagistrateService.generate_neighbor_bio(
                name=spec['governor_name'],
                county_name=spec['county_name'],
                archetype=spec['archetype'],
                style=spec['style_key'],
                county_type=spec['c_type'],
            )

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_idx = {executor.submit(_gen, s): i for i, s in enumerate(specs)}
            try:
                for future in as_completed(future_to_idx, timeout=LLM_DEFAULT_TIMEOUT + 5):
                    idx = future_to_idx[future]
                    try:
                        bios[idx] = future.result()
                    except Exception as e:
                        logger.warning("Bio generation failed for subordinate %d: %s", idx, e)
            except FuturesTimeoutError:
                logger.warning("Subordinate bio generation timed out; %d bio(s) will use fallback text",
                               bios.count(''))
        return bios

    # ==================== 月度结算 ====================

    @classmethod
    def advance_month(cls, game):
        """
        推进知府游戏一个月：
        1. 对每个下辖县运行完整 settle_county（含 AI 决策）
        2. 收取各县上缴，更新府库
        3. 腊月扣除年度行政开支
        4. 汇报月生成汇报
        5. 更新 current_season
        """
        import time as _time
        _t0 = _time.monotonic()

        prefecture_unit = game.player_unit
        pdata = prefecture_unit.unit_data
        season = game.current_season
        moy = month_of_year(season)

        blocker = AnnualReviewService.get_prefecture_advance_blocker(game)
        if blocker:
            return {"error": blocker}

        # ── 建设队列推进（每月冒头）──
        completed_construction = cls._tick_construction(pdata, season)
        for proj in completed_construction:
            EventLog.objects.create(
                game=game, season=season,
                event_type='prefecture_construction_complete',
                category='PREFECTURE',
                description=f'【建设完工】{proj.get("label", proj.get("project", ""))} 升至等级 {proj.get("level", "")}',
                data=proj,
            )

        subordinates = list(
            AdminUnit.objects.filter(game=game, unit_type='COUNTY', parent=prefecture_unit)
        )

        # ── AI 决策：优先使用后台预推演缓存 ──
        _t_ai_start = _time.monotonic()
        precompute = NeighborPrecompute.objects.filter(
            game=game, season=season, status='done',
        ).first()
        if precompute:
            logger.info("Using prefecture precomputed results for game %s season %s (%d counties)",
                        game.id, season, len(precompute.results))
            decision_results = cls._apply_cached_ai_results(subordinates, precompute.results)
        else:
            logger.info("No prefecture precompute ready for game %s season %s, computing in parallel",
                        game.id, season)
            decision_results = cls._compute_ai_decisions(subordinates, season)
        _t_ai_end = _time.monotonic()
        logger.info("advance_month game=%s season=%s: AI决策阶段 %.2fs (cache=%s)",
                    game.id, season, _t_ai_end - _t_ai_start, precompute is not None)

        # 清除已消费的预计算记录
        NeighborPrecompute.objects.filter(game=game).delete()

        # ── 府级基础建设上下文（传入县级结算，影响灾害/商业/人口）──
        prefecture_ctx = {
            "road_level":  pdata.get("road_level", 0),
            "river_level": pdata.get("river_work_level", 0),
            "granary":     bool(pdata.get("granary", False)),
        }

        # ── AI 县应急拨粮/借粮（结算前执行，使本月结算能感知到粮食补充）──
        cls._process_ai_emergency_relief(subordinates, pdata, season, game)

        # ── 物理结算 ──
        _t_settle_start = _time.monotonic()
        remit_total = 0.0
        for unit in subordinates:
            EmergencyService.ensure_state(unit.unit_data)
            adapter = _SubordinateAdapter(unit)

            # 结算前快照 fiscal_year，用于计算本月上缴增量
            fy_before = dict(unit.unit_data.get('fiscal_year', {}))

            report = {"season": season, "events": []}
            events = decision_results.get(unit.id, [])

            # 存储 AI 决策摘要供汇报月使用
            if events:
                # 过滤掉 "【析】" 开头的分析条目，只保留行动
                action_events = [e for e in events if '析】' not in e]
                unit.unit_data['_last_ai_actions'] = '；'.join(action_events[:3]) if action_events else '无特别行动'

            # 清理已消费的指令
            unit.unit_data.pop('pending_directives', None)

            # AI 决策已修改 unit.unit_data（通过 adapter），直接进行物理结算
            SettlementService.settle_county(unit.unit_data, season, report, game=None,
                                            prefecture_ctx=prefecture_ctx)

            # ── Bug-A 修复：正月用玩家配额覆盖 _set_annual_quota 的公式计算值 ──
            # settle_county 内 _set_annual_quota 会用公式重算 annual_quota，
            # 但玩家已通过 distribute_quota 设定了本年配额，必须恢复玩家意图。
            if moy == 1:
                assigned = pdata.get('quota_assignments', {}).get(str(unit.id))
                if assigned is not None:
                    fq = unit.unit_data.get('annual_quota', {})
                    ft = fq.get('total', 0) or float(assigned)
                    ar = fq.get('agricultural', ft * 0.65) / ft if ft else 0.65
                    unit.unit_data['annual_quota'] = {
                        'total': round(float(assigned), 1),
                        'agricultural': round(float(assigned) * ar, 1),
                        'corvee': round(float(assigned) * (1 - ar), 1),
                    }

            # ── 计算本月实际上缴增量（从 fiscal_year 差值推导）──
            fy_after = unit.unit_data.get('fiscal_year', {})
            if moy == 1:
                # 正月重置后 fy_after 只含本月新增
                commercial_remit = (
                    fy_after.get('commercial_tax', 0) - fy_after.get('commercial_retained', 0)
                )
                corvee_remit = (
                    fy_after.get('corvee_tax', 0) - fy_after.get('corvee_retained', 0)
                )
                agri_remit = 0.0
            else:
                commercial_remit = (
                    (fy_after.get('commercial_tax', 0) - fy_before.get('commercial_tax', 0)) -
                    (fy_after.get('commercial_retained', 0) - fy_before.get('commercial_retained', 0))
                )
                corvee_remit = (
                    (fy_after.get('corvee_tax', 0) - fy_before.get('corvee_tax', 0)) -
                    (fy_after.get('corvee_retained', 0) - fy_before.get('corvee_retained', 0))
                )
                agri_remit = fy_after.get('agri_remitted', 0) - fy_before.get('agri_remitted', 0)

            remit = max(0.0, commercial_remit + corvee_remit + agri_remit)
            unit.unit_data['last_remit'] = round(remit, 1)
            remit_total += remit

            unit.save(update_fields=['unit_data'])

        _t_settle_end = _time.monotonic()
        logger.info("advance_month game=%s season=%s: 物理结算 %.2fs (%d县, 合计上缴%.1f)",
                    game.id, season, _t_settle_end - _t_settle_start, len(subordinates), remit_total)

        # ── 府库更新 ──
        pdata['treasury'] = round(pdata.get('treasury', 0) + remit_total, 1)
        # 累计年度已收（正月重置）
        if moy == 1:
            pdata['treasury_collected'] = round(remit_total, 1)
        else:
            pdata['treasury_collected'] = round(pdata.get('treasury_collected', 0) + remit_total, 1)

        # ── 正月：省级年度施政重点下达 ──
        if moy == 1:
            pdata['province_annual_focus'] = cls._generate_province_focus(pdata, season)
            focus_list = pdata.get('province_annual_focus') or []
            EventLog.objects.create(
                game=game, season=season,
                event_type='prefecture_annual_focus',
                category='PREFECTURE',
                description=f'【省级施政重点】本年重点：{"、".join(focus_list) if focus_list else "无特别指示"}',
                data={'focus': focus_list},
            )

        # ── 三月：才池年度结算 ──
        if moy == 3:
            cls._advance_talent_pool(pdata, subordinates)

        # ── 九月：义仓秋粮充库（各县农业上缴额的3%） ──
        if moy == 9 and pdata.get('granary'):
            stock = pdata.get('granary_stock', 0.0)
            from .constants import GRAIN_PER_LIANG
            for unit in subordinates:
                fy = unit.unit_data.get('fiscal_year', {})
                agri_remitted = fy.get('agri_remitted', 0.0)
                # 农业上缴折算为粮食（按 GRAIN_PER_LIANG 斤/两），取3%充库
                grain_contribution = agri_remitted * GRAIN_PER_LIANG * 0.03
                space = max(0.0, GRANARY_MAX_STOCK - stock)
                actual = min(grain_contribution, space)
                stock = round(stock + actual, 1)
            pdata['granary_stock'] = stock
            EventLog.objects.create(
                game=game, season=season,
                event_type='prefecture_granary_fill',
                category='PREFECTURE',
                description=f'【义仓秋粮充库】本月充库后存量：{stock:.0f} 斤（上限 {GRANARY_MAX_STOCK:.0f} 斤）',
                data={'granary_stock': stock, 'granary_max': GRANARY_MAX_STOCK},
            )

        # ── 腊月：扣除年度行政开支 + 上缴省级定额 + 义仓损耗 ──
        if moy == 12:
            school_cost = [0, 120, 240, 480][min(pdata.get('school_level', 0), 3)]
            road_cost = [0, 100, 200][min(pdata.get('road_level', 0), 2)]
            total_cost = PREFECTURE_ANNUAL_ADMIN_TOTAL + school_cost + road_cost
            # 扣行政开支（保证不透支：府库不足时按实际扣除）
            admin_deducted = min(total_cost, pdata.get('treasury', 0))
            pdata['treasury'] = round(pdata.get('treasury', 0) - admin_deducted, 1)

            # 省级上缴（在行政开支之后执行，保证不透支）
            province_remit_due = float(pdata.get('annual_quota', 0))
            province_remitted = round(min(province_remit_due, max(0.0, pdata.get('treasury', 0))), 1)
            pdata['treasury'] = round(pdata.get('treasury', 0) - province_remitted, 1)
            pdata['province_remit_due'] = round(province_remit_due, 1)
            pdata['province_remitted'] = province_remitted
            pdata['province_remit_gap'] = round(province_remit_due - province_remitted, 1)

            # 义仓年度损耗（5%）
            if pdata.get('granary') and pdata.get('granary_stock', 0) > 0:
                pdata['granary_stock'] = round(pdata['granary_stock'] * 0.95, 1)

            pdata['year_end_review_pending'] = True
            gap = pdata['province_remit_gap']
            gap_note = f'缺口 {gap:.0f} 两' if gap > 0 else '足额上缴'
            EventLog.objects.create(
                game=game, season=season,
                event_type='prefecture_year_end',
                category='PREFECTURE',
                description=(
                    f'【腊月年终】行政开支 {admin_deducted:.0f} 两'
                    f'｜省级上缴 {province_remitted:.0f}/{province_remit_due:.0f} 两（{gap_note}）'
                    f'｜结余府库 {pdata["treasury"]:.0f} 两'
                ),
                data={
                    'admin_deducted': round(admin_deducted, 1),
                    'province_remit_due': pdata['province_remit_due'],
                    'province_remitted': province_remitted,
                    'province_remit_gap': gap,
                    'treasury_after': pdata['treasury'],
                },
            )

        # ── 十月：府试自动结算 ──
        exam_result = None
        if moy == 10:
            exam_result = cls._run_exam(pdata, season)
            if exam_result:
                passed = exam_result.get('passed_count', 0)
                top = exam_result.get('top_candidate', {})
                top_note = f'，第一名：{top.get("name", "")}（{top.get("score", 0):.0f}分）' if top else ''
                EventLog.objects.create(
                    game=game, season=season,
                    event_type='prefecture_exam_result',
                    category='PREFECTURE',
                    description=f'【府试放榜】本届录取 {passed} 人{top_note}',
                    data=exam_result,
                )

        # ── 汇报月：生成模糊汇报 ──
        if moy in REPORT_MONTHS:
            _t_report_start = _time.monotonic()
            cls._generate_reports(subordinates, season, pdata)
            logger.info("advance_month game=%s season=%s: 生成汇报 %.2fs",
                        game.id, season, _time.monotonic() - _t_report_start)

        # ── 重置核查次数（正月重置）──
        if moy == 1:
            pdata['inspection_used'] = {"tongpan": 0, "tuiguan": 0}

        # ── 县级司法月：AI 下辖州县先行处理本地卷宗 ──
        pending_cases = []
        judicial_processed = None
        if moy in {2, 5, 8, 11}:
            _t_judicial_start = _time.monotonic()
            judicial_processed = JudicialCaseflowService.auto_process_ai_counties(game, season)
            logger.info("advance_month game=%s season=%s: 司法自动处理 %.2fs",
                        game.id, season, _time.monotonic() - _t_judicial_start)

        # ── 府志月报快照（每月固定写入）──
        county_remit_summary = [
            {'unit_id': u.id,
             'county_name': u.unit_data.get('county_name', ''),
             'remit': u.unit_data.get('last_remit', 0.0)}
            for u in subordinates
        ]
        EventLog.objects.create(
            game=game, season=season,
            event_type='prefecture_month_settled',
            category='PREFECTURE',
            description=(
                f'【{month_name(season)}府政月报】'
                f'本月收缴 {remit_total:.0f} 两，府库 {pdata["treasury"]:.0f} 两'
            ),
            data={
                'remit_total': round(remit_total, 1),
                'treasury': pdata['treasury'],
                'treasury_collected': pdata.get('treasury_collected', 0),
                'county_remit': county_remit_summary,
                'granary_stock': pdata.get('granary_stock', 0),
            },
        )

        next_season = season + 1
        transition = AnnualReviewService.handle_prefecture_transition(
            game=game,
            processed_season=season,
            next_season=next_season,
        )
        if transition.get("personnel_result"):
            pdata["personnel_last_result"] = transition["personnel_result"]

        prefecture_unit.unit_data = pdata
        prefecture_unit.save(update_fields=['unit_data'])

        game.current_season = next_season
        game.save(update_fields=['current_season'])

        logger.info("advance_month game=%s season=%s: 总耗时 %.2fs",
                    game.id, season, _time.monotonic() - _t0)

        result = {
            "season": season,  # the month just processed
            "remit_total": round(remit_total, 1),
            "treasury": pdata['treasury'],
            "report_generated": moy in REPORT_MONTHS,
            "exam_result": exam_result,
            "year_end_review_pending": pdata.get('year_end_review_pending', False),
            "construction_completed": completed_construction,
            "pending_cases": pending_cases,
        }
        if judicial_processed is not None:
            result["judicial_processed"] = judicial_processed
        if transition.get("personnel_opened"):
            result["personnel_opened"] = True
            result["personnel_ready_count"] = transition.get("personnel_ready_count", 0)
        if transition.get("personnel_result"):
            result["personnel_result"] = transition["personnel_result"]
        return result

    @classmethod
    def _apply_cached_ai_results(cls, subordinates, cached: dict) -> dict:
        """将预推演缓存应用到下辖州县对象上。"""
        decision_results = {}
        for unit in subordinates:
            entry = cached.get(str(unit.id))
            if entry:
                unit.unit_data = copy.deepcopy(entry.get("unit_data") or unit.unit_data)
                decision_results[unit.id] = entry.get("events", [])
            else:
                decision_results[unit.id] = []
        return decision_results

    @classmethod
    def _compute_ai_decisions(cls, subordinates, season):
        """并行 AI 决策，返回 {unit.id: [event_str, ...]}"""
        results = {}

        def _decide(unit):
            from django.db import connection as _conn
            try:
                adapter = _SubordinateAdapter(unit)
                events = AIGovernorService.make_decisions(adapter, season)
                return unit.id, events
            except Exception as e:
                logger.warning("AI decision failed for subordinate unit %s: %s", unit.id, e)
                return unit.id, []
            finally:
                _conn.close()

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_decide, u): u for u in subordinates}
            try:
                for future in as_completed(futures, timeout=LLM_DEFAULT_TIMEOUT + 5):
                    uid, events = future.result()
                    results[uid] = events
            except FuturesTimeoutError:
                # 超时：用空决策填充未完成的县，确保结算继续进行
                missing = [u.id for u in futures.values() if u.id not in results]
                logger.warning(
                    "AI decisions timed out for season %s; %d unit(s) defaulting to no action: %s",
                    season, len(missing), missing,
                )
                for uid in missing:
                    results[uid] = []

        return results

    @classmethod
    def _process_ai_emergency_relief(cls, subordinates, pdata, season, game=None):
        """结算前：为缺粮紧急的AI县执行知府拨粮和邻县借粮。

        由 AIGovernorService._ai_set_emergency_grain_flags() 在 make_decisions 时写入请求标志，
        此处统一执行实际粮食转移（操作真实 unit_data，在物理结算之前完成）。
        拨粮顺序：优先从义仓拨粮，义仓不足时从府库拨银购粮。
        """
        from .constants import GRAIN_PER_LIANG
        prefecture_treasury = pdata.get('treasury', 0.0)

        for unit in subordinates:
            county = unit.unit_data
            EmergencyService.ensure_state(county)
            emergency = county.get('emergency', {})

            # ── 向知府申请拨粮 ──
            if county.pop('_ai_request_prefect_grain', False) and prefecture_treasury > 50:
                baseline = float(emergency.get('baseline_monthly_consumption', 0.0))
                reserve = float(county.get('peasant_grain_reserve', 0.0))
                shortage = max(0.0, baseline - reserve)

                if shortage > 10 and baseline > 0:
                    # 可拨上限：义仓全部可用量 + 府库最多10%且不超过300两等值
                    granary_stock = pdata.get('granary_stock', 0.0) if pdata.get('granary') else 0.0
                    max_treasury_cost = min(prefecture_treasury * 0.10, 300.0)
                    max_grant = granary_stock + max_treasury_cost * GRAIN_PER_LIANG
                    grant = round(min(shortage * 1.5, max_grant), 1)

                    # 义仓优先拨粮
                    grain_from_granary = min(grant, granary_stock)
                    grain_from_treasury = grant - grain_from_granary
                    cost = round(grain_from_treasury / GRAIN_PER_LIANG, 1)

                    if grant > 10 and cost <= prefecture_treasury - 50:
                        county['peasant_grain_reserve'] = round(reserve + grant, 1)
                        # 义仓扣减
                        if grain_from_granary > 0:
                            pdata['granary_stock'] = round(granary_stock - grain_from_granary, 1)
                        # 府库扣减
                        if cost > 0:
                            pdata['treasury'] = round(prefecture_treasury - cost, 1)
                            prefecture_treasury = pdata['treasury']
                        county_name = county.get('county_name', '本县')
                        governor_name = county.get('governor_meta', {}).get('name', '知县')
                        logger.info(
                            "AI county %s received prefecture grain grant %.0f jin "
                            "(granary=%.0f jin, cost=%.1f liang)",
                            county_name, grant, grain_from_granary, cost,
                        )
                        # 记入县本月事件，供月摘要使用
                        source_detail = ""
                        if grain_from_granary > 0:
                            source_detail += f"义仓{round(grain_from_granary)}斤"
                        if cost > 0:
                            source_detail += f"{'，' if source_detail else ''}府库{cost}两"
                        county.setdefault('_emergency_events', []).append(
                            f"【知府拨粮】{governor_name}上书告急，拨粮{round(grant)}斤（{source_detail}）以解燃眉之急"
                        )
                        # 府志记录
                        if game is not None:
                            EventLog.objects.create(
                                game=game,
                                season=season,
                                event_type='prefecture_relief_grant',
                                category='PREFECTURE',
                                description=(
                                    f"【应急拨粮】{county_name}{governor_name}上书告急，"
                                    f"拨粮{round(grant)}斤（{source_detail}）"
                                ),
                                data={
                                    'county_name': county_name,
                                    'grant': round(grant, 1),
                                    'grain_from_granary': round(grain_from_granary, 1),
                                    'silver_cost': cost,
                                    'pref_treasury_after': round(pdata.get('treasury', 0), 1),
                                    'pref_granary_after': round(pdata.get('granary_stock', 0), 1),
                                },
                            )

            # ── 从邻县借粮 ──
            if county.pop('_ai_borrow_neighbor_grain', False):
                EmergencyService.ensure_state(county)
                emergency = county.get('emergency', {})
                baseline = float(emergency.get('baseline_monthly_consumption', 0.0))
                reserve = float(county.get('peasant_grain_reserve', 0.0))
                shortage = max(0.0, baseline - reserve)

                if shortage > 50:
                    for donor_unit in subordinates:
                        if donor_unit.id == unit.id:
                            continue
                        donor = donor_unit.unit_data
                        EmergencyService.ensure_state(donor)
                        d_baseline = float(donor.get('emergency', {}).get('baseline_monthly_consumption', 0.0))
                        d_reserve = float(donor.get('peasant_grain_reserve', 0.0))
                        # 贷方保留自身1.5个月消耗量后的余粮
                        d_available = max(0.0, d_reserve - d_baseline * 1.5)
                        if d_available < 100:
                            continue

                        borrow = round(min(shortage * 1.2, d_available * 0.6), 1)
                        if borrow < 50:
                            continue

                        county['peasant_grain_reserve'] = round(reserve + borrow, 1)
                        donor['peasant_grain_reserve'] = round(d_reserve - borrow, 1)
                        shortage = max(0.0, baseline - county['peasant_grain_reserve'])
                        reserve = county['peasant_grain_reserve']

                        loan = {
                            "lender_unit_id": donor_unit.id,
                            "lender_name": donor.get('county_name', '邻县'),
                            "principal_grain": borrow,
                            "remaining_grain": borrow,
                            "installment_grain": round(borrow / 24.0, 1),
                            "term_months": 24,
                            "months_paid": 0,
                            "next_due_season": season + 1,
                            "overdue_months": 0,
                            "status": "ACTIVE",
                        }
                        county['emergency'].setdefault('neighbor_loans', []).append(loan)

                        county_name = county.get('county_name', '本县')
                        donor_name = donor.get('county_name', '邻县')
                        logger.info(
                            "AI county %s borrowed %.0f jin from %s",
                            county_name, borrow, donor_name,
                        )
                        county.setdefault('_emergency_events', []).append(
                            f"【邻县借粮】{county_name}向{donor_name}借得{round(borrow)}斤粮，约定24期归还"
                        )

                        if shortage <= 0:
                            break

    @classmethod
    def invalidate_precompute(cls, game) -> None:
        """清理当前月份的府级AI预推演缓存。"""
        NeighborPrecompute.objects.filter(game=game).delete()

    @classmethod
    def precompute_ai_decisions(cls, game_id: int, season: int) -> None:
        """后台预推演下辖州县 AI 施政决策。"""
        from django.db import connection as outer_conn

        try:
            precompute, created = NeighborPrecompute.objects.get_or_create(
                game_id=game_id,
                defaults={'season': season, 'status': 'computing', 'results': {}},
            )
            if not created and precompute.status == 'computing' and precompute.season == season:
                logger.info("Prefecture precompute already running for game %s season %s, skipping",
                            game_id, season)
                return
            if not created and precompute.status == 'done' and precompute.season == season:
                logger.info("Prefecture precompute already done for game %s season %s, skipping",
                            game_id, season)
                return

            if not created:
                precompute.season = season
                precompute.status = 'computing'
                precompute.results = {}
                precompute.save(update_fields=['season', 'status', 'results', 'updated_at'])

            game = GameState.objects.select_related('player_unit').get(id=game_id)
            if game.player_role != 'PREFECT' or not game.player_unit_id:
                precompute.status = 'done'
                precompute.save(update_fields=['status', 'updated_at'])
                return

            subordinates = list(
                AdminUnit.objects.filter(
                    game=game, unit_type='COUNTY', parent=game.player_unit,
                )
            )
            if not subordinates:
                precompute.status = 'done'
                precompute.save(update_fields=['status', 'updated_at'])
                return

            subordinate_copies = []
            for unit in subordinates:
                unit_copy = copy.copy(unit)
                unit_copy.unit_data = copy.deepcopy(unit.unit_data)
                subordinate_copies.append((unit.id, unit_copy))

            logger.info("Starting prefecture precompute for game %s season %s (%d counties)",
                        game_id, season, len(subordinate_copies))

            results = {}

            def _compute_one(unit_id, unit_copy):
                from django.db import connection as thread_conn
                try:
                    adapter = _SubordinateAdapter(unit_copy)
                    events = AIGovernorService.make_decisions(adapter, season)
                    return unit_id, {
                        "events": events,
                        "unit_data": unit_copy.unit_data,
                        "last_reasoning": unit_copy.unit_data.get('_last_reasoning', ''),
                        "county_name": unit_copy.unit_data.get('county_name', ''),
                        "governor_name": unit_copy.unit_data.get('governor_profile', {}).get('name', ''),
                    }
                except Exception as e:
                    logger.warning(
                        "Prefecture precompute failed for county %s (%s): %s",
                        unit_copy.unit_data.get('county_name', ''), unit_id, e,
                    )
                    return unit_id, None
                finally:
                    thread_conn.close()

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(_compute_one, unit_id, unit_copy): unit_id
                    for unit_id, unit_copy in subordinate_copies
                }
                for future in as_completed(futures):
                    unit_id, result = future.result()
                    if result is not None:
                        results[str(unit_id)] = result
                    precompute.results = results
                    precompute.save(update_fields=['results', 'updated_at'])

            if not results:
                NeighborPrecompute.objects.filter(game_id=game_id).delete()
                logger.warning("Prefecture precompute produced no usable county results for game %s season %s",
                               game_id, season)
                return

            precompute.status = 'done'
            precompute.save(update_fields=['status', 'updated_at'])
            logger.info("Prefecture precompute done for game %s season %s: %d/%d succeeded",
                        game_id, season, len(results), len(subordinate_copies))

        except Exception:
            logger.warning("Prefecture precompute failed", exc_info=True)
            # 删除失败缓存，保证正式推进时会回退到同步计算，而不是消费空结果。
            NeighborPrecompute.objects.filter(game_id=game_id).delete()
        finally:
            outer_conn.close()

    @classmethod
    def get_precompute_status(cls, game_id: int, season: int) -> dict:
        """查询府级AI预推演进度。"""
        precompute = NeighborPrecompute.objects.filter(game_id=game_id).first()
        if not precompute or precompute.season != season:
            return {"status": "idle", "completed": [], "completed_count": 0}

        completed = []
        for unit_id, entry in precompute.results.items():
            completed.append({
                "unit_id": int(unit_id),
                "county_name": entry.get("county_name", ""),
                "governor_name": entry.get("governor_name", ""),
            })

        return {
            "status": precompute.status if precompute.status in ('computing', 'done') else 'idle',
            "completed": completed,
            "completed_count": len(completed),
        }

    # ==================== 汇报生成 ====================

    @classmethod
    def _generate_reports(cls, subordinates, season, pdata):
        """
        汇报月为每个下辖县生成一份模糊汇报，存入 county unit_data['subordinate_reports']。
        失真程度由知县类型（CORRUPT 多报1–2档）决定。
        """
        for unit in subordinates:
            cd = unit.unit_data
            archetype = cd.get('governor_profile', {}).get('archetype', 'MIDDLING')
            affinity = cd.get('prefect_affinity', 50)

            # 好感度决定基础偏差（低好感→多报虚假好消息）；CORRUPT 额外+1档
            if affinity >= 70:
                base_bias = 0
            elif affinity >= 50:
                base_bias = 1
            elif affinity >= 30:
                base_bias = 1
            else:
                base_bias = 2
            corrupt_bonus = 1 if archetype == 'CORRUPT' else 0
            bias = base_bias + corrupt_bonus

            def _fuzz(raw_score, extra_bias=0):
                """将真实分值加噪声后转为档位标签"""
                noise = random.randint(0, bias + extra_bias)
                fuzzed = min(99, raw_score + noise * 12)   # 每档约12分
                return score_to_tier(fuzzed)

            total_pop = sum(v.get('population', 0) for v in cd.get('villages', []))
            total_farmland = sum(v.get('farmland', 0) for v in cd.get('villages', []))

            # 人口档位：以 5000 人为100分基准粗估
            pop_score = min(99, int(total_pop / 60))

            indicators = {
                "民心":   _fuzz(cd.get('morale', 50)),
                "治安":   _fuzz(cd.get('security', 50)),
                "商业":   _fuzz(cd.get('commercial', 50)),
                "文教":   _fuzz(cd.get('education', 50)),
                "人口规模": _fuzz(pop_score),
                "县库状况": _fuzz(min(99, int(cd.get('treasury', 0) / 12))),
            }
            report_entry = {
                "month": season,
                "indicators": indicators,
                "trend": cls._calc_trend(cd, indicators),
                "actions": cd.get('_last_ai_actions', '无特别行动'),
                "notes": "",
            }

            # 低好感度或 CORRUPT 知县有概率隐瞒负面事项
            hide_prob = 0.0
            if archetype == 'CORRUPT':
                hide_prob = 0.6
            elif affinity < 40:
                hide_prob = 0.3
            if random.random() < hide_prob:
                report_entry['notes'] = "（无特记事项）"
            else:
                report_entry['notes'] = cd.get('_last_report_note', '')

            reports = cd.get('subordinate_reports', [])
            reports.append(report_entry)
            cd['subordinate_reports'] = reports[-8:]  # 保留最近8条

            unit.unit_data = cd
            unit.save(update_fields=['unit_data'])

    @staticmethod
    def _calc_trend(cd, cur_indicators):
        """与上次汇报相比各指标趋势（↑→↓）"""
        reports = cd.get('subordinate_reports', [])
        if not reports:
            return {k: '→' for k in cur_indicators}
        prev = reports[-1].get('indicators', {})

        def _arrow(key):
            prev_label = prev.get(key)
            cur_label = cur_indicators.get(key)
            if not prev_label or not cur_label:
                return '→'
            prev_idx = next((i for i, (_, _, l) in enumerate(TIER_THRESHOLDS) if l == prev_label), 4)
            cur_idx = next((i for i, (_, _, l) in enumerate(TIER_THRESHOLDS) if l == cur_label), 4)
            if cur_idx > prev_idx:
                return '↑'
            if cur_idx < prev_idx:
                return '↓'
            return '→'

        return {k: _arrow(k) for k in cur_indicators}

    # ==================== 信息验证 ====================

    @classmethod
    def inspect_county(cls, game, unit_id: int, inspect_type: str) -> dict:
        """
        通判核账（tongpan）或推官巡查（tuiguan）：返回真实精确数值。
        每年每类最多3次（消耗1次），交通基建等级决定每次可覆盖的县数：
          road_level 0 → 1县，road_level 1 → 2县，road_level 2 → 3县。
        目标县固定为 unit_id，额外县由系统自动选取（交通基建加成）。
        返回 {"results": [...], "road_level": N, "bonus_counties": M}。
        """
        pdata = game.player_unit.unit_data
        used = pdata.get('inspection_used', {"tongpan": 0, "tuiguan": 0})

        if used.get(inspect_type, 0) >= 3:
            return {"error": f"本年度{inspect_type}核查次数已用完（最多3次）"}

        target = AdminUnit.objects.filter(id=unit_id, game=game, unit_type='COUNTY').first()
        if not target:
            return {"error": "县不存在"}

        # 交通基建决定本次可覆盖县数（目标县 + 额外县）
        road_level = pdata.get("road_level", 0)
        max_counties = 1 + road_level  # 0→1, 1→2, 2→3

        # 构建待核查列表：目标县在前，余量由其他下辖县按 unit_id 顺序填充
        prefecture_unit = game.player_unit
        all_subordinates = list(
            AdminUnit.objects.filter(
                game=game, unit_type='COUNTY', parent=prefecture_unit
            ).exclude(id=unit_id).order_by('id')
        )
        targets = [target] + all_subordinates[:max_counties - 1]

        def _extract(unit, itype):
            cd = unit.unit_data
            county_name = cd.get('county_name') or cd.get('prefecture_name') or f"行政单元#{unit.id}"
            total_pop = sum(v.get('population', 0) for v in cd.get('villages', []))
            if itype == 'tongpan':
                return {
                    "type": "通判核账",
                    "county_name": county_name,
                    "unit_id": unit.id,
                    "treasury": round(cd.get('treasury', 0), 1),
                    "last_remit": round(cd.get('last_remit', 0), 1),
                    "tax_rate": cd.get('tax_rate', 0.12),
                    "commercial_tax_rate": cd.get('commercial_tax_rate', 0.03),
                }
            else:
                return {
                    "type": "推官巡查",
                    "county_name": county_name,
                    "unit_id": unit.id,
                    "security": round(cd.get('security', 50), 1),
                    "morale": round(cd.get('morale', 50), 1),
                    "population": total_pop,
                    "education": round(cd.get('education', 50), 1),
                }

        results = [_extract(u, inspect_type) for u in targets]

        used[inspect_type] = used.get(inspect_type, 0) + 1
        pdata['inspection_used'] = used
        game.player_unit.unit_data = pdata
        game.player_unit.save(update_fields=['unit_data'])

        return {
            "results": results,
            "road_level": road_level,
            "bonus_counties": len(targets) - 1,
        }

    # ==================== 配额分配 ====================

    @classmethod
    def distribute_quota(cls, game, assignments: dict) -> dict:
        """
        设定各下辖县的年度上缴目标。assignments = {unit_id: amount}。
        仅在正月（month_of_year(current_season) == 1）生效。
        同步将配额写入各县 unit_data['annual_quota']，供 AI 知县 LLM 决策使用。
        """
        moy = month_of_year(game.current_season)
        if moy != 1:
            return {"error": "配额分配仅在正月执行"}

        pdata = game.player_unit.unit_data
        annual_quota = pdata.get('annual_quota', 0)
        total_assigned = sum(assignments.values())

        warnings = []
        if total_assigned < annual_quota:
            warnings.append(f"总分配 {total_assigned} 两低于省级定额 {annual_quota} 两，差额需由府库垫付")
        if total_assigned > annual_quota * 1.3:
            warnings.append("总分配超出省级定额30%，下属可能向巡抚申诉")

        pdata['quota_assignments'] = {str(k): v for k, v in assignments.items()}
        game.player_unit.unit_data = pdata
        game.player_unit.save(update_fields=['unit_data'])

        # 同步写入各县 annual_quota，让 AI 知县感知到本年配额
        subordinates = list(
            AdminUnit.objects.filter(game=game, unit_type='COUNTY', parent=game.player_unit)
        )
        for unit in subordinates:
            assigned = assignments.get(unit.id, assignments.get(str(unit.id)))
            if assigned is None:
                continue
            cd = unit.unit_data
            # 按历史配额结构估算农/役比例
            old_quota = cd.get('annual_quota') or {}
            old_total = old_quota.get('total', 0) or assigned
            agri_ratio = old_quota.get('agricultural', old_total * 0.65) / old_total if old_total else 0.65
            corvee_ratio = 1.0 - agri_ratio
            cd['annual_quota'] = {
                'total': round(assigned, 1),
                'agricultural': round(assigned * agri_ratio, 1),
                'corvee': round(assigned * corvee_ratio, 1),
            }
            # 写入系统指令，确保 LLM 决策阶段能感知到
            system_directive = {
                "season": game.current_season,
                "directive": f"本年府级税赋配额已下达，尔县应缴总额为{round(assigned)}两（含农赋与徭役折银），务请依时足额完纳。",
            }
            directives = cd.get('pending_directives', [])
            directives.append(system_directive)
            cd['pending_directives'] = directives[-3:]
            unit.unit_data = cd
            unit.save(update_fields=['unit_data'])

        return {"assigned": total_assigned, "annual_quota": annual_quota, "warnings": warnings}

    # ==================== 资源调拨 ====================

    @classmethod
    def relief_county(cls, game, unit_id: int, amount: float) -> dict:
        """
        玩家主动向指定下辖县拨济，单位为两（银两）。
        后端优先动用府级义仓（斤→折银等价），不足部分再从府库扣银购粮。
        不能凭空产出：义仓不足 + 府库不足 → 报错。

        义仓路径：1两 = GRAIN_PER_LIANG 斤，从 granary_stock 拨粮给县 peasant_grain_reserve。
        银两路径：直接拨银给县 treasury（县可自行购粮）。
        """
        from .constants import GRAIN_PER_LIANG
        if amount < 10:
            return {"error": "单次拨款不得低于10两"}

        pdata = game.player_unit.unit_data
        treasury = pdata.get('treasury', 0.0)

        unit = AdminUnit.objects.filter(
            id=unit_id, game=game, unit_type='COUNTY', parent=game.player_unit,
        ).first()
        if not unit:
            return {"error": "县不存在"}

        # 请求粮食总量（斤）
        grain_needed = amount * GRAIN_PER_LIANG

        # ── Step 1：优先从义仓拨粮 ──
        granary_stock = pdata.get('granary_stock', 0.0) if pdata.get('granary') else 0.0
        grain_from_granary = min(grain_needed, granary_stock)
        silver_from_granary = grain_from_granary / GRAIN_PER_LIANG  # 义仓不扣银，仅记录等值

        # ── Step 2：剩余缺口从府库购粮（扣银） ──
        remaining_grain = grain_needed - grain_from_granary
        silver_needed = round(remaining_grain / GRAIN_PER_LIANG, 1)

        # 验证府库是否足够（义仓已覆盖全部 → silver_needed=0，跳过验证）
        if silver_needed > 0:
            max_relief = round(treasury * 0.30, 1)
            if silver_needed > max_relief:
                return {"error": (
                    f"义仓存粮不足（仅{round(granary_stock)}斤），剩余缺口需从府库支银"
                    f"{silver_needed}两，超过府库余额30%上限（{max_relief}两）"
                )}
            if treasury < silver_needed + 50:
                return {"error": (
                    f"府库余额不足（现有{round(treasury, 1)}两），至少需{silver_needed + 50}两"
                )}

        # ── 执行扣减 ──
        cd = unit.unit_data
        county_name = cd.get('county_name', '本县')

        # 义仓拨粮
        if grain_from_granary > 0:
            pdata['granary_stock'] = round(granary_stock - grain_from_granary, 1)
            cd['peasant_grain_reserve'] = round(cd.get('peasant_grain_reserve', 0) + grain_from_granary, 1)

        # 府库拨银（剩余缺口）
        if silver_needed > 0:
            pdata['treasury'] = round(treasury - silver_needed, 1)
            cd['treasury'] = round(cd.get('treasury', 0) + silver_needed, 1)

        # ── 好感度奖励 ──
        if amount >= 200:
            affinity_delta = 6
        elif amount >= 100:
            affinity_delta = 4
        else:
            affinity_delta = 3
        old_affinity = cd.get('prefect_affinity', 50)
        cd['prefect_affinity'] = max(0, min(100, old_affinity + affinity_delta))

        # 记录事件
        event_desc = f"【知府调济】"
        if grain_from_granary > 0:
            event_desc += f"义仓拨粮{round(grain_from_granary)}斤"
        if silver_needed > 0:
            event_desc += f"{'，' if grain_from_granary > 0 else ''}府库拨银{silver_needed}两购粮"
        cd.setdefault('_emergency_events', []).append(event_desc)

        unit.unit_data = cd
        unit.save(update_fields=['unit_data'])
        game.player_unit.unit_data = pdata
        game.player_unit.save(update_fields=['unit_data'])

        gp = cd.get('governor_profile', {})
        return {
            "unit_id": unit_id,
            "county_name": county_name,
            "governor_name": gp.get('name', ''),
            "amount_requested": round(amount, 1),
            "grain_from_granary": round(grain_from_granary, 1),
            "grain_from_treasury": round(remaining_grain, 1),
            "silver_spent": round(silver_needed, 1),
            "granary_stock_after": round(pdata.get('granary_stock', 0), 1),
            "county_grain_reserve_after": round(cd.get('peasant_grain_reserve', 0), 1),
            "county_treasury_after": round(cd.get('treasury', 0), 1),
            "prefecture_treasury_after": round(pdata.get('treasury', 0), 1),
            "affinity_after": cd['prefect_affinity'],
        }

    # ==================== 约谈施压 ====================

    # 约谈强度 → 中文标签 / affinity 基础惩罚
    _PRESSURE_LABELS = {"light": "温和提醒", "moderate": "正式约谈", "heavy": "严厉训斥"}
    _PRESSURE_AFFINITY_BASE = {"light": -1, "moderate": -3, "heavy": -6}

    @classmethod
    def confront_county(cls, game, unit_id: int, pressure: str, message: str) -> dict:
        """
        玩家约谈下属知县。
        - 好感度 >= 65：诚实回应，承诺改进；affinity 轻微下降
        - 好感度 30-64：敷衍或申辩；affinity 中幅下降
        - 好感度 < 30：阳奉阴违；affinity 大幅下降，写入"消极"指令标记
        LLM 生成知县回应文本，失败时规则兜底。
        """
        unit = AdminUnit.objects.filter(
            id=unit_id, game=game, unit_type='COUNTY', parent=game.player_unit,
        ).first()
        if not unit:
            return {"error": "县不存在"}

        cd = unit.unit_data
        gp = cd.get('governor_profile', {})
        affinity = cd.get('prefect_affinity', 50)
        archetype = gp.get('archetype', 'MIDDLING')
        style = gp.get('style', 'baoshou')

        pressure_label = cls._PRESSURE_LABELS.get(pressure, '正式约谈')
        base_delta = cls._PRESSURE_AFFINITY_BASE.get(pressure, -3)

        # 决定结果类型
        if affinity >= 65:
            outcome_default = "承诺改进"
        elif affinity >= 30:
            outcome_default = "敷衍了事" if archetype == 'CORRUPT' else "据理申辩"
        else:
            outcome_default = "阳奉阴违"

        # 尝试 LLM 生成回应
        response_text = None
        outcome = outcome_default
        affinity_hint = base_delta
        try:
            from llm.client import LLMClient
            from llm.prompts import PromptRegistry as PR
            _archetype_labels = {'VIRTUOUS': '清廉能吏', 'MIDDLING': '普通官员', 'CORRUPT': '贪腐官员'}
            _style_labels = {
                'minben': '民本派', 'zhengji': '政绩派',
                'baoshou': '保守派', 'jinjin': '进取派', 'yuanhua': '圆滑派',
            }
            total_land = sum(v.get('farmland', 0) for v in cd.get('villages', []))
            total_pop = sum(v.get('population', 0) for v in cd.get('villages', []))
            aq = cd.get('annual_quota', {})
            aq_total = aq.get('total', 0) if isinstance(aq, dict) else 0
            fy = cd.get('fiscal_year', {})
            fy_done = fy.get('agri_remitted', 0) + fy.get('commercial_tax', 0) + fy.get('corvee_tax', 0)
            quota_pct = (fy_done / aq_total * 100) if aq_total else 100.0

            sys_p, usr_p = PR.render(
                'confront_response',
                magistrate_name=gp.get('name', '知县'),
                county_name=cd.get('county_name', '本县'),
                archetype_label=_archetype_labels.get(archetype, '普通官员'),
                style_label=_style_labels.get(style, '保守派'),
                affinity=affinity,
                morale_label=score_to_tier(cd.get('morale', 50)),
                security_label=score_to_tier(cd.get('security', 50)),
                quota_pct=quota_pct,
                pressure_label=pressure_label,
                message=message or f"本府对尔县近况甚为关切，请如实汇报。",
            )
            client = LLMClient(timeout=10, max_retries=1)
            result = client.chat_json(
                [{'role': 'system', 'content': sys_p}, {'role': 'user', 'content': usr_p}],
                temperature=0.75, max_tokens=350,
            )
            if isinstance(result, dict) and result.get('response_text'):
                response_text = result['response_text']
                outcome = result.get('outcome', outcome_default)
                affinity_hint = max(-12, min(5, int(result.get('affinity_hint', base_delta))))
        except Exception as e:
            logger.warning("约谈 LLM 失败（静默降级）: %s", e)

        # 规则兜底
        if not response_text:
            if outcome_default == "承诺改进":
                response_text = (
                    f"下官承蒙知府垂询，惶恐之至。{cd.get('county_name', '本县')}近况确有不足，"
                    "下官当竭力改进，不负大人厚望，请大人宽限时日。"
                )
            elif outcome_default == "据理申辩":
                response_text = (
                    f"大人明察。下官自知任内多有不易，然实情如此——"
                    "本县民情复杂，资源有限，下官已尽力而为，望大人体察下情。"
                )
            elif outcome_default == "敷衍了事":
                response_text = (
                    f"下官谨遵大人训示，定当改进。诸事皆已在安排之中，请大人放心。"
                )
            else:  # 阳奉阴违
                response_text = (
                    f"下官谨记大人教诲，自当照章办理。"
                )
            affinity_hint = base_delta

        # 更新好感度
        new_affinity = max(0, min(100, affinity + affinity_hint))
        cd['prefect_affinity'] = new_affinity

        # 若承诺改进，写入 pending_directive 强化执行
        if outcome == "承诺改进" and message:
            directive = {
                "season": game.current_season,
                "directive": f"【约谈后承诺】{message[:80]}",
            }
            directives = cd.get('pending_directives', [])
            directives.append(directive)
            cd['pending_directives'] = directives[-3:]

        # 若阳奉阴违，写入消极标记
        if outcome == "阳奉阴违":
            cd['_passive_resistance'] = True

        unit.unit_data = cd
        unit.save(update_fields=['unit_data'])

        return {
            "unit_id": unit_id,
            "county_name": cd.get('county_name', ''),
            "governor_name": gp.get('name', ''),
            "pressure": pressure_label,
            "outcome": outcome,
            "response_text": response_text,
            "affinity_before": affinity,
            "affinity_after": new_affinity,
        }

    # ==================== 弹劾免职 ====================

    @classmethod
    def impeach_county(cls, game, unit_id: int, reason: str) -> dict:
        """
        玩家弹劾下属知县，需省级批准。
        批准概率由 inspector_favor（按察使观感）和是否有客观证据决定。
        通过后：替换为新随机知县，好感度重置为 MIDDLING 初始值。
        府库扣 300 两（差旅/接任费用）。
        """
        pdata = game.player_unit.unit_data

        unit = AdminUnit.objects.filter(
            id=unit_id, game=game, unit_type='COUNTY', parent=game.player_unit,
        ).first()
        if not unit:
            return {"error": "县不存在"}

        cd = unit.unit_data
        gp = cd.get('governor_profile', {})
        old_name = gp.get('name', '该知县')
        county_name = cd.get('county_name', '本县')
        archetype = gp.get('archetype', 'MIDDLING')

        # 检查是否有可用年度评议（差评为客观证据）
        from .annual_review import AnnualReviewService
        current_year = year_of(game.current_season)
        cycle = AnnualReviewService._find_cycle(cd, current_year) or AnnualReviewService._find_cycle(cd, current_year - 1)
        has_poor_review = False
        if cycle:
            pr = cycle.get('prefect_review', {})
            has_poor_review = pr.get('grade') == '差'

        # 批准概率
        inspector_favor = pdata.get('inspector_favor', 50)
        base_prob = inspector_favor / 100.0   # 0~1
        if has_poor_review:
            base_prob += 0.20
        if archetype == 'CORRUPT':
            base_prob += 0.15
        base_prob = min(0.95, max(0.10, base_prob))

        approved = random.random() < base_prob

        if not approved:
            # 弹劾被驳回：inspector_favor 略降，关系有损
            pdata['inspector_favor'] = round(_clamp_meter(inspector_favor - 5), 1)
            game.player_unit.unit_data = pdata
            game.player_unit.save(update_fields=['unit_data'])
            return {
                "approved": False,
                "unit_id": unit_id,
                "county_name": county_name,
                "governor_name": old_name,
                "reason": f"按察使审核后认为证据不足，驳回弹劾。inspector_favor 已降至 {pdata['inspector_favor']}。",
            }

        # 弹劾通过：扣府库，替换知县
        cost = 300
        if pdata.get('treasury', 0) < cost:
            return {"error": f"府库不足（需{cost}两差旅费用）"}
        pdata['treasury'] = round(pdata['treasury'] - cost, 1)

        # 生成新知县
        c_type = cd.get('county_type', 'balanced_inland')
        new_archetype = random.choices(
            ['VIRTUOUS', 'MIDDLING', 'CORRUPT'], weights=[0.35, 0.50, 0.15]
        )[0]
        new_profile = generate_governor_profile(new_archetype)
        new_style = derive_governor_style(new_profile)
        used_names = {gp.get('name', '')}
        new_name = '某知县'
        for _ in range(20):
            candidate = (
                random.choice(list(GOVERNOR_SURNAMES))
                + random.choice(list(GOVERNOR_GIVEN_NAMES))
            )
            if candidate not in used_names:
                new_name = candidate
                break

        new_profile['name'] = new_name
        new_profile['style'] = new_style
        new_profile['archetype'] = new_archetype
        new_profile['bio'] = f"{new_name}，新任{county_name}知县，接替{old_name}。"

        cd['governor_profile'] = new_profile
        cd['prefect_affinity'] = random.randint(45, 60)   # 新人中立好感度
        # 适应期：能力暂降，3个月后自行恢复
        cd['_new_magistrate_adaptation_months'] = 3

        unit.unit_data = cd
        unit.save(update_fields=['unit_data'])

        # 按察使观感小幅提升（配合弹劾）
        pdata['inspector_favor'] = round(_clamp_meter(inspector_favor + 3), 1)
        game.player_unit.unit_data = pdata
        game.player_unit.save(update_fields=['unit_data'])

        return {
            "approved": True,
            "unit_id": unit_id,
            "county_name": county_name,
            "old_governor": old_name,
            "new_governor": new_name,
            "new_archetype": new_archetype,
            "cost": cost,
            "prefecture_treasury_after": pdata['treasury'],
            "message": f"{old_name}已被免职，{new_name}接任{county_name}知县。新任知县需3个月适应期。",
        }

    # ==================== 查询接口 ====================

    @classmethod
    def get_prefecture_overview(cls, game) -> dict:
        """返回府情总览数据"""
        pdata = game.player_unit.unit_data
        personnel = AnnualReviewService.get_prefecture_personnel_payload(game)
        judicial_payload = JudicialCaseflowService.get_prefecture_payload(game)
        pending_judicial_count = len(judicial_payload.get('pending_cases', []))
        subordinates = list(
            AdminUnit.objects.filter(game=game, unit_type='COUNTY', parent=game.player_unit)
        )

        # 司法统计（批量查询，避免 N+1）
        subordinate_ids = [u.id for u in subordinates]
        judicial_stats_map = JudicialCaseflowService.get_county_judicial_stats(game, subordinate_ids)

        # 汇总最新汇报数据（取各县最后一次汇报的指标）
        county_summaries = []
        for unit in subordinates:
            cd = unit.unit_data
            reports = cd.get('subordinate_reports', [])
            latest = reports[-1] if reports else None
            gp = cd.get('governor_profile', {})
            disaster = cd.get('disaster_this_year') or {}
            county_summaries.append({
                "unit_id": unit.id,
                "county_name": cd.get('county_name', ''),
                "governor_name": gp.get('name', ''),
                "governor_style": gp.get('style', ''),
                "governor_archetype": gp.get('archetype', 'MIDDLING'),
                "latest_report": latest,
                "quota": pdata.get('quota_assignments', {}).get(str(unit.id), 0),
                "has_disaster": bool(disaster),
                "disaster_type": disaster.get('type'),
                "judicial_stats": judicial_stats_map.get(unit.id, {}),
            })

        return {
            "game_id": game.id,
            "prefecture_name": pdata.get('prefecture_name', ''),
            "prefecture_type_name": pdata.get('prefecture_type_name', ''),
            "treasury": pdata.get('treasury', 0),
            "treasury_collected": pdata.get('treasury_collected', 0),
            "annual_quota": pdata.get('annual_quota', 0),
            "school_level": pdata.get('school_level', 0),
            "road_level": pdata.get('road_level', 0),
            "river_work_level": pdata.get('river_work_level', 0),
            "judicial_prestige": pdata.get('judicial_prestige', 50),
            "inspector_favor": pdata.get('inspector_favor', 50),
            "current_season": game.current_season,
            "year_end_review_pending": pdata.get('year_end_review_pending', False),
            "exam_pending": pdata.get('exam_pending', False),
            "pending_judicial_count": pending_judicial_count,
            "todo_items": cls._build_overview_todos(pdata, subordinates, pending_judicial_count),
            "counties": county_summaries,
            "personnel_available": personnel.get("available", False),
            "personnel_phase": personnel.get("phase"),
            "personnel_summary": personnel.get("summary", {}),
            "province_annual_focus": pdata.get('province_annual_focus'),
        }

    @classmethod
    def _build_overview_todos(cls, pdata: dict, subordinates: list, pending_judicial_count: int) -> list:
        """汇总府情总览的待办事项提醒。"""
        todo_items = []

        # 正月：省级施政重点下达提醒（配额分配前）
        focus = pdata.get('province_annual_focus') or {}
        if focus.get('focuses') and not pdata.get('quota_assignments'):
            todo_items.append({
                "type": "province_focus",
                "severity": "high",
                "title": f"省级施政重点已下达：{'、'.join(focus['focuses'])}，请尽快分配配额",
                "count": len(focus['focuses']),
                "county_names": [],
                "target_tab": "pref-tab-overview",
            })

        if pdata.get('year_end_review_pending'):
            todo_items.append({
                "type": "year_end_review",
                "severity": "high",
                "title": "腊月评议待完成",
                "count": 1,
                "county_names": [],
                "target_tab": "pref-tab-overview",
            })

        if pending_judicial_count:
            todo_items.append({
                "type": "judicial_case",
                "severity": "high",
                "title": f"待审议司法案件 {pending_judicial_count} 件",
                "count": pending_judicial_count,
                "county_names": [],
                "target_tab": "pref-tab-judicial",
            })

        disaster_counties = []
        disaster_types = set()
        for unit in subordinates:
            disaster = unit.unit_data.get('disaster_this_year') or {}
            if not disaster:
                continue
            county_name = unit.unit_data.get('county_name', '')
            if county_name:
                disaster_counties.append(county_name)
            dtype = DISASTER_TYPE_LABELS.get(disaster.get('type'))
            if dtype:
                disaster_types.add(dtype)

        if disaster_counties:
            disaster_summary = "、".join(disaster_counties[:3])
            if len(disaster_counties) > 3:
                disaster_summary += "等"
            type_summary = "、".join(sorted(disaster_types))
            title = f"{len(disaster_counties)} 个下辖县州发生自然灾害"
            if type_summary:
                title += f"（{type_summary}）"
            todo_items.append({
                "type": "county_disaster",
                "severity": "medium",
                "title": title,
                "count": len(disaster_counties),
                "county_names": disaster_counties,
                "summary": disaster_summary,
                "target_tab": "pref-tab-counties",
            })

        return todo_items

    # 施政重点选项（key → 关联指标）
    _FOCUS_OPTIONS = {
        "农业增产": {"metric": "morale",    "label": "全府均民心须不低于勉强（38）"},
        "商业振兴": {"metric": "commercial", "label": "全府均商业须不低于及格（50）"},
        "治安整顿": {"metric": "security",  "label": "全府均治安须不低于及格（50）"},
        "文教兴盛": {"metric": "education", "label": "全府均文教须不低于勉强（38）"},
    }
    _FOCUS_THRESHOLDS = {
        "农业增产": 38,
        "商业振兴": 50,
        "治安整顿": 50,
        "文教兴盛": 38,
    }

    @classmethod
    def _generate_province_focus(cls, pdata: dict, season: int) -> dict:
        """正月随机生成1~2个省级施政重点，重置完成状态。"""
        keys = list(cls._FOCUS_OPTIONS.keys())
        count = random.choices([1, 2], weights=[0.3, 0.7])[0]
        chosen = random.sample(keys, count)
        return {
            "focuses": chosen,
            "labels": [cls._FOCUS_OPTIONS[k]["label"] for k in chosen],
            "year": year_of(season),
            "completed": [],   # 年底填入已完成项
        }

    @classmethod
    def evaluate_province_focus(cls, game, subordinates: list) -> dict:
        """
        年底（腊月/正月入口）评估省级施政重点完成情况。
        返回 {completed: [...], missed: [...], bonus_score: 0-20}。
        """
        pdata = game.player_unit.unit_data
        focus_data = pdata.get('province_annual_focus') or {}
        focuses = focus_data.get('focuses', [])
        if not focuses:
            return {"completed": [], "missed": [], "bonus_score": 0}

        # 各指标全府加权均值
        metric_map = {
            "农业增产": "morale",
            "商业振兴": "commercial",
            "治安整顿": "security",
            "文教兴盛": "education",
        }
        totals: dict = {}
        weights: dict = {}
        for unit in subordinates:
            cd = unit.unit_data
            pop = sum(v.get('population', 0) for v in cd.get('villages', []))
            w = max(pop, 1)
            for f, metric in metric_map.items():
                totals[f] = totals.get(f, 0.0) + cd.get(metric, 50) * w
                weights[f] = weights.get(f, 0.0) + w

        completed = []
        missed = []
        for f in focuses:
            avg = totals.get(f, 0) / weights.get(f, 1)
            threshold = cls._FOCUS_THRESHOLDS.get(f, 38)
            if avg >= threshold:
                completed.append(f)
            else:
                missed.append(f)

        bonus_score = len(completed) * 10
        focus_data['completed'] = completed
        pdata['province_annual_focus'] = focus_data
        return {"completed": completed, "missed": missed, "bonus_score": bonus_score}

    @classmethod
    def get_county_detail(cls, game, unit_id: int) -> dict:
        """返回单个下辖县的详细信息（含历史汇报，仍为档位格式）"""
        unit = AdminUnit.objects.filter(id=unit_id, game=game, unit_type='COUNTY').first()
        if not unit:
            return None
        cd = unit.unit_data
        gp = cd.get('governor_profile', {})
        return {
            "unit_id": unit.id,
            "county_name": cd.get('county_name', ''),
            "county_type": cd.get('county_type', ''),
            "governor": {
                "name": gp.get('name', ''),
                "style": gp.get('style', ''),
                "archetype": gp.get('archetype', 'MIDDLING'),
                "bio": gp.get('bio', ''),
            },
            "reports": cd.get('subordinate_reports', []),
            "quota": game.player_unit.unit_data.get('quota_assignments', {}).get(str(unit_id), 0),
            "infrastructure": {
                "irrigation_level":    cd.get('irrigation_level', 0),
                "medical_level":       cd.get('medical_level', 0),
                "school_level":        cd.get('school_level', 0),
                "bailiff_level":       cd.get('bailiff_level', 0),
                "tax_rate":            cd.get('tax_rate', 0.12),
                "commercial_tax_rate": cd.get('commercial_tax_rate', 0.03),
                "has_granary":         cd.get('has_granary', False),
                "active_investments":  [
                    {
                        "description":       inv.get('description', ''),
                        "completion_season": inv.get('completion_season'),
                        "started_season":    inv.get('started_season'),
                    }
                    for inv in cd.get('active_investments', [])
                ],
            },
            "annual_review": AnnualReviewService._serialize_cycle(
                AnnualReviewService._find_cycle(
                    cd, AnnualReviewService.display_year_for_season(game.current_season),
                )
            ),
            "judicial": {
                "stats": JudicialCaseflowService.get_county_judicial_stats(game, [unit_id]).get(unit_id, {}),
                "recent_decisions": JudicialCaseflowService.get_county_judicial_decisions(game, unit_id, limit=5),
            },
        }

    # ==================== 府级基础建设 ====================

    @classmethod
    def _tick_construction(cls, pdata: dict, season: int) -> list:
        """
        推进建设队列一个月，返回本月完成项目的描述字符串列表。
        直接修改 pdata，不保存。
        """
        queue = pdata.get('construction_queue', [])
        if not queue:
            return []

        remaining = []
        completed = []
        for item in queue:
            item = dict(item)
            item['months_remaining'] -= 1
            if item['months_remaining'] <= 0:
                spec = PREFECTURE_INVESTMENT_SPECS.get(item['project'])
                if spec:
                    field = spec['field']
                    level = item['level']
                    if field == 'granary':
                        pdata['granary'] = True
                        pdata.setdefault('granary_stock', GRANARY_INIT_STOCK)
                    else:
                        pdata[field] = level
                    completed.append(f"{spec['label']}扩建完成（{level}级）")
            else:
                remaining.append(item)

        pdata['construction_queue'] = remaining
        return completed

    @classmethod
    def invest(cls, game, project: str, level: int) -> dict:
        """
        启动府级基础建设投资。
        project: "school" | "road" | "granary" | "river"
        level:   目标等级（必须为当前等级+1，按序建设）
        """
        spec = PREFECTURE_INVESTMENT_SPECS.get(project)
        if not spec:
            return {"error": f"未知投资项目: {project}"}

        pdata = game.player_unit.unit_data
        field = spec['field']

        current_level = 1 if (field == 'granary' and pdata.get('granary')) else pdata.get(field, 0)

        if level != current_level + 1:
            return {"error": f"必须按等级顺序投资，当前{spec['label']}为{current_level}级，只能建造{current_level + 1}级"}
        if level > spec['max_level']:
            return {"error": f"{spec['label']}已达最高等级（{spec['max_level']}级）"}

        cost = spec['costs'][level - 1]
        duration = spec['durations'][level - 1]

        if pdata.get('treasury', 0) < cost:
            return {"error": f"府库不足，需要{cost}两，现有{round(pdata.get('treasury', 0), 1)}两"}

        queue = pdata.get('construction_queue', [])
        if any(item['project'] == project for item in queue):
            return {"error": f"{spec['label']}已在建设中，请等待完工后再升级"}

        pdata['treasury'] = round(pdata['treasury'] - cost, 1)

        if duration == 0:
            # 即时完工（义仓）
            if field == 'granary':
                pdata['granary'] = True
                pdata.setdefault('granary_stock', GRANARY_INIT_STOCK)
            else:
                pdata[field] = level
            pdata.setdefault('construction_queue', [])
            game.player_unit.unit_data = pdata
            game.player_unit.save(update_fields=['unit_data'])
            return {
                "project": project,
                "label": spec['label'],
                "level": level,
                "cost": cost,
                "duration": 0,
                "treasury_after": pdata['treasury'],
                "status": "completed",
                "message": f"{spec['label']}建设完成",
            }

        queue.append({
            "project": project,
            "label": spec['label'],
            "level": level,
            "months_remaining": duration,
            "started_season": game.current_season,
        })
        pdata['construction_queue'] = queue
        game.player_unit.unit_data = pdata
        game.player_unit.save(update_fields=['unit_data'])

        return {
            "project": project,
            "label": spec['label'],
            "level": level,
            "cost": cost,
            "duration": duration,
            "treasury_after": pdata['treasury'],
            "status": "started",
            "message": f"{spec['label']}（{level}级）建设开始，预计{duration}月完工",
        }

    @classmethod
    def get_invest_status(cls, game) -> dict:
        """返回府级基础建设当前状态与可投资项目列表"""
        pdata = game.player_unit.unit_data
        queue = pdata.get('construction_queue', [])
        treasury = pdata.get('treasury', 0)
        in_queue_projects = {item['project'] for item in queue}

        projects = []
        for key, spec in PREFECTURE_INVESTMENT_SPECS.items():
            field = spec['field']
            current = 1 if (field == 'granary' and pdata.get('granary')) else pdata.get(field, 0)
            next_level = current + 1
            maxed = current >= spec['max_level']
            in_queue = key in in_queue_projects
            next_cost = spec['costs'][next_level - 1] if not maxed else None
            next_duration = spec['durations'][next_level - 1] if not maxed else None
            can_invest = (
                not maxed
                and not in_queue
                and next_cost is not None
                and treasury >= next_cost
            )
            projects.append({
                "project": key,
                "label": spec['label'],
                "current_level": current,
                "max_level": spec['max_level'],
                "next_level": next_level if not maxed else None,
                "next_cost": next_cost,
                "next_duration": next_duration,
                "in_queue": in_queue,
                "can_invest": can_invest,
                "maxed": maxed,
            })

        return {
            "treasury": treasury,
            "projects": projects,
            "construction_queue": queue,
        }

    # ==================== 才池与府试 ====================

    @classmethod
    def _init_talent_pool(cls, pdata: dict, subordinates: list) -> None:
        """
        建府时初始化全府年轻人才池。
        各村人口 × 3%，年龄 19~22 随机，潜力 80~199，能力随机。
        直接修改 pdata，不保存。
        """
        pool = []
        for unit in subordinates:
            cd = unit.unit_data
            county_name = cd.get('county_name', '')
            for v in cd.get('villages', []):
                pop = v.get('population', 0)
                has_school = v.get('has_school', False)
                count = max(1, int(pop * 0.03))
                for _ in range(count):
                    potential = random.randint(80, 199)
                    base_ability = random.randint(1, max(1, potential // 2))
                    ability = min(potential, base_ability + (5 if has_school else 0))
                    pool.append({
                        "county_id":   unit.id,
                        "county_name": county_name,
                        "village":     v.get('name', ''),
                        "age":         random.randint(19, 22),
                        "potential":   potential,
                        "ability":     ability,
                    })
        pdata['talent_pool'] = pool

    @classmethod
    def _advance_talent_pool(cls, pdata: dict, subordinates: list) -> None:
        """
        三月年度才池结算（在 advance_month moy==3 时调用）：
        1. 全员年龄 +1，超过35岁者离池
        2. 按所在县学等级增长能力值
        3. 各村新增 age=18 人才（人口 × 1%）
        直接修改 pdata，不保存。
        """
        school_map = {u.id: u.unit_data.get('school_level', 0) for u in subordinates}

        # 按 (county_id, village_name) 记录村庄数据，用于新增人才
        village_map = {}
        for u in subordinates:
            for v in u.unit_data.get('villages', []):
                village_map[(u.id, v.get('name', ''))] = {
                    'population':  v.get('population', 0),
                    'has_school':  v.get('has_school', False),
                    'county_name': u.unit_data.get('county_name', ''),
                }

        pool = pdata.get('talent_pool', [])
        grown = []
        for t in pool:
            t = dict(t)
            t['age'] += 1
            if t['age'] > 35:
                continue   # 归隐/务农，离池
            sl = school_map.get(t['county_id'], 0)
            if sl == 1:
                t['ability'] = min(t['potential'], t['ability'] + random.randint(1, 2))
            elif sl == 2:
                t['ability'] = min(t['potential'], t['ability'] + random.randint(1, 3))
            elif sl >= 3:
                t['ability'] = min(t['potential'], t['ability'] + random.randint(2, 4))
            # sl == 0：无县学，无增长
            grown.append(t)

        # 新增 age=18 人才
        for (county_id, village_name), vd in village_map.items():
            count = max(0, int(vd['population'] * 0.01))
            for _ in range(count):
                potential = random.randint(80, 199)
                base_ability = random.randint(1, max(1, potential // 2))
                ability = min(potential, base_ability + (5 if vd['has_school'] else 0))
                grown.append({
                    "county_id":   county_id,
                    "county_name": vd['county_name'],
                    "village":     village_name,
                    "age":         18,
                    "potential":   potential,
                    "ability":     ability,
                })

        pdata['talent_pool'] = grown

    @classmethod
    def _run_exam(cls, pdata: dict, season: int) -> dict:
        """
        十月府试：按能力值（加府学等级噪声）选拔前100名，建立门生关系。
        名字在此处临时生成，不持久存储在才池中。
        直接修改 pdata，不保存。返回本届府试记录。
        """
        pool = pdata.get('talent_pool', [])
        school_level = pdata.get('school_level', 0)
        noise = EXAM_NOISE_BY_SCHOOL[min(school_level, 3)]

        # 加噪声后排名（用 index 确保移除时不出错）
        noisy = [
            (i, t, t['ability'] + (random.randint(-noise, noise) if noise else 0))
            for i, t in enumerate(pool)
        ]
        noisy.sort(key=lambda x: x[2], reverse=True)

        top_items = noisy[:EXAM_TOP_N]
        selected_indices = {i for i, _t, _s in top_items}

        # 生成录取名单（此时才生成姓名）
        selected = []
        county_counts = {}
        for i, t, _ in top_items:
            name = random.choice(list(GOVERNOR_SURNAMES)) + random.choice(list(GOVERNOR_GIVEN_NAMES))
            county = t.get('county_name', '')
            selected.append({
                "name":     name,
                "county":   county,
                "village":  t.get('village', ''),
                "ability":  t['ability'],
                "potential": t['potential'],
                "age":      t['age'],
            })
            county_counts[county] = county_counts.get(county, 0) + 1

        # 从才池中移除录取者
        pdata['talent_pool'] = [t for i, t, _ in noisy if i not in selected_indices]

        year = (season - 1) // 12 + 1
        exam_record = {
            "season":       season,
            "year":         year,
            "count":        len(selected),
            "top_10":       selected[:10],
            "county_counts": county_counts,
            "pool_before":  len(pool),
        }

        results = pdata.get('exam_results', [])
        results.append(exam_record)
        pdata['exam_results'] = results[-3:]   # 保留最近3届
        pdata['total_disciples'] = pdata.get('total_disciples', 0) + len(selected)
        pdata['exam_pending'] = False

        return exam_record

    # ==================== 司法系统 ====================

    @classmethod
    def get_judicial_cases(cls, game) -> dict:
        """返回待决卷宗列表（完整数据）和已决日志"""
        return JudicialCaseflowService.get_prefecture_payload(game)

    @classmethod
    def get_judicial_debug_data(cls, game) -> dict:
        return JudicialCaseflowService.get_debug_payload(game)

    @classmethod
    def _find_source_county_unit(cls, game, case_data: dict):
        source_unit_id = case_data.get('source_unit_id')
        if source_unit_id:
            unit = AdminUnit.objects.filter(
                id=source_unit_id, game=game, unit_type='COUNTY', parent=game.player_unit,
            ).first()
            if unit is not None:
                return unit

        source_county = case_data.get('source_county')
        if not source_county:
            return None
        return AdminUnit.objects.filter(
            game=game, unit_type='COUNTY', parent=game.player_unit,
            unit_data__county_name=source_county,
        ).first()

    @classmethod
    def _apply_judicial_effects(cls, game, pdata: dict, case_data: dict, effects: dict) -> dict:
        """将司法决策的即时效果真正落到府域存档中。"""
        applied = {}

        treasury_delta = round(float(effects.get('treasury', 0) or 0), 1)
        if treasury_delta:
            pdata['treasury'] = round(pdata.get('treasury', 0) + treasury_delta, 1)
        applied['treasury'] = treasury_delta

        prestige_delta = int(effects.get('prestige', 0) or 0)
        if prestige_delta:
            pdata['judicial_prestige'] = round(_clamp_meter(
                pdata.get('judicial_prestige', 50) + prestige_delta
            ), 1)
        else:
            pdata.setdefault('judicial_prestige', 50)
        applied['judicial_prestige'] = pdata.get('judicial_prestige', 50)

        inspector_delta = int(effects.get('inspector_favor', 0) or 0)
        if inspector_delta:
            pdata['inspector_favor'] = round(_clamp_meter(
                pdata.get('inspector_favor', 50) + inspector_delta
            ), 1)
        else:
            pdata.setdefault('inspector_favor', 50)
        applied['inspector_favor'] = pdata.get('inspector_favor', 50)

        magistrate_delta = int(effects.get('magistrate_favor', 0) or 0)
        applied['prefect_affinity'] = None
        if magistrate_delta:
            target_unit = cls._find_source_county_unit(game, case_data)
            if target_unit is not None:
                cd = target_unit.unit_data
                cd['prefect_affinity'] = round(_clamp_meter(
                    cd.get('prefect_affinity', 50) + magistrate_delta
                ), 1)
                target_unit.unit_data = cd
                target_unit.save(update_fields=['unit_data'])
                applied['prefect_affinity'] = cd['prefect_affinity']

        return applied

    @classmethod
    def _derive_judicial_signals(cls, case_data: dict, action: str) -> dict:
        factors = case_data.get('initial_review_factors') or {}
        beneficiary_gain = float(factors.get('beneficiary_gain', 0.5) or 0.5)
        coverup_risk = float(factors.get('coverup_risk', 0.5) or 0.5)
        evidence_doubt = float(factors.get('evidence_doubt', 0.5) or 0.5)

        competence = 0
        integrity = 0
        if action == '核准原判':
            competence = 1
            integrity = 1 if evidence_doubt <= 0.45 and beneficiary_gain <= 0.45 else 0
        elif action == '驳回重审':
            competence = -1
            integrity = -1 if coverup_risk >= 0.65 else 0
        elif action == '提审改判':
            competence = -2
            integrity = -2 if beneficiary_gain >= 0.60 or coverup_risk >= 0.65 else -1
        return {
            'competence': competence,
            'integrity': integrity,
        }

    @classmethod
    def _record_county_judicial_outcome(cls, game, case_data: dict, action: str, option: dict) -> Optional[dict]:
        target_unit = cls._find_source_county_unit(game, case_data)
        if target_unit is None:
            return None

        cd = target_unit.unit_data
        history = list(cd.get('judicial_review_history', []))
        performance = dict(cd.get('judicial_performance', {}))
        signals = cls._derive_judicial_signals(case_data, action)

        assigned_season = int(case_data.get('assigned_season') or game.current_season)
        review_year = year_of(assigned_season)
        upheld = action == '核准原判'
        remanded = action == '驳回重审'
        overturned = action == '提审改判'

        history.append({
            'instance_id': case_data.get('instance_id'),
            'template_case_id': case_data.get('template_case_id') or case_data.get('case_id'),
            'case_id': case_data.get('case_id'),
            'case_name': case_data.get('case_name'),
            'source_unit_id': target_unit.id,
            'source_county': target_unit.unit_data.get('county_name', ''),
            'source_governor_name': target_unit.unit_data.get('governor_profile', {}).get('name', ''),
            'assigned_season': assigned_season,
            'review_year': review_year,
            'initial_magistrate_decision': case_data.get('initial_magistrate_decision', 'AFFIRM_ORIGINAL'),
            'initial_magistrate_reason': case_data.get('initial_magistrate_reason', ''),
            'prefect_action': action,
            'effects': copy.deepcopy(option.get('immediate_effects', {})),
            'judicial_signal': signals,
            'upheld': upheld,
            'remanded': remanded,
            'overturned': overturned,
        })
        cd['judicial_review_history'] = history[-20:]

        performance['upheld_count'] = int(performance.get('upheld_count', 0) or 0) + (1 if upheld else 0)
        performance['remand_count'] = int(performance.get('remand_count', 0) or 0) + (1 if remanded else 0)
        performance['overturned_count'] = int(performance.get('overturned_count', 0) or 0) + (1 if overturned else 0)
        performance['competence_signal'] = int(performance.get('competence_signal', 0) or 0) + signals['competence']
        performance['integrity_signal'] = int(performance.get('integrity_signal', 0) or 0) + signals['integrity']
        cd['judicial_performance'] = performance

        target_unit.unit_data = cd
        target_unit.save(update_fields=['unit_data'])
        return {
            'source_unit_id': target_unit.id,
            'source_county': cd.get('county_name', ''),
            'signals': signals,
            'performance': performance,
        }

    @classmethod
    def decide_judicial_case(cls, game, case_id: str, action: str) -> dict:
        """
        玩家对卷宗作出决策，应用即时效果，将案件移入已决列表。
        """
        pdata = game.player_unit.unit_data
        try:
            instance_id = int(case_id)
        except (TypeError, ValueError):
            return {"error": "案件不存在"}

        instance = JudicialCaseInstance.objects.filter(
            id=instance_id,
            game=game,
            prefect_unit=game.player_unit,
            prefect_review_season=game.current_season,
            status__in=['SUBMITTED_TO_PREFECT', 'DEFERRED_TO_PREFECT'],
        ).first()
        if instance is None:
            return {"error": "案件不存在"}

        case_data = copy.deepcopy(instance.local_payload)

        option = next((o for o in case_data.get('options', []) if o['action'] == action), None)
        if not option:
            return {"error": f"无效决策选项: {action}"}

        effects = option.get('immediate_effects', {})
        applied_state = cls._apply_judicial_effects(game, pdata, case_data, effects)
        county_review = cls._record_county_judicial_outcome(game, case_data, action, option)
        if county_review is not None:
            applied_state['county_review'] = county_review

        # 移入已决列表
        decided = pdata.get('decided_cases', [])
        template_case_id = case_data.get('template_case_id') or instance.template_case_id or case_id
        if template_case_id not in decided:
            decided.append(template_case_id)
        pdata['decided_cases'] = decided

        # 写入司法日志（府志用）
        log = pdata.get('judicial_log', [])
        log.append({
            'case_id':     case_data.get('case_id', case_id),
            'instance_id': instance.id,
            'template_case_id': template_case_id,
            'case_name':   case_data['case_name'],
            'category':    case_data['category'],
            'difficulty':  case_data['difficulty'],
            'season':      game.current_season - 1,
            'source_unit_id': case_data.get('source_unit_id'),
            'source_county': case_data.get('source_county', ''),
            'source_governor_name': case_data.get('source_governor_name', ''),
            'initial_magistrate_decision': case_data.get('initial_magistrate_decision'),
            'initial_magistrate_reason': case_data.get('initial_magistrate_reason', ''),
            'action':      action,
            'effects':     effects,
            'applied_state': applied_state,
            'chain_events': option.get('chain_events', []),
        })
        pdata['judicial_log'] = log[-30:]

        instance.prefect_decision = {
            'season': game.current_season,
            'action': action,
            'effects': effects,
            'chain_events': option.get('chain_events', []),
        }
        instance.status = 'PREFECT_DECIDED'
        instance.save(update_fields=['prefect_decision', 'status', 'updated_at'])

        game.player_unit.unit_data = pdata
        game.player_unit.save(update_fields=['unit_data'])

        return {
            'case_id':     case_data.get('case_id', case_id),
            'case_name':   case_data['case_name'],
            'action':      action,
            'effects':     effects,
            'applied_state': applied_state,
            'chain_events': option.get('chain_events', []),
            'treasury':    pdata['treasury'],
        }

    @classmethod
    def get_talent_info(cls, game) -> dict:
        """返回才池统计信息与历史府试结果"""
        pdata = game.player_unit.unit_data
        pool = pdata.get('talent_pool', [])

        by_county: dict = {}
        age_distribution: dict = {}
        for t in pool:
            cname = t.get('county_name', '未知')
            entry = by_county.setdefault(cname, {'count': 0, 'ability_sum': 0})
            entry['count'] += 1
            entry['ability_sum'] += t.get('ability', 0)
            age_key = str(t.get('age', 0))
            age_distribution[age_key] = age_distribution.get(age_key, 0) + 1

        county_list = [
            {
                "county_name": cname,
                "count": v['count'],
                "avg_ability": round(v['ability_sum'] / v['count'], 1) if v['count'] else 0,
            }
            for cname, v in by_county.items()
        ]
        county_list.sort(key=lambda x: x['count'], reverse=True)

        return {
            "total":           len(pool),
            "by_county":       county_list,
            "age_distribution": age_distribution,
            "exam_results":    pdata.get('exam_results', []),
            "total_disciples": pdata.get('total_disciples', 0),
            "school_level":    pdata.get('school_level', 0),
        }
