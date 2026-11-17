from django.db import transaction

from .models import SeriesIdentity, RoundIdentity
from quizzes.models import Quiz

def get_round_identity(quiz: Quiz) -> RoundIdentity:
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