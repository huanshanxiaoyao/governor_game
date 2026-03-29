# Server Maintenance Guide (59.110.40.50 / makebetter.top)

## Stack (no Docker)

| Component  | How it runs                        |
|------------|------------------------------------|
| Django app | Gunicorn via systemd               |
| Nginx      | Reverse proxy via systemd          |
| PostgreSQL | Native system service              |
| Redis      | Native system service              |
| Python     | `/usr/bin/python3.11`              |
| Packages   | `~/.local/lib/python3.11/`         |

- **App root**: `/home/jack/workspace/governor_game/governor_game/backend/`
- **Logs**: `/home/jack/workspace/governor_game/governor_game/logs/`
- **Systemd unit**: `/etc/systemd/system/governor-game.service`
- **Nginx config**: `/etc/nginx/conf.d/governor-game.conf`
- **Public URL**: `http://makebetter.top/g1/` (also `http://59.110.40.50/g1/`)

## Request Flow

```
Internet (port 80)
  → Nginx
      /g1/static/*  → staticfiles/ directory (served by Nginx directly)
      /g1/*         → strips /g1 prefix → Gunicorn on 127.0.0.1:8001
      /api/*        → Gunicorn on 127.0.0.1:8001 (JS uses hardcoded /api/ paths)
```

Gunicorn listens on `127.0.0.1:8001` only — not directly accessible from outside.

---

## Daily Operations

### Check service status
```bash
sudo systemctl status governor-game
sudo systemctl status nginx
```

### Restart after code changes
```bash
cd /home/jack/workspace/governor_game/governor_game
git pull
sudo systemctl restart governor-game
```

### Run migrations after model changes
```bash
cd /home/jack/workspace/governor_game/governor_game/backend
POSTGRES_HOST=localhost python3.11 manage.py migrate
sudo systemctl restart governor-game
```

### Collect static files after frontend changes
```bash
cd /home/jack/workspace/governor_game/governor_game/backend
POSTGRES_HOST=localhost python3.11 manage.py collectstatic --noinput
# No restart needed — Nginx serves staticfiles directly
```

### Install a new Python package
```bash
pip3 install <package> --user
# Then add it to the Dockerfile/requirements on your Mac for Docker parity
sudo systemctl restart governor-game
```

### View live logs
```bash
# App logs (access + errors from Gunicorn)
tail -f /home/jack/workspace/governor_game/governor_game/logs/error.log
tail -f /home/jack/workspace/governor_game/governor_game/logs/access.log

# Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log

# systemd journal (includes startup errors)
sudo journalctl -u governor-game -f
sudo journalctl -u nginx -f
```

---

## Deploying Code Updates

```bash
cd /home/jack/workspace/governor_game/governor_game

# 1. Pull latest code
git pull

# 2. Install any new Python dependencies
pip3 install -r backend/requirements.txt --user

# 3. Run migrations if models changed
cd backend
POSTGRES_HOST=localhost python3.11 manage.py migrate

# 4. Collect static if frontend changed
POSTGRES_HOST=localhost python3.11 manage.py collectstatic --noinput

# 5. Restart app (no need to restart Nginx unless config changed)
sudo systemctl restart governor-game
```

---

## Service Control

```bash
# App (Gunicorn)
sudo systemctl start governor-game
sudo systemctl stop governor-game
sudo systemctl restart governor-game
sudo systemctl status governor-game

# Nginx
sudo systemctl reload nginx          # reload config without downtime
sudo systemctl restart nginx         # full restart
sudo systemctl status nginx

# Edit Nginx config then reload:
sudo nano /etc/nginx/conf.d/governor-game.conf
sudo nginx -t                        # test config first
sudo systemctl reload nginx
```

---

## Database

PostgreSQL is running natively with these defaults (no env vars needed locally):
- Host: `localhost`
- DB: `mandarin_game`
- User: `postgres`
- Password: `postgres`

```bash
# Open psql
psql -U postgres -d mandarin_game

# Backup
pg_dump -U postgres mandarin_game > backup_$(date +%Y%m%d).sql

# Restore
psql -U postgres mandarin_game < backup_YYYYMMDD.sql
```

---

## Editing the Systemd Unit

The service file is at `/etc/systemd/system/governor-game.service`.
After any edits:
```bash
sudo systemctl daemon-reload
sudo systemctl restart governor-game
```

To add environment variables (e.g. API keys), add lines to the `[Service]` section:
```
Environment="DEEPSEEK_API_KEY=your-key-here"
```

---

## Troubleshooting

**App won't start**
```bash
sudo journalctl -u governor-game -n 50
```

**Nginx won't start / bad config**
```bash
sudo nginx -t
sudo journalctl -u nginx -n 50
```

**Port conflict on 8001**
```bash
ss -tlnp | grep 8001
kill <pid>
sudo systemctl start governor-game
```

**Migration errors**
```bash
cd backend
POSTGRES_HOST=localhost python3.11 manage.py showmigrations
POSTGRES_HOST=localhost python3.11 manage.py migrate --run-syncdb
```

**Static files not updating**
```bash
cd backend
POSTGRES_HOST=localhost python3.11 manage.py collectstatic --noinput --clear
# No service restart needed
```

**Check Gunicorn workers**
```bash
ps aux | grep gunicorn
```
