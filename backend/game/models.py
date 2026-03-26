from django.db import models
from django.contrib.auth.models import User


class UserLoginLog(models.Model):
    """用户登录日志 — 记录每次 session 的登录/登出与在线时长"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='login_logs')
    session_key = models.CharField(max_length=40, null=True, blank=True, db_index=True, help_text='Django session key，用于精确匹配登出记录')
    ip_address = models.GenericIPAddressField(null=True, blank=True, help_text='客户端 IP')
    user_agent = models.CharField(max_length=300, blank=True, default='', help_text='浏览器 User-Agent')
    logged_out_at = models.DateTimeField(null=True, blank=True, help_text='登出时间（null = session 仍开着或异常断开）')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_login_logs'
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user.username} {self.ip_address} {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def duration_minutes(self):
        """在线时长（分钟），登出后才有值。"""
        if self.logged_out_at and self.created_at:
            return round((self.logged_out_at - self.created_at).total_seconds() / 60, 1)
        return None


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

    game = models.OneToOneField(GameState, on_delete=models.CASCADE, related_name='player')

    # 内在能力（隐藏，1.0-10.0，随游戏经历提升）
    knowledge = models.FloatField(default=3.0, help_text='知识：农耕/经济/地理，影响治理效果')
    skill = models.FloatField(default=3.0, help_text='技能：谈判/协调，影响谈判效果')

    # 声望四维（半隐藏，0-100，玩家看5档分级±偏差，行为驱动）
    integrity = models.IntegerField(default=50, help_text='清名：公正廉洁的口碑')
    competence = models.IntegerField(default=30, help_text='能名：干练能干的口碑')
    popularity = models.IntegerField(default=10, help_text='人缘：官场好相处的口碑')
    authority = models.IntegerField(default=40, help_text='威名：必要时强硬、令人敬畏的口碑')

    # 政治理念三维（半隐藏，0.0-1.0，从中立0.5出发，随行为漂移）
    state_vs_people = models.FloatField(default=0.5, help_text='社稷—黎民：0=优先百姓，1=优先国家指标')
    central_vs_local = models.FloatField(default=0.5, help_text='集权—分权：0=地方自主，1=恭顺中央')
    pragmatic_vs_ideal = models.FloatField(default=0.5, help_text='现实—理想：0=坚守原则，1=务实妥协')

    # 家产（个人财富，两）
    personal_wealth = models.FloatField(default=0.0, help_text='家产（两）：任内积累的个人财富，含合法薪俸与灰色所得')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'player_profiles'

    def __str__(self):
        return f"Player - Game#{self.game_id}"


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
        ('JUDICIAL', '司法'),
        ('LETTER', '书信'),
        ('BRIBERY', '行贿'),
        ('PROFILE', '声望'),
        ('DISASTER', '灾害'),
        ('SETTLEMENT', '结算'),
        ('ANNEXATION', '兼并'),
        ('PROMISE', '承诺'),
        ('PREFECT', '知府'),
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


class PlayerFeedback(models.Model):
    """玩家反馈记录"""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='game_feedbacks')
    game = models.ForeignKey(GameState, on_delete=models.CASCADE, related_name='feedbacks')
    content = models.TextField(help_text='玩家反馈内容')
    sent_to_feishu = models.BooleanField(default=False, help_text='是否已推送到飞书')
    feishu_error = models.CharField(max_length=300, blank=True, default='', help_text='飞书发送失败原因')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'player_feedbacks'
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['game', '-created_at']),
        ]

    def __str__(self):
        return f"Feedback#{self.id} User:{self.user_id} Game:{self.game_id}"


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
    attributes = models.JSONField(
        default=dict,
        help_text='AI知县属性：player_affinity(对玩家好感度, 0-99), memory(记忆列表, 最近20条)',
    )
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


class JudicialGenerationState(models.Model):
    """司法卷宗实例化后台任务状态。"""

    STATUS_CHOICES = [
        ('PENDING', '待生成'),
        ('RUNNING', '生成中'),
        ('READY', '已完成'),
        ('FAILED', '失败'),
    ]

    game = models.OneToOneField(
        GameState, on_delete=models.CASCADE, related_name='judicial_generation',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    total_windows = models.IntegerField(default=0)
    generated_windows = models.IntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'judicial_generation_states'

    def __str__(self):
        return f"JudicialGeneration Game#{self.game_id} [{self.status}]"


class JudicialCaseInstance(models.Model):
    """本局司法卷宗实例。"""

    STATUS_CHOICES = [
        ('PENDING_ASSISTANT_REVIEW', '待县丞意见'),
        ('PENDING_MAGISTRATE_ROUND_1', '待知县一审'),
        ('RETURNED_FOR_REVIEW', '打回重审'),
        ('PENDING_MAGISTRATE_ROUND_2', '待知县二审'),
        ('SUBMITTED_TO_PREFECT', '已上呈知府'),
        ('DEFERRED_TO_PREFECT', '委托知府裁定'),
        ('PREFECT_DECIDED', '知府已裁定'),
        ('WITHDRAWN_THIS_QUARTER', '本季度暂缓'),
    ]

    game = models.ForeignKey(
        GameState, on_delete=models.CASCADE, related_name='judicial_cases',
    )
    county_unit = models.ForeignKey(
        AdminUnit, null=True, blank=True, on_delete=models.CASCADE,
        related_name='judicial_cases',
    )
    prefect_unit = models.ForeignKey(
        AdminUnit, null=True, blank=True, on_delete=models.CASCADE,
        related_name='prefecture_judicial_cases',
    )
    template_case_id = models.CharField(max_length=50)
    county_review_season = models.IntegerField()
    prefect_review_season = models.IntegerField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING_MAGISTRATE_ROUND_1')
    local_payload = models.JSONField(default=dict)
    actor_map = models.JSONField(default=dict)
    assistant_rounds = models.JSONField(default=list)
    magistrate_rounds = models.JSONField(default=list)
    submitted_to_prefect = models.BooleanField(default=False)
    submitted_season = models.IntegerField(null=True, blank=True)
    prefect_decision = models.JSONField(default=dict, blank=True)
    debug_meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'judicial_case_instances'
        indexes = [
            models.Index(fields=['game', 'county_review_season']),
            models.Index(fields=['game', 'prefect_review_season']),
            models.Index(fields=['county_unit', 'status']),
            models.Index(fields=['prefect_unit', 'status']),
        ]

    def __str__(self):
        county_name = ''
        if self.county_unit_id:
            county_name = self.county_unit.unit_data.get('county_name', '')
        return f"JudicialCase {self.template_case_id} {county_name} S{self.county_review_season} [{self.status}]"


class ProposedPolicy(models.Model):
    """玩家自创施政选项 — 经省布政司审批后落地为可执行投资动作"""

    class Status(models.TextChoices):
        PENDING  = 'PENDING',  '待审核'
        APPROVED = 'APPROVED', '已批准'
        REJECTED = 'REJECTED', '已拒绝'
        ARCHIVED = 'ARCHIVED', '管理员归档（不采纳）'
        PROMOTED = 'PROMOTED', '已提升为标准选项'

    game         = models.ForeignKey(
        GameState, on_delete=models.CASCADE, related_name='proposed_policies',
    )
    proposer     = models.CharField(max_length=50, help_text='提案人：知县 / 师爷 / 县丞')
    raw_proposal = models.TextField(help_text='原始申请描述（玩家/NPC 的自然语言表述）')
    policy_name  = models.CharField(max_length=100, help_text='施政选项名称')
    action_key   = models.CharField(
        max_length=100, blank=True,
        help_text='布政使批准后生成的唯一动作标识，供 InvestmentService 识别',
    )
    status       = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
        db_index=True,
    )
    cost         = models.IntegerField(null=True, blank=True, help_text='批准花费（两），批复后填入')
    delay_months = models.IntegerField(null=True, blank=True, help_text='工期（月），批复后填入')
    effects_data = models.JSONField(
        default=dict,
        help_text=(
            '效果定义，格式：'
            '{"immediate": {"morale": 3}, "on_complete": {"commercial": 15}, '
            '"add_market": {"merchants": 10}, "description": "..."}'
        ),
    )
    rationale        = models.TextField(blank=True, help_text='布政使批复原文（批准或拒绝均填写）')
    rejection_reason = models.TextField(blank=True, help_text='拒绝原因（REJECTED 时填写）')
    synced_from      = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='synced_copies',
        help_text='邻县同步时指向原始申请记录，本县原始申请此字段为 null',
    )
    is_executed  = models.BooleanField(default=False, help_text='本县是否已执行过此选项')

    # ── 分级施政系统 (Phase 1) ──
    class CodeStatus(models.TextChoices):
        PENDING_DEV   = 'pending_dev',  '待开发'
        DEV_COMPLETE  = 'dev_complete', '开发完成'
        ACTIVATED     = 'activated',    '已激活'

    tier                 = models.IntegerField(
        default=1,
        help_text='施政分级：1=无需代码改动，2=需要新增后端代码',
    )
    code_status          = models.CharField(
        max_length=20, choices=CodeStatus.choices, null=True, blank=True,
        help_text='Tier 2 专用：开发状态（pending_dev/dev_complete/activated）',
    )
    activated_game_ids   = models.JSONField(
        default=list,
        help_text='Tier 2 已激活的 game_id 列表（仅对这些对局可执行）',
    )
    unsupported_effects  = models.JSONField(
        default=list,
        help_text='无法被现有引擎处理的 effect 键，批复时自动分析填入',
    )
    global_promotion     = models.BooleanField(
        default=False,
        help_text='Tier 2 激活后：是否推广为全局可用（对所有对局生效）',
    )
    is_synced_to_neighbors = models.BooleanField(
        default=False,
        help_text='Tier 1 批准后是否已同步至邻县',
    )

    created_at   = models.DateTimeField(auto_now_add=True)
    reviewed_at  = models.DateTimeField(null=True, blank=True, help_text='布政使批复时间')
    rejected_at  = models.DateTimeField(null=True, blank=True, help_text='拒绝时间，用于6个月冷却计算')

    class Meta:
        db_table = 'proposed_policies'
        indexes = [
            models.Index(fields=['game', 'status']),
            models.Index(fields=['game', 'policy_name']),
            models.Index(fields=['synced_from']),
            models.Index(fields=['tier', 'code_status']),
        ]

    def __str__(self):
        return f"ProposedPolicy#{self.id} 「{self.policy_name}」 [{self.get_status_display()}] Game#{self.game_id}"


class StandardPolicy(models.Model):
    """
    标准施政选项（数据库级）— 由管理员从 ProposedPolicy 提升而来。
    新对局的 InvestmentService 初始化时会合并此表与内置 INVESTMENT_TYPES。
    已有对局不受此表影响。
    """

    action_key       = models.CharField(
        max_length=100, unique=True,
        help_text='唯一动作标识，不可与内置 INVESTMENT_TYPES 键名重复',
    )
    policy_name      = models.CharField(max_length=100, help_text='施政选项名称（投资面板展示用）')
    cost_base        = models.IntegerField(help_text='基准花费（两），实际花费 = cost_base × price_index')
    delay_months     = models.IntegerField(help_text='工期（月）；0 表示即时生效')
    requires_village = models.BooleanField(default=False, help_text='是否需要指定目标村庄')
    effects_data     = models.JSONField(
        default=dict,
        help_text='效果定义，格式同 ProposedPolicy.effects_data',
    )
    description      = models.TextField(blank=True, help_text='施政选项简述，显示在投资面板')
    source_policy    = models.ForeignKey(
        ProposedPolicy, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='standard_versions',
        help_text='来源 ProposedPolicy，追溯用',
    )
    is_active        = models.BooleanField(
        default=True, db_index=True,
        help_text='False 时不出现在新对局中（管理员可临时停用）',
    )
    promoted_at  = models.DateTimeField(auto_now_add=True, help_text='提升时间')
    promoted_by  = models.CharField(max_length=100, help_text='操作管理员用户名')
    notes        = models.TextField(blank=True, help_text='管理员备注（平衡性说明等）')

    class Meta:
        db_table = 'standard_policies'
        indexes = [
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        status = '启用' if self.is_active else '停用'
        return f"StandardPolicy 「{self.policy_name}」 ({self.action_key}) [{status}]"


class Letter(models.Model):
    """书信 — 官场社交基础设施，支持知府指令、私信往来、公文传递"""

    class LetterType(models.TextChoices):
        OFFICIAL     = 'OFFICIAL',     '公文'
        PERSONAL     = 'PERSONAL',     '私信'
        MEMORIAL     = 'MEMORIAL',     '奏折'
        INTELLIGENCE = 'INTELLIGENCE', '情报'
        CIRCULAR     = 'CIRCULAR',     '檄文'

    class Confidentiality(models.TextChoices):
        PUBLIC   = 'PUBLIC',   '公开'
        PERSONAL = 'PERSONAL', '个人'
        SECRET   = 'SECRET',   '机密'
        BURN     = 'BURN',     '焚毁件'

    class Status(models.TextChoices):
        DRAFT      = 'DRAFT',      '草稿'
        IN_TRANSIT = 'IN_TRANSIT', '传递中'
        DELIVERED  = 'DELIVERED',  '已送达'
        READ       = 'READ',       '已读'
        REPLIED    = 'REPLIED',    '已回复'
        ARCHIVED   = 'ARCHIVED',   '已归档'
        BURNED     = 'BURNED',     '已焚毁'

    game = models.ForeignKey(
        GameState, on_delete=models.CASCADE, related_name='letters',
    )

    # 发件人（sender_agent=None 且 player_is_sender=True 表示玩家发送）
    sender_agent     = models.ForeignKey(
        'Agent', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='letters_sent',
    )
    player_is_sender = models.BooleanField(default=False)

    # 收件人
    recipient_agent     = models.ForeignKey(
        'Agent', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='letters_received',
    )
    player_is_recipient    = models.BooleanField(default=False)
    circular_recipient_ids = models.JSONField(default=list, blank=True,
                                              help_text='檄文专用：多收件NPC的Agent ID列表')

    # 内容
    letter_type     = models.CharField(max_length=20, choices=LetterType.choices,
                                       default=LetterType.OFFICIAL)
    subject         = models.CharField(max_length=200)
    body            = models.TextField()
    confidentiality = models.CharField(max_length=20, choices=Confidentiality.choices,
                                       default=Confidentiality.PERSONAL)

    # 时间
    sent_month      = models.IntegerField(help_text='发出时的游戏绝对月份')
    delivery_delay  = models.IntegerField(default=1, help_text='送达延迟（月）')
    delivered_month = models.IntegerField(help_text='sent_month + delivery_delay')

    # 回复机制
    requires_reply       = models.BooleanField(default=False)
    is_blocking          = models.BooleanField(default=False,
                                               help_text='True=超时未回复则硬阻断月份推进')
    reply_deadline_month = models.IntegerField(null=True, blank=True)
    reply_options        = models.JSONField(null=True, blank=True,
                                            help_text='None=自由回复; list=选择题选项')
    default_choice_id    = models.CharField(max_length=50, null=True, blank=True,
                                            help_text='软deadline超时自动选项')

    # 回复内容
    reply_body      = models.TextField(blank=True, default='')
    reply_choice_id = models.CharField(max_length=50, blank=True, default='')
    replied_month   = models.IntegerField(null=True, blank=True)

    # 状态
    status         = models.CharField(max_length=20, choices=Status.choices,
                                      default=Status.IN_TRANSIT)
    read_at_month  = models.IntegerField(null=True, blank=True)

    # 往来线索
    parent_letter = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='thread_replies',
    )

    # LLM 元数据
    llm_generated      = models.BooleanField(default=False)
    generation_context = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'letters'
        ordering = ['-sent_month', '-created_at']
        indexes = [
            models.Index(fields=['game', 'status'], name='letters_game_status_idx'),
            models.Index(fields=['game', 'delivered_month'], name='letters_game_delivered_idx'),
            models.Index(fields=['game', 'is_blocking', 'reply_deadline_month'],
                         name='letters_blocking_idx'),
        ]

    def __str__(self):
        return f"Letter#{self.id} [{self.letter_type}] {self.subject[:30]}"
