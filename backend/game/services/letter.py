"""书信服务 — 信件创建、投递、回复、LLM生成"""
import logging

from .eventlog import log_game_event

logger = logging.getLogger('game')


def _sender_display(letter):
    if letter.player_is_sender:
        return "玩家"
    if letter.sender_agent_id:
        a = letter.sender_agent
        return f"{a.role_title} {a.name}".strip()
    return "系统"


def _recipient_display(letter):
    if letter.player_is_recipient:
        return "玩家"
    if letter.recipient_agent_id:
        a = letter.recipient_agent
        return f"{a.role_title} {a.name}".strip()
    return "未知"


class LetterService:

    # -------------------------------------------------------------------------
    # 月度推进钩子
    # -------------------------------------------------------------------------

    @staticmethod
    def blocking_check(game, current_month):
        """返回阻断推进的信件列表（硬deadline未回复）。"""
        from ..models import Letter
        blockers = (
            Letter.objects.filter(
                game=game,
                player_is_recipient=True,
                is_blocking=True,
                reply_deadline_month__lte=current_month,
            )
            .exclude(
                status__in=[
                    Letter.Status.REPLIED,
                    Letter.Status.ARCHIVED,
                    Letter.Status.BURNED,
                ]
            )
            .select_related('sender_agent')
        )
        return [
            {
                "id": l.id,
                "subject": l.subject,
                "sender_name": _sender_display(l),
                "reply_deadline_month": l.reply_deadline_month,
            }
            for l in blockers
        ]

    @staticmethod
    def run_month_advance(game, current_month):
        """月度书信三步：投递 → 软deadline后果 → NPC生成回复。"""
        LetterService._deliver_letters(game, current_month)
        LetterService._apply_soft_deadline_consequences(game, current_month)
        LetterService._generate_npc_replies(game, current_month)

    @staticmethod
    def _deliver_letters(game, current_month):
        from ..models import Letter
        Letter.objects.filter(
            game=game,
            status=Letter.Status.IN_TRANSIT,
            delivered_month__lte=current_month,
        ).update(status=Letter.Status.DELIVERED)

    @staticmethod
    def _apply_soft_deadline_consequences(game, current_month):
        """软deadline超时：应用默认选项，记录后果（不阻断）。"""
        from ..models import Letter
        expired = (
            Letter.objects.filter(
                game=game,
                player_is_recipient=True,
                requires_reply=True,
                is_blocking=False,
                reply_deadline_month__lte=current_month,
                reply_deadline_month__isnull=False,
            )
            .exclude(
                status__in=[
                    Letter.Status.REPLIED,
                    Letter.Status.ARCHIVED,
                    Letter.Status.BURNED,
                    Letter.Status.DRAFT,
                    Letter.Status.IN_TRANSIT,
                ]
            )
        )
        for letter in expired:
            choice_id = letter.default_choice_id or 'ignored'
            letter.reply_choice_id = choice_id
            letter.replied_month = current_month
            letter.status = Letter.Status.REPLIED
            letter.save(update_fields=['reply_choice_id', 'replied_month', 'status'])
            logger.info("软deadline自动处理 letter#%s → %s", letter.id, choice_id)

    # 本县 NPC 角色集合（直接交谈，书信必回）
    _LOCAL_ROLES = {'ADVISOR', 'DEPUTY', 'GENTRY', 'VILLAGER'}
    # 玩家（知县）品级
    _PLAYER_RANK = 7
    # 玩家默认理念（偏民本·中立·偏务实）
    _PLAYER_IDEOLOGY = {
        'people_vs_authority':    0.6,
        'reform_vs_tradition':    0.5,
        'pragmatic_vs_idealist':  0.6,
    }
    # 理念相似阈值（欧氏距离，越小越相似）
    _IDEOLOGY_SIMILARITY_THRESHOLD = 0.35
    # 皇帝敌意关键词
    _EMPEROR_HOSTILE_KEYWORDS = [
        '昏君', '昏庸', '无道', '暴君', '残暴', '不配', '造反', '谋逆', '弑君',
        '昏聩', '荒淫', '无能', '昏主', '废黜', '推翻', '乱臣', '贼子',
    ]

    @staticmethod
    def _ideology_similar(npc_ideology: dict) -> bool:
        """判断NPC理念是否与玩家相近（欧氏距离 < 阈值）。"""
        import math
        pi = LetterService._PLAYER_IDEOLOGY
        dist_sq = sum(
            (npc_ideology.get(k, 0.5) - pi.get(k, 0.5)) ** 2
            for k in pi
        )
        return math.sqrt(dist_sq) < LetterService._IDEOLOGY_SIMILARITY_THRESHOLD

    @staticmethod
    def _is_hostile_to_emperor(body: str) -> bool:
        return any(kw in body for kw in LetterService._EMPEROR_HOSTILE_KEYWORDS)

    @staticmethod
    def _create_canned_reply(game, original_letter, agent, body_text, current_month):
        """创建固定文本回复，不调用 LLM。"""
        from ..models import Letter
        reply = Letter.objects.create(
            game=game,
            sender_agent=agent,
            player_is_sender=False,
            recipient_agent=None,
            player_is_recipient=True,
            letter_type=original_letter.letter_type,
            confidentiality=original_letter.confidentiality,
            subject=f"回复：{original_letter.subject}",
            body=body_text,
            sent_month=current_month,
            delivery_delay=1,
            delivered_month=current_month + 1,
            requires_reply=False,
            is_blocking=False,
            status=Letter.Status.IN_TRANSIT,
            parent_letter=original_letter,
        )
        original_letter.status = Letter.Status.REPLIED
        original_letter.replied_month = current_month
        original_letter.save(update_fields=['status', 'replied_month'])
        return reply

    @staticmethod
    def _trigger_imperial_dismissal(game, letter, current_month):
        """玩家对皇帝出言不逊 → 触发罢黜事件。"""
        from .state import load_player_state, save_player_state
        from .eventlog import log_game_event
        try:
            state = load_player_state(game)
            state['imperial_dismissal'] = True
            state['imperial_dismissal_month'] = current_month
            save_player_state(game, state)
        except Exception as e:
            logger.warning("罢黜状态写入失败: %s", e)
        log_game_event(
            game,
            event_type='imperial_dismissal',
            category='DISASTER',
            season=current_month,
            description='天子震怒，下旨罢黜知县，即刻离任。',
            data={'trigger': 'hostile_letter_to_emperor', 'letter_id': letter.id},
        )
        # 同时以圣旨形式回信
        agent = letter.recipient_agent
        LetterService._create_canned_reply(
            game, letter, agent,
            "朕览卿书，言辞狂悖，有失臣节。着即罢黜，令其离任，永不叙用。钦此。",
            current_month,
        )

    @staticmethod
    def _generate_npc_replies(game, current_month):
        """为本月刚投递到NPC的玩家来信，依规则决定是否回复及方式。"""
        from ..models import Letter
        player_sent = (
            Letter.objects.filter(
                game=game,
                player_is_sender=True,
                delivered_month=current_month,
                status=Letter.Status.DELIVERED,
            )
            .exclude(letter_type=Letter.LetterType.CIRCULAR)
            .select_related('recipient_agent')
        )
        for letter in player_sent:
            if not letter.recipient_agent_id:
                continue
            # 避免重复生成
            if Letter.objects.filter(parent_letter=letter).exists():
                continue
            agent = letter.recipient_agent
            attrs = agent.attributes or {}
            role = agent.role

            try:
                # ── 皇帝特例 ──────────────────────────────────────
                if role == 'EMPEROR':
                    if LetterService._is_hostile_to_emperor(letter.body):
                        LetterService._trigger_imperial_dismissal(game, letter, current_month)
                    else:
                        LetterService._create_canned_reply(
                            game, letter, agent,
                            "卿书已阅。望卿勤勉任事，安靖地方，不负皇恩。",
                            current_month,
                        )
                    continue

                # ── 本县 NPC：直接走 LLM ──────────────────────────
                if role in LetterService._LOCAL_ROLES:
                    LetterService._generate_agent_reply(game, letter, current_month)
                    continue

                # ── 外部官员：品级高于玩家时检查理念相似度 ──────
                agent_rank = attrs.get('rank', LetterService._PLAYER_RANK)
                if agent_rank < LetterService._PLAYER_RANK:
                    # 品级更高；理念不相近则不回复
                    npc_ideology = attrs.get('ideology') or {}
                    if not LetterService._ideology_similar(npc_ideology):
                        logger.info(
                            "letter#%s: %s(%s) 与玩家理念不符，不回复",
                            letter.id, agent.name, role,
                        )
                        continue  # 书信石沉大海

                # 品级相近或理念相似 → LLM 正常回复
                LetterService._generate_agent_reply(game, letter, current_month)

            except Exception as e:
                logger.warning("NPC回复生成失败 letter#%s: %s", letter.id, e)

    # -------------------------------------------------------------------------
    # 信件创建
    # -------------------------------------------------------------------------

    @staticmethod
    def create_directive_letter(game, current_month, unit, directive_text):
        """
        知府玩家向下辖县下达指令时，创建对应书信记录（进入玩家发件箱）。
        unit: AdminUnit (county)
        """
        from ..models import Letter
        county_name = unit.unit_data.get('county_name', '下辖县')
        subject = f"关于{county_name}施政事宜的训令"
        letter = Letter.objects.create(
            game=game,
            sender_agent=None,
            player_is_sender=True,
            recipient_agent=unit.ai_agent if unit.ai_agent_id else None,
            player_is_recipient=False,
            letter_type=Letter.LetterType.OFFICIAL,
            confidentiality=Letter.Confidentiality.PUBLIC,
            subject=subject,
            body=directive_text,
            sent_month=current_month,
            delivery_delay=1,
            delivered_month=current_month + 1,
            requires_reply=False,
            is_blocking=False,
            status=Letter.Status.IN_TRANSIT,
        )
        log_game_event(
            game,
            event_type='player_directive_letter_sent',
            category='LETTER',
            season=current_month,
            description=f'已向{county_name}发出训令书信',
            data={
                'letter_id': letter.id,
                'unit_name': county_name,
                'delivered_month': letter.delivered_month,
            },
        )
        return letter

    @staticmethod
    def create_player_letter(game, current_month, recipient_agent, letter_type,
                             confidentiality, subject, body):
        """玩家主动写信给NPC，下月送达，NPC再下月生成回复。"""
        from ..models import Letter
        letter = Letter.objects.create(
            game=game,
            sender_agent=None,
            player_is_sender=True,
            recipient_agent=recipient_agent,
            player_is_recipient=False,
            letter_type=letter_type,
            confidentiality=confidentiality,
            subject=subject,
            body=body,
            sent_month=current_month,
            delivery_delay=1,
            delivered_month=current_month + 1,
            requires_reply=False,
            is_blocking=False,
            status=Letter.Status.IN_TRANSIT,
        )
        log_game_event(
            game,
            event_type='player_letter_sent',
            category='LETTER',
            season=current_month,
            description=f'已向{recipient_agent.name}发出书信：《{subject[:24]}{"…" if len(subject) > 24 else ""}》',
            data={
                'letter_id': letter.id,
                'recipient_agent_id': recipient_agent.id,
                'recipient_name': recipient_agent.name,
                'letter_type': letter_type,
                'confidentiality': confidentiality,
                'subject': subject,
                'delivered_month': letter.delivered_month,
            },
        )
        return letter

    @staticmethod
    def create_npc_letter(game, current_month, sender_agent, subject, body,
                          letter_type='OFFICIAL', confidentiality='PERSONAL',
                          requires_reply=False, is_blocking=False,
                          reply_deadline_month=None, reply_options=None,
                          default_choice_id=None, llm_generated=False,
                          generation_context=None, delivery_delay=1):
        """NPC主动给玩家写信（事件驱动）。delivery_delay=0 时立即送达。"""
        from ..models import Letter
        immediate = delivery_delay == 0
        return Letter.objects.create(
            game=game,
            sender_agent=sender_agent,
            player_is_sender=False,
            recipient_agent=None,
            player_is_recipient=True,
            letter_type=letter_type,
            confidentiality=confidentiality,
            subject=subject,
            body=body,
            sent_month=current_month,
            delivery_delay=delivery_delay,
            delivered_month=current_month if immediate else current_month + delivery_delay,
            requires_reply=requires_reply,
            is_blocking=is_blocking,
            reply_deadline_month=reply_deadline_month,
            reply_options=reply_options,
            default_choice_id=default_choice_id,
            status=Letter.Status.DELIVERED if immediate else Letter.Status.IN_TRANSIT,
            llm_generated=llm_generated,
            generation_context=generation_context,
        )

    @staticmethod
    def create_hidden_land_report_letter(game, current_month, reporter_agent,
                                         target_village_name, hidden_amount):
        """村民代表举报地主隐匿田产的书信（下月送达）。
        reporter_agent 可为 None，此时以匿名方式处理。
        """
        if reporter_agent:
            reporter_name = reporter_agent.name
            reporter_village = (reporter_agent.attributes or {}).get('village_name', '邻村')
        else:
            reporter_name = '来报村民'
            reporter_village = '邻村'

        subject = f"关于{target_village_name}地主隐匿田产一事"
        body = (
            f"大人台启：小民{reporter_name}，来自{reporter_village}，冒昧修书，"
            f"实有紧要事禀报。近日修建水利，小民偶然察觉{target_village_name}地主名下"
            f"似有隐匿田产约{hidden_amount}亩，未曾登记在册，恐有逃税漏籍之嫌。"
            f"特此禀报，恭请大人明察。小民{reporter_name}叩首。"
        )
        return LetterService.create_npc_letter(
            game=game,
            current_month=current_month,
            sender_agent=reporter_agent,
            subject=subject,
            body=body,
            letter_type='PERSONAL',
            confidentiality='PERSONAL',
            requires_reply=False,
            is_blocking=False,
            reply_options=[
                {
                    "id": "ack",
                    "text": "知道了，本县已着手调查，多谢告知",
                    "hint": "回复后双方好感度+2",
                    "consequence_tags": ["sender_relation:+2"],
                },
                {
                    "id": "dismiss",
                    "text": "（不予回复）",
                },
            ],
            default_choice_id=None,
            delivery_delay=0,
        )

    # -------------------------------------------------------------------------
    # 读取与回复
    # -------------------------------------------------------------------------

    @staticmethod
    def mark_as_read(letter, current_month):
        from ..models import Letter
        if letter.status == Letter.Status.DELIVERED:
            letter.status = Letter.Status.READ
            letter.read_at_month = current_month
            letter.save(update_fields=['status', 'read_at_month'])

    @staticmethod
    def apply_reply(letter, current_month, choice_id=None, body=None):
        """玩家回复信件，触发选项后果。返回 (ok, message)。"""
        from ..models import Letter
        if letter.status not in (Letter.Status.DELIVERED, Letter.Status.READ):
            return False, "信件状态不允许回复"
        if not letter.requires_reply:
            return False, "该信件不需要回复"

        letter.reply_choice_id = choice_id or ''
        letter.reply_body = body or ''
        letter.replied_month = current_month
        letter.status = Letter.Status.REPLIED
        letter.save(update_fields=[
            'reply_choice_id', 'reply_body', 'replied_month', 'status',
        ])
        LetterService._apply_choice_consequence(letter)
        sender_name = letter.sender_agent.name if letter.sender_agent_id else '来函对象'
        log_game_event(
            letter.game,
            event_type='player_letter_reply',
            category='LETTER',
            season=current_month,
            description=f'已回复来自{sender_name}的书信：《{letter.subject[:24]}{"…" if len(letter.subject) > 24 else ""}》',
            data={
                'letter_id': letter.id,
                'subject': letter.subject,
                'sender_name': sender_name,
                'choice_id': choice_id or '',
                'reply_body': body or '',
            },
        )
        return True, "回复成功"

    @staticmethod
    def _apply_choice_consequence(letter):
        """依据 reply_choice_id 的 consequence_tags 应用效果。

        支持的标签：
          sender_relation:+N / sender_relation:-N — 调整发信人 player_affinity
        """
        if not letter.reply_options or not letter.reply_choice_id:
            return
        tags = []
        for opt in letter.reply_options:
            if opt.get('id') == letter.reply_choice_id:
                tags = opt.get('consequence_tags', [])
                break

        logger.info("信件#%s 触发后果标签: %s", letter.id, tags)

        for tag in tags:
            if tag.startswith('sender_relation:') and letter.sender_agent_id:
                try:
                    delta = int(tag.split(':')[1])
                except (IndexError, ValueError):
                    continue
                agent = letter.sender_agent
                attrs = agent.attributes or {}
                attrs['player_affinity'] = max(0, min(99, attrs.get('player_affinity', 50) + delta))
                agent.attributes = attrs
                agent.save(update_fields=['attributes'])

    # -------------------------------------------------------------------------
    # LLM 生成
    # -------------------------------------------------------------------------

    @staticmethod
    def _generate_agent_reply(game, original_letter, current_month):
        """调用LLM为NPC生成回复，并创建下月到达的回复信件。"""
        from llm.client import LLMClient
        from llm.prompts import PromptRegistry
        from ..models import Letter
        from .agent import AgentService

        agent = original_letter.recipient_agent

        # 复用 AgentService 的完整上下文（bio/性格/关系/县情/村庄等）
        ctx = AgentService.build_system_context(game, agent)
        # 补充书信专属字段
        ctx['current_month'] = current_month
        ctx['original_subject'] = original_letter.subject
        ctx['original_body'] = original_letter.body

        system, user = PromptRegistry.render('letter_npc_reply', **ctx)
        from llm.context import LLMContext
        from llm import call_sources
        try:
            result = LLMClient(context=LLMContext(
                call_source=call_sources.NPC_LETTER,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            )).chat_json(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                max_tokens=600,
            )
            if not isinstance(result, dict):
                raise ValueError(f"LLM 返回非字典: {type(result)}")
        except Exception:
            import logging
            logging.getLogger('game').warning(
                "书信NPC回复生成失败，使用占位内容", exc_info=True)
            result = {}

        subject = result.get('subject', f"回复：{original_letter.subject}")[:200]
        body    = result.get('body', '（回复生成失败，请联系管理员）')
        conf    = result.get('confidentiality', original_letter.confidentiality)

        reply = Letter.objects.create(
            game=game,
            sender_agent=agent,
            player_is_sender=False,
            recipient_agent=None,
            player_is_recipient=True,
            letter_type=original_letter.letter_type,
            confidentiality=conf,
            subject=subject,
            body=body,
            sent_month=current_month,
            delivery_delay=1,
            delivered_month=current_month + 1,
            requires_reply=False,
            is_blocking=False,
            status=Letter.Status.IN_TRANSIT,
            parent_letter=original_letter,
            llm_generated=True,
            generation_context=ctx,
        )

        original_letter.status = Letter.Status.REPLIED
        original_letter.replied_month = current_month
        original_letter.save(update_fields=['status', 'replied_month'])
        return reply

    # -------------------------------------------------------------------------
    # 序列化辅助（视图层用）
    # -------------------------------------------------------------------------

    @staticmethod
    def serialize_list(letters):
        return [LetterService._list_item(l) for l in letters]

    @staticmethod
    def _list_item(l):
        from ..models import Letter
        return {
            "id": l.id,
            "sender_name": _sender_display(l),
            "recipient_name": _recipient_display(l),
            "subject": l.subject,
            "letter_type": l.letter_type,
            "letter_type_display": l.get_letter_type_display(),
            "confidentiality": l.confidentiality,
            "status": l.status,
            "sent_month": l.sent_month,
            "delivered_month": l.delivered_month,
            "requires_reply": l.requires_reply,
            "is_blocking": l.is_blocking,
            "reply_deadline_month": l.reply_deadline_month,
            "has_reply_options": bool(l.reply_options),
            "player_is_sender": l.player_is_sender,
            "player_is_recipient": l.player_is_recipient,
        }

    @staticmethod
    def serialize_detail(l):
        d = LetterService._list_item(l)
        d.update({
            "body": l.body,
            "reply_options": l.reply_options,
            "reply_body": l.reply_body,
            "reply_choice_id": l.reply_choice_id,
            "replied_month": l.replied_month,
            "parent_letter_id": l.parent_letter_id,
            "burned": False,
        })
        return d

    @staticmethod
    def get_inbox_summary(game):
        """返回收件箱摘要（未读数、阻断数），用于更新徽标。"""
        from ..models import Letter
        inbox = Letter.objects.filter(
            game=game,
            player_is_recipient=True,
        ).exclude(
            status__in=[
                Letter.Status.DRAFT,
                Letter.Status.IN_TRANSIT,
                Letter.Status.BURNED,
                Letter.Status.ARCHIVED,
            ]
        )
        unread   = inbox.filter(status=Letter.Status.DELIVERED).count()
        blocking = inbox.filter(
            is_blocking=True,
            requires_reply=True,
            status__in=[Letter.Status.DELIVERED, Letter.Status.READ],
        ).count()
        return {"unread_count": unread, "blocking_count": blocking}
