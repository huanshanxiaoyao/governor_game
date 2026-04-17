import pytest
from django.utils import timezone


@pytest.mark.django_db
def test_llm_call_log_create_and_query():
    """LLMCallLog 可以创建并按 game_id+season 查询。"""
    from llm.models import LLMCallLog

    LLMCallLog.objects.create(
        user_id=1,
        game_id=99,
        season=3,
        call_source='agent_chat',
        provider='qwen',
        model='qwen-plus',
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        latency_ms=800,
        success=True,
    )
    LLMCallLog.objects.create(
        user_id=1,
        game_id=99,
        season=3,
        call_source='counsel',
        provider='qwen',
        model='qwen-plus',
        prompt_tokens=200,
        completion_tokens=80,
        total_tokens=280,
        latency_ms=1200,
        success=True,
    )
    LLMCallLog.objects.create(
        user_id=1,
        game_id=99,
        season=4,
        call_source='agent_chat',
        provider='qwen',
        model='qwen-plus',
        prompt_tokens=90,
        completion_tokens=40,
        total_tokens=130,
        latency_ms=700,
        success=True,
    )

    season3_rows = LLMCallLog.objects.filter(game_id=99, season=3)
    assert season3_rows.count() == 2

    from django.db.models import Sum
    total = LLMCallLog.objects.filter(game_id=99).aggregate(t=Sum('total_tokens'))['t']
    assert total == 560


from unittest.mock import patch, MagicMock


def _make_mock_response(content, prompt_tokens=10, completion_tokens=20):
    """构造 OpenAI SDK 风格的 mock response。"""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


@pytest.mark.django_db
def test_llm_client_logs_when_context_provided():
    """有 LLMContext 时，成功调用后写一条 LLMCallLog。"""
    from llm.client import LLMClient
    from llm.context import LLMContext
    from llm import call_sources
    from llm.models import LLMCallLog

    ctx = LLMContext(
        call_source=call_sources.COUNSEL,
        game_id=42,
        season=5,
        user_id=1,
    )
    mock_resp = _make_mock_response('回复内容', prompt_tokens=100, completion_tokens=50)

    with patch('llm.client.OpenAI') as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_resp

        client = LLMClient(context=ctx)
        result = client.chat([{'role': 'user', 'content': '你好'}], max_tokens=50)

    assert result == '回复内容'
    assert LLMCallLog.objects.filter(game_id=42, season=5).count() == 1
    log = LLMCallLog.objects.get(game_id=42, season=5)
    assert log.call_source == call_sources.COUNSEL
    assert log.prompt_tokens == 100
    assert log.completion_tokens == 50
    assert log.total_tokens == 150
    assert log.success is True


@pytest.mark.django_db
def test_llm_client_no_log_without_context():
    """无 LLMContext 时，不写 LLMCallLog。"""
    from llm.client import LLMClient
    from llm.models import LLMCallLog

    mock_resp = _make_mock_response('回复')

    with patch('llm.client.OpenAI') as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_resp

        client = LLMClient()   # 无 context
        client.chat([{'role': 'user', 'content': '你好'}], max_tokens=50)

    assert LLMCallLog.objects.count() == 0
