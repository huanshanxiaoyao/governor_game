from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from game.models import Agent, GameState, NegotiationSession, Promise
from game.services.county import CountyService
from game.services.state import load_county_state


def _create_local_agent(game, *, name, role, role_title, village_name):
    return Agent.objects.create(
        game=game,
        name=name,
        role=role,
        role_title=role_title,
        tier="LIGHT",
        attributes={
            "player_affinity": 50,
            "village_name": village_name,
            "social_identity": {
                "clan_id": "测试府张氏",
                "surname": name[:1],
                "native_place": "测试府",
            },
        },
    )


@pytest.mark.django_db
def test_delegate_can_resolve_village_school_request_without_manual_message():
    user = get_user_model().objects.create_user(username="delegate_school_user", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    county = CountyService.create_initial_county("fiscal_core")
    village_name = county["villages"][0]["name"]
    game = GameState.objects.create(user=user, current_season=18, county_data=county)
    villager = _create_local_agent(
        game, name="张里正", role="VILLAGER", role_title="里长", village_name=village_name
    )
    _create_local_agent(
        game, name="沈师爷", role="ADVISOR", role_title="师爷", village_name=""
    )
    session = NegotiationSession.objects.create(
        game=game,
        agent=villager,
        event_type="VILLAGE_REQ_SCHOOL",
        status="active",
        current_round=0,
        max_rounds=6,
        season=game.current_season,
        context_data={"village_name": village_name, "schools_elsewhere": 2},
    )

    with patch(
        "game.services.negotiation.NegotiationService._negotiate_village_req_school",
        return_value={
            "dialogue": "谢大人恩典。",
            "final_decision": "accept",
            "attitude_change": 2,
            "new_memory": "",
        },
    ):
        response = client.post(
            f"/api/games/{game.id}/negotiations/{session.id}/chat/",
            {"speaker_role": "ADVISOR"},
            format="json",
        )

    assert response.status_code == 200
    payload = response.json()
    session.refresh_from_db()
    assert payload["status"] == "resolved"
    assert payload["handoff_to_player"] is False
    assert session.status == "resolved"
    promise = Promise.objects.get(game=game, promise_type="BUILD_SCHOOL")
    assert promise.context["target_village"] == village_name


@pytest.mark.django_db
def test_delegate_can_resolve_landlord_facility_request_without_handoff():
    user = get_user_model().objects.create_user(username="delegate_facility_user", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    county = CountyService.create_initial_county("fiscal_core")
    village_name = county["villages"][0]["name"]
    game = GameState.objects.create(user=user, current_season=22, county_data=county)
    gentry = _create_local_agent(
        game, name="周员外", role="GENTRY", role_title="地主", village_name=village_name
    )
    session = NegotiationSession.objects.create(
        game=game,
        agent=gentry,
        event_type="LANDLORD_DEMAND_FACILITY",
        status="active",
        current_round=0,
        max_rounds=6,
        season=game.current_season,
        context_data={
            "village_name": village_name,
            "low_facilities": "医馆",
            "low_facility_keys": ["medical_level"],
        },
    )

    with patch(
        "game.services.negotiation.NegotiationService._negotiate_landlord_demand_facility",
        return_value={
            "dialogue": "既如此，便请县衙尽快改善。",
            "final_decision": "accept",
            "attitude_change": 1,
            "new_memory": "",
        },
    ):
        response = client.post(
            f"/api/games/{game.id}/negotiations/{session.id}/chat/",
            {"speaker_role": "DEPUTY", "message": ""},
            format="json",
        )

    assert response.status_code == 200
    payload = response.json()
    session.refresh_from_db()
    gentry.refresh_from_db()
    county_after = load_county_state(game, refresh=True)
    assert payload["status"] == "resolved"
    assert payload["handoff_to_player"] is False
    assert session.status == "resolved"
    assert gentry.attributes["player_affinity"] == 55
    assert county_after["morale"] >= county["morale"]
