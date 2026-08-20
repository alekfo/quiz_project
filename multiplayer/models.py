from django.db import models
from django.conf import settings

from quizzes.models import Quiz, Question, AnswerOption
from gameplay.models import GameSession

class Room(models.Model):

    STATUS_CHOICES = [
        ('waiting', 'В ожидании'),
        ('in_progress', 'В процессе'),
        ('finished', 'Завершена'),
    ]

    #предположительно quiz будет меняться
    quiz = models.ForeignKey(Quiz, on_delete=models.SET_NULL, null=True, default=None)

    # предположительно game_session будет меняться
    game_session = models.ForeignKey(GameSession, null=True, on_delete=models.SET_NULL, default=None)

    token = models.CharField(max_length=32, unique=True)
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="own_rooms")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='waiting')
    created_at = models.DateTimeField(auto_now_add=True)

class RoomPlayer(models.Model):

    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="room_players")
    is_ready = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["room", "user"],
                name="unique_per_user_room",
            )
        ]