"""
宗族机制测试
Layer 1: 纯逻辑（无DB）— 配合系数 / 治安修正计算
Layer 2: 初始化路径（无DB）— county 生成后宗族/身份数据结构
Layer 3: 结算路径（无DB）— 宗族影响秋季税收 / 月度治安
"""

import copy
import random

from django.test import SimpleTestCase

from game.services.clan import (
    affinity_to_compliance,
    get_county_tax_compliance,
    get_county_security_delta,
)
from game.services.county import CountyService
from game.services.settlement import SettlementService


# ═══════════════════════════════════════════════════════
# Layer 1 — 纯逻辑
# ═══════════════════════════════════════════════════════

class TestClanComplianceLogic(SimpleTestCase):
    """affinity_to_compliance 档位正确性"""

    def test_high_affinity_gives_bonus(self):
        self.assertEqual(affinity_to_compliance(70), 1.05)
        self.assertEqual(affinity_to_compliance(65), 1.05)

    def test_normal_affinity_no_change(self):
        self.assertEqual(affinity_to_compliance(64), 1.00)
        self.assertEqual(affinity_to_compliance(30), 1.00)

    def test_low_affinity_penalty(self):
        self.assertEqual(affinity_to_compliance(29), 0.85)
        self.assertEqual(affinity_to_compliance(10), 0.85)

    def test_hostile_affinity_severe_penalty(self):
        self.assertEqual(affinity_to_compliance(9), 0.65)
        self.assertEqual(affinity_to_compliance(0), 0.65)


class TestCountyTaxCompliance(SimpleTestCase):
    """get_county_tax_compliance 加权平均"""

    def test_no_clans_returns_one(self):
        self.assertEqual(get_county_tax_compliance({}), 1.0)
        self.assertEqual(get_county_tax_compliance({'clans': {}}), 1.0)

    def test_single_cooperative_clan(self):
        county = {'clans': {'A氏': {'clan_affinity': 70, 'power': 100, 'members': []}}}
        self.assertAlmostEqual(get_county_tax_compliance(county), 1.05)

    def test_single_hostile_clan(self):
        county = {'clans': {'A氏': {'clan_affinity': 5, 'power': 100, 'members': []}}}
        self.assertAlmostEqual(get_county_tax_compliance(county), 0.65)

    def test_mixed_clans_weighted_by_power(self):
        # 大宗族配合(power=200)，小宗族对抗(power=50)
        county = {'clans': {
            'A氏': {'clan_affinity': 65, 'power': 200, 'members': []},  # 1.05
            'B氏': {'clan_affinity': 5,  'power': 50,  'members': []},  # 0.65
        }}
        expected = (1.05 * 200 + 0.65 * 50) / 250
        result = get_county_tax_compliance(county)
        self.assertAlmostEqual(result, expected, places=5)

    def test_zero_power_clans_fallback(self):
        county = {'clans': {'A氏': {'clan_affinity': 5, 'power': 0, 'members': []}}}
        self.assertEqual(get_county_tax_compliance(county), 1.0)


class TestClanSecurityDelta(SimpleTestCase):
    """get_county_security_delta 治安修正"""

    def test_no_clans_returns_zero(self):
        self.assertEqual(get_county_security_delta({}), 0.0)

    def test_cooperative_clan_positive_delta(self):
        # power=80 = REF_POWER → factor=1.0, base=+1.5
        county = {'clans': {'A氏': {'clan_affinity': 70, 'power': 80, 'members': []}}}
        self.assertAlmostEqual(get_county_security_delta(county), 1.5)

    def test_hostile_clan_negative_delta(self):
        # affinity < 5 → base=-4.0, power=80 → factor=1.0
        county = {'clans': {'A氏': {'clan_affinity': 3, 'power': 80, 'members': []}}}
        self.assertAlmostEqual(get_county_security_delta(county), -4.0)

    def test_large_clan_amplifies_delta(self):
        # power=160 = 2×REF → factor=2.0 (cap), base=-4.0 → -8, per-clan cap=-5
        county = {'clans': {'A氏': {'clan_affinity': 3, 'power': 160, 'members': []}}}
        self.assertAlmostEqual(get_county_security_delta(county), -5.0)

    def test_total_cap_applied(self):
        # 3 hostile large clans, each would produce -5, total would be -15 → capped at -10
        county = {'clans': {
            'A氏': {'clan_affinity': 3, 'power': 200, 'members': []},
            'B氏': {'clan_affinity': 3, 'power': 200, 'members': []},
            'C氏': {'clan_affinity': 3, 'power': 200, 'members': []},
        }}
        self.assertAlmostEqual(get_county_security_delta(county), -10.0)

    def test_neutral_affinity_no_delta(self):
        county = {'clans': {'A氏': {'clan_affinity': 50, 'power': 100, 'members': []}}}
        self.assertAlmostEqual(get_county_security_delta(county), 0.0)


# ═══════════════════════════════════════════════════════
# Layer 2 — county 初始化结构验证（无DB）
# ═══════════════════════════════════════════════════════

class TestCountyInitStructure(SimpleTestCase):
    """新建 county 应包含 player_social_identity 字段"""

    def test_player_social_identity_present(self):
        county = CountyService.create_initial_county()
        self.assertIn('player_social_identity', county)
        psi = county['player_social_identity']
        self.assertIn('surname', psi)
        self.assertIn('native_place', psi)
        self.assertIn('clan_id', psi)
        self.assertIn('age', psi)

    def test_player_age_in_range(self):
        county = CountyService.create_initial_county()
        age = county['player_social_identity']['age']
        self.assertGreaterEqual(age, 26)
        self.assertLessEqual(age, 38)

    def test_player_clan_id_format(self):
        county = CountyService.create_initial_county()
        psi = county['player_social_identity']
        expected = psi['native_place'] + psi['surname'] + '氏'
        self.assertEqual(psi['clan_id'], expected)


# ═══════════════════════════════════════════════════════
# Layer 3 — 结算路径（纯 county_data，无DB）
# ═══════════════════════════════════════════════════════

def _make_county_with_clans(clan_affinity: int, gentry_ratio: float = 0.4) -> dict:
    """创建带指定宗族亲密度的测试 county。"""
    county = CountyService.create_initial_county('fiscal_core')
    county['gentry_land_ratio'] = gentry_ratio
    county['clans'] = {
        '测试府张氏': {
            'clan_affinity': clan_affinity,
            'power': 80,
            'members': [],
        }
    }
    return county


class TestClanTaxEffect(SimpleTestCase):
    """宗族亲密度影响秋季农业税收"""

    def _run_to_autumn(self, county, *, seed=1234):
        # Seed random so baseline vs variant share the same disaster/yield
        # rolls regardless of prior-test pollution of the global RNG.
        random.seed(seed)
        for month in range(1, 10):
            report = {'season': month, 'events': []}
            SettlementService.settle_county(county, month, report)
        return report

    def test_hostile_clan_reduces_agri_tax(self):
        """敌对宗族（affinity=5）应导致农业税低于同一县无宗族基准。"""
        base = CountyService.create_initial_county('fiscal_core')
        base['gentry_land_ratio'] = 0.4

        baseline = copy.deepcopy(base)
        hostile = copy.deepcopy(base)
        hostile['clans'] = {
            '测试府张氏': {'clan_affinity': 5, 'power': 80, 'members': []}
        }

        r_base = self._run_to_autumn(baseline)
        r_hostile = self._run_to_autumn(hostile)

        self.assertLess(
            r_hostile['autumn']['agri_tax'],
            r_base['autumn']['agri_tax'],
            "敌对宗族（affinity=5）应导致农业税低于无宗族基准",
        )

    def test_cooperative_clan_increases_agri_tax(self):
        """配合宗族（affinity=70）应导致农业税高于同一县无宗族基准。"""
        # 必须从同一初始 county 深拷贝，排除随机差异
        base = CountyService.create_initial_county('fiscal_core')
        base['gentry_land_ratio'] = 0.4

        baseline = copy.deepcopy(base)
        cooperative = copy.deepcopy(base)
        cooperative['clans'] = {
            '测试府张氏': {'clan_affinity': 70, 'power': 80, 'members': []}
        }

        r_base = self._run_to_autumn(baseline)
        r_coop = self._run_to_autumn(cooperative)

        self.assertGreater(
            r_coop['autumn']['agri_tax'],
            r_base['autumn']['agri_tax'],
            "配合宗族（affinity=70）应导致农业税高于无宗族基准",
        )

    def test_hostile_clan_event_in_report(self):
        """敌对宗族应在月报中产生宗族征收提示。"""
        hostile = _make_county_with_clans(clan_affinity=5)
        report = self._run_to_autumn(hostile)
        events = ' '.join(report.get('events', []))
        self.assertIn('宗族征收', events, "月报应包含宗族征收提示")

    def test_neutral_clan_no_effect(self):
        """中性宗族（affinity=50）不应触发宗族征收提示。"""
        neutral = _make_county_with_clans(clan_affinity=50)
        report = self._run_to_autumn(neutral)
        events = ' '.join(report.get('events', []))
        self.assertNotIn('宗族征收', events, "中性宗族不应触发征收折减提示")


class TestClanSecurityEffect(SimpleTestCase):
    """宗族亲密度影响月度治安"""

    def _run_to_month(self, county, month=3):
        for m in range(1, month + 1):
            report = {'season': m, 'events': []}
            SettlementService.settle_county(county, m, report)
        return county['security'], report

    def test_hostile_clan_lowers_security(self):
        """敌对宗族应导致治安低于无宗族基准。"""
        baseline = CountyService.create_initial_county('fiscal_core')
        hostile = CountyService.create_initial_county('fiscal_core')
        # 强制相同初始治安以消除随机误差
        baseline['security'] = 60
        hostile['security'] = 60
        for v in baseline['villages']:
            v['security'] = 60
        for v in hostile['villages']:
            v['security'] = 60
        hostile['clans'] = {
            '测试府李氏': {'clan_affinity': 3, 'power': 80, 'members': []}
        }

        sec_base, _ = self._run_to_month(baseline, month=3)
        sec_hostile, _ = self._run_to_month(hostile, month=3)

        self.assertLess(sec_hostile, sec_base,
                        f"敌对宗族应拉低治安: hostile={sec_hostile:.1f} < baseline={sec_base:.1f}")

    def test_cooperative_clan_raises_security(self):
        """配合宗族应使治安高于无宗族基准。"""
        baseline = CountyService.create_initial_county('fiscal_core')
        cooperative = CountyService.create_initial_county('fiscal_core')
        baseline['security'] = 50
        cooperative['security'] = 50
        for v in baseline['villages']:
            v['security'] = 50
        for v in cooperative['villages']:
            v['security'] = 50
        cooperative['clans'] = {
            '测试府王氏': {'clan_affinity': 70, 'power': 80, 'members': []}
        }

        sec_base, _ = self._run_to_month(baseline, month=3)
        sec_coop, _ = self._run_to_month(cooperative, month=3)

        self.assertGreater(sec_coop, sec_base,
                           f"配合宗族应拉高治安: coop={sec_coop:.1f} > baseline={sec_base:.1f}")

    def test_security_event_mentions_clan_delta(self):
        """治安变化事件应包含宗族修正说明。"""
        county = CountyService.create_initial_county('fiscal_core')
        county['security'] = 50
        for v in county['villages']:
            v['security'] = 50
        county['clans'] = {
            '测试府陈氏': {'clan_affinity': 3, 'power': 80, 'members': []}
        }
        _, report = self._run_to_month(county, month=3)
        events = ' '.join(report.get('events', []))
        self.assertIn('宗族修正', events, "治安事件应包含宗族修正说明")

    def test_bailiff_level_three_can_offset_hostile_clan_drag(self):
        """满级衙役应能实质压住中等规模敌对宗族的月度治安拖拽。"""
        county = CountyService.create_initial_county('fiscal_core')
        county['security'] = 8
        county['morale'] = 70
        county['bailiff_level'] = 3
        for v in county['villages']:
            v['security'] = 8
            v['morale'] = 70
        county['clans'] = {
            '测试府张氏': {'clan_affinity': 3, 'power': 80, 'members': []}
        }

        report = {'season': 1, 'events': []}
        SettlementService._update_security(county, report)

        self.assertGreater(
            county['security'],
            8.0,
            "满级衙役应能缓冲敌对宗族造成的治安下滑",
        )
