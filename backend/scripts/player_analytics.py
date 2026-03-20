"""
玩家行为分析脚本 — 在 Django shell 中运行：
    docker compose exec backend python manage.py shell < scripts/player_analytics.py
"""

from django.contrib.auth.models import User
from django.db.models import Count, Sum, F, ExpressionWrapper, DurationField
from django.db.models.functions import Coalesce
from game.models import GameState, UserLoginLog

print(f"\n{'='*95}")
print(f"{'用户名':<15} {'登录次数':>6} {'IP数':>5} {'开档数':>5} {'推进月份':>8} {'在线时长(分)':>12}  最后登录")
print(f"{'='*95}")

for u in User.objects.filter(is_staff=False).order_by('username'):
    # 登录次数 & IP 数
    login_logs = UserLoginLog.objects.filter(user=u)
    login_count = login_logs.count()
    ip_count = login_logs.values('ip_address').distinct().count()

    # 开档数 & 推进月份数（current_season 从1起，每推进一次+1）
    games = GameState.objects.filter(user=u)
    game_count = games.count()
    total_advances = sum(max(g.current_season - 1, 0) for g in games)

    # 在线时长：已关闭 session 的 (logged_out_at - created_at) 之和，单位分钟
    closed = login_logs.filter(logged_out_at__isnull=False)
    online_minutes = 0.0
    for log in closed:
        online_minutes += (log.logged_out_at - log.created_at).total_seconds() / 60

    last_login = u.last_login.strftime('%m-%d %H:%M') if u.last_login else '从未'
    print(f"{u.username:<15} {login_count:>6} {ip_count:>5} {game_count:>5} "
          f"{total_advances:>8} {online_minutes:>12.1f}  {last_login}")

print(f"{'='*95}")
print("注：在线时长仅统计已正常登出的 session；推进月份为各存档 current_season-1 之和\n")
