"""玩家状态访问层。"""

import copy


def load_player_state(game, refresh=False):
    """Load current player state from the canonical source as a deep copy."""
    if refresh:
        game.refresh_from_db()

    if game.player_unit_id:
        player_unit = game.player_unit
        if refresh:
            player_unit.refresh_from_db()
        return copy.deepcopy(player_unit.unit_data or {})

    return copy.deepcopy(game.county_data or {})


def load_county_state(game, refresh=False):
    """County-mode convenience alias for current player state."""
    return load_player_state(game, refresh=refresh)


def save_player_state(game, state, mirror_legacy=True):
    """Persist current player state using full-dict replacement."""
    payload = copy.deepcopy(state or {})

    if game.player_unit_id:
        player_unit = game.player_unit
        player_unit.unit_data = copy.deepcopy(payload)
        player_unit.save(update_fields=["unit_data"])

        if mirror_legacy and player_unit.unit_type == "COUNTY":
            game.county_data = copy.deepcopy(payload)
            game.save(update_fields=["county_data", "updated_at"])
            return payload

        game.save(update_fields=["updated_at"])
        return payload

    game.county_data = payload
    game.save(update_fields=["county_data", "updated_at"])
    return payload


def mutate_player_state(game, mutator, mirror_legacy=True):
    """Run a mutator against the current player state and persist it."""
    state = load_player_state(game)
    mutator(state)
    save_player_state(game, state, mirror_legacy=mirror_legacy)
    return state
