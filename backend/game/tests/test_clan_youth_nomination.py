import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from game.models import Agent, GameState
from game.services.agent import AgentService
from game.services.county import CountyService


def _create_clan_youth(game, *, name, generated_season, exam_eligible):
    return Agent.objects.create(
        game=game,
        name=name,
        role="CLAN_YOUTH",
        role_title="宗族后生",
        tier="LIGHT",
        attributes={
            "age": 18,
            "village_name": "甲村",
            "bio": f"{name}，宗族后生。",
            "exam_eligible": exam_eligible,
            "player_affinity": 50,
            "generated_season": generated_season,
            "social_identity": {
                "clan_id": "测试府张氏",
                "surname": "张",
                "native_place": "测试府",
            },
        },
    )


@pytest.mark.django_db
def test_agent_list_normalizes_stale_clan_youth_nomination():
    user = get_user_model().objects.create_user(username="clan_youth_list_user", password="pw")
    game = GameState.objects.create(
        user=user,
        current_season=17,
        county_data=CountyService.create_initial_county("fiscal_core"),
    )
    stale = _create_clan_youth(
        game,
        name="张旧年",
        generated_season=5,
        exam_eligible=True,
    )

    agents = AgentService.get_agents_list(game)

    stale.refresh_from_db()
    stale_payload = next(item for item in agents if item["id"] == stale.id)
    assert stale.attributes["exam_eligible"] is False
    assert stale_payload["attributes"]["exam_eligible"] is False
    assert stale_payload["attributes"]["can_nominate"] is False


@pytest.mark.django_db
def test_second_year_nomination_ignores_previous_year_residue():
    user = get_user_model().objects.create_user(username="clan_youth_nominate_user", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    county = CountyService.create_initial_county("fiscal_core")
    county["clan_youth_pending"] = True
    game = GameState.objects.create(user=user, current_season=17, county_data=county)

    stale = _create_clan_youth(
        game,
        name="张旧年",
        generated_season=5,
        exam_eligible=True,
    )
    fresh = _create_clan_youth(
        game,
        name="张新年",
        generated_season=17,
        exam_eligible=False,
    )

    response = client.post(f"/api/games/{game.id}/clan-youth/{fresh.id}/nominate/")

    assert response.status_code == 200
    payload = response.json()
    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.attributes["exam_eligible"] is False
    assert fresh.attributes["exam_eligible"] is True
    assert payload["eligible_count"] == 1


@pytest.mark.django_db
def test_old_year_clan_youth_cannot_be_nominated_again():
    user = get_user_model().objects.create_user(username="clan_youth_old_user", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    game = GameState.objects.create(
        user=user,
        current_season=17,
        county_data=CountyService.create_initial_county("fiscal_core"),
    )
    stale = _create_clan_youth(
        game,
        name="张旧年",
        generated_season=5,
        exam_eligible=False,
    )

    response = client.post(f"/api/games/{game.id}/clan-youth/{stale.id}/nominate/")

    assert response.status_code == 400
    assert "仅可举荐本年度新推举的宗族后生" in response.json()["error"]
