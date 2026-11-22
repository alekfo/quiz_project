from django.contrib import admin

from .models import AchievementAward, AnswerRating, Notification, RatingEntry


class StatisticsReadOnlyAdmin(admin.ModelAdmin):
    """Даёт уполномоченным сотрудникам доступ к просмотру записей рейтинга."""

    def has_add_permission(self, request):
        """Запрещает ручное добавление исторических записей."""
        return False

    def has_change_permission(self, request, obj=None):
        """Запрещает изменение исторических записей."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Запрещает удаление исторических записей."""
        return False


@admin.register(RatingEntry)
class RatingEntryAdmin(StatisticsReadOnlyAdmin):
    """Показывает журнал начислений."""

    list_display = ("id", "recipient", "kind", "amount", "earned_at")
    list_select_related = ("recipient",)


@admin.register(AnswerRating)
class AnswerRatingAdmin(StatisticsReadOnlyAdmin):
    """Показывает сохранённые оценки ответов."""

    list_display = ("id", "original_answer_id", "points", "reason", "answered_at")


@admin.register(AchievementAward)
class AchievementAwardAdmin(StatisticsReadOnlyAdmin):
    """Показывает выданные достижения."""

    list_display = ("id", "notification_owner", "kind", "threshold", "awarded_at")
    list_select_related = ("notification_owner",)


@admin.register(Notification)
class NotificationAdmin(StatisticsReadOnlyAdmin):
    """Показывает уведомления и время их прочтения."""

    list_display = ("id", "recipient", "award_id", "created_at", "read_at")
    list_select_related = ("recipient",)
