from django import forms
from django.core import validators

from .models import Quiz, Question, QuizSeries, Category
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

class QuizFormWithSeriesId(forms.ModelForm):
    points_per_correct = forms.IntegerField(label="Количество очков за правильный ответ", min_value=1, max_value=100,
                                            initial=1, required=False)

    class Meta:
        model = Quiz
        fields = ["subject", "level", "style", "audience", "time_limit_seconds", "points_per_correct", "dynamic_scoring_mode"]
        labels = {
            "subject": "Тема вопросов раунда",
            "level": "Уровень сложности вопросов раунда",
            "style": "Стиль вопросов раунда",
            "audience": "Аудитория вопросов раунда",
            "time_limit_seconds": "Отведенное время на ответ (в секундах)",
            "points_per_correct": "Количество очков за правильный ответ",
            "dynamic_scoring_mode": "Блиц-раунд",

        }

    def clean(self):
        """
        Проверка: если пользователь запонлнил чек-бокс dynamic_scoring_mode,
        то проставляем у points_per_correct 1, а если не заполнил dynamic_scoring_mode
        и points_per_correct is None - ошибка валидации
        """
        cleaned_data = super().clean()

        dynamic_scoring_mode = cleaned_data.get("dynamic_scoring_mode")
        points_per_correct = cleaned_data.get("points_per_correct")

        if dynamic_scoring_mode:
            cleaned_data["points_per_correct"] = 1
        elif points_per_correct is None:
            self.add_error(
                "points_per_correct",
                "Укажите количество очков за правильный ответ. Или выберите режим динамического подсчета очков (Блиц-раунд)",
            )
        return cleaned_data

class QuizForm(QuizFormWithSeriesId):
    """
          Наследует поля раунда (subject/level/style/audience/time_limit_seconds)
          от QuizFormWithSeriesId и добавляет поля будущей QuizSeries - используется,
          когда series_id НЕТ (создаём новый квиз с нуля, а не раунд в существующий).
          title/category/description/status объявлены прямо на классе, а не через
          Meta.fields - это поля QuizSeries, а не Quiz, Meta.model у формы остаётся
          Quiz (унаследован от родителя), так что form.save() их не тронет; достаёшь
          их вручную из cleaned_data в forms_valid() при QuizSeries.objects.create(...).
          """
    title = forms.CharField(label="Название квиза", max_length=50)
    category = forms.ModelChoiceField(label="Категория квиза", queryset=Category.objects.all())
    description = forms.CharField(label="Описание квиза", max_length=255, required=False, widget=forms.Textarea(attrs={"rows": 4}))
    status = forms.ChoiceField(label="Уровень доступности квиза", choices=QuizSeries.STATUS_CHOICES)

    field_order = ["title", "category", "description", "status",
                   "subject", "level", "style", "audience", "time_limit_seconds", "points_per_correct", "dynamic_scoring_mode"]


class QuizSeriesUpdateForm(forms.ModelForm):
    class Meta:
        model = QuizSeries
        fields = ["title", "description", "category", "status"]
        labels = {
            "title": "Название квиза",
            "description": "Описание квиза",
            "category": "Категория квиза",
            "status": "Уровень доступности квиза",

        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "cols": 30})
        }