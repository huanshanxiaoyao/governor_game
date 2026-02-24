from django.test import SimpleTestCase

from game.services.county import CountyService
from game.services.settlement import SettlementService


class CountyInitializationTests(SimpleTestCase):
    def test_fiscal_core_population_uses_initial_irrigation_level(self):
        county = CountyService.create_initial_county("fiscal_core")
        self.assertEqual(county.get("irrigation_level"), 1)

        for village in county["villages"]:
            expected_ceiling = SettlementService._calculate_village_ceiling(village, county)
            self.assertEqual(
                village.get("ceiling"),
                expected_ceiling,
                msg=f"{village['name']} ceiling should be derived from irrigation_level=1",
            )
            self.assertEqual(
                village["population"],
                int(expected_ceiling * 0.60),
                msg=f"{village['name']} population should be 60% of computed ceiling",
            )

    def test_clan_governance_population_uses_initial_irrigation_level(self):
        county = CountyService.create_initial_county("clan_governance")
        self.assertEqual(county.get("irrigation_level"), 1)

        for village in county["villages"]:
            expected_ceiling = SettlementService._calculate_village_ceiling(village, county)
            self.assertEqual(village.get("ceiling"), expected_ceiling)
            self.assertEqual(village["population"], int(expected_ceiling * 0.60))
