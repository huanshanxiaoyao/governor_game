"""Agent 服务层 — 初始化、上下文构建、对话处理"""
import logging

from .agent_defs import MVP_AGENTS, MVP_RELATIONSHIPS
from .models import Agent, DialogueMessage, Relationship

from llm.client import LLMClient
from llm.prompts import PromptRegistry

logger = logging.getLogger('game')


class AgentService:
    """管理NPC Agent的核心服务"""

    # ------------------------------------------------------------------
    # 1. Initialization
    # ------------------------------------------------------------------

    @classmethod
    def initialize_agents(cls, game):
        """为新游戏创建16个NPC (4固定 + 6村庄地主 + 6村民代表) 及其关系网络"""
        name_to_agent = {}

        for defn in MVP_AGENTS:
            agent = Agent.objects.create(
                game=game,
                name=defn['name'],
                role=defn['role'],
                role_title=defn['role_title'],
                tier=defn['tier'],
                attributes=defn['attributes'],
            )
            name_to_agent[defn['name']] = agent

        for a_name, b_name, affinity, data in MVP_RELATIONSHIPS:
            Relationship.objects.create(
                agent_a=name_to_agent[a_name],
                agent_b=name_to_agent[b_name],
                affinity=affinity,
                data=data,
            )

        return list(name_to_agent.values())

    # ------------------------------------------------------------------
    # 2. Context Building
    # ------------------------------------------------------------------

    @classmethod
    def build_system_context(cls, agent, game):
        """构建模板渲染所需的全部 kwargs"""
        attrs = agent.attributes
        village_name = attrs.get('village_name', '')
        village_summary = ''
        if village_name:
            village_summary = cls._get_village_summary(game, village_name)

        return {
            'agent_name': agent.name,
            'role_title': agent.role_title,
            'bio': attrs.get('bio', ''),
            'personality_desc': cls._describe_personality(attrs),
            'ideology_desc': cls._describe_ideology(attrs),
            'goals_desc': cls._describe_goals(attrs),
            'relationships_desc': cls._describe_relationships(agent),
            'memory_desc': cls._describe_recent_memory(agent),
            'county_summary': cls._summarize_county(game),
            'village_summary': village_summary,
            'season': game.current_season,
            'affinity': attrs.get('player_affinity', 50),
        }

    @staticmethod
    def _describe_personality(attrs):
        p = attrs.get('personality', {})
        parts = []
        if p.get('openness', 0.5) >= 0.7:
            parts.append('思维开放，善于接受新事物')
        elif p.get('openness', 0.5) <= 0.3:
            parts.append('保守谨慎，倾向于维持现状')
        if p.get('conscientiousness', 0.5) >= 0.7:
            parts.append('做事严谨负责')
        if p.get('agreeableness', 0.5) >= 0.7:
            parts.append('待人温和宽厚')
        elif p.get('agreeableness', 0.5) <= 0.3:
            parts.append('性格强硬不易妥协')
        return '；'.join(parts) if parts else '性情平和'

    @staticmethod
    def _describe_ideology(attrs):
        ideo = attrs.get('ideology', {})
        parts = []
        rv = ideo.get('reform_vs_tradition', 0.5)
        if rv >= 0.7:
            parts.append('倾向变法革新')
        elif rv <= 0.3:
            parts.append('崇尚祖制传统')
        pa = ideo.get('people_vs_authority', 0.5)
        if pa >= 0.7:
            parts.append('重视民间疾苦')
        elif pa <= 0.3:
            parts.append('强调上级权威')
        pi = ideo.get('pragmatic_vs_idealist', 0.5)
        if pi >= 0.7:
            parts.append('注重实际成效')
        elif pi <= 0.3:
            parts.append('追求理想道义')
        return '；'.join(parts) if parts else '立场中庸'

    @staticmethod
    def _describe_goals(attrs):
        goals = attrs.get('goals', [])
        if not goals:
            return '暂无明确目标'
        return '\n'.join(f'- {g}' for g in goals)

    @staticmethod
    def _describe_relationships(agent):
        """描述该Agent与其他NPC的关系"""
        rels_a = agent.relationships_as_a.select_related('agent_b').all()
        rels_b = agent.relationships_as_b.select_related('agent_a').all()

        lines = []
        for r in rels_a:
            desc = r.data.get('desc', '')
            lines.append(f'- {r.agent_b.name}({r.agent_b.role_title}): 好感{r.affinity}, {desc}')
        for r in rels_b:
            desc = r.data.get('desc', '')
            lines.append(f'- {r.agent_a.name}({r.agent_a.role_title}): 好感{r.affinity}, {desc}')
        return '\n'.join(lines) if lines else '暂无已知关系'

    @staticmethod
    def _describe_recent_memory(agent):
        memory = agent.attributes.get('memory', [])
        if not memory:
            return '初来乍到，尚无特别记忆'
        # Show last 5 memories
        recent = memory[-5:]
        return '\n'.join(f'- {m}' for m in recent)

    @staticmethod
    def _summarize_county(game):
        c = game.county_data
        total_pop = sum(v['population'] for v in c.get('villages', []))
        total_farmland = sum(v['farmland'] for v in c.get('villages', []))
        disaster = c.get('disaster_this_year')
        disaster_text = '无' if not disaster else f"{disaster['type']}(严重度{disaster['severity']:.0%})"

        return (
            f"民心: {c.get('morale', '?')}, 治安: {c.get('security', '?')}, "
            f"商业: {c.get('commercial', '?')}, 文教: {c.get('education', '?')}\n"
            f"县库: {c.get('treasury', '?')}两, 税率: {c.get('tax_rate', '?'):.0%}\n"
            f"总人口: {total_pop}, 总耕地: {total_farmland}亩\n"
            f"当前灾害: {disaster_text}"
        )

    @classmethod
    def _get_village_summary(cls, game, village_name):
        """Return formatted village summary for gentry agents."""
        village = cls._get_village_data(game, village_name)
        if village is None:
            return ''
        return cls._summarize_village(village)

    @staticmethod
    def _get_village_data(game, village_name):
        """Find village dict by name from county_data."""
        for v in game.county_data.get('villages', []):
            if v['name'] == village_name:
                return v
        return None

    @staticmethod
    def _summarize_village(village):
        """Format a village dict into a readable summary string."""
        return (
            f'【你的村庄 — {village["name"]}】\n'
            f'人口: {village["population"]}, 耕地: {village["farmland"]}亩, '
            f'地主占地: {village.get("gentry_land_pct", 0):.0%}\n'
            f'村民心: {village.get("morale", "?")}, 治安: {village.get("security", "?")}\n'
            f'村塾: {"有" if village.get("has_school") else "无"}\n\n'
        )

    # ------------------------------------------------------------------
    # 3. Chat Handling
    # ------------------------------------------------------------------

    @classmethod
    def chat_with_agent(cls, game, agent, player_message):
        """与NPC对话的完整流程"""
        # 1. 保存玩家消息
        DialogueMessage.objects.create(
            game=game,
            agent=agent,
            role='player',
            content=player_message,
            season=game.current_season,
        )

        # 2. 构建上下文
        ctx = cls.build_system_context(agent, game)
        ctx['player_message'] = player_message

        # 3. 根据tier选择不同处理方式
        if agent.tier == 'FULL':
            result = cls._chat_full(ctx, game, agent)
        else:
            result = cls._chat_light(ctx, game, agent)

        return result

    @classmethod
    def _chat_full(cls, ctx, game, agent):
        """FULL agent: LLM JSON对话"""
        system_prompt, user_prompt = PromptRegistry.render(
            'agent_full_chat_json', **ctx,
        )

        # 构建消息列表 (system + 最近历史 + 当前)
        messages = [{'role': 'system', 'content': system_prompt}]

        # 加入最近对话历史
        recent = DialogueMessage.objects.filter(
            game=game, agent=agent,
        ).order_by('-created_at')[:10]

        # 排除刚刚保存的玩家消息（最新一条），因为它已在user_prompt中
        history_msgs = list(reversed(recent))[:-1]
        for msg in history_msgs:
            if msg.role == 'player':
                messages.append({'role': 'user', 'content': f'县令对你说："{msg.content}"'})
            elif msg.role == 'agent':
                messages.append({'role': 'assistant', 'content': msg.content})

        messages.append({'role': 'user', 'content': user_prompt})

        # 调用LLM
        try:
            client = LLMClient()
            result = client.chat_json(messages, temperature=0.8, max_tokens=512)
        except Exception as e:
            logger.error("LLM chat failed for agent %s: %s", agent.name, e)
            result = {
                'dialogue': f'{agent.name}沉吟片刻，似乎有些走神。',
                'reasoning': f'LLM调用失败: {e}',
                'attitude_change': 0,
                'new_memory': '',
            }

        # 4. 归一化响应
        result = cls._normalize_response(result)

        # 5. 保存agent回复
        DialogueMessage.objects.create(
            game=game,
            agent=agent,
            role='agent',
            content=result['dialogue'],
            season=game.current_season,
            metadata={
                'reasoning': result.get('reasoning', ''),
                'attitude_change': result.get('attitude_change', 0),
                'new_memory': result.get('new_memory', ''),
            },
        )

        # 6. 更新好感度和记忆
        cls._apply_chat_effects(agent, result)

        return result

    @classmethod
    def _chat_light(cls, ctx, game, agent):
        """LIGHT agent: LLM简短对话"""
        system_prompt, user_prompt = PromptRegistry.render(
            'agent_light_chat', **ctx,
        )

        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ]

        try:
            client = LLMClient()
            dialogue = client.chat(messages, temperature=0.8, max_tokens=256)
        except Exception as e:
            logger.error("LLM chat failed for light agent %s: %s", agent.name, e)
            dialogue = f'{agent.name}憨厚一笑，不知如何作答。'

        result = {
            'dialogue': dialogue.strip(),
            'reasoning': '',
            'attitude_change': 0,
            'new_memory': '',
        }

        # 保存agent回复
        DialogueMessage.objects.create(
            game=game,
            agent=agent,
            role='agent',
            content=result['dialogue'],
            season=game.current_season,
        )

        return result

    @staticmethod
    def _normalize_response(result):
        """确保响应包含所有必要字段"""
        defaults = {
            'dialogue': '（沉默不语）',
            'reasoning': '',
            'attitude_change': 0,
            'new_memory': '',
        }
        for key, default in defaults.items():
            if key not in result:
                result[key] = default

        # Clamp attitude_change
        try:
            result['attitude_change'] = max(-5, min(5, int(result['attitude_change'])))
        except (ValueError, TypeError):
            result['attitude_change'] = 0

        return result

    @staticmethod
    def _apply_chat_effects(agent, result):
        """更新Agent的好感度和记忆"""
        attrs = agent.attributes

        # 好感度变化
        change = result.get('attitude_change', 0)
        if change:
            old = attrs.get('player_affinity', 50)
            attrs['player_affinity'] = max(-99, min(99, old + change))

        # 追加记忆
        new_mem = result.get('new_memory', '')
        if new_mem:
            memory = attrs.get('memory', [])
            memory.append(new_mem)
            # 最多保留20条记忆
            if len(memory) > 20:
                memory = memory[-20:]
            attrs['memory'] = memory

        agent.attributes = attrs
        agent.save(update_fields=['attributes'])

    # ------------------------------------------------------------------
    # 4. Query Helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_agents_list(cls, game):
        """返回游戏中所有NPC的概要信息"""
        agents = Agent.objects.filter(game=game).order_by('id')
        result = []
        for a in agents:
            memory = a.attributes.get('memory', [])
            recent_memory = memory[-3:] if memory else []
            attrs = a.attributes
            result.append({
                'id': a.id,
                'name': a.name,
                'role': a.role,
                'role_title': a.role_title,
                'tier': a.tier,
                'affinity': attrs.get('player_affinity', 50),
                'bio': attrs.get('bio', ''),
                'village_name': attrs.get('village_name', ''),
                'memory': recent_memory,
                'intelligence': attrs.get('intelligence', 5),
                'charisma': attrs.get('charisma', 5),
                'loyalty': attrs.get('loyalty', 5),
                'personality': attrs.get('personality', {}),
                'ideology': attrs.get('ideology', {}),
                'reputation': attrs.get('reputation', {}),
                'goals': attrs.get('goals', []),
                'backstory': attrs.get('backstory', ''),
                'all_memory': a.attributes.get('memory', []),
            })
        return result

    @classmethod
    def get_dialogue_history(cls, game, agent, limit=20):
        """返回最近对话历史"""
        messages = DialogueMessage.objects.filter(
            game=game, agent=agent,
        ).order_by('-created_at')[:limit]

        return [
            {
                'role': m.role,
                'content': m.content,
                'season': m.season,
                'created_at': m.created_at.isoformat(),
            }
            for m in reversed(messages)
        ]
