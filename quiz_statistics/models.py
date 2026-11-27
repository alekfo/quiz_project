from django.db import models
from django.conf import settings
from django.db.models import PositiveIntegerField

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

KIND_CHOICES = [
    ("player", "Игрок"),
    ("author", "Автор"),
]

ANSWER_RATING_REASON_CHOICES = [
    ("abandoned", "Прервано"),
    ("own", "Собственный квиз"),
    ("training", "Тренировка"),
    ("timeout", "Вышло время"),
    ("skipped", "Явный пропуск"),
    ("incorrect", "Неправильный ответ"),
    ("correct", "Правильный ответ"),
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

class AnswerRating(models.Model):
    """
    Объяснение рейтинга одного принятого ответа. Сохраняется и положительный результат, и ноль с причиной:
    ошибка, пропуск, просрочка, повторное участие или собственный квиз.
    Нельзя определять просрочку только по is_skipped: существующий код объединяет явный пропуск и таймаут.
    Сервису нужны фактические данные расчёта времени этого ответа.
    """

    answer = models.OneToOneField(
        "gameplay.GameAnswer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    original_answer_id = models.PositiveBigIntegerField(unique=True)
    participation = models.ForeignKey(
        RoundParticipation,
        on_delete=models.PROTECT,
        related_name="answer_ratings"
    )
    original_question_id = models.PositiveBigIntegerField()
    is_correct = models.BooleanField()
    is_skipped = models.BooleanField()
    timed_out = models.BooleanField()
    answered_at = models.DateTimeField()
    elapsed_seconds = models.FloatField()
    time_limit_seconds = models.PositiveIntegerField()
    dynamic_scoring_mode = models.BooleanField()
    points = models.PositiveIntegerField()
    reason = models.CharField(max_length=30, choices=ANSWER_RATING_REASON_CHOICES)
    rules_version = PositiveIntegerField()

class RatingEntry(models.Model):
    """
    Журнал начислений. Одна запись означает «кому, сколько, когда и за какое событие начислено».
    Для игрока событие — AnswerRating, для автора — квалифицирующее завершение SeriesProgress.
    Итоговый рейтинг — сумма записей нужного типа. Нулей в журнале нет, их объясняет AnswerRating.
    Удаление вопроса не удаляет ни объяснение принятого ответа, ни начисление.
    """
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="rating_entries"
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    amount = models.PositiveIntegerField()
    earned_at = models.DateTimeField()
    rules_version = models.PositiveIntegerField()
    answer_rating = models.OneToOneField(
        AnswerRating,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    series_progress = models.OneToOneField(
        SeriesProgress,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    class Meta:
        #При нарушении ограничения во время записи Django получит от базы IntegrityError.
        constraints = [
            #amount строго больше нуля
            #нулевой результат и его причина остаются в AnswerRating
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), #__gt - grater than (__gte - grater than or equal,  __lt - less than)
                name="stats_entry_positive_amount",
            ),
            #прописываем условия "когда запиь пройдет":
            #(kind="player" И answer_rating обязательно заполнен И series_progress должен быть NULL) ИЛИ
            #(kind="author" И answer_rating должен быть NULL И series_progress обязательно заполнен)
            models.CheckConstraint(
                condition=(
                        models.Q(
                            kind="player",
                            answer_rating__isnull=False,
                            series_progress__isnull=True,
                        )
                        | models.Q(
                            kind="author",
                            answer_rating__isnull=True,
                            series_progress__isnull=False,
                        )
                ),
                name="stats_entry_source_matches_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "earned_at", "recipient"]),
        ]


class AuthorAudience(models.Model):
    """Хранит первое квалифицирующее завершение квиза игроком у автора."""

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="author_audience",
    )
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audience_memberships",
    )
    first_qualified_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["author", "player"],
                name="stats_author_player_uniq",
            ),
        ]


class AchievementAward(models.Model):
    """Фиксирует достижение игрока, автора или квиза."""

    class Kind(models.TextChoices):
        """Определяет показатель, за который выдано достижение."""

        AUTHOR_AUDIENCE = "author_audience", "Аудитория автора"
        SERIES_AUDIENCE = "series_audience", "Аудитория квиза"
        AUTHOR_POPULAR_SERIES = "author_popular_series", "Востребованные квизы автора"
        PLAYER_COMPLETED_SERIES = "player_completed_series", "Завершённые квизы игрока"

    kind = models.CharField(max_length=30, choices=Kind.choices)
    threshold = models.PositiveIntegerField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        related_name="author_achievement_awards",
    )
    series_identity = models.ForeignKey(
        SeriesIdentity,
        on_delete=models.PROTECT,
        null=True,
        related_name="achievement_awards",
    )
    awarded_at = models.DateTimeField()
    notification_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_achievement_awards",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind="series_audience",
                        author__isnull=True,
                        series_identity__isnull=False,
                    )
                    | models.Q(
                        kind__in=["author_audience", "author_popular_series"],
                        author__isnull=False,
                        series_identity__isnull=True,
                    )
                    | models.Q(
                        kind="player_completed_series",
                        author__isnull=True,
                        series_identity__isnull=True,
                    )
                ),
                name="stats_award_subject_matches_kind",
            ),
            models.UniqueConstraint(
                fields=["author", "kind", "threshold"],
                condition=models.Q(author__isnull=False),
                name="stats_author_award_uniq",
            ),
            models.UniqueConstraint(
                fields=["series_identity", "kind", "threshold"],
                condition=models.Q(series_identity__isnull=False),
                name="stats_series_award_uniq",
            ),
            models.UniqueConstraint(
                fields=["notification_owner", "threshold"],
                condition=models.Q(kind="player_completed_series"),
                name="stats_player_completed_award_uniq",
            ),
        ]


class Notification(models.Model):
    """Хранит уведомление о достижении и время его прочтения."""

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="achievement_notifications",
    )
    award = models.OneToOneField(AchievementAward, on_delete=models.PROTECT)
    created_at = models.DateTimeField()
    read_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["recipient", "read_at", "created_at"],
                name="stats_notif_rec_read_idx",
            ),
        ]
