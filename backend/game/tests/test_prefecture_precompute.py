from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from game.models import AdminUnit, GameState, NeighborPrecompute
from game.services.prefecture import PrefectureService


def _build_prefecture_game():
    user = get_user_model().objects.create_user(username="pref_precompute_u", password="pw")
    game = GameState.objects.create(
        user=user,
        current_season=1,
        county_data={},
        player_role="PREFECT",
    )
    prefecture = AdminUnit.objects.create(
        game=game,
        unit_type="PREFECTURE",
        is_player_controlled=True,
        unit_data={
            "prefecture_name": "苏州府",
            "prefecture_type_name": "财赋重府",
            "treasury": 800,
            "treasury_collected": 0,
            "annual_quota": 0,
            "quota_assignments": {},
            "inspection_used": {"tongpan": 0, "tuiguan": 0},
            "school_level": 0,
            "road_level": 0,
            "granary": False,
            "river_work_level": 0,
            "year_end_review_pending": False,
            "exam_pending": False,
            "pending_events": [],
            "construction_queue": [],
            "talent_pool": [],
            "exam_results": [],
            "total_disciples": 0,
        },
    )
    game.player_unit = prefecture
    game.save(update_fields=["player_unit"])

    for idx in range(2):
        AdminUnit.objects.create(
            game=game,
            unit_type="COUNTY",
            parent=prefecture,
            unit_data={
                "county_name": f"测试县{idx + 1}",
                "governor_profile": {
                    "name": f"知县{idx + 1}",
                    "style": "minben",
                    "archetype": "VIRTUOUS",
                    "bio": "",
                },
                "villages": [],
                "markets": [],
                "fiscal_year": {
                    "commercial_tax": 0,
                    "commercial_retained": 0,
                    "corvee_tax": 0,
                    "corvee_retained": 0,
                    "agri_tax": 0,
                    "agri_remitted": 0,
                },
                "subordinate_reports": [],
                "morale": 50,
                "security": 50,
                "commercial": 50,
                "education": 50,
                "treasury": 100,
                "tax_rate": 0.12,
                "commercial_tax_rate": 0.03,
            },
        )

    return game


@pytest.mark.django_db
@patch("game.services.prefecture.AIGovernorService.make_decisions", return_value=["测试施政"])
def test_prefecture_precompute_persists_results(_mock_decisions):
    game = _build_prefecture_game()

    PrefectureService.precompute_ai_decisions(game.id, game.current_season)

    precompute = NeighborPrecompute.objects.get(game=game)
    assert precompute.status == "done"
    assert precompute.season == 1
    assert len(precompute.results) == 2
    assert all(entry["events"] == ["测试施政"] for entry in precompute.results.values())

    status = PrefectureService.get_precompute_status(game.id, game.current_season)
    assert status["status"] == "done"
    assert status["completed_count"] == 2


@pytest.mark.django_db
@patch("game.services.prefecture.SettlementService.settle_county", return_value=None)
@patch("game.services.prefecture.AIGovernorService.make_decisions", return_value=["缓存施政"])
def test_advance_month_uses_prefecture_precompute(_mock_decisions, _mock_settle):
    game = _build_prefecture_game()

    PrefectureService.precompute_ai_decisions(game.id, game.current_season)

    with patch.object(PrefectureService, "_compute_ai_decisions", side_effect=AssertionError("should not compute")):
        result = PrefectureService.advance_month(game)

    assert result["season"] == 1
    assert not NeighborPrecompute.objects.filter(game=game).exists()


@pytest.mark.django_db
@patch("game.services.prefecture.AIGovernorService.make_decisions", side_effect=RuntimeError("boom"))
def test_prefecture_precompute_failure_clears_cache(_mock_decisions):
    game = _build_prefecture_game()

    PrefectureService.precompute_ai_decisions(game.id, game.current_season)

    assert not NeighborPrecompute.objects.filter(game=game).exists()
