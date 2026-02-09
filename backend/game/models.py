from django.db import models
from django.contrib.auth.models import User


class GameState(models.Model):
    """游戏存档 - 核心表"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='games')
    current_season = models.IntegerField(default=1, help_text='当前季度 (1-12)')
    county_data = models.JSONField(default=dict, help_text='所有县域数据')
    pending_events = models.JSONField(default=list, help_text='待处理事件')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'game_states'
        indexes = [
            models.Index(fields=['user', '-updated_at']),
        ]

    def __str__(self):
        return f"Game #{self.id} - User:{self.user.username} Season:{self.current_season}"


class PlayerProfile(models.Model):
    """玩家档案 - 每局游戏一个"""
    BACKGROUND_CHOICES = [
        ('HUMBLE', '寒门子弟'),
        ('SCHOLAR', '书香门第'),
        ('OFFICIAL', '官宦之后'),
    ]

    # 默认初始值：知识/技能按出身背景设定
    BACKGROUND_DEFAULTS = {
        'HUMBLE': {'knowledge': 3.0, 'skill': 3.0},
        'SCHOLAR': {'knowledge': 5.0, 'skill': 3.0},
        'OFFICIAL': {'knowledge': 4.0, 'skill': 5.0},
    }

    game = models.OneToOneField(GameState, on_delete=models.CASCADE, related_name='player')
    background = models.CharField(max_length=10, choices=BACKGROUND_CHOICES, help_text='出身背景')

    # 内在能力（隐藏，1-10，通过失败后成功提升）
    knowledge = models.FloatField(default=3.0, help_text='知识：农耕/经济/地理，影响治理效果')
    skill = models.FloatField(default=3.0, help_text='技能：谈判/协调，影响谈判效果')

    # 声望（半隐藏，0-100，玩家看5档分级±偏差）
    integrity = models.IntegerField(default=50, help_text='清名：公正廉洁的口碑')
    competence = models.IntegerField(default=30, help_text='能名：干练能干的口碑')
    popularity = models.IntegerField(default=10, help_text='人缘：官场好相处的口碑')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'player_profiles'

    def __str__(self):
        return f"Player - Game#{self.game_id} ({self.get_background_display()})"


class Agent(models.Model):
    """Agent实体"""
    TIER_CHOICES = [
        ('FULL', '完整Agent (LLM驱动)'),
        ('LIGHT', '轻量Agent (规则+模板)'),
    ]

    name = models.CharField(max_length=50, help_text='名字')
    role = models.CharField(max_length=50, help_text='角色')
    tier = models.CharField(max_length=5, choices=TIER_CHOICES, help_text='层级')
    attributes = models.JSONField(default=dict, help_text='所有属性 (JSONB)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'agents'

    def __str__(self):
        return f"{self.name} ({self.role})"


class Relationship(models.Model):
    """关系网络"""
    agent_a = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='relationships_as_a')
    agent_b = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='relationships_as_b')
    affinity = models.IntegerField(default=10, help_text='好感度 (-99 to 99)')
    data = models.JSONField(default=dict, help_text='其他关系数据')

    class Meta:
        db_table = 'relationships'
        constraints = [
            models.UniqueConstraint(fields=['agent_a', 'agent_b'], name='unique_relationship'),
        ]

    def __str__(self):
        return f"{self.agent_a.name} <-> {self.agent_b.name} ({self.affinity})"


class EventLog(models.Model):
    """事件记录 - 用于调试和历史回溯"""
    game = models.ForeignKey(GameState, on_delete=models.CASCADE, related_name='event_logs')
    season = models.IntegerField(help_text='触发季度')
    event_type = models.CharField(max_length=100, help_text='事件类型')
    choice = models.CharField(max_length=200, blank=True, default='', help_text='玩家选择')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'event_logs'
        indexes = [
            models.Index(fields=['game', 'season']),
        ]

    def __str__(self):
        return f"Game#{self.game_id} S{self.season}: {self.event_type}"
