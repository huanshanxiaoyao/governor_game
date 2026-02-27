from django.test import SimpleTestCase

from game.services.county import CountyService
from game.services.constants import (
    ANNUAL_CONSUMPTION,
    GENTRY_HELPER_FEE_RATE,
    IRRIGATION_DAMAGE_REDUCTION,
    MAX_YIELD_PER_MU,
)
from game.services.ledger import advance_gentry_grain_ledgers, refresh_village_grain_ledgers


class LedgerGrainBackfillTests(SimpleTestCase):
    def test_refresh_can_backfill_zeroed_village_grain_ledgers(self):
        county = CountyService.create_initial_county("fiscal_core")

        for village in county["villages"]:
            village["peasant_ledger"]["grain_surplus"] = 0.0
            village["peasant_ledger"]["monthly_consumption"] = 0.0
            village["peasant_ledger"]["monthly_surplus"] = 0.0
            village["gentry_ledger"]["grain_surplus"] = 0.0
            village["gentry_ledger"]["grain_surplus_seeded"] = False

        refresh_village_grain_ledgers(county)

        for village in county["villages"]:
            peasant = village["peasant_ledger"]
            gentry = village["gentry_ledger"]
            self.assertNotEqual(peasant["grain_surplus"], 0.0)
            self.assertGreater(peasant["monthly_consumption"], 0.0)
            self.assertNotEqual(gentry["grain_surplus"], 0.0)

        self.assertAlmostEqual(
            sum(v["peasant_ledger"]["grain_surplus"] for v in county["villages"]),
            county["peasant_grain_reserve"],
            delta=2.0,
        )

    def test_gentry_surplus_accounts_for_elapsed_months_since_harvest(self):
        county = CountyService.create_initial_county("fiscal_core")
        village = county["villages"][0]

        village["gentry_ledger"]["grain_surplus"] = 0.0
        village["gentry_ledger"]["grain_surplus_seeded"] = False
        refresh_village_grain_ledgers(county, current_season=9)  # just harvested
        sep_surplus = village["gentry_ledger"]["grain_surplus"]

        village["gentry_ledger"]["grain_surplus"] = 0.0
        village["gentry_ledger"]["grain_surplus_seeded"] = False
        refresh_village_grain_ledgers(county, current_season=1)  # 4 months elapsed
        jan_surplus = village["gentry_ledger"]["grain_surplus"]

        self.assertLess(jan_surplus, sep_surplus)

    def test_gentry_surplus_accumulates_on_autumn_harvest_instead_of_reset(self):
        county = CountyService.create_initial_county("fiscal_core")
        village = county["villages"][0]
        gentry = village["gentry_ledger"]

        before_sep = gentry["grain_surplus"]
        advance_gentry_grain_ledgers(county, 9)  # autumn month
        after_sep = gentry["grain_surplus"]

        ag_suit = county["environment"]["agriculture_suitability"]
        irrigation_mult = 1 + county.get("irrigation_level", 0) * 0.15
        tax_rate = county.get("tax_rate", 0.12)
        registered = gentry["registered_farmland"]
        hidden = gentry["hidden_farmland"]
        yield_per_mu = MAX_YIELD_PER_MU * ag_suit * irrigation_mult
        gross = (registered + hidden) * yield_per_mu
        morale = county.get("morale", 50)
        collection_efficiency = 0.7 + 0.3 * (morale / 100)
        # 隐匿地不纳税
        tax_paid = registered * yield_per_mu * tax_rate * collection_efficiency
        helper_fee = gross * GENTRY_HELPER_FEE_RATE
        harvest_after_cost = gross - tax_paid - helper_fee
        monthly_cost = (
            gentry["registered_population"] * ANNUAL_CONSUMPTION * 3
            + gentry["hidden_population"] * ANNUAL_CONSUMPTION
        ) / 12

        self.assertAlmostEqual(
            after_sep - before_sep,
            harvest_after_cost - monthly_cost,
            delta=0.2,
        )

    def test_gentry_autumn_harvest_uses_disaster_and_collection_efficiency(self):
        county = {
            "morale": 0,
            "tax_rate": 0.12,
            "irrigation_level": 0,
            "environment": {"agriculture_suitability": 1.0},
            "villages": [
                {
                    "name": "甲村",
                    "population": 1000,
                    "farmland": 1000,
                    "gentry_land_pct": 0.5,
                    "peasant_ledger": {
                        "registered_population": 1000,
                        "farmland": 500,
                    },
                    "gentry_ledger": {
                        "registered_population": 12,
                        "hidden_population": 0,
                        "registered_farmland": 1000,
                        "hidden_farmland": 0,
                        "grain_surplus": 1000.0,
                        "grain_surplus_seeded": True,
                    },
                },
            ],
            "disaster_this_year": {
                "type": "flood",
                "severity": 0.5,
            },
        }

        advance_gentry_grain_ledgers(county, 9)
        gentry = county["villages"][0]["gentry_ledger"]

        gross = 1000 * MAX_YIELD_PER_MU * 1.0 * 1.0
        damage_factor = 0.5 * (1 - IRRIGATION_DAMAGE_REDUCTION[0])
        gross_after_disaster = gross * (1 - damage_factor)
        collection_efficiency = 0.7 + 0.3 * 0.0
        tax_paid = gross_after_disaster * 0.12 * collection_efficiency
        helper_fee = gross_after_disaster * GENTRY_HELPER_FEE_RATE
        monthly_cost = (12 * ANNUAL_CONSUMPTION * 3) / 12
        expected = 1000 + gross_after_disaster - tax_paid - helper_fee - monthly_cost

        self.assertAlmostEqual(gentry["grain_surplus"], expected, delta=0.2)
