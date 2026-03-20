"""Django signal handlers — 登录日志等。"""
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


def _get_client_ip(request):
    """取真实客户端 IP，兼容反向代理。"""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    """每次登录写一条 UserLoginLog。"""
    from .models import UserLoginLog
    UserLoginLog.objects.create(
        user=user,
        ip_address=_get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:300],
    )
