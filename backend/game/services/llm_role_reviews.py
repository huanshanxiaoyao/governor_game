"""任期述职多方评价：按角色信息边界 + 人格镜头 生成评语。"""

from __future__ import annotations

import copy
import logging
import re

from llm.client import LLMClient
from llm.prompts import PromptRegistry
from llm.providers import get_provider

from ..models import Agent, EventLog
from .constants import MAX_MONTH

logger = logging.getLogger("game")


class LLMRoleReviewService:
    """Generate role-based peer reviews for summary-v2."""

    CACHE_KEY = "summary_peer_reviews"
    CACHE_VERSION = "persona_v3"
    ROLE_ORDER = ("prefect", "advisor", "gentry", "villager")
    ROLE_LABELS = {
        "prefect": "知府",
        "advisor": "师爷",
        "gentry": "士绅评议",
        "villager": "百姓口碑",
    }
    DIMENSION_LABELS = {
        "fiscal": "财赋",
        "livelihood": "民生",
        "order": "秩序",
        "reform": "革新",
        "credibility": "诚信",
        "property": "地权",
        "education": "文教",
    }
    BASE_WEIGHTS = {
        "prefect": {
            "fiscal": 0.34,
            "livelihood": 0.14,
            "order": 0.24,
            "reform": 0.10,
            "credibility": 0.12,
            "property": 0.04,
            "education": 0.02,
        },
        "advisor": {
            "fiscal": 0.22,
            "livelihood": 0.20,
            "order": 0.18,
            "reform": 0.14,
            "credibility": 0.14,
            "property": 0.03,
            "education": 0.09,
        },
        "gentry": {
            "fiscal": 0.20,
            "livelihood": 0.10,
            "order": 0.15,
            "reform": 0.07,
            "credibility": 0.10,
            "property": 0.35,
            "education": 0.03,
        },
        "villager": {
            "fiscal": 0.06,
            "livelihood": 0.42,
            "order": 0.20,
            "reform": 0.08,
            "credibility": 0.15,
            "property": 0.03,
            "education": 0.06,
        },
    }
    ROLE_SCOPE_NOTE = {
        "prefect": "你主要掌握县级政务结果、税赋执行与稳定风险，不能引用基层私人谈判内情。",
        "advisor": "你熟悉县政走势与村庄变化，能做结构分析，但不应引用他人私密动机。",
        "gentry": "你只可依据公开政策、地面秩序和本村可感知事实发言，不可冒充官府内账。",
        "villager": "你只可依据本村体感、治安温饱与公开政策发言，不可引用衙门内情。",
    }
    EVIDENCE_MARKER_RE = re.compile(r"(?:^|[\s\(\[（【])(?:k_|h_|r_|e_y|v_|self_evt_)\w*")

    @classmethod
    def generate_reviews(cls, game, county, review_context, fallback_reviews):
        """Generate four reviews in fixed order, with graceful fallback."""
        cached = county.get(cls.CACHE_KEY) or {}
        if (
            cached.get("version") == cls.CACHE_VERSION
            and isinstance(cached.get("items"), list)
            and len(cached.get("items")) == 4
        ):
            return copy.deepcopy(cached["items"])

        fallback_map = {
            item.get("role"): item.get("comment", "")
            for item in (fallback_reviews or [])
        }
        role_specs, event_rows = cls._build_role_specs(game, review_context)
        fact_pack = cls._build_fact_pack(review_context)
        llm_enabled = cls._llm_available()

        reviews = []
        for spec in role_specs:
            if llm_enabled:
                review = cls._generate_single_review(spec, fact_pack, event_rows)
            else:
                review = None

            if review is None:
                review = cls._fallback_review(spec, fallback_map)
            reviews.append(review)

        cls._cache_reviews(game, county, reviews)
        return reviews

    @classmethod
    def _llm_available(cls):
        try:
            provider = get_provider()
            return bool((provider.api_key or "").strip())
        except Exception:
            return False

    @classmethod
    def _build_role_specs(cls, game, review_context):
        event_rows = list(
            EventLog.objects.filter(
                game=game, season__lte=MAX_MONTH,
            ).values("season", "category", "description", "data")
        )
        village_impact = cls._build_village_impact_map(review_context)

        prefect = Agent.objects.filter(game=game, role="PREFECT").order_by("id").first()
        advisor = Agent.objects.filter(game=game, role="ADVISOR").order_by("id").first()
        gentry_candidates = list(
            Agent.objects.filter(
                game=game, role="GENTRY", role_title="地主",
            ).order_by("id")
        )
        villager_candidates = list(
            Agent.objects.filter(
                game=game, role="VILLAGER", role_title="村民代表",
            ).order_by("id")
        )

        gentry = cls._select_representative_agent(
            gentry_candidates, village_impact, event_rows,
        )
        villager = cls._select_representative_agent(
            villager_candidates, village_impact, event_rows,
        )

        specs = []
        for role_key, agent in (
            ("prefect", prefect),
            ("advisor", advisor),
            ("gentry", gentry),
            ("villager", villager),
        ):
            attrs = agent.attributes if agent else {}
            specs.append({
                "role_key": role_key,
                "role_label": cls.ROLE_LABELS[role_key],
                "scope_note": cls.ROLE_SCOPE_NOTE[role_key],
                "agent": agent,
                "agent_name": agent.name if agent else "",
                "village_name": attrs.get("village_name", ""),
                "persona": cls._build_persona(role_key, attrs),
            })
        return specs, event_rows

    @classmethod
    def _build_village_impact_map(cls, review_context):
        impacts = {}
        for v in review_context.get("villages", []):
            name = v.get("name")
            if not name:
                continue
            pop = abs(cls._safe_float(v.get("population_delta"), 0.0))
            farm = abs(cls._safe_float(v.get("farmland_delta"), 0.0))
            gentry = abs(cls._safe_float(v.get("gentry_delta"), 0.0))
            # Keep score scale simple and stable across runs.
            score = pop / 50.0 + farm / 200.0 + gentry * 120.0
            impacts[name] = round(score, 4)
        return impacts

    @classmethod
    def _select_representative_agent(cls, candidates, village_impact, event_rows):
        if not candidates:
            return None

        scored = []
        for agent in candidates:
            attrs = agent.attributes or {}
            village_name = attrs.get("village_name", "")
            affinity = cls._safe_float(attrs.get("player_affinity"), 50.0)
            impact_score = village_impact.get(village_name, 0.0)
            event_score = cls._count_related_events(agent.name, village_name, event_rows)
            affinity_score = abs(affinity - 50.0) / 10.0
            total = impact_score + event_score + affinity_score
            scored.append((total, agent.id, agent))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored[0][2]

    @classmethod
    def _count_related_events(cls, agent_name, village_name, event_rows):
        score = 0.0
        for row in event_rows:
            data = row.get("data") or {}
            category = row.get("category", "")
            base = 2.0 if category in ("NEGOTIATION", "PROMISE", "ANNEXATION") else 1.0
            desc = row.get("description", "")
            if data.get("agent_name") == agent_name:
                score += 2.5 * base
            if village_name and data.get("village_name") == village_name:
                score += 1.5 * base
            if agent_name and agent_name in desc:
                score += 1.0
            if village_name and village_name in desc:
                score += 0.8
        return score

    @classmethod
    def _build_persona(cls, role_key, attrs):
        personality = attrs.get("personality", {}) if isinstance(attrs, dict) else {}
        ideology = attrs.get("ideology", {}) if isinstance(attrs, dict) else {}
        goals = attrs.get("goals", []) if isinstance(attrs, dict) else []
        memory = attrs.get("memory", []) if isinstance(attrs, dict) else []
        affinity = cls._safe_float(attrs.get("player_affinity"), 50.0)
        focus_weights = cls._compute_focus_weights(role_key, attrs)
        tone_hint = cls._build_tone_hint(affinity, personality)

        return {
            "bio": attrs.get("bio", ""),
            "personality_desc": cls._describe_personality(personality),
            "ideology_desc": cls._describe_ideology(ideology),
            "goals_desc": cls._describe_goals(goals),
            "memory_desc": cls._describe_memory(memory),
            "affinity": round(affinity, 1),
            "focus_weights": focus_weights,
            "focus_desc": cls._format_focus_desc(focus_weights),
            "tone_hint": tone_hint,
            "top_dimensions": cls._top_dimensions(focus_weights, top_n=3),
        }

    @classmethod
    def _compute_focus_weights(cls, role_key, attrs):
        weights = dict(cls.BASE_WEIGHTS[role_key])
        personality = attrs.get("personality", {}) if isinstance(attrs, dict) else {}
        ideology = attrs.get("ideology", {}) if isinstance(attrs, dict) else {}

        people_shift = cls._safe_float(ideology.get("people_vs_authority"), 0.5) - 0.5
        reform_shift = cls._safe_float(ideology.get("reform_vs_tradition"), 0.5) - 0.5
        pragmatic_shift = cls._safe_float(ideology.get("pragmatic_vs_idealist"), 0.5) - 0.5
        open_shift = cls._safe_float(personality.get("openness"), 0.5) - 0.5
        diligent_shift = cls._safe_float(personality.get("conscientiousness"), 0.5) - 0.5

        weights["livelihood"] += people_shift * 0.30
        weights["fiscal"] -= people_shift * 0.18
        weights["order"] -= people_shift * 0.05

        weights["reform"] += reform_shift * 0.25
        weights["education"] += reform_shift * 0.15
        weights["order"] -= reform_shift * 0.08

        weights["fiscal"] += pragmatic_shift * 0.15
        weights["order"] += pragmatic_shift * 0.10
        weights["reform"] -= pragmatic_shift * 0.08

        weights["reform"] += open_shift * 0.18
        weights["credibility"] += diligent_shift * 0.22
        weights["order"] += diligent_shift * 0.08

        for key in list(weights.keys()):
            weights[key] = max(0.02, weights[key])

        total = sum(weights.values()) or 1.0
        normalized = {k: round(v / total, 4) for k, v in weights.items()}
        return normalized

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _describe_personality(personality):
        parts = []
        openness = LLMRoleReviewService._safe_float(personality.get("openness"), 0.5)
        diligence = LLMRoleReviewService._safe_float(personality.get("conscientiousness"), 0.5)
        agree = LLMRoleReviewService._safe_float(personality.get("agreeableness"), 0.5)

        if openness >= 0.7:
            parts.append("思路开放")
        elif openness <= 0.3:
            parts.append("偏守旧")
        if diligence >= 0.7:
            parts.append("做事严谨")
        elif diligence <= 0.3:
            parts.append("行事随性")
        if agree >= 0.7:
            parts.append("处事温和")
        elif agree <= 0.3:
            parts.append("言辞强硬")
        return "，".join(parts) if parts else "性格中和"

    @staticmethod
    def _describe_ideology(ideology):
        parts = []
        reform = LLMRoleReviewService._safe_float(ideology.get("reform_vs_tradition"), 0.5)
        people = LLMRoleReviewService._safe_float(ideology.get("people_vs_authority"), 0.5)
        pragmatic = LLMRoleReviewService._safe_float(ideology.get("pragmatic_vs_idealist"), 0.5)

        if reform >= 0.7:
            parts.append("偏向改革")
        elif reform <= 0.3:
            parts.append("偏重守成")
        if people >= 0.7:
            parts.append("重民生")
        elif people <= 0.3:
            parts.append("重权威")
        if pragmatic >= 0.7:
            parts.append("务实求效")
        elif pragmatic <= 0.3:
            parts.append("重理想名义")
        return "，".join(parts) if parts else "立场中庸"

    @staticmethod
    def _describe_goals(goals):
        if not goals:
            return "暂无明确目标"
        return "；".join(str(g) for g in goals[:3])

    @staticmethod
    def _describe_memory(memory):
        if not memory:
            return "近期无突出记忆"
        recent = [str(m) for m in memory[-3:]]
        return "；".join(recent)

    @classmethod
    def _build_tone_hint(cls, affinity, personality):
        agree = cls._safe_float(personality.get("agreeableness"), 0.5)
        if affinity >= 70:
            tone = "整体语气偏宽和，可肯定但不溢美。"
        elif affinity <= 35:
            tone = "整体语气偏审慎甚至苛刻，可直言不足。"
        else:
            tone = "整体语气中性克制，兼顾肯定与提醒。"
        if agree <= 0.3:
            tone += "你性格强硬，措辞可更锋利。"
        return tone

    @classmethod
    def _top_dimensions(cls, weights, top_n=3):
        pairs = sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:top_n]
        return [cls.DIMENSION_LABELS.get(key, key) for key, _ in pairs]

    @classmethod
    def _format_focus_desc(cls, weights):
        pairs = sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:4]
        return "、".join(
            f"{cls.DIMENSION_LABELS.get(key, key)}({value:.2f})"
            for key, value in pairs
        )

    @classmethod
    def _build_fact_pack(cls, review_context):
        facts = {}
        role_ids = {key: [] for key in cls.ROLE_ORDER}
        village_fact_id_map = {}

        def add(fid, text, targets):
            if not text:
                return
            if fid in facts:
                return
            facts[fid] = text
            for target in targets:
                role_ids[target].append(fid)

        add(
            "k_grade",
            f"述职综合评级为{review_context.get('grade', '-')}"
            f"（综合分{cls._safe_float(review_context.get('overall_score'), 0):.1f}）。",
            ("prefect", "advisor"),
        )
        add(
            "k_treasury_delta",
            f"任内县库较任初变化{cls._safe_float(review_context.get('treasury_delta'), 0):+.1f}两。",
            ("prefect", "advisor"),
        )
        add(
            "k_morale_delta",
            f"任内民心较任初变化{cls._safe_float(review_context.get('morale_delta'), 0):+.1f}。",
            ("prefect", "advisor", "gentry", "villager"),
        )
        add(
            "k_security_delta",
            f"任内治安较任初变化{cls._safe_float(review_context.get('security_delta'), 0):+.1f}。",
            ("prefect", "advisor", "gentry", "villager"),
        )
        add(
            "k_commercial_delta",
            f"任内商业较任初变化{cls._safe_float(review_context.get('commercial_delta'), 0):+.1f}。",
            ("prefect", "advisor", "gentry"),
        )
        add(
            "k_education_delta",
            f"任内文教较任初变化{cls._safe_float(review_context.get('education_delta'), 0):+.1f}。",
            ("prefect", "advisor", "villager"),
        )
        tax_growth = review_context.get("tax_growth_pct")
        if tax_growth is not None:
            add(
                "k_tax_growth",
                f"首末月预期税基变化（默认税率口径）{cls._safe_float(tax_growth):+.1f}%。",
                ("prefect", "advisor", "gentry"),
            )
        pop_change = review_context.get("pop_change_pct")
        if pop_change is not None:
            add(
                "k_pop_change",
                f"任内总人口较任初变化{cls._safe_float(pop_change):+.1f}%。",
                ("prefect", "advisor", "villager"),
            )
        add(
            "k_disaster_count",
            f"任内记录灾害事件{int(cls._safe_float(review_context.get('disaster_count'), 0))}次。",
            ("prefect", "advisor", "gentry", "villager"),
        )
        add(
            "k_annexation_count",
            f"任内兼并相关事件触发{int(cls._safe_float(review_context.get('annexation_count'), 0))}次。",
            ("prefect", "advisor", "gentry"),
        )
        add(
            "k_broken_promises",
            f"任内存在{int(cls._safe_float(review_context.get('broken_promises'), 0))}项违约承诺。",
            ("prefect", "advisor", "gentry", "villager"),
        )
        add(
            "k_disaster_multiplier",
            f"灾害暴露消偏系数为x{cls._safe_float(review_context.get('disaster_multiplier'), 1.0):.3f}。",
            ("prefect", "advisor"),
        )

        for idx, item in enumerate(review_context.get("highlights", [])[:4], start=1):
            add(
                f"h_{idx}",
                f"亮点：{item.get('title', '')}，{item.get('detail', '')}",
                ("prefect", "advisor"),
            )
        for idx, item in enumerate(review_context.get("risks", [])[:4], start=1):
            add(
                f"r_{idx}",
                f"风险：{item.get('title', '')}，{item.get('detail', '')}",
                ("prefect", "advisor"),
            )

        for idx, village in enumerate(review_context.get("villages", [])[:12], start=1):
            name = village.get("name")
            if not name:
                continue
            village_id = f"v_{idx}"
            pop_delta = village.get("population_delta")
            farm_delta = village.get("farmland_delta")
            gentry_delta = village.get("gentry_delta")
            text = (
                f"{name}：人口变化{cls._fmt_signed(pop_delta, '人')}，"
                f"耕地变化{cls._fmt_signed(farm_delta, '亩')}，"
                f"地主占地比变化{cls._fmt_signed_pct(gentry_delta)}。"
            )
            add(village_id, text, ("advisor",))
            village_fact_id_map[name] = village_id

        for year in review_context.get("yearly_reports", []):
            y = int(cls._safe_float(year.get("year"), 0))
            for eidx, event in enumerate(year.get("key_events", [])[:4], start=1):
                event_id = f"e_y{y}_{eidx}"
                desc = event.get("description", "")
                season = event.get("season")
                category = event.get("category", "")
                text = f"第{season}月[{category}] {desc}"
                targets = ["prefect", "advisor"]
                if category in ("DISASTER", "ANNEXATION", "NEGOTIATION", "PROMISE"):
                    targets.extend(["gentry", "villager"])
                add(event_id, text, tuple(targets))

        return {
            "facts": facts,
            "role_ids": role_ids,
            "village_fact_id_map": village_fact_id_map,
        }

    @classmethod
    def _fmt_signed(cls, value, unit):
        if value is None:
            return f"0{unit}"
        num = cls._safe_float(value, 0.0)
        return f"{num:+.0f}{unit}"

    @classmethod
    def _fmt_signed_pct(cls, value):
        if value is None:
            return "+0.0%"
        num = cls._safe_float(value, 0.0) * 100.0
        return f"{num:+.1f}%"

    @classmethod
    def _generate_single_review(cls, spec, fact_pack, event_rows):
        visible_facts = cls._build_visible_facts(spec, fact_pack, event_rows)
        if not visible_facts:
            return None

        visible_fact_lines = [f"- {row['text']}" for row in visible_facts]
        evidence_index_lines = [f"- {row['id']}: {row['text']}" for row in visible_facts]
        visible_fact_ids = {row["id"] for row in visible_facts}
        persona = spec["persona"]
        ctx = {
            "reviewer_role": spec["role_label"],
            "reviewer_name": spec.get("agent_name", "") or spec["role_label"],
            "reviewer_bio": persona["bio"] or "暂无额外背景。",
            "personality_desc": persona["personality_desc"],
            "ideology_desc": persona["ideology_desc"],
            "goals_desc": persona["goals_desc"],
            "memory_desc": persona["memory_desc"],
            "affinity": persona["affinity"],
            "focus_desc": persona["focus_desc"],
            "top_dimensions": "、".join(persona["top_dimensions"]),
            "tone_hint": persona["tone_hint"],
            "scope_note": spec["scope_note"],
            "visible_facts_text": "\n".join(visible_fact_lines),
            "evidence_index": "\n".join(evidence_index_lines),
            "output_role": spec["role_label"],
        }
        system_prompt, user_prompt = PromptRegistry.render("term_peer_review_json", **ctx)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        for attempt in (1, 2):
            try:
                client = LLMClient(timeout=20.0, max_retries=2)
                result = client.chat_json(
                    messages,
                    temperature=0.55 if attempt == 1 else 0.2,
                    max_tokens=420,
                )
            except Exception as exc:
                logger.warning("Role review LLM failed (%s): %s", spec["role_key"], exc)
                continue

            if not cls._is_valid_review(result, visible_fact_ids):
                continue
            return cls._normalize_review(result, spec, persona["top_dimensions"])

        return None

    @classmethod
    def _build_visible_facts(cls, spec, fact_pack, event_rows):
        role_key = spec["role_key"]
        facts = fact_pack["facts"]
        role_ids = list(fact_pack["role_ids"].get(role_key, []))
        village_name = spec.get("village_name", "")
        village_fact_id = fact_pack["village_fact_id_map"].get(village_name)
        if village_fact_id:
            role_ids.append(village_fact_id)

        visible = []
        for fid in role_ids:
            if fid in facts:
                visible.append({"id": fid, "text": facts[fid]})

        if role_key in ("gentry", "villager"):
            extra = cls._related_event_facts(spec, event_rows, limit=4)
            visible.extend(extra)

        # Keep concise context windows.
        max_facts = 14 if role_key in ("prefect", "advisor") else 10
        return visible[:max_facts]

    @classmethod
    def _related_event_facts(cls, spec, event_rows, limit=4):
        agent_name = spec.get("agent_name", "")
        village_name = spec.get("village_name", "")
        if not agent_name and not village_name:
            return []

        selected = []
        for row in sorted(event_rows, key=lambda item: item.get("season", 0), reverse=True):
            data = row.get("data") or {}
            desc = row.get("description", "")
            matched = False
            if data.get("agent_name") == agent_name:
                matched = True
            if village_name and data.get("village_name") == village_name:
                matched = True
            if agent_name and agent_name in desc:
                matched = True
            if village_name and village_name in desc:
                matched = True
            if not matched:
                continue

            sid = row.get("season")
            cat = row.get("category", "")
            fact_id = f"self_evt_{len(selected) + 1}"
            selected.append({
                "id": fact_id,
                "text": f"第{sid}月[{cat}] {desc}",
            })
            if len(selected) >= limit:
                break
        return selected

    @classmethod
    def _is_valid_review(cls, result, visible_fact_ids):
        if not isinstance(result, dict):
            return False

        comment = str(result.get("comment", "")).strip()
        if len(comment) < 8:
            return False
        if cls._contains_evidence_marker(comment):
            return False

        evidence = result.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence:
            return False
        for eid in evidence:
            if str(eid) not in visible_fact_ids:
                return False
        return True

    @classmethod
    def _contains_evidence_marker(cls, text):
        if not text:
            return False
        return bool(cls.EVIDENCE_MARKER_RE.search(text))

    @classmethod
    def _normalize_review(cls, result, spec, top_dimensions):
        comment = str(result.get("comment", "")).strip()
        stance = str(result.get("stance", "mixed")).strip().lower()
        if stance not in ("positive", "mixed", "negative"):
            stance = cls._infer_stance(comment)

        focus = result.get("focus_dimensions")
        if not isinstance(focus, list):
            focus = []
        focus = [str(item).strip() for item in focus if str(item).strip()]
        if not focus:
            focus = list(top_dimensions)
        focus = focus[:3]

        evidence_ids = []
        for item in result.get("evidence_ids", []):
            text = str(item).strip()
            if text and text not in evidence_ids:
                evidence_ids.append(text)

        return {
            "role": spec["role_label"],
            "comment": comment,
            "stance": stance,
            "focus_dimensions": focus,
            "evidence_ids": evidence_ids,
            "source_agent_name": spec.get("agent_name", ""),
            "source_village": spec.get("village_name", ""),
        }

    @classmethod
    def _infer_stance(cls, comment):
        negative_keys = ("隐忧", "不足", "偏弱", "承压", "怨", "风险", "欠账")
        positive_keys = ("稳健", "有序", "见效", "向好", "可期", "得当", "安稳")
        if any(key in comment for key in negative_keys):
            return "negative"
        if any(key in comment for key in positive_keys):
            return "positive"
        return "mixed"

    @classmethod
    def _fallback_review(cls, spec, fallback_map):
        comment = fallback_map.get(spec["role_label"], "")
        if not comment:
            comment = "所见有限，仍需结合后任施政持续观测。"
        return {
            "role": spec["role_label"],
            "comment": comment,
            "stance": cls._infer_stance(comment),
            "focus_dimensions": spec["persona"]["top_dimensions"],
            "evidence_ids": [],
            "source_agent_name": spec.get("agent_name", ""),
            "source_village": spec.get("village_name", ""),
        }

    @classmethod
    def _cache_reviews(cls, game, county, reviews):
        payload = {"version": cls.CACHE_VERSION, "items": copy.deepcopy(reviews)}
        if county.get(cls.CACHE_KEY) == payload:
            return

        county[cls.CACHE_KEY] = payload
        try:
            game.county_data = county
            game.save(update_fields=["county_data"])
        except Exception as exc:
            logger.warning("Failed to cache peer reviews: %s", exc)
