import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from game.models import GameState
from game.services.county import CountyService


@pytest.mark.django_db
def test_counsel_proactive_returns_enriched_suggested_actions():
    user = get_user_model().objects.create_user(username="counsel_user", password="pw")
    county = CountyService.create_initial_county("fiscal_core")
    county["morale"] = 60
    county["security"] = 60
    county["commercial"] = 60
    county["education"] = 20
    county["treasury"] = 99999
    game = GameState.objects.create(
        user=user,
        current_season=1,
        county_data=county,
    )

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(f"/api/games/{game.id}/counsel/proactive/")

    assert response.status_code == 200
    trigger = response.json()["trigger"]
    assert trigger is not None
    assert trigger["speaker_name"]

    cards = trigger["suggested_actions"]
    assert cards

    for card in cards:
        assert "name" in card
        assert "cost" in card
        assert "requires_village" in card
        assert "disabled_reason" in card

    assert any(
        card.get("cost") is not None and not card.get("disabled_reason")
        for card in cards
    )
