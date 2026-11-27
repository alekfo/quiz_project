from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Exists, OuterRef, Q, Sum

from quiz_statistics.models import (
    AchievementAward,
    AnswerRating,
    AuthorAudience,
    Notification,
    RatingEntry,
    SeriesProgress,
    SeriesProgressRound,
)
from quiz_statistics.rules import (
    AUTHOR_AUDIENCE_THRESHOLDS,
    AUTHOR_POPULAR_SERIES_THRESHOLDS,
    POPULAR_SERIES_MIN_FINISHERS,
    SERIES_AUDIENCE_THRESHOLDS,
    calculate_author_reputation,
)


class Command(BaseCommand):
    """Проверяет целостность рейтинга, аудитории и достижений."""

    help = "Проверяет согласованность рейтинговых записей без изменения данных."

    def handle(self, *args, **options):
        """Выводит найденные расхождения и завершает проверку."""
        violations = self._rating_violations() + self._achievement_violations()
        for message in violations:
            self.stderr.write(message)
        if violations:
            raise CommandError(f"Нарушений: {len(violations)}")
        self.stdout.write(self.style.SUCCESS("Статистика согласована"))

    def _rating_violations(self) -> list[str]:
        """Сверяет записи журнала с ответами и накопительным прогрессом."""
        valid_player_entries = RatingEntry.objects.filter(
            kind="player",
            answer_rating_id=OuterRef("pk"),
            amount=OuterRef("points"),
            recipient_id=OuterRef("participation__player_id"),
            earned_at=OuterRef("answered_at"),
            rules_version=OuterRef("rules_version"),
        )
        bad_answers = AnswerRating.objects.filter(points__gt=0).filter(
            ~Exists(valid_player_entries),
        )
        violations = [
            f"AnswerRating {pk}: отсутствует соответствующее начисление игроку"
            for pk in bad_answers.order_by("pk").values_list("pk", flat=True).iterator()
        ]

        author_entries = RatingEntry.objects.filter(kind="author", series_progress_id=OuterRef("pk"))
        bad_progress = SeriesProgress.objects.filter(reward_status="awarded").filter(
            Q(qualified_at__isnull=True) | ~Exists(author_entries),
        )
        violations.extend(
            f"SeriesProgress {pk}: выданная награда не подтверждена журналом или датой"
            for pk in bad_progress.order_by("pk").values_list("pk", flat=True).iterator()
        )

        qualified = SeriesProgress.objects.filter(qualified_at__isnull=False)
        completed_rounds = SeriesProgressRound.objects.filter(
            progress_id=OuterRef("pk"),
            completed_at__isnull=False,
            excluded_at__isnull=True,
            questions_count__gt=0,
        )
        violations.extend(
            f"SeriesProgress {pk}: квалифицирующее завершение без завершённого раунда"
            for pk in qualified.filter(~Exists(completed_rounds))
            .order_by("pk").values_list("pk", flat=True).iterator()
        )
        audience_members = AuthorAudience.objects.filter(
            author_id=OuterRef("author_at_start_id"),
            player_id=OuterRef("player_id"),
        )
        violations.extend(
            f"SeriesProgress {pk}: игрок отсутствует в аудитории автора"
            for pk in qualified.filter(~Exists(audience_members))
            .order_by("pk").values_list("pk", flat=True).iterator()
        )

        entries = RatingEntry.objects.filter(kind="author").annotate(
            questions_count=Sum(
                "series_progress__series_progress_rounds__questions_count",
                filter=Q(series_progress__series_progress_rounds__excluded_at__isnull=True),
                default=0,
            ),
        ).select_related("series_progress").order_by("pk")
        for entry in entries.iterator():
            if (
                entry.recipient_id != entry.series_progress.author_at_start_id
                or entry.amount != calculate_author_reputation(entry.questions_count)
            ):
                violations.append(f"RatingEntry {entry.pk}: неверная сумма или получатель")
        return violations

    def _achievement_violations(self) -> list[str]:
        """Сверяет обязательные пороги и уведомления для выданных достижений."""
        kind = AchievementAward.Kind
        series_awards = defaultdict(set)
        author_awards = defaultdict(set)
        for award_kind, threshold, author_id, series_id in AchievementAward.objects.values_list(
            "kind", "threshold", "author_id", "series_identity_id",
        ).iterator():
            if award_kind == kind.SERIES_AUDIENCE:
                series_awards[series_id].add(threshold)
            else:
                author_awards[author_id, award_kind].add(threshold)

        valid_notices = Notification.objects.filter(
            award_id=OuterRef("pk"),
            recipient_id=OuterRef("notification_owner_id"),
        )
        violations = [
            f"AchievementAward {pk}: отсутствует уведомление для получателя"
            for pk in AchievementAward.objects.filter(~Exists(valid_notices))
            .order_by("pk").values_list("pk", flat=True).iterator()
        ]
        series_counts = (
            SeriesProgress.objects.filter(qualified_at__isnull=False)
            .values_list("series_identity_id").annotate(total=Count("pk"))
            .order_by("series_identity_id")
        )
        for series_id, count in series_counts.iterator():
            violations.extend(self._missing_thresholds(
                f"SeriesIdentity {series_id}", kind.SERIES_AUDIENCE,
                SERIES_AUDIENCE_THRESHOLDS, count, series_awards[series_id],
            ))

        audience_counts = (
            AuthorAudience.objects.values_list("author_id").annotate(total=Count("pk"))
            .order_by("author_id")
        )
        for author_id, count in audience_counts.iterator():
            violations.extend(self._missing_thresholds(
                f"User {author_id}", kind.AUTHOR_AUDIENCE, AUTHOR_AUDIENCE_THRESHOLDS,
                count, author_awards[author_id, kind.AUTHOR_AUDIENCE],
            ))

        popular_counts = (
            AchievementAward.objects.filter(
                kind=kind.SERIES_AUDIENCE, threshold=POPULAR_SERIES_MIN_FINISHERS,
            )
            .values_list("notification_owner_id").annotate(total=Count("series_identity_id", distinct=True))
            .order_by("notification_owner_id")
        )
        for author_id, count in popular_counts.iterator():
            violations.extend(self._missing_thresholds(
                f"User {author_id}", kind.AUTHOR_POPULAR_SERIES, AUTHOR_POPULAR_SERIES_THRESHOLDS,
                count, author_awards[author_id, kind.AUTHOR_POPULAR_SERIES],
            ))
        return violations

    def _missing_thresholds(
        self, subject: str, kind: str, thresholds: tuple[int, ...], count: int, awarded: set[int],
    ) -> list[str]:
        """Перечисляет достигнутые пороги, за которые ещё нет записи."""
        return [
            f"{subject}: отсутствует AchievementAward {kind} за порог {threshold}"
            for threshold in thresholds
            if threshold <= count and threshold not in awarded
        ]
