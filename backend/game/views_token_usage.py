# backend/game/views_token_usage.py
"""Token 用量统计 API"""

from django.db.models import Sum
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .view_helpers import game_view

# season → 中文月名
_MONTH_NAMES = [
    '正月', '二月', '三月', '四月', '五月', '六月',
    '七月', '八月', '九月', '十月', '冬月', '腊月',
]


def _season_name(n):
    if n is None:
        return '未知'
    year = (n - 1) // 12 + 1
    month = _MONTH_NAMES[(n - 1) % 12]
    return f'第{year}年·{month}'


class TokenUsageView(APIView):
    """
    GET /api/games/<game_id>/token-usage/

    返回本局所有 LLM 调用的 token 用量，按 season 聚合。

    Response:
        {
            "total_tokens": 48320,
            "by_season": [
                {
                    "season": 1,
                    "season_name": "第1年·正月",
                    "total_tokens": 1820,
                    "by_source": {"agent_chat": 980, "counsel": 540, ...}
                },
                ...
            ]
        }
    """
    permission_classes = [IsAuthenticated]

    @game_view()
    def get(self, request, game, *, game_id):
        from llm.models import LLMCallLog

        rows = (
            LLMCallLog.objects
            .filter(game_id=game_id)
            .values('season', 'call_source')
            .annotate(tokens=Sum('total_tokens'))
            .order_by('season', 'call_source')
        )

        # 整理成嵌套结构
        seasons_map = {}
        for row in rows:
            s = row['season']
            if s not in seasons_map:
                seasons_map[s] = {}
            seasons_map[s][row['call_source']] = row['tokens']

        by_season = [
            {
                'season':       s,
                'season_name':  _season_name(s),
                'total_tokens': sum(src_map.values()),
                'by_source':    src_map,
            }
            for s, src_map in sorted(seasons_map.items())
        ]
        total = sum(s['total_tokens'] for s in by_season)

        return Response({
            'total_tokens': total,
            'by_season':    by_season,
        })
