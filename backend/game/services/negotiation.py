"""谈判服务 — 地主兼并 / 兴建水利 / 隐匿土地 多轮谈判状态机"""
import logging
import random
import threading

from django.utils import timezone

from ..models import Agent, DialogueMessage, EventLog, NegotiationSession
from .agent import AgentService
from .eventlog import adjust_player_profile_stat, log_game_event
from .ledger import (
    ensure_county_ledgers,
    ensure_village_ledgers,
    sync_county_gentry_land_ratio,
    sync_legacy_from_ledgers,
)
from .settlement_metrics import MetricsMixin
from .state import load_county_state, save_player_state

from llm.client import LLMClient
from llm.prompts import PromptRegistry

logger = logging.getLogger('game')
NEGOTIATION_INACTIVE_SEASONS = 3

# NPC 主动发起 vs 玩家发起的谈判类型
_NPC_INITIATED_TYPES = frozenset({'VILLAGE_REQ_SCHOOL', 'VILLAGE_REQ_TAX', 'LANDLORD_DEMAND_FACILITY', 'GENTRY_RELIEF_OFFER'})
_PLAYER_INITIATED_TYPES = frozenset({'ANNEXATION', 'IRRIGATION', 'HIDDEN_LAND'})
_REQUEST_STYLE_DELEGATION_TYPES = frozenset({
    'VILLAGE_REQ_SCHOOL',
    'VILLAGE_REQ_TAX',
    'LANDLORD_DEMAND_FACILITY',
    'GENTRY_RELIEF_OFFER',
})

# Round-pressure text by progress
_PRESSURE_EARLY = ''
_PRESSURE_MID = '需认真考虑对方论点，可适当让步。'
_PRESSURE_LATE = '谈判即将结束，准备给出最终答复。'
_PRESSURE_FINAL = '这是最后一轮，你必须在 final_decision 中给出明确决定，不能为 null。'


def _authority_tone(authority: int) -> str:
    """Option C: 威名定性描述，注入谈判 prompt 供 LLM 参考。"""
    if authority >= 76:
        return '\n【关于县令】此人在本地以铁腕著称，强制执行几乎是其惯常手段。\n'
    if authority >= 60:
        return '\n【关于县令】此人素有雷厉风行之名，必要时不惜动用官府强制手段。\n'
    return ''


def _round_pressure(current_round, max_rounds):
    """Return pressure text based on negotiation progress."""
    remaining_pct = (max_rounds - current_round) / max_rounds
    if current_round >= max_rounds:
        return _PRESSURE_FINAL
    if remaining_pct > 0.6:
        return _PRESSURE_EARLY
    if remaining_pct > 0.3:
        return _PRESSURE_MID
    if remaining_pct > 0:
        return _PRESSURE_LATE
    return _PRESSURE_FINAL


class NegotiationService:
    """管理谈判会话的核心服务"""
    SPEAKER_ROLE_MAP = {
        'PLAYER': {'agent_role': None, 'label': '县令'},
        'ADVISOR': {'agent_role': 'ADVISOR', 'label': '师爷'},
        'DEPUTY': {'agent_role': 'DEPUTY', 'label': '县丞'},
    }

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    @classmethod
    def start_negotiation(cls, game, agent, event_type, context_data):
        """Create a new NegotiationSession.

        Returns (session, error_message).  error_message is None on success.
        """
        cls.expire_stale_negotiations(game, current_season=game.current_season)
        context_data = context_data or {}
        village_name = context_data.get('village_name') or agent.attributes.get('village_name', '')

        # 同一agent同时仅允许一场进行中谈判（与DB约束一致）
        existing = NegotiationSession.objects.filter(
            game=game, agent=agent, status='active',
        ).first()
        if existing:
            existing_type = dict(NegotiationSession.EVENT_TYPES).get(
                existing.event_type, existing.event_type)
            place = village_name or agent.name
            return None, f'{place}已有进行中的{existing_type}谈判，请先处理'

        max_rounds = {
            'ANNEXATION': 6, 'IRRIGATION': 6, 'HIDDEN_LAND': 6,
            'VILLAGE_REQ_SCHOOL': 6, 'VILLAGE_REQ_TAX': 6, 'LANDLORD_DEMAND_FACILITY': 6,
            'GENTRY_RELIEF_OFFER': 4,
        }.get(event_type, 6)

        session = NegotiationSession.objects.create(
            game=game,
            agent=agent,
            event_type=event_type,
            status='active',
            current_round=0,
            max_rounds=max_rounds,
            season=game.current_season,
            context_data=context_data,
        )
        log_game_event(
            game,
            event_type='negotiation_started',
            category='NEGOTIATION',
            description=f'{village_name or agent.name}发起{dict(NegotiationSession.EVENT_TYPES).get(event_type, event_type)}谈判',
            data={
                'negotiation_id': session.id,
                'agent_name': agent.name,
                'event_type': event_type,
                'village_name': village_name,
                'context_data': context_data,
            },
        )

        # 异步生成开场白：NPC 发起型生成 NPC 开场陈情；玩家发起型生成师爷提示
        if event_type in _NPC_INITIATED_TYPES:
            threading.Thread(
                target=cls._generate_npc_opening, args=(game, session), daemon=True,
            ).start()
        elif event_type == 'IRRIGATION':
            # 水利谈判：投资创建时已预生成摘要并缓存，直接同步注入避免竞态
            cached_brief = cls._pop_cached_irrigation_brief(game, village_name)
            if cached_brief:
                advisor = Agent.objects.filter(game=game, role='ADVISOR').first()
                try:
                    DialogueMessage.objects.create(
                        game=game,
                        agent=agent,
                        role='advisor',
                        content=cached_brief['brief'],
                        season=game.current_season,
                        metadata={
                            'negotiation_id': session.id,
                            'is_advisor_brief': True,
                            'advisor_name': cached_brief.get('advisor_name') or (advisor.name if advisor else '师爷'),
                        },
                    )
                except Exception as e:
                    logger.warning("Failed to inject cached irrigation brief: %s", e)
            else:
                # 缓存未命中（投资刚创建、预生成尚未完成），回退到异步生成
                threading.Thread(
                    target=cls._generate_advisor_brief, args=(game, session), daemon=True,
                ).start()
        elif event_type in _PLAYER_INITIATED_TYPES:
            threading.Thread(
                target=cls._generate_advisor_brief, args=(game, session), daemon=True,
            ).start()

        return session, None

    @classmethod
    def get_active_negotiation(cls, game):
        """Return the active NegotiationSession for a game, or None."""
        cls.expire_stale_negotiations(game, current_season=game.current_season)
        return NegotiationSession.objects.filter(
            game=game, status='active',
        ).select_related('agent').order_by('created_at').first()

    @classmethod
    def resolve_session(cls, session, outcome):
        """Mark session resolved and apply game effects."""
        session.outcome = outcome
        session.status = 'resolved'
        session.resolved_at = timezone.now()
        session.save()

        if session.event_type == 'ANNEXATION':
            cls._apply_annexation_outcome(session, outcome)
        elif session.event_type == 'HIDDEN_LAND':
            cls._apply_hidden_land_outcome(session, outcome)
        elif session.event_type == 'VILLAGE_REQ_SCHOOL':
            cls._apply_village_req_school_outcome(session, outcome)
        elif session.event_type == 'VILLAGE_REQ_TAX':
            cls._apply_village_req_tax_outcome(session, outcome)
        elif session.event_type == 'LANDLORD_DEMAND_FACILITY':
            cls._apply_landlord_demand_facility_outcome(session, outcome)
        elif session.event_type == 'GENTRY_RELIEF_OFFER':
            cls._apply_gentry_relief_offer_outcome(session, outcome)
        else:
            cls._apply_irrigation_outcome(session, outcome)

        try:
            cls._generate_session_summary(session)
        except Exception as e:
            logger.warning("Session summary generation failed (non-fatal): %s", e)

    # ------------------------------------------------------------------
    # Round Processing
    # ------------------------------------------------------------------

    @classmethod
    def negotiate_round(cls, game, session, player_message, speaker_role='PLAYER'):
        """Process one round of negotiation.

        Returns a result dict with dialogue, round info, and status.
        """
        cls.expire_stale_negotiations(game, current_season=game.current_season)
        session.refresh_from_db()
        if session.status != 'active':
            return {'error': '该谈判已结束'}

        if session.current_round >= session.max_rounds:
            return {'error': '已达最大谈判轮数'}

        speaker_role = cls._normalize_speaker_role(speaker_role)
        delegate_agent = cls._get_delegate_agent(game, speaker_role)
        player_message = (player_message or '').strip()
        if speaker_role != 'PLAYER' and not player_message:
            player_message = cls._build_delegate_message(session, speaker_role, delegate_agent)
        llm_player_message = cls._format_player_message(player_message, speaker_role, delegate_agent)

        # 1. Increment round
        session.current_round += 1
        session.save(update_fields=['current_round'])

        # 2. Save player message
        message_meta = {'negotiation_id': session.id, 'speaker_role': speaker_role}
        if delegate_agent is not None:
            message_meta['speaker_name'] = delegate_agent.name
        DialogueMessage.objects.create(
            game=game,
            agent=session.agent,
            role='player',
            content=player_message,
            season=game.current_season,
            metadata=message_meta,
        )

        # 2b. Extract promises from player message (run in background thread — non-blocking)
        if speaker_role == 'PLAYER':
            from .promise import PromiseService

            def _extract_promises():
                try:
                    PromiseService.extract_and_save(game, session.agent, session, player_message)
                except Exception as e:
                    logger.warning("Promise extraction failed (non-fatal): %s", e)

            threading.Thread(target=_extract_promises, daemon=True).start()

        # 3. Build LLM context
        agent = session.agent
        ctx = AgentService.build_system_context(agent, game)
        ctx['player_message'] = llm_player_message

        # Add negotiation-specific context
        ctx['current_round'] = session.current_round
        ctx['max_rounds'] = session.max_rounds
        ctx['round_pressure'] = _round_pressure(session.current_round, session.max_rounds)
        ctx['village_name'] = agent.attributes.get('village_name', '')
        try:
            ctx['authority_hint'] = _authority_tone(game.player.authority)
        except Exception:
            ctx['authority_hint'] = ''

        if session.event_type == 'ANNEXATION':
            result = cls._negotiate_annexation(ctx, game, session)
        elif session.event_type == 'HIDDEN_LAND':
            result = cls._negotiate_hidden_land(ctx, game, session)
        elif session.event_type == 'VILLAGE_REQ_SCHOOL':
            result = cls._negotiate_village_req_school(ctx, game, session)
        elif session.event_type == 'VILLAGE_REQ_TAX':
            result = cls._negotiate_village_req_tax(ctx, game, session)
        elif session.event_type == 'LANDLORD_DEMAND_FACILITY':
            result = cls._negotiate_landlord_demand_facility(ctx, game, session)
        elif session.event_type == 'GENTRY_RELIEF_OFFER':
            result = cls._negotiate_gentry_relief_offer(ctx, game, session)
        else:
            result = cls._negotiate_irrigation(ctx, game, session)

        handoff_to_player = False
        handoff_message = ''
        if speaker_role in ('ADVISOR', 'DEPUTY'):
            delegated = cls._evaluate_delegate_attempt(
                session, result, speaker_role, delegate_agent,
            )
            if delegated.get('success'):
                result['final_decision'] = delegated.get('final_decision')
                if session.event_type == 'IRRIGATION':
                    result['contribution_offer'] = delegated.get(
                        'contribution_offer', result.get('contribution_offer', 0),
                    )
            else:
                # Delegate failed: hand over to player instead of finalizing a failure outcome.
                handoff_to_player = True
                handoff_message = delegated.get('handoff_message', '')
                result['final_decision'] = None
                # Ensure player still has at least one manual round if delegate failed at limit.
                if session.current_round >= session.max_rounds and session.max_rounds > 0:
                    session.current_round = session.max_rounds - 1
                    session.save(update_fields=['current_round'])
                # 记录代理人曾失败，若玩家亲自谈成可获得能名+1
                if not session.context_data.get('delegate_failed_once'):
                    session.context_data['delegate_failed_once'] = True
                    session.save(update_fields=['context_data'])

        # 4. Save agent response
        DialogueMessage.objects.create(
            game=game,
            agent=agent,
            role='agent',
            content=result['dialogue'],
            season=game.current_season,
            metadata={
                'negotiation_id': session.id,
                'reasoning': result.get('reasoning', ''),
                'attitude_change': result.get('attitude_change', 0),
            },
        )

        # 5. Update affinity and memory
        AgentService._apply_chat_effects(agent, result)

        # 6. Check resolution
        resolved = False
        if not handoff_to_player and result.get('final_decision') is not None:
            resolved = True
            cls.resolve_session(session, result)
        elif not handoff_to_player and session.current_round >= session.max_rounds:
            # Fallback resolution
            resolved = True
            fallback_outcome = cls._fallback_resolution(session, result)
            cls.resolve_session(session, fallback_outcome)
            result['final_decision'] = fallback_outcome.get('final_decision')

        # 委托失败后玩家亲自谈成 → 能名+1
        _SUCCESS_DECISIONS = {'stop_annexation', 'accept', 'declare_all'}
        if (resolved
                and speaker_role == 'PLAYER'
                and session.context_data.get('delegate_failed_once')
                and result.get('final_decision') in _SUCCESS_DECISIONS):
            adjust_player_profile_stat(
                game,
                'competence',
                1,
                source_event='negotiation_player_salvage',
                source_label=f'{session.agent.name}谈判亲谈成功',
                extra_data={
                    'negotiation_id': session.id,
                    'event_type': session.event_type,
                    'final_decision': result.get('final_decision'),
                },
            )

        response = {
            'agent_name': agent.name,
            'dialogue': result['dialogue'],
            'round': session.current_round,
            'max_rounds': session.max_rounds,
            'status': 'resolved' if resolved else 'active',
            'final_decision': result.get('final_decision'),
            'event_type': session.event_type,
            'speaker_role': speaker_role,
            'handoff_to_player': handoff_to_player,
            'handoff_message': handoff_message,
        }

        if resolved:
            # Refresh game to get updated treasury (save may have used different ref)
            game.refresh_from_db()
            response['treasury'] = round(load_county_state(game, refresh=True).get('treasury', 0), 1)
            if session.event_type == 'IRRIGATION':
                response['contribution_offer'] = result.get('contribution_offer', 0)
            # Include generated summary (written by resolve_session → _generate_session_summary)
            session.refresh_from_db()
            summary = (session.outcome or {}).get('summary')
            if summary:
                response['summary'] = summary

        return response

    @classmethod
    def expire_stale_negotiations(cls, game, current_season=None):
        """Auto-close active negotiations that were inactive for >= 3 months."""
        season = int(current_season if current_season is not None else game.current_season or 0)
        sessions = list(
            NegotiationSession.objects.filter(game=game, status='active')
            .select_related('agent')
            .order_by('id')
        )
        if not sessions:
            return []

        event_type_map = dict(NegotiationSession.EVENT_TYPES)
        expired = []
        for session in sessions:
            last_activity = cls._last_activity_season(session)
            inactive_for = season - last_activity
            if inactive_for < NEGOTIATION_INACTIVE_SEASONS:
                continue

            session.status = 'resolved'
            session.resolved_at = timezone.now()
            session.outcome = {
                'final_decision': 'auto_close',
                'reason': 'inactive_timeout',
                'inactive_for_seasons': inactive_for,
                'timeout_seasons': NEGOTIATION_INACTIVE_SEASONS,
                'closed_season': season,
            }
            session.save(update_fields=['status', 'resolved_at', 'outcome'])

            event_type_name = event_type_map.get(session.event_type, session.event_type)
            desc = (
                f"{session.agent.name}的{event_type_name}谈判已连续"
                f"{inactive_for}个月未推进，系统自动关闭"
            )
            EventLog.objects.create(
                game=game,
                season=season,
                event_type='negotiation_auto_closed',
                category='NEGOTIATION',
                description=desc,
                data={
                    'negotiation_id': session.id,
                    'agent_name': session.agent.name,
                    'event_type': session.event_type,
                    'event_type_display': event_type_name,
                    'last_activity_season': last_activity,
                    'inactive_for_seasons': inactive_for,
                    'timeout_seasons': NEGOTIATION_INACTIVE_SEASONS,
                },
            )
            expired.append(
                {
                    'negotiation_id': session.id,
                    'agent_name': session.agent.name,
                    'event_type': session.event_type,
                    'event_type_display': event_type_name,
                    'inactive_for_seasons': inactive_for,
                }
            )
        return expired

    # ------------------------------------------------------------------
    # Annexation Negotiation
    # ------------------------------------------------------------------

    @classmethod
    def _negotiate_annexation(cls, ctx, game, session):
        """Process one annexation negotiation round via LLM."""
        cd = session.context_data
        ctx['current_pct'] = cd.get('current_pct', 0.35)
        proposed_increase = cd.get('proposed_pct_increase', 0.05)
        ctx['proposed_pct'] = ctx['current_pct'] + proposed_increase
        ctx['proposed_increase'] = proposed_increase

        template_name = 'negotiation_annexation'
        system_prompt, user_prompt = PromptRegistry.render(template_name, **ctx)

        messages = cls._build_negotiation_messages(
            system_prompt, user_prompt, game, session,
        )

        try:
            from llm.context import LLMContext
            from llm import call_sources
            client = LLMClient(context=LLMContext(
                call_source=call_sources.NEGOTIATION,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            result = client.chat_json(messages, temperature=0.8, max_tokens=512)
        except Exception as e:
            logger.warning("Negotiation LLM failed for %s: %s", session.agent.name, e)
            raw = getattr(e, 'raw_content', '') or ''
            dialogue = raw.strip()[:200] if raw and not raw.strip().startswith('{') else f'{session.agent.name}面色不善，沉默不语。'
            result = {
                'dialogue': dialogue,
                'reasoning': f'LLM调用失败: {e}',
                'attitude_change': 0,
                'willingness_to_stop': 0.3,
                'final_decision': None,
                'new_memory': '',
            }

        return cls._normalize_annexation_response(result)

    @classmethod
    def _normalize_annexation_response(cls, result):
        """Ensure annexation response has all required fields."""
        defaults = {
            'dialogue': '（沉默不语）',
            'reasoning': '',
            'attitude_change': 0,
            'willingness_to_stop': 0.3,
            'final_decision': None,
            'new_memory': '',
        }
        for key, default in defaults.items():
            if key not in result:
                result[key] = default

        # Clamp values
        try:
            result['attitude_change'] = max(-5, min(5, int(result['attitude_change'])))
        except (ValueError, TypeError):
            result['attitude_change'] = 0

        try:
            result['willingness_to_stop'] = max(0.0, min(1.0, float(result['willingness_to_stop'])))
        except (ValueError, TypeError):
            result['willingness_to_stop'] = 0.3

        # Validate final_decision
        if result['final_decision'] not in (None, 'stop_annexation', 'proceed_annexation'):
            result['final_decision'] = None

        return result

    # ------------------------------------------------------------------
    # Irrigation Negotiation
    # ------------------------------------------------------------------

    @classmethod
    def _negotiate_irrigation(cls, ctx, game, session):
        """Process one irrigation negotiation round via LLM."""
        cd = session.context_data
        ctx['max_contribution'] = cd.get('max_contribution', 20)

        template_name = 'negotiation_irrigation'
        system_prompt, user_prompt = PromptRegistry.render(template_name, **ctx)

        messages = cls._build_negotiation_messages(
            system_prompt, user_prompt, game, session,
        )

        try:
            from llm.context import LLMContext
            from llm import call_sources
            client = LLMClient(context=LLMContext(
                call_source=call_sources.NEGOTIATION,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            result = client.chat_json(messages, temperature=0.8, max_tokens=512)
        except Exception as e:
            logger.warning("Negotiation LLM failed for %s: %s", session.agent.name, e)
            raw = getattr(e, 'raw_content', '') or ''
            dialogue = raw.strip()[:200] if raw and not raw.strip().startswith('{') else f'{session.agent.name}捻须不语，似在盘算。'
            result = {
                'dialogue': dialogue,
                'reasoning': f'LLM调用失败: {e}',
                'attitude_change': 0,
                'contribution_offer': 0,
                'final_decision': None,
                'new_memory': '',
            }

        return cls._normalize_irrigation_response(result, session)

    @classmethod
    def _normalize_irrigation_response(cls, result, session):
        """Ensure irrigation response has all required fields."""
        max_contrib = session.context_data.get('max_contribution', 20)
        defaults = {
            'dialogue': '（沉默不语）',
            'reasoning': '',
            'attitude_change': 0,
            'contribution_offer': 0,
            'final_decision': None,
            'new_memory': '',
        }
        for key, default in defaults.items():
            if key not in result:
                result[key] = default

        try:
            result['attitude_change'] = max(-5, min(5, int(result['attitude_change'])))
        except (ValueError, TypeError):
            result['attitude_change'] = 0

        try:
            result['contribution_offer'] = max(0, min(max_contrib, int(result['contribution_offer'])))
        except (ValueError, TypeError):
            result['contribution_offer'] = 0

        if result['final_decision'] not in (None, 'accept', 'refuse'):
            result['final_decision'] = None

        return result

    # ------------------------------------------------------------------
    # Hidden Land Negotiation
    # ------------------------------------------------------------------

    @classmethod
    def _negotiate_hidden_land(cls, ctx, game, session):
        """Process one hidden land negotiation round via LLM."""
        cd = session.context_data
        ctx['hidden_land'] = cd.get('hidden_land', 0)
        ctx['current_farmland'] = cd.get('current_farmland', 0)
        ctx['current_gentry_pct'] = cd.get('current_gentry_pct', 0.3)

        template_name = 'negotiation_hidden_land'
        system_prompt, user_prompt = PromptRegistry.render(template_name, **ctx)

        messages = cls._build_negotiation_messages(
            system_prompt, user_prompt, game, session,
        )

        try:
            from llm.context import LLMContext
            from llm import call_sources
            client = LLMClient(context=LLMContext(
                call_source=call_sources.NEGOTIATION,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            result = client.chat_json(messages, temperature=0.8, max_tokens=512)
        except Exception as e:
            logger.warning("Negotiation LLM failed for %s: %s", session.agent.name, e)
            raw = getattr(e, 'raw_content', '') or ''
            dialogue = raw.strip()[:200] if raw and not raw.strip().startswith('{') else f'{session.agent.name}面色不善，沉默不语。'
            result = {
                'dialogue': dialogue,
                'reasoning': f'LLM调用失败: {e}',
                'attitude_change': 0,
                'willingness_to_declare': 0.3,
                'final_decision': None,
                'new_memory': '',
            }

        return cls._normalize_hidden_land_response(result)

    @classmethod
    def _normalize_hidden_land_response(cls, result):
        """Ensure hidden land response has all required fields."""
        defaults = {
            'dialogue': '（沉默不语）',
            'reasoning': '',
            'attitude_change': 0,
            'willingness_to_declare': 0.3,
            'final_decision': None,
            'new_memory': '',
        }
        for key, default in defaults.items():
            if key not in result:
                result[key] = default

        try:
            result['attitude_change'] = max(-5, min(5, int(result['attitude_change'])))
        except (ValueError, TypeError):
            result['attitude_change'] = 0

        try:
            result['willingness_to_declare'] = max(0.0, min(1.0, float(result['willingness_to_declare'])))
        except (ValueError, TypeError):
            result['willingness_to_declare'] = 0.3

        if result['final_decision'] not in (None, 'declare_all', 'refuse'):
            result['final_decision'] = None

        return result

    # ------------------------------------------------------------------
    # Shared Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @classmethod
    def _delegate_role_bonus(cls, speaker_role, event_type):
        table = {
            'ADVISOR': {'ANNEXATION': 0.10, 'HIDDEN_LAND': 0.12, 'IRRIGATION': 0.08},
            'DEPUTY': {'ANNEXATION': 0.06, 'HIDDEN_LAND': 0.08, 'IRRIGATION': 0.06},
        }
        return table.get(speaker_role, {}).get(event_type, 0.0)

    @classmethod
    def _delegate_trait_bonus(cls, delegate_agent):
        if delegate_agent is None:
            return 0.0
        attrs = delegate_agent.attributes or {}
        personality = attrs.get('personality', {}) or {}
        conscientiousness = max(0.0, min(1.0, cls._safe_float(personality.get('conscientiousness', 0.5), 0.5)))
        agreeableness = max(0.0, min(1.0, cls._safe_float(personality.get('agreeableness', 0.5), 0.5)))
        intelligence = max(0.0, min(1.0, cls._safe_float(attrs.get('intelligence', 5), 5) / 10.0))
        affinity = max(-99.0, min(99.0, cls._safe_float(attrs.get('player_affinity', 50), 50)))
        affinity_bonus = (affinity - 50.0) * 0.0006
        return 0.05 * conscientiousness + 0.03 * agreeableness + 0.03 * intelligence + affinity_bonus

    @classmethod
    def _delegate_handoff_message(cls, speaker_role):
        if speaker_role == 'ADVISOR':
            return '师爷交涉未果，请大人亲自出面。'
        return '县丞交涉未果，请大人亲自定夺。'

    @classmethod
    def _build_delegate_message(cls, session, speaker_role, delegate_agent=None):
        if speaker_role == 'PLAYER':
            return ''

        title = delegate_agent.name if delegate_agent else ('师爷' if speaker_role == 'ADVISOR' else '县丞')
        village_name = session.context_data.get('village_name') or session.agent.attributes.get('village_name', '')
        village_prefix = f'{village_name}一事' if village_name else '此事'

        templates = {
            'VILLAGE_REQ_SCHOOL': f'{title}奉县令之命前来听取{village_prefix}的建塾请愿，请把本村诉求直说。',
            'VILLAGE_REQ_TAX': f'{title}奉县令之命前来核实{village_prefix}的减税请愿，请陈明缘由。',
            'LANDLORD_DEMAND_FACILITY': f'{title}奉县令之命前来商议{village_prefix}的设施诉求，请把要求与顾虑明说。',
            'GENTRY_RELIEF_OFFER': f'{title}奉县令之命前来答复{village_prefix}的放粮提议，请把可行数目与条件说明。',
        }
        return templates.get(session.event_type, f'{title}奉县令之命前来会商，请直陈此事。')

    @classmethod
    def _evaluate_delegate_attempt(cls, session, result, speaker_role, delegate_agent):
        """Delegate handles one automatic attempt: success resolves, failure hands off."""
        event_type = session.event_type
        role_bonus = cls._delegate_role_bonus(speaker_role, event_type)
        trait_bonus = cls._delegate_trait_bonus(delegate_agent)

        if event_type in _REQUEST_STYLE_DELEGATION_TYPES:
            decision = result.get('final_decision')
            if decision in ('accept', 'refuse'):
                return {'success': True, 'final_decision': decision}
            return {'success': False, 'handoff_message': cls._delegate_handoff_message(speaker_role)}

        if event_type == 'ANNEXATION':
            willingness = max(0.0, min(1.0, cls._safe_float(result.get('willingness_to_stop', 0.3), 0.3)))
            score = willingness + role_bonus + trait_bonus
            success = result.get('final_decision') == 'stop_annexation' or score >= 0.68
            if success:
                return {'success': True, 'final_decision': 'stop_annexation'}
            return {'success': False, 'handoff_message': cls._delegate_handoff_message(speaker_role)}

        if event_type == 'HIDDEN_LAND':
            willingness = max(0.0, min(1.0, cls._safe_float(result.get('willingness_to_declare', 0.3), 0.3)))
            score = willingness + role_bonus + trait_bonus
            success = result.get('final_decision') == 'declare_all' or score >= 0.70
            if success:
                return {'success': True, 'final_decision': 'declare_all'}
            return {'success': False, 'handoff_message': cls._delegate_handoff_message(speaker_role)}

        max_contrib = max(1, int(session.context_data.get('max_contribution', 1)))
        offered = max(0, int(result.get('contribution_offer', 0) or 0))
        offer_ratio = offered / max_contrib
        score = offer_ratio + role_bonus + trait_bonus
        success = (result.get('final_decision') == 'accept' and offered > 0) or score >= 0.55
        if success:
            if offered <= 0:
                offered = max(1, int(max_contrib * 0.2))
            return {
                'success': True,
                'final_decision': 'accept',
                'contribution_offer': offered,
            }
        return {'success': False, 'handoff_message': cls._delegate_handoff_message(speaker_role)}

    @classmethod
    def _normalize_speaker_role(cls, speaker_role):
        role = (speaker_role or 'PLAYER').upper()
        if role not in cls.SPEAKER_ROLE_MAP:
            return 'PLAYER'
        return role

    @classmethod
    def _get_delegate_agent(cls, game, speaker_role):
        config = cls.SPEAKER_ROLE_MAP.get(speaker_role, {})
        agent_role = config.get('agent_role')
        if not agent_role:
            return None
        return Agent.objects.filter(game=game, role=agent_role).first()

    @classmethod
    def _format_player_message(cls, message, speaker_role, delegate_agent=None):
        if speaker_role == 'ADVISOR':
            speaker = delegate_agent.name if delegate_agent else '师爷'
            return f'（县令委托{speaker}代为交涉）{message}'
        if speaker_role == 'DEPUTY':
            speaker = delegate_agent.name if delegate_agent else '县丞'
            return f'（县令委托{speaker}代为交涉）{message}'
        return message

    @classmethod
    def _format_history_player_message(cls, msg):
        metadata = msg.metadata or {}
        speaker_role = cls._normalize_speaker_role(metadata.get('speaker_role', 'PLAYER'))
        if speaker_role == 'ADVISOR':
            speaker = metadata.get('speaker_name') or '师爷'
            return f'县令委托{speaker}对你说："{msg.content}"'
        if speaker_role == 'DEPUTY':
            speaker = metadata.get('speaker_name') or '县丞'
            return f'县令委托{speaker}对你说："{msg.content}"'
        return f'县令对你说："{msg.content}"'

    @classmethod
    def _build_negotiation_messages(cls, system_prompt, user_prompt, game, session):
        """Build message list with negotiation history."""
        messages = [{'role': 'system', 'content': system_prompt}]

        # Fetch recent negotiation dialogue (last 6 messages = 3 rounds of context)
        recent = DialogueMessage.objects.filter(
            game=game,
            agent=session.agent,
            metadata__negotiation_id=session.id,
        ).order_by('-created_at')[:6]

        # Exclude the player message we just saved (the latest one)
        history_msgs = list(reversed(recent))[:-1]
        for msg in history_msgs:
            if msg.role == 'player':
                messages.append({'role': 'user', 'content': cls._format_history_player_message(msg)})
            elif msg.role == 'agent':
                messages.append({'role': 'assistant', 'content': msg.content})

        messages.append({'role': 'user', 'content': user_prompt})
        return messages

    @classmethod
    def _last_activity_season(cls, session):
        latest_msg_season = (
            DialogueMessage.objects.filter(
                game=session.game,
                agent=session.agent,
                metadata__negotiation_id=session.id,
            )
            .order_by('-season', '-id')
            .values_list('season', flat=True)
            .first()
        )
        if latest_msg_season is None:
            return int(session.season or 0)
        return int(latest_msg_season)

    @classmethod
    def _fallback_resolution(cls, session, last_result):
        """Programmatic fallback when LLM fails to give final_decision at max round."""
        if session.event_type == 'ANNEXATION':
            wts = last_result.get('willingness_to_stop', 0.3)
            if wts >= 0.5:
                decision = 'stop_annexation'
            else:
                decision = 'proceed_annexation'
            return {
                'final_decision': decision,
                'willingness_to_stop': wts,
                'fallback': True,
            }
        elif session.event_type == 'HIDDEN_LAND':
            wtd = last_result.get('willingness_to_declare', 0.3)
            decision = 'declare_all' if wtd >= 0.5 else 'refuse'
            return {
                'final_decision': decision,
                'willingness_to_declare': wtd,
                'fallback': True,
            }
        elif session.event_type in ('VILLAGE_REQ_SCHOOL', 'VILLAGE_REQ_TAX',
                                    'LANDLORD_DEMAND_FACILITY', 'GENTRY_RELIEF_OFFER'):
            # NPC 请愿类：超轮未决时默认拒绝
            return {'final_decision': 'refuse', 'fallback': True}
        else:
            # IRRIGATION: use last contribution_offer
            offer = last_result.get('contribution_offer', 0)
            if offer > 0:
                decision = 'accept'
            else:
                decision = 'refuse'
            return {
                'final_decision': decision,
                'contribution_offer': offer,
                'fallback': True,
            }

    # ------------------------------------------------------------------
    # Resolution Effects
    # ------------------------------------------------------------------

    @classmethod
    def _apply_annexation_outcome(cls, session, outcome):
        """Apply annexation outcome to game state."""
        game = session.game
        county = load_county_state(game)
        ensure_county_ledgers(county)
        agent = session.agent
        village_name = agent.attributes.get('village_name', '')
        decision = outcome.get('final_decision', 'proceed_annexation')
        hidden_pop = 0
        annexed_land = 0

        for v in county['villages']:
            if v['name'] == village_name:
                ensure_village_ledgers(v)
                if decision == 'proceed_annexation':
                    increase = session.context_data.get('proposed_pct_increase', 0.05)
                    old_pct = v.get('gentry_land_pct', 0.3)
                    target_pct = min(0.8, old_pct + increase)

                    peasant = v['peasant_ledger']
                    gentry = v['gentry_ledger']
                    peasant_land_before = max(0, int(peasant.get('farmland', 0)))
                    gentry_land_before = max(0, int(gentry.get('registered_farmland', 0)))
                    total_registered_before = peasant_land_before + gentry_land_before

                    desired_gentry_land = int(round(total_registered_before * target_pct))
                    annexed_land = max(0, min(peasant_land_before, desired_gentry_land - gentry_land_before))

                    peasant['farmland'] = max(0, peasant_land_before - annexed_land)
                    gentry['registered_farmland'] = gentry_land_before + annexed_land
                    v['morale'] = max(0.0, min(100.0, float(v.get('morale', 50.0)) - 8))
                    # 隐匿户口 (doc 06a §3.2): 按兼并自耕地比例从村民账本转入地主隐匿人口
                    peasant_pop_before = max(0, int(peasant.get('registered_population', v.get('population', 0))))
                    transfer_ratio = annexed_land / max(peasant_land_before, 1)
                    hidden_pop = int(peasant_pop_before * transfer_ratio)
                    peasant['registered_population'] = max(0, peasant_pop_before - hidden_pop)
                    gentry['hidden_population'] = max(0, int(gentry.get('hidden_population', 0)) + hidden_pop)
                    # legacy fields
                    sync_legacy_from_ledgers(v)
                    # Gentry affinity +5 (they got what they wanted)
                    attrs = agent.attributes
                    attrs['player_affinity'] = min(99, attrs.get('player_affinity', 50) + 5)
                    agent.attributes = attrs
                    agent.save(update_fields=['attributes'])
                else:
                    # Stopped — gentry affinity -8
                    attrs = agent.attributes
                    attrs['player_affinity'] = max(-99, attrs.get('player_affinity', 50) - 8)
                    agent.attributes = attrs
                    agent.save(update_fields=['attributes'])
                break

        sync_county_gentry_land_ratio(county)
        # Model A：村级民心已变更，立即同步县级聚合值
        MetricsMixin._sync_county_from_villages(county, 'morale')
        save_player_state(game, county)

        # 威名效果：玩家赢（停止兼并）时，地主有概率向知府投诉
        if decision == 'stop_annexation':
            cls._check_gentry_complaint(game, county, agent)

        desc = (f'{village_name}地主{agent.name}兼并谈判结束：'
                f'{"继续兼并" if decision == "proceed_annexation" else "停止兼并"}'
                f'{f"，兼并耕地{annexed_land}亩" if annexed_land > 0 else ""}'
                f'{f"，隐匿户口{hidden_pop}人" if hidden_pop > 0 else ""}')
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='annexation_outcome',
            category='NEGOTIATION',
            description=desc,
            data={
                'agent_name': agent.name,
                'village_name': village_name,
                'decision': decision,
                'hidden_pop': hidden_pop,
                'annexed_land': annexed_land,
            },
        )

    @classmethod
    def _apply_hidden_land_outcome(cls, session, outcome):
        """Apply hidden land negotiation outcome to game state (doc 06a §2.4)."""
        game = session.game
        county = load_county_state(game)
        ensure_county_ledgers(county)
        agent = session.agent
        village_name = agent.attributes.get('village_name', '')
        decision = outcome.get('final_decision', 'refuse')

        discovered = 0
        for v in county['villages']:
            if v['name'] == village_name:
                ensure_village_ledgers(v)
                gentry = v['gentry_ledger']
                hidden = max(0, int(gentry.get('hidden_farmland', v.get('hidden_land', 0))))

                if decision == 'declare_all':
                    discovered = hidden
                    # 地主主动上报，百姓略感官府有所作为：目标村民心+1
                    v['morale'] = max(0.0, min(100.0, float(v.get('morale', 50.0)) + 1))
                    # Gentry affinity -3 (reluctant compliance)
                    attrs = agent.attributes
                    attrs['player_affinity'] = max(-99, attrs.get('player_affinity', 50) - 3)
                    agent.attributes = attrs
                    agent.save(update_fields=['attributes'])
                else:
                    # Forced survey: discover 50-90%
                    morale_score = max(0.0, min(1.0, v.get('morale', 50) / 100))
                    bailiff_score = max(0.0, min(1.0, county.get('bailiff_level', 0) / 3))
                    try:
                        knowledge_raw = float(getattr(game.player, 'knowledge', 0))
                    except Exception:
                        knowledge_raw = 0.0
                    knowledge_score = max(0.0, min(1.0, knowledge_raw / 10))
                    quality = 0.35 * morale_score + 0.35 * bailiff_score + 0.30 * knowledge_score
                    ratio = 0.5 + quality * 0.4 + random.uniform(-0.03, 0.03)
                    ratio = max(0.5, min(0.9, ratio))
                    discovered = int(hidden * ratio)
                    # 强制清丈，百姓见官府为民做主：目标村民心+3
                    v['morale'] = max(0.0, min(100.0, float(v.get('morale', 50.0)) + 3))
                    # 强制清丈：地主好感下降（适度惩罚，与兼并停止-8拉开但不过重）
                    attrs = agent.attributes
                    attrs['player_affinity'] = max(-99, attrs.get('player_affinity', 50) - 10)
                    agent.attributes = attrs
                    agent.save(update_fields=['attributes'])

                gentry['registered_farmland'] = max(0, int(gentry.get('registered_farmland', 0)) + discovered)
                gentry['hidden_farmland'] = max(0, hidden - discovered)
                v['hidden_land_discovered'] = True
                sync_legacy_from_ledgers(v)
                break

        sync_county_gentry_land_ratio(county)
        # Model A：村级民心已变更，立即同步县级聚合值
        MetricsMixin._sync_county_from_villages(county, 'morale')
        save_player_state(game, county)

        # 威名效果：强制清丈成功 → 威名+1；地主有概率向知府投诉
        if decision != 'declare_all':
            try:
                player = game.player
                player.authority = min(100, player.authority + 1)
                player.save(update_fields=['authority'])
            except Exception:
                pass
            cls._check_gentry_complaint(game, county, agent)

        method = '主动申报' if decision == 'declare_all' else '强制清丈'
        desc = (f'{village_name}地主{agent.name}隐匿土地交涉结束：'
                f'{method}，发现隐田{discovered}亩')
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='hidden_land_outcome',
            category='HIDDEN_LAND',
            description=desc,
            data={
                'agent_name': agent.name,
                'village_name': village_name,
                'decision': decision,
                'discovered': discovered,
            },
        )

    @classmethod
    def _apply_irrigation_outcome(cls, session, outcome):
        """Apply irrigation outcome to game state."""
        game = session.game
        county = load_county_state(game)
        agent = session.agent
        decision = outcome.get('final_decision', 'refuse')
        contribution = outcome.get('contribution_offer', 0)

        village_name = agent.attributes.get('village_name', '')

        # Always record this village as negotiated (regardless of outcome)
        for inv in county.get('active_investments', []):
            if inv.get('action') == 'build_irrigation':
                inv.setdefault('negotiated_villages', [])
                if village_name not in inv['negotiated_villages']:
                    inv['negotiated_villages'].append(village_name)
                break

        if decision == 'accept' and contribution > 0:
            # Refund contribution to treasury
            county['treasury'] += contribution

            # Record contribution on active investment
            for inv in county.get('active_investments', []):
                if inv.get('action') == 'build_irrigation':
                    inv.setdefault('gentry_contributions', [])
                    inv['gentry_contributions'].append({
                        'agent_id': agent.id,
                        'agent_name': agent.name,
                        'village_name': village_name,
                        'amount': contribution,
                    })
                    break

            # Gentry affinity decreases proportional to contribution extracted
            max_contrib = session.context_data.get('max_contribution', 20)
            if max_contrib > 0:
                # schoolbook round-half-up (avoid Python banker's rounding: round(2.5)==2)
                affinity_loss = max(1, int(5 * contribution / max_contrib + 0.5))
                attrs = agent.attributes
                attrs['player_affinity'] = max(-99, attrs.get('player_affinity', 50) - affinity_loss)
                agent.attributes = attrs
                agent.save(update_fields=['attributes'])

        save_player_state(game, county)

        desc = (f'{agent.attributes.get("village_name", "")}地主{agent.name}'
                f'水利协商结束：{"同意出资" + str(contribution) + "两" if decision == "accept" else "拒绝出资"}')
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='irrigation_outcome',
            category='NEGOTIATION',
            description=desc,
            data={
                'agent_name': agent.name,
                'decision': decision,
                'contribution': contribution,
                'treasury_after': round(county.get('treasury', 0), 1),
            },
        )

    # ------------------------------------------------------------------
    # Authority (威名) — Gentry Complaint
    # ------------------------------------------------------------------

    @classmethod
    def _check_gentry_complaint(cls, game, county, agent):
        """有概率触发地主向知府打报告（隐性事件）。

        触发条件：玩家威名 ≥ 60，地主声望.authority ≥ 55。
        概率：base 10% + 每点威名超出60加0.5%；地主抵抗意志高则再加0-10%。
        后果：county['prefect_complaints'] +1，写入隐性 EventLog。
        """
        try:
            player_authority = game.player.authority
        except Exception:
            return

        if player_authority < 60:
            return

        gentry_authority = int(
            (agent.attributes.get('reputation') or {}).get('authority', 0)
        )
        if gentry_authority < 55:
            return

        base_prob = 0.10 + (player_authority - 60) * 0.005
        gentry_factor = max(0, (gentry_authority - 55) / 45) * 0.10
        prob = min(0.40, base_prob + gentry_factor)

        if random.random() >= prob:
            return

        county['prefect_complaints'] = county.get('prefect_complaints', 0) + 1
        save_player_state(game, county)

        logger.info(
            "威名事件：地主%s向知府投诉（p=%.0f%%，当前累计=%d）",
            agent.name, prob * 100, county['prefect_complaints'],
        )
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='gentry_complaint',
            category='GENTRY_COMPLAINT',
            description=f'地主{agent.name}不满县令强硬手段，已悄然向知府陈情告状',
            data={
                'agent_name': agent.name,
                'player_authority': player_authority,
                'gentry_authority': gentry_authority,
                'prob': round(prob, 3),
                'cumulative_complaints': county['prefect_complaints'],
            },
        )

    # ------------------------------------------------------------------
    # 村民请愿·建村塾（VILLAGE_REQ_SCHOOL）
    # ------------------------------------------------------------------

    @classmethod
    def _negotiate_village_req_school(cls, ctx, game, session):
        """村民里长向知县请愿建村塾——轻量LLM对话。"""
        cd = session.context_data
        ctx['schools_elsewhere'] = cd.get('schools_elsewhere', 0)

        system_prompt, user_prompt = PromptRegistry.render('npc_request_school', **ctx)
        messages = cls._build_negotiation_messages(system_prompt, user_prompt, game, session)

        try:
            from llm.context import LLMContext
            from llm import call_sources
            client = LLMClient(context=LLMContext(
                call_source=call_sources.NEGOTIATION,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            result = client.chat_json(messages, temperature=0.75, max_tokens=384)
        except Exception as e:
            logger.warning("VILLAGE_REQ_SCHOOL LLM failed: %s", e)
            result = {
                'dialogue': f'{session.agent.name}期待地看着县令，等待回应。',
                'attitude_change': 0,
                'final_decision': None,
                'new_memory': '',
            }
        return cls._normalize_npc_request_response(result)

    @classmethod
    def _apply_village_req_school_outcome(cls, session, outcome):
        """村塾请愿结算：民心微调；答应则直接生成 BUILD_SCHOOL 承诺。"""
        from .state import load_county_state, save_player_state
        from .settlement_metrics import MetricsMixin
        from ..models import Promise
        game = session.game
        county = load_county_state(game)
        decision = outcome.get('final_decision')
        village_name = session.context_data.get('village_name', '')

        for v in county.get('villages', []):
            if v['name'] == village_name:
                if decision == 'accept':
                    v['morale'] = min(100.0, float(v.get('morale', 50)) + 3)
                else:
                    v['morale'] = max(0.0, float(v.get('morale', 50)) - 3)
                break

        MetricsMixin._sync_county_from_villages(county, 'morale')
        save_player_state(game, county)

        EventLog.objects.create(
            game=game, season=game.current_season,
            event_type='village_req_school_outcome',
            category='SOCIAL',
            description=f'{village_name}建塾请愿：{"县令应允" if decision == "accept" else "县令婉拒"}',
            data={'village_name': village_name, 'decision': decision},
        )

        # 答应请愿 → 直接创建 BUILD_SCHOOL 承诺（不依赖 LLM 提取）
        if decision == 'accept':
            already_exists = Promise.objects.filter(
                game=game,
                promise_type='BUILD_SCHOOL',
                status='PENDING',
                context__target_village=village_name,
            ).exists()
            if not already_exists:
                deadline_season = game.current_season + 8  # 宽限8个月；建设中不计违约
                Promise.objects.create(
                    game=game,
                    agent=session.agent,
                    negotiation=session,
                    promise_type='BUILD_SCHOOL',
                    description=f'为{village_name}兴建村塾',
                    status='PENDING',
                    season_made=game.current_season,
                    deadline_season=deadline_season,
                    context={'target_village': village_name},
                )
                EventLog.objects.create(
                    game=game,
                    season=game.current_season,
                    event_type='promise_made',
                    category='PROMISE',
                    description=f'县令向{session.agent.name}承诺：为{village_name}兴建村塾（截止第{deadline_season}月）',
                    data={
                        'promise_type': 'BUILD_SCHOOL',
                        'agent_name': session.agent.name,
                        'village_name': village_name,
                        'deadline_season': deadline_season,
                    },
                )

    # ------------------------------------------------------------------
    # 村民请愿·减税（VILLAGE_REQ_TAX）
    # ------------------------------------------------------------------

    @classmethod
    def _negotiate_village_req_tax(cls, ctx, game, session):
        """村民里长向知县请愿减税——轻量LLM对话。"""
        cd = session.context_data
        ctx['agri_suitability_pct'] = round(cd.get('agri_suitability', 0.5) * 100)
        ctx['current_tax_pct'] = round(cd.get('current_tax_rate', 0.12) * 100, 1)

        system_prompt, user_prompt = PromptRegistry.render('npc_request_tax', **ctx)
        messages = cls._build_negotiation_messages(system_prompt, user_prompt, game, session)

        try:
            from llm.context import LLMContext
            from llm import call_sources
            client = LLMClient(context=LLMContext(
                call_source=call_sources.NEGOTIATION,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            result = client.chat_json(messages, temperature=0.75, max_tokens=384)
        except Exception as e:
            logger.warning("VILLAGE_REQ_TAX LLM failed: %s", e)
            result = {
                'dialogue': f'{session.agent.name}双手合十，面露愁容。',
                'attitude_change': 0,
                'final_decision': None,
                'new_memory': '',
            }
        return cls._normalize_npc_request_response(result)

    @classmethod
    def _apply_village_req_tax_outcome(cls, session, outcome):
        """减税请愿结算：接受则直接降税至当前税率的80%；拒绝则民心下降。"""
        from .state import load_county_state, save_player_state
        from .settlement_metrics import MetricsMixin
        game = session.game
        county = load_county_state(game)
        decision = outcome.get('final_decision')

        if decision == 'accept':
            old_rate = county.get('tax_rate', 0.12)
            new_rate = round(old_rate * 0.80, 4)
            new_rate = max(0.05, new_rate)  # 最低5%税率
            # 记录税收缺口（用于推算上交知府的税收压力）
            county['tax_gap'] = county.get('tax_gap', 0) + round(old_rate - new_rate, 4)
            county['tax_rate'] = new_rate
            # 民心微升
            MetricsMixin.apply_county_stat_delta(county, 'morale', 5)
            desc = f'减税请愿：县令应允，税率从{old_rate:.1%}降至{new_rate:.1%}'
        else:
            MetricsMixin.apply_county_stat_delta(county, 'morale', -8)
            desc = '减税请愿：县令拒绝，民心下降'

        save_player_state(game, county)
        EventLog.objects.create(
            game=game, season=game.current_season,
            event_type='village_req_tax_outcome',
            category='SOCIAL',
            description=desc,
            data={'decision': decision, 'new_tax_rate': county.get('tax_rate')},
        )

    # ------------------------------------------------------------------
    # 地主要求·升级公共设施（LANDLORD_DEMAND_FACILITY）
    # ------------------------------------------------------------------

    @classmethod
    def _negotiate_landlord_demand_facility(cls, ctx, game, session):
        """地主要求升级公共设施——轻量LLM对话。"""
        cd = session.context_data
        ctx['low_facilities'] = cd.get('low_facilities', '相关设施')

        system_prompt, user_prompt = PromptRegistry.render('npc_demand_facility', **ctx)
        messages = cls._build_negotiation_messages(system_prompt, user_prompt, game, session)

        try:
            from llm.context import LLMContext
            from llm import call_sources
            client = LLMClient(context=LLMContext(
                call_source=call_sources.NEGOTIATION,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            result = client.chat_json(messages, temperature=0.80, max_tokens=384)
        except Exception as e:
            logger.warning("LANDLORD_DEMAND_FACILITY LLM failed: %s", e)
            result = {
                'dialogue': f'{session.agent.name}眉头微皱，等待县令表态。',
                'attitude_change': 0,
                'final_decision': None,
                'new_memory': '',
            }
        return cls._normalize_npc_request_response(result)

    @classmethod
    def _apply_landlord_demand_facility_outcome(cls, session, outcome):
        """升级设施要求结算：接受则民心/好感+5；拒绝则-5。承诺由 PromiseService 提取。"""
        from .state import load_county_state, save_player_state
        from .settlement_metrics import MetricsMixin
        game = session.game
        county = load_county_state(game)
        agent = session.agent
        decision = outcome.get('final_decision')
        village_name = session.context_data.get('village_name', '')

        delta = 5 if decision == 'accept' else -5
        MetricsMixin.apply_county_stat_delta(county, 'morale', delta)
        save_player_state(game, county)

        # 好感度调整
        attrs = agent.attributes or {}
        attrs['player_affinity'] = max(-99, min(99, int(attrs.get('player_affinity', 50)) + delta))
        agent.attributes = attrs
        agent.save(update_fields=['attributes'])

        EventLog.objects.create(
            game=game, season=game.current_season,
            event_type='landlord_demand_facility_outcome',
            category='SOCIAL',
            description=f'{village_name}地主{agent.name}升级设施要求：{"县令承诺改善" if decision == "accept" else "县令拒绝"}',
            data={'village_name': village_name, 'decision': decision,
                  'low_facilities': session.context_data.get('low_facilities', '')},
        )

    # ------------------------------------------------------------------
    # G3: 地主主动救济·开仓放粮（GENTRY_RELIEF_OFFER）
    # ------------------------------------------------------------------

    @classmethod
    def _negotiate_gentry_relief_offer(cls, ctx, game, session):
        """地主主动提出开仓救济——LLM对话。"""
        cd = session.context_data
        ctx['disaster_type'] = cd.get('disaster_type', '灾情')
        ctx['grain_surplus'] = cd.get('grain_surplus', 0.0)
        ctx['relief_estimate'] = cd.get('relief_estimate', 0.0)

        system_prompt, user_prompt = PromptRegistry.render('npc_gentry_relief_offer', **ctx)
        messages = cls._build_negotiation_messages(system_prompt, user_prompt, game, session)

        try:
            from llm.context import LLMContext
            from llm import call_sources
            client = LLMClient(context=LLMContext(
                call_source=call_sources.NEGOTIATION,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            result = client.chat_json(messages, temperature=0.80, max_tokens=384)
        except Exception as e:
            logger.warning("GENTRY_RELIEF_OFFER LLM failed: %s", e)
            result = {
                'dialogue': f'{session.agent.name}捻须静候，等待知县大人回应。',
                'attitude_change': 0,
                'final_decision': None,
                'new_memory': '',
            }
        return cls._normalize_npc_request_response(result)

    @classmethod
    def _apply_gentry_relief_offer_outcome(cls, session, outcome):
        """G3结算：
        accept — 地主affinity+5；按grain_surplus 15-25%释放余粮入民仓；
                  本县所有VILLAGER agent追加memory记录 + affinity+3；
                  写EventLog。
        refuse — 地主affinity-3；无粮食变化；写EventLog。
        """
        from .ledger import refresh_village_grain_ledgers
        game = session.game
        county = load_county_state(game)
        agent = session.agent
        decision = outcome.get('final_decision')
        village_name = session.context_data.get('village_name', '')
        cd = session.context_data

        if decision == 'accept':
            # ── 释放余粮 ──
            grain_surplus = cd.get('grain_surplus', 0.0)
            ratio = random.uniform(0.15, 0.25)
            released = round(grain_surplus * ratio, 1)

            # 从地主账本扣除，加入村民粮仓
            for v in county.get('villages', []):
                if v['name'] == village_name:
                    gentry_ledger = v.setdefault('gentry_ledger', {})
                    current_surplus = float(gentry_ledger.get('grain_surplus', 0.0))
                    gentry_ledger['grain_surplus'] = max(0.0, round(current_surplus - released, 1))
                    break

            county['peasant_grain_reserve'] = round(
                float(county.get('peasant_grain_reserve', 0.0)) + released, 1
            )
            refresh_village_grain_ledgers(county, current_season=game.current_season, seed_gentry_if_needed=False)

            # ── 地主 affinity +5 ──
            attrs = dict(agent.attributes or {})
            old_affinity = float(attrs.get('player_affinity', 50.0))
            attrs['player_affinity'] = min(99.0, old_affinity + 5.0)
            memory = list(attrs.get('memory', []))
            memory.append(f'主动开仓放粮{round(released)}斤，知县当场嘉奖并记录在案')
            attrs['memory'] = memory[-20:]
            agent.attributes = attrs
            agent.save(update_fields=['attributes'])

            # ── 本县所有 VILLAGER agent：追加 memory + affinity+3 ──
            villager_agents = list(Agent.objects.filter(game=game, role='VILLAGER'))
            for va in villager_agents:
                va_attrs = dict(va.attributes or {})
                va_affinity = float(va_attrs.get('player_affinity', 50.0))
                va_attrs['player_affinity'] = min(99.0, va_affinity + 3.0)
                va_memory = list(va_attrs.get('memory', []))
                va_memory.append(
                    f'{village_name}地主{agent.name}开仓放粮{round(released)}斤救济乡里，'
                    f'知县嘉奖有加，乡邻感念此义举'
                )
                va_attrs['memory'] = va_memory[-20:]
                va.attributes = va_attrs
            if villager_agents:
                Agent.objects.bulk_update(villager_agents, ['attributes'])

            save_player_state(game, county)

            desc = (
                f'{village_name}地主{agent.name}主动开仓放粮{round(released)}斤，'
                f'知县予以当面嘉奖并记录善举'
            )
            EventLog.objects.create(
                game=game,
                season=game.current_season,
                event_type='gentry_relief_offer_accepted',
                category='SOCIAL',
                description=desc,
                data={
                    'agent_name': agent.name,
                    'village_name': village_name,
                    'released': released,
                    'grain_surplus_before': grain_surplus,
                    'decision': decision,
                },
            )
        else:
            # ── 拒绝：地主 affinity -3 ──
            attrs = dict(agent.attributes or {})
            old_affinity = float(attrs.get('player_affinity', 50.0))
            attrs['player_affinity'] = max(-99.0, old_affinity - 3.0)
            memory = list(attrs.get('memory', []))
            memory.append('本欲开仓救济乡里，知县态度冷漠，此事作罢')
            attrs['memory'] = memory[-20:]
            agent.attributes = attrs
            agent.save(update_fields=['attributes'])

            EventLog.objects.create(
                game=game,
                season=game.current_season,
                event_type='gentry_relief_offer_refused',
                category='SOCIAL',
                description=f'{village_name}地主{agent.name}主动提出开仓救济，县令未予接纳',
                data={
                    'agent_name': agent.name,
                    'village_name': village_name,
                    'decision': decision,
                },
            )

    # ------------------------------------------------------------------
    # 辅助：NPC请愿类响应归一化
    # ------------------------------------------------------------------

    @classmethod
    def _normalize_npc_request_response(cls, result):
        """对话请愿类统一归一化（比谈判类更简洁）。"""
        defaults = {
            'dialogue': '（沉默不语）',
            'attitude_change': 0,
            'final_decision': None,
            'new_memory': '',
        }
        for key, default in defaults.items():
            if key not in result:
                result[key] = default
        try:
            result['attitude_change'] = max(-5, min(5, int(result['attitude_change'])))
        except (ValueError, TypeError):
            result['attitude_change'] = 0
        if result['final_decision'] not in (None, 'accept', 'refuse'):
            result['final_decision'] = None
        return result

    # ------------------------------------------------------------------
    # Chat History
    # ------------------------------------------------------------------

    @classmethod
    def get_negotiation_history(cls, session):
        """Return negotiation dialogue history including advisor briefs and NPC opening."""
        messages = DialogueMessage.objects.filter(
            game=session.game,
            agent=session.agent,
            metadata__negotiation_id=session.id,
        ).order_by('created_at')

        return [
            {
                'role': m.role,
                'content': m.content,
                'speaker_role': (m.metadata or {}).get('speaker_role', 'PLAYER'),
                'speaker_name': (m.metadata or {}).get('speaker_name', ''),
                'advisor_name': (m.metadata or {}).get('advisor_name', ''),
                'is_opening': (m.metadata or {}).get('is_opening', False),
                'is_advisor_brief': (m.metadata or {}).get('is_advisor_brief', False),
                'season': m.season,
                'created_at': m.created_at.isoformat(),
            }
            for m in messages
        ]

    # ------------------------------------------------------------------
    # Opening Messages & Advisor Brief
    # ------------------------------------------------------------------

    @classmethod
    def _generate_npc_opening(cls, game, session):
        """NPC 主动发起型：在会话开始时异步生成 NPC 的开场陈情，保存为第一条 agent 消息。"""
        try:
            event_type = session.event_type
            cd = session.context_data or {}
            agent = session.agent

            ctx = AgentService.build_system_context(agent, game)
            ctx['current_round'] = 1
            ctx['max_rounds'] = session.max_rounds
            ctx['round_pressure'] = ''

            if event_type == 'VILLAGE_REQ_SCHOOL':
                ctx['schools_elsewhere'] = cd.get('schools_elsewhere', 0)
                system_prompt, _ = PromptRegistry.render('npc_request_school', **ctx)
            elif event_type == 'VILLAGE_REQ_TAX':
                ctx['agri_suitability_pct'] = round(cd.get('agri_suitability', 0.5) * 100)
                ctx['current_tax_pct'] = round(cd.get('current_tax_rate', 0.12) * 100, 1)
                system_prompt, _ = PromptRegistry.render('npc_request_tax', **ctx)
            elif event_type == 'LANDLORD_DEMAND_FACILITY':
                ctx['low_facilities'] = cd.get('low_facilities', '相关设施')
                system_prompt, _ = PromptRegistry.render('npc_demand_facility', **ctx)
            elif event_type == 'GENTRY_RELIEF_OFFER':
                ctx['disaster_type'] = cd.get('disaster_type', '灾情')
                ctx['grain_surplus'] = cd.get('grain_surplus', 0.0)
                ctx['relief_estimate'] = cd.get('relief_estimate', 0.0)
                system_prompt, _ = PromptRegistry.render('npc_gentry_relief_offer', **ctx)
            else:
                return

            opening_user = (
                '你刚刚来到县衙，正在觐见知县大人。这是初次觐见，请主动开口，'
                '以你的身份陈述此番来意（古风口吻，60-80字，语气须符合你的性格特征）。\n\n'
                '（以JSON格式回复，仅包含 dialogue 字段，不要有JSON之外的任何文字）'
            )
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': opening_user},
            ]
            from llm.context import LLMContext
            from llm import call_sources
            client = LLMClient(context=LLMContext(
                call_source=call_sources.NEGOTIATION,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            result = client.chat_json(messages, temperature=0.85, max_tokens=200)
            dialogue = (result.get('dialogue') or '').strip()
            if not dialogue:
                dialogue = f'草民{agent.name}拜见大人，有要事禀报。'
        except Exception as e:
            logger.warning("NPC opening generation failed: %s", e)
            dialogue = f'草民{session.agent.name}拜见大人，有要事禀报。'

        try:
            DialogueMessage.objects.create(
                game=game,
                agent=session.agent,
                role='agent',
                content=dialogue,
                season=game.current_season,
                metadata={
                    'negotiation_id': session.id,
                    'is_opening': True,
                },
            )
            # G3 专属：NPC 开场后注入一条师爷提示，提醒玩家正确应对策略
            if event_type == 'GENTRY_RELIEF_OFFER':
                advisor = Agent.objects.filter(game=game, role='ADVISOR').first()
                advisor_name = advisor.name if advisor else '师爷'
                steward_hint = (
                    f'【{advisor_name}提示】此地主好意难得，大人宜先当面嘉奖，'
                    '以示体察民情之志；接受其善举后，日后可酌情荐举其族中子弟或减其徭役，'
                    '方为驭人之道，切不可冷漠推辞。'
                )
                DialogueMessage.objects.create(
                    game=game,
                    agent=session.agent,
                    role='advisor',
                    content=steward_hint,
                    season=game.current_season,
                    metadata={
                        'negotiation_id': session.id,
                        'is_advisor_brief': True,
                        'advisor_name': advisor_name,
                    },
                )
        except Exception as e:
            logger.warning("Failed to save NPC opening message: %s", e)

    @classmethod
    def prefetch_irrigation_briefs(cls, game, county):
        """兴建水利投资创建时调用：异步为所有村庄预生成师爷摘要，缓存到 county_data['irrigation_advisor_briefs']。"""
        villages = [v for v in county.get('villages', []) if v.get('name')]

        def _run():
            advisor = Agent.objects.filter(game=game, role='ADVISOR').first()
            advisor_name = advisor.name if advisor else '师爷'
            for village in villages:
                village_name = village['name']
                try:
                    gentry = Agent.objects.filter(
                        game=game, role='GENTRY', attributes__village_name=village_name,
                    ).first()
                    if not gentry:
                        continue
                    max_c = max(1, min(
                        int(village.get('farmland', 0) * village.get('gentry_land_pct', 0.3) * 0.0075), 40,
                    ))
                    situation = (
                        f'县衙正筹建水利，{village_name}地主{gentry.name}经估算最多可出资{max_c}两。'
                        f'水利建成后其田产浇灌亦将受益，大人需邀其出资共建。'
                    )
                    goal = f'争取{gentry.name}出资，金额越高越好，上限{max_c}两。'
                    chips = '晓以利害（水利受益直接惠及其田产）；可给予象征性回报；可言明知府亦关注此事。'
                    system = (
                        f'你是{advisor_name}，随大人赴任此地的师爷，熟谙官场事务，处事老练。'
                        f'请以师爷身份，用古风口吻向大人简要分析此次交涉形势，给出建议。'
                    )
                    user = (
                        f'【此次交涉概况】{situation}\n【目标】{goal}\n【可用筹码】{chips}\n\n'
                        f'请向大人简要陈述分析与建议，不超过120字，古风口吻。\n'
                        '（以JSON格式回复：{"brief": "师爷的分析与建议"}）'
                    )
                    from llm.context import LLMContext
                    from llm import call_sources
                    client = LLMClient(context=LLMContext(
                        call_source=call_sources.NEGOTIATION,
                        game_id=game.id,
                        season=game.current_season,
                        user_id=game.user_id,
                    ))
                    result = client.chat_json(
                        [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                        temperature=0.7, max_tokens=256,
                    )
                    brief = (result.get('brief') or '').strip() or f'大人，此番与{gentry.name}交涉，还请从容应对，把握分寸。'
                except Exception as e:
                    logger.warning("Prefetch irrigation brief failed for %s: %s", village_name, e)
                    brief = '大人，请注意此番交涉的目标，从容应对。'
                try:
                    cd = load_county_state(game)
                    cd.setdefault('irrigation_advisor_briefs', {})[village_name] = {
                        'brief': brief, 'advisor_name': advisor_name,
                    }
                    save_player_state(game, cd)
                except Exception as e:
                    logger.warning("Failed to cache irrigation brief for %s: %s", village_name, e)

        threading.Thread(target=_run, daemon=True).start()

    @classmethod
    def _pop_cached_irrigation_brief(cls, game, village_name):
        """取出并删除缓存的水利师爷摘要（一次性使用）。返回 {'brief': ..., 'advisor_name': ...} 或 None。"""
        try:
            cd = load_county_state(game)
            briefs = cd.get('irrigation_advisor_briefs') or {}
            cached = briefs.pop(village_name, None)
            if cached:
                save_player_state(game, cd)
            return cached
        except Exception as e:
            logger.warning("Failed to pop cached irrigation brief for %s: %s", village_name, e)
            return None

    @classmethod
    def _generate_advisor_brief(cls, game, session):
        """玩家发起型：在会话开始时异步生成师爷提示，保存为 role='advisor' 消息。"""
        try:
            event_type = session.event_type
            cd = session.context_data or {}
            agent = session.agent
            village_name = agent.attributes.get('village_name', '') or cd.get('village_name', '')
            agent_name = agent.name

            advisor = Agent.objects.filter(game=game, role='ADVISOR').first()
            advisor_name = advisor.name if advisor else '师爷'

            if event_type == 'ANNEXATION':
                cur = cd.get('current_pct', 0.35)
                inc = cd.get('proposed_pct_increase', 0.05)
                situation = (
                    f'{village_name}地主{agent_name}趁民心低迷大量收购村民田产，'
                    f'其占地比已达{cur:.0%}，拟再增{inc:.0%}至{cur + inc:.0%}。大人须出面交涉令其停止。'
                )
                goal = '说服地主停止兼并，保护村民田产。'
                chips = '可晓以法度（兼并过甚官府有权干预）；若威名高可隐然施压；可允小利换大局。'
            elif event_type == 'IRRIGATION':
                max_c = cd.get('max_contribution', 20)
                situation = (
                    f'县衙正筹建水利，{village_name}地主{agent_name}经估算最多可出资{max_c}两。'
                    f'水利建成后其田产浇灌亦将受益，大人需邀其出资共建。'
                )
                goal = f'争取{agent_name}出资，金额越高越好，上限{max_c}两。'
                chips = '晓以利害（水利受益直接惠及其田产）；可给予象征性回报；可言明知府亦关注此事。'
            elif event_type == 'HIDDEN_LAND':
                hidden = cd.get('hidden_land', 0)
                situation = (
                    f'本衙核查地籍，发现{village_name}地主{agent_name}藏匿了约{hidden}亩田产未申报。'
                    f'大人需其主动申报，否则官府将强制清丈。'
                )
                goal = '使地主主动申报全部隐田，减少对抗，维护官府权威。'
                chips = '明告主动申报可保体面免追查；警告拒绝则强制清丈并损名声；可给数日宽限期。'
            else:
                return

            system = (
                f'你是{advisor_name}，随大人赴任此地的师爷，熟谙官场事务，处事老练。'
                f'请以师爷身份，用古风口吻向大人简要分析此次交涉形势，给出建议。'
            )
            user = (
                f'【此次交涉概况】{situation}\n'
                f'【目标】{goal}\n'
                f'【可用筹码】{chips}\n\n'
                f'请向大人简要陈述分析与建议，不超过120字，古风口吻。\n'
                '（以JSON格式回复：{"brief": "师爷的分析与建议"}）'
            )
            messages = [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ]
            from llm.context import LLMContext
            from llm import call_sources
            client = LLMClient(context=LLMContext(
                call_source=call_sources.NEGOTIATION,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            result = client.chat_json(messages, temperature=0.7, max_tokens=256)
            brief = (result.get('brief') or '').strip()
            if not brief:
                brief = f'大人，此番与{agent_name}交涉，还请从容应对，把握分寸。'
        except Exception as e:
            logger.warning("Advisor brief generation failed: %s", e)
            brief = f'大人，请注意此番交涉的目标，从容应对。'
            advisor_name = '师爷'

        try:
            DialogueMessage.objects.create(
                game=game,
                agent=session.agent,
                role='advisor',
                content=brief,
                season=game.current_season,
                metadata={
                    'negotiation_id': session.id,
                    'is_advisor_brief': True,
                    'advisor_name': advisor_name,
                },
            )
        except Exception as e:
            logger.warning("Failed to save advisor brief message: %s", e)

    @classmethod
    def _generate_session_summary(cls, session):
        """谈判结束后同步生成结构化摘要，存入 session.outcome['summary']。"""
        game = session.game
        event_type_name = dict(NegotiationSession.EVENT_TYPES).get(session.event_type, session.event_type)
        final_decision = (session.outcome or {}).get('final_decision', '')

        # 取对话正文（排除开场白和师爷提示，避免干扰分析）
        msgs = list(
            DialogueMessage.objects.filter(
                game=game,
                agent=session.agent,
                metadata__negotiation_id=session.id,
            )
            .exclude(role='advisor')
            .exclude(metadata__is_opening=True)
            .order_by('created_at')[:14]
        )
        if not msgs:
            return

        lines = []
        for m in msgs:
            speaker = '县令' if m.role == 'player' else session.agent.name
            lines.append(f'{speaker}：{m.content}')
        conv_text = '\n'.join(lines)

        system_prompt = (
            '你是一个谈判记录分析专家。请分析以下谈判对话，提取关键信息。\n'
            '以JSON格式回复，包含以下字段：\n'
            '{"conclusion": "一句话（25字以内）总结谈判结果", '
            '"player_promises": ["县令在谈判中明确给出的承诺，若无则为空数组"], '
            '"npc_concessions": ["对方明确做出的让步或承诺，若无则为空数组"], '
            '"key_moment": "影响谈判走向的关键转折（25字以内），若无则为空字符串"}'
        )
        user_prompt = (
            f'【谈判类型】{event_type_name}\n'
            f'【最终结果】{final_decision}\n\n'
            f'【对话记录】\n{conv_text}\n\n'
            '请分析并返回JSON摘要。（仅返回JSON，不要有其他文字）'
        )
        messages_llm = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ]

        from llm.context import LLMContext
        from llm import call_sources
        client = LLMClient(context=LLMContext(
            call_source=call_sources.NEGOTIATION,
            game_id=game.id,
            season=game.current_season,
            user_id=game.user_id,
        ))
        result = client.chat_json(messages_llm, temperature=0.2, max_tokens=400)
        summary = {
            'conclusion': (result.get('conclusion') or '').strip()[:60],
            'player_promises': [str(p) for p in (result.get('player_promises') or []) if p][:5],
            'npc_concessions': [str(p) for p in (result.get('npc_concessions') or []) if p][:5],
            'key_moment': (result.get('key_moment') or '').strip()[:80],
        }
        outcome = session.outcome or {}
        outcome['summary'] = summary
        session.outcome = outcome
        session.save(update_fields=['outcome'])
