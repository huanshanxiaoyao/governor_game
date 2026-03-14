import random
import uuid

import pytest
from django.contrib.auth import get_user_model

from game.models import GameState, Relationship
from game.services.agent import AgentService
from game.services.county import CountyService
from game.services.officialdom import OfficialdomService


def _create_game(county_type):
    user = get_user_model().objects.create_user(
        username=f"agent_init_{uuid.uuid4().hex[:8]}",
        password="pw",
    )
    county = CountyService.create_initial_county(county_type=county_type)
    return GameState.objects.create(user=user, current_season=1, county_data=county)


def _official_ties(game):
    ties = []
    for rel in Relationship.objects.filter(agent_a__game=game).select_related('agent_a', 'agent_b'):
        if rel.agent_a.role == "GENTRY" and rel.agent_a.role_title == "地主":
            if (rel.data or {}).get("generated") == "official_tie":
                ties.append(rel)
    return ties


@pytest.mark.django_db
def test_initialize_agents_matches_actual_village_count_and_names():
    random.seed(7)
    game = _create_game("coastal")

    AgentService.initialize_agents(game)

    villages = {v["name"]: v for v in game.county_data["villages"]}
    gentries = list(game.agents.filter(role="GENTRY", role_title="地主").order_by("id"))
    villagers = list(game.agents.filter(role="VILLAGER", role_title="村民代表").order_by("id"))

    assert len(gentries) == len(villages) == 4
    assert len(villagers) == len(villages) == 4

    for agent in gentries + villagers:
        attrs = agent.attributes or {}
        village_name = attrs.get("village_name")
        assert village_name in villages
        assert agent.name[0] == village_name[0]
        assert attrs.get("gender") == "male"


@pytest.mark.django_db
def test_fiscal_core_creates_at_least_two_strong_official_ties():
    random.seed(11)
    game = _create_game("fiscal_core")

    AgentService.initialize_agents(game)
    OfficialdomService.initialize_officialdom(game)

    ties = _official_ties(game)
    assert len(ties) >= 2

    valid_roles = {
        "PROVINCIAL_GOVERNOR",
        "PROVINCIAL_COMMISSIONER",
        "CABINET_CHIEF",
        "CABINET_MEMBER",
        "MINISTER",
        "VICE_MINISTER",
        "CHIEF_CENSOR",
        "VICE_CENSOR",
        "CENSOR",
    }
    assert all(rel.agent_b.role in valid_roles for rel in ties)


@pytest.mark.django_db
def test_clan_governance_builds_same_surname_kinship_ties():
    random.seed(3)
    game = _create_game("clan_governance")

    AgentService.initialize_agents(game)
    OfficialdomService.initialize_officialdom(game)

    ties = [rel for rel in _official_ties(game) if (rel.data or {}).get("type") == "kinship"]
    assert ties
    for rel in ties:
        assert rel.agent_a.name[0] == rel.agent_b.name[0]
        assert rel.agent_b.role in {
            "PREFECT",
            "PROVINCIAL_GOVERNOR",
            "PROVINCIAL_COMMISSIONER",
            "CABINET_CHIEF",
            "CABINET_MEMBER",
            "MINISTER",
            "VICE_MINISTER",
            "CHIEF_CENSOR",
            "VICE_CENSOR",
            "CENSOR",
        }


@pytest.mark.django_db
def test_other_county_types_create_single_strong_tie():
    random.seed(17)
    game = _create_game("coastal")

    AgentService.initialize_agents(game)
    OfficialdomService.initialize_officialdom(game)

    ties = _official_ties(game)
    assert len(ties) == 1
    assert ties[0].agent_b.role in {
        "PROVINCIAL_GOVERNOR",
        "PROVINCIAL_COMMISSIONER",
        "CABINET_CHIEF",
        "CABINET_MEMBER",
        "MINISTER",
        "VICE_MINISTER",
        "CHIEF_CENSOR",
        "VICE_CENSOR",
        "CENSOR",
    }
