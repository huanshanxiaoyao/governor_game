import uuid

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError
from rest_framework.test import APIClient

from game.models import AdminUnit, GameState, JudicialCaseInstance, PlayerProfile
from game.services.county import CountyService
from game.services.judicial_caseflow import JudicialCaseflowService
from game.services.prefecture import PrefectureService


def _make_user(prefix):
    return get_user_model().objects.create_user(
        username=f"{prefix}_{uuid.uuid4().hex[:8]}",
        password="pw",
    )


def _make_county_game(season=2):
    user = _make_user("judicial_county")
    county = CountyService.create_initial_county(county_type="fiscal_core")
    county["county_name"] = "清河县"
    game = GameState.objects.create(user=user, current_season=season, county_data=county)
    PlayerProfile.objects.create(game=game)
    unit = AdminUnit.objects.create(
        game=game,
        unit_type="COUNTY",
        unit_data=county,
        is_player_controlled=True,
    )
    game.player_unit = unit
    game.save(update_fields=["player_unit"])
    return user, game, unit


def _make_prefecture_game(season=3):
    user = _make_user("judicial_pref")
    game = GameState.objects.create(user=user, current_season=season, county_data={}, player_role="PREFECT")
    prefecture = AdminUnit.objects.create(
        game=game,
        unit_type="PREFECTURE",
        is_player_controlled=True,
        unit_data={
            "prefecture_name": "苏州府",
            "prefecture_type_name": "财赋重府",
            "treasury": 900,
            "judicial_prestige": 50,
            "inspector_favor": 50,
            "judicial_log": [],
        },
    )
    game.player_unit = prefecture
    game.save(update_fields=["player_unit"])

    county = CountyService.create_initial_county(county_type="fiscal_core")
    county["county_name"] = "华亭县"
    county["governor_profile"] = {"name": "沈知县", "style": "minben", "archetype": "VIRTUOUS"}
    subordinate = AdminUnit.objects.create(
        game=game,
        unit_type="COUNTY",
        parent=prefecture,
        unit_data=county,
        is_player_controlled=False,
    )
    return user, game, prefecture, subordinate


@pytest.mark.django_db
def test_generation_creates_five_localized_cases_for_county_window():
    _user, game, unit = _make_county_game(season=2)

    state = JudicialCaseflowService.ensure_generation_progress(game, budget_windows=1)
    payload = JudicialCaseflowService.get_county_payload(game)

    assert state.total_windows >= 12
    assert payload["available"] is True
    assert payload["pending_count"] == 5
    assert all(case["source_county"] == unit.unit_data.get("county_name", "") for case in payload["cases"])
    assert all(case["assistant_opinion"]["opinion_label"] in {"维持原判", "打回重审"} for case in payload["cases"])


@pytest.mark.django_db
def test_county_round_one_remand_generates_second_round_options():
    _user, game, _unit = _make_county_game(season=2)
    JudicialCaseflowService.ensure_generation_progress(game, budget_windows=1)
    first = JudicialCaseInstance.objects.filter(game=game, county_review_season=2).order_by("id").first()

    result = JudicialCaseflowService.decide_county_case(game, first.id, "打回重审")
    first.refresh_from_db()

    assert "error" not in result
    assert first.status == "PENDING_MAGISTRATE_ROUND_2"
    assert len(first.assistant_rounds) == 2
    assert result["case"]["available_actions"] == ["维持原判", "搁置委托上级裁定"]


@pytest.mark.django_db
def test_county_judicial_api_and_defer_penalty():
    user, game, _unit = _make_county_game(season=2)
    JudicialCaseflowService.ensure_generation_progress(game, budget_windows=1)
    case = JudicialCaseInstance.objects.filter(game=game, county_review_season=2).order_by("id").first()
    JudicialCaseflowService.decide_county_case(game, case.id, "打回重审")

    client = APIClient()
    client.force_authenticate(user=user)
    before = PlayerProfile.objects.get(game=game)
    response = client.post(f"/api/games/{game.id}/judicial/decide/", {"case_id": case.id, "action": "搁置委托上级裁定"}, format="json")
    after = PlayerProfile.objects.get(game=game)
    case.refresh_from_db()

    assert response.status_code == 200
    assert case.status == "DEFERRED_TO_PREFECT"
    assert after.competence == before.competence - 2
    assert after.popularity == before.popularity - 1


@pytest.mark.django_db
def test_prefecture_payload_reads_submitted_instances():
    _user, game, prefecture, subordinate = _make_prefecture_game(season=3)
    JudicialCaseflowService.ensure_generation_progress(game)
    instance = JudicialCaseInstance.objects.filter(game=game, county_unit=subordinate, county_review_season=2).order_by("id").first()
    instance.status = "SUBMITTED_TO_PREFECT"
    instance.submitted_to_prefect = True
    instance.submitted_season = 2
    instance.magistrate_rounds = [{"round_no": 1, "action": "AFFIRM_ORIGINAL", "action_label": "维持原判"}]
    instance.save(update_fields=["status", "submitted_to_prefect", "submitted_season", "magistrate_rounds", "updated_at"])

    payload = PrefectureService.get_judicial_cases(game)

    assert len(payload["pending_cases"]) == 1
    assert payload["pending_cases"][0]["case_id"] == str(instance.id)
    assert payload["pending_cases"][0]["source_county"] == "华亭县"


@pytest.mark.django_db
def test_county_judicial_debug_page_renders_html():
    user, game, _unit = _make_county_game(season=2)
    JudicialCaseflowService.ensure_generation_progress(game, budget_windows=1)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(f"/api/games/{game.id}/judicial/debug/page/")

    assert response.status_code == 200
    assert "text/html" in response["Content-Type"]
    assert "清河县" in response.rendered_content


@pytest.mark.django_db
def test_global_judicial_debug_page_uses_game_selector():
    user, game, _unit = _make_county_game(season=2)
    JudicialCaseflowService.ensure_generation_progress(game, budget_windows=1)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/judicial/debug/page/")

    assert response.status_code == 200
    assert "请选择游戏存档" in response.rendered_content
    assert f"#{game.id}" in response.rendered_content


@pytest.mark.django_db
def test_ai_magistrate_uses_llm_result_when_available(monkeypatch):
    _user, game, prefecture, subordinate = _make_prefecture_game(season=2)
    instance = JudicialCaseInstance.objects.create(
        game=game,
        county_unit=subordinate,
        prefect_unit=prefecture,
        template_case_id="pool_001",
        county_review_season=2,
        prefect_review_season=3,
        status="PENDING_MAGISTRATE_ROUND_1",
        local_payload={
            "case_name": "华亭县仓案",
            "difficulty": "进阶",
            "category": "吏治贪腐类",
            "dossier_text": "赈粮仓案，疑点较多。",
            "attachments": ["账册", "供词"],
            "suspicion_markers": {"critical": ["疑点一"], "secondary": ["疑点二"]},
            "options": [],
            "source_county": "华亭县",
        },
        assistant_rounds=[{"round_no": 1, "opinion": "REMAND", "opinion_label": "打回重审", "reason": "疑点较多"}],
    )

    monkeypatch.setattr(settings, "JUDICIAL_MAGISTRATE_LLM_ENABLED", True)
    monkeypatch.setitem(settings.LLM_PROVIDERS[settings.LLM_DEFAULT_PROVIDER], "api_key", "fake-key")

    def fake_llm(unit, instance, season, baseline):
        return {
            "action_label": "维持原判",
            "action_code": "AFFIRM_ORIGINAL",
            "reason": "为免案牍往返，先维持原判上呈。",
            "decision_source": "llm",
            "model": "fake-judicial-model",
            "confidence": 0.81,
            "factors": baseline["factors"],
            "latency_ms": 23,
        }

    monkeypatch.setattr(JudicialCaseflowService, "_request_llm_magistrate_decision", fake_llm)

    JudicialCaseflowService._auto_decide_ai_case(subordinate, instance, 2)
    instance.refresh_from_db()

    assert instance.status == "SUBMITTED_TO_PREFECT"
    assert instance.magistrate_rounds[-1]["decision_source"] == "llm"
    assert instance.magistrate_rounds[-1]["model"] == "fake-judicial-model"


@pytest.mark.django_db
def test_ai_magistrate_falls_back_to_rule_when_llm_fails(monkeypatch):
    _user, game, prefecture, subordinate = _make_prefecture_game(season=2)
    instance = JudicialCaseInstance.objects.create(
        game=game,
        county_unit=subordinate,
        prefect_unit=prefecture,
        template_case_id="pool_001",
        county_review_season=2,
        prefect_review_season=3,
        status="PENDING_MAGISTRATE_ROUND_1",
        local_payload={
            "case_name": "华亭县仓案",
            "difficulty": "进阶",
            "category": "吏治贪腐类",
            "dossier_text": "赈粮仓案，疑点较多。",
            "attachments": ["账册", "供词"],
            "suspicion_markers": {"critical": ["疑点一", "疑点二"], "secondary": []},
            "options": [],
            "source_county": "华亭县",
        },
        assistant_rounds=[{"round_no": 1, "opinion": "REMAND", "opinion_label": "打回重审", "reason": "疑点较多"}],
    )

    monkeypatch.setattr(settings, "JUDICIAL_MAGISTRATE_LLM_ENABLED", True)
    monkeypatch.setitem(settings.LLM_PROVIDERS[settings.LLM_DEFAULT_PROVIDER], "api_key", "fake-key")

    def boom(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(JudicialCaseflowService, "_request_llm_magistrate_decision", boom)

    JudicialCaseflowService._auto_decide_ai_case(subordinate, instance, 2)
    instance.refresh_from_db()

    assert instance.status == "PENDING_MAGISTRATE_ROUND_2"
    assert instance.magistrate_rounds[-1]["decision_source"] == "rule"


@pytest.mark.django_db
def test_county_judicial_api_returns_clear_error_when_storage_unavailable(monkeypatch):
    user, game, _unit = _make_county_game(season=2)

    def broken(_game):
        raise OperationalError("no such table: judicial_generation_states")

    monkeypatch.setattr(JudicialCaseflowService, "get_county_payload", broken)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(f"/api/games/{game.id}/judicial/")

    assert response.status_code == 503
    assert response.json()["error"] == "司法系统数据库未初始化，请先执行迁移。"
