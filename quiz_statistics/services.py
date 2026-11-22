from datetime import datetime

from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import (
    AchievementAward,
    AnswerRating,
    AuthorAudience,
    Notification,
    RatingAttempt,
    RatingEntry,
    RoundIdentity,
    RoundParticipation,
    SeriesIdentity,
    SeriesProgress,
    SeriesProgressRound,
)
from quizzes.models import Quiz, QuizSeries
from gameplay.models import GameParticipant, GameAnswer, GameSession, SeriesRun
from .rules import (
    AUTHOR_AUDIENCE_THRESHOLDS,
    AUTHOR_POPULAR_SERIES_THRESHOLDS,
    PLAYER_COMPLETED_SERIES_THRESHOLDS,
    POPULAR_SERIES_MIN_FINISHERS,
    RATING_RULES_VERSION,
    SERIES_AUDIENCE_THRESHOLDS,
    calculate_author_reputation,
    calculate_player_rating,
)

def get_round_identity(quiz: Quiz) -> RoundIdentity:
    """Возвращает рейтинговую запись раунда, создавая её при необходимости.

    Вызывается из register_round_start для текущего раунда и при создании
    снимка раундов чужой серии.
    Для раунда в серии также получает или создаёт запись серии.
    Обновляет названия существующих записей, если они изменились.
    """

    series_identity = None
    with transaction.atomic():
        if quiz.series_id:
            series = quiz.series
            series_identity, si_created = SeriesIdentity.objects.get_or_create(
                original_series_id=series.pk,
                defaults={
                    "series": series,
                    "author_id": series.user_id,
                    "title": series.title,
                }
            )

            #если SeriesIdentity уже существовало и title серии был изменен - меняем title у найденной SeriesIdentity
            if not si_created and series_identity.title != series.title:
                series_identity.title = series.title
                series_identity.save(update_fields=["title"])

        round_identity, ri_created = RoundIdentity.objects.get_or_create(
            original_quiz_id=quiz.pk,
            defaults={
                "quiz": quiz,
                "series_identity": series_identity,
                "title": quiz.subject
            }
        )
        if not ri_created and round_identity.title != quiz.subject:
            round_identity.title = quiz.subject
            round_identity.save(update_fields=["title"])

        return round_identity

def register_round_start(participant: GameParticipant) -> RoundParticipation:
    """Регистрирует участие игрока в раунде и возвращает его запись.

    По плану вызывается из solo_room и room_start сразу после создания
    GameParticipant, в той же транзакции.
    На своём раунде режим — own. На чужом первая попытка рейтинговая,
    последующие тренировочные. При первом старте чужой серии фиксирует
    её публичность и состав раундов с вопросами для учёта прогресса.
    """
    session = participant.session
    quiz = session.quiz
    player = participant.user
    round_identity = get_round_identity(quiz)
    series_author = quiz.series.user_id if quiz.series_id else quiz.user_id

    #получаем или создаем новую RoundParticipation, проверяя RatingAttempt
    with transaction.atomic():
        round_participation, rp_created = RoundParticipation.objects.get_or_create(
            original_participant_id=participant.pk,
            defaults={
                "player": player,
                "round_identity": round_identity,
                "participant": participant,
                "original_session_id": session.pk,
                "mode": session.mode,
                "started_at": participant.joined_at,
                "questions_count": quiz.questions.count(),
                "status": "in_progress",
                "rating_mode": "own" if series_author == player.pk else "training",
            }
        )
        if rp_created and round_participation.rating_mode != "own":
            raiting_attempt, ra_created = RatingAttempt.objects.get_or_create(
                player=player,
                round_identity=round_identity,
                defaults={
                    "participation": round_participation,
                    "started_at": participant.joined_at,
                    "status": "in_progress"
                }
            )
            if ra_created:
                round_participation.rating_mode = "rated"
                round_participation.save(update_fields=["rating_mode"])

        if round_participation.rating_mode != "own" and quiz.series_id:
            series_progress, sp_created = SeriesProgress.objects.get_or_create(
                player=player,
                series_identity=round_identity.series_identity,
                defaults={
                    "author_at_start_id": series_author,
                    "started_at": timezone.now(),
                    "was_public_at_start": quiz.series.status == "public",
                    "reward_status": "pending" if quiz.series.status == "public" else "ineligible",
                    "reward_reason": "" if quiz.series.status == "public" else "private_at_start"
                }
            )
            if sp_created:
                all_rounds = quiz.series.rounds.all()
                for r in all_rounds:
                    questions_count = r.questions.count()
                    if questions_count == 0:
                        continue
                    snapshot_round_identity = get_round_identity(r)
                    series_progress_round, spr_created = SeriesProgressRound.objects.get_or_create(
                        progress=series_progress,
                        round_identity=snapshot_round_identity,
                        defaults={
                            "questions_count": questions_count,
                        }
                    )
        return round_participation

def record_answer_rating(answer: GameAnswer, *, elapsed_seconds: float, time_limit_seconds: int, timed_out: bool) -> AnswerRating | None:
    """Возвращает существующую оценку ответа или сохраняет новую.

    По плану вызывается в POST play для каждого обработанного GameAnswer
    после обновления игрового счёта, в той же транзакции. Для неверных
    ответов, пропусков и таймаутов сохраняет причину нулевого начисления.

    Сохраняет причину и сумму начисления. При положительной сумме
    одновременно добавляет запись в журнал рейтинга игрока.
    """
    answer_rating = AnswerRating.objects.filter(original_answer_id=answer.pk).first()

    #возвращаем существующий answer_rating, если найден
    if answer_rating:
        return answer_rating

    #находим RoundParticipation в котором уже определен rating_mode, по нему определяем что делать дальше при создании AnswerRating
    round_participation = RoundParticipation.objects.filter(participant_id=answer.participant_id).first()
    if not round_participation:
        return None

    #вычисляем dynamic_scoring_mode
    dynamic_scoring_mode = answer.participant.session.quiz.dynamic_scoring_mode

    # вычисляем reason
    if round_participation.status == "abandoned":
        reason = "abandoned"
    elif round_participation.rating_mode == "training":
        reason = "training"
    elif round_participation.rating_mode == "own":
        reason = "own"
    elif timed_out:
        reason = "timeout"
    elif answer.is_skipped:
        reason = "skipped"
    elif answer.is_correct:
        reason = "correct"
    else:
        reason = "incorrect"

    #вычисляем points (только для rated)
    points = 0
    if reason == "correct":
        points = calculate_player_rating(
            is_correct=answer.is_correct,
            is_skipped=answer.is_skipped,
            elapsed_seconds=elapsed_seconds,
            time_limit_seconds=time_limit_seconds,
            dynamic_scoring_mode=dynamic_scoring_mode
        )
    with transaction.atomic():
        #создаем answer_rating
        answer_rating = AnswerRating.objects.create(
            answer=answer,
            original_answer_id=answer.pk,
            participation=round_participation,
            original_question_id=answer.question_id,
            is_correct=answer.is_correct,
            is_skipped=answer.is_skipped,
            timed_out=timed_out,
            answered_at=answer.answered_at,
            elapsed_seconds=elapsed_seconds,
            time_limit_seconds=time_limit_seconds,
            dynamic_scoring_mode=dynamic_scoring_mode,
            points=points,
            reason=reason,
            rules_version=RATING_RULES_VERSION
        )

        #если points > 0 то записываем собитие в журнал начислений AnswerRating
        if points > 0:
            rating_entry = RatingEntry.objects.create(
                recipient=round_participation.player,
                kind="player",
                amount=points,
                earned_at=answer.answered_at,
                rules_version=RATING_RULES_VERSION,
                answer_rating=answer_rating
            )
        return answer_rating

def ensure_round_editable(quiz: Quiz) -> None:
    """Проверяет, можно ли изменить раунд после захвата блокировки Quiz.
    Если у Quiz существует сессия in_progress - изменить Quiz нельзя
    """

    if GameSession.objects.filter(quiz_id=quiz.pk, status="in_progress").exists():
        raise ValidationError("Нельзя изменять раунд, пока есть активные прохождения")

def finalize_round_participation(participant: GameParticipant, *, status: str, finished_at: datetime) -> RoundParticipation | None:
    """Сохраняет итог участия и завершает связанную рейтинговую попытку."""

    round_participation = RoundParticipation.objects.filter(original_participant_id=participant.pk).select_related("rating_attempts").first()

    #обновляем данный round_participating
    if round_participation:
        if round_participation.status in ["completed", "abandoned"]:
            return round_participation
        round_participation.score = participant.score
        round_participation.correct_answers_count = sum(1 for pa in participant.participants_answers.all() if pa.is_correct and pa.answered_at)
        round_participation.finished_at = finished_at
        round_participation.status = status
        round_participation.save(update_fields=["score", "correct_answers_count", "finished_at", "status"])
    else:
        return None

    #Также обновляем связанный RatingAttempt, подгражуем его за счет select_related в round_participation
    rating_attempt = getattr(round_participation, "rating_attempts", None)
    if rating_attempt is not None:
        rating_attempt.status = status
        rating_attempt.finished_at = finished_at
        rating_attempt.save(update_fields=["status", "finished_at"])

    return round_participation


@transaction.atomic
def complete_round(participation: RoundParticipation) -> None:
    """Учитывает первое завершение раунда в накопительном прогрессе игрока."""
    if participation.status != "completed" or participation.finished_at is None:
        return

    series_identity_id = participation.round_identity.series_identity_id
    if series_identity_id is None:
        return

    progress = SeriesProgress.objects.select_for_update().filter(
        player_id=participation.player_id,
        series_identity_id=series_identity_id,
    ).first()
    if progress is None:
        return

    updated_count = progress.series_progress_rounds.filter(
        round_identity_id=participation.round_identity_id,
        completed_at__isnull=True,
        excluded_at__isnull=True,
    ).update(completed_at=participation.finished_at)
    if updated_count:
        evaluate_series_completion(progress, event_time=participation.finished_at)


@transaction.atomic
def evaluate_series_completion(progress: SeriesProgress, *, event_time: datetime) -> None:
    """Фиксирует завершение серии и однократно начисляет репутацию автору."""
    progress = SeriesProgress.objects.select_for_update().get(pk=progress.pk)
    if progress.completed_at is not None:
        return

    required_rounds = progress.series_progress_rounds.filter(excluded_at__isnull=True)
    if required_rounds.filter(completed_at__isnull=True).exists():
        return

    questions_count = required_rounds.aggregate(total=Sum("questions_count", default=0))["total"]
    if questions_count == 0:
        return

    progress.completed_at = event_time
    if progress.reward_status != "pending":
        progress.save(update_fields=["completed_at"])
        _award_player_completions(progress, event_time=event_time)
        return

    series_id = progress.series_identity.series_id
    if progress.player_id == progress.author_at_start_id:
        progress.reward_status = "ineligible"
    elif not progress.was_public_at_start:
        progress.reward_status = "ineligible"
        progress.reward_reason = "private_at_start"
    elif not QuizSeries.objects.filter(pk=series_id, status="public").exists():
        progress.reward_status = "ineligible"
        progress.reward_reason = "private_at_completion"
    else:
        RatingEntry.objects.create(
            recipient_id=progress.author_at_start_id,
            kind="author",
            amount=calculate_author_reputation(questions_count),
            earned_at=event_time,
            rules_version=RATING_RULES_VERSION,
            series_progress=progress,
        )
        progress.qualified_at = event_time
        progress.reward_status = "awarded"

    progress.save(update_fields=["completed_at", "qualified_at", "reward_status", "reward_reason"])
    _award_player_completions(progress, event_time=event_time)
    if progress.qualified_at is not None:
        award_achievements(progress, event_time=event_time)


def _award_player_completions(progress: SeriesProgress, *, event_time: datetime) -> None:
    """Выдаёт игроку достижения за завершение разных чужих квизов."""
    if progress.player_id == progress.author_at_start_id:
        return

    if connection.vendor == "postgresql":
        # Сериализуем подсчёт порогов при одновременном завершении разных квизов.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [f"quiz_statistics:player-completions:{progress.player_id}"],
            )

    count = SeriesProgress.objects.filter(
        player_id=progress.player_id,
        completed_at__isnull=False,
    ).exclude(author_at_start_id=progress.player_id).count()
    _award_thresholds(
        kind=AchievementAward.Kind.PLAYER_COMPLETED_SERIES,
        thresholds=PLAYER_COMPLETED_SERIES_THRESHOLDS,
        count=count,
        recipient_id=progress.player_id,
        event_time=event_time,
    )


@transaction.atomic
def award_achievements(progress: SeriesProgress, *, event_time: datetime) -> None:
    """Обновляет аудиторию автора и выдаёт достижения за достигнутые пороги."""
    if progress.qualified_at is None:
        return

    author_id = progress.author_at_start_id
    series_identity_id = progress.series_identity_id
    if connection.vendor == "postgresql":
        # Сериализуем выдачу по автору без блокировки строки пользователя.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [f"quiz_statistics:achievements:{author_id}"],
            )

    AuthorAudience.objects.get_or_create(
        author_id=author_id,
        player_id=progress.player_id,
        defaults={"first_qualified_at": event_time},
    )
    series_audience_count = SeriesProgress.objects.filter(
        series_identity_id=series_identity_id,
        qualified_at__isnull=False,
    ).count()
    author_audience_count = AuthorAudience.objects.filter(author_id=author_id).count()

    _award_thresholds(
        kind=AchievementAward.Kind.SERIES_AUDIENCE,
        thresholds=SERIES_AUDIENCE_THRESHOLDS,
        count=series_audience_count,
        recipient_id=author_id,
        event_time=event_time,
        series_identity_id=series_identity_id,
    )
    _award_thresholds(
        kind=AchievementAward.Kind.AUTHOR_AUDIENCE,
        thresholds=AUTHOR_AUDIENCE_THRESHOLDS,
        count=author_audience_count,
        recipient_id=author_id,
        event_time=event_time,
    )
    popular_series_count = AchievementAward.objects.filter(
        kind=AchievementAward.Kind.SERIES_AUDIENCE,
        threshold=POPULAR_SERIES_MIN_FINISHERS,
        notification_owner_id=author_id,
    ).count()
    _award_thresholds(
        kind=AchievementAward.Kind.AUTHOR_POPULAR_SERIES,
        thresholds=AUTHOR_POPULAR_SERIES_THRESHOLDS,
        count=popular_series_count,
        recipient_id=author_id,
        event_time=event_time,
    )


def _award_thresholds(
    *,
    kind: AchievementAward.Kind,
    thresholds: tuple[int, ...],
    count: int,
    recipient_id: int,
    event_time: datetime,
    series_identity_id: int | None = None,
) -> None:
    """Создаёт недостающие достижения и по одному уведомлению на каждое."""
    subject = {"author_id": None, "series_identity_id": series_identity_id}
    defaults = {"awarded_at": event_time, "notification_owner_id": recipient_id}
    if kind == AchievementAward.Kind.PLAYER_COMPLETED_SERIES:
        subject["notification_owner_id"] = recipient_id
        del defaults["notification_owner_id"]
    elif series_identity_id is None:
        subject["author_id"] = recipient_id

    for threshold in thresholds:
        if threshold > count:
            break
        award, created = AchievementAward.objects.get_or_create(
            kind=kind,
            threshold=threshold,
            defaults=defaults,
            **subject,
        )
        if created:
            Notification.objects.create(
                recipient_id=recipient_id,
                award=award,
                created_at=event_time,
            )


def _abandon_round_participations(round_ids: list[int], *, deleted_at: datetime) -> None:
    """Сохраняет итоги незавершённых участий перед удалением раундов."""
    session_ids = list(
        GameSession.objects.select_for_update()
        .filter(quiz_id__in=round_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    participations = RoundParticipation.objects.filter(
        participant__session_id__in=session_ids,
        status="in_progress",
    ).select_related("participant").order_by("pk")
    for participation in participations:
        finalize_round_participation(
            participation.participant,
            status="abandoned",
            finished_at=deleted_at,
        )


@transaction.atomic
def delete_round(quiz: Quiz) -> None:
    """Удаляет раунд, сохраняя историю и проверяя завершение серии."""
    if quiz.series_id is not None:
        QuizSeries.objects.select_for_update(no_key=True).get(pk=quiz.series_id)
    quiz = Quiz.objects.select_for_update().get(pk=quiz.pk)
    ensure_round_deletable(quiz)
    deleted_at = timezone.now()
    _abandon_round_participations([quiz.pk], deleted_at=deleted_at)

    pending_snapshots = SeriesProgressRound.objects.filter(
        round_identity__quiz_id=quiz.pk,
        completed_at__isnull=True,
        excluded_at__isnull=True,
    )
    progresses = SeriesProgress.objects.select_for_update().filter(
        pk__in=pending_snapshots.values("progress_id"),
    ).order_by("pk")
    for progress in progresses:
        if pending_snapshots.filter(progress=progress).update(excluded_at=deleted_at):
            evaluate_series_completion(progress, event_time=deleted_at)
    quiz.delete()


@transaction.atomic
def delete_series(series: QuizSeries) -> None:
    """Удаляет серию целиком, сохраняя историю без новых начислений."""
    series = QuizSeries.objects.select_for_update(no_key=True).get(pk=series.pk)
    if (
        SeriesRun.objects.filter(series=series, status="in_progress").exists()
        or GameSession.objects.filter(quiz__series=series, status="in_progress").exists()
    ):
        raise ValidationError("Нельзя удалить квиз, пока есть незавершённое прохождение.")
    round_ids = list(
        series.rounds.select_for_update().order_by("pk").values_list("pk", flat=True)
    )
    _abandon_round_participations(round_ids, deleted_at=timezone.now())
    series.delete()


def ensure_round_deletable(quiz: Quiz) -> None:
    """Проверяет, что раунд не идёт и не ожидается в начатом прогоне."""
    sessions = GameSession.objects.select_for_update(of=("self",)).order_by("pk")
    if quiz.series_id is None:
        sessions = sessions.filter(quiz=quiz)
    else:
        sessions = sessions.filter(quiz__series_id=quiz.series_id)
    locked_sessions = list(sessions.values_list("quiz_id", "status"))
    if any(quiz_id == quiz.pk and status == "in_progress" for quiz_id, status in locked_sessions):
        raise ValidationError("Нельзя удалить раунд, пока он идёт.")
    if quiz.series_id is not None and SeriesRun.objects.filter(
        series_id=quiz.series_id,
        current_round_index=quiz.round_order,
        status="in_progress",
    ).exists():
        raise ValidationError("Нельзя удалить раунд, пока его ожидает незавершённый прогон.")
