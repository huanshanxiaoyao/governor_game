"""Shared view decorators.

The ``game_view`` decorator kills the most common piece of boilerplate in
``views.py`` — a 5-line try/except block repeated 48+ times:

    try:
        game = GameState.objects.get(id=game_id, user=request.user)
    except GameState.DoesNotExist:
        return Response({"error": "游戏不存在"}, status=status.HTTP_404_NOT_FOUND)

With the decorator, the same view shrinks to:

    @game_view(check_playable=True)
    def post(self, request, game, *, game_id):
        ...

Why keep ``game_id`` in the signature too? Django's URL resolver passes it
as a kwarg; the decorator pops it, does the lookup, then injects ``game``
alongside. Declaring both makes the view's contract explicit and keeps
existing tests that call ``view(request, game_id=...)`` working.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Optional

from django.db.models import QuerySet
from rest_framework import status
from rest_framework.response import Response

from .models import GameState


def _lookup_game(request, game_id: int, *, select_related: Optional[tuple[str, ...]] = None):
    qs: QuerySet[GameState] = GameState.objects.filter(
        id=game_id, user=request.user,
    )
    if select_related:
        qs = qs.select_related(*select_related)
    return qs.first()


def game_view(
    *,
    check_playable: bool = False,
    check_takeover: bool = False,
    select_related: Optional[tuple[str, ...]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap an APIView method so the common game lookup/guard is automatic.

    Parameters
    ----------
    check_playable:
        If True, invokes ``_check_game_playable`` (term-end / dismissal /
        season-overflow guard) before calling the handler. Use this on any
        mutating endpoint that advances time or burns resources.

    check_takeover:
        If True, invokes ``_blocked_by_takeover`` (emergency/riot governance
        block) before calling the handler. Use this on endpoints that the
        player should not be able to trigger while the county is in a
        takeover/emergency state.

    select_related:
        Tuple of ORM ``select_related`` arguments. Defaults to ``None``.
        Set this when the handler reads adjacent FKs (e.g. ``player`` or
        ``player_unit``) so the lookup doesn't stack extra queries.

    Notes
    -----
    * The wrapped method MUST accept ``game`` as a positional parameter
      after ``request``. ``game_id`` remains in the signature as a
      keyword-only argument for clarity and tests.
    * Guard imports from views.py are done lazily to avoid a circular
      import (views.py imports this module).
    """

    def decorator(view_method: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view_method)
        def wrapper(self, request, *args, **kwargs):
            # Django's URL resolver puts path converters in kwargs.
            game_id = kwargs.pop("game_id", None)
            if game_id is None and args:
                # Positional fallback for legacy call sites / tests.
                game_id, *rest = args
                args = tuple(rest)

            game = _lookup_game(request, game_id, select_related=select_related)
            if game is None:
                return Response(
                    {"error": "游戏不存在"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if check_takeover:
                from .views import _blocked_by_takeover  # lazy: circular
                blocked = _blocked_by_takeover(game)
                if blocked is not None:
                    return blocked

            if check_playable:
                from .views import _check_game_playable  # lazy: circular
                not_playable = _check_game_playable(game)
                if not_playable is not None:
                    return not_playable

            return view_method(self, request, game, *args, game_id=game_id, **kwargs)

        return wrapper

    return decorator


__all__ = ["game_view"]
