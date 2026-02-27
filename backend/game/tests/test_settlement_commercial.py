from django.test import SimpleTestCase

from game.services.constants import ANNUAL_CONSUMPTION, EXCESS_CONSUMPTION_THRESHOLD
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

    def test_excess_consumption_increases_monthly_consumption(self):
        county = self._build_county(reserve=900000)
        report = {"events": []}
        month = 1

        total_pop = 1500
        base_monthly_consumption = total_pop * ANNUAL_CONSUMPTION / 12
        months_to_harvest = 8
        remaining_consumption = base_monthly_consumption * months_to_harvest
        monthly_pcs = ((900000 - remaining_consumption) / total_pop) / months_to_harvest

        ratio = monthly_pcs / EXCESS_CONSUMPTION_THRESHOLD
        expected_multiplier = 1 + ratio * ratio * 0.1
        expected_monthly_consumption = base_monthly_consumption * expected_multiplier

        SettlementService._update_commercial(county, month, report)

        actual_consumption = 900000 - county["peasant_grain_reserve"]
        self.assertAlmostEqual(actual_consumption, expected_monthly_consumption, places=6)
        self.assertGreater(actual_consumption, base_monthly_consumption)
        self.assertGreater(county["peasant_surplus"]["monthly_consumption"], round(base_monthly_consumption))

    def test_no_excess_consumption_when_surplus_below_threshold(self):
        county = self._build_county(reserve=450000)
        report = {"events": []}
        month = 1

        total_pop = 1500
        base_monthly_consumption = total_pop * ANNUAL_CONSUMPTION / 12

        SettlementService._update_commercial(county, month, report)

        actual_consumption = 450000 - county["peasant_grain_reserve"]
        self.assertAlmostEqual(actual_consumption, base_monthly_consumption, places=6)
