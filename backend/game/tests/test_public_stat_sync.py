import pytest

from game.services.investment import InvestmentService
from game.services.settlement_metrics import MetricsMixin


@pytest.mark.django_db(databases=[])
def test_apply_county_stat_delta_updates_villages_by_default(county):
    county["morale"] = 40.0
    for village in county["villages"]:
        village["morale"] = 40.0

    actual = MetricsMixin.apply_county_stat_delta(county, "morale", 5.0)

    assert actual == pytest.approx(5.0)
    assert county["morale"] == pytest.approx(45.0)
    assert all(village["morale"] == pytest.approx(45.0) for village in county["villages"])


@pytest.mark.django_db(databases=[])
def test_build_granary_keeps_village_morale_in_sync(county):
    county["morale"] = 40.0
    county["has_granary"] = False
    for village in county["villages"]:
        village["morale"] = 40.0

    _, message = InvestmentService.apply_effects(county, "build_granary", season=8)

    assert county["morale"] == pytest.approx(45.0)
    assert all(village["morale"] == pytest.approx(45.0) for village in county["villages"])
    assert "民心+5.0" in message


@pytest.mark.django_db(databases=[])
def test_refresh_metric_report_lines_uses_final_county_values(county):
    county["morale"] = 53.0
    county["security"] = 47.0
    report = {
        "_metric_bases": {"morale": 40.0, "security": 50.0},
        "events": [
            "民心变化: +0.0 (当前: 40.0)",
            "治安变化: +0.0 (当前: 50.0)",
        ],
    }

    MetricsMixin.refresh_metric_report_lines(county, report)

    assert report["events"][0] == "民心变化: +13.0 (当前: 53.0)"
    assert report["events"][1] == "治安变化: -3.0 (当前: 47.0)"
    assert "_metric_bases" not in report
