from django.test import SimpleTestCase

from game.services.investment import InvestmentService


class InvestmentAvailableActionsTests(SimpleTestCase):
    @staticmethod
    def _build_county(has_school_flags):
        villages = []
        for idx, has_school in enumerate(has_school_flags):
            villages.append({
                "name": f"村{idx + 1}",
                "population": 1000,
                "farmland": 10000,
                "has_school": has_school,
            })
        return {
            "treasury": 1000,
            "price_index": 1.0,
            "active_investments": [],
            "disaster_this_year": None,
            "villages": villages,
        }

    def test_targeted_investments_are_available_when_any_village_is_valid(self):
        county = self._build_county([False, True])
        actions = {
            item["action"]: item
            for item in InvestmentService.get_available_actions(county)
        }

        self.assertIsNone(actions["reclaim_land"]["disabled_reason"])
        self.assertIsNone(actions["fund_village_school"]["disabled_reason"])

    def test_targeted_investments_not_blocked_by_missing_target_reason(self):
        county = self._build_county([True, True])
        actions = {
            item["action"]: item
            for item in InvestmentService.get_available_actions(county)
        }

        self.assertIsNone(actions["reclaim_land"]["disabled_reason"])
        self.assertIsNotNone(actions["fund_village_school"]["disabled_reason"])
        self.assertNotIn(
            "需要指定目标村庄",
            actions["fund_village_school"]["disabled_reason"],
        )

    def test_reclaim_land_warning_threshold_is_strictly_above_85_percent(self):
        county = self._build_county([False, False])
        county["villages"] = [
            {
                "name": "甲村",
                "population": 1000,
                "farmland": 850,
                "hidden_land": 0,
                "land_ceiling": 1000,
                "gentry_land_pct": 0.3,
            },
            {
                "name": "乙村",
                "population": 1000,
                "farmland": 851,
                "hidden_land": 0,
                "land_ceiling": 1000,
                "gentry_land_pct": 0.3,
            },
        ]

        actions = {
            item["action"]: item
            for item in InvestmentService.get_available_actions(county)
        }
        warnings = actions["reclaim_land"].get("village_warnings", [])
        warned_villages = {w["village"] for w in warnings}

        self.assertNotIn("甲村", warned_villages)  # exactly 85.0%
        self.assertIn("乙村", warned_villages)     # 85.1%
        self.assertNotIn("utilization", warnings[0])

    def test_reclaim_land_allows_exactly_120_percent_of_land_ceiling(self):
        county = self._build_county([False])
        county["villages"] = [
            {
                "name": "甲村",
                "population": 1000,
                "farmland": 4000,
                "hidden_land": 0,
                "land_ceiling": 4000,
                "gentry_land_pct": 0.0,
            },
        ]

        is_valid, reason = InvestmentService.validate(county, "reclaim_land", "甲村", season=3)

        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_reclaim_land_is_blocked_when_this_reclaim_would_exceed_120_percent(self):
        county = self._build_county([False])
        county["villages"] = [
            {
                "name": "甲村",
                "population": 1000,
                "farmland": 4100,
                "hidden_land": 0,
                "land_ceiling": 4000,
                "gentry_land_pct": 0.0,
            },
        ]

        is_valid, reason = InvestmentService.validate(county, "reclaim_land", "甲村", season=3)
        actions = {
            item["action"]: item
            for item in InvestmentService.get_available_actions(county, season=3)
        }

        self.assertFalse(is_valid)
        self.assertEqual(reason, "该村继续开垦将超过土地开发上限，无法执行")
        self.assertEqual(actions["reclaim_land"]["disabled_reason"], "该村继续开垦将超过土地开发上限，无法执行")
        self.assertEqual(actions["reclaim_land"].get("blocked_villages"), ["甲村"])

    def test_reclaim_land_cap_counts_pending_reclaims_for_same_village(self):
        county = self._build_county([False])
        county["active_investments"] = [
            {
                "action": "reclaim_land",
                "target_village": "甲村",
                "started_season": 1,
                "completion_season": 3,
                "description": "开垦荒地",
            },
        ]
        county["villages"] = [
            {
                "name": "甲村",
                "population": 1000,
                "farmland": 3201,
                "hidden_land": 0,
                "land_ceiling": 4000,
                "gentry_land_pct": 0.0,
            },
        ]

        is_valid, reason = InvestmentService.validate(county, "reclaim_land", "甲村", season=2)

        self.assertFalse(is_valid)
        self.assertEqual(reason, "该村继续开垦将超过土地开发上限，无法执行")
