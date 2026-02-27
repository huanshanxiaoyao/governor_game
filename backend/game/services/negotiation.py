"""谈判服务 — 地主兼并 / 兴建水利 / 隐匿土地 多轮谈判状态机"""
import logging
import random

from django.utils import timezone

from ..models import Agent, DialogueMessage, EventLog, NegotiationSession
from .agent import AgentService
from .ledger import (
    ensure_county_ledgers,
    ensure_village_ledgers,
    sync_county_gentry_land_ratio,
    sync_legacy_from_ledgers,
)

from llm.client import LLMClient
from llm.prompts import PromptRegistry

logger = logging.getLogger('game')

# Round-pressure text by progress
_PRESSURE_EARLY = '你可以坚持立场，从容应对。'
_PRESSURE_MID = '需认真考虑对方论点，可适当让步。'
_PRESSURE_LATE = '谈判即将结束，准备给出最终答复。'
_PRESSURE_FINAL = '这是最后一轮，你必须在 final_decision 中给出明确决定，不能为 null。'


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

        max_rounds = {'ANNEXATION': 8, 'IRRIGATION': 12, 'HIDDEN_LAND': 8}.get(event_type, 8)

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
        return session, None

    @classmethod
    def get_active_negotiation(cls, game):
        """Return the active NegotiationSession for a game, or None."""
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
        else:
            cls._apply_irrigation_outcome(session, outcome)

    # ------------------------------------------------------------------
    # Round Processing
    # ------------------------------------------------------------------

    @classmethod
    def negotiate_round(cls, game, session, player_message, speaker_role='PLAYER'):
        """Process one round of negotiation.

        Returns a result dict with dialogue, round info, and status.
        """
        if session.status != 'active':
            return {'error': '该谈判已结束'}

        if session.current_round >= session.max_rounds:
            return {'error': '已达最大谈判轮数'}

        speaker_role = cls._normalize_speaker_role(speaker_role)
        delegate_agent = cls._get_delegate_agent(game, speaker_role)
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

        # 2b. Extract promises from player message
        if speaker_role == 'PLAYER':
            from .promise import PromiseService
            try:
                PromiseService.extract_and_save(game, session.agent, session, player_message)
            except Exception as e:
                logger.warning("Promise extraction failed (non-fatal): %s", e)

        # 3. Build LLM context
        agent = session.agent
        ctx = AgentService.build_system_context(agent, game)
        ctx['player_message'] = llm_player_message

        # Add negotiation-specific context
        ctx['current_round'] = session.current_round
        ctx['max_rounds'] = session.max_rounds
        ctx['round_pressure'] = _round_pressure(session.current_round, session.max_rounds)
        ctx['village_name'] = agent.attributes.get('village_name', '')

        if session.event_type == 'ANNEXATION':
            result = cls._negotiate_annexation(ctx, game, session)
        elif session.event_type == 'HIDDEN_LAND':
            result = cls._negotiate_hidden_land(ctx, game, session)
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
            response['treasury'] = round(game.county_data.get('treasury', 0), 1)
            if session.event_type == 'IRRIGATION':
                response['contribution_offer'] = result.get('contribution_offer', 0)

        return response

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
            client = LLMClient()
            result = client.chat_json(messages, temperature=0.8, max_tokens=512)
        except Exception as e:
            logger.error("Negotiation LLM failed for %s: %s", session.agent.name, e)
            result = {
                'dialogue': f'{session.agent.name}面色不善，沉默不语。',
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
            client = LLMClient()
            result = client.chat_json(messages, temperature=0.8, max_tokens=512)
        except Exception as e:
            logger.error("Negotiation LLM failed for %s: %s", session.agent.name, e)
            result = {
                'dialogue': f'{session.agent.name}捻须不语，似在盘算。',
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
            client = LLMClient()
            result = client.chat_json(messages, temperature=0.8, max_tokens=512)
        except Exception as e:
            logger.error("Negotiation LLM failed for %s: %s", session.agent.name, e)
            result = {
                'dialogue': f'{session.agent.name}面色不善，沉默不语。',
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
    def _evaluate_delegate_attempt(cls, session, result, speaker_role, delegate_agent):
        """Delegate handles one automatic attempt: success resolves, failure hands off."""
        event_type = session.event_type
        role_bonus = cls._delegate_role_bonus(speaker_role, event_type)
        trait_bonus = cls._delegate_trait_bonus(delegate_agent)

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

        # Fetch recent negotiation dialogue
        recent = DialogueMessage.objects.filter(
            game=game,
            agent=session.agent,
            metadata__negotiation_id=session.id,
        ).order_by('-created_at')[:20]

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
        county = game.county_data
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
                    v['morale'] = max(0, v['morale'] - 8)
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
        game.county_data = county
        game.save()

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
        county = game.county_data
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
                    # Gentry affinity drops sharply on forced measurement
                    attrs = agent.attributes
                    attrs['player_affinity'] = max(-99, attrs.get('player_affinity', 50) - 20)
                    agent.attributes = attrs
                    agent.save(update_fields=['attributes'])

                gentry['registered_farmland'] = max(0, int(gentry.get('registered_farmland', 0)) + discovered)
                gentry['hidden_farmland'] = max(0, hidden - discovered)
                v['hidden_land_discovered'] = True
                sync_legacy_from_ledgers(v)
                break

        sync_county_gentry_land_ratio(county)
        game.county_data = county
        game.save()

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
        county = game.county_data
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
                        'agent_name': agent.name,
                        'village_name': village_name,
                        'amount': contribution,
                    })
                    break

            # Gentry affinity decreases proportional to contribution extracted
            max_contrib = session.context_data.get('max_contribution', 20)
            if max_contrib > 0:
                affinity_loss = int(8 * (contribution / max_contrib))
                attrs = agent.attributes
                attrs['player_affinity'] = max(-99, attrs.get('player_affinity', 50) - affinity_loss)
                agent.attributes = attrs
                agent.save(update_fields=['attributes'])

        game.county_data = county
        game.save()

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
    # Chat History
    # ------------------------------------------------------------------

    @classmethod
    def get_negotiation_history(cls, session):
        """Return negotiation dialogue history."""
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
                'season': m.season,
                'created_at': m.created_at.isoformat(),
            }
            for m in messages
        ]
