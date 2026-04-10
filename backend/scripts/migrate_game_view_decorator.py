"""One-shot rewriter: migrate the `GameState.objects.get(id=game_id, user=...)`
boilerplate in `game/views.py` to the `@game_view` decorator.

Run once:
    python scripts/migrate_game_view_decorator.py

This script is deliberately conservative:

* It only touches methods whose body starts with the exact boilerplate
  pattern (try/except, optional takeover guard, optional playable guard).
  Anything else — e.g. a method that also reads a different model first —
  is left alone and must be migrated by hand.
* It prints a report of (a) how many methods it migrated, (b) which
  method names they were on, and (c) how many `DoesNotExist` lines remain
  so we can spot-check the leftovers.

The transformation is intentionally textual rather than AST-based:
black-style AST rewriters mangle comments and blank lines, and views.py
has a lot of both.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VIEWS_PATH = Path(__file__).resolve().parent.parent / "game" / "views.py"

# ── regex building blocks ──────────────────────────────────────────

# Match a view method definition where game_id is the first URL kwarg.
# Capture:  indent, http method name, any leading args (comma-prefixed) before game_id
DEF_RE = re.compile(
    r"^(?P<indent> {4,})def (?P<method>get|post|put|patch|delete)"
    r"\(self, request, game_id(?P<tail>[^)]*)\):\n"
    r"(?P<rest>(?: {8,}.*\n|\n)*)",
    re.MULTILINE,
)

BOILERPLATE_RE = re.compile(
    r"^        try:\n"
    r"            game = GameState\.objects\.get\(id=game_id, user=request\.user\)\n"
    r"        except GameState\.DoesNotExist:\n"
    r"            return Response\(\{\"error\": \"游戏不存在\"\}, status=status\.HTTP_404_NOT_FOUND\)\n"
    r"(?P<after>(?:\n)?)",
)

TAKEOVER_RE = re.compile(
    r"^        blocked = _blocked_by_takeover\(game\)\n"
    r"        if blocked is not None:\n"
    r"            return blocked\n"
    r"(?:\n)?",
)

PLAYABLE_RE = re.compile(
    r"^        blocked = _check_game_playable\(game\)\n"
    r"        if blocked is not None:\n"
    r"            return blocked\n"
    r"(?:\n)?",
)


def migrate(source: str) -> tuple[str, list[str], int]:
    migrated_methods: list[str] = []

    def rewrite_method(match: re.Match) -> str:
        indent = match.group("indent")
        method = match.group("method")
        tail = match.group("tail") or ""  # e.g. ", npc_key" — extra URL kwargs
        rest = match.group("rest")

        boiler = BOILERPLATE_RE.match(rest)
        if not boiler:
            return match.group(0)  # leave untouched

        after_boiler = rest[boiler.end():]

        # Optional guards (order-independent, at most one of each).
        check_takeover = False
        check_playable = False
        while True:
            m = TAKEOVER_RE.match(after_boiler)
            if m and not check_takeover:
                check_takeover = True
                after_boiler = after_boiler[m.end():]
                continue
            m = PLAYABLE_RE.match(after_boiler)
            if m and not check_playable:
                check_playable = True
                after_boiler = after_boiler[m.end():]
                continue
            break

        # Build the decorator arg list.
        dec_args: list[str] = []
        if check_playable:
            dec_args.append("check_playable=True")
        if check_takeover:
            dec_args.append("check_takeover=True")
        dec_call = f"@game_view({', '.join(dec_args)})"

        # New signature: game is positional, game_id becomes kw-only for
        # callers/tests that still pass it explicitly.
        new_sig = (
            f"{indent}{dec_call}\n"
            f"{indent}def {method}(self, request, game{tail}, *, game_id):\n"
        )

        migrated_methods.append(method)
        # Reassemble: new signature + everything after the boilerplate/guards,
        # stripping any now-redundant leading blank line that previously
        # separated the try-block from the rest of the body.
        new_body = after_boiler.lstrip("\n")
        if not new_body:
            # Extremely short method whose entire body was boilerplate; keep a
            # `pass` so Python still parses it (shouldn't occur in practice).
            new_body = "        pass\n"
        return new_sig + new_body

    new_source = DEF_RE.sub(rewrite_method, source)
    remaining = new_source.count("GameState.DoesNotExist")
    return new_source, migrated_methods, remaining


def main() -> int:
    original = VIEWS_PATH.read_text()
    before = original.count("GameState.DoesNotExist")
    new_source, migrated, remaining = migrate(original)

    if new_source == original:
        print("No changes.")
        return 0

    VIEWS_PATH.write_text(new_source)
    print(f"Migrated {len(migrated)} methods ({before} → {remaining} remaining)")
    print(f"Methods rewritten: {', '.join(migrated)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
