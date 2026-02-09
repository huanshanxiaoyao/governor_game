from django.contrib.auth import authenticate, login, logout
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import GameState, PlayerProfile
from .serializers import (
    CreateGameSerializer,
    GameDetailSerializer,
    GameListSerializer,
    InvestActionSerializer,
    TaxRateSerializer,
)
from .services import CountyService, InvestmentService, SettlementService


class LoginView(APIView):
    permission_classes = []

    def post(self, request):
        user = authenticate(
            request,
            username=request.data.get("username"),
            password=request.data.get("password"),
        )
        if user:
            login(request, user)
            return Response({"username": user.username})
        return Response({"error": "用户名或密码错误"}, status=status.HTTP_400_BAD_REQUEST)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response({"message": "已登出"})


class GameListCreateView(APIView):
    """
    GET  /api/games/      — list my games
    POST /api/games/      — create new game
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        games = GameState.objects.filter(user=request.user).order_by("-updated_at")
        serializer = GameListSerializer(games, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CreateGameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        background = serializer.validated_data["background"]

        # Create game with initial county data
        game = GameState.objects.create(
            user=request.user,
            current_season=1,
            county_data=CountyService.create_initial_county(),
        )

        # Create player profile with background-specific defaults
        defaults = PlayerProfile.BACKGROUND_DEFAULTS[background]
        PlayerProfile.objects.create(
            game=game,
            background=background,
            knowledge=defaults["knowledge"],
            skill=defaults["skill"],
        )

        detail = GameDetailSerializer(game)
        return Response(detail.data, status=status.HTTP_201_CREATED)


class GameDetailView(APIView):
    """
    GET /api/games/{id}/  — game detail
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.select_related("player").get(
                id=game_id, user=request.user,
            )
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        serializer = GameDetailSerializer(game)
        return Response(serializer.data)


class InvestView(APIView):
    """
    POST /api/games/{id}/invest/  — execute investment action
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        serializer = InvestActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data["action"]
        target_village = serializer.validated_data.get("target_village")

        success, message = InvestmentService.execute(game, action, target_village)

        if success:
            return Response({
                "success": True,
                "message": message,
                "treasury": round(game.county_data["treasury"], 1),
            })
        return Response(
            {"success": False, "message": message},
            status=status.HTTP_400_BAD_REQUEST,
        )


class AdvanceSeasonView(APIView):
    """
    POST /api/games/{id}/advance/  — advance to next season
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        if game.current_season > 12:
            return Response(
                {"error": "游戏已结束，请查看总结"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        report = SettlementService.advance_season(game)
        return Response(report)


class TaxRateView(APIView):
    """
    POST /api/games/{id}/tax-rate/  — adjust tax rate
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        if game.current_season > 12:
            return Response(
                {"error": "游戏已结束"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = TaxRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_rate = serializer.validated_data["tax_rate"]
        county = game.county_data
        old_rate = county["tax_rate"]
        county["tax_rate"] = new_rate
        game.county_data = county
        game.save()

        message = f"税率由{old_rate:.0%}调整为{new_rate:.0%}"
        if new_rate > 0.12:
            message += "，较高的税率可能影响民心"

        return Response({
            "tax_rate": new_rate,
            "message": message,
        })


class GameSummaryView(APIView):
    """
    GET /api/games/{id}/summary/  — end-game summary
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        summary = SettlementService.get_summary(game)
        if summary is None:
            return Response(
                {"error": f"游戏尚未结束（当前第{game.current_season}季度）"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(summary)
