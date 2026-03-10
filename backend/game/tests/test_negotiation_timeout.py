from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from game.models import DialogueMessage, EventLog, GameState, NegotiationSession
from game.services import AgentService, CountyService
from game.services.negotiation import NEGOTIATION_INACTIVE_SEASONS, NegotiationService
from game.services.settlement import SettlementService


def _create_game_with_agents(start_season=5):
    username = f"u_neg_timeout_{uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, password="pw")
    county_data = CountyService.create_initial_county(county_type="fiscal_core")
    game = GameState.objects.create(user=user, current_season=start_season, county_data=county_data)
    AgentService.initialize_agents(game)
    gentry = game.agents.filter(role="GENTRY", role_title="地主").first()
    assert gentry is not None
    village_name = (gentry.attributes or {}).get("village_name") or game.county_data["villages"][0]["name"]
    return game, gentry, village_name


@pytest.mark.django_db
def test_expire_stale_negotiation_after_three_months_inactive():
    game, gentry, village_name = _create_game_with_agents(start_season=5)
    session, err = NegotiationService.start_negotiation(
        game,
        gentry,
        "ANNEXATION",
        {"village_name": village_name, "current_pct": 0.35, "proposed_pct_increase": 0.05},
    )
    assert err is None
    assert session is not None

    game.current_season = 8
    game.save(update_fields=["current_season"])

    expired = NegotiationService.expire_stale_negotiations(game, current_season=game.current_season)
    session.refresh_from_db()

    assert len(expired) == 1
    assert session.status == "resolved"
    assert session.outcome.get("reason") == "inactive_timeout"
    assert session.outcome.get("timeout_seasons") == NEGOTIATION_INACTIVE_SEASONS

    log = EventLog.objects.filter(
        game=game,
        event_type="negotiation_auto_closed",
        category="NEGOTIATION",
    ).first()
    assert log is not None
    assert log.data.get("negotiation_id") == session.id


@pytest.mark.django_db
def test_start_negotiation_auto_closes_stale_session_for_same_agent():
    game, gentry, village_name = _create_game_with_agents(start_season=5)
    old_session, err = NegotiationService.start_negotiation(
        game,
        gentry,
        "ANNEXATION",
        {"village_name": village_name, "current_pct": 0.35, "proposed_pct_increase": 0.05},
    )
    assert err is None
    assert old_session is not None

    game.current_season = 9
    game.save(update_fields=["current_season"])

    new_session, new_err = NegotiationService.start_negotiation(
        game,
        gentry,
        "IRRIGATION",
        {"village_name": village_name, "base_cost": 100, "max_contribution": 20},
    )
    old_session.refresh_from_db()

    assert new_err is None
    assert new_session is not None
    assert new_session.id != old_session.id
    assert old_session.status == "resolved"
    assert NegotiationSession.objects.filter(game=game, agent=gentry, status="active").count() == 1


@pytest.mark.django_db
def test_advance_season_auto_closes_stale_negotiations_and_reports_event():
    game, gentry, village_name = _create_game_with_agents(start_season=6)
    session, err = NegotiationService.start_negotiation(
        game,
        gentry,
        "HIDDEN_LAND",
        {"village_name": village_name, "hidden_land": 120, "current_farmland": 800, "current_gentry_pct": 0.35},
    )
    assert err is None
    assert session is not None

    game.current_season = 10
    game.save(update_fields=["current_season"])

    report = SettlementService.advance_season(game)
    session.refresh_from_db()

    assert session.status == "resolved"
    assert any("谈判自动关闭" in evt for evt in report.get("events", []))


@pytest.mark.django_db
def test_negotiation_not_closed_within_three_months_of_activity():
    game, gentry, village_name = _create_game_with_agents(start_season=5)
    session, err = NegotiationService.start_negotiation(
        game,
        gentry,
        "ANNEXATION",
        {"village_name": village_name, "current_pct": 0.35, "proposed_pct_increase": 0.05},
    )
    assert err is None
    assert session is not None

    DialogueMessage.objects.create(
        game=game,
        agent=gentry,
        role="player",
        content="先议一议此事",
        season=7,
        metadata={"negotiation_id": session.id},
    )

    game.current_season = 9
    game.save(update_fields=["current_season"])
    expired = NegotiationService.expire_stale_negotiations(game, current_season=game.current_season)
    session.refresh_from_db()

    assert expired == []
    assert session.status == "active"
