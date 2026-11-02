import logging
from typing import List

from django.db.models import Count
from django.shortcuts import render, redirect, reverse
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.contrib.auth.decorators import login_required
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.views.decorators.http import require_POST

from .models import GameSession, GameParticipant, GameAnswer, SeriesRun
from quizzes.models import Quiz, Question, AnswerOption, QuizSeries
from multiplayer.views import _notify_room
from .services import get_series_progress, get_answer_stats, get_role

logger = logging.getLogger(__name__)

def _notify_session(session: GameSession) -> None:
    """
    Сигнал "что-то в сессии изменилось" всем открытым WebSocket-соединениям
    этой сессии — сам
    HTML не передаём, каждый подключённый рендерит фрагмент под себя.
    """
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"session_{session.pk}",
        {"type": "session.update"},
    )

def _update_gameAnswer(answer_pk, opt: AnswerOption, session:GameSession, request: HttpRequest) -> GameAnswer:
    """
    Получаем GameAnswer по pk, обновляем его данные по AnswerOption, сохраняем
    :param answer_pk: pk текущего ответа на вопрос от пользователя
    :param opt: вариант ответа для определения параметра is_correct у GameAnswer
    :return: возвращаем GameAnswer
    """

    answ = get_object_or_404(
        GameAnswer,
        pk=answer_pk,
        participant__user=request.user,
        participant__session=session
    )

    #проверяем не пришел ли ответ на вопрос, который уже переключен
    if answ.question_id != session.current_question_id:
        raise PermissionDenied

    #вычисляем время, затраченное на вопрос для дальнейшего определения is_skipped
    elapsed = (timezone.now() - answ.shown_at).total_seconds()

    #определяем наличие таймаута
    timed_out = elapsed > session.quiz.time_limit_seconds

    if answ.answered_at or answ.is_skipped:
        raise PermissionDenied("На этот вопрос уже отвечали или он был пропущен")

    if opt and opt.question_id != answ.question_id:
        raise PermissionDenied
    with transaction.atomic():
        answ.chosen_option = opt
        answ.is_skipped = opt is None or timed_out
        answ.is_correct = opt is not None and opt.is_correct and not timed_out
        answ.answered_at = timezone.now()
        answ.save()
        transaction.on_commit(lambda: _notify_session(session))
    return answ

def _update_total_score(sess: GameSession, req: HttpRequest) -> GameParticipant:
    """
    Функция для изменения score у текущего GameParticipant
    :param session: текущая сессия
    :param request: данные запроса для получения USER
    :return: возвращает текущий GameParticipant
    """

    participant = get_object_or_404(GameParticipant, session=sess, user=req.user)
    #меняем значение score прямо в БД (на вермя выполнения update другие транзакции не доступны)
    GameParticipant.objects.filter(pk=participant.pk).update(score=F('score') + sess.quiz.points_per_correct)
    #нужно синхронизировать participant с БД, т.к мы меняли score в обход него
    participant.refresh_from_db(fields=["score"])
    return participant


def _check_and_make_complete(sess: GameSession) -> GameSession:
    """
    Проверяем всех participant данной sess, если хоть у одного есть незавершенность участия в этой сессии (if not participant.finished_at) -
    прерываем цикл и возвращаем сессию без изменения
    :param sess: сессия для проверки завершенности
    :return: возвращает сессию (либо с измененным status на completed ли бо такую же
    """
    #если current_question is None считаем что сессия завершена
    if sess.current_question is None:
        with transaction.atomic():
            #проставляем время завершения у всех участников сессии
            for participant in sess.participants.all():
                if not participant.finished_at:
                    participant.finished_at = timezone.now()
                    participant.save()
            #меняем статус сессии на completed
            sess.status = "completed"
            sess.save(update_fields=["status"])

            #продвигаем раунд в series_run
            if sess.series_run is not None:
                curr_series_run = _advance_series_run(sess.series_run, completed_round_order=sess.quiz.round_order)

            if sess.room_id:
                #меняем статус комнаты с in_progress на waiting
                #и сбрасываем current_game_session у комнаты
                sess.room.status = "waiting"
                sess.room.current_game_session = None
                sess.room.save(update_fields=["status", "current_game_session"])

                #переключаем готовность у всех членов комнаты
                sess.room.room_players.update(is_ready=False)

                #если после продвижения раунда в series_run в _advance_series_run серия приняла статус completed,
                #то у комнаты удаляем current_series и current_series_run
                if sess.series_run.status == "completed":
                    sess.room.current_series = None
                    sess.room.current_series_run = None
                    sess.room.save(update_fields=["current_series", "current_series_run"])

                transaction.on_commit(lambda: _notify_room(sess.room))


            # on_commit, а не прямой вызов: этот notify всё ещё внутри
            # транзакции, а WS-консьюмер читает БД через отдельное
            # соединение — без on_commit он может успеть выполнить свой
            # запрос раньше, чем эта транзакция закоммитится, и увидеть
            # старый status/current_question. С on_commit колбэк
            # _notify_session откладывается и реально выполняется только
            # после того, как транзакция под этим with transaction.atomic()
            # (внешняя, если это вложенный вызов) успешно закоммитится —
            # то есть уже по гарантированно свежим данным.
            transaction.on_commit(lambda: _notify_session(sess))

    return sess


def _advance_series_run(series_run: SeriesRun, completed_round_order: int) -> SeriesRun:
    """
    продвигает раунд в SeriesRun в соло
    """
    with transaction.atomic():
        #__gt — это field lookup Django ORM ("greater than"), транслируется в WHERE order > значение на уровне SQL. В _check_and_advance_round:
        #Логика: "найди среди вопросов те, у которых order строго больше текущего, отсортируй по возрастанию, возьми первый" — то есть ближайший следующий по значению, а не по позиции в списке.
        next_round = series_run.series.rounds.filter(
            round_order__gt=completed_round_order
        ).order_by("round_order").first()

        if next_round:
            series_run.current_round_index = next_round.round_order
        else:
            series_run.current_round_index = None
            series_run.status = "completed"
            series_run.finished_at = timezone.now()

        series_run.save(update_fields=["current_round_index", "status", "finished_at"])
        return series_run


def _play_multiplayer_or_solo(request: HttpRequest, session: GameSession, participant: GameParticipant, role: str):

    # проверяем для mode=multiplayer, завершена ли для всех сессия
    if session.current_question == None:
        # проверяем, завершена ли GameSession в целом для всех GameParticipant, т.к как нет текущего вопроса
        session = _check_and_make_complete(session)
        if session.status == "completed":
            #считаем что сессия завершена у всех GameParticipant
            return  redirect("gameplay:result", pk=session.pk)

    if role == "player":
        current_answer, _ = GameAnswer.objects.get_or_create(
            participant=participant,
            question=session.current_question
        )

        already_answered = bool(current_answer and (current_answer.answered_at or current_answer.is_skipped))

        elapsed = (timezone.now() - current_answer.shown_at).total_seconds()
        # max(0, ...) - если игрок провозился дольше лимита, time_limit_seconds - elapsed
        # уйдёт в минус; на экране должно быть "0", а не отрицательное число
        remaining_seconds = max(0, int(session.quiz.time_limit_seconds - elapsed))
        points_gained = session.quiz.points_per_correct if current_answer.is_correct else 0

    #вспомогательные переменные и is_last_question
    questions = list(session.quiz.questions.all())  # уже по order, из prefetch
    current_index = next(i for i, q in enumerate(questions) if q.pk == session.current_question_id)
    question_number = current_index + 1
    is_last_question = current_index == len(questions) - 1

    answers_progress = []
    streak = 0
    if role == "player":
        answers_by_question = {a.question_id: a for a in participant.participants_answers.all()}
        # свежий current_answer из get_or_create — prefetch мог его не застать
        answers_by_question[current_answer.question_id] = current_answer

        # определеяем прогресс каждого вопроса из questions
        for q in questions:
            a = answers_by_question.get(q.pk)
            if a is None or not (a.answered_at or a.is_skipped):
                state = "current" if q.pk == session.current_question_id else "pending"
            elif a.is_correct:
                state = "correct"
            elif a.is_skipped:
                state = "skipped"
            else:
                state = "wrong"
            answers_progress.append({"question": q, "state": state})

        #Определяем streak
        # серия: с текущего вопроса назад, до первого неверного
        for item in reversed(answers_progress[:current_index + 1]):
            if item["state"] == "current":  # текущий без ответа — не считается и не прерывает
                continue
            if item["state"] != "correct":
                break
            streak += 1

    #показывать ли кнопку Следующий вопрос
    if session.mode == "solo":
        can_advance = already_answered
    elif session.mode == "multiplayer":
        can_advance = (session.room.host_id == request.user.id) and (role == "host_observer" or already_answered)


    context = {
        "mode": session.mode,
        "role": role,
        "current_session": session,
        "current_question": session.current_question,
        "question_number": question_number,
        "question_count": len(questions),
        "is_last_question": is_last_question,
        "current_answer": current_answer if role == "player" else None,
        "already_answered": already_answered if role == "player" else None,
        "points_gained": points_gained if role == "player" else None,
        "score": participant.score if role == "player" else None,
        "streak": streak if role == "player" else None,
        "answers_progress": answers_progress if role == "player" else None,
        "remaining_seconds": remaining_seconds if role == "player" else None,
        "can_advance": can_advance,
    }

    # получаем необходимый контекст подсчета ответтов (participants_count, answered_count, all_answered)
    # из функции get_answer_stats
    answer_context = get_answer_stats(session)
    context.update(answer_context)

    return render(request, "gameplay/play.html", context=context)

@login_required
def start(request: HttpRequest, pk: int):
    """
    Стартовая вьюха для соло прохождения

    Серия ищется через visible_to(request.user): сыграть можно любую публичную серию
    или свою приватную. pk серии приходит прямо из URL, поэтому без этого фильтра
    чужую приватную серию можно было бы запустить, просто подставив её pk (IDOR) -
    и через SeriesRun/GameSession увидеть все её вопросы.
    """
    series = get_object_or_404(
        QuizSeries.objects
        .visible_to(request.user)
        .prefetch_related("rounds", "runs__game_sessions")
        .annotate(rounds_count=Count("rounds")),
        pk=pk
    )

    if not series.rounds.all():
        messages.error(request, "Невозможно начать квиз без раундов. Обратитесь к автору квиза.")
        url = reverse("quizzes:quizzes_preview", kwargs={"pk": series.pk})
        return redirect(url)

    if request.method == "POST":
        user = request.user
        #проверяем на наличие IntegrityError в транзакции, если было - сессия in_progress уже существует, забираем ее и идем на gameplay:play
        try:
            #для заполнения current_round_index определяем какой индекс первого раунда реально щас у series (при удаении первого раунда в серии (с индеком 0) первый раунд может стать с индексом не 0
            first_round = series.rounds.order_by('round_order').first()
            with transaction.atomic():
                series_run = SeriesRun.objects.create(
                    series=series,
                    mode="solo",
                    created_by=user,
                    current_round_index=first_round.round_order if first_round else None
                )
                logger.info("Пользователем %s создана соло комната №%s для квиза №%s",series_run.created_by.username, series_run.pk, series.pk)
        except IntegrityError:
            series_run = SeriesRun.objects.get(series=series, created_by=user, status="in_progress")
        url = reverse("gameplay:solo_room", kwargs={"pk": series_run.pk})
        return redirect(url)

    #если есть активный раунд (есть GameSession) - переходим в игру сразу
    game_session_in_progress = GameSession.objects.filter(series_run__series=series, created_by=request.user, status="in_progress").first()
    if game_session_in_progress:
        url = reverse("gameplay:play", kwargs={"pk": game_session_in_progress.pk})
        return redirect(url)

    #если активных раундов нет - проверяем, нет ли активной серии (solo_room), если есть - переходим туда
    series_run_in_progress = SeriesRun.objects.filter(series=series, created_by=request.user, status="in_progress").first()
    if series_run_in_progress:
        url = reverse("gameplay:solo_room", kwargs={"pk": series_run_in_progress.pk})
        return redirect(url)

    #если это первый вход по данной серии данного пользователя:
    context = {
        "series": series,
        "rounds_count": series.rounds_count
    }

    return render(request, "gameplay/start.html", context=context)

@login_required
def solo_room(request: HttpRequest, pk: int):
    series_run = get_object_or_404(
        SeriesRun.objects.select_related("series", "created_by", "room").prefetch_related("series__rounds", "game_sessions__quiz", "game_sessions__participants", "room__room_players__user"),
        pk=pk
    )
    #формируем список room_players для определения доступа к solo_room для не владельцев SeriesRun, но участников комнаты
    room_players = []
    if series_run.room:
        room_players = [rp.user for rp in series_run.room.room_players.all()]

    if series_run.created_by_id != request.user.id and request.user not in room_players:
        raise PermissionDenied

    if request.method == "POST":
        if series_run.status in ["completed", "abandoned"] or series_run.created_by_id != request.user.id or series_run.mode != "solo":
            raise PermissionDenied
        curr_quiz = series_run.series.rounds.filter(round_order=series_run.current_round_index).first()

        if curr_quiz is None:
            #текущий раунд удалён - прогон прерываем ("abandoned", как при смене серии/закрытии комнаты),
            #иначе он навсегда остался бы in_progress и start() каждый раз возвращал бы в эту же комнату
            series_run.status = "abandoned"
            series_run.finished_at = timezone.now()
            series_run.save(update_fields=["status", "finished_at"])
            messages.error(request, "Похоже раунд был удален. Прохождение прервано, квиз можно начать заново")
            url = reverse("quizzes:quizzes_preview", kwargs={"pk": series_run.series.pk})
            return redirect(url)

        user = request.user
        # проверяем на наличие IntegrityError в транзакции, если было - сессия in_progress уже существует, забираем ее и идем на gameplay:play
        try:
            with transaction.atomic():
                session = GameSession.objects.create(
                    quiz=curr_quiz,
                    mode="solo",
                    created_by=user,
                    series_run=series_run,
                    current_question=curr_quiz.questions.first()
                )
                participant = GameParticipant.objects.create(
                    session=session,
                    user=user
                )
                logger.info("Сессия %s квиза %s создана и начата пользователем %s", session.pk, curr_quiz.pk,
                            session.created_by.username)
        except IntegrityError:
            session = GameSession.objects.get(quiz=curr_quiz, created_by=user, status="in_progress")
        url = reverse("gameplay:play", kwargs={"pk": session.pk})
        return redirect(url)

    #проверяем, нет ли активной game_sessions
    game_session_in_progress = next((g for g in series_run.game_sessions.all() if g.status == "in_progress"), None)
    if game_session_in_progress:
        url = reverse("gameplay:play", kwargs={"pk": game_session_in_progress.pk})
        return redirect(url)

    context = get_series_progress(series_run)

    return render(request, "gameplay/solo_room.html", context=context)

@login_required
def play(request: HttpRequest, pk: int):
    if request.method == "POST":
        #достаем все необходимое из POST
        option = None
        chosen_option_pk = request.POST.get("chosen_option_id", "")
        current_answer_pk = request.POST.get("current_answer_id", "")

        #получаем AnswerOption по chosen_option_pk
        if chosen_option_pk:
            option = get_object_or_404(AnswerOption, pk=chosen_option_pk)

        with transaction.atomic():

            # получаем сессию для редиректа на gameplay:play
            # используем select_for_update для того, чтобы заблокировать запись в БД для других операций
            session = get_object_or_404(
                GameSession.objects.select_for_update(of=("self",)).select_related("quiz"),
                pk=pk
            )

            #получаем, обновляем и сохраняем GameAnswer
            answer = _update_gameAnswer(current_answer_pk, option, session, request)

            #если answer is correct - обновляем score
            if answer.is_correct:
                participant = _update_total_score(session, request)

        url = reverse("gameplay:play", kwargs={"pk": session.pk})
        return redirect(url)

    session = get_object_or_404(
        GameSession.objects.select_related(
            "quiz",# time_limit_seconds, points_per_correct
            "room",# host_id — роль и can_advance
            "current_question",# текст вопроса, факт; без него — отдельный запрос
            "series_run",# нужен _check_and_make_complete
        ).prefetch_related(
            "participants__user",# имена в списке игроков
            "participants__participants_answers",# статистика, прогресс, серия
            "quiz__questions__options",# список вопросов и варианты
        ),
        pk=pk)

    if session.status == "completed":
        url = reverse("gameplay:result", kwargs={"pk": session.pk})
        return redirect(url)

    #получаем роль пользователя в текущей сессии
    role, participant = get_role(session, request.user.id)
    if role == "undefined":
        raise PermissionDenied

    return _play_multiplayer_or_solo(request, session, participant, role)

@login_required
@require_POST
def next_question(request: HttpRequest, pk: int):

    with transaction.atomic():
        session = get_object_or_404(
            (GameSession.objects
             .select_for_update(of=("self",))
             .select_related("quiz", "room", "current_question", "series_run")
             .prefetch_related("participants", "participants__participants_answers")),
            pk=pk
        )

        if session.status != "in_progress":
            url = reverse("gameplay:result", kwargs={"pk": session.pk})
            return redirect(url)

        role, participant = get_role(session, request.user.id)
        is_host = session.room_id is not None and session.room.host_id == request.user.id

        # соло — переключает сам игрок, мультиплеер — только хост
        if session.mode == "solo" and participant is None:
            raise PermissionDenied
        if session.mode == "multiplayer" and not is_host:
            raise PermissionDenied

        curr_question = session.current_question
        if curr_question is None:  # вопросы кончились, но раунд не закрыт
            _check_and_make_complete(session)
            return redirect("gameplay:result", pk=session.pk)

        #если session.current_question_id не равно question_id из формы - считаем,
        # что вопрос уже кто-то продвинул и просто редиректимся на play (двойной клик)
        if str(curr_question.pk) != request.POST.get("question_id"):
            url = reverse("gameplay:play", kwargs={"pk": session.pk})
            return redirect(url)

        # кто нажимает и сам играет (соло или играющий хост) — сначала отвечает
        if participant is not None:
            own = next((a for a in participant.participants_answers.all()
                        if a.question_id == curr_question.pk), None)
            if own is None or not (own.answered_at or own.is_skipped):
                raise PermissionDenied

        #продвигаем у каждого GameParticipant ответ
        if session.mode == "multiplayer":
            for gp in session.participants.all():
                cur_ans, _ = GameAnswer.objects.get_or_create(
                    participant=gp,
                    question=curr_question
                )
                if not cur_ans.answered_at and not cur_ans.is_skipped:
                    cur_ans.is_skipped = True
                    cur_ans.save(update_fields=["is_skipped"])

        #вычисляем новый вопрос и присваиваем его сессии
        next_q = session.quiz.questions.filter(order__gt=curr_question.order).order_by("order").first()
        session.current_question = next_q
        session.save(update_fields=["current_question"])
        if next_q is None:
            session = _check_and_make_complete(session)
            return redirect("gameplay:result", pk=session.pk)

        transaction.on_commit(lambda: _notify_session(session))

    url = reverse("gameplay:play", kwargs={"pk": session.pk})
    return redirect(url)


@login_required
def result(request: HttpRequest, pk: int):
    session = get_object_or_404(
        GameSession.objects.select_related(
            "quiz",
            "created_by",
            "room"
        ).prefetch_related(
            "participants",
            "participants__user",
            "quiz__questions",
            "quiz__questions__options",
            "participants__participants_answers__question",
            "participants__participants_answers__chosen_option",
            "room__host"
        ),
        pk=pk)

    #хоста пропускаем даже если он не участник (может смотреть результаты)
    is_host = session.room.host == request.user if session.room else False

    # Получаем всех участников из кэша (без дополнительного запроса)
    participants = session.participants.all()

    # Находим нужного. Останавливаемся на первом найденном за счет next
    curr_participant = next((p for p in participants if p.user_id == request.user.id), None)

    if curr_participant is None and not is_host:
        raise PermissionDenied

    #считаем количество верных ответов
    correct_answer_count = 0
    if curr_participant is not None:
        correct_answer_count = sum(1 for pa in curr_participant.participants_answers.all() if pa.is_correct)

    context = {
        "curr_participant": curr_participant,
        "session": session,
        "question_count": len(session.quiz.questions.all()),
        "correct_answer_count": correct_answer_count,
    }
    return render(request, "gameplay/result.html", context=context)