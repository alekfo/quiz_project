from django.shortcuts import render, reverse, redirect, get_object_or_404
from django.http import HttpResponse, HttpRequest

from .models import GenerationRequest
from .forms import GenerationRequestForm, QuestionFormSet
from .prompts import generate_quiz_questions
from quizzes.models import Category
from quizzes.services import create_quiz_from_any_data

def _questions_to_initial(questions: list[dict]) -> list[dict]:
    """
    Адаптер между форматом ответа Claude и форматом, который ожидает QuestionForm.

    Claude (см. prompts.generate_quiz_questions) возвращает вопрос так:
        {"question": "...", "options": ["a", "b", "c", "d"],
         "correct_answer": "b", "fact": "..."}

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
        correct_answer = q.get("correct_answer")

        # ищем позицию правильного ответа в списке options, чтобы заранее
        # отметить нужный radio-button (correct_index) в форме
        try:
            correct_index = options.index(correct_answer)
        except ValueError:
            # Claude не гарантирует строгое соответствие формату (иногда
            # correct_answer может не совпасть буквально ни с одним из
            # options - опечатка/лишний пробел и т.п.) - подстрахуемся,
            # чтобы не упасть с исключением, и отметим первый вариант
            correct_index = 0

        initial.append({
            "question": q.get("question", ""),
            "option_1": options[0] if len(options) > 0 else "",
            "option_2": options[1] if len(options) > 1 else "",
            "option_3": options[2] if len(options) > 2 else "",
            "option_4": options[3] if len(options) > 3 else "",
            "correct_index": correct_index,
            "fact": q.get("fact", ""),
        })
    return initial


def index(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = GenerationRequestForm(request.POST)
        if form.is_valid():

            instruction_data = {
                "quiz_title": request.POST.get("title", ""),
                "quiz_subject": request.POST.get("subject", ""),
                "quiz_questions": request.POST.get("questions", 0),
                "quiz_level": request.POST.get("level", ""),
                "quiz_audience": request.POST.get("audience", ""),
                "question_style": request.POST.get("style", ""),
                "quiz_category": request.POST.get("category", ""),
            }

            category = Category.objects.filter(name=request.clean_data.get("quiz_category", ""))

            res = generate_quiz_questions(instruction_data)

            gen_request = GenerationRequest.objects.create(
                user = request.user,
                title = instruction_data.get("quiz_title", ""),
                subject = instruction_data.get("quiz_subject", ""),
                category=category,
                questions = instruction_data.get("quiz_questions", 0),
                level = instruction_data.get("quiz_level", ""),
                audience = instruction_data.get("quiz_audience", ""),
                style = instruction_data.get("question_style", ""),
                result = res,
                status = "completed"
            )

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


def save(request: HttpRequest) -> HttpResponse:

    if request.method == "POST":
        # generation_request_id пришёл скрытым input'ом из temp_ai_quiz.html
        # (мы положили его туда в index()). По этому id находим ту самую
        # GenerationRequest - в ней уже лежат subject/level/audience/style/title,
        # которые пользователь на этой странице не редактирует, поэтому их не
        # нужно было тащить отдельными hidden-полями через форму.
        gen_request = get_object_or_404(
            GenerationRequest, id=request.POST.get("generation_request_id")
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

            # TODO: тут вызов функции из quizzes/services.py для сохранения
            # квиза после подтверждения пользователем - создаёт Quiz,
            # Question и AnswerOption на основе gen_request (title, subject,
            # level, audience, style) и questions_data (отредактированные
            # вопросы/варианты/правильный ответ/факт)
            quiz = create_quiz_from_any_data(gen_request, questions_data)

        url = reverse("ai_generator:index")
        return redirect(url)

    return redirect(reverse("ai_generator:index"))