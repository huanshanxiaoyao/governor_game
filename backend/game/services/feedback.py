"""玩家反馈服务：落库并推送到飞书群。"""

import json
import urllib.request

from django.conf import settings
from django.utils import timezone

from game.models import PlayerFeedback


class FeedbackService:
    """处理玩家反馈的持久化与飞书通知。"""

    @classmethod
    def submit_feedback(cls, game, user, content):
        feedback = PlayerFeedback.objects.create(
            game=game,
            user=user,
            content=content,
        )
        delivered, error_message = cls._push_to_feishu(feedback)
        feedback.sent_to_feishu = delivered
        feedback.feishu_error = "" if delivered else (error_message or "")[:300]
        feedback.save(update_fields=["sent_to_feishu", "feishu_error"])
        return feedback, delivered, error_message

    @classmethod
    def _push_to_feishu(cls, feedback):
        webhook_url = getattr(settings, "FEISHU_FEEDBACK_WEBHOOK", "")
        if not webhook_url:
            return False, "未配置 FEISHU_FEEDBACK_WEBHOOK"

        try:
            payload = json.dumps({
                "msg_type": "text",
                "content": {
                    "text": cls._build_message(feedback),
                },
            }).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
            return True, ""
        except Exception as exc:
            return False, str(exc) or exc.__class__.__name__

    @classmethod
    def _build_message(cls, feedback):
        game = feedback.game
        user = feedback.user
        created_at = timezone.localtime(feedback.created_at)
        unit_data = game.get_unit_data() or {}
        unit_name = (
            unit_data.get("county_name")
            or unit_data.get("prefecture_name")
            or unit_data.get("province_name")
            or "未命名辖区"
        )
        return "\n".join([
            "【县令模拟器玩家反馈】",
            f"用户: {user.username} (ID: {user.id})",
            f"存档: #{game.id}",
            f"角色: {game.get_player_role_display()}",
            f"辖区: {unit_name}",
            f"月份: 第{game.current_season}月",
            f"时间: {created_at.strftime('%Y-%m-%d %H:%M:%S %Z')}",
            "反馈内容:",
            feedback.content,
        ])
