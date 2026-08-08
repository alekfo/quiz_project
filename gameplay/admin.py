from django.contrib import admin

from .models import GameSession, GameParticipant, GameAnswer


@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):

    list_display = "pk", "quiz", "mode", "status", "created_by", "started_at", "finished_at"
    ordering = ("-pk",)

@admin.register(GameParticipant)
class GameParticipantAdmin(admin.ModelAdmin):

    list_display = "pk", "session", "user", "score", "joined_at"
    ordering = ("-pk",)

@admin.register(GameAnswer)
class GameAnswerAdmin(admin.ModelAdmin):

    list_display = "pk", "participant", "question", "chosen_option", "is_correct", "is_skipped", "shown_at", "answered_at"
    ordering = ("-pk",)
