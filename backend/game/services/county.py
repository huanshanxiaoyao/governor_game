"""县域初始化服务"""


class CountyService:
    """县域初始化"""

    @staticmethod
    def create_initial_county():
        """生成MVP中等难度县的初始 county_data JSONB"""
        county = {
            # 县域核心指标
            "morale": 50,           # 民心
            "security": 55,         # 治安
            "commercial": 35,       # 商业指数
            "education": 25,        # 文教指数
            "treasury": 400,        # 县库现金（两）
            "tax_rate": 0.12,       # 当前税率 (12% base)
            "irrigation_level": 0,  # 水利等级 (0/1/2)
            "has_granary": False,   # 义仓
            "bailiff_level": 0,     # 衙役等级 (0/1/2/3)
            "admin_cost": 80,       # 年度行政开支
            "medical_level": 0,     # 医疗等级 (0/1/2/3)

            # 村庄 (6个 MVP) — farmland ×4 for 200斤/亩 yield model
            "villages": [
                {"name": "李家村", "farmland": 6400,
                 "gentry_land_pct": 0.35, "morale": 50, "security": 55, "has_school": False},
                {"name": "张家村", "farmland": 5200,
                 "gentry_land_pct": 0.30, "morale": 52, "security": 58, "has_school": False},
                {"name": "王家村", "farmland": 7200,
                 "gentry_land_pct": 0.40, "morale": 48, "security": 50, "has_school": False},
                {"name": "陈家村", "farmland": 4400,
                 "gentry_land_pct": 0.25, "morale": 55, "security": 60, "has_school": False},
                {"name": "赵家村", "farmland": 5600,
                 "gentry_land_pct": 0.38, "morale": 45, "security": 52, "has_school": False},
                {"name": "刘家村", "farmland": 3200,
                 "gentry_land_pct": 0.20, "morale": 53, "security": 57, "has_school": False},
            ],

            # 集市 (2个 MVP)
            "markets": [
                {"name": "东关集", "merchants": 15, "trade_index": 35},
                {"name": "西街市", "merchants": 10, "trade_index": 30},
            ],

            # 全局环境 (doc 06 §2)
            "environment": {
                "agriculture_suitability": 0.7,  # 农业适宜度
                "flood_risk": 0.4,               # 水患风险度
                "border_threat": 0.2,            # 边患风险度
            },

            # 灾害状态
            "disaster_this_year": None,  # or {"type": "flood", "severity": 0.5, "relieved": False}

            # 投资追踪
            "active_investments": [],  # 进行中的项目
        }

        # Compute initial population as 60% of ceiling for each village
        from .settlement import SettlementService
        for v in county["villages"]:
            ceiling = SettlementService._calculate_village_ceiling(v, county)
            v["population"] = int(ceiling * 0.60)
            v["ceiling"] = ceiling

        return county
