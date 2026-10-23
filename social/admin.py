from django.contrib import admin

from .models import QuizSeriesLike, Follow, SavedQuizSeries

@admin.register(QuizSeriesLike)
class QuizSeriesLikeAdmin(admin.ModelAdmin):

    list_display = "pk", "series", "user", "created_at"
    list_select_related = ("series", "user")
    ordering = ("pk",)

@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):

    list_display = "pk", "follower", "following", "created_at"
    list_select_related = ("follower", "following")
    ordering = ("pk",)

@admin.register(SavedQuizSeries)
class SavedQuizSeriesAdmin(admin.ModelAdmin):

    list_display = "pk", "user", "series", "created_at"
    list_select_related = ("user", "series")
    ordering = ("pk",)

