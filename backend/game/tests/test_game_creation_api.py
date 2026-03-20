import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from game.models import GameState


@pytest.mark.django_db
def test_create_county_game_returns_json_payload_and_persists_player_unit():
    user = get_user_model().objects.create_user(username="create_api_user", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/games/",
        {
            "county_type": "fiscal_core",
        },
        format="json",
    )

    assert response.status_code == 201
    assert response["Content-Type"].startswith("application/json")

    payload = response.json()
    assert payload["id"] > 0

    game = GameState.objects.select_related("player_unit").get(id=payload["id"])
    assert game.player_unit is not None
    assert game.player_unit.unit_type == "COUNTY"


@pytest.mark.django_db
def test_game_knowledge_endpoint_returns_markdown_payload():
    user = get_user_model().objects.create_user(username="knowledge_api_user", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/game-knowledge/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "治县要略"
    assert "财政收支" in payload["markdown"]
