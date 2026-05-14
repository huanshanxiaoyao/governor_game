"""Agent 服务层 — 初始化、上下文构建、对话处理"""
import logging
import random

from ..agent_defs import MVP_AGENTS, MVP_RELATIONSHIPS
from ..models import Agent, DialogueMessage, Relationship
from .local_npc import build_county_local_agent_definitions, build_yamen_staff_definitions, ensure_county_local_cast
from .state import load_county_state, save_player_state

from llm.client import LLMClient
from llm.prompts import PromptRegistry

logger = logging.getLogger('game')


class AgentService:
    """管理NPC Agent的核心服务"""

    CENTRAL_OFFICIAL_ROLES = (
        'CABINET_CHIEF', 'CABINET_MEMBER',
        'MINISTER', 'VICE_MINISTER',
        'CHIEF_CENSOR', 'VICE_CENSOR', 'CENSOR',
    )
    LOCAL_RELATION_PROFILES = {
        'villager_to_gentry': {
            'seasoned_old_farmer': 5,
            'marketwise_householder': -5,
            'fiery_tenant_leader': -20,
            'educated_youth': 6,
            'cautious_smallholder': -12,
            'security_burdened_father': 4,
        },
        'gentry_to_elder': {
            'wealthy_power_broker': (-20, 'rivalry', '大户作风强横，耆老看不惯其恃财压人'),
            'reformist_scholar_gentry': (35, 'respect', '同为读书人，彼此谈得来，也愿互相给面子'),
            'well_connected_opportunist': (-8, 'unease', '耆老对其攀附权势颇有微词'),
        },
        'gentry_to_headman': {
            'clan_elder_landlord': (20, 'cooperation', '同在乡里主事，遇事仍需彼此照应'),
            'smallholder_pragmatist': (15, 'friendly', '都盼着小村子安稳过日子，平日尚算说得拢'),
            'wealthy_power_broker': (-10, 'tension', '地主扩张心切，里长对其兼并手段颇有怨言'),
            'well_connected_opportunist': (-6, 'pressure', '里长顾忌其外头门路，往来时多有防备'),
        },
        'peer_pairs': {
            frozenset({'wealthy_power_broker', 'well_connected_opportunist'}):
                (30, 'alliance', '两家都善于经营关系，私下常有利益往来'),
            frozenset({'reformist_scholar_gentry', 'well_connected_opportunist'}):
                (-15, 'tension', '一方讲名声教化，一方讲门路算计，彼此多有掣肘'),
        },
    }

    # ------------------------------------------------------------------
    # 1. Initialization
    # ------------------------------------------------------------------

    @classmethod
    def initialize_agents(cls, game):
        """为新游戏创建县衙核心 NPC 与按村生成的地主/村民代表。"""
        import copy

        county = load_county_state(game)
        if ensure_county_local_cast(county):
            save_player_state(game, county)

        local_prefecture = (county.get('admin_location') or {}).get('prefecture', '')
        player_si = county.get('player_social_identity') or {}

        name_to_agent = {}
        all_defs = (
            list(MVP_AGENTS)
            + build_yamen_staff_definitions(county)
            + build_county_local_agent_definitions(county)
        )

        for defn in all_defs:
            agent = Agent.objects.create(
                game=game,
                name=defn['name'],
                role=defn['role'],
                role_title=defn['role_title'],
                tier=defn['tier'],
                attributes=copy.deepcopy(defn['attributes']),
            )
            name_to_agent[defn['name']] = agent

        for a_name, b_name, affinity, data in MVP_RELATIONSHIPS:
            agent_a = name_to_agent.get(a_name)
            agent_b = name_to_agent.get(b_name)
            if agent_a is None or agent_b is None:
                continue
            Relationship.objects.create(agent_a=agent_a, agent_b=agent_b, affinity=affinity, data=data)

        cls._create_dynamic_local_relationships(name_to_agent)

        # ── 社交身份后处理 ──
        all_agents = list(name_to_agent.values())
        if local_prefecture:
            cls._resolve_local_social_identities(all_agents, local_prefecture)
        if player_si:
            cls._apply_hometown_bonuses(all_agents, player_si)
        cls._initialize_clans(game, all_agents, county)

        return all_agents

    @classmethod
    def _create_dynamic_local_relationships(cls, name_to_agent):
        """按 village/persona 生成本地关系，而非依赖固定姓名。"""
        villages = {}
        gentry_by_persona = {}
        elder = name_to_agent.get('李秀才')
        headman = name_to_agent.get('张铁根')
        created_pairs = set()

        def _create(agent_a, agent_b, affinity, data):
            if agent_a is None or agent_b is None:
                return
            pair = (agent_a.id, agent_b.id)
            if pair in created_pairs:
                return
            Relationship.objects.create(
                agent_a=agent_a,
                agent_b=agent_b,
                affinity=affinity,
                data=data,
            )
            created_pairs.add(pair)

        for agent in name_to_agent.values():
            attrs = agent.attributes or {}
            village_name = attrs.get('village_name')
            persona_id = attrs.get('persona_id')
            if village_name:
                villages.setdefault(village_name, {})[agent.role] = agent
            if agent.role == 'GENTRY' and agent.role_title == '地主' and persona_id:
                gentry_by_persona[persona_id] = agent

        for village_name, members in villages.items():
            gentry = members.get('GENTRY')
            villager = members.get('VILLAGER')
            if gentry and gentry.role_title == '地主' and villager and villager.role_title == '村民代表':
                affinity, rel_type, desc = cls._derive_villager_gentry_relation(villager, gentry)
                _create(villager, gentry, affinity, {'type': rel_type, 'desc': desc, 'generated': 'local'})

        for persona_id, (affinity, rel_type, desc) in cls.LOCAL_RELATION_PROFILES['gentry_to_elder'].items():
            _create(
                gentry_by_persona.get(persona_id), elder, affinity,
                {'type': rel_type, 'desc': desc, 'generated': 'local'},
            )

        for persona_id, (affinity, rel_type, desc) in cls.LOCAL_RELATION_PROFILES['gentry_to_headman'].items():
            _create(
                gentry_by_persona.get(persona_id), headman, affinity,
                {'type': rel_type, 'desc': desc, 'generated': 'local'},
            )

        for personas, (affinity, rel_type, desc) in cls.LOCAL_RELATION_PROFILES['peer_pairs'].items():
            first, second = list(personas)
            _create(
                gentry_by_persona.get(first), gentry_by_persona.get(second), affinity,
                {'type': rel_type, 'desc': desc, 'generated': 'local'},
            )

    @classmethod
    def _derive_villager_gentry_relation(cls, villager, gentry):
        villager_attrs = villager.attributes or {}
        gentry_attrs = gentry.attributes or {}
        villager_persona = villager_attrs.get('persona_id', '')
        gentry_persona = gentry_attrs.get('persona_id', '')

        affinity = cls.LOCAL_RELATION_PROFILES['villager_to_gentry'].get(villager_persona, 0)
        # high assertiveness → less agreeable → lower affinity with tenants (invert)
        affinity += int((0.5 - float(gentry_attrs.get('personality', {}).get('assertiveness', 0.5))) * 20)
        # high state_vs_people → less people-oriented → lower affinity (invert)
        affinity += int((0.5 - float(gentry_attrs.get('ideology', {}).get('state_vs_people', 0.5))) * 20)

        if villager_persona == 'fiery_tenant_leader':
            affinity -= 10
        if villager_persona == 'educated_youth' and gentry_persona == 'reformist_scholar_gentry':
            affinity += 15
        if gentry_persona == 'wealthy_power_broker':
            affinity -= 8
        if gentry_persona == 'smallholder_pragmatist':
            affinity += 6

        if affinity >= 20:
            return 25, 'gratitude', '本村代表认为此地主尚知顾念乡里，往来间颇有敬重'
        if affinity >= 5:
            return 10, 'cooperation', '同在一村，虽各有立场，平日仍需彼此周旋合作'
        if affinity > -10:
            return -5, 'tension', '田租与赋役牵动生计，本村代表与地主时有争执'
        if affinity > -20:
            return -15, 'fear', '代表对地主心存怨气，却顾忌其势力，不敢轻易翻脸'
        return -30, 'hostility', '代表长期不满地主盘剥，彼此积怨颇深'

    @classmethod
    def initialize_official_ties(cls, game):
        """在官场体系完成后，为地主生成与上层官员的强联系。"""
        county = load_county_state(game)
        county_type = county.get('county_type', '')
        province = (county.get('admin_location') or {}).get('province', '')
        if not province:
            return []

        gentry_agents = list(
            Agent.objects.filter(game=game, role='GENTRY', role_title='地主').order_by('id')
        )
        if not gentry_agents:
            return []

        province_officials = list(
            Agent.objects.filter(
                game=game,
                role__in=('PROVINCIAL_GOVERNOR', 'PROVINCIAL_COMMISSIONER'),
                attributes__province=province,
            ).order_by('id')
        )
        prefect = Agent.objects.filter(game=game, role='PREFECT').first()
        central_officials = list(
            Agent.objects.filter(game=game, role__in=cls.CENTRAL_OFFICIAL_ROLES).order_by('id')
        )
        candidate_pool = province_officials + central_officials
        existing_pairs = set(
            Relationship.objects.filter(
                agent_a__game=game,
                agent_a__role='GENTRY',
                agent_a__role_title='地主',
            ).values_list('agent_a_id', 'agent_b_id')
        )
        created = []

        def _create_strong_tie(gentry, official, *, tie_type, desc):
            if gentry is None or official is None:
                return
            pair = (gentry.id, official.id)
            if pair in existing_pairs:
                return
            affinity = random.randint(52, 70)
            Relationship.objects.create(
                agent_a=gentry,
                agent_b=official,
                affinity=affinity,
                data={
                    'type': tie_type,
                    'desc': desc,
                    'generated': 'official_tie',
                    'strength': 'strong',
                },
            )
            existing_pairs.add(pair)
            created.append((gentry.name, official.name, tie_type))

        if county_type == 'fiscal_core' and candidate_pool:
            min_required = min(2, len(gentry_agents))
            max_allowed = min(3, len(gentry_agents))
            gentry_count = max_allowed if min_required == max_allowed else random.randint(min_required, max_allowed)
            selected_gentries = random.sample(gentry_agents, gentry_count)
            selected_officials = random.sample(candidate_pool, min(len(candidate_pool), gentry_count))
            for idx, gentry in enumerate(selected_gentries):
                official = selected_officials[idx % len(selected_officials)]
                _create_strong_tie(
                    gentry, official, tie_type='patronage',
                    desc='财赋重地豪强根基深厚，与上层官员往来频仍，彼此多有照应',
                )
            return created

        if county_type == 'clan_governance':
            clan_candidates = [official for official in [prefect] + province_officials + central_officials if official]
            for gentry in gentry_agents:
                surname = (gentry.name or '')[:1]
                if not surname:
                    continue
                for official in clan_candidates:
                    if (official.name or '')[:1] != surname:
                        continue
                    _create_strong_tie(
                        gentry, official, tie_type='kinship',
                        desc=f'同为{surname}姓，在地方舆论中被视作一门一谱，往来尤为密切',
                    )
            return created

        if candidate_pool:
            gentry = random.choice(gentry_agents)
            official = random.choice(candidate_pool)
            _create_strong_tie(
                gentry, official, tie_type='patronage',
                desc='此地并非财赋重镇，但该地主另有门路，可借上层声势自保',
            )

        return created

    # ------------------------------------------------------------------
    # 1b. 社会身份后处理
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_local_social_identities(agents, local_prefecture: str):
        """将 MVP 本地 NPC 的 social_identity 中的 '__local__' 替换为实际府名。"""
        to_update = []
        for agent in agents:
            attrs = agent.attributes or {}
            si = attrs.get('social_identity')
            if not si or si.get('native_place') != '__local__':
                continue
            si['native_place'] = local_prefecture
            si['clan_id'] = f"{local_prefecture}{si['surname']}氏"
            to_update.append(agent)
        if to_update:
            for agent in to_update:
                Agent.objects.filter(pk=agent.pk).update(attributes=agent.attributes)

    @staticmethod
    def _apply_hometown_bonuses(agents, player_si: dict):
        """
        对与玩家同籍贯或年龄相仿的 NPC 施加 player_affinity 加成，
        并在 attributes 中记录 hometown_relation 标签。
        同籍贯: +20；年龄差 ≤5 岁: 额外 +10（两者可叠加，上限 +30）。
        """
        player_native = player_si.get('native_place', '')
        player_age = player_si.get('age', 0)
        if not player_native and not player_age:
            return

        to_update = []
        for agent in agents:
            attrs = agent.attributes or {}
            si = attrs.get('social_identity') or {}
            agent_native = si.get('native_place', '')
            agent_age = attrs.get('age', 0)

            bonus = 0
            tags = []

            if player_native and agent_native and agent_native not in ('__local__', '') \
                    and agent_native == player_native:
                bonus += 20
                tags.append('同乡')

            if player_age and agent_age and abs(agent_age - player_age) <= 5:
                bonus += 10
                tags.append('年岁相仿')

            if bonus <= 0:
                continue

            attrs['player_affinity'] = min(99, attrs.get('player_affinity', 50) + bonus)
            if tags:
                attrs['hometown_relation'] = '、'.join(tags)
            to_update.append(agent)

        if to_update:
            for agent in to_update:
                Agent.objects.filter(pk=agent.pk).update(attributes=agent.attributes)

    @staticmethod
    def _agent_clan_power(agent) -> int:
        """根据 role 与属性估算该 agent 对宗族实力的贡献值。"""
        attrs = agent.attributes or {}
        intelligence = attrs.get('intelligence', 5)
        charisma = attrs.get('charisma', 5)
        role = agent.role
        if role == 'GENTRY':
            return 30 + intelligence * 4
        if role == 'VILLAGER':
            return 10 + charisma * 2
        if role in ('ADVISOR', 'DEPUTY'):
            return 20
        return 5

    # 宗族本地成员只算地主（GENTRY）；村民代表（VILLAGER）不代表宗族势力
    _LOCAL_ROLES = {'GENTRY'}

    @classmethod
    def _initialize_clans(cls, game, agents, county):
        """
        从 agents 的 social_identity 聚合宗族信息，写入 county_data['clans']。
        宗族定义：府 × 姓氏（如"登州府张氏"），是跨县的府级网络。
        知县只能看到本县有 GENTRY/VILLAGER 落脚点的宗族。

        每条宗族数据结构：
          local_members:        本县 GENTRY/VILLAGER agent ID 列表
          local_villages:       本县有族人的村庄名列表
          local_power:          本县地主实力累计
          official_members:     同宗但任职官员的 agent ID 列表（任意角色）
          other_county_branches:同府其他县的族人数量级（游戏初始化时随机模拟，0-4）
          total_influence:      宗族整体影响力（local_power + 官员加成 + 他县加成）
          clan_affinity:        本县 GENTRY player_affinity 均值
        """
        clans: dict = {}
        local_prefecture = (county.get('admin_location') or {}).get('prefecture', '')

        # 保留已有的 other_county_branches（避免每次刷新随机变化）
        existing_clans = county.get('clans') or {}

        for agent in agents:
            si = (agent.attributes or {}).get('social_identity') or {}
            clan_id = si.get('clan_id', '')
            native_place = si.get('native_place', '')
            if not clan_id or clan_id == '__local__':
                continue
            # 只纳入与本县同府的宗族
            if local_prefecture and native_place != local_prefecture:
                continue

            if clan_id not in clans:
                existing = existing_clans.get(clan_id) or {}
                clans[clan_id] = {
                    'local_members': [],
                    'local_villages': [],
                    'local_power': 0,
                    'official_members': [],
                    'other_county_branches': existing.get(
                        'other_county_branches', random.randint(0, 4)
                    ),
                    'total_influence': 0,
                    'clan_affinity': 50,
                }

            role = agent.role
            if role in cls._LOCAL_ROLES:
                clans[clan_id]['local_members'].append(agent.id)
                clans[clan_id]['local_power'] += cls._agent_clan_power(agent)
                village_name = (agent.attributes or {}).get('village_name', '')
                if village_name and village_name not in clans[clan_id]['local_villages']:
                    clans[clan_id]['local_villages'].append(village_name)
            else:
                clans[clan_id]['official_members'].append(agent.id)

        # 只保留本县有落脚点的宗族（知县视角）
        clans = {cid: c for cid, c in clans.items() if c['local_members']}

        # 计算 clan_affinity（只取 GENTRY 均值）和 total_influence
        agent_map = {a.id: a for a in agents}
        for clan_id, clan in clans.items():
            gentry_agents = [
                agent_map[mid] for mid in clan['local_members']
                if mid in agent_map and agent_map[mid].role == 'GENTRY'
            ]
            if gentry_agents:
                avg = sum(
                    (a.attributes or {}).get('player_affinity', 50)
                    for a in gentry_agents
                ) / len(gentry_agents)
                clan['clan_affinity'] = round(avg)

            official_influence = len(clan['official_members']) * 50
            other_influence = clan['other_county_branches'] * 30
            clan['total_influence'] = clan['local_power'] + official_influence + other_influence
            # 向后兼容：结算代码读 'power'，保持与 local_power 同步
            clan['power'] = clan['local_power']

        county['clans'] = clans
        save_player_state(game, county)

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
        '- 民心和治安每月都会自然衰减\n'
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
        '- 义仓和赈灾可减轻灾害人口损失；赈灾可在灾后安抚民心\n'
        '\n'
        '四、灾害与风险\n'
        '- 水灾、旱灾、蝗灾、疫病可能在夏季发生\n'
        '- 水利设施可降低水患概率并减轻秋收减产；义仓和赈灾不影响秋收减产，仅影响人口损失\n'
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
        county_type = load_county_state(game).get('county_type', '')
        county_type_desc = cls.COUNTY_TYPE_DESCS.get(county_type, '')
        return cls.GAME_KNOWLEDGE_TEMPLATE.format(county_type_desc=county_type_desc)

    @staticmethod
    def _build_recent_policy_brief(game, max_items=5):
        from ..models import EventLog
        season = game.current_season
        logs = EventLog.objects.filter(
            game=game,
            category='INVESTMENT',
            season__gte=season - 3,
        ).order_by('-season', '-id')[:max_items]
        lines = [f'  - 第{log.season}月：{log.description}' for log in logs]
        return '\n'.join(lines) if lines else '（近期无显著施政）'

    @staticmethod
    def _build_recent_history_with_player(game, agent, max_promises=3, max_events=5):
        from ..models import Promise, EventLog
        lines = []
        promises = Promise.objects.filter(
            game=game, agent=agent,
            status__in=('PENDING', 'BROKEN'),
        ).order_by('-season_made')[:max_promises]
        for p in promises:
            tag = '未兑现' if p.status == 'BROKEN' else f'尚未兑现（截止第{p.deadline_season}月）'
            lines.append(f'  - 大人曾许：{p.description}（{tag}）')
        events = EventLog.objects.filter(
            game=game, data__agent_id=agent.id,
        ).order_by('-season', '-id')[:max_events]
        for ev in events:
            lines.append(f'  - 第{ev.season}月：{ev.description}')
        return '\n'.join(lines) if lines else '（近期无具体往来）'

    @classmethod
    def build_system_context(cls, game, agent, player_message=''):
        """构建模板渲染所需的全部 kwargs（统一 ctx 字典，含稳定段与动态段）"""
        from . import AgentMemoryService
        from .rumors import RumorsService
        attrs = agent.attributes or {}

        # 知府对话使用专属上下文（模糊县情）
        if agent.role == 'PREFECT':
            from .ai_prefect import PrefectAIService
            return PrefectAIService.build_chat_context(agent, game)

        ctx = {
            # 稳定段
            'agent_name': agent.name,
            'role_title': agent.role_title,
            'bio': attrs.get('bio', ''),
            'backstory': attrs.get('backstory', ''),
            'age_desc': cls._describe_age_gender(attrs, game),
            'gender': attrs.get('gender', '男'),
            'capability_desc': cls._describe_capability(attrs),
            'personality_desc': cls._describe_personality(attrs),
            'ideology_desc': cls._describe_ideology(attrs),
            'reputation_desc': cls._describe_reputation(attrs),
            'goals_desc': cls._describe_goals(attrs),
            'relationships_desc': cls._describe_relationships(agent),
            'speech_examples': '\n'.join(
                f'  - {ex}' for ex in cls.get_speech_examples(agent)
            ) or '（无）',

            # 动态段
            'county_summary': cls._summarize_county(game),
            'recent_policy_brief': cls._build_recent_policy_brief(game),
            'audible_rumors': '\n'.join(
                f'  - {r}' for r in RumorsService.get_audible_for(
                    game, agent, limit=3)
            ) or '（暂无传闻）',
            'recent_history_with_player': cls._build_recent_history_with_player(
                game, agent),
            'relevant_memories': '\n'.join(
                f'  - [{m.topic}] {m.text}' for m in
                AgentMemoryService.fetch_relevant(
                    agent, current_season=game.current_season,
                    query_text=player_message, limit=8)
            ) or '（无相关记忆）',
            'affinity': int(attrs.get('player_affinity', 50)),
            'season': game.current_season,
            'player_message': player_message,

            # 向后兼容：旧调用者可能直接读 memory_desc / village_summary / game_knowledge
            'memory_desc': cls._describe_recent_memory(agent),
            'village_summary': '',
            'game_knowledge': '',
        }

        if agent.role in ('ADVISOR', 'DEPUTY'):
            ctx['game_knowledge'] = cls._build_game_knowledge(game)
        if agent.role in ('GENTRY', 'VILLAGER'):
            ctx['village_summary'] = cls._get_village_summary(
                game, attrs.get('village_name'))

        return ctx

    @staticmethod
    def _describe_personality(attrs):
        p = attrs.get('personality', {})
        parts = []
        soc = p.get('sociability', 0.5)
        if soc >= 0.7:
            parts.append('善于社交，注重人际关系')
        elif soc <= 0.3:
            parts.append('独立自主，不易受舆论左右')
        rat = p.get('rationality', 0.5)
        if rat >= 0.7:
            parts.append('思虑严谨，凡事讲究条理')
        elif rat <= 0.3:
            parts.append('凭直觉行事，感情用事')
        asr = p.get('assertiveness', 0.5)
        if asr >= 0.7:
            parts.append('性格强硬，立场坚定')
        elif asr <= 0.3:
            parts.append('为人温和，善于妥协')
        return '；'.join(parts) if parts else '性情平和'

    @staticmethod
    def _describe_ideology(attrs):
        ideo = attrs.get('ideology', {})
        parts = []
        sp = ideo.get('state_vs_people', 0.5)
        if sp >= 0.7:
            parts.append('以社稷大局为重，强调上缴指标')
        elif sp <= 0.3:
            parts.append('重视黎民疾苦，优先民生')
        cl = ideo.get('central_vs_local', 0.5)
        if cl >= 0.7:
            parts.append('恭顺中央，凡事请示上级')
        elif cl <= 0.3:
            parts.append('主张地方自主，因地制宜')
        pi = ideo.get('pragmatic_vs_ideal', 0.5)
        if pi >= 0.7:
            parts.append('务实妥协，注重实际成效')
        elif pi <= 0.3:
            parts.append('坚守原则，追求理想道义')
        return '；'.join(parts) if parts else '立场中庸'

    @staticmethod
    def _describe_goals(attrs):
        goals = attrs.get('goals', [])
        if not goals:
            return '暂无明确目标'
        return '\n'.join(f'- {g}' for g in goals)

    @staticmethod
    def _describe_capability(attrs):
        parts = []
        intel = int(attrs.get('intelligence', 50))
        if intel >= 80:
            parts.append('心思缜密')
        elif intel >= 60:
            parts.append('颇有见识')
        elif intel >= 40:
            parts.append('心思尚算清明')
        else:
            parts.append('反应迟钝')

        charisma = int(attrs.get('charisma', 50))
        if charisma >= 80:
            parts.append('言谈讨喜，颇受推崇')
        elif charisma >= 60:
            parts.append('言语得体')
        elif charisma >= 40:
            parts.append('言语平实')
        else:
            parts.append('木讷寡言')

        loyalty = int(attrs.get('loyalty', 50))
        if loyalty >= 80:
            parts.append('对大人忠心耿耿')
        elif loyalty >= 60:
            parts.append('与大人尚算同心')
        elif loyalty >= 40:
            parts.append('对大人态度中立')
        else:
            parts.append('对大人心存芥蒂')
        return '；'.join(parts) + '。'

    @staticmethod
    def _describe_reputation(attrs):
        rep = attrs.get('reputation') or {}
        parts = []
        integrity = int(rep.get('integrity', 50))
        if integrity >= 70:
            parts.append('在乡里清名素著')
        elif integrity <= 30:
            parts.append('清名稍欠')
        competence = int(rep.get('competence', 50))
        if competence >= 70:
            parts.append('办事颇有干才')
        elif competence <= 30:
            parts.append('办事多有疏漏')
        popularity = int(rep.get('popularity', 50))
        if popularity >= 70:
            parts.append('在乡邻间颇有人缘')
        elif popularity <= 30:
            parts.append('乡邻多有微词')
        authority = int(rep.get('authority', 50))
        if authority >= 70:
            parts.append('威名颇重，村民敬畏')
        elif authority <= 30:
            parts.append('威望平平')
        return '；'.join(parts) + '。' if parts else '声望平平。'

    @staticmethod
    def _describe_age_gender(attrs, game):
        age_base = int(attrs.get('age_base', 40))
        years_elapsed = max(0, (getattr(game, 'current_season', 1) - 1) // 12)
        age = age_base + years_elapsed
        gender = attrs.get('gender', '男')
        if age >= 60:
            age_word = '年逾花甲'
        elif age >= 50:
            age_word = '年近五旬'
        elif age >= 35:
            age_word = '正当壮年'
        elif age >= 20:
            age_word = '年轻力壮'
        else:
            age_word = '尚是少年'
        return f'{age_word}的{gender}子'

    @staticmethod
    def get_speech_examples(agent):
        attrs = agent.attributes or {}
        ex = attrs.get('speech_examples')
        if ex:
            return list(ex)
        if agent.role == 'PREFECT':
            gm = (agent.game.county_data or {}).get('governor_meta') or {}
            return list(gm.get('speech_examples') or [])
        return []

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
        c = load_county_state(game)
        total_pop = sum(v['population'] for v in c.get('villages', []))
        total_farmland = sum(v['farmland'] for v in c.get('villages', []))
        disaster = c.get('disaster_this_year')
        disaster_text = '无' if not disaster else f"{disaster['type']}(严重度{disaster['severity']:.0%})"
        tax_rate = c.get('tax_rate')
        try:
            tax_rate_text = f"{float(tax_rate):.0%}"
        except (TypeError, ValueError):
            tax_rate_text = "—"

        return (
            f"民心: {c.get('morale', '?')}, 治安: {c.get('security', '?')}, "
            f"商业: {c.get('commercial', '?')}, 文教: {c.get('education', '?')}\n"
            f"县库: {c.get('treasury', '?')}两, 税率: {tax_rate_text}\n"
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
        for v in load_county_state(game).get('villages', []):
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
        ctx = cls.build_system_context(game, agent, player_message=player_message)

        # 3. 根据tier选择不同处理方式
        if agent.tier == 'FULL':
            result = cls._chat_full(ctx, game, agent)
        else:
            result = cls._chat_light(ctx, game, agent)

        # 4. 异步提取承诺（本县 NPC 角色）
        if 'error' not in result and agent.role in ('ADVISOR', 'DEPUTY', 'GENTRY', 'VILLAGER'):
            import threading
            from .promise import PromiseService

            def _extract_chat_promises():
                try:
                    PromiseService.extract_and_save(
                        game, agent, None, player_message,
                        context_type='交谈',
                    )
                except Exception as e:
                    logger.warning("Chat promise extraction failed (non-fatal): %s", e)

            threading.Thread(target=_extract_chat_promises, daemon=True).start()

        return result

    @classmethod
    def _chat_full(cls, ctx, game, agent):
        """FULL agent: LLM JSON对话"""
        if agent.role in ('ADVISOR', 'DEPUTY'):
            template_name = 'official_chat_json'
        elif agent.role == 'PREFECT':
            template_name = 'prefect_chat_json'
        else:
            template_name = 'commoner_chat_json'
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
        from llm.context import LLMContext
        from llm import call_sources
        try:
            client = LLMClient(context=LLMContext(
                call_source=call_sources.AGENT_CHAT,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
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

        # 7. 记录 NPC 请求到 EventLog
        for req in result.get('requests', []):
            req_type = req.get('type', 'OTHER')
            req_desc = req.get('description', '')
            if req_desc:
                from .eventlog import log_game_event
                log_game_event(
                    game,
                    event_type='npc_request',
                    category='SOCIAL',
                    season=game.current_season,
                    description=f'{agent.name}请求：{req_desc}',
                    data={
                        'agent_id': agent.id,
                        'agent_name': agent.name,
                        'request_type': req_type,
                        'description': req_desc,
                    },
                )

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

        from llm.context import LLMContext
        from llm import call_sources
        try:
            client = LLMClient(context=LLMContext(
                call_source=call_sources.AGENT_CHAT,
                game_id=game.id,
                season=game.current_season,
                user_id=game.user_id,
            ))
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
        """确保响应包含所有必要字段。new_memory 兼容 字符串/对象/缺失 三种形态。"""
        defaults = {
            'dialogue': '（沉默不语）',
            'reasoning': '',
            'attitude_change': 0,
            'requests': [],
        }
        for key, default in defaults.items():
            if key not in result:
                result[key] = default

        # Clamp attitude_change
        try:
            result['attitude_change'] = max(-5, min(5, int(result['attitude_change'])))
        except (ValueError, TypeError):
            result['attitude_change'] = 0

        # Ensure requests is a list
        if not isinstance(result.get('requests'), list):
            result['requests'] = []

        # new_memory: 兼容 字符串(旧) / dict(新) / 缺失
        valid_topics = ('POLICY', 'PROMISE', 'DISASTER',
                        'NEGOTIATION', 'CHAT', 'OTHER')
        nm = result.get('new_memory')
        if isinstance(nm, str):
            if nm:
                result['new_memory'] = {
                    'text': nm, 'topic': 'CHAT', 'importance': 5,
                }
            else:
                result['new_memory'] = None
        elif isinstance(nm, dict):
            text = nm.get('text', '') if isinstance(nm.get('text'), str) else ''
            topic = nm.get('topic') or 'CHAT'
            if topic not in valid_topics:
                topic = 'OTHER'
            try:
                importance = max(1, min(10, int(nm.get('importance', 5))))
            except (TypeError, ValueError):
                importance = 5
            result['new_memory'] = {
                'text': text, 'topic': topic, 'importance': importance,
            }
        else:
            result['new_memory'] = None

        return result

    @staticmethod
    def _apply_chat_effects(agent, result):
        """更新Agent的记忆（好感度仅由结局决定，对话轮不直接改affinity）"""
        attrs = agent.attributes

        # 对话轮 attitude_change 不再写入 player_affinity，
        # 仅用于影响LLM下一轮的语气和willingness参数。
        # 好感度由谈判结局硬编码决定（见 negotiation.py 各 _apply_*_outcome）。

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
        from django.db.models import Q
        from .clan_youth import ClanYouthService

        ClanYouthService.normalize_game_nominations(game)
        agent_qs = list(Agent.objects.filter(game=game).order_by('id'))

        # 批量查询关系，避免 N+1
        all_rels = Relationship.objects.filter(
            Q(agent_a__game=game) | Q(agent_b__game=game)
        ).select_related('agent_a', 'agent_b')

        rel_map = {}
        for r in all_rels:
            for aid, other in [(r.agent_a_id, r.agent_b), (r.agent_b_id, r.agent_a)]:
                if aid not in rel_map:
                    rel_map[aid] = []
                rel_map[aid].append({
                    'name': other.name,
                    'role_title': other.role_title,
                    'affinity': r.affinity,
                    'desc': r.data.get('desc', ''),
                })

        result = []
        for a in agent_qs:
            memory = a.attributes.get('memory', [])
            recent_memory = memory[-3:] if memory else []
            attrs = a.attributes
            youth_attrs = {}
            if a.role == 'CLAN_YOUTH':
                youth_attrs = {
                    'exam_eligible': ClanYouthService.is_active_nomination(attrs, game.current_season),
                    'generated_season': attrs.get('generated_season', 0),
                    'can_nominate': ClanYouthService.is_current_year_youth(attrs, game.current_season),
                }
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
                'province': attrs.get('province', ''),
                'prefecture': attrs.get('prefecture', ''),
                'relationships': rel_map.get(a.id, []),
                # 社会身份（年龄 / 籍贯 / 宗族）
                'age': attrs.get('age'),
                'social_identity': attrs.get('social_identity', {}),
                'hometown_relation': attrs.get('hometown_relation', ''),
                # 宗族后生专用
                'attributes': youth_attrs,
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
