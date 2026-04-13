"""玩家状态访问层。"""

import copy

from django.db import transaction


# ──────────────────────────────────────────────────────────────────────
# county_data schema migration — 旧存档缺失字段自动补齐
# ──────────────────────────────────────────────────────────────────────
#
# 每当新功能向 county_data 添加字段时，在下面的 _COUNTY_DEFAULTS 中加一行。
# load 时自动 setdefault，service 层可安心使用 county["key"] 而无需 .get()。
#
# 规则：
# • 只放"多处读写"的字段；单一 service 内部缓存字段不需要放这里
# • 嵌套 dict/list 的默认值用 lambda 避免共享可变对象
# • 不要在这里做复杂计算——只做最小化补齐

_COUNTY_DEFAULTS: list[tuple[str, object]] = [
    # 标识
    ("county_type", "fiscal_core"),
    ("county_type_name", ""),
    ("county_name", ""),

    # 核心指标
    ("morale", 50.0),
    ("security", 50.0),
    ("commercial", 50.0),
    ("education", 40.0),

    # 财政
    ("treasury", 0.0),
    ("tax_rate", 0.12),
    ("commercial_tax_rate", 0.03),
    ("remit_ratio", 0.65),
    ("price_index", 1.0),
    ("fiscal_year", lambda: {
        "commercial_tax": 0, "commercial_retained": 0,
        "corvee_tax": 0, "corvee_retained": 0,
        "agri_tax": 0, "agri_remitted": 0,
    }),
    ("annual_quota", lambda: {}),
    ("quota_completion", lambda: {}),
    ("admin_cost", 0.0),
    ("admin_cost_detail", lambda: {}),

    # 人口 / 土地
    ("gentry_land_ratio", 0.4),
    ("peasant_grain_reserve", 0.0),

    # 基建
    ("bailiff_level", 0),
    ("baojia_level", 0),
    ("advisor_level", 1),
    ("advisor_questions_used", 0),
    ("school_level", 1),
    ("irrigation_level", 0),
    ("medical_level", 0),
    ("agriculture_bonus", 0),
    ("road_repair_count", 0),

    # 粮仓
    ("has_granary", False),
    ("granary_needs_rebuild", False),
    ("granary_rebuild_cost", None),
    ("granary_last_used_season", None),

    # 投资 / 施政
    ("active_investments", lambda: []),

    # 环境 / 灾害
    ("environment", lambda: {
        "agriculture_suitability": 0.7,
        "flood_risk": 0.3,
        "border_threat": 0.1,
    }),
    ("disaster_this_year", None),
    ("relief_application", lambda: {}),
    ("autumn_tax_assessment", lambda: {}),

    # 知府关系
    ("prefect_affinity", 50),
    ("prefect_directives", lambda: []),
    ("prefect_inspection_pending", False),

    # 商业
    ("markets", lambda: []),
    ("villages", lambda: []),

    # 宗族
    ("clans", lambda: {}),

    # NPC 请求
    ("npc_pending_requests", lambda: []),
]


def _ensure_county_defaults(county: dict) -> None:
    """补齐旧存档中缺失的 county_data 字段。就地修改，无返回值。"""
    for key, default in _COUNTY_DEFAULTS:
        if key not in county:
            county[key] = default() if callable(default) else default


def load_player_state(game, refresh=False):
    """Load current player state from the canonical source as a deep copy."""
    if refresh:
        game.refresh_from_db()

    if game.player_unit_id:
        player_unit = game.player_unit
        if refresh:
            player_unit.refresh_from_db()
        state = copy.deepcopy(player_unit.unit_data or {})
    else:
        state = copy.deepcopy(game.county_data or {})

    _ensure_county_defaults(state)
    return state


def load_county_state(game, refresh=False):
    """County-mode convenience alias for current player state."""
    return load_player_state(game, refresh=refresh)


def save_player_state(game, state, mirror_legacy=True):
    """Persist current player state using full-dict replacement."""
    payload = copy.deepcopy(state or {})

    with transaction.atomic():
        if game.player_unit_id:
            player_unit = game.player_unit
            player_unit.unit_data = payload
            player_unit.save(update_fields=["unit_data"])

            if mirror_legacy and player_unit.unit_type == "COUNTY":
                game.county_data = payload
                game.save(update_fields=["county_data", "updated_at"])
                return payload

            game.save(update_fields=["updated_at"])
            return payload

        game.county_data = payload
        game.save(update_fields=["county_data", "updated_at"])
        return payload


def mutate_player_state(game, mutator, mirror_legacy=True):
    """Run a mutator against the current player state and persist it.

    供 Phase 3 写路径迁移使用：将"读-改-写"收口到此函数，
    mutator(state) 只操作内存中的 state dict，无需关心保存细节。
    """
    state = load_player_state(game)
    mutator(state)
    save_player_state(game, state, mirror_legacy=mirror_legacy)
    return state
