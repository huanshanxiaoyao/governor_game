"""官场体系常量 — 君主原型、派系模板、官员配置、人物属性预映射"""

# ────────────────────────────────────────────────────────────────
# 1. 君主原型
# ────────────────────────────────────────────────────────────────

MONARCH_ARCHETYPE_MAP = {
    # archetype → 可选的历史君主 key_persons ID 列表
    'diligent':   ['0001', '0010'],                    # 朱元璋, 朱棣
    'delegating': ['0027', '0033'],                    # 嘉靖, 万历
    'moderate':   ['0013', '0014', '0024'],            # 仁宗, 宣宗, 弘治
}

ARCHETYPE_ATTRIBUTES = {
    'diligent': {
        'tax_pressure': 0.8,
        'corruption_risk': 0.9,
        'faction_intensity': 0.3,
        'player_freedom': 0.4,
        'meritocracy': 0.7,
        'governing_style': '高压、重农、反腐',
    },
    'delegating': {
        'tax_pressure': 0.5,
        'corruption_risk': 0.5,
        'faction_intensity': 0.9,
        'player_freedom': 0.7,
        'meritocracy': 0.3,
        'governing_style': '垂拱、放权、党争激烈',
    },
    'moderate': {
        'tax_pressure': 0.5,
        'corruption_risk': 0.5,
        'faction_intensity': 0.5,
        'player_freedom': 0.6,
        'meritocracy': 0.6,
        'governing_style': '宽仁、守成、平衡各方',
    },
}

# ────────────────────────────────────────────────────────────────
# 2. 派系模板（按君主原型）
# ────────────────────────────────────────────────────────────────

FACTION_TEMPLATES = {
    'diligent': [
        {
            'name': '改革派',
            'ideology': {
                'state_vs_people': 0.4,
                'central_vs_local': 0.7,
                'reform_vs_tradition': 0.8,
                'description': '主张整顿吏治、清丈土地、推行新法',
            },
            'imperial_favor': 70,
        },
        {
            'name': '保守派',
            'ideology': {
                'state_vs_people': 0.6,
                'central_vs_local': 0.5,
                'reform_vs_tradition': 0.2,
                'description': '崇尚祖制，反对激进变革，维护现有利益格局',
            },
            'imperial_favor': 40,
        },
    ],
    'delegating': [
        {
            'name': '阁臣派',
            'ideology': {
                'state_vs_people': 0.5,
                'central_vs_local': 0.8,
                'reform_vs_tradition': 0.5,
                'description': '依附内阁权臣，追求实权与晋升',
            },
            'imperial_favor': 60,
        },
        {
            'name': '言官派',
            'ideology': {
                'state_vs_people': 0.3,
                'central_vs_local': 0.5,
                'reform_vs_tradition': 0.6,
                'description': '以道德文章立身，敢于弹劾权贵',
            },
            'imperial_favor': 40,
        },
        {
            'name': '中间派',
            'ideology': {
                'state_vs_people': 0.5,
                'central_vs_local': 0.5,
                'reform_vs_tradition': 0.5,
                'description': '不偏不倚，见风使舵，明哲保身',
            },
            'imperial_favor': 50,
        },
    ],
    'moderate': [
        {
            'name': '务实派',
            'ideology': {
                'state_vs_people': 0.4,
                'central_vs_local': 0.6,
                'reform_vs_tradition': 0.6,
                'description': '注重实政，关注民生与财政',
            },
            'imperial_favor': 60,
        },
        {
            'name': '清流派',
            'ideology': {
                'state_vs_people': 0.3,
                'central_vs_local': 0.4,
                'reform_vs_tradition': 0.4,
                'description': '以学问品行自居，重风骨轻实务',
            },
            'imperial_favor': 50,
        },
    ],
}

# ────────────────────────────────────────────────────────────────
# 3. 官员姓名库（与邻县知县姓名库分开）
# ────────────────────────────────────────────────────────────────

OFFICIAL_SURNAMES = [
    "丁", "方", "沈", "陆", "钱", "顾", "韩", "魏",
    "贺", "卫", "范", "许", "闻", "梅", "裴", "庄",
    "傅", "尹", "孟", "曹", "薛", "秦", "邵", "柳",
    "霍", "纪", "蒋", "石", "童", "吕", "戴", "谭",
    "程", "叶", "彭", "段", "邓", "苗", "董", "毛",
]

OFFICIAL_GIVEN_NAMES = [
    "岩", "泽民", "承恩", "文博", "学思", "敬之", "怀远",
    "仲谋", "慕清", "世安", "伯衡", "景行", "子厚", "如璋",
    "廷玉", "士弘", "国维", "宗翰", "元亮", "正卿", "鸿渐",
    "思远", "明德", "崇礼", "维桢", "秉文", "嘉猷", "鼎臣",
    "济川", "安世", "怀德", "尚义", "若水", "载之", "立本",
    "允恭", "公望", "希贤", "克明", "从周", "存道", "敏行",
]

# ────────────────────────────────────────────────────────────────
# 4. 官职配置表 — 定义每局游戏需要创建的官员
# ────────────────────────────────────────────────────────────────
# (role, role_title, org, rank, count, category_pool)
# role: Agent.ROLE_CHOICES key
# role_title: 显示称谓
# org: 机构代码
# rank: 品级 (1=一品, 7=七品)
# count: 每局创建数量
# category_pool: 从 key_persons.json 的 类别 中匹配

POSITION_SPECS = [
    # ── 皇帝 ──
    ('EMPEROR',                '皇帝',       'IMPERIAL',    1, 1, ['君主']),
    # ── 内阁 (首辅1 + 成员3) ──
    ('CABINET_CHIEF',          '内阁首辅',    'CABINET',     1, 1, ['文臣']),
    ('CABINET_MEMBER',         '内阁成员',    'CABINET',     2, 3, ['文臣']),
    # ── 吏部 (尚书1 + 侍郎6) ──
    ('MINISTER',               '吏部尚书',    'LIBU',        2, 1, ['文臣']),
    ('VICE_MINISTER',          '吏部侍郎',    'LIBU',        3, 6, ['文臣']),
    # ── 户部 (尚书1 + 侍郎3) ──
    ('MINISTER',               '户部尚书',    'HUBU',        2, 1, ['文臣']),
    ('VICE_MINISTER',          '户部侍郎',    'HUBU',        3, 3, ['文臣']),
    # ── 礼部 (尚书1 + 侍郎3) ──
    ('MINISTER',               '礼部尚书',    'LIBU2',       2, 1, ['文臣']),
    ('VICE_MINISTER',          '礼部侍郎',    'LIBU2',       3, 3, ['文臣']),
    # ── 兵部 (尚书1 + 侍郎3) ──
    ('MINISTER',               '兵部尚书',    'BINGBU',      2, 1, ['武将', '文臣/武将']),
    ('VICE_MINISTER',          '兵部侍郎',    'BINGBU',      3, 3, ['武将', '文臣/武将', '文臣']),
    # ── 刑部 (尚书1 + 侍郎3) ──
    ('MINISTER',               '刑部尚书',    'XINGBU',      2, 1, ['文臣']),
    ('VICE_MINISTER',          '刑部侍郎',    'XINGBU',      3, 3, ['文臣']),
    # ── 工部 (尚书1 + 侍郎3) ──
    ('MINISTER',               '工部尚书',    'GONGBU',      2, 1, ['文臣']),
    ('VICE_MINISTER',          '工部侍郎',    'GONGBU',      3, 3, ['文臣']),
    # ── 都察院 (左都御史1×二品 + 左副都御史1×五品 + 监察御史6×七品) ──
    ('CHIEF_CENSOR',           '左都御史',    'DUCHAYUAN',   2, 1, ['文臣']),
    ('VICE_CENSOR',            '左副都御史',   'DUCHAYUAN',   5, 1, ['文臣']),
    ('CENSOR',                 '监察御史',    'DUCHAYUAN',   7, 6, ['文臣']),
    # ── 地方官员由 xingzhengquhua.json 动态生成，不在此静态列表中 ──
]
# 合计: 1皇帝 + 4内阁 + 6×6部(尚书+侍郎) = 30 + 8都察院 = 39 中央 Agent
# 地方官员(巡抚/布政使/按察使/知府)从行政区划数据动态生成，约200个
# 现有 PREFECT（赵廷章）保留，仅追加 source_name + 官场属性

# ────────────────────────────────────────────────────────────────
# 4b. 行政区划相关常量
# ────────────────────────────────────────────────────────────────

# 排除的布政使司（交趾仅短暂存在1407-1428）
EXCLUDED_PROVINCES = {'交趾布政使司'}

# 省名标准化映射 — 将 JSON key 统一为显示名
PROVINCE_DISPLAY_NAMES = {
    '北直隶': '北直隶',
    '南直隶': '南直隶',
    '山东布政使司': '山东',
    '山西布政使司': '山西',
    '河南布政使司': '河南',
    '陕西布政使司': '陕西',
    '四川布政使司': '四川',
    '湖广布政使司': '湖广',
    '浙江布政使司': '浙江',
    '江西布政使司': '江西',
    '福建布政使司': '福建',
    '广东布政使司': '广东',
    '广西布政使司': '广西',
    '云南布政使司': '云南',
    '贵州布政使司': '贵州',
}

# ────────────────────────────────────────────────────────────────
# 5. 考核倾向映射 — 官员评价知县时的偏好
# ────────────────────────────────────────────────────────────────

ASSESSMENT_TENDENCIES = {
    'CABINET':    'balance',      # 首辅看全局平衡
    'LIBU':       'competence',   # 吏部看政绩
    'HUBU':       'fiscal',       # 户部看税赋
    'LIBU2':      'education',    # 礼部看文教
    'BINGBU':     'security',     # 兵部看治安
    'XINGBU':     'justice',      # 刑部看司法
    'GONGBU':     'infrastructure',  # 工部看基建
    'DUCHAYUAN':  'integrity',    # 都察院看操守
    'PREFECTURE': 'balance',      # 府/州看综合
    'PROVINCE':   'balance',      # 省级看综合
}

# ────────────────────────────────────────────────────────────────
# 6. 历史人物属性预映射 — 约40个关键人物的数值化属性
# ────────────────────────────────────────────────────────────────

CHARACTER_ATTRIBUTE_MAP = {
    # ── 君主 ──
    '0001': {  # 朱元璋
        'intelligence': 9, 'charisma': 8, 'loyalty': 10,
        'personality': {'openness': 0.3, 'conscientiousness': 0.95, 'agreeableness': 0.15},
        'ideology': {'reform_vs_tradition': 0.7, 'people_vs_authority': 0.2, 'pragmatic_vs_idealist': 0.9},
        'reputation': {'scholarly': 40, 'political': 95, 'popular': 50},
        'goals': ['巩固皇权', '严惩贪腐', '恢复生产'],
    },
    '0010': {  # 朱棣
        'intelligence': 9, 'charisma': 9, 'loyalty': 10,
        'personality': {'openness': 0.7, 'conscientiousness': 0.9, 'agreeableness': 0.2},
        'ideology': {'reform_vs_tradition': 0.7, 'people_vs_authority': 0.3, 'pragmatic_vs_idealist': 0.8},
        'reputation': {'scholarly': 60, 'political': 90, 'popular': 60},
        'goals': ['威德远播', '巩固边防', '文治武功'],
    },
    '0013': {  # 朱高炽（仁宗）
        'intelligence': 7, 'charisma': 7, 'loyalty': 10,
        'personality': {'openness': 0.6, 'conscientiousness': 0.7, 'agreeableness': 0.8},
        'ideology': {'reform_vs_tradition': 0.4, 'people_vs_authority': 0.6, 'pragmatic_vs_idealist': 0.6},
        'reputation': {'scholarly': 70, 'political': 65, 'popular': 80},
        'goals': ['休养生息', '宽仁治国'],
    },
    '0014': {  # 朱瞻基（宣宗）
        'intelligence': 8, 'charisma': 8, 'loyalty': 10,
        'personality': {'openness': 0.6, 'conscientiousness': 0.8, 'agreeableness': 0.6},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.7},
        'reputation': {'scholarly': 75, 'political': 80, 'popular': 70},
        'goals': ['守成治国', '安定四方'],
    },
    '0024': {  # 朱祐樘（弘治）
        'intelligence': 7, 'charisma': 7, 'loyalty': 10,
        'personality': {'openness': 0.5, 'conscientiousness': 0.8, 'agreeableness': 0.8},
        'ideology': {'reform_vs_tradition': 0.4, 'people_vs_authority': 0.6, 'pragmatic_vs_idealist': 0.5},
        'reputation': {'scholarly': 65, 'political': 70, 'popular': 85},
        'goals': ['清明政治', '与民休息'],
    },
    '0027': {  # 朱厚熜（嘉靖）
        'intelligence': 9, 'charisma': 6, 'loyalty': 10,
        'personality': {'openness': 0.3, 'conscientiousness': 0.4, 'agreeableness': 0.2},
        'ideology': {'reform_vs_tradition': 0.3, 'people_vs_authority': 0.2, 'pragmatic_vs_idealist': 0.4},
        'reputation': {'scholarly': 70, 'political': 85, 'popular': 20},
        'goals': ['修玄求道', '驾驭群臣', '维护皇权'],
    },
    '0033': {  # 朱翊钧（万历）
        'intelligence': 7, 'charisma': 5, 'loyalty': 10,
        'personality': {'openness': 0.3, 'conscientiousness': 0.2, 'agreeableness': 0.3},
        'ideology': {'reform_vs_tradition': 0.3, 'people_vs_authority': 0.2, 'pragmatic_vs_idealist': 0.3},
        'reputation': {'scholarly': 50, 'political': 60, 'popular': 15},
        'goals': ['安享太平', '不理朝政'],
    },

    # ── 文臣 ──
    '0004': {  # 李善长
        'intelligence': 8, 'charisma': 7, 'loyalty': 5,
        'personality': {'openness': 0.4, 'conscientiousness': 0.7, 'agreeableness': 0.3},
        'ideology': {'reform_vs_tradition': 0.4, 'people_vs_authority': 0.3, 'pragmatic_vs_idealist': 0.8},
        'reputation': {'scholarly': 60, 'political': 90, 'popular': 30},
        'goals': ['维护淮西集团', '巩固权势'],
    },
    '0005': {  # 刘基
        'intelligence': 9, 'charisma': 7, 'loyalty': 8,
        'personality': {'openness': 0.7, 'conscientiousness': 0.9, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.6, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.5},
        'reputation': {'scholarly': 95, 'political': 70, 'popular': 60},
        'goals': ['辅佐明君', '宽仁治国', '善终'],
    },
    '0011': {  # 姚广孝
        'intelligence': 9, 'charisma': 6, 'loyalty': 7,
        'personality': {'openness': 0.8, 'conscientiousness': 0.7, 'agreeableness': 0.3},
        'ideology': {'reform_vs_tradition': 0.7, 'people_vs_authority': 0.4, 'pragmatic_vs_idealist': 0.4},
        'reputation': {'scholarly': 80, 'political': 85, 'popular': 20},
        'goals': ['辅佐成大业', '建功立业'],
    },
    '0015': {  # 杨士奇
        'intelligence': 8, 'charisma': 7, 'loyalty': 8,
        'personality': {'openness': 0.5, 'conscientiousness': 0.8, 'agreeableness': 0.7},
        'ideology': {'reform_vs_tradition': 0.4, 'people_vs_authority': 0.6, 'pragmatic_vs_idealist': 0.6},
        'reputation': {'scholarly': 85, 'political': 80, 'popular': 65},
        'goals': ['辅佐幼主', '安定朝纲'],
    },
    '0016': {  # 杨荣
        'intelligence': 8, 'charisma': 7, 'loyalty': 7,
        'personality': {'openness': 0.6, 'conscientiousness': 0.8, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.7},
        'reputation': {'scholarly': 80, 'political': 75, 'popular': 50},
        'goals': ['维护内阁运转', '平衡各方'],
    },
    '0017': {  # 杨溥
        'intelligence': 7, 'charisma': 6, 'loyalty': 9,
        'personality': {'openness': 0.4, 'conscientiousness': 0.9, 'agreeableness': 0.7},
        'ideology': {'reform_vs_tradition': 0.3, 'people_vs_authority': 0.6, 'pragmatic_vs_idealist': 0.5},
        'reputation': {'scholarly': 80, 'political': 65, 'popular': 60},
        'goals': ['恪尽职守', '匡正朝纲'],
    },
    '0020': {  # 于谦
        'intelligence': 8, 'charisma': 8, 'loyalty': 10,
        'personality': {'openness': 0.6, 'conscientiousness': 0.95, 'agreeableness': 0.4},
        'ideology': {'reform_vs_tradition': 0.6, 'people_vs_authority': 0.7, 'pragmatic_vs_idealist': 0.4},
        'reputation': {'scholarly': 75, 'political': 85, 'popular': 90},
        'goals': ['守卫社稷', '清廉从政'],
    },
    '0026': {  # 王守仁
        'intelligence': 10, 'charisma': 9, 'loyalty': 8,
        'personality': {'openness': 0.9, 'conscientiousness': 0.8, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.7, 'people_vs_authority': 0.7, 'pragmatic_vs_idealist': 0.4},
        'reputation': {'scholarly': 95, 'political': 80, 'popular': 75},
        'goals': ['知行合一', '平定叛乱', '传播心学'],
    },
    '0028': {  # 严嵩
        'intelligence': 7, 'charisma': 7, 'loyalty': 3,
        'personality': {'openness': 0.3, 'conscientiousness': 0.5, 'agreeableness': 0.6},
        'ideology': {'reform_vs_tradition': 0.3, 'people_vs_authority': 0.2, 'pragmatic_vs_idealist': 0.8},
        'reputation': {'scholarly': 65, 'political': 85, 'popular': 10},
        'goals': ['揽权固位', '排除异己'],
    },
    '0029': {  # 徐阶
        'intelligence': 9, 'charisma': 8, 'loyalty': 6,
        'personality': {'openness': 0.5, 'conscientiousness': 0.8, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.6, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.7},
        'reputation': {'scholarly': 80, 'political': 90, 'popular': 50},
        'goals': ['扳倒严嵩', '恢复清明政治'],
    },
    '0030': {  # 海瑞
        'intelligence': 7, 'charisma': 6, 'loyalty': 10,
        'personality': {'openness': 0.3, 'conscientiousness': 0.99, 'agreeableness': 0.1},
        'ideology': {'reform_vs_tradition': 0.4, 'people_vs_authority': 0.9, 'pragmatic_vs_idealist': 0.2},
        'reputation': {'scholarly': 60, 'political': 40, 'popular': 95},
        'goals': ['为民请命', '清廉到底'],
    },
    '0031': {  # 张居正
        'intelligence': 10, 'charisma': 9, 'loyalty': 7,
        'personality': {'openness': 0.6, 'conscientiousness': 0.95, 'agreeableness': 0.3},
        'ideology': {'reform_vs_tradition': 0.9, 'people_vs_authority': 0.4, 'pragmatic_vs_idealist': 0.9},
        'reputation': {'scholarly': 85, 'political': 95, 'popular': 45},
        'goals': ['推行一条鞭法', '考成法整顿吏治', '中兴大明'],
    },
    '0080': {  # 方孝孺
        'intelligence': 8, 'charisma': 6, 'loyalty': 10,
        'personality': {'openness': 0.3, 'conscientiousness': 0.9, 'agreeableness': 0.3},
        'ideology': {'reform_vs_tradition': 0.2, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.1},
        'reputation': {'scholarly': 90, 'political': 40, 'popular': 60},
        'goals': ['维护正统', '殉节守义'],
    },
    '0086': {  # 夏原吉
        'intelligence': 8, 'charisma': 6, 'loyalty': 8,
        'personality': {'openness': 0.4, 'conscientiousness': 0.9, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.4, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.8},
        'reputation': {'scholarly': 70, 'political': 75, 'popular': 55},
        'goals': ['充实国库', '精打细算'],
    },
    '0090': {  # 李贤
        'intelligence': 8, 'charisma': 7, 'loyalty': 7,
        'personality': {'openness': 0.5, 'conscientiousness': 0.8, 'agreeableness': 0.6},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.7},
        'reputation': {'scholarly': 75, 'political': 80, 'popular': 55},
        'goals': ['匡扶社稷', '安定朝局'],
    },
    '0092': {  # 王恕
        'intelligence': 7, 'charisma': 6, 'loyalty': 9,
        'personality': {'openness': 0.4, 'conscientiousness': 0.9, 'agreeableness': 0.4},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.7, 'pragmatic_vs_idealist': 0.5},
        'reputation': {'scholarly': 75, 'political': 65, 'popular': 70},
        'goals': ['直言进谏', '整顿吏治'],
    },
    '0095': {  # 杨一清
        'intelligence': 8, 'charisma': 7, 'loyalty': 7,
        'personality': {'openness': 0.6, 'conscientiousness': 0.8, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.6, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.7},
        'reputation': {'scholarly': 75, 'political': 80, 'popular': 50},
        'goals': ['安定边防', '整顿朝纲'],
    },
    '0096': {  # 杨廷和
        'intelligence': 8, 'charisma': 7, 'loyalty': 7,
        'personality': {'openness': 0.4, 'conscientiousness': 0.8, 'agreeableness': 0.4},
        'ideology': {'reform_vs_tradition': 0.4, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.6},
        'reputation': {'scholarly': 80, 'political': 80, 'popular': 50},
        'goals': ['辅佐新君', '维护朝纲正统'],
    },
    '0098': {  # 严世蕃
        'intelligence': 8, 'charisma': 5, 'loyalty': 2,
        'personality': {'openness': 0.4, 'conscientiousness': 0.3, 'agreeableness': 0.2},
        'ideology': {'reform_vs_tradition': 0.3, 'people_vs_authority': 0.1, 'pragmatic_vs_idealist': 0.9},
        'reputation': {'scholarly': 40, 'political': 70, 'popular': 5},
        'goals': ['揽权敛财', '维护严党'],
    },
    '0099': {  # 胡宗宪
        'intelligence': 8, 'charisma': 7, 'loyalty': 6,
        'personality': {'openness': 0.5, 'conscientiousness': 0.7, 'agreeableness': 0.4},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.4, 'pragmatic_vs_idealist': 0.8},
        'reputation': {'scholarly': 60, 'political': 75, 'popular': 50},
        'goals': ['平定倭寇', '保全自身'],
    },
    '0108': {  # 章溢
        'intelligence': 7, 'charisma': 6, 'loyalty': 8,
        'personality': {'openness': 0.5, 'conscientiousness': 0.8, 'agreeableness': 0.6},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.6, 'pragmatic_vs_idealist': 0.5},
        'reputation': {'scholarly': 70, 'political': 55, 'popular': 55},
        'goals': ['辅佐开国', '造福一方'],
    },
    '0112': {  # 金幼孜
        'intelligence': 7, 'charisma': 6, 'loyalty': 7,
        'personality': {'openness': 0.5, 'conscientiousness': 0.7, 'agreeableness': 0.6},
        'ideology': {'reform_vs_tradition': 0.4, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.6},
        'reputation': {'scholarly': 75, 'political': 60, 'popular': 45},
        'goals': ['辅佐内阁', '编修文献'],
    },
    '0116': {  # 商辂
        'intelligence': 8, 'charisma': 7, 'loyalty': 7,
        'personality': {'openness': 0.5, 'conscientiousness': 0.8, 'agreeableness': 0.6},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.6, 'pragmatic_vs_idealist': 0.6},
        'reputation': {'scholarly': 85, 'political': 70, 'popular': 60},
        'goals': ['匡正朝纲', '安定社稷'],
    },
    '0122': {  # 赵文华
        'intelligence': 6, 'charisma': 6, 'loyalty': 3,
        'personality': {'openness': 0.3, 'conscientiousness': 0.3, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.3, 'people_vs_authority': 0.2, 'pragmatic_vs_idealist': 0.8},
        'reputation': {'scholarly': 30, 'political': 60, 'popular': 10},
        'goals': ['依附权贵', '升官发财'],
    },
    '0124': {  # 潘季驯
        'intelligence': 8, 'charisma': 6, 'loyalty': 7,
        'personality': {'openness': 0.5, 'conscientiousness': 0.9, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.6, 'pragmatic_vs_idealist': 0.8},
        'reputation': {'scholarly': 70, 'political': 65, 'popular': 60},
        'goals': ['治理黄河', '造福百姓'],
    },
    '0125': {  # 沈一贯
        'intelligence': 7, 'charisma': 6, 'loyalty': 5,
        'personality': {'openness': 0.4, 'conscientiousness': 0.6, 'agreeableness': 0.4},
        'ideology': {'reform_vs_tradition': 0.4, 'people_vs_authority': 0.4, 'pragmatic_vs_idealist': 0.6},
        'reputation': {'scholarly': 70, 'political': 65, 'popular': 35},
        'goals': ['稳坐首辅', '周旋各方'],
    },
    '0126': {  # 李三才
        'intelligence': 7, 'charisma': 7, 'loyalty': 6,
        'personality': {'openness': 0.6, 'conscientiousness': 0.6, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.6, 'people_vs_authority': 0.6, 'pragmatic_vs_idealist': 0.6},
        'reputation': {'scholarly': 65, 'political': 70, 'popular': 55},
        'goals': ['整顿漕运', '扩大影响'],
    },
    '0128': {  # 左光斗
        'intelligence': 7, 'charisma': 7, 'loyalty': 9,
        'personality': {'openness': 0.5, 'conscientiousness': 0.9, 'agreeableness': 0.3},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.7, 'pragmatic_vs_idealist': 0.3},
        'reputation': {'scholarly': 75, 'political': 60, 'popular': 70},
        'goals': ['弹劾奸党', '守节殉道'],
    },

    # ── 武将/文臣武将 ──
    '0032': {  # 戚继光
        'intelligence': 9, 'charisma': 8, 'loyalty': 8,
        'personality': {'openness': 0.6, 'conscientiousness': 0.9, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.6, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.8},
        'reputation': {'scholarly': 60, 'political': 65, 'popular': 80},
        'goals': ['荡平倭寇', '练兵强军'],
    },
    '0035': {  # 袁崇焕
        'intelligence': 8, 'charisma': 8, 'loyalty': 9,
        'personality': {'openness': 0.6, 'conscientiousness': 0.8, 'agreeableness': 0.3},
        'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.6},
        'reputation': {'scholarly': 55, 'political': 60, 'popular': 70},
        'goals': ['守卫辽东', '五年平辽'],
    },
    '0109': {  # 铁铉
        'intelligence': 7, 'charisma': 7, 'loyalty': 10,
        'personality': {'openness': 0.4, 'conscientiousness': 0.9, 'agreeableness': 0.4},
        'ideology': {'reform_vs_tradition': 0.3, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.4},
        'reputation': {'scholarly': 65, 'political': 50, 'popular': 75},
        'goals': ['效忠建文帝', '守城抗敌'],
    },
    '0121': {  # 唐顺之
        'intelligence': 8, 'charisma': 6, 'loyalty': 7,
        'personality': {'openness': 0.7, 'conscientiousness': 0.8, 'agreeableness': 0.5},
        'ideology': {'reform_vs_tradition': 0.6, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.6},
        'reputation': {'scholarly': 85, 'political': 55, 'popular': 50},
        'goals': ['文武兼备', '抗倭救民'],
    },
}

# 默认属性 — 用于未在 CHARACTER_ATTRIBUTE_MAP 中预映射的人物
DEFAULT_OFFICIAL_ATTRIBUTES = {
    'intelligence': 7, 'charisma': 6, 'loyalty': 6,
    'personality': {'openness': 0.5, 'conscientiousness': 0.7, 'agreeableness': 0.5},
    'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.5, 'pragmatic_vs_idealist': 0.5},
    'reputation': {'scholarly': 60, 'political': 60, 'popular': 40},
    'goals': ['恪尽职守', '安稳度日'],
}
