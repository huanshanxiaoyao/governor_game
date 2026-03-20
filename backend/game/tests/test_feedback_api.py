import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from game.models import GameState, PlayerFeedback


@pytest.mark.django_db
def test_submit_feedback_persists_and_posts_to_feishu(settings):
    settings.FEISHU_FEEDBACK_WEBHOOK = "https://example.com/feishu"
    user = get_user_model().objects.create_user(username="feedback_user", password="pw")
    game = GameState.objects.create(
        user=user,
        current_season=8,
        county_data={"county_name": "临江县"},
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with patch("game.services.feedback.urllib.request.urlopen") as mock_urlopen:
        response = client.post(
            f"/api/games/{game.id}/feedback/",
            {"content": "八月推进后余粮显示和事件提示不一致。"},
            format="json",
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["delivered"] is True

    feedback = PlayerFeedback.objects.get(game=game, user=user)
    assert feedback.content == "八月推进后余粮显示和事件提示不一致。"
    assert feedback.sent_to_feishu is True
    assert feedback.feishu_error == ""

    request_obj = mock_urlopen.call_args.args[0]
    message_payload = json.loads(request_obj.data.decode("utf-8"))
    text = message_payload["content"]["text"]
    assert "用户: feedback_user" in text
    assert f"存档: #{game.id}" in text
    assert "反馈内容:" in text
    assert "八月推进后余粮显示和事件提示不一致。" in text


@pytest.mark.django_db
def test_submit_feedback_keeps_record_when_feishu_fails(settings):
    settings.FEISHU_FEEDBACK_WEBHOOK = "https://example.com/feishu"
    user = get_user_model().objects.create_user(username="feedback_fail_user", password="pw")
    game = GameState.objects.create(
        user=user,
        current_season=3,
        county_data={"county_name": "安宁县"},
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with patch("game.services.feedback.urllib.request.urlopen", side_effect=RuntimeError("boom")):
        response = client.post(
            f"/api/games/{game.id}/feedback/",
            {"content": "弹窗按钮在移动端挤压布局。"},
            format="json",
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["delivered"] is False
    assert "飞书通知发送失败" in payload["message"]
    assert payload["warning"] == "boom"

    feedback = PlayerFeedback.objects.get(game=game, user=user)
    assert feedback.sent_to_feishu is False
    assert feedback.feishu_error == "boom"


@pytest.mark.django_db
def test_submit_feedback_rejects_other_users_game():
    user = get_user_model().objects.create_user(username="feedback_owner", password="pw")
    other_user = get_user_model().objects.create_user(username="feedback_other", password="pw")
    game = GameState.objects.create(
        user=user,
        current_season=1,
        county_data={"county_name": "平江县"},
    )
    client = APIClient()
    client.force_authenticate(user=other_user)

    response = client.post(
        f"/api/games/{game.id}/feedback/",
        {"content": "test"},
        format="json",
    )

    assert response.status_code == 404
    assert PlayerFeedback.objects.count() == 0
