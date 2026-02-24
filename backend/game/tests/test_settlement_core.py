"""Core settlement tests using settle_county() (pure data, no DB)."""

import pytest

from game.services.constants import MAX_MONTH, month_of_year
from game.services.settlement import SettlementService


@pytest.mark.django_db(databases=[])
class TestSettlement36MonthSmoke:
    """36-month smoke test — advance through all months, verify basic sanity."""

    def test_smoke_36_months(self, county):
        """Advance 36 months, verify positive population, non-negative treasury bounds."""
        for month in range(1, MAX_MONTH + 1):
            report = {"season": month, "events": []}
            SettlementService.settle_county(county, month, report)

        total_pop = sum(v["population"] for v in county["villages"])
        assert total_pop > 0, "Population should remain positive after 36 months"
        assert county["morale"] >= 0
        assert county["morale"] <= 100
        assert county["security"] >= 0
        assert county["security"] <= 100
        assert county["commercial"] >= 0
        assert county["commercial"] <= 100
        assert county["education"] >= 0
        assert county["education"] <= 100


@pytest.mark.django_db(databases=[])
class TestSeasonalTriggers:
    """Verify seasonal events fire at the correct months."""

    def test_autumn_produces_report(self, county):
        """Month 9 should produce report['autumn']."""
        # Advance months 1-9
        for month in range(1, 10):
            report = {"season": month, "events": []}
            SettlementService.settle_county(county, month, report)
        assert "autumn" in report, "Month 9 should produce autumn report"
        assert "total_agri_output" in report["autumn"]

    def test_winter_produces_snapshot(self, county):
        """Month 12 should produce report['winter_snapshot']."""
        for month in range(1, 13):
            report = {"season": month, "events": []}
            SettlementService.settle_county(county, month, report)
        assert "winter_snapshot" in report, "Month 12 should produce winter snapshot"
        assert "total_population" in report["winter_snapshot"]

    def test_fiscal_year_reset_at_january(self, county):
        """Month 1 (and 13) should reset fiscal_year counters."""
        # Advance through year 1 to accumulate fiscal data
        for month in range(1, 13):
            report = {"season": month, "events": []}
            SettlementService.settle_county(county, month, report)

        fy_year1 = dict(county.get("fiscal_year", {}))

        # Month 13 (year 2 January): should reset first, then accumulate month-13 values.
        report = {"season": 13, "events": []}
        SettlementService.settle_county(county, 13, report)
        fy = county["fiscal_year"]

        # Reset signal should appear in report events.
        assert any("财政年度重置" in evt for evt in report["events"])

        # If reset failed, month 13 values would continue growing from year-1 totals.
        assert fy["commercial_tax"] <= fy_year1["commercial_tax"]
        assert fy["commercial_retained"] <= fy_year1["commercial_retained"]
        assert fy["corvee_tax"] <= fy_year1["corvee_tax"]
        assert fy["corvee_retained"] <= fy_year1["corvee_retained"]


@pytest.mark.django_db(databases=[])
class TestMetricBounds:
    """Metrics should stay within [0, 100] across all 36 months."""

    def test_metrics_bounded(self, county):
        for month in range(1, MAX_MONTH + 1):
            report = {"season": month, "events": []}
            SettlementService.settle_county(county, month, report)

            assert 0 <= county["morale"] <= 100, f"morale out of bounds at month {month}"
            assert 0 <= county["security"] <= 100, f"security out of bounds at month {month}"
            assert 0 <= county["commercial"] <= 100, f"commercial out of bounds at month {month}"
            assert 0 <= county["education"] <= 100, f"education out of bounds at month {month}"


@pytest.mark.django_db(databases=[])
class TestCountyTypes:
    """Ensure all county types can be created and settled without errors."""

    @pytest.mark.parametrize("county_type", [
        "fiscal_core", "clan_governance", "coastal", "disaster_prone",
    ])
    def test_county_type_settles(self, county_type):
        from game.services.county import CountyService
        county = CountyService.create_initial_county(county_type=county_type)
        for month in range(1, 13):
            report = {"season": month, "events": []}
            SettlementService.settle_county(county, month, report)
        total_pop = sum(v["population"] for v in county["villages"])
        assert total_pop > 0
