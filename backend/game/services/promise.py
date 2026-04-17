"""承诺系统服务 — 提取、追踪、验证玩家承诺"""
import logging

from django.utils import timezone

from ..models import EventLog, Promise
from .state import load_county_state

from llm.client import LLMClient
from llm.prompts import PromptRegistry

logger = logging.getLogger('game')


class PromiseService:
    """管理玩家承诺的核心服务"""

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    @classmethod
    def extract_and_save(cls, game, agent, session, player_message,
                         context_type=None):
        """从玩家发言中提取承诺并保存。

        context_type: '谈判'|'交谈'|'书信'，用于 LLM 上下文提示。
        Returns list of created Promise objects (may be empty).
        """
        village_name = agent.attributes.get('village_name', '')
        if context_type is None:
            # 向后兼容：有 session 则为谈判
            event_type = session.get_event_type_display() if session else ''
            context_type = event_type if event_type else '谈判'

        ctx = {
            'context_type': context_type,
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

        from llm.context import LLMContext
        from llm import call_sources
        try:
            client = LLMClient(context=LLMContext(
                call_source=call_sources.PROMISE_EXTRACT,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
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

            # Validate direction
            direction = p.get('direction', 'PLAYER_TO_NPC')
            if direction not in ('PLAYER_TO_NPC', 'NPC_TO_PLAYER'):
                direction = 'PLAYER_TO_NPC'

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
                direction=direction,
                description=p.get('description', ''),
                status='PENDING',
                season_made=game.current_season,
                deadline_season=deadline_season,
                context=context,
            )
            created.append(promise)

            # Log event
            if direction == 'NPC_TO_PLAYER':
                desc = f'{agent.name}向县令承诺：{promise.description}（截止第{deadline_season}月）'
            else:
                desc = f'县令向{agent.name}承诺：{promise.description}（截止第{deadline_season}月）'
            EventLog.objects.create(
                game=game,
                season=game.current_season,
                event_type='promise_made',
                category='PROMISE',
                description=desc,
                data={
                    'promise_id': promise.id,
                    'promise_type': promise_type,
                    'direction': direction,
                    'agent_name': agent.name,
                    'deadline_season': deadline_season,
                },
            )

        return created

    @staticmethod
    def _snapshot_current_values(game, promise_type, target_village):
        """Capture current values needed for validation later."""
        county = load_county_state(game)
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

        elif promise_type == 'UPGRADE_FACILITY':
            snapshot['initial_facility_levels'] = {
                'school_level':    county.get('school_level', 0),
                'irrigation_level': county.get('irrigation_level', 0),
                'medical_level':   county.get('medical_level', 0),
                'bailiff_level':   county.get('bailiff_level', 0),
            }

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
            is_npc_promise = getattr(promise, 'direction', 'PLAYER_TO_NPC') == 'NPC_TO_PLAYER'

            # Check if fulfilled early
            fulfilled = cls._validate_promise(promise, game)
            if fulfilled:
                cls._resolve_promise(promise, game, 'FULFILLED')
                if is_npc_promise:
                    events.append(f'{promise.agent.name}的承诺已履行：{promise.description}（好感+5）')
                else:
                    events.append(f'承诺已履行：{promise.description}（清名+3）')
            elif game.current_season >= promise.deadline_season:
                # Deadline reached — but defer if project is still under construction
                if not is_npc_promise and cls._is_in_construction(promise, game):
                    events.append(f'承诺延期：{promise.description}（项目建设中，暂不计为违约）')
                else:
                    cls._resolve_promise(promise, game, 'BROKEN')
                    cls._apply_breach_penalty(promise, game)
                    if is_npc_promise:
                        events.append(f'{promise.agent.name}违约：{promise.description}（好感-10）')
                    else:
                        penalty = cls._BREACH_PENALTY_TABLE.get(
                            promise.promise_type, cls._BREACH_PENALTY_TABLE['OTHER'])
                        parts = [f'承诺已违约：{promise.description}（清名-5']
                        if penalty['morale']:
                            parts.append(f'，民心{penalty["morale"]}')
                        if penalty['affinity']:
                            parts.append(f'，好感{penalty["affinity"]}')
                        parts.append('）')
                        events.append(''.join(parts))

        return events

    # Promise type → investment action 映射（仅限基建/延迟类投资）
    _TYPE_TO_ACTION = {
        'BUILD_SCHOOL': 'fund_village_school',
        'BUILD_IRRIGATION': 'build_irrigation',
        'RECLAIM_LAND': 'reclaim_land',
        'REPAIR_ROADS': 'repair_roads',
        'BUILD_GRANARY': 'build_granary',
        'BUILD_MEDICAL': 'build_medical',
        # UPGRADE_FACILITY 可对应多个设施 action，特殊处理
    }

    # UPGRADE_FACILITY 可能对应的设施 action
    _FACILITY_ACTIONS = ('build_irrigation', 'expand_school', 'build_medical', 'hire_bailiffs')

    @classmethod
    def _is_in_construction(cls, promise, game):
        """检查承诺对应的项目是否仍在建设中（active_investments）。"""
        county = load_county_state(game)
        active = county.get('active_investments', [])

        if promise.promise_type == 'UPGRADE_FACILITY':
            # 只要有任意县级设施正在建设即视为在建
            for inv in active:
                if inv.get('action') in cls._FACILITY_ACTIONS:
                    return True
            return False

        action = cls._TYPE_TO_ACTION.get(promise.promise_type)
        if not action:
            return False

        target_village = promise.context.get('target_village')
        for inv in active:
            if inv.get('action') != action:
                continue
            # 对村庄定向投资，还需匹配村庄
            if target_village and inv.get('target_village') and inv['target_village'] != target_village:
                continue
            return True
        return False

    @classmethod
    def _validate_promise(cls, promise, game):
        """Check if a promise has been fulfilled. Returns True/False."""
        county = load_county_state(game)
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

        elif promise.promise_type == 'UPGRADE_FACILITY':
            # 承诺后只要任意县级设施等级有提升即视为履行
            initial_levels = ctx.get('initial_facility_levels', {})
            FACILITY_KEYS = ('school_level', 'irrigation_level', 'medical_level', 'bailiff_level')
            for key in FACILITY_KEYS:
                if county.get(key, 0) > initial_levels.get(key, 0):
                    return True
            return False

        # OTHER: cannot auto-validate
        return False

    # ── 统一违约惩罚框架 ──
    # integrity 一律 -5（在 _resolve_promise 中处理）
    # 额外 morale / affinity 惩罚按承诺类型查表：
    #   scope="village" → 村级 morale delta（通过 village_delta 参数）
    #   scope="county"  → 全县 morale delta
    #   affinity_delta  → 相关 NPC 好感变化（有请愿人时生效）
    _BREACH_PENALTY_TABLE = {
        'BUILD_SCHOOL':      {'scope': 'village', 'morale': -5,  'affinity': -8},
        'LOWER_TAX':         {'scope': 'county',  'morale': -8,  'affinity': -10},
        'RELIEF':            {'scope': 'county',  'morale': -8,  'affinity': 0},
        'RECLAIM_LAND':      {'scope': 'village', 'morale': -5,  'affinity': -8},
        'BUILD_IRRIGATION':  {'scope': 'county',  'morale': -5,  'affinity': 0},
        'HIRE_BAILIFFS':     {'scope': 'county',  'morale': -5,  'affinity': 0},
        'UPGRADE_FACILITY':  {'scope': 'county',  'morale': -8,  'affinity': -10},
        'REPAIR_ROADS':      {'scope': 'county',  'morale': -5,  'affinity': 0},
        'BUILD_GRANARY':     {'scope': 'county',  'morale': -5,  'affinity': 0},
        'BUILD_MEDICAL':     {'scope': 'county',  'morale': -5,  'affinity': 0},
        'OTHER':             {'scope': None,       'morale': 0,   'affinity': 0},
    }

    @classmethod
    def _apply_breach_penalty(cls, promise, game):
        """统一违约惩罚：根据承诺类型查表施加 morale 和 affinity 惩罚。

        NPC_TO_PLAYER 方向的承诺违约：不扣玩家清名，改扣 NPC 好感度 -10。
        """
        is_npc_promise = getattr(promise, 'direction', 'PLAYER_TO_NPC') == 'NPC_TO_PLAYER'

        if is_npc_promise:
            # NPC 违约：仅扣好感度（玩家对该 NPC 信任下降）
            if promise.agent:
                agent = promise.agent
                attrs = agent.attributes or {}
                attrs['player_affinity'] = max(-99, min(99,
                    int(attrs.get('player_affinity', 50)) - 10))
                agent.attributes = attrs
                agent.save(update_fields=['attributes'])
            return

        from .state import load_county_state, save_player_state
        from .settlement_metrics import MetricsMixin

        penalty = cls._BREACH_PENALTY_TABLE.get(
            promise.promise_type, cls._BREACH_PENALTY_TABLE['OTHER'])
        morale_delta = penalty['morale']
        affinity_delta = penalty['affinity']

        if morale_delta != 0:
            county = load_county_state(game)
            target_village = (promise.context or {}).get('target_village')

            if penalty['scope'] == 'village' and target_village:
                # 单村惩罚：仅影响目标村，县级通过聚合更新
                for v in county.get('villages', []):
                    if v['name'] == target_village:
                        v['morale'] = max(0.0, min(100.0,
                            float(v.get('morale', 50.0)) + morale_delta))
                        break
                MetricsMixin._sync_county_from_villages(county, 'morale')
            else:
                MetricsMixin.apply_county_stat_delta(county, 'morale', morale_delta)

            save_player_state(game, county)

        if affinity_delta != 0 and promise.agent:
            agent = promise.agent
            attrs = agent.attributes or {}
            attrs['player_affinity'] = max(-99, min(99,
                int(attrs.get('player_affinity', 50)) + affinity_delta))
            agent.attributes = attrs
            agent.save(update_fields=['attributes'])

    @classmethod
    def _resolve_promise(cls, promise, game, new_status):
        """Mark promise as fulfilled or broken, adjust integrity.

        NPC_TO_PLAYER 方向的承诺不影响玩家清名。
        """
        is_npc_promise = getattr(promise, 'direction', 'PLAYER_TO_NPC') == 'NPC_TO_PLAYER'

        promise.status = new_status
        promise.resolved_at = timezone.now()
        promise.save(update_fields=['status', 'resolved_at'])

        integrity_change = 0
        if not is_npc_promise:
            # 玩家承诺：调整玩家清名
            player = getattr(game, 'player', None)
            if player is None:
                try:
                    from ..models import PlayerProfile
                    player = PlayerProfile.objects.get(game=game)
                except Exception:
                    player = None

            if player is not None:
                if new_status == 'FULFILLED':
                    player.integrity = min(100, player.integrity + 3)
                    integrity_change = 3
                elif new_status == 'BROKEN':
                    player.integrity = max(0, player.integrity - 5)
                    integrity_change = -5
                player.save(update_fields=['integrity'])
        else:
            # NPC 承诺兑现：好感 +5
            if new_status == 'FULFILLED' and promise.agent:
                agent = promise.agent
                attrs = agent.attributes or {}
                attrs['player_affinity'] = min(99,
                    int(attrs.get('player_affinity', 50)) + 5)
                agent.attributes = attrs
                agent.save(update_fields=['attributes'])

        # Log event
        status_text = '已履行' if new_status == 'FULFILLED' else '已违约'
        if is_npc_promise:
            desc = f'{promise.agent.name}的承诺{status_text}：{promise.description}'
        else:
            desc = f'承诺{status_text}：{promise.description}（清名{integrity_change:+d}）'
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type=f'promise_{new_status.lower()}',
            category='PROMISE',
            description=desc,
            data={
                'promise_id': promise.id,
                'promise_type': promise.promise_type,
                'direction': getattr(promise, 'direction', 'PLAYER_TO_NPC'),
                'status': new_status,
                'integrity_change': integrity_change,
            },
        )
