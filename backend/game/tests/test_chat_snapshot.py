import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from game.models import Agent, GameState, Promise
from game.services.county import CountyService
from game.services.agent import AgentService


def _make_user_and_game():
    user = get_user_model().objects.create_user(
        username=f"snap_test_{uuid.uuid4().hex[:8]}", password="pw",
    )
    county_data = CountyService.create_initial_county(county_type="coastal")
    game = GameState.objects.create(
        user=user, current_season=5, county_data=county_data,
    )
    AgentService.initialize_agents(game)
    return user, game


@pytest.mark.django_db
def test_chat_snapshot_returns_fields():
    user, game = _make_user_and_game()
    agent = Agent.objects.filter(game=game).first()
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.get(
        f'/api/games/{game.id}/agents/{agent.id}/chat-snapshot/'
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in ('agent_id', 'agent_name', 'topics_of_concern',
                'recent_focus', 'has_unresolved_promise',
                'highest_importance_memory_hint'):
        assert key in data, f'missing key: {key}'


@pytest.mark.django_db
def test_chat_snapshot_unknown_agent_404():
    user, game = _make_user_and_game()
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.get(f'/api/games/{game.id}/agents/999999/chat-snapshot/')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_chat_snapshot_promise_flag():
    user, game = _make_user_and_game()
    agent = Agent.objects.filter(game=game, role='ADVISOR').first()
    Promise.objects.create(
        game=game, agent=agent, promise_type='OTHER',
        direction='PLAYER_TO_NPC', description='涨月银',
        status='PENDING', season_made=1, deadline_season=4,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.get(
        f'/api/games/{game.id}/agents/{agent.id}/chat-snapshot/'
    )
    assert resp.json()['has_unresolved_promise'] is True
