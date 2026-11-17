from django.db import models
from django.conf import settings

MODE_CHOICES = [
    ('solo', 'Одиночная игра'),
    ('multiplayer', 'Групповая игра'),
]

STATUS_CHOICES = [
    ('in_progress', 'В процессе'),
    ('completed', 'Завершена'),
    ('abandoned', 'Заброшена'),
]

RATING_MODE_CHOICES = [
    ('rated', 'С рейтингом'),
    ('training', 'Тренировка'),
    ('own', 'Собственный квиз'),
]

REWARD_STATUS_CHOICES = [
    ('pending', 'Ожидает выполнения условий'),
    ('awarded', 'Награда начислена'),
    ('ineligible', 'Награда не положена'),
]

REWARD_REASON_CHOICES = [
    ('', 'Нет причины отказа'),
    ('private_at_start', 'Квиз был приватным при первом старте'),
    ('private_at_completion', 'Квиз был приватным при полном завершении'),
]

class SeriesIdentity(models.Model):
    """
    Постоянная запись о квизе для статистики.
    Она имеет свой pk. Поле со ссылкой на живой QuizSeries позволяет открыть квиз, пока он существует.
    SET_NULL означает: при удалении квиза очистить эту ссылку, но оставить запись статистики.
    Исходный pk (original_series_id) — отдельное числовое поле, а
    не FK: оно сохраняет номер удалённого объекта.
    Название нужно для истории и уведомлений, автор — для начисления репутации.
    """

    series = models.OneToOneField(
        "quizzes.QuizSeries",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    original_series_id = models.PositiveBigIntegerField(unique=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=50)

class RoundIdentity(models.Model):
    """
    постоянная запись о раунде. Она имеет свой pk,
    ссылку на SeriesIdentity, nullable-ссылку на живой Quiz, исходный pk (original_quiz_id)
    и название раунда. После удаления «Планет» все участия по-прежнему относятся к одной постоянной записи.
    Одной nullable-ссылки на Quiz недостаточно: после удаления она пуста, поэтому
    не годится как единственный ключ попыток и истории (поэтому добавили original_quiz_id).
    """

    quiz = models.OneToOneField(
        "quizzes.Quiz",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    original_quiz_id = models.PositiveBigIntegerField(unique=True)
    series_identity = models.ForeignKey(
        SeriesIdentity,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='round_identities',
    )
    title = models.CharField(max_length=50)

class RoundParticipation(models.Model):
    """
    Одно участие одного игрока в одном раунде.
    Здесь хранятся режим solo/multiplayer, время старта, статус,
    итоговый игровой счёт, число вопросов и правильных ответов.
    Во время игры счёт продолжает вести GameParticipant;
    при завершении/прерывании сервис сохраняет итог в историю.
    Статистическая запись создаётся при старте и содержит стабильный исходный ID участника,
    чтобы повторный вызов не создавал вторую историю того же участия.
    Перезагрузка вкладки продолжает существующее участие. Новая игра создаёт новое.
    """

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='participations',
    )
    round_identity = models.ForeignKey(
        RoundIdentity,
        on_delete=models.PROTECT,
        related_name='participations',
    )
    participant = models.OneToOneField(
        "gameplay.GameParticipant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    original_participant_id = models.PositiveBigIntegerField(unique=True)
    original_session_id = models.PositiveBigIntegerField()
    mode = models.CharField(max_length=20, choices=MODE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="in_progress")
    rating_mode = models.CharField(max_length=20, choices=RATING_MODE_CHOICES)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    score = models.IntegerField(default=0)
    questions_count = models.PositiveIntegerField()
    correct_answers_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(
                fields=["player", "round_identity", "status"],
                name="stats_part_player_round_idx",
            ),
            models.Index(
                fields=["original_session_id"],
                name="stats_part_session_idx",
            ),
        ]

class RatingAttempt(models.Model):
    """
    Единственная рейтинговая попытка игрока для постоянной записи раунда.

    Закрепляется при первом старте чужого раунда и указывает на участие,
    ответы которого могут приносить рейтинг. Право общее для соло и
    мультиплеера; повторные участия считаются тренировкой. Завершение или
    прерывание попытки не освобождает право для следующей игры, а закрытие
    вкладки само по себе не завершает попытку. Пара игрок + раунд уникальна.
    """

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='rating_attempts',
    )
    round_identity = models.ForeignKey(
        RoundIdentity,
        on_delete=models.PROTECT,
        related_name='rating_attempts',
    )
    participation = models.OneToOneField(
        RoundParticipation,
        on_delete=models.PROTECT,
        related_name='rating_attempts',
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)

    # невозможно создать инстанс с сочитанием одинаковых "player", "round_identity"
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "round_identity"],
                name="stats_attempt_player_round_uniq",
            )
        ]

class SeriesProgress(models.Model):
    """
    Накопительный прогресс игрока в чужой серии для аналитики и награды автора.

    Одна запись на пару игрок + постоянная запись серии, общая для всех
    запусков и режимов. Например, первый раунд можно завершить в соло,
    второй — позже в мультиплеере: оба завершения дополняют один прогресс.
    При первом старте фиксируются автор, публичность, время знакомства
    и список требований в SeriesProgressRound. При повторах снимок не меняется.

    completed_at отмечает полное завершение для аналитики независимо от
    публичности: все оставшиеся требования выполнены и хотя бы один раунд
    реально завершён. qualified_at отмечает завершение для репутации,
    популярности и достижений: серия публична и на старте, и при завершении.
    Награда автору — сумма учитываемых вопросов снимка, максимум 50 очков.
    reward_status: pending — решение ожидается, awarded — награда начислена,
    ineligible — окончательный отказ; reward_reason поясняет отказ.
    Приватные серии учитываются в аналитике без награды; последующая
    публикация не отменяет отказ. Собственные прохождения автора исключены.
    """

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='series_progresses',
    )
    series_identity = models.ForeignKey(
        SeriesIdentity,
        on_delete=models.PROTECT,
        related_name='series_progresses',
    )
    author_at_start = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='authored_series_progresses',
    )
    started_at = models.DateTimeField()
    was_public_at_start = models.BooleanField()
    completed_at = models.DateTimeField(null=True)
    qualified_at = models.DateTimeField(null=True)
    reward_status = models.CharField(max_length=30, choices=REWARD_STATUS_CHOICES)
    reward_reason = models.CharField(max_length=40, blank=True, choices=REWARD_REASON_CHOICES)

    # невозможно создать инстанс с сочитанием одинаковых "player", "series_identity"
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "series_identity"],
                name="stats_progress_player_series_uniq",
            )
        ]

class SeriesProgressRound(models.Model):
    """
    Один обязательный раунд в исходном снимке прогресса игрока по серии.

    Если в серии два непустых раунда, при первом старте создаются две строки
    для одного SeriesProgress. Каждая хранит раунд и исходное questions_count
    для расчёта репутации. Позднее добавленные раунды и изменения числа
    вопросов не меняют снимок. Пара прогресс + раунд уникальна.

    completed_at хранит первое личное завершение раунда. При удалении ещё
    не завершённого раунда excluded_at исключает его из требований и суммы
    вопросов для награды. Уже завершённый удалённый раунд остаётся учтённым.
    """

    progress = models.ForeignKey(
        SeriesProgress,
        on_delete=models.PROTECT,
        related_name='series_progress_rounds',
    )
    round_identity = models.ForeignKey(
        RoundIdentity,
        on_delete=models.PROTECT,
        related_name='series_progress_rounds',
    )
    questions_count = models.PositiveIntegerField()
    completed_at = models.DateTimeField(null=True)
    excluded_at = models.DateTimeField(null=True)

    # невозможно создать инстанс с сочитанием одинаковых "progress", "round_identity"
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["progress", "round_identity"],
                name="stats_progress_round_uniq",
            )
        ]
