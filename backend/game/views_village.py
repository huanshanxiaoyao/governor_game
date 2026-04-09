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

from .models import Agent, GameState, Promise
from .services.constants import year_of
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
            elif req_type == 'GENTRY_TRADE_ROUTE':
                result = cls_handle_trade_route(action, target, game, county)
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
        # 拒绝：民心惩罚按财政能力分级
        from .services.investment import InvestmentService
        relief_cost = InvestmentService.get_actual_cost(county, 'relief')
        treasury = county.get('treasury', 0)
        if treasury >= relief_cost:
            # 有钱不救，惩罚更重
            penalty = -15
            msg = "拒绝赈灾（府库充裕），民心大幅下降"
        else:
            # 确实无力赈灾，惩罚较轻
            penalty = -10
            msg = "拒绝赈灾（府库不足），民心下降"
        MetricsMixin.apply_county_stat_delta(county, 'morale', penalty)
        MetricsMixin._sync_county_from_villages(county, 'morale')
        return {"message": msg}


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


def cls_handle_trade_route(action, req, game, county):
    """G2: 地主引荐商路，换取明年宗族后生举荐承诺"""
    village_name = req.get('village_name', '')
    agent_id = req.get('agent_id')
    agent_name = req.get('agent_name', '地主')
    est_gain = int(req.get('est_gain', 5))

    if action == 'accept':
        # commercial 是县级独立指标，直接更新县级值（不经过村级）
        old = float(county.get('commercial', 50))
        county['commercial'] = min(100.0, old + est_gain)
        actual_gain = round(county['commercial'] - old, 1)

        # 地主好感 +5
        if agent_id:
            try:
                agent = Agent.objects.get(id=agent_id, game=game)
                attrs = agent.attributes or {}
                attrs['player_affinity'] = min(99, int(attrs.get('player_affinity', 50)) + 5)
                # 记录期望：明年举荐宗族后生
                attrs['expects_nomination_year'] = year_of(game.current_season) + 1
                agent.attributes = attrs
                agent.save(update_fields=['attributes'])
            except Agent.DoesNotExist:
                pass

        # 生成承诺记录：明年举荐其族中后生
        Promise.objects.create(
            game=game,
            agent=Agent.objects.filter(id=agent_id).first() if agent_id else None,
            promise_type='OTHER',
            description=f'举荐{agent_name}族中宗族后生参加府试',
            status='PENDING',
            season_made=game.current_season,
            deadline_season=game.current_season + 12,
            context={'village_name': village_name, 'agent_name': agent_name},
        )

        return {"message": f"已允准{agent_name}引荐商路，{village_name}商业+{actual_gain}，并承诺明年举荐其族中后生"}
    else:
        # 拒绝：地主好感 -5
        if agent_id:
            try:
                agent = Agent.objects.get(id=agent_id, game=game)
                attrs = agent.attributes or {}
                attrs['player_affinity'] = max(-99, int(attrs.get('player_affinity', 50)) - 5)
                agent.attributes = attrs
                agent.save(update_fields=['attributes'])
            except Agent.DoesNotExist:
                pass
        return {"message": f"婉拒{agent_name}引荐商路，机会已过，对方略感失望"}


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
        currently_eligible = attrs.get('exam_eligible', False)
        nominating = not currently_eligible  # True = 举荐操作，False = 取消举荐

        # ── 每年最多举荐 2 人：举荐前检查已有名额 ──
        MAX_PER_YEAR = 2
        if nominating:
            current_year = year_of(game.current_season)
            # 统计本局当前已举荐（exam_eligible=True）的宗族后生数，排除自身
            eligible_others = [
                a for a in Agent.objects.filter(game=game, role='CLAN_YOUTH').exclude(id=agent_id)
                if (a.attributes or {}).get('exam_eligible', False)
            ]
            if len(eligible_others) >= MAX_PER_YEAR:
                return Response(
                    {"error": f"本年度举荐名额已满（最多{MAX_PER_YEAR}人），如需更换请先取消已有举荐"},
                    status=400,
                )

        # ── 更新 exam_eligible ──
        attrs['exam_eligible'] = nominating
        agent.attributes = attrs
        agent.save(update_fields=['attributes'])

        affinity_msg = ''
        if nominating:
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

            # ── 兑现关联承诺：若举荐人有对应的待兑现「举荐后生」承诺，标记为已兑现 ──
            if sponsor_id:
                from django.utils import timezone
                fulfilled = Promise.objects.filter(
                    game=game,
                    agent_id=sponsor_id,
                    promise_type='OTHER',
                    status='PENDING',
                ).filter(description__contains='宗族后生')
                fulfilled.update(status='FULFILLED', resolved_at=timezone.now())

            msg = f'已举荐{agent.name}为府试候选{affinity_msg}'
        else:
            msg = f'已取消{agent.name}的举荐'

        # ── 同步 clan_youth_pending：至少1人已举荐则可推进月份 ──
        county = load_county_state(game)
        new_eligible_count = sum(
            1 for a in Agent.objects.filter(game=game, role='CLAN_YOUTH')
            if (a.attributes or {}).get('exam_eligible', False)
        )
        if new_eligible_count >= 1:
            county['clan_youth_pending'] = False
        else:
            # 无人举荐时，若本月有宗族后生可选，恢复待操作提示
            has_youths = Agent.objects.filter(game=game, role='CLAN_YOUTH').exists()
            county['clan_youth_pending'] = has_youths
        save_player_state(game, county)

        return Response({
            'success': True,
            'agent_id': agent_id,
            'exam_eligible': nominating,
            'message': msg,
            'eligible_count': new_eligible_count,
            'max_per_year': MAX_PER_YEAR,
            # 返回最新的 pending 状态，供前端同步内存中的 g.county_data
            'clan_youth_pending': county.get('clan_youth_pending', False),
        })
