from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import SeriesIdentity, RoundIdentity, RoundParticipation, RatingAttempt, SeriesProgress, SeriesProgressRound, AnswerRating, RatingEntry
from quizzes.models import Quiz
from gameplay.models import GameParticipant, GameAnswer, GameSession
from .rules import calculate_player_rating, RATING_RULES_VERSION

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