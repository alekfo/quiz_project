from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    ai_quizzes_generated = models.PositiveIntegerField(default=0)
    ai_quizzes_limit_per_day = models.PositiveSmallIntegerField(default=1)
    is_subscribed = models.BooleanField(default=False)
    is_premium = models.BooleanField(default=False)
    email_confirmed = models.BooleanField(default=False)
    subscription_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'