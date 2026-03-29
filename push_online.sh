#!/bin/bash
set -e

REPO_DIR="/home/jack/workspace/governor_game/governor_game"
BACKEND_DIR="$REPO_DIR/backend"

echo "=== [1/4] Pulling latest code ==="
cd "$REPO_DIR"
git pull

echo "=== [2/4] Running migrations ==="
cd "$BACKEND_DIR"
python3.11 manage.py migrate

echo "=== [3/4] Collecting static files ==="
python3.11 manage.py collectstatic --noinput

echo "=== [4/4] Restarting service ==="
sudo systemctl restart governor-game
sleep 2
sudo systemctl status governor-game --no-pager

echo ""
echo "Done! Live at http://makebetter.top/g1/"
