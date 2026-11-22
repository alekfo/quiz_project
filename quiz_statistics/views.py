from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST, require_safe

from .models import Notification
from .selectors import USER_BOARDS, get_leaderboard, get_notifications


@require_safe
def rankings(request):
    """Показывает выбранную таблицу рейтинга и личные места посетителя."""
    board = request.GET.get("board", "players")
    period = request.GET.get("period", "month")
    if board != "quizzes" and board not in USER_BOARDS:
        board = "players"
    if period not in {"month", "all"}:
        period = "month"
    context = get_leaderboard(board=board, period=period, viewer=request.user, now=timezone.now())
    return render(request, "quiz_statistics/rankings.html", context)


@login_required
@require_safe
def notification_list(request):
    """Показывает страницу собственных уведомлений о достижениях."""
    paginator = Paginator(get_notifications(request.user), 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "quiz_statistics/notifications.html", {
        "notifications": page.object_list,
        "page_obj": page,
    })


@login_required
@require_POST
@csrf_protect
def notification_read(request, pk: int):
    """Отмечает собственное уведомление прочитанным при первом запросе."""
    owned = Notification.objects.filter(pk=pk, recipient=request.user)
    updated = owned.filter(read_at__isnull=True).update(read_at=timezone.now())
    if not updated and not owned.exists():
        raise Http404
    return redirect("quiz_statistics:notifications")
