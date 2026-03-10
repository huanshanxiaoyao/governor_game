from django.test import SimpleTestCase

from game.services.investment import InvestmentService


class InvestmentEffectsTests(SimpleTestCase):
    def test_hire_bailiffs_boosts_county_and_village_security(self):
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
                {"name": "丙村"},
            ],
        }

        actual_cost, msg = InvestmentService.apply_effects(county, "hire_bailiffs", season=1)

        self.assertEqual(actual_cost, 40)
        self.assertEqual(county["treasury"], 460)
        self.assertEqual(county["bailiff_level"], 1)
        self.assertEqual(county["security"], 68)
        self.assertEqual(county["admin_cost"], 140)
        self.assertEqual(county["admin_cost_detail"]["bailiff_cost"], 40)
        self.assertEqual(county["villages"][0]["security"], 65)
        self.assertEqual(county["villages"][1]["security"], 100)
        self.assertEqual(county["villages"][2]["security"], 55)
        self.assertIn("各村治安+5", msg)

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
