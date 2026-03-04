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
    POSITION_SPECS,
    PROVINCE_DISPLAY_NAMES,
)

logger = logging.getLogger('game')

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
    def _anonymize_name(used_names):
        """生成随机的游戏内名字"""
        for _ in range(500):  # 防止无限循环
            name = random.choice(OFFICIAL_SURNAMES) + random.choice(OFFICIAL_GIVEN_NAMES)
            if name not in used_names:
                used_names.add(name)
                return name
        # fallback
        return random.choice(OFFICIAL_SURNAMES) + str(random.randint(1, 999))

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

                game_name = cls._anonymize_name(used_names)

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

                game_name = cls._anonymize_name(used_names)
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

                game_name = cls._anonymize_name(used_names)
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
        """为现有知府(赵廷章)追加历史原型和官场属性"""
        prefect = Agent.objects.filter(game=game, role='PREFECT').first()
        if not prefect:
            return

        person = cls._pick_character(pool, ['文臣'], used_ids)
        if not person:
            return

        prefect.source_name = person['姓名']

        attrs = prefect.attributes
        attrs['hometown'] = person.get('籍贯', '不详')
        attrs['org'] = 'PREFECTURE'
        attrs['rank'] = 4
        attrs['source_person_id'] = person['ID']
        attrs['deeds'] = person.get('主要事迹', '')
        attrs['political_views'] = person.get('政治观点', '')
        attrs['assessment_tendency'] = 'balance'
        if province:
            attrs['province'] = province
        if prefecture:
            attrs['prefecture'] = prefecture

        if factions:
            attrs['faction_name'] = random.choice(
                [f.name for f in factions]
            )

        prefect.attributes = attrs
        prefect.save(update_fields=['source_name', 'attributes'])

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
        """在 county_data 中记录行政归属信息"""
        county_data = game.county_data
        county_data['admin_location'] = {
            'province': province,
            'prefecture': prefecture,
        }
        game.county_data = county_data
        game.save(update_fields=['county_data'])

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
        player_province = game.county_data.get('admin_location', {}).get('province', '')

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
