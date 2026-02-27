from django.test import SimpleTestCase

from game.services.settlement import SettlementService


class DualLedgerRuleTests(SimpleTestCase):
    def test_corvee_uses_peasant_registered_population_only(self):
        county = {
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
                        "registered_population": 200,
                        "hidden_population": 300,
                        "registered_farmland": 500,
                        "hidden_farmland": 100,
                    },
                },
                {
                    "name": "乙村",
                    "population": 600,
                    "farmland": 800,
                    "gentry_land_pct": 0.4,
                    "peasant_ledger": {
                        "registered_population": 600,
                        "farmland": 480,
                    },
                    "gentry_ledger": {
                        "registered_population": 150,
                        "hidden_population": 120,
                        "registered_farmland": 320,
                        "hidden_farmland": 80,
                    },
                },
            ],
            "treasury": 0.0,
            "remit_ratio": 0.65,
            "fiscal_year": {
                "commercial_tax": 0.0,
                "commercial_retained": 0.0,
                "corvee_tax": 0.0,
                "corvee_retained": 0.0,
            },
        }
        report = {"events": []}

        SettlementService._collect_corvee(county, report)

        liable_pop = 1600  # peasants only
        expected_half = liable_pop * 0.3 / 2
        expected_retained = expected_half * (1 - county["remit_ratio"])

        self.assertAlmostEqual(county["fiscal_year"]["corvee_tax"], expected_half, places=6)
        self.assertAlmostEqual(
            county["fiscal_year"]["corvee_retained"], expected_retained, places=6
        )
        self.assertAlmostEqual(county["treasury"], expected_retained, places=6)

    def test_overdevelopment_bonus_is_point_two_pp_per_one_percent(self):
        county = {
            "villages": [
                {
                    "name": "甲村",
                    "population": 1000,
                    "farmland": 920,
                    "hidden_land": 0,
                    "land_ceiling": 1000,
                    "peasant_ledger": {
                        "registered_population": 1000,
                        "farmland": 920,
                    },
                    "gentry_ledger": {
                        "registered_population": 0,
                        "hidden_population": 0,
                        "registered_farmland": 0,
                        "hidden_farmland": 0,
                    },
                }
            ]
        }
        bonus = SettlementService._overdevelopment_bonus(county)
        # 92% utilization -> 2% over -> +0.4pp = +0.004 probability
        self.assertAlmostEqual(bonus, 0.004, places=6)

    def test_land_survey_verdict_is_binary_by_90_percent(self):
        county = {
            "pending_land_surveys": ["甲村", "乙村"],
            "villages": [
                {
                    "name": "甲村",
                    "population": 1000,
                    "farmland": 890,
                    "hidden_land": 0,
                    "land_ceiling": 1000,
                    "peasant_ledger": {
                        "registered_population": 1000,
                        "farmland": 890,
                    },
                    "gentry_ledger": {
                        "registered_population": 0,
                        "hidden_population": 0,
                        "registered_farmland": 0,
                        "hidden_farmland": 0,
                    },
                },
                {
                    "name": "乙村",
                    "population": 1000,
                    "farmland": 900,
                    "hidden_land": 0,
                    "land_ceiling": 1000,
                    "peasant_ledger": {
                        "registered_population": 1000,
                        "farmland": 900,
                    },
                    "gentry_ledger": {
                        "registered_population": 0,
                        "hidden_population": 0,
                        "registered_farmland": 0,
                        "hidden_farmland": 0,
                    },
                },
            ],
        }
        report = {"events": []}

        SettlementService._process_land_surveys(county, report)

        joined = "\n".join(report["events"])
        self.assertIn("甲村", joined)
        self.assertIn("乙村", joined)
        self.assertIn("甲村：在册耕地890亩，土地利用率89.0%，适合开垦", joined)
        self.assertIn("乙村：在册耕地900亩，土地利用率90.0%，不适合开垦", joined)
