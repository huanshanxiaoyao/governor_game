"""Tests for LLM role-based peer reviews."""

import copy
import uuid

import pytest
from django.contrib.auth import get_user_model

from game.models import GameState, PlayerProfile
from game.services.agent import AgentService
from game.services.constants import MAX_MONTH
from game.services.county import CountyService
from game.services.llm_role_reviews import LLMRoleReviewService


def _attach_initial_snapshot(county):
    county["initial_villages"] = copy.deepcopy(county["villages"])
    county["initial_snapshot"] = {
        "treasury": county["treasury"],
        "morale": county["morale"],
        "security": county["security"],
        "commercial": county["commercial"],
        "education": county["education"],
        "tax_rate": county["tax_rate"],
        "commercial_tax_rate": county.get("commercial_tax_rate", 0.03),
        "school_level": county.get("school_level", 1),
        "irrigation_level": county.get("irrigation_level", 0),
        "medical_level": county.get("medical_level", 0),
        "admin_cost": county["admin_cost"],
        "peasant_grain_reserve": county.get("peasant_grain_reserve", 0),
    }


def _create_completed_game_with_agents():
    user = get_user_model().objects.create_user(
        username=f"rr_{uuid.uuid4().hex[:8]}",
        password="pw",
    )
    county = CountyService.create_initial_county(county_type="fiscal_core")
    _attach_initial_snapshot(county)
    game = GameState.objects.create(
        user=user,
        current_season=MAX_MONTH + 1,
        county_data=county,
    )
    PlayerProfile.objects.create(game=game, background="HUMBLE")
    AgentService.initialize_agents(game)
    return game


def _build_review_context(game):
    villages = []
    for idx, v in enumerate(game.county_data.get("villages", []), start=1):
        villages.append({
            "name": v["name"],
            "population": v["population"],
            "population_delta": idx * 5,
            "farmland": v["farmland"],
            "farmland_delta": idx * 10,
            "gentry_delta": 0.01 * idx,
            "has_school": v.get("has_school", False),
        })

    return {
        "objective_score": 72.5,
        "overall_score": 74.2,
        "grade": "良",
        "outcome": "稳健留任",
        "treasury_delta": 180.0,
        "morale_delta": 4.0,
        "security_delta": 2.0,
        "commercial_delta": 6.0,
        "education_delta": 5.0,
        "pop_change_pct": 3.2,
        "tax_growth_pct": 8.0,
        "disaster_count": 1,
        "annexation_count": 2,
        "broken_promises": 1,
        "prefect_affinity": 55.0,
        "disaster_multiplier": 1.025,
        "villages": villages,
        "highlights": [{"title": "财政韧性", "detail": "县库较任初稳步增长。"}],
        "risks": [{"title": "土地兼并压力", "detail": "个别村庄地权纠纷仍需化解。"}],
        "yearly_reports": [
            {
                "year": 1,
                "key_events": [
                    {"season": 6, "category": "DISASTER", "description": "夏旱冲击秋收预期"},
                ],
            },
            {
                "year": 2,
                "key_events": [
                    {"season": 17, "category": "NEGOTIATION", "description": "与地主协商水利出资"},
                ],
            },
        ],
    }


@pytest.mark.django_db
def test_focus_weights_shift_for_people_oriented_ideology():
    low_people = {
        "personality": {"openness": 0.5, "conscientiousness": 0.5, "agreeableness": 0.5},
        "ideology": {"people_vs_authority": 0.2, "reform_vs_tradition": 0.5, "pragmatic_vs_idealist": 0.5},
    }
    high_people = {
        "personality": {"openness": 0.5, "conscientiousness": 0.5, "agreeableness": 0.5},
        "ideology": {"people_vs_authority": 0.9, "reform_vs_tradition": 0.5, "pragmatic_vs_idealist": 0.5},
    }

    low_weights = LLMRoleReviewService._compute_focus_weights("villager", low_people)
    high_weights = LLMRoleReviewService._compute_focus_weights("villager", high_people)

    assert high_weights["livelihood"] > low_weights["livelihood"]
    assert high_weights["fiscal"] < low_weights["fiscal"]


@pytest.mark.django_db
def test_representative_sampling_prefers_high_impact_village():
    game = _create_completed_game_with_agents()
    gentry_candidates = list(
        game.agents.filter(role="GENTRY", role_title="地主").order_by("id")
    )
    assert len(gentry_candidates) >= 2

    low = gentry_candidates[0]
    high = gentry_candidates[1]
    village_impact = {
        low.attributes.get("village_name"): 1.0,
        high.attributes.get("village_name"): 12.0,
    }
    selected = LLMRoleReviewService._select_representative_agent(
        gentry_candidates, village_impact, event_rows=[],
    )
    assert selected.id == high.id


@pytest.mark.django_db
def test_generate_reviews_fallback_keeps_role_comment_shape(monkeypatch):
    game = _create_completed_game_with_agents()
    county = game.county_data
    context = _build_review_context(game)
    fallback = [
        {"role": "知府", "comment": "账目清楚，执行稳定，可托大任。"},
        {"role": "师爷", "comment": "县库与税基基本匹配，节奏把控得当。"},
        {"role": "士绅评议", "comment": "地面秩序平稳，商路可期。"},
        {"role": "百姓口碑", "comment": "日子比前些年安稳。"},
    ]

    monkeypatch.setattr(
        LLMRoleReviewService,
        "_llm_available",
        classmethod(lambda cls: False),
    )

    reviews = LLMRoleReviewService.generate_reviews(
        game=game,
        county=county,
        review_context=context,
        fallback_reviews=fallback,
    )
    assert [r.get("role") for r in reviews] == ["知府", "师爷", "士绅评议", "百姓口碑"]
    assert all(r.get("comment") for r in reviews)
    assert "source_agent_name" in reviews[2]
    assert "source_village" in reviews[3]

    game.refresh_from_db()
    cache = game.county_data.get(LLMRoleReviewService.CACHE_KEY) or {}
    assert cache.get("version") == LLMRoleReviewService.CACHE_VERSION
    assert len(cache.get("items", [])) == 4


def test_review_validation_rejects_id_marker_in_comment():
    visible_ids = {"k_security_delta", "k_tax_growth"}
    bad = {
        "comment": "治安提升明显（k_security_delta），可见治理有序。",
        "stance": "positive",
        "focus_dimensions": ["秩序"],
        "evidence_ids": ["k_security_delta", "k_tax_growth"],
    }
    ok = {
        "comment": "治安提升明显，乡里夜巡更稳，治理节奏较为稳妥。",
        "stance": "positive",
        "focus_dimensions": ["秩序"],
        "evidence_ids": ["k_security_delta", "k_tax_growth"],
    }

    assert LLMRoleReviewService._is_valid_review(bad, visible_ids) is False
    assert LLMRoleReviewService._is_valid_review(ok, visible_ids) is True
