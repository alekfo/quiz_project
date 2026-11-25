from django.db.models import Sum

from .models import Notification, RatingEntry


def notification_counts(request) -> dict:
    """Возвращает число непрочитанных уведомлений и очки рейтинга для шапки сайта."""
    unread_notifications_count = 0
    header_player_points = 0
    header_author_points = 0
    if request.user.is_authenticated:
        unread_notifications_count = Notification.objects.filter(recipient=request.user, read_at__isnull=True).count()
        header_player_points = RatingEntry.objects.filter(
            recipient=request.user, kind="player"
        ).aggregate(total=Sum('amount'))['total'] or 0
        header_author_points = RatingEntry.objects.filter(
            recipient=request.user, kind="author"
        ).aggregate(total=Sum('amount'))['total'] or 0

    return {
        "unread_notifications_count": unread_notifications_count,
        "header_player_points": header_player_points,
        "header_author_points": header_author_points,
    }
