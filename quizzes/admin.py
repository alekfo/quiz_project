from django.contrib import admin

from .models import Quiz, Question, AnswerOption, Category

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):

    list_display = "pk", "title"
    ordering = ("pk",)

class QuestionInline(admin.TabularInline):
    model = Question

class AnswerInline(admin.TabularInline):
    model = AnswerOption

@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):

    inlines = [
        QuestionInline
    ]

    list_display = "pk", "user", "title", "description", "type", "category", "subject", "level", "status", "created_at", "style"
    ordering = ("pk",)

    def get_queryset(self, request):
        return  Quiz.objects.select_related("user", "category").prefetch_related("questions__options")

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