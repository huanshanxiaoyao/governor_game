from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from game.models import Agent, GameState, NeighborCounty
from game.services import AgentService, CountyService
from game.services.constants import ANNUAL_CONSUMPTION, GRAIN_PER_LIANG
from game.services.emergency import EmergencyService
from game.services.settlement import SettlementService


def _baseline(county):
    total_pop = sum(
        v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
        for v in county.get("villages", [])
    )
    return total_pop * ANNUAL_CONSUMPTION / 12


@pytest.mark.django_db(databases=[])
def test_negative_reserve_clears_treasury_and_converts_to_grain(county):
    county["peasant_grain_reserve"] = -5000.0
    county["treasury"] = 20.0

    report = {"season": 5, "events": []}
    EmergencyService.prepare_month(county, month=5, report=report)

    assert county["treasury"] == 0.0
    assert county["peasant_grain_reserve"] == pytest.approx(-5000.0 + 20.0 * GRAIN_PER_LIANG)
    assert any("紧急折粮" in evt for evt in report["events"])


@pytest.mark.django_db(databases=[])
def test_negative_reserve_uses_half_monthly_consumption(county):
    county["peasant_grain_reserve"] = -1000.0
    county["treasury"] = 0.0
    report = {"season": 3, "events": []}

    EmergencyService.prepare_month(county, month=3, report=report)
    before = county["peasant_grain_reserve"]

    SettlementService._update_commercial(county, month=3, report={"events": []})
    consumed = before - county["peasant_grain_reserve"]

    expected = _baseline(county) * 0.5
    assert consumed == pytest.approx(expected)
    assert county["peasant_surplus"].get("consumption_multiplier") == 0.5


@pytest.mark.django_db(databases=[])
def test_two_consecutive_negative_months_trigger_riot(county):
    county["peasant_grain_reserve"] = -3000.0
    county["treasury"] = 0.0

    report1 = {"season": 7, "events": []}
    EmergencyService.finish_month(county, month=7, report=report1)
    assert not county["emergency"]["riot"]["active"]

    county["peasant_grain_reserve"] = -2500.0
    report2 = {"season": 8, "events": []}
    EmergencyService.finish_month(county, month=8, report=report2)

    assert county["emergency"]["riot"]["active"] is True
    assert county["emergency"]["prefect_takeover"]["active"] is True
    assert county["security"] == 0
    assert any("暴动" in evt for evt in report2["events"])


@pytest.mark.django_db
def test_borrow_from_neighbor_creates_36_installments():
    user = get_user_model().objects.create_user(username="u_emergency_borrow", password="pw")
    county = CountyService.create_initial_county("fiscal_core")
    game = GameState.objects.create(user=user, current_season=6, county_data=county)

    EmergencyService.ensure_state(game.county_data)
    baseline = _baseline(game.county_data)
    game.county_data["peasant_grain_reserve"] = baseline * 0.3
    game.save(update_fields=["county_data"])

    neighbor_county = CountyService.create_initial_county("coastal")
    EmergencyService.ensure_state(neighbor_county)
    n_baseline = _baseline(neighbor_county)
    neighbor_county["peasant_grain_reserve"] = n_baseline * 10

    neighbor = NeighborCounty.objects.create(
        game=game,
        county_name="永安县",
        governor_name="许明达",
        governor_style="minben",
        governor_archetype="VIRTUOUS",
        county_data=neighbor_county,
    )

    with patch("game.services.emergency.random.random", return_value=0.0):
        result = EmergencyService.borrow_from_neighbor(game, neighbor.id, amount=12000)

    assert result["success"] is True
    game.refresh_from_db()
    loans = game.county_data["emergency"]["neighbor_loans"]
    assert len(loans) == 1
    assert loans[0]["term_months"] == 36
    assert loans[0]["installment_grain"] == pytest.approx(round(loans[0]["principal_grain"] / 36.0, 1))


@pytest.mark.django_db
def test_force_levy_reduces_gentry_affinity_and_creates_complaint():
    user = get_user_model().objects.create_user(username="u_emergency_force", password="pw")
    county = CountyService.create_initial_county("fiscal_core")
    game = GameState.objects.create(user=user, current_season=10, county_data=county)
    AgentService.initialize_agents(game)

    EmergencyService.ensure_state(game.county_data)
    baseline = _baseline(game.county_data)
    game.county_data["peasant_grain_reserve"] = baseline * 0.4
    game.save(update_fields=["county_data"])

    gentry = Agent.objects.filter(game=game, role="GENTRY", role_title="地主").first()
    assert gentry is not None
    old_affinity = float((gentry.attributes or {}).get("player_affinity", 50.0))
    village_before = {
        v["name"]: float((v.get("gentry_ledger") or {}).get("grain_surplus", 0.0))
        for v in game.county_data.get("villages", [])
    }

    result = EmergencyService.force_levy_gentry(game, amount=50000)
    assert result["success"] is True

    game.refresh_from_db()
    gentry.refresh_from_db()
    new_affinity = float((gentry.attributes or {}).get("player_affinity", 50.0))

    assert new_affinity < old_affinity
    village_after = {
        v["name"]: float((v.get("gentry_ledger") or {}).get("grain_surplus", 0.0))
        for v in game.county_data.get("villages", [])
    }
    assert any(village_after[name] < village_before.get(name, 0.0) for name in village_after)

    breakdown = result.get("levy_breakdown") or []
    assert breakdown
    assert sum(item.get("taken", 0.0) for item in breakdown) == pytest.approx(result["collected"], abs=0.2)
    for item in breakdown:
        vname = item.get("village_name")
        assert vname in village_after
        assert village_after[vname] == pytest.approx(item.get("remaining", 0.0), abs=0.2)
        assert village_before[vname] - village_after[vname] == pytest.approx(item.get("taken", 0.0), abs=0.2)

    complaints = game.county_data["emergency"].get("complaints") or []
    assert complaints and complaints[-1]["status"] == "pending"
    assert complaints[-1]["trigger_season"] == game.current_season + 1
