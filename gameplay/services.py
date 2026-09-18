from django.db.models import Sum
from django.contrib.auth import get_user_model

from gameplay.models import GameParticipant, SeriesRun

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

    return {
        "series_run": series_run,
        "completed_sessions": completed_sessions,
        "current_round": current_round,
        "is_completed": is_completed,
        "leaderboard": leaderboard,
    }