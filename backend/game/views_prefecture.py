"""知府游戏 API 视图"""

import threading

from django.template.response import TemplateResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AdminUnit, EventLog, GameState
from .services import PrefectureService
from .services.annual_review import AnnualReviewService
from .services.constants import month_of_year
from .services.judicial_caseflow import JudicialCaseflowService


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

        # 创建玩家档案（统一起点，无出身背景差异）
        from .models import PlayerProfile
        PlayerProfile.objects.create(
            game=game,
            knowledge=3.0,
            skill=3.0,
            personal_wealth=round(_random.uniform(10, 30), 1),
        )

        # 初始化府域
        PrefectureService.create_prefecture_game(game, prefecture_type=prefecture_type)
        JudicialCaseflowService.schedule_generation(game.id)

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

        # 书信阻断检查
        from .services.letter import LetterService
        letter_blockers = LetterService.blocking_check(game, game.current_season)
        if letter_blockers:
            return Response(
                {"error": "有紧急公文尚未处理，请先回复", "blocking_letters": letter_blockers},
                status=status.HTTP_400_BAD_REQUEST,
            )

        season = game.current_season
        result = PrefectureService.advance_month(game)
        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        # 书信月度处理
        try:
            LetterService.run_month_advance(game, season)
        except Exception as e:
            import logging
            logging.getLogger('game').warning("书信月度处理失败（非致命）: %s", e)

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
        details = '｜'.join(
            f'{item.get("county_name","")}{item.get("amount",0):.0f}两'
            for item in result.get('assignments', [])
        )
        EventLog.objects.create(
            game=game, season=game.current_season,
            event_type='prefecture_quota_set',
            category='PREFECTURE',
            description=f'【配额下达】{details}',
            data={'assignments': result.get('assignments', [])},
        )
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

        # 同步创建书信记录（进入玩家发件箱）
        try:
            from .services.letter import LetterService
            LetterService.create_directive_letter(
                game=game,
                current_month=game.current_season,
                unit=unit,
                directive_text=directive,
            )
        except Exception as e:
            import logging
            logging.getLogger('game').warning("指令书信创建失败（非致命）: %s", e)

        # 强硬指令使好感度略微下降（催科/整顿措辞触发）
        harsh_keywords = ('催科', '催纳', '限期', '严查', '追究', '问责', '不得有误')
        is_harsh = any(kw in directive for kw in harsh_keywords)
        if is_harsh:
            cd = unit.unit_data
            old_affinity = cd.get('prefect_affinity', 50)
            cd['prefect_affinity'] = max(0, min(100, old_affinity - 3))
            unit.unit_data = cd
            unit.save(update_fields=['unit_data'])

        gp = unit.unit_data.get('governor_profile', {})
        county_name = unit.unit_data.get('county_name', '')
        EventLog.objects.create(
            game=game, season=game.current_season,
            event_type='prefecture_directive_sent',
            category='PREFECTURE',
            description=f'【发出指令】→ {county_name}：{directive[:60]}{"…" if len(directive) > 60 else ""}',
            data={'unit_id': unit_id, 'county_name': county_name,
                  'directive': directive, 'is_harsh': is_harsh},
        )
        return Response({
            "unit_id": unit_id,
            "county_name": county_name,
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
        if result.get('status') == 'completed':
            desc = f'【府级建设完工】{result["label"]} 升至 {result["level"]} 级（即时完工），耗资 {result["cost"]} 两，府库余 {result["treasury_after"]:.1f} 两'
            etype = 'prefecture_invest_complete'
        else:
            desc = f'【府级建设启动】{result["label"]} 升至 {result["level"]} 级，耗资 {result["cost"]} 两，预计 {result["duration"]} 月后完工'
            etype = 'prefecture_invest_start'
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type=etype,
            category='INVESTMENT',
            description=desc,
            data=result,
        )
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


class PrefectureJudicialDebugView(APIView):
    """
    GET /api/prefecture/<game_id>/judicial/debug/
    返回最近一次卷宗抽取与知县初审的后台调试数据。
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err
        return Response(PrefectureService.get_judicial_debug_data(game))


class PrefectureJudicialDebugPageView(APIView):
    """
    GET /api/prefecture/<game_id>/judicial/debug/page/
    返回司法后台调试页面，展示实例化卷宗与 AI 知县初审结果。
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err

        debug_data = PrefectureService.get_judicial_debug_data(game)
        judicial_cases = PrefectureService.get_judicial_cases(game)
        context = {
            "game": game,
            "debug_data": debug_data,
            "generation": debug_data.get("generation") or {},
            "cases": debug_data.get("cases") or [],
            "status_summary": debug_data.get("status_summary") or {},
            "pending_cases": judicial_cases.get("pending_cases") or [],
        }
        return TemplateResponse(request, "game/prefecture_judicial_debug.html", context)


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
        action_label = {'核准原判': '核准', '驳回重审': '驳回重审', '提审改判': '提审改判'}.get(action, action)
        county = result.get('county_review', {}).get('county_name', '') or ''
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='prefecture_judicial_decide',
            category='JUDICIAL',
            description=(
                f'【司法复核】{result["case_name"]}（{county}）— {action_label}'
                + (f'  司法声望→{result["applied_state"].get("judicial_prestige", "")}' if result.get('applied_state') else '')
            ),
            data=result,
        )
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
        type_label = '通判核账' if inspect_type == 'tongpan' else '推官巡查'
        county_name = result.get('county_name', '')
        EventLog.objects.create(
            game=game, season=game.current_season,
            event_type='prefecture_inspect_result',
            category='PREFECTURE',
            description=f'【{type_label}】{county_name}：{result.get("summary", "")[:60]}',
            data={'unit_id': unit_id, 'inspect_type': inspect_type,
                  'county_name': county_name, 'metrics': result.get('metrics', {})},
        )
        return Response(result)


class PrefectureReliefView(APIView):
    """
    POST /api/prefecture/<game_id>/relief/
    从府库拨款至指定下辖县（资源调拨）
    Body: { "unit_id": <int>, "amount": <float> }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err

        unit_id = request.data.get('unit_id')
        amount = request.data.get('amount')
        if not unit_id or amount is None:
            return Response({"error": "unit_id 和 amount 不能为空"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            amount = float(amount)
        except (ValueError, TypeError):
            return Response({"error": "amount 必须为数字"},
                            status=status.HTTP_400_BAD_REQUEST)

        result = PrefectureService.relief_county(game, int(unit_id), amount)
        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        PrefectureService.invalidate_precompute(game)
        county_name = result.get('county_name', '')
        grain_note = ''
        if result.get('grain_from_granary', 0) > 0:
            grain_note = f'义仓拨粮 {result["grain_from_granary"]:.0f} 斤'
        if result.get('silver_spent', 0) > 0:
            grain_note += ('、' if grain_note else '') + f'府库折银 {result["silver_spent"]:.1f} 两'
        EventLog.objects.create(
            game=game, season=game.current_season,
            event_type='prefecture_relief_dispatched',
            category='PREFECTURE',
            description=f'【赈灾拨付】→ {county_name}：{grain_note or f"拨付{amount:.0f}两"}',
            data={'unit_id': unit_id, 'county_name': county_name, **{
                k: result[k] for k in
                ('grain_from_granary', 'grain_from_treasury', 'silver_spent',
                 'granary_stock_after', 'county_grain_reserve_after')
                if k in result
            }},
        )
        return Response(result)


class PrefectureConfrontView(APIView):
    """
    POST /api/prefecture/<game_id>/confront/
    约谈施压下属知县
    Body: { "unit_id": <int>, "pressure": "light"|"moderate"|"heavy", "message": <str> }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err

        unit_id = request.data.get('unit_id')
        pressure = request.data.get('pressure', 'moderate')
        message = request.data.get('message', '').strip()

        if not unit_id:
            return Response({"error": "unit_id 不能为空"},
                            status=status.HTTP_400_BAD_REQUEST)
        if pressure not in ('light', 'moderate', 'heavy'):
            return Response({"error": "pressure 必须为 light/moderate/heavy"},
                            status=status.HTTP_400_BAD_REQUEST)

        result = PrefectureService.confront_county(game, int(unit_id), pressure, message)
        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        PrefectureService.invalidate_precompute(game)
        pressure_label = {'light': '温和劝勉', 'moderate': '正式约谈', 'heavy': '严厉施压'}.get(pressure, pressure)
        county_name = result.get('county_name', '')
        EventLog.objects.create(
            game=game, season=game.current_season,
            event_type='prefecture_confront',
            category='PERSONNEL',
            description=f'【{pressure_label}】{county_name}：{message[:50] if message else result.get("effect", "")[:50]}',
            data={'unit_id': unit_id, 'county_name': county_name,
                  'pressure': pressure, 'message': message, 'effect': result.get('effect', '')},
        )
        return Response(result)


class PrefectureImpeachView(APIView):
    """
    POST /api/prefecture/<game_id>/impeach/
    弹劾免职下属知县（需省级审批）
    Body: { "unit_id": <int>, "reason": <str> }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id):
        game, err = _get_prefect_game(request, game_id)
        if err:
            return err

        unit_id = request.data.get('unit_id')
        reason = request.data.get('reason', '').strip()
        if not unit_id or not reason:
            return Response({"error": "unit_id 和 reason 不能为空"},
                            status=status.HTTP_400_BAD_REQUEST)

        result = PrefectureService.impeach_county(game, int(unit_id), reason)
        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        PrefectureService.invalidate_precompute(game)
        county_name = result.get('county_name', '')
        EventLog.objects.create(
            game=game, season=game.current_season,
            event_type='prefecture_impeach',
            category='PERSONNEL',
            description=f'【弹劾奏报】{county_name}：{reason[:60]}{"…" if len(reason) > 60 else ""}',
            data={'unit_id': unit_id, 'county_name': county_name,
                  'reason': reason, 'status': result.get('status', '')},
        )
        return Response(result)
