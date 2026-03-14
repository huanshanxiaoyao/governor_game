"""仕途轨迹服务：管理知县候选池状态、品级变化及升迁条件展示。

候选池等级说明（v1）：
  0 — 未入候选池（初始状态）
  1 — 初步候选池：任意年度最终考评 ≥ 良
  2 — 二阶候选池：三年任期届满（MAX_MONTH）且最终考评 ≥ 良；品级七品→六品，候选调任
  3 — 三阶候选池：调任后任期完满（非革退）且考评 ≥ 中；具备知府候选资格

【设计备注 v2 扩展点】
  候选人范围当前仅限同府下属 AI 知县 NPC；
  v2 可扩展至全省级别候选池，届时需引入省级 NPC 候选人数据结构与跨府比较逻辑。
"""

from __future__ import annotations

from typing import Optional

from .constants import MAX_MONTH
from .state import load_county_state, save_player_state


class CareerTrackService:

    GRADE_RANK: dict[str, int] = {"优": 4, "良": 3, "中": 2, "差": 1}

    POOL_LABELS: dict[int, str] = {
        0: "未入候选池",
        1: "初步候选池",
        2: "二阶候选池",
        3: "三阶候选池",
    }

    # 品级对养廉银的加成系数（七品→六品 +10%）
    STIPEND_RANK_BONUS: dict[str, float] = {
        "七品": 1.0,
        "六品": 1.1,
    }

    # ── 初始化 ──────────────────────────────────────────────────────────────

    @classmethod
    def get_or_init(cls, county: dict) -> dict:
        """从 county_data 读取或初始化 career_track，就地写入并返回。"""
        track = county.get("career_track")
        if not isinstance(track, dict):
            track = {
                "rank": "七品",
                "term_index": 1,
                "term_start_season": 1,
                "candidate_pool_level": 0,
                "pool_entry_log": [],
            }
            county["career_track"] = track
        return track

    # ── 年度考评后更新 ───────────────────────────────────────────────────────

    @classmethod
    def update_after_annual_review(
        cls,
        county: dict,
        final_grade: str,
        season: int,
        is_term_end: bool,
    ) -> dict:
        """
        在正月巡抚复核完成后调用。
        返回 dict，包含 pool_level_changed（新等级，或 None）、rank_changed（bool）。
        """
        track = cls.get_or_init(county)
        result: dict = {}
        grade_val = cls.GRADE_RANK.get(final_grade, 0)
        current_level = track.get("candidate_pool_level", 0)

        # ── 三阶候选池：调任后任期完满，已在二阶，且评价 ≥ 中 ──
        if is_term_end and current_level == 2 and grade_val >= 2:
            track["candidate_pool_level"] = 3
            track["pool_entry_log"].append({
                "level": 3,
                "season": season,
                "grade": final_grade,
                "note": f"调任后任期完满，考评{final_grade}，升入三阶候选池，具备知府候选资格",
            })
            result["pool_level_changed"] = 3
            return result

        # ── 二阶候选池：三年任期届满，评价 ≥ 良 ──
        if is_term_end and grade_val >= 3 and current_level < 2:
            old_rank = track.get("rank", "七品")
            track["candidate_pool_level"] = 2
            track["rank"] = "六品"
            track["pool_entry_log"].append({
                "level": 2,
                "season": season,
                "grade": final_grade,
                "note": (
                    f"三年任期届满，考评{final_grade}，升入二阶候选池，"
                    f"品级由{old_rank}升为六品，候选调任本府他县"
                ),
            })
            result["pool_level_changed"] = 2
            result["rank_changed"] = True
            return result

        # ── 初步候选池：任意年评价 ≥ 良 ──
        if grade_val >= 3 and current_level < 1:
            track["candidate_pool_level"] = 1
            track["pool_entry_log"].append({
                "level": 1,
                "season": season,
                "grade": final_grade,
                "note": f"年度考评{final_grade}，进入初步候选池",
            })
            result["pool_level_changed"] = 1

        return result

    # ── 养廉银加成 ───────────────────────────────────────────────────────────

    @classmethod
    def get_stipend_multiplier(cls, county: dict) -> float:
        """根据品级返回养廉银加成系数（六品 +10%）。"""
        rank = (county.get("career_track") or {}).get("rank", "七品")
        return cls.STIPEND_RANK_BONUS.get(rank, 1.0)

    # ── 前端 payload ─────────────────────────────────────────────────────────

    @classmethod
    def get_career_payload(cls, game) -> dict:
        """返回仕途轨迹页所需的完整数据。"""
        county = load_county_state(game)
        track = cls.get_or_init(county)
        # 初始化后若有改动，写回游戏
        if county.get("career_track") is None:
            save_player_state(game, county)

        reviews = cls._build_review_list(county)
        reviews_by_term = cls._build_reviews_by_term(county)
        current_level = track.get("candidate_pool_level", 0)
        season = game.current_season

        from .promotion_event import PromotionEventService
        event_payload = PromotionEventService.get_event_payload(track.get("promotion_event"))

        return {
            "rank": track.get("rank", "七品"),
            "term_index": track.get("term_index", 1),
            "term_start_season": track.get("term_start_season", 1),
            "current_season": season,
            "max_season": MAX_MONTH,
            "candidate_pool_level": current_level,
            "pool_level_label": cls.POOL_LABELS.get(current_level, "未知"),
            "pool_entry_log": track.get("pool_entry_log", []),
            "annual_reviews": reviews,
            "annual_reviews_by_term": reviews_by_term,
            "promotion_requirements": cls._build_promo_requirements(current_level, season),
            "promotion_event": event_payload,
        }

    # ── 内部辅助 ─────────────────────────────────────────────────────────────

    @classmethod
    def _build_review_list(cls, county: dict) -> list:
        out = []
        for cycle in county.get("annual_reviews", []):
            snap = cycle.get("objective_snapshot") or {}
            out.append({
                "year": cycle.get("year"),
                "objective_score": snap.get("objective_score"),
                "objective_grade": snap.get("objective_grade"),
                "prefect_grade": (cycle.get("prefect_review") or {}).get("grade"),
                "final_grade": cycle.get("final_grade") or "",
                "governor_decision": (cycle.get("governor_recheck") or {}).get("decision") or "",
                "state": cycle.get("state") or "",
                "incident_flags": snap.get("incident_flags") or [],
            })
        return out

    @classmethod
    def _build_reviews_by_term(cls, county: dict) -> list:
        """
        按任期分组返回所有考评记录（历史任期 + 当前任期）。
        每组：{term_index, county_name, reviews[]}
        """
        result = []
        history = county.get("annual_reviews_history") or []
        track = county.get("career_track") or {}
        pool_log = track.get("pool_entry_log") or []

        # 历史任期（已归档的每一组）
        for idx, term_reviews in enumerate(history):
            term_num = idx + 1
            # 从 pool_entry_log 推断该任期结束时的县名（暂时用 county_name）
            result.append({
                "term_index": term_num,
                "county_name": county.get("county_name", ""),
                "reviews": [cls._serialize_cycle_brief(r) for r in term_reviews],
            })

        # 当前任期
        current_term = len(history) + 1
        current_reviews = county.get("annual_reviews") or []
        result.append({
            "term_index": current_term,
            "county_name": county.get("county_name", ""),
            "reviews": [cls._serialize_cycle_brief(r) for r in current_reviews],
        })
        return result

    @classmethod
    def _serialize_cycle_brief(cls, cycle: dict) -> dict:
        snap = cycle.get("objective_snapshot") or {}
        return {
            "year": cycle.get("year"),
            "objective_score": snap.get("objective_score"),
            "objective_grade": snap.get("objective_grade"),
            "prefect_grade": (cycle.get("prefect_review") or {}).get("grade"),
            "final_grade": cycle.get("final_grade") or "",
            "governor_decision": (cycle.get("governor_recheck") or {}).get("decision") or "",
            "state": cycle.get("state") or "",
            "incident_flags": snap.get("incident_flags") or [],
        }

    @classmethod
    def _build_promo_requirements(cls, current_level: int, season: int) -> dict:
        if current_level == 0:
            return {
                "next_level": 1,
                "next_level_label": "初步候选池",
                "description": "任意年度考评最终结果达「良」或以上，即可进入初步候选池",
                "seasons_remaining": None,
            }
        if current_level == 1:
            seasons_remaining = max(0, MAX_MONTH - season + 1)
            return {
                "next_level": 2,
                "next_level_label": "二阶候选池",
                "description": (
                    "三年任期届满（第36月）且年终考评达「良」或以上，"
                    "升入二阶候选池并晋为六品，同时候选调任本府他县"
                ),
                "seasons_remaining": seasons_remaining,
            }
        if current_level == 2:
            return {
                "next_level": 3,
                "next_level_label": "三阶候选池",
                "description": (
                    "调任后任期完满（非革退），且年终考评达「中」或以上，"
                    "升入三阶候选池，具备知府候选资格"
                ),
                "seasons_remaining": None,
            }
        # level == 3
        return {
            "next_level": None,
            "next_level_label": None,
            "description": "已进入三阶候选池，每年正月巡抚将随机决定是否出缺，出缺后即启动升迁提名流程",
            "seasons_remaining": None,
        }
