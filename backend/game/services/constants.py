"""游戏数值常量"""

import random as _random

# ===== 指标档位映射 (0-100 → 8档描述) =====
# 使用半开区间 [lo, hi)，区间之间无空洞，覆盖 [0, 100]。
# 注意：上限用 101 是为了让 100 也能匹配"优秀"。
TIER_THRESHOLDS = [
    (0,  13,  "极差"),
    (13, 25,  "差"),
    (25, 38,  "稍差"),
    (38, 50,  "勉强"),
    (50, 63,  "及格"),
    (63, 75,  "稍好"),
    (75, 88,  "良好"),
    (88, 101, "优秀"),
]


def score_to_tier(score: float) -> str:
    """将 0–100 数值转换为 8 档状况描述 (民心/治安/商业/文教 通用)."""
    v = max(0.0, min(100.0, float(score)))
    for lo, hi, label in TIER_THRESHOLDS:
        if lo <= v < hi:
            return label
    return "优秀"


# ===== 时间系统 =====
MONTHS_PER_YEAR = 12
MAX_MONTH = 36  # 3年任期 = 36个月

MONTH_NAMES = [
    "正月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "冬月", "腊月",
]


def month_of_year(month):
    """月份 (1-36) → 当年第几月 (1-12)"""
    return (month - 1) % MONTHS_PER_YEAR + 1


def year_of(month):
    """月份 (1-36) → 第几年 (1-3)"""
    return (month - 1) // MONTHS_PER_YEAR + 1


def month_name(month):
    """月份 (1-36) → '第X年·正月' 格式"""
    y = year_of(month)
    m = month_of_year(month)
    return f"第{y}年·{MONTH_NAMES[m - 1]}"


MAX_YIELD_PER_MU = 200       # 斤/亩
ANNUAL_CONSUMPTION = 300     # 斤/人/年
GENTRY_HELPER_FEE_RATE = 0.05  # 地主帮佣费用（占地主粮食总收入，暂定写死）
BASE_GROWTH_RATE = 0.015     # 1.5% annual natural growth
GROWTH_RATE_CLAMP = 0.025    # ±2.5% max

# ===== 基础设施等级系统 (doc 06a §1.1b) =====
INFRA_MAX_LEVEL = 3

# 基建类型定义: base_cost(C), base_maint(M), base_months(级1), scale_type
INFRA_TYPES = {
    "school": {"base_cost": 80, "base_maint": 15, "base_months": 2, "scale": "pi"},
    "irrigation": {"base_cost": 20, "base_maint": 10, "base_months": 8, "scale": "farmland_pi"},
    "medical": {"base_cost": 12, "base_maint": 5, "base_months": 2, "scale": "pop_pi"},
}

# 各级工期月数 [级1, 级2, 级3]
INFRA_BUILD_MONTHS = {
    "school": [2, 3, 5],
    "irrigation": [8, 12, 18],
    "medical": [2, 3, 5],
}

# 水利灾害减损率 [level 0, 1, 2, 3]
IRRIGATION_DAMAGE_REDUCTION = [0, 0.15, 0.30, 0.60]

# 灾害人口损失减免系数（乘到人口损失上；值越小减免越强）
GRANARY_POP_LOSS_MULTIPLIER = 0.65
RELIEF_POP_LOSS_MULTIPLIER = 0.65

# 商税地方留存比例（独立于 remit_ratio）
COMMERCIAL_TAX_RETENTION = 0.60

# 消费信心灵敏度：cc=+50斤/月 → index=2.0，cc=-25斤/月 → index=0.5
CC_SENSITIVITY = 50

# 人口迁移（邻县竞争，doc 06a §3.1）
MIGRATION_SIGNIFICANT_DIFF = 15  # 显著领先/落后阈值（含边界）
MIGRATION_PARITY_DIFF = 10       # 持平阈值（严格小于）
MIGRATION_RATE_BY_DIM_COUNT = {
    1: 0.005,  # 1项显著领先/落后
    2: 0.015,  # 2项显著领先/落后
    3: 0.020,  # 3项显著领先/落后
    4: 0.025,  # 4项显著领先/落后
}
MIGRATION_FLOW_CAP_RATE = 0.05   # 单年人口迁移总量上限（占本县人口）
MIGRATION_COMPETITION_DIMS = ("morale", "security", "commercial", "education")


def calculate_infra_scale(infra_type, county):
    """计算基建缩放因子"""
    pi = county.get("price_index", 1.0)
    spec = INFRA_TYPES[infra_type]
    if spec["scale"] == "pi":
        return pi
    elif spec["scale"] == "farmland_pi":
        total_farmland = sum(v["farmland"] for v in county.get("villages", []))
        return (total_farmland / 10000) * pi
    elif spec["scale"] == "pop_pi":
        total_pop = sum(v["population"] for v in county.get("villages", []))
        return (total_pop / 1000) * pi
    return pi


def calculate_infra_cost(infra_type, target_level, county):
    """计算基建升级投资费用 = base_cost × scale × 2^(target_level-1)"""
    spec = INFRA_TYPES[infra_type]
    scale = calculate_infra_scale(infra_type, county)
    return round(spec["base_cost"] * scale * (2 ** (target_level - 1)))


def calculate_infra_maint(infra_type, level, county):
    """计算基建年度维护费用 = base_maint × scale × 2^(level-1)，level 0 时为 0"""
    if level <= 0:
        return 0
    spec = INFRA_TYPES[infra_type]
    scale = calculate_infra_scale(infra_type, county)
    return round(spec["base_maint"] * scale * (2 ** (level - 1)))


def calculate_infra_months(infra_type, target_level):
    """计算基建升级工期"""
    months = INFRA_BUILD_MONTHS.get(infra_type, [4, 6, 9])
    idx = max(0, min(target_level - 1, len(months) - 1))
    return months[idx]

# 徭役折银 (doc 06a §4.2)
CORVEE_PER_CAPITA = 0.3          # 两/人·年
GENTRY_POP_RATIO_COEFF = 0.12   # 地主人口比例 ≈ 占地比 × 此系数

# 粮食与银两换算 (doc 06a §4.2)
# 换算依据：设计亩产2两/亩（0.5两×4缩放）对应200斤/亩粮食产出 → 1两≈100斤
GRAIN_PER_LIANG = 100            # 斤/两
EMERGENCY_BUY_GRAIN_RATE = 70    # 斤/两（紧急采购溢价：每两只能买70斤，反映灾时粮价腾涨）

# 知府配额制 (doc 06a §4.4)
# 配额按"标准年"估算（base_yield=0.5两/亩，无农业适宜度，含水利加成）
QUOTA_BASE_COLLECTION_EFFICIENCY = 0.85  # 标准年征收效率（民心50基准时约0.85）

# 府级基础建设对下辖县影响系数
ROAD_COMMERCE_BONUS_PER_LEVEL = 0.10       # 跨县驿道：每级商业GMV相对加成10%
RIVER_DISASTER_REDUCTION_PER_LEVEL = 0.15  # 河道治理：每级洪灾/旱灾概率相对减少15%
PREF_GRANARY_POP_LOSS_MULT = 0.80          # 府级义仓：灾后人口损失额外减免20%

# 灾害减免申请参数 (doc 06a §4.7)
RELIEF_OVERREPORT_THRESHOLD = 1.5   # claimed > actual × 此倍数 → 进入高风险被查区间
RELIEF_DETECTION_BASE_PROB = 0.30   # 刚超过阈值时的基础被查概率（超报越多越高）
RELIEF_BASE_APPROVAL_PROB = 0.75    # 诚实申报（claimed ≤ actual）时的基础批准概率

# 县域类型定义 (doc 06a §1.3, §6.1)
# 每局创建时各数值在基准值上 ±20% 随机波动
# 地主占地比基准 = 历史数据 × 0.7
#
# 注意：farmland 使用 ×4 缩放约定（与 MAX_YIELD_PER_MU=200斤/亩 和
# base_yield=0.5两/亩 配套）。设计文档中"15000亩"在代码中为 60000。
# 经济产出公式已相应调整，最终结果与文档一致。
COUNTY_TYPES = {
    "fiscal_core": {
        "name": "财赋核心型",
        "description": "江南太湖平原，高额赋税定额与地主占地集中的冲突",
        "population": 8000,
        "farmland": 60000,       # 设计值15000亩 ×4
        "gentry_land_ratio": 0.63,
        "treasury": 600,
        "remit_ratio": 0.75,
        "morale": 40,
        "security": 60,
        "commercial": 55,
        "education": 40,
        "agriculture_suitability": 0.85,
        "flood_risk": 0.3,
        "border_threat": 0.1,
        "admin_cost": 200,
        "village_count": 6,
        "market_count": 3,
        "price_index": 1.4,      # 江南物价高
    },
    "clan_governance": {
        "name": "宗族治理型",
        "description": "皖南徽州、赣东南山区，宗族势力与官府权力的博弈",
        "population": 6000,
        "farmland": 52000,       # 设计值13000亩 ×4（补偿低适宜度+宗族占地对人口承载的影响）
        "gentry_land_ratio": 0.55,
        "treasury": 400,
        "remit_ratio": 0.65,
        "morale": 55,
        "security": 65,
        "commercial": 30,
        "education": 50,
        "agriculture_suitability": 0.65,
        "flood_risk": 0.2,
        "border_threat": 0.1,
        "admin_cost": 150,
        "village_count": 6,
        "market_count": 2,
        "price_index": 1.1,      # 基准物价
    },
    "coastal": {
        "name": "沿海治理型",
        "description": "闽浙粤沿海，海防安全与民生发展的平衡，财政紧张",
        "population": 3000,
        "farmland": 20000,       # 设计值5000亩 ×4
        "gentry_land_ratio": 0.36,
        "treasury": 150,
        "remit_ratio": 0.60,
        "morale": 45,
        "security": 35,
        "commercial": 40,
        "education": 20,
        "agriculture_suitability": 0.55,
        "flood_risk": 0.4,
        "border_threat": 0.5,
        "admin_cost": 110,
        "village_count": 4,
        "market_count": 2,
        "price_index": 0.9,      # 偏远物价低
    },
    "disaster_prone": {
        "name": "黄淮灾荒型",
        "description": "黄河淮河中下游，灾荒频发与固定赋税定额的冲突，流民问题突出",
        "population": 5000,
        "farmland": 48000,       # 设计值12000亩 ×4
        "gentry_land_ratio": 0.47,
        "treasury": 250,
        "remit_ratio": 0.65,
        "morale": 35,
        "security": 40,
        "commercial": 20,
        "education": 25,
        "agriculture_suitability": 0.70,
        "flood_risk": 0.7,
        "border_threat": 0.3,
        "admin_cost": 130,
        "village_count": 6,
        "market_count": 1,
        "price_index": 0.8,      # 中部稍低
    },
}

# 行政开支明细（各县域类型基础值）
ADMIN_COST_DETAIL = {
    "fiscal_core": {
        "official_salary": 50,   # 官员俸禄（知县+典史等）
        "deputy_salary": 25,     # 县丞俸禄
        "advisor_fee": 30,       # 师爷束脩
        "clerks_cost": 30,       # 六房书办
        "bailiff_cost": 16,      # 衙役饷银（基础4人 × 4两）
        "school_cost": 24,       # 县学经费
        "office_cost": 25,       # 衙署杂费
    },
    "clan_governance": {
        "official_salary": 40,
        "deputy_salary": 20,
        "advisor_fee": 25,
        "clerks_cost": 20,       # 宗族分担部分管理
        "bailiff_cost": 12,      # 基础4人 × 3两
        "school_cost": 18,
        "office_cost": 15,
    },
    "coastal": {
        "official_salary": 30,   # 偏远小县
        "deputy_salary": 15,
        "advisor_fee": 20,
        "clerks_cost": 15,       # 人少事少
        "bailiff_cost": 8,       # 基础4人 × 2两
        "school_cost": 10,       # 最简陋
        "office_cost": 12,
    },
    "disaster_prone": {
        "official_salary": 35,
        "deputy_salary": 18,
        "advisor_fee": 22,
        "clerks_cost": 18,
        "bailiff_cost": 10,      # 基础4人 × 2.5两
        "school_cost": 15,
        "office_cost": 12,
    },
}

# 行政开支项目中文标签
ADMIN_COST_LABELS = {
    "official_salary": "官员俸禄",
    "deputy_salary": "县丞俸禄",
    "advisor_fee": "师爷束脩",
    "clerks_cost": "六房书办",
    "bailiff_cost": "衙役饷银",
    "school_cost": "县学经费",
    "office_cost": "衙署杂费",
    "irrigation_maint": "水利维护",
    "medical_maint": "医疗维护",
}

# 各类型的村庄名称池
VILLAGE_NAMES = {
    "fiscal_core": [
        "沈家圩", "钱家浜", "陆家荡", "顾家桥", "周家泾",
        "徐家塘", "蒋家埭", "朱家角", "吴家湾", "孙家港",
    ],
    "clan_governance": [
        "程家坊", "汪家祠", "吴家岭", "胡家源", "方家坞",
        "罗家畈", "黄家堡", "曹家冲", "许家桥", "戴家墩",
    ],
    "coastal": [
        "林家澳", "陈家寨", "黄家埕", "郑家浦", "蔡家墩",
        "洪家岙", "叶家屿", "施家港", "杨家塘", "邱家礁",
    ],
    "disaster_prone": [
        "李家堤", "张家集", "王家铺", "赵家屯", "马家寨",
        "刘家庄", "孟家洼", "韩家岗", "曹家店", "宋家堡",
    ],
}

MARKET_NAMES = {
    "fiscal_core": ["东关集", "西街市", "南塘市"],
    "clan_governance": ["宗祠前市", "溪口集"],
    "coastal": ["港口集", "渔市街"],
    "disaster_prone": ["官道集"],
}

# ===== 邻县系统常量 =====

GOVERNOR_SURNAMES = ["王", "李", "张", "陈", "杨", "周", "吴", "郑", "赵", "孙"]
GOVERNOR_GIVEN_NAMES = [
    "维新", "文华", "志远", "怀德", "慎言",
    "敬之", "世安", "明远", "正道", "秉文",
]

NEIGHBOR_COUNTY_NAMES = {
    "fiscal_core": ["临安县", "松江县", "嘉定县", "昆山县", "常熟县"],
    "clan_governance": ["婺源县", "休宁县", "临川县", "南丰县", "祁门县"],
    "coastal": ["福清县", "同安县", "海宁县", "奉化县", "惠安县"],
    "disaster_prone": ["商丘县", "归德县", "凤阳县", "泗州县", "颍上县"],
}

# 玩家治理县的县名（与邻县名称池不重叠）
PLAYER_COUNTY_NAMES = {
    "fiscal_core": "华亭县",
    "clan_governance": "歙县",
    "coastal": "闽县",
    "disaster_prone": "睢阳县",
}

GOVERNOR_STYLES = {
    "minben": {
        "name": "民本型",
        "bio_template": "为人宽厚仁慈，深信为官一任造福一方。施政以百姓福祉为先，宁可官考平平也不愿苛待民众。",
        "instruction": (
            "你的施政理念是民本为先。优先保障民心和百姓生活，"
            "倾向降税减负、赈灾救济、兴办教育。即使财政吃紧也不愿加重百姓负担。"
        ),
    },
    "zhengji": {
        "name": "政绩型",
        "bio_template": "为人精明强干，一心追求仕途上进。施政以可量化的成绩为导向，重视商业、文教等显性政绩。",
        "instruction": (
            "你的施政理念是追求政绩。优先发展商业、文教等考核看重的指标，"
            "投资基建以展示施政成果。为了政绩可以适当加税。"
        ),
    },
    "baoshou": {
        "name": "保守型",
        "bio_template": "为人持重谨慎，信奉无为而治。施政以财政稳健为第一要务，不轻易冒险投资。",
        "instruction": (
            "你的施政理念是稳健守成。优先保持财政盈余，只在非常必要时才投资。"
            "宁可不作为也不愿冒险赔钱。县库低于200两时绝不投资。"
        ),
    },
    "jinqu": {
        "name": "进取型",
        "bio_template": "为人果决刚毅，信奉实干兴邦。施政风格大刀阔斧，敢于投资基建、改善民生。",
        "instruction": (
            "你的施政理念是积极进取。大胆投资水利、开垦等长期工程，"
            "愿意为发展适当加税。看到问题就想立刻解决。"
        ),
    },
    "yuanhua": {
        "name": "圆滑型",
        "bio_template": "为人圆融通达，善于审时度势。施政讲求平衡，各方面照顾周全，不走极端。",
        "instruction": (
            "你的施政理念是均衡发展。各项指标都不能太差，哪里短板补哪里。"
            "税率保持中庸，投资量力而行。"
        ),
    },
}

# ===== 知县三层属性体系 =====
# 每种施政类型（archetype）的属性基准均值，创建时叠加随机扰动。
# 执政风格（governor_style）不再作为属性生成的来源，
# 而是由已生成的属性通过 derive_governor_style() 动态推导。

ARCHETYPE_ATTRIBUTE_PROFILES = {
    "VIRTUOUS": {
        # 循吏型：重民本，温和，略偏理想主义，中等智识
        "intelligence": 7,
        "stamina": 6,
        "personality": {
            "sociability":   0.60,   # 略合群，善于联络民间
            "rationality":   0.55,   # 略理性，但不失仁心
            "assertiveness": 0.35,   # 温顺低调，不善强硬施压
        },
        "ideology": {
            "state_vs_people":    0.25,   # 重黎民福祉
            "central_vs_local":   0.40,   # 略重地方自主
            "pragmatic_vs_ideal": 0.40,   # 略偏理想主义驱动
        },
        "goals_base": {
            "welfare": 0.32, "reputation": 0.22,
            "power": 0.12, "wealth": 0.07, "legacy": 0.27,
        },
    },
    "MIDDLING": {
        # 中庸守成型：稳健务实，不走极端，以自保为先
        "intelligence": 6,
        "stamina": 5,
        "personality": {
            "sociability":   0.50,   # 中等，随机应变
            "rationality":   0.68,   # 偏理性谨慎
            "assertiveness": 0.25,   # 低调守成，不主动冒险（偏低以区别 zhengji）
        },
        "ideology": {
            "state_vs_people":    0.45,   # 略偏民本（不如 VIRTUOUS 强烈）
            "central_vs_local":   0.55,   # 略服从上级
            "pragmatic_vs_ideal": 0.65,   # 务实为先
        },
        "goals_base": {
            "welfare": 0.18, "reputation": 0.18,
            "power": 0.15, "wealth": 0.20, "legacy": 0.29,
        },
    },
    "CORRUPT": {
        # 贪酷恶劣型：强硬精于计算，个人利益优先，善于交际（为利）
        "intelligence": 7,
        "stamina": 7,
        "personality": {
            "sociability":   0.65,   # 善于交际，擅长拉拢关系
            "rationality":   0.70,   # 精于利益计算
            "assertiveness": 0.75,   # 强硬施压，为己牟利
        },
        "ideology": {
            "state_vs_people":    0.70,   # 重社稷/自身利益，轻百姓
            "central_vs_local":   0.60,   # 顺从上级以求庇护
            "pragmatic_vs_ideal": 0.80,   # 极端务实，唯利是图
        },
        "goals_base": {
            "welfare": 0.08, "reputation": 0.22,
            "power": 0.28, "wealth": 0.30, "legacy": 0.12,
        },
    },
}


# ===== 知县施政类型体系 =====

# 各县域类型对应的施政类型概率权重 [VIRTUOUS, MIDDLING, CORRUPT]
ARCHETYPE_COUNTY_TYPE_WEIGHTS = {
    'fiscal_core':     [0.25, 0.45, 0.30],
    'clan_governance': [0.35, 0.45, 0.20],
    'coastal':         [0.30, 0.45, 0.25],
    'disaster_prone':  [0.20, 0.40, 0.40],
}

# wealth 目标权重覆盖范围（min, max），按施政类型差异化
ARCHETYPE_WEALTH_GOAL = {
    'VIRTUOUS': (0.04, 0.10),
    'MIDDLING': (0.15, 0.25),
    'CORRUPT':  (0.38, 0.55),
}

# 旧版兼容：保留 ARCHETYPE_TO_STYLES 供存量代码引用，不再参与主流程
ARCHETYPE_TO_STYLES = {
    'VIRTUOUS': ['minben', 'jinqu'],
    'MIDDLING': ['baoshou', 'yuanhua'],
    'CORRUPT':  ['zhengji', 'yuanhua'],
}


def derive_governor_style(profile):
    """从三层属性动态推导执政风格，返回 5 种风格之一。

    推导逻辑：对每种风格按属性特征打分，取最高分。
      minben  — 低 state_vs_people，高 welfare 目标，低 assertiveness
      zhengji — 高 state_vs_people，高 assertiveness，高 power/reputation 目标
      baoshou — 高 rationality，低 assertiveness，中高 wealth 目标
      jinqu   — 高 assertiveness，低 pragmatic_vs_ideal（理想驱动），高 power 目标
      yuanhua — 高 sociability，目标分布均衡（低方差）
    """
    p = profile.get("personality", {})
    ideo = profile.get("ideology", {})
    goals = profile.get("goals", {})

    soc = p.get("sociability", 0.5)
    rat = p.get("rationality", 0.5)
    ass = p.get("assertiveness", 0.5)
    svp = ideo.get("state_vs_people", 0.5)
    pvl = ideo.get("pragmatic_vs_ideal", 0.5)

    welfare    = goals.get("welfare", 0.20)
    wealth     = goals.get("wealth", 0.20)
    power      = goals.get("power", 0.20)
    reputation = goals.get("reputation", 0.20)
    legacy     = goals.get("legacy", 0.20)

    scores = {
        "minben":  0.0,
        "zhengji": 0.0,
        "baoshou": 0.0,
        "jinqu":   0.0,
        "yuanhua": 0.0,
    }

    # minben：重民本，温顺，以民众福祉为目标
    scores["minben"] += (1.0 - svp) * 2.0       # 重黎民
    scores["minben"] += (1.0 - ass) * 1.0        # 温顺低调
    scores["minben"] += welfare * 3.0            # 福祉目标高
    scores["minben"] -= wealth * 2.0             # 财富目标低

    # zhengji：重仕途政绩，强硬务实，追求权力声望
    scores["zhengji"] += svp * 2.0              # 重社稷/仕途
    scores["zhengji"] += ass * 2.5              # 强硬果决（提高权重以拉开与 baoshou 的差距）
    scores["zhengji"] += (power + reputation) * 2.0
    scores["zhengji"] += pvl * 0.5              # 务实（降低权重，避免与 baoshou 重叠）

    # baoshou：理性保守，低调，守住财富；务实（pragmatic）的保守官员同样适配
    scores["baoshou"] += rat * 2.0              # 理性谨慎
    scores["baoshou"] += (1.0 - ass) * 1.5     # 低调守成
    scores["baoshou"] += wealth * 2.0           # 财富保留
    scores["baoshou"] += pvl * 0.8              # 务实守成（区别于理想主义的 jinqu）
    scores["baoshou"] -= welfare * 1.0          # 不在乎民生

    # jinqu：强硬进取，理想驱动，扩张权力
    scores["jinqu"] += ass * 2.5               # 强硬进取
    scores["jinqu"] += (1.0 - pvl) * 1.5      # 理想主义驱动
    scores["jinqu"] += power * 2.0             # 权力扩张
    scores["jinqu"] -= wealth * 1.0            # 非财富导向

    # yuanhua：合群圆滑，目标均衡（低方差）
    scores["yuanhua"] += soc * 2.0             # 合群圆滑
    goal_vals = [welfare, wealth, power, reputation, legacy]
    mean_g = sum(goal_vals) / len(goal_vals)
    variance = sum((v - mean_g) ** 2 for v in goal_vals) / len(goal_vals)
    # 方差越低（目标越均衡）得分越高；基准方差≈0.0 时满分
    scores["yuanhua"] += max(0.0, (0.02 - variance) * 15.0)

    return max(scores, key=scores.get)


def generate_governor_profile(archetype="MIDDLING", style=None):  # noqa: ARG001 (style已废弃)
    """根据施政类型（archetype）生成三层属性，返回 dict。

    archetype 决定属性均值分布；style 参数已废弃，保留仅供旧调用兼容。
    执政风格请在需要时调用 derive_governor_style(profile) 动态推导。
    """
    base = ARCHETYPE_ATTRIBUTE_PROFILES.get(archetype, ARCHETYPE_ATTRIBUTE_PROFILES["MIDDLING"])

    def _perturb(val, lo=0.0, hi=1.0):
        return round(max(lo, min(hi, val + _random.uniform(-0.18, 0.18))), 2)

    profile = {
        "intelligence": max(1, min(10, base["intelligence"] + _random.randint(-1, 2))),
        "stamina":      max(1, min(10, base["stamina"]      + _random.randint(-1, 2))),
        "personality": {k: _perturb(v) for k, v in base["personality"].items()},
        "ideology":    {k: _perturb(v) for k, v in base["ideology"].items()},
    }

    # 目标权重：扰动后重新归一化
    raw_goals = {k: max(0.04, _perturb(v, 0.04, 0.65)) for k, v in base["goals_base"].items()}
    total = sum(raw_goals.values())
    profile["goals"] = {k: round(v / total, 2) for k, v in raw_goals.items()}

    profile["memory"] = []

    # archetype wealth bias：将 goals.wealth 强制落入原型对应区间后重新归一化
    if archetype in ARCHETYPE_WEALTH_GOAL:
        w_min, w_max = ARCHETYPE_WEALTH_GOAL[archetype]
        target_wealth = round(_random.uniform(w_min, w_max), 2)
        goals = profile["goals"]
        old_wealth = goals.get("wealth", 0.15)
        delta = target_wealth - old_wealth
        other_keys = [k for k in goals if k != "wealth"]
        other_total = sum(goals[k] for k in other_keys)
        if other_total > 0:
            for k in other_keys:
                goals[k] = max(0.02, round(goals[k] - delta * (goals[k] / other_total), 2))
        goals["wealth"] = target_wealth
        total = sum(goals.values())
        profile["goals"] = {k: round(v / total, 2) for k, v in goals.items()}

    return profile
