from django.test import SimpleTestCase

from game.services.county import CountyService
from game.services.settlement import SettlementService


class DisasterReliefAdviceTests(SimpleTestCase):
    def _build_county_with_disaster(self):
        county = CountyService.create_initial_county(county_type="disaster_prone")
        county["disaster_this_year"] = {
            "type": "flood",
            "severity": 0.5,
            "relieved": False,
        }
        return county

    def test_advice_available_in_september_with_disaster(self):
        county = self._build_county_with_disaster()
        advice = SettlementService.compute_relief_advice(county, season=9)

        self.assertTrue(advice["available"])
        self.assertGreater(advice["suggested_claim"], 0)
        self.assertGreaterEqual(advice["suggested_claim"], advice["suggest_min"])
        self.assertLessEqual(advice["suggested_claim"], advice["suggest_max"])
        self.assertIn("建议申报", advice["advisor_note"])

    def test_advice_unavailable_after_window(self):
        county = self._build_county_with_disaster()
        advice = SettlementService.compute_relief_advice(county, season=10)

        self.assertFalse(advice["available"])
        self.assertIn("窗口已过", advice["reason"])

    def test_advice_unavailable_after_submission_in_same_year(self):
        county = self._build_county_with_disaster()
        county["relief_application"] = {
            "year": 1,
            "status": "PENDING",
            "claimed_loss": 123.0,
            "submitted_season": 9,
        }
        advice = SettlementService.compute_relief_advice(county, season=9)

        self.assertFalse(advice["available"])
        self.assertEqual(advice["status"], "PENDING")
        self.assertEqual(advice["claimed_loss"], 123.0)

    def test_estimated_loss_uses_tax_and_remit_not_gross_loss(self):
        county = {
            "villages": [{"name": "甲村", "farmland": 10000, "population": 1000}],
            "environment": {"agriculture_suitability": 1.0},
            "irrigation_level": 0,
            "disaster_this_year": {"type": "flood", "severity": 0.4, "relieved": False},
            "tax_rate": 0.10,
            "morale": 50,
            "remit_ratio": 0.60,
        }

        estimated = SettlementService._estimate_disaster_loss(county)
        gross_output_loss = 10000 * 0.5 * 0.4  # 2000两
        expected = gross_output_loss * 0.10 * 0.85 * 0.60  # 102两
        self.assertAlmostEqual(estimated, expected, places=6)

    def test_advice_note_explains_when_buffer_is_clipped_by_risk_cap(self):
        county = self._build_county_with_disaster()
        advice = SettlementService.compute_relief_advice(county, season=9)

        self.assertTrue(advice["available"])
        self.assertIn("受核查风险约束", advice["advisor_note"])
        self.assertGreaterEqual(advice["effective_buffer_included"], 0)
