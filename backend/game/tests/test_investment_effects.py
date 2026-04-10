from django.test import SimpleTestCase

from game.services.investment import InvestmentService
from game.services.settlement import SettlementService


class InvestmentEffectsTests(SimpleTestCase):
    def test_hire_bailiffs_boosts_county_and_village_security(self):
        """衙役募集为两阶段：下令当月扣钱进入 active_investments，
        次月结算时由 SeasonalMixin 完成效果（等级+1、治安提升、行政开支增加）。"""
        county = {
            "treasury": 500,
            "price_index": 1.0,
            "bailiff_level": 0,
            "security": 60,
            "admin_cost": 100,
            "admin_cost_detail": {"bailiff_cost": 0},
            "active_investments": [],
            "villages": [
                {"name": "甲村", "security": 60},
                {"name": "乙村", "security": 98},
                {"name": "丙村", "security": 50},
            ],
        }

        # ── 阶段1：下令 —— 仅扣钱、入 active_investments ──
        actual_cost, msg = InvestmentService.apply_effects(county, "hire_bailiffs", season=1)
        self.assertEqual(actual_cost, 40)
        self.assertEqual(county["treasury"], 460)
        self.assertEqual(county["bailiff_level"], 0)  # 尚未生效
        self.assertEqual(len(county["active_investments"]), 1)
        self.assertEqual(county["active_investments"][0]["completion_season"], 2)
        self.assertIn("启动", msg)

        # ── 阶段2：次月完成结算 ──
        report = {"events": []}
        SettlementService._apply_completed_investments(county, season=2, report=report)
        self.assertEqual(county["bailiff_level"], 1)
        self.assertEqual(county["admin_cost_detail"]["bailiff_cost"], 40)
        self.assertEqual(county["admin_cost"], 140)
        # L1 衙役治安 +8（递减公式 9 - new_level）；Model A 聚合村庄
        self.assertEqual(county["villages"][0]["security"], 68)
        self.assertEqual(county["villages"][1]["security"], 100)  # clamp
        self.assertEqual(county["villages"][2]["security"], 58)
        self.assertEqual(county["active_investments"], [])

    def test_relief_cost_scales_with_severity_and_price_index(self):
        county = {
            "treasury": 500,
            "price_index": 1.0,
            "active_investments": [],
            "villages": [],
            "disaster_this_year": {"type": "flood", "severity": 0.2, "relieved": False},
        }

        low_cost = InvestmentService.get_actual_cost(county, "relief")
        county["disaster_this_year"]["severity"] = 0.8
        high_severity_cost = InvestmentService.get_actual_cost(county, "relief")
        county["price_index"] = 1.5
        high_price_cost = InvestmentService.get_actual_cost(county, "relief")

        self.assertGreater(high_severity_cost, low_cost)
        self.assertGreater(high_price_cost, high_severity_cost)
