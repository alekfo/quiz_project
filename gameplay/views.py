import logging
from datetime import datetime
from typing import List, Any

from django.db.models import Count
from django.shortcuts import render, redirect, reverse
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import PermissionDenied

from .models import GameSession, GameParticipant, GameAnswer
from quizzes.models import Quiz, Question, AnswerOption

logger = logging.getLogger(__name__)

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
    if answ.answered_at:
        raise PermissionDenied("На этот вопрос уже отвечали")
    if opt and opt.question_id != answ.question_id:
        raise PermissionDenied
    answ.chosen_option = opt
    answ.is_correct = opt.is_correct if opt else False
    answ.is_skipped = opt is None
    answ.answered_at = timezone.now()
    answ.save()
    return answ

def _update_total_score(sess: GameSession, req: HttpRequest) -> GameParticipant:
    """
    Функция для изменения score у текущего GameParticipant
    :param session: текущая сессия
    :param request: данные запроса для получения USER
    :return: возвращает текущий GameParticipant
    """

    participant = get_object_or_404(GameParticipant, session=sess, user=req.user)
    old_score = participant.score
    participant.score = old_score + 1
    participant.save()
    return participant

def _get_questions_without_answer(part: GameParticipant, sess: GameSession) -> List[Question]:
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
    for participant in sess.participants.all():
        if not participant.finished_at:
            break
    else:
        sess.status = "completed"
        sess.save()
    return sess

def start(request: HttpRequest, pk: int):
    quiz = get_object_or_404(
        Quiz.objects.annotate(questions_count=Count("questions")),
        pk=pk
    )
    sessions_in_progress = GameSession.objects.filter(quiz=quiz, created_by=request.user, status="in_progress").first()
    if sessions_in_progress:
        url = reverse("gameplay:play", kwargs={"pk": sessions_in_progress.pk})
        return redirect(url)

    if request.method == "POST":
        user = request.user
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
        url = reverse("gameplay:play", kwargs={"pk": session.pk})
        return redirect(url)

    context = {
        "quiz": quiz,
        "questions_count": quiz.questions_count
    }

    return render(request, "gameplay/start.html", context=context)

def play(request: HttpRequest, pk: int):
    if request.method == "POST":
        #достаем все необходимое из POST
        option = None
        chosen_option_pk = request.POST.get("chosen_option_id", "")
        current_answer_pk = request.POST.get("current_answer_id", "")

        #получаем сессию для редиректа на gameplay:play
        session = get_object_or_404(GameSession, pk=pk)

        #получаем AnswerOption по chosen_option_pk
        if chosen_option_pk:
            option = get_object_or_404(AnswerOption, pk=chosen_option_pk)

        with transaction.atomic():
            #получаем, обновляем и сохраняем GameAnswer
            answer = _update_gameAnswer(current_answer_pk, option, session, request)

            #если answer is correct - обновляем score
            if answer.is_correct:
                participant = _update_total_score(session, request)

        url = reverse("gameplay:play", kwargs={"pk": session.pk})
        return redirect(url)

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

    if session.status == "completed":
        url = reverse("gameplay:result", kwargs={"pk": session.pk})
        return redirect(url)

    # Получаем всех участников из кэша (без дополнительного запроса)
    participants = session.participants.all()

    # Находим нужного. Останавливаемся на первом найденном за счет next
    participant = next((p for p in participants if p.user_id == request.user.id), None)

    if participant is None:
        raise PermissionDenied

    #получаем список вопросов данного квиза без ответов для данного GameParticipant
    questions_without_answers = _get_questions_without_answer(participant, session)

    if not questions_without_answers:
        #считаем, что у данного GameParticipant все вопросы пройдены и сессия для него завершена, ставим время finished_at для данного participant
        if not participant.finished_at:
            participant.finished_at = timezone.now()
            participant.save()

        #проверяем для mode=multiplayer, завершена ли для всех сессия, а для mode=solo завершаем сессию
        if session.mode == "multiplayer":
            #проверяем, завершена ли GameSession в целом для всех GameParticipant если mode=multiplayer
            session = _check_and_make_complete(session)
        else:
            session.status = "completed"
            session.save()

        url = reverse("gameplay:result", kwargs={"pk": session.pk})
        return redirect(url)

    current_question = questions_without_answers[0]

    current_answer, _ = GameAnswer.objects.get_or_create(
        participant=participant,
        question=current_question
    )
    context = {
        "current_question": current_question,
        "current_answer": current_answer,
        "current_session": session,
    }

    return render(request, "gameplay/play.html", context=context)

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

    context = {
        "curr_participant": curr_participant,
        "session": session
    }
    return render(request, "gameplay/result.html", context=context)