# Operation Log — 2026-03-29

## Context

Production node: 59.110.40.50 / makebetter.top
Stack: no Docker — Gunicorn (systemd) + Nginx + native PostgreSQL + Redis
App runs at: http://makebetter.top/g1/

---

## Changes Made

### 1. `backend/config/settings.py` — env-driven FORCE_SCRIPT_NAME

**Problem**: `FORCE_SCRIPT_NAME = '/g1'` was hardcoded. This would break local Docker dev
where the app runs at `/` with no prefix.

**Fix**: Changed to read from env var, only set if present:

```python
_script_name = os.getenv('FORCE_SCRIPT_NAME', '')
if _script_name:
    FORCE_SCRIPT_NAME = _script_name
```

The env var is supplied by the systemd unit on the server (see below).
Local Docker dev: no env var set → Django behaves as before.

---

### 2. `governor-game.service` — added FORCE_SCRIPT_NAME env

Added to `[Service]` section:
```
Environment="FORCE_SCRIPT_NAME=/g1"
```

This is what activates the prefix in production.
Deployed to `/etc/systemd/system/governor-game.service`, reloaded and restarted.

---

### 3. `backend/game/templates/game/admin_tools_index.html` — reverted hardcoded /g1/ link

The "返回游戏首页" button was pointing to `/g1/` (hardcoded). Reverted to `/`.
With `FORCE_SCRIPT_NAME` set, Django prepends the prefix automatically on all URL
reversals — but since this is a raw href, it remains `/`. Nginx handles it via the
`location /g1/` → proxy_pass stripping, so the Django app sees `/` correctly.

---

### 4. `backend/game/services/neighbor.py` — LLM timeout safety (pre-existing fix)

Added `timeout=90s` / `timeout=60s` to the `as_completed()` calls for parallel LLM
neighbor decisions and settlements. Catches `FuturesTimeoutError` and falls back to
empty results instead of hanging indefinitely.

---

### 5. `.gitignore` — added production-generated directories

```
backend/staticfiles/
logs/
```

These are generated at runtime (`collectstatic`, Gunicorn log files) and should never
be committed.

---

### 6. `deploy/nginx/governor-game.conf` — Nginx config captured into repo

The live Nginx config at `/etc/nginx/conf.d/governor-game.conf` was not in git.
Copied verbatim to `deploy/nginx/governor-game.conf` for future deployments.

Key routing logic:
- `/g1/static/*` and `/static/*` → served directly from `backend/staticfiles/` by Nginx
- `/api/*` → proxied to Gunicorn at `127.0.0.1:8001` (JS uses hardcoded `/api/` paths)
- `/g1/` → proxy_pass to `http://127.0.0.1:8001/` (trailing slash strips the /g1 prefix)
- `/llm-bench/` → proxied to Gunicorn

---

### 7. `SERVER.md` — new file in repo root

Comprehensive ops guide for the production node. Covers:
- Stack overview and file paths
- Request flow diagram
- Daily operations (status check, restart, migrate, collectstatic)
- Full deploy steps
- Service control commands
- Database access and backup
- Troubleshooting recipes

---

### 8. `push_online.sh` — deploy script in repo root

One-shot deploy script: git pull → migrate → collectstatic → restart service.
Usage: `bash push_online.sh`

---

## How to Deploy to a New Node

1. Install: Python 3.11, pip, PostgreSQL, Redis, Nginx, gunicorn (`pip install gunicorn --user`)
2. Clone repo, install Python deps: `pip3 install -r backend/requirements.txt --user`
3. Create DB: `createdb -U postgres mandarin_game`
4. Run migrations: `cd backend && POSTGRES_HOST=localhost python3.11 manage.py migrate`
5. Collect static: `python3.11 manage.py collectstatic --noinput`
6. Copy systemd unit: `sudo cp governor-game.service /etc/systemd/system/`
   - Edit paths inside if the user/home dir differs
   - Add API key env vars in the `[Service]` section
7. Enable and start: `sudo systemctl daemon-reload && sudo systemctl enable --now governor-game`
8. Copy Nginx config: `sudo cp deploy/nginx/governor-game.conf /etc/nginx/conf.d/`
   - Edit `server_name`, `alias` paths, and proxy port if needed
   - Test: `sudo nginx -t`
   - Reload: `sudo systemctl reload nginx`
9. Verify: `curl http://localhost/g1/` should return the game HTML
