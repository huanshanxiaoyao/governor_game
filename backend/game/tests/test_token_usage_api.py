import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def auth_client(db):
    user = User.objects.create_user(username='testplayer', password='pw')
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.mark.django_db
def test_token_usage_empty(auth_client):
    """无日志时返回空 by_season 列表，total 为 0。"""
    from game.models import GameState
    client, user = auth_client
    game = GameState.objects.create(user=user, current_season=1)

    resp = client.get(f'/api/games/{game.id}/token-usage/')
    assert resp.status_code == 200
    data = resp.json()
    assert data['total_tokens'] == 0
    assert data['by_season'] == []


@pytest.mark.django_db
def test_token_usage_aggregated(auth_client):
    """有日志时按 season 聚合，并拆分 by_source。"""
    from game.models import GameState
    from llm.models import LLMCallLog

    client, user = auth_client
    game = GameState.objects.create(user=user, current_season=3)

    LLMCallLog.objects.create(
        game_id=game.id, season=1, call_source='agent_chat',
        provider='qwen', model='qwen-plus',
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        success=True,
    )
    LLMCallLog.objects.create(
        game_id=game.id, season=1, call_source='counsel',
        provider='qwen', model='qwen-plus',
        prompt_tokens=200, completion_tokens=80, total_tokens=280,
        success=True,
    )
    LLMCallLog.objects.create(
        game_id=game.id, season=2, call_source='agent_chat',
        provider='qwen', model='qwen-plus',
        prompt_tokens=90, completion_tokens=40, total_tokens=130,
        success=True,
    )
    # 失败的也计入（tokens 为 0，不影响总数）
    LLMCallLog.objects.create(
        game_id=game.id, season=2, call_source='counsel',
        provider='qwen', model='qwen-plus',
        prompt_tokens=0, completion_tokens=0, total_tokens=0,
        success=False,
    )

    resp = client.get(f'/api/games/{game.id}/token-usage/')
    assert resp.status_code == 200
    data = resp.json()

    assert data['total_tokens'] == 560
    assert len(data['by_season']) == 2

    s1 = next(s for s in data['by_season'] if s['season'] == 1)
    assert s1['total_tokens'] == 430
    assert s1['by_source']['agent_chat'] == 150
    assert s1['by_source']['counsel'] == 280

    s2 = next(s for s in data['by_season'] if s['season'] == 2)
    assert s2['total_tokens'] == 130   # 失败行 total_tokens=0 不影响


@pytest.mark.django_db
def test_token_usage_other_game_not_visible(auth_client):
    """不能查看他人游戏的 token 用量。"""
    from game.models import GameState
    client, user = auth_client
    other_user = User.objects.create_user(username='other', password='pw')
    other_game = GameState.objects.create(user=other_user, current_season=1)

    resp = client.get(f'/api/games/{other_game.id}/token-usage/')
    assert resp.status_code == 404
