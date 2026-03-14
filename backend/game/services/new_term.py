"""新任期服务：任期届满后的续任逻辑。

处理三种续任场景：
  留任（pool 0/1）    — 保留全部县情，重置任期计数
  调任（pool 2）      — 接管最接近规模的 AI 邻县，继承其现状
  候选任期（pool 3）  — 同留任，但升迁提名系统继续激活
"""

from __future__ import annotations

import copy
import logging
from typing import Optional

from ..models import GameState, NeighborCounty
from .career_track import CareerTrackService
from .constants import (
    ADMIN_COST_DETAIL,
    COUNTY_TYPES,
    generate_governor_profile,
)
from .emergency import EmergencyService
from .ledger import ensure_county_ledgers
from .state import load_county_state, save_player_state

logger = logging.getLogger("game")

TERMINAL_REASONS = {"ANNUAL_REVIEW_DISMISSED", "PROMOTED_TO_PREFECT"}


class NewTermService:

    @classmethod
    def can_start_new_term(cls, game: GameState) -> Optional[str]:
        """
        返回 None 表示可以续任；返回字符串则是阻断原因。
        """
        from .constants import MAX_MONTH
        reason = load_county_state(game).get("term_end_reason")
        if reason in TERMINAL_REASONS:
            return "游戏已终止，无法续任"
        if game.current_season <= MAX_MONTH:
            return "任期尚未届满"
        # season > MAX_MONTH 且非 terminal reason 即允许续任；
        # awaiting_new_term 标志仅为前端信号，旧存档可能缺失，无需强制校验
        return None

    @classmethod
    def start_new_term(cls, game: GameState) -> dict:
        """
        执行续任：
          1. 归档考评记录
          2. 若 pool_level == 2，接管邻县；否则留任
          3. 重置任期计数器
          4. 重置紧急状态、年度配额
        返回新的 game_data dict 供前端更新。
        """
        blocker = cls.can_start_new_term(game)
        if blocker:
            return {"error": blocker}

        county = load_county_state(game)
        track = CareerTrackService.get_or_init(county)
        pool_level = track.get("candidate_pool_level", 0)
        term_index = track.get("term_index", 1)

        # ── Step 1: 归档考评 ──────────────────────────────────────────────
        current_reviews = county.get("annual_reviews") or []
        history = county.get("annual_reviews_history") or []
        if current_reviews:
            history.append(copy.deepcopy(current_reviews))

        # ── Step 2: 留任 or 调任 ─────────────────────────────────────────
        transfer_info = None
        if pool_level == 2:
            transfer_result = cls._transfer_to_neighbor(game, county, track)
            if transfer_result.get("ok"):
                county = transfer_result["new_county"]
                transfer_info = transfer_result
            else:
                logger.warning("调任邻县失败（%s），改为留任", transfer_result.get("reason"))

        # ── Step 3: 重置任期计数 ──────────────────────────────────────────
        county["annual_reviews_history"] = history
        county["annual_reviews"] = []
        county["awaiting_new_term"] = False
        county["term_end_reason"] = None
        county["annual_quota"] = {}        # 等待正月知府重新下达
        county["fiscal_year"] = {          # 清空年度财政累计
            "agri_tax": 0, "agri_remitted": 0,
            "commercial_tax": 0, "commercial_retained": 0,
            "corvee_tax": 0, "corvee_retained": 0,
        }
        county["disaster_this_year"] = None
        county["relief_application"] = {}
        county["autumn_tax_assessment"] = {}
        county["active_investments"] = county.get("active_investments") or []

        # 重置紧急状态（保留结构，清除上任遗留）
        EmergencyService.ensure_state(county)
        county["emergency"]["player_status"] = "ACTIVE"
        county["emergency"]["active"] = False
        county["emergency"]["riot"] = {"active": False, "start_season": None, "source": "", "seized_grain": 0.0}
        county["emergency"]["complaints"] = []
        county["emergency"]["complaint_pressure"] = 0.0
        county["emergency"]["consecutive_negative_reserve"] = 0

        # 更新 career_track
        track["term_index"] = term_index + 1
        track["term_start_season"] = 1
        # 若升迁事件已结束（未成功），重置计数器
        if track.get("promotion_event") and (track["promotion_event"] or {}).get("state") == "result_published":
            track["promotion_event"] = None
            track["tier3_januarys_without_event"] = 0

        county["career_track"] = track
        save_player_state(game, county)
        game.current_season = 1
        game.save(update_fields=["current_season", "updated_at"])

        return {
            "ok": True,
            "term_index": term_index + 1,
            "pool_level": pool_level,
            "transfer_info": transfer_info,
        }

    # ── 调任核心逻辑 ─────────────────────────────────────────────────────

    @classmethod
    def _transfer_to_neighbor(cls, game: GameState, old_county: dict, track: dict) -> dict:
        """
        选择一个 AI 邻县，接管其现状，合并玩家的 career_track 等字段。
        """
        neighbors = list(NeighborCounty.objects.filter(game=game).order_by("id"))
        if not neighbors:
            return {"ok": False, "reason": "无可用邻县"}

        # 选择人口规模最接近的邻县
        old_pop = sum(v.get("population", 0) for v in old_county.get("villages", []))
        chosen = min(
            neighbors,
            key=lambda n: abs(
                sum(v.get("population", 0) for v in n.county_data.get("villages", [])) - old_pop
            ),
        )

        new_county = copy.deepcopy(chosen.county_data)

        # ── 补全邻县可能缺失的字段（老存档防御性补全）──
        cls._backfill_missing_fields(new_county, old_county)

        # ── 继承玩家专属字段 ──
        new_county["county_name"] = chosen.county_name   # 写入县名（邻县 county_data 可能缺此字段）
        new_county["career_track"] = track
        new_county["admin_location"] = old_county.get("admin_location", {})
        new_county["governor_profile"] = old_county.get("governor_profile", {})
        new_county["prefect_affinity"] = 50.0

        # ── 从 neighbors 中移除（它现在归玩家治理）──
        chosen.delete()

        # ── 重建本县地主/村民代表 Agent 记录 ──
        # 旧县的 GENTRY/VILLAGER 绑定了旧村庄；调任后需删除并按新县村庄重建。
        cls._rebuild_local_agents(game, new_county)

        logger.info(
            "game#%d 调任：%s → %s（邻县 id=%d）",
            game.id, old_county.get("county_name", "旧县"), chosen.county_name, chosen.id,
        )
        return {
            "ok": True,
            "new_county": new_county,
            "old_county_name": old_county.get("county_name", ""),
            "new_county_name": chosen.county_name,
            "neighbor_id": chosen.id,
        }

    @classmethod
    def _rebuild_local_agents(cls, game: GameState, new_county: dict) -> None:
        """
        调任后重建本县地主/村民代表 Agent。
        删除旧县的 GENTRY/VILLAGER 记录（它们绑定了旧村庄名），
        再按新县 villages 数据生成新的 Agent 记录。
        """
        import copy
        from ..models import Agent
        from .local_npc import build_county_local_agent_definitions, ensure_county_local_cast

        # 删除旧的本县级 Agent（保留朝廷/府级等系统 NPC）
        Agent.objects.filter(game=game, role__in=("GENTRY", "VILLAGER")).delete()

        # 确保村庄有 persona_id 和名字分配
        ensure_county_local_cast(new_county)

        defs = build_county_local_agent_definitions(new_county)
        for defn in defs:
            Agent.objects.create(
                game=game,
                name=defn["name"],
                role=defn["role"],
                role_title=defn["role_title"],
                tier=defn["tier"],
                attributes=copy.deepcopy(defn["attributes"]),
            )
        logger.info("game#%d 重建本县 Agent（%d 条）", game.id, len(defs))

    @classmethod
    def _backfill_missing_fields(cls, county: dict, reference: dict) -> None:
        """
        为老存档邻县补全缺失字段，确保 settlement 引擎能正常运行。
        使用同类型县的默认值，参考旧县仅用于 admin_location 等元信息。
        """
        ensure_county_ledgers(county)
        EmergencyService.ensure_state(county)

        county_type = county.get("county_type", "fiscal_core")

        # admin_cost_detail（影响每月行政开销）
        if not county.get("admin_cost_detail"):
            county["admin_cost_detail"] = dict(ADMIN_COST_DETAIL.get(county_type, {}))
            county["admin_cost"] = sum(county["admin_cost_detail"].values())

        # governor_profile（暂时用默认值，start_new_term 会覆盖为玩家 profile）
        if not county.get("governor_profile"):
            county["governor_profile"] = generate_governor_profile("yuanhua")

        # 基础设施等级默认值
        county.setdefault("school_level", 1)
        county.setdefault("irrigation_level", 0)
        county.setdefault("medical_level", 0)
        county.setdefault("bailiff_level", 0)
        county.setdefault("advisor_level", 1)
        county.setdefault("has_granary", False)
        county.setdefault("granary_needs_rebuild", False)
        county.setdefault("granary_rebuild_cost", None)
        county.setdefault("granary_last_used_season", None)

        # 财政字段
        county.setdefault("annual_quota", {})
        county.setdefault("quota_completion", {})
        county.setdefault("active_investments", [])
        county.setdefault("road_repair_count", 0)
        county.setdefault("commercial_tax_rate", 0.03)
        county.setdefault("advisor_questions_used", 0)
        county.setdefault("price_index", 1.0)
        county.setdefault("remit_ratio", 0.6)

        county.setdefault("disaster_this_year", None)
        county.setdefault("relief_application", {})
        county.setdefault("autumn_tax_assessment", {})

        if not county.get("environment"):
            county["environment"] = {
                "agriculture_suitability": 0.6,
                "flood_risk": 0.3,
                "border_threat": 0.3,
            }

    # ── 任期总结（简版，供弹窗展示）────────────────────────────────────────

    @classmethod
    def build_term_summary(cls, game: GameState, county: dict) -> dict:
        """生成任期届满弹窗所需的简版总结数据。"""
        track = CareerTrackService.get_or_init(county)
        reviews = county.get("annual_reviews", [])
        final_review = reviews[-1] if reviews else {}
        final_grade = final_review.get("final_grade", "")
        pool_level = track.get("candidate_pool_level", 0)
        term_index = track.get("term_index", 1)

        # 候选池 → 续任说明
        flavor_map = {
            0: "考评欠佳，仍需历练，留任原职第{n}任。",
            1: "初露锋芒，仍须磨砺，留任原职第{n}任。",
            2: "政绩卓著，调任本府他县，续任第{n}任知县。",
            3: "已具知府候选资格，留任候缺，续任第{n}任。",
        }
        flavor = flavor_map.get(pool_level, "任期届满，续任第{n}任。").format(n=term_index + 1)

        # 若 pool 2，附上目标邻县名
        transfer_preview = None
        if pool_level == 2:
            neighbors = list(NeighborCounty.objects.filter(game=game).order_by("id"))
            if neighbors:
                old_pop = sum(v.get("population", 0) for v in county.get("villages", []))
                best = min(
                    neighbors,
                    key=lambda n: abs(
                        sum(v.get("population", 0) for v in n.county_data.get("villages", [])) - old_pop
                    ),
                )
                transfer_preview = {
                    "county_name": best.county_name,
                    "governor_name": best.governor_name,
                    "pop": sum(v.get("population", 0) for v in best.county_data.get("villages", [])),
                }

        return {
            "term_index": term_index,
            "final_grade": final_grade,
            "pool_level": pool_level,
            "pool_label": CareerTrackService.POOL_LABELS.get(pool_level, ""),
            "rank": track.get("rank", "七品"),
            "flavor": flavor,
            "transfer_preview": transfer_preview,
            "morale": round(county.get("morale", 0), 1),
            "security": round(county.get("security", 0), 1),
            "commercial": round(county.get("commercial", 0), 1),
            "education": round(county.get("education", 0), 1),
            "treasury": round(county.get("treasury", 0), 1),
        }
