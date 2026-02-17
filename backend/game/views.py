from django.contrib.auth import authenticate, login, logout
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent, EventLog, GameState, NegotiationSession, PlayerProfile, Promise
from .serializers import (
    ChatMessageSerializer,
    CreateGameSerializer,
    EventLogSerializer,
    GameDetailSerializer,
    GameListSerializer,
    InvestActionSerializer,
    MedicalLevelSerializer,
    NegotiationChatSerializer,
    NegotiationSessionSerializer,
    PromiseSerializer,
    StartIrrigationSerializer,
    TaxRateSerializer,
)
from .agent_service import AgentService
from .negotiation_service import NegotiationService
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
        import copy

        serializer = CreateGameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        background = serializer.validated_data["background"]

        # Create game with initial county data
        county_data = CountyService.create_initial_county()
        # Store initial village snapshot for delta display
        county_data['initial_villages'] = copy.deepcopy(county_data['villages'])

        game = GameState.objects.create(
            user=request.user,
            current_season=1,
            county_data=county_data,
        )

        # Create player profile with background-specific defaults
        defaults = PlayerProfile.BACKGROUND_DEFAULTS[background]
        PlayerProfile.objects.create(
            game=game,
            background=background,
            knowledge=defaults["knowledge"],
            skill=defaults["skill"],
        )

        # Initialize NPC agents
        AgentService.initialize_agents(game)

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

        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='tax_rate_change',
            category='TAX',
            description=message,
            data={'old_rate': old_rate, 'new_rate': new_rate},
        )

        return Response({
            "tax_rate": new_rate,
            "message": message,
        })


class MedicalLevelView(APIView):
    """
    POST /api/games/{id}/medical-level/  — set medical level (0-3)
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

        serializer = MedicalLevelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from .services import MEDICAL_COSTS, MEDICAL_NAMES

        new_level = serializer.validated_data["medical_level"]
        county = game.county_data
        old_level = county.get("medical_level", 0)
        county["medical_level"] = new_level
        game.county_data = county
        game.save()

        cost = MEDICAL_COSTS.get(new_level, 0)
        name = MEDICAL_NAMES.get(new_level, "无")
        message = f"医疗等级调整为{new_level}级（{name}），年度开支{cost}两"

        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='medical_level_change',
            category='INVESTMENT',
            description=message,
            data={'old_level': old_level, 'new_level': new_level, 'annual_cost': cost},
        )

        return Response({
            "medical_level": new_level,
            "medical_name": name,
            "annual_cost": cost,
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


class AgentListView(APIView):
    """
    GET /api/games/{id}/agents/  — list all NPCs in this game
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        agents = AgentService.get_agents_list(game)
        return Response(agents)


class AgentChatView(APIView):
    """
    POST /api/games/{id}/agents/{agent_id}/chat/  — send message to NPC
    GET  /api/games/{id}/agents/{agent_id}/chat/  — get dialogue history
    """
    permission_classes = [IsAuthenticated]

    def _get_game_and_agent(self, request, game_id, agent_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return None, None, Response(
                {"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND,
            )
        try:
            agent = Agent.objects.get(id=agent_id, game=game)
        except Agent.DoesNotExist:
            return None, None, Response(
                {"error": "该NPC不存在"}, status=status.HTTP_404_NOT_FOUND,
            )
        return game, agent, None

    def get(self, request, game_id, agent_id):
        game, agent, err = self._get_game_and_agent(request, game_id, agent_id)
        if err:
            return err

        history = AgentService.get_dialogue_history(game, agent)
        return Response({
            "agent_name": agent.name,
            "agent_role_title": agent.role_title,
            "messages": history,
        })

    def post(self, request, game_id, agent_id):
        game, agent, err = self._get_game_and_agent(request, game_id, agent_id)
        if err:
            return err

        serializer = ChatMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        player_message = serializer.validated_data["message"]
        result = AgentService.chat_with_agent(game, agent, player_message)

        return Response({
            "agent_name": agent.name,
            "agent_role_title": agent.role_title,
            "dialogue": result["dialogue"],
            "season": game.current_season,
        })


class ActiveNegotiationView(APIView):
    """
    GET /api/games/{id}/negotiations/active/  — get active negotiation
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        session = NegotiationService.get_active_negotiation(game)
        if session is None:
            return Response({"active": False, "session": None})

        serializer = NegotiationSessionSerializer(session)
        return Response({"active": True, "session": serializer.data})


class NegotiationChatView(APIView):
    """
    POST /api/games/{id}/negotiations/{session_id}/chat/  — send negotiation message
    GET  /api/games/{id}/negotiations/{session_id}/chat/  — get negotiation history
    """
    permission_classes = [IsAuthenticated]

    def _get_game_and_session(self, request, game_id, session_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return None, None, Response(
                {"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND,
            )
        try:
            session = NegotiationSession.objects.select_related('agent').get(
                id=session_id, game=game,
            )
        except NegotiationSession.DoesNotExist:
            return None, None, Response(
                {"error": "谈判会话不存在"}, status=status.HTTP_404_NOT_FOUND,
            )
        return game, session, None

    def get(self, request, game_id, session_id):
        game, session, err = self._get_game_and_session(request, game_id, session_id)
        if err:
            return err

        history = NegotiationService.get_negotiation_history(session)
        session_data = NegotiationSessionSerializer(session).data
        return Response({
            "session": session_data,
            "messages": history,
        })

    def post(self, request, game_id, session_id):
        game, session, err = self._get_game_and_session(request, game_id, session_id)
        if err:
            return err

        if session.status != 'active':
            return Response(
                {"error": "该谈判已结束"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = NegotiationChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        player_message = serializer.validated_data["message"]
        result = NegotiationService.negotiate_round(game, session, player_message)

        if 'error' in result:
            return Response(
                {"error": result["error"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result)


class EventLogListView(APIView):
    """
    GET /api/games/{id}/events/  — list event logs
    Query params: category, season, limit (default 50, max 200)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        qs = EventLog.objects.filter(game=game).order_by('-created_at')

        category = request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)

        season = request.query_params.get('season')
        if season:
            try:
                qs = qs.filter(season=int(season))
            except (ValueError, TypeError):
                pass

        limit = min(int(request.query_params.get('limit', 50)), 200)
        qs = qs[:limit]

        serializer = EventLogSerializer(qs, many=True)
        return Response(serializer.data)


class PromiseListView(APIView):
    """
    GET /api/games/{id}/promises/  — list promises
    Query params: status (PENDING/FULFILLED/BROKEN)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        qs = Promise.objects.filter(game=game).select_related('agent').order_by('-created_at')

        promise_status = request.query_params.get('status')
        if promise_status:
            qs = qs.filter(status=promise_status)

        serializer = PromiseSerializer(qs, many=True)
        return Response(serializer.data)


class StartIrrigationNegotiationView(APIView):
    """
    POST /api/games/{id}/negotiations/start-irrigation/  — start irrigation negotiation
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        serializer = StartIrrigationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        village_name = serializer.validated_data["village_name"]
        county = game.county_data

        # Validate active irrigation investment exists
        has_irrigation = any(
            inv.get('action') == 'build_irrigation'
            for inv in county.get('active_investments', [])
        )
        if not has_irrigation:
            return Response(
                {"error": "当前没有进行中的水利工程投资"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find village
        village = None
        for v in county.get('villages', []):
            if v['name'] == village_name:
                village = v
                break
        if village is None:
            return Response(
                {"error": f"村庄 '{village_name}' 不存在"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find gentry agent
        gentry = Agent.objects.filter(
            game=game,
            role='GENTRY',
            attributes__village_name=village_name,
        ).first()
        if gentry is None:
            return Response(
                {"error": f"'{village_name}' 没有对应的地主"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Calculate max_contribution (coeff reduced for ×4 farmland scale)
        max_contribution = min(
            int(village['farmland'] * village.get('gentry_land_pct', 0.3) * 0.0075),
            40,
        )
        max_contribution = max(1, max_contribution)

        context_data = {
            'village_name': village_name,
            'base_cost': 100,
            'max_contribution': max_contribution,
        }

        session, err = NegotiationService.start_negotiation(
            game, gentry, 'IRRIGATION', context_data,
        )
        if err:
            return Response(
                {"error": err},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            NegotiationSessionSerializer(session).data,
            status=status.HTTP_201_CREATED,
        )
