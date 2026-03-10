from rest_framework import serializers
from .models import (
    Agent, EventLog, Faction, GameState, MonarchProfile,
    NeighborCounty, NeighborEventLog,
    NegotiationSession, PlayerProfile, Promise,
)

COUNTY_TYPE_CHOICES = [
    ("fiscal_core", "财赋核心型"),
    ("clan_governance", "宗族治理型"),
    ("coastal", "沿海治理型"),
    ("disaster_prone", "黄淮灾荒型"),
]

PREFECTURE_TYPE_CHOICES = [
    ("fiscal_heavy", "财赋重府"),
    ("frontier_heavy", "边防要府"),
    ("balanced_inland", "均衡内陆"),
    ("remote_poor", "贫困边远"),
]


class CreateGameSerializer(serializers.Serializer):
    background = serializers.ChoiceField(
        choices=PlayerProfile.BACKGROUND_CHOICES,
        help_text="出身背景: HUMBLE/SCHOLAR/OFFICIAL",
    )
    county_type = serializers.ChoiceField(
        choices=COUNTY_TYPE_CHOICES,
        required=False,
        allow_null=True,
        default=None,
        help_text="县域类型（不传则随机）: fiscal_core/clan_governance/coastal/disaster_prone",
    )


class CreatePrefectureSerializer(serializers.Serializer):
    background = serializers.ChoiceField(
        choices=PlayerProfile.BACKGROUND_CHOICES,
        default='OFFICIAL',
        help_text="出身背景: HUMBLE/SCHOLAR/OFFICIAL",
    )
    prefecture_type = serializers.ChoiceField(
        choices=PREFECTURE_TYPE_CHOICES,
        required=False,
        allow_null=True,
        default=None,
        help_text="府域类型（不传则随机）: fiscal_heavy/frontier_heavy/balanced_inland/remote_poor",
    )


class InvestActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=[
            ("reclaim_land", "开垦荒地"),
            ("build_irrigation", "修建水利"),
            ("expand_school", "扩建县学"),
            ("build_medical", "建设医疗"),
            ("fund_village_school", "资助村塾"),
            ("hire_bailiffs", "增设衙役"),
            ("repair_roads", "修缮道路"),
            ("build_granary", "开设义仓"),
            ("relief", "赈灾救济"),
        ],
        help_text="投资类型",
    )
    target_village = serializers.CharField(
        required=False,
        allow_null=True,
        default=None,
        help_text="目标村庄名（开垦荒地/资助村塾时必填）",
    )


class TaxRateSerializer(serializers.Serializer):
    tax_rate = serializers.FloatField(
        min_value=0.09,
        max_value=0.15,
        help_text="税率 (9%~15%)",
    )


class CommercialTaxRateSerializer(serializers.Serializer):
    commercial_tax_rate = serializers.FloatField(
        min_value=0.01,
        max_value=0.05,
        help_text="商税税率 (1%~5%)",
    )


class PlayerProfileSerializer(serializers.ModelSerializer):
    background_display = serializers.CharField(
        source="get_background_display", read_only=True,
    )
    wealth_tier = serializers.SerializerMethodField()

    class Meta:
        model = PlayerProfile
        fields = [
            "background", "background_display",
            "knowledge", "skill",
            "integrity", "competence", "popularity",
            "personal_wealth", "wealth_tier",
        ]

    def get_wealth_tier(self, obj):
        w = obj.personal_wealth or 0
        if w < 50:
            return "清贫"
        if w < 200:
            return "小康"
        if w < 500:
            return "殷实"
        if w < 1000:
            return "富裕"
        return "巨富"


class GameDetailSerializer(serializers.ModelSerializer):
    player = PlayerProfileSerializer(read_only=True)
    available_investments = serializers.SerializerMethodField()
    disaster_relief_advice = serializers.SerializerMethodField()

    class Meta:
        model = GameState
        fields = [
            "id", "current_season", "player_role", "county_data",
            "player", "available_investments", "disaster_relief_advice",
            "created_at", "updated_at",
        ]

    def get_available_investments(self, obj):
        from .services import InvestmentService
        return InvestmentService.get_available_actions(obj.county_data, season=obj.current_season)

    def get_disaster_relief_advice(self, obj):
        from .services import SettlementService
        return SettlementService.compute_relief_advice(
            obj.county_data,
            season=obj.current_season,
        )


class GameListSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameState
        fields = ["id", "current_season", "player_role", "created_at", "updated_at"]


class ChatMessageSerializer(serializers.Serializer):
    message = serializers.CharField(
        max_length=500,
        help_text="玩家对NPC说的话",
    )


class NegotiationChatSerializer(serializers.Serializer):
    message = serializers.CharField(
        max_length=500,
        help_text="玩家的谈判发言",
    )
    speaker_role = serializers.ChoiceField(
        choices=[
            ("PLAYER", "县令亲谈"),
            ("ADVISOR", "委托师爷"),
            ("DEPUTY", "委托县丞"),
        ],
        required=False,
        default="PLAYER",
        help_text="本轮发言人：PLAYER/ADVISOR/DEPUTY",
    )


class StartIrrigationSerializer(serializers.Serializer):
    village_name = serializers.CharField(
        max_length=50,
        help_text="目标村庄名称",
    )


class EmergencyBorrowSerializer(serializers.Serializer):
    neighbor_id = serializers.IntegerField(min_value=1, help_text="邻县ID")
    amount = serializers.FloatField(min_value=1, help_text="借粮数量（斤）")


class EmergencyGrainAmountSerializer(serializers.Serializer):
    amount = serializers.FloatField(min_value=1, help_text="粮食数量（斤）")


class EmergencyDebugToggleSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(help_text="是否显式展示隐藏触发")


class EventLogSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(
        source='get_category_display', read_only=True,
    )

    class Meta:
        model = EventLog
        fields = [
            'id', 'season', 'event_type', 'category', 'category_display',
            'description', 'choice', 'data', 'created_at',
        ]


class PromiseSerializer(serializers.ModelSerializer):
    promise_type_display = serializers.CharField(
        source='get_promise_type_display', read_only=True,
    )
    status_display = serializers.CharField(
        source='get_status_display', read_only=True,
    )
    agent_name = serializers.CharField(source='agent.name', read_only=True)

    class Meta:
        model = Promise
        fields = [
            'id', 'promise_type', 'promise_type_display',
            'description', 'status', 'status_display',
            'season_made', 'deadline_season', 'context',
            'agent_name', 'created_at', 'resolved_at',
        ]


class NeighborCountySummarySerializer(serializers.ModelSerializer):
    governor_style_display = serializers.CharField(
        source='get_governor_style_display', read_only=True,
    )
    governor_archetype_display = serializers.CharField(
        source='get_governor_archetype_display', read_only=True,
    )
    county_type_name = serializers.SerializerMethodField()

    class Meta:
        model = NeighborCounty
        fields = [
            'id', 'county_name', 'governor_name', 'governor_style',
            'governor_style_display', 'governor_archetype', 'governor_archetype_display',
            'governor_bio', 'county_type_name',
            'county_data', 'last_reasoning',
        ]

    def get_county_type_name(self, obj):
        return obj.county_data.get('county_type_name', '')


class NeighborEventLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NeighborEventLog
        fields = [
            'id', 'season', 'event_type', 'category',
            'description', 'data', 'created_at',
        ]


class NegotiationSessionSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source='agent.name', read_only=True)
    agent_role_title = serializers.CharField(source='agent.role_title', read_only=True)
    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)

    class Meta:
        model = NegotiationSession
        fields = [
            'id', 'event_type', 'event_type_display', 'status',
            'current_round', 'max_rounds', 'season',
            'context_data', 'outcome',
            'agent_name', 'agent_role_title',
            'created_at', 'resolved_at',
        ]


# ── 官场体系 ──

class OfficialAgentSerializer(serializers.ModelSerializer):
    rank = serializers.SerializerMethodField()
    faction_name = serializers.SerializerMethodField()
    org = serializers.SerializerMethodField()
    province = serializers.SerializerMethodField()
    prefecture = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            'id', 'name', 'source_name', 'role', 'role_title',
            'rank', 'faction_name', 'org', 'province', 'prefecture',
        ]

    def get_rank(self, obj):
        return obj.attributes.get('rank')

    def get_faction_name(self, obj):
        return obj.attributes.get('faction_name')

    def get_org(self, obj):
        return obj.attributes.get('org')

    def get_province(self, obj):
        return obj.attributes.get('province', '')

    def get_prefecture(self, obj):
        return obj.attributes.get('prefecture', '')


class FactionSerializer(serializers.ModelSerializer):
    leader_name = serializers.CharField(source='leader.name', default='', read_only=True)
    leader_source_name = serializers.CharField(source='leader.source_name', default='', read_only=True)
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Faction
        fields = [
            'id', 'name', 'ideology', 'imperial_favor',
            'leader_name', 'leader_source_name', 'member_count',
        ]

    def get_member_count(self, obj):
        return Agent.objects.filter(
            game=obj.game,
            attributes__faction_name=obj.name,
        ).count()


class MonarchProfileSerializer(serializers.ModelSerializer):
    archetype_display = serializers.CharField(
        source='get_archetype_display', read_only=True
    )

    class Meta:
        model = MonarchProfile
        fields = ['archetype', 'archetype_display', 'attributes']
