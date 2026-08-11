import json
import logging
import requests

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import HttpResponse, HttpRequest
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from .models import GenerationRequest
from .forms import GenerationRequestForm, QuestionFormSet
from .prompts import generate_quiz_questions
from quizzes.models import Category
from quizzes.services import create_quiz_from_any_data

logger = logging.getLogger(__name__)

def _questions_to_initial(questions: list[dict]) -> list[dict]:
    """
    Адаптер между форматом ответа Claude и форматом, который ожидает QuestionForm.

    Claude (см. prompts.generate_quiz_questions) возвращает вопрос так:
        {"question": "...", "options": ["a", "b", "c", "d"],
         "correct_answer_index": "..", "fact": "..."}

    А QuestionForm ждёт словарь, ключи которого СОВПАДАЮТ С ИМЕНАМИ ПОЛЕЙ формы:
        {"question": "...", "option_1": "a", "option_2": "b", "option_3": "c",
         "option_4": "d", "correct_index": 1, "fact": "..."}

    Django formset сам не умеет "раскладывать" список options по 4 полям -
    он просто на рендере берёт initial[i][field_name] для каждого поля формы
    с индексом i. Поэтому перекладываем данные вручную, до создания formset'а.

    Возвращает список словарей - i-й словарь станет initial-данными i-й формы
    в formset'е (значит и количество форм в formset'е = len(questions)).
    """
    initial = []
    for q in questions:
        options = q.get("options", [])
        correct_answer_index = q.get("correct_answer_index")

        initial.append({
            "question": q.get("question", ""),
            "option_1": options[0] if len(options) > 0 else "",
            "option_2": options[1] if len(options) > 1 else "",
            "option_3": options[2] if len(options) > 2 else "",
            "option_4": options[3] if len(options) > 3 else "",
            "correct_index": int(correct_answer_index),
            "fact": q.get("fact", ""),
        })
    return initial

@login_required
def index(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = GenerationRequestForm(request.POST)
        if form.is_valid():

            instruction_data = {
                "quiz_title": form.cleaned_data.get("title", ""),
                "quiz_subject": form.cleaned_data.get("subject", ""),
                "quiz_questions": form.cleaned_data.get("questions"),
                "quiz_level": form.cleaned_data.get("level", ""),
                "quiz_audience": form.cleaned_data.get("audience", ""),
                "question_style": form.cleaned_data.get("style", ""),
            }
            try:
                res = generate_quiz_questions(instruction_data)
            except (requests.RequestException, RuntimeError, json.JSONDecodeError) as e:
                logger.warning("Claude generation failed: %s", e)
                messages.error(request, "Не удалось сгенерировать квиз, попробуйте ещё раз чуть позже.")
                return render(request, "ai_generator/ai_generator_index.html", {"form": form})

            gen_request = GenerationRequest.objects.create(
                user = request.user,
                title = instruction_data.get("quiz_title", ""),
                subject = instruction_data.get("quiz_subject", ""),
                category_id=form.cleaned_data['category'],
                questions = instruction_data.get("quiz_questions", 0),
                level = instruction_data.get("quiz_level", ""),
                audience = instruction_data.get("quiz_audience", ""),
                style = instruction_data.get("question_style", ""),
                result = res,
                status = "completed"
            )
            logger.info("Новый запрос к Claude API от пользователя %s "
                           "успешно прошел и добавлен в базу: GenerationRequest №%s", request.user.username, gen_request.pk)

            # Строим formset ТОЛЬКО с initial (никакого request.POST здесь) -
            # это даёт "unbound"-формы, они предназначены именно для первого
            # рендера страницы, а не для валидации данных пользователя.
            # initial - список словарей; i-й словарь = стартовые значения
            # i-й формы. Длина этого списка определяет, сколько форм создаст
            # formset (т.к. фабрика сделана с extra=0 - см. forms.py).
            formset = QuestionFormSet(
                initial=_questions_to_initial(res.get("questions", []))
            )

            context = {
                "res": res,               # сырые данные для "шапки" (тема, уровень и т.д.)
                "formset": formset,        # редактируемые пользователем вопросы
                "gen_request": gen_request,  # нужен gen_request.id - именно по нему
                                              # save() потом найдёт эту же генерацию в БД
            }
            return render(request, "ai_generator/temp_ai_quiz.html", context=context)

    context = {
        "form": GenerationRequestForm(),
    }

    return render(request, "ai_generator/ai_generator_index.html", context=context)

@login_required
def save(request: HttpRequest) -> HttpResponse:

    if request.method == "POST":
        # generation_request_id пришёл скрытым input'ом из temp_ai_quiz.html
        # (мы положили его туда в index()). По этому id находим ту самую
        # GenerationRequest - в ней уже лежат subject/level/audience/style/title,
        # которые пользователь на этой странице не редактирует, поэтому их не
        # нужно было тащить отдельными hidden-полями через форму.
        gen_request = get_object_or_404(
            GenerationRequest, id=request.POST.get("generation_request_id"), user=request.user
        )

        # Тут formset строится уже ИЗ request.POST - это "bound"-формы,
        # предназначенные для валидации. Django не спрашивает нас, сколько
        # форм разбирать - он сам прочитает служебные скрытые поля
        # form-TOTAL_FORMS / form-INITIAL_FORMS (их отрендерил
        # {{ formset.management_form }} в шаблоне) и по ним поймёт, сколько
        # форм искать среди form-0-question, form-1-question, ... в POST.
        formset = QuestionFormSet(request.POST)

        if formset.is_valid():
            # formset итерируется как список форм. f.cleaned_data - это уже
            # провалидированные и приведённые к нужному типу значения ПОСЛЕ
            # правок пользователя (а не то, что изначально вернул Claude).
            questions_data = []
            for f in formset:
                questions_data.append({
                    "text": f.cleaned_data["question"],
                    "fact": f.cleaned_data["fact"],
                    "options": [
                        f.cleaned_data["option_1"],
                        f.cleaned_data["option_2"],
                        f.cleaned_data["option_3"],
                        f.cleaned_data["option_4"],
                    ],
                    # correct_index приходит из ChoiceField строкой ("0", "1", ...),
                    # приводим к int, чтобы дальше можно было использовать как
                    # индекс списка options
                    "correct_index": int(f.cleaned_data["correct_index"]),
                })

            quiz = create_quiz_from_any_data(gen_request, questions_data)

            url = reverse("quizzes:quizzes_preview", kwargs={"pk": quiz.pk})
            logger.info("Пользователь %s сохранил квиз №%s в базу", request.user.username, quiz.pk)
            return redirect(url)

    return redirect(reverse("ai_generator:index"))