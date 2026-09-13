from django.contrib import admin

from .models import Quiz, Question, AnswerOption, Category, QuizSeries

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):

    list_display = "pk", "title"
    ordering = ("pk",)

class QuestionInline(admin.TabularInline):
    model = Question

class AnswerInline(admin.TabularInline):
    model = AnswerOption

class QuizInline(admin.TabularInline):
    model = Quiz

@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):

    inlines = [
        QuestionInline
    ]

    list_display = "pk", "user", "series", "type", "subject", "level", "created_at", "style", "audience", "time_limit_seconds",  "round_order"
    ordering = ("pk",)

    def get_queryset(self, request):
        return  Quiz.objects.select_related("user", "series").prefetch_related("questions__options")

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):

    inlines = [
        AnswerInline
    ]

    list_display = "pk", "quiz", "text", "order", "fact"
    ordering = ("-pk",)

    def get_queryset(self, request):
        return  Question.objects.select_related("quiz").prefetch_related("options")

@admin.register(AnswerOption)
class AnswerOptionAdmin(admin.ModelAdmin):

    list_display = "pk", "question", "text", "is_correct", "order"
    ordering = ("pk",)

    def get_queryset(self, request):
        return  AnswerOption.objects.select_related("question")

@admin.register(QuizSeries)
class QuizSeriesAdmin(admin.ModelAdmin):
    inlines = [
        QuizInline
    ]

    list_display = "pk", "title", "user", "description_short",  "category", "status"
    ordering = ("pk",)

    def get_queryset(self, request):
        return  QuizSeries.objects.select_related("user", "category").prefetch_related("rounds__questions__options")

    def description_short(self, obj: QuizSeries) -> str:
        if len(obj.description) < 30:
            return obj.description
        return obj.description[:30] + "..."
