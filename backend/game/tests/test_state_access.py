import copy
import uuid

import pytest
from django.contrib.auth import get_user_model

from game.models import AdminUnit, GameState
from game.serializers import GameDetailSerializer
from game.services import CountyService
from game.services.state import load_county_state, save_player_state


def _create_user(prefix="state"):
    return get_user_model().objects.create_user(
        username=f"{prefix}_{uuid.uuid4().hex[:8]}",
        password="pw",
    )


@pytest.mark.django_db
def test_save_player_state_syncs_county_game_player_unit_and_legacy_county_data():
    user = _create_user("county")
    county_data = CountyService.create_initial_county()
    county_data["treasury"] = 120
    game = GameState.objects.create(
        user=user,
        current_season=1,
        county_data=copy.deepcopy(county_data),
        player_role="COUNTY_MAGISTRATE",
    )
    player_unit = AdminUnit.objects.create(
        game=game,
        unit_type="COUNTY",
        is_player_controlled=True,
        unit_data=copy.deepcopy(county_data),
    )
    game.player_unit = player_unit
    game.save(update_fields=["player_unit"])

    state = load_county_state(game)
    state["treasury"] = 345
    save_player_state(game, state)

    game.refresh_from_db()
    player_unit.refresh_from_db()

    assert player_unit.unit_data["treasury"] == 345
    assert game.county_data["treasury"] == 345


@pytest.mark.django_db
def test_save_player_state_prefecture_mode_does_not_overwrite_legacy_county_data():
    user = _create_user("pref")
    game = GameState.objects.create(
        user=user,
        current_season=1,
        county_data={"treasury": 11, "legacy": True},
        player_role="PREFECT",
    )
    player_unit = AdminUnit.objects.create(
        game=game,
        unit_type="PREFECTURE",
        is_player_controlled=True,
        unit_data={"prefecture_name": "应天府", "treasury": 500},
    )
    game.player_unit = player_unit
    game.save(update_fields=["player_unit"])

    state = load_county_state(game)
    state["treasury"] = 680
    save_player_state(game, state)

    game.refresh_from_db()
    player_unit.refresh_from_db()

    assert player_unit.unit_data["treasury"] == 680
    assert game.county_data == {"treasury": 11, "legacy": True}


@pytest.mark.django_db
def test_save_player_state_falls_back_to_county_data_for_legacy_save():
    user = _create_user("legacy")
    game = GameState.objects.create(
        user=user,
        current_season=1,
        county_data={"county_name": "旧档县", "treasury": 88},
        player_role="COUNTY_MAGISTRATE",
    )

    state = load_county_state(game)
    state["treasury"] = 99
    save_player_state(game, state)

    game.refresh_from_db()
    assert game.county_data["treasury"] == 99


@pytest.mark.django_db
def test_game_detail_serializer_reads_canonical_player_state():
    user = _create_user("serializer")
    county_data = CountyService.create_initial_county()
    legacy_data = copy.deepcopy(county_data)
    legacy_data["treasury"] = 10
    unit_data = copy.deepcopy(county_data)
    unit_data["treasury"] = 90

    game = GameState.objects.create(
        user=user,
        current_season=1,
        county_data=legacy_data,
        player_role="COUNTY_MAGISTRATE",
    )
    player_unit = AdminUnit.objects.create(
        game=game,
        unit_type="COUNTY",
        is_player_controlled=True,
        unit_data=unit_data,
    )
    game.player_unit = player_unit
    game.save(update_fields=["player_unit"])

    player_unit.unit_data = copy.deepcopy(unit_data)
    player_unit.save(update_fields=["unit_data"])

    data = GameDetailSerializer(game).data

    assert data["county_data"]["treasury"] == 90
