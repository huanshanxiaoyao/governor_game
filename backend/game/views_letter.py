"""书信系统视图"""
import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from .models import Agent, GameState, Letter
from .services.letter import LetterService

logger = logging.getLogger('game')


def _get_game(request, game_id):
    try:
        return GameState.objects.get(id=game_id, user=request.user), None
    except GameState.DoesNotExist:
        return None, Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)


class LetterInboxView(APIView):
    """
    GET  /api/games/<game_id>/letters/         — 收件箱
    POST /api/games/<game_id>/letters/         — 玩家写信
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err
        letters = (
            Letter.objects.filter(game=game, player_is_recipient=True)
            .exclude(status__in=[
                Letter.Status.DRAFT,
                Letter.Status.IN_TRANSIT,
                Letter.Status.BURNED,
            ])
            .select_related('sender_agent')
            .order_by('-delivered_month', '-created_at')[:50]
        )
        letters = list(letters)
        unread   = sum(1 for l in letters if l.status == Letter.Status.DELIVERED)
        blocking = sum(
            1 for l in letters
            if l.is_blocking and l.requires_reply
            and l.status in (Letter.Status.DELIVERED, Letter.Status.READ)
        )
        return Response({
            "unread_count": unread,
            "blocking_count": blocking,
            "results": LetterService.serialize_list(letters),
        })

    def post(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err

        agent_id        = request.data.get('recipient_agent_id')
        letter_type     = request.data.get('letter_type', Letter.LetterType.PERSONAL)
        confidentiality = request.data.get('confidentiality', Letter.Confidentiality.PERSONAL)
        subject         = (request.data.get('subject') or '').strip()
        body            = (request.data.get('body') or '').strip()

        if not subject or not body:
            return Response({"error": "主题和正文不能为空"}, status=status.HTTP_400_BAD_REQUEST)
        if not agent_id:
            return Response({"error": "请选择收件人"}, status=status.HTTP_400_BAD_REQUEST)
        if letter_type not in [c[0] for c in Letter.LetterType.choices]:
            return Response({"error": "无效的信件类型"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            agent = Agent.objects.get(id=agent_id, game=game)
        except Agent.DoesNotExist:
            return Response({"error": "收件人不存在"}, status=status.HTTP_404_NOT_FOUND)

        letter = LetterService.create_player_letter(
            game=game,
            current_month=game.current_season,
            recipient_agent=agent,
            letter_type=letter_type,
            confidentiality=confidentiality,
            subject=subject,
            body=body,
        )
        return Response({
            "id": letter.id,
            "status": letter.status,
            "delivered_month": letter.delivered_month,
            "message": f"信件已发出，预计第{letter.delivered_month}月送达",
        }, status=status.HTTP_201_CREATED)


class LetterSentView(APIView):
    """GET /api/games/<game_id>/letters/sent/ — 发件箱"""
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err
        letters = (
            Letter.objects.filter(game=game, player_is_sender=True)
            .select_related('recipient_agent')
            .order_by('-sent_month', '-created_at')[:50]
        )
        return Response({"results": LetterService.serialize_list(letters)})


class LetterPendingView(APIView):
    """GET /api/games/<game_id>/letters/pending/ — 待回复"""
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err
        letters = (
            Letter.objects.filter(
                game=game,
                player_is_recipient=True,
                requires_reply=True,
            )
            .exclude(status__in=[
                Letter.Status.REPLIED,
                Letter.Status.ARCHIVED,
                Letter.Status.BURNED,
                Letter.Status.DRAFT,
                Letter.Status.IN_TRANSIT,
            ])
            .select_related('sender_agent')
            .order_by('reply_deadline_month', '-delivered_month')
        )
        return Response({"results": LetterService.serialize_list(letters)})


class LetterBlockingCheckView(APIView):
    """GET /api/games/<game_id>/letters/blocking-check/ — 推进前检查"""
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err
        blockers = LetterService.blocking_check(game, game.current_season)
        return Response({"blocked": bool(blockers), "blocking_letters": blockers})


class LetterSummaryView(APIView):
    """GET /api/games/<game_id>/letters/summary/ — 徽标用摘要（未读数、阻断数）"""
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id):
        game, err = _get_game(request, game_id)
        if err:
            return err
        return Response(LetterService.get_inbox_summary(game))


class LetterDetailView(APIView):
    """GET /api/games/<game_id>/letters/<letter_id>/ — 详情，触发已读"""
    permission_classes = [IsAuthenticated]

    def get(self, request, game_id, letter_id):
        game, err = _get_game(request, game_id)
        if err:
            return err
        try:
            letter = Letter.objects.select_related(
                'sender_agent', 'recipient_agent', 'parent_letter',
            ).get(id=letter_id, game=game)
        except Letter.DoesNotExist:
            return Response({"error": "信件不存在"}, status=status.HTTP_404_NOT_FOUND)

        LetterService.mark_as_read(letter, game.current_season)
        data = LetterService.serialize_detail(letter)

        # 焚毁件：已读后自动销毁
        if (letter.confidentiality == Letter.Confidentiality.BURN
                and letter.status in (Letter.Status.READ, Letter.Status.DELIVERED)):
            letter.status = Letter.Status.BURNED
            letter.save(update_fields=['status'])
            data['burned'] = True

        # 附上来信摘要（若为回复线索）
        if letter.parent_letter_id:
            p = letter.parent_letter
            data['parent_summary'] = {
                "id": p.id,
                "subject": p.subject,
                "sent_month": p.sent_month,
            }

        return Response(data)


class LetterReplyView(APIView):
    """POST /api/games/<game_id>/letters/<letter_id>/reply/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id, letter_id):
        game, err = _get_game(request, game_id)
        if err:
            return err
        try:
            letter = Letter.objects.get(
                id=letter_id, game=game, player_is_recipient=True,
            )
        except Letter.DoesNotExist:
            return Response({"error": "信件不存在"}, status=status.HTTP_404_NOT_FOUND)

        choice_id = (request.data.get('choice_id') or '').strip()
        body      = (request.data.get('body') or '').strip()
        if not choice_id and not body:
            return Response(
                {"error": "请选择回复选项或填写回复内容"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ok, msg = LetterService.apply_reply(
            letter=letter,
            current_month=game.current_season,
            choice_id=choice_id or None,
            body=body or None,
        )
        if not ok:
            return Response({"error": msg}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": "REPLIED", "message": msg})


class LetterArchiveView(APIView):
    """POST /api/games/<game_id>/letters/<letter_id>/archive/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, game_id, letter_id):
        game, err = _get_game(request, game_id)
        if err:
            return err
        try:
            letter = Letter.objects.get(id=letter_id, game=game)
        except Letter.DoesNotExist:
            return Response({"error": "信件不存在"}, status=status.HTTP_404_NOT_FOUND)
        if letter.status not in (
            Letter.Status.DELIVERED,
            Letter.Status.READ,
            Letter.Status.REPLIED,
        ):
            return Response({"error": "当前状态不可归档"}, status=status.HTTP_400_BAD_REQUEST)
        letter.status = Letter.Status.ARCHIVED
        letter.save(update_fields=['status'])
        return Response({"status": "ARCHIVED"})
