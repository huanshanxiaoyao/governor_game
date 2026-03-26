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
                    "id":                  p.id,
                    "policy_name":         p.policy_name,
                    "status":              p.status,
                    "action_key":          p.action_key,
                    "cost":                p.cost,
                    "delay_months":        p.delay_months,
                    "effects_data":        p.effects_data,
                    "rationale":           p.rationale,
                    "rejection_reason":    p.rejection_reason,
                    "is_executed":         p.is_executed,
                    "synced_from_id":      p.synced_from_id,
                    "tier":                p.tier,
                    "code_status":         p.code_status,
                    "unsupported_effects": p.unsupported_effects,
                    "created_at":          p.created_at.isoformat(),
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


class PolicyReviewDebugPageView(APIView):
    """GET /api/admin/policy-review/ — render the debug page"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.shortcuts import render
        from .models import GameState
        games = GameState.objects.filter(
            user=request.user,
            player_role='COUNTY_MAGISTRATE',
        ).order_by('-updated_at')[:20]
        return render(request, 'game/policy_review_debug.html', {'games': games})


class PolicyReviewDebugRunView(APIView):
    """POST /api/admin/policy-review/run/ — run LLM review on test proposal"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        import json
        from .models import GameState, ProposedPolicy
        from .services.policy_review import PolicyReviewService
        from .services.state import load_county_state
        from llm.prompts import PromptRegistry
        from llm.client import LLMClient
        from llm.exceptions import LLMJSONParseError, LLMRequestError

        policy_name = (request.data.get('policy_name') or '').strip()
        description = (request.data.get('description') or '').strip()
        game_id = request.data.get('game_id')

        if not policy_name:
            return Response({'error': '请填写施政名称'}, status=400)

        # Load county context (use game if provided, else use defaults)
        county = None
        game = None
        if game_id:
            try:
                game = GameState.objects.get(id=game_id, user=request.user)
                county = load_county_state(game)
            except GameState.DoesNotExist:
                pass

        if county is None:
            # Default county snapshot for testing without a game
            county = {
                'province_name': '浙江',
                'morale': 55, 'security': 50, 'commercial': 45,
                'education': 40, 'treasury': 800,
                'villages': [{'population': 500}, {'population': 400}],
                'price_index': 1.0,
            }

        province_name = county.get('province_name', '浙江')

        # Build existing policies summary
        existing_summary = PolicyReviewService._build_existing_policies_summary(county, game)
        county_snapshot = PolicyReviewService._build_county_snapshot(county)

        # Recent rejections (from game if available)
        rejected = []
        if game:
            rejected = list(ProposedPolicy.objects.filter(
                game=game, status=ProposedPolicy.Status.REJECTED,
            ).order_by('-reviewed_at')[:10])
        recent_rejections = PolicyReviewService._build_rejections_summary(rejected)

        # Build the fake proposal
        proposals_json = json.dumps([{
            'proposal_id': 0,
            'policy_name': policy_name,
            'description': description or policy_name,
        }], ensure_ascii=False, indent=2)

        system_prompt, user_prompt = PromptRegistry.render(
            'provincial_review_json',
            province_name=province_name,
            existing_policies_summary=existing_summary,
            county_snapshot=county_snapshot,
            recent_rejections=recent_rejections,
            proposals_json=proposals_json,
        )

        # Call LLM
        llm_error = None
        decision = None
        raw_output = None
        try:
            client = LLMClient()
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ]
            raw = client.chat_json(messages, max_tokens=1500)
            raw_output = raw
            # Parse result (same logic as PolicyReviewService._call_llm)
            if isinstance(raw, list) and raw:
                decision = raw[0]
            elif isinstance(raw, dict):
                for key in ('reviews', 'results', 'decisions'):
                    if key in raw and isinstance(raw[key], list) and raw[key]:
                        decision = raw[key][0]
                        break
                if decision is None and 'proposal_id' in raw:
                    decision = raw
            if decision is None:
                llm_error = f'LLM 返回格式异常: {type(raw).__name__}'
        except LLMJSONParseError as e:
            llm_error = f'JSON 解析失败: {e}'
        except LLMRequestError as e:
            llm_error = f'LLM 请求失败: {e}'
        except Exception as e:
            llm_error = f'未知错误: {e}'

        # Analyze effects_data
        effects_analysis = []
        if decision and decision.get('approved') and decision.get('effects_data'):
            effects_analysis = _analyze_effects(decision['effects_data'])

        return Response({
            'prompt': {
                'system': system_prompt,
                'user': user_prompt,
            },
            'context': {
                'existing_policies': existing_summary,
                'county_snapshot': county_snapshot,
                'recent_rejections': recent_rejections,
            },
            'decision': decision,
            'raw_output': raw_output,
            'llm_error': llm_error,
            'effects_analysis': effects_analysis,
            'overall': _overall_verdict(effects_analysis),
        })


def _analyze_effects(effects_data):
    """Analyze each key in effects_data, classify as supported or needs new code."""
    # Keys natively supported by _apply_stat_delta() and _apply_on_complete()
    SUPPORTED_STAT_KEYS = {
        'morale':     ('county_data 核心指标', '直接通过 MetricsMixin 写入，有上下限约束'),
        'security':   ('county_data 核心指标', '直接通过 MetricsMixin 写入，有上下限约束'),
        'commercial': ('county_data 核心指标', '直接通过 MetricsMixin 写入，有上下限约束'),
        'education':  ('county_data 核心指标', '直接通过 MetricsMixin 写入，有上下限约束'),
        'agriculture':('agriculture_bonus 字段', '映射到 county["agriculture_bonus"]，秋收（九月）乘入产出'),
        '民心':       ('中文别名→morale', '已有 EFFECTS_STAT_ALIASES 映射，建议改用英文键'),
        '治安':       ('中文别名→security', '已有 EFFECTS_STAT_ALIASES 映射，建议改用英文键'),
        '商业':       ('中文别名→commercial', '已有 EFFECTS_STAT_ALIASES 映射，建议改用英文键'),
        '文教':       ('中文别名→education', '已有 EFFECTS_STAT_ALIASES 映射，建议改用英文键'),
    }
    SUPPORTED_SPECIAL_KEYS = {
        'add_market': (
            '新增集市',
            '_apply_on_complete() 特殊处理，追加到 county["markets"]，'
            '下月 _update_commercial() 自动计算 GMV 和商业税',
        ),
    }

    results = []
    for section in ('immediate', 'on_complete'):
        section_data = effects_data.get(section, {})
        if not isinstance(section_data, dict):
            continue
        for key, value in section_data.items():
            if key in SUPPORTED_STAT_KEYS:
                mech, notes = SUPPORTED_STAT_KEYS[key]
                results.append({
                    'section': section,
                    'key': key,
                    'value': value,
                    'supported': True,
                    'mechanism': mech,
                    'notes': notes,
                    'needs_new_code': False,
                    'needs_forced_refresh': False,
                    'refresh_path': 'advance_season → save_player_state → getGame → setGame，全自动',
                })
            elif key in SUPPORTED_SPECIAL_KEYS:
                mech, notes = SUPPORTED_SPECIAL_KEYS[key]
                results.append({
                    'section': section,
                    'key': key,
                    'value': value,
                    'supported': True,
                    'mechanism': mech,
                    'notes': notes,
                    'needs_new_code': False,
                    'needs_forced_refresh': False,
                    'refresh_path': 'advance_season → save_player_state → getGame → setGame → renderDashboard，全自动',
                })
            else:
                results.append({
                    'section': section,
                    'key': key,
                    'value': value,
                    'supported': False,
                    'mechanism': '未知字段',
                    'notes': (
                        f'"{key}" 不在已支持的 effect 键集合中，'
                        '_apply_stat_delta() 会尝试直接写入 county[key]，'
                        '若该字段不存在于 county_data，效果将被静默丢弃。'
                        '需要在 investment.py 的 _apply_on_complete() 或 '
                        '_apply_stat_delta() 中添加对应处理逻辑，'
                        '并在 settlement_seasonal.py 中决定该字段如何参与结算。'
                    ),
                    'needs_new_code': True,
                    'needs_forced_refresh': False,
                    'refresh_path': '改完后端后，仍走现有 getGame → setGame 刷新链路，无需额外刷新代码',
                })
    return results


def _overall_verdict(effects_analysis):
    if not effects_analysis:
        return {'needs_new_code': False, 'needs_forced_refresh': False, 'summary': '无 effects_data 可分析'}
    needs_code = any(e['needs_new_code'] for e in effects_analysis)
    unsupported = [e['key'] for e in effects_analysis if e['needs_new_code']]
    if needs_code:
        return {
            'needs_new_code': True,
            'needs_forced_refresh': False,
            'unsupported_keys': unsupported,
            'summary': (
                f'以下效果键需要新增后端代码：{", ".join(unsupported)}。'
                '刷新机制本身不需要改动，仍走现有 advance → getGame → setGame 路径。'
            ),
        }
    return {
        'needs_new_code': False,
        'needs_forced_refresh': False,
        'summary': '所有效果均在现有机制支持范围内，后端和前端均无需新增代码，刷新全自动。',
    }


# ─────────────────────────────────────────────
# Phase 4: Tier 2 激活工作流
# ─────────────────────────────────────────────

class PolicyQueuePageView(APIView):
    """GET /api/admin/policy-queue/ — 施政队列管理后台"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.shortcuts import render
        from .models import ProposedPolicy, GameState

        # 查询当前用户所有游戏的已批准自创施政（含邻县同步来的）
        user_game_ids = list(
            GameState.objects.filter(user=request.user)
            .values_list('id', flat=True)
        )

        tier1_policies = list(ProposedPolicy.objects.filter(
            game_id__in=user_game_ids,
            status=ProposedPolicy.Status.APPROVED,
            tier=1,
        ).select_related('game').order_by('-reviewed_at'))

        tier2_policies = list(ProposedPolicy.objects.filter(
            game_id__in=user_game_ids,
            status=ProposedPolicy.Status.APPROVED,
            tier=2,
        ).select_related('game').order_by('-reviewed_at'))

        return render(request, 'game/policy_queue.html', {
            'tier1_policies': tier1_policies,
            'tier2_policies': tier2_policies,
        })


class PolicyQueueMarkDevCompleteView(APIView):
    """POST /api/admin/policy-queue/<id>/mark-dev-complete/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, policy_id):
        from .models import ProposedPolicy, GameState
        try:
            pp = ProposedPolicy.objects.select_related('game').get(id=policy_id)
        except ProposedPolicy.DoesNotExist:
            return Response({'error': '施政记录不存在'}, status=status.HTTP_404_NOT_FOUND)

        # 仅允许操作自己游戏的记录
        if pp.game.user_id != request.user.id:
            return Response({'error': '无权限'}, status=status.HTTP_403_FORBIDDEN)

        if pp.tier != 2:
            return Response({'error': '仅 Tier 2 施政需要此操作'}, status=status.HTTP_400_BAD_REQUEST)
        if pp.code_status != ProposedPolicy.CodeStatus.PENDING_DEV:
            return Response({
                'error': f'当前状态为 {pp.code_status}，无法标记开发完成'
            }, status=status.HTTP_400_BAD_REQUEST)

        pp.code_status = ProposedPolicy.CodeStatus.DEV_COMPLETE
        pp.save(update_fields=['code_status'])

        return Response({
            'id': pp.id,
            'code_status': pp.code_status,
            'message': f'「{pp.policy_name}」已标记为开发完成，可进行激活。',
        })


class PolicyQueueActivateView(APIView):
    """POST /api/admin/policy-queue/<id>/activate/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, policy_id):
        from .models import ProposedPolicy, GameState
        try:
            pp = ProposedPolicy.objects.select_related('game').get(id=policy_id)
        except ProposedPolicy.DoesNotExist:
            return Response({'error': '施政记录不存在'}, status=status.HTTP_404_NOT_FOUND)

        if pp.game.user_id != request.user.id:
            return Response({'error': '无权限'}, status=status.HTTP_403_FORBIDDEN)

        if pp.tier != 2:
            return Response({'error': '仅 Tier 2 施政需要激活'}, status=status.HTTP_400_BAD_REQUEST)
        if pp.code_status != ProposedPolicy.CodeStatus.DEV_COMPLETE:
            return Response({
                'error': f'当前状态为 {pp.code_status}，须先完成开发再激活'
            }, status=status.HTTP_400_BAD_REQUEST)

        # 激活：将提案对局加入 activated_game_ids
        activated_ids = list(pp.activated_game_ids or [])
        if pp.game_id not in activated_ids:
            activated_ids.append(pp.game_id)
        pp.activated_game_ids = activated_ids
        pp.code_status = ProposedPolicy.CodeStatus.ACTIVATED
        pp.save(update_fields=['code_status', 'activated_game_ids'])

        return Response({
            'id': pp.id,
            'code_status': pp.code_status,
            'activated_game_ids': pp.activated_game_ids,
            'message': f'「{pp.policy_name}」已激活，对局 #{pp.game_id} 的玩家现在可以执行。',
        })


class PolicyQueuePromoteGlobalView(APIView):
    """POST /api/admin/policy-queue/<id>/promote-global/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, policy_id):
        from .models import ProposedPolicy, GameState
        from .services.state import load_county_state
        try:
            pp = ProposedPolicy.objects.select_related('game').get(id=policy_id)
        except ProposedPolicy.DoesNotExist:
            return Response({'error': '施政记录不存在'}, status=status.HTTP_404_NOT_FOUND)

        if pp.game.user_id != request.user.id:
            return Response({'error': '无权限'}, status=status.HTTP_403_FORBIDDEN)

        if pp.tier != 2:
            return Response({'error': '仅 Tier 2 施政支持全局推广'}, status=status.HTTP_400_BAD_REQUEST)
        if pp.code_status != ProposedPolicy.CodeStatus.ACTIVATED:
            return Response({'error': '须先激活再进行全局推广'}, status=status.HTTP_400_BAD_REQUEST)

        pp.global_promotion = True
        pp.save(update_fields=['global_promotion'])

        # 触发邻县同步
        try:
            from .services.policy_sync import PolicySyncService
            county = load_county_state(pp.game)
            # Tier 2 全局推广：临时将 is_synced_to_neighbors 设为 False 以触发同步
            pp.is_synced_to_neighbors = False
            pp.tier = 1  # 临时当 Tier 1 处理以通过 sync 过滤器
            pp.save(update_fields=['is_synced_to_neighbors', 'tier'])
            PolicySyncService.sync_approved_to_neighbors(pp.game, county)
            # 恢复 tier
            pp.tier = 2
            pp.save(update_fields=['tier'])
            synced = True
        except Exception as e:
            logger.warning('policy_queue: global promote sync failed: %s', e)
            synced = False

        return Response({
            'id': pp.id,
            'global_promotion': pp.global_promotion,
            'synced_to_neighbors': synced,
            'message': f'「{pp.policy_name}」已设为全局推广。' + ('邻县同步完成。' if synced else '邻县同步失败，请检查日志。'),
        })
