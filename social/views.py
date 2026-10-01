import logging

from django.shortcuts import get_object_or_404, redirect
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.views.decorators.http import require_POST
from django.http import HttpRequest
from django.shortcuts import render

from .models import QuizSeriesLike, Follow
from quizzes.models import QuizSeries
from .services import get_like_context, get_follow_context

logger = logging.getLogger(__name__)

def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"

@login_required
@require_POST
def follow(request: HttpRequest, user_id: int):

    current_user = request.user
    target_user = get_object_or_404(get_user_model(), pk=user_id)

    if target_user == current_user:
        raise PermissionDenied

    following, created = Follow.objects.get_or_create(follower=current_user, following=target_user)

    if created:
        logger.info(f"Пользователь с id = {current_user.pk} успешно подписался на пользователя с id = {target_user.pk}")

    if _is_htmx(request):
        return render(request, "social/_follow_button.html", get_follow_context(current_user, target_user))
    return redirect("users:user_detail", pk=user_id)

@login_required
@require_POST
def unfollow(request: HttpRequest, user_id: int):

    current_user = request.user
    target_user = get_object_or_404(get_user_model(), pk=user_id)

    deleted_count, _ = Follow.objects.filter(follower=current_user, following=target_user).delete()

    if deleted_count != 0:
        logger.info(f"Подписка пользователя с id = {current_user.pk} на пользователя с id = {target_user.pk} успешно отменена")

    if _is_htmx(request):
        return render(request, "social/_follow_button.html", get_follow_context(current_user, target_user))
    return redirect("users:user_detail", pk=user_id)

@login_required
@require_POST
def create_like(request: HttpRequest, series_id: int):
    """
    Лайк ставится только на серию, которую пользователь вправе видеть (visible_to):
    публичную или свою. series_id приходит из URL - без фильтра можно было бы лайкнуть
    чужую приватную серию и перебором id выяснять, какие приватные серии существуют
    (404 против редиректа).
    """
    current_user = request.user
    target_series = get_object_or_404(
        QuizSeries.objects.visible_to(current_user),
        pk=series_id,
    )

    like, created = QuizSeriesLike.objects.get_or_create(series=target_series, user=current_user)
    if created:
        logger.info(f"Пользователь с id = {current_user.pk} успешно лайкнул квиз с id = {target_series.pk}")

    if _is_htmx(request):
        return render(request, "social/_like_button.html", get_like_context(current_user, target_series))
    return redirect("quizzes:quizzes_preview", pk=series_id)

@login_required
@require_POST
def delete_like(request: HttpRequest, series_id: int):
    """
    visible_to - для симметрии с create_like: снять лайк можно только с серии,
    которую пользователь видит. Известное следствие: если автор сделал серию
    приватной после лайка, снять этот лайк уже нельзя (404). Для удаления своего
    лайка проверка видимости по сути не нужна (удаляется только свой лайк, ответ
    одинаковый) - её стоит убрать, когда появится список "мои лайки".
    """
    current_user = request.user
    target_series = get_object_or_404(
        QuizSeries.objects.visible_to(current_user),
        pk=series_id,
    )

    deleted_count, _ = QuizSeriesLike.objects.filter(series=target_series, user=current_user).delete()

    if deleted_count != 0:
        logger.info(f"Лайк от пользователя с id = {current_user.pk} на квиз с id = {target_series.pk} успешно снят")

    if _is_htmx(request):
        return render(request, "social/_like_button.html", get_like_context(current_user, target_series))
    return redirect("quizzes:quizzes_preview", pk=series_id)