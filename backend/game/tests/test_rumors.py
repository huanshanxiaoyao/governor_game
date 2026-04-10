from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from game.models import GameState
from game.services import CountyService
from game.services.rumors import RumorsService


@pytest.mark.django_db
def test_peasant_rumors_use_current_surplus_snapshot_fields():
    user = get_user_model().objects.create_user(username="u_rumors_current", password="pw")
    county = CountyService.create_initial_county()
    county["peasant_surplus"] = {
        "reserve": 4742217,
        "months_to_harvest": 7,
        "per_capita_surplus": 395.7,
        "consumer_confidence": 56.5,
        "confidence_index": 2.0,
        "monthly_consumption": 415450,
        "baseline_monthly_consumption": 207725,
        "consumption_multiplier": 2.0,
    }
    game = GameState.objects.create(user=user, current_season=15, county_data=county)
    village_names = [v["name"] for v in county.get("villages", [])]

    with patch("game.services.rumors.random.choice", side_effect=lambda seq: seq[0]):
        rumors = RumorsService._get_peasant_surplus_rumors(county, 15, village_names)

    texts = [item["text"] for item in rumors]
    assert "今年风调雨顺，村里的粮仓都快堆不下了，连老鼠都胖了一圈！" in texts
    assert "集市上热闹得很，大家手里有余粮就换了铜板，买这买那，商贩们笑开了花。" in texts
    assert not any("掺野菜" in text for text in texts)


@pytest.mark.django_db
def test_peasant_rumors_fall_back_to_legacy_surplus_fields():
    user = get_user_model().objects.create_user(username="u_rumors_legacy", password="pw")
    county = CountyService.create_initial_county()
    county["peasant_surplus"] = {
        "reserve": 800000,
        "months_to_harvest": 8,
        "per_capita_surplus": 80.0,
        "monthly_per_capita_surplus": 10.0,
        "demand_factor": 1.8,
        "monthly_consumption": 200000,
    }
    game = GameState.objects.create(user=user, current_season=1, county_data=county)
    village_names = [v["name"] for v in county.get("villages", [])]

    with patch("game.services.rumors.random.choice", side_effect=lambda seq: seq[0]):
        rumors = RumorsService._get_peasant_surplus_rumors(county, 1, village_names)

    texts = [item["text"] for item in rumors]
    assert "今年风调雨顺，村里的粮仓都快堆不下了，连老鼠都胖了一圈！" in texts
    assert "集市上热闹得很，大家手里有余粮就换了铜板，买这买那，商贩们笑开了花。" in texts
