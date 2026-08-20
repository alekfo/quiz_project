from django.contrib import admin

from .models import Room, RoomPlayer

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = "pk", "quiz", "game_session", "host", "status", "created_at"
    ordering = ("-pk",)

@admin.register(RoomPlayer)
class RoomPlayerAdmin(admin.ModelAdmin):
    list_display = "pk", "room", "user", "is_ready", "joined_at"
    ordering = ("-pk",)