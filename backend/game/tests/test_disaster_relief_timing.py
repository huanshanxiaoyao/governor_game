"""Disaster relief timing: September submission, October decision/payment."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from game.models import GameState, PlayerProfile
from game.services.county import CountyService
from game.services.settlement import SettlementService


def _create_game_with_disaster(month):
    username = f"u_relief_{uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, password="pw")
    county = CountyService.create_initial_county(county_type="disaster_prone")
    county["disaster_this_year"] = {
        "type": "flood",
        "severity": 0.4,
        "relieved": False,
    }
    game = GameState.objects.create(user=user, current_season=month, county_data=county)
    PlayerProfile.objects.create(game=game, background="HUMBLE")
    return game


@pytest.mark.django_db
def test_disaster_relief_submission_requires_september_and_allows_only_once():
    game = _create_game_with_disaster(month=8)

    res = SettlementService.process_disaster_relief(game, 120)
    assert res["success"] is False
    assert "九月" in res["error"]

    game.current_season = 9
    game.save(update_fields=["current_season"])

    submitted = SettlementService.process_disaster_relief(game, 120)
    assert submitted["success"] is True
    assert submitted["pending_review"] is True

    game.refresh_from_db()
    app = game.county_data.get("relief_application") or {}
    assert app.get("status") == "PENDING"
    assert app.get("claimed_loss") == 120.0

    second = SettlementService.process_disaster_relief(game, 80)
    assert second["success"] is False
    assert "已提交" in second["error"]


@pytest.mark.django_db(databases=[])
def test_september_assessment_defers_agri_remit_to_october(county):
    county["disaster_this_year"] = None
    report = {"season": 9, "events": []}

    SettlementService.settle_county(county, 9, report)

    fy = county.get("fiscal_year", {})
    autumn = report.get("autumn") or {}
    assessment = county.get("autumn_tax_assessment") or {}

    assert autumn.get("payment_pending") is True
    assert fy.get("agri_tax", 0) > 0
    assert fy.get("agri_remitted", 0) == 0
    assert assessment.get("status") == "PENDING_PAYMENT"
    assert assessment.get("payment_month_of_year") == 10
    assert autumn.get("net_treasury_change") == -county.get("admin_cost")


@pytest.mark.django_db(databases=[])
def test_october_collects_deferred_agri_payment_without_relief(county):
    county["disaster_this_year"] = None
    report9 = {"season": 9, "events": []}
    SettlementService.settle_county(county, 9, report9)

    due = float((county.get("autumn_tax_assessment") or {}).get("agri_remit_due", 0))
    tax = float((county.get("autumn_tax_assessment") or {}).get("agri_tax", 0))

    report10 = {"season": 10, "events": []}
    SettlementService.settle_county(county, 10, report10)

    payment = report10.get("autumn_payment") or {}
    assert payment
    assert payment.get("agri_remit_due") == round(due, 1)
    assert payment.get("agri_remit_final") == round(due, 1)
    assert payment.get("net_treasury_change") == round(tax - due, 1)
    assert county.get("fiscal_year", {}).get("agri_remitted") == round(due, 1)
    assert (county.get("autumn_tax_assessment") or {}).get("status") == "PAID"


@pytest.mark.django_db(databases=[])
def test_october_review_applies_relief_deduction_and_is_not_repeatable(county):
    county["disaster_this_year"] = {
        "type": "flood",
        "severity": 0.4,
        "relieved": False,
    }
    county["annual_quota"] = {
        "year": 1,
        "agricultural": 500.0,
        "corvee": 300.0,
        "total": 800.0,
    }
    county["fiscal_year"] = {
        "commercial_tax": 0.0,
        "commercial_retained": 0.0,
        "corvee_tax": 0.0,
        "corvee_retained": 0.0,
        "agri_tax": 0.0,
        "agri_remitted": 0.0,
    }
    county["autumn_tax_assessment"] = {
        "year": 1,
        "agri_tax": 200.0,
        "agri_remit_due": 120.0,
        "agri_retained_due": 80.0,
        "status": "PENDING_PAYMENT",
        "payment_month_of_year": 10,
    }
    county["relief_application_submitted"] = True
    county["relief_application"] = {
        "year": 1,
        "status": "PENDING",
        "claimed_loss": 80.0,
        "submitted_season": 9,
    }

    report = {"season": 10, "events": []}
    with patch("game.services.settlement.random.random", return_value=0.0):
        SettlementService._process_october_agri_payment(county, 10, report, game=None)

    payment = report.get("autumn_payment") or {}
    assert payment.get("relief_deduction", 0) > 0
    assert payment.get("agri_remit_final", 0) < 120.0
    assert payment.get("agri_retained_final", 0) > 80.0
    assert (county.get("relief_application") or {}).get("status") == "APPROVED"
    assert (county.get("autumn_tax_assessment") or {}).get("status") == "PAID"

    report_again = {"season": 10, "events": []}
    SettlementService._process_october_agri_payment(county, 10, report_again, game=None)
    assert "autumn_payment" not in report_again


@pytest.mark.django_db(databases=[])
def test_severe_disaster_uses_partial_approval_instead_of_full_denial(county):
    county["disaster_this_year"] = {
        "type": "flood",
        "severity": 0.8,
        "relieved": False,
    }
    county["villages"] = [
        {"name": "甲村", "population": 1000, "farmland": 10000, "gentry_land_pct": 0.3},
    ]
    county["environment"] = {"agriculture_suitability": 1.0}
    county["irrigation_level"] = 0
    county["tax_rate"] = 0.12
    county["morale"] = 50
    county["remit_ratio"] = 0.65
    county["annual_quota"] = {
        "year": 1,
        "agricultural": 500.0,
        "corvee": 300.0,
        "total": 800.0,
    }
    county["fiscal_year"] = {
        "commercial_tax": 0.0,
        "commercial_retained": 0.0,
        "corvee_tax": 0.0,
        "corvee_retained": 0.0,
        "agri_tax": 0.0,
        "agri_remitted": 0.0,
    }
    county["autumn_tax_assessment"] = {
        "year": 1,
        "agri_tax": 300.0,
        "agri_remit_due": 180.0,
        "agri_retained_due": 120.0,
        "status": "PENDING_PAYMENT",
        "payment_month_of_year": 10,
    }
    county["relief_application_submitted"] = True
    county["relief_application"] = {
        "year": 1,
        "status": "PENDING",
        "claimed_loss": 100.0,
        "submitted_season": 9,
    }

    report = {"season": 10, "events": []}
    # random.random=0.99 makes full-approval branch fail; severe灾情仍应走部分批准
    with patch("game.services.settlement.random.random", return_value=0.99):
        SettlementService._process_october_agri_payment(county, 10, report, game=None)

    app = county.get("relief_application") or {}
    payment = report.get("autumn_payment") or {}
    assert app.get("status") == "PARTIAL_APPROVED"
    assert payment.get("relief_deduction", 0) > 0
    assert payment.get("agri_remit_final", 0) < 180.0
