"""
Tests for AI知府 improvements:
  - _append_quarterly_memory: 季度记忆快照
  - _apply_decision: 月度笔记带县情快照
  - judicial_caseflow.auto_review_county_by_prefect: 知府司法复审
"""

import pytest

from game.services.ai_prefect import PrefectAIService, _MAX_MEMORY
from game.services.constants import generate_governor_profile
from game.services.county import CountyService
from game.services.judicial_caseflow import JudicialCaseflowService


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def county():
    return CountyService.create_initial_county(county_type="fiscal_core")


class _FakePrefect:
    """最小化知府 Agent 模拟（无 DB）。"""
    def __init__(self, attrs=None):
        self.attributes = attrs or {
            'player_affinity': 50,
            'evaluation_notes': [],
            'memory': [],
            'personality': {'openness': 0.5, 'conscientiousness': 0.7, 'agreeableness': 0.5},
            'ideology': {'reform_vs_tradition': 0.5, 'people_vs_authority': 0.6, 'pragmatic_vs_idealist': 0.5},
        }
        self._saved = False

    def save(self, update_fields=None):
        self._saved = True


# ─────────────────────────────────────────────
# _append_quarterly_memory
# ─────────────────────────────────────────────

@pytest.mark.django_db(databases=[])
class TestAppendQuarterlyMemory:
    def test_writes_entry_at_month_3(self, county):
        prefect = _FakePrefect()
        county['morale'] = 65
        county['security'] = 48
        county['annual_quota'] = {'agricultural': 350, 'corvee': 50, 'total': 400}
        county['fiscal_year'] = {'agri_remitted': 0, 'corvee_tax': 0, 'corvee_retained': 0}

        # month=3 → moy=3 → Q1
        PrefectAIService._append_quarterly_memory(prefect, county, month=3)

        memory = prefect.attributes['memory']
        assert len(memory) == 1
        assert '一季度' in memory[0]
        assert '民心' in memory[0]
        assert '治安' in memory[0]
        assert prefect._saved

    def test_writes_entry_at_month_6(self, county):
        prefect = _FakePrefect()
        county['annual_quota'] = {'agricultural': 250, 'corvee': 50, 'total': 300}
        county['fiscal_year'] = {'agri_remitted': 150, 'corvee_tax': 0, 'corvee_retained': 0}

        PrefectAIService._append_quarterly_memory(prefect, county, month=6)

        memory = prefect.attributes['memory']
        assert '二季度' in memory[0]
        assert '50%' in memory[0]  # 150/300

    def test_writes_entry_at_month_9(self, county):
        prefect = _FakePrefect()
        PrefectAIService._append_quarterly_memory(prefect, county, month=9)
        assert '三季度' in prefect.attributes['memory'][0]

    def test_directive_type_included_when_present(self, county):
        prefect = _FakePrefect()
        county['prefect_directives'] = [{'directive_type': '催科'}]
        PrefectAIService._append_quarterly_memory(prefect, county, month=3)
        assert '催科' in prefect.attributes['memory'][0]

    def test_complaints_included_when_nonzero(self, county):
        prefect = _FakePrefect()
        county['prefect_complaints'] = 3
        PrefectAIService._append_quarterly_memory(prefect, county, month=6)
        assert '陈情3件' in prefect.attributes['memory'][0]

    def test_memory_capped_at_max(self, county):
        prefect = _FakePrefect()
        # 预填满记忆
        prefect.attributes['memory'] = [f'旧记忆{i}' for i in range(_MAX_MEMORY)]
        PrefectAIService._append_quarterly_memory(prefect, county, month=3)
        assert len(prefect.attributes['memory']) == _MAX_MEMORY
        assert '一季度' in prefect.attributes['memory'][-1]

    def test_no_quota_graceful(self, county):
        """配额未下达时不报错，格式正常。"""
        prefect = _FakePrefect()
        county['annual_quota'] = {}
        PrefectAIService._append_quarterly_memory(prefect, county, month=3)
        memory = prefect.attributes['memory']
        assert len(memory) == 1
        assert '配额未定' in memory[0]


# ─────────────────────────────────────────────
# _apply_decision: monthly note enrichment
# ─────────────────────────────────────────────

@pytest.mark.django_db(databases=[])
class TestApplyDecisionEnrichment:
    def _make_decision(self, action_type='memo_only', memo='测试笔记', directive_type='', directive_text=''):
        return {
            'action': {
                'type': action_type,
                'affinity_delta': 0,
                'memo_entry': memo,
                'directive_type': directive_type,
                'directive_text': directive_text,
            }
        }

    def test_memo_includes_county_snapshot(self, county):
        """月度笔记应包含民心和治安档位描述。"""
        prefect = _FakePrefect()
        county['morale'] = 70
        county['security'] = 55

        class _FakeGame:
            player_role = 'COUNTY_MAGISTRATE'

        from unittest.mock import patch, MagicMock
        with patch('game.models.EventLog.objects.create'):
            PrefectAIService._apply_decision(
                prefect, county, month=4,
                decision=self._make_decision(),
                report={'events': []},
                game=_FakeGame(),
            )

        notes = prefect.attributes['evaluation_notes']
        assert len(notes) == 1
        assert '民心' in notes[0]
        assert '治安' in notes[0]

    def test_empty_memo_gets_default_text(self, county):
        """memo_entry 为空时，仍然追加含县情的默认笔记。"""
        prefect = _FakePrefect()

        class _FakeGame:
            player_role = 'COUNTY_MAGISTRATE'

        from unittest.mock import patch
        with patch('game.models.EventLog.objects.create'):
            PrefectAIService._apply_decision(
                prefect, county, month=2,
                decision=self._make_decision(memo=''),
                report={'events': []},
                game=_FakeGame(),
            )

        notes = prefect.attributes['evaluation_notes']
        assert len(notes) == 1
        assert '例行观察' in notes[0] or '民心' in notes[0]


# ─────────────────────────────────────────────
# _pick_prefect_verdict_code
# ─────────────────────────────────────────────

@pytest.mark.django_db(databases=[])
class TestPickPrefectVerdictCode:
    _verdict_options = [
        {'verdict_code': 'CONVICT_HEAVY', 'verdict_label': '重判'},
        {'verdict_code': 'CONVICT_LIGHT', 'verdict_label': '轻判'},
        {'verdict_code': 'MEDIATION', 'verdict_label': '调解'},
        {'verdict_code': 'INSUFFICIENT_EVIDENCE', 'verdict_label': '证据不足'},
    ]

    def test_people_focused_prefect_prefers_conviction(self):
        attrs = {
            'ideology': {'people_vs_authority': 0.8},
            'personality': {'conscientiousness': 0.5},
        }
        factors = {'evidence_doubt': 0.2}
        code = JudicialCaseflowService._pick_prefect_verdict_code(
            self._verdict_options, attrs, factors
        )
        assert code == 'CONVICT_HEAVY'

    def test_authority_focused_prefect_prefers_mediation(self):
        attrs = {
            'ideology': {'people_vs_authority': 0.2},
            'personality': {'conscientiousness': 0.5},
        }
        factors = {'evidence_doubt': 0.2}
        code = JudicialCaseflowService._pick_prefect_verdict_code(
            self._verdict_options, attrs, factors
        )
        assert code == 'MEDIATION'

    def test_high_doubt_conscientious_prefect_picks_insufficient_evidence(self):
        attrs = {
            'ideology': {'people_vs_authority': 0.5},
            'personality': {'conscientiousness': 0.8},
        }
        factors = {'evidence_doubt': 0.65}
        code = JudicialCaseflowService._pick_prefect_verdict_code(
            self._verdict_options, attrs, factors
        )
        assert code == 'INSUFFICIENT_EVIDENCE'

    def test_empty_options_returns_none(self):
        attrs = {'ideology': {}, 'personality': {}}
        code = JudicialCaseflowService._pick_prefect_verdict_code([], attrs, {})
        assert code is None


# ─────────────────────────────────────────────
# _apply_verdict_effects_to_county_dict
# ─────────────────────────────────────────────

@pytest.mark.django_db(databases=[])
class TestApplyVerdictEffectsToCountyDict:
    """morale/security 走 Model A（apply_county_stat_delta → 各村 → 聚合），
    所以要在村级设置初值，不能直接改 county['morale']。
    """

    @staticmethod
    def _set_all_village_field(county, field, value):
        for v in county.get('villages', []):
            v[field] = value
        # 聚合回县级（简单平均足够，测试只关心 delta 方向/clamp）
        county[field] = float(value)

    def test_applies_morale_delta(self, county):
        # 司法判决对 morale 单次最大 ±2（见 _clamp_morale_security / _DEFAULT_CLAMPS），
        # 故 +8 会被截断为 +2。
        self._set_all_village_field(county, 'morale', 50)
        option = {'immediate_effects': {'morale': 8}}
        JudicialCaseflowService._apply_verdict_effects_to_county_dict(county, option)
        assert county['morale'] == 52

    def test_applies_treasury_delta(self, county):
        county['treasury'] = 200
        option = {'immediate_effects': {'treasury': -50}}
        JudicialCaseflowService._apply_verdict_effects_to_county_dict(county, option)
        assert county['treasury'] == 150

    def test_clamps_morale_at_100(self, county):
        self._set_all_village_field(county, 'morale', 98)
        option = {'immediate_effects': {'morale': 10}}
        JudicialCaseflowService._apply_verdict_effects_to_county_dict(county, option)
        assert county['morale'] == 100

    def test_no_effects_no_change(self, county):
        self._set_all_village_field(county, 'morale', 55)
        option = {}
        JudicialCaseflowService._apply_verdict_effects_to_county_dict(county, option)
        assert county['morale'] == 55
