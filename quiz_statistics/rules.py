RATING_RULES_VERSION = 1
PLAYER_CORRECT_POINTS = 10
BLITZ_FAST_PERCENT = 30
BLITZ_MEDIUM_PERCENT = 70
BLITZ_FAST_POINTS = 10
BLITZ_MEDIUM_POINTS = 7
BLITZ_SLOW_POINTS = 4
AUTHOR_POINTS_PER_QUESTION = 1
AUTHOR_COMPLETION_POINTS_CAP = 50 #потолок очков автору за квиз
AUTHOR_AUDIENCE_THRESHOLDS = (1, 5, 10, 25, 50, 100, 500, 1000)
SERIES_AUDIENCE_THRESHOLDS = (1, 5, 10, 25, 50, 100, 500, 1000)
POPULAR_SERIES_MIN_FINISHERS = 5
AUTHOR_POPULAR_SERIES_THRESHOLDS = (1, 5, 10, 25, 50)
PLAYER_COMPLETED_SERIES_THRESHOLDS = (1, 5, 10, 25, 50, 100)
LEADERBOARD_TOP_SIZE = 10

#Одиночная * в сигнатуре Python означает, что все параметры после неё нужно передавать по имени.
#Вызов calculate_player_rating(True, False, 2.5, 10, True) вызовет TypeError, потому что здесь аргументы переданы по позиции.
def calculate_player_rating(*, is_correct: bool, is_skipped: bool, elapsed_seconds: float, time_limit_seconds: int, dynamic_scoring_mode: bool) -> int:
    """Вычисляет очки рейтинга игрока за ответ.

    Вызывается из record_answer_rating для правильного рейтингового ответа.
    За пропуск, ошибку или превышение лимита даёт 0. За правильный ответ
    в пределах лимита даёт фиксированные очки или, в блице, очки по скорости.
    """
    if is_skipped or not is_correct or elapsed_seconds > time_limit_seconds:
        return 0
    if dynamic_scoring_mode:
        if elapsed_seconds * 100 <= time_limit_seconds * BLITZ_FAST_PERCENT:
            return BLITZ_FAST_POINTS
        elif elapsed_seconds * 100 <= time_limit_seconds * BLITZ_MEDIUM_PERCENT:
            return BLITZ_MEDIUM_POINTS
        else:
            return BLITZ_SLOW_POINTS
    else:
        return PLAYER_CORRECT_POINTS

def calculate_author_reputation(questions_count: int) -> int:
    """Вычисляет очки репутации автора за число вопросов с учётом лимита.

    По плану вызывается из evaluate_series_completion, когда завершение
    серии игроком даёт автору право на начисление.
    """
    return min(questions_count * AUTHOR_POINTS_PER_QUESTION, AUTHOR_COMPLETION_POINTS_CAP)
