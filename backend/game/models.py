from django.db import models
from django.contrib.auth.models import User


class GameState(models.Model):
    """游戏存档 - 核心表"""
    ROLE_CHOICES = [
        ('COUNTY_MAGISTRATE', '知县'),
        ('PREFECT', '知府'),
        ('GOVERNOR', '巡抚'),
        ('CABINET', '内阁'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='games')
    current_season = models.IntegerField(default=1, help_text='当前月份 (1-36)')
    county_data = models.JSONField(default=dict, help_text='所有县域数据（知县游戏用）')
    pending_events = models.JSONField(default=list, help_text='待处理事件')
    player_role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default='COUNTY_MAGISTRATE',
        help_text='玩家当前官职层级',
    )
    player_unit = models.ForeignKey(
        'AdminUnit', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='player_game', help_text='玩家当前治理的行政单元',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'game_states'
        indexes = [
            models.Index(fields=['user', '-updated_at']),
        ]

    def __str__(self):
        return f"Game #{self.id} - User:{self.user.username} Season:{self.current_season}"

    def get_unit_data(self):
        """返回玩家单位的数据（县级游戏兼容 county_data）"""
        if self.player_unit_id is not None:
            return self.player_unit.unit_data
        return self.county_data


class AdminUnit(models.Model):
    """行政单位 — 支持县/府/省/朝廷各层级，构成可扩展的治理层级树"""
    UNIT_TYPE_CHOICES = [
        ('COUNTY', '县/州'),
        ('PREFECTURE', '府'),
        ('PROVINCE', '省'),
        ('EMPIRE', '朝廷'),
    ]

    game = models.ForeignKey(
        'GameState', on_delete=models.CASCADE, related_name='admin_units',
    )
    unit_type = models.CharField(max_length=15, choices=UNIT_TYPE_CHOICES)
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children',
    )
    unit_data = models.JSONField(default=dict, help_text='行政单元状态数据（与county_data同结构或府/省专用结构）')
    is_player_controlled = models.BooleanField(default=False)
    ai_agent = models.ForeignKey(
        'Agent', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='governed_units', help_text='AI治理官员（非玩家控制时）',
    )

    class Meta:
        db_table = 'admin_units'
        indexes = [
            models.Index(fields=['game', 'unit_type']),
            models.Index(fields=['parent']),
        ]

    def __str__(self):
        name = self.unit_data.get('county_name') or self.unit_data.get('prefecture_name', '')
        return f"AdminUnit({self.get_unit_type_display()}) {name} - Game#{self.game_id}"


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

    # 家产（个人财富，两）
    personal_wealth = models.FloatField(default=0.0, help_text='家产（两）：任内积累的个人财富，含合法薪俸与灰色所得')

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

    ROLE_CHOICES = [
        # 县级
        ('ADVISOR', '师爷'),
        ('DEPUTY', '县丞'),
        ('PREFECT', '知府'),
        ('GENTRY', '士绅'),
        ('VILLAGER', '村民'),
        # 官场体系
        ('EMPEROR', '皇帝'),
        ('CABINET_CHIEF', '内阁首辅'),
        ('CABINET_MEMBER', '内阁成员'),
        ('MINISTER', '尚书'),
        ('VICE_MINISTER', '侍郎'),
        ('CHIEF_CENSOR', '左都御史'),
        ('VICE_CENSOR', '左副都御史'),
        ('CENSOR', '监察御史'),
        ('GOVERNOR_GENERAL', '总督'),
        ('PROVINCIAL_GOVERNOR', '巡抚'),
        ('PROVINCIAL_COMMISSIONER', '布政使/按察使'),
        ('PREFECT_PEER', '知州'),
    ]

    game = models.ForeignKey(GameState, on_delete=models.CASCADE, related_name='agents')
    name = models.CharField(max_length=50, help_text='名字')
    source_name = models.CharField(max_length=50, blank=True, default='',
                                   help_text='历史原型真名（如 徐阶）')
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, help_text='角色')
    role_title = models.CharField(max_length=50, help_text='显示称谓 (师爷/知府/地主/耆老/里长)')
    tier = models.CharField(max_length=5, choices=TIER_CHOICES, help_text='层级')
    attributes = models.JSONField(default=dict, help_text='所有属性 (JSONB)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'agents'
        indexes = [
            models.Index(fields=['game', 'role']),
        ]

    def __str__(self):
        return f"{self.name} ({self.role_title}) - Game#{self.game_id}"


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
    CATEGORY_CHOICES = [
        ('SYSTEM', '系统'),
        ('INVESTMENT', '投资'),
        ('TAX', '税率'),
        ('NEGOTIATION', '谈判'),
        ('DISASTER', '灾害'),
        ('SETTLEMENT', '结算'),
        ('ANNEXATION', '兼并'),
        ('PROMISE', '承诺'),
    ]

    game = models.ForeignKey(GameState, on_delete=models.CASCADE, related_name='event_logs')
    season = models.IntegerField(help_text='触发月份')
    event_type = models.CharField(max_length=100, help_text='事件类型')
    category = models.CharField(
        max_length=20, choices=CATEGORY_CHOICES, default='SYSTEM',
        help_text='事件分类',
    )
    description = models.TextField(blank=True, default='', help_text='人类可读的事件描述')
    choice = models.CharField(max_length=200, blank=True, default='', help_text='玩家选择')
    data = models.JSONField(default=dict, blank=True, help_text='结构化事件数据')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'event_logs'
        indexes = [
            models.Index(fields=['game', 'season']),
            models.Index(fields=['game', 'category']),
        ]

    def __str__(self):
        return f"Game#{self.game_id} S{self.season}: [{self.category}] {self.event_type}"


class NegotiationSession(models.Model):
    """谈判会话 — 地主兼并 / 兴建水利 多轮谈判状态机"""
    EVENT_TYPES = [
        ('ANNEXATION', '地主兼并'),
        ('IRRIGATION', '兴建水利'),
        ('HIDDEN_LAND', '隐匿土地'),
    ]
    STATUS_CHOICES = [
        ('active', '进行中'),
        ('resolved', '已结算'),
    ]

    game = models.ForeignKey(GameState, on_delete=models.CASCADE, related_name='negotiations')
    agent = models.ForeignKey('Agent', on_delete=models.CASCADE, related_name='negotiations')
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    current_round = models.IntegerField(default=0)
    max_rounds = models.IntegerField(help_text='ANNEXATION=8, IRRIGATION=12')
    season = models.IntegerField(help_text='触发时的月份')
    context_data = models.JSONField(default=dict, help_text='事件参数')
    outcome = models.JSONField(default=dict, blank=True, help_text='结算结果')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'negotiation_sessions'
        constraints = [
            models.UniqueConstraint(
                fields=['game', 'agent'],
                condition=models.Q(status='active'),
                name='one_active_negotiation_per_agent',
            ),
        ]
        indexes = [
            models.Index(fields=['game', 'status']),
            models.Index(fields=['game', 'event_type', '-created_at']),
        ]

    def __str__(self):
        return (f"Negotiation #{self.id} {self.get_event_type_display()} "
                f"G#{self.game_id} R{self.current_round}/{self.max_rounds} [{self.status}]")


class DialogueMessage(models.Model):
    """对话消息记录"""
    ROLE_CHOICES = [
        ('player', '玩家'),
        ('agent', 'NPC'),
        ('system', '系统'),
    ]

    game = models.ForeignKey(GameState, on_delete=models.CASCADE, related_name='dialogue_messages')
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='dialogue_messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, help_text='消息角色')
    content = models.TextField(help_text='消息内容')
    season = models.IntegerField(help_text='对话时的月份')
    metadata = models.JSONField(default=dict, blank=True, help_text='附加数据 (reasoning, attitude_change等)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'dialogue_messages'
        indexes = [
            models.Index(fields=['game', 'agent', '-created_at']),
        ]

    def __str__(self):
        return f"[{self.role}] {self.agent.name} G#{self.game_id} S{self.season}: {self.content[:30]}"


class Promise(models.Model):
    """玩家承诺追踪"""
    PROMISE_TYPES = [
        ('LOWER_TAX', '降低税率'),
        ('BUILD_SCHOOL', '资助村塾'),
        ('BUILD_IRRIGATION', '修建水利'),
        ('RELIEF', '赈灾救济'),
        ('HIRE_BAILIFFS', '增设衙役'),
        ('RECLAIM_LAND', '开垦荒地'),
        ('REPAIR_ROADS', '修缮道路'),
        ('BUILD_GRANARY', '开设义仓'),
        ('OTHER', '其他'),
    ]
    STATUS_CHOICES = [
        ('PENDING', '待履行'),
        ('FULFILLED', '已履行'),
        ('BROKEN', '已违约'),
    ]

    game = models.ForeignKey(GameState, on_delete=models.CASCADE, related_name='promises')
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='promises')
    negotiation = models.ForeignKey(
        NegotiationSession, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='promises',
    )
    promise_type = models.CharField(max_length=20, choices=PROMISE_TYPES, help_text='承诺类型')
    description = models.TextField(help_text='人类可读描述')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    season_made = models.IntegerField(help_text='承诺时的月份')
    deadline_season = models.IntegerField(help_text='履约截止月份')
    context = models.JSONField(default=dict, blank=True, help_text='承诺上下文参数')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'promises'
        indexes = [
            models.Index(fields=['game', 'status']),
            models.Index(fields=['game', 'agent']),
        ]

    def __str__(self):
        return f"Promise #{self.id} [{self.get_promise_type_display()}] G#{self.game_id} ({self.get_status_display()})"


class NeighborCounty(models.Model):
    """邻县 — AI知县治理的县"""
    STYLE_CHOICES = [
        ('minben', '民本型'),
        ('zhengji', '政绩型'),
        ('baoshou', '保守型'),
        ('jinqu', '进取型'),
        ('yuanhua', '圆滑型'),
    ]
    ARCHETYPE_CHOICES = [
        ('VIRTUOUS', '循吏型'),
        ('MIDDLING', '中庸守成型'),
        ('CORRUPT', '贪酷恶劣型'),
    ]

    game = models.ForeignKey(GameState, on_delete=models.CASCADE, related_name='neighbors')
    county_name = models.CharField(max_length=100, help_text='邻县名称')
    governor_name = models.CharField(max_length=50, help_text='AI知县姓名')
    governor_style = models.CharField(max_length=20, choices=STYLE_CHOICES, help_text='施政风格')
    governor_archetype = models.CharField(
        max_length=10, choices=ARCHETYPE_CHOICES, default='MIDDLING',
        help_text='知县施政类型（循吏/中庸/贪酷）'
    )
    governor_bio = models.TextField(blank=True, default='', help_text='知县人设描述')
    county_data = models.JSONField(default=dict, help_text='同玩家county_data结构')
    last_reasoning = models.TextField(blank=True, default='', help_text='上月LLM决策reasoning')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'neighbor_counties'
        indexes = [
            models.Index(fields=['game']),
        ]

    def __str__(self):
        return f"{self.county_name} ({self.governor_name}) - Game#{self.game_id}"


class NeighborEventLog(models.Model):
    """邻县事件记录"""
    CATEGORY_CHOICES = [
        ('SETTLEMENT', '结算'),
        ('DISASTER', '灾害'),
        ('INVESTMENT', '投资'),
        ('TAX', '税率'),
        ('AI_DECISION', 'AI决策'),
    ]

    neighbor_county = models.ForeignKey(
        NeighborCounty, on_delete=models.CASCADE, related_name='event_logs',
    )
    season = models.IntegerField(help_text='触发月份')
    event_type = models.CharField(max_length=100, help_text='事件类型')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='SETTLEMENT')
    description = models.TextField(blank=True, default='')
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'neighbor_event_logs'
        indexes = [
            models.Index(fields=['neighbor_county', 'season']),
        ]

    def __str__(self):
        return f"Neighbor#{self.neighbor_county_id} S{self.season}: [{self.category}] {self.event_type}"


class NeighborPrecompute(models.Model):
    """邻县AI决策预计算结果（持久化到DB，替代Redis缓存）"""
    STATUS_CHOICES = [
        ('computing', '计算中'),
        ('done', '已完成'),
    ]

    game = models.OneToOneField(GameState, on_delete=models.CASCADE,
                                related_name='neighbor_precompute')
    season = models.IntegerField(help_text='预计算对应的月份')
    results = models.JSONField(default=dict, help_text='各邻县AI决策结果')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='computing')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'neighbor_precomputes'

    def __str__(self):
        return f"Precompute Game#{self.game_id} S{self.season} [{self.status}]"


class MonarchProfile(models.Model):
    """君主档案 — 每局游戏一个，决定全局政治气候"""
    ARCHETYPE_CHOICES = [
        ('diligent', '勤政型'),
        ('delegating', '怠政型'),
        ('moderate', '中庸型'),
    ]

    game = models.OneToOneField(GameState, on_delete=models.CASCADE,
                                related_name='monarch')
    agent = models.OneToOneField(Agent, on_delete=models.SET_NULL,
                                 related_name='monarch_profile',
                                 null=True, blank=True,
                                 help_text='关联的皇帝Agent')
    archetype = models.CharField(max_length=20, choices=ARCHETYPE_CHOICES,
                                 help_text='君主原型')
    attributes = models.JSONField(default=dict,
                                  help_text='治国系数 (tax_pressure, corruption_risk等)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'monarch_profiles'

    def __str__(self):
        return f"Monarch Game#{self.game_id} ({self.get_archetype_display()})"


class Faction(models.Model):
    """朝廷派系"""
    game = models.ForeignKey(GameState, on_delete=models.CASCADE,
                             related_name='factions')
    name = models.CharField(max_length=50, help_text='派系名称')
    ideology = models.JSONField(default=dict,
                                help_text='派系意识形态 (state_vs_people等)')
    leader = models.ForeignKey(Agent, on_delete=models.SET_NULL,
                               null=True, blank=True,
                               related_name='led_factions',
                               help_text='派系领袖')
    imperial_favor = models.IntegerField(default=50,
                                         help_text='圣眷 (0-100)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'factions'
        indexes = [
            models.Index(fields=['game']),
        ]

    def __str__(self):
        return f"{self.name} (圣眷:{self.imperial_favor}) - Game#{self.game_id}"
