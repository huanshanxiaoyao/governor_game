# Governor Game (知县模拟器)

A historical strategy game where the player governs a Chinese county as a magistrate (知县). Built with Django + vanilla JS frontend, runs in Docker.

**Early stage project** — architecture, tech stack, and conventions are all subject to change. Don't assume current patterns are permanent; ask before enforcing consistency with existing code if a better approach exists.

## Architecture

- **Runtime**: Docker Compose (postgres:15, redis:7, Django dev server)
- **Backend**: Django 5 + DRF, single project at `backend/`
- **Frontend**: Vanilla JS SPA served via Django templates (`game/static/`, `game/templates/`)
- **LLM**: Multi-provider (DeepSeek, Qwen, OpenAI) via `llm/` app
- **DB**: PostgreSQL with `JSONField` for game state (`county_data`)

## Project Layout

```
backend/
  config/           # Django settings, celery, wsgi
  game/             # Main game app
    models.py       # GameState, Agent, EventLog, NegotiationSession, Promise, PlayerProfile
    views.py        # DRF APIViews (all REST endpoints)
    serializers.py  # DRF serializers
    urls.py         # /api/games/... routes
    services/       # Business logic package
      constants.py  # Numeric constants (yields, growth rates, medical costs)
      county.py     # CountyService — county initialization
      investment.py # InvestmentService — investment actions
      settlement.py # SettlementService — season advancement engine
    agent_defs/     # NPC blueprint data
      agents.py     # MVP_AGENTS (16 NPCs)
      relationships.py  # MVP_RELATIONSHIPS (23 pairs)
    agent_service.py      # Agent CRUD + chat (calls LLM)
    negotiation_service.py # Negotiation session logic
    promise_service.py     # Promise tracking
    static/game/    # JS/CSS frontend
    templates/game/ # index.html (SPA shell)
  llm/              # LLM client abstraction
    client.py       # Unified LLM client
    providers.py    # Provider configs
    prompts.py      # System prompts
  scripts/          # Debug/utility scripts
docs/               # Game design documents (GDD, numbered 00-07)
```

## Key Conventions

- **Language**: Code is in English; game content, comments, and docstrings are in Chinese
- **Services pattern**: Business logic lives in service classes (`XxxService`), not in views or models
- **Package re-exports**: `services/__init__.py` and `agent_defs/__init__.py` re-export all public names — imports like `from .services import SettlementService` work unchanged

## Development

```bash
# Start all services
docker compose up --build

# Django management (inside container)
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py createsuperuser

# Local Python (requires psycopg2 + running postgres)
cd backend && python manage.py check
```

- API base: `http://localhost:8000/api/`
- Admin: `http://localhost:8000/admin/`
- No test suite yet — verify manually via API or admin

## Design Docs Reference

The `docs/` folder contains the full GDD (game design documents):
- `00` — Overall design (GDD)
- `01` — AI Agent system
- `02` — County management model
- `06` — Numerical system (constants, formulas referenced in `services/settlement.py`)
