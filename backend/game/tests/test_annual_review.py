import copy
import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from game.models import AdminUnit, GameState, PlayerProfile
from game.services.state import load_county_state, save_player_state
from game.services.annual_review import AnnualReviewService
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


def _make_user(prefix):
    return get_user_model().objects.create_user(
        username=f"{prefix}_{uuid.uuid4().hex[:8]}",
        password="pw",
    )


def _make_county_game(season=11):
    user = _make_user("annual")
    county = CountyService.create_initial_county(county_type="fiscal_core")
    _attach_initial_snapshot(county)
    game = GameState.objects.create(
        user=user,
        current_season=season,
        county_data=county,
    )
    PlayerProfile.objects.create(game=game, background="HUMBLE")
    return user, game


def _make_prefecture_game(season=12):
    user = _make_user("pref_annual")
    game = GameState.objects.create(
        user=user,
        current_season=season,
        county_data={},
        player_role="PREFECT",
    )
    prefecture = AdminUnit.objects.create(
        game=game,
        unit_type="PREFECTURE",
        is_player_controlled=True,
        unit_data={
            "prefecture_name": "应天府",
            "prefecture_type_name": "财赋重府",
            "treasury": 1000,
            "treasury_collected": 500,
            "annual_quota": 900,
            "quota_assignments": {},
            "school_level": 1,
            "road_level": 1,
            "river_work_level": 1,
            "year_end_review_pending": True,
            "pending_judicial_cases": [],
        },
    )
    game.player_unit = prefecture
    game.save(update_fields=["player_unit"])

    for idx, county_name in enumerate(["华亭县", "上海县"], start=1):
        county = CountyService.create_initial_county(county_type="fiscal_core")
        county["county_name"] = county_name
        county["morale"] = 86 if idx == 1 else 38
        county["security"] = 84 if idx == 1 else 34
        county["commercial"] = 78 if idx == 1 else 42
        county["education"] = 74 if idx == 1 else 36
        county["treasury"] = 420 if idx == 1 else 40
        county["annual_quota"] = {"total": 400}
        county["fiscal_year"] = {
            "agri_remitted": 320 if idx == 1 else 60,
            "commercial_tax": 55 if idx == 1 else 15,
            "commercial_retained": 5 if idx == 1 else 6,
            "corvee_tax": 22 if idx == 1 else 8,
            "corvee_retained": 4 if idx == 1 else 4,
        }
        county["governor_profile"] = {
            "name": f"知县{idx}",
            "style": "zhengji" if idx == 1 else "baoshou",
            "archetype": "MIDDLING" if idx == 1 else "CORRUPT",
            "bio": f"知县{idx}简历",
        }
        county["subordinate_reports"] = []
        AdminUnit.objects.create(
            game=game,
            unit_type="COUNTY",
            parent=prefecture,
            unit_data=county,
        )

    return game


@pytest.mark.django_db
def test_county_advance_requires_self_statement_before_december():
    user, game = _make_county_game(season=11)
    client = APIClient()
    client.force_authenticate(user=user)

    resp = client.post(f"/api/games/{game.id}/advance/", {}, format="json")
    assert resp.status_code == 400
    assert "年度自陈" in resp.json()["error"]

    submit = client.post(
        f"/api/games/{game.id}/annual-review/",
        {
            "achievements": "今年赋税大体如期征解。",
            "unfinished": "商路修整尚未收尾。",
            "faults": "部分事务催办迟缓。",
            "plan": "来年先补商路与仓储短板。",
        },
        format="json",
    )
    assert submit.status_code == 200
    assert submit.json()["entry"]["self_statement"]["achievements"] == "今年赋税大体如期征解。"

    advance = client.post(f"/api/games/{game.id}/advance/", {}, format="json")
    assert advance.status_code == 200
    assert any("知府已完成本年初评" in evt for evt in advance.json()["events"])

    game.refresh_from_db()
    cycle = game.county_data["annual_reviews"][0]
    assert game.current_season == 12
    assert cycle["prefect_review"]["grade"] in AnnualReviewService.GRADES


@pytest.mark.django_db
def test_county_final_bad_review_ends_game_early():
    user, game = _make_county_game(season=11)
    county = load_county_state(game)
    county["morale"] = 18
    county["security"] = 20
    county["commercial"] = 24
    county["education"] = 22
    county["treasury"] = 0
    county["annual_quota"] = {"total": 600}
    county["fiscal_year"] = {
        "agri_remitted": 40,
        "commercial_tax": 8,
        "commercial_retained": 4,
        "corvee_tax": 6,
        "corvee_retained": 3,
    }
    county["disaster_this_year"] = {"type": "flood", "severity": 0.8}
    save_player_state(game, county)

    client = APIClient()
    client.force_authenticate(user=user)
    submit = client.post(
        f"/api/games/{game.id}/annual-review/",
        {
            "achievements": "诸事大体无碍。",
            "unfinished": "无。",
            "faults": "无。",
            "plan": "来年再议。",
        },
        format="json",
    )
    assert submit.status_code == 200

    nov = client.post(f"/api/games/{game.id}/advance/", {}, format="json")
    assert nov.status_code == 200

    dec = client.post(f"/api/games/{game.id}/advance/", {}, format="json")
    assert dec.status_code == 200
    assert dec.json()["game_over"] is True
    assert any("革退" in evt for evt in dec.json()["events"])

    game.refresh_from_db()
    assert game.current_season > 36
    cycle = game.county_data["annual_reviews"][0]
    assert cycle["governor_recheck"]["final_grade"] == "差"


@pytest.mark.django_db
def test_early_dismissal_summary_uses_actual_term_window():
    user, game = _make_county_game(season=11)
    county = load_county_state(game)
    county["morale"] = 18
    county["security"] = 20
    county["commercial"] = 24
    county["education"] = 22
    county["treasury"] = 0
    county["annual_quota"] = {"total": 600}
    county["fiscal_year"] = {
        "agri_remitted": 40,
        "commercial_tax": 8,
        "commercial_retained": 4,
        "corvee_tax": 6,
        "corvee_retained": 3,
    }
    county["disaster_this_year"] = {"type": "flood", "severity": 0.8}
    save_player_state(game, county)

    client = APIClient()
    client.force_authenticate(user=user)
    assert client.post(
        f"/api/games/{game.id}/annual-review/",
        {
            "achievements": "诸事大体无碍。",
            "unfinished": "无。",
            "faults": "无。",
            "plan": "来年再议。",
        },
        format="json",
    ).status_code == 200
    assert client.post(f"/api/games/{game.id}/advance/", {}, format="json").status_code == 200
    assert client.post(f"/api/games/{game.id}/advance/", {}, format="json").status_code == 200

    game.refresh_from_db()
    summary = SettlementService.get_summary_v2(game)

    assert summary is not None
    assert summary["meta"]["final_month"] == 12
    assert summary["meta"]["ended_early"] is True
    assert "第1年·腊月" in summary["meta"]["term_note"]
    assert "三年述职" not in summary["headline"]["title"]
    assert len(summary["yearly_reports"]) == 1


@pytest.mark.django_db
def test_prefecture_review_blocker_and_january_replacement():
    game = _make_prefecture_game(season=12)

    payload = AnnualReviewService.get_prefecture_personnel_payload(game)
    assert payload["available"] is True
    assert payload["phase"] == "review"
    assert AnnualReviewService.get_prefecture_advance_blocker(game) is not None

    poor_unit = payload["counties"][1]
    ok_unit = payload["counties"][0]

    result_ok = AnnualReviewService.submit_prefecture_review(
        game=game,
        unit_id=ok_unit["unit_id"],
        grade="良",
        strengths="税赋尚能应差，办事有条理。",
        weaknesses="县库调度仍显拘谨。",
        focus="来年先补县库与道路。",
    )
    assert result_ok["prefect_review"]["grade"] == "良"

    result_poor = AnnualReviewService.submit_prefecture_review(
        game=game,
        unit_id=poor_unit["unit_id"],
        grade="差",
        strengths="偶有守成之举。",
        weaknesses="税赋、治安俱弱，且有瞒饰之嫌。",
        focus="若仍不振作，当行撤换。",
    )
    original_name = poor_unit["governor_name"]
    assert result_poor["prefect_review"]["grade"] == "差"
    assert AnnualReviewService.get_prefecture_advance_blocker(game) is None

    summary = AnnualReviewService.finalize_prefecture_reviews(game, publish_season=13)
    assert summary["finalized"] == 2
    assert summary["replaced"] == 1

    replaced_unit = AdminUnit.objects.get(id=poor_unit["unit_id"])
    cycle = next(item for item in replaced_unit.unit_data["annual_reviews"] if item["year"] == 1)
    assert cycle["governor_recheck"]["final_grade"] == "差"
    assert cycle["replacement"]["incoming_name"]
    assert replaced_unit.unit_data["governor_profile"]["name"] != original_name
