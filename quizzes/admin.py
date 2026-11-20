from django.contrib import admin
from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .models import Quiz, Question, AnswerOption, Category, QuizSeries
from quiz_statistics.services import ensure_round_editable


def check_rounds_editable(round_ids):
    """Блокирует и проверяет указанные раунды внутри транзакции admin POST."""
    rounds = Quiz.objects.select_for_update().filter(pk__in=round_ids).order_by("pk")
    for quiz in rounds:
        ensure_round_editable(quiz)


class QuizAdminForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()
        if not self.instance._state.adding:
            check_rounds_editable([self.instance.pk])
        return cleaned_data


class QuestionAdminForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()
        quiz = cleaned_data.get("quiz")
        quiz_id = quiz.pk if quiz is not None else self.instance.quiz_id
        if quiz_id is not None:
            check_rounds_editable([quiz_id])
        return cleaned_data

class AnswerOptionAdminForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()
        question = cleaned_data.get("question")
        if question is None and self.instance.question_id is not None:
            question = self.instance.question
        if question is not None:
            check_rounds_editable([question.quiz_id])
        return cleaned_data


class QuizInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        round_ids = {
            form.instance.pk
            for form in self.forms
            if not form.instance._state.adding and form.has_changed()
        }
        check_rounds_editable(round_ids)


class RoundContentDeleteMixin:
    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_deleted_objects(self, objs, request):
        if request.method == "POST":
            try:
                check_rounds_editable({self.get_round_id(obj) for obj in objs})
            except ValidationError as exc:
                return [], {}, set(), exc.messages
        return super().get_deleted_objects(objs, request)

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
    formset = QuizInlineFormSet

@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):

    inlines = [
        QuestionInline
    ]

    list_display = "pk", "user", "series", "type", "subject", "level", "created_at", "style", "audience", "time_limit_seconds", "points_per_correct", "dynamic_scoring_mode", "round_order"
    ordering = ("pk",)
    form = QuizAdminForm

    def get_queryset(self, request):
        return  Quiz.objects.select_related("user", "series").prefetch_related("questions__options")

@admin.register(Question)
class QuestionAdmin(RoundContentDeleteMixin, admin.ModelAdmin):

    inlines = [
        AnswerInline
    ]

    list_display = "pk", "quiz", "text", "order", "fact"
    ordering = ("-pk",)
    form = QuestionAdminForm

    def get_queryset(self, request):
        return  Question.objects.select_related("quiz").prefetch_related("options")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return (*fields, "quiz") if obj is not None else fields

    def get_round_id(self, obj):
        return obj.quiz_id

@admin.register(AnswerOption)
class AnswerOptionAdmin(RoundContentDeleteMixin, admin.ModelAdmin):

    list_display = "pk", "question", "text", "is_correct", "order"
    ordering = ("pk",)
    form = AnswerOptionAdminForm

    def get_queryset(self, request):
        return  AnswerOption.objects.select_related("question")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return (*fields, "question") if obj is not None else fields

    def get_round_id(self, obj):
        return obj.question.quiz_id

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
