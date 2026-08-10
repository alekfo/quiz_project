from django.db import models
from django.conf import settings

from quizzes.models import Quiz, Question, AnswerOption

class GameSession(models.Model):

    MODE_CHOICES = [
        ('solo', 'Одиночная игра'),
        ('multiplayer', 'Групповая игра'),
    ]

    STATUS_CHOICES = [
        ('in_progress', 'В процессе'),
        ('completed', 'Завершена'),
        ('abandoned', 'Заброшена'),
    ]

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="game_sessions")
    mode = models.CharField(max_length=50, choices=MODE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='in_progress')
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_game_sessions")

class GameParticipant(models.Model):

    session = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="participants")
    score = models.IntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "user"], name="unique_session_participant")
        ]

class GameAnswer(models.Model):

    participant = models.ForeignKey(GameParticipant, on_delete=models.CASCADE, related_name="participants_answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="participants_answers")
    #делаем on_delete=models.SET_NULL т.к при редактирвание квиза и редактировании ответов - старые удаляются из БД, что привело бы к удалению GameAnswer при CASCADE
    chosen_option = models.ForeignKey(AnswerOption, on_delete=models.SET_NULL, related_name="participants_answers", null=True)
    is_correct = models.BooleanField(default=False)
    is_skipped = models.BooleanField(default=False)
    shown_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["participant", "question"], name="unique_question_participant")
        ]