"""Django signal handlers — 登录/登出日志。"""
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone


def _get_client_ip(request):
    """取真实客户端 IP，兼容反向代理。"""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    """每次登录写一条 UserLoginLog，保存 session_key 以便精确匹配登出。"""
    from .models import UserLoginLog
    UserLoginLog.objects.create(
        user=user,
        session_key=request.session.session_key,
        ip_address=_get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:300],
    )


@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    """登出时通过 session_key 精确定位并关闭对应的登录记录。
    多 tab 并发 session 下各自独立，互不干扰。
    """
    if user is None:
        return
    from .models import UserLoginLog
    session_key = request.session.session_key if hasattr(request, 'session') else None
    if session_key:
        # 直接 UPDATE，无需先 SELECT，多 tab 下精确匹配本次 session
        UserLoginLog.objects.filter(
            session_key=session_key,
            logged_out_at__isnull=True,
        ).update(logged_out_at=timezone.now())
    else:
        # 降级：session 已被清除时，关闭该用户最近一条未关闭记录
        entry = (
            UserLoginLog.objects
            .filter(user=user, logged_out_at__isnull=True)
            .order_by('-created_at')
            .first()
        )
        if entry:
            entry.logged_out_at = timezone.now()
            entry.save(update_fields=['logged_out_at'])
