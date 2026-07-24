from django.db import models

from django.conf import settings

class Category(models.Model):
    title = models.CharField(max_length=50)
    name = models.CharField(max_length=50)

class Quiz(models.Model):
    TYPE_CHOICES = [
        ('ai', 'Сгенерировано AI'),
        ('by_user', 'Создано вручную'),
    ]

    STATUS_CHOICES = [
        ('private', 'Приватная викторина'),
        ('public', 'Публичная викторина'),
    ]

    LEVEL_CHOICES = [
        ('common', 'Без уровня'),
        ('junior', 'Junior'),
        ('middle', 'Middle'),
        ('pro', 'Pro'),
    ]

    STYLE_CHOICES = [
        ('serious', 'Серьезный стиль'),
        ('humorous', 'Шутливый стиль'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quizzes')
    title = models.CharField(max_length=50)
    description = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='quizzes')
    subject = models.CharField(max_length=50)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='private')
    created_at = models.DateTimeField(auto_now_add=True)
    style = models.CharField(max_length=20, choices=STYLE_CHOICES)

class Question(models.Model):

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField()
    order = models.PositiveIntegerField(default=0)
    fact = models.TextField(blank=True)


class AnswerOption(models.Model):
    question = models.ForeignKey(Question, related_name='options', on_delete=models.CASCADE)
    text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
