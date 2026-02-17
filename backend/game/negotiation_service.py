"""谈判服务 — 地主兼并 / 兴建水利 多轮谈判状态机"""
import logging

from django.utils import timezone

from .models import Agent, DialogueMessage, EventLog, NegotiationSession
from .agent_service import AgentService

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

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    @classmethod
    def start_negotiation(cls, game, agent, event_type, context_data):
        """Create a new NegotiationSession.

        Returns (session, error_message).  error_message is None on success.
        """
        # Check no active negotiation already
        if NegotiationSession.objects.filter(game=game, status='active').exists():
            return None, '当前已有一场进行中的谈判，请先完成后再开始新谈判'

        max_rounds = 8 if event_type == 'ANNEXATION' else 12

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
        ).select_related('agent').first()

    @classmethod
    def resolve_session(cls, session, outcome):
        """Mark session resolved and apply game effects."""
        session.outcome = outcome
        session.status = 'resolved'
        session.resolved_at = timezone.now()
        session.save()

        if session.event_type == 'ANNEXATION':
            cls._apply_annexation_outcome(session, outcome)
        else:
            cls._apply_irrigation_outcome(session, outcome)

    # ------------------------------------------------------------------
    # Round Processing
    # ------------------------------------------------------------------

    @classmethod
    def negotiate_round(cls, game, session, player_message):
        """Process one round of negotiation.

        Returns a result dict with dialogue, round info, and status.
        """
        if session.status != 'active':
            return {'error': '该谈判已结束'}

        if session.current_round >= session.max_rounds:
            return {'error': '已达最大谈判轮数'}

        # 1. Increment round
        session.current_round += 1
        session.save(update_fields=['current_round'])

        # 2. Save player message
        DialogueMessage.objects.create(
            game=game,
            agent=session.agent,
            role='player',
            content=player_message,
            season=game.current_season,
            metadata={'negotiation_id': session.id},
        )

        # 2b. Extract promises from player message
        from .promise_service import PromiseService
        try:
            PromiseService.extract_and_save(game, session.agent, session, player_message)
        except Exception as e:
            logger.warning("Promise extraction failed (non-fatal): %s", e)

        # 3. Build LLM context
        agent = session.agent
        ctx = AgentService.build_system_context(agent, game)
        ctx['player_message'] = player_message

        # Add negotiation-specific context
        ctx['current_round'] = session.current_round
        ctx['max_rounds'] = session.max_rounds
        ctx['round_pressure'] = _round_pressure(session.current_round, session.max_rounds)
        ctx['village_name'] = agent.attributes.get('village_name', '')

        if session.event_type == 'ANNEXATION':
            result = cls._negotiate_annexation(ctx, game, session)
        else:
            result = cls._negotiate_irrigation(ctx, game, session)

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
        if result.get('final_decision') is not None:
            resolved = True
            cls.resolve_session(session, result)
        elif session.current_round >= session.max_rounds:
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
    # Shared Helpers
    # ------------------------------------------------------------------

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
                messages.append({'role': 'user', 'content': f'县令对你说："{msg.content}"'})
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
        agent = session.agent
        village_name = agent.attributes.get('village_name', '')
        decision = outcome.get('final_decision', 'proceed_annexation')

        for v in county['villages']:
            if v['name'] == village_name:
                if decision == 'proceed_annexation':
                    increase = session.context_data.get('proposed_pct_increase', 0.05)
                    v['gentry_land_pct'] = min(0.8, v['gentry_land_pct'] + increase)
                    v['morale'] = max(0, v['morale'] - 8)
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

        game.county_data = county
        game.save()

        desc = (f'{village_name}地主{agent.name}兼并谈判结束：'
                f'{"继续兼并" if decision == "proceed_annexation" else "停止兼并"}')
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
                'season': m.season,
                'created_at': m.created_at.isoformat(),
            }
            for m in messages
        ]
