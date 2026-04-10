"""NPC 调试聚合服务。"""

from __future__ import annotations

import copy
from typing import Optional

from llm.prompts import PromptRegistry

from ..models import (
    AdminUnit,
    Agent,
    DialogueMessage,
    EventLog,
    GameState,
    JudicialCaseInstance,
    NegotiationSession,
    NeighborCounty,
    NeighborEventLog,
    Promise,
    Relationship,
)
from .agent import AgentService
from .ai_governor import AIGovernorService


class _UnitGovernorAdapter:
    """让府模式下的下辖县以 NeighborCounty 接口被 AI 调试工具复用。"""

    def __init__(self, unit: AdminUnit):
        self._unit = unit

    @property
    def id(self):
        return f"sub_{self._unit.id}"

    @property
    def county_data(self):
        return self._unit.unit_data

    @property
    def county_name(self):
        return self._unit.unit_data.get("county_name", "")

    @property
    def governor_name(self):
        return self._unit.unit_data.get("governor_profile", {}).get("name", "")

    @property
    def governor_style(self):
        return self._unit.unit_data.get("governor_profile", {}).get("style", "yuanhua")

    @property
    def governor_bio(self):
        return self._unit.unit_data.get("governor_profile", {}).get("bio", "")

    @property
    def governor_archetype(self):
        return self._unit.unit_data.get("governor_profile", {}).get("archetype", "MIDDLING")


class NPCDebugService:
    """按“某个已开局游戏”聚合 NPC 调试信息。"""

    AGENT_EVENT_SCAN_LIMIT = 240

    @classmethod
    def list_npcs(cls, game: GameState) -> list[dict]:
        items = []
        for agent in Agent.objects.filter(game=game).order_by("id"):
            attrs = agent.attributes or {}
            memory = attrs.get("memory", [])
            items.append({
                "npc_key": f"agent:{agent.id}",
                "npc_kind": "agent",
                "id": agent.id,
                "name": agent.name,
                "role": agent.role,
                "role_title": agent.role_title,
                "source_model": "Agent",
                "group_label": "Agent",
                "tier": agent.tier,
                "affinity": attrs.get("player_affinity", 50),
                "memory_count": len(memory),
                "village_name": attrs.get("village_name", ""),
                "summary": cls._compact_agent_summary(agent),
            })

        for neighbor in NeighborCounty.objects.filter(game=game).order_by("id"):
            profile = (neighbor.county_data or {}).get("governor_profile", {})
            memory = profile.get("memory", []) if isinstance(profile, dict) else []
            items.append({
                "npc_key": f"neighbor:{neighbor.id}",
                "npc_kind": "neighbor",
                "id": neighbor.id,
                "name": neighbor.governor_name,
                "role": "COUNTY_MAGISTRATE",
                "role_title": "AI知县",
                "source_model": "NeighborCounty",
                "group_label": "邻县 AI",
                "tier": "GOVERNOR_AI",
                "affinity": None,
                "memory_count": len(memory),
                "village_name": "",
                "summary": {
                    "county_name": neighbor.county_name,
                    "style": neighbor.governor_style,
                    "archetype": neighbor.governor_archetype,
                    "last_reasoning": bool(neighbor.last_reasoning),
                },
            })

        for unit in AdminUnit.objects.filter(game=game, unit_type="COUNTY").order_by("id"):
            if game.player_unit_id and unit.id == game.player_unit_id:
                continue
            gp = (unit.unit_data or {}).get("governor_profile", {})
            if not isinstance(gp, dict) or not gp.get("name"):
                continue
            memory = gp.get("memory", [])
            items.append({
                "npc_key": f"subordinate:{unit.id}",
                "npc_kind": "subordinate",
                "id": unit.id,
                "name": gp.get("name", ""),
                "role": "COUNTY_MAGISTRATE",
                "role_title": "下辖知县",
                "source_model": "AdminUnit",
                "group_label": "府属县 AI",
                "tier": "GOVERNOR_AI",
                "affinity": unit.unit_data.get("prefect_affinity", 50),
                "memory_count": len(memory),
                "village_name": "",
                "summary": {
                    "county_name": unit.unit_data.get("county_name", ""),
                    "style": gp.get("style", ""),
                    "archetype": gp.get("archetype", "MIDDLING"),
                    "last_reasoning": bool(unit.unit_data.get("_last_reasoning", "")),
                },
            })

        items.sort(key=lambda item: (item["group_label"], item["id"]))
        return items

    @classmethod
    def get_npc_detail(cls, game: GameState, npc_key: str) -> Optional[dict]:
        kind, obj_id = cls._parse_npc_key(npc_key)
        if kind == "agent":
            agent = Agent.objects.filter(game=game, id=obj_id).first()
            return cls._build_agent_detail(game, agent) if agent else None
        if kind == "neighbor":
            neighbor = NeighborCounty.objects.filter(game=game, id=obj_id).first()
            return cls._build_neighbor_detail(game, neighbor) if neighbor else None
        if kind == "subordinate":
            unit = AdminUnit.objects.filter(game=game, id=obj_id, unit_type="COUNTY").first()
            return cls._build_subordinate_detail(game, unit) if unit else None
        return None

    @staticmethod
    def _parse_npc_key(npc_key: str) -> tuple[str, int]:
        kind, raw_id = (npc_key or "").split(":", 1)
        return kind, int(raw_id)

    @classmethod
    def _build_agent_detail(cls, game: GameState, agent: Agent) -> dict:
        attrs = copy.deepcopy(agent.attributes or {})
        context = AgentService.build_system_context(agent, game)
        prompt_preview = cls._build_agent_prompt_preview(agent, context)
        relationships = cls._build_agent_relationships(agent)
        dialogues = cls._serialize_dialogues(
            DialogueMessage.objects.filter(game=game, agent=agent).order_by("-created_at")[:20]
        )
        negotiations = cls._serialize_negotiations(
            NegotiationSession.objects.filter(game=game, agent=agent).order_by("-created_at")[:10]
        )
        promises = cls._serialize_promises(
            Promise.objects.filter(game=game, agent=agent).order_by("-created_at")[:10]
        )
        related_events = cls._find_related_agent_events(game, agent, limit=20)
        latest_agent_message = next((item for item in reversed(dialogues) if item["role"] == "agent"), None)

        return {
            "npc_key": f"agent:{agent.id}",
            "npc_kind": "agent",
            "identity": {
                "id": agent.id,
                "name": agent.name,
                "source_name": agent.source_name,
                "role": agent.role,
                "role_title": agent.role_title,
                "tier": agent.tier,
                "created_at": cls._iso(agent.created_at),
            },
            "source": {
                "model": "Agent",
                "game_id": game.id,
                "player_role": game.player_role,
                "attributes_keys": sorted(attrs.keys()),
            },
            "profile_raw": attrs,
            "profile_normalized": cls._normalize_agent_profile(attrs),
            "observation_context": context,
            "prompt_preview": prompt_preview,
            "relationships": relationships,
            "memory": cls._serialize_memory(attrs.get("memory", [])),
            "runtime": {
                "last_reasoning": (latest_agent_message or {}).get("metadata", {}).get("reasoning", ""),
                "dialogues": dialogues,
                "negotiations": negotiations,
                "promises": promises,
                "events": related_events,
            },
        }

    @classmethod
    def _build_neighbor_detail(cls, game: GameState, neighbor: NeighborCounty) -> dict:
        county = copy.deepcopy(neighbor.county_data or {})
        profile = copy.deepcopy(county.get("governor_profile", {}))
        prompt_preview = AIGovernorService.build_debug_prompt(neighbor, season=game.current_season)

        return {
            "npc_key": f"neighbor:{neighbor.id}",
            "npc_kind": "neighbor",
            "identity": {
                "id": neighbor.id,
                "name": neighbor.governor_name,
                "role": "COUNTY_MAGISTRATE",
                "role_title": "AI知县",
                "county_name": neighbor.county_name,
                "style": neighbor.governor_style,
                "archetype": neighbor.governor_archetype,
                "created_at": cls._iso(neighbor.created_at),
            },
            "source": {
                "model": "NeighborCounty",
                "game_id": game.id,
                "player_role": game.player_role,
            },
            "profile_raw": profile,
            "profile_normalized": cls._normalize_governor_profile(profile, county),
            "observation_context": prompt_preview["context"],
            "prompt_preview": {
                "system_prompt": prompt_preview["system_prompt"],
                "user_prompt": prompt_preview["user_prompt"],
            },
            "relationships": [],
            "memory": cls._serialize_memory(profile.get("memory", [])),
            "runtime": {
                "last_reasoning": neighbor.last_reasoning,
                "events": cls._serialize_neighbor_events(
                    NeighborEventLog.objects.filter(neighbor_county=neighbor).order_by("-created_at")[:20]
                ),
                "county_snapshot": cls._build_county_snapshot(county),
                "raw_state": county,
            },
        }

    @classmethod
    def _build_subordinate_detail(cls, game: GameState, unit: AdminUnit) -> dict:
        unit_data = copy.deepcopy(unit.unit_data or {})
        profile = copy.deepcopy(unit_data.get("governor_profile", {}))
        adapter = _UnitGovernorAdapter(unit)
        prompt_preview = AIGovernorService.build_debug_prompt(adapter, season=game.current_season)
        judicial_cases = JudicialCaseInstance.objects.filter(
            game=game, county_unit=unit,
        ).order_by("-updated_at")[:12]

        return {
            "npc_key": f"subordinate:{unit.id}",
            "npc_kind": "subordinate",
            "identity": {
                "id": unit.id,
                "name": profile.get("name", ""),
                "role": "COUNTY_MAGISTRATE",
                "role_title": "下辖知县",
                "county_name": unit_data.get("county_name", ""),
                "style": profile.get("style", ""),
                "archetype": profile.get("archetype", "MIDDLING"),
            },
            "source": {
                "model": "AdminUnit",
                "game_id": game.id,
                "player_role": game.player_role,
                "unit_type": unit.unit_type,
                "parent_id": unit.parent_id,
                "is_player_controlled": unit.is_player_controlled,
            },
            "profile_raw": profile,
            "profile_normalized": cls._normalize_governor_profile(profile, unit_data),
            "observation_context": prompt_preview["context"],
            "prompt_preview": {
                "system_prompt": prompt_preview["system_prompt"],
                "user_prompt": prompt_preview["user_prompt"],
            },
            "relationships": [],
            "memory": cls._serialize_memory(profile.get("memory", [])),
            "runtime": {
                "last_reasoning": unit_data.get("_last_reasoning", ""),
                "county_snapshot": cls._build_county_snapshot(unit_data),
                "pending_directives": copy.deepcopy(unit_data.get("pending_directives", [])),
                "annual_reviews": copy.deepcopy((unit_data.get("annual_reviews") or [])[-3:]),
                "judicial_cases": cls._serialize_judicial_cases(judicial_cases),
                "raw_state": unit_data,
            },
        }

    @staticmethod
    def _compact_agent_summary(agent: Agent) -> dict:
        attrs = agent.attributes or {}
        return {
            "bio": attrs.get("bio", ""),
            "intelligence": attrs.get("intelligence"),
            "charisma": attrs.get("charisma"),
            "loyalty": attrs.get("loyalty"),
        }

    @classmethod
    def _build_agent_prompt_preview(cls, agent: Agent, context: dict) -> dict:
        ctx = dict(context)
        ctx["player_message"] = "（后台调试预览）"
        if agent.tier == "FULL":
            if agent.role == "ADVISOR":
                template_name = "advisor_chat_json"
            elif agent.role == "PREFECT":
                # 知府走专属模板，ctx 由 PrefectAIService.build_chat_context 提供
                template_name = "prefect_chat_json"
            else:
                template_name = "agent_full_chat_json"
        else:
            template_name = "agent_light_chat"
        system_prompt, user_prompt = PromptRegistry.render(template_name, **ctx)
        return {
            "template_name": template_name,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }

    @classmethod
    def _build_agent_relationships(cls, agent: Agent) -> list[dict]:
        rows = []
        rels_a = agent.relationships_as_a.select_related("agent_b").all()
        rels_b = agent.relationships_as_b.select_related("agent_a").all()
        for rel in rels_a:
            rows.append({
                "direction": "outgoing",
                "partner_id": rel.agent_b_id,
                "partner_name": rel.agent_b.name,
                "partner_role_title": rel.agent_b.role_title,
                "affinity": rel.affinity,
                "data": copy.deepcopy(rel.data or {}),
            })
        for rel in rels_b:
            rows.append({
                "direction": "incoming",
                "partner_id": rel.agent_a_id,
                "partner_name": rel.agent_a.name,
                "partner_role_title": rel.agent_a.role_title,
                "affinity": rel.affinity,
                "data": copy.deepcopy(rel.data or {}),
            })
        rows.sort(key=lambda item: (-abs(int(item["affinity"])), item["partner_id"]))
        return rows

    @classmethod
    def _find_related_agent_events(cls, game: GameState, agent: Agent, limit=20) -> list[dict]:
        attrs = agent.attributes or {}
        village_name = attrs.get("village_name", "")
        rows = []
        recent = EventLog.objects.filter(game=game).order_by("-created_at")[:cls.AGENT_EVENT_SCAN_LIMIT]
        for event in recent:
            data = event.data or {}
            desc = event.description or ""
            matched = False
            if data.get("agent_id") == agent.id or data.get("agent_name") == agent.name:
                matched = True
            elif village_name and data.get("village_name") == village_name:
                matched = True
            elif agent.name and agent.name in desc:
                matched = True
            elif village_name and village_name in desc:
                matched = True
            if not matched:
                continue
            rows.append({
                "season": event.season,
                "category": event.category,
                "event_type": event.event_type,
                "description": desc,
                "data": copy.deepcopy(data),
                "created_at": cls._iso(event.created_at),
            })
            if len(rows) >= limit:
                break
        return rows

    @classmethod
    def _serialize_dialogues(cls, messages) -> list[dict]:
        items = []
        for message in reversed(list(messages)):
            items.append({
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "season": message.season,
                "metadata": copy.deepcopy(message.metadata or {}),
                "created_at": cls._iso(message.created_at),
            })
        return items

    @classmethod
    def _serialize_negotiations(cls, sessions) -> list[dict]:
        return [
            {
                "id": session.id,
                "event_type": session.event_type,
                "status": session.status,
                "current_round": session.current_round,
                "max_rounds": session.max_rounds,
                "season": session.season,
                "context_data": copy.deepcopy(session.context_data or {}),
                "outcome": copy.deepcopy(session.outcome or {}),
                "created_at": cls._iso(session.created_at),
                "resolved_at": cls._iso(session.resolved_at),
            }
            for session in sessions
        ]

    @classmethod
    def _serialize_promises(cls, promises) -> list[dict]:
        return [
            {
                "id": promise.id,
                "promise_type": promise.promise_type,
                "status": promise.status,
                "description": promise.description,
                "season_made": promise.season_made,
                "deadline_season": promise.deadline_season,
                "context": copy.deepcopy(promise.context or {}),
                "created_at": cls._iso(promise.created_at),
                "resolved_at": cls._iso(promise.resolved_at),
            }
            for promise in promises
        ]

    @classmethod
    def _serialize_neighbor_events(cls, events) -> list[dict]:
        return [
            {
                "id": item.id,
                "season": item.season,
                "event_type": item.event_type,
                "category": item.category,
                "description": item.description,
                "data": copy.deepcopy(item.data or {}),
                "created_at": cls._iso(item.created_at),
            }
            for item in events
        ]

    @classmethod
    def _serialize_judicial_cases(cls, cases) -> list[dict]:
        return [
            {
                "id": case.id,
                "template_case_id": case.template_case_id,
                "status": case.status,
                "county_review_season": case.county_review_season,
                "prefect_review_season": case.prefect_review_season,
                "case_name": (case.local_payload or {}).get("case_name", ""),
                "assistant_rounds": copy.deepcopy(case.assistant_rounds or []),
                "magistrate_rounds": copy.deepcopy(case.magistrate_rounds or []),
                "prefect_decision": copy.deepcopy(case.prefect_decision or {}),
                "updated_at": cls._iso(case.updated_at),
            }
            for case in cases
        ]

    @staticmethod
    def _serialize_memory(memory_items) -> list[dict]:
        result = []
        for idx, item in enumerate(memory_items or [], start=1):
            result.append({
                "index": idx,
                "text": item,
            })
        return result

    @staticmethod
    def _build_county_snapshot(county: dict) -> dict:
        villages = county.get("villages") or []
        total_pop = sum(int(v.get("population", 0) or 0) for v in villages)
        total_farmland = sum(int(v.get("farmland", 0) or 0) for v in villages)
        return {
            "county_name": county.get("county_name", ""),
            "county_type": county.get("county_type", ""),
            "county_type_name": county.get("county_type_name", ""),
            "treasury": county.get("treasury"),
            "morale": county.get("morale"),
            "security": county.get("security"),
            "commercial": county.get("commercial"),
            "education": county.get("education"),
            "tax_rate": county.get("tax_rate"),
            "commercial_tax_rate": county.get("commercial_tax_rate"),
            "population_total": total_pop,
            "farmland_total": total_farmland,
            "village_count": len(villages),
            "pending_directive_count": len(county.get("pending_directives") or []),
            "active_investment_count": len(county.get("active_investments") or []),
        }

    @staticmethod
    def _normalize_agent_profile(attrs: dict) -> dict:
        attrs = attrs or {}
        return {
            "core": {
                "intelligence": attrs.get("intelligence"),
                "charisma": attrs.get("charisma"),
                "loyalty": attrs.get("loyalty"),
                "player_affinity": attrs.get("player_affinity"),
            },
            "personality": copy.deepcopy(attrs.get("personality", {})),
            "ideology": copy.deepcopy(attrs.get("ideology", {})),
            "reputation": copy.deepcopy(attrs.get("reputation", {})),
            "goals": copy.deepcopy(attrs.get("goals", [])),
            "location": {
                "province": attrs.get("province", ""),
                "prefecture": attrs.get("prefecture", ""),
                "village_name": attrs.get("village_name", ""),
            },
            "bio": attrs.get("bio", ""),
            "backstory": attrs.get("backstory", ""),
        }

    @staticmethod
    def _normalize_governor_profile(profile: dict, county: dict) -> dict:
        profile = profile or {}
        return {
            "core": {
                "name": profile.get("name", ""),
                "style": profile.get("style", ""),
                "archetype": profile.get("archetype", "MIDDLING"),
                "intelligence": profile.get("intelligence"),
                "stamina": profile.get("stamina"),
            },
            "personality": copy.deepcopy(profile.get("personality", {})),
            "ideology": copy.deepcopy(profile.get("ideology", {})),
            "goals": copy.deepcopy(profile.get("goals", {})),
            "bio": profile.get("bio", ""),
            "county_snapshot": NPCDebugService._build_county_snapshot(county or {}),
        }

    @staticmethod
    def _iso(value):
        return value.isoformat() if value else None
