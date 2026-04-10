import pytest
from django.contrib.auth import get_user_model

from game.models import Agent, GameState, NegotiationSession
from game.services.negotiation import NegotiationService
from game.services.settlement import SettlementService
from game.services.state import load_county_state


def _build_county(village_name, gentry_id, *, active_investments):
    return {
        "treasury": 500,
        "price_index": 1.0,
        "morale": 50,
        "security": 50,
        "commercial": 50,
        "education": 50,
        "bailiff_level": 0,
        "school_level": 1,
        "irrigation_level": 0,
        "medical_level": 0,
        "admin_cost": 100,
        "admin_cost_detail": {
            "school_cost": 0,
            "irrigation_maint": 0,
            "medical_maint": 0,
            "bailiff_cost": 0,
        },
        "active_investments": active_investments,
        "villages": [
            {
                "name": village_name,
                "population": 1000,
                "farmland": 10000,
                "gentry_land_pct": 0.4,
                "hidden_land": 200,
                "land_ceiling": 13000,
                "morale": 50,
                "security": 50,
                "has_school": False,
                "peasant_ledger": {
                    "registered_population": 1000,
                    "farmland": 6000,
                    "grain_surplus": 0.0,
                    "monthly_consumption": 0.0,
                    "monthly_surplus": 0.0,
                },
                "gentry_ledger": {
                    "registered_population": 60,
                    "hidden_population": 0,
                    "registered_farmland": 4000,
                    "hidden_farmland": 200,
                    "grain_surplus": 0.0,
                },
            }
        ],
        "clans": {
            "测试府张氏": {
                "local_members": [gentry_id],
                "local_villages": [village_name],
                "local_power": 80,
                "official_members": [],
                "other_county_branches": 0,
                "total_influence": 80,
                "clan_affinity": 20,
                "power": 80,
            }
        },
    }


def _create_gentry(game, village_name):
    return Agent.objects.create(
        game=game,
        name="张员外",
        role="GENTRY",
        role_title="地主",
        tier="LIGHT",
        attributes={
            "player_affinity": 20,
            "village_name": village_name,
            "social_identity": {
                "clan_id": "测试府张氏",
                "surname": "张",
                "native_place": "测试府",
            },
        },
    )


@pytest.mark.django_db
def test_reclaim_land_completion_boosts_local_gentry_and_clan_affinity():
    user = get_user_model().objects.create_user(username="reclaim_rel_user", password="pw")
    village_name = "甲村"
    game = GameState.objects.create(user=user, current_season=2, county_data={})
    gentry = _create_gentry(game, village_name)
    game.county_data = _build_county(
        village_name,
        gentry.id,
        active_investments=[
            {
                "action": "reclaim_land",
                "target_village": village_name,
                "started_season": 1,
                "completion_season": 2,
                "description": "开垦荒地",
            }
        ],
    )
    game.save(update_fields=["county_data"])

    county = load_county_state(game, refresh=True)
    report = {"events": []}
    SettlementService._apply_completed_investments(county, season=2, report=report, game=game)

    gentry.refresh_from_db()
    assert gentry.attributes["player_affinity"] == 24
    assert county["clans"]["测试府张氏"]["clan_affinity"] == 24


@pytest.mark.django_db
def test_village_school_completion_boosts_local_gentry_and_clan_affinity():
    user = get_user_model().objects.create_user(username="school_rel_user", password="pw")
    village_name = "乙村"
    game = GameState.objects.create(user=user, current_season=4, county_data={})
    gentry = _create_gentry(game, village_name)
    game.county_data = _build_county(
        village_name,
        gentry.id,
        active_investments=[
            {
                "action": "fund_village_school",
                "target_village": village_name,
                "started_season": 1,
                "completion_season": 4,
                "description": "资助村塾",
            }
        ],
    )
    game.save(update_fields=["county_data"])

    county = load_county_state(game, refresh=True)
    report = {"events": []}
    SettlementService._apply_completed_investments(county, season=4, report=report, game=game)

    gentry.refresh_from_db()
    assert gentry.attributes["player_affinity"] == 23
    assert county["villages"][0]["has_school"] is True
    assert county["clans"]["测试府张氏"]["clan_affinity"] == 23


@pytest.mark.django_db
def test_irrigation_completion_offsets_part_of_contributor_affinity_loss():
    user = get_user_model().objects.create_user(username="irrigation_rel_user", password="pw")
    village_name = "丙村"
    game = GameState.objects.create(user=user, current_season=2, county_data={})
    gentry = _create_gentry(game, village_name)
    county = _build_county(
        village_name,
        gentry.id,
        active_investments=[
            {
                "action": "build_irrigation",
                "started_season": 1,
                "completion_season": 2,
                "description": "修建水利",
            }
        ],
    )
    game.county_data = county
    game.save(update_fields=["county_data"])

    session = NegotiationSession.objects.create(
        game=game,
        agent=gentry,
        event_type="IRRIGATION",
        status="resolved",
        current_round=1,
        max_rounds=6,
        season=1,
        context_data={
            "village_name": village_name,
            "base_cost": 100,
            "max_contribution": 40,
        },
        outcome={},
    )

    NegotiationService._apply_irrigation_outcome(
        session,
        {"final_decision": "accept", "contribution_offer": 20},
    )

    gentry.refresh_from_db()
    assert gentry.attributes["player_affinity"] == 17

    county_after_negotiation = load_county_state(game, refresh=True)
    report = {"events": []}
    SettlementService._apply_completed_investments(
        county_after_negotiation,
        season=2,
        report=report,
        game=game,
    )

    gentry.refresh_from_db()
    assert gentry.attributes["player_affinity"] == 20
    assert county_after_negotiation["clans"]["测试府张氏"]["clan_affinity"] == 20
    assert any("观感回升" in event for event in report["events"])
