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

QuestionFormSet = forms.inlineformset_factory(
        Quiz, Question,
        form=QuestionForm,
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