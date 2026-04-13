"""
county_data schema migration 测试
验证 _ensure_county_defaults 能正确补齐旧存档缺失字段
"""
from django.test import SimpleTestCase

from game.services.state import _ensure_county_defaults


class TestEnsureCountyDefaults(SimpleTestCase):
    """_ensure_county_defaults 应补齐缺失字段，不覆盖已有值。"""

    def test_empty_dict_gets_all_defaults(self):
        county = {}
        _ensure_county_defaults(county)
        # 抽样验证关键字段已补齐
        self.assertEqual(county["tax_rate"], 0.12)
        self.assertEqual(county["bailiff_level"], 0)
        self.assertFalse(county["has_granary"])
        self.assertEqual(county["prefect_affinity"], 50)
        self.assertIsInstance(county["fiscal_year"], dict)
        self.assertIsInstance(county["active_investments"], list)
        self.assertIsInstance(county["clans"], dict)
        self.assertIsInstance(county["environment"], dict)
        self.assertIn("agriculture_suitability", county["environment"])

    def test_existing_values_not_overwritten(self):
        county = {
            "tax_rate": 0.15,
            "bailiff_level": 3,
            "treasury": 999.0,
            "has_granary": True,
        }
        _ensure_county_defaults(county)
        self.assertEqual(county["tax_rate"], 0.15)
        self.assertEqual(county["bailiff_level"], 3)
        self.assertEqual(county["treasury"], 999.0)
        self.assertTrue(county["has_granary"])

    def test_mutable_defaults_not_shared(self):
        """每次调用应生成独立的 list/dict，不共享引用。"""
        county_a = {}
        county_b = {}
        _ensure_county_defaults(county_a)
        _ensure_county_defaults(county_b)
        # 修改 a 不影响 b
        county_a["active_investments"].append({"action": "test"})
        self.assertEqual(len(county_b["active_investments"]), 0)
        county_a["fiscal_year"]["commercial_tax"] = 999
        self.assertEqual(county_b["fiscal_year"]["commercial_tax"], 0)

    def test_full_county_preserves_existing(self):
        """对已完整的 county_data，已有字段不被覆盖。"""
        from game.services.county import CountyService
        import copy
        county = CountyService.create_initial_county("fiscal_core")
        original = copy.deepcopy(county)
        _ensure_county_defaults(county)
        # 原有 key 的值不变
        for key, value in original.items():
            self.assertEqual(county[key], value, f"key '{key}' was overwritten")

    def test_partial_county_only_fills_gaps(self):
        """只有缺失的 key 被补齐，已有的所有 key 保持原值。"""
        county = {
            "morale": 80.0,
            "security": 70.0,
            "treasury": 500.0,
            "villages": [{"name": "甲村"}],
        }
        _ensure_county_defaults(county)
        self.assertEqual(county["morale"], 80.0)
        self.assertEqual(county["security"], 70.0)
        self.assertEqual(county["treasury"], 500.0)
        self.assertEqual(county["villages"], [{"name": "甲村"}])
        # 缺失的被补上
        self.assertEqual(county["commercial_tax_rate"], 0.03)
        self.assertIsInstance(county["annual_quota"], dict)
