from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

from django.conf import settings

from quizzes.models import Category, QuizSeries


class GenerationRequest(models.Model):
    #GenerationRequest — параметры (тема, кол-во вопросов, сложность, стиль), статус задачи, результат

    LEVEL_CHOICES = [
        ('common', 'Без уровня'),
        ('junior', 'Junior'),
        ('middle', 'Middle'),
        ('pro', 'Pro'),
    ]

    AUDIENCE_CHOICES = [
        ('common', 'Без классификатора'),
        ('children', 'Дети'),
        ('teens', 'Подростки'),
        ('middle_ages', 'Средние года'),
        ('elderly', 'Пожилые'),
    ]

    STYLE_CHOICES = [
        ('serious', 'Серьезный стиль'),
        ('humorous', 'Шутливый стиль'),
    ]

    STATUS_CHOICES = [
        ('in_progress', 'В процессе'),
        ('completed', 'Вопросы готовы, ждут проверки'),
        ('saved', 'Раунд сохранен'),
        ('discarded', 'Отменен пользователем'),
        ('failed', 'AI не ответил'),
    ]


    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='generation_requests')
    title = models.CharField(max_length=100)
    subject = models.CharField(max_length=100)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='generation_requests')
    series = models.ForeignKey(QuizSeries, null=True, blank=True, on_delete=models.SET_NULL, related_name='generation_requests')
    description = models.CharField(max_length=255, blank=True)
    questions = models.PositiveSmallIntegerField()
    level = models.CharField(choices=LEVEL_CHOICES, default='common')
    audience = models.CharField(choices=AUDIENCE_CHOICES, default='common')
    style = models.CharField(choices=STYLE_CHOICES, default='humorous')
    created_at = models.DateTimeField(auto_now_add=True)
    result = models.JSONField(null=True, blank=True, default=None)
    status = models.CharField(choices=STATUS_CHOICES, default='in_progress')
    quiz_status = models.CharField(choices=QuizSeries.STATUS_CHOICES)
    time_limit_seconds = models.IntegerField(default=50)
    points_per_correct = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])

    # невозможно создать инстанс у одного юзера при status="in_progress" - будет integrity
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="in_progress"),
                name="unique_in_progress_generation_request_per_user",
            )
        ]

    def __str__(self):
        return self.title