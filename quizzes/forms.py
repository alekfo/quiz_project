from django import forms
from django.core import validators

from .models import Quiz, Question, AnswerOption, Category
from users.models import User

class QuestionForm(forms.ModelForm):
    """
    при сохранении форм в инстанс модели Question,
    будут проигнорированы поля, не указанные в Meta,
    но в форме они будут
    """
    option_1 = forms.CharField(label="Вариант ответа №1", max_length=255)
    option_2 = forms.CharField(label="Вариант ответа №2", max_length=255)
    option_3 = forms.CharField(label="Вариант ответа №3", max_length=255)
    option_4 = forms.CharField(label="Вариант ответа №4", max_length=255)
    correct_index = forms.ChoiceField(
        label="Правильный вариант ответа",
        choices=[(0, "Вариант ответа №1"), (1, "Вариант ответа №2"), (2, "Вариант ответа №3"), (3, "Вариант ответа №4")],
        widget=forms.RadioSelect,
    )

    class Meta:
            model = Question
            fields = ["text", "order", "fact"]
            labels = {
                "text": "Введите текст вопроса",
                "order": "Номер по порядку",
                "fact": "Любопытный факт",

            }

class BaseQuestionFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        active_forms = [
            f for f in self.forms
            if f.has_changed() and not f.cleaned_data.get("DELETE", False)
        ]
        if len(active_forms) < 2:
            raise forms.ValidationError("Нужно минимум 2 вопроса")

        all_orders = [f.cleaned_data.get("order") for f in active_forms]
        if len(all_orders) != len(set(all_orders)):
            raise forms.ValidationError("У вопросов не должно быть одинакового порядкового номера order")

QuestionFormSet = forms.inlineformset_factory(
        Quiz, Question,
        form=QuestionForm,
        formset=BaseQuestionFormSet,
        extra=1,
        can_delete=True,
    )

class QuizForm(forms.ModelForm):

    class Meta:
        model = Quiz
        fields = ["title", "description", "category", "subject", "level", "status", "style", "audience"]
        labels = {
            "title": "Название квиза",
            "description": "Описание квиза",
            "category": "Категория квиза",
            "subject": "Тема квиза",
            "level": "Уровень сложности вопросов квиза",
            "status": "Уровень доступности квиза",
            "style": "Стиль вопросов квиза",
            "audience": "Аудитория квиза",

        }
        widgets = {
            "description": forms.Textarea(attrs={"rows":10, "cols": 30})
        }