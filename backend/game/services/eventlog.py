"""EventLog and player-profile audit helpers."""

from __future__ import annotations

from ..models import EventLog, PlayerProfile


PLAYER_STAT_LABELS = {
    "competence": "能名",
    "integrity": "清名",
    "popularity": "人缘",
    "authority": "威名",
}


def log_game_event(
    game,
    *,
    event_type: str,
    category: str = "SYSTEM",
    description: str = "",
    data: dict | None = None,
    choice: str = "",
    season: int | None = None,
):
    return EventLog.objects.create(
        game=game,
        season=season if season is not None else game.current_season,
        event_type=event_type,
        category=category,
        description=description,
        choice=choice or "",
        data=data or {},
    )


def adjust_player_profile_stat(
    game,
    field: str,
    delta: int,
    *,
    category: str = "PROFILE",
    source_event: str,
    source_label: str = "",
    extra_data: dict | None = None,
) -> int:
    if not delta:
        return 0

    player = PlayerProfile.objects.filter(game=game).first()
    if player is None:
        return 0

    old_value = int(getattr(player, field, 0) or 0)
    new_value = max(0, min(100, old_value + int(delta)))
    actual_delta = new_value - old_value
    if not actual_delta:
        return 0

    setattr(player, field, new_value)
    player.save(update_fields=[field, "updated_at"])

    label = PLAYER_STAT_LABELS.get(field, field)
    sign = "+" if actual_delta > 0 else ""
    prefix = f"【{source_label}】" if source_label else ""
    log_game_event(
        game,
        event_type=f"player_{field}_changed",
        category=category,
        description=f"{prefix}{label}{sign}{actual_delta}，当前{label}{new_value}",
        data={
            "field": field,
            "label": label,
            "old_value": old_value,
            "delta": actual_delta,
            "new_value": new_value,
            "source_event": source_event,
            **(extra_data or {}),
        },
    )
    return actual_delta
