"""Neighbor baseline/snapshot persistence tests."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from game.models import GameState, NeighborCounty, NeighborEventLog
from game.services.county import CountyService
from game.services.neighbor import NeighborService


def _build_game(county_type="fiscal_core"):
    """Create a game with minimal required data for neighbor tests."""
    user = get_user_model().objects.create_user(username="u_neighbor", password="pw")
    county_data = CountyService.create_initial_county(county_type=county_type)
    return GameState.objects.create(
        user=user,
        current_season=1,
        county_data=county_data,
    )


@pytest.mark.django_db
def test_create_neighbors_persists_initial_baseline_fields():
    game = _build_game()

    NeighborService.create_neighbors(game)
    neighbors = NeighborCounty.objects.filter(game=game).order_by("id")

    assert neighbors.count() == 5
    for neighbor in neighbors:
        county = neighbor.county_data
        assert county.get("initial_villages"), "initial_villages should be persisted"
        assert len(county["initial_villages"]) == len(county.get("villages", []))

        snap = county.get("initial_snapshot") or {}
        for key in (
            "treasury",
            "morale",
            "security",
            "commercial",
            "education",
            "tax_rate",
            "commercial_tax_rate",
            "school_level",
            "irrigation_level",
            "medical_level",
            "admin_cost",
            "peasant_grain_reserve",
        ):
            assert key in snap

        assert snap["treasury"] == county["treasury"]
        assert snap["morale"] == county["morale"]
        assert snap["security"] == county["security"]
        assert snap["commercial"] == county["commercial"]
        assert snap["education"] == county["education"]


@pytest.mark.django_db
@patch("game.services.neighbor.AIGovernorService.make_decisions", return_value=[])
def test_advance_all_persists_structured_monthly_snapshot(_mock_decisions):
    game = _build_game()
    NeighborService.create_neighbors(game)

    NeighborService.advance_all(game, season=1)

    neighbors = NeighborCounty.objects.filter(game=game)
    assert neighbors.exists()

    for neighbor in neighbors:
        snapshot_logs = NeighborEventLog.objects.filter(
            neighbor_county=neighbor,
            season=1,
            event_type="season_snapshot",
        )
        assert snapshot_logs.count() == 1

        payload = snapshot_logs.first().data
        monthly = payload.get("monthly_snapshot") or {}

        assert monthly.get("season") == 1
        assert monthly.get("total_population") == sum(
            v.get("population", 0) for v in neighbor.county_data.get("villages", [])
        )
        assert monthly.get("total_farmland") == sum(
            v.get("farmland", 0) for v in neighbor.county_data.get("villages", [])
        )
        assert "tax_rate" in monthly
        assert "commercial_tax_rate" in monthly
        assert "disaster_before_settlement" in payload
        assert "disaster_after_settlement" in payload

        assert NeighborEventLog.objects.filter(
            neighbor_county=neighbor,
            season=1,
            event_type="season_settlement",
            category="SETTLEMENT",
        ).exists(), "legacy text settlement logs should remain"


@pytest.mark.django_db
@patch("game.services.neighbor.AIGovernorService.make_decisions", return_value=[])
def test_advance_all_backfills_missing_neighbor_baseline_for_old_saves(_mock_decisions):
    game = _build_game()
    county_data = CountyService.create_initial_county(county_type="coastal")
    county_data.pop("initial_villages", None)
    county_data.pop("initial_snapshot", None)

    neighbor = NeighborCounty.objects.create(
        game=game,
        county_name="旧档邻县",
        governor_name="王守成",
        governor_style="minben",
        governor_bio="测试旧档邻县",
        county_data=county_data,
    )

    NeighborService.advance_all(game, season=1)
    neighbor.refresh_from_db()

    assert neighbor.county_data.get("initial_villages"), "old saves should be backfilled"
    assert neighbor.county_data.get("initial_snapshot"), "old saves should be backfilled"
