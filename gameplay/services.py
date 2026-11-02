from django.db.models import Sum
from django.contrib.auth import get_user_model

from gameplay.models import GameParticipant, SeriesRun, GameSession

User = get_user_model()

def get_series_progress(series_run: SeriesRun) -> dict:
    completed_sessions = [
        game_session
        for game_session in series_run.game_sessions.all()
        if game_session.status == "completed"
    ]
    current_round = series_run.series.rounds.filter(round_order=series_run.current_round_index).first()

    is_completed = series_run.status != "in_progress"

    #получаем список инстансов GameParticipant для данной series_run, группируем их
    #с помощью values по user_id а второй столбец будет просчитанной суммой score у всех одинаковых user_id
    scores = list(
        GameParticipant.objects
        .filter(session__series_run=series_run, session__status="completed")
        .values("user_id")
        .annotate(total_score=Sum("score"))
        .order_by("-total_score")
    )

    #формируем словарь {"user_id": user_instance}
    users_by_id = User.objects.in_bulk([row["user_id"] for row in scores])

    #формируем конечный список из (user_instance, total_score)
    leaderboard = [(users_by_id[row["user_id"]], row["total_score"]) for row in scores]

    #проверяем в процессе ли текущий current_round
    is_current_round_is_in_progress = any(
        gs for gs in series_run.game_sessions.all()
        if gs.quiz == current_round and gs.status == "in_progress"
    )

    #все раунды прогона
    rounds = sorted(series_run.series.rounds.all(), key=lambda r: r.round_order)

    #словарь завершенных сессиий прогона где ключ - quiz_id текущей завершенной сессии, значение - сама текущая завершенная сессия
    completed_by_quiz = {s.quiz_id: s for s in completed_sessions}

    rounds_progress = []
    for r in rounds:
        session = completed_by_quiz.get(r.pk)
        if session:
            state = "completed"
        elif r.round_order == series_run.current_round_index:
            state = "current"
        else:
            state = "pending"
        rounds_progress.append({"round": r, "state": state, "session": session})

    return {
        "series_run": series_run,
        "completed_sessions": completed_sessions,
        "rounds_progress": rounds_progress,
        "rounds_count": len(rounds),
        "current_round": current_round,
        "is_completed": is_completed,
        "leaderboard": leaderboard,
        "is_current_round_is_in_progress": is_current_round_is_in_progress,
    }

def get_answer_stats(session: GameSession) -> dict:
    if session.current_question_id is None:
        return {"participants_count": 0, "answered_count": 0, "all_answered": False}

    #вычисляем количество участников в сессии
    participants_count = len(session.participants.all())

    #вычисляем количество уже ответивших на наш вопрос (session.current_question)
    answered_count = 0
    for p in session.participants.all():
        for pa in p.participants_answers.all():
            if pa.question_id == session.current_question_id and (pa.answered_at or pa.is_skipped):
                answered_count += 1

    #флаг "ответили все участники"
    all_answered = answered_count == participants_count

    return {
        "participants_count": participants_count,
        "answered_count": answered_count,
        "all_answered": all_answered,
    }

def get_role(session: GameSession, user_id) -> tuple[str, GameParticipant | None]:
    participant = next((p for p in session.participants.all() if p.user_id == user_id), None)
    if participant:
        role = "player"
    elif session.room and session.room.host_id == user_id:
        role = "host_observer"
    else:
        role = "undefined"
    return role, participant