from game.services import AgentService


def test_normalize_new_memory_string_wrapped():
    data = {'new_memory': '一句话', 'attitude_change': 0, 'requests': []}
    out = AgentService._normalize_response(data)
    assert out['new_memory']['text'] == '一句话'
    assert out['new_memory']['topic'] == 'CHAT'
    assert out['new_memory']['importance'] == 5


def test_normalize_new_memory_dict_clamps_importance():
    data = {'new_memory': {'text': 'x', 'topic': 'POLICY', 'importance': 99}}
    out = AgentService._normalize_response(data)
    assert out['new_memory']['importance'] == 10


def test_normalize_new_memory_invalid_topic_fallback():
    data = {'new_memory': {'text': 'x', 'topic': 'WEIRD', 'importance': 5}}
    out = AgentService._normalize_response(data)
    assert out['new_memory']['topic'] == 'OTHER'


def test_normalize_new_memory_none_when_missing():
    data = {'attitude_change': 0}
    out = AgentService._normalize_response(data)
    assert out.get('new_memory') is None


def test_normalize_new_memory_empty_text_preserved():
    data = {'new_memory': {'text': '', 'topic': 'CHAT', 'importance': 5}}
    out = AgentService._normalize_response(data)
    assert out['new_memory']['text'] == ''
