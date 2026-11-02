import json
import logging
from datetime import timedelta

import requests

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import Http404, HttpResponse, HttpRequest
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.contrib import messages
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.db.models import F
from django.contrib.auth import get_user_model

from .models import GenerationRequest
from .forms import GenerationRequestFormWithSeries_id, GenerationRequestForm, QuestionFormSet
from .prompts import generate_quiz_questions
from quizzes.models import QuizSeries, Category
from quizzes.services import create_quiz_from_any_data

logger = logging.getLogger(__name__)
User = get_user_model()

# Через сколько "in_progress" считается зависшей (воркер убит посреди запроса и т.п.).
# Должно быть заведомо больше максимальной длительности generate_quiz_questions():
# prompts._ask делает до 3 попыток по timeout=90 сек + паузы между ними (~4.5 мин).
GENERATION_STALE_AFTER = timedelta(minutes=6)

def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"

def _fail_if_stale(gen_request: GenerationRequest) -> None:
    """
    Переводит зависшую in_progress-генерацию в failed, чтобы она не блокировала
    пользователя навсегда (UniqueConstraint на in_progress + проверка в index()).
    UPDATE с условием по статусу, а не .save() - чтобы не затереть completed,
    если генерация всё-таки завершилась между чтением и записью.
    """
    if gen_request.status != "in_progress":
        return
    if gen_request.created_at > timezone.now() - GENERATION_STALE_AFTER:
        return
    GenerationRequest.objects.filter(pk=gen_request.pk, status="in_progress").update(status="failed")
    gen_request.refresh_from_db(fields=["status"])
    logger.warning("GenerationRequest №%s зависла в in_progress и переведена в failed", gen_request.pk)

def _index_url(gen_request: GenerationRequest) -> str:
    """Форма генерации, с которой была запущена эта GenerationRequest."""
    if gen_request.series_id:
        return reverse("ai_generator:index_for_series", kwargs={"series_id": gen_request.series_id})
    return reverse("ai_generator:index")

def _review_context(gen_request: GenerationRequest, formset) -> dict:
    """Общий контекст temp_ai_quiz.html для review() и для save() с невалидным formset'ом."""
    return {
        "res": gen_request.result,   # сырые данные для "шапки" (тема, уровень и т.д.)
        "formset": formset,          # редактируемые пользователем вопросы
        "gen_request": gen_request,  # по gen_request.pk строятся адреса save/discard
        "series_id": gen_request.series_id,
    }

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
def index(request: HttpRequest, series_id=None) -> HttpResponse:
    """
    Одна вьюха на два сценария (аналогично quizzes.views.QuizCreateView) - сама
    генерация через Claude не отличается, разница только в том, куда пойдёт
    результат:
      - URL 'ai_generator:index'            (series_id=None) -> сгенерированный
        квиз позже станет раундом новой QuizSeries
      - URL 'ai_generator:index_for_series' (series_id=<pk>) -> раундом уже
        существующей серии
    series_id сохраняется в GenerationRequest.series - save() берёт его оттуда,
    а не из скрытого поля формы.

    Порядок проверок (и для GET, и для POST):
      1. есть активная генерация (in_progress / completed) -> на review(),
         новую запустить нельзя, пока эта не сохранена или не отклонена;
      2. дневной лимит;
      3. только потом форма / запрос к Claude.
    """
    series = None
    if series_id:
        series = get_object_or_404(QuizSeries, pk=series_id, user=request.user)

    active_request = GenerationRequest.objects.filter(
        user=request.user, status__in=["in_progress", "completed"]
    ).order_by("-created_at").first()

    if active_request:
        _fail_if_stale(active_request) # переводим в статус failed зависшие запросы
        if active_request.status == "completed":
            messages.info(request, "У вас есть несохранённый результат генерации. "
                                   "Сохраните его или откажитесь, чтобы запустить новую.")
        if active_request.status in ("in_progress", "completed"):
            return redirect("ai_generator:review", pk=active_request.pk)

    # Дневной лимит: failed не считается (за неё не заплачено), discarded - считается.
    generated_today = GenerationRequest.objects.filter(
        user=request.user,
        created_at__date=timezone.localdate(),
    ).exclude(status="failed").count()
    if generated_today >= request.user.ai_quizzes_limit_per_day:
        logger.warning("Daily limit reached for user=%s (%d/%d)", request.user.username, generated_today, request.user.ai_quizzes_limit_per_day)
        messages.error(request, 'Дневной лимит ai-генераций исчерпан. Возвращайтесь завтра.')
        if series:
            return redirect("quizzes:quizzes_details", pk=series.pk)
        return redirect("quizzes:menu")

    if series:
        form_class = GenerationRequestFormWithSeries_id
    else:
        form_class = GenerationRequestForm

    if request.method == "POST":
        form = form_class(request.POST)
        if form.is_valid():
            if series:
                title = series.title
                category = series.category_id
                description = series.description
                quiz_status = series.status
            else:
                title = form.cleaned_data.get("title", "")
                category = form.cleaned_data.get("category", "")
                description = form.cleaned_data.get("description", "")
                quiz_status = form.cleaned_data.get("quiz_status", "private")

            instruction_data = {
                "quiz_title": title,
                "quiz_category": Category.objects.get(pk=category).title,
                "quiz_subject": form.cleaned_data.get("subject", ""),
                "quiz_questions": form.cleaned_data.get("questions"),
                "quiz_level": form.cleaned_data.get("level", ""),
                "quiz_audience": form.cleaned_data.get("audience", ""),
                "question_style": form.cleaned_data.get("style", ""),
            }
            # Запись создаётся ДО вызова Claude и сразу коммитится (отдельный atomic -
            # чтобы IntegrityError не ломал внешнюю транзакцию): с этого момента её
            # видят другие вкладки/запросы, а UniqueConstraint на in_progress не даст
            # тому же пользователю запустить вторую генерацию параллельно.
            try:
                with transaction.atomic():
                    new_gen_request = GenerationRequest.objects.create(
                        user=request.user,
                        title=title,
                        description=description,
                        subject=instruction_data.get("quiz_subject", ""),
                        category_id=category,
                        series=series,
                        questions=instruction_data.get("quiz_questions", 0),
                        level=instruction_data.get("quiz_level", ""),
                        audience=instruction_data.get("quiz_audience", ""),
                        style=instruction_data.get("question_style", ""),
                        time_limit_seconds=form.cleaned_data.get("time_limit_seconds", 50),
                        points_per_correct=form.cleaned_data.get("points_per_correct", 1),
                        status="in_progress",
                        quiz_status=quiz_status
                    )
            except IntegrityError:
                messages.error(request, "Генерация уже идёт, дождитесь результата.")
                return redirect(request.path)

            # Сам вызов Claude - БЕЗ transaction.atomic(): это долгий сетевой запрос,
            # держать на нём открытую транзакцию незачем, откатывать тут нечего.
            try:
                res = generate_quiz_questions(instruction_data)
                if not isinstance(res, dict) or not res.get("questions"):
                    raise RuntimeError("Claude returned a response without questions")
            except (requests.RequestException, RuntimeError, json.JSONDecodeError) as e:
                logger.warning("Claude generation failed: %s", e)
                new_gen_request.status = "failed"
                new_gen_request.save(update_fields=["status"])
                messages.error(request, "Не удалось сгенерировать квиз, попробуйте ещё раз чуть позже.")
                return render(request, "ai_generator/ai_generator_index.html", {"form": form})
            except Exception:
                # непредвиденная ошибка - всё равно снимаем замок in_progress, 500 отдаём как есть
                new_gen_request.status = "failed"
                new_gen_request.save(update_fields=["status"])
                raise

            with transaction.atomic():
                new_gen_request.result = res
                new_gen_request.status = "completed"
                new_gen_request.save(update_fields=["result", "status"])
                User.objects.filter(pk=request.user.pk).update(ai_quizzes_generated=F("ai_quizzes_generated") + 1)

            logger.info("Новый запрос к Claude API от пользователя %s "
                           "успешно прошел и добавлен в базу: GenerationRequest №%s", request.user.username, new_gen_request.pk)

            #переходим на ревью сгенерированного раунда для редиктирования/подтверждения
            return redirect("ai_generator:review", pk=new_gen_request.pk)
    else:
        form = form_class()

    context = {
        "form": form,
    }

    return render(request, "ai_generator/ai_generator_index.html", context=context)

@login_required
def review(request: HttpRequest, pk: int) -> HttpResponse:
    """
    Страница GenerationRequest по GET - обновлять можно сколько угодно, это чтение из базы.
    Только владелец (чужой pk -> 404). Что показывается, зависит от статуса:
      - in_progress -> экран ожидания (опрашивает status() и сам перезагрузится);
      - completed   -> formset с вопросами на проверку;
      - failed      -> сообщение об ошибке и обратно на форму генерации;
      - saved / discarded -> 404, с этой генерацией уже всё сделано.
    """
    gen_request = get_object_or_404(
        GenerationRequest.objects.select_related("category", "series"),
        pk=pk,
        user=request.user,
    )
    _fail_if_stale(gen_request)

    if gen_request.status == "in_progress":
        return render(request, "ai_generator/generation_wait.html", {"gen_request": gen_request})

    if gen_request.status == "failed":
        messages.error(request, "Не удалось сгенерировать квиз, попробуйте ещё раз чуть позже.")
        return redirect(_index_url(gen_request))

    if gen_request.status != "completed":
        raise Http404

    # Строим formset ТОЛЬКО с initial (никакого request.POST здесь) -
    # это даёт "unbound"-формы, они предназначены именно для первого
    # рендера страницы, а не для валидации данных пользователя.
    # initial - список словарей; i-й словарь = стартовые значения
    # i-й формы. Длина этого списка определяет, сколько форм создаст
    # formset (т.к. фабрика сделана с extra=0 - см. forms.py).
    formset = QuestionFormSet(
        initial=_questions_to_initial(gen_request.result.get("questions", []))
    )
    return render(request, "ai_generator/temp_ai_quiz.html", context=_review_context(gen_request, formset))

@login_required
@require_GET
def status(request: HttpRequest, pk: int) -> HttpResponse:
    """
    Эндпоинт для экрана ожидания (HTMX-опрос раз в несколько секунд).
    Пока генерация идёт - пустой 204 (HTMX ничего не делает). Как только статус
    сменился - 204 + HX-Redirect на review(), а та уже сама решает, что показать
    (вопросы / ошибка). Без HTMX - обычный редирект туда же.
    """
    gen_request = get_object_or_404(GenerationRequest, pk=pk, user=request.user)
    _fail_if_stale(gen_request)
    review_url = reverse("ai_generator:review", kwargs={"pk": gen_request.pk})

    if not _is_htmx(request):
        return redirect(review_url)

    response = HttpResponse(status=204)
    if gen_request.status != "in_progress":
        response["HX-Redirect"] = review_url
    return response

@login_required
def save(request: HttpRequest, pk: int) -> HttpResponse:
    """
    Последний шаг генерации - тут formset с правками пользователя реально
    превращается в Quiz/Question/AnswerOption (через create_quiz_from_any_data,
    см. quizzes/services.py - там же и лежит вся логика "новая серия или
    существующая").
    Принимает только свою GenerationRequest в статусе completed и в той же
    транзакции переводит её в saved - повторный POST получит 404.
    series_id берётся из GenerationRequest.series (None -> создаётся новая серия).
    """
    if request.method == "POST":
        # subject/level/audience/style/title и т.д. уже лежат в GenerationRequest -
        # пользователь на странице проверки их не редактирует, из формы приходят
        # только вопросы.
        gen_request = get_object_or_404(
            GenerationRequest.objects.select_related("category", "series"),
            pk=pk,
            user=request.user,
            status="completed",
        )
        series_id = gen_request.series_id

        # Тут formset строится уже ИЗ request.POST - это "bound"-формы,
        # предназначенные для валидации. Django не спрашивает нас, сколько
        # форм разбирать - он сам прочитает служебные скрытые поля
        # form-TOTAL_FORMS / form-INITIAL_FORMS (их отрендерил
        # {{ formset.management_form }} в шаблоне) и по ним поймёт, сколько
        # форм искать среди form-0-question, form-1-question, ... в POST.
        formset = QuestionFormSet(request.POST)

        if not formset.is_valid():
            # Рендерим ту же страницу с bound-формсетом: правки пользователя и
            # ошибки остаются на экране, генерация не теряется.
            messages.error(request, "Проверьте вопросы: не все поля заполнены корректно.")
            return render(request, "ai_generator/temp_ai_quiz.html", context=_review_context(gen_request, formset))

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
        with transaction.atomic():
            # UPDATE с условием по статусу вместо gen_request.save(): из двух
            # одновременных POST (двойной клик, две вкладки) строку обновит только
            # один - второй дождётся его коммита, получит 0 строк и раунд не создаст.
            updated = GenerationRequest.objects.filter(
                pk=gen_request.pk, status="completed"
            ).update(status="saved")
            if not updated:
                raise Http404
            quiz = create_quiz_from_any_data(gen_request, questions_data, series_id)
        # Редирект зависит от того, с чем мы имели дело - тем же способом,
        # что и в quizzes.views.QuizCreateView.get_success_url: если это
        # был новый раунд в уже существующей серии - возвращаемся на страницу
        # серии (quizzes_details), если только что создали серию с нуля -
        # ведём на превью нового квиза (quizzes_preview).
        if series_id:
            url = reverse("quizzes:quizzes_details", kwargs={"pk": series_id})
            success_info = "Новый раунд успешно создан"
            logger.info("Пользователь %s добавил раунд №%s в квиз №%s", request.user.username, quiz.pk, quiz.series)
        else:
            url = reverse("quizzes:quizzes_preview", kwargs={"pk": quiz.series_id})
            success_info = "Новый квиз успешно создан"
            logger.info("Пользователь %s сохранил серию №%s и квиз №%s в базу", request.user.username, quiz.series,
                        quiz.pk)
        messages.success(request, success_info)
        return redirect(url)

    return redirect("ai_generator:review", pk=pk)

@login_required
@require_POST
def discard(request: HttpRequest, pk: int) -> HttpResponse:
    """
    «Не сохранять»: completed -> discarded. Только POST (меняет данные) и только
    своя генерация в статусе completed, иначе 404. Дневной лимит при этом
    потрачен - запрос к Claude уже оплачен.
    """
    gen_request = get_object_or_404(
        GenerationRequest,
        pk=pk,
        user=request.user,
        status="completed",
    )
    gen_request.status = "discarded"
    gen_request.save(update_fields=["status"])
    if gen_request.series_id:
        url = reverse("quizzes:quizzes_details", kwargs={"pk": gen_request.series_id})
        success_info = "Сгенерированный раунд отклонен"
        logger.info("Пользователь %s отклонил сгенерированный раунд", request.user.username)
    else:
        url = reverse("quizzes:menu")
        success_info = "Сгенерированный квиз отклонен"
        logger.info("Пользователь %s отклонил сгенерированный квиз", request.user.username)
    messages.success(request, success_info)
    return redirect(url)
