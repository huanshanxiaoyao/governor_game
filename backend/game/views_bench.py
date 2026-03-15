"""LLM Benchmark views — 开发调试工具，用于对比不同 Provider 的响应"""
import dataclasses
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import GameState, Agent, NeighborCounty, AdminUnit
from .services import AgentService
from .services.ai_governor import AIGovernorService
from .services.constants import generate_governor_profile
from llm.client import LLMClient, _extract_json as _strip_code_fence
from llm.exceptions import LLMError
from llm.prompts import PromptRegistry
from llm.providers import get_provider

logger = logging.getLogger('game')


class BenchGamesView(APIView):
    """GET /api/bench/games/ — 列出最近20个存档，按 player_role 返回对应 NPC"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        games = GameState.objects.order_by('-created_at')[:20]
        result = []
        for g in games:
            if g.player_role == 'COUNTY_MAGISTRATE':
                county_name = g.county_data.get('county_name', '未知县') if g.county_data else '未知县'
                npcs = _get_neighbor_npcs(g)
            elif g.player_role == 'PREFECT':
                # 府的名称存在 player_unit.unit_data
                pu = g.admin_units.filter(is_player_controlled=True, unit_type='PREFECTURE').first()
                county_name = pu.unit_data.get('prefecture_name', '未知府') if pu else '未知府'
                npcs = _get_prefecture_county_npcs(g)
            else:
                county_name = g.county_data.get('county_name', '未知') if g.county_data else '未知'
                npcs = []

            result.append({
                'game_id': g.id,
                'county_name': county_name,
                'season': g.current_season,
                'player_role': g.player_role,
                'npcs': npcs,
            })
        return Response({'games': result})


class BenchContextView(APIView):
    """POST /api/bench/context/ — 用真实存档数据渲染 Prompt，供预览"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        game_id = request.data.get('game_id')
        npc_source = request.data.get('npc_source')
        npc_id = request.data.get('npc_id')

        try:
            game = GameState.objects.get(id=game_id)
        except GameState.DoesNotExist:
            return Response({'error': f'存档 {game_id} 不存在'}, status=404)

        neighbor, err = _load_governor_npc(npc_source, npc_id, game)
        if err:
            return Response({'error': err}, status=404)

        profile = _get_or_generate_profile(neighbor)
        ctx = AIGovernorService._build_context(neighbor, neighbor.county_data, game.current_season, profile)
        system_prompt, user_prompt = PromptRegistry.render('ai_governor_decision', **ctx)

        return Response({
            'template_name': 'ai_governor_decision',
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
        })


class BenchRunView(APIView):
    """POST /api/bench/run/ — 并行调用多个 Provider，返回对比结果"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        game_id = request.data.get('game_id')
        npc_source = request.data.get('npc_source')
        npc_id = request.data.get('npc_id')
        # runs: [{provider, model}, ...] — 支持同一 provider 多个模型并排对比
        runs = request.data.get('runs', [])

        if not runs:
            return Response({'error': '未选择任何模型'}, status=400)

        try:
            game = GameState.objects.get(id=game_id)
        except GameState.DoesNotExist:
            return Response({'error': f'存档 {game_id} 不存在'}, status=404)

        neighbor, err = _load_governor_npc(npc_source, npc_id, game)
        if err:
            return Response({'error': err}, status=404)

        profile = _get_or_generate_profile(neighbor)
        ctx = AIGovernorService._build_context(neighbor, neighbor.county_data, game.current_season, profile)
        system_prompt, user_prompt = PromptRegistry.render('ai_governor_decision', **ctx)

        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ]

        results_map = {}
        with ThreadPoolExecutor(max_workers=min(len(runs), 6)) as executor:
            futures = {
                executor.submit(_call_provider, run['provider'], messages, True, run.get('model')): i
                for i, run in enumerate(runs)
            }
            for future in as_completed(futures):
                results_map[futures[future]] = future.result()

        results = [results_map[i] for i in range(len(runs))]

        return Response({
            'template_name': 'ai_governor_decision',
            'is_json': True,
            'results': results,
        })


# ---------------------------------------------------------------------------
# NPC loading helpers
# ---------------------------------------------------------------------------

def _get_neighbor_npcs(game):
    """县游戏：返回邻县知县列表"""
    npcs = []
    for nc in NeighborCounty.objects.filter(game=game).order_by('id'):
        arch_display = dict(NeighborCounty.ARCHETYPE_CHOICES).get(nc.governor_archetype, nc.governor_archetype)
        npcs.append({
            'id': nc.id,
            'source': 'neighbor_county',
            'name': nc.governor_name,
            'county_name': nc.county_name,
            'description': f'邻县知县 · {arch_display}',
        })
    return npcs


def _get_prefecture_county_npcs(game):
    """府游戏：返回下属知县/知州列表（非玩家控制的 COUNTY AdminUnit）"""
    npcs = []
    for unit in AdminUnit.objects.filter(
        game=game, unit_type='COUNTY', is_player_controlled=False
    ).order_by('id'):
        ud = unit.unit_data
        gov_name = ud.get('governor_name', '未知知县')
        county_name = ud.get('county_name', '未知县')
        archetype = ud.get('governor_archetype', '')
        arch_map = {'VIRTUOUS': '循吏型', 'MIDDLING': '中庸守成型', 'CORRUPT': '贪酷恶劣型'}
        arch_display = arch_map.get(archetype, archetype)
        npcs.append({
            'id': unit.id,
            'source': 'admin_unit',
            'name': gov_name,
            'county_name': county_name,
            'description': f'下属知县 · {arch_display}',
        })
    return npcs


def _load_governor_npc(npc_source, npc_id, game):
    """加载 governor NPC，返回 (pseudo_neighbor, error_msg)"""
    if npc_source == 'neighbor_county':
        try:
            nc = NeighborCounty.objects.get(id=npc_id, game=game)
        except NeighborCounty.DoesNotExist:
            return None, f'邻县 {npc_id} 不存在'
        return nc, None

    elif npc_source == 'admin_unit':
        try:
            unit = AdminUnit.objects.get(id=npc_id, game=game, unit_type='COUNTY')
        except AdminUnit.DoesNotExist:
            return None, f'下属县 AdminUnit {npc_id} 不存在'
        ud = unit.unit_data
        pseudo = SimpleNamespace(
            governor_name=ud.get('governor_name', '未知知县'),
            governor_style=ud.get('governor_style', 'zhengji'),
            governor_bio=ud.get('governor_bio', ''),
            governor_archetype=ud.get('governor_archetype', 'MIDDLING'),
            county_name=ud.get('county_name', '未知县'),
            county_data=ud,
        )
        return pseudo, None

    return None, f'未知 npc_source: {npc_source}'


def _get_or_generate_profile(neighbor):
    """获取或临时生成 governor_profile（不写库，仅用于 bench）"""
    profile = neighbor.county_data.get('governor_profile')
    if not profile:
        style = getattr(neighbor, 'governor_style', 'zhengji')
        profile = generate_governor_profile(style)
    return profile


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------

def _call_provider(provider_name, messages, is_json, model_override=None):
    """调用单个 Provider，捕获异常，记录耗时"""
    try:
        config = get_provider(provider_name)
    except Exception as e:
        return {
            'provider': provider_name, 'model': '?', 'latency_ms': 0,
            'raw': '', 'parsed': None, 'valid': False, 'error': str(e),
        }

    if model_override:
        config = dataclasses.replace(config, default_model=model_override)

    client = LLMClient(config=config)
    start = time.time()
    raw = ''
    parsed = None
    valid = False
    error = None
    reasoning_content = None

    try:
        # chat_bench 使用 max_tokens=8192，防止推理模型（deepseek-reasoner 等）
        # 因思考链耗尽 token 预算导致 content 为空
        raw, reasoning_content = client.chat_bench(messages, temperature=0.7)
        if not raw:
            raise ValueError('模型返回空响应（推理模型 max_tokens 不足或接口异常）')
        if is_json:
            parsed = json.loads(_strip_code_fence(raw))
            valid = True
        else:
            valid = bool(raw.strip())
    except LLMError as e:
        error = str(e)
        raw = raw or str(e)
    except (json.JSONDecodeError, TypeError) as e:
        error = f'JSON 解析失败: {e}'
        valid = False
    except Exception as e:
        error = str(e)
        raw = raw or str(e)

    return {
        'provider': provider_name,
        'model': config.default_model,
        'latency_ms': int((time.time() - start) * 1000),
        'raw': raw,
        'parsed': parsed,
        'valid': valid,
        'error': error,
        'reasoning_content': reasoning_content,
    }
