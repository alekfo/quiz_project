import logging
from datetime import datetime

from django.db.models import Count
from django.shortcuts import render, redirect, reverse
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone

from .models import GameSession, GameParticipant, GameAnswer
from quizzes.models import Quiz, Question, AnswerOption

logger = logging.getLogger(__name__)

def start(request: HttpRequest, pk: int):

    if request.method == "POST":
        quiz = get_object_or_404(Quiz, pk=request.POST["quiz_id"])
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

    quiz = get_object_or_404(
        Quiz.objects.prefetch_related("game_sessions").annotate(questions_count=Count("questions")),
        pk=pk
    )
    sessions_in_progress = [
        s
        for s in quiz.game_sessions.all()
        if s.status == "in_progress"
    ]
    if sessions_in_progress:
        url = reverse("gameplay:play", kwargs={"pk": sessions_in_progress[0].pk})
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
        current_session_pk = request.POST.get("current_session_id", "")
        chosen_option_pk = request.POST.get("chosen_option_id", "")
        current_answer_pk = request.POST.get("current_answer_id", "")

        #получаем сессиб для редиректа на gameplay:play
        session = get_object_or_404(GameSession, pk=current_session_pk)
        #получаем AnswerOption по chosen_option_pk
        if chosen_option_pk:
            option = get_object_or_404(AnswerOption, pk=chosen_option_pk)
        # получаем ранее созданный GameAnswer
        answer = get_object_or_404(
            GameAnswer,
            pk=current_answer_pk
        )
        #обновляем и сохраняем answer
        answer.chosen_option = option
        answer.is_correct = option.is_correct if option else False
        answer.is_skipped = option is None
        answer.answered_at = timezone.now()
        answer.save()

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

    participant = session.participants.get(user=request.user)
    answered_question_ids = [
        a.question_id
        for a in participant.participants_answers.all()
        if a.answered_at is not None or a.is_skipped
    ]
    questions_without_answers = [
        q
        for q in session.quiz.questions.all()
        if q.pk not in answered_question_ids
    ]
    if not questions_without_answers:
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
    return HttpResponse(f"Тут результат сессии {pk}")