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
    """每次登录写一条 UserLoginLog。"""
    from .models import UserLoginLog
    UserLoginLog.objects.create(
        user=user,
        ip_address=_get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:300],
    )


@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    """登出时，找到该用户最近一条未关闭的 session，填写登出时间。"""
    if user is None:
        return
    from .models import UserLoginLog
    last_open = (
        UserLoginLog.objects
        .filter(user=user, logged_out_at__isnull=True)
        .order_by('-created_at')
        .first()
    )
    if last_open:
        last_open.logged_out_at = timezone.now()
        last_open.save(update_fields=['logged_out_at'])
