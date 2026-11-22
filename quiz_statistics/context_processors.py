from .models import Notification


def notification_counts(request) -> dict:
    """Возвращает число непрочитанных уведомлений для шапки сайта."""
    count = 0
    if request.user.is_authenticated:
        count = Notification.objects.filter(recipient=request.user, read_at__isnull=True).count()
    return {"unread_notifications_count": count}
