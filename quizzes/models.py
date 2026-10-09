from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

from django.conf import settings
from django.db.models import OuterRef, Exists
from django.apps import apps

class Category(models.Model):
    """Категория квиза (история, наука, спорт...) - плоский справочник, без иерархии."""
    title = models.CharField(max_length=50)

    def __str__(self):
        return self.title


class QuizSeriesQuerySet(models.QuerySet):
    """
    В этом классе мы создаем новые методы на QuerySet,
    условно говоря, это заготовленные методы для упрощенной
    в дальнейшем фильтрации
    """

    def visible_to(self, user):
        """
        Серии, которые пользователь вправе видеть: все публичные + свои (любого статуса).
        """
        if not user.is_authenticated:
            return self.filter(status="public")
        return self.filter(models.Q(status="public") | models.Q(user=user))

    def saved_by_user(self, user):
        """
        Серии, которые сохранены пользователем и
        сейчас в статусе "public"
        """
        if not user.is_authenticated:
            return self.none()
        SavedQuizSeries = apps.get_model("social", "SavedQuizSeries") #чтобы избежать циклического импорта

        saved = SavedQuizSeries.objects.filter(user=user, series=OuterRef("pk")) #OuterRef - ссылка на внешний пк основного запроса, т.к данная строка - это подзапрос
        return self.filter(models.Q(status="public") & Exists(saved))

    def available_to(self, user):
        """
        Серии, которые сохранены пользователем и
        сейчас в статусе "public" + СВОИ (для мультиплеера)
        """
        if not user.is_authenticated:
            return self.none()
        SavedQuizSeries = apps.get_model("social", "SavedQuizSeries")  # чтобы избежать циклического импорта
        saved = SavedQuizSeries.objects.filter(user=user, series=OuterRef("pk")) #OuterRef - ссылка на внешний пк основного запроса, т.к данная строка - это подзапрос
        return self.filter(models.Q(user=user) | (models.Q(status="public") & Exists(saved)))

    def showcase_series(self, user):
        """
        Серии, которые доступны на странице quizzes_menu:
        не мои + я не добавлял еще к себе + публичные
        """
        if not user.is_authenticated:
            return self.filter(status="public")
        SavedQuizSeries = apps.get_model("social", "SavedQuizSeries")
        saved = SavedQuizSeries.objects.filter(user=user, series=OuterRef("pk")) #OuterRef - ссылка на внешний пк основного запроса, т.к данная строка - это подзапрос
        return self.filter(models.Q(status="public") & (~Exists(saved))).exclude(user=user)

    def with_in_progress(self, user):
        """Добавляет каждой серии поле in_progress: есть ли у user незавершённый прогон."""
        if not user.is_authenticated:
            # т.к для незалогиненного пользователя мы тоже показываем карточки на стартовой странице,
            # то и поле in_progress для шаблона тоже должна быть,
            # мы не можем просто написать in_progress=False, т.к это обычное питоновское значение и база данных не превратит его в кусок запроса,
            # поэтому нам надо передать в in_progress особую константу, которая правильно преобразуется в SQL запрос, это константой является models.Value,
            # мы подставляем в эту константу значение False и говорим django (output_field), какой типа этой константы (models.BooleanField()),
            # таким образом для шаблонов любого незалогиненного пользователя на стартовой странице будет доступна переменная in_progress=False
            return self.annotate(in_progress=models.Value(False, output_field=models.BooleanField()))
        SeriesRun = apps.get_model("gameplay", "SeriesRun")  # чтобы избежать циклического импорта
        runs = SeriesRun.objects.filter(series=OuterRef("pk"), created_by=user, status="in_progress") #OuterRef - ссылка на внешний пк основного запроса, т.к данная строка - это подзапрос
        return self.annotate(in_progress=Exists(runs)) #тут мы говорим базе: «в этом конкретном запросе, кроме обычных колонок серии, посчитай и верни ещё одно значение и назови его in_progress» (типа ) SELECT id, title, ..., EXISTS(SELECT ...) AS in_progress

class QuizSeries(models.Model):
    """
    Контейнер верхнего уровня - то, что пользователь в UI и называет "квиз":
    название, владелец, публичность и упорядоченный набор раундов (`Quiz`,
    см. related_name='rounds' на Quiz.series). Сама по себе не содержит вопросов -
    все Question/AnswerOption по-прежнему привязаны к конкретному Quiz (раунду),
    не к серии напрямую.
    """

    STATUS_CHOICES = [
        ('private', 'Приватный квиз'),
        ('public', 'Публичный квиз'),
    ]
    title = models.CharField(max_length=50)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quiz_series')
    description = models.CharField(max_length=255, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='quiz_series')

    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='private')

    objects = QuizSeriesQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title



class Quiz(models.Model):
    """
    По сути это РАУНД, а не квиз - один самостоятельно играемый набор вопросов
    (то, что целиком потребляет GameSession в gameplay/). Имя "Quiz" осталось
    историческим: модель была создана и называлась так задолго до появления
    QuizSeries выше, а переименовать её в "Round" означало бы переименовать
    FK/related_name/urls/views/templates по всем четырём приложениям проекта
    (gameplay.GameSession.quiz, multiplayer.Room.current_quiz, ai_generator,
    все quizzes:* маршруты и переменные quiz/object в шаблонах) - решили не
    делать этот рефакторинг ради одного имени, см. обсуждение в CLAUDE.md/
    quiz_project_plan.md (раздел "Раунды"). Поэтому:
      - Quiz.series=None - самостоятельный раунд вне какой-либо серии
        (старое поведение до появления QuizSeries, полностью рабочее)
      - Quiz.series=<QuizSeries> - раунд входит в серию, Quiz.round_order
        задаёт его порядок внутри неё
    """
    TYPE_CHOICES = [
        ('ai', 'Сгенерировано AI'),
        ('by_user', 'Создано вручную'),
    ]

    LEVEL_CHOICES = [
        ('common', 'Без уровня'),
        ('junior', 'Junior'),
        ('middle', 'Middle'),
        ('pro', 'Pro'),
    ]

    STYLE_CHOICES = [
        ('serious', 'Серьезный стиль'),
        ('humorous', 'Шутливый стиль'),
    ]
    AUDIENCE_CHOICES = [
        ('common', 'Без классификатора'),
        ('children', 'Дети'),
        ('teens', 'Подростки'),
        ('middle_ages', 'Средние года'),
        ('elderly', 'Пожилые'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quizzes')
    series = models.ForeignKey(QuizSeries, on_delete=models.CASCADE, null=True, blank=True, related_name='rounds')

    type = models.CharField(max_length=20, choices=TYPE_CHOICES)

    subject = models.CharField(max_length=50)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES)

    created_at = models.DateTimeField(auto_now_add=True)
    style = models.CharField(max_length=20, choices=STYLE_CHOICES)
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default='common')
    time_limit_seconds = models.IntegerField(default=50)
    points_per_correct = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    round_order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.subject

class Question(models.Model):
    """Один вопрос внутри раунда (Quiz). fact - "любопытный факт", показывается
    игроку вместе с вопросом/после ответа, генерируется AI на уровне вопроса
    целиком, не привязан к конкретному варианту ответа."""

    class Meta:
        ordering = ['order']

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField()
    order = models.PositiveIntegerField(default=0)
    fact = models.TextField(blank=True)

    def __str__(self):
        return self.text[:50]

class AnswerOption(models.Model):
    """Один из вариантов ответа на Question. Ровно один is_correct=True на вопрос -
    инвариант держится только корректностью кода создания, в БД не проверяется."""

    class Meta:
        ordering = ['order']

    question = models.ForeignKey(Question, related_name='options', on_delete=models.CASCADE)
    text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.text[:50]