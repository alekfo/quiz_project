import logging
from typing import List

from django.db.models import Count
from django.shortcuts import render, redirect, reverse
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.contrib.auth.decorators import login_required
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import GameSession, GameParticipant, GameAnswer
from quizzes.models import Quiz, Question, AnswerOption
from multiplayer.views import _notify_room

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
    #вычисляем время, затраченное на вопрос для дальнейшего определения is_skipped
    elapsed = (timezone.now() - answ.shown_at).total_seconds()

    #определяем наличие таймаута
    timed_out = elapsed > session.quiz.time_limit_seconds

    if answ.answered_at:
        raise PermissionDenied("На этот вопрос уже отвечали")
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
    GameParticipant.objects.filter(pk=participant.pk).update(score=F('score') + 1)
    #нужно синхронизировать participant с БД, т.к мы меняли score в обход него
    participant.refresh_from_db(fields=["score"])
    return participant

def _get_questions_without_answer(part: GameParticipant, sess: GameSession) -> List[Question]:
    """
    Идем циклом по всем GameAnswer данного GameParticipant,
    проверяем у данного GameAnswer if a.answered_at is not None or a.is_skipped (или уже отвечено или пропущено),
    формируем список из Question у вопросов, у которых (if a.answered_at is not None or a.is_skipped) = True.
    Таким образом мы имеем список вопросов, у которых уже есть ответ и которые принадлежат данному GameParticipant.
    Далее идем циклом по всем
    Далее проверяется каждый вопрос Question из данной сессии и квиза и если айди данного вопроса не найден в предыдущем списке вопросв,
    на который дан ответ, то этот вопрос и есть тот, который мы ищем - без ответа, и он попадает в конечный q_without_answers
    :param part: текущий участник сессии
    :param sess: сессия
    :return: список Question данного квиза на котореы еще не ответили в рамках текущей сессии
    """

    answered_question_ids = [
        a.question_id
        for a in part.participants_answers.all()
        if a.answered_at is not None or a.is_skipped
    ]
    q_without_answers = [
        q
        for q in sess.quiz.questions.all()
        if q.pk not in answered_question_ids
    ]
    return q_without_answers

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
            if sess.room_id:
                #меняем статус комнаты с in_progress на waiting
                #и сбрасываем current_quiz у комнаты
                sess.room.status = "waiting"
                sess.room.current_quiz = None
                sess.room.save(update_fields=["status", "current_quiz"])

                #переключаем готовность у всех членов комнаты
                sess.room.room_players.update(is_ready=False)

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
            transaction.on_commit(lambda: _notify_room(sess.room))
    return sess


def _check_and_advance_round(session_pk):
    with transaction.atomic():
        session = GameSession.objects.select_for_update().get(pk=session_pk)
        current_question = session.current_question
        if current_question is None:
            return session

        total = session.participants.count()
        answered = GameAnswer.objects.filter(
            participant__session=session,
            question=current_question,
        ).filter(
            Q(answered_at__isnull=False) | Q(is_skipped=True)
        ).count()

        if answered < total:
            return session
        with transaction.atomic():
            next_question = session.quiz.questions.filter(
                order__gt=current_question.order
            ).order_by("order").first()
            session.current_question = next_question
            session.save(update_fields=["current_question"])
            transaction.on_commit(lambda: _notify_session(session))
        return session

def _play_solo(request: HttpRequest, session: GameSession, participant: GameParticipant):
    """
    Логика view-функции play для solo-режима
    :param request:
    :param session:
    :param participant:
    :return:
    """

    # получаем список вопросов данного квиза без ответов для данного GameParticipant
    questions_without_answers = _get_questions_without_answer(participant, session)

    if not questions_without_answers:
        # считаем, что у данного GameParticipant все вопросы пройдены и сессия для него завершена, ставим время finished_at для данного participant
        if not participant.finished_at:
            participant.finished_at = timezone.now()
            participant.save()

        session.status = "completed"
        session.save()

        url = reverse("gameplay:result", kwargs={"pk": session.pk})
        return redirect(url)

    current_question = questions_without_answers[0]

    current_answer, _ = GameAnswer.objects.get_or_create(
        participant=participant,
        question=current_question
    )
    elapsed = (timezone.now() - current_answer.shown_at).total_seconds()
    # max(0, ...) - если игрок провозился дольше лимита, time_limit_seconds - elapsed
    # уйдёт в минус; на экране должно быть "0", а не отрицательное число
    remaining_seconds = max(0, int(session.quiz.time_limit_seconds - elapsed))

    context = {
        "mode": "solo",
        "current_question": current_question,
        "current_answer": current_answer,
        "current_session": session,
        "remaining_seconds": remaining_seconds,
    }

    return render(request, "gameplay/play.html", context=context)

def _play_multiplayer(request: HttpRequest, session: GameSession, participant: GameParticipant):

    # проверяем для mode=multiplayer, завершена ли для всех сессия
    if session.current_question == None:
        # проверяем, завершена ли GameSession в целом для всех GameParticipant, т.к как нет текущего вопроса
        session = _check_and_make_complete(session)
        if session.status == "completed":
            #считаем что сессия завершена у всех GameParticipant
            return  redirect("gameplay:result", pk=session.pk)

    current_answer, _ = GameAnswer.objects.get_or_create(
        participant=participant,
        question=session.current_question
    )

    already_answered = bool(current_answer and (current_answer.answered_at or current_answer.is_skipped))

    elapsed = (timezone.now() - current_answer.shown_at).total_seconds()
    # max(0, ...) - если игрок провозился дольше лимита, time_limit_seconds - elapsed
    # уйдёт в минус; на экране должно быть "0", а не отрицательное число
    remaining_seconds = max(0, int(session.quiz.time_limit_seconds - elapsed))

    context = {
        "mode": "multiplayer",
        "current_question": session.current_question,
        "current_answer": current_answer,
        "current_session": session,
        "remaining_seconds": remaining_seconds,
        "already_answered": already_answered,
    }

    return render(request, "gameplay/play.html", context=context)

@login_required
def start(request: HttpRequest, pk: int):
    quiz = get_object_or_404(
        Quiz.objects.annotate(questions_count=Count("questions")),
        pk=pk
    )

    if request.method == "POST":
        user = request.user
        #проверяем на наличие IntegrityError в транзакции, если было - сессия in_progress уже существует, забираем ее и идем на gameplay:play
        try:
            with transaction.atomic():
                session = GameSession.objects.create(
                    quiz=quiz,
                    mode="solo",
                    created_by=user
                )
                participant = GameParticipant.objects.create(
                    session=session,
                    user=user
                )
                logger.info("Сессия %s квиза %s создана и начата пользователем %s", session.pk, quiz.pk, session.created_by.username)
        except IntegrityError:
            session = GameSession.objects.get(quiz=quiz, created_by=user, status="in_progress")
        url = reverse("gameplay:play", kwargs={"pk": session.pk})
        return redirect(url)

    sessions_in_progress = GameSession.objects.filter(quiz=quiz, created_by=request.user, status="in_progress").first()
    if sessions_in_progress:
        url = reverse("gameplay:play", kwargs={"pk": sessions_in_progress.pk})
        return redirect(url)

    context = {
        "quiz": quiz,
        "questions_count": quiz.questions_count
    }

    return render(request, "gameplay/start.html", context=context)

@login_required
def play(request: HttpRequest, pk: int):
    if request.method == "POST":
        #достаем все необходимое из POST
        option = None
        chosen_option_pk = request.POST.get("chosen_option_id", "")
        current_answer_pk = request.POST.get("current_answer_id", "")

        #получаем сессию для редиректа на gameplay:play
        session = get_object_or_404(GameSession.objects.select_related('quiz'), pk=pk)

        #получаем AnswerOption по chosen_option_pk
        if chosen_option_pk:
            option = get_object_or_404(AnswerOption, pk=chosen_option_pk)

        with transaction.atomic():
            #получаем, обновляем и сохраняем GameAnswer
            answer = _update_gameAnswer(current_answer_pk, option, session, request)

            #если answer is correct - обновляем score
            if answer.is_correct:
                participant = _update_total_score(session, request)

            if session.mode == "multiplayer":
                _check_and_advance_round(session.pk)

        url = reverse("gameplay:play", kwargs={"pk": session.pk})
        return redirect(url)

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
            "room__room_players"
        ),
        pk=pk)

    if session.status == "completed":
        url = reverse("gameplay:result", kwargs={"pk": session.pk})
        return redirect(url)

    # Получаем всех участников из кэша (без дополнительного запроса)
    participants = session.participants.all()

    # Находим нужного. Останавливаемся на первом найденном за счет next
    participant = next((p for p in participants if p.user_id == request.user.id), None)

    if participant is None:
        raise PermissionDenied

    if session.mode == "multiplayer":
        return _play_multiplayer(request, session, participant)
    return _play_solo(request, session, participant)

@login_required
def result(request: HttpRequest, pk: int):
    session = get_object_or_404(
        GameSession.objects.select_related(
            "quiz",
            "created_by"
        ).prefetch_related(
            "participants",
            "participants__user",
            "quiz__questions",
            "quiz__questions__options",
            "participants__participants_answers__question",
            "participants__participants_answers__chosen_option"
        ),
        pk=pk)

    # Получаем всех участников из кэша (без дополнительного запроса)
    participants = session.participants.all()

    # Находим нужного. Останавливаемся на первом найденном за счет next
    curr_participant = next((p for p in participants if p.user_id == request.user.id), None)

    if curr_participant is None:
        raise PermissionDenied

    context = {
        "curr_participant": curr_participant,
        "session": session
    }
    return render(request, "gameplay/result.html", context=context)