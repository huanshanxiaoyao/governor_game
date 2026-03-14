"""年度评议流程：知县自陈、知府初评、巡抚复核。"""

from __future__ import annotations

import random
from typing import Dict, Iterable, List, Optional, Tuple

from ..models import AdminUnit
from .constants import (
    ARCHETYPE_COUNTY_TYPE_WEIGHTS,
    ARCHETYPE_TO_STYLES,
    GOVERNOR_GIVEN_NAMES,
    GOVERNOR_SURNAMES,
    MAX_MONTH,
    generate_governor_profile,
    month_of_year,
    year_of,
)
from .emergency import EmergencyService
from .state import load_county_state, save_player_state


class AnnualReviewService:
    """Annual review workflow for county and prefecture modes."""

    GRADES = ("优", "良", "中", "差")
    GRADE_RANK = {"优": 4, "良": 3, "中": 2, "差": 1}
    DISPLAY_MONTHS = {1, 11, 12}

    @classmethod
    def get_county_review_payload(cls, game) -> dict:
        """Return current county-player annual review payload for dashboard."""
        season = game.current_season
        moy = month_of_year(season)
        year = cls.display_year_for_season(season)
        county = load_county_state(game)
        cycle = cls._find_cycle(county, year)
        available = moy in {11, 12} or (moy == 1 and year >= 1 and cycle is not None)

        if cycle is None and moy == 11:
            cycle = cls._build_cycle(
                review_year=year,
                season=season,
                subject_name="本县知县",
                subject_style="",
                subject_archetype="",
                objective_snapshot=cls._build_objective_snapshot(county, season),
            )

        phase = None
        if available:
            if moy == 11:
                phase = "self_statement"
            elif moy == 12:
                phase = "prefect_review"
            else:
                phase = "published"

        return {
            "available": available,
            "phase": phase,
            "display_year": year,
            "can_submit": bool(moy == 11 and game.current_season <= MAX_MONTH),
            "advance_blocked": bool(moy == 11 and cls.get_county_advance_blocker(game)),
            "entry": cls._serialize_cycle(cycle),
        }

    @classmethod
    def submit_county_self_statement(cls, game, payload: dict) -> dict:
        """Persist county-player self statement in November."""
        if game.current_season > MAX_MONTH:
            return {"error": "任期已结束，无法提交年度自陈"}

        season = game.current_season
        if month_of_year(season) != 11:
            return {"error": "年度自陈仅可在冬月（十一月）提交"}

        county = load_county_state(game)
        review_year = year_of(season)
        statement = cls._normalize_statement_payload(payload)
        if statement is None:
            return {"error": "年度自陈四部分均不能为空"}

        snapshot = cls._build_objective_snapshot(county, season)
        candor_penalty, audit_flags = cls._estimate_statement_risk(statement, snapshot)
        cycle = cls._ensure_cycle(
            county,
            review_year=review_year,
            season=season,
            subject_name="本县知县",
            subject_style="",
            subject_archetype="",
        )
        cycle["self_statement"] = statement
        cycle["objective_snapshot"] = snapshot
        cycle["self_statement_meta"] = {
            "submitted_season": season,
            "candor_penalty": candor_penalty,
            "audit_flags": audit_flags,
        }
        cycle["state"] = "submitted"

        save_player_state(game, county)
        return cls.get_county_review_payload(game)

    @classmethod
    def get_county_advance_blocker(cls, game) -> Optional[str]:
        """November cannot advance before player submits self statement."""
        if month_of_year(game.current_season) != 11:
            return None

        cycle = cls._find_cycle(load_county_state(game), year_of(game.current_season))
        if cycle and cycle.get("self_statement"):
            return None
        return "冬月须先提交年度自陈，方可推进至腊月"

    @classmethod
    def handle_county_transition(
        cls,
        game,
        county: dict,
        processed_season: int,
        next_season: int,
        report: dict,
    ) -> dict:
        """Apply annual-review hooks when county mode crosses key months."""
        result: Dict[str, object] = {}
        next_moy = month_of_year(next_season)

        if next_moy == 11:
            review_year = year_of(next_season)
            cls._ensure_cycle(
                county,
                review_year=review_year,
                season=next_season,
                subject_name="本县知县",
                subject_style="",
                subject_archetype="",
                objective_snapshot=cls._build_objective_snapshot(county, processed_season),
            )
            report.setdefault("events", []).append("【年度评议】冬月将至，请于县情总览提交年度自陈。")

        elif next_moy == 12:
            review_year = year_of(processed_season)
            cycle = cls._ensure_cycle(
                county,
                review_year=review_year,
                season=next_season,
                subject_name="本县知县",
                subject_style="",
                subject_archetype="",
            )
            if cycle.get("self_statement"):
                review = cls._build_prefect_review(
                    county=county,
                    cycle=cycle,
                    season=next_season,
                    reviewer_name="本府知府",
                )
                cycle["prefect_review"] = review
                cycle["state"] = "prefect_reviewed"
                report.setdefault("events", []).append(
                    f"【年度评议】知府已完成本年初评，评为{review['grade']}。"
                )
                result["prefect_review"] = review

        elif next_moy == 1:
            review_year = year_of(processed_season)
            cycle = cls._find_cycle(county, review_year)
            if cycle and cycle.get("prefect_review"):
                final = cls._build_governor_recheck(
                    county=county,
                    cycle=cycle,
                    season=next_season,
                    reviewer_name="本省巡抚",
                )
                cycle["governor_recheck"] = final
                cycle["final_grade"] = final["final_grade"]
                cycle["state"] = "finalized"
                cycle["published_season"] = next_season
                report.setdefault("events", []).append(
                    f"【省府复核】巡抚复核本年考评，最终评为{final['final_grade']}。"
                )
                result["governor_recheck"] = final
                if final["final_grade"] == "差":
                    EmergencyService.ensure_state(county)
                    county["emergency"]["player_status"] = "DISMISSED"
                    county["term_end_reason"] = "ANNUAL_REVIEW_DISMISSED"
                    report.setdefault("events", []).append(
                        "【革退】巡抚认定失职，革退原任，本局到此结束。"
                    )
                    result["next_season_override"] = MAX_MONTH + 1
                else:
                    from .career_track import CareerTrackService
                    is_term_end = next_season > MAX_MONTH
                    ct = CareerTrackService.update_after_annual_review(
                        county=county,
                        final_grade=final["final_grade"],
                        season=next_season,
                        is_term_end=is_term_end,
                    )
                    if ct.get("rank_changed"):
                        report.setdefault("events", []).append(
                            "【仕途】任期届满，考评达良，品级由七品升为六品，已列入二阶候选池，候选调任本府他县。"
                        )
                    elif ct.get("pool_level_changed") == 3:
                        report.setdefault("events", []).append(
                            "【仕途】调任任期完满，已升入三阶候选池，具备知府候选资格。"
                        )
                    elif ct.get("pool_level_changed") == 1:
                        report.setdefault("events", []).append(
                            f"【仕途】年度考评{final['final_grade']}，已进入初步候选池。"
                        )
                    result["career_track"] = ct
                    # 正月：若玩家已在三阶候选池，检查是否触发升迁事件
                    if not is_term_end:
                        from .promotion_event import PromotionEventService
                        pe = PromotionEventService.check_and_trigger(game, county, report)
                        if pe:
                            result["promotion_event_triggered"] = pe

        elif next_moy == 2:
            # 正月末：关闭升迁行动窗口，提交提名至吏部
            from .promotion_event import PromotionEventService
            pe = PromotionEventService.advance_to_ministry(county, report)
            if pe:
                result["promotion_nomination"] = pe

        elif next_moy == 3:
            # 二月末：吏部放榜
            from .promotion_event import PromotionEventService
            pe = PromotionEventService.compute_result(game, county, report)
            if pe:
                result["promotion_result"] = pe.get("result")
                if pe.get("next_season_override"):
                    result["next_season_override"] = pe["next_season_override"]

        return result

    @classmethod
    def ensure_prefecture_self_reviews(cls, game) -> dict:
        """Generate subordinate self statements for current annual-review window."""
        season = game.current_season
        moy = month_of_year(season)
        if moy not in {11, 12}:
            return {"changed": 0}

        review_year = year_of(season)
        subordinates = list(
            AdminUnit.objects.filter(
                game=game, unit_type="COUNTY", parent=game.player_unit,
            ).order_by("id")
        )
        changed = 0
        for unit in subordinates:
            cd = unit.unit_data
            gp = cd.get("governor_profile", {})
            cycle = cls._find_cycle(cd, review_year)
            if cycle is None:
                cycle = cls._ensure_cycle(
                    cd,
                    review_year=review_year,
                    season=season,
                    subject_name=gp.get("name", "某知县"),
                    subject_style=gp.get("style", ""),
                    subject_archetype=gp.get("archetype", "MIDDLING"),
                )
                changed += 1

            snapshot = cls._build_objective_snapshot(cd, season)
            cycle["objective_snapshot"] = snapshot
            if not cycle.get("self_statement"):
                statement = cls._build_ai_self_statement(cd, cycle, snapshot)
                candor_penalty, audit_flags = cls._estimate_statement_risk(statement, snapshot)
                cycle["self_statement"] = statement
                cycle["self_statement_meta"] = {
                    "submitted_season": season,
                    "candor_penalty": candor_penalty,
                    "audit_flags": audit_flags,
                }
                cycle["state"] = "submitted"
                changed += 1
            unit.unit_data = cd
            unit.save(update_fields=["unit_data"])
        return {"changed": changed, "count": len(subordinates)}

    @classmethod
    def get_prefecture_personnel_payload(cls, game) -> dict:
        """Return personnel-tab payload for prefecture mode."""
        season = game.current_season
        moy = month_of_year(season)
        display_year = cls.display_year_for_season(season)
        subordinates = list(
            AdminUnit.objects.filter(
                game=game, unit_type="COUNTY", parent=game.player_unit,
            ).order_by("id")
        )
        has_previous_cycle = any(
            cls._find_cycle(unit.unit_data, display_year) is not None for unit in subordinates
        ) if moy == 1 and display_year >= 1 else False
        available = moy in {11, 12} or (moy == 1 and display_year >= 1 and has_previous_cycle)
        phase = None
        if available:
            if moy == 11:
                phase = "self_statement"
            elif moy == 12:
                phase = "review"
            else:
                phase = "published"

        if moy in {11, 12}:
            cls.ensure_prefecture_self_reviews(game)

        counties = []
        summary = {
            "total": 0,
            "submitted": 0,
            "reviewed": 0,
            "finalized": 0,
            "poor": 0,
        }
        for unit in subordinates:
            cycle = cls._find_cycle(unit.unit_data, display_year)
            item = cls._serialize_prefecture_cycle(unit, cycle, phase)
            counties.append(item)
            summary["total"] += 1
            if item["self_statement"]:
                summary["submitted"] += 1
            if item["prefect_review"]:
                summary["reviewed"] += 1
            if item["governor_recheck"]:
                summary["finalized"] += 1
            if item["final_grade"] == "差":
                summary["poor"] += 1

        return {
            "available": available,
            "phase": phase,
            "display_year": display_year,
            "current_month": moy,
            "summary": summary,
            "counties": counties,
        }

    @classmethod
    def get_prefecture_advance_blocker(cls, game) -> Optional[str]:
        """December cannot advance before prefect reviews every subordinate."""
        if month_of_year(game.current_season) != 12:
            return None

        cls.ensure_prefecture_self_reviews(game)
        review_year = year_of(game.current_season)
        pending = []
        subordinates = AdminUnit.objects.filter(
            game=game, unit_type="COUNTY", parent=game.player_unit,
        ).order_by("id")
        for unit in subordinates:
            cycle = cls._find_cycle(unit.unit_data, review_year)
            if not cycle or not cycle.get("prefect_review"):
                pending.append(unit.unit_data.get("county_name", f"州县#{unit.id}"))
        if not pending:
            return None
        names = "、".join(pending[:3])
        if len(pending) > 3:
            names += "等"
        return f"腊月须先完成所有下属年度评议，尚未评议：{names}"

    @classmethod
    def submit_prefecture_review(
        cls,
        game,
        unit_id: int,
        grade: str,
        strengths: str,
        weaknesses: str,
        focus: str,
    ) -> dict:
        """Persist one prefect review during December."""
        if month_of_year(game.current_season) != 12:
            return {"error": "年度评议仅可在腊月（十二月）提交"}
        if grade not in cls.GRADES:
            return {"error": "grade 必须为 优/良/中/差"}
        strengths = (strengths or "").strip()
        weaknesses = (weaknesses or "").strip()
        focus = (focus or "").strip()
        if not (strengths and weaknesses and focus):
            return {"error": "评语三部分均不能为空"}

        cls.ensure_prefecture_self_reviews(game)
        unit = AdminUnit.objects.filter(
            id=unit_id, game=game, unit_type="COUNTY", parent=game.player_unit,
        ).first()
        if unit is None:
            return {"error": "下辖县州不存在"}

        cycle = cls._find_cycle(unit.unit_data, year_of(game.current_season))
        if cycle is None or not cycle.get("self_statement"):
            return {"error": "该下属尚未形成年度自陈"}

        cycle["prefect_review"] = {
            "grade": grade,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "focus": focus,
            "reviewer_name": "本府知府",
            "review_season": game.current_season,
        }
        cycle["state"] = "prefect_reviewed"
        unit.save(update_fields=["unit_data"])
        return cls._serialize_prefecture_cycle(unit, cycle, "review")

    @classmethod
    def handle_prefecture_transition(
        cls,
        game,
        processed_season: int,
        next_season: int,
    ) -> dict:
        """Apply annual-review hooks around prefecture month transitions."""
        result: Dict[str, object] = {}
        next_moy = month_of_year(next_season)

        if next_moy == 11:
            prepared = cls.ensure_prefecture_self_reviews(game)
            result["personnel_opened"] = True
            result["personnel_ready_count"] = prepared.get("count", 0)

        elif next_moy == 1:
            finalized = cls.finalize_prefecture_reviews(game, next_season)
            result["personnel_result"] = finalized

        return result

    @classmethod
    def finalize_prefecture_reviews(cls, game, publish_season: int) -> dict:
        """Governor recheck + dismissal/replacement when entering January."""
        review_year = year_of(publish_season) - 1
        subordinates = list(
            AdminUnit.objects.filter(
                game=game, unit_type="COUNTY", parent=game.player_unit,
            ).order_by("id")
        )
        summary = {
            "year": review_year,
            "finalized": 0,
            "adjusted": 0,
            "replaced": 0,
            "results": [],
        }

        for unit in subordinates:
            cd = unit.unit_data
            cycle = cls._find_cycle(cd, review_year)
            if cycle is None or not cycle.get("prefect_review"):
                continue

            snapshot = cls._build_objective_snapshot(cd, publish_season)
            cycle["objective_snapshot"] = snapshot
            recheck = cls._build_governor_recheck(
                county=cd,
                cycle=cycle,
                season=publish_season,
                reviewer_name="本省巡抚",
            )
            cycle["governor_recheck"] = recheck
            cycle["final_grade"] = recheck["final_grade"]
            cycle["state"] = "finalized"
            cycle["published_season"] = publish_season
            cycle["replacement"] = None

            replacement_name = ""
            if recheck["final_grade"] == "差":
                replacement_name = cls._appoint_successor(unit, publish_season)
                cycle["replacement"] = {
                    "season": publish_season,
                    "incoming_name": replacement_name,
                }
                summary["replaced"] += 1

            if recheck["decision"] == "改定":
                summary["adjusted"] += 1
            summary["finalized"] += 1
            summary["results"].append({
                "unit_id": unit.id,
                "county_name": cd.get("county_name", ""),
                "subject_name": cycle.get("subject_name", ""),
                "final_grade": recheck["final_grade"],
                "decision": recheck["decision"],
                "replacement_name": replacement_name,
            })
            unit.unit_data = cd
            unit.save(update_fields=["unit_data"])

        if summary["finalized"] > 0:
            pdata = game.player_unit.unit_data
            pdata["personnel_last_result"] = summary
            game.player_unit.unit_data = pdata
            game.player_unit.save(update_fields=["unit_data"])

        return summary

    @classmethod
    def display_year_for_season(cls, season: int) -> int:
        """November/December show current year; January shows previous year results."""
        year = year_of(season)
        return year - 1 if month_of_year(season) == 1 else year

    @classmethod
    def _serialize_cycle(cls, cycle: Optional[dict]) -> Optional[dict]:
        if cycle is None:
            return None
        return {
            "year": cycle.get("year"),
            "state": cycle.get("state"),
            "objective_snapshot": cycle.get("objective_snapshot"),
            "self_statement": cycle.get("self_statement"),
            "self_statement_meta": cycle.get("self_statement_meta"),
            "prefect_review": cycle.get("prefect_review"),
            "governor_recheck": cycle.get("governor_recheck"),
            "final_grade": cycle.get("final_grade"),
            "published_season": cycle.get("published_season"),
        }

    @classmethod
    def _serialize_prefecture_cycle(cls, unit: AdminUnit, cycle: Optional[dict], phase: Optional[str]) -> dict:
        cd = unit.unit_data
        gp = cd.get("governor_profile", {})
        return {
            "unit_id": unit.id,
            "county_name": cd.get("county_name", ""),
            "governor_name": gp.get("name", ""),
            "governor_style": gp.get("style", ""),
            "governor_archetype": gp.get("archetype", "MIDDLING"),
            "review_subject_name": cycle.get("subject_name", gp.get("name", "")) if cycle else gp.get("name", ""),
            "review_subject_style": cycle.get("subject_style", gp.get("style", "")) if cycle else gp.get("style", ""),
            "review_subject_archetype": cycle.get("subject_archetype", gp.get("archetype", "MIDDLING")) if cycle else gp.get("archetype", "MIDDLING"),
            "review_state": cycle.get("state") if cycle else "",
            "objective_snapshot": cycle.get("objective_snapshot") if cycle else None,
            "self_statement": cycle.get("self_statement") if cycle else None,
            "self_statement_meta": cycle.get("self_statement_meta") if cycle else None,
            "prefect_review": cycle.get("prefect_review") if cycle else None,
            "governor_recheck": cycle.get("governor_recheck") if cycle else None,
            "final_grade": cycle.get("final_grade") if cycle else "",
            "replacement": cycle.get("replacement") if cycle else None,
            "can_review": bool(phase == "review"),
        }

    @classmethod
    def _normalize_statement_payload(cls, payload: dict) -> Optional[dict]:
        if not isinstance(payload, dict):
            return None
        fields = {
            "achievements": (payload.get("achievements") or "").strip(),
            "unfinished": (payload.get("unfinished") or "").strip(),
            "faults": (payload.get("faults") or "").strip(),
            "plan": (payload.get("plan") or "").strip(),
        }
        if any(not value for value in fields.values()):
            return None
        return fields

    @classmethod
    def _build_cycle(
        cls,
        review_year: int,
        season: int,
        subject_name: str,
        subject_style: str,
        subject_archetype: str,
        objective_snapshot: Optional[dict] = None,
    ) -> dict:
        return {
            "year": review_year,
            "opened_season": season,
            "state": "self_review_pending",
            "subject_name": subject_name,
            "subject_style": subject_style,
            "subject_archetype": subject_archetype,
            "objective_snapshot": objective_snapshot or {},
            "self_statement": None,
            "self_statement_meta": {},
            "prefect_review": None,
            "governor_recheck": None,
            "final_grade": "",
            "published_season": None,
            "replacement": None,
        }

    @classmethod
    def _reviews_list(cls, state: dict) -> list:
        reviews = state.get("annual_reviews")
        if not isinstance(reviews, list):
            reviews = []
            state["annual_reviews"] = reviews
        return reviews

    @classmethod
    def _find_cycle(cls, state: dict, review_year: int) -> Optional[dict]:
        for item in cls._reviews_list(state):
            if item.get("year") == review_year:
                return item
        return None

    @classmethod
    def _ensure_cycle(
        cls,
        state: dict,
        review_year: int,
        season: int,
        subject_name: str,
        subject_style: str,
        subject_archetype: str,
        objective_snapshot: Optional[dict] = None,
    ) -> dict:
        cycle = cls._find_cycle(state, review_year)
        if cycle is not None:
            if objective_snapshot:
                cycle["objective_snapshot"] = objective_snapshot
            return cycle

        cycle = cls._build_cycle(
            review_year=review_year,
            season=season,
            subject_name=subject_name,
            subject_style=subject_style,
            subject_archetype=subject_archetype,
            objective_snapshot=objective_snapshot,
        )
        cls._reviews_list(state).append(cycle)
        cls._reviews_list(state).sort(key=lambda item: item.get("year", 0))
        return cycle

    @classmethod
    def _build_objective_snapshot(cls, county: dict, season: int) -> dict:
        fy = county.get("fiscal_year") or {}
        quota = county.get("annual_quota") or {}
        annual_quota = float(quota.get("total", 0) or 0)
        annual_collected = float(fy.get("agri_remitted", 0) or 0)
        annual_collected += max(0.0, float(fy.get("commercial_tax", 0) or 0) - float(fy.get("commercial_retained", 0) or 0))
        annual_collected += max(0.0, float(fy.get("corvee_tax", 0) or 0) - float(fy.get("corvee_retained", 0) or 0))
        quota_completion_pct = round((annual_collected / annual_quota) * 100, 1) if annual_quota > 0 else 0.0

        morale = float(county.get("morale", 50) or 0)
        security = float(county.get("security", 50) or 0)
        commercial = float(county.get("commercial", 50) or 0)
        education = float(county.get("education", 50) or 0)
        treasury = float(county.get("treasury", 0) or 0)
        treasury_score = max(0.0, min(100.0, treasury / 8.0))
        stability_score = (morale + security) / 2.0
        development_score = (commercial + education) / 2.0

        incident_flags = []
        incident_penalty = 0.0
        disaster = county.get("disaster_this_year") or {}
        if disaster.get("type"):
            incident_flags.append("本年有灾情")
            incident_penalty += 8.0
        relief = county.get("relief_application") or {}
        if relief.get("status") == "CAUGHT":
            incident_flags.append("减免申报失实")
            incident_penalty += 15.0
        emergency = county.get("emergency") or {}
        riot = emergency.get("riot") or {}
        if riot.get("active"):
            incident_flags.append("发生暴动")
            incident_penalty += 20.0
        takeover = emergency.get("prefect_takeover") or {}
        if takeover.get("active"):
            incident_flags.append("知府接管")
            incident_penalty += 15.0

        score = (
            min(100.0, quota_completion_pct) * 0.35
            + stability_score * 0.35
            + development_score * 0.20
            + treasury_score * 0.10
            - incident_penalty
        )
        score = round(max(0.0, min(100.0, score)), 1)

        return {
            "season": season,
            "annual_quota": round(annual_quota, 1),
            "annual_collected": round(annual_collected, 1),
            "quota_completion_pct": quota_completion_pct,
            "morale": round(morale, 1),
            "security": round(security, 1),
            "commercial": round(commercial, 1),
            "education": round(education, 1),
            "treasury": round(treasury, 1),
            "objective_score": score,
            "objective_grade": cls._grade_from_score(score),
            "incident_flags": incident_flags,
        }

    @classmethod
    def _estimate_statement_risk(cls, statement: dict, snapshot: dict) -> Tuple[int, List[str]]:
        penalty = 0
        flags: List[str] = []
        if cls._is_blankish(statement.get("unfinished")) and snapshot.get("quota_completion_pct", 0) < 85:
            penalty += 6
            flags.append("未完事项交代偏少")
        if cls._is_blankish(statement.get("faults")) and (
            snapshot.get("incident_flags") or snapshot.get("objective_score", 0) < 65
        ):
            penalty += 8
            flags.append("过失记录疑有隐瞒")
        if len(statement.get("achievements", "")) >= 80 and snapshot.get("objective_score", 0) < 60:
            penalty += 3
            flags.append("政绩表述偏于夸大")
        return penalty, flags

    @classmethod
    def _build_prefect_review(cls, county: dict, cycle: dict, season: int, reviewer_name: str) -> dict:
        snapshot = cls._build_objective_snapshot(county, season)
        cycle["objective_snapshot"] = snapshot
        candor_penalty = int((cycle.get("self_statement_meta") or {}).get("candor_penalty", 0))
        adjusted_score = max(0.0, snapshot.get("objective_score", 0) - candor_penalty)
        grade = cls._grade_from_score(adjusted_score)
        strengths = cls._build_strengths(snapshot)
        weaknesses = cls._build_weaknesses(snapshot, cycle.get("self_statement_meta") or {})
        focus = cls._build_focus(snapshot)
        return {
            "grade": grade,
            "score": round(adjusted_score, 1),
            "strengths": strengths,
            "weaknesses": weaknesses,
            "focus": focus,
            "reviewer_name": reviewer_name,
            "review_season": season,
        }

    @classmethod
    def _build_governor_recheck(cls, county: dict, cycle: dict, season: int, reviewer_name: str) -> dict:
        snapshot = cls._build_objective_snapshot(county, season)
        cycle["objective_snapshot"] = snapshot
        objective_grade = snapshot.get("objective_grade") or "中"
        prefect_grade = ((cycle.get("prefect_review") or {}).get("grade")) or objective_grade

        final_grade = prefect_grade
        decision = "维持"
        rank_gap = cls.GRADE_RANK[prefect_grade] - cls.GRADE_RANK[objective_grade]
        audit_flags = (cycle.get("self_statement_meta") or {}).get("audit_flags") or []

        if abs(rank_gap) >= 2:
            final_grade = objective_grade
            decision = "改定"
        elif rank_gap > 0 and audit_flags:
            final_grade = objective_grade
            decision = "改定"
        elif rank_gap < 0 and snapshot.get("objective_score", 0) >= 85:
            final_grade = objective_grade
            decision = "改定"

        if decision == "维持":
            comment = f"巡抚复核后，认定本年初评与实绩大体相符，维持{final_grade}。"
        else:
            comment = (
                f"巡抚复核后，认定初评与实绩不尽相符，"
                f"据客观情形改定为{final_grade}。"
            )
        return {
            "decision": decision,
            "final_grade": final_grade,
            "comment": comment,
            "reviewer_name": reviewer_name,
            "review_season": season,
        }

    @classmethod
    def _build_ai_self_statement(cls, county: dict, cycle: dict, snapshot: dict) -> dict:
        gp = county.get("governor_profile", {})
        style = gp.get("style", "")
        archetype = gp.get("archetype", "MIDDLING")
        strengths = cls._strength_labels(snapshot)
        weaknesses = cls._weakness_labels(snapshot)
        county_name = county.get("county_name", "本县")

        achievements = f"{county_name}本年以{strengths[0]}为先，"
        if len(strengths) > 1:
            achievements += f"兼顾{strengths[1]}，"
        achievements += "府里下达之事大体有次第推进。"

        unfinished = f"未竟之务主要在{weaknesses[0]}。"
        if len(weaknesses) > 1:
            unfinished += f"另有{weaknesses[1]}尚待收束。"

        if archetype == "CORRUPT":
            faults = "日常办差尚称无大错，细务间或失之急躁。"
        elif archetype == "VIRTUOUS":
            faults = f"本官自知在{weaknesses[0]}一项仍多不足，处置有时偏缓。"
        else:
            faults = f"过失主要在{weaknesses[0]}，尚未尽合上司所期。"

        if snapshot.get("incident_flags"):
            if archetype == "CORRUPT":
                faults = "本年亦有数事牵累吏治，然多属下情纷杂所致。"
            else:
                faults += " 又因本年有灾变牵制，处置未能周全。"

        if style == "minben":
            plan = "来年当先安民力、实仓廪，再图缓缓进取。"
        elif style == "zhengji":
            plan = "来年当补齐短板，务求赋税与文教两端并进。"
        elif style == "jinqu":
            plan = "来年当先整饬积弊，择要大举兴办，求见成效。"
        else:
            plan = "来年当循序整顿旧务，先稳后进，不使短板再拖累全局。"

        return {
            "achievements": achievements,
            "unfinished": unfinished,
            "faults": faults,
            "plan": plan,
        }

    @classmethod
    def _build_strengths(cls, snapshot: dict) -> str:
        labels = cls._strength_labels(snapshot)
        if len(labels) == 1:
            return f"{labels[0]}尚可，办事还有根底。"
        return f"{labels[0]}与{labels[1]}两端表现较稳，足见任事并非全无章法。"

    @classmethod
    def _build_weaknesses(cls, snapshot: dict, meta: dict) -> str:
        labels = cls._weakness_labels(snapshot)
        text = f"{labels[0]}偏弱，已成今年最明显的短板。"
        if len(labels) > 1:
            text += f" 另有{labels[1]}亦不可轻忽。"
        audit_flags = meta.get("audit_flags") or []
        if audit_flags:
            text += f" 自陈中并有“{audit_flags[0]}”之嫌。"
        return text

    @classmethod
    def _build_focus(cls, snapshot: dict) -> str:
        labels = cls._weakness_labels(snapshot)
        primary = labels[0]
        if primary == "税赋征解":
            return "来年务须先把赋税征解与岁收节奏理顺，不可再拖。"
        if primary == "民心治安":
            return "来年当先稳民心、肃治安，凡扰民失控之事不得再起。"
        if primary == "文教工商":
            return "来年宜补文教工商之弱，勿只顾眼前而伤及后势。"
        return "来年当先整饬县务根本，诸事以求稳求实为主。"

    @classmethod
    def _strength_labels(cls, snapshot: dict) -> List[str]:
        pairs = [
            ("税赋征解", min(100.0, float(snapshot.get("quota_completion_pct", 0) or 0))),
            ("民心治安", (float(snapshot.get("morale", 0)) + float(snapshot.get("security", 0))) / 2.0),
            ("文教工商", (float(snapshot.get("commercial", 0)) + float(snapshot.get("education", 0))) / 2.0),
            ("县库支应", max(0.0, min(100.0, float(snapshot.get("treasury", 0) or 0) / 8.0))),
        ]
        pairs.sort(key=lambda item: item[1], reverse=True)
        return [pairs[0][0], pairs[1][0]]

    @classmethod
    def _weakness_labels(cls, snapshot: dict) -> List[str]:
        pairs = [
            ("税赋征解", min(100.0, float(snapshot.get("quota_completion_pct", 0) or 0))),
            ("民心治安", (float(snapshot.get("morale", 0)) + float(snapshot.get("security", 0))) / 2.0),
            ("文教工商", (float(snapshot.get("commercial", 0)) + float(snapshot.get("education", 0))) / 2.0),
            ("县库支应", max(0.0, min(100.0, float(snapshot.get("treasury", 0) or 0) / 8.0))),
        ]
        pairs.sort(key=lambda item: item[1])
        return [pairs[0][0], pairs[1][0]]

    @classmethod
    def _appoint_successor(cls, unit: AdminUnit, season: int) -> str:
        cd = unit.unit_data
        old_profile = cd.get("governor_profile") or {}
        old_name = old_profile.get("name", "")
        county_type = cd.get("county_type", "fiscal_core")
        archetype = cls._pick_successor_archetype(county_type)
        style = random.choice(ARCHETYPE_TO_STYLES.get(archetype, ["yuanhua"]))
        new_name = cls._pick_new_governor_name(
            unit.parent.children.exclude(id=unit.id),
            excluded={old_name},
        )
        cd["governor_profile"] = {
            **generate_governor_profile(style, archetype=archetype),
            "name": new_name,
            "style": style,
            "archetype": archetype,
            "bio": f"{new_name}，新任{cd.get('county_name', '本县')}知县，甫经调补到任，正待整顿旧务。",
        }
        cd["prefect_affinity"] = 50.0
        cd["_last_ai_actions"] = "新官到任，先行熟悉县务"
        return new_name

    @classmethod
    def _pick_successor_archetype(cls, county_type: str) -> str:
        weights = ARCHETYPE_COUNTY_TYPE_WEIGHTS.get(county_type, [0.25, 0.55, 0.20])
        labels = ["VIRTUOUS", "MIDDLING", "CORRUPT"]
        return random.choices(labels, weights=weights, k=1)[0]

    @classmethod
    def _pick_new_governor_name(cls, siblings: Iterable[AdminUnit], excluded: set) -> str:
        used = set(excluded)
        for sibling in siblings:
            gp = sibling.unit_data.get("governor_profile", {})
            if gp.get("name"):
                used.add(gp["name"])
        for _ in range(200):
            name = random.choice(list(GOVERNOR_SURNAMES)) + random.choice(list(GOVERNOR_GIVEN_NAMES))
            if name not in used:
                return name
        return f"新任知县{random.randint(1, 999)}"

    @classmethod
    def _grade_from_score(cls, score: float) -> str:
        if score >= 85:
            return "优"
        if score >= 70:
            return "良"
        if score >= 55:
            return "中"
        return "差"

    @staticmethod
    def _is_blankish(text: Optional[str]) -> bool:
        if text is None:
            return True
        compact = "".join(str(text).split())
        for token in ("。", "，", "；", "、", ".", ",", ";", ":", "："):
            compact = compact.replace(token, "")
        return compact in {"", "无", "暂无", "无有", "无特记事项", "未有"}
