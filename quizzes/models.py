from django.db import models

from django.conf import settings

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
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='private')

    def __str__(self):
        return self.title

class Category(models.Model):
    """Тема раунда (история, наука, спорт...) - плоский справочник, без иерархии."""
    title = models.CharField(max_length=50)

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

    STATUS_CHOICES = [
        ('private', 'Приватный раунд'),
        ('public', 'Публичный раунд'),
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
    title = models.CharField(max_length=50)
    description = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='quizzes')
    subject = models.CharField(max_length=50)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='private')
    created_at = models.DateTimeField(auto_now_add=True)
    style = models.CharField(max_length=20, choices=STYLE_CHOICES)
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default='common')
    time_limit_seconds = models.IntegerField(default=50)
    round_order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.title

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