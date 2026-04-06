"""村级 NPC 主动行动 — API 端点

POST /api/games/{game_id}/npc-requests/{request_id}/respond/
    处理简单类请愿（V2 赈灾 / G1 地主出资建村塾）的玩家接受/拒绝

GET  /api/games/{game_id}/clan-youth/
    获取本局所有宗族后生列表

POST /api/games/{game_id}/clan-youth/{agent_id}/nominate/
    标记宗族后生为"可举荐府试候选"
"""
import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from .models import Agent, GameState
from .services.state import load_county_state, save_player_state
from .services.settlement_metrics import MetricsMixin

logger = logging.getLogger('game')


class NpcRequestRespondView(APIView):
    """
    POST /api/games/{game_id}/npc-requests/{request_id}/respond/
    Body: {"action": "accept" | "refuse"}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id, request_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        action = request.data.get('action')
        if action not in ('accept', 'refuse'):
            return Response({"error": "action 必须为 accept 或 refuse"}, status=400)

        county = load_county_state(game)
        pending = county.get('npc_pending_requests', [])

        # 找到目标请求
        target = None
        remaining = []
        for req in pending:
            if req.get('id') == request_id:
                target = req
            else:
                remaining.append(req)

        if not target:
            return Response({"error": "请求不存在或已处理"}, status=404)

        county['npc_pending_requests'] = remaining

        req_type = target.get('type')
        try:
            if req_type == 'VILLAGE_RELIEF':
                result = cls_handle_relief(action, target, game, county)
            elif req_type == 'GENTRY_FUND_SCHOOL':
                result = cls_handle_fund_school(action, target, game, county)
            else:
                result = {"message": f"未知请求类型 {req_type}"}
        except Exception as e:
            logger.error("NPC request respond failed: %s", e, exc_info=True)
            return Response({"error": str(e)}, status=500)

        save_player_state(game, county)
        return Response({"success": True, "action": action, "result": result})


def cls_handle_relief(action, req, game, county):
    """V2: 村民请愿·赈灾"""
    if action == 'accept':
        from .services.investment import InvestmentService
        success, msg = InvestmentService.execute(game, 'relief')
        if not success:
            # 若已赈灾或无灾害，仍算接受（容错）
            msg = f'赈灾处理：{msg}'
        return {"message": msg}
    else:
        # 拒绝：民心大降 -15
        MetricsMixin.apply_county_stat_delta(county, 'morale', -15)
        MetricsMixin._sync_county_from_villages(county, 'morale')
        return {"message": "拒绝赈灾，民心大幅下降"}


def cls_handle_fund_school(action, req, game, county):
    """G1: 地主出资·兴建村塾"""
    village_name = req.get('village_name', '')
    agent_id = req.get('agent_id')
    contribution = req.get('landlord_contribution', 15)

    if action == 'accept':
        # 地主出资，县衙补足剩余费用，发起 fund_village_school 投资
        from .services.investment import InvestmentService
        # 地主贡献减免费用
        county['landlord_school_subsidy'] = county.get('landlord_school_subsidy', 0) + contribution
        success, msg = InvestmentService.execute(
            game, 'fund_village_school', target_village=village_name,
        )
        # 无论是否即时成功（费用不足也会排队），尝试撤回补贴额度
        if not success:
            county['landlord_school_subsidy'] = max(
                0, county.get('landlord_school_subsidy', 0) - contribution
            )
        # 地主好感 +5
        if agent_id:
            try:
                agent = Agent.objects.get(id=agent_id, game=game)
                attrs = agent.attributes or {}
                attrs['player_affinity'] = min(99, int(attrs.get('player_affinity', 50)) + 5)
                agent.attributes = attrs
                agent.save(update_fields=['attributes'])
            except Agent.DoesNotExist:
                pass
        return {"message": msg or f'{village_name}村塾建设已启动，地主出资{contribution}两'}
    else:
        # 拒绝地主好意：无惩罚，机会错过
        return {"message": f"婉拒{village_name}地主出资，机会已过"}


class ClanYouthListView(APIView):
    """
    GET /api/games/{game_id}/clan-youth/
    返回所有宗族后生 NPC 列表
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=404)

        youths = Agent.objects.filter(game=game, role='CLAN_YOUTH').order_by('created_at')
        result = []
        for y in youths:
            attrs = y.attributes or {}
            si = attrs.get('social_identity', {})
            result.append({
                'id': y.id,
                'name': y.name,
                'age': attrs.get('age', '?'),
                'village_name': attrs.get('village_name', ''),
                'clan_id': si.get('clan_id', ''),
                'native_place': si.get('native_place', ''),
                'bio': attrs.get('bio', ''),
                'exam_eligible': attrs.get('exam_eligible', False),
                'generated_season': attrs.get('generated_season', 0),
            })
        return Response({'youths': result, 'count': len(result)})


class ClanYouthNominateView(APIView):
    """
    POST /api/games/{game_id}/clan-youth/{agent_id}/nominate/
    标记或取消举荐宗族后生为府试候选
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id, agent_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=404)

        try:
            agent = Agent.objects.get(id=agent_id, game=game, role='CLAN_YOUTH')
        except Agent.DoesNotExist:
            return Response({"error": "宗族后生不存在"}, status=404)

        import random as _random
        attrs = agent.attributes or {}
        current = attrs.get('exam_eligible', False)
        attrs['exam_eligible'] = not current
        agent.attributes = attrs
        agent.save(update_fields=['attributes'])

        affinity_msg = ''
        if attrs['exam_eligible']:
            # 被举荐后生好感 +10
            attrs['player_affinity'] = min(99, int(attrs.get('player_affinity', 50)) + 10)
            agent.attributes = attrs
            agent.save(update_fields=['attributes'])

            # 同游戏中其他宗族后生好感 -3（竞争落选）
            others = Agent.objects.filter(game=game, role='CLAN_YOUTH').exclude(id=agent_id)
            to_update = []
            for other in others:
                oa = other.attributes or {}
                oa['player_affinity'] = max(0, int(oa.get('player_affinity', 50)) - 3)
                other.attributes = oa
                to_update.append(other)
            if to_update:
                Agent.objects.bulk_update(to_update, ['attributes'])

            # 举荐人（地主）好感提升 + 写入记忆
            sponsor_id = attrs.get('sponsor_agent_id')
            if sponsor_id:
                try:
                    sponsor = Agent.objects.get(id=sponsor_id, game=game)
                    sp_attrs = sponsor.attributes or {}
                    gain = _random.randint(5, 10)
                    sp_attrs['player_affinity'] = min(99, int(sp_attrs.get('player_affinity', 50)) + gain)
                    # 写入记忆
                    memory = sp_attrs.get('memory', [])
                    memory.append(
                        f'第{game.current_season}月，族中后生{agent.name}获知县举荐参加府试，举族皆荣。'
                    )
                    if len(memory) > 20:
                        memory = memory[-20:]
                    sp_attrs['memory'] = memory
                    sponsor.attributes = sp_attrs
                    sponsor.save(update_fields=['attributes'])
                    affinity_msg = f'，{sponsor.name}好感+{gain}'
                except Agent.DoesNotExist:
                    pass

            # 清除待操作标记
            county = load_county_state(game)
            county['clan_youth_pending'] = False
            save_player_state(game, county)

            msg = f'已举荐{agent.name}为府试候选{affinity_msg}'
        else:
            msg = f'已取消{agent.name}的举荐'

        return Response({
            'success': True,
            'agent_id': agent_id,
            'exam_eligible': attrs['exam_eligible'],
            'message': msg,
        })
