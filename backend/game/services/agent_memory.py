"""结构化 NPC 记忆服务"""
from __future__ import annotations

from ..models import AgentMemory, Agent

_POLICY_KEYWORDS = ('水利', '学堂', '赈灾', '加税', '降税', '调衡量',
                    '盐铁', '修路', '医馆', '粮仓', '差役')


def _extract_keywords(query_text: str, agent) -> dict:
    text = query_text or ''
    out = {'villages': [], 'policies': [], 'agents': []}
    if not text:
        return out
    game = agent.game
    county = getattr(game, 'county_data', None) or {}
    for v in county.get('villages', []):
        name = v.get('name')
        if name and name in text:
            out['villages'].append(name)
    for kw in _POLICY_KEYWORDS:
        if kw in text:
            out['policies'].append(kw)
    for other in Agent.objects.filter(game=game).exclude(pk=agent.pk):
        if other.name and other.name in text:
            out['agents'].append(other.name)
    return out


def _recency_bonus(memory_season: int, current_season: int) -> int:
    diff = current_season - memory_season
    if diff <= 0:
        return 5
    if diff <= 3:
        return 3
    if diff <= 12:
        return 1
    return 0


def _match_bonus(memory, keywords: dict) -> int:
    score = 0
    text = memory.text or ''
    related = memory.related_entities or {}
    for v in keywords['villages']:
        if v in text or related.get('village') == v:
            score += 2
    for kw in keywords['policies']:
        if kw in text:
            score += 2
    for name in keywords['agents']:
        agents_list = related.get('agents') or []
        if name in text or name in agents_list:
            score += 2
    return min(score, 6)


class AgentMemoryService:

    @staticmethod
    def record(agent, *, text, topic, importance, source, season,
               related_entities=None):
        """记录 NPC 记忆。

        Args:
            agent: Agent instance
            text: 记忆内容文本
            topic: 记忆主题（e.g., 'interaction', 'rumor', 'event'）
            importance: 重要性权重（1-10）
            source: 记忆来源（e.g., 'direct_interaction', 'rumor_board', 'event'）
            season: 季节号
            related_entities: 相关实体字典，默认 {}

        Returns:
            创建的 AgentMemory 实例
        """
        importance = max(1, min(10, int(importance)))
        return AgentMemory.objects.create(
            agent=agent,
            text=text,
            topic=topic,
            importance=importance,
            season=season,
            source=source,
            related_entities=related_entities or {},
        )

    @staticmethod
    def fetch_relevant(agent, *, current_season, query_text='', limit=8):
        """按重要性 + 时效性 + 关键词匹配打分，返回最相关的记忆列表。

        Args:
            agent: Agent instance
            current_season: 当前季节号，用于计算时效衰减
            query_text: 查询文本，用于关键词匹配加分
            limit: 返回记忆条数上限

        Returns:
            排序后的 AgentMemory 列表（最多 limit 条，高重要性记忆可能额外追加）
        """
        memories = list(AgentMemory.objects.filter(agent=agent))
        if not memories:
            return []
        keywords = _extract_keywords(query_text, agent)

        scored = []
        for m in memories:
            score = (
                m.importance * 2
                + _recency_bonus(m.season, current_season)
                + _match_bonus(m, keywords)
            )
            scored.append((score, m))
        scored.sort(key=lambda t: (-t[0], -t[1].season, -t[1].importance))

        picked = [m for _, m in scored[:limit]]
        in_picked = {m.pk for m in picked}
        high_missing = [m for _, m in scored
                        if m.importance >= 8 and m.pk not in in_picked][:2]
        if high_missing:
            picked = picked + high_missing
        return picked

    @staticmethod
    def compact_if_needed(agent, threshold=80):
        """超过 threshold 时删除 importance<=3 且 season < current-8 的条目。

        Args:
            agent: Agent instance
            threshold: 记忆数超过此阈值时触发清理

        Returns:
            删除的记忆条目数

        TODO(v2): LLM 总结合并。
        """
        qs = AgentMemory.objects.filter(agent=agent)
        if qs.count() <= threshold:
            return 0

        current = getattr(agent.game, 'current_season', 1)
        old = qs.filter(importance__lte=3, season__lt=current - 8)
        count = old.count()
        old.delete()
        return count
