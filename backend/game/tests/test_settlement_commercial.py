from django.test import SimpleTestCase

from game.services.constants import ANNUAL_CONSUMPTION, CC_SENSITIVITY
from game.services.settlement import SettlementService


class CommercialConsumptionTests(SimpleTestCase):
    @staticmethod
    def _build_county(reserve):
        return {
            "villages": [
                {"name": "甲村", "population": 1000},
                {"name": "乙村", "population": 500},
            ],
            "markets": [
                {"name": "东市", "merchants": 10, "gmv": 0},
            ],
            "commercial": 50,
            "commercial_tax_rate": 0.03,
            "peasant_grain_reserve": float(reserve),
            "fiscal_year": {
                "commercial_tax": 0.0,
                "commercial_retained": 0.0,
            },
            "treasury": 0.0,
        }

    def test_surplus_reserve_increases_consumption_via_confidence(self):
        """余粮充足时，消费信心指数 >1，月消耗高于基准。

        month=1 → months_to_harvest=8, pop=1500, base_monthly=37500
        reserve=600000 → per_capita_surplus=200斤 → cc=200/8=25 → multiplier=1.5
        """
        county = self._build_county(reserve=600000)
        report = {"events": []}
        month = 1

        total_pop = 1500
        base_monthly = total_pop * ANNUAL_CONSUMPTION / 12  # 37500
        months_to_harvest = 8
        per_capita_surplus = (600000 - months_to_harvest * base_monthly) / total_pop  # 200
        cc = per_capita_surplus / months_to_harvest  # 25
        expected_multiplier = 1.0 + cc / CC_SENSITIVITY  # 1.5
        expected_consumption = base_monthly * expected_multiplier

        SettlementService._update_commercial(county, month, report)

        actual_consumption = 600000 - county["peasant_grain_reserve"]
        self.assertAlmostEqual(actual_consumption, expected_consumption, places=4)
        self.assertGreater(actual_consumption, base_monthly)
        self.assertGreater(county["peasant_surplus"]["monthly_consumption"], round(base_monthly))

    def test_neutral_reserve_gives_baseline_consumption(self):
        """余粮恰好覆盖到秋收时，消费信心=0，月消耗等于基准值。

        reserve = months_to_harvest * base_monthly → per_capita_surplus=0 → cc=0 → multiplier=1.0
        """
        total_pop = 1500
        base_monthly = total_pop * ANNUAL_CONSUMPTION / 12  # 37500
        months_to_harvest = 8
        neutral_reserve = months_to_harvest * base_monthly  # 300000

        county = self._build_county(reserve=neutral_reserve)
        report = {"events": []}
        month = 1

        SettlementService._update_commercial(county, month, report)

        actual_consumption = neutral_reserve - county["peasant_grain_reserve"]
        self.assertAlmostEqual(actual_consumption, base_monthly, places=4)
