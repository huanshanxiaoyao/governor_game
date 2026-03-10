from django.test import SimpleTestCase

from game.services.county import CountyService
from game.services.investment import InvestmentService
from game.services.settlement import SettlementService


class GranaryRebuildTests(SimpleTestCase):
    def test_granary_rebuild_cost_is_locked_to_first_build(self):
        county = CountyService.create_initial_county(county_type="disaster_prone")
        county["treasury"] = 9999
        county["price_index"] = 1.2
        county["disaster_this_year"] = {
            "type": "flood",
            "severity": 0.4,
            "relieved": False,
        }

        first_cost = InvestmentService.get_actual_cost(county, "build_granary")
        self.assertEqual(first_cost, round(70 * 1.2))

        InvestmentService.apply_effects(county, "build_granary", season=1)
        self.assertEqual(county.get("granary_rebuild_cost"), first_cost)
        self.assertTrue(county.get("has_granary"))

        report = {"season": 9, "events": []}
        SettlementService._autumn_settlement(county, report)
        self.assertFalse(county.get("has_granary"))
        self.assertTrue(county.get("granary_needs_rebuild"))
        self.assertEqual(county.get("granary_last_used_season"), 9)
        self.assertTrue(any("义仓本次赈济后已耗尽" in evt for evt in report["events"]))

        county["price_index"] = 2.0
        rebuild_cost = InvestmentService.get_actual_cost(county, "build_granary")
        self.assertEqual(rebuild_cost, first_cost)

