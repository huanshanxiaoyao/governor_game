"""知府游戏服务 — 府域初始化、月度结算、汇报生成"""

import copy
import json
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed

from django.db import connection

from ..models import AdminUnit, Agent
from .constants import (
    COUNTY_TYPES,
    ARCHETYPE_TO_STYLES,
    ARCHETYPE_COUNTY_TYPE_WEIGHTS,
    GOVERNOR_STYLES,
    GOVERNOR_SURNAMES,
    GOVERNOR_GIVEN_NAMES,
    NEIGHBOR_COUNTY_NAMES,
    generate_governor_profile,
    month_of_year,
    month_name,
    CORVEE_PER_CAPITA,
    QUOTA_BASE_COLLECTION_EFFICIENCY,
)
from .county import CountyService
from .settlement import SettlementService
from .ai_governor import AIGovernorService
from .emergency import EmergencyService
from .magistrate_service import MagistrateService

logger = logging.getLogger('game')


# ===== 司法案件池（模块级加载）=====
def _load_judicial_pool():
    """从 docs/historical_materials/law_cases_pool.json 加载案件池"""
    pool_path = os.path.join(
        os.path.dirname(__file__),   # .../game/services/
        '..',                        # .../game/
        'judicial_cases.json',
    )
    pool_path = os.path.normpath(pool_path)
    try:
        with open(pool_path, encoding='utf-8') as f:
            data = json.load(f)
        return {c['case_id']: c for c in data.get('cases', [])}
    except Exception as e:
        logger.warning("司法案件池加载失败，路径: %s，错误: %s", pool_path, e)
        return {}


JUDICIAL_CASE_POOL = _load_judicial_pool()
JUDICIAL_CASES_LIST = list(JUDICIAL_CASE_POOL.values())

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


def score_to_tier(score: float) -> str:
    """将 0–99 数值转换为 8 档状况描述"""
    s = max(0, min(99, int(score)))
    for lo, hi, label in TIER_THRESHOLDS:
        if lo <= s <= hi:
            return label
    return "及格"


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
        "label": "跨县驿道",
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
        "label": "河道治理",
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
            "annual_quota": 0,           # 在县初始化后动态计算，见下方
            "quota_assignments": {},         # {unit_id: amount}
            "inspection_used": {"tongpan": 0, "tuiguan": 0},  # 年度核查次数
            "school_level": 0,               # 府学等级 0–3
            "road_level": 0,                 # 跨县驿道等级 0–2
            "granary": False,                # 府级义仓
            "river_work_level": 0,           # 河道治理进度 0–2
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
            style_key = random.choice(ARCHETYPE_TO_STYLES[archetype])
            names_pool = list(NEIGHBOR_COUNTY_NAMES.get(c_type, ["下辖县"]))
            county_name = names_pool[i % len(names_pool)]
            specs.append({
                'c_type': c_type,
                'archetype': archetype,
                'style_key': style_key,
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
                **generate_governor_profile(spec['style_key'], archetype=spec['archetype']),
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
                for future in as_completed(future_to_idx, timeout=20):
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
        prefecture_unit = game.player_unit
        pdata = prefecture_unit.unit_data
        season = game.current_season
        moy = month_of_year(season)

        # ── 建设队列推进（每月冒头）──
        completed_construction = cls._tick_construction(pdata, season)

        subordinates = list(
            AdminUnit.objects.filter(game=game, unit_type='COUNTY', parent=prefecture_unit)
        )

        # ── AI 决策（并行 LLM）──
        decision_results = cls._compute_ai_decisions(subordinates, season)

        # ── 府级基础建设上下文（传入县级结算，影响灾害/商业/人口）──
        prefecture_ctx = {
            "road_level":  pdata.get("road_level", 0),
            "river_level": pdata.get("river_work_level", 0),
            "granary":     bool(pdata.get("granary", False)),
        }

        # ── 物理结算 ──
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

        # ── 府库更新 ──
        pdata['treasury'] = round(pdata.get('treasury', 0) + remit_total, 1)
        # 累计年度已收（正月重置）
        if moy == 1:
            pdata['treasury_collected'] = round(remit_total, 1)
        else:
            pdata['treasury_collected'] = round(pdata.get('treasury_collected', 0) + remit_total, 1)

        # ── 三月：才池年度结算 ──
        if moy == 3:
            cls._advance_talent_pool(pdata, subordinates)

        # ── 腊月：扣除年度行政开支 ──
        if moy == 12:
            school_cost = [0, 120, 240, 480][min(pdata.get('school_level', 0), 3)]
            road_cost = [0, 100, 200][min(pdata.get('road_level', 0), 2)]
            total_cost = PREFECTURE_ANNUAL_ADMIN_TOTAL + school_cost + road_cost
            pdata['treasury'] = round(pdata['treasury'] - total_cost, 1)
            pdata['year_end_review_pending'] = True

        # ── 十月：府试自动结算 ──
        exam_result = None
        if moy == 10:
            exam_result = cls._run_exam(pdata, season)

        # ── 汇报月：生成模糊汇报 ──
        if moy in REPORT_MONTHS:
            cls._generate_reports(subordinates, season, pdata)

        # ── 重置核查次数（正月重置）──
        if moy == 1:
            pdata['inspection_used'] = {"tongpan": 0, "tuiguan": 0}

        # ── 季度末：生成司法案件 ──
        pending_cases = []
        if moy in {3, 6, 9, 12}:
            pending_cases = cls._generate_judicial_cases(pdata, subordinates, moy, season)

        prefecture_unit.unit_data = pdata
        prefecture_unit.save(update_fields=['unit_data'])

        game.current_season = season + 1
        game.save(update_fields=['current_season'])

        return {
            "season": season,  # the month just processed
            "remit_total": round(remit_total, 1),
            "treasury": pdata['treasury'],
            "report_generated": moy in REPORT_MONTHS,
            "exam_result": exam_result,
            "year_end_review_pending": pdata.get('year_end_review_pending', False),
            "construction_completed": completed_construction,
            "pending_cases": pending_cases,
        }

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
                for future in as_completed(futures, timeout=20):
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
            bias = 1 if archetype == 'CORRUPT' else 0   # CORRUPT 知县汇报偏高1档

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

            # CORRUPT 知县有概率隐瞒负面事项
            if archetype == 'CORRUPT' and random.random() < 0.6:
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
        每年每类最多3次（消耗1次），跨县驿道等级决定每次可覆盖的县数：
          road_level 0 → 1县，road_level 1 → 2县，road_level 2 → 3县。
        目标县固定为 unit_id，额外县由系统自动选取（驿道加成）。
        返回 {"results": [...], "road_level": N, "bonus_counties": M}。
        """
        pdata = game.player_unit.unit_data
        used = pdata.get('inspection_used', {"tongpan": 0, "tuiguan": 0})

        if used.get(inspect_type, 0) >= 3:
            return {"error": f"本年度{inspect_type}核查次数已用完（最多3次）"}

        target = AdminUnit.objects.filter(id=unit_id, game=game, unit_type='COUNTY').first()
        if not target:
            return {"error": "县不存在"}

        # 驿道决定本次可覆盖县数（目标县 + 额外县）
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
            total_pop = sum(v.get('population', 0) for v in cd.get('villages', []))
            if itype == 'tongpan':
                return {
                    "type": "通判核账",
                    "county_name": unit.name,
                    "unit_id": unit.id,
                    "treasury": round(cd.get('treasury', 0), 1),
                    "last_remit": round(cd.get('last_remit', 0), 1),
                    "tax_rate": cd.get('tax_rate', 0.12),
                    "commercial_tax_rate": cd.get('commercial_tax_rate', 0.03),
                }
            else:
                return {
                    "type": "推官巡查",
                    "county_name": unit.name,
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

        return {"assigned": total_assigned, "annual_quota": annual_quota, "warnings": warnings}

    # ==================== 查询接口 ====================

    @classmethod
    def get_prefecture_overview(cls, game) -> dict:
        """返回府情总览数据"""
        pdata = game.player_unit.unit_data
        subordinates = list(
            AdminUnit.objects.filter(game=game, unit_type='COUNTY', parent=game.player_unit)
        )

        # 汇总最新汇报数据（取各县最后一次汇报的指标）
        county_summaries = []
        for unit in subordinates:
            cd = unit.unit_data
            reports = cd.get('subordinate_reports', [])
            latest = reports[-1] if reports else None
            gp = cd.get('governor_profile', {})
            county_summaries.append({
                "unit_id": unit.id,
                "county_name": cd.get('county_name', ''),
                "governor_name": gp.get('name', ''),
                "governor_style": gp.get('style', ''),
                "governor_archetype": gp.get('archetype', 'MIDDLING'),
                "latest_report": latest,
                "quota": pdata.get('quota_assignments', {}).get(str(unit.id), 0),
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
            "current_season": game.current_season,
            "year_end_review_pending": pdata.get('year_end_review_pending', False),
            "exam_pending": pdata.get('exam_pending', False),
            "pending_judicial_count": len(pdata.get('pending_judicial_cases', [])),
            "counties": county_summaries,
        }

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
    def _generate_judicial_cases(cls, pdata: dict, subordinates: list, moy: int, season: int) -> list:
        """
        季度末生成 1–2 份待决卷宗，存入 pdata['pending_judicial_cases']。
        返回供前端立即展示的完整卷宗列表。
        """
        if not JUDICIAL_CASES_LIST:
            return []

        decided = set(pdata.get('decided_cases', []))
        available = [c for c in JUDICIAL_CASES_LIST if c['case_id'] not in decided]
        if not available:
            # 案件池耗尽则重置（允许重复）
            decided = set()
            pdata['decided_cases'] = []
            available = JUDICIAL_CASES_LIST[:]

        # 按季度偏好分类
        category_prefs = {
            3:  ['吏治贪腐类', '冤狱平反类'],
            6:  ['冤狱平反类', '民事纠纷类'],
            9:  ['吏治贪腐类', '刑事重案类'],
            12: ['统筹治理类', '吏治贪腐类'],
        }
        prefs = category_prefs.get(moy, [])
        preferred = [c for c in available if c['category'] in prefs]
        others    = [c for c in available if c['category'] not in prefs]

        # 难度权重随游戏年份递增
        year = (season - 1) // 12 + 1
        if year == 1:
            diff_w = {'新手': 0.60, '进阶': 0.35, '高难': 0.05}
        elif year == 2:
            diff_w = {'新手': 0.25, '进阶': 0.55, '高难': 0.20}
        else:
            diff_w = {'新手': 0.10, '进阶': 0.50, '高难': 0.40}

        def _pick(pool):
            if not pool:
                return None
            weights = [diff_w.get(c['difficulty'], 0.33) for c in pool]
            return random.choices(pool, weights=weights, k=1)[0]

        selected = []
        c1 = _pick(preferred or others)
        if c1:
            selected.append(c1)
            rest = [c for c in available if c['case_id'] != c1['case_id']]
            if rest and random.random() < 0.6:   # 60% 概率生成第二份卷宗
                c2 = _pick(rest)
                if c2:
                    selected.append(c2)

        # 写入待决列表（只存元数据，完整数据按需从 JUDICIAL_CASE_POOL 查取）
        pdata['pending_judicial_cases'] = [
            {
                'case_id':       c['case_id'],
                'case_name':     c['case_name'],
                'difficulty':    c['difficulty'],
                'category':      c['category'],
                'source_county': c['source_county'],
                'assigned_season': season,
            }
            for c in selected
        ]

        return selected   # 完整卷宗数据直接返回给前端

    @classmethod
    def get_judicial_cases(cls, game) -> dict:
        """返回待决卷宗列表（完整数据）和已决日志"""
        pdata = game.player_unit.unit_data
        pending_meta = pdata.get('pending_judicial_cases', [])

        # 从案件池查取完整卷宗数据
        pending_full = []
        for m in pending_meta:
            full = JUDICIAL_CASE_POOL.get(m['case_id'])
            if full:
                pending_full.append(full)

        return {
            'pending_cases': pending_full,
            'judicial_log': pdata.get('judicial_log', []),
        }

    @classmethod
    def decide_judicial_case(cls, game, case_id: str, action: str) -> dict:
        """
        玩家对卷宗作出决策，应用即时效果，将案件移入已决列表。
        """
        case_data = JUDICIAL_CASE_POOL.get(case_id)
        if not case_data:
            return {"error": "案件不存在"}

        option = next((o for o in case_data.get('options', []) if o['action'] == action), None)
        if not option:
            return {"error": f"无效决策选项: {action}"}

        pdata = game.player_unit.unit_data
        effects = option.get('immediate_effects', {})

        # 应用府库变化
        treasury_delta = effects.get('treasury', 0)
        pdata['treasury'] = round(pdata.get('treasury', 0) + treasury_delta, 1)

        # 移入已决列表
        decided = pdata.get('decided_cases', [])
        if case_id not in decided:
            decided.append(case_id)
        pdata['decided_cases'] = decided

        # 从待决列表移除
        pdata['pending_judicial_cases'] = [
            c for c in pdata.get('pending_judicial_cases', [])
            if c['case_id'] != case_id
        ]

        # 写入司法日志（府志用）
        log = pdata.get('judicial_log', [])
        log.append({
            'case_id':     case_id,
            'case_name':   case_data['case_name'],
            'category':    case_data['category'],
            'difficulty':  case_data['difficulty'],
            'season':      game.current_season - 1,
            'action':      action,
            'effects':     effects,
            'chain_events': option.get('chain_events', []),
        })
        pdata['judicial_log'] = log[-30:]

        game.player_unit.unit_data = pdata
        game.player_unit.save(update_fields=['unit_data'])

        return {
            'case_id':     case_id,
            'case_name':   case_data['case_name'],
            'action':      action,
            'effects':     effects,
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
