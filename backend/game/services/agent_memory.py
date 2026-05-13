"""结构化 NPC 记忆服务"""
from __future__ import annotations

from ..models import AgentMemory


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
