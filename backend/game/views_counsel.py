"""幕僚群聊 & 自创施政选项视图"""

import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from .models import GameState, ProposedPolicy
from .services.state import load_county_state, save_player_state

logger = logging.getLogger('game')


def _get_game(request, game_id):
    try:
        return GameState.objects.get(id=game_id, user=request.user), None
    except GameState.DoesNotExist:
        return None, Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)


def _enrich_suggested_actions(game, county, suggested_actions):
    """补全建议施政卡片字段，确保主动建议与普通对话返回结构一致。"""
    from .services.investment import InvestmentService

    available_map = {
        a['action']: a
        for a in InvestmentService.get_available_actions(
            county,
            season=game.current_season,
            game=game,
        )
    }

    enriched = []
    for sa in suggested_actions or []:
        action_key = sa.get('action')
        info = available_map.get(action_key, {})
        enriched.append({
            **sa,
            'name': info.get('name', action_key),
            'cost': info.get('cost'),
            'requires_village': info.get('requires_village', False),
            'disabled_reason': info.get('disabled_reason'),
            'is_custom': info.get('is_custom', False),
        })
    return enriched


class CounselMessageView(APIView):
    """
    POST /api/games/<game_id>/counsel/message/

    幕僚群聊发送消息，返回幕僚回复及施政建议卡片。

    Request body:
        {
            "message": "知县的发言",
            "history": [{"role": "user"|"assistant", "content": "..."}, ...]
        }

    Response:
        {
            "speaker": "shiye"|"xiancheng",
            "speaker_name": "吴先生",
            "reply": "...",
            "suggested_actions": [...],
            "proposed_policies": [...]
        }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err

        message = (request.data.get('message') or '').strip()
        if not message:
            return Response({"error": "消息不能为空"}, status=status.HTTP_400_BAD_REQUEST)

        history = request.data.get('history', [])
        if not isinstance(history, list):
            history = []

        county = load_county_state(game)

        from .services.counsel import CounselService
        result = CounselService.chat(game, county, history, message)

        # 补充发言者姓名（供前端显示头像/名称）
        personas = CounselService.get_npc_personas(game)
        speaker_key = result.get('speaker', 'shiye')
        result['speaker_name'] = personas.get(speaker_key, {}).get('name', speaker_key)

        # 给 suggested_actions 补充实际成本（前端渲染卡片用）
        result['suggested_actions'] = _enrich_suggested_actions(
            game,
            county,
            result.get('suggested_actions', []),
        )

        return Response(result)


class CounselProposeView(APIView):
    """
    POST /api/games/<game_id>/counsel/propose/

    提交非常规施政申请（创建 ProposedPolicy PENDING）。

    Request body:
        {"policy_name": "新建集市", "rationale": "商业低迷，增加集市可改善"}

    Response:
        {"id": 1, "policy_name": "新建集市", "status": "PENDING", "message": "已提交"}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err

        if game.current_season > 36:
            return Response({"error": "游戏已结束"}, status=status.HTTP_400_BAD_REQUEST)

        policy_name = (request.data.get('policy_name') or '').strip()
        rationale   = (request.data.get('rationale') or '').strip()
        if not policy_name:
            return Response({"error": "施政构想名称不能为空"}, status=status.HTTP_400_BAD_REQUEST)

        # 已批准的不允许重复提交
        if ProposedPolicy.objects.filter(
            game=game,
            policy_name=policy_name,
            status=ProposedPolicy.Status.APPROVED,
        ).exists():
            return Response({"error": f"「{policy_name}」已批准，可直接执行"}, status=status.HTTP_400_BAD_REQUEST)

        # 同回合已 PENDING 的不允许重复提交
        if ProposedPolicy.objects.filter(
            game=game,
            policy_name=policy_name,
            status=ProposedPolicy.Status.PENDING,
        ).exists():
            return Response({"error": f"「{policy_name}」已提交审核，等候下月批复"}, status=status.HTTP_400_BAD_REQUEST)

        pp = ProposedPolicy.objects.create(
            game=game,
            proposer='知县',
            raw_proposal=rationale or policy_name,
            policy_name=policy_name,
            status=ProposedPolicy.Status.PENDING,
        )

        return Response({
            "id":          pp.id,
            "policy_name": pp.policy_name,
            "status":      pp.status,
            "message":     f"已向省布政司递交申请「{policy_name}」，请等候下月批复。",
        }, status=status.HTTP_201_CREATED)


class CounselPoliciesView(APIView):
    """
    GET /api/games/<game_id>/counsel/policies/

    获取本对局自创选项列表（不含 PENDING，含邻县同步来的）。

    Response:
        {"policies": [{id, policy_name, status, cost, delay_months,
                       effects_data, rationale, is_executed, action_key, synced_from_id}]}
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err

        policies = ProposedPolicy.objects.filter(
            game=game,
        ).exclude(
            status=ProposedPolicy.Status.PENDING,
        ).order_by('-created_at')

        return Response({
            "policies": [
                {
                    "id":             p.id,
                    "policy_name":    p.policy_name,
                    "status":         p.status,
                    "action_key":     p.action_key,
                    "cost":           p.cost,
                    "delay_months":   p.delay_months,
                    "effects_data":   p.effects_data,
                    "rationale":      p.rationale,
                    "rejection_reason": p.rejection_reason,
                    "is_executed":    p.is_executed,
                    "synced_from_id": p.synced_from_id,
                    "created_at":     p.created_at.isoformat(),
                }
                for p in policies
            ],
        })


class CounselPendingNotificationsView(APIView):
    """
    GET /api/games/<game_id>/counsel/pending-notifications/

    读取并清除结算后存入 county_data 的批复通知。
    前端打开幕僚面板时调用。

    Response:
        {"notifications": [{policy_name, approved, rationale, cost, effects_data, action_key}]}
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err

        county = load_county_state(game)
        notifications = county.pop('pending_policy_notifications', [])
        if notifications:
            save_player_state(game, county)

        return Response({"notifications": notifications})


class CounselProactiveView(APIView):
    """
    GET /api/games/<game_id>/counsel/proactive/

    检查主动提醒条件（任意指标 < 40）。
    前端打开幕僚面板时调用，决定是否插入主动提醒卡片。

    Response:
        {"trigger": null} 或
        {"trigger": {speaker, message, suggested_actions, stat, stat_value}}
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err

        county = load_county_state(game)
        from .services.counsel import CounselService
        trigger = CounselService.check_proactive_trigger(
            county,
            game=game,
            season=game.current_season,
        )

        if trigger:
            # 补充发言者姓名
            personas = CounselService.get_npc_personas(game)
            trigger['speaker_name'] = personas.get(
                trigger['speaker'], {}
            ).get('name', trigger['speaker'])
            trigger['suggested_actions'] = _enrich_suggested_actions(
                game,
                county,
                trigger.get('suggested_actions', []),
            )

        return Response({"trigger": trigger})
