import copy

from django.test import SimpleTestCase

from game.services.county import CountyService
from game.services.constants import (
    ANNUAL_CONSUMPTION,
    GENTRY_HELPER_FEE_RATE,
    MAX_YIELD_PER_MU,
)
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

    def test_dual_ledgers_are_initialized_and_synced(self):
        county = CountyService.create_initial_county("fiscal_core")

        for village in county["villages"]:
            self.assertIn("peasant_ledger", village)
            self.assertIn("gentry_ledger", village)

            peasant = village["peasant_ledger"]
            gentry = village["gentry_ledger"]

            self.assertEqual(village["population"], peasant["registered_population"])
            self.assertEqual(
                village["farmland"],
                peasant["farmland"] + gentry["registered_farmland"],
            )
            self.assertEqual(village["hidden_land"], gentry["hidden_farmland"])
            self.assertGreater(village["land_ceiling"], village["farmland"] + village["hidden_land"])

    def test_village_grain_ledgers_are_seeded_on_new_game(self):
        county = CountyService.create_initial_county("fiscal_core")
        env = county.get("environment", {})
        ag_suit = env.get("agriculture_suitability", 0.7)
        irrigation_mult = 1 + county.get("irrigation_level", 0) * 0.15
        tax_rate = county.get("tax_rate", 0.12)

        reserve_sum = 0.0

        for village in county["villages"]:
            peasant = village["peasant_ledger"]
            gentry = village["gentry_ledger"]
            pop = peasant["registered_population"]
            land = peasant["farmland"]
            income = land * MAX_YIELD_PER_MU * ag_suit * irrigation_mult * (1 - tax_rate)
            expected_opening_reserve = income - pop * ANNUAL_CONSUMPTION * (4 / 12)

            self.assertNotEqual(
                peasant["grain_surplus"],
                0.0,
                msg=f"{village['name']} peasant grain surplus should be initialized",
            )
            self.assertAlmostEqual(
                peasant["grain_surplus"],
                expected_opening_reserve,
                delta=0.2,  # rounded to 0.1 in ledger
            )
            self.assertGreater(
                peasant["monthly_consumption"],
                0.0,
                msg=f"{village['name']} peasant monthly consumption should be initialized",
            )
            self.assertNotEqual(
                gentry["grain_surplus"],
                0.0,
                msg=f"{village['name']} gentry grain surplus should be initialized",
            )
            registered_land = gentry["registered_farmland"]
            hidden_land = gentry["hidden_farmland"]
            gentry_land = registered_land + hidden_land
            yield_per_mu = MAX_YIELD_PER_MU * ag_suit * irrigation_mult
            gentry_gross = gentry_land * yield_per_mu
            # 隐匿地不纳税 — tax only on registered farmland
            tax_paid = registered_land * yield_per_mu * tax_rate
            helper_fee = gentry_gross * GENTRY_HELPER_FEE_RATE
            gentry_income = gentry_gross - tax_paid - helper_fee
            gentry_cost_opening = (
                gentry["registered_population"] * ANNUAL_CONSUMPTION * 3
                + gentry["hidden_population"] * ANNUAL_CONSUMPTION
            ) * (4 / 12)
            self.assertAlmostEqual(
                gentry["grain_surplus"],
                gentry_income - gentry_cost_opening,
                delta=0.2,
            )
            reserve_sum += peasant["grain_surplus"]

        self.assertAlmostEqual(
            reserve_sum,
            county["peasant_grain_reserve"],
            delta=2.0,  # cumulative rounding drift across villages
        )

    def test_village_ceiling_no_longer_depends_on_agriculture_suitability(self):
        county = CountyService.create_initial_county("fiscal_core")
        village = county["villages"][0]

        low_env = copy.deepcopy(county)
        high_env = copy.deepcopy(county)
        low_env["environment"]["agriculture_suitability"] = 0.3
        high_env["environment"]["agriculture_suitability"] = 1.0

        low_ceiling = SettlementService._calculate_village_ceiling(village, low_env)
        high_ceiling = SettlementService._calculate_village_ceiling(village, high_env)
        self.assertEqual(low_ceiling, high_ceiling)
