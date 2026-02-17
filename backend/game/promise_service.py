"""承诺系统服务 — 提取、追踪、验证玩家承诺"""
import logging

from django.utils import timezone

from .models import EventLog, Promise

from llm.client import LLMClient
from llm.prompts import PromptRegistry

logger = logging.getLogger('game')


class PromiseService:
    """管理玩家承诺的核心服务"""

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    @classmethod
    def extract_and_save(cls, game, agent, session, player_message):
        """从玩家发言中提取承诺并保存。

        Returns list of created Promise objects (may be empty).
        """
        village_name = agent.attributes.get('village_name', '')
        event_type = session.get_event_type_display() if session else ''

        ctx = {
            'event_type': event_type,
            'village_name': village_name,
            'agent_name': agent.name,
            'current_season': game.current_season,
            'player_message': player_message,
        }

        system_prompt, user_prompt = PromptRegistry.render('promise_extraction', **ctx)
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ]

        try:
            client = LLMClient()
            result = client.chat_json(messages, temperature=0.2, max_tokens=512)
        except Exception as e:
            logger.error("Promise extraction LLM failed: %s", e)
            return []

        raw_promises = result.get('promises', [])
        if not raw_promises:
            return []

        created = []
        for p in raw_promises:
            promise_type = p.get('type', 'OTHER')
            # Validate type
            valid_types = [t[0] for t in Promise.PROMISE_TYPES]
            if promise_type not in valid_types:
                promise_type = 'OTHER'

            deadline_seasons = int(p.get('deadline_seasons', 4))
            deadline_season = game.current_season + deadline_seasons

            context = {}
            if p.get('target_village'):
                context['target_village'] = p['target_village']
            if p.get('target_value') is not None:
                context['target_value'] = p['target_value']
            # Snapshot current values for validation
            context.update(cls._snapshot_current_values(game, promise_type, p.get('target_village')))

            promise = Promise.objects.create(
                game=game,
                agent=agent,
                negotiation=session,
                promise_type=promise_type,
                description=p.get('description', ''),
                status='PENDING',
                season_made=game.current_season,
                deadline_season=deadline_season,
                context=context,
            )
            created.append(promise)

            # Log event
            EventLog.objects.create(
                game=game,
                season=game.current_season,
                event_type='promise_made',
                category='PROMISE',
                description=f'县令向{agent.name}承诺：{promise.description}（截止第{deadline_season}季度）',
                data={
                    'promise_id': promise.id,
                    'promise_type': promise_type,
                    'agent_name': agent.name,
                    'deadline_season': deadline_season,
                },
            )

        return created

    @staticmethod
    def _snapshot_current_values(game, promise_type, target_village):
        """Capture current values needed for validation later."""
        county = game.county_data
        snapshot = {}

        if promise_type == 'LOWER_TAX':
            snapshot['initial_tax_rate'] = county.get('tax_rate', 0.12)
        elif promise_type == 'BUILD_IRRIGATION':
            snapshot['initial_irrigation_level'] = county.get('irrigation_level', 0)
        elif promise_type == 'HIRE_BAILIFFS':
            snapshot['initial_bailiff_level'] = county.get('bailiff_level', 0)
        elif promise_type == 'RECLAIM_LAND' and target_village:
            for v in county.get('villages', []):
                if v['name'] == target_village:
                    snapshot['initial_farmland'] = v['farmland']
                    break

        return snapshot

    # ------------------------------------------------------------------
    # Validation (called each season)
    # ------------------------------------------------------------------

    @classmethod
    def check_promises(cls, game):
        """Check all pending promises. Returns list of event description strings."""
        pending = Promise.objects.filter(game=game, status='PENDING')
        events = []

        for promise in pending:
            # Check if fulfilled early
            fulfilled = cls._validate_promise(promise, game)
            if fulfilled:
                cls._resolve_promise(promise, game, 'FULFILLED')
                events.append(f'承诺已履行：{promise.description}（清名+3）')
            elif game.current_season >= promise.deadline_season:
                # Deadline reached without fulfillment
                cls._resolve_promise(promise, game, 'BROKEN')
                events.append(f'承诺已违约：{promise.description}（清名-5）')

        return events

    @classmethod
    def _validate_promise(cls, promise, game):
        """Check if a promise has been fulfilled. Returns True/False."""
        county = game.county_data
        ctx = promise.context

        if promise.promise_type == 'LOWER_TAX':
            target = ctx.get('target_value')
            if target is not None:
                return county.get('tax_rate', 1.0) <= target
            # No explicit target: just check if rate decreased
            return county.get('tax_rate', 1.0) < ctx.get('initial_tax_rate', 1.0)

        elif promise.promise_type == 'BUILD_SCHOOL':
            target_village = ctx.get('target_village')
            for v in county.get('villages', []):
                if target_village and v['name'] != target_village:
                    continue
                if v.get('has_school'):
                    return True
            return False

        elif promise.promise_type == 'BUILD_IRRIGATION':
            initial = ctx.get('initial_irrigation_level', 0)
            return county.get('irrigation_level', 0) > initial

        elif promise.promise_type == 'RELIEF':
            disaster = county.get('disaster_this_year')
            if disaster and disaster.get('relieved'):
                return True
            # If no disaster, can't fulfill or break — keep pending
            return False

        elif promise.promise_type == 'HIRE_BAILIFFS':
            initial = ctx.get('initial_bailiff_level', 0)
            return county.get('bailiff_level', 0) > initial

        elif promise.promise_type == 'RECLAIM_LAND':
            target_village = ctx.get('target_village')
            initial_farmland = ctx.get('initial_farmland', 0)
            for v in county.get('villages', []):
                if target_village and v['name'] != target_village:
                    continue
                if v['farmland'] > initial_farmland:
                    return True
            return False

        elif promise.promise_type == 'REPAIR_ROADS':
            # Check if there's an active or completed road investment
            for inv in county.get('active_investments', []):
                if inv.get('action') == 'repair_roads':
                    return True
            # If commercial went up, roads were repaired (completed)
            return False

        elif promise.promise_type == 'BUILD_GRANARY':
            return county.get('has_granary', False)

        # OTHER: cannot auto-validate
        return False

    @classmethod
    def _resolve_promise(cls, promise, game, new_status):
        """Mark promise as fulfilled or broken, adjust integrity."""
        promise.status = new_status
        promise.resolved_at = timezone.now()
        promise.save(update_fields=['status', 'resolved_at'])

        # Adjust integrity
        player = getattr(game, 'player', None)
        if player is None:
            try:
                from .models import PlayerProfile
                player = PlayerProfile.objects.get(game=game)
            except Exception:
                return

        if new_status == 'FULFILLED':
            player.integrity = min(100, player.integrity + 3)
        elif new_status == 'BROKEN':
            player.integrity = max(0, player.integrity - 5)
        player.save(update_fields=['integrity'])

        # Log event
        status_text = '已履行' if new_status == 'FULFILLED' else '已违约'
        integrity_change = '+3' if new_status == 'FULFILLED' else '-5'
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type=f'promise_{new_status.lower()}',
            category='PROMISE',
            description=f'承诺{status_text}：{promise.description}（清名{integrity_change}）',
            data={
                'promise_id': promise.id,
                'promise_type': promise.promise_type,
                'status': new_status,
                'integrity_change': 3 if new_status == 'FULFILLED' else -5,
            },
        )
