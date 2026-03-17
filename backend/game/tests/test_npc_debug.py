import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from game.models import AdminUnit, Agent, DialogueMessage, GameState, NeighborCounty
from game.services.county import CountyService


def _prefecture_data(name="测试府"):
    return {
        "prefecture_name": name,
        "treasury": 800,
        "pending_events": [],
    }


@pytest.mark.django_db
def test_npc_debug_list_and_detail_cover_agent_neighbor_and_subordinate():
    user = get_user_model().objects.create_user(
        username="npc_debug_staff", password="pw", is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)

    game = GameState.objects.create(
        user=user,
        current_season=3,
        player_role="PREFECT",
        county_data={},
    )
    prefecture_unit = AdminUnit.objects.create(
        game=game,
        unit_type="PREFECTURE",
        is_player_controlled=True,
        unit_data=_prefecture_data(),
    )
    game.player_unit = prefecture_unit
    game.save(update_fields=["player_unit"])

    agent = Agent.objects.create(
        game=game,
        name="赵廷章",
        role="PREFECT",
        role_title="知府",
        tier="FULL",
        attributes={
            "bio": "老成持重的知府。",
            "personality": {"openness": 0.4, "conscientiousness": 0.8, "agreeableness": 0.3},
            "ideology": {"reform_vs_tradition": 0.3, "people_vs_authority": 0.2, "pragmatic_vs_idealist": 0.7},
            "goals": ["保境安民", "催科完赋"],
            "memory": ["记得该局初建时的财赋压力。"],
            "player_affinity": 42,
        },
    )
    DialogueMessage.objects.create(
        game=game,
        agent=agent,
        role="agent",
        content="府中催科，不可懈怠。",
        season=3,
        metadata={"reasoning": "此县需稳住上缴。"},
    )

    neighbor_county = CountyService.create_initial_county(county_type="coastal")
    neighbor_county["governor_profile"] = {
        "name": "沈知县",
        "style": "minben",
        "archetype": "VIRTUOUS",
        "bio": "勤于抚民。",
        "intelligence": 7,
        "stamina": 6,
        "personality": {"sociability": 0.6, "rationality": 0.5, "assertiveness": 0.4},
        "ideology": {"state_vs_people": 0.2, "central_vs_local": 0.4, "pragmatic_vs_ideal": 0.3},
        "goals": {"welfare": 0.4, "reputation": 0.2, "power": 0.1, "wealth": 0.1, "legacy": 0.2},
        "memory": ["曾在灾后减征商税。"],
    }
    neighbor = NeighborCounty.objects.create(
        game=game,
        county_name="海宁县",
        governor_name="沈知县",
        governor_style="minben",
        governor_archetype="VIRTUOUS",
        governor_bio="勤于抚民。",
        county_data=neighbor_county,
        last_reasoning="本月当先安民。",
    )

    subordinate_county = CountyService.create_initial_county(county_type="fiscal_core")
    subordinate_county["county_name"] = "华亭县"
    subordinate_county["governor_profile"] = {
        "name": "顾知县",
        "style": "zhengji",
        "archetype": "MIDDLING",
        "bio": "专务政绩。",
        "intelligence": 8,
        "stamina": 7,
        "personality": {"sociability": 0.5, "rationality": 0.7, "assertiveness": 0.8},
        "ideology": {"state_vs_people": 0.6, "central_vs_local": 0.7, "pragmatic_vs_ideal": 0.8},
        "goals": {"welfare": 0.1, "reputation": 0.3, "power": 0.3, "wealth": 0.1, "legacy": 0.2},
        "memory": ["牢记府里催科甚严。"],
    }
    subordinate_county["_last_reasoning"] = "先顾财赋，再谋其余。"
    subordinate = AdminUnit.objects.create(
        game=game,
        unit_type="COUNTY",
        parent=prefecture_unit,
        is_player_controlled=False,
        unit_data=subordinate_county,
    )

    response = client.get(f"/api/games/{game.id}/npc-debug/")
    assert response.status_code == 200
    items = response.json()["items"]
    keys = {item["npc_key"] for item in items}
    assert f"agent:{agent.id}" in keys
    assert f"neighbor:{neighbor.id}" in keys
    assert f"subordinate:{subordinate.id}" in keys

    agent_detail = client.get(
        f"/api/games/{game.id}/npc-debug/detail/?npc_key=agent:{agent.id}",
    )
    assert agent_detail.status_code == 200
    agent_payload = agent_detail.json()
    assert agent_payload["identity"]["name"] == "赵廷章"
    assert "赵廷章" in agent_payload["prompt_preview"]["system_prompt"]
    assert agent_payload["runtime"]["dialogues"][-1]["metadata"]["reasoning"] == "此县需稳住上缴。"

    neighbor_detail = client.get(
        f"/api/games/{game.id}/npc-debug/detail/?npc_key=neighbor:{neighbor.id}",
    )
    assert neighbor_detail.status_code == 200
    neighbor_payload = neighbor_detail.json()
    assert neighbor_payload["identity"]["county_name"] == "海宁县"
    assert neighbor_payload["runtime"]["last_reasoning"] == "本月当先安民。"
    assert neighbor_payload["profile_normalized"]["core"]["style"] == "minben"

    subordinate_detail = client.get(
        f"/api/games/{game.id}/npc-debug/detail/?npc_key=subordinate:{subordinate.id}",
    )
    assert subordinate_detail.status_code == 200
    subordinate_payload = subordinate_detail.json()
    assert subordinate_payload["identity"]["county_name"] == "华亭县"
    assert subordinate_payload["runtime"]["last_reasoning"] == "先顾财赋，再谋其余。"
    assert subordinate_payload["profile_normalized"]["core"]["archetype"] == "MIDDLING"


@pytest.mark.django_db
def test_npc_debug_requires_staff():
    user = get_user_model().objects.create_user(username="npc_debug_user", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)
    game = GameState.objects.create(user=user, current_season=1)

    response = client.get(f"/api/games/{game.id}/npc-debug/")
    assert response.status_code == 403
