from typing import List

from django import forms

from quizzes.models import Category, QuizSeries

LEVEL_CHOICES = [
    ('common', 'Без уровня'),
    ('junior', 'Junior'),
    ('middle', 'Middle'),
    ('pro', 'Pro'),
]

AUDIENCE_CHOICES = [
    ('common', 'Без классификатора'),
    ('children', 'Дети'),
    ('teens', 'Подростки'),
    ('middle_ages', 'Средние года'),
    ('elderly', 'Пожилые'),
]

STYLE_CHOICES = [
    ('serious', 'Серьезный стиль'),
    ('humorous', 'Шутливый стиль'),
]

def get_all_categories() -> List[tuple]:

    categories = Category.objects.all()

    CATEGORY_CHOICES = [
        (i_cat.pk, i_cat.title)
        for i_cat in categories
    ]

    return CATEGORY_CHOICES


class GenerationRequestForm(forms.Form):

    #поля при создании квиза
    title = forms.CharField(label="Наименование квиза", max_length=50)
    category = forms.ChoiceField(label="Категория квиза", choices=get_all_categories)
    description = forms.CharField(label="Описание квиза", max_length=100)
    quiz_status = forms.ChoiceField(label="Уровень доступности квиза", choices=QuizSeries.STATUS_CHOICES)

    #общие поля квиз/раунд
    subject = forms.CharField(label="Тема раунда", max_length=50)
    questions = forms.IntegerField(label="Количество вопросов в раунде", min_value=1)
    level = forms.ChoiceField(label="Уровень сложности вопросов", choices=LEVEL_CHOICES)
    audience = forms.ChoiceField(label="Целевая аудитория раунда", choices=AUDIENCE_CHOICES)
    style = forms.ChoiceField(label="Стиль вопросов раунда", choices=STYLE_CHOICES)
    time_limit_seconds = forms.IntegerField(label="Отведенное время на ответ (в секундах)")

class GenerationRequestFormWithSeries_id(forms.Form):

    # общие поля квиз/раунд
    subject = forms.CharField(label="Тема раунда", max_length=50)
    questions = forms.IntegerField(label="Количество вопросов в раунде", min_value=1)
    level = forms.ChoiceField(label="Уровень сложности вопросов", choices=LEVEL_CHOICES)
    audience = forms.ChoiceField(label="Целевая аудитория раунда", choices=AUDIENCE_CHOICES)
    style = forms.ChoiceField(label="Стиль вопросов раунда", choices=STYLE_CHOICES)
    time_limit_seconds = forms.IntegerField(label="Отведенное время на ответ (в секундах)")

class QuestionForm(forms.Form):
    """
    Форма ОДНОГО вопроса викторины.

    formset_factory ниже размножит эту форму на N экземпляров (по числу
    вопросов, которые вернул Claude) - каждое поле формы получит уникальное
    имя вида form-0-question, form-1-question, ... (0, 1, ... - индекс формы
    внутри formset'а). Мы сами это имя нигде не пишем - Django сам
    подставляет префикс form-N- при рендере и сам же его разбирает обратно
    при получении POST.
    """

    # сам текст вопроса, который пользователь может отредактировать
    question = forms.CharField(
        label="Вопрос",
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    # 4 варианта ответа - плоские отдельные поля, а не вложенный formset.
    # Вложенность (formset внутри formset) тут не нужна, потому что в промпте
    # generate_quiz_questions() мы всегда просим Claude вернуть ровно 4
    # варианта - количество вариантов не "плавающее", как количество вопросов.
    option_1 = forms.CharField(label="Вариант 1", max_length=255)
    option_2 = forms.CharField(label="Вариант 2", max_length=255)
    option_3 = forms.CharField(label="Вариант 3", max_length=255)
    option_4 = forms.CharField(label="Вариант 4", max_length=255)

    # Храним ИНДЕКС правильного варианта (0..3), а не сам текст ответа.
    # Если хранить текст ("correct_answer": "Париж") и дать пользователю
    # отредактировать текст варианта, то текст правильного ответа и текст
    # варианта разъедутся - придётся ловить рассинхрон. Индекс же всегда
    # однозначно указывает "какой из option_1..option_4 сейчас правильный",
    # независимо от того, что там написано.
    correct_index = forms.ChoiceField(
        label="Правильный вариант",
        choices=[(0, "Вариант 1"), (1, "Вариант 2"), (2, "Вариант 3"), (3, "Вариант 4")],
        widget=forms.RadioSelect,
    )

    # необязательный доп. факт от AI (см. quizzes.Question.fact) - required=False,
    # т.к. Claude не всегда возвращает непустой fact, а модель поля fact = blank=True
    fact = forms.CharField(
        label="Интересный факт",
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
    )


# formset_factory - НЕ создаёт формы сам по себе, а возвращает КЛАСС-фабрику,
# которая умеет породить/провалидировать N экземпляров QuestionForm разом:
#   - на рендере (unbound) он берёт initial=[...] и создаёт len(initial) форм;
#   - на приёме POST (bound) он читает служебные скрытые поля
#     form-TOTAL_FORMS / form-INITIAL_FORMS (см. {{ formset.management_form }}
#     в шаблоне) и по ним понимает, сколько форм разбирать из request.POST.
# extra=0 - "не добавляй ни одной лишней пустой формы поверх initial".
# По умолчанию formset_factory добавляет extra=1 лишнюю пустую форму - нам
# она не нужна, вопросов должно быть ровно столько, сколько сгенерировал AI.
QuestionFormSet = forms.formset_factory(QuestionForm, extra=0)