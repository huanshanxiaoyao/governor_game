import uuid

import pytest
from django.contrib.auth import get_user_model

from game.models import Agent, GameState
from game.services import AgentMemoryService
from game.services.county import CountyService
from game.services.agent import AgentService


def _make_game():
    """创建有 agents 的 GameState，供记忆测试使用。"""
    user = get_user_model().objects.create_user(
        username=f"mem_test_{uuid.uuid4().hex[:8]}",
        password="pw",
    )
    county_data = CountyService.create_initial_county(county_type="coastal")
    game = GameState.objects.create(user=user, current_season=5, county_data=county_data)
    AgentService.initialize_agents(game)
    return game


@pytest.mark.django_db
def test_fetch_orders_by_importance_recency():
    game = _make_game()
    agent = Agent.objects.filter(game=game).first()
    AgentMemoryService.record(agent, text='old low', topic='OTHER',
        importance=2, source='test', season=1)
    AgentMemoryService.record(agent, text='recent high', topic='POLICY',
        importance=9, source='test', season=game.current_season)
    AgentMemoryService.record(agent, text='last season mid', topic='POLICY',
        importance=5, source='test', season=game.current_season - 1)
    out = AgentMemoryService.fetch_relevant(
        agent, current_season=game.current_season, query_text='', limit=3)
    assert out[0].text == 'recent high'
    assert len(out) == 3


@pytest.mark.django_db
def test_fetch_keeps_high_importance():
    game = _make_game()
    agent = Agent.objects.filter(game=game).first()
    AgentMemoryService.record(agent, text='critical', topic='PROMISE',
        importance=10, source='test', season=1)
    for i in range(20):
        AgentMemoryService.record(agent, text=f'noise{i}', topic='CHAT',
            importance=3, source='test', season=game.current_season)
    out = AgentMemoryService.fetch_relevant(
        agent, current_season=game.current_season, limit=8)
    assert any(m.text == 'critical' for m in out)


@pytest.mark.django_db
def test_fetch_keyword_boost_by_village():
    game = _make_game()
    agent = Agent.objects.filter(game=game).first()
    villages = game.county_data.get('villages', [])
    target = villages[0]['name'] if villages else '赵村'
    AgentMemoryService.record(agent, text='平常事',
        topic='OTHER', importance=5, season=game.current_season,
        source='test')
    AgentMemoryService.record(agent, text=f'修水利于{target}',
        topic='POLICY', importance=5, season=game.current_season,
        source='test', related_entities={'village': target})
    out = AgentMemoryService.fetch_relevant(
        agent, current_season=game.current_season,
        query_text=f'{target}怎么样了？', limit=2)
    assert out[0].related_entities.get('village') == target


@pytest.mark.django_db
def test_fetch_empty_returns_empty():
    game = _make_game()
    agent = Agent.objects.filter(game=game).first()
    out = AgentMemoryService.fetch_relevant(
        agent, current_season=game.current_season)
    assert out == []
