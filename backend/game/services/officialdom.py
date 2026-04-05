"""官场体系服务 — 初始化君主、派系、官员层级（含全国省/府）"""
import copy
import json
import logging
import os
import random

from ..models import Agent, Faction, MonarchProfile
from .officialdom_constants import (
    ARCHETYPE_ATTRIBUTES,
    ASSESSMENT_TENDENCIES,
    CHARACTER_ATTRIBUTE_MAP,
    DEFAULT_OFFICIAL_ATTRIBUTES,
    EXCLUDED_PROVINCES,
    FACTION_TEMPLATES,
    MONARCH_ARCHETYPE_MAP,
    OFFICIAL_GIVEN_NAMES,
    OFFICIAL_SURNAMES,
    OFFICIAL_SURNAME_WEIGHTS,
    POSITION_SPECS,
    PROVINCE_DISPLAY_NAMES,
)
from .state import load_county_state, save_player_state

logger = logging.getLogger('game')

# 历史人物籍贯 → 府级行政区划映射（按 key_persons.json 中的 ID）
# 无法对应到府级单位的留 None，并在注释中说明原因
_HOMETOWN_TO_PREFECTURE = {
    # ── 文臣 ──
    '0004': '凤阳府',       # 李善长: 定远人（凤阳府定远县）
    '0005': '处州府',       # 刘基: 青田人（处州府青田县）
    '0007': '凤阳府',       # 胡惟庸: 定远人（凤阳府定远县）
    '0011': '苏州府',       # 姚广孝: 苏州人
    '0015': '吉安府',       # 杨士奇: 泰和人（江西吉安府泰和县）
    '0016': '建宁府',       # 杨荣: 建安人（福建建宁府建安县）
    '0017': '荆州府',       # 杨溥: 石首人（湖广荆州府石首县）
    '0020': '杭州府',       # 于谦: 钱塘人（浙江杭州府钱塘县）
    '0026': '绍兴府',       # 王守仁: 余姚人（浙江绍兴府余姚县）
    '0028': '袁州府',       # 严嵩: 分宜人（江西袁州府分宜县）
    '0029': '松江府',       # 徐阶: 华亭人（南直隶松江府华亭县）
    '0030': '琼州府',       # 海瑞: 琼山人（广东琼州府琼山县）
    '0031': '荆州府',       # 张居正: 江陵人（湖广荆州府江陵县）
    '0035': '广州府',       # 袁崇焕: 东莞人（广东广州府东莞县）
    '0080': '台州府',       # 方孝孺: 宁海人（浙江台州府宁海县）
    '0081': '袁州府',       # 黄子澄: 分宜人（江西袁州府）
    '0082': '应天府',       # 齐泰: 溧水人（南直隶应天府溧水县）
    '0086': '饶州府',       # 夏原吉: 德兴人（江西饶州府德兴县）
    '0090': '南阳府',       # 李贤: 邓州人（河南南阳府邓州）
    '0092': '西安府',       # 王恕: 三原人（陕西西安府三原县）
    '0095': '镇江府',       # 杨一清: 镇江人（南直隶镇江府）
    '0096': '成都府',       # 杨廷和: 新都人（四川成都府新都县）
    '0098': '袁州府',       # 严世蕃: 分宜人（江西袁州府分宜县）
    '0099': '徽州府',       # 胡宗宪: 绩溪人（南直隶徽州府绩溪县）
    '0108': '处州府',       # 章溢: 龙泉人（浙江处州府龙泉县）
    '0109': '南阳府',       # 铁铉: 邓州人（河南南阳府邓州）
    '0112': '临江府',       # 金幼孜: 新淦人（江西临江府新淦县）
    '0113': '吉安府',       # 胡广: 吉水人（江西吉安府吉水县）
    '0116': '严州府',       # 商辂: 淳安人（浙江严州府淳安县）
    '0117': '杭州府',       # 于冕: 钱塘人（浙江杭州府钱塘县）
    '0121': '常州府',       # 唐顺之: 武进人（南直隶常州府武进县）
    '0122': '宁波府',       # 赵文华: 慈溪人（浙江宁波府慈溪县）
    '0124': '湖州府',       # 潘季驯: 乌程人（浙江湖州府乌程县）
    '0125': '宁波府',       # 沈一贯: 鄞县人（浙江宁波府鄞县）
    '0126': '顺天府',       # 李三才: 通州人（北直隶顺天府通州）
    '0128': '安庆府',       # 左光斗: 桐城人（南直隶安庆府桐城县）
    '0129': '开封府',       # 史可法: 祥符人（河南开封府祥符县）
    '0130': '贵阳军民府',   # 马士英: 贵阳人（贵州贵阳军民府）
    '0131': '保定府',       # 孙承宗: 高阳人（北直隶保定府高阳县）
    # ── 武将（用于兵部尚书/侍郎职位）──
    '0002': '凤阳府',       # 徐达: 濠州人（濠州即凤阳府）
    '0003': '凤阳府',       # 常遇春: 怀远人（凤阳府怀远县）
    '0008': '凤阳府',       # 蓝玉: 定远人（凤阳府定远县）
    '0032': '登州府',       # 戚继光: 山东蓬莱人（山东登州府蓬莱县）
    '0039': '太原府',       # 孙传庭: 山西代县人（山西太原府代州）
    '0072': '凤阳府',       # 汤和: 濠州人（凤阳府）
    '0073': '凤阳府',       # 朱文正: 濠州人（凤阳府）
    '0074': '凤阳府',       # 沐英: 濠州人（凤阳府）
    '0075': '凤阳府',       # 冯胜: 濠州人（凤阳府）
    '0076': '凤阳府',       # 傅友德: 濠州人（凤阳府）
    '0077': '庐州府',       # 赵普胜: 巢湖渔民（南直隶庐州府巢县）
    '0083': None,           # ⚠️ 李景隆: 籍贯不详
    '0084': '凤阳府',       # 耿炳文: 濠州人（凤阳府）
    '0088': '西安府',       # 石亨: 渭南人（陕西西安府渭南县）
    '0100': '泉州府',       # 俞大猷: 晋江人（福建泉州府晋江县）
    '0105': '凤阳府',       # 冯国用: 濠州人（凤阳府）
    '0106': '庐州府',       # 俞通海: 巢湖渔民（南直隶庐州府）
    '0110': None,           # ⚠️ 盛庸: 籍贯不详
    '0111': None,           # ⚠️ 平安: 滁州人 → 滁州为南直隶直隶州，无对应府级单位
    '0115': '真定府',       # 王骥: 束鹿人（北直隶真定府束鹿县）
    '0132': None,           # ⚠️ 祖大寿: 辽东人 → 辽东都司不在布政使司行政区划内
    # ── 君主 ──
    '0001': '凤阳府',       # 朱元璋: 濠州钟离人（濠州即凤阳府）
    '0010': '应天府',       # 朱棣: 应天人
    '0013': '顺天府',       # 朱高炽: 北平人（北平即顺天府）
    '0014': '顺天府',       # 朱瞻基: 北平人
    '0018': '顺天府',       # 朱祁镇: 北京人
    '0021': '顺天府',       # 朱祁钰: 北京人
    '0022': '顺天府',       # 朱见深: 北京人
    '0024': '顺天府',       # 朱祐樘: 北京人
    '0025': '顺天府',       # 朱厚照: 北京人
    '0027': None,           # ⚠️ 朱厚熜(嘉靖): 湖广安陆人 → 安陆州为直隶州，无对应府级单位
    '0033': '顺天府',       # 朱翊钧: 北京人
    '0036': '顺天府',       # 朱由检: 北京人
    '0079': '应天府',       # 朱允炆: 应天人
    '0102': '顺天府',       # 朱载垕: 北京人
}

# 数据文件路径（位于 game/data/ 目录下，Docker 可访问）
KEY_PERSONS_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'key_persons.json',
)
ADMIN_DIVISIONS_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'xingzhengquhua.json',
)


class OfficialdomService:
    """管理官场体系的核心服务"""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @classmethod
    def initialize_officialdom(cls, game):
        """为新游戏初始化完整官场体系

        调用时机: GameListCreateView.post 中，在 NeighborService.create_neighbors 之后
        """
        # 1. 选择君主原型
        archetype = cls._select_archetype()

        # 2. 加载历史人物池
        pool = cls._load_character_pool()

        # 3. 已使用的人物ID集合（避免重复，池耗尽时允许复用）
        used_ids = set()

        # 4. 创建皇帝 Agent + MonarchProfile
        emperor_agent = cls._create_monarch_agent(game, archetype, pool, used_ids)

        # 5. 创建派系
        factions = cls._create_factions(game, archetype)

        # 6. 创建中央各级官员（内阁、六部、都察院）
        officials = cls._create_officials(game, pool, factions, used_ids)

        # 7. 确定玩家所在省/府
        player_province, player_prefecture = cls._pick_player_location()

        # 8. 创建全国地方官员（巡抚/布政使/按察使/知府）
        local_officials = cls._create_local_officials(game, pool, factions, used_ids)
        officials.extend(local_officials)

        # 9. 为现有知府追加官场属性（含省/府归属）
        cls._link_existing_prefect(
            game, pool, factions, used_ids,
            province=player_province,
            prefecture=player_prefecture,
        )

        # 10. 设置上下级层级关系
        all_officials = [emperor_agent] + officials
        cls._set_hierarchy(game, all_officials)

        # 11. 指定派系领袖
        cls._assign_faction_leaders(game, factions, officials)

        # 12. 在 county_data 中记录行政归属
        cls._assign_admin_location(game, player_province, player_prefecture)

        # 13. 县内地主与上层官员的动态强联系
        from .agent import AgentService
        AgentService.initialize_official_ties(game)

        logger.info("官场体系初始化完成: game=%s, archetype=%s, officials=%d",
                     game.id, archetype, len(officials) + 1)

        return all_officials

    # ------------------------------------------------------------------
    # 内部方法 — 通用
    # ------------------------------------------------------------------

    @staticmethod
    def _select_archetype():
        """随机选择君主原型"""
        return random.choice(list(MONARCH_ARCHETYPE_MAP.keys()))

    @classmethod
    def _load_character_pool(cls):
        """读取 key_persons.json，按 类别 分组返回"""
        try:
            with open(KEY_PERSONS_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("无法加载 key_persons.json: %s", e)
            return {}

        pool = {}
        for person in data.get('人物数据表', []):
            category = person.get('类别', '其他')
            pool.setdefault(category, []).append(person)
        return pool

    @classmethod
    def _load_admin_divisions(cls):
        """读取 xingzhengquhua.json，返回行政区划数据"""
        try:
            with open(ADMIN_DIVISIONS_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("无法加载 xingzhengquhua.json: %s", e)
            return {}

    @classmethod
    def _pick_character(cls, pool, categories, used_ids):
        """从池中挑选一个未使用的历史人物，池耗尽时允许复用"""
        candidates = []
        for cat in categories:
            candidates.extend(pool.get(cat, []))

        if not candidates:
            return None

        # 优先选未使用的
        available = [p for p in candidates if p['ID'] not in used_ids]
        if available:
            chosen = random.choice(available)
            used_ids.add(chosen['ID'])
            return chosen

        # 池耗尽：允许复用（同一历史人物可以支撑多个游戏内官员）
        chosen = random.choice(candidates)
        return chosen

    @classmethod
    def _pick_specific_character(cls, pool, person_id, used_ids):
        """按ID挑选特定的历史人物"""
        for persons in pool.values():
            for p in persons:
                if p['ID'] == person_id:
                    used_ids.add(person_id)
                    return p
        return None

    @staticmethod
    def _anonymize_name(used_names, surname=None):
        """生成随机游戏内名字，保留历史原型的姓氏"""
        if not surname:
            surname = random.choices(OFFICIAL_SURNAMES, weights=OFFICIAL_SURNAME_WEIGHTS, k=1)[0]
        for _ in range(500):  # 防止无限循环
            name = surname + random.choice(OFFICIAL_GIVEN_NAMES)
            if name not in used_names:
                used_names.add(name)
                return name
        # fallback：保留姓氏加随机数避重
        return surname + str(random.randint(1, 999))

    @classmethod
    def _build_agent_attributes(cls, person, org, rank, faction_name,
                                superior_agent_id=None, province=None,
                                prefecture=None):
        """将历史人物数据转换为 Agent.attributes"""
        person_id = person['ID']

        # 从预映射获取数值属性，没有则用默认值
        base = copy.deepcopy(
            CHARACTER_ATTRIBUTE_MAP.get(person_id, DEFAULT_OFFICIAL_ATTRIBUTES)
        )

        # 补充官场专有字段
        base['hometown'] = person.get('籍贯', '不详')
        base['org'] = org
        base['rank'] = rank

        # 从历史原型生成 social_identity（用于前端籍贯显示）
        native_place = _HOMETOWN_TO_PREFECTURE.get(person_id)
        if native_place:
            surname = person.get('姓名', '')[:1]
            base['social_identity'] = {
                'surname': surname,
                'native_place': native_place,
                'clan_id': native_place + surname + '氏',
            }
        base['faction_name'] = faction_name
        base['superior_agent_id'] = superior_agent_id
        base['source_person_id'] = person_id
        base['deeds'] = person.get('主要事迹', '')
        base['political_views'] = person.get('政治观点', '')
        base['bio'] = person.get('个人品格与做事风格', '')
        base['backstory'] = person.get('主要事迹', '')[:200]
        base['assessment_tendency'] = ASSESSMENT_TENDENCIES.get(org, 'balance')
        base['player_affinity'] = 30
        base['memory'] = []

        # 年龄：按官阶基准 + 随机扰动（±5岁）
        # rank 1=阁揆/御史台长 → 55-65；rank 2=尚书/巡抚 → 50-60；
        # rank 3=侍郎 → 45-55；rank 4=知府 → 41-51；rank 5+=低阶 → 38-48
        _rank_age_base = {1: 60, 2: 55, 3: 50, 4: 46, 5: 43}
        age_base = _rank_age_base.get(rank, 40)
        base['age'] = age_base + random.randint(-5, 5)

        # 地方官专有字段
        if province:
            base['province'] = province
        if prefecture:
            base['prefecture'] = prefecture

        return base

    # ------------------------------------------------------------------
    # 内部方法 — 皇帝 & 派系
    # ------------------------------------------------------------------

    @classmethod
    def _create_monarch_agent(cls, game, archetype, pool, used_ids):
        """创建皇帝Agent + MonarchProfile（直接使用历史真名）"""
        monarch_ids = MONARCH_ARCHETYPE_MAP[archetype]
        person = None
        for mid in random.sample(monarch_ids, len(monarch_ids)):
            person = cls._pick_specific_character(pool, mid, used_ids)
            if person:
                break

        if not person:
            person = cls._pick_character(pool, ['君主'], used_ids)

        real_name = person['姓名'] if person else '天子'

        attrs = cls._build_agent_attributes(
            person, 'IMPERIAL', 1, None
        ) if person else copy.deepcopy(DEFAULT_OFFICIAL_ATTRIBUTES)

        emperor = Agent.objects.create(
            game=game,
            name=real_name,
            source_name='',  # 皇帝无需 source_name，name 就是真名
            role='EMPEROR',
            role_title='皇帝',
            tier='FULL',
            attributes=attrs,
        )

        archetype_attrs = copy.deepcopy(ARCHETYPE_ATTRIBUTES[archetype])
        MonarchProfile.objects.create(
            game=game,
            agent=emperor,
            archetype=archetype,
            attributes=archetype_attrs,
        )

        return emperor

    @classmethod
    def _create_factions(cls, game, archetype):
        """根据君主原型创建派系"""
        templates = FACTION_TEMPLATES.get(archetype, [])
        factions = []
        for tpl in templates:
            faction = Faction.objects.create(
                game=game,
                name=tpl['name'],
                ideology=copy.deepcopy(tpl['ideology']),
                imperial_favor=tpl['imperial_favor'],
            )
            factions.append(faction)
        return factions

    # ------------------------------------------------------------------
    # 内部方法 — 中央官员
    # ------------------------------------------------------------------

    @classmethod
    def _create_officials(cls, game, pool, factions, used_ids):
        """按 POSITION_SPECS 创建中央官员 Agent"""
        officials = []
        used_names = set()

        specs_without_emperor = [
            s for s in POSITION_SPECS if s[0] != 'EMPEROR'
        ]

        faction_names = [f.name for f in factions] if factions else []

        for role, role_title, org, rank, count, cat_pool in specs_without_emperor:
            for _ in range(count):
                person = cls._pick_character(pool, cat_pool, used_ids)
                if not person:
                    person = cls._pick_character(
                        pool, ['文臣', '文臣/武将'], used_ids
                    )
                if not person:
                    logger.warning("人物池不足，跳过 %s", role_title)
                    continue

                game_name = cls._anonymize_name(used_names, person['姓名'][:1])

                # 派系分配
                if role == 'CABINET_CHIEF' and faction_names:
                    faction_name = max(
                        factions, key=lambda f: f.imperial_favor
                    ).name
                elif faction_names:
                    faction_name = random.choice(faction_names)
                else:
                    faction_name = None

                attrs = cls._build_agent_attributes(
                    person, org, rank, faction_name
                )

                agent = Agent.objects.create(
                    game=game,
                    name=game_name,
                    source_name=person['姓名'],
                    role=role,
                    role_title=role_title,
                    tier='FULL',
                    attributes=attrs,
                )
                officials.append(agent)

        return officials

    # ------------------------------------------------------------------
    # 内部方法 — 地方官员（全国省/府）
    # ------------------------------------------------------------------

    @classmethod
    def _create_local_officials(cls, game, pool, factions, used_ids):
        """从 xingzhengquhua.json 为全国所有省和府创建地方官员

        每个省: 巡抚(1) + 布政使(1) + 按察使(1)
        每个府: 知府(1)
        跳过直隶州（用户明确说不需要州&县级别）
        """
        divisions = cls._load_admin_divisions()
        if not divisions:
            logger.warning("行政区划数据加载失败，跳过地方官员生成")
            return []

        officials = []
        used_names = set()
        faction_names = [f.name for f in factions] if factions else []

        # 收集所有待批量创建的 Agent 数据
        agents_to_create = []

        for prov_key, prov_data in divisions.items():
            if prov_key in EXCLUDED_PROVINCES:
                continue

            province_name = PROVINCE_DISPLAY_NAMES.get(prov_key, prov_key)
            fu_list = prov_data.get('府州', [])

            # ── 省级官员: 巡抚 + 布政使 + 按察使 ──
            province_specs = [
                ('PROVINCIAL_GOVERNOR', '巡抚', 2),
                ('PROVINCIAL_COMMISSIONER', '布政使', 5),
                ('PROVINCIAL_COMMISSIONER', '按察使', 5),
            ]

            for role, role_title, rank in province_specs:
                person = cls._pick_character(pool, ['文臣'], used_ids)
                if not person:
                    person = cls._pick_character(
                        pool, ['文臣', '文臣/武将'], used_ids
                    )
                if not person:
                    continue

                game_name = cls._anonymize_name(used_names, person['姓名'][:1])
                faction_name = random.choice(faction_names) if faction_names else None

                attrs = cls._build_agent_attributes(
                    person, 'PROVINCE', rank, faction_name,
                    province=province_name,
                )

                agents_to_create.append(Agent(
                    game=game,
                    name=game_name,
                    source_name=person['姓名'],
                    role=role,
                    role_title=f'{province_name}{role_title}',
                    tier='FULL',
                    attributes=attrs,
                ))

            # ── 府级官员: 知府 (仅 type=府 or 军民府) ──
            for fu in fu_list:
                fu_type = fu.get('type', '')
                if fu_type not in ('府', '军民府'):
                    continue  # 跳过直隶州

                fu_name = fu['name']

                person = cls._pick_character(pool, ['文臣'], used_ids)
                if not person:
                    person = cls._pick_character(
                        pool, ['文臣', '文臣/武将'], used_ids
                    )
                if not person:
                    continue

                game_name = cls._anonymize_name(used_names, person['姓名'][:1])
                faction_name = random.choice(faction_names) if faction_names else None

                attrs = cls._build_agent_attributes(
                    person, 'PREFECTURE', 4, faction_name,
                    province=province_name,
                    prefecture=fu_name,
                )

                agents_to_create.append(Agent(
                    game=game,
                    name=game_name,
                    source_name=person['姓名'],
                    role='PREFECT_PEER',
                    role_title=f'{fu_name}知府',
                    tier='FULL',
                    attributes=attrs,
                ))

        # 批量创建以提升性能
        if agents_to_create:
            officials = Agent.objects.bulk_create(agents_to_create)

        logger.info("地方官员生成完成: %d 个 Agent", len(officials))
        return list(officials)

    # ------------------------------------------------------------------
    # 内部方法 — 关联 & 层级
    # ------------------------------------------------------------------

    @classmethod
    def _pick_player_location(cls):
        """随机选择玩家所在的省和府"""
        divisions = cls._load_admin_divisions()
        if not divisions:
            return '某省', '某府'

        # 过滤掉排除的省
        candidates = {
            k: v for k, v in divisions.items()
            if k not in EXCLUDED_PROVINCES
        }
        if not candidates:
            return '某省', '某府'

        prov_key = random.choice(list(candidates.keys()))
        province_name = PROVINCE_DISPLAY_NAMES.get(prov_key, prov_key)

        # 从该省中随机选一个府
        fu_list = [
            f for f in candidates[prov_key].get('府州', [])
            if f.get('type') in ('府', '军民府')
        ]
        if fu_list:
            fu = random.choice(fu_list)
            fu_name = fu['name']
        else:
            fu_name = '某府'

        return province_name, fu_name

    @classmethod
    def _link_existing_prefect(cls, game, pool, factions, used_ids,
                               province=None, prefecture=None):
        """创建或补全玩家所在府的知府 Agent（role='PREFECT'）。

        知府以历史人物为原型，属性由 _build_agent_attributes 生成。
        若 Agent 已存在则补全省/府归属字段；否则新建。
        额外追加 AI 知府专用字段：evaluation_notes, player_affinity。
        """
        person = cls._pick_character(pool, ['文臣'], used_ids)
        if not person:
            person = cls._pick_character(pool, ['文臣', '文臣/武将'], used_ids)
        if not person:
            logger.warning("人物池不足，无法生成知府 Agent")
            return

        faction_name = random.choice([f.name for f in factions]) if factions else None
        attrs = cls._build_agent_attributes(
            person, 'PREFECTURE', 4, faction_name,
            province=province,
            prefecture=prefecture,
        )
        # AI 知府专用字段
        attrs['evaluation_notes'] = []
        attrs['player_affinity'] = 45 + random.randint(0, 15)  # 初始好感50±

        prefect = Agent.objects.filter(game=game, role='PREFECT').first()
        if prefect:
            # 已存在（旧存档兼容）：仅补全缺失字段
            prefect.source_name = person['姓名']
            existing = prefect.attributes
            for k, v in attrs.items():
                if k not in existing:
                    existing[k] = v
            if province:
                existing['province'] = province
            if prefecture:
                existing['prefecture'] = prefecture
            if 'evaluation_notes' not in existing:
                existing['evaluation_notes'] = []
            prefect.attributes = existing
            prefect.save(update_fields=['source_name', 'attributes'])
        else:
            # 新建知府 Agent
            used_names = set(
                Agent.objects.filter(game=game).values_list('name', flat=True)
            )
            game_name = cls._anonymize_name(used_names, person['姓名'][:1])
            Agent.objects.create(
                game=game,
                name=game_name,
                source_name=person['姓名'],
                role='PREFECT',
                role_title=f'{prefecture or "本府"}知府',
                tier='FULL',
                attributes=attrs,
            )
            logger.info("知府 Agent 创建完成: %s (原型: %s)", game_name, person['姓名'])

    @classmethod
    def _set_hierarchy(cls, game, officials):
        """设置上下级关系 (superior_agent_id)"""
        by_role = {}
        for agent in officials:
            by_role.setdefault(agent.role, []).append(agent)

        emperor = by_role.get('EMPEROR', [None])[0]
        cabinet_chief = by_role.get('CABINET_CHIEF', [None])[0]
        chief_censor = by_role.get('CHIEF_CENSOR', [None])[0]
        vice_censor = by_role.get('VICE_CENSOR', [None])[0]

        # 按省分组的巡抚
        governors_by_prov = {}
        for a in by_role.get('PROVINCIAL_GOVERNOR', []):
            prov = a.attributes.get('province', '')
            if prov:
                governors_by_prov[prov] = a

        agents_to_update = []

        for agent in officials:
            superior_id = None

            if agent.role == 'EMPEROR':
                continue
            elif agent.role == 'CABINET_CHIEF':
                superior_id = emperor.id if emperor else None
            elif agent.role == 'CABINET_MEMBER':
                superior_id = cabinet_chief.id if cabinet_chief else None
            elif agent.role in ('MINISTER', 'VICE_MINISTER'):
                superior_id = cabinet_chief.id if cabinet_chief else None
            elif agent.role == 'CHIEF_CENSOR':
                superior_id = emperor.id if emperor else None
            elif agent.role == 'VICE_CENSOR':
                superior_id = chief_censor.id if chief_censor else None
            elif agent.role == 'CENSOR':
                superior_id = vice_censor.id if vice_censor else (
                    chief_censor.id if chief_censor else None
                )
            elif agent.role == 'PROVINCIAL_GOVERNOR':
                superior_id = emperor.id if emperor else None
            elif agent.role == 'PROVINCIAL_COMMISSIONER':
                prov = agent.attributes.get('province', '')
                gov = governors_by_prov.get(prov)
                superior_id = gov.id if gov else (emperor.id if emperor else None)
            elif agent.role == 'PREFECT_PEER':
                prov = agent.attributes.get('province', '')
                gov = governors_by_prov.get(prov)
                superior_id = gov.id if gov else None

            if superior_id is not None:
                agent.attributes['superior_agent_id'] = superior_id
                agents_to_update.append(agent)

        # 批量更新
        if agents_to_update:
            Agent.objects.bulk_update(agents_to_update, ['attributes'], batch_size=100)

        # 知府(赵廷章) → 同省巡抚
        prefect = Agent.objects.filter(game=game, role='PREFECT').first()
        if prefect:
            prov = prefect.attributes.get('province', '')
            gov = governors_by_prov.get(prov)
            if gov:
                prefect.attributes['superior_agent_id'] = gov.id
                prefect.save(update_fields=['attributes'])

        # 侍郎 → 对应的尚书
        ministers = by_role.get('MINISTER', [])
        vice_ministers = by_role.get('VICE_MINISTER', [])
        org_to_minister = {}
        for m in ministers:
            org_to_minister[m.attributes.get('org')] = m

        vms_to_update = []
        for vm in vice_ministers:
            vm_org = vm.attributes.get('org')
            if vm_org in org_to_minister:
                vm.attributes['superior_agent_id'] = org_to_minister[vm_org].id
                vms_to_update.append(vm)

        if vms_to_update:
            Agent.objects.bulk_update(vms_to_update, ['attributes'], batch_size=100)

    @classmethod
    def _assign_faction_leaders(cls, game, factions, officials):
        """为每个派系指定领袖（从该派系最高级别的成员中选）"""
        for faction in factions:
            members = [
                a for a in officials
                if a.attributes.get('faction_name') == faction.name
            ]
            if not members:
                continue
            members.sort(key=lambda a: a.attributes.get('rank', 99))
            faction.leader = members[0]
            faction.save(update_fields=['leader'])

    @staticmethod
    def _assign_admin_location(game, province='某省', prefecture='某府'):
        """
        在 county_data 中记录行政归属信息，并补全本地 GENTRY/VILLAGER 的
        social_identity（因为 AgentService.initialize_agents 先于本步骤运行，
        彼时 admin_location 尚未设置，导致地主/村民的 native_place/clan_id 为空）。
        """
        from .agent import AgentService

        county_data = load_county_state(game)
        county_data['admin_location'] = {
            'province': province,
            'prefecture': prefecture,
        }
        save_player_state(game, county_data)

        # 补全本地 NPC 的 social_identity 并重建宗族
        local_roles = {'GENTRY', 'VILLAGER'}
        agents_qs = list(Agent.objects.filter(game=game))
        to_fix = []
        for agent in agents_qs:
            if agent.role not in local_roles:
                continue
            attrs = agent.attributes or {}
            si = attrs.get('social_identity') or {}
            # 只修复尚未设置 native_place 的（空字符串 或 '__local__'）
            if si.get('native_place') in ('', None, '__local__'):
                surname = (agent.name or '')[:1]
                si['native_place'] = prefecture
                si['clan_id'] = f"{prefecture}{surname}氏" if surname else ''
                attrs['social_identity'] = si
                agent.attributes = attrs
                to_fix.append(agent)
        if to_fix:
            for agent in to_fix:
                Agent.objects.filter(pk=agent.pk).update(attributes=agent.attributes)

        AgentService._initialize_clans(game, agents_qs, county_data)

    # ------------------------------------------------------------------
    # 查询接口（供 API 使用）
    # ------------------------------------------------------------------

    @classmethod
    def get_officialdom(cls, game):
        """获取完整官场层级数据，包含全国省/府"""
        try:
            monarch_profile = game.monarch
        except MonarchProfile.DoesNotExist:
            return None

        # 获取所有官场 Agent
        officialdom_roles = [
            'EMPEROR', 'CABINET_CHIEF', 'CABINET_MEMBER',
            'MINISTER', 'VICE_MINISTER',
            'CHIEF_CENSOR', 'VICE_CENSOR', 'CENSOR',
            'GOVERNOR_GENERAL', 'PROVINCIAL_GOVERNOR',
            'PROVINCIAL_COMMISSIONER', 'PREFECT', 'PREFECT_PEER',
        ]
        officials = Agent.objects.filter(
            game=game, role__in=officialdom_roles
        ).order_by('id')

        # 分组
        emperor = None
        cabinet = []
        ministries = {}
        censorate = []
        # 按省分组的地方官
        provinces = {}  # {省名: {'governor': agent, 'commissioners': [], 'prefects': []}}

        for agent in officials:
            if agent.role == 'EMPEROR':
                emperor = agent
            elif agent.role in ('CABINET_CHIEF', 'CABINET_MEMBER'):
                cabinet.append(agent)
            elif agent.role in ('MINISTER', 'VICE_MINISTER'):
                org = agent.attributes.get('org', '')
                ministries.setdefault(org, []).append(agent)
            elif agent.role in ('CHIEF_CENSOR', 'VICE_CENSOR', 'CENSOR'):
                censorate.append(agent)
            elif agent.role in ('PROVINCIAL_GOVERNOR', 'PROVINCIAL_COMMISSIONER',
                                'PREFECT', 'PREFECT_PEER'):
                prov = agent.attributes.get('province', '未知')
                if prov not in provinces:
                    provinces[prov] = {
                        'governor': None,
                        'commissioners': [],
                        'prefects': [],
                    }
                if agent.role == 'PROVINCIAL_GOVERNOR':
                    provinces[prov]['governor'] = agent
                elif agent.role == 'PROVINCIAL_COMMISSIONER':
                    provinces[prov]['commissioners'].append(agent)
                elif agent.role in ('PREFECT', 'PREFECT_PEER'):
                    provinces[prov]['prefects'].append(agent)

        # 排序
        cabinet.sort(key=lambda a: 0 if a.role == 'CABINET_CHIEF' else 1)

        censor_order = {'CHIEF_CENSOR': 0, 'VICE_CENSOR': 1, 'CENSOR': 2}
        censorate.sort(key=lambda a: censor_order.get(a.role, 99))

        # 六部显示名映射
        org_display = {
            'LIBU': '吏部', 'HUBU': '户部', 'LIBU2': '礼部',
            'BINGBU': '兵部', 'XINGBU': '刑部', 'GONGBU': '工部',
        }
        display_ministries = {}
        for org_code, agents in ministries.items():
            display_name = org_display.get(org_code, org_code)
            agents.sort(key=lambda a: a.attributes.get('rank', 99))
            display_ministries[display_name] = agents

        # 按省显示名排序（PROVINCE_DISPLAY_NAMES 的值顺序）
        prov_order = list(PROVINCE_DISPLAY_NAMES.values())
        sorted_provinces = {}
        for prov_name in prov_order:
            if prov_name in provinces:
                p = provinces[prov_name]
                # 知府按 prefecture 名排序
                p['prefects'].sort(
                    key=lambda a: a.attributes.get('prefecture', '')
                )
                sorted_provinces[prov_name] = p
        # 追加未在映射中的省
        for prov_name, p in provinces.items():
            if prov_name not in sorted_provinces:
                p['prefects'].sort(
                    key=lambda a: a.attributes.get('prefecture', '')
                )
                sorted_provinces[prov_name] = p

        # 获取派系
        factions = Faction.objects.filter(game=game).select_related('leader')

        # 玩家所在省
        player_province = load_county_state(game).get('admin_location', {}).get('province', '')

        return {
            'monarch_profile': monarch_profile,
            'emperor': emperor,
            'cabinet': cabinet,
            'ministries': display_ministries,
            'censorate': censorate,
            'provinces': sorted_provinces,
            'factions': factions,
            'player_province': player_province,
        }
