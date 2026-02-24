"""游戏数值常量"""

import random as _random

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

# 过度消费机制 (doc 06a §2.3)
EXCESS_CONSUMPTION_THRESHOLD = 15  # 每月人均余粮(斤)触发阈值

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
# 每种 governor_style 的属性基准值，创建时 ±0.15 随机扰动

GOVERNOR_STYLE_PROFILES = {
    "minben": {
        "intelligence": 6, "stamina": 5,
        "personality": {"sociability": 0.7, "rationality": 0.4, "assertiveness": 0.3},
        "ideology": {"state_vs_people": 0.2, "central_vs_local": 0.4, "pragmatic_vs_ideal": 0.3},
        "goals": {"welfare": 0.35, "reputation": 0.20, "power": 0.10, "wealth": 0.10, "legacy": 0.25},
    },
    "zhengji": {
        "intelligence": 8, "stamina": 7,
        "personality": {"sociability": 0.6, "rationality": 0.7, "assertiveness": 0.8},
        "ideology": {"state_vs_people": 0.6, "central_vs_local": 0.7, "pragmatic_vs_ideal": 0.8},
        "goals": {"welfare": 0.10, "reputation": 0.35, "power": 0.30, "wealth": 0.15, "legacy": 0.10},
    },
    "baoshou": {
        "intelligence": 6, "stamina": 4,
        "personality": {"sociability": 0.3, "rationality": 0.8, "assertiveness": 0.2},
        "ideology": {"state_vs_people": 0.5, "central_vs_local": 0.6, "pragmatic_vs_ideal": 0.6},
        "goals": {"welfare": 0.15, "reputation": 0.15, "power": 0.15, "wealth": 0.35, "legacy": 0.20},
    },
    "jinqu": {
        "intelligence": 7, "stamina": 8,
        "personality": {"sociability": 0.5, "rationality": 0.6, "assertiveness": 0.9},
        "ideology": {"state_vs_people": 0.4, "central_vs_local": 0.3, "pragmatic_vs_ideal": 0.7},
        "goals": {"welfare": 0.20, "reputation": 0.25, "power": 0.25, "wealth": 0.10, "legacy": 0.20},
    },
    "yuanhua": {
        "intelligence": 7, "stamina": 6,
        "personality": {"sociability": 0.8, "rationality": 0.5, "assertiveness": 0.5},
        "ideology": {"state_vs_people": 0.5, "central_vs_local": 0.5, "pragmatic_vs_ideal": 0.5},
        "goals": {"welfare": 0.20, "reputation": 0.20, "power": 0.20, "wealth": 0.20, "legacy": 0.20},
    },
}


def generate_governor_profile(style):
    """根据知县风格生成三层属性（基准值 + ±0.15 随机扰动），返回 dict"""
    base = GOVERNOR_STYLE_PROFILES.get(style)
    if not base:
        base = GOVERNOR_STYLE_PROFILES["yuanhua"]

    def _perturb(val, lo=0.0, hi=1.0):
        return round(max(lo, min(hi, val + _random.uniform(-0.15, 0.15))), 2)

    profile = {
        "intelligence": max(1, min(10, base["intelligence"] + _random.randint(-1, 1))),
        "stamina": max(1, min(10, base["stamina"] + _random.randint(-1, 1))),
        "personality": {k: _perturb(v) for k, v in base["personality"].items()},
        "ideology": {k: _perturb(v) for k, v in base["ideology"].items()},
    }

    # 目标权重：扰动后重新归一化
    raw_goals = {k: max(0.05, _perturb(v, 0.05, 0.60)) for k, v in base["goals"].items()}
    total = sum(raw_goals.values())
    profile["goals"] = {k: round(v / total, 2) for k, v in raw_goals.items()}

    profile["memory"] = []
    return profile
