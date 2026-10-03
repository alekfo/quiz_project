from django.contrib.auth.models import AbstractUser
from django.db import models

def avatar_directory_path(instance: "User", filename: str) -> str:
    """Путь для сохранения аватара: media/users/<pk>/avatar/<filename>.

    К этому моменту имя файла уже сгенерировано как UUID (см.
    ProfileUpdateForm.clean_avatar) — лишней санитизации не нужно, коллизий
    между пользователями не будет из-за подпапки с pk.
    """
    return f"users/{instance.pk}/avatar/{filename}"

class User(AbstractUser):
    email = models.EmailField(unique=True)
    ai_quizzes_generated = models.PositiveIntegerField(default=0)
    ai_quizzes_limit_per_day = models.PositiveSmallIntegerField(default=1)
    is_subscribed = models.BooleanField(default=False)
    is_premium = models.BooleanField(default=False)
    email_confirmed = models.BooleanField(default=False)
    subscription_expires_at = models.DateTimeField(null=True, blank=True)
    # null=True не добавляем: конвенция Django для File/ImageField - отсутствие файла
    # хранить как '', а не NULL, иначе filter(avatar="") и filter(avatar__isnull=True)
    # расходятся на два разных "пусто".
    avatar = models.ImageField(blank=True, upload_to=avatar_directory_path)

    class Meta:
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'