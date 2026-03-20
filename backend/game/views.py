from pathlib import Path

from django.contrib.auth import authenticate, login, logout
from django.db import OperationalError, ProgrammingError
from django.template.response import TemplateResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    AdminUnit, Agent, EventLog, GameState, NeighborCounty, NeighborEventLog,
    NegotiationSession, PlayerProfile, Promise,
)
from .serializers import (
    AnnualReviewSubmitSerializer,
    ChatMessageSerializer,
    CommercialTaxRateSerializer,
    CreateGameSerializer,
    EmergencyBorrowSerializer,
    EmergencyDebugToggleSerializer,
    EmergencyGrainAmountSerializer,
    EventLogSerializer,
    FeedbackSubmitSerializer,
    GameDetailSerializer,
    GameListSerializer,
    InvestActionSerializer,
    NeighborCountySummarySerializer,
    NeighborEventLogSerializer,
    NegotiationChatSerializer,
    NegotiationSessionSerializer,
    FactionSerializer,
    MonarchProfileSerializer,
    OfficialAgentSerializer,
    PromiseSerializer,
    StartIrrigationSerializer,
    TaxRateSerializer,
)
from .services import (
    AgentService, CountyService, InvestmentService,
    NegotiationService, NeighborService, OfficialdomService,
    SettlementService, EmergencyService, FeedbackService,
)
from .services.annual_review import AnnualReviewService
from .services.bribery import BriberyService
from .services.career_track import CareerTrackService
from .services.constants import MAX_MONTH
from .services.new_term import NewTermService, TERMINAL_REASONS
from .services.promotion_event import PromotionEventService
from .services.state import load_county_state, save_player_state
from .services.judicial_caseflow import JudicialCaseflowService
from .services.npc_debug import NPCDebugService
from .services.rumors import RumorsService
from .services.prefecture import PrefectureService


def _blocked_by_takeover(game):
    reason = EmergencyService.governance_block_reason(load_county_state(game))
    if not reason:
        return None
    return Response({"error": reason}, status=status.HTTP_400_BAD_REQUEST)


def _check_game_playable(game):
    """
    返回 Response(error) 若游戏不可继续操作，否则返回 None。
    替换各视图中重复的 current_season > MAX_MONTH 检查。
    """
    end_reason = load_county_state(game).get("term_end_reason")
    if end_reason in TERMINAL_REASONS:
        return Response({"error": "游戏已结束，请查看总结"}, status=status.HTTP_400_BAD_REQUEST)
    if game.current_season > MAX_MONTH:
        return Response(
            {"error": "任期已届满，请先续任", "term_complete": True},
            status=status.HTTP_400_BAD_REQUEST,
        )


def _judicial_debug_template_context(game, game_options=None, selected_game_id=None):
    debug_data = JudicialCaseflowService.get_debug_payload(game)
    pending_cases = []
    county_cases = []
    if game.player_role == "PREFECT" and game.player_unit_id:
        pending_cases = PrefectureService.get_judicial_cases(game).get("pending_cases") or []
    else:
        county_cases = JudicialCaseflowService.get_county_payload(game).get("cases") or []

    return {
        "game": game,
        "debug_data": debug_data,
        "generation": debug_data.get("generation") or {},
        "cases": debug_data.get("cases") or [],
        "status_summary": debug_data.get("status_summary") or {},
        "pending_cases": pending_cases,
        "county_cases": county_cases,
        "game_options": game_options or [],
        "selected_game_id": str(selected_game_id or game.id),
    }


def _judicial_error_response(exc):
    message = str(exc) or "司法系统暂不可用"
    if isinstance(exc, (OperationalError, ProgrammingError)):
        message = "司法系统数据库未初始化，请先执行迁移。"
    return Response({"error": message}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return None


def _ensure_staff(request):
    if getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False):
        return None
    return Response({"error": "仅后台管理员可访问该调试页面"}, status=status.HTTP_403_FORBIDDEN)


def _debug_game_label(game):
    if game.player_role == "PREFECT":
        title = (game.player_unit.unit_data or {}).get("prefecture_name") if game.player_unit_id else "知府局"
        role_label = "知府"
    else:
        title = load_county_state(game).get("county_name", "县局")
        role_label = "知县/知州"
    return f"#{game.id} · {role_label} · {title} · 第{game.current_season}月"


def _npc_debug_template_context(game, game_options=None, selected_game_id=None, selected_npc_key=None):
    npc_items = NPCDebugService.list_npcs(game)
    resolved_key = selected_npc_key
    if resolved_key:
        try:
            detail = NPCDebugService.get_npc_detail(game, resolved_key)
        except (TypeError, ValueError):
            detail = None
        if detail is None:
            resolved_key = None
    if not resolved_key and npc_items:
        resolved_key = npc_items[0]["npc_key"]
    if resolved_key:
        try:
            detail = NPCDebugService.get_npc_detail(game, resolved_key)
        except (TypeError, ValueError):
            detail = None
    else:
        detail = None

    return {
        "game": game,
        "game_options": game_options or [],
        "selected_game_id": str(selected_game_id or game.id),
        "npc_items": npc_items,
        "selected_npc_key": resolved_key or "",
        "selected_detail": detail,
        "npc_summary": {
            "total": len(npc_items),
            "agent_count": sum(1 for item in npc_items if item["npc_kind"] == "agent"),
            "neighbor_count": sum(1 for item in npc_items if item["npc_kind"] == "neighbor"),
            "subordinate_count": sum(1 for item in npc_items if item["npc_kind"] == "subordinate"),
        },
    }


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


class GameKnowledgeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        knowledge_path = Path(__file__).resolve().parent / "game_knowledge.md"
        markdown = knowledge_path.read_text(encoding="utf-8")
        title = "治县要略"
        for line in markdown.splitlines():
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip() or title
                break
        return Response({
            "title": title,
            "markdown": markdown,
        })


class GameFeedbackView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        serializer = FeedbackSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        feedback, delivered, error_message = FeedbackService.submit_feedback(
            game=game,
            user=request.user,
            content=serializer.validated_data["content"],
        )
        payload = {
            "id": feedback.id,
            "delivered": delivered,
            "message": "反馈已发送到飞书群" if delivered else "反馈已保存，但飞书通知发送失败",
        }
        if error_message:
            payload["warning"] = error_message
        return Response(payload, status=status.HTTP_201_CREATED)


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

        county_type = serializer.validated_data.get("county_type")

        # Create game with initial county data
        county_data = CountyService.create_initial_county(county_type=county_type)
        # 设置玩家县名（调任后旧县会成为邻县，需有名字）
        from .services.constants import PLAYER_COUNTY_NAMES
        county_data['county_name'] = PLAYER_COUNTY_NAMES.get(county_type, "本县")
        # Store initial village snapshot for delta display
        county_data['initial_villages'] = copy.deepcopy(county_data['villages'])
        # Store initial county-level snapshot for 任期述职 baseline
        county_data['initial_snapshot'] = {
            'treasury': county_data['treasury'],
            'morale': county_data['morale'],
            'security': county_data['security'],
            'commercial': county_data['commercial'],
            'education': county_data['education'],
            'tax_rate': county_data['tax_rate'],
            'commercial_tax_rate': county_data.get('commercial_tax_rate', 0.03),
            'school_level': county_data.get('school_level', 1),
            'irrigation_level': county_data.get('irrigation_level', 0),
            'medical_level': county_data.get('medical_level', 0),
            'admin_cost': county_data['admin_cost'],
            'peasant_grain_reserve': county_data.get('peasant_grain_reserve', 0),
        }

        game = GameState.objects.create(
            user=request.user,
            current_season=1,
            county_data=county_data,
        )

        # Create player profile (uniform starting point, no background differentiation)
        import random as _random
        PlayerProfile.objects.create(
            game=game,
            knowledge=3.0,
            skill=3.0,
            personal_wealth=round(_random.uniform(10, 30), 1),
        )

        # 创建玩家控制的行政单位（县级）
        _player_unit = AdminUnit.objects.create(
            game=game,
            unit_type='COUNTY',
            unit_data=county_data,
            is_player_controlled=True,
        )
        game.player_unit = _player_unit
        game.save(update_fields=['player_unit'])

        # Initialize NPC agents
        AgentService.initialize_agents(game)

        # Create AI-governed neighbor counties
        NeighborService.create_neighbors(game)

        # Initialize officialdom hierarchy (emperor, factions, officials)
        OfficialdomService.initialize_officialdom(game)

        JudicialCaseflowService.schedule_generation(game.id)

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


class AnnualReviewSubmitView(APIView):
    """
    POST /api/games/{id}/annual-review/  — 提交知县年度自陈
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        serializer = AnnualReviewSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = AnnualReviewService.submit_county_self_statement(
            game,
            serializer.validated_data,
        )
        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class CountyJudicialView(APIView):
    """
    GET /api/games/{id}/judicial/ — 县级司法 tab 数据
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(JudicialCaseflowService.get_county_payload(game))
        except Exception as exc:
            return _judicial_error_response(exc)


class CountyJudicialDecideView(APIView):
    """
    POST /api/games/{id}/judicial/decide/ — 处理县级司法案件
    Body: { "case_id": int, "action": str }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        case_id = request.data.get("case_id")
        action = (request.data.get("action") or "").strip()
        verdict_code = (request.data.get("verdict_code") or "").strip() or None
        if not case_id or not action:
            return Response({"error": "case_id 和 action 不能为空"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            case_id = int(case_id)
        except (TypeError, ValueError):
            return Response({"error": "case_id 必须为整数"}, status=status.HTTP_400_BAD_REQUEST)

        result = JudicialCaseflowService.decide_county_case(game, case_id, action, verdict_code=verdict_code)
        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class CountyJudicialDebugView(APIView):
    """
    GET /api/games/{id}/judicial/debug/ — 查看本局实例化卷宗调试数据
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(JudicialCaseflowService.get_debug_payload(game))
        except Exception as exc:
            return _judicial_error_response(exc)


class CountyJudicialDebugPageView(APIView):
    """
    GET /api/games/{id}/judicial/debug/page/ — 查看本局实例化卷宗调试页面
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)
        try:
            return TemplateResponse(request, "game/prefecture_judicial_debug.html", _judicial_debug_template_context(game))
        except Exception as exc:
            return _judicial_error_response(exc)


class JudicialDebugPageView(APIView):
    """
    GET /api/judicial/debug/page/ — 统一司法调试页，可直接选择游戏存档
    Query: ?game_id=<id>
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        games = list(GameState.objects.filter(user=request.user).select_related("player_unit").order_by("-updated_at"))
        selected_game = None
        requested_id = request.GET.get("game_id")
        if requested_id:
            try:
                selected_game = next((game for game in games if game.id == int(requested_id)), None)
            except (TypeError, ValueError):
                selected_game = None
        if selected_game is None and games:
            selected_game = games[0]

        game_options = []
        for game in games:
            if game.player_role == "PREFECT":
                title = (game.player_unit.unit_data or {}).get("prefecture_name") if game.player_unit_id else "知府局"
                role_label = "知府"
            else:
                title = load_county_state(game).get("county_name", "县局")
                role_label = "知县/知州"
            game_options.append({
                "id": str(game.id),
                "label": f"#{game.id} · {role_label} · {title} · 第{game.current_season}月",
            })

        if selected_game is None:
            return TemplateResponse(request, "game/prefecture_judicial_debug.html", {
                "game": None,
                "game_options": game_options,
                "selected_game_id": "",
                "generation": {},
                "cases": [],
                "status_summary": {},
                "pending_cases": [],
                "county_cases": [],
            })

        try:
            context = _judicial_debug_template_context(
                selected_game,
                game_options=game_options,
                selected_game_id=selected_game.id,
            )
            return TemplateResponse(request, "game/prefecture_judicial_debug.html", context)
        except Exception as exc:
            return _judicial_error_response(exc)


class NPCDebugListView(APIView):
    """
    GET /api/games/{id}/npc-debug/ — 某局 NPC 调试索引
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        denied = _ensure_staff(request)
        if denied:
            return denied

        game = GameState.objects.filter(id=game_id).first()
        if game is None:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "game_id": game.id,
            "items": NPCDebugService.list_npcs(game),
        })


class NPCDebugDetailView(APIView):
    """
    GET /api/games/{id}/npc-debug/detail/?npc_key=agent:1 — 某个 NPC 的完整调试详情
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        denied = _ensure_staff(request)
        if denied:
            return denied

        npc_key = request.GET.get("npc_key", "")
        if not npc_key:
            return Response({"error": "缺少 npc_key"}, status=status.HTTP_400_BAD_REQUEST)

        game = GameState.objects.filter(id=game_id).first()
        if game is None:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        try:
            detail = NPCDebugService.get_npc_detail(game, npc_key)
        except (TypeError, ValueError):
            detail = None
        if detail is None:
            return Response({"error": "NPC不存在"}, status=status.HTTP_404_NOT_FOUND)

        return Response(detail)


class NPCDebugPageView(APIView):
    """
    GET /api/npc/debug/page/ — 统一 NPC 调试页，可选择任意存档与 NPC
    Query: ?game_id=<id>&npc_key=<kind:id>
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _ensure_staff(request)
        if denied:
            return denied

        games = list(GameState.objects.filter().select_related("player_unit").order_by("-updated_at"))
        selected_game = None
        requested_id = request.GET.get("game_id")
        requested_npc_key = request.GET.get("npc_key", "")

        if requested_id:
            try:
                selected_game = next((game for game in games if game.id == int(requested_id)), None)
            except (TypeError, ValueError):
                selected_game = None
        if selected_game is None and games:
            selected_game = next((game for game in games if game.id == 120), None) or games[0]

        game_options = [{"id": str(game.id), "label": _debug_game_label(game)} for game in games]

        if selected_game is None:
            return TemplateResponse(request, "game/npc_debug.html", {
                "game": None,
                "game_options": game_options,
                "selected_game_id": "",
                "selected_npc_key": "",
                "npc_items": [],
                "selected_detail": None,
                "npc_summary": {},
            })

        context = _npc_debug_template_context(
            selected_game,
            game_options=game_options,
            selected_game_id=selected_game.id,
            selected_npc_key=requested_npc_key,
        )
        return TemplateResponse(request, "game/npc_debug.html", context)


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

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        serializer = InvestActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data["action"]
        target_village = serializer.validated_data.get("target_village")

        success, message = InvestmentService.execute(game, action, target_village)

        if success:
            county = load_county_state(game, refresh=True)
            return Response({
                "success": True,
                "message": message,
                "treasury": round(county["treasury"], 1),
            })
        return Response(
            {"success": False, "message": message},
            status=status.HTTP_400_BAD_REQUEST,
        )


class RequestLandSurveyView(APIView):
    """
    POST /api/games/{id}/land-survey/  — request land survey for a village
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        village_name = request.data.get("village_name")
        if not village_name:
            return Response({"error": "请指定村庄"}, status=status.HTTP_400_BAD_REQUEST)

        county = load_county_state(game)
        village_names = [v["name"] for v in county.get("villages", [])]
        if village_name not in village_names:
            return Response({"error": f"村庄 '{village_name}' 不存在"}, status=status.HTTP_400_BAD_REQUEST)

        surveys = county.setdefault("pending_land_surveys", [])
        if village_name not in surveys:
            surveys.append(village_name)
        save_player_state(game, county)

        return Response({"success": True, "message": f"已安排{village_name}土地勘查，结果将在下月报告中呈报"})


class CheckBribesView(APIView):
    """
    GET /api/games/{id}/check-bribes/
    结算前调用：扫描本月潜在贿赂事件，返回 pending_bribes 列表。
    同时重置 accepted_bribes，确保结算前状态干净。
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        county = load_county_state(game)
        monthly_surplus = SettlementService._estimate_monthly_surplus_per_capita(
            county, game.current_season
        )
        offers = BriberyService.check_county_bribes(county, monthly_surplus)
        save_player_state(game, county)

        return Response({"offers": offers})


class RespondBribeView(APIView):
    """
    POST /api/games/{id}/respond-bribe/
    玩家对单笔贿赂做出决定：accept=true/false。
    Body: {village_name, event_type, accept}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        village_name = request.data.get("village_name")
        event_type = request.data.get("event_type")
        accept = request.data.get("accept", False)

        if not village_name or event_type not in ("annexation", "hidden_land"):
            return Response({"error": "参数错误"}, status=status.HTTP_400_BAD_REQUEST)

        county = load_county_state(game)

        # Find the bribe in pending list and get its amount
        pending = county.get("pending_bribes", [])
        matched = next(
            (b for b in pending if b["village_name"] == village_name and b["event_type"] == event_type),
            None,
        )
        if not matched:
            return Response({"error": "未找到对应的行贿记录"}, status=status.HTTP_400_BAD_REQUEST)

        player_profile = PlayerProfile.objects.filter(game=game).first()

        if accept:
            BriberyService.accept_bribe(county, village_name, event_type, matched["amount"], player=player_profile)
            msg = f"收受{matched['gentry_name']}银两{matched['amount']}两，此事不予追究。"
        else:
            # 记录拒绝，确保结算时绕过随机概率门直接触发交涉
            from .services.bribery import bribe_key as _bk
            key = _bk(village_name, event_type)
            if 'rejected_bribes' not in county:
                county['rejected_bribes'] = {}
            county['rejected_bribes'][key] = True
            msg = f"拒绝{matched['gentry_name']}的行贿，将依法处置。"

        # 清名变化：接受贿赂−5，拒绝贿赂+1
        if player_profile:
            if accept:
                player_profile.integrity = max(0, player_profile.integrity - 5)
            else:
                player_profile.integrity = min(100, player_profile.integrity + 1)
            player_profile.save(update_fields=['integrity'])

        # Remove from pending list
        county["pending_bribes"] = [
            b for b in pending
            if not (b["village_name"] == village_name and b["event_type"] == event_type)
        ]

        save_player_state(game, county)

        personal_wealth = player_profile.personal_wealth if player_profile else None
        return Response({
            "success": True,
            "accepted": accept,
            "message": msg,
            "personal_wealth": personal_wealth,
        })


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

        blocked = _check_game_playable(game)
        if blocked is not None:
            return blocked

        blocker = AnnualReviewService.get_county_advance_blocker(game)
        if blocker:
            return Response({"error": blocker}, status=status.HTTP_400_BAD_REQUEST)

        judicial_blocker = JudicialCaseflowService.get_county_advance_blocker(game)
        if judicial_blocker:
            return Response({"error": judicial_blocker}, status=status.HTTP_400_BAD_REQUEST)

        season = game.current_season
        report = SettlementService.advance_season(game)

        # Advance neighbor counties (LLM decisions + settlement)
        try:
            NeighborService.advance_all(game, season)
        except Exception:
            import logging
            logging.getLogger('game').warning(
                "Neighbor advance failed (non-fatal)", exc_info=True)

        return Response(report)


class NeighborPrecomputeView(APIView):
    """
    POST /api/games/{id}/neighbors/precompute/  — 后台预计算邻县AI决策
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        import threading
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        if game.current_season > MAX_MONTH:
            return Response({"status": "game_over"})

        next_season = game.current_season
        threading.Thread(
            target=NeighborService.precompute_decisions,
            args=(game.id, next_season),
            daemon=True,
        ).start()

        return Response({"status": "started", "season": next_season},
                        status=status.HTTP_202_ACCEPTED)

    def get(self, request, game_id):
        """GET — 查询预计算进度"""
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        result = NeighborService.get_precompute_status(game.id, game.current_season)
        return Response(result)


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

        blocked = _check_game_playable(game)
        if blocked is not None:
            return blocked
        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        # 田赋税率仅八、九月可调
        month_of_year = ((game.current_season - 1) % 12) + 1
        if month_of_year not in (8, 9):
            return Response(
                {"error": "田赋税率仅可在八月、九月调整"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = TaxRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_rate = serializer.validated_data["tax_rate"]
        county = load_county_state(game)
        old_rate = county["tax_rate"]
        county["tax_rate"] = new_rate

        # Immediate morale effect: 1% tax change = ±3 morale
        rate_diff_pct = round((old_rate - new_rate) * 100)  # positive = tax decreased
        morale_delta = rate_diff_pct * 3
        old_morale = county["morale"]
        county["morale"] = max(0, min(100, county["morale"] + morale_delta))
        actual_morale_change = round(county["morale"] - old_morale, 1)

        # Propagate 50% to village morale
        if actual_morale_change != 0:
            for v in county["villages"]:
                v["morale"] = max(0, min(100, v["morale"] + actual_morale_change * 0.5))

        save_player_state(game, county)

        message = f"税率由{old_rate:.0%}调整为{new_rate:.0%}"
        if actual_morale_change != 0:
            sign = "+" if actual_morale_change > 0 else ""
            message += f"，民心{sign}{actual_morale_change:.0f}"

        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='tax_rate_change',
            category='TAX',
            description=message,
            data={
                'old_rate': old_rate,
                'new_rate': new_rate,
                'morale_change': actual_morale_change,
            },
        )

        return Response({
            "tax_rate": new_rate,
            "message": message,
            "morale": round(county["morale"], 1),
            "morale_change": actual_morale_change,
        })


class CommercialTaxRateView(APIView):
    """
    POST /api/games/{id}/commercial-tax-rate/  — adjust commercial tax rate
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        blocked = _check_game_playable(game)
        if blocked is not None:
            return blocked
        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        serializer = CommercialTaxRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_rate = serializer.validated_data["commercial_tax_rate"]
        county = load_county_state(game)
        old_rate = county.get("commercial_tax_rate", 0.03)
        county["commercial_tax_rate"] = new_rate

        # Morale effect: every 0.5% change → ±1 morale (milder than agri tax)
        morale_delta = round((old_rate - new_rate) * 100 / 0.5) * 1
        old_morale = county["morale"]
        county["morale"] = max(0, min(100, county["morale"] + morale_delta))
        actual_morale_change = round(county["morale"] - old_morale, 1)

        # Propagate 50% to village morale
        if actual_morale_change != 0:
            for v in county["villages"]:
                v["morale"] = max(0, min(100, v["morale"] + actual_morale_change * 0.5))

        save_player_state(game, county)

        message = f"商税税率由{old_rate:.1%}调整为{new_rate:.1%}"
        if actual_morale_change != 0:
            sign = "+" if actual_morale_change > 0 else ""
            message += f"，民心{sign}{actual_morale_change:.0f}"

        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='commercial_tax_rate_change',
            category='TAX',
            description=message,
            data={
                'old_rate': old_rate,
                'new_rate': new_rate,
                'morale_change': actual_morale_change,
            },
        )

        return Response({
            "commercial_tax_rate": new_rate,
            "message": message,
            "morale": round(county["morale"], 1),
            "morale_change": actual_morale_change,
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
                {"error": f"游戏尚未结束（当前第{game.current_season}月）"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(summary)


class GameSummaryV2View(APIView):
    """
    GET /api/games/{id}/summary-v2/  — enriched end-game report
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        summary = SettlementService.get_summary_v2(game)
        if summary is None:
            return Response(
                {"error": f"游戏尚未结束（当前第{game.current_season}月）"},
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


class StaffInfoView(APIView):
    """
    GET /api/games/{id}/staff/  — get staff (幕僚) info
    """
    permission_classes = [IsAuthenticated]

    LIUFANG = [
        {"name": "吏房", "desc": "掌管官吏考核、任免文书"},
        {"name": "户房", "desc": "掌管户籍、田赋、钱粮征收"},
        {"name": "礼房", "desc": "掌管科举、祭祀、教化"},
        {"name": "兵房", "desc": "掌管兵丁、驿站、治安巡防"},
        {"name": "刑房", "desc": "掌管刑狱、诉讼、缉捕"},
        {"name": "工房", "desc": "掌管营建、水利、工匠"},
    ]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        county = load_county_state(game)
        advisor_level = county.get("advisor_level", 1)

        # Advisor (师爷)
        advisor_data = None
        try:
            advisor = Agent.objects.get(game=game, role='ADVISOR')
            advisor_data = {
                "agent_id": advisor.id,
                "name": advisor.name,
                "role_title": advisor.role_title,
                "level": advisor_level,
                "questions_used": county.get("advisor_questions_used", 0),
                "questions_limit": advisor_level,
                "bio": advisor.attributes.get("bio", ""),
                "affinity": advisor.attributes.get("player_affinity", 50),
            }
        except Agent.DoesNotExist:
            pass

        # Deputy (县丞)
        deputy_data = None
        try:
            deputy = Agent.objects.get(game=game, role='DEPUTY')
            deputy_data = {
                "agent_id": deputy.id,
                "name": deputy.name,
                "role_title": deputy.role_title,
                "bio": deputy.attributes.get("bio", ""),
                "affinity": deputy.attributes.get("player_affinity", 50),
            }
        except Agent.DoesNotExist:
            pass

        # Bailiffs (衙役)
        bailiff_level = county.get("bailiff_level", 0)
        bailiff_data = {
            "level": bailiff_level,
            "count": 4 + 4 * bailiff_level,
            "max_level": 3,
            "base_count": 4,
        }

        return Response({
            "advisor": advisor_data,
            "deputy": deputy_data,
            "bailiffs": bailiff_data,
            "liufang": self.LIUFANG,
        })


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

        if 'error' in result:
            return Response(
                {"error": result["error"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

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


class ActiveNegotiationsListView(APIView):
    """
    GET /api/games/{id}/negotiations/active-list/  — get all active negotiations
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        NegotiationService.expire_stale_negotiations(game, current_season=game.current_season)
        sessions = NegotiationSession.objects.filter(
            game=game, status='active',
        ).select_related('agent').order_by('id')
        serializer = NegotiationSessionSerializer(sessions, many=True)
        return Response({"negotiations": serializer.data})


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

        NegotiationService.expire_stale_negotiations(game, current_season=game.current_season)
        session.refresh_from_db()
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

        NegotiationService.expire_stale_negotiations(game, current_season=game.current_season)
        session.refresh_from_db()
        if session.status != 'active':
            return Response(
                {"error": "该谈判已结束"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = NegotiationChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        player_message = serializer.validated_data["message"]
        speaker_role = serializer.validated_data.get("speaker_role", "PLAYER")
        result = NegotiationService.negotiate_round(
            game, session, player_message, speaker_role=speaker_role,
        )

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

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        serializer = StartIrrigationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        village_name = serializer.validated_data["village_name"]
        county = load_county_state(game)

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


class PrefectureOverviewForCountyView(APIView):
    """
    GET /api/games/{id}/prefecture-overview/  — 知县视角下的府情和府志

    返回 AI 知府的基本信息（府情）和本局 EventLog（府志），
    供邻县 Tab 的「本府概览」卡片使用。
    """
    permission_classes = [IsAuthenticated]

    _AFFINITY_LABELS = [
        (80, "极为赏识", "#1a7a4a"),
        (65, "颇为赏识", "#27ae60"),
        (50, "尚可",     "#6b5d45"),
        (35, "颇有微词", "#c0702a"),
        (20, "甚为不满", "#c0392b"),
        (0,  "深恶痛绝", "#7b241c"),
    ]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        from .models import Agent, EventLog as EL
        from .services.constants import month_of_year, year_of

        prefect = Agent.objects.filter(game=game, role='PREFECT').first()
        county = game.get_unit_data()

        # 府情
        if prefect:
            attrs = prefect.attributes
            prefecture_name = attrs.get('prefecture', '本府')
            prefect_name = prefect.name
            prefect_title = prefect.role_title or f'{prefecture_name}知府'
            affinity = attrs.get('player_affinity', county.get('prefect_affinity', 50))
        else:
            prefecture_name = county.get('prefecture_name', '本府')
            prefect_name = county.get('prefect_name', '知府')
            prefect_title = f'{prefecture_name}知府'
            attrs = {}
            affinity = county.get('prefect_affinity', 50)

        affinity_label, affinity_color = "尚可", "#6b5d45"
        for threshold, label, color in self._AFFINITY_LABELS:
            if affinity >= threshold:
                affinity_label, affinity_color = label, color
                break

        quota = county.get('annual_quota', {})
        fy = county.get('fiscal_year', {})
        agri_quota = quota.get('agri', quota.get('total', 0) * 0.7) if quota.get('total') else 0
        agri_remitted = fy.get('agri_remitted', 0)
        completion_pct = round(agri_remitted / agri_quota * 100) if agri_quota > 0 else 0
        moy = month_of_year(game.current_season)
        expected_pct = round(moy / 12 * 100)
        if completion_pct >= expected_pct - 5:
            quota_status = '进度正常'
        elif completion_pct < expected_pct - 20:
            quota_status = '严重滞后'
        else:
            quota_status = '略有滞后'

        # 待处理指令（未回复）
        all_dirs = county.get('prefect_directives', [])
        pending_dirs = [d for d in all_dirs if not d.get('responded', False)]

        # 府志：只取知府相关事件（PREFECT 类别），最近 60 条，按时间倒序
        CATEGORY_DISPLAY = {'PREFECT': '知府'}
        logs = EL.objects.filter(game=game, category='PREFECT').order_by('-season', '-created_at')[:60]
        gazette_entries = [
            {
                'season': e.season,
                'year': year_of(e.season),
                'month': month_of_year(e.season),
                'category': e.category,
                'category_display': CATEGORY_DISPLAY.get(e.category, e.category),
                'event_type': e.event_type,
                'description': e.description,
            }
            for e in logs
        ]

        return Response({
            'prefecture_name': prefecture_name,
            'prefect_name': prefect_name,
            'prefect_title': prefect_title,
            'affinity': affinity,
            'affinity_label': affinity_label,
            'affinity_color': affinity_color,
            'bio': attrs.get('bio', ''),
            'inspection_pending': county.get('prefect_inspection_pending', False),
            'quota_progress': {
                'agri_quota': round(agri_quota),
                'agri_remitted': round(agri_remitted),
                'completion_pct': completion_pct,
                'expected_pct': expected_pct,
                'status': quota_status,
            },
            'pending_directives': pending_dirs,
            'gazette_entries': gazette_entries,
        })


class NeighborListView(APIView):
    """
    GET /api/games/{id}/neighbors/  — list neighbor counties
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        neighbors = NeighborCounty.objects.filter(game=game).order_by('id')
        serializer = NeighborCountySummarySerializer(neighbors, many=True)
        return Response(serializer.data)


class NeighborDetailView(APIView):
    """
    GET /api/games/{id}/neighbors/{nid}/  — neighbor county detail
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id, neighbor_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        try:
            neighbor = NeighborCounty.objects.get(id=neighbor_id, game=game)
        except NeighborCounty.DoesNotExist:
            return Response({"error": "邻县不存在"}, status=status.HTTP_404_NOT_FOUND)

        serializer = NeighborCountySummarySerializer(neighbor)
        return Response(serializer.data)


class NeighborEventsView(APIView):
    """
    GET /api/games/{id}/neighbors/{nid}/events/  — neighbor event logs
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id, neighbor_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        try:
            neighbor = NeighborCounty.objects.get(id=neighbor_id, game=game)
        except NeighborCounty.DoesNotExist:
            return Response({"error": "邻县不存在"}, status=status.HTTP_404_NOT_FOUND)

        qs = NeighborEventLog.objects.filter(
            neighbor_county=neighbor,
        ).order_by('-created_at')

        limit = min(int(request.query_params.get('limit', 50)), 200)
        qs = qs[:limit]

        serializer = NeighborEventLogSerializer(qs, many=True)
        return Response(serializer.data)


class NeighborSummaryV2View(APIView):
    """
    GET /api/games/{id}/neighbors/{nid}/summary-v2/  — on-demand neighbor term report
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id, neighbor_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        try:
            neighbor = NeighborCounty.objects.get(id=neighbor_id, game=game)
        except NeighborCounty.DoesNotExist:
            return Response({"error": "邻县不存在"}, status=status.HTTP_404_NOT_FOUND)

        summary = SettlementService.get_neighbor_summary_v2(game, neighbor)
        if summary is None:
            return Response(
                {"error": f"游戏尚未结束（当前第{game.current_season}月）"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(summary)


class OfficialdomView(APIView):
    """
    GET /api/games/{id}/officialdom/  — 官场层级数据
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        data = OfficialdomService.get_officialdom(game)
        if data is None:
            return Response({
                "available": False,
                "message": "本局游戏尚未生成官场数据",
            })

        monarch_profile = data['monarch_profile']
        emperor = data['emperor']

        # 序列化省份数据
        provinces_data = {}
        for prov_name, prov_info in data.get('provinces', {}).items():
            provinces_data[prov_name] = {
                'governor': OfficialAgentSerializer(prov_info['governor']).data if prov_info['governor'] else None,
                'commissioners': OfficialAgentSerializer(prov_info['commissioners'], many=True).data,
                'prefects': OfficialAgentSerializer(prov_info['prefects'], many=True).data,
            }

        result = {
            "available": True,
            "monarch": {
                "archetype": monarch_profile.archetype,
                "archetype_display": monarch_profile.get_archetype_display(),
                "agent": OfficialAgentSerializer(emperor).data if emperor else None,
                "gameplay_attributes": monarch_profile.attributes,
            },
            "cabinet": OfficialAgentSerializer(data['cabinet'], many=True).data,
            "ministries": {
                name: OfficialAgentSerializer(agents, many=True).data
                for name, agents in data['ministries'].items()
            },
            "censorate": OfficialAgentSerializer(data['censorate'], many=True).data,
            "provinces": provinces_data,
            "player_province": data.get('player_province', ''),
            "factions": FactionSerializer(data['factions'], many=True).data,
        }
        return Response(result)


class DisasterReliefView(APIView):
    """
    POST /api/games/{id}/disaster-relief/  — 提交灾害减免申请（九月提交，十月批示）

    Body: { "claimed_loss": <两> }
      claimed_loss: 玩家申报的秋税上缴减免额度（两）。
      仅九月可提交，每年一次；十月统一给出批示并执行秋税扣减。
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        claimed_loss = request.data.get("claimed_loss")
        if claimed_loss is None:
            return Response(
                {"error": "请提供申请减免的灾损数额（claimed_loss，单位：两）"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            claimed_loss = float(claimed_loss)
        except (TypeError, ValueError):
            return Response(
                {"error": "claimed_loss 必须为数字"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = SettlementService.process_disaster_relief(game, claimed_loss)
        if result.get("success") is False and "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class AdjustRemitRatioView(APIView):
    """POST /api/games/{id}/remit-ratio/ — 调整本县上缴比例（九月核定后专用）"""

    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        new_ratio = request.data.get("remit_ratio")
        if new_ratio is None:
            return Response({"error": "缺少 remit_ratio 参数"}, status=status.HTTP_400_BAD_REQUEST)

        result = SettlementService.adjust_remit_ratio(game, new_ratio)
        if not result.get("success"):
            return Response({"error": result.get("error")}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            **result,
            "autumn_tax_assessment": load_county_state(game).get("autumn_tax_assessment", {}),
        })


class EmergencyPrefectureReliefView(APIView):
    """POST /api/games/{id}/emergency/prefecture-relief/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        result = EmergencyService.request_prefecture_relief(game)
        if result.get("success") is False:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class EmergencyBorrowNeighborView(APIView):
    """POST /api/games/{id}/emergency/borrow-neighbor/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        serializer = EmergencyBorrowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = EmergencyService.borrow_from_neighbor(
            game,
            neighbor_id=serializer.validated_data["neighbor_id"],
            amount=serializer.validated_data["amount"],
        )
        if result.get("success") is False:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class EmergencyGentryReliefView(APIView):
    """POST /api/games/{id}/emergency/gentry-relief/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        serializer = EmergencyGrainAmountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = EmergencyService.negotiate_gentry_relief(
            game,
            requested_amount=serializer.validated_data["amount"],
        )
        if result.get("success") is False:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class EmergencyForceLevyView(APIView):
    """POST /api/games/{id}/emergency/force-levy/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        serializer = EmergencyGrainAmountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = EmergencyService.force_levy_gentry(
            game,
            amount=serializer.validated_data["amount"],
        )
        if result.get("success") is False:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class EmergencyDebugToggleView(APIView):
    """POST /api/games/{id}/emergency/debug-toggle/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        serializer = EmergencyDebugToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = EmergencyService.set_debug_reveal(
            game,
            enabled=serializer.validated_data["enabled"],
        )
        return Response(result)


class CareerView(APIView):
    """
    GET /api/games/{id}/career/  — 知县仕途轨迹数据
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        if game.player_role != "COUNTY_MAGISTRATE":
            return Response({"error": "仅知县模式支持仕途轨迹"}, status=status.HTTP_400_BAD_REQUEST)

        data = CareerTrackService.get_career_payload(game)
        return Response(data)


class PromotionActionView(APIView):
    """
    POST /api/games/{id}/promotion-action/
    sub_action: 'reveal_advisor'
    action_type: 'gift_governor' | 'gift_ministry' | 'gift_both' | 'none'
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        if game.player_role != "COUNTY_MAGISTRATE":
            return Response({"error": "仅知县模式支持升迁操作"}, status=status.HTTP_400_BAD_REQUEST)

        county = load_county_state(game)
        sub_action = request.data.get("sub_action")
        action_type = request.data.get("action_type")

        if sub_action == "reveal_advisor":
            result = PromotionEventService.reveal_advisor_tip(game, county)
            if result.get("error"):
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
            return Response(result)

        if action_type in ("gift_governor", "gift_ministry", "gift_both", "none"):
            result = PromotionEventService.apply_player_action(game, county, action_type)
            if result.get("error"):
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
            return Response(result)

        return Response({"error": "无效操作"}, status=status.HTTP_400_BAD_REQUEST)


class NewTermView(APIView):
    """
    POST /api/games/{id}/new-term/  — 任期届满后续任（留任 or 调任）
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        choice = request.data.get("choice", "transfer")  # transfer | stay | retire
        result = NewTermService.start_new_term(game, choice=choice)
        if result.get("error"):
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        # 刷新 game 数据返回前端
        game.refresh_from_db()
        from .serializers import GameDetailSerializer
        return Response({
            "ok": True,
            "term_index": result["term_index"],
            "pool_level": result["pool_level"],
            "transfer_info": result.get("transfer_info"),
            "game": GameDetailSerializer(game).data,
        })


class CountyRumorsView(APIView):
    """
    GET /api/games/{id}/rumors/  — 流言板（民间舆情）
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        if game.player_role != "COUNTY_MAGISTRATE":
            return Response({"error": "仅知县模式支持流言板"}, status=status.HTTP_400_BAD_REQUEST)

        rumors = RumorsService.get_county_rumors(game)
        return Response({"rumors": rumors})


class EmergencyBuyGrainView(APIView):
    """POST /api/games/{id}/emergency/buy-grain/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        blocked = _blocked_by_takeover(game)
        if blocked is not None:
            return blocked

        serializer = EmergencyGrainAmountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = EmergencyService.buy_grain_from_treasury(
            game,
            amount_jin=serializer.validated_data["amount"],
        )
        if not result.get("success"):
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class AnnualReviewDraftView(APIView):
    """POST /api/games/{id}/annual-review/draft/  — 师爷代写年度自陈草稿"""

    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        try:
            game = GameState.objects.get(id=game_id, user=request.user)
        except GameState.DoesNotExist:
            return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

        if game.player_role != "COUNTY_MAGISTRATE":
            return Response({"error": "仅知县模式支持师爷代写"}, status=status.HTTP_400_BAD_REQUEST)

        draft = AnnualReviewService.generate_player_review_draft(game)
        return Response({"draft": draft})


class AdminPlayerStatsView(APIView):
    """GET /api/admin/player-stats/  — 管理员专用：玩家行为汇总数据"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return Response({"error": "权限不足"}, status=status.HTTP_403_FORBIDDEN)

        from django.contrib.auth.models import User
        from .models import UserLoginLog

        rows = []
        for u in User.objects.all().order_by('username'):
            login_logs = UserLoginLog.objects.filter(user=u)
            login_count = login_logs.count()
            ip_set = list(
                login_logs.values_list('ip_address', flat=True)
                .distinct()
                .order_by()
            )

            games = GameState.objects.filter(user=u)
            game_count = games.count()
            total_advances = sum(max(g.current_season - 1, 0) for g in games)

            closed = login_logs.filter(logged_out_at__isnull=False)
            online_minutes = sum(
                (log.logged_out_at - log.created_at).total_seconds() / 60
                for log in closed
            )

            recent_logins = [
                {
                    "ip": log.ip_address,
                    "time": log.created_at.strftime('%m-%d %H:%M'),
                    "duration": log.duration_minutes,
                }
                for log in login_logs.order_by('-created_at')[:10]
            ]

            rows.append({
                "username": u.username,
                "is_staff": u.is_staff,
                "login_count": login_count,
                "ip_count": len([ip for ip in ip_set if ip]),
                "ip_list": [ip for ip in ip_set if ip],
                "game_count": game_count,
                "total_advances": total_advances,
                "online_minutes": round(online_minutes, 1),
                "last_login": u.last_login.strftime('%Y-%m-%d %H:%M') if u.last_login else None,
                "recent_logins": recent_logins,
            })

        return Response({"players": rows})


class AdminPanelPageView(APIView):
    """GET /admin-panel/  — 管理员统计面板页面"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return Response({"error": "权限不足"}, status=status.HTTP_403_FORBIDDEN)
        return TemplateResponse(request, "game/admin_panel.html", {})
