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

    #предположительно current_quiz будет меняться
    current_quiz = models.ForeignKey(Quiz, on_delete=models.SET_NULL, null=True, blank=True, default=None, related_name="rooms")

    # предположительно current_game_session будет меняться и указывать на активную сессию (или последнюю)
    current_game_session = models.ForeignKey(GameSession, null=True, blank=True, on_delete=models.SET_NULL, default=None, related_name="current_for_rooms")

    title = models.CharField(max_length=100)
    token = models.CharField(max_length=32, unique=True)
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="own_rooms")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='waiting')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class RoomPlayer(models.Model):

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="room_players")
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