from rest_framework import serializers
from .models import EventLog, GameState, NegotiationSession, PlayerProfile, Promise


class CreateGameSerializer(serializers.Serializer):
    background = serializers.ChoiceField(
        choices=PlayerProfile.BACKGROUND_CHOICES,
        help_text="出身背景: HUMBLE/SCHOLAR/OFFICIAL",
    )


class InvestActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=[
            ("reclaim_land", "开垦荒地"),
            ("build_irrigation", "修建水利"),
            ("expand_school", "扩建县学"),
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


class PlayerProfileSerializer(serializers.ModelSerializer):
    background_display = serializers.CharField(
        source="get_background_display", read_only=True,
    )

    class Meta:
        model = PlayerProfile
        fields = [
            "background", "background_display",
            "knowledge", "skill",
            "integrity", "competence", "popularity",
        ]


class GameDetailSerializer(serializers.ModelSerializer):
    player = PlayerProfileSerializer(read_only=True)

    class Meta:
        model = GameState
        fields = [
            "id", "current_season", "county_data",
            "player", "created_at", "updated_at",
        ]


class GameListSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameState
        fields = ["id", "current_season", "created_at", "updated_at"]


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


class MedicalLevelSerializer(serializers.Serializer):
    medical_level = serializers.IntegerField(
        min_value=0,
        max_value=3,
        help_text="医疗等级 (0-3)",
    )


class StartIrrigationSerializer(serializers.Serializer):
    village_name = serializers.CharField(
        max_length=50,
        help_text="目标村庄名称",
    )


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
