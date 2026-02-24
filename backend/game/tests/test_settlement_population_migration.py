from unittest.mock import patch

from django.test import SimpleTestCase

from game.services.settlement import SettlementService


class MoraleSecurityLinkTests(SimpleTestCase):
    @staticmethod
    def _build_county(security):
        return {
            "morale": 50.0,
            "education": 0.0,
            "tax_rate": 0.12,
            "security": float(security),
            "villages": [
                {"name": "甲村", "population": 1000, "morale": 50.0},
            ],
        }

    def _run_update(self, security):
        county = self._build_county(security)
        report = {"events": []}
        with patch.object(SettlementService, "_sync_county_from_villages", lambda *_args, **_kwargs: None):
            SettlementService._update_morale(county, report)
        return county["morale"]

    def test_security_above_60_adds_morale(self):
        morale = self._run_update(61)
        self.assertAlmostEqual(morale, 50.17, places=2)

    def test_security_below_30_reduces_morale(self):
        morale = self._run_update(29)
        self.assertAlmostEqual(morale, 49.17, places=2)

    def test_mid_security_keeps_base_morale_decay(self):
        morale = self._run_update(45)
        self.assertAlmostEqual(morale, 49.67, places=2)


class PopulationMigrationCompetitionTests(SimpleTestCase):
    @staticmethod
    def _build_county(
        morale=60,
        security=60,
        commercial=60,
        education=60,
        population=1000,
    ):
        return {
            "villages": [
                {
                    "name": "甲村",
                    "population": population,
                    "farmland": 10000,
                    "gentry_land_pct": 0.3,
                    "morale": 50,
                    "security": 50,
                },
            ],
            "environment": {"agriculture_suitability": 0.8},
            "irrigation_level": 1,
            "tax_rate": 0.10,
            "medical_level": 0,
            "morale": float(morale),
            "security": float(security),
            "commercial": float(commercial),
            "education": float(education),
        }

    @staticmethod
    def _annual_update(county, peers):
        report = {"events": []}
        with patch.object(SettlementService, "_capacity_modifier", return_value=0):
            SettlementService._annual_population_update(county, report, peer_counties=peers)
        return report

    def test_inflow_triggers_on_two_significant_leads_and_other_dims_parity(self):
        county = self._build_county(morale=70, security=70, commercial=70, education=70, population=1000)
        peer = self._build_county(morale=50, security=50, commercial=63, education=63, population=1000)

        report = self._annual_update(county, [peer])

        self.assertEqual(county["villages"][0]["population"], 1015)
        self.assertEqual(report["population_update"]["migration"]["inflow_total"], 15)
        self.assertEqual(report["population_update"]["migration"]["outflow_total"], 0)
        pair = report["population_update"]["migration"]["pairs"][0]
        self.assertEqual(pair["peer_name"], "邻县1")
        self.assertEqual(pair["dim_details"]["morale"]["bucket"], "lead")
        self.assertEqual(pair["dim_details"]["security"]["bucket"], "lead")
        self.assertEqual(pair["dim_details"]["commercial"]["bucket"], "parity")
        self.assertEqual(pair["dim_details"]["education"]["bucket"], "parity")

    def test_inflow_triggers_when_one_dimension_significantly_leads(self):
        county = self._build_county(morale=70, security=70, commercial=70, education=70, population=1000)
        peer = self._build_county(morale=50, security=63, commercial=63, education=63, population=1000)

        report = self._annual_update(county, [peer])

        self.assertEqual(county["villages"][0]["population"], 1005)
        self.assertEqual(report["population_update"]["migration"]["inflow_total"], 5)
        self.assertEqual(report["population_update"]["migration"]["outflow_total"], 0)

    def test_inflow_triggers_when_non_significant_dimensions_include_mid(self):
        county = self._build_county(morale=70, security=70, commercial=70, education=70, population=1000)
        peer = self._build_county(morale=50, security=50, commercial=58, education=63, population=1000)

        report = self._annual_update(county, [peer])

        self.assertEqual(county["villages"][0]["population"], 1015)
        self.assertEqual(report["population_update"]["migration"]["inflow_total"], 15)
        self.assertEqual(report["population_update"]["migration"]["outflow_total"], 0)

    def test_outflow_triggers_on_two_significant_lags_and_other_dims_parity(self):
        county = self._build_county(morale=50, security=50, commercial=63, education=63, population=1000)
        peer = self._build_county(morale=70, security=70, commercial=63, education=63, population=1000)

        report = self._annual_update(county, [peer])

        self.assertEqual(county["villages"][0]["population"], 985)
        self.assertEqual(report["population_update"]["migration"]["outflow_total"], 15)
        self.assertEqual(report["population_update"]["migration"]["inflow_total"], 0)

    def test_outflow_triggers_when_one_dimension_significantly_lags(self):
        county = self._build_county(morale=50, security=63, commercial=63, education=63, population=1000)
        peer = self._build_county(morale=70, security=63, commercial=63, education=63, population=1000)

        report = self._annual_update(county, [peer])

        self.assertEqual(county["villages"][0]["population"], 995)
        self.assertEqual(report["population_update"]["migration"]["outflow_total"], 5)
        self.assertEqual(report["population_update"]["migration"]["inflow_total"], 0)

    def test_inflow_also_triggers_when_significant_lead_count_is_three(self):
        county = self._build_county(morale=80, security=80, commercial=80, education=63, population=1000)
        peer = self._build_county(morale=60, security=60, commercial=60, education=63, population=1000)

        report = self._annual_update(county, [peer])

        self.assertEqual(county["villages"][0]["population"], 1020)
        self.assertEqual(report["population_update"]["migration"]["inflow_total"], 20)

    def test_no_flow_when_significant_lead_and_lag_coexist(self):
        county = self._build_county(morale=70, security=50, commercial=63, education=63, population=1000)
        peer = self._build_county(morale=50, security=70, commercial=63, education=63, population=1000)

        report = self._annual_update(county, [peer])

        self.assertEqual(county["villages"][0]["population"], 1000)
        self.assertEqual(report["population_update"]["migration"]["inflow_total"], 0)
        self.assertEqual(report["population_update"]["migration"]["outflow_total"], 0)

    def test_inflow_triggers_for_case_two_leads_one_parity_one_mid(self):
        county = self._build_county(
            morale=68.6, security=70.2, commercial=86.0, education=80.0, population=1000
        )
        peer = self._build_county(
            morale=60.0, security=60.0, commercial=60.0, education=60.0, population=1000
        )

        report = self._annual_update(county, [peer])

        pair = report["population_update"]["migration"]["pairs"][0]
        self.assertEqual(pair["lead_count"], 2)
        self.assertEqual(pair["lag_count"], 0)
        self.assertEqual(pair["parity_count"], 1)
        self.assertEqual(pair["mid_count"], 1)
        self.assertEqual(pair["direction"], "inflow")
        self.assertAlmostEqual(pair["rate"], 0.015, places=6)
        self.assertEqual(report["population_update"]["migration"]["inflow_total"], 15)
