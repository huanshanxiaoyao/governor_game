"""幕僚群聊服务 — 对话施政核心逻辑"""

import json
import logging

logger = logging.getLogger(__name__)

# 主动提醒触发阈值
PROACTIVE_THRESHOLD = 40

# 指标低于阈值时的主动提醒映射
# key: county 中的指标字段名
# value: (speaker, [推荐的 action_key 列表])
PROACTIVE_TRIGGERS = {
    'morale':    ('shiye',      ['fund_village_school', 'relief']),
    'security':  ('xiancheng',  ['hire_bailiffs', 'repair_roads']),
    'commercial': ('shiye',     ['repair_roads']),
    'education': ('xiancheng',  ['expand_school', 'fund_village_school']),
}

# 主动提醒文案模板
PROACTIVE_MESSAGES = {
    'morale': (
        '大人，近日民心有所低落（已低于警戒线），属下以为宜早作抚慰。'
        '资助村塾或酌情赈济，或可收拢人心。'
    ),
    'security': (
        '大人，县内治安渐趋松弛，盗匪滋扰之虞不可不防。'
        '增设衙役或修缮道路，皆有助于稳定地方。'
    ),
    'commercial': (
        '大人，县内商贸近来不振，集市冷清。'
        '修缮道路可改善行商条件，或有助于提振商业。'
    ),
    'education': (
        '大人，本县文教尚有欠缺，士风稍显凋敝。'
        '扩建县学或资助村塾，乃长远之计。'
    ),
}

# 指标中文名（用于 prompt 中的 snapshot）
STAT_LABELS = {
    'morale':    '民心',
    'security':  '治安',
    'commercial': '商业',
    'education': '文教',
}


class CounselService:
    """幕僚议事服务：对话施政入口"""

    @classmethod
    def get_npc_personas(cls, game):
        """加载本局师爷（ADVISOR）和县丞（DEPUTY）的 Agent 记录，构建 persona 字典。
        找不到时返回默认中性 persona，不抛异常。
        """
        from ..models import Agent
        from .agent import AgentService

        personas = {}
        for role, key in [('ADVISOR', 'shiye'), ('DEPUTY', 'xiancheng')]:
            try:
                agent = game.agents.filter(role=role).select_related().first()
            except Exception:
                agent = None

            if agent:
                attrs = agent.attributes or {}
                personas[key] = {
                    'name': agent.name,
                    'bio': attrs.get('bio', f'{agent.role_title}，尽忠职守'),
                    'personality': AgentService._describe_personality(attrs),
                    'ideology': AgentService._describe_ideology(attrs),
                    'goals': AgentService._describe_goals(attrs),
                    'affinity': attrs.get('player_affinity', 50),
                }
            else:
                # 默认 persona（中性，不影响游戏逻辑）
                default_name = '吴先生' if key == 'shiye' else '钱县丞'
                personas[key] = {
                    'name': default_name,
                    'bio': '经验丰富的幕僚，处事稳重',
                    'personality': '性情平和',
                    'ideology': '立场中庸',
                    'goals': '辅佐县令，维持地方稳定',
                    'affinity': 50,
                }

        return personas

    @classmethod
    def build_county_snapshot(cls, county):
        """构建县情快照字符串，注入 LLM prompt。"""
        morale    = round(county.get('morale', 0))
        security  = round(county.get('security', 0))
        commercial = round(county.get('commercial', 0))
        education = round(county.get('education', 0))
        treasury  = round(county.get('treasury', 0))

        active_inv = county.get('active_investments', [])
        inv_desc = '无' if not active_inv else '、'.join(
            inv.get('description', inv.get('action', '')) for inv in active_inv
        )

        # 即时行动当前状态（供 LLM 判断是否需要再次建议）
        bailiff_level  = county.get('bailiff_level', 0)
        has_granary    = county.get('has_granary', False)
        disaster       = county.get('disaster_this_year')
        relief_done    = bool(disaster and disaster.get('relieved'))

        state_parts = [f'衙役等级：{bailiff_level}/3']
        if has_granary:
            state_parts.append('义仓：已建成')
        if relief_done:
            state_parts.append('赈灾：本年已执行')

        return (
            f'民心{morale} · 治安{security} · 商业{commercial} · 文教{education}\n'
            f'县库：{treasury}两\n'
            f'在建工程：{inv_desc}\n'
            f'施政状态：{"、".join(state_parts)}'
        )

    @classmethod
    def build_available_actions_summary(cls, county, game=None):
        """构建可用标准施政选项摘要，供 LLM 在建议时引用。"""
        from .investment import InvestmentService
        actions = InvestmentService.get_available_actions(county, game=game)
        lines = []
        for a in actions:
            if a.get('is_custom'):
                continue  # 自创选项不出现在 LLM 可引用列表中（避免 LLM 重复提案）
            if a.get('disabled_reason'):
                continue  # 不可用的不列出
            name = a['name']
            cost = a['cost']
            lines.append(f'- {name}（{cost}两）[{a["action"]}]')
        return '\n'.join(lines) if lines else '（当前资金有限，暂无可用施政）'

    @classmethod
    def chat(cls, game, county, history, user_message):
        """发送消息到幕僚群聊，返回结构化结果。

        Args:
            game: GameState 实例
            county: 县域状态 dict
            history: 前端传来的对话历史 list（[{role, content}, ...]）
            user_message: 玩家本次发言

        Returns:
            dict: {
                speaker: 'shiye' | 'xiancheng',
                reply: str,
                suggested_actions: [...],
                proposed_policies: [...],
            }
        """
        from llm.client import LLMClient
        from llm.prompts import PromptRegistry
        from llm.exceptions import LLMJSONParseError, LLMRequestError

        personas = cls.get_npc_personas(game)
        shiye = personas['shiye']
        xiancheng = personas['xiancheng']

        system_prompt, user_template = PromptRegistry.render(
            'counsel_chat_json',
            shiye_name=shiye['name'],
            shiye_bio=shiye['bio'],
            shiye_personality=shiye['personality'],
            shiye_ideology=shiye['ideology'],
            shiye_goals=shiye['goals'],
            shiye_affinity=shiye['affinity'],
            xiancheng_name=xiancheng['name'],
            xiancheng_bio=xiancheng['bio'],
            xiancheng_personality=xiancheng['personality'],
            xiancheng_ideology=xiancheng['ideology'],
            xiancheng_goals=xiancheng['goals'],
            xiancheng_affinity=xiancheng['affinity'],
            county_snapshot=cls.build_county_snapshot(county),
            available_actions_summary=cls.build_available_actions_summary(county, game),
            season=game.current_season,
            player_message=user_message,
        )

        # 构建消息列表：system + 最近6轮历史 + 当前用户消息
        messages = [{'role': 'system', 'content': system_prompt}]
        for h in history[-12:]:  # 最近6轮 = 12条消息
            if h.get('role') in ('user', 'assistant') and h.get('content'):
                messages.append({'role': h['role'], 'content': h['content']})
        messages.append({'role': 'user', 'content': user_template})

        from llm.context import LLMContext
        from llm import call_sources
        try:
            client = LLMClient(context=LLMContext(
                call_source=call_sources.COUNSEL,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
            raw = client.chat_json(messages, max_tokens=800)
        except LLMJSONParseError as e:
            logger.warning('counsel_chat: JSON parse error: %s', e)
            return cls._fallback_response(shiye['name'])
        except LLMRequestError as e:
            logger.warning('counsel_chat: LLM request error: %s', e)
            return cls._fallback_response(shiye['name'])

        return cls._parse_chat_response(raw, county, game)

    @classmethod
    def _parse_chat_response(cls, raw, county, game):
        """解析 LLM 返回，校验 suggested_actions 中的 action_key 合法性。"""
        from .investment import InvestmentService

        speaker = raw.get('speaker', 'shiye')
        if speaker not in ('shiye', 'xiancheng'):
            speaker = 'shiye'

        reply = raw.get('reply', '')

        # 校验 suggested_actions：只保留实际可用的 action_key
        valid_actions = {
            a['action'] for a in InvestmentService.get_available_actions(county, game=game)
        }
        suggested = []
        for item in raw.get('suggested_actions', []):
            action_key = item.get('action', '')
            if action_key in valid_actions:
                suggested.append({
                    'action': action_key,
                    'target_village': item.get('target_village'),
                    'rationale': item.get('rationale', ''),
                })
            else:
                logger.debug('counsel: suggested action %r not in available set, dropped', action_key)

        # proposed_policies：过滤掉已有标准选项中的名字（防止 LLM 把现有选项当新构想）
        existing_names = {
            a['name'] for a in InvestmentService.get_available_actions(county, game=game)
        }
        proposed = []
        for item in raw.get('proposed_policies', []):
            name = item.get('name', '').strip()
            if name and name not in existing_names:
                proposed.append({
                    'name': name,
                    'rationale': item.get('rationale', ''),
                })

        return {
            'speaker': speaker,
            'reply': reply,
            'suggested_actions': suggested,
            'proposed_policies': proposed,
        }

    @classmethod
    def _fallback_response(cls, speaker_name):
        """LLM 失败时的降级响应。"""
        return {
            'speaker': 'shiye',
            'reply': f'（{speaker_name}正在思考，请稍后再问）',
            'suggested_actions': [],
            'proposed_policies': [],
        }

    @classmethod
    def check_proactive_trigger(cls, county, game=None, season=None):
        """检查是否满足主动提醒条件（任意指标 < PROACTIVE_THRESHOLD）。

        Returns:
            dict | None: 触发时返回提醒数据，否则 None。
            格式：{speaker, message, suggested_actions, stat, stat_value}
        """
        from .investment import InvestmentService

        # 找到最低的触发指标
        triggered_stat = None
        triggered_value = None
        for stat in PROACTIVE_TRIGGERS:
            val = county.get(stat, 100)
            if val < PROACTIVE_THRESHOLD:
                if triggered_value is None or val < triggered_value:
                    triggered_stat = stat
                    triggered_value = val

        if triggered_stat is None:
            return None

        speaker, action_keys = PROACTIVE_TRIGGERS[triggered_stat]
        message_text = PROACTIVE_MESSAGES[triggered_stat]

        # 检查推荐的 action 是否实际可用（资金等）
        available = InvestmentService.get_available_actions(
            county,
            season=season,
            game=game,
        )
        available_map = {a['action']: a for a in available}

        suggested = []
        for key in action_keys:
            if key in available_map and not available_map[key].get('disabled_reason'):
                suggested.append({
                    'action': key,
                    'target_village': None,
                    'rationale': f'{STAT_LABELS[triggered_stat]}偏低',
                })

        # 资金不足时改为文字提示
        if not suggested:
            message_text = (
                f'大人，{STAT_LABELS[triggered_stat]}已低至{triggered_value}，'
                f'属下以为需引起重视，然目前财力有限，宜待秋收后再行筹措。'
            )

        return {
            'speaker': speaker,
            'message': message_text,
            'suggested_actions': suggested,
            'stat': triggered_stat,
            'stat_value': triggered_value,
        }
