from rest_framework import serializers
from .models import GameState, PlayerProfile


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
