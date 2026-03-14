import uuid

import pytest
from django.contrib.auth import get_user_model

from game.models import AdminUnit, GameState
from game.services.prefecture import PrefectureService


def _create_prefecture_game():
    user = get_user_model().objects.create_user(
        username=f"pref_{uuid.uuid4().hex[:8]}",
        password="pw",
    )
    game = GameState.objects.create(
        user=user,
        current_season=12,
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
            "treasury": 1280,
            "treasury_collected": 620,
            "annual_quota": 1000,
            "quota_assignments": {},
            "school_level": 2,
            "road_level": 1,
            "river_work_level": 2,
            "year_end_review_pending": True,
            "exam_pending": False,
            "pending_judicial_cases": [
                {"case_id": "j_1", "case_name": "甲案"},
                {"case_id": "j_2", "case_name": "乙案"},
            ],
        },
    )
    game.player_unit = prefecture
    game.save(update_fields=["player_unit"])

    AdminUnit.objects.create(
        game=game,
        unit_type="COUNTY",
        parent=prefecture,
        unit_data={
            "county_name": "华亭县",
            "governor_profile": {
                "name": "张知县",
                "style": "minben",
                "archetype": "VIRTUOUS",
            },
            "subordinate_reports": [{
                "month": 11,
                "indicators": {"民心": "良好", "治安": "及格", "商业": "稍好", "文教": "勉强"},
                "trend": {"民心": "↑", "治安": "→", "商业": "→", "文教": "↑"},
            }],
            "disaster_this_year": {"type": "flood", "severity": 0.4, "relieved": False},
        },
    )
    AdminUnit.objects.create(
        game=game,
        unit_type="COUNTY",
        parent=prefecture,
        unit_data={
            "county_name": "上海县",
            "governor_profile": {
                "name": "李知县",
                "style": "zhengji",
                "archetype": "MIDDLING",
            },
            "subordinate_reports": [],
            "disaster_this_year": None,
        },
    )
    return game


@pytest.mark.django_db
def test_prefecture_overview_includes_todos_and_water_infra():
    game = _create_prefecture_game()

    overview = PrefectureService.get_prefecture_overview(game)

    assert overview["river_work_level"] == 2
    assert overview["pending_judicial_count"] == 2
    assert len(overview["counties"]) == 2
    assert any(county["has_disaster"] for county in overview["counties"])

    todo_types = [item["type"] for item in overview["todo_items"]]
    assert todo_types == ["year_end_review", "judicial_case", "county_disaster"]

    disaster_item = next(item for item in overview["todo_items"] if item["type"] == "county_disaster")
    assert disaster_item["count"] == 1
    assert disaster_item["county_names"] == ["华亭县"]
    assert "洪灾" in disaster_item["title"]


@pytest.mark.django_db
def test_prefecture_invest_status_uses_new_display_labels():
    game = _create_prefecture_game()

    invest_status = PrefectureService.get_invest_status(game)
    labels = {item["project"]: item["label"] for item in invest_status["projects"]}

    assert labels["road"] == "交通基建"
    assert labels["river"] == "水利基建"


@pytest.mark.django_db
def test_decide_judicial_case_persists_effects_to_prefecture_and_county():
    game = _create_prefecture_game()
    prefecture = game.player_unit
    AdminUnit.objects.create(
        game=game,
        unit_type="COUNTY",
        parent=prefecture,
        unit_data={
            "county_name": "祥符县",
            "governor_profile": {
                "name": "王知县",
                "style": "baoshou",
                "archetype": "MIDDLING",
            },
            "subordinate_reports": [],
            "disaster_this_year": None,
        },
    )
    pdata = prefecture.unit_data
    pdata["pending_judicial_cases"] = [
        {
            "case_id": "pool_001",
            "case_name": "祥符县常平仓赈粮监守自盗案",
            "difficulty": "新手",
            "category": "吏治贪腐类",
            "source_county": "祥符县",
            "assigned_season": 12,
        },
    ]
    prefecture.unit_data = pdata
    prefecture.save(update_fields=["unit_data"])

    result = PrefectureService.decide_judicial_case(game, "pool_001", "提审改判")

    prefecture.refresh_from_db()
    county = next(
        unit for unit in AdminUnit.objects.filter(game=game, unit_type="COUNTY", parent=prefecture)
        if unit.unit_data.get("county_name") == "祥符县"
    )

    assert result["treasury"] == 2080
    assert result["applied_state"]["judicial_prestige"] == 80
    assert result["applied_state"]["inspector_favor"] == 70
    assert result["applied_state"]["prefect_affinity"] == 30

    assert prefecture.unit_data["treasury"] == 2080
    assert prefecture.unit_data["judicial_prestige"] == 80
    assert prefecture.unit_data["inspector_favor"] == 70
    assert prefecture.unit_data["pending_judicial_cases"] == []
    assert prefecture.unit_data["judicial_log"][-1]["applied_state"]["prefect_affinity"] == 30
    assert county.unit_data["prefect_affinity"] == 30


@pytest.mark.django_db
def test_inspect_county_uses_admin_unit_county_name_and_returns_bonus_targets():
    game = _create_prefecture_game()
    target = AdminUnit.objects.filter(game=game, unit_type="COUNTY").order_by("id").first()

    tongpan_result = PrefectureService.inspect_county(game, target.id, "tongpan")
    tuiguan_result = PrefectureService.inspect_county(game, target.id, "tuiguan")

    assert tongpan_result["bonus_counties"] == 1
    assert [item["county_name"] for item in tongpan_result["results"]] == ["华亭县", "上海县"]
    assert [item["county_name"] for item in tuiguan_result["results"]] == ["华亭县", "上海县"]
    assert tongpan_result["results"][0]["type"] == "通判核账"
    assert tuiguan_result["results"][0]["type"] == "推官巡查"
