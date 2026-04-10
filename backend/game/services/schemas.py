"""TypedDict schemas for the most load-bearing JSON blobs in the codebase.

These are *intentionally loose* at rollout time:

* Every dict is declared ``total=False`` so existing call sites don't need
  to be updated to pass every field — this is how we incrementally opt in.
* Numeric fields use ``float`` where the runtime stores a mix of ``int``/
  ``float`` after settlement rounding. Treat them as "number".
* The dicts mirror ``county_data`` / ``unit_data`` as they exist in
  ``GameState.county_data`` and ``AdminUnit.unit_data`` JSONFields. Keys
  present here are the ones multiple services read or write. Keys that are
  written in exactly one place (e.g. debug caches) are intentionally
  omitted and should be accessed via ``.get(...)``.

Usage notes:

* Do not import this module at runtime for branching; these types are for
  static type checkers (mypy, pyright) only. The TypedDicts evaluate to
  plain ``dict`` at runtime.
* When adding a new field used by multiple services, add it here with a
  short comment explaining the unit/range. That's the cheapest form of
  shared contract documentation the codebase has.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


# ────────────────────────────────────────────────────────────────────
# Ledgers (村级双账本 — see docs/06a 数值补遗)
# ────────────────────────────────────────────────────────────────────


class PeasantLedger(TypedDict, total=False):
    """自耕农账本：在册人口/在册田亩/余粮"""
    registered_population: int
    farmland: int                    # 在册耕地，单位：亩
    grain_surplus: float             # 累计余粮，单位：斤
    monthly_consumption: float       # 上月消耗（参考）
    monthly_surplus: float           # 上月净余（参考）


class GentryLedger(TypedDict, total=False):
    """地主/宗族账本：在册 vs 隐匿"""
    registered_population: int
    hidden_population: int
    registered_farmland: int
    hidden_farmland: int
    grain_surplus: float


# ────────────────────────────────────────────────────────────────────
# Village
# ────────────────────────────────────────────────────────────────────


class Village(TypedDict, total=False):
    """县下最小行政单元。真相源 (Model A)：县级指标是村级聚合视图。"""
    name: str

    # 规模
    population: int                  # 总人口（在册+隐匿的自耕农+地主户）
    farmland: int                    # 总在册耕地，单位：亩
    hidden_land: int
    hidden_land_discovered: bool
    land_ceiling: int                # 土地开发上限（含开垦潜力）
    gentry_land_pct: float           # 地主占地比例 [0, 1]

    # 账本
    peasant_ledger: PeasantLedger
    gentry_ledger: GentryLedger

    # 村级指标（0-100）
    morale: float
    security: float

    # 施政标记
    has_school: bool


# ────────────────────────────────────────────────────────────────────
# County sub-structures
# ────────────────────────────────────────────────────────────────────


class Environment(TypedDict, total=False):
    agriculture_suitability: float   # [0.3, 1.0]
    flood_risk: float                # [0, 1]
    border_threat: float             # [0, 1]


class FiscalYear(TypedDict, total=False):
    """正月重置的年度累计账。"""
    commercial_tax: float
    commercial_retained: float
    corvee_tax: float
    corvee_retained: float
    agri_tax: float
    agri_remitted: float


class AdminCostDetail(TypedDict, total=False):
    advisor_fee: float
    deputy_salary: float
    clerks_cost: float
    bailiff_cost: float


class ActiveInvestment(TypedDict, total=False):
    action: str                      # e.g. "hire_bailiffs", "reclaim_land"
    target_village: Optional[str]
    started_season: int
    completion_season: int
    description: str
    cost_paid: float


class Disaster(TypedDict, total=False):
    type: str                        # "flood" | "drought" | "plague" | ...
    severity: float                  # [0, 1]
    season: int
    relieved: bool


class GovernorProfile(TypedDict, total=False):
    """AI 知县画像（仅邻县/子县使用；玩家本县此字段缺失）。"""
    name: str
    style: str                       # "minben" | "midway" | "fatou" | ...
    archetype: str                   # "VIRTUOUS" | "MIDDLING" | "CORRUPT"
    bio: str


# ────────────────────────────────────────────────────────────────────
# County (the main JSONField on GameState.county_data)
# ────────────────────────────────────────────────────────────────────


class County(TypedDict, total=False):
    """县级数据根，即 GameState.county_data 的运行时形状。

    注意：
    * 所有指标 (morale/security/commercial/education) 在 Model A 下是
      village 的加权聚合视图，不要直接赋值；应调用
      ``apply_county_stat_delta`` 经 _clamp_morale_security 再分摊到村。
    * 字段均为可选：总是用 ``.get(key, default)``，不要做 `county["x"]`
      硬索引（老存档可能缺字段，见 ``NeighborService._ensure_initial_baseline``）。
    """

    # 标识
    county_type: str
    county_type_name: str
    county_name: str

    # 核心指标（0-100；见 Model A 注释）
    morale: float
    security: float
    commercial: float
    education: float

    # 财政
    treasury: float
    tax_rate: float                  # 田赋税率，通常 0.08~0.15
    commercial_tax_rate: float       # 商税率，通常 0.03~0.10
    remit_ratio: float               # 田赋上缴比例
    price_index: float               # 价格指数，乘在施政成本上
    fiscal_year: FiscalYear
    annual_quota: Dict[str, float]
    quota_completion: Dict[str, Any]

    # 人口/土地
    villages: List[Village]          # 真相源
    gentry_land_ratio: float

    # 施政状态
    bailiff_level: int
    advisor_level: int
    advisor_questions_used: int
    school_level: int
    irrigation_level: int
    medical_level: int
    agriculture_bonus: float         # 百分点
    road_repair_count: int
    admin_cost: float
    admin_cost_detail: AdminCostDetail
    active_investments: List[ActiveInvestment]

    # 粮仓
    has_granary: bool
    granary_needs_rebuild: bool
    granary_rebuild_cost: Optional[float]
    granary_last_used_season: Optional[int]
    peasant_grain_reserve: float

    # 环境 / 灾害
    environment: Environment
    disaster_this_year: Optional[Disaster]
    relief_application: Dict[str, Any]
    autumn_tax_assessment: Dict[str, Any]

    # 知府关系
    prefect_affinity: float          # 0-100
    prefect_directives: List[Dict[str, Any]]
    prefect_inspection_pending: bool
    prefect_complaints: int

    # 邻县 / AI 专属
    governor_profile: GovernorProfile  # 仅 AI 县
    initial_villages: List[Village]    # 任期基线快照（邻县考核用）
    initial_snapshot: Dict[str, Any]   # 任期基线指标


# ────────────────────────────────────────────────────────────────────
# Report shapes emitted by SettlementService.advance_season
# ────────────────────────────────────────────────────────────────────


class Report(TypedDict, total=False):
    """SettlementService 产出的月报/季报根字典。各结算阶段会追加自己的子字段。"""
    season: int
    events: List[str]
    autumn: Dict[str, Any]
    winter_snapshot: Dict[str, Any]
    population_update: Dict[str, Any]
    report_generated: bool
    exam_triggered: bool
    year_end_review_pending: bool


# ────────────────────────────────────────────────────────────────────
# Prefecture unit_data (AdminUnit.unit_data where unit_type='PREFECTURE')
# ────────────────────────────────────────────────────────────────────


class Prefecture(TypedDict, total=False):
    prefecture_name: str
    prefecture_type_name: str
    treasury: float
    treasury_collected: float
    annual_quota: Dict[str, float]
    judicial_prestige: float
    inspector_favor: float
    judicial_log: List[Dict[str, Any]]
    pending_directives: List[Dict[str, Any]]
    evaluation_notes: List[str]
    memory: List[str]


__all__ = [
    "ActiveInvestment",
    "AdminCostDetail",
    "County",
    "Disaster",
    "Environment",
    "FiscalYear",
    "GentryLedger",
    "GovernorProfile",
    "PeasantLedger",
    "Prefecture",
    "Report",
    "Village",
]
