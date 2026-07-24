from django.db import models

from django.conf import settings

from quizzes.models import Category


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
        ('pending', 'Ожидает'),
        ('in_progress', 'В процессе'),
        ('completed', 'Завершено'),
    ]


    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='generation_requests')
    title = models.CharField(max_length=100)
    subject = models.CharField(max_length=100)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='generation_requests')
    questions = models.PositiveSmallIntegerField()
    level = models.CharField(choices=LEVEL_CHOICES, default='common')
    audience = models.CharField(choices=AUDIENCE_CHOICES, default='common')
    style = models.CharField(choices=STYLE_CHOICES, default='humorous')
    created_at = models.DateTimeField(auto_now_add=True)
    result = models.JSONField(default=dict)
    status = models.CharField(choices=STATUS_CHOICES, default='pending')