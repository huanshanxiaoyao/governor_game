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
        import copy
        name_to_agent = {}

        for defn in MVP_AGENTS:
            agent = Agent.objects.create(
                game=game,
                name=defn['name'],
                role=defn['role'],
                role_title=defn['role_title'],
                tier=defn['tier'],
                attributes=copy.deepcopy(defn['attributes']),
            )
            name_to_agent[defn['name']] = agent

        # 将地主和村民代表的 village_name 重新映射到实际生成的村庄
        actual_villages = [v["name"] for v in game.county_data.get("villages", [])]
        all_agents = list(name_to_agent.values())
        gentry_agents = [a for a in all_agents
                         if a.role == 'GENTRY' and a.role_title == '地主']
        villager_agents = [a for a in all_agents
                          if a.role == 'VILLAGER' and a.role_title == '村民代表']

        for i, vname in enumerate(actual_villages):
            if i < len(gentry_agents):
                gentry_agents[i].attributes['village_name'] = vname
                gentry_agents[i].save(update_fields=['attributes'])
            if i < len(villager_agents):
                villager_agents[i].attributes['village_name'] = vname
                villager_agents[i].save(update_fields=['attributes'])

        for a_name, b_name, affinity, data in MVP_RELATIONSHIPS:
            Relationship.objects.create(
                agent_a=name_to_agent[a_name],
                agent_b=name_to_agent[b_name],
                affinity=affinity,
                data=data,
            )

        return all_agents

    # ------------------------------------------------------------------
    # 2. Context Building
    # ------------------------------------------------------------------

    COUNTY_TYPE_DESCS = {
        "fiscal_core": "本县为江南财赋重地，田多税重，上缴压力极大。地主占地比高，平民徭役负担重。商业较为繁荣，但需警惕入不敷出。",
        "clan_governance": "本县为山区宗族之地，宗族势力根深蒂固。社会秩序稳定、征税效率高，但改革阻力大、商业薄弱。",
        "coastal": "本县为沿海偏僻之地，人少地少，财政紧张。一次灾害即可能令县库见底，治安堪忧，需精打细算。",
        "disaster_prone": "本县地处黄淮之间，水患频繁，民心低迷。需持续投入水利和赈灾，否则灾年农税骤降而上缴不减。",
    }

    GAME_KNOWLEDGE_TEMPLATE = (
        '【治县要略 — 你作为师爷应熟知的治理之道】\n'
        '\n'
        '一、财政收支\n'
        '- 县库收入来自三大税源：田赋（农业税）、徭役折银、商税\n'
        '- 田赋取决于耕地、农事丰歉和税率，民心越高征收效率越好\n'
        '- 徭役只征自耕农和佃户，绅衿地主依制免役；地主占地越多，应役人口越少，徭役收入越低\n'
        '- 商税取决于集市商户多寡和商业繁荣程度\n'
        '- 每年秋季需向上级缴纳定额赋税（上缴比例因地而异），剩余方为县用\n'
        '- 行政开支、衙役俸禄、医疗开支均在秋季扣除\n'
        '\n'
        '二、民心与治安\n'
        '- 民心和治安每季度都会自然衰减\n'
        '- 文教兴盛有助于民心回升；衙役充足有助于治安维持\n'
        '- 民心低落时地主容易趁机兼并田地；治安低迷则百姓流离失所\n'
        '- 全县民心与各村民心相互影响、联动变化\n'
        '\n'
        '三、投资施政\n'
        '- 开垦荒地可增加耕地、降低地主占地比，有利于农民\n'
        '- 修建水利可减轻水患、提高产量，但需要时日；可与地主协商分担费用\n'
        '- 扩建县学提升文教，间接促进民心恢复\n'
        '- 增设衙役立竿见影提升治安，但会永久增加行政开支\n'
        '- 修缮道路可促进商业繁荣\n'
        '- 义仓可减轻灾害损失；赈灾可在灾后安抚民心\n'
        '\n'
        '四、灾害与风险\n'
        '- 水灾、旱灾、蝗灾、疫病可能在夏季发生\n'
        '- 水利设施可降低水患概率；义仓和赈灾可减轻灾害损失\n'
        '- 医疗投入可降低疫病风险\n'
        '\n'
        '五、人口\n'
        '- 人口承载力取决于耕地（非地主占有部分）、农事丰歉和税率\n'
        '- 商业繁荣可吸引人口流入；治安低迷则导致人口外流\n'
        '\n'
        '六、县域特色\n'
        '- {county_type_desc}\n'
    )

    @classmethod
    def _build_game_knowledge(cls, game):
        """构建治县要略文本（仅供师爷/县丞使用）"""
        county_type = game.county_data.get('county_type', '')
        county_type_desc = cls.COUNTY_TYPE_DESCS.get(county_type, '')
        return cls.GAME_KNOWLEDGE_TEMPLATE.format(county_type_desc=county_type_desc)

    @classmethod
    def build_system_context(cls, agent, game):
        """构建模板渲染所需的全部 kwargs"""
        attrs = agent.attributes
        village_name = attrs.get('village_name', '')
        village_summary = ''
        if village_name:
            village_summary = cls._get_village_summary(game, village_name)

        # 师爷和县丞获得治县要略
        game_knowledge = ''
        if agent.role in ('ADVISOR', 'DEPUTY'):
            game_knowledge = cls._build_game_knowledge(game)

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
            'game_knowledge': game_knowledge,
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
        # 0. 师爷问策次数限制
        if agent.role == 'ADVISOR':
            county = game.county_data
            level = county.get('advisor_level', 1)
            used = county.get('advisor_questions_used', 0)
            if used >= level:
                return {
                    'error': '本季度师爷已无余力回答更多问题，请推进到下一季度',
                    'questions_used': used,
                    'questions_limit': level,
                }

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

        # 4. 师爷问策次数计数
        if agent.role == 'ADVISOR' and 'error' not in result:
            county = game.county_data
            county['advisor_questions_used'] = county.get('advisor_questions_used', 0) + 1
            game.county_data = county
            game.save(update_fields=['county_data'])

        return result

    @classmethod
    def _chat_full(cls, ctx, game, agent):
        """FULL agent: LLM JSON对话"""
        template_name = 'advisor_chat_json' if agent.role == 'ADVISOR' else 'agent_full_chat_json'
        system_prompt, user_prompt = PromptRegistry.render(
            template_name, **ctx,
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
