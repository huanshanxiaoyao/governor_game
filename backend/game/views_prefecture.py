"""知府游戏 API 视图"""

import threading

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AdminUnit, GameState
from .services import PrefectureService
from .services.annual_review import AnnualReviewService
from .services.constants import month_of_year


def _get_prefect_game(request, game_id):
    """获取并验证知府游戏，返回 (game, error_response)"""
    try:
        game = GameState.objects.select_related('player_unit').get(
            id=game_id, user=request.user,
        )
    except GameState.DoesNotExist:
        return None, Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)
    if game.player_role != 'PREFECT':
        return None, Response({"error": "当前游戏非知府模式"}, status=status.HTTP_400_BAD_REQUEST)
    if not game.player_unit_id:
        return None, Response({"error": "府域数据未初始化"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return game, None


class PrefectureCreateView(APIView):
    """
    POST /api/prefecture/create/
    创建知府游戏（新游戏，独立于知县游戏）
    Body: { "prefecture_type": "balanced_inland" }  （可选，不传则随机）
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .serializers import CreatePrefectureSerializer
        from .services.magistrate_service import MagistrateService
        import random as _random

        ser = CreatePrefectureSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        prefecture_type = ser.validated_data.get('prefecture_type')

        # 创建底层 GameState（county_data 空，由府域接管）
        game = GameState.objects.create(
            user=request.user,
            current_season=1,
            county_data={},
            player_role='PREFECT',
        )

        # 创建玩家档案
        from .models import PlayerProfile
        background = ser.validated_data.get('background', 'OFFICIAL')
        defaults = PlayerProfile.BACKGROUND_DEFAULTS[background]
        WEALTH_START = {
            'HUMBLE':  _random.uniform(30, 80),
            'SCHOLAR': _random.uniform(80, 150),
            'OFFICIAL': _random.uniform(200, 400),
        }
        PlayerProfile.objects.create(
            game=game,
            background=background,
            knowledge=defaults['knowledge'],
            skill=defaults['skill'],
            personal_wealth=round(WEALTH_START.get(background, 100), 1),
        )

        # 初始化府域
        PrefectureService.create_prefecture_game(game, prefecture_type=prefecture_type)

        return Response(
            PrefectureService.get_prefecture_overview(game),
            status=status.HTTP_201_CREATED,
        )


class PrefectureOverviewView(APIView):
    """
    GET /api/prefecture/<game_id>/
    府情总览：府库、定额进度、下辖县列表（含最新汇报档位）
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        if month_of_year(game.current_season) in {11, 12}:
            AnnualReviewService.ensure_prefecture_self_reviews(game)
        return Response(PrefectureService.get_prefecture_overview(game))


class PrefectureAdvanceView(APIView):
    """
    POST /api/prefecture/<game_id>/advance/
    推进一个月
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        if game.current_season > 36:
            return Response({"error": "任期已满"}, status=status.HTTP_400_BAD_REQUEST)
        blocker = AnnualReviewService.get_prefecture_advance_blocker(game)
        if blocker:
            return Response({"error": blocker}, status=status.HTTP_400_BAD_REQUEST)
        result = PrefectureService.advance_month(game)
        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class PrefecturePrecomputeView(APIView):
    """
    POST /api/prefecture/<game_id>/precompute/
    后台预推演下辖州县 AI 施政，供下次推进复用
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        if game.current_season > 36:
            return Response({"status": "game_over"})

        next_season = game.current_season
        threading.Thread(
            target=PrefectureService.precompute_ai_decisions,
            args=(game.id, next_season),
            daemon=True,
        ).start()
        return Response({"status": "started", "season": next_season},
                        status=status.HTTP_202_ACCEPTED)

    def get(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        return Response(PrefectureService.get_precompute_status(game.id, game.current_season))


class PrefectureCountyListView(APIView):
    """
    GET /api/prefecture/<game_id>/counties/
    下辖县州总览列表（含最新汇报档位、好感趋势）
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        overview = PrefectureService.get_prefecture_overview(game)
        return Response({"counties": overview["counties"]})


class PrefectureCountyDetailView(APIView):
    """
    GET /api/prefecture/<game_id>/counties/<unit_id>/
    单个下辖县详情：知县档案 + 历史汇报（最多8条，均为档位格式）
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id, unit_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        detail = PrefectureService.get_county_detail(game, unit_id)
        if not detail:
            return Response({"error": "县不存在"}, status=status.HTTP_404_NOT_FOUND)
        return Response(detail)


class PrefecturePersonnelView(APIView):
    """
    GET  /api/prefecture/<game_id>/personnel/        — 人事评议总览
    POST /api/prefecture/<game_id>/personnel/        — 提交单个下属年度评议
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        return Response(AnnualReviewService.get_prefecture_personnel_payload(game))

    def post(self, request, game_id):
        from .serializers import PrefectureAnnualReviewSerializer

        game, err = _get_prefect_game(request, game_id)
        if err:
            return err

        serializer = PrefectureAnnualReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = AnnualReviewService.submit_prefecture_review(
            game=game,
            unit_id=data["unit_id"],
            grade=data["grade"],
            strengths=data["strengths"],
            weaknesses=data["weaknesses"],
            focus=data["focus"],
        )
        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class PrefectureQuotaView(APIView):
    """
    POST /api/prefecture/<game_id>/quota/
    分配年度税赋配额（仅正月可用）
    Body: { "assignments": { "<unit_id>": <amount>, ... } }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err

        assignments = request.data.get('assignments')
        if not isinstance(assignments, dict):
            return Response({"error": "assignments 必须为 {unit_id: amount} 字典"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            assignments = {int(k): float(v) for k, v in assignments.items()}
        except (ValueError, TypeError):
            return Response({"error": "assignments 格式错误"},
                            status=status.HTTP_400_BAD_REQUEST)

        result = PrefectureService.distribute_quota(game, assignments)
        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        PrefectureService.invalidate_precompute(game)
        return Response(result)


class PrefectureDirectiveView(APIView):
    """
    POST /api/prefecture/<game_id>/directive/
    向指定下辖县发出政策指令（LLM 生成知县响应）
    Body: { "unit_id": <int>, "directive": "<指令内容>" }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err

        unit_id = request.data.get('unit_id')
        directive = request.data.get('directive', '').strip()
        if not unit_id or not directive:
            return Response({"error": "unit_id 和 directive 不能为空"},
                            status=status.HTTP_400_BAD_REQUEST)

        unit = AdminUnit.objects.filter(
            id=unit_id, game=game, unit_type='COUNTY',
        ).first()
        if not unit:
            return Response({"error": "县不存在"}, status=status.HTTP_404_NOT_FOUND)

        # 记录指令到 unit_data，AI 下月决策时会参考
        unit.unit_data.setdefault('pending_directives', []).append({
            "season": game.current_season,
            "directive": directive,
        })
        # 保留最近3条未消费指令
        unit.unit_data['pending_directives'] = unit.unit_data['pending_directives'][-3:]
        unit.save(update_fields=['unit_data'])
        PrefectureService.invalidate_precompute(game)

        gp = unit.unit_data.get('governor_profile', {})
        return Response({
            "unit_id": unit_id,
            "county_name": unit.unit_data.get('county_name', ''),
            "governor_name": gp.get('name', ''),
            "directive": directive,
            "response": f"{gp.get('name', '该知县')}接到指令，将于下月施政中予以考量。",
        })


class PrefectureInvestView(APIView):
    """
    GET  /api/prefecture/<game_id>/invest/  — 查看可投资项目与建设队列
    POST /api/prefecture/<game_id>/invest/  — 启动府级投资
    Body: { "project": str, "level": int }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        return Response(PrefectureService.get_invest_status(game))

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        project = request.data.get('project', '').strip()
        level = request.data.get('level')
        if not project or level is None:
            return Response({"error": "project 和 level 不能为空"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            level = int(level)
        except (ValueError, TypeError):
            return Response({"error": "level 必须为整数"},
                            status=status.HTTP_400_BAD_REQUEST)
        result = PrefectureService.invest(game, project, level)
        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        PrefectureService.invalidate_precompute(game)
        return Response(result)


class PrefectureTalentView(APIView):
    """
    GET /api/prefecture/<game_id>/talent/
    返回全府才池统计信息与历史府试结果
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        return Response(PrefectureService.get_talent_info(game))


class PrefectureJudicialView(APIView):
    """
    GET /api/prefecture/<game_id>/judicial/
    返回待决卷宗（完整数据）和司法日志
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        return Response(PrefectureService.get_judicial_cases(game))


class PrefectureJudicialDecideView(APIView):
    """
    POST /api/prefecture/<game_id>/judicial/decide/
    对卷宗作出司法决策
    Body: { "case_id": str, "action": "核准原判"|"驳回重审"|"提审改判" }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err

        case_id = request.data.get('case_id', '').strip()
        action  = request.data.get('action', '').strip()
        if not case_id or not action:
            return Response({"error": "case_id 和 action 不能为空"},
                            status=status.HTTP_400_BAD_REQUEST)

        result = PrefectureService.decide_judicial_case(game, case_id, action)
        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class PrefectureInspectView(APIView):
    """
    POST /api/prefecture/<game_id>/inspect/
    通判核账或推官巡查，临时返回精确数值（每年每类最多3次）
    Body: { "unit_id": <int>, "inspect_type": "tongpan" | "tuiguan" }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err

        unit_id = request.data.get('unit_id')
        inspect_type = request.data.get('inspect_type', 'tongpan')
        if inspect_type not in ('tongpan', 'tuiguan'):
            return Response({"error": "inspect_type 必须为 tongpan 或 tuiguan"},
                            status=status.HTTP_400_BAD_REQUEST)

        result = PrefectureService.inspect_county(game, unit_id, inspect_type)
        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)
