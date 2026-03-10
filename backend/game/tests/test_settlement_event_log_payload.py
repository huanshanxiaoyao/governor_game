"""Settlement event log payload tests."""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from game.models import EventLog, GameState, PlayerProfile
from game.services.county import CountyService
from game.services.settlement import SettlementService


@pytest.mark.django_db
def test_advance_season_persists_full_settlement_report_payload():
    username = f"u_settle_payload_{uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, password="pw")
    county_data = CountyService.create_initial_county(county_type="fiscal_core")
    game = GameState.objects.create(user=user, current_season=1, county_data=county_data)
    PlayerProfile.objects.create(game=game, background="HUMBLE")

    report = SettlementService.advance_season(game)

    log = EventLog.objects.get(
        game=game,
        season=1,
        category="SETTLEMENT",
        event_type="season_settlement",
    )

    payload = log.data.get("settlement_report")
    assert isinstance(payload, dict)
    assert payload.get("season") == 1
    assert payload.get("next_season") == 2
    assert payload.get("game_over") is False
    assert payload.get("events") == report.get("events")
    assert "monthly_snapshot" in log.data
