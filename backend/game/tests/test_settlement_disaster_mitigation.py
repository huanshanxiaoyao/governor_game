import copy
from unittest.mock import patch

from django.test import SimpleTestCase

from game.services.constants import (
    GRANARY_POP_LOSS_MULTIPLIER,
    RELIEF_POP_LOSS_MULTIPLIER,
)
from game.services.settlement import SettlementService


class DisasterMitigationTests(SimpleTestCase):
    @staticmethod
    def _build_county(has_granary=False, relieved=False):
        return {
            "villages": [
                {
                    "name": "甲村",
                    "population": 1000,
                    "farmland": 10000,
                    "gentry_land_pct": 0.5,
                    "morale": 50,
                    "security": 50,
                },
            ],
            "markets": [{"name": "东市", "merchants": 10, "gmv": 0}],
            "environment": {"agriculture_suitability": 1.0},
            "irrigation_level": 1,
            "tax_rate": 0.10,
            "morale": 50,
            "commercial": 50,
            "has_granary": has_granary,
            "disaster_this_year": {
                "type": "flood",
                "severity": 0.5,
                "relieved": relieved,
            },
            "fiscal_year": {
                "commercial_tax": 0.0,
                "commercial_retained": 0.0,
                "corvee_tax": 0.0,
                "corvee_retained": 0.0,
            },
            "admin_cost": 0,
            "treasury": 0.0,
            "peasant_grain_reserve": 0.0,
        }

    def test_granary_and_relief_do_not_change_disaster_crop_damage(self):
        base_county = self._build_county(has_granary=False, relieved=False)
        base_output = SettlementService._compute_peasant_production(base_county, include_disaster=True)

        granary_county = self._build_county(has_granary=True, relieved=False)
        granary_output = SettlementService._compute_peasant_production(granary_county, include_disaster=True)

        relief_county = self._build_county(has_granary=False, relieved=True)
        relief_output = SettlementService._compute_peasant_production(relief_county, include_disaster=True)

        both_county = self._build_county(has_granary=True, relieved=True)
        both_output = SettlementService._compute_peasant_production(both_county, include_disaster=True)

        self.assertAlmostEqual(granary_output, base_output, places=6)
        self.assertAlmostEqual(relief_output, base_output, places=6)
        self.assertAlmostEqual(both_output, base_output, places=6)

    def test_autumn_crop_damage_depends_only_on_irrigation(self):
        def run_once(has_granary, relieved):
            county = self._build_county(has_granary=has_granary, relieved=relieved)
            report = {"events": []}
            with patch.object(
                SettlementService,
                "_annual_population_update",
                lambda _c, _r, **_kwargs: None,
            ):
                with patch("game.services.settlement.random.uniform", return_value=0.1):
                    SettlementService._autumn_settlement(county, report)
            return report["autumn"]["total_agri_output"]

        baseline = run_once(False, False)
        self.assertEqual(run_once(True, False), baseline)
        self.assertEqual(run_once(False, True), baseline)
        self.assertEqual(run_once(True, True), baseline)

    def test_population_loss_uses_065_multipliers(self):
        base = self._build_county(has_granary=False, relieved=False)
        granary = self._build_county(has_granary=True, relieved=False)
        relief = self._build_county(has_granary=False, relieved=True)
        both = self._build_county(has_granary=True, relieved=True)

        def run_population_after(county):
            county = copy.deepcopy(county)
            report = {"events": []}
            with patch.object(
                SettlementService,
                "_annual_population_update",
                lambda _c, _r, **_kwargs: None,
            ):
                with patch("game.services.settlement.random.uniform", return_value=0.1):
                    SettlementService._autumn_settlement(county, report)
            return county["villages"][0]["population"]

        # Base loss: int(1000 * 0.1) = 100
        self.assertEqual(run_population_after(base), 900)

        # Single mitigation: int(100 * 0.65) = 65
        expected_single_loss = int(100 * GRANARY_POP_LOSS_MULTIPLIER)
        self.assertEqual(run_population_after(granary), 1000 - expected_single_loss)
        expected_relief_loss = int(100 * RELIEF_POP_LOSS_MULTIPLIER)
        self.assertEqual(run_population_after(relief), 1000 - expected_relief_loss)

        # Stacked mitigation: sequential int cast as implemented in settlement loop
        expected_stacked_loss = int(int(100 * GRANARY_POP_LOSS_MULTIPLIER) * RELIEF_POP_LOSS_MULTIPLIER)
        self.assertEqual(run_population_after(both), 1000 - expected_stacked_loss)
