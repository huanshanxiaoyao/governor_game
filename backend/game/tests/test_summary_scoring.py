"""Summary scoring tests for horizontal benchmark and disaster de-bias correction."""

import copy
import uuid

import pytest
from django.contrib.auth import get_user_model

from game.models import Agent, EventLog, GameState, NeighborCounty, NeighborEventLog, PlayerProfile
from game.services.constants import MAX_MONTH
from game.services.county import CountyService
from game.services.settlement import SettlementService


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


def _create_completed_game():
    user = get_user_model().objects.create_user(
        username=f"summary_{uuid.uuid4().hex[:8]}",
        password="pw",
    )
    county = CountyService.create_initial_county(county_type="fiscal_core")
    county["morale"] = 50
    county["security"] = 50
    county["commercial"] = 50
    county["education"] = 50
    _attach_initial_snapshot(county)

    game = GameState.objects.create(
        user=user,
        current_season=MAX_MONTH + 1,
        county_data=county,
    )
    PlayerProfile.objects.create(game=game, background="HUMBLE")
    return game


def _seed_player_settlement_logs(game, y1_tax=100.0, y3_tax=120.0):
    county = game.county_data
    total_pop = sum(v["population"] for v in county["villages"])
    total_farmland = sum(v["farmland"] for v in county["villages"])
    for season in range(1, MAX_MONTH + 1):
        data = {
            "monthly_snapshot": {
                "season": season,
                "treasury": round(county["treasury"], 1),
                "total_population": total_pop,
                "total_farmland": total_farmland,
                "morale": round(county["morale"], 1),
                "security": round(county["security"], 1),
                "commercial": round(county["commercial"], 1),
                "education": round(county["education"], 1),
                "peasant_grain_reserve": round(county.get("peasant_grain_reserve", 0)),
                "total_gmv": 100.0,
                "school_level": county.get("school_level", 1),
                "irrigation_level": county.get("irrigation_level", 0),
                "medical_level": county.get("medical_level", 0),
            },
        }
        if season == 9:
            data["autumn"] = {"total_tax": y1_tax}
        elif season == 33:
            data["autumn"] = {"total_tax": y3_tax}
        if season in (12, 24, 36):
            data["winter_snapshot"] = {
                "year": season // 12,
                "total_population": total_pop,
                "total_farmland": total_farmland,
                "treasury": round(county["treasury"], 1),
                "morale": round(county["morale"], 1),
                "security": round(county["security"], 1),
                "commercial": round(county["commercial"], 1),
                "education": round(county["education"], 1),
            }
        EventLog.objects.create(
            game=game,
            season=season,
            event_type="season_settlement",
            category="SETTLEMENT",
            description=f"第{season}月结算",
            data=data,
        )


def _seed_neighbor_snapshots(
    neighbor,
    y1_tax=100.0,
    y3_tax=120.0,
    disaster_entries=None,
):
    county = neighbor.county_data
    total_pop = sum(v["population"] for v in county["villages"])
    total_farmland = sum(v["farmland"] for v in county["villages"])

    disaster_by_season = {}
    for season, d_type, severity in (disaster_entries or []):
        disaster_by_season[season] = {"type": d_type, "severity": severity}

    for season in (6, 9, 12, 18, 24, 33, 36):
        data = {
            "monthly_snapshot": {
                "season": season,
                "treasury": round(county["treasury"], 1),
                "total_population": total_pop,
                "total_farmland": total_farmland,
                "morale": round(county["morale"], 1),
                "security": round(county["security"], 1),
                "commercial": round(county["commercial"], 1),
                "education": round(county["education"], 1),
            },
            "disaster_before_settlement": disaster_by_season.get(season),
        }
        if season == 9:
            data["autumn"] = {"total_tax": y1_tax}
        if season == 33:
            data["autumn"] = {"total_tax": y3_tax}
        if season in (12, 24, 36):
            data["winter_snapshot"] = {
                "year": season // 12,
                "total_population": total_pop,
                "total_farmland": total_farmland,
                "treasury": round(county["treasury"], 1),
                "morale": round(county["morale"], 1),
                "security": round(county["security"], 1),
                "commercial": round(county["commercial"], 1),
                "education": round(county["education"], 1),
            }
        NeighborEventLog.objects.create(
            neighbor_county=neighbor,
            season=season,
            event_type="season_snapshot",
            category="SETTLEMENT",
            description="测试邻县快照",
            data=data,
        )


@pytest.mark.django_db
def test_incident_score_no_longer_directly_penalizes_disaster_count():
    game = _create_completed_game()
    _seed_player_settlement_logs(game)

    # Multiple disasters should not directly lower incident_score.
    for season in (6, 18, 30):
        EventLog.objects.create(
            game=game,
            season=season,
            event_type="disaster_flood",
            category="DISASTER",
            description="测试灾害",
            data={"disaster_type": "flood", "severity": 0.5},
        )

    summary = SettlementService.get_summary_v2(game)
    assert summary is not None
    assert summary["scores"]["incident_score"] == 100.0
    assert summary["disaster_adjustment"]["disaster_count"] == 3


@pytest.mark.django_db
def test_summary_uses_result_infra_split_and_subjective_multiplier():
    game = _create_completed_game()
    _seed_player_settlement_logs(game, y1_tax=100.0, y3_tax=120.0)

    Agent.objects.create(
        game=game,
        name="赵廷章",
        role="PREFECT",
        role_title="知府",
        tier="FULL",
        attributes={"player_affinity": 80},
    )

    summary = SettlementService.get_summary_v2(game)
    assert summary is not None

    scores = summary["scores"]
    expected_objective = round(scores["result_score"] * 0.7 + scores["infrastructure_score_adjusted"] * 0.3, 1)
    assert scores["objective"] == pytest.approx(expected_objective, abs=0.1)
    assert scores["subjective_bonus"] == pytest.approx(1.0, abs=1e-6)

    expected_overall = round(min(100.0, scores["objective"] * scores["subjective_bonus"]), 1)
    assert summary["headline"]["overall_score"] == expected_overall


@pytest.mark.django_db
def test_subjective_multiplier_is_fixed_to_one_for_mvp():
    game = _create_completed_game()
    _seed_player_settlement_logs(game, y1_tax=100.0, y3_tax=120.0)

    prefect = Agent.objects.create(
        game=game,
        name="赵廷章",
        role="PREFECT",
        role_title="知府",
        tier="FULL",
        attributes={"player_affinity": -999},
    )
    summary = SettlementService.get_summary_v2(game)
    assert summary["scores"]["subjective_bonus"] == 1.0

    prefect.attributes["player_affinity"] = 999
    prefect.save(update_fields=["attributes"])
    summary = SettlementService.get_summary_v2(game)
    assert summary["scores"]["subjective_bonus"] == 1.0


@pytest.mark.django_db
def test_tax_growth_excludes_direct_agri_tax_rate_spike_effect():
    game = _create_completed_game()
    _seed_player_settlement_logs(game, y1_tax=170.0, y3_tax=250.0)

    s9 = EventLog.objects.get(game=game, category="SETTLEMENT", season=9)
    d9 = s9.data or {}
    d9["autumn"] = {
        "total_tax": 170.0,
        "agri_tax": 120.0,
        "corvee_tax_ytd": 30.0,
        "commercial_tax_ytd": 20.0,
    }
    s9.data = d9
    s9.save(update_fields=["data"])

    s33 = EventLog.objects.get(game=game, category="SETTLEMENT", season=33)
    d33 = s33.data or {}
    d33["autumn"] = {
        "total_tax": 250.0,
        "agri_tax": 200.0,
        "corvee_tax_ytd": 30.0,
        "commercial_tax_ytd": 20.0,
    }
    s33.data = d33
    s33.save(update_fields=["data"])

    EventLog.objects.create(
        game=game,
        season=24,
        event_type="tax_rate_change",
        category="TAX",
        description="测试税率调整",
        data={"old_rate": 0.12, "new_rate": 0.20},
    )

    summary = SettlementService.get_summary_v2(game)
    assert summary is not None

    tax_row = next(
        row for row in summary["horizontal_benchmark"]
        if row["id"] == "tax_growth"
    )
    assert tax_row["player_term_value"] == pytest.approx(0.0, abs=0.1)
    assert summary["scores"]["tax_score_raw"] == pytest.approx(70.0, abs=0.1)


@pytest.mark.django_db
def test_horizontal_benchmark_includes_rank_and_percentile_against_neighbors():
    game = _create_completed_game()
    _seed_player_settlement_logs(game, y1_tax=100.0, y3_tax=110.0)

    n_county = CountyService.create_initial_county(county_type="fiscal_core")
    n_county["morale"] = 50
    n_county["security"] = 50
    n_county["commercial"] = 50
    n_county["education"] = 50
    _attach_initial_snapshot(n_county)
    # Neighbor performs better on morale delta (+20).
    n_county["morale"] = 70

    neighbor = NeighborCounty.objects.create(
        game=game,
        county_name="测试邻县",
        governor_name="李同知",
        governor_style="zhengji",
        governor_bio="测试邻县描述",
        county_data=n_county,
    )

    # Provide minimal neighbor season snapshots for tax-growth extraction.
    for season, total_tax in ((9, 100.0), (33, 130.0)):
        NeighborEventLog.objects.create(
            neighbor_county=neighbor,
            season=season,
            event_type="season_snapshot",
            category="SETTLEMENT",
            description="测试快照",
            data={
                "autumn": {"total_tax": total_tax},
                "monthly_snapshot": {"season": season},
                "disaster_before_settlement": None,
            },
        )

    summary = SettlementService.get_summary_v2(game)
    assert summary is not None

    morale_row = next(
        row for row in summary["horizontal_benchmark"]
        if row["id"] == "morale_delta"
    )
    assert morale_row["total_count"] >= 2
    assert morale_row["rank"] == 2
    assert morale_row["percentile"] <= 50


@pytest.mark.django_db
def test_disaster_debias_is_multiplier_on_infra_score():
    game = _create_completed_game()
    _seed_player_settlement_logs(game)

    n_county = CountyService.create_initial_county(county_type="fiscal_core")
    _attach_initial_snapshot(n_county)
    NeighborCounty.objects.create(
        game=game,
        county_name="测试邻县2",
        governor_name="王同知",
        governor_style="wenjiao",
        governor_bio="测试邻县描述2",
        county_data=n_county,
    )

    for season, d_type, severity in ((6, "flood", 0.9), (18, "drought", 0.6)):
        EventLog.objects.create(
            game=game,
            season=season,
            event_type=f"disaster_{d_type}",
            category="DISASTER",
            description="测试灾害",
            data={"disaster_type": d_type, "severity": severity},
        )

    summary = SettlementService.get_summary_v2(game)
    assert summary is not None

    scores = summary["scores"]
    dis = summary["disaster_adjustment"]

    assert dis["disaster_count"] == 2
    assert dis["exposure_gap"] > 0
    assert dis["exposure_offset"] > 0
    assert 1.0 <= dis["disaster_multiplier"] <= 1.1
    assert scores["disaster_correction"] == dis["disaster_multiplier"]
    assert dis["total_correction"] == dis["disaster_multiplier"]

    assert "response_adjustment" not in scores
    for key in (
        "preparedness_score",
        "relief_count",
        "relief_ratio",
        "recovery_score",
        "response_score",
        "response_adjustment",
    ):
        assert key not in dis


@pytest.mark.django_db
def test_summary_v2_includes_neighbor_governor_score_benchmark():
    game = _create_completed_game()
    _seed_player_settlement_logs(game, y1_tax=100.0, y3_tax=110.0)

    n1_county = CountyService.create_initial_county(county_type="fiscal_core")
    n1_county["morale"] = 68
    n1_county["security"] = 64
    n1_county["commercial"] = 58
    n1_county["education"] = 56
    _attach_initial_snapshot(n1_county)
    n1 = NeighborCounty.objects.create(
        game=game,
        county_name="北邻县",
        governor_name="赵知县",
        governor_style="zhengji",
        governor_bio="测试邻县1",
        county_data=n1_county,
    )
    _seed_neighbor_snapshots(
        n1,
        y1_tax=100.0,
        y3_tax=140.0,
        disaster_entries=[(6, "flood", 0.8), (18, "drought", 0.5)],
    )

    n2_county = CountyService.create_initial_county(county_type="fiscal_core")
    n2_county["morale"] = 46
    n2_county["security"] = 47
    n2_county["commercial"] = 44
    n2_county["education"] = 43
    _attach_initial_snapshot(n2_county)
    n2 = NeighborCounty.objects.create(
        game=game,
        county_name="南邻县",
        governor_name="钱知县",
        governor_style="baoshou",
        governor_bio="测试邻县2",
        county_data=n2_county,
    )
    _seed_neighbor_snapshots(
        n2,
        y1_tax=100.0,
        y3_tax=90.0,
        disaster_entries=[(6, "flood", 0.2)],
    )

    summary = SettlementService.get_summary_v2(game)
    rows = summary.get("governor_score_benchmark") or []
    assert len(rows) == 2
    assert {r["neighbor_id"] for r in rows} == {n1.id, n2.id}
    assert all("comprehensive_score" in r for r in rows)
    assert all("rank" in r for r in rows)
    assert all("grade" in r for r in rows)

    scores = [r["comprehensive_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.django_db
def test_neighbor_term_summary_can_be_generated_on_demand():
    game = _create_completed_game()
    _seed_player_settlement_logs(game, y1_tax=100.0, y3_tax=120.0)

    n_county = CountyService.create_initial_county(county_type="fiscal_core")
    n_county["morale"] = 63
    n_county["security"] = 61
    n_county["commercial"] = 55
    n_county["education"] = 57
    _attach_initial_snapshot(n_county)
    neighbor = NeighborCounty.objects.create(
        game=game,
        county_name="测试邻县",
        governor_name="孙知县",
        governor_style="jinqu",
        governor_bio="测试邻县画像",
        county_data=n_county,
    )
    _seed_neighbor_snapshots(
        neighbor,
        y1_tax=100.0,
        y3_tax=132.0,
        disaster_entries=[(6, "flood", 0.6)],
    )

    report = SettlementService.get_neighbor_summary_v2(game, neighbor)
    assert report is not None
    assert report["meta"]["generated_mode"] == "on_demand"
    assert report["meta"]["neighbor_id"] == neighbor.id
    assert report["governor"]["governor_name"] == "孙知县"
    assert "overall_score" in report["headline"]
    assert "rank" in report["scores"]
    assert isinstance(report.get("yearly_reports"), list)
    assert isinstance(report.get("recent_events"), list)
