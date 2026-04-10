"""宗族后生年度举荐辅助逻辑。"""

from __future__ import annotations

from ..models import Agent
from .constants import year_of


class ClanYouthService:
    """Normalize clan-youth nomination state against the current year."""

    @staticmethod
    def _parse_generated_season(attrs) -> int | None:
        try:
            value = int((attrs or {}).get("generated_season", 0) or 0)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @classmethod
    def is_current_year_youth(cls, attrs, current_season: int) -> bool:
        generated_season = cls._parse_generated_season(attrs)
        if generated_season is None:
            # Legacy fallback: if the save lacks generated_season, do not
            # silently hide the youth from the player.
            return True
        return year_of(generated_season) == year_of(current_season)

    @classmethod
    def is_active_nomination(cls, attrs, current_season: int) -> bool:
        return bool((attrs or {}).get("exam_eligible", False)) and cls.is_current_year_youth(
            attrs, current_season
        )

    @classmethod
    def normalize_attrs(cls, attrs, current_season: int):
        normalized = dict(attrs or {})
        changed = False
        if normalized.get("exam_eligible", False) and not cls.is_current_year_youth(
            normalized, current_season
        ):
            normalized["exam_eligible"] = False
            changed = True
        return normalized, changed

    @classmethod
    def normalize_game_nominations(cls, game, *, current_season: int | None = None) -> int:
        season = current_season or game.current_season
        youths = list(
            Agent.objects.filter(game=game, role="CLAN_YOUTH").only("id", "attributes")
        )
        to_update = []
        for youth in youths:
            attrs, changed = cls.normalize_attrs(youth.attributes, season)
            if not changed:
                continue
            youth.attributes = attrs
            to_update.append(youth)

        if to_update:
            Agent.objects.bulk_update(to_update, ["attributes"])
        return len(to_update)

    @classmethod
    def current_year_eligible_agents(cls, game):
        current_season = game.current_season
        return [
            youth
            for youth in Agent.objects.filter(game=game, role="CLAN_YOUTH").order_by("created_at")
            if cls.is_active_nomination(youth.attributes or {}, current_season)
        ]
