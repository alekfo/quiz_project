from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db.models import Count, Max, OuterRef, QuerySet, Subquery, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse

from gameplay.models import GameParticipant, SeriesRun
from quizzes.models import QuizSeries
from social.models import QuizSeriesLike
from .models import (
    AchievementAward,
    AuthorAudience,
    Notification,
    RatingAttempt,
    RatingEntry,
    RoundParticipation,
    SeriesProgress,
)
from .rules import LEADERBOARD_TOP_SIZE, POPULAR_SERIES_MIN_FINISHERS, SERIES_AUDIENCE_THRESHOLDS


MOSCOW = ZoneInfo("Europe/Moscow")
USER_BOARDS = {"players": "player", "authors": "author"}


def get_period_bounds(period: str, *, now: datetime) -> tuple[datetime | None, datetime]:
    """Возвращает границы зачёта с исключающей верхней границей."""
    if period == "all":
        return None, now
    start = now.astimezone(MOSCOW).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, now


def _ranked_rows(*, board: str, period: str, now: datetime):
    """Возвращает участников зачёта в порядке их итоговых мест."""
    start, end = get_period_bounds(period, now=now)
    if board == "quizzes":
        events = SeriesProgress.objects.filter(
            series_identity__series__status="public",
            qualified_at__lt=end,
        )
        time_field = "qualified_at"
        subject_field = "series_identity__series_id"
        value = Count("pk")
    else:
        events = RatingEntry.objects.filter(kind=USER_BOARDS[board], earned_at__lt=end)
        time_field = "earned_at"
        subject_field = "recipient_id"
        value = Sum("amount")

    if start is not None:
        events = events.filter(**{f"{time_field}__gte": start})
    return (
        events.values(subject_field)
        .annotate(value=value, reached_at=Max(time_field))
        .filter(value__gt=0)
        .order_by("-value", "reached_at", subject_field)
        .values_list(subject_field, "value")
    )


def get_user_position(user, *, board: str, period: str, now: datetime) -> dict:
    """Возвращает порядковое место и очки игрока или автора."""
    for rank, (subject_id, points) in enumerate(
        _ranked_rows(board=board, period=period, now=now).iterator(), start=1
    ):
        if subject_id == user.pk:
            return {"rank": rank, "points": points}
    return {"rank": None, "points": 0}


def _user_leaderboard(*, board: str, period: str, viewer, now: datetime) -> dict:
    """Возвращает десятку пользователей и личное место за её пределами."""
    rows = _ranked_rows(board=board, period=period, now=now)
    top = list(rows[:LEADERBOARD_TOP_SIZE])
    users = get_user_model().objects.in_bulk(subject_id for subject_id, _ in top)
    top_rows = [
        {
            "rank": rank,
            "subject": users[subject_id],
            "value": points,
            "url": reverse("users:user_detail", args=[subject_id]),
        }
        for rank, (subject_id, points) in enumerate(top, start=1)
    ]
    my_position = None
    if viewer.is_authenticated and viewer.pk not in users:
        my_position = get_user_position(viewer, board=board, period=period, now=now)
    return {"top_rows": top_rows, "my_position": my_position, "my_quiz_positions": []}


def _quiz_leaderboard(*, period: str, viewer, now: datetime) -> dict:
    """Возвращает десятку квизов и места остальных публичных квизов автора."""
    my_ids = set()
    if viewer.is_authenticated:
        my_ids = set(
            QuizSeries.objects.filter(user_id=viewer.pk, status="public")
            .values_list("pk", flat=True)
        )

    rows = _ranked_rows(board="quizzes", period=period, now=now)
    if not my_ids:
        rows = rows[:LEADERBOARD_TOP_SIZE]
    top = []
    my_entries = []
    unranked_ids = my_ids.copy()
    for rank, (series_id, completions) in enumerate(rows.iterator(), start=1):
        if rank <= LEADERBOARD_TOP_SIZE:
            top.append((rank, series_id, completions))
        elif series_id in my_ids:
            my_entries.append((rank, series_id, completions))
        unranked_ids.discard(series_id)
        if rank >= LEADERBOARD_TOP_SIZE and not unranked_ids:
            break
    my_entries.extend((None, series_id, 0) for series_id in sorted(unranked_ids))

    likes = (
        QuizSeriesLike.objects.filter(series_id=OuterRef("pk"))
        .order_by()
        .values("series_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    series_by_id = QuizSeries.objects.filter(status="public").annotate(
        leaderboard_likes=Coalesce(Subquery(likes), 0),
    ).in_bulk({series_id for _, series_id, _ in (*top, *my_entries)})
    return {
        "top_rows": _quiz_board_rows(top, series_by_id),
        "my_position": None,
        "my_quiz_positions": _quiz_board_rows(my_entries, series_by_id),
    }


def _quiz_board_rows(entries, series_by_id) -> list[dict]:
    """Формирует строки таблицы квизов с местом, лайками и ссылкой."""
    return [
        {
            "rank": rank,
            "subject": series_by_id[series_id],
            "value": completions,
            "likes": series_by_id[series_id].leaderboard_likes,
            "url": reverse("quizzes:quizzes_preview", args=[series_id]),
        }
        for rank, series_id, completions in entries
        if series_id in series_by_id
    ]


def get_leaderboard(*, board: str, period: str, viewer, now: datetime) -> dict:
    """Собирает таблицу зачёта и видимые посетителю личные места."""
    if board == "quizzes":
        positions = _quiz_leaderboard(period=period, viewer=viewer, now=now)
    else:
        positions = _user_leaderboard(board=board, period=period, viewer=viewer, now=now)
    return {"board": board, "period": period, **positions}


def get_player_stats(user, *, now: datetime) -> dict:
    """Возвращает рейтинг игрока, число завершённых квизов и достижения."""
    stats = {
        period: get_user_position(user, board="players", period=period, now=now)
        for period in ("month", "all")
    }
    stats["completed_series_count"] = SeriesProgress.objects.filter(
        player=user,
        completed_at__isnull=False,
    ).exclude(author_at_start=user).count()
    stats["achievements"] = list(
        AchievementAward.objects.filter(
            notification_owner=user,
            kind=AchievementAward.Kind.PLAYER_COMPLETED_SERIES,
        ).order_by("threshold")
    )
    return stats


def get_author_stats(user, *, now: datetime) -> dict:
    """Возвращает места автора, его аудиторию и сохранённые достижения."""
    stats = {
        period: get_user_position(user, board="authors", period=period, now=now)
        for period in ("month", "all")
    }
    awards = AchievementAward.objects.filter(notification_owner=user).exclude(
        kind=AchievementAward.Kind.PLAYER_COMPLETED_SERIES,
    )
    stats.update(
        unique_audience=AuthorAudience.objects.filter(author=user).count(),
        popular_series_count=awards.filter(
            kind=AchievementAward.Kind.SERIES_AUDIENCE,
            threshold=POPULAR_SERIES_MIN_FINISHERS,
        ).count(),
        achievements=list(awards.select_related("series_identity").order_by("-awarded_at", "-pk")),
    )
    return stats


def get_series_card_stats(series_ids: list[int]) -> dict:
    """Пакетно возвращает число завершивших и достигнутый порог для карточек."""
    stats = {
        series_id: {"unique_finishers": 0, "popularity_level": None}
        for series_id in series_ids
    }
    if not stats:
        return stats

    finishers = (
        SeriesProgress.objects.filter(
            series_identity__series_id__in=stats,
            qualified_at__isnull=False,
        )
        .values_list("series_identity__series_id")
        .annotate(total=Count("pk"))
    )
    for series_id, total in finishers:
        stats[series_id]["unique_finishers"] = total

    levels = (
        AchievementAward.objects.filter(
            kind=AchievementAward.Kind.SERIES_AUDIENCE,
            series_identity__series_id__in=stats,
        )
        .values_list("series_identity__series_id")
        .annotate(level=Max("threshold"))
    )
    for series_id, level in levels:
        stats[series_id]["popularity_level"] = level
    return stats


def get_top_quiz_results(series: QuizSeries) -> list[dict]:
    """Возвращает лучшие соло-результаты трёх разных игроков."""
    solo_results = GameParticipant.objects.filter(
        session__series_run__series=series,
        session__series_run__mode="solo",
        session__series_run__status="completed",
        session__status="completed",
    )
    results_by_run = solo_results.values(
        "session__series_run_id",
        "user_id",
        "user__username",
    )
    results_by_run = results_by_run.annotate(total_score=Sum("score"))
    results_by_run = results_by_run.order_by("-total_score", "user_id")

    top_results = []
    seen_players = set()
    for result in results_by_run.iterator():
        player_id = result["user_id"]
        if player_id in seen_players:
            continue

        seen_players.add(player_id)
        top_results.append({
            "username": result["user__username"],
            "score": result["total_score"],
        })
        if len(top_results) == 3:
            break

    return top_results


def get_personal_series_progress(user, series: QuizSeries) -> dict:
    """Возвращает завершённые текущие раунды и активный соло-прогон игрока."""
    history = RoundParticipation.objects.filter(
        player=user,
        round_identity__series_identity__series=series,
    )
    completed_rounds = (
        history.filter(status="completed", round_identity__quiz__series=series)
        .order_by()
        .values("round_identity__quiz_id")
        .distinct()
        .count()
    )
    active_run = SeriesRun.objects.filter(
        series=series,
        mode="solo",
        created_by=user,
        status="in_progress",
    ).first()
    return {
        "total_rounds": series.rounds.count(),
        "completed_rounds": completed_rounds,
        "started": completed_rounds > 0 or history.exists(),
        "active_run": active_run,
    }


def get_round_rating_statuses(user, series: QuizSeries) -> dict:
    """Возвращает доступность рейтинговой попытки для каждого текущего раунда."""
    own_series = series.user_id == user.pk
    statuses = {
        quiz.pk: "own" if own_series else "available"
        for quiz in series.rounds.all()
    }
    if own_series or not statuses:
        return statuses

    attempts = RatingAttempt.objects.filter(
        player=user,
        round_identity__quiz_id__in=statuses,
    ).values_list("round_identity__quiz_id", "status", "participation__status")
    for quiz_id, attempt_status, participation_status in attempts:
        statuses[quiz_id] = (
            "in_progress" if attempt_status == participation_status == "in_progress" else "used"
        )
    return statuses


def get_author_quiz_stats(series: QuizSeries, *, period: str, now: datetime) -> dict:
    """Возвращает завершение когорты, репутацию и прогресс достижения квиза."""
    start, end = get_period_bounds(period, now=now)
    progress = SeriesProgress.objects.filter(series_identity__series=series).exclude(
        player_id=series.user_id,
    )
    cohort = progress.filter(started_at__lt=end)
    qualified = progress.filter(qualified_at__lt=end)
    entries = RatingEntry.objects.filter(
        kind="author",
        series_progress__series_identity__series=series,
        earned_at__lt=end,
    )
    if start is not None:
        cohort = cohort.filter(started_at__gte=start)
        qualified = qualified.filter(qualified_at__gte=start)
        entries = entries.filter(earned_at__gte=start)

    counts = cohort.aggregate(
        started=Count("pk"),
        completed=Count("completed_at"),
    )
    started = counts["started"]
    completed = counts["completed"]
    current = progress.filter(qualified_at__lt=end).count()
    next_level = next((level for level in SERIES_AUDIENCE_THRESHOLDS if level > current), None)
    return {
        "started": started,
        "completed_from_started": completed,
        "completion_rate": 100 * completed / started if started else None,
        "qualifying_completions": qualified.count(),
        "reputation": entries.aggregate(total=Sum("amount", default=0))["total"],
        "achievement_progress": {
            "current": current,
            "next": next_level,
            "remaining": next_level - current if next_level is not None else 0,
        },
    }


def get_participation_result(participation: RoundParticipation) -> dict:
    """Возвращает рейтинг за участие, игровой рекорд и режим попытки."""
    personal_best = RoundParticipation.objects.filter(
        player_id=participation.player_id,
        round_identity_id=participation.round_identity_id,
        status="completed",
    ).aggregate(best=Max("score"))["best"]
    rating_total = RatingEntry.objects.filter(
        kind="player",
        answer_rating__participation=participation,
    ).aggregate(total=Sum("amount", default=0))["total"]
    return {
        "rating_total_this_participation": rating_total,
        "personal_best": personal_best,
        "rating_mode": participation.rating_mode,
    }


def get_exit_rating_context(user, *, session_ids: list[int]) -> dict:
    """Возвращает сохранённые очки и статус попытки перед выходом из раунда."""
    active = RoundParticipation.objects.filter(
        player=user,
        participant__session_id__in=session_ids,
        status="in_progress",
    )
    points = RatingEntry.objects.filter(
        kind="player",
        answer_rating__participation__in=active,
    ).aggregate(total=Sum("amount", default=0))["total"]
    return {
        "rating_points_to_keep": points,
        "rating_attempt_will_be_used": active.filter(rating_mode="rated").exists(),
    }


def get_notifications(user) -> QuerySet[Notification]:
    """Возвращает уведомления получателя с данными достижений."""
    return (
        Notification.objects.filter(recipient=user)
        .select_related("award__author", "award__series_identity__series")
        .order_by("-created_at", "-pk")
    )
