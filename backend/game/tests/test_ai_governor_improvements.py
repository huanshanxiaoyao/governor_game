"""
Tests for AI governor improvements:
  - _term_penalty: 任期意识惩罚
  - _fallback_investment: 晚期任期不选长工期投资
  - _append_memory: 记忆格式加厚
"""

import pytest

from game.services.ai_governor import AIGovernorService
from game.services.constants import generate_governor_profile, MAX_MONTH
from game.services.county import CountyService


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def county():
    return CountyService.create_initial_county(county_type="fiscal_core")


@pytest.fixture
def profile_welfare():
    """高福祉导向的知县属性（VIRTUOUS archetype）"""
    return generate_governor_profile("VIRTUOUS")


@pytest.fixture
def profile_balanced():
    """中庸知县属性"""
    return generate_governor_profile("MIDDLING")


class _FakeNeighbor:
    """最小化模拟 NeighborCounty 对象，供 _fallback_investment 使用"""
    def __init__(self, county_data, profile):
        self.county_data = county_data
        self.governor_name = "测知县"
        self.governor_bio = "测试用知县"
        self.governor_archetype = "MIDDLING"
        self.county_name = "测试县"
        county_data["governor_profile"] = profile


# ─────────────────────────────────────────────
# _term_penalty
# ─────────────────────────────────────────────

@pytest.mark.django_db(databases=[])
class TestTermPenalty:
    def test_no_penalty_when_months_left_sufficient(self):
        """任期充足（>12月）时不惩罚任何投资"""
        assert AIGovernorService._term_penalty("build_irrigation", 18) == 0
        assert AIGovernorService._term_penalty("expand_school", 15) == 0

    def test_heavy_penalty_when_build_exceeds_remaining(self):
        """水利工期8月，剩余任期<8月 → 重惩罚 -50"""
        # build_irrigation 工期8月，season=30 → months_left=6
        penalty = AIGovernorService._term_penalty("build_irrigation", 6)
        assert penalty == -50

    def test_moderate_penalty_when_build_is_half_remaining(self):
        """水利工期8月，剩余任期10月 → 工期占50%+ → 中惩罚 -20"""
        penalty = AIGovernorService._term_penalty("build_irrigation", 10)
        assert penalty == -20

    def test_no_penalty_for_instant_actions(self):
        """即时生效（hire_bailiffs/relief/build_granary）不受任期惩罚"""
        for action in ("hire_bailiffs", "relief", "build_granary"):
            assert AIGovernorService._term_penalty(action, 3) == 0

    def test_no_penalty_when_months_left_zero_but_instant(self):
        """最后一月，即时投资仍无惩罚"""
        assert AIGovernorService._term_penalty("hire_bailiffs", 0) == 0

    def test_irrigation_penalty_at_season_30(self):
        """第30月（剩余6月），水利惩罚 -50"""
        months_left = MAX_MONTH - 30
        penalty = AIGovernorService._term_penalty("build_irrigation", months_left)
        assert penalty == -50


# ─────────────────────────────────────────────
# _fallback_investment: 任期意识
# ─────────────────────────────────────────────

@pytest.mark.django_db(databases=[])
class TestFallbackInvestmentTermAwareness:
    def _run_fallback(self, county, profile, season):
        neighbor = _FakeNeighbor(county, profile)
        return AIGovernorService._fallback_investment(neighbor, county, season, profile)

    def test_irrigation_avoided_in_late_game(self, county, profile_balanced):
        """任期末（season=30，剩余6月），水利工期8月 → 规则引擎不应选水利"""
        # 确保资金充足且洪灾风险高（本来会优选水利）
        county["treasury"] = 500
        county["flood_risk"] = 0.8  # 高洪灾风险，本来水利得分很高
        county["morale"] = 60
        county["security"] = 60

        events = self._run_fallback(county, profile_balanced, season=30)

        # 事件中不应包含"水利"投资
        irrigation_events = [e for e in events if "水利" in e]
        assert not irrigation_events, f"晚期任期不应启动水利工程，但得到：{irrigation_events}"

    def test_irrigation_allowed_in_early_game(self, county, profile_welfare):
        """任期早期（season=2，剩余34月），洪灾风险高 → 可以启动水利"""
        county["treasury"] = 500
        county["flood_risk"] = 0.8
        county["morale"] = 60
        county["security"] = 60
        # 确保没有在建工程
        county.setdefault("active_investments", [])

        events = self._run_fallback(county, profile_welfare, season=2)

        # 早期应该有投资行为（不一定是水利，但不应完全不投）
        assert len(events) > 0 or county["treasury"] < 500  # 有投资或资金减少

    def test_no_investment_when_treasury_low(self, county, profile_balanced):
        """资金不足时（低于保守阈值），规则引擎不发起非紧急投资"""
        county["treasury"] = 50
        county["flood_risk"] = 0.8

        events = self._run_fallback(county, profile_balanced, season=5)

        # 没有灾害时，不应有任何投资
        non_relief = [e for e in events if "赈灾" not in e and "购粮" not in e]
        assert not non_relief


# ─────────────────────────────────────────────
# _append_memory: 记忆格式加厚
# ─────────────────────────────────────────────

@pytest.mark.django_db(databases=[])
class TestAppendMemory:
    def _run_memory(self, county, season, events):
        AIGovernorService._append_memory(county, season, events)
        profile = county.get("governor_profile", {})
        memory = profile.get("memory", [])
        return memory[-1] if memory else None

    def test_basic_fields_present(self, county):
        """记忆条目包含月份、税率、库存、民心、治安"""
        county["governor_profile"] = generate_governor_profile("MIDDLING")
        county["tax_rate"] = 0.12
        county["treasury"] = 300
        county["morale"] = 55
        county["security"] = 48

        entry = self._run_memory(county, season=3, events=[])
        assert entry is not None
        assert "税率12%" in entry
        assert "库300两" in entry
        assert "民心55" in entry
        assert "治安48" in entry

    def test_grain_shortage_tag(self, county):
        """粮储严重不足时，记忆包含【粮荒】标记"""
        county["governor_profile"] = generate_governor_profile("MIDDLING")
        county["peasant_grain_reserve"] = 1.0  # 极少

        entry = self._run_memory(county, season=5, events=[])
        assert "粮荒" in entry

    def test_grain_low_tag(self, county):
        """粮储偏低时，记忆包含【粮偏低】标记"""
        county["governor_profile"] = generate_governor_profile("MIDDLING")
        # 约1.5个月消耗量（偏低但未达粮荒）
        total_pop = sum(v.get("population", 0) for v in county.get("villages", []))
        monthly = total_pop * 300 / 12.0
        county["peasant_grain_reserve"] = monthly * 1.5

        entry = self._run_memory(county, season=5, events=[])
        assert "粮偏低" in entry

    def test_no_grain_tag_when_sufficient(self, county):
        """粮储充足时，记忆不含粮食警告标记"""
        county["governor_profile"] = generate_governor_profile("MIDDLING")
        total_pop = sum(v.get("population", 0) for v in county.get("villages", []))
        monthly = total_pop * 300 / 12.0
        county["peasant_grain_reserve"] = monthly * 3.0  # 充足

        entry = self._run_memory(county, season=5, events=[])
        assert "粮荒" not in entry
        assert "粮偏低" not in entry

    def test_disaster_tag(self, county):
        """有灾害时，记忆包含灾害类型标记"""
        county["governor_profile"] = generate_governor_profile("MIDDLING")
        county["disaster_this_year"] = {"type": "flood", "severity": 0.4, "relieved": False}

        entry = self._run_memory(county, season=7, events=[])
        assert "洪灾" in entry

    def test_plague_tag(self, county):
        """疫病灾害标记"""
        county["governor_profile"] = generate_governor_profile("MIDDLING")
        county["disaster_this_year"] = {"type": "plague", "severity": 0.3, "relieved": False}

        entry = self._run_memory(county, season=7, events=[])
        assert "疫病" in entry

    def test_population_increase_tag(self, county):
        """人口增加时，记忆含 人口↑"""
        county["governor_profile"] = generate_governor_profile("MIDDLING")
        total_pop = sum(v.get("population", 0) for v in county.get("villages", []))
        county["_pop_last_month"] = total_pop - 100  # 上月少100人

        entry = self._run_memory(county, season=8, events=[])
        assert "人口↑" in entry

    def test_population_decrease_tag(self, county):
        """人口减少时，记忆含 人口↓"""
        county["governor_profile"] = generate_governor_profile("MIDDLING")
        total_pop = sum(v.get("population", 0) for v in county.get("villages", []))
        county["_pop_last_month"] = total_pop + 200  # 上月多200人（即现在少了200）

        entry = self._run_memory(county, season=8, events=[])
        assert "人口↓" in entry

    def test_investment_extracted_from_events(self, county):
        """投资事件被提取到记忆条目"""
        county["governor_profile"] = generate_governor_profile("MIDDLING")
        events = ["测知县投资修缮道路，花费80两，预计第1年·三月完成"]

        entry = self._run_memory(county, season=1, events=events)
        assert "投资修缮道路" in entry

    def test_memory_capped_at_max(self, county):
        """记忆不超过 _MAX_MEMORY 条"""
        from game.services.ai_governor import _MAX_MEMORY
        county["governor_profile"] = generate_governor_profile("MIDDLING")

        for s in range(1, _MAX_MEMORY + 5):
            AIGovernorService._append_memory(county, s, [])

        memory = county["governor_profile"]["memory"]
        assert len(memory) == _MAX_MEMORY
