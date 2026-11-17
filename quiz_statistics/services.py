from django.db import transaction
from django.utils import timezone

from .models import SeriesIdentity, RoundIdentity, RoundParticipation, RatingAttempt, SeriesProgress, SeriesProgressRound
from quizzes.models import Quiz
from gameplay.models import GameParticipant

def get_round_identity(quiz: Quiz) -> RoundIdentity:
    """
    Создает в базе рейтинговые записи (SeriesIdentity и RoundIdentity
    для quiz нового прохождения. Или обновляет title у существующих, если
    они уже существовали
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