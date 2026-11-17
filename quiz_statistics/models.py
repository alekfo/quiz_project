from django.db import models
from django.conf import settings

class SeriesIdentity(models.Model):

    series = models.OneToOneField(
        "quizzes.QuizSeries",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    original_series_id = models.PositiveBigIntegerField(unique=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=50)

class RoundIdentity(models.Model):

    quiz = models.OneToOneField(
        "quizzes.Quiz",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    original_quiz_id = models.PositiveBigIntegerField(unique=True)
    series_identity = models.ForeignKey(
        SeriesIdentity,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='round_identities',
    )
    title = models.CharField(max_length=50)

class RoundParticipation(models.Model):
    MODE_CHOICES = [
        ('solo', 'Одиночная игра'),
        ('multiplayer', 'Групповая игра'),
    ]

    STATUS_CHOICES = [
        ('in_progress', 'В процессе'),
        ('completed', 'Завершена'),
        ('abandoned', 'Заброшена'),
    ]

    RATING_MODE_CHOICES = [
        ('rated', 'С рейтингом'),
        ('training', 'Тренировка'),
        ('own', 'Собственный квиз'),
    ]

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='participations',
    )
    round_identity = models.ForeignKey(
        RoundIdentity,
        on_delete=models.PROTECT,
        related_name='participations',
    )
    participant = models.OneToOneField(
        "gameplay.GameParticipant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    original_participant_id = models.PositiveBigIntegerField(unique=True)
    original_session_id = models.PositiveBigIntegerField()
    mode = models.CharField(max_length=20, choices=MODE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="in_progress")
    rating_mode = models.CharField(max_length=20, choices=RATING_MODE_CHOICES)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    score = models.IntegerField(default=0)
    questions_count = models.PositiveIntegerField()
    correct_answers_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(
                fields=["player", "round_identity", "status"],
                name="stats_part_player_round_idx",
            ),
            models.Index(
                fields=["original_session_id"],
                name="stats_part_session_idx",
            ),
        ]