"""新版司法流转：模板实例化、县级审理、府级复审。"""

from __future__ import annotations

import copy
import json
import logging
import os
import random
import threading
import time
from datetime import timedelta
from typing import Dict, List, Optional, Sequence

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from llm.client import LLMClient
from llm.prompts import PromptRegistry

from ..models import AdminUnit, GameState, JudicialCaseInstance, JudicialGenerationState
from .constants import MAX_MONTH, month_of_year, month_name
from .eventlog import adjust_player_profile_stat, log_game_event
from .local_npc import ensure_county_local_cast, surname_from_village
from .settlement_metrics import MetricsMixin

logger = logging.getLogger("game")

COUNTY_JUDICIAL_MONTHS = {2, 5, 8, 11}
PREFECT_JUDICIAL_MONTHS = {3, 6, 9, 12}
CASES_PER_WINDOW = 3


class JudicialCaseflowService:
    _template_cache: Optional[List[dict]] = None

    @classmethod
    def schedule_generation(cls, game_id: int) -> None:
        threading.Thread(target=cls._generate_for_game_id, args=(game_id,), daemon=True).start()

    @classmethod
    def _generate_for_game_id(cls, game_id: int) -> None:
        try:
            game = GameState.objects.select_related("player_unit").get(id=game_id)
        except GameState.DoesNotExist:
            return
        try:
            cls.ensure_generation_progress(game)
        except Exception as exc:
            logger.warning("Judicial generation failed for game %s: %s", game_id, exc)

    @classmethod
    def load_templates(cls) -> List[dict]:
        if cls._template_cache is not None:
            return cls._template_cache
        pool_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "judicial_cases_county.json"))
        with open(pool_path, encoding="utf-8") as fh:
            data = json.load(fh)
        cls._template_cache = list(data.get("cases", []))
        return cls._template_cache

    @classmethod
    def county_review_seasons(cls) -> List[int]:
        seasons = []
        for base in (2, 5, 8, 11):
            cur = base
            while cur <= MAX_MONTH:
                seasons.append(cur)
                cur += 12
        return seasons

    @classmethod
    def ensure_generation_progress(cls, game, budget_windows: Optional[int] = None) -> JudicialGenerationState:
        state, _ = JudicialGenerationState.objects.get_or_create(game=game)
        county_units = cls._target_county_units(game)
        windows = cls.county_review_seasons()
        state.total_windows = len(county_units) * len(windows)
        state.started_at = state.started_at or timezone.now()

        missing = []
        for unit in county_units:
            for season in windows:
                existing = JudicialCaseInstance.objects.filter(
                    game=game, county_unit=unit, county_review_season=season,
                ).count()
                if existing < CASES_PER_WINDOW:
                    missing.append((unit, season, existing))

        if not missing:
            state.generated_windows = state.total_windows
            state.status = "READY"
            state.last_error = ""
            state.finished_at = timezone.now()
            state.save(update_fields=["total_windows", "generated_windows", "status", "last_error", "finished_at", "updated_at"])
            return state

        state.status = "RUNNING"
        state.last_error = ""
        state.save(update_fields=["total_windows", "status", "last_error", "started_at", "updated_at"])

        templates = cls.load_templates()
        work_items = missing[:budget_windows] if budget_windows else missing
        try:
            for unit, season, existing in work_items:
                cls._generate_window_cases(game, unit, season, existing, templates)
        except Exception as exc:
            state.status = "FAILED"
            state.last_error = str(exc)
            state.save(update_fields=["status", "last_error", "updated_at"])
            raise

        generated_windows = 0
        for unit in county_units:
            for season in windows:
                if JudicialCaseInstance.objects.filter(
                    game=game, county_unit=unit, county_review_season=season,
                ).count() >= CASES_PER_WINDOW:
                    generated_windows += 1
        state.generated_windows = generated_windows
        state.status = "READY" if generated_windows >= state.total_windows else "RUNNING"
        if state.status == "READY":
            state.finished_at = timezone.now()
        state.save(update_fields=["generated_windows", "status", "finished_at", "updated_at"])
        return state

    @classmethod
    def get_county_payload(cls, game) -> dict:
        state = cls.ensure_generation_progress(game, budget_windows=2)
        season = game.current_season
        available = month_of_year(season) in COUNTY_JUDICIAL_MONTHS
        cases: List[dict] = []
        if available and game.player_unit_id:
            queryset = JudicialCaseInstance.objects.filter(
                game=game,
                county_unit=game.player_unit,
                county_review_season=season,
                status__in=["PENDING_MAGISTRATE_ROUND_1", "PENDING_MAGISTRATE_ROUND_2"],
            ).order_by("id")
            cases = [cls._serialize_county_case(item) for item in queryset]

        # 已审案件：当前任期内所有已处理案件（供归档展示）
        reviewed_cases: List[dict] = []
        if game.player_unit_id:
            reviewed_queryset = JudicialCaseInstance.objects.filter(
                game=game,
                county_unit=game.player_unit,
            ).exclude(
                status__in=["PENDING_MAGISTRATE_ROUND_1", "PENDING_MAGISTRATE_ROUND_2"],
            ).order_by("-county_review_season", "id")
            reviewed_cases = [cls._serialize_reviewed_case(item) for item in reviewed_queryset]

        return {
            "available": available,
            "current_season": season,
            "generation": cls._serialize_generation_state(state),
            "pending_count": len(cases),
            "cases": cases,
            "reviewed_cases": reviewed_cases,
        }

    @classmethod
    def _serialize_reviewed_case(cls, instance: JudicialCaseInstance) -> dict:
        """归档展示用：仅返回案件摘要，不含完整卷宗。"""
        payload = instance.local_payload or {}
        latest_magistrate = (instance.magistrate_rounds or [])[-1] if instance.magistrate_rounds else {}
        return {
            "instance_id": instance.id,
            "case_name": payload.get("case_name", ""),
            "category": payload.get("category", ""),
            "difficulty": payload.get("difficulty", ""),
            "county_review_season": instance.county_review_season,
            "status": instance.status,
            "magistrate_action": latest_magistrate.get("action_label", ""),
            "verdict_label": latest_magistrate.get("verdict_label", ""),
            "verdict_code": latest_magistrate.get("verdict_code", ""),
            "dossier_text": payload.get("dossier_text", ""),
            "verdict_options": payload.get("verdict_options") or [],
            "prefect_decision": instance.prefect_decision,
        }

    @classmethod
    def get_county_advance_blocker(cls, game) -> Optional[str]:
        """若当前月份有未审结案件，返回阻止推进的提示文字；否则返回 None。"""
        if not game.player_unit_id:
            return None
        if month_of_year(game.current_season) not in COUNTY_JUDICIAL_MONTHS:
            return None
        pending = JudicialCaseInstance.objects.filter(
            game=game,
            county_unit=game.player_unit,
            county_review_season=game.current_season,
            status__in=["PENDING_MAGISTRATE_ROUND_1", "PENDING_MAGISTRATE_ROUND_2"],
        ).count()
        if pending:
            return f"司法月份尚有 {pending} 件案件待审，必须先完成案件审理方可推进月份"
        return None

    @classmethod
    def decide_county_case(cls, game, case_instance_id: int, action: str, verdict_code: Optional[str] = None) -> dict:
        if not game.player_unit_id:
            return {"error": "县域数据未初始化"}
        season = game.current_season
        if month_of_year(season) not in COUNTY_JUDICIAL_MONTHS:
            return {"error": "当前不在县级司法处理月份"}

        applied_effects: Optional[dict] = None
        instance = JudicialCaseInstance.objects.filter(
            id=case_instance_id,
            game=game,
            county_unit=game.player_unit,
            county_review_season=season,
        ).first()
        if instance is None:
            return {"error": "案件不存在"}

        latest_assistant = (instance.assistant_rounds or [])[-1] if instance.assistant_rounds else {}
        magistrate_rounds = list(instance.magistrate_rounds or [])
        status = instance.status

        if status == "PENDING_MAGISTRATE_ROUND_1":
            if action == "判决":
                result = cls._apply_player_verdict(instance, magistrate_rounds, round_no=1, season=season, verdict_code=verdict_code)
                if "error" in result:
                    return result
                cls._apply_verdict_effects(game, result["selected_option"])
                applied_effects = cls._clamp_verdict_effects(
                    result["selected_option"].get("immediate_effects") or {}
                )
                instance.status = "SUBMITTED_TO_PREFECT"
                instance.submitted_to_prefect = True
                instance.submitted_season = season
            elif action == "搁置委托上级裁定":
                magistrate_rounds.append({"round_no": 1, "season": season, "action": "DEFER_TO_PREFECT", "action_label": "搁置委托上级裁定"})
                instance.submitted_to_prefect = True
                instance.submitted_season = season
                instance.status = "DEFERRED_TO_PREFECT"
                cls._apply_defer_penalty(game)
            else:
                return {"error": "第一轮仅可选择判决或搁置委托上级裁定"}

        elif status == "PENDING_MAGISTRATE_ROUND_2":
            if action == "判决":
                result = cls._apply_player_verdict(instance, magistrate_rounds, round_no=2, season=season, verdict_code=verdict_code)
                if "error" in result:
                    return result
                cls._apply_verdict_effects(game, result["selected_option"])
                applied_effects = cls._clamp_verdict_effects(
                    result["selected_option"].get("immediate_effects") or {}
                )
                instance.submitted_to_prefect = True
                instance.submitted_season = season
                instance.status = "SUBMITTED_TO_PREFECT"
            elif action == "搁置委托上级裁定":
                magistrate_rounds.append({"round_no": 2, "season": season, "action": "DEFER_TO_PREFECT", "action_label": "搁置委托上级裁定"})
                instance.submitted_to_prefect = True
                instance.submitted_season = season
                instance.status = "DEFERRED_TO_PREFECT"
                cls._apply_defer_penalty(game)
            else:
                return {"error": "第二轮仅可选择判决或搁置委托上级裁定"}
        else:
            return {"error": "该案当前不可处理"}

        instance.magistrate_rounds = magistrate_rounds
        instance.save(update_fields=["assistant_rounds", "magistrate_rounds", "status", "submitted_to_prefect", "submitted_season", "updated_at"])

        last_round = (instance.magistrate_rounds or [])[-1] if instance.magistrate_rounds else {}
        cls._log_player_case_action(
            game,
            instance,
            action,
            season,
            verdict_code=last_round.get("verdict_code"),
            verdict_label=last_round.get("verdict_label", ""),
            round_no=int(last_round.get("round_no", 0) or 0),
            status_after=instance.status,
            applied_effects=applied_effects,
        )

        resp: dict = {
            "case": cls._serialize_county_case(instance),
            "message": f"已处理：{instance.local_payload.get('case_name', '')} - {action}",
        }
        if applied_effects is not None:
            resp["applied_effects"] = applied_effects
        return resp

    @classmethod
    def _apply_player_verdict(cls, instance: JudicialCaseInstance, magistrate_rounds: list, round_no: int, season: int, verdict_code: Optional[str]) -> dict:
        """校验并记录玩家判决，返回 {'selected_option': ...} 或 {'error': ...}。"""
        if not verdict_code:
            return {"error": "判决时需提供verdict_code"}
        verdict_options = instance.local_payload.get("verdict_options") or []
        selected = next((o for o in verdict_options if o.get("verdict_code") == verdict_code), None)
        if selected is None:
            return {"error": f"无效的verdict_code: {verdict_code}"}
        magistrate_rounds.append({
            "round_no": round_no,
            "season": season,
            "action": "VERDICT",
            "action_label": "判决",
            "verdict_code": verdict_code,
            "verdict_label": selected.get("verdict_label", verdict_code),
            "immediate_effects": selected.get("immediate_effects", {}),
        })
        return {"selected_option": selected}

    @staticmethod
    def _clamp_verdict_effects(effects: dict) -> dict:
        """返回实际落地的效果值（已按上限截断，与 _apply_verdict_effects_to_unit 保持一致）。"""
        clamped = {}
        for field in ("morale", "security"):
            delta = effects.get(field, 0)
            if delta:
                clamped[field] = max(-2, min(2, delta))
        for field in ("gentry_favor", "commercial", "education"):
            delta = effects.get(field, 0)
            if delta:
                clamped[field] = max(-5, min(5, delta))
        if effects.get("treasury"):
            clamped["treasury"] = effects["treasury"]
        return clamped

    @classmethod
    def _apply_verdict_effects_to_unit(cls, unit, verdict_option: dict) -> None:
        """将判决效果写入 AdminUnit.unit_data（玩家县和AI县共用）。"""
        effects = verdict_option.get("immediate_effects") or {}
        if not effects:
            return
        data = dict(unit.unit_data or {})
        # 民心、治安：上限 ±2，同步更新各村庄数值，防止县级与村级数值不一致
        for field in ("morale", "security"):
            delta = effects.get(field, 0)
            if delta:
                delta = max(-2, min(2, delta))
                MetricsMixin.apply_county_stat_delta(data, field, delta)
        # 其他字段：上限 ±5，直接写入
        for field in ("gentry_favor", "commercial", "education"):
            delta = effects.get(field, 0)
            if delta:
                delta = max(-5, min(5, delta))
                data[field] = max(0, min(100, round(float(data.get(field, 50)) + delta, 1)))
        treasury_delta = effects.get("treasury", 0)
        if treasury_delta:
            data["treasury"] = max(0, round(float(data.get("treasury", 0)) + treasury_delta, 2))
        unit.unit_data = data
        unit.save(update_fields=["unit_data"])

    @classmethod
    def _apply_verdict_effects(cls, game, verdict_option: dict) -> None:
        """玩家路径：将判决效果写入县域 unit_data。"""
        if not game.player_unit_id:
            return
        cls._apply_verdict_effects_to_unit(game.player_unit, verdict_option)

    @classmethod
    def _log_player_case_action(
        cls,
        game,
        instance: JudicialCaseInstance,
        action: str,
        season: int,
        *,
        verdict_code: Optional[str] = None,
        verdict_label: str = "",
        round_no: int = 0,
        status_after: str = "",
        applied_effects: Optional[dict] = None,
    ) -> None:
        case_name = (instance.local_payload or {}).get("case_name", "未名案件")
        if action == "判决":
            event_type = "player_judicial_verdict"
            description = f"《{case_name}》第{round_no}轮判决：{verdict_label or verdict_code or '未定'}"
        elif action == "打回重审":
            event_type = "player_judicial_remand"
            description = f"《{case_name}》第{round_no}轮打回重审"
        else:
            event_type = "player_judicial_defer"
            description = f"《{case_name}》第{round_no}轮搁置并委托知府裁定"

        log_game_event(
            game,
            event_type=event_type,
            category="JUDICIAL",
            season=season,
            description=description,
            choice=action,
            data={
                "case_id": instance.id,
                "case_name": case_name,
                "round_no": round_no,
                "action": action,
                "verdict_code": verdict_code,
                "verdict_label": verdict_label,
                "status_after": status_after,
                "immediate_effects": applied_effects or {},
            },
        )

    @classmethod
    def _settle_player_competence_after_prefect_review(
        cls,
        game,
        instance: JudicialCaseInstance,
        *,
        season: int,
        overturned: bool,
        prefect_verdict_code: Optional[str],
    ) -> int:
        last_verdict = None
        for item in reversed(instance.magistrate_rounds or []):
            if item.get("action") == "VERDICT":
                last_verdict = item
                break
        if last_verdict is None:
            return 0

        payload = instance.local_payload or {}
        difficulty = payload.get("difficulty", "")
        delta = 2 if difficulty == "高难" else 1
        if overturned:
            delta = -delta

        return adjust_player_profile_stat(
            game,
            "competence",
            delta,
            source_event="prefect_judicial_review",
            source_label=payload.get("case_name", "司法复审"),
            extra_data={
                "case_id": instance.id,
                "difficulty": difficulty,
                "overturned": overturned,
                "magistrate_verdict_code": last_verdict.get("verdict_code"),
                "prefect_verdict_code": prefect_verdict_code,
                "season": season,
            },
        )

    @classmethod
    def auto_process_ai_counties(cls, game, season: int) -> dict:
        if month_of_year(season) not in COUNTY_JUDICIAL_MONTHS:
            return {"processed": 0}
        cls.ensure_generation_progress(game, budget_windows=4)
        if game.player_role != "PREFECT" or not game.player_unit_id:
            return {"processed": 0}

        # 按县收集待处理案件（同一县的多个案件必须顺序处理，避免 unit_data 竞争写入）
        county_work: dict = {}
        counties = AdminUnit.objects.filter(game=game, unit_type="COUNTY", parent=game.player_unit)
        for unit in counties:
            queryset = JudicialCaseInstance.objects.filter(
                game=game,
                county_unit=unit,
                county_review_season=season,
                status__in=["PENDING_MAGISTRATE_ROUND_1", "PENDING_MAGISTRATE_ROUND_2"],
            ).order_by("id")
            instances = list(queryset)
            if instances:
                county_work[unit.id] = (unit, instances)

        if not county_work:
            return {"processed": 0}

        # 跨县并行，同县内顺序处理（避免同一 AdminUnit.unit_data 被多线程同时写入）
        from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

        def _process_county(unit, instances):
            from django.db import connection as _conn
            count = 0
            try:
                for instance in instances:
                    try:
                        cls._auto_decide_ai_case(unit, instance, season)
                        count += 1
                    except Exception as exc:
                        logger.warning("AI judicial auto-decide failed for case %s: %s", instance.id, exc)
            finally:
                _conn.close()
            return count

        processed = 0
        max_workers = min(5, len(county_work))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_process_county, unit, instances)
                for unit, instances in county_work.values()
            ]
            for f in _as_completed(futures):
                processed += f.result()

        return {"processed": processed}

    @classmethod
    def get_prefecture_payload(cls, game) -> dict:
        season = game.current_season
        if not game.player_unit_id:
            return {"pending_cases": [], "judicial_log": [], "judicial_meta": {}}
        queryset = JudicialCaseInstance.objects.filter(
            game=game,
            prefect_unit=game.player_unit,
            prefect_review_season=season,
            status__in=["SUBMITTED_TO_PREFECT", "DEFERRED_TO_PREFECT"],
        ).order_by("id")
        pending_cases = [cls._serialize_prefect_case(item) for item in queryset]
        pdata = game.player_unit.unit_data
        return {
            "pending_cases": pending_cases,
            "judicial_log": pdata.get("judicial_log", []),
            "judicial_meta": {
                "judicial_prestige": pdata.get("judicial_prestige", 50),
                "inspector_favor": pdata.get("inspector_favor", 50),
            },
        }

    @classmethod
    def get_debug_payload(cls, game) -> dict:
        state = cls.ensure_generation_progress(game, budget_windows=2)
        queryset = JudicialCaseInstance.objects.filter(game=game).select_related("county_unit", "prefect_unit").order_by("county_review_season", "county_unit_id", "id")[:200]
        status_summary: Dict[str, int] = {}
        cases = []
        for item in queryset:
            status_summary[item.status] = status_summary.get(item.status, 0) + 1
            cases.append({
                "instance_id": item.id,
                "template_case_id": item.template_case_id,
                "county_name": item.local_payload.get("source_county", ""),
                "case_name": item.local_payload.get("case_name", ""),
                "county_review_season": item.county_review_season,
                "prefect_review_season": item.prefect_review_season,
                "status": item.status,
                "assistant_rounds": item.assistant_rounds,
                "magistrate_rounds": item.magistrate_rounds,
                "submitted_to_prefect": item.submitted_to_prefect,
                "prefect_decision": item.prefect_decision,
                "actor_map": item.actor_map,
            })
        return {
            "generation": cls._serialize_generation_state(state),
            "cases": cases,
            "status_summary": status_summary,
        }

    @classmethod
    def _target_county_units(cls, game) -> List[AdminUnit]:
        if game.player_role == "PREFECT" and game.player_unit_id:
            return list(AdminUnit.objects.filter(game=game, unit_type="COUNTY", parent=game.player_unit).order_by("id"))
        if game.player_unit_id and game.player_unit.unit_type == "COUNTY":
            return [game.player_unit]
        return []

    @classmethod
    def _generate_window_cases(cls, game, unit, season: int, existing_count: int, templates: Sequence[dict]) -> None:
        needed = max(0, CASES_PER_WINDOW - existing_count)
        if needed == 0:
            return
        rnd = random.Random(f"judicial:{game.id}:{unit.id}:{season}")
        existing_template_ids = list(
            JudicialCaseInstance.objects.filter(game=game, county_unit=unit, county_review_season=season)
            .values_list("template_case_id", flat=True)
        )
        pool = [copy.deepcopy(item) for item in templates]
        rnd.shuffle(pool)
        picks = []
        for item in pool:
            if item.get("case_id") not in existing_template_ids:
                picks.append(item)
            if len(picks) >= needed:
                break
        while len(picks) < needed:
            picks.append(copy.deepcopy(rnd.choice(pool)))

        with transaction.atomic():
            for template in picks[:needed]:
                payload, actor_map = cls._materialize_template(template, unit, season)
                first_opinion = cls._build_assistant_opinion(payload, round_no=1)
                JudicialCaseInstance.objects.create(
                    game=game,
                    county_unit=unit,
                    prefect_unit=unit.parent if unit.parent_id else None,
                    template_case_id=template.get("case_id", ""),
                    county_review_season=season,
                    prefect_review_season=season + 1,
                    status="PENDING_MAGISTRATE_ROUND_1",
                    local_payload=payload,
                    actor_map=actor_map,
                    assistant_rounds=[first_opinion],
                    magistrate_rounds=[],
                    debug_meta={"generation_seed": f"judicial:{game.id}:{unit.id}:{season}"},
                )

    @classmethod
    def _materialize_template(cls, template: dict, unit, season: int) -> tuple:
        county = copy.deepcopy(unit.unit_data)
        ensure_county_local_cast(county)
        villages = county.get("villages") or []
        if not villages:
            villages = [{"name": county.get("county_name", "本县"), "gentry_name": "赵员外", "villager_name": "李大郎"}]
        primary = villages[(season - 1) % len(villages)]
        secondary = villages[(season) % len(villages)]
        clerk_name = surname_from_village(primary.get("name", "赵村")) + "文书"
        runner_name = surname_from_village(secondary.get("name", "李村")) + "快手"
        actor_map = {
            "primary_village": primary.get("name", ""),
            "secondary_village": secondary.get("name", ""),
            "gentry_name": primary.get("gentry_name", "赵员外"),
            "villager_name": primary.get("villager_name", "李大郎"),
            "tenant_name": secondary.get("villager_name", primary.get("villager_name", "李大郎")),
            "witness_name": secondary.get("villager_name", "王二郎"),
            "clerk_name": clerk_name,
            "runner_name": runner_name,
        }
        old_county = template.get("source_county", "")
        new_county = county.get("county_name", old_county)

        payload = copy.deepcopy(template)
        payload = cls._replace_payload_county(payload, old_county, new_county)
        payload = cls._replace_local_roles(payload, actor_map)
        payload["source_county"] = new_county
        payload["source_unit_id"] = unit.id
        payload["source_governor_name"] = county.get("governor_profile", {}).get("name", "")
        payload["county_review_season"] = season
        payload["prefect_review_season"] = season + 1
        payload["actor_map"] = actor_map
        return payload, actor_map

    @classmethod
    def _replace_payload_county(cls, payload, old_county: str, new_county: str):
        if isinstance(payload, dict):
            return {k: cls._replace_payload_county(v, old_county, new_county) for k, v in payload.items()}
        if isinstance(payload, list):
            return [cls._replace_payload_county(v, old_county, new_county) for v in payload]
        if isinstance(payload, str) and old_county:
            return payload.replace(old_county, new_county)
        return payload

    @classmethod
    def _replace_local_roles(cls, payload, actor_map: dict):
        replacements = {
            "地主": f"{actor_map['primary_village']}地主{actor_map['gentry_name']}",
            "农民": f"{actor_map['primary_village']}农民{actor_map['villager_name']}",
            "佃户": f"{actor_map['secondary_village']}佃户{actor_map['tenant_name']}",
            "村民": f"{actor_map['secondary_village']}村民{actor_map['witness_name']}",
            "证人": f"证人{actor_map['witness_name']}",
            "书吏": f"书吏{actor_map['clerk_name']}",
            "衙役": f"衙役{actor_map['runner_name']}",
            "仓大使": f"仓大使{actor_map['clerk_name']}",
            "驿丞": f"驿丞{actor_map['clerk_name']}",
            "马夫": f"马夫{actor_map['runner_name']}",
        }
        if isinstance(payload, dict):
            return {k: cls._replace_local_roles(v, actor_map) for k, v in payload.items()}
        if isinstance(payload, list):
            return [cls._replace_local_roles(v, actor_map) for v in payload]
        if isinstance(payload, str):
            text = payload
            for old, new in replacements.items():
                text = text.replace(old, new)
            return text
        return payload

    @classmethod
    def _build_assistant_opinion(cls, payload: dict, round_no: int, previous_opinion: Optional[str] = None) -> dict:
        suspicion = payload.get("suspicion_markers") or {}
        critical = len(suspicion.get("critical") or [])
        secondary = len(suspicion.get("secondary") or [])
        difficulty = payload.get("difficulty", "新手")
        risk = critical * 0.24 + secondary * 0.08
        if difficulty == "高难":
            risk += 0.18
        elif difficulty == "进阶":
            risk += 0.10
        if payload.get("drum_eligible"):
            risk += 0.10

        verdict_options = payload.get("verdict_options") or []
        high_risk = risk >= 0.42
        med_risk = risk >= 0.28

        # 根据疑点风险推荐判决方向
        recommended = None
        if high_risk:
            # 优先推荐存疑发回续查
            for opt in verdict_options:
                if opt.get("verdict_code") == "INSUFFICIENT_EVIDENCE":
                    recommended = opt
                    break
            if recommended is None and verdict_options:
                recommended = verdict_options[-1]  # 最谨慎选项
            reason = "县丞认为卷宗疑点较多，建议谨慎处置，或发回续查。"
        elif med_risk:
            # 中等风险推荐中间选项
            if len(verdict_options) >= 2:
                recommended = verdict_options[len(verdict_options) // 2]
            elif verdict_options:
                recommended = verdict_options[0]
            reason = "县丞认为证据尚可，但仍有若干疑点，建议酌情判决。"
        else:
            # 低风险推荐第一（通常为较重/明确）选项
            if verdict_options:
                recommended = verdict_options[0]
            reason = "县丞认为证据较为充分，建议依律照案判决。"

        if recommended:
            opinion = recommended.get("verdict_code", "INSUFFICIENT_EVIDENCE")
            opinion_label = recommended.get("verdict_label", opinion)
        else:
            opinion = "INSUFFICIENT_EVIDENCE"
            opinion_label = "证据存疑，发回续查"
            reason = "县丞无法确定判决方向，建议发回续查。"

        return {
            "round_no": round_no,
            "opinion": opinion,
            "opinion_label": opinion_label,
            "reason": reason,
            "generated_by": "rule",
        }

    @classmethod
    def _serialize_county_case(cls, instance: JudicialCaseInstance) -> dict:
        payload = copy.deepcopy(instance.local_payload)
        latest_assistant = (instance.assistant_rounds or [])[-1] if instance.assistant_rounds else {}
        verdict_options = payload.get("verdict_options") or []
        is_round2 = instance.status == "PENDING_MAGISTRATE_ROUND_2"
        # 可选过程动作（判决 + 程序动作）
        procedural = ["搁置委托上级裁定"]
        available_actions = ["判决"] + procedural
        payload.update({
            "instance_id": instance.id,
            "case_id": instance.id,
            "status": instance.status,
            "assistant_opinion": latest_assistant,
            "assistant_rounds": instance.assistant_rounds,
            "magistrate_rounds": instance.magistrate_rounds,
            "verdict_options": verdict_options,
            "current_round": 2 if is_round2 else 1,
            "available_actions": available_actions,
        })
        return payload

    @classmethod
    def _serialize_prefect_case(cls, instance: JudicialCaseInstance) -> dict:
        payload = copy.deepcopy(instance.local_payload)
        latest_assistant = (instance.assistant_rounds or [])[-1] if instance.assistant_rounds else {}
        latest_magistrate = (instance.magistrate_rounds or [])[-1] if instance.magistrate_rounds else {}
        payload.update({
            "case_id": str(instance.id),
            "instance_id": instance.id,
            "template_case_id": instance.template_case_id,
            "source_county": payload.get("source_county", ""),
            "assistant_opinion": latest_assistant,
            "magistrate_decision": latest_magistrate,
            "status": instance.status,
        })
        return payload

    @classmethod
    def _serialize_generation_state(cls, state: JudicialGenerationState) -> dict:
        return {
            "status": state.status,
            "total_windows": state.total_windows,
            "generated_windows": state.generated_windows,
            "last_error": state.last_error,
        }

    @classmethod
    def _estimate_case_factors(cls, payload: dict) -> dict:
        suspicion = payload.get("suspicion_markers") or {}
        critical = len(suspicion.get("critical") or [])
        secondary = len(suspicion.get("secondary") or [])
        difficulty = payload.get("difficulty", "新手")
        category = payload.get("category", "")
        dossier_text = payload.get("dossier_text", "")

        difficulty_bias = {"新手": 0.18, "进阶": 0.3, "高难": 0.42}.get(difficulty, 0.25)
        evidence_doubt = min(0.95, 0.16 + critical * 0.18 + secondary * 0.05 + difficulty_bias)
        coverup_risk = min(0.95, 0.2 + critical * 0.14 + secondary * 0.05 + (0.08 if payload.get("drum_eligible") else 0))
        beneficiary_gain = 0.28
        if "贪腐" in category or "税" in category or "仓" in dossier_text or "赈" in dossier_text:
            beneficiary_gain += 0.18
        if "妻舅" in dossier_text or "亲信" in dossier_text or "姻亲" in dossier_text:
            beneficiary_gain += 0.12
        public_harm = 0.22
        if any(token in dossier_text for token in ["赈", "灾", "仓", "命案", "斗殴", "饥民"]):
            public_harm += 0.22
        if any(token in category for token in ["民变", "命案", "贪腐"]):
            public_harm += 0.12

        overturn_risk = min(0.95, round(evidence_doubt * 0.45 + coverup_risk * 0.4 + public_harm * 0.15, 3))
        return {
            "beneficiary_gain": round(min(0.95, beneficiary_gain), 3),
            "coverup_risk": round(coverup_risk, 3),
            "public_harm": round(min(0.95, public_harm), 3),
            "evidence_doubt": round(evidence_doubt, 3),
            "overturn_risk": overturn_risk,
        }

    @classmethod
    def _pick_verdict_for_archetype(cls, verdict_options: List[dict], archetype: str, factors: dict) -> Optional[str]:
        """根据知县性格和证据风险从 verdict_options 中选择 verdict_code。"""
        if not verdict_options:
            return None
        evidence_doubt = factors.get("evidence_doubt", 0.3)
        # 疑点过高 → 优先存疑续查
        if evidence_doubt >= 0.60:
            for opt in verdict_options:
                if opt.get("verdict_code") == "INSUFFICIENT_EVIDENCE":
                    return opt["verdict_code"]
        # 按性格偏好排序
        if archetype == "CORRUPT":
            preference = ("CONVICT_LIGHT", "MEDIATION", "DEFENDANT_WIN", "ACQUIT", "REMEDIATE")
        elif archetype == "VIRTUOUS":
            preference = ("CONVICT_HEAVY", "STRICT_ENFORCE", "ACQUIT", "PLAINTIFF_WIN", "CONVICT_LIGHT")
        else:
            preference = ("CONVICT_LIGHT", "PLAINTIFF_WIN", "MEDIATION", "CONVICT_HEAVY")
        for code in preference:
            for opt in verdict_options:
                if opt.get("verdict_code") == code:
                    return opt["verdict_code"]
        return verdict_options[0].get("verdict_code")

    @classmethod
    def _rule_decide_ai_case(cls, unit, instance: JudicialCaseInstance, season: int) -> dict:
        gp = unit.unit_data.get("governor_profile", {})
        archetype = gp.get("archetype", "MIDDLING")
        latest_assistant = (instance.assistant_rounds or [])[-1] if instance.assistant_rounds else {}
        suspicion = instance.local_payload.get("suspicion_markers") or {}
        critical = len(suspicion.get("critical") or [])
        factors = cls._estimate_case_factors(instance.local_payload)
        verdict_options = instance.local_payload.get("verdict_options") or []

        if instance.status == "PENDING_MAGISTRATE_ROUND_1":
            # 清廉知县面对大量疑点倾向搁置委托上级裁定；其余直接判决
            if archetype == "VIRTUOUS" and critical >= 2:
                action, action_code, verdict_code = "搁置委托上级裁定", "DEFER_TO_PREFECT", None
                reason = "知县性格清直，疑点较多时选择搁置并委托知府裁定。"
            else:
                action, action_code = "判决", "VERDICT"
                verdict_code = cls._pick_verdict_for_archetype(verdict_options, archetype, factors)
                selected = next((o for o in verdict_options if o.get("verdict_code") == verdict_code), {})
                reason = f"知县依卷宗判决：{selected.get('verdict_label', verdict_code)}。"
        else:
            # 第二轮：清廉＋高疑点 → 委托上级，其余判决
            if archetype == "VIRTUOUS" and critical >= 2:
                action, action_code, verdict_code = "搁置委托上级裁定", "DEFER_TO_PREFECT", None
                reason = "知县二审后仍无把握，决定搁置并委托知府裁定。"
            else:
                action, action_code = "判决", "VERDICT"
                verdict_code = cls._pick_verdict_for_archetype(verdict_options, archetype, factors)
                selected = next((o for o in verdict_options if o.get("verdict_code") == verdict_code), {})
                reason = f"知县二审后判决：{selected.get('verdict_label', verdict_code)}。"

        return {
            "action_label": action,
            "action_code": action_code,
            "verdict_code": verdict_code,
            "reason": reason,
            "decision_source": "rule",
            "model": "rule_fallback",
            "confidence": 0.62,
            "factors": factors,
            "latency_ms": 0,
        }

    @classmethod
    def _llm_enabled(cls) -> bool:
        if not getattr(settings, "JUDICIAL_MAGISTRATE_LLM_ENABLED", False):
            return False
        provider_name = getattr(settings, "LLM_DEFAULT_PROVIDER", "")
        provider_cfg = (getattr(settings, "LLM_PROVIDERS", {}) or {}).get(provider_name, {})
        return bool(provider_cfg.get("api_key"))

    @classmethod
    def _build_llm_review_context(cls, unit, instance: JudicialCaseInstance, season: int, baseline: dict) -> dict:
        county = unit.unit_data or {}
        governor = county.get("governor_profile", {})
        latest_assistant = (instance.assistant_rounds or [])[-1] if instance.assistant_rounds else {}
        round_no = 2 if instance.status == "PENDING_MAGISTRATE_ROUND_2" else 1
        payload = instance.local_payload or {}
        verdict_options = payload.get("verdict_options") or []
        verdict_options_text = "\n".join(
            f"- [{o.get('verdict_code')}] {o.get('verdict_label', '')}: {o.get('rationale', '')}"
            for o in verdict_options
        ) or "- 无"
        verdict_codes_allowed = " / ".join(o.get("verdict_code", "") for o in verdict_options)
        procedural_options = "搁置委托上级裁定（DEFER_TO_PREFECT）"
        factors = baseline.get("factors") or cls._estimate_case_factors(payload)
        attachments = payload.get("attachments") or []
        suspicion = payload.get("suspicion_markers") or {}

        total_pop = int(county.get("population") or 0)
        county_summary = (
            f"县名: {county.get('county_name', '本县')}；人口: {total_pop}；"
            f"县库: {round(county.get('treasury', 0))}两；民心: {round(county.get('morale', 50))}；"
            f"治安: {round(county.get('security', 50))}；商业: {round(county.get('commercial', 30))}；"
            f"文教: {round(county.get('education', 30))}；税率: {county.get('tax_rate', 0.12):.0%}。"
        )
        round_rules = "必须结案，只能从判决方向中选择verdict_code直接判决，或搁置委托上级裁定（DEFER_TO_PREFECT）。"

        return {
            "season_label": f"第{season}月（{month_name(month_of_year(season))}）",
            "governor_name": governor.get("name", county.get("county_name", "本县") + "知县"),
            "governor_bio": f"你是{county.get('county_name', '本县')}知县，施政风格{governor.get('style', '稳健')}，人格类型{governor.get('archetype', 'MIDDLING')}。",
            "governor_style": governor.get("style", "midway"),
            "governor_archetype": governor.get("archetype", "MIDDLING"),
            "county_summary": county_summary,
            "case_name": payload.get("case_name", "未名案件"),
            "case_category": payload.get("category", ""),
            "case_difficulty": payload.get("difficulty", "新手"),
            "dossier_text": payload.get("dossier_text", ""),
            "attachments_text": "\n".join(f"- {item}" for item in attachments) if attachments else "- 无",
            "suspicion_text": "\n".join([f"- {item}" for item in (suspicion.get("critical") or []) + (suspicion.get("secondary") or [])]) or "- 暂无",
            "verdict_options_text": verdict_options_text,
            "verdict_codes_allowed": verdict_codes_allowed,
            "procedural_options": procedural_options,
            "baseline_factors": "\n".join([f"- {key}: {value:.2f}" for key, value in factors.items()]),
            "baseline_decision": baseline.get("action_label", "判决"),
            "baseline_verdict_code": baseline.get("verdict_code", ""),
            "baseline_reason": baseline.get("reason", ""),
            "round_rules": round_rules,
        }

    @classmethod
    def _request_llm_magistrate_decision(cls, unit, instance: JudicialCaseInstance, season: int, baseline: dict) -> dict:
        ctx = cls._build_llm_review_context(unit, instance, season, baseline)
        system_prompt, user_prompt = PromptRegistry.render("magistrate_judicial_review", **ctx)
        client = LLMClient(
            timeout=getattr(settings, "JUDICIAL_MAGISTRATE_LLM_TIMEOUT", 8),
            max_retries=getattr(settings, "JUDICIAL_MAGISTRATE_LLM_MAX_RETRIES", 1),
        )
        started = time.monotonic()
        result = client.chat_json(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.25,
            max_tokens=512,
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        payload = instance.local_payload or {}
        verdict_options = payload.get("verdict_options") or []
        valid_verdict_codes = {o.get("verdict_code") for o in verdict_options}
        is_round2 = instance.status == "PENDING_MAGISTRATE_ROUND_2"

        # LLM应返回 {"action": "VERDICT"|"REMAND"|"DEFER_TO_PREFECT", "verdict_code": "...", ...}
        action_raw = str(result.get("action") or "").strip().upper()
        verdict_code_raw = str(result.get("verdict_code") or "").strip().upper()

        if action_raw == "VERDICT" and verdict_code_raw in valid_verdict_codes:
            action_code = "VERDICT"
            action_label = "判决"
            verdict_code = verdict_code_raw
        elif action_raw == "DEFER_TO_PREFECT":
            action_code = "DEFER_TO_PREFECT"
            action_label = "搁置委托上级裁定"
            verdict_code = None
        else:
            raise ValueError(f"invalid LLM judicial action/verdict_code: {action_raw}/{verdict_code_raw}")

        raw_factors = result.get("factors") or {}
        factors = {}
        for key, baseline_value in (baseline.get("factors") or {}).items():
            try:
                value = float(raw_factors.get(key, baseline_value))
            except (TypeError, ValueError):
                value = float(baseline_value)
            factors[key] = round(min(1.0, max(0.0, value)), 3)

        confidence = result.get("confidence", baseline.get("confidence", 0.6))
        try:
            confidence = round(min(1.0, max(0.0, float(confidence))), 3)
        except (TypeError, ValueError):
            confidence = baseline.get("confidence", 0.6)

        return {
            "action_label": action_label,
            "action_code": action_code,
            "verdict_code": verdict_code,
            "reason": str(result.get("reason") or baseline.get("reason") or ""),
            "decision_source": "llm",
            "model": getattr(client.config, "default_model", ""),
            "confidence": confidence,
            "factors": factors,
            "latency_ms": latency_ms,
        }

    @classmethod
    def _auto_decide_ai_case(cls, unit, instance: JudicialCaseInstance, season: int) -> None:
        latest_assistant = (instance.assistant_rounds or [])[-1] if instance.assistant_rounds else {}
        rounds = list(instance.magistrate_rounds or [])
        baseline = cls._rule_decide_ai_case(unit, instance, season)
        decision = baseline
        if cls._llm_enabled():
            try:
                decision = cls._request_llm_magistrate_decision(unit, instance, season, baseline)
            except Exception as exc:
                logger.warning("AI magistrate judicial LLM fallback for case %s: %s", instance.id, exc)

        action_code = decision["action_code"]
        verdict_code = decision.get("verdict_code")
        round_no = 2 if instance.status == "PENDING_MAGISTRATE_ROUND_2" else 1

        round_entry = {
            "round_no": round_no,
            "season": season,
            "action": action_code,
            "action_label": decision["action_label"],
            "actor": "ai_magistrate",
            "decision_source": decision["decision_source"],
            "model": decision["model"],
            "reason": decision["reason"],
            "confidence": decision["confidence"],
            "factors": decision["factors"],
            "latency_ms": decision["latency_ms"],
        }
        if verdict_code:
            verdict_options = instance.local_payload.get("verdict_options") or []
            selected = next((o for o in verdict_options if o.get("verdict_code") == verdict_code), {})
            round_entry["verdict_code"] = verdict_code
            round_entry["verdict_label"] = selected.get("verdict_label", verdict_code)
            round_entry["immediate_effects"] = selected.get("immediate_effects", {})

        rounds.append(round_entry)

        if action_code == "VERDICT" and verdict_code:
            # 应用效果，提交知府
            verdict_options = instance.local_payload.get("verdict_options") or []
            selected = next((o for o in verdict_options if o.get("verdict_code") == verdict_code), {})
            cls._apply_verdict_effects_for_ai(unit, selected)
            instance.submitted_to_prefect = True
            instance.submitted_season = season
            instance.status = "SUBMITTED_TO_PREFECT"
        elif action_code == "REMAND_TO_ASSISTANT":
            assistant_rounds = list(instance.assistant_rounds or [])
            assistant_rounds.append(cls._build_assistant_opinion(instance.local_payload, round_no=2, previous_opinion=latest_assistant.get("opinion")))
            instance.assistant_rounds = assistant_rounds
            instance.status = "PENDING_MAGISTRATE_ROUND_2"
        elif action_code == "DEFER_TO_PREFECT":
            instance.submitted_to_prefect = True
            instance.submitted_season = season
            instance.status = "DEFERRED_TO_PREFECT"

        instance.magistrate_rounds = rounds
        instance.save(update_fields=["assistant_rounds", "magistrate_rounds", "status", "submitted_to_prefect", "submitted_season", "updated_at"])

    @classmethod
    def _apply_verdict_effects_for_ai(cls, unit, verdict_option: dict) -> None:
        """AI路径：将判决效果写入县域 unit_data（不含玩家档案声誉），复用共用底层。"""
        cls._apply_verdict_effects_to_unit(unit, verdict_option)

    # ==================== 知府复审（知县游戏路径）====================

    @classmethod
    def auto_review_county_by_prefect(cls, game, prefect_agent, season: int, county: dict, report: dict) -> None:
        """将待审案件标记为 PREFECT_REVIEWING，后台线程异步 LLM 决策。
        结果由 deliver_pending_prefect_reviews() 在后续推进时投递。
        """
        if not game.player_unit_id:
            return
        cases = JudicialCaseInstance.objects.filter(
            game=game,
            county_unit=game.player_unit,
            prefect_review_season=season,
            status__in=['SUBMITTED_TO_PREFECT', 'DEFERRED_TO_PREFECT'],
        ).order_by('id')
        if not cases.exists():
            return

        ids = []
        now = timezone.now()
        for instance in cases:
            dm = dict(instance.debug_meta or {})
            dm['prefect_review_original_status'] = instance.status
            instance.debug_meta = dm
            instance.prefect_review_queued_at = now
            instance.status = 'PREFECT_REVIEWING'
            instance.save(update_fields=['status', 'debug_meta', 'prefect_review_queued_at', 'updated_at'])
            ids.append(instance.id)

        threading.Thread(
            target=cls._background_review_cases,
            args=(game.id, prefect_agent.id, ids),
            daemon=True,
        ).start()
        logger.info("[知府司法] 已将 %d 件案件加入后台复审队列 season=%d", len(ids), season)

    @classmethod
    def _background_review_cases(cls, game_id: int, prefect_agent_id: int, case_ids: list) -> None:
        """后台线程：逐案调用 LLM/规则引擎，存储结果，标记 PREFECT_REVIEWED。"""
        try:
            from ..models import Agent, GameState
            game = GameState.objects.select_related('player_unit').get(id=game_id)
            prefect_agent = Agent.objects.get(id=prefect_agent_id)
        except Exception as exc:
            logger.warning("[知府司法后台] 加载数据失败 game=%s: %s", game_id, exc)
            return

        for case_id in case_ids:
            try:
                instance = JudicialCaseInstance.objects.get(id=case_id, status='PREFECT_REVIEWING')
            except JudicialCaseInstance.DoesNotExist:
                continue
            try:
                cls._compute_prefect_decision(game, prefect_agent, instance)
            except Exception as exc:
                logger.warning("[知府司法后台] 案件 %s 处理失败，尝试规则兜底: %s", case_id, exc)
                try:
                    cls._compute_prefect_decision(game, prefect_agent, instance, force_fallback=True)
                except Exception as exc2:
                    logger.error("[知府司法后台] 案件 %s 兜底也失败: %s", case_id, exc2)

    @classmethod
    def _compute_prefect_decision(
        cls, game, prefect_agent, instance: JudicialCaseInstance, force_fallback: bool = False
    ) -> None:
        """计算知府判决（LLM优先/规则兜底），存入 instance.prefect_decision，状态→PREFECT_REVIEWED。"""
        from .state import load_county_state

        prefect_attrs = prefect_agent.attributes
        payload = instance.local_payload or {}
        verdict_options = payload.get('verdict_options') or []
        case_name = payload.get('case_name', '未名案件')
        is_deferred = instance.debug_meta.get('prefect_review_original_status') == 'DEFERRED_TO_PREFECT'
        season = instance.prefect_review_season

        # 取知县最终判决
        magistrate_verdict_code = None
        magistrate_verdict_label = ''
        for r in reversed(instance.magistrate_rounds or []):
            if r.get('action') == 'VERDICT':
                magistrate_verdict_code = r.get('verdict_code')
                magistrate_verdict_label = r.get('verdict_label', magistrate_verdict_code or '')
                break

        # 规则引擎基线
        factors = cls._estimate_case_factors(payload)
        rule_verdict_code = cls._pick_prefect_verdict_code(verdict_options, prefect_attrs, factors)
        if rule_verdict_code is None:
            return

        # LLM 或强制 fallback
        prefect_verdict_code = rule_verdict_code
        llm_letter = ''
        if not force_fallback:
            try:
                county = load_county_state(game)
                ctx = cls._build_prefect_review_context(
                    prefect_agent, prefect_attrs, county, season, payload,
                    verdict_options, factors, magistrate_verdict_code,
                    magistrate_verdict_label, is_deferred,
                )
                llm_result = cls._request_llm_prefect_decision(ctx, verdict_options)
                if llm_result:
                    prefect_verdict_code = llm_result['verdict_code']
                    llm_letter = llm_result.get('letter', '')
            except Exception as exc:
                logger.warning("[知府司法后台] 案件 %s LLM失败，降级规则引擎: %s", instance.id, exc)

        selected_option = next(
            (o for o in verdict_options if o.get('verdict_code') == prefect_verdict_code), {}
        )
        prefect_verdict_label = selected_option.get('verdict_label', prefect_verdict_code)

        # 确定结果类型和好感变动
        if is_deferred:
            overturned = False
            affinity_delta = 0
            tag = '【知府裁定】'
            fallback_body = f'知县呈请上裁，依卷宗裁定：{prefect_verdict_label}'
        elif magistrate_verdict_code == prefect_verdict_code:
            overturned = False
            affinity_delta = 1
            tag = '【知府复审·维持原判】'
            fallback_body = f'{prefect_verdict_label}，判决合理，知府表示认可'
        else:
            overturned = True
            public_harm = factors.get('public_harm', 0.22)
            affinity_delta = -6 if public_harm > 0.5 else -3
            tag = '【知府改判】'
            fallback_body = (
                f'原判"{magistrate_verdict_label}"，改判为"{prefect_verdict_label}"，'
                f'认为原判有失妥当'
            )

        instance.prefect_decision = {
            'verdict_code': prefect_verdict_code,
            'verdict_label': prefect_verdict_label,
            'overturned': overturned,
            'affinity_delta': affinity_delta,
            'letter': llm_letter,
            'queue_season': season,
            'is_deferred': is_deferred,
            'case_name': case_name,
            'magistrate_verdict_code': magistrate_verdict_code,
            'magistrate_verdict_label': magistrate_verdict_label or '',
            'tag': tag,
            'fallback_body': fallback_body,
            'selected_option': selected_option,
        }
        instance.status = 'PREFECT_REVIEWED'
        instance.save(update_fields=['prefect_decision', 'status', 'updated_at'])
        logger.info("[知府司法后台] 案件 %s 计算完成: %s (%s)", instance.id, prefect_verdict_code, tag)

    @classmethod
    def deliver_pending_prefect_reviews(cls, game, county: dict, report: dict) -> None:
        """在 advance_season 开头调用：投递已完成的知府复审结果，并处理超时兜底。"""
        if not game.player_unit_id:
            return

        from ..models import Agent
        prefect = Agent.objects.filter(game=game, role='PREFECT').first()
        if prefect is None:
            return

        delivery_season = game.current_season

        # 超时兜底：PREFECT_REVIEWING 超过5分钟 → 规则引擎强制完成
        _TIMEOUT_MINUTES = 5
        timeout_threshold = timezone.now() - timedelta(minutes=_TIMEOUT_MINUTES)
        for instance in JudicialCaseInstance.objects.filter(
            game=game,
            county_unit=game.player_unit,
            status='PREFECT_REVIEWING',
            prefect_review_queued_at__lt=timeout_threshold,
        ):
            try:
                cls._compute_prefect_decision(game, prefect, instance, force_fallback=True)
                logger.warning("[知府司法] 案件 %s 超时，已使用规则引擎兜底完成", instance.id)
            except Exception as exc:
                logger.error("[知府司法] 案件 %s 超时兜底失败: %s", instance.id, exc)

        # 投递已完成的复审
        ready_cases = JudicialCaseInstance.objects.filter(
            game=game,
            county_unit=game.player_unit,
            status='PREFECT_REVIEWED',
        ).order_by('id')
        for instance in ready_cases:
            try:
                cls._deliver_single_prefect_review(game, prefect, county, report, instance, delivery_season)
            except Exception as exc:
                logger.warning("[知府司法] 案件 %s 投递失败: %s", instance.id, exc)

    @classmethod
    def _deliver_single_prefect_review(
        cls, game, prefect_agent, county: dict, report: dict,
        instance: JudicialCaseInstance, delivery_season: int,
    ) -> None:
        """将已计算好的知府复审结果应用到游戏状态并写入事件。"""
        from ..models import EventLog
        from .constants import month_name, month_of_year

        d = instance.prefect_decision or {}
        prefect_verdict_code = d.get('verdict_code', '')
        prefect_verdict_label = d.get('verdict_label', prefect_verdict_code)
        overturned = d.get('overturned', False)
        affinity_delta = int(d.get('affinity_delta', 0))
        llm_letter = d.get('letter', '')
        is_deferred = d.get('is_deferred', False)
        case_name = d.get('case_name', '未名案件')
        magistrate_verdict_code = d.get('magistrate_verdict_code')
        magistrate_verdict_label = d.get('magistrate_verdict_label', '')
        tag = d.get('tag', '【知府复审】')
        fallback_body = d.get('fallback_body', prefect_verdict_label)
        selected_option = d.get('selected_option') or {}

        letter_body = llm_letter if llm_letter else fallback_body
        event_text = f'{tag}《{case_name}》：{letter_body}'

        # 判决效果（改判或上裁时需写入县数据）
        if overturned or is_deferred:
            cls._apply_verdict_effects_to_county_dict(county, selected_option)

        # 知府好感度 + 评价笔记
        prefect_attrs = prefect_agent.attributes
        old_affinity = prefect_attrs.get('player_affinity', 50)
        prefect_attrs['player_affinity'] = max(-99, min(99, old_affinity + affinity_delta))
        county['prefect_affinity'] = prefect_attrs['player_affinity']

        if is_deferred:
            note_text = f'《{case_name}》委托上裁，知府裁定：{prefect_verdict_label}'
        elif not overturned:
            note_text = f'《{case_name}》维持原判（{prefect_verdict_label}），判决得当'
        else:
            note_text = (
                f'《{case_name}》改判：{magistrate_verdict_label}→{prefect_verdict_label}，'
                f'好感{affinity_delta:+d}'
            )
        notes = prefect_attrs.get('evaluation_notes', [])
        notes.append(f'[{month_name(month_of_year(delivery_season))}] {note_text}')
        if len(notes) > 12:
            notes = notes[-12:]
        prefect_attrs['evaluation_notes'] = notes
        prefect_agent.attributes = prefect_attrs
        prefect_agent.save(update_fields=['attributes'])

        # 更新案件状态
        instance.status = 'PREFECT_DECIDED'
        instance.save(update_fields=['status', 'updated_at'])

        # 月报事件
        report['events'].append(event_text)
        if overturned:
            report.setdefault('prefect_judicial_overturn', []).append({
                'case_name': case_name,
                'magistrate_verdict': magistrate_verdict_code,
                'magistrate_verdict_label': magistrate_verdict_label,
                'prefect_verdict': prefect_verdict_code,
                'prefect_verdict_label': prefect_verdict_label,
                'affinity_delta': affinity_delta,
                'letter': llm_letter,
            })

        # 府志
        EventLog.objects.create(
            game=instance.game,
            season=delivery_season,
            event_type='prefect_judicial_review',
            category='PREFECT',
            description=f'{tag}{case_name}：{letter_body[:60]}',
            data={
                'case_name': case_name,
                'overturned': overturned,
                'affinity_delta': affinity_delta,
                'prefect_verdict': prefect_verdict_code,
            },
        )

        if not is_deferred:
            cls._settle_player_competence_after_prefect_review(
                instance.game,
                instance,
                season=delivery_season,
                overturned=overturned,
                prefect_verdict_code=prefect_verdict_code,
            )

    @classmethod
    def _build_prefect_review_context(
        cls, prefect_agent, prefect_attrs: dict, county: dict, season: int,
        payload: dict, verdict_options: list, factors: dict,
        magistrate_verdict_code: Optional[str], magistrate_verdict_label: str,
        is_deferred: bool,
    ) -> dict:
        """构建知府司法复审的 LLM 上下文。"""
        from .ai_prefect import PrefectAIService
        from .constants import month_name, month_of_year

        personality_desc, ideology_desc, _ = PrefectAIService._describe_attrs(prefect_attrs)

        # 记忆拼合：近期记忆 + 最新3条评价笔记
        memory_lines = [f'- {m}' for m in prefect_attrs.get('memory', [])[-4:]]
        note_lines = [f'- {n}' for n in prefect_attrs.get('evaluation_notes', [])[-3:]]
        memory_desc = '\n'.join(memory_lines) if memory_lines else '初任，尚无积累'
        if note_lines:
            memory_desc += '\n近期批注：\n' + '\n'.join(note_lines)

        # 县情概要（模糊）
        from .ai_prefect import _tier_label
        morale_lbl = _tier_label(county.get('morale', 50))
        security_lbl = _tier_label(county.get('security', 50))
        treasury = county.get('treasury', 0)
        treasury_lbl = '充裕' if treasury > 500 else ('尚可' if treasury > 200 else ('紧张' if treasury > 50 else '匮乏'))
        county_situation = f'民心{morale_lbl}·治安{security_lbl}·县库{treasury_lbl}'

        # 判决选项文本
        verdict_options_text = '\n'.join(
            f'- [{o.get("verdict_code")}] {o.get("verdict_label", "")}: {o.get("rationale", "")}'
            for o in verdict_options
        ) or '- 无'

        # 附件
        attachments = payload.get('attachments') or []
        attachments_block = (
            '- 附件：\n' + '\n'.join(f'  · {a}' for a in attachments) + '\n'
            if attachments else ''
        )

        # 疑点
        suspicion = payload.get('suspicion_markers') or {}
        suspicion_items = (suspicion.get('critical') or []) + (suspicion.get('secondary') or [])
        suspicion_text = '\n'.join(f'- {s}' for s in suspicion_items) if suspicion_items else '- 暂无明显疑点'

        # 知县处置描述
        if is_deferred:
            magistrate_situation = '知县经两轮审理后仍无把握，搁置案件并呈请本府裁定。你需主动作出判决。'
        elif magistrate_verdict_code:
            magistrate_situation = (
                f'知县已作出判决：{magistrate_verdict_label}（代码：{magistrate_verdict_code}）。'
                f'你可维持原判，也可改判为其他选项。'
            )
        else:
            magistrate_situation = '知县处置情况不明。'

        # 风险因素文本
        factors_text = '\n'.join(f'- {k}: {v:.2f}' for k, v in factors.items())

        return {
            'prefect_name': prefect_agent.name,
            'prefecture_name': prefect_attrs.get('prefecture', '本府'),
            'bio': prefect_attrs.get('bio', ''),
            'personality_desc': personality_desc,
            'ideology_desc': ideology_desc,
            'memory_desc': memory_desc,
            'affinity': prefect_attrs.get('player_affinity', 50),
            'season_label': month_name(month_of_year(season)),
            'county_name': county.get('county_type_name', county.get('county_name', '本县')),
            'county_situation': county_situation,
            'case_name': payload.get('case_name', '未名案件'),
            'case_category': payload.get('category', ''),
            'case_difficulty': payload.get('difficulty', '新手'),
            'dossier_text': payload.get('dossier_text', ''),
            'attachments_block': attachments_block,
            'suspicion_text': suspicion_text,
            'verdict_options_text': verdict_options_text,
            'magistrate_situation': magistrate_situation,
            'factors_text': factors_text,
        }

    @classmethod
    def _request_llm_prefect_decision(cls, ctx: dict, verdict_options: list) -> Optional[dict]:
        """调用 LLM 获取知府判决。返回 {'verdict_code': ..., 'letter': ...} 或 None。"""
        from llm.client import LLMClient
        from llm.prompts import PromptRegistry

        system_prompt, user_prompt = PromptRegistry.render('prefect_judicial_review', **ctx)
        client = LLMClient(timeout=10, max_retries=1)
        result = client.chat_json(
            [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=0.5,
            max_tokens=400,
        )

        if not isinstance(result, dict):
            return None
        verdict_code = str(result.get('verdict_code') or '').strip()
        valid_codes = {o.get('verdict_code') for o in verdict_options}
        if verdict_code not in valid_codes:
            logger.warning("知府 LLM 返回无效 verdict_code: %r（有效: %s）", verdict_code, valid_codes)
            return None
        return {
            'verdict_code': verdict_code,
            'letter': str(result.get('letter') or '').strip(),
        }

    @classmethod
    def _pick_prefect_verdict_code(
        cls, verdict_options: list, prefect_attrs: dict, factors: dict
    ) -> Optional[str]:
        """根据知府性格和案件因素选择判决。"""
        if not verdict_options:
            return None
        people_focus = prefect_attrs.get('ideology', {}).get('people_vs_authority', 0.5)
        conscientiousness = prefect_attrs.get('personality', {}).get('conscientiousness', 0.5)
        evidence_doubt = factors.get('evidence_doubt', 0.3)

        # 良知官员面对高疑点倾向存疑续查
        if evidence_doubt >= 0.55 and conscientiousness >= 0.65:
            for opt in verdict_options:
                if opt.get('verdict_code') == 'INSUFFICIENT_EVIDENCE':
                    return opt['verdict_code']

        # 按民本/权威取向决定倾向
        if people_focus >= 0.65:
            preference = ('CONVICT_HEAVY', 'PLAINTIFF_WIN', 'STRICT_ENFORCE', 'CONVICT_LIGHT', 'MEDIATION', 'ACQUIT')
        elif people_focus <= 0.35:
            preference = ('MEDIATION', 'CONVICT_LIGHT', 'DEFENDANT_WIN', 'ACQUIT', 'CONVICT_HEAVY')
        else:
            preference = ('CONVICT_LIGHT', 'PLAINTIFF_WIN', 'MEDIATION', 'ACQUIT', 'CONVICT_HEAVY')
        for code in preference:
            for opt in verdict_options:
                if opt.get('verdict_code') == code:
                    return opt['verdict_code']
        return verdict_options[0].get('verdict_code')

    @classmethod
    def _apply_verdict_effects_to_county_dict(cls, county: dict, verdict_option: dict) -> None:
        """将判决效果写入县域 dict（知府改判/上裁路径，不触达 AdminUnit）。"""
        effects = verdict_option.get('immediate_effects') or {}
        for field in ('gentry_favor', 'commercial', 'education'):
            delta = effects.get(field, 0)
            if delta:
                county[field] = max(0, min(100, round(float(county.get(field, 50)) + delta, 1)))
        for field in ('morale', 'security'):
            delta = effects.get(field, 0)
            if delta:
                delta = max(-2, min(2, delta))  # 单案判决上限 ±2
                MetricsMixin.apply_county_stat_delta(county, field, delta)
        treasury_delta = effects.get('treasury', 0)
        if treasury_delta:
            county['treasury'] = max(0, round(float(county.get('treasury', 0)) + treasury_delta, 2))

    # ==================== 府情总览 / 下辖县统计 ====================

    @classmethod
    def get_county_judicial_stats(cls, game, county_unit_ids: list) -> dict:
        """批量返回各下辖县的司法健康度统计。键为 unit_id（int）。
        每个值：{'resolved': int, 'deferred': int, 'pending': int}
        """
        if not county_unit_ids:
            return {}
        stats = {uid: {'resolved': 0, 'deferred': 0, 'pending': 0} for uid in county_unit_ids}
        for inst in JudicialCaseInstance.objects.filter(
            game=game,
            county_unit_id__in=county_unit_ids,
        ).only('county_unit_id', 'status'):
            uid = inst.county_unit_id
            if uid not in stats:
                continue
            if inst.status == 'SUBMITTED_TO_PREFECT':
                stats[uid]['resolved'] += 1
            elif inst.status == 'DEFERRED_TO_PREFECT':
                stats[uid]['deferred'] += 1
            elif inst.status.startswith('PENDING_'):
                stats[uid]['pending'] += 1
        return stats

    @classmethod
    def get_county_judicial_decisions(cls, game, unit_id: int, limit: int = 5) -> list:
        """返回单县最近 N 个AI知县已处理案件的摘要（供府情县详情展示）。"""
        decisions = []
        qs = JudicialCaseInstance.objects.filter(
            game=game,
            county_unit_id=unit_id,
            status__in=['SUBMITTED_TO_PREFECT', 'DEFERRED_TO_PREFECT'],
        ).only('local_payload', 'magistrate_rounds', 'status', 'county_review_season')\
         .order_by('-county_review_season')
        for inst in qs:
            payload = inst.local_payload or {}
            rounds = inst.magistrate_rounds or []
            last_round = rounds[-1] if rounds else {}
            decisions.append({
                'case_name':   payload.get('case_name', ''),
                'category':    payload.get('category', ''),
                'season':      inst.county_review_season,
                'action':      last_round.get('action_label', last_round.get('action', '')),
                'verdict_code':  last_round.get('verdict_code', ''),
                'verdict_label': last_round.get('verdict_label', ''),
                'status': inst.status,
            })
            if len(decisions) >= limit:
                break
        return decisions

    @classmethod
    def _apply_defer_penalty(cls, game) -> None:
        adjust_player_profile_stat(
            game,
            "competence",
            -2,
            source_event="player_judicial_defer",
            source_label="司法搁置上裁",
        )
        adjust_player_profile_stat(
            game,
            "popularity",
            -1,
            source_event="player_judicial_defer",
            source_label="司法搁置上裁",
        )
