"""游戏数值常量"""

import random as _random

MAX_YIELD_PER_MU = 200       # 斤/亩
ANNUAL_CONSUMPTION = 300     # 斤/人/年
BASE_GROWTH_RATE = 0.015     # 1.5% annual natural growth
GROWTH_RATE_CLAMP = 0.025    # ±2.5% max
MEDICAL_COST_PER_THOUSAND = {0: 0, 1: 5, 2: 12, 3: 22}
MEDICAL_NAMES = {0: "无", 1: "简易医馆", 2: "县医署", 3: "完善医疗"}


def calculate_medical_cost(level, population, price_index):
    """计算年度医疗开支 = 每千人费用 × (人口/1000) × 物价指数"""
    per_thousand = MEDICAL_COST_PER_THOUSAND.get(level, 0)
    return round(per_thousand * (population / 1000) * price_index)

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
        "admin_cost": 100,
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
