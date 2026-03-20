"""
玩家行为分析脚本 — 在 Django shell 中运行：
    docker compose exec backend python manage.py shell < scripts/player_analytics.py
"""

from django.contrib.auth.models import User
from django.db.models import Count, Min, Max
from game.models import GameState, EventLog, UserLoginLog

print(f"\n{'='*80}")
print(f"{'用户名':<15} {'登录次数':>6} {'IP数':>5} {'开档数':>5} {'操作数':>6} {'约游玩分钟':>10}  最后登录")
print(f"{'='*80}")

for u in User.objects.filter(is_staff=False).order_by('username'):
    login_logs = UserLoginLog.objects.filter(user=u)
    login_count = login_logs.count()
    ip_count = login_logs.values('ip_address').distinct().count()

    games = GameState.objects.filter(user=u)
    game_count = games.count()

    events = EventLog.objects.filter(game__user=u)
    event_count = events.count()

    # 粗估：各局游戏中，首条到末条事件的跨度之和
    total_minutes = 0
    for g in games:
        agg = EventLog.objects.filter(game=g).aggregate(
            first=Min('created_at'), last=Max('created_at')
        )
        if agg['first'] and agg['last']:
            total_minutes += (agg['last'] - agg['first']).total_seconds() / 60

    last_login = u.last_login.strftime('%m-%d %H:%M') if u.last_login else '从未'
    print(f"{u.username:<15} {login_count:>6} {ip_count:>5} {game_count:>5} "
          f"{event_count:>6} {total_minutes:>10.0f}  {last_login}")

print(f"{'='*80}\n")

# 登录 IP 明细（过去30次/人）
print("\n--- IP 明细（最近10条登录）---")
for u in User.objects.filter(is_staff=False).order_by('username'):
    logs = UserLoginLog.objects.filter(user=u).order_by('-created_at')[:10]
    if logs:
        entries = ', '.join(f"{l.ip_address}@{l.created_at:%m-%d %H:%M}" for l in logs)
        print(f"{u.username:<15} {entries}")
